# SPDX-License-Identifier: GPL-3.0-only
"""PHA SYNC under an external MIDI clock, the FM-1 finding of hil_clock on the 0.9.5 candidate (int-final 82c6bed,
2026-10-25): 'period start 100 BPM from beat 8: 597.06 ms / sent 600.27 ms (-0.53 %)' over USB and TRS, every other
synced LFO within 0.1 %. Not the phaser: hil_clock's own take rendered on the host gives the same -0.6 % while the
firmware's phaser phase (ph_ph, at every dump) follows the sent beats to 0.01 %. The analysis read the phase per
beat from the band with the largest mean modulation alone; on the phaser's spectral feature that band scattered
0.05-0.1 cycle from beat to beat (the restrike's decay right after it, the chord's partials beating), and the
8-beat slope after the restart (7 windows, a restrike in them) came out -0.4 .. -0.6 % on the host and on the FM-1
(c502dad: -0.37 % USB, +0.77 % TRS). Now every band is used, each turned by its mean phase and weighted by its mean
(the same as before for a one-band feature: the tremolo's and the motor's numbers do not change)."""
import os
import sys

import numpy as np

import hostrun

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_clock as C  # noqa: E402

FS = 44100
T0 = 2.0                                                     # the take's start on the host (s)
MSG = {"clock": "0xF8 0 0", "start": "0xFA 0 0", "continue": "0xFB 0 0", "stop": "0xFC 0 0"}


def line(t, msg):
    k = msg[0]
    m = MSG.get(k) or (f"0x90 {msg[1]} {msg[2]}" if k == "note_on" else f"0x80 {msg[1]} 0" if k == "note_off"
                       else f"0xB0 {msg[1]} {msg[2]}")
    return f"{int(t * 1000)} midi {m}"


def render_take(name, bpm=120, dumps_every=None, hold_first_ms=0):
    """hil_clock's take of item name exactly (its CCs, chord, strikes, clock, Start / Stop), on the host: (run, ev,
    the dump times); hold_first_ms: the first reference note (on and off) reaches the firmware that much later than
    sent (a flash write holding the main loop)"""
    it = C.items()[name]
    take = C.plan_take(name, it, bpm, C.CHANGE if bpm == C.CHANGE[0] else None)
    lines = [hostrun.cc(20 + k, c, v) for k, (c, v) in enumerate(it["ccs"])
             if not (it["kind"] == "delay" and c == 88)]           # (as run_device: the delay on after the references)
    stamps = []
    r0 = take["refs"][0]
    held = {r0, next(i for i in range(r0 + 1, len(take["sched"])) if take["sched"][i][2][0] == "note_off")}
    for i, (t, _, msg) in enumerate(take["sched"]):
        lines.append(line(t + T0 + (hold_first_ms / 1000 if i in held else 0), msg))
        stamps.append(int((t + T0) * 1000) / 1000.0)        # (the host takes each message in its ms)
    ev = C.take_tau(take, stamps, 0.0, FS, FS)
    dumps = []
    if dumps_every:
        for sg in ev["segs"]:
            bb = ev["beats"][sg["i0"]:sg["i1"]]
            dumps += [int(1000 * x) for x in np.arange(bb[0] + sg["lock"] * (bb[1] - bb[0]), bb[-1], dumps_every)]
    end = int((take["dur"] + T0) * 1000) + 200
    r = hostrun.run(f"hilclock-{name}-{bpm}", lines + [f"{t} dump" for t in dumps], end_ms=end)
    return r, ev, dumps


def test_phaser_take_period_and_the_firmware_phase():
    """the phaser's take at 120 -> 100 BPM with the restart: hil_clock's period within its 0.5 % in every segment
    (it read -0.59 % after the restart), and the firmware's own phaser phase on the sent beats (SYNC 1/4: a cycle a
    beat, phase 0 on the beat) from beat 6 of each segment to 0.05 % of the period and 2 % of a beat"""
    r, ev, dumps = render_take("phaser", dumps_every=0.37 * 0.6)
    x = (r.L + r.R) / 2 / 32767
    res = C.lfo_analyze(x, FS, ev)
    assert len(res["periods"]) == 3, res["periods"]
    for p in res["periods"]:
        assert p["ok"], p
        assert abs(p["err_pct"]) < 0.3, p
    for sg in ev["segs"]:
        bb = np.array(ev["beats"][sg["i0"]:sg["i1"]])
        ts = [t for t in dumps if bb[0] + 6 * (bb[1] - bb[0]) <= t / 1000 < bb[-1]]   # (a change: ~0.12 beat at
                                                             # first, out with a beat's time constant)
        tm = np.array([int(r.state(t)["wavn"]) / FS for t in ts])
        ph = np.array([int(r.state(t)["phph"]) / 2 ** 32 for t in ts])
        pos = C._pos(bb, tm)
        e = (ph - pos + 0.5) % 1.0 - 0.5
        assert np.max(np.abs(e)) < 0.02, (sg["kind"], sg["bpm"], np.max(np.abs(e)))
        assert abs(np.polyfit(pos, e, 1)[0]) < 0.0005, (sg["kind"], sg["bpm"])


def test_phaser_takes_at_90_and_137():
    """the other tempos' phaser takes: within 0.3 % (90 read +0.46 %, by the same band's scatter)"""
    for bpm in (90, 137):
        r, ev, _ = render_take("phaser", bpm)
        res = C.lfo_analyze((r.L + r.R) / 2 / 32767, FS, ev)
        for p in res["periods"]:
            assert p["ok"] and abs(p["err_pct"]) < 0.3, (bpm, p)


def test_latency_from_the_smaller_reference():
    """two reference notes 0.4 s apart; the first held up 40 ms (the pattern's auto save on the FM-1, 2026-10-25):
    the take's latency is the second's (as without the stall), both reported, flagged as over 5 ms apart, and the
    takes still judged right. Without the stall: both within 3 ms, not flagged"""
    for name, ana in (("trem", C.lfo_analyze), ("delay_dig", C.delay_analyze), ("arp", C.grid_analyze)):
        lat, verdicts = {}, {}
        for hold in (0, 40):
            r, ev, _ = render_take(name, hold_first_ms=hold)
            res = ana((r.L + r.R) / 2 / 32767, FS, ev)
            a, b = res["ref_latency_ms"]
            lat[hold] = res["latency_ms"]
            if hold:
                assert res["ref_spread"] and 35 < a - b < 45 and res["latency_ms"] == b, (name, res["ref_latency_ms"])
            else:
                assert not res["ref_spread"] and abs(a - b) < 3, (name, res["ref_latency_ms"])
            for p in res.get("periods", []):
                assert p["ok"], (name, hold, p)
            for sg in res.get("segs", []):
                assert sg["ok"], (name, hold, sg)
            verdicts[hold] = [p.get("ok") for p in res.get("probes", [])]   # (the host is not in the device's dry
        assert verdicts[0] == verdicts[40], (name, verdicts)                # state: the same echoes either way)
        assert abs(lat[40] - lat[0]) < 3, (name, lat)
