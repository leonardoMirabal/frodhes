#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The plugin monkey on the Move (M1-12 / M1-23): tests/py/test_move_monkey.py's seeded streams, played by the
plugin as Schwung has it loaded in slot 1, with the click detector, for N minutes.

    tools/move_monkey.py [--minutes 30] [--seed 1000] [--out DIR] [--ws URL] [--host USER@HOST] [--dry-run]

Each seed is one take of about 34 s over the tap path (tools/move_hil.py TapRig's transport, move/move_bridge.h
dev_play): the stream's MIDI (monkey.gen: notes on any channel, lost note-offs, the pedal, bend, every CC, program
changes, MIDI clock and transport, garbage statuses) goes to the plugin's own player, so no Move track plays along;
the Move-only parts (parameter writes through set_param on every chain_params key, preset browses, TYPE changes and
preset loads under a held, pedalled chord) go over Schwung's WebSocket at their time in the take, paced (PACE: at
most BURST writes in any WINDOW_S, MIN_GAP_S apart, so the manager's 32-entry set ring never fills). The state reads
and loads (getstate / setstate) have no WebSocket path, and the dev player refuses SysEx (the stream's garbage
statuses 0xF0 / 0xF7): they are counted and left out (the host monkey runs them).
Then monkey.panic()'s MIDI end, with the Move-only latches let go before it (s_sustain, s_mch: RESETS). The plugin
starts a take on its 2560 ms grid, so a write lands between its time and LATE_MS after it; the writes stop
CUT_MS before the panic and the resets go RESET_MS before it, and a write that cannot be sent before the panic's
deadline is counted late (a late reset fails the seed: the checks after the panic would not mean anything).

The checks, per seed (tests/py/test_move_monkey.analyse, the host monkey's own): the take arrives (the plugin
alive), the limiter's ceiling, no rail, exact silence 10-12 s after the panic (no stuck note, tail or DC), the
probe note sounds and is never louder after its key-up than held (+1 dB). Also: the dev player's own counters
(dropped frames, refused pieces, aborted: fail; late events: information), Schwung's errors on the WebSocket (a
"web param set ring full" fails: a write was lost), and the plugin was not loaded again during the run (boot.txt's
starts). The click detector (tools/move_hil.py glitches, hil_crackle's) runs on every take: repeat and stale (a
block played twice, a stale buffer) fail; zero, clip and click are information, because a random stream makes the
parameter jumps the FM-1's monkey also only counts (tools/hil_monkey.py). A failing seed keeps its WAV, script and
report under OUT/fail-<seed>/; every seed's line goes to OUT/report.json.

The state of slot 1 is read first and written back at the end (move_hil.KeptState), also after ctrl-C. Takes the
MOVE lock. Never run from a test or a gate without the user's OK to use the Move (the device rules)."""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests", "py")]
import monkey  # noqa: E402
import test_move_monkey as tmm  # noqa: E402

OUT = os.path.join(ROOT, "build", "move", "monkey-device")
LEAD_MS = 100                     # dev_play "go 100": the take starts on the first 2560 ms mark at least 100 ms ahead
LATE_MS = LEAD_MS + 2560          # the latest start after the go: a write meant for t is sent at go + LATE_MS + t
CUT_MS = 3200                     # the random writes stop this long before the panic
RESET_MS = 3000                   # the Move-only resets go this long before the panic
SAFE_MS = 300                     # every write must land this long before the panic (at the earliest start)
MIN_GAP_S, BURST, WINDOW_S = 0.02, 8, 0.25    # PACE: >= 20 ms apart, at most 8 in any 250 ms
FAIL_KINDS = ("repeat", "stale")              # the click detector's kinds that fail a take (the rest: information)
PLAYER_REFUSES = (0xF0, 0xF7)                 # SysEx: the dev player refuses it (move_bridge.h mp_set: counted bad)
TAIL_MS = 1000                                # the take runs this long after the probe's key-up


def segment(seed):
    """one seed's take: {'events': [(ms, status, d1, d2)], 'writes': [(ms, key, value, is_reset)], 'mk', 'dur_ms',
    'skipped': {kind: n}}; the writes on the take's own clock, the random ones cut before the panic, the resets
    moved RESET_MS before it"""
    text, mk = tmm.gen(seed)
    t0 = mk["before"][1] + 7                             # (monkey.panic(t0): its first marks are t0 - 17, t0 - 7)
    ev, wr, skipped = [], [], {}
    resets = {k for k, _ in tmm.RESETS}
    for ln in text.splitlines():
        t, kind, *rest = ln.split()
        t = int(t)
        if kind == "midi":
            st, d1, d2 = (int(x, 0) for x in rest)
            if st in PLAYER_REFUSES or not 0x80 <= st <= 0xFF:
                skipped["sysex"] = skipped.get("sysex", 0) + 1
                continue
            ev.append((t, st, d1, d2))
        elif kind == "key":
            k, v = rest
            if k in resets and t >= t0 - 100:            # (the panic's own resets: placed below)
                continue
            if t <= t0 - CUT_MS:
                wr.append((t, k, v, False))
            else:
                skipped["late-cut"] = skipped.get("late-cut", 0) + 1
        elif kind == "preset":
            if t <= t0 - CUT_MS:
                wr.append((t, "preset", rest[0], False))
            else:
                skipped["late-cut"] = skipped.get("late-cut", 0) + 1
        elif kind in ("getstate", "setstate"):
            skipped[kind] = skipped.get(kind, 0) + 1
    wr += [(t0 - RESET_MS, k, v, True) for k, v in tmm.RESETS]
    dur = mk["note"][1] + TAIL_MS
    return {"events": [e for e in ev if e[0] < dur], "writes": sorted(wr, key=lambda w: w[0]), "mk": mk,
            "dur_ms": dur, "t0": t0, "skipped": skipped}


class Pace:
    """the WebSocket writes' pace: at(now) -> the earliest time the next write may go (>= MIN_GAP_S after the last,
    at most BURST in any WINDOW_S)"""

    def __init__(self, gap=MIN_GAP_S, burst=BURST, window=WINDOW_S):
        self.gap, self.burst, self.window, self.sent = gap, burst, window, []

    def at(self, now):
        t = now
        if self.sent:
            t = max(t, self.sent[-1] + self.gap)
        if len(self.sent) >= self.burst:
            t = max(t, self.sent[-self.burst] + self.window)
        return t

    def mark(self, t):
        self.sent.append(t)
        del self.sent[:-self.burst]


def schedule(writes, t_go, t0, pace=None):
    """the wall times of the writes (the take's go at t_go, s): [(wall s, key, value, is_reset, late)], each write at
    t_go + LATE_MS + its ms or later (paced); late: past the deadline (t_go + LEAD_MS + t0 - SAFE_MS: at the earliest
    start, the write would land after it)"""
    pace = pace or Pace()
    dead = t_go + (LEAD_MS + t0 - SAFE_MS) / 1000.0
    out = []
    for t, k, v, rs in writes:
        w = pace.at(t_go + (LATE_MS + t) / 1000.0)
        pace.mark(w)
        out.append((w, k, v, rs, w > dead))
    return out


def guard_events(events):
    """the stream's notes and CCs for the click detector's guards (move_hil.glitches): [(s, kind, a, b)]"""
    out = []
    for t, st, d1, d2 in events:
        hi = st & 0xF0
        if hi == 0x90 and d2:
            out.append((t / 1000.0, "on", d1, d2))
        elif hi == 0x80 or (hi == 0x90 and not d2):
            out.append((t / 1000.0, "off", d1, 0))
        elif hi == 0xB0:
            out.append((t / 1000.0, "cc", d1, d2))
    return out


def judge(x, seg, info, ws_errors, late_resets, clicks):
    """[(check, message)]: the host monkey's checks on the take x ((n, 2) int32 from the take's start), the dev
    player's counters (info: the tap's json), Schwung's errors, the late resets, the click detector's events"""
    bad = list(tmm.analyse(0, "", x, seg["mk"]))
    for k in ("dropped", "bad", "aborted"):
        if info.get(k):
            bad.append(("player", "%s %s" % (k, info[k])))
    if info.get("played", 0) < len(seg["events"]):
        bad.append(("player", "played %s of %d events" % (info.get("played"), len(seg["events"]))))
    for e in ws_errors:
        bad.append(("schwung", e))
    if late_resets:
        bad.append(("reset", "%d reset(s) past the deadline: the checks after the panic do not hold" % late_resets))
    t0 = seg["t0"] / 1000.0
    for t, k, d in clicks:
        if k in FAIL_KINDS:
            bad.append((k, "%.3f s%s: %s" % (t, " (after the panic)" if t >= t0 else "", d)))
    return bad


def _starts(host):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                        "cat /data/UserData/schwung/module_data/frodhes/boot.txt"], capture_output=True, text=True)
    for f in r.stdout.split():
        if f.startswith("starts="):
            return int(f[7:])
    return None


def run_seed(remote, fetch, seed, out_dir, stamp, say=print):
    import move_hil as mh
    import move_ws
    import wave
    seg = segment(seed)
    take = "mk%s-%d" % (stamp, seed)
    errors = []
    remote.play(0, take, seg["dur_ms"], seg["events"], lead_ms=LEAD_MS)
    t_go = time.monotonic() - 0.2                        # (play() pumps 0.2 s after its go)
    sch = schedule(seg["writes"], t_go, seg["t0"])
    late = late_resets = 0
    for w, k, v, rs, lt in sch:
        while True:
            left = w - time.monotonic()
            if left <= 0:
                break
            try:
                remote.pump(min(left, 0.05))
            except move_ws.SchwungError as e:
                errors.append(str(e))
        try:
            remote.set_param(0, "synth:" + k, v)
        except move_ws.ConnectionClosed as e:
            errors.append("connection closed: %s" % e)
            break
        late += lt
        late_resets += lt and rs
    end = t_go + (LATE_MS + seg["dur_ms"]) / 1000.0 + 0.5
    while time.monotonic() < end:
        try:
            remote.pump(min(0.2, max(0.01, end - time.monotonic())))
        except move_ws.SchwungError as e:
            errors.append(str(e))
    got, tw = None, time.monotonic()
    while got is None and time.monotonic() - tw < 15:
        got = fetch(take)
        if got is None:
            time.sleep(0.5)
    if got is None:
        return {"seed": seed, "bad": [("crash", "the tap %s did not arrive" % take)], "late": late}
    path, info = got
    with wave.open(path) as wv:
        x = np.frombuffer(wv.readframes(wv.getnframes()), "<i2").reshape(-1, 2).astype(np.int32)
    clicks = mh.glitches(x.astype(float), mh.FS, guard_events(seg["events"]))
    bad = judge(x, seg, info, errors, late_resets, clicks)
    n_cl = {}
    for _, k, _ in clicks:
        n_cl[k] = n_cl.get(k, 0) + 1
    rep = {"seed": seed, "events": len(seg["events"]), "writes": len(seg["writes"]), "late": late,
           "skipped": seg["skipped"], "player": {k: info.get(k) for k in ("played", "late", "dropped", "bad", "aborted")},
           "peak": int(np.abs(x).max()) if len(x) else 0, "clicks": n_cl, "bad": bad}
    if bad:
        d = os.path.join(out_dir, "fail-%d" % seed)
        os.makedirs(d, exist_ok=True)
        os.replace(path, os.path.join(d, "take.wav"))
        json.dump({"rep": rep, "clicks": clicks[:200], "info": info}, open(os.path.join(d, "report.json"), "w"),
                  indent=1, default=str)
        with open(os.path.join(d, "script.txt"), "w") as f:
            f.write(tmm.gen(seed)[0])
    else:
        os.remove(path)
    say("seed %d: %d events, %d writes (%d late), player late %s, peak %d, clicks %s: %s" % (
        seed, rep["events"], rep["writes"], late, info.get("late"), rep["peak"], n_cl or "none",
        "FAIL " + "; ".join("%s: %s" % b for b in bad[:4]) if bad else "ok"))
    return rep


def main(argv=None):
    import move_hil as mh
    import move_install as mi
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--seeds", help="only these seeds, comma-separated (in place of --minutes)")
    ap.add_argument("--out")
    ap.add_argument("--ws", default=os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui"))
    ap.add_argument("--host", default=os.environ.get("MOVE_HOST", "ableton@move.local"))
    ap.add_argument("--dry-run", action="store_true", help="print the first seed's plan; nothing sent")
    a = ap.parse_args(argv)
    if a.dry_run:
        seg = segment(a.seed)
        sch = schedule(seg["writes"], 0.0, seg["t0"])
        print("seed %d: %d MIDI events, %d writes (%d late), %.1f s a take, skipped %s; about %d seeds in %.0f min" % (
            a.seed, len(seg["events"]), len(sch), sum(s[4] for s in sch), seg["dur_ms"] / 1000, seg["skipped"],
            a.minutes * 60 / (seg["dur_ms"] / 1000 + 8), a.minutes))
        return 0
    import move_ws
    out = a.out or os.path.join(OUT, mh.stamp())
    os.makedirs(out, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    reps = []
    with mi.move_lock("move_monkey"):
        s0 = _starts(a.host)
        with mh.KeptState(a.ws, 0) as ks:
            remote = move_ws.Remote(a.ws)
            remote.set_param(0, "synth:dev_play", "stop")
            remote.pump(0.5)
            fetch = mh.scp_fetch(a.host, os.path.join(out, "tap"))
            t_end, seed = time.monotonic() + a.minutes * 60, a.seed
            only = [int(x) for x in a.seeds.split(",")] if a.seeds else None
            try:
                while (len(reps) < len(only)) if only else (time.monotonic() < t_end):
                    if only:
                        seed = only[len(reps)]
                    try:
                        reps.append(run_seed(remote, fetch, seed, out, stamp))
                    except move_ws.ConnectionClosed:
                        remote = move_ws.Remote(a.ws)
                        reps.append({"seed": seed, "bad": [("schwung", "the WebSocket dropped")]})
                    seed += 1
                    if reps[-1]["bad"] and reps[-1]["bad"][0][0] == "crash":
                        break
            finally:
                try:
                    remote.set_param(0, "synth:dev_play", "stop")
                    remote.pump(0.3)
                    remote.close()
                except Exception:
                    pass
        s1 = _starts(a.host)
    fails = [r for r in reps if r["bad"]]
    reload = s0 is None or s1 != s0
    clicks = {}
    for r in reps:
        for k, n in r.get("clicks", {}).items():
            clicks[k] = clicks.get(k, 0) + n
    summ = {"seeds": len(reps), "first": a.seed, "failing": [r["seed"] for r in fails], "starts": (s0, s1),
            "clicks": clicks, "state": ks.report["verdict"],
            "late_writes": sum(r.get("late", 0) for r in reps),
            "player_late": sum((r.get("player") or {}).get("late") or 0 for r in reps)}
    json.dump({"summary": summ, "seeds": reps}, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
    ok = not fails and not reload and ks.report["verdict"] == "PASS"
    print("move monkey: %d seeds (%s), %d failing %s, plugin starts %s -> %s, clicks %s, state %s: %s" % (
        len(reps), a.seeds or "%d-%d" % (a.seed, a.seed + len(reps) - 1), len(fails), summ["failing"][:10], s0, s1, clicks or "none",
        ks.report["verdict"], "PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
