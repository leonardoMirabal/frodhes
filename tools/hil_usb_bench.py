#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The USB audio bench (BUGHUNT #24): the FM-1 running a bench build, recorded for N minutes. Two builds:
--kind ring (APP_DEFS=-DFELUCCA_UAC_TONE=1): usb.c puts triangles of period 100 frames in the USB ring instead of the
music, at 44.1 and at 48 kHz, L = -R, steps of 1000 LSB: the ring, the rate adjustment, the packets, the EP4 DMA and
the computer. --kind app (APP_DEFS=-DFELUCCA_UAC_TONE=2): frodhes.c app_audio's output replaced by triangles of period
147 frames after the engine and the effects ran: the platform's tail, the USB tap and the 44.1 -> 48 kHz resampler as
well (147 frames in are exactly 160 out: the recording repeats itself every 160 frames bit for bit, and no two
neighbouring frames are equal; at 44.1 kHz every 147). Every recorded frame must follow the one before: a repeated frame, a lost one or a
jump anywhere in the recording is counted and placed, with the firmware's own counters polled (CC 116 every
0.5 s: underruns, missed, overruns, the ring's low point, adjustments; tools/hil_must.py) beside it.

    tools/hil_usb_bench.py [--kind ring|app] [--minutes 5] [--rate 48000|44100] [--midi none|monkey|notes] [--out DIR]
    tools/hil_usb_bench.py --analyse FILE.npz        a saved recording judged again (no FM-1)
    tools/hil_usb_bench.py --self-test               the analysis on synthetic triangles with injected faults

--midi: none (no MIDI at all but the CC 116 polls: is it the stream alone?), monkey (tools/monkey.py's MIDI stream
as tools/hil_monkey.py sends it, seeds from 1: does MIDI-IN traffic trigger it?), notes (a chord every 2 s: some
traffic, the engine busy). Run each for the same minutes; the bench build plays the triangle whatever the engine
does, so the voices and effects only load the CPU.

Report: OUT/report-<kind>-<rate>-<midi>.json + the recording (OUT/rec-<kind>-<rate>-<midi>.npz, int16 L R).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil", "usb_bench")
STEP, TOP = 1000, 25000


def analyse(x, fs):
    """x (n, 2) int LSB: the bench triangle checked frame by frame. {'frames', 'start', 'repeat': [(t s, run)],
    'jump': [(t s, step)], 'lr': n frames where R != -L, 'ok': bool}. The stream starts with the ring's priming
    silence (and the device may start mid-triangle): judged from the first frame on the triangle to the last"""
    L = np.asarray(x[:, 0], np.int64)
    R = np.asarray(x[:, 1], np.int64)
    on = np.flatnonzero((np.abs(L) % STEP == 0) & (L != 0) & (np.abs(L) <= TOP))
    if len(on) < 200:
        return dict(frames=len(L), start=None, repeat=[], jump=[], lr=0, ok=False, why="no triangle in the recording")
    a, b = int(on[0]), int(on[-1]) + 1
    L, R = L[a:b], R[a:b]
    d = np.diff(L)
    rep = np.flatnonzero(d == 0)
    runs = []
    if len(rep):
        st = np.concatenate([[rep[0]], rep[1:][np.diff(rep) > 1]])
        for s in st:
            k = s
            while k + 1 < len(d) and d[k + 1] == 0:
                k += 1
            runs.append((round((a + s) / fs, 4), int(k - s + 2)))
    # a legal step is +-STEP; at the top / bottom the direction turns (the step is still +-STEP)
    bad = np.flatnonzero((np.abs(d) != STEP) & (d != 0))
    jumps = [(round((a + int(i)) / fs, 4), int(d[i])) for i in bad]
    lr = int((R != -L).sum())
    return dict(frames=int(b - a), start=round(a / fs, 4), repeat=runs, jump=jumps, lr=lr,
                ok=not runs and not jumps and not lr)


def analyse_app(x, fs):
    """x (n, 2) int LSB: the app-output bench (FELUCCA_UAC_TONE=2), periodic with P = 160 frames at 48 kHz (147 at
    44.1). Judged from the first stretch of 10 exact periods to the last: every frame must equal the one P before;
    a slip (frames inserted or lost) breaks that for about a period, then the wave runs on shifted: each slip placed
    and its shift found against the period before it (+k: k frames too many, a repeat; -k: k lost). Also every pair
    of equal neighbouring frames (the triangle has none). {'frames', 'start', 'repeat': [(t s, run)], 'slip': [(t s,
    shift or None)], 'mismatch': frames, 'ok'}"""
    P = 160 if fs == 48000 else 147
    x = np.asarray(x, np.int64)
    eqp = np.all(x[P:] == x[:-P], axis=1)                    # eqp[m]: frame m + P == frame m
    good = np.convolve(eqp.astype(int), np.ones(10 * P, int), "valid") == 10 * P
    gi = np.flatnonzero(good)
    if not len(gi):
        return dict(frames=len(x), start=None, repeat=[], slip=[], mismatch=0, ok=False, why="no periodic stretch")
    a, b = int(gi[0]), int(gi[-1]) + 11 * P                 # [a, b): frames whose period before / after is checked
    bad = np.flatnonzero(~eqp[a:b - P]) + a + P               # frame m + P differs from frame m
    slips, s0 = [], None
    for k, m in enumerate(bad):
        if s0 is None:
            s0 = m
        if k + 1 == len(bad) or bad[k + 1] - m > P:
            T = x[s0 - P:s0]                                  # the period before the slip
            e = int(m) + 1
            sh = None
            for l in range(-8, 9):
                idx = (np.arange(e, min(e + P, len(x))) - (s0 - P) - l) % P
                if len(idx) == P and np.all(x[e:e + P] == T[idx]):
                    sh = l
                    break
            slips.append((round(int(s0) / fs, 4), sh))
            s0 = None
    eq = np.flatnonzero(np.all(x[a + 1:b] == x[a:b - 1], axis=1)) + a
    # (the wave's own: how the 160 frames fall on the triangle depends on where it started against the resampler's
    # phase, and some starts put two equal frames on each turn, every period: FM-1 2026-10-26, --midi none, 181139 of
    # them, slip 0, mismatch 0. Only equal neighbours the period before did not have are counted)
    eq = eq[(eq < a + P) | ~np.all(x[eq + 1 - P] == x[eq - P], axis=1)] if len(eq) else eq
    eq = eq[eq >= a + P] if len(eq) else eq
    runs = []
    for i in eq:
        if runs and runs[-1][2] == i - 1:
            runs[-1][1] += 1
            runs[-1][2] = i
        else:
            runs.append([round(int(i) / fs, 4), 2, int(i)])
    peak = int(np.abs(x[a:b, 0]).max())
    level_ok = APP_PEAK[0] <= peak <= APP_PEAK[1]
    return dict(frames=int(b - a), start=round(a / fs, 4), repeat=[(t, n) for t, n, _ in runs], slip=slips,
                mismatch=int(len(bad)), peak=peak, level_ok=level_ok, ok=not slips and not runs and level_ok)


APP_PEAK = (7900, 8250)   # the app bench's peak through the resampler, LSB (USB VOL fixed: MASTER does not scale it)


def app_wave(n, fs=48000, ph0=0):
    """the app bench's recording as the resampler makes it (P frames repeated; tests/py/test_hil_usb_bench.py checks
    it against usb.c's own resampler): a stand-in for --self-test"""
    P = 160 if fs == 48000 else 147
    t = (np.arange(n) + ph0) % P
    L = np.round(8041 * (1 - 2 * np.abs(2 * t / P - 1)) + 37 * np.sin(2 * np.pi * 3 * t / P)).astype(np.int64)
    return np.stack([L, -L], 1)


def triangle(n, ph0=0):
    ph = (np.arange(n) + ph0) % 100
    q = np.where(ph < 50, ph, 100 - ph)
    L = q * STEP - TOP
    return np.stack([L, -L], 1)


def self_test():
    x = np.concatenate([np.zeros((300, 2), int), triangle(48000 * 3, 17)])
    r = analyse(x, 48000)
    assert r["ok"] and r["start"] == round(300 / 48000, 4), r
    y = x.copy()
    y = np.insert(y, 50000, y[49999], axis=0)                 # one frame repeated (an underrun's copy)
    y = np.insert(y, 90000, [y[89999]] * 3, axis=0)           # three more copies
    y = np.delete(y, [120000], axis=0)                        # one frame lost
    r = analyse(y, 48000)
    assert [run for _, run in r["repeat"]] == [2, 4] and len(r["jump"]) == 1 and r["jump"][0][1] in (2000, -2000, 0), r
    z = x.copy()
    z[70000, 1] += 1
    assert analyse(z, 48000)["lr"] == 1
    print("self-test ok: a clean triangle passes; 1 + 3 copies, a lost frame and an L / R mismatch are found")
    x = np.concatenate([np.zeros((300, 2), int), app_wave(48000 * 3, 48000, 17)])
    r = analyse_app(x, 48000)
    assert r["ok"] and r["start"] == round(300 / 48000, 4), r
    y = np.insert(x, 50000, x[49999], axis=0)
    y = np.insert(y, 90000, [y[89999]] * 3, axis=0)
    y = np.delete(y, [120000], axis=0)
    r = analyse_app(y, 48000)
    assert [sh for _, sh in r["slip"]] == [1, 3, -1] and [n for _, n in r["repeat"]] == [2, 4], r
    print("self-test ok (--kind app): a clean wave passes; 1 + 3 copies and a lost frame are found and their shift")
    return 0


def midi_events(kind, seconds):
    """[(t s, bytes)] for --midi"""
    if kind == "none":
        return []
    if kind == "notes":
        ev = []
        for k in range(int(seconds // 2)):
            for n in (48, 55, 60, 64, 67):
                ev.append((2.0 * k + 0.1, [0x90, n, 90]))
                ev.append((2.0 * k + 1.6, [0x80, n, 0]))
        return ev
    import hil_monkey as hm
    ev, t0, s = [], 0.0, 1
    while t0 < seconds:
        ev += [(t0 + t, b) for t, b in hm.segment(s)]
        ev += [(t0 + hm.SEG_S + hm.PANIC_S, m) for m in hm.panic_msgs()]
        t0 += hm.SEG_S + 1.0
        s += 1
    return sorted([e for e in ev if e[0] < seconds], key=lambda e: e[0])


def run(a):
    import sounddevice as sd
    import hil_must as hm
    dev = hm.Dev()
    usb = hm.find_usb(sd)
    if usb is None:
        print("no FM-1 USB audio input")
        return 2
    os.makedirs(a.out, exist_ok=True)
    secs = 60.0 * a.minutes
    ev = midi_events(a.midi, secs)
    for k in range(3):                         # USB VOL fixed (MASTER does not scale the stream), then checked on a
        dev.cc(114, 127)                       # 3 s take before the real one: the bench's wave at its level
        time.sleep(0.3)
        pre = hm.Rec(usb, a.rate, 2, (0, 1), 3.0, switch_rate=True, sd=sd)
        time.sleep(3.2)
        xp = np.clip(np.round(np.asarray(pre.stop(), float) * 32768), -32768, 32767).astype(np.int64)
        rp = (analyse_app if a.kind == "app" else analyse)(xp, a.rate)
        if rp.get("start") is not None and rp.get("level_ok", True):
            break
        print(f"pre-check {k + 1}: " + ("no bench wave" if rp.get("start") is None else f"peak {rp.get('peak')} LSB")
              + ": USB VOL set again")
    else:
        print("the bench wave is not there at its level (the bench build installed? --kind right?): stopped")
        dev.close()
        return 2
    load0 = host_load()
    rec = hm.Rec(usb, a.rate, 2, (0, 1), secs + 5.0, switch_rate=True, sd=sd)
    try:
        time.sleep(1.0)
        M = dev.mido.Message
        with hm.Poller(dev) as acc:
            t0 = time.monotonic()
            for t, b in ev:
                while time.monotonic() - t0 < t:
                    time.sleep(0.0005)
                with dev.lock:
                    dev.out.send(M.from_bytes(b))
            while time.monotonic() - t0 < secs:
                time.sleep(0.05)
            with dev.lock:
                for c in range(16):
                    dev.out.send(M.from_bytes([0xB0 | c, 123, 0]))
                    dev.out.send(M.from_bytes([0xB0 | c, 64, 0]))
    finally:
        x = rec.stop()
        dev.close()
    load1 = host_load()
    xi = np.clip(np.round(np.asarray(x, float) * 32768), -32768, 32767).astype(np.int16)
    tag = f"{a.kind}-{a.rate}-{a.midi}"
    np.savez_compressed(os.path.join(a.out, f"rec-{tag}.npz"), usb=xi, fs=a.rate, flags=rec.flags, kind=a.kind)
    r = (analyse_app if a.kind == "app" else analyse)(xi.astype(np.int64), a.rate)
    rows = hm.gate_counters(acc, a.rate)
    js = dict(date=time.strftime("%Y-%m-%d %H:%M"), kind=a.kind, rate=a.rate, midi=a.midi, minutes=a.minutes,
              midi_messages=len(ev), recorder_flags=rec.flags, bench=r, counters=acc.summary(),
              counter_rows=[list(map(str, x)) for x in rows], load=[load0, load1])
    json.dump(js, open(os.path.join(a.out, f"report-{tag}.json"), "w"), indent=1, default=str)
    print_report(js)
    return 0 if r["ok"] and not [x for x in rows if x[3] == "FAIL"] else 1


def host_load():
    """the computer's 1-minute load average (a starved recorder is the rig's problem, not the FM-1's)"""
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def print_report(js):
    r = js["bench"]
    if "slip" in r:
        print(f"app bench, {js.get('rate')} Hz, MIDI {js.get('midi')} ({js.get('midi_messages', 0)} messages), "
              f"{js.get('minutes')} min: {r['frames']} frames judged from {r['start']} s; slips {len(r['slip'])}, "
              f"equal neighbours {len(r['repeat'])}, frames off the period {r['mismatch']}, peak {r.get('peak')} LSB"
              f"{'' if r.get('level_ok', True) else ' (NOT the fixed level: USB VOL / MASTER?)'}; "
              f"recorder flags {js.get('recorder_flags')}")
        for t, sh in r["slip"][:20]:
            print(f"  slip at {t:.4f} s: shift {sh} frames" + (" (inserted: a repeat)" if sh and sh > 0 else ""))
    else:
        print(f"{js.get('rate')} Hz, MIDI {js.get('midi')} ({js.get('midi_messages', 0)} messages), "
              f"{js.get('minutes')} min: {r['frames']} frames judged from {r['start']} s; repeats {len(r['repeat'])}, "
              f"jumps {len(r['jump'])}, "
              f"L / R mismatches {r['lr']}; recorder flags {js.get('recorder_flags')}")
    for t, n in r["repeat"][:20]:
        print(f"  repeat at {t:.4f} s: {n} equal frames")
    for t, s in r.get("jump", [])[:20]:
        print(f"  jump at {t:.4f} s: step {s}")
    for row in js.get("counter_rows", []):
        print("  counter: " + " | ".join(row))
    if js.get("load"):
        print(f"host load (1 min) before / after: {js['load'][0]} / {js['load'][1]}")
    print("BENCH " + ("PASS" if r["ok"] else "FAIL"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", default="ring", choices=("ring", "app"), help="the bench build installed (see above)")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--midi", default="none", choices=("none", "monkey", "notes"))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--analyse")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.analyse:
        z = np.load(a.analyse)
        kind = str(z["kind"]) if "kind" in z.files else "ring"
        fn = analyse_app if kind == "app" else analyse
        js = dict(rate=int(z["fs"]), bench=fn(z["usb"].astype(np.int64), int(z["fs"])))
        print_report(js)
        return 0 if js["bench"]["ok"] else 1
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
