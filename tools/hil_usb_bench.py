#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The USB audio bench (BUGHUNT #24): the FM-1 running a bench build (APP_DEFS=-DFELUCCA_UAC_TONE=1: usb.c puts
triangles of period 100 frames in the USB ring instead of the music, at 44.1 and at 48 kHz, L = -R, steps of 1000
LSB), recorded for N minutes. Every recorded frame must follow the one before: a repeated frame, a lost one or a
jump anywhere in the recording is counted and placed, with the firmware's own counters polled (CC 116 every
0.5 s: underruns, missed, overruns, the ring's low point, adjustments; tools/hil_must.py) beside it.

    tools/hil_usb_bench.py [--minutes 5] [--rate 48000|44100] [--midi none|monkey|notes] [--out build/hil/usb_bench]
    tools/hil_usb_bench.py --analyse FILE.npz        a saved recording judged again (no FM-1)
    tools/hil_usb_bench.py --self-test               the analysis on synthetic triangles with injected faults

--midi: none (no MIDI at all but the CC 116 polls: is it the stream alone?), monkey (tools/monkey.py's MIDI stream
as tools/hil_monkey.py sends it, seeds from 1: does MIDI-IN traffic trigger it?), notes (a chord every 2 s: some
traffic, the engine busy). Run each for the same minutes; the bench build plays the triangle whatever the engine
does, so the voices and effects only load the CPU.

Report: OUT/report-<rate>-<midi>.json + the recording (OUT/rec-<rate>-<midi>.npz, int16 L R).
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
    dev.cc(114, 127)
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
    xi = np.clip(np.round(np.asarray(x, float) * 32768), -32768, 32767).astype(np.int16)
    tag = f"{a.rate}-{a.midi}"
    np.savez_compressed(os.path.join(a.out, f"rec-{tag}.npz"), usb=xi, fs=a.rate, flags=rec.flags)
    r = analyse(xi.astype(np.int64), a.rate)
    rows = hm.gate_counters(acc, a.rate)
    js = dict(date=time.strftime("%Y-%m-%d %H:%M"), rate=a.rate, midi=a.midi, minutes=a.minutes,
              midi_messages=len(ev), recorder_flags=rec.flags, bench=r, counters=acc.summary(),
              counter_rows=[list(map(str, x)) for x in rows])
    json.dump(js, open(os.path.join(a.out, f"report-{tag}.json"), "w"), indent=1, default=str)
    print_report(js)
    return 0 if r["ok"] and not [x for x in rows if x[3] == "FAIL"] else 1


def print_report(js):
    r = js["bench"]
    print(f"{js.get('rate')} Hz, MIDI {js.get('midi')} ({js.get('midi_messages', 0)} messages), "
          f"{js.get('minutes')} min: {r['frames']} frames judged from {r['start']} s; repeats {len(r['repeat'])}, "
          f"jumps {len(r['jump'])}, "
          f"L / R mismatches {r['lr']}; recorder flags {js.get('recorder_flags')}")
    for t, n in r["repeat"][:20]:
        print(f"  repeat at {t:.4f} s: {n} equal frames")
    for t, s in r["jump"][:20]:
        print(f"  jump at {t:.4f} s: step {s}")
    for row in js.get("counter_rows", []):
        print("  counter: " + " | ".join(row))
    print("BENCH " + ("PASS" if r["ok"] else "FAIL"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
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
        js = dict(rate=int(z["fs"]), bench=analyse(z["usb"].astype(np.int64), int(z["fs"])))
        print_report(js)
        return 0 if js["bench"]["ok"] else 1
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
