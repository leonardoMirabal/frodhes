#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""One command for a whole Frodhes release (AGENTS.md section 9), stopping at the first failure.

    tools/release.py [patch|minor|X.Y.Z] [--dry-run] [--no-device] [--no-emulator] [--yes]

 1 version       the latest v* tag bumped (patch by default, or minor), or X.Y.Z; refused if the tag exists
                 locally or on origin
 2 preconditions on main, no tracked changes (the release notes excepted), not behind origin/main (after a fetch),
                 docs/releases/vX.Y.Z.md present without a DRAFT banner (missing: a draft is written, then stop)
 3 host          make test, make pytest, tools/gate_r.py, ./build.sh --release X.Y.Z (its sha256)
 4 FM-1          (--no-device skips it) the device lock, install, hil_cpu --worst (Rhodes and VIBES: <= 4500 us,
                 nothing shed), hil_scan (the release set, then vibes_vel), hil_jack on both engines (SNR; the jack
                 must be connected)
 5 docs          README / MANUAL download, "Latest release" and "Version" lines, every screenshot's ?v=, then
                 tests/py/test_docs_vs_code.py and the release commit
 6 confirm       what will be pushed, then "Publish X.Y.Z? [y/N]" (--yes skips the prompt). --dry-run stops here.
 7 publish       push main, wait for ci on that commit (every job green), tag and push the tag, wait for the
                 release workflow, download the asset and compare its sha256 with the local build
 8 emulator      (--no-emulator skips it) web/build.sh, the Playwright selftest (Chromium + WebKit, 1280 and 390 px),
                 web/dist into the gh-pages worktree build/pages, push, compare the live files' sha256
 9 verify        the release page lists the asset; README on GitHub at the tag links the ?v=X.Y.Z pictures

--dry-run runs every local check and test (2-5 report, but write and commit nothing) and never pushes, tags or
publishes. Each step prints PASS / FAIL with its evidence; the summary lists every step: done, skipped (why), failed.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "leonardoMirabal/frodhes"
PAGES_URL = "https://leonardomirabal.github.io/frodhes/"
PY = os.path.join(".local", "venv", "bin", "python")
CPU_LIMIT_US = 4500
JACK_PEAK_MIN_DB = -27.0          # the loud chord peaks near -15 dBFS on the jack; far below = not connected
TRAILER = ("Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01Dz8MB3aMWQ3NXyFVXtBmgi")
STEPS = ["version", "preconditions", "host", "FM-1", "docs", "confirm", "publish", "emulator", "verify"]
SCAN_SET = "silence,latency,keyboard,velocity,sustain,held_tail,stress,presets"


class Stop(Exception):
    pass


# ---- the shell (tests replace sh) ----

def sh(cmd, check=True, input=None, timeout=None, quiet=False):
    """run cmd (a list, or a string through the shell) in ROOT; returns (code, stdout+stderr)"""
    if not quiet:
        print("   $ " + (cmd if isinstance(cmd, str) else " ".join(cmd)), flush=True)
    p = subprocess.run(cmd, cwd=ROOT, shell=isinstance(cmd, str), input=input, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if check and p.returncode:
        raise Stop(f"`{cmd if isinstance(cmd, str) else ' '.join(cmd)}` exited {p.returncode}:\n" + tail(p.stdout))
    return p.returncode, p.stdout


def out(cmd, **kw):
    return sh(cmd, quiet=True, **kw)[1].strip()


def tail(text, n=25):
    return "\n".join(text.rstrip().splitlines()[-n:])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- version logic ----

def parse_version(s):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", s.strip())
    if not m:
        raise ValueError(f"not a version X.Y.Z: {s!r}")
    return tuple(int(x) for x in m.groups())


def next_version(latest, kind):
    """latest 'vA.B.C' (or None), kind 'patch' | 'minor' | 'X.Y.Z' -> 'X.Y.Z'"""
    cur = parse_version(latest) if latest else (0, 0, 0)
    if kind == "patch":
        v = (cur[0], cur[1], cur[2] + 1)
    elif kind == "minor":
        v = (cur[0], cur[1] + 1, 0)
    else:
        v = parse_version(kind)
        if latest and v <= cur:
            raise ValueError(f"{kind} is not after the latest release {latest}")
    return "%d.%d.%d" % v


def bump_docs(text, ver):
    """README / MANUAL text with the download, 'Latest release', 'Version' lines and every ?v= at ver"""
    text = re.sub(r"frodhes-\d+\.\d+\.\d+\.fwsc", f"frodhes-{ver}.fwsc", text)
    text = re.sub(r"(Latest release: \[Frodhes )\d+\.\d+\.\d+", rf"\g<1>{ver}", text)
    text = re.sub(r"(\bVersion )\d+\.\d+\.\d+", rf"\g<1>{ver}", text)
    text = re.sub(r"(\.png\?v=)[0-9A-Za-z.\-]+", rf"\g<1>{ver}", text)
    return text


def parse_args(argv):
    ap = argparse.ArgumentParser(description="the whole Frodhes release, stopping at the first failure")
    ap.add_argument("version", nargs="?", default="patch", help="patch (default), minor or X.Y.Z")
    ap.add_argument("--dry-run", action="store_true", help="every local check; no commit, push, tag or publish")
    ap.add_argument("--no-device", action="store_true", help="skip the FM-1 tests (printed loudly)")
    ap.add_argument("--no-emulator", action="store_true", help="skip publishing the web emulator")
    ap.add_argument("--yes", action="store_true", help="publish without the prompt")
    a = ap.parse_args(argv)
    if a.version not in ("patch", "minor"):
        try:
            parse_version(a.version)
        except ValueError as e:
            ap.error(str(e))
    return a


# ---- the steps ----

class Release:
    def __init__(self, a):
        self.a = a
        self.results = {}           # step number -> (status, evidence)
        self.ver = self.prev = self.sha = self.head = None

    def log(self, msg):
        print("   " + msg.replace("\n", "\n   "), flush=True)

    # 1
    def s_version(self):
        sh(["git", "fetch", "--tags", "origin"], check=False)
        tags = out(["git", "tag", "--sort=-v:refname", "--list", "v*"]).split()
        self.prev = tags[0] if tags else None
        try:
            self.ver = next_version(self.prev, self.a.version)
        except ValueError as e:
            raise Stop(str(e))
        tag = "v" + self.ver
        if out(["git", "tag", "--list", tag]):
            raise Stop(f"{tag} already exists locally")
        code, remote = sh(["git", "ls-remote", "--tags", "origin", "refs/tags/" + tag], check=False, quiet=True)
        if code:
            raise Stop("git ls-remote origin failed:\n" + tail(remote))
        if remote.strip():
            raise Stop(f"{tag} already exists on origin: {remote.strip()}")
        return f"latest tag {self.prev}, {self.a.version} -> {self.ver}; {tag} exists neither locally nor on origin"

    # 2
    def s_preconditions(self):
        notes = f"docs/releases/v{self.ver}.md"
        problems, ev = [], []
        branch = out(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if branch != "main":
            problems.append(f"on branch {branch!r}, not main")
        dirty = [l for l in out(["git", "status", "--porcelain", "--untracked-files=no"]).splitlines()
                 if l[3:].strip() != notes]
        if dirty:
            problems.append("tracked changes:\n" + "\n".join(dirty[:15]))
        sh(["git", "fetch", "origin", "main"])
        self.head = out(["git", "rev-parse", "HEAD"])
        behind = out(["git", "rev-list", "--count", "HEAD..origin/main"])
        ahead = out(["git", "rev-list", "--count", "origin/main..HEAD"])
        if behind != "0":
            problems.append(f"{behind} commit(s) behind origin/main (pull first)")
        ev.append(f"branch {branch}, HEAD {self.head[:10]}, {ahead} ahead / {behind} behind origin/main, "
                  f"{len(dirty)} tracked change(s)")
        path = os.path.join(ROOT, notes)
        if not os.path.exists(path):
            log = out(["git", "log", f"{self.prev}..HEAD", "--oneline", "--no-merges"]) if self.prev else ""
            if self.a.dry_run:
                problems.append(f"{notes} missing (a real run writes a draft from {len(log.splitlines())} commits "
                                "and stops)")
            else:
                write_draft(path, self.ver, self.prev, log)
                raise Stop(f"{notes} was missing: a draft is written from `git log {self.prev}..HEAD`; "
                           "edit it, remove the DRAFT line, run again")
        elif re.search(r"^\W*DRAFT", open(path, encoding="utf-8").read(), re.M):
            problems.append(f"{notes} still has its DRAFT banner (edit it, remove the line)")
        else:
            ev.append(f"{notes} present, no DRAFT banner")
        if problems:
            msg = "\n".join(ev + ["PROBLEM: " + p for p in problems])
            if self.a.dry_run:
                return "DRY-RUN, a real run stops here:\n" + msg, "warn"
            raise Stop(msg)
        return "\n".join(ev)

    # 3
    def s_host(self):
        ev = []
        for cmd in (["make", "test"], ["make", "pytest", "PY=" + PY], [PY, "tools/gate_r.py"],
                    ["./build.sh", "--release", self.ver]):
            t0 = time.time()
            _, o = sh(cmd, timeout=3600)
            last = [l for l in o.strip().splitlines() if l.strip()][-1:] or [""]
            ev.append(f"{' '.join(cmd)}: exit 0 in {time.time() - t0:.0f} s ({last[0][:100]})")
        fw = os.path.join(ROOT, "build", f"frodhes-{self.ver}.fwsc")
        if not os.path.exists(fw):
            raise Stop(f"{fw} was not built")
        self.sha = sha256(fw)
        ev.append(f"build/frodhes-{self.ver}.fwsc {os.path.getsize(fw)} B sha256 {self.sha}")
        return "\n".join(ev)

    # 4
    def s_device(self):
        lock = os.path.join(ROOT, "build", "hil", "DEVICE.lockdir")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        try:
            os.mkdir(lock)
        except FileExistsError:
            who = open(os.path.join(lock, "owner")).read() if os.path.exists(os.path.join(lock, "owner")) else "?"
            raise Stop(f"the FM-1 is locked ({lock}): {who.strip()}")
        ev = []
        try:
            with open(os.path.join(lock, "owner"), "w") as f:
                f.write(f"tools/release.py {self.ver} pid {os.getpid()} since {time.ctime()}\n")
            fw = f"build/frodhes-{self.ver}.fwsc"
            _, o = sh(f"echo y | {PY} tools/fm1_install.py {fw}", timeout=600)
            ev.append(f"installed {fw}: {tail(o, 1)}")
            for eng in ("rhodes", "vibes"):
                cmd = [PY, "tools/hil_cpu.py", "--worst"] + (["--engine", "vibes"] if eng == "vibes" else [])
                _, o = sh(cmd, timeout=900)
                ev.append(check_cpu(eng, o))
            _, o = sh([PY, "tools/hil_scan.py", "--only", SCAN_SET], timeout=3600)
            ev.append(f"hil_scan {SCAN_SET}: exit 0 ({tail(o, 1)})")
            _, o = sh([PY, "tools/hil_scan.py", "--only", "vibes_vel", "--source", "both"], timeout=3600)
            ev.append(f"hil_scan vibes_vel --source both: exit 0 ({tail(o, 1)})")
            for eng in ("rhodes", "vibes"):
                sh([PY, "tools/hil_jack.py", "--engine", eng], timeout=900)
                rep = json.load(open(os.path.join(ROOT, "build", "hil",
                                                  "jack.json" if eng == "rhodes" else "jack-vibes.json")))
                ev.append(check_jack(eng, rep))
        finally:
            shutil.rmtree(lock, ignore_errors=True)
        return "\n".join(ev)

    # 5
    def s_docs(self):
        ev, changed = [], []
        for name in ("README.md", "docs/MANUAL.md"):
            p = os.path.join(ROOT, name)
            old = open(p, encoding="utf-8").read()
            new = bump_docs(old, self.ver)
            n = sum(a != b for a, b in zip(old.splitlines(), new.splitlines()))
            ev.append(f"{name}: {n} line(s) {'would change' if self.a.dry_run else 'changed'} to {self.ver}")
            if new != old:
                changed.append(name)
                if not self.a.dry_run:
                    open(p, "w", encoding="utf-8").write(new)
        _, o = sh([PY, "-m", "pytest", "-q", "tests/py/test_docs_vs_code.py"])
        ev.append(f"test_docs_vs_code: {tail(o, 1)}")
        notes = f"docs/releases/v{self.ver}.md"
        if self.a.dry_run:
            ev.append(f"DRY-RUN: no commit (a real run commits {', '.join(changed + [notes])})")
            return "\n".join(ev)
        sh(["git", "add", *changed, notes])
        body = "\n".join(f"- {s}: {e.splitlines()[0]}" for s, (st, e) in sorted(self.results.items())
                         if st in ("done", "skipped"))
        dev = ("FM-1: hil_cpu, hil_scan, hil_jack with the release build installed" if not self.a.no_device
               else "FM-1: NOT tested (--no-device)")
        msg = (f"Release {self.ver}: README / MANUAL point at frodhes-{self.ver}.fwsc, the screenshots at ?v={self.ver}"
               f"\n\nTested by tools/release.py: host (make test, make pytest, gate_r, the release build sha256 "
               f"{self.sha}); {dev}.\n{body}\n\n{TRAILER}\n")
        if out(["git", "diff", "--cached", "--name-only"]):
            sh(["git", "commit", "-q", "-F", "-"], input=msg)
        self.head = out(["git", "rev-parse", "HEAD"])
        ev.append(f"commit {self.head[:10]} {out(['git', 'log', '-1', '--format=%s'])}")
        return "\n".join(ev)

    # 6
    def s_confirm(self):
        commits = out(["git", "log", "--oneline", "origin/main..HEAD"]) or "(none)"
        files = out(["git", "diff", "--stat", "origin/main..HEAD"]) or "(none)"
        self.log(f"to push to origin main:\n{commits}\n{files}\nthen the tag v{self.ver} at {self.head[:10]}")
        if self.a.dry_run:
            return "DRY-RUN: stopped before any push. A real run would now ask \"Publish %s? [y/N]\", then 7 push " \
                   "main + wait for ci, tag v%s + wait for the release workflow, compare the asset's sha256; " \
                   "8 the emulator%s; 9 verify on GitHub." % (self.ver, self.ver,
                                                             " (skipped: --no-emulator)" if self.a.no_emulator else "")
        if not self.a.yes:
            ans = input(f"Publish {self.ver}? [y/N] ").strip().lower()
            if ans != "y":
                raise Stop("not confirmed: nothing pushed (the release commit stays local)")
            return f"confirmed by the user at the prompt; {len(commits.splitlines())} commit(s)"
        return f"--yes (no prompt); {len(commits.splitlines())} commit(s)"

    # 7
    def s_publish(self):
        tag = "v" + self.ver
        ev = []
        sh(["git", "push", "origin", "HEAD:main"])
        ev.append(f"pushed main {self.head[:10]}")
        ev.append(wait_run("ci.yml", self.head))
        sh(["git", "tag", "-a", tag, "-m", f"Frodhes {self.ver}"])
        sh(["git", "push", "origin", tag])
        ev.append(f"tag {tag} pushed")
        # ci just passed on this commit, so release.yml's ci-gate skips the fallback tests (its generators-macos job
        # is "skipped"): toolchain + build + publish only
        ev.append(wait_run("release.yml", self.head, ok=("success", "skipped")))
        d = tempfile.mkdtemp(prefix="frodhes-rel-", dir=os.path.join(ROOT, "build"))
        try:
            sh(["gh", "release", "download", tag, "-R", REPO, "-p", f"frodhes-{self.ver}.fwsc", "-D", d])
            got = sha256(os.path.join(d, f"frodhes-{self.ver}.fwsc"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if got != self.sha:
            raise Stop(f"the published asset's sha256 {got} differs from the local build's {self.sha}")
        ev.append(f"release asset sha256 {got} = the local build")
        return "\n".join(ev)

    # 8
    def s_emulator(self):
        ev = []
        sh(["sh", "web/build.sh"], timeout=1800)
        dist = os.path.join(ROOT, "web", "dist")
        ev.append(web_selftest(dist))
        pages = os.path.join(ROOT, "build", "pages")
        if out(["git", "-C", pages, "rev-parse", "--abbrev-ref", "HEAD"]) != "gh-pages":
            raise Stop(f"{pages} is not the gh-pages worktree")
        local = {}
        for f in sorted(os.listdir(dist)):
            shutil.copy2(os.path.join(dist, f), os.path.join(pages, f))
            local[f] = sha256(os.path.join(dist, f))
        sh(["git", "-C", pages, "add", "-A"])
        if out(["git", "-C", pages, "status", "--porcelain"]):
            sh(["git", "-C", pages, "commit", "-q", "-F", "-"],
               input=f"Emulator for Frodhes {self.ver} (main {self.head[:10]})\n\n{TRAILER}\n")
        sh(["git", "-C", pages, "push", "origin", "gh-pages"])
        ev.append(f"gh-pages {out(['git', '-C', pages, 'rev-parse', '--short', 'HEAD'])} pushed ({len(local)} files)")
        for i in range(24):                      # Pages deploys in a minute or two
            bad = [f for f, h in local.items() if fetch_sha(PAGES_URL + f) != h]
            if not bad:
                ev.append(f"live {PAGES_URL}: every file's sha256 = the local one ({', '.join(local)})")
                return "\n".join(ev)
            self.log(f"live differs ({', '.join(bad)}), retry {i + 1}/24 in 15 s")
            time.sleep(15)
        raise Stop(f"the live emulator still differs after 6 minutes: {', '.join(bad)}")

    # 9
    def s_verify(self):
        tag = "v" + self.ver
        r = json.loads(out(["gh", "release", "view", tag, "-R", REPO, "--json", "assets,url,isDraft"]))
        names = [x["name"] for x in r["assets"]]
        if f"frodhes-{self.ver}.fwsc" not in names or r.get("isDraft"):
            raise Stop(f"{r['url']}: assets {names}, draft {r.get('isDraft')}")
        readme = out(["gh", "api", f"repos/{REPO}/contents/README.md?ref={tag}", "-H", "Accept: application/vnd.github.raw"])
        vs = set(re.findall(r"\.png\?v=([0-9A-Za-z.\-]+)", readme))
        if vs != {self.ver} or f"frodhes-{self.ver}.fwsc" not in readme:
            raise Stop(f"README on GitHub at {tag}: pictures ?v={sorted(vs)}, download line "
                       f"{'ok' if f'frodhes-{self.ver}.fwsc' in readme else 'missing'}")
        return f"{r['url']} lists {', '.join(names)}; README at {tag} links ?v={self.ver} only, frodhes-{self.ver}.fwsc"

    # ---- the driver ----
    def run(self):
        plan = [self.s_version, self.s_preconditions, self.s_host, self.s_device, self.s_docs, self.s_confirm,
                self.s_publish, self.s_emulator, self.s_verify]
        if self.a.no_device:
            print("\n" + "!" * 78 + "\n!!  --no-device: the FM-1 is NOT tested in this run (step 4 skipped)\n"
                  + "!" * 78, flush=True)
        failed = False
        for i, fn in enumerate(plan, 1):
            name = STEPS[i - 1]
            skip = ("--no-device" if i == 4 and self.a.no_device else "--no-emulator" if i == 8 and self.a.no_emulator
                    else "--dry-run" if i > 6 and self.a.dry_run else None)
            if failed:
                self.results[i] = ("not run", "an earlier step failed")
                continue
            if skip:
                self.results[i] = ("skipped", skip)
                print(f"\n[{i}/9] {name}: SKIPPED ({skip})", flush=True)
                continue
            print(f"\n[{i}/9] {name}", flush=True)
            try:
                r = fn()
                ev, st = r if isinstance(r, tuple) else (r, "done")
                self.results[i] = (st, ev)
                self.log(ev)
                print(f"[{i}/9] {name}: {'PASS' if st == 'done' else 'WARN (dry-run)'}", flush=True)
            except (Stop, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as e:
                self.results[i] = ("FAILED", str(e))
                self.log(str(e))
                print(f"[{i}/9] {name}: FAIL", flush=True)
                failed = True
        self.summary()
        return 1 if failed else 0

    def summary(self):
        print("\n" + "=" * 78 + f"\nFrodhes release {self.ver or '?'}{' (DRY-RUN)' if self.a.dry_run else ''}: summary")
        for i, name in enumerate(STEPS, 1):
            st, ev = self.results.get(i, ("not run", ""))
            lines = ev.splitlines() or [""]
            if st in ("warn", "FAILED"):              # the problems, not the banner
                lines = [l for l in lines if l.startswith("PROBLEM") or l.startswith("`")] or lines
            print(f" {i}  {name:14s} {st:8s} {lines[0][:120]}")
            for l in lines[1:4] if st in ("warn", "FAILED") else []:
                print(f"    {'':14s} {'':8s} {l[:120]}")
        if self.a.no_device:
            print(" !! the FM-1 was NOT tested (--no-device)")
        print("=" * 78)


def check_cpu(eng, text):
    m = re.search(r"worst (\d+) us", text)
    if not m:
        raise Stop(f"hil_cpu {eng}: no 'worst' line:\n" + tail(text))
    worst = int(m.group(1))
    shed = [int(x) for x in re.findall(r"voices shed (\d+)", text)]
    if worst > CPU_LIMIT_US or any(shed):
        raise Stop(f"hil_cpu {eng}: worst {worst} us (limit {CPU_LIMIT_US}), voices shed {shed}")
    return f"hil_cpu --worst {eng}: worst {worst} us <= {CPU_LIMIT_US}, voices shed {sum(shed)} in {len(shed)} scenarios"


def check_jack(eng, rep):
    peak = rep["loud"]["jack_peak_db"]
    if peak < JACK_PEAK_MIN_DB:
        raise Stop(f"hil_jack {eng}: the loud chord peaks at {peak:.1f} dBFS on the jack (usually about -15): "
                   "is the jack connected to the interface?")
    return f"hil_jack {eng}: SNR {rep['snr_db']:.1f} dB, loud chord jack peak {peak:.1f} dBFS"


def wait_run(workflow, sha, appear_s=300, ok=("success",)):
    """wait for workflow's run on commit sha; Stop with the failing jobs' log lines unless it succeeds and every job's
    conclusion is in ok"""
    t0, run = time.time(), None
    while time.time() - t0 < appear_s:
        rows = json.loads(out(["gh", "run", "list", "-R", REPO, "--workflow", workflow, "--commit", sha,
                               "--json", "databaseId,headSha,status,conclusion,event"]) or "[]")
        rows = [r for r in rows if r["headSha"] == sha]
        if rows:
            run = rows[0]
            break
        time.sleep(10)
    if not run:
        raise Stop(f"no {workflow} run on {sha[:10]} after {appear_s} s")
    rid = str(run["databaseId"])
    code, _ = sh(["gh", "run", "watch", rid, "-R", REPO, "--exit-status", "--interval", "20"], check=False,
                 timeout=7200)
    v = json.loads(out(["gh", "run", "view", rid, "-R", REPO, "--json", "conclusion,jobs,url"]))
    jobs = [(j["name"], j["conclusion"]) for j in v["jobs"]]
    if code or v["conclusion"] != "success" or any(c not in ok for _, c in jobs):
        _, logs = sh(["gh", "run", "view", rid, "-R", REPO, "--log-failed"], check=False, quiet=True)
        errs = [l for l in logs.splitlines() if re.search(r"error|fail|assert", l, re.I)][-30:]
        raise Stop(f"{workflow} {v['url']}: {v['conclusion']}, jobs {jobs}\n" + "\n".join(errs or [tail(logs)]))
    return f"{workflow} {v['url']}: success, jobs {', '.join(f'{n} {c}' for n, c in jobs)}"


def fetch_sha(url):
    try:
        with urllib.request.urlopen(f"{url}?nocache={time.time():.0f}", timeout=30) as r:
            return hashlib.sha256(r.read()).hexdigest()
    except OSError:
        return None


SELFTEST_JS = r"""
import json, sys
from playwright.sync_api import sync_playwright
url, res = sys.argv[1], []
with sync_playwright() as p:
    for bname in ("chromium", "webkit"):
        b = getattr(p, bname).launch()
        for w in (1280, 390):
            pg = b.new_page(viewport={"width": w, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(url + "?selftest")
            pg.wait_for_function("document.title.startsWith('SELFTEST')", timeout=30000)
            sw = pg.evaluate("document.documentElement.scrollWidth")
            res.append(dict(browser=bname, width=w, title=pg.title(), scroll=sw, errors=errs))
            pg.close()
        b.close()
print(json.dumps(res))
"""


def web_selftest(dist):
    """the ?selftest page in Chromium and WebKit at 1280 and 390 px, served from dist; Stop on any failure"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", dist],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=os.path.join(ROOT, ".local", "playwright"))
        p = subprocess.run([os.path.join(ROOT, PY), "-c", SELFTEST_JS, f"http://127.0.0.1:{port}/"], cwd=ROOT,
                           env=env, text=True, capture_output=True, timeout=300)
        if p.returncode:
            raise Stop("Playwright selftest failed:\n" + tail(p.stdout + p.stderr))
        res = json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
    bad, ok = [], []
    for r in res:
        m = re.search(r"rms=(-?[\d.]+)dB screen=(\d+)px", r["title"])
        tag = f"{r['browser']} {r['width']}px"
        if not m or float(m.group(1)) < -60 or int(m.group(2)) == 0 or r["errors"] or r["scroll"] > r["width"]:
            bad.append(f"{tag}: {r['title']!r} scrollWidth {r['scroll']} errors {r['errors']}")
        else:
            ok.append(f"{tag} rms {m.group(1)} dB, {m.group(2)} px lit")
    if bad:
        raise Stop("emulator selftest: " + "; ".join(bad))
    return "selftest " + "; ".join(ok)


def write_draft(path, ver, prev, log):
    lines = "\n".join(f"- {l.split(' ', 1)[1]}" for l in log.splitlines() if " " in l) or "- (nothing yet)"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"> **DRAFT, not released yet.** Edit these notes, then delete this line.\n\n"
                f"**Frodhes {ver}**: a physically modelled Fender Rhodes and vibraphone for the M-VAVE FM-1.\n\n"
                f"**Download:** `frodhes-{ver}.fwsc` below. **Try it first in your browser:** {PAGES_URL}\n\n"
                f"## What's new since {prev}\n\n{lines}\n\n## Install\n\n"
                f"`python3 tools/fm1_install.py frodhes-{ver}.fwsc` (see the manual, section 1, and the rescue).\n")


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    return Release(a).run()


if __name__ == "__main__":
    sys.exit(main())
