#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate S: a change for the Move leaves the firmware alone. The FM-1 package and the three SMK-37 packages built at
HEAD are byte-identical (sha256) to the ones built at the parent commit (or --ref).

    tools/gate_s.py --build [--ref REF] [--out DIR]     build both sides (HEAVY lock, Docker on a Mac), compare
    tools/gate_s.py --cur DIR --base DIR                compare packages already built (each DIR holds the .fwsc)

Exit 0: every package equal. Exit 1: a package differs or is missing (the names and both sha256 printed).

The base side is built in a detached worktree of REF under build/gate_s_tree (removed after), with the toolchain
and the SDK of the main checkout's .local/ (JIELI_TOOLCHAIN / AC79_SDK win when set). Dev builds carry APP_VER "0.0"
and no date, so equal sources give equal packages. A build takes the HEAVY lock (tools/lock.py; a gate queue holding
it passes its token down) and runs under nice -n 19, one package at a time (the Docker lock is build.py's own).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# (name, build.py arguments): the files ./build.sh writes in build/. The FM-1 last: each build rewrites the shared
# build/frodhes.bin / .elf, so the checkout is left as ./build.sh leaves it (tests/py/test_identity.py reads them)
PACKAGES = [
    ("frodhes-smk37.fwsc", ["--board", "smk37", "--model", "pro"]),
    ("frodhes-smk37-elite.fwsc", ["--board", "smk37", "--model", "elite"]),
    ("frodhes-smk37-mke.fwsc", ["--board", "smk37", "--model", "mke"]),
    ("frodhes.fwsc", []),
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def compare(cur, base, names=None):
    """cur, base: directories -> [(name, sha_cur or None, sha_base or None, equal)]"""
    out = []
    for name in names or [n for n, _ in PACKAGES]:
        a, b = Path(cur) / name, Path(base) / name
        sa = sha256(a) if a.is_file() else None
        sb = sha256(b) if b.is_file() else None
        out.append((name, sa, sb, sa is not None and sa == sb))
    return out


def report(rows):
    lines = []
    for name, sa, sb, ok in rows:
        if ok:
            lines.append(f"  equal    {name}  {sa}")
        else:
            lines.append(f"  DIFFERS  {name}\n           HEAD {sa or 'missing'}\n           base {sb or 'missing'}")
    bad = sum(not r[3] for r in rows)
    lines.append("Gate S: PASS (%d packages byte-identical)" % len(rows) if not bad else
                 "Gate S: FAIL (%d of %d packages differ or are missing)" % (bad, len(rows)))
    return "\n".join(lines), not bad


def git(*args, cwd=ROOT, run=subprocess.run):
    r = run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"gate_s: git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def main_checkout():
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent


def toolchain_env(env=None):
    env = dict(os.environ if env is None else env)
    local = main_checkout() / ".local"
    env.setdefault("JIELI_TOOLCHAIN", str(local / "jieli" / "toolchain"))
    env.setdefault("AC79_SDK", str(local / "fw-AC79_AIoT_SDK"))
    return env


def build_tree(tree, out, run=subprocess.run, env=None):
    """build every package in tree, copy each into out"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, args in PACKAGES:
        cmd = ["nice", "-n", "19", "./build.sh", *args]
        print(f"gate_s: {tree}: {' '.join(cmd)}", flush=True)
        r = run(cmd, cwd=tree, env=env)
        if r.returncode:
            raise SystemExit(f"gate_s: the build of {name} in {tree} failed ({r.returncode})")
        shutil.copy2(Path(tree) / "build" / name, out / name)


def build_both(ref, out, run=subprocess.run, take_lock=True, cur_tree=ROOT, base_tree=None):
    """HEAD in this checkout (cur_tree), ref in a detached worktree (base_tree); -> (cur dir, base dir)"""
    out = Path(out)
    base_tree = Path(base_tree or ROOT / "build" / "gate_s_tree")   # (not under out: out / "base" holds the base's
                                                # packages, and removing the worktree took them with it)
    env = toolchain_env()
    sha = git("rev-parse", "--verify", ref + "^{commit}")
    lk = None
    if take_lock:
        sys.path.insert(0, str(ROOT / "tools"))
        import lock
        lk = lock.Lock("HEAVY", who="gate_s", why=f"Gate S: FM-1/SMK packages, HEAD vs {sha[:8]}", wait_s=4 * 3600)
        lk.__enter__()
    try:
        build_tree(cur_tree, out / "cur", run, env)
        if base_tree.exists():
            git("worktree", "remove", "--force", str(base_tree), run=run)
        base_tree.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--detach", str(base_tree), sha, run=run)
        try:
            build_tree(base_tree, out / "base", run, env)
        finally:
            git("worktree", "remove", "--force", str(base_tree), run=run)
    finally:
        if lk is not None:
            lk.__exit__(None, None, None)
    return out / "cur", out / "base"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true", help="build HEAD and REF (heavy: the HEAVY lock, 8 builds)")
    ap.add_argument("--ref", default="HEAD^", help="the base commit (default: the parent, HEAD^)")
    ap.add_argument("--out", default=str(ROOT / "build" / "gate_s"), help="where the packages go")
    ap.add_argument("--cur", help="a directory of packages built at HEAD")
    ap.add_argument("--base", help="a directory of packages built at the base")
    a = ap.parse_args(argv)
    if a.build:
        cur, base = build_both(a.ref, a.out)
    elif a.cur and a.base:
        cur, base = a.cur, a.base
    else:
        ap.error("--build, or --cur DIR and --base DIR")
    text, ok = report(compare(cur, base))
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
