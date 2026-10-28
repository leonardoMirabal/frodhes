#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""A release's device plan on both devices at once, unattended (plan 05, M7-04 and section 6.6).

    tools/release_devices.py X.Y.Z [--dry-run] [--since TAG] [--files F ...] [--full] [--null-proven]
                             [--fm1 FWSC] [--move TARBALL] [--records DIR] [--out JSON] [--fake [--fake-fail STEP]]

1 the change       tools/test_map.py per target over `git diff --name-only <TAG>..HEAD` (TAG: the latest v* tag; or
                   --files): which files reach the FM-1 and which reach the Move (tools/target_closure.py)
2 the packages     the FM-1 package (build/frodhes-X.Y.Z.fwsc) and the Move package (build/move/frodhes-module.tar.gz,
                   whose dsp.so must be the one move_install.py installs from build/move/frodhes/), their sha256
3 reuse            a target whose package sha256 already has a PASS record covering the tiers it needs (a device-done
                   JSON in --records, default <main checkout>/build/device-done/, or an older build/device-done-*.json:
                   the FM-1's) runs nothing: the record is carried over, with its file named. Never across different
                   sha256
4 the plan         per target, from the change (the table in section 6.6):
                   FM-1   install + T1 (hil_scan --tier must) on every new package; T2 (release.py's suites, its checks)
                          when the map asks for it or --full; with --null-proven the null test on every engine in place
                          of T2 (its device capture is not built yet: such a plan is INCOMPLETE, never a pass)
                   Move   install, then the release minimum (d2 over the tap: usb-host = the null test on the tap,
                          usb-stress = the crackle and the CPU at 16 voices, usb-restrike, HQ on and off) when only shared
                          code changed or nothing did; MT1 (the whole d2, HQ on and off) when the Move's own code
                          changed; + the HQ stress when firmware/src/hq/ changed; + MT2-lite (a 5 min usb-stress) with
                          --full
5 the run          both targets in parallel (one thread each; the wall time is the longer plan), each step a process
                   whose output goes to <out dir>/<target>-<step>.log; a failed step ends its target (the other goes on).
                   The locks: DEVICE (the FM-1 and the MOTU) and MOVE, both taken before anything runs, never waited
                   for (held by another session: stop at once)
6 the record       one JSON (--out, default <records>/device-done-X.Y.Z.json), tools/release.py --device-done's schema: a
                   section per target at the top, {"fm1": {sha256, tiers, runs, ...}, "move": {...}} (plus the
                   verdict, the minutes, the package; reused: where from). A section that did not pass records no
                   tiers, so release.py refuses it. The Move's tiers: MIN, MT1 (what release.py checks), MHQ, MT2

--dry-run prints the plan, its minutes per target and the wall time, and runs nothing (no lock, no device). --fake runs
the plan with every step replaced by a stand-in that only prints (tests; --fake-fail STEP makes that one fail): it
touches no device, and its locks are the ones under FRODHES_LOCK_ROOT when that is set.

Recording: the FM-1 is recorded on the MOTU (input 5) by its own tools; the Move's plan records nothing on the MOTU
(the tap and its USB-C stream), so the two plans share no audio input. The Move's line out on the MOTU (plan 6.5) comes
with the golden baselines (M1-26): its takes will then join the FM-1's recorder process here.
Exit: 0 every target PASS (or reused), 1 a FAIL, 2 INCOMPLETE or stopped (never a pass for a release).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lock  # noqa: E402
import test_map  # noqa: E402

TARGETS = ("fm1", "move")
LOCKS = {"fm1": "DEVICE", "move": "MOVE"}
NAMES = {"fm1": "FM-1", "move": "Move"}
MOVE_TARBALL = os.path.join("build", "move", "frodhes-module.tar.gz")
MOVE_SRC = os.path.join("build", "move", "frodhes")
# the minutes of each step (estimates: the FM-1's from AGENTS.md section 5 and the 0.9.6 / 0.9.7 runs; the Move's
# from the d2 runs of 2026-10-28, 13.6 min with HQ on and 11.5 min off; refined by the first rehearsal, M7-05)
MIN = {"fm1-install": 3, "fm1-t1": 20, "fm1-t2": 70, "fm1-null": 7,
       "move-install": 3, "move-min-hq1": 6, "move-min-hq0": 4, "move-mt1-hq1": 14, "move-mt1-hq0": 12,
       "move-hq-stress": 5, "move-mt2-lite": 15}
MOVE_MIN_TESTS = "usb-host,usb-stress,usb-restrike"   # the release minimum: the tap's null test, the crackle + CPU
MOVE_TIERS = ("MIN", "MT1", "MHQ", "MT2")     # (release.py reads MIN and MT1)


def rel(p):
    """a path relative to the checkout when it is inside it, else as given"""
    r = os.path.relpath(os.path.abspath(p), ROOT)
    return p if r.startswith("..") else r


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def move_package(ver):
    """the Move package release.py builds (build/frodhes-X.Y.Z-move.tar.gz, a copy of make move's tarball), else the
    tarball itself"""
    import release as rl
    p = os.path.join(ROOT, "build", rl.MOVE_VERSIONED.format(v=ver))
    return p if os.path.exists(p) else os.path.join(ROOT, MOVE_TARBALL)


def move_tar_matches(tar, src):
    """the tarball's dsp.so = the one move_install.py installs (build/move/frodhes/dsp.so): (ok, why)"""
    try:
        with tarfile.open(tar) as t:
            m = t.extractfile("frodhes/dsp.so")
            a = hashlib.sha256(m.read()).hexdigest() if m else None
    except (OSError, KeyError, tarfile.TarError) as e:
        return False, f"{tar}: {e}"
    so = os.path.join(src, "dsp.so")
    b = sha256(so) if os.path.exists(so) else None
    return (a is not None and a == b), f"the tarball's dsp.so {str(a)[:12]}, {so} {str(b)[:12]}"


# ---------------------------------------------------------------- the plan
class Step:
    def __init__(self, name, cmd, minutes, tier, check=None, timeout=None, why=""):
        self.name, self.cmd, self.minutes, self.tier, self.check, self.why = name, cmd, minutes, tier, check, why
        self.timeout = timeout or max(600, int(minutes * 60 * 3))

    def text(self):
        c = self.cmd if isinstance(self.cmd, str) or self.cmd is None else " ".join(self.cmd)
        return c or f"(not available: {self.why})"


def need_of(pt, files, full=False, null_proven=False):
    """the tiers each target needs, by tools/release.py's own rules (so its --device-done reader accepts the record):
    the FM-1 T1 on every release, T2 when test_map's analysis of the whole diff has it or --full; the Move MIN on
    every release, MT1 when a file under release.MOVE_ONLY changed (or the Move's own code by the closure, or --full);
    the plan's extras MHQ (HQ changed) and MT2 (--full). pt (test_map.per_target) names the files per target.
    {'fm1': {'tiers', 'null', 'why'}, 'move': {'tiers', 'why'}}"""
    import release as rl
    t2 = "T2" in test_map.analyze(files)["tiers"]
    tiers = ["T1"] + (["T2"] if (t2 and not null_proven) or full else [])
    fm1 = {"tiers": tiers, "null": bool(t2 and null_proven and not full),
           "why": ("the FM-1 builds from %d changed file(s)" % len(pt["fm1"]["files"]) if pt["fm1"]["files"]
                   else "no FM-1 file changed") + ("; T2: --full" if full else "; T2: an engine / the DSP changed"
                                                   if "T2" in tiers else "; the null test replaces T2 (--null-proven)"
                                                   if t2 else "")}
    mv_files = set(pt["move"]["files"]) | set(pt["hq"]["files"])
    own = sorted((mv_files - set(pt["shared"])) | {f for f in files if f.startswith(rl.MOVE_ONLY)})
    hq = any(f.startswith(("firmware/src/hq/", "firmware/gen/hq/")) for f in set(files) | mv_files)
    tiers = ["MIN"] + (["MT1"] if own or full else []) + (["MHQ"] if hq else []) + (["MT2"] if full else [])
    move = {"tiers": tiers, "why": (f"the Move's own code: {len(own)} file(s)" if own else
                                    "shared code only" if mv_files else "no Move file changed")
            + ("; HQ changed" if hq else "") + ("; --full" if full else "")}
    return {"fm1": fm1, "move": move}


def fm1_steps(ver, fw, need, py):
    import release as rl
    q = lambda c: " ".join(c)                                   # noqa: E731
    s = [Step("install", f"echo y | {py} tools/fm1_install.py {fw}", MIN["fm1-install"], "T1"),
         Step("t1", [py, "tools/hil_scan.py", "--tier", "must"], MIN["fm1-t1"], "T1")]
    if need["null"]:
        s.append(Step("null", None, MIN["fm1-null"], "T2",
                      why="the FM-1's null-test capture (M1-26): device_null.py judges WAVs only"))
    if "T2" in need["tiers"]:
        n = len(rl.ENGINES) * 2 + 4
        per = MIN["fm1-t2"] / n
        for e in rl.ENGINES:
            s.append(Step("t2-cpu-" + e, [py] + rl.cpu_cmd(e)[1:], per, "T2",
                          check=lambda o, e=e: rl.check_cpu(e, o)))
        s += [Step("t2-scan", [py, "tools/hil_scan.py", "--only", rl.SCAN_SET], per, "T2"),
              Step("t2-vibes-vel", [py, "tools/hil_scan.py", "--only", "vibes_vel", "--source", "both"], per, "T2"),
              Step("t2-marimba", [py, "tools/hil_scan.py", "--marimba", "--source", "both"], per, "T2",
                   check=lambda o: rl.check_marimba_scan(
                       json.load(open(os.path.join(ROOT, "build", "hil", "marimba-report.json"))))),
              Step("t2-sampleback", [py, "tools/vib_sampleback.py", "--engine", "marimba"], per, "T2",
                   check=rl.check_sampleback)]
        for e in rl.ENGINES:
            j = os.path.join(ROOT, "build", "hil", rl.JACK_JSON[e])
            s.append(Step("t2-jack-" + e, [py, "tools/hil_jack.py", "--engine", e], per, "T2",
                          check=lambda o, e=e, j=j: (rl.check_jack_marimba if e == "marimba" else
                                                     (lambda r: rl.check_jack(e, r)))(json.load(open(j)))))
    return s


def move_steps(need, py, out_dir):
    d2 = lambda hq, *x: [py, "tools/move_hil.py", "d2", "--path", "tap", "--hq", str(hq), "--go",  # noqa: E731
                         "--out", os.path.join(out_dir, f"move-d2-hq{hq}" + ("-" + x[0] if x else "")), *x[1:]]
    s = [Step("install", [py, "tools/move_install.py", "install", "--go"], MIN["move-install"], "MIN")]
    if "MT1" in need["tiers"]:
        s += [Step("mt1-hq1", d2(1), MIN["move-mt1-hq1"], "MT1"), Step("mt1-hq0", d2(0), MIN["move-mt1-hq0"], "MT1")]
    else:
        s += [Step("min-hq1", d2(1, "min", "--tests", MOVE_MIN_TESTS), MIN["move-min-hq1"], "MIN"),
              Step("min-hq0", d2(0, "min", "--tests", MOVE_MIN_TESTS), MIN["move-min-hq0"], "MIN")]
    if "MHQ" in need["tiers"]:
        s.append(Step("hq-stress", d2(1, "hq", "--engines", "rhodes", "--tests", "usb-stress,usb-restrike",
                                      "--secs", "120"), MIN["move-hq-stress"], "MHQ"))
    if "MT2" in need["tiers"]:
        s.append(Step("mt2-lite", d2(1, "mt2", "--tests", "usb-stress", "--secs", "300"), MIN["move-mt2-lite"], "MT2"))
    return s


def find_record(target, sha, tiers, dirs):
    """a PASS record of target on this sha256 whose tiers cover these: (path, section) or (None, None)"""
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d), reverse=True):
            if not (f.startswith("device-done") and f.endswith(".json")):
                continue
            p = os.path.join(d, f)
            try:
                r = json.load(open(p))
            except (OSError, ValueError):
                continue
            if r.get("fake"):
                continue                                   # (a --fake run proves nothing about a device)
            sec = r.get(target)
            if sec is None and target == "fm1" and r.get("sha256") and "move" not in r:
                sec = r                                    # (an older record: the FM-1's alone, flat)
            if (isinstance(sec, dict) and sec.get("sha256") == sha and sec.get("verdict", "PASS") in ("PASS", "REUSED")
                    and all(t in (sec.get("tiers") or []) for t in tiers)):
                return p, sec
    return None, None


def plan(ver, files, pkgs, records, full=False, null_proven=False, py=None, out_dir=None, pt=None):
    """{'need', 'targets': {t: {'sha256', 'package', 'tiers', 'reuse' (path | None), 'steps', 'minutes', 'why'}},
    'wall'}. pkgs: {t: path}; a missing package stops the plan (a ValueError)"""
    py = py or default_py()
    pt = pt or test_map.per_target(files)
    need = need_of(pt, files, full, null_proven)
    out = {"version": ver, "files": len(files), "targets": {}}
    for t in TARGETS:
        p = pkgs[t]
        if not os.path.exists(p):
            raise ValueError(f"{NAMES[t]}: no package {p}")
        sha, nd = sha256(p), need[t]
        path, sec = find_record(t, sha, nd["tiers"], records)
        if path:
            steps = []
        elif t == "fm1":
            steps = fm1_steps(ver, rel(p), nd, py)
        else:
            steps = move_steps(nd, py, out_dir or os.path.join(ROOT, "build", "release-devices"))
        out["targets"][t] = dict(sha256=sha, package=p, tiers=nd["tiers"], reuse=path, reused=sec, steps=steps,
                                 minutes=round(sum(s.minutes for s in steps), 1), why=nd["why"])
    out["wall"] = max(v["minutes"] for v in out["targets"].values())
    return out


def plan_lines(pl):
    ls = [f"release {pl['version']}: the device plan ({pl['files']} changed file(s)); wall about {pl['wall']:.0f} min "
          f"(the longer target; both run at once)"]
    for t, v in pl["targets"].items():
        ls.append(f"{NAMES[t]:5s} {rel(v['package'])} sha256 {v['sha256'][:16]}..., tiers "
                  f"{'+'.join(v['tiers'])} ({v['why']})")
        if v["reuse"]:
            ls.append(f"      REUSE {v['reuse']}: tested on this exact package ({'+'.join(v['reused']['tiers'])}), "
                      "runs nothing")
            continue
        for s in v["steps"]:
            ls.append(f"      {s.minutes:5.1f} min  {s.tier:4s} {s.name:15s} {s.text()}")
        ls.append(f"      {v['minutes']:5.1f} min in all")
    return ls


# ---------------------------------------------------------------- the run
def default_py():
    p = os.path.join(lock.main_repo(), ".local", "venv", "bin", "python")
    return p if os.path.exists(p) else sys.executable


def shell_runner(step, log):
    """runs a step: (exit code, output)"""
    cmd = step.cmd
    with open(log, "w") as f:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=step.timeout)
        f.write(p.stdout)
    return p.returncode, p.stdout


def fake_runner(fail=()):
    def run(step, log):
        o = f"(fake) {step.text()}\n"
        open(log, "w").write(o)
        time.sleep(0.01)
        return (1 if step.name in fail else 0), o
    run.fake = True                                       # (its output is no device's: the output checks are skipped)
    return run


def run_target(t, v, out_dir, runner, say):
    """one target's steps in order; the first failure ends it. The section of the record"""
    sec = dict(sha256=v["sha256"], package=rel(v["package"]), tiers=list(v["tiers"]), runs=[],
               verdict="PASS", minutes=0.0)
    t0 = time.time()
    for s in v["steps"]:
        if s.cmd is None:
            sec["runs"].append(f"{s.name}: NOT RUN ({s.why})")
            sec["verdict"] = "INCOMPLETE"
            say(f"{NAMES[t]}: {s.name} not available ({s.why}): INCOMPLETE")
            continue
        log = os.path.join(out_dir, f"{t}-{s.name}.log")
        say(f"{NAMES[t]}: {s.name}: {s.text()}")
        ts = time.time()
        try:
            code, o = runner(s, log)
            if code == 0 and s.check and not getattr(runner, "fake", False):
                s.check(o)
            res = "exit 0" if code == 0 else f"exit {code}"
        except Exception as e:                       # (a check's Stop, a timeout: the step's FAIL)
            code, res = 1, f"{type(e).__name__}: {e}"
        sec["runs"].append(f"{s.name}: {' '.join(s.text().split()[:6])}...: {res} in {time.time() - ts:.0f} s "
                           f"(log {rel(log)})")
        if code:
            sec["verdict"] = "FAIL"
            say(f"{NAMES[t]}: {s.name} FAILED ({res}); the rest of the {NAMES[t]} plan is not run")
            break
    if sec["verdict"] != "PASS":
        sec["tiers"] = []                            # (no tier is done on a FAIL / INCOMPLETE)
    sec["minutes"] = round((time.time() - t0) / 60, 1)
    return sec


def run(pl, out_dir, runner, say=print, lock_root=None):
    """both targets at once, the locks held throughout: the combined record"""
    os.makedirs(out_dir, exist_ok=True)
    rec = {"version": pl["version"], "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    secs = {}
    todo = [t for t, v in pl["targets"].items() if not v["reuse"]]
    for t, v in pl["targets"].items():
        if v["reuse"]:
            secs[t] = dict(v["reused"], verdict="REUSED", reused_from=v["reuse"])
    held = []
    try:
        for t in todo:
            lk = lock.Lock(LOCKS[t], root=lock_root, who="tools/release_devices.py " + pl["version"],
                           why=f"the {NAMES[t]} release plan", wait_s=0, poll_s=0)
            try:
                lk.acquire()
            except SystemExit:
                raise RuntimeError(f"the {NAMES[t]} is locked ({lk.dir}): {lk.owner()}")
            held.append(lk)
        res, ths = {}, []
        for t in todo:
            th = threading.Thread(target=lambda t=t: res.__setitem__(
                t, run_target(t, pl["targets"][t], out_dir, runner, say)))
            th.start()
            ths.append(th)
        for th in ths:
            th.join()
        secs.update(res)
    finally:
        for lk in held:
            lk.release()
    for t in TARGETS:                             # (A's schema, release.py --device-done: {"fm1": {sha256, tiers,
        if t in secs:                             # runs}, "move": {...}}; a section that did not pass has no tiers)
            rec[t] = secs[t]
    return rec


def code_of(rec):
    v = [rec[t]["verdict"] for t in TARGETS if t in rec]
    return 0 if all(x in ("PASS", "REUSED") for x in v) else 1 if "FAIL" in v else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("version")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="the tag to diff from (default: the latest v* tag)")
    ap.add_argument("--files", nargs="*", help="the changed files (in place of the diff)")
    ap.add_argument("--full", action="store_true", help="T2 on the FM-1, MT1 + MT2-lite on the Move (the drift guard)")
    ap.add_argument("--null-proven", action="store_true", help="the null test in place of the FM-1's T2 (M1-26)")
    ap.add_argument("--fm1", help="the FM-1 package (default build/frodhes-X.Y.Z.fwsc)")
    ap.add_argument("--move", help="the Move package (default " + MOVE_TARBALL + ")")
    ap.add_argument("--records", help="the device-done records (default <main checkout>/build/device-done)")
    ap.add_argument("--out", help="the record to write (default <records>/device-done-X.Y.Z.json)")
    ap.add_argument("--fake", action="store_true", help="every step a stand-in that only prints (no device)")
    ap.add_argument("--fake-fail", action="append", default=[], help="--fake: this step fails")
    a = ap.parse_args(argv)
    if a.files is not None:
        files = a.files
    else:
        tag = a.since or test_map.latest_tag()
        if not tag:
            ap.error("no v* tag to diff from (pass --since REF or --files)")
        files = test_map.changed_since(tag)
    main_root = lock.main_repo()
    recs = a.records or os.path.join(main_root, "build", "device-done")
    dirs = [recs] + ([os.path.join(main_root, "build")] if not a.records else [])
    pkgs = {"fm1": a.fm1 or os.path.join(ROOT, "build", f"frodhes-{a.version}.fwsc"),
            "move": a.move or move_package(a.version)}
    out_dir = os.path.join(main_root, "build", "release-devices", a.version + "-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        pl = plan(a.version, files, pkgs, dirs, a.full, a.null_proven, out_dir=out_dir)
    except ValueError as e:
        print(f"STOP: {e}")
        return 2
    if not pl["targets"]["move"]["reuse"] and not a.move:
        ok, why = move_tar_matches(pkgs["move"], os.path.join(ROOT, MOVE_SRC))
        if not ok:
            print(f"STOP: the Move tarball is not what move_install.py installs: {why} (make move)")
            return 2
    print("\n".join(plan_lines(pl)), flush=True)
    if a.dry_run:
        return 0
    runner = fake_runner(set(a.fake_fail)) if a.fake else shell_runner
    try:
        rec = run(pl, out_dir, runner, say=lambda m: print(time.strftime("%H:%M:%S ") + m, flush=True))
    except RuntimeError as e:
        print(f"STOP: {e}")
        return 2
    if a.fake:
        rec["fake"] = True
    os.makedirs(recs, exist_ok=True)
    path = a.out or os.path.join(recs, f"device-done-{a.version}" + ("-fake" if a.fake else "") + ".json")
    json.dump(rec, open(path, "w"), indent=1)
    for t, s in ((t, rec[t]) for t in TARGETS if t in rec):
        print(f"{NAMES[t]}: {s['verdict']}" + (f" (from {s['reused_from']})" if s.get("reused_from") else
                                               f" in {s['minutes']} min"))
    print(f"record: {path}")
    return code_of(rec)


if __name__ == "__main__":
    sys.exit(main())
