#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""One command for a whole Frodhes release (AGENTS.md section 9), stopping at the first failure.

    tools/release.py [patch|minor|X.Y.Z] [--dry-run] [--no-device [--device-done JSON]] [--full] [--no-emulator] [--yes]
    tools/release.py X.Y.Z --after-merge [--no-emulator] [--yes]   after the release PR's merge: tag and publish
    tools/release.py [X.Y.Z] --verify-only      step 9 alone, against a published release (read-only)
    tools/release.py --web-selftest [DIST [X.Y.Z]]   the emulator selftest alone (ci.yml / release.yml, job web)

The version lives in one place, the GitHub release (its tag, page, notes and assets): README / MANUAL name no version
(they link releases/latest and releases/latest/download/frodhes.fwsc, every picture's ?v= is its file's hash:
tools/doc_links.py), and the notes are assembled from the fragments each change adds in docs/changes/
(tools/changes.py). Nothing is bumped by hand or by this tool.

 1 version       the latest v* tag bumped (patch by default, or minor), or X.Y.Z; refused if the tag exists
                 locally or on origin
 2 preconditions on main, no tracked changes, not behind origin/main (after a fetch), the fragments in docs/changes/
                 well formed (at least one) and no docs/releases/vX.Y.Z.md written by hand (one already committed with
                 no fragments left: a re-run after step 5, accepted)
 3 host          make test, make pytest, tools/gate_r.py, ./build.sh --release X.Y.Z (its sha256)
 4 FM-1          (--no-device skips it and lists its commands) the tiers this release needs, from
                 `git diff <previous tag>..HEAD` through tools/test_map.py (printed with the reason): T1 on every
                 release, T2 when an engine / the DSP changed (or --full). The device lock, install, then
                 T1 must-pass   hil_scan --tier must (the real-use scenarios, the firmware's counters gated; S1 is
                                stress_max, tools/hil_crackle.py, at 48 and 44.1 kHz)
                 T2 full scans  hil_cpu --worst (Rhodes, VIBES and MARIMBA: <= 4500 us, nothing shed), hil_scan (the
                                release set, then vibes_vel, then --marimba: MARIMBA_MUST), hil_jack on every engine
                                (SNR; the jack must be connected; MARIMBA: the A5 tails and the idle floor too),
                                vib_sampleback --engine marimba (Gate VD-M, every key C2..C7)
                 --no-device --device-done JSON: those runs were made on this exact build; its "tiers" must cover the
                 needed ones (checked here, as soon as the build's sha256 is known)
 5 docs          tools/doc_links.py's check (each picture's ?v= = its file's hash, no version named in README /
                 MANUAL), tests/py/test_docs_vs_code.py, test_doc_links.py, test_changes.py, test_manual_shots.py
                 (the screenshots = what the code draws); the fragments assembled into docs/releases/vX.Y.Z.md (shown in
                 full), then the release commit: the notes added, the fragments removed
 6 confirm       what will be pushed, then "Publish X.Y.Z? [y/N]" (--yes skips the prompt). --dry-run stops here.
 7 publish       main is protected (enforce_admins: nobody pushes to it, an admin neither), so the release commit
                 goes in through a pull request: a normal run pushes nothing and stops here with the commands (push
                 the branch release-X.Y.Z, open the PR, merge it once every CI job is green; exit 3, "handed off").
                 --after-merge, on main = origin/main with the release commit merged (its git tree, so nothing else
                 came in): steps 3-5 are the release build alone, its sha256 = the one the release commit records;
                 then wait for ci on main (or reuse the PR's green run on the same tree), tag and push the tag, wait
                 for the release workflow, download the asset and compare its sha256 with the local build
 8 emulator      (--no-emulator skips it) release.yml already built web/dist from the tag, ran the Playwright selftest
                 (Chromium + WebKit, 1280 and 390 px) and deployed it to gh-pages before publishing the release
                 (tools/deploy_pages.sh): here only the live files' sha256 = gh-pages's, and gh-pages's index.html /
                 app.js = the tag's web/. Fallback, for a release.yml without that job: web/build.sh, the selftest,
                 web/dist into the gh-pages worktree build/pages (tools/deploy_pages.sh), push, the live sha256
 9 verify        the release page (not a draft) lists frodhes-X.Y.Z.fwsc, frodhes.fwsc and the SMK-37 packages
                 (SMK_ASSETS, marked experimental);
                 releases/latest/download/frodhes.fwsc redirects to vX.Y.Z and is byte for byte the build (with
                 --verify-only: the versioned asset); README on GitHub at the tag names no version and links it

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

import changes
import doc_links
import lock
import test_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "leonardoMirabal/frodhes"
PAGES_URL = "https://leonardomirabal.github.io/frodhes/"
STABLE = "frodhes.fwsc"                       # release.yml uploads the package under this name too
# release.yml also builds and attaches the SMK-37 packages (./build.sh --board smk37 [--model elite|mke]), marked
# experimental (not tested on hardware): the published name -> the build's file (tools/build.py package())
SMK_ASSETS = {"frodhes-smk37-experimental.fwsc": "frodhes-{v}-smk37.fwsc",
              "frodhes-smk37-elite-experimental.fwsc": "frodhes-{v}-smk37-elite.fwsc",
              "frodhes-smk37-mke-experimental.fwsc": "frodhes-{v}-smk37-mke.fwsc"}
# and the Apache-2.0 text that goes with the 3 JieLi SDK files inside every .fwsc (Apache-2.0 section 4)
LICENCE_ASSET = "Apache-2.0.txt"
LATEST_DL = f"https://github.com/{REPO}/releases/latest/download/{STABLE}"
PY = os.path.join(".local", "venv", "bin", "python")
CPU_LIMIT_US = 4500
JACK_PEAK_MIN_DB = -27.0          # the loud chord peaks near -15 dBFS on the jack; far below = not connected
TRAILER = "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"   # (no session links: a public repo)
STEPS = ["version", "preconditions", "host", "FM-1", "docs", "confirm", "publish", "emulator", "verify"]
SCAN_SET = "silence,latency,keyboard,velocity,sustain,held_tail,stress,presets"
ENGINES = ("rhodes", "vibes", "marimba")
# hil_scan --marimba (every MARIMBA suite, USB and the jack) exits 1 on any FAIL row, so its report is judged here.
# A KNOWN row (hil_scan m_known: tools/marimba_scan.py KNOWN / KNOWN_CARDS, the user's decisions) never fails.
# As VIBES (the release runs vibes_vel, not vibes_jump / vibes_fx, whose stale effect limits fail on every version):
# the four voice suites must pass every row; the two effect suites (the on/off loudness steps, every FX control) are
# reported and gate only the rows below. MARIMBA_INFO: rows reported, never gating, with the reason.
MARIMBA_MUST = ("marimba_voice", "marimba_decay", "marimba_vel", "marimba_roll")
MARIMBA_FX = ("marimba_jump", "marimba_fx")
MARIMBA_FX_MUST = (r"^suite error$", r"^takes: ", r": clicks at the switches$", r"^reference drift$",
                   r"^FX bypass \(CC 111\)", r"^MARIMBA re ST77, ")
MARIMBA_INFO = {("marimba_vel", "keys C2..C7: level span"):
                "information, as the host scan's M1 'level span C2-C7' (tools/marimba_scan.py): a marimba is louder "
                "low than high (FM-1 scans 3 / 4: 8.6 dB); the neighbour step gates the curve"}


class Stop(Exception):
    pass


class Handoff(Exception):
    """step 7 without --after-merge: the release commit is ready, the PR is the user's (nothing pushed)"""


def pr_steps(ver, head):
    """what the user does between the two runs (main is protected: the release goes in through a PR)"""
    return (f"the release commit {head[:10]} is local and nothing was pushed: main is protected (enforce_admins), so it "
            f"goes in through a pull request. Next (the user, or the session with the user's OK):\n"
            f"  git push origin HEAD:refs/heads/release-{ver}\n"
            f"  gh pr create --base main --head release-{ver} --title \"Frodhes {ver}\" --body-file docs/releases/v{ver}.md\n"
            f"  every CI job green, then merge it (a merge commit or a fast-forward, not a squash: main's tree must be "
            f"this commit's)\n"
            f"  git fetch origin && git merge --ff-only origin/main\n"
            f"  tools/release.py {ver} --after-merge [--yes]      (tags main, waits for release.yml, verifies)")


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


def parse_args(argv):
    ap = argparse.ArgumentParser(description="the whole Frodhes release, stopping at the first failure")
    ap.add_argument("version", nargs="?", default="patch", help="patch (default), minor or X.Y.Z")
    ap.add_argument("--dry-run", action="store_true", help="every local check; no commit, push, tag or publish")
    ap.add_argument("--no-device", action="store_true", help="skip the FM-1 tests (printed loudly)")
    ap.add_argument("--full", action="store_true", help="the FM-1 stage runs T2 (the full scans) whatever changed")
    ap.add_argument("--device-done", metavar="JSON",
                    help="with --no-device: the FM-1 tests already ran on this exact build: {\"sha256\": the tested "
                         ".fwsc's, \"tiers\": [\"T1\", ...], \"runs\": [what ran]}; refused unless the sha256 "
                         "equals the release build's and the tiers cover the ones this release needs")
    ap.add_argument("--no-emulator", action="store_true", help="skip publishing the web emulator")
    ap.add_argument("--yes", action="store_true", help="publish without the prompt")
    ap.add_argument("--after-merge", action="store_true",
                    help="the release PR is merged: on main = origin/main, the release build's sha256 against the one "
                         "the release commit records, then tag, release.yml, verify (steps 4 and 5 were done before "
                         "the PR)")
    ap.add_argument("--verify-only", action="store_true",
                    help="only step 9 against a published release (X.Y.Z, or the latest tag); read-only")
    a = ap.parse_args(argv)
    if a.after_merge and (a.dry_run or a.verify_only or a.version in ("patch", "minor")):
        ap.error("--after-merge needs the version X.Y.Z (the release commit's), without --dry-run / --verify-only")
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
        self.ver = self.prev = self.sha = self.head = self.recorded_sha = None
        self.frags = []             # docs/changes/ (step 2)
        self.tmap = None            # the tiers this release needs (tiers(), from step 4)

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
        dirty = out(["git", "status", "--porcelain", "--untracked-files=no"]).splitlines()
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
        try:
            self.frags = changes.load(ROOT)
        except changes.Bad as e:
            self.frags = []
            problems.append(f"malformed fragment(s) in {changes.DIR}/ (tools/changes.py):\n{e}")
        else:
            names = ", ".join(f for f, _ in self.frags)
            if os.path.exists(os.path.join(ROOT, notes)):
                if self.frags:
                    problems.append(f"{notes} exists and {changes.DIR}/ has fragments ({names}): the notes are "
                                    f"assembled from the fragments, never written by hand (git rm {notes})")
                else:
                    ev.append(f"{notes} already assembled and committed, no fragments left (a re-run after step 5)")
            elif not self.frags:
                problems.append(f"no release-note fragments in {changes.DIR}/ (each change adds its own "
                                f"{changes.DIR}/<topic>.md; see CONTRIBUTING.md)")
            else:
                ev.append(f"{changes.DIR}/: {len(self.frags)} fragment(s), well formed ({names})")
        if self.a.after_merge:
            problems += self.merged_problems(notes, ahead, behind, ev)
        if problems:
            msg = "\n".join(ev + ["PROBLEM: " + p for p in problems])
            if self.a.dry_run:
                return "DRY-RUN, a real run stops here:\n" + msg, "warn"
            raise Stop(msg)
        return "\n".join(ev)

    def release_commit(self, notes):
        """the commit that added docs/releases/vX.Y.Z.md (the release commit of the first run), or None"""
        return out(["git", "log", "-1", "--format=%H", "--diff-filter=A", "--", notes]) or None

    def merged_problems(self, notes, ahead, behind, ev):
        """--after-merge: main = origin/main, the notes committed (no fragments), main's tree = the release commit's"""
        p = []
        if ahead != "0":
            p.append(f"{ahead} local commit(s) not on origin/main: --after-merge tags what the PR put on main")
        if not os.path.exists(os.path.join(ROOT, notes)) or self.frags:
            p.append(f"{notes} is not committed with the fragments removed: run the release without --after-merge "
                     f"first (it makes the release commit), then the PR")
            return p
        rc = self.release_commit(notes)
        if not rc:
            p.append(f"no commit added {notes}")
            return p
        t_rc, t_head = out(["git", "rev-parse", rc + "^{tree}"]), out(["git", "rev-parse", "HEAD^{tree}"])
        if t_rc != t_head:
            p.append(f"main's tree {t_head[:10]} is not the release commit {rc[:10]}'s ({t_rc[:10]}): something else "
                     f"was merged after it, so main is not what was tested")
        m = re.search(r"release build sha256 ([0-9a-f]{64})", out(["git", "log", "-1", "--format=%B", rc]))
        if not m:
            p.append(f"the release commit {rc[:10]} records no release build sha256")
        else:
            self.recorded_sha = m.group(1)
            ev.append(f"--after-merge: the release commit {rc[:10]} merged, main's tree = its tree; it records the "
                      f"release build sha256 {self.recorded_sha[:16]}...")
        return p

    # 3
    def s_host(self):
        ev = []
        if self.a.after_merge:              # (the tests ran before the PR, on this tree: the build alone)
            sh(["./build.sh", "--release", self.ver], timeout=3600)
            fw = os.path.join(ROOT, "build", f"frodhes-{self.ver}.fwsc")
            self.sha = sha256(fw)
            if self.sha != self.recorded_sha:
                raise Stop(f"the release build's sha256 {self.sha} is not the one the release commit records "
                           f"({self.recorded_sha}): not the build that was tested")
            return (f"--after-merge: ./build.sh --release {self.ver}: sha256 {self.sha} = the release commit's (the "
                    f"host tests and the FM-1 tiers ran on this build before the PR)")
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
        lk = lock.Lock("DEVICE", root=ROOT, who=f"tools/release.py {self.ver}", wait_s=0, poll_s=0)
        try:                                   # held by another session: stop at once; held by our caller: go on
            lk.acquire()
        except SystemExit:
            raise Stop(f"the FM-1 is locked ({lk.dir}): {lk.owner()}")
        ev = []
        try:
            tm = self.tiers()
            ev += tm["lines"]
            fw = f"build/frodhes-{self.ver}.fwsc"
            _, o = sh(f"echo y | {PY} tools/fm1_install.py {fw}", timeout=600)
            ev.append(f"installed {fw}: {tail(o, 1)}")
            _, o = sh(T1_CMD, timeout=3600)
            ev.append(f"T1 hil_scan --tier must: exit 0 ({tail(o, 1)})")
            if "T2" not in tm["need"]:
                ev.append("T2 not run (" + tm["why_t2"] + "; --full forces it)")
                return "\n".join(ev)
            for eng in ENGINES:
                _, o = sh(cpu_cmd(eng), timeout=900)
                ev.append(check_cpu(eng, o))
            _, o = sh([PY, "tools/hil_scan.py", "--only", SCAN_SET], timeout=3600)
            ev.append(f"hil_scan {SCAN_SET}: exit 0 ({tail(o, 1)})")
            _, o = sh([PY, "tools/hil_scan.py", "--only", "vibes_vel", "--source", "both"], timeout=3600)
            ev.append(f"hil_scan vibes_vel --source both: exit 0 ({tail(o, 1)})")
            rep_m = os.path.join(ROOT, "build", "hil", "marimba-report.json")
            t0 = time.time()
            sh([PY, "tools/hil_scan.py", "--marimba", "--source", "both"], check=False, timeout=5400)
            if not os.path.exists(rep_m) or os.path.getmtime(rep_m) < t0 - 1:
                raise Stop(f"hil_scan --marimba wrote no new {os.path.relpath(rep_m, ROOT)}")
            ev.append(check_marimba_scan(json.load(open(rep_m))))
            for eng in ENGINES:
                sh([PY, "tools/hil_jack.py", "--engine", eng], timeout=900)
                rep = json.load(open(os.path.join(ROOT, "build", "hil", JACK_JSON[eng])))
                ev.append(check_jack_marimba(rep) if eng == "marimba" else check_jack(eng, rep))
            _, o = sh([PY, "tools/vib_sampleback.py", "--engine", "marimba"], timeout=1800)
            ev.append(check_sampleback(o))
        finally:
            lk.release()
        return "\n".join(ev)

    # 5
    def s_docs(self):
        ev, problems = [], doc_links.problems(ROOT)
        ev.append(f"doc_links: README.md / docs/MANUAL.md, {len(problems)} problem(s) (every picture's ?v= its "
                  "file's hash, no version named)")
        problems = [f"{p} (tools/doc_links.py --fix, or tools/manual_shots.py)" for p in problems]
        _, o = sh([PY, "-m", "pytest", "-q", "tests/py/test_docs_vs_code.py", "tests/py/test_doc_links.py",
                   "tests/py/test_changes.py", "tests/py/test_manual_shots.py"])
        ev.append(f"test_docs_vs_code, test_doc_links, test_changes, test_manual_shots: {tail(o, 1)}")
        notes = f"docs/releases/v{self.ver}.md"
        frags, text = self.frags, None
        if frags:
            try:
                text = changes.assemble(self.ver, frags, ROOT)
            except changes.Bad as e:
                problems.append(str(e))
        elif not os.path.exists(os.path.join(ROOT, notes)):
            problems.append(f"no fragments in {changes.DIR}/ and no {notes}")
        if problems:
            msg = "\n".join(ev + ["PROBLEM: " + p for p in problems])
            if self.a.dry_run:
                return "DRY-RUN, a real run stops here:\n" + msg, "warn"
            raise Stop(msg)
        gone = [f"{changes.DIR}/{f}" for f, _ in frags]
        if text is None:
            ev.append(f"{notes}: already assembled (no fragments left)")
        elif self.a.dry_run:
            ev.append(f"DRY-RUN: no commit. A real run writes {notes} (below, {len(text.splitlines())} lines, from "
                      f"{', '.join(gone)}), removes the fragments and commits\n"
                      + "-" * 30 + f" {notes} " + "-" * 30 + "\n" + text.rstrip() + "\n" + "-" * 78)
            return "\n".join(ev)
        else:
            open(os.path.join(ROOT, notes), "w", encoding="utf-8").write(text)
            sh(["git", "rm", "-q", *gone])
            sh(["git", "add", notes])
            ev.append(f"{notes} written ({len(text.splitlines())} lines) from {', '.join(gone)}; the fragments removed")
        body = "\n".join(f"- {s}: {e.splitlines()[0]}" for s, (st, e) in sorted(self.results.items())
                         if st in ("done", "skipped"))
        if not self.a.no_device:
            need = self.tiers()["need"]
            dev = (f"FM-1: tiers {'+'.join(need)} with the release build installed (hil_scan --tier must"
                   + (", then hil_cpu, hil_scan, hil_jack, vib_sampleback)" if "T2" in need else "; T2 not needed)"))
        else:
            dev = self.device_done() or "FM-1: NOT tested (--no-device)"
        msg = (f"Release {self.ver}: the notes {notes}, assembled from {changes.DIR}/ (the fragments removed)"
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
        if self.a.after_merge:
            self.log(f"main {self.head[:10]} (= origin/main, the release PR merged): the tag v{self.ver} goes there")
        else:
            self.log(f"for the release PR into main (step 7 pushes nothing: it prints the commands):\n{commits}\n"
                     f"{files}\nthen, after the merge, the tag v{self.ver}")
        if self.a.dry_run:
            return "DRY-RUN: stopped before any push. A real run would now ask \"Publish %s? [y/N]\", then 7 print " \
                   "the release PR's commands (nothing pushed: main is protected); after the merge, --after-merge: " \
                   "wait for ci (or reuse the PR's green run on the same git tree), tag v%s + wait for the " \
                   "release workflow, compare the asset's sha256; " \
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
        if not self.a.after_merge:          # main is protected: never pushed here (the PR is the user's)
            raise Handoff(pr_steps(self.ver, self.head))
        ev.append(f"main {self.head[:10]} = origin/main (the release PR merged; nothing pushed to main)")
        # a green ci.yml run on a commit with the identical git tree (the PR's head, when main is GitHub's merge
        # commit of it) is the same code: reuse it instead of waiting ~38 min for a second run on main's new sha
        ev.append(same_tree_run("ci.yml", self.head) or wait_run("ci.yml", self.head))
        sh(["git", "tag", "-a", tag, "-m", f"Frodhes {self.ver}"])
        sh(["git", "push", "origin", tag])
        ev.append(f"tag {tag} pushed")
        # ci passed on this commit or on one with its tree, so release.yml's ci-gate (the same tree rule) skips the
        # fallback tests (its generators-macos job
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
        if workflow_deploys_pages():
            return self.emulator_verify()
        return self.emulator_local()

    def emulator_verify(self):
        """release.yml built, selftested and deployed the emulator from the tag before it published the release
        (step 7 waited for it): only check that the live files are gh-pages's and gh-pages's page is the tag's"""
        tag = "v" + self.ver
        sh(["git", "fetch", "-q", "origin", "gh-pages"])
        head = out(["git", "log", "-1", "--format=%h %s", "FETCH_HEAD"])
        ev = [f"release.yml deployed the emulator before publishing (tools/deploy_pages.sh, tools/pages_wait.sh): verify only; gh-pages {head}"]
        want = {f: blob_sha("FETCH_HEAD", f) for f in WEB_FILES}
        for f in ("index.html", "app.js", "fm1install.js", "install.js"):   # web/build.sh copies these unchanged
            if want[f] != blob_sha(tag, "web/" + f):
                raise Stop(f"gh-pages {f} is not {tag}'s web/{f}: the emulator was not deployed from {tag}")
        stamp = hashlib.sha256(f'window.FRODHES_VERSION = "{self.ver}";\n'.encode()).hexdigest()
        if want["version.js"] != stamp:
            raise Stop(f"gh-pages version.js does not say {self.ver} (web/build.sh --release {self.ver})")
        ev.append(f"gh-pages index.html, app.js = {tag}'s web/; version.js says {self.ver}")
        if self.sha and want["frodhes.fwsc"] != self.sha:
            raise Stop(f"gh-pages frodhes.fwsc {want['frodhes.fwsc']} is not the release's package {self.sha}")
        ev.append(f"gh-pages frodhes.fwsc = the release's package (the page's installer)")
        ev.append(wait_live(want, self.log))
        return "\n".join(ev)

    def emulator_local(self):
        """the fallback (a release.yml that does not deploy the emulator): build, selftest, deploy from build/pages, verify"""
        ev = ["release.yml does not deploy the emulator: the local fallback"]
        sh(["sh", "web/build.sh", "--release", self.ver], timeout=1800)
        dist = os.path.join(ROOT, "web", "dist")
        ev.append(web_selftest(dist, self.ver))
        pages = os.path.join(ROOT, "build", "pages")
        if out(["git", "-C", pages, "rev-parse", "--abbrev-ref", "HEAD"]) != "gh-pages":
            raise Stop(f"{pages} is not the gh-pages worktree")
        sh(["git", "-C", pages, "pull", "-q", "--ff-only", "origin", "gh-pages"])
        sh(["sh", os.path.join(ROOT, "tools", "web_firmware.sh"), os.path.join(ROOT, "build", f"frodhes-{self.ver}.fwsc"),
            dist, self.ver])
        sh(["sh", os.path.join(ROOT, "tools", "deploy_pages.sh"), dist, pages, self.ver])
        local = {f: sha256(os.path.join(dist, f)) for f in sorted(os.listdir(dist))}
        ev.append(f"gh-pages {out(['git', '-C', pages, 'rev-parse', '--short', 'HEAD'])} pushed ({len(local)} files)")
        ev.append(wait_live(local, self.log))
        return "\n".join(ev)

    # 9
    def s_verify(self):
        tag = "v" + self.ver
        versioned = f"frodhes-{self.ver}.fwsc"
        r = json.loads(out(["gh", "release", "view", tag, "-R", REPO, "--json", "assets,url,isDraft"]))
        names = [x["name"] for x in r["assets"]]
        missing = [n for n in (versioned, STABLE, *SMK_ASSETS, LICENCE_ASSET) if n not in names]
        if missing or r.get("isDraft"):
            raise Stop(f"{r['url']}: assets {names} (missing {missing}), draft {r.get('isDraft')}")
        ev = [f"{r['url']} lists {', '.join(names)}"]
        want = self.sha
        if want is None:                               # --verify-only: the versioned asset is the reference
            want = resolve(f"https://github.com/{REPO}/releases/download/{tag}/{versioned}")[1]
            if want is None:
                raise Stop(f"could not download {versioned} from {tag}")
        loc, got = resolve(LATEST_DL)
        if not loc or f"/releases/download/{tag}/{STABLE}" not in loc:
            raise Stop(f"{LATEST_DL} redirects to {loc}, not to {tag}'s {STABLE} (is {tag} marked latest?)")
        if got != want:
            raise Stop(f"{LATEST_DL} (-> {loc}): sha256 {got}, the build's {want}")
        ev.append(f"{LATEST_DL} -> {loc}: sha256 {got} = {'the local build' if self.sha else versioned}")
        readme = out(["gh", "api", f"repos/{REPO}/contents/README.md?ref={tag}", "-H", "Accept: application/vnd.github.raw"])
        pins = [m.group(0) for rx, _ in doc_links.PINS for m in rx.finditer(readme)]
        if pins or LATEST_DL not in readme:
            raise Stop(f"README on GitHub at {tag}: versions named {pins}, the stable download link "
                       f"{'ok' if LATEST_DL in readme else 'missing'}")
        ev.append(f"README at {tag} names no version and links {LATEST_DL}")
        return "\n".join(ev)

    def tiers(self):
        """the tiers this release needs: tools/test_map.py over `git diff --name-only <previous tag>..HEAD`, T1 on
        every release, T2 also with --full; {'need', 'map', 'lines' (the tiers and why), 'why_t2'}"""
        if self.tmap:
            return self.tmap
        if self.prev:
            code, o = sh(["git", "diff", "--name-only", f"{self.prev}..HEAD"], check=False, quiet=True)
            if code:
                raise Stop(f"git diff --name-only {self.prev}..HEAD exited {code}:\n" + tail(o))
            files = [l.strip() for l in o.splitlines() if l.strip()]
            src = f"git diff {self.prev}..HEAD, {len(files)} file(s)"
            m = test_map.analyze(files)
        else:                                           # no tag yet: everything is new
            src, m = "no previous tag: everything", test_map.analyze(["firmware/(everything)"])
        need = sorted(set(m["tiers"]) | {"T0", "T1"} | ({"T2"} if self.a.full else set()))
        t2_groups = [g for g, d in m["groups"].items() if "T2" in d["tiers"]]
        why_t2 = ("the map: " + ", ".join(t2_groups) + " changed" if t2_groups else
                  "--full (the map: no engine / DSP file changed)" if self.a.full else
                  "the map: no engine / DSP file changed")
        lines = [f"tiers needed: {' + '.join(need)} ({src}; T1 on every release; T2: {why_t2})"]
        lines += ["map: " + l for l in test_map.reason(m)]
        self.tmap = dict(need=need, map=m, lines=lines, why_t2=why_t2)
        return self.tmap

    def s_device_done(self):
        """step 4 with --no-device --device-done: the runs made on this exact build, checked against the tiers"""
        return "\n".join(self.tiers()["lines"] + [self.device_done()])

    def device_done(self):
        """--device-done: the FM-1 runs made before the release on this very build (its sha256 checked, its tiers
        covering the needed ones), or None"""
        if not self.a.device_done:
            return None
        d = json.load(open(self.a.device_done))
        if d.get("sha256") != self.sha:
            raise Stop(f"--device-done {self.a.device_done}: sha256 {d.get('sha256')} is not the release build's {self.sha}")
        need = [t for t in self.tiers()["need"] if t != "T0"]       # (T0: step 3, in this run)
        done = d.get("tiers") or []
        missing = [t for t in need if t not in done]
        if missing:
            raise Stop(f"--device-done {self.a.device_done}: tiers {done or '(none recorded)'} do not cover the needed "
                       f"{need} (missing {', '.join(missing)}); " + self.tiers()["lines"][0])
        return (f"FM-1: tested before the release on this exact build (sha256 equal), tiers {'+'.join(done)} "
                f"(needed {'+'.join(need)}): " + "; ".join(d.get("runs", [])))

    # ---- the driver ----
    def run(self):
        plan = [self.s_version, self.s_preconditions, self.s_host, self.s_device, self.s_docs, self.s_confirm,
                self.s_publish, self.s_emulator, self.s_verify]
        if self.a.no_device and not self.a.verify_only:
            print("\n" + "!" * 78 + "\n!!  --no-device: the FM-1 is NOT tested in this run (step 4 skipped)\n"
                  + "!" * 78, flush=True)
        failed = handed = False
        if self.a.verify_only:
            tags = out(["git", "tag", "--sort=-v:refname", "--list", "v*"]).split()
            self.ver = ("%d.%d.%d" % parse_version(self.a.version) if self.a.version not in ("patch", "minor")
                        else tags[0][1:] if tags else None)
        for i, fn in enumerate(plan, 1):
            name = STEPS[i - 1]
            skip = ("--verify-only" if self.a.verify_only and i != 9 else
                    "--after-merge: done before the PR (the release commit records it)" if i in (4, 5) and
                    self.a.after_merge else
                    "--no-device" if i == 4 and self.a.no_device and not self.a.device_done else "--no-emulator" if i == 8 and self.a.no_emulator
                    else "--dry-run" if i > 6 and self.a.dry_run and not self.a.verify_only else None)
            if failed:
                self.results[i] = ("not run", "an earlier step failed")
                continue
            if handed:
                self.results[i] = ("not run", f"after the PR's merge: tools/release.py {self.ver} --after-merge")
                continue
            if skip:
                self.results[i] = ("skipped", skip)
                print(f"\n[{i}/9] {name}: SKIPPED ({skip})", flush=True)
                if i == 4:
                    try:
                        tm = self.tiers()
                    except Stop as e:
                        tm = dict(need=["T0", "T1", "T2"], lines=[f"tiers needed: T0 + T1 + T2 (the diff failed: {e})"])
                    self.log("\n".join(tm["lines"]))
                    self.log("with the FM-1 this step runs, in order:\n"
                             + "\n".join(device_plan(self.ver or "X.Y.Z", tm["need"])))
                continue
            print(f"\n[{i}/9] {name}", flush=True)
            try:
                r = self.s_device_done() if i == 4 and self.a.no_device else fn()
                ev, st = r if isinstance(r, tuple) else (r, "done")
                self.results[i] = (st, ev)
                self.log(ev)
                print(f"[{i}/9] {name}: {'PASS' if st == 'done' else 'WARN (dry-run)'}", flush=True)
            except Handoff as e:
                self.results[i] = ("handoff", str(e))
                self.log(str(e))
                print(f"[{i}/9] {name}: HANDED OFF (the release PR)", flush=True)
                handed = True
            except (Stop, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as e:
                self.results[i] = ("FAILED", str(e))
                self.log(str(e))
                print(f"[{i}/9] {name}: FAIL", flush=True)
                failed = True
        self.summary()
        return 1 if failed else 3 if handed else 0

    def summary(self):
        mode = ' (VERIFY-ONLY)' if self.a.verify_only else ' (DRY-RUN)' if self.a.dry_run else ''
        print("\n" + "=" * 78 + f"\nFrodhes release {self.ver or '?'}{mode}: summary")
        for i, name in enumerate(STEPS, 1):
            st, ev = self.results.get(i, ("not run", ""))
            lines = ev.splitlines() or [""]
            if st == "handoff":
                lines = [l for l in ev.splitlines() if l.startswith("  ")] or lines
                print(f" {i}  {name:14s} {st:8s} the release PR (nothing pushed); then:")
                for l in lines:
                    print(f"    {l.strip()[:130]}")
                continue
            if st in ("warn", "FAILED"):              # the problems, not the banner
                lines = [l for l in lines if l.startswith("PROBLEM") or l.startswith("`")] or lines
            print(f" {i}  {name:14s} {st:8s} {lines[0][:120]}")
            for l in lines[1:4] if st in ("warn", "FAILED") else []:
                print(f"    {'':14s} {'':8s} {l[:120]}")
        if self.a.no_device:
            print(" !! the FM-1 was NOT tested (--no-device)")
        print("=" * 78)


JACK_JSON = dict(rhodes="jack.json", vibes="jack-vibes.json", marimba="jack-marimba.json")


def cpu_cmd(eng):
    return [PY, "tools/hil_cpu.py", "--worst"] + ([] if eng == "rhodes" else ["--engine", eng])


T1_CMD = [PY, "tools/hil_scan.py", "--tier", "must"]


def device_plan(ver, tiers=("T0", "T1", "T2")):
    """step 4's commands in order (what s_device runs for these tiers) with what each must show, for --no-device
    to list"""
    q = " ".join
    t1 = [f"echo y | {PY} tools/fm1_install.py build/frodhes-{ver}.fwsc",
          q(T1_CMD) + "   (T1 must-pass: exit 0, every scenario's counters 0)"]
    if "T2" not in tiers:
        return t1
    return (t1
            + [q(cpu_cmd(e)) + f"   (worst <= {CPU_LIMIT_US} us, nothing shed)" for e in ENGINES]
            + [q([PY, "tools/hil_scan.py", "--only", SCAN_SET]) + "   (exit 0)",
               q([PY, "tools/hil_scan.py", "--only", "vibes_vel", "--source", "both"]) + "   (exit 0)",
               q([PY, "tools/hil_scan.py", "--marimba", "--source", "both"])
               + f"   (marimba-report.json: {', '.join(MARIMBA_MUST)} every row; {', '.join(MARIMBA_FX)} the "
               "takes / clicks / drift / bypass / level re ST77 rows; KNOWN rows pass)"]
            + [q([PY, "tools/hil_jack.py", "--engine", e]) + f"   ({JACK_JSON[e]}: the jack connected"
               + ("; the A5 tails, the idle floor)" if e == "marimba" else ")") for e in ENGINES]
            + [q([PY, "tools/vib_sampleback.py", "--engine", "marimba"]) + "   (Gate VD-M: 0 keys outside)"])


def check_marimba_scan(rep):
    """build/hil/marimba-report.json against MARIMBA_MUST / MARIMBA_FX_MUST / MARIMBA_INFO; Stop with every failing
    row that must pass"""
    suites = rep["suites"]
    missing = [n for n in MARIMBA_MUST + MARIMBA_FX if n not in suites]
    if missing:
        raise Stop(f"hil_scan --marimba: suites missing from the report: {', '.join(missing)}")
    bad, info, known, nfx = [], [], 0, 0
    for n, rows in suites.items():
        for r in rows:
            known += bool(r.get("known"))
            if r["ok"]:
                continue
            if (n, r["check"]) in MARIMBA_INFO:
                info.append(f"{n} {r['check']}: USB {r['usb']} jack {r['jack']} (not gating: "
                            f"{MARIMBA_INFO[n, r['check']]})")
            elif n in MARIMBA_MUST or any(re.search(x, r["check"]) for x in MARIMBA_FX_MUST):
                bad.append(f"{n}: {r['check']} [{r['setting']}] USB {r['usb']} jack {r['jack']} ({r['crit']}) "
                           f"{r['status']}")
            else:
                nfx += 1
    if bad:
        raise Stop("hil_scan --marimba: rows that must pass failed:\n" + "\n".join(bad))
    lvl = [r for r in suites["marimba_jump"] if r["check"].startswith("MARIMBA re ST77")]
    return (f"hil_scan --marimba --source both: {', '.join(MARIMBA_MUST)} every row ok ({known} KNOWN, user "
            f"decisions); {', '.join(MARIMBA_FX)}: the must rows ok, {nfx} other row(s) failed (not gating)"
            + (f"; MARIMBA re ST77 USB {lvl[0]['usb']} jack {lvl[0]['jack']} LU" if lvl else "")
            + "".join("\n" + x for x in info))


def check_jack_marimba(rep):
    m = rep["marimba"]
    peak = m["loud"]["jack_peak_db"]
    if peak < JACK_PEAK_MIN_DB:
        raise Stop(f"hil_jack marimba: the loud chord peaks at {peak:.1f} dBFS on the jack (usually about -15): "
                   "is the jack connected to the interface?")
    bad = [f"tail {k}: {v}" for k, v in rep["tails"].items() if not v.get("ok")]
    if not rep["idle_ok"]:
        bad.append(f"idle: {rep['idle']}")
    if bad:
        raise Stop("hil_jack marimba (TEST-PLAN A5, the idle floor):\n" + "\n".join(bad))
    return (f"hil_jack marimba: SNR {m['snr_db']:.1f} dB ({rep['snr_vs_st77_db']:+.1f} re ST77 in the same session, "
            f"information: MARIMBA's chord is quieter), loud chord jack peak {peak:.1f} dBFS; {len(rep['tails'])} "
            "tails and the idle floor ok")


def check_sampleback(text):
    m = re.search(r"(\d+) notes, (\d+) outside Gate VD-M", text)
    if not m:
        raise Stop("vib_sampleback marimba: no 'outside Gate VD-M' line:\n" + tail(text))
    if int(m.group(2)):
        raise Stop(f"vib_sampleback marimba: {m.group(2)} of {m.group(1)} keys outside Gate VD-M:\n" + tail(text))
    return f"vib_sampleback marimba: {m.group(1)} keys, 0 outside Gate VD-M (device vs host)"


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


def tree_of(sha):
    """the git tree sha of commit sha: the local object, else the GitHub API's commit.tree.sha; None if unknown"""
    code, t = sh(["git", "rev-parse", "-q", "--verify", sha + "^{tree}"], check=False, quiet=True)
    if not code and re.fullmatch(r"[0-9a-f]{40}", t.strip()):
        return t.strip()
    code, t = sh(["gh", "api", f"repos/{REPO}/commits/{sha}", "--jq", ".commit.tree.sha"], check=False, quiet=True)
    return t.strip() if not code and re.fullmatch(r"[0-9a-f]{40}", t.strip()) else None


def same_tree_run(workflow, sha, limit=50, max_commits=20):
    """a completed, successful workflow run (every job success) on another commit whose git tree is identical to sha's
    (the same code by construction): its evidence line, or None (then the caller waits for a run on sha itself)"""
    tree = tree_of(sha)
    if not tree:
        print(f"   the git tree of {sha[:10]} is unknown: waiting for {workflow} on exactly this commit", flush=True)
        return None
    code, txt = sh(["gh", "run", "list", "-R", REPO, "--workflow", workflow, "--status", "success", "-L", str(limit),
                    "--json", "databaseId,headSha,headBranch,status,conclusion,event"], check=False, quiet=True)
    try:
        rows = json.loads(txt or "[]") if not code else []
    except ValueError:
        rows = []
    seen = set()
    for r in rows:
        h = r.get("headSha") or ""
        if r.get("status") != "completed" or r.get("conclusion") != "success" or not h or h == sha or h in seen:
            continue
        if len(seen) >= max_commits:
            break
        seen.add(h)
        if tree_of(h) != tree:
            continue
        rid = str(r["databaseId"])
        code, txt = sh(["gh", "run", "view", rid, "-R", REPO, "--json", "conclusion,jobs,url"], check=False, quiet=True)
        try:
            v = json.loads(txt) if not code else None
        except ValueError:
            v = None
        jobs = [(j.get("name"), j.get("conclusion")) for j in (v or {}).get("jobs", [])]
        if not v or v.get("conclusion") != "success" or not jobs or any(c != "success" for _, c in jobs):
            print(f"   {workflow} run {rid} on {h[:10]} has tree {tree[:10]} but is not all green ({jobs}): "
                  "not reused", flush=True)
            continue
        msg = (f"{workflow} {v['url']}: success on {h[:10]} ({r.get('event')}, {r.get('headBranch')}), whose git tree "
               f"{tree[:10]} is identical to {sha[:10]}'s (the same code): reused, no second run waited for; jobs "
               + ", ".join(f"{n} {c}" for n, c in jobs))
        print("   " + msg, flush=True)
        return msg
    print(f"   no green {workflow} run on a commit with {sha[:10]}'s tree {tree[:10]}: waiting for one on {sha[:10]}",
          flush=True)
    return None


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


WEB_FILES = ("index.html", "app.js", "fm1install.js", "install.js", "version.js", "frodhes.js", "frodhes.wasm",
             "frodhes.fwsc", "firmware.json")   # web/build.sh's web/dist, and the package (tools/web_firmware.sh)


def workflow_deploys_pages(path=None):
    """release.yml deploys the emulator itself (a job running tools/deploy_pages.sh) before it publishes"""
    try:
        with open(path or os.path.join(ROOT, ".github", "workflows", "release.yml")) as f:
            return "tools/deploy_pages.sh" in f.read()
    except OSError:
        return False


def blob_sha(ref, path):
    """the sha256 of path's bytes at git ref (None if it is not there)"""
    p = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True)
    return hashlib.sha256(p.stdout).hexdigest() if p.returncode == 0 else None


def wait_live(want, log, tries=24, pause=15):
    """every file of want ({name: sha256}) served by PAGES_URL with that sha256 (Pages deploys in a minute or two)"""
    bad = list(want)
    for i in range(tries):
        bad = [f for f, h in want.items() if fetch_sha(PAGES_URL + f) != h]
        if not bad:
            return f"live {PAGES_URL}: every file's sha256 = the expected one ({', '.join(want)})"
        log(f"live differs ({', '.join(bad)}), retry {i + 1}/{tries} in {pause} s")
        time.sleep(pause)
    raise Stop(f"the live emulator still differs after {tries * pause} s: {', '.join(bad)}")


def fetch_sha(url):
    try:
        with urllib.request.urlopen(f"{url}?nocache={time.time():.0f}", timeout=30) as r:
            return hashlib.sha256(r.read()).hexdigest()
    except OSError:
        return None


def resolve(url):
    """(the first redirect's target, the sha256 of what the redirects end at) for url; None for what failed"""
    first = []

    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            first.append(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        with urllib.request.build_opener(Redirect).open(url, timeout=60) as r:
            return (first[0] if first else None), hashlib.sha256(r.read()).hexdigest()
    except OSError:
        return (first[0] if first else None), None


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
            ver = pg.evaluate("[document.getElementById('ver').textContent, "
                              "document.getElementById('ver-foot').textContent]")
            inst = pg.evaluate("[!!document.getElementById('install'), document.getElementById('inst-ui').hidden, "
                               "document.getElementById('inst-none').hidden, !!navigator.requestMIDIAccess]")
            res.append(dict(browser=bname, width=w, title=pg.title(), scroll=sw, errors=errs, ver=ver, inst=inst))
            pg.close()
        if bname == "chromium":                     # the installer, through the page, on a fake FM-1
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script(path="tests/web/fake_fm1.js")
            pg.goto(url + "?installtest")
            pg.wait_for_function("document.title.startsWith('INSTALLTEST')", timeout=60000)
            res.append(dict(browser=bname, installtest=pg.title(), errors=errs))
            pg.close()
        b.close()
print(json.dumps(res))
"""


def web_selftest(dist, version=None):
    """the ?selftest page in Chromium and WebKit at 1280 and 390 px, served from dist; Stop on any failure. The page
    must show dist/version.js's version (web/build.sh --release X.Y.Z, else "dev") in its heading and footer, and
    that must be version when one is given"""
    shown = dist_version(dist)
    if shown is None:
        raise Stop(f"{dist}/version.js missing or malformed (web/build.sh writes it)")
    if version is not None and shown != version:
        raise Stop(f"{dist}/version.js says {shown!r}, not {version!r} (web/build.sh --release {version})")
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", dist],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        env = dict(os.environ)
        if "PLAYWRIGHT_BROWSERS_PATH" not in env and os.path.isdir(os.path.join(ROOT, ".local", "playwright")):
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(ROOT, ".local", "playwright")
        py = os.path.join(ROOT, PY)                  # the project venv, else (CI) this interpreter
        p = subprocess.run([py if os.path.exists(py) else sys.executable, "-c", SELFTEST_JS, f"http://127.0.0.1:{port}/"], cwd=ROOT,
                           env=env, text=True, capture_output=True, timeout=300)
        if p.returncode:
            raise Stop("Playwright selftest failed:\n" + tail(p.stdout + p.stderr))
        res = json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
    bad, ok = [], []
    for r in res:
        if "installtest" in r:                      # Chromium: a full install through the page on the fake FM-1
            if not r["installtest"].startswith("INSTALLTEST ok") or r["errors"]:
                bad.append(f"{r['browser']} install: {r['installtest']!r} errors {r['errors']}")
            else:
                ok.append(f"{r['browser']} install on a fake FM-1 ok")
            continue
        # the install section: there; with Web MIDI its controls, without it (WebKit) the command line instead
        present, ui_hidden, none_hidden, has_midi = r["inst"]
        if not present or ui_hidden == has_midi or none_hidden != has_midi:
            bad.append(f"{r['browser']} {r['width']}px: the install section {r['inst']}")
        m = re.search(r"rms=(-?[\d.]+)dB screen=(\d+)px", r["title"])
        tag = f"{r['browser']} {r['width']}px"
        if (not m or float(m.group(1)) < -60 or int(m.group(2)) == 0 or r["errors"] or r["scroll"] > r["width"]
                or r.get("ver") != [shown, shown]):
            bad.append(f"{tag}: {r['title']!r} scrollWidth {r['scroll']} errors {r['errors']} version {r.get('ver')}")
        else:
            ok.append(f"{tag} rms {m.group(1)} dB, {m.group(2)} px lit")
    if bad:
        raise Stop("emulator selftest: " + "; ".join(bad))
    return f"selftest (Frodhes {shown}) " + "; ".join(ok)


def dist_version(dist):
    """the version web/build.sh stamped into dist/version.js, or None"""
    try:
        with open(os.path.join(dist, "version.js")) as f:
            m = re.fullmatch(r'window\.FRODHES_VERSION = "([0-9A-Za-z.-]+)";\n', f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--web-selftest"]:   # ci.yml / release.yml: tools/release.py --web-selftest web/dist [X.Y.Z]
        try:
            dist = os.path.abspath(argv[1] if len(argv) > 1 else os.path.join(ROOT, "web", "dist"))
            print(web_selftest(dist, argv[2] if len(argv) > 2 else None))
            return 0
        except Stop as e:
            print(f"FAIL: {e}", flush=True)
            return 1
    a = parse_args(argv)
    return Release(a).run()


if __name__ == "__main__":
    sys.exit(main())
