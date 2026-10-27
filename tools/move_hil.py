#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move's hardware-in-the-loop driver (M1-18), the offline skeleton: every subcommand plans its device commands
and prints them (DRY RUN by default; --go runs the ones that are implemented). The detectors are the FM-1's
(tools/hil_crackle.py) with the Move's timing; the null test is tools/device_null.py.

    tools/move_hil.py [--go] facts           the device facts (kernel, CPU, RAM, glibc, the Schwung version)
    tools/move_hil.py [--go] install|rollback|info [--src DIR]   tools/move_install.py
    tools/move_hil.py [--go] logs            the module's logs (its folder, /tmp) -> build/move/hil/<time>/
    tools/move_hil.py [--go] cpu [--file F]  the dev build's per-callback timer ring: pulled, or F read; the worst
                                             callback against U12 (working limit 725 us, design 600 us)
    tools/move_hil.py crackle [--engine E] [--analyze WAV]   the takes (hil_crackle's stimulus) and their minutes;
                                             --analyze: the stale / zero / clip detectors on a recording
    tools/move_hil.py must                   MT1, the Move's must-pass (smoke, CPU, crackle, null): the plan; running
                                             it needs the glue and D1's answers (the MIDI path, the ring's location)
    tools/move_hil.py null [--self-test]     tools/device_null.py's takes / proof

Never run from a test or a gate without the user's OK to use the Move (the device rules); --host USER@HOST (default
$MOVE_HOST, else ableton@move.local)."""
import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_install as mi  # noqa: E402

OUT = os.path.join(ROOT, "build", "move", "hil")
FS = 44100
BLOCK = 128                                  # the Move's callback: 128 frames (2902 us)
BLOCK_US = BLOCK * 1e6 / FS
U12_LIMIT_US = 725                           # plan 05 U12: the working limit (a quarter of the block); D1 sets it
U12_DESIGN_US = 600
FACTS = ("uname -a; echo; cat /etc/os-release; echo; ldd --version | head -1; echo; free -m; echo; nproc; "
         "grep -E 'model name|Hardware|CPU part' /proc/cpuinfo | sort | uniq -c; echo; "
         "cat /data/UserData/schwung/VERSION 2>/dev/null; df -h /data")
# the dev ring as the worker logs it (to agree with agent A's move_bridge.h when it lands): one line per callback,
# "<callback index> <us in render_block> [<frames rendered this call>]"; lines starting with # are comments
RING_LINE = re.compile(r"^\s*(\d+)\s+(\d+)(?:\s+(\d+))?\s*$")
MT1 = [("smoke: install, load, a note per engine, the TYPE pages", 5),
       ("cpu: 10 and 16 voices per engine, FX off / on, the ring", 10),
       ("crackle: the worst and the recipe takes per engine (line out + the ring)", 15),
       ("null: the device null set (device_null.py) on the ring and the line out", 10)]


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def facts(t, st=None):
    d = os.path.join(OUT, st or stamp())
    t.out("+ mkdir -p " + d)
    if t.go:
        os.makedirs(d, exist_ok=True)
    code, out = t.run(FACTS)
    if t.go and not code:
        open(os.path.join(d, "facts.txt"), "w").write(out)
    return d


def logs(t, st=None):
    d = os.path.join(OUT, st or stamp())
    if t.go:
        os.makedirs(d, exist_ok=True)
    src = t.mods + "/" + mi.MODULE_ID
    if t.local:
        t._exec(["sh", "-c", "cp %s/*.log %s/ 2>/dev/null || true" % (mi.q(src), mi.q(d))])
    else:
        t._exec(["scp", *mi.SSH_OPTS, "-q", "%s:%s/*.log" % (t.host, src), d + "/"])
        t._exec(["scp", *mi.SSH_OPTS, "-q", "%s:/tmp/frodhes*.log" % t.host, d + "/"])
    return d


def parse_ring(text):
    """[(callback, us, frames)] from the ring's log"""
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("#") or not ln.strip():
            continue
        m = RING_LINE.match(ln)
        if not m:
            raise ValueError("ring log: %r" % ln)
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
    return out


def cpu_summary(rows, limit=U12_LIMIT_US):
    """{n, worst, p99, over, missing, verdict}: over = callbacks above the limit; missing = gaps in the callback
    index (the ring overran: the worker fell behind, the data is incomplete)"""
    if not rows:
        return {"n": 0, "verdict": "no data"}
    us = sorted(r[1] for r in rows)
    idx = [r[0] for r in rows]
    missing = sum(max(0, b - a - 1) for a, b in zip(idx, idx[1:]))
    over = sum(1 for u in us if u > limit)
    s = {"n": len(us), "worst": us[-1], "p99": us[min(len(us) - 1, int(0.99 * len(us)))], "over": over,
         "missing": missing, "limit": limit, "block_us": round(BLOCK_US)}
    s["verdict"] = "FAIL" if over or missing else ("PASS (over the design target %d us)" % U12_DESIGN_US
                                                   if s["worst"] > U12_DESIGN_US else "PASS")
    return s


def cpu(t, path=None):
    if path:
        s = cpu_summary(parse_ring(open(path).read()))
    else:
        code, out = t.run("cat %s/%s/cpu-ring.log" % (t.mods, mi.MODULE_ID))
        if not t.go:
            return None
        s = cpu_summary(parse_ring(out))
    t.out(" ".join("%s=%s" % kv for kv in s.items()))
    return s


def crackle_plan(engine="all", out=print):
    import hil_crackle as hc
    total = 0.0
    for e in (hc.ENGINES if engine == "all" else (engine,)):
        for name, _, events, dur in hc.takes(e, nv=16):
            out("%-24s %5.1f s  %5d events  (16 voices; the line out + the ring)" % (name, dur, len(events)))
            total += dur
    out("%.1f min of takes; blocks of %d frames (%.0f us)" % (total / 60, BLOCK, BLOCK_US))
    return total


def crackle_analyze(path, out=print):
    """the detectors that need no USB reference: stale repeats, dropouts to zero, clipping (hil_crackle's)"""
    import numpy as np
    import wave
    import hil_crackle as hc
    w = wave.open(path)
    fs, ch = w.getframerate(), w.getnchannels()
    q = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.int64).reshape(-1, ch)
    if ch == 1:
        q = np.repeat(q, 2, axis=1)
    ev = [("stale", f, d) for f, d in hc.det_stale(q)]
    ev += [("zero", f, d) for f, d in hc.det_zero(q.astype(float), fs, exact=True)]
    ev += [("clip", f, d) for f, d in hc.det_clip(q.astype(float), 32767)]
    for kind, f, d in sorted(ev, key=lambda e: e[1]):
        out("%-6s %8.3f s  %s" % (kind, f / fs, d))
    out("%d event(s)" % len(ev))
    return ev


def must(out=print):
    for name, minutes in MT1:
        out("%3d min  %s" % (minutes, name))
    out("%d min in all. Not runnable yet: needs the glue (move/move_plugin.c) and D1's answers" % sum(m for _, m in MT1))
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("facts", "install", "rollback", "info", "logs", "cpu", "crackle", "must", "null"))
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--target-dir", help="a local folder in place of the Move's module folder (tests)")
    ap.add_argument("--src", default=os.path.join(ROOT, "build", "move", mi.MODULE_ID))
    ap.add_argument("--file", help="cpu: read this ring log instead of pulling it")
    ap.add_argument("--engine", default="all")
    ap.add_argument("--analyze", help="crackle: a recording (WAV, 16 bit) to run the detectors on")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    t = mi.Target(host=None if a.target_dir else a.host, local_dir=a.target_dir, go=a.go or a.cmd == "info")
    if a.cmd in ("facts", "install", "rollback", "logs", "cpu") and not a.go and not a.file:
        print("(dry run: add --go to execute; %s)" % (a.target_dir or a.host))
    if a.cmd == "facts":
        facts(t)
    elif a.cmd == "install":
        mi.install(t, a.src)
    elif a.cmd == "rollback":
        mi.rollback(t)
    elif a.cmd == "info":
        mi.info(t)
    elif a.cmd == "logs":
        logs(t)
    elif a.cmd == "cpu":
        s = cpu(t, a.file)
        return 1 if s and s["verdict"].startswith("FAIL") else 0
    elif a.cmd == "crackle":
        if a.analyze:
            return 1 if crackle_analyze(a.analyze) else 0
        crackle_plan(a.engine)
    elif a.cmd == "must":
        return must()
    elif a.cmd == "null":
        import device_null
        if a.self_test:
            return 0 if all(w == g for _, w, g, _ in device_null.self_test()) else 1
        device_null.dry_run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
