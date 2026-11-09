#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The three factory preset lists against presets.c: the FM-1's (firmware/src/presets.c, the source), the Move's and
the plugin's, by name and by every parameter's value after the load.

  F01-F19  presets.c's PR_FNAME / PR_FDATA (each record over params.c's defaults): the Move and the plugin compile
           presets.c itself, so a new or changed factory preset reaches them with the build; this proves it did
  F20-F57  the Move's variants (move/move_presets.h, tools/gen_move_presets.py): each B / C record as
           move/presets/proposed_records.txt has it (params.c's later ids at their defaults), the names NAMES; and
           every A record there must be presets.c's (tools/gen_move_presets.py --sync carries a change over)

The Move's list: build/move/t/test_bridge presets build/move/frodhes/dsp.so (in the Move image on macOS; the module's
16 voices and HQ path are its own and not compared: only the preset list). The plugin's:
build/plugin/frp_clap_render <the CLAP> --presets N (an empty FRODHES_PRESET_DIR: the factory list only).

  tools/preset_lists.py [--plugin CLAP] [--no-move] [--no-plugin]     exit 1 on any difference
"""
import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_move_presets as gp  # noqa: E402


def param_ids():
    """params.c's ids in order (the enum: P_TYPE .. P_COUNT)"""
    s = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    enum = re.search(r"enum \{ (P_TYPE.*?)P_COUNT \};", s, re.S).group(1)
    return re.findall(r"\b(P_[A-Z0-9_]+)\b", re.sub(r"/\*.*?\*/", "", enum, flags=re.S))


def expected():
    """[(label "F01 STAGE 77", [value per id])] for F01-F57, from presets.c and the Move's records; and the problems
    of the records' A against presets.c"""
    ids = param_ids()
    recs, rows = gp.presets_c()
    s = open(os.path.join(ROOT, "firmware", "src", "presets.c")).read()
    names = re.findall(r'"([^"]*)"', re.search(r"PR_FNAME\[PR_NF\] = \{(.*?)\};", s, re.S).group(1))
    out, bad = [], []
    rec = gp.records()
    for k, (nm, r) in enumerate(zip(names, recs)):
        full = [r.get(n, rows[n][0]) for n in ids]
        out.append(("F%02d %s" % (k + 1, nm), full))
        a = {n: v for n, _, v in rec.get(("F%02d" % (k + 1), "A"), ("", []))[1]}
        diff = [n for n in a if a[n] != r.get(n, rows[n][0])]
        if not a or diff:
            bad.append("records F%02d A: %s against presets.c (tools/gen_move_presets.py --sync)"
                       % (k + 1, ", ".join(diff[:6]) if a else "missing"))
    for num, name, _, base, v, pairs in gp.table():
        got = {n: x for n, _, x in pairs}
        out.append(("F%02d %s" % (num, name), [got.get(n, rows[n][0]) for n in ids]))
    return ids, out, bad


def parse(text):
    """{label index order: (label, [values])} from "preset ..." / "pv ..." lines; label "Fxx NAME" """
    names, vals = {}, {}
    for ln in text.splitlines():
        t = ln.split()
        if t[:1] == ["pv"]:
            vals[int(t[1])] = [int(x) for x in t[2:]]
        elif t[:1] == ["preset"]:
            names[int(t[1])] = t
    return names, vals


def compare(what, got, want, ids):
    """[problems]: got [(label, values)] in list order against want"""
    bad = []
    if len(got) != len(want):
        bad.append("%s: %d factory presets, presets.c + the variants give %d" % (what, len(got), len(want)))
    for (gl, gv), (wl, wv) in zip(got, want):
        if gl != wl:
            bad.append("%s: %r where presets.c says %r" % (what, gl, wl))
            continue
        d = [(ids[i], gv[i] if i < len(gv) else None, w) for i, w in enumerate(wv) if i >= len(gv) or gv[i] != w]
        if d:
            bad.append("%s %s: %s" % (what, wl, ", ".join("%s %s (want %d)" % x for x in d[:6])))
    return bad


def plugin_list(clap, n):
    exe = os.path.join(ROOT, "build", "plugin", "frp_clap_render")
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([exe, clap, "--presets", str(n)], capture_output=True, text=True,
                           env=dict(os.environ, FRODHES_PRESET_DIR=d))
    if r.returncode:
        raise SystemExit("plugin: %s --presets failed: %s" % (exe, (r.stdout + r.stderr)[-800:]))
    names, vals = parse(r.stdout)
    return [("%s %s" % (names[i][2], " ".join(names[i][3:])), vals[i][:n]) for i in sorted(names)]


def move_list(n):
    inner = ("make -s move-tools-native >/dev/null 2>&1 && build/move/t/test_bridge presets build/move/frodhes/dsp.so")
    if platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64"):
        cmd = ["sh", "-c", inner]
    elif shutil.which("docker"):
        cmd = ["docker", "run", "--rm", "--cpus=2", "--platform", "linux/arm64", "-v", ROOT + ":/src", "-w", "/src",
               "frodhes-move:bookworm", "sh", "-c", inner]
    else:
        raise SystemExit("move: no Linux arm64 and no Docker (the frodhes-move:bookworm image)")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("move: test_bridge presets failed: %s" % (r.stdout + r.stderr)[-800:])
    names, vals = parse(r.stdout)
    # (the Move's list: F01-F19 at 0-18, the user's U01-U16 at 19-34, F20-F57 from 35)
    return [(" ".join(names[i][3:]), vals[i][:n]) for i in sorted(names) if i < 19 or i >= 35]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plugin", default=os.path.join(ROOT, "build", "plugin", "bundles", "Frodhes.clap"))
    ap.add_argument("--no-plugin", action="store_true")
    ap.add_argument("--no-move", action="store_true")
    a = ap.parse_args()
    ids, want, bad = expected()
    print("presets.c: %d factory presets, the Move's variants %d, %d parameters each"
          % (19, len(want) - 19, len(ids)))
    print(("FAIL  " if bad else "ok    ") + "records: every A is presets.c's" + ("" if not bad else ": " + bad[0]))
    for what, get in (("plugin", None if a.no_plugin else lambda: plugin_list(a.plugin, len(ids))),
                      ("move", None if a.no_move else lambda: move_list(len(ids)))):
        if get is None:
            print("skip  %s" % what)
            continue
        b = compare(what, get(), want, ids)
        print(("FAIL  " if b else "ok    ") + "%s: %d presets, names and values as presets.c / the variants%s"
              % (what, len(want), "" if not b else ":\n        " + "\n        ".join(b[:10])))
        bad += b
    print("preset_lists: %s" % ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
