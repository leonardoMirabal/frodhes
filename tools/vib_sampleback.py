#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""VIBES sample-back (PLAN P7.5): play the FM-1's own VIBES over MIDI, record its USB audio, measure every note with
the SAME procedure as the reference recordings (tools/vib_ref_measure.py), and compare the device with the model's
targets (docs/model/data/bars_spec.json) and with the reference set (measured in place, numbers only). The same
loop the Rhodes was tuned with: a difference is fixed in the data / generator, never by hand in the tables.

    python3 tools/vib_sampleback.py [--round N] [--keys 53-89] [--step 1] [--vel 100] [--ref DIR]

Writes build/vib_sampleback/rN/*.wav (the FM-1's own output) and appends a table to
private/vibraphone/research/P7-sampleback.md. Gate VD (PLAN §10): pitch +-1 c, V1 T60 +-10 % (pedal down), level
per key +-1 dB re its neighbours' trend. Needs the FM-1 on USB (sandbox off for CoreAudio).
"""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_scan as hs           # noqa: E402  (the HIL rig: MIDI out, USB audio in, a known panel state)
import vib_ref_measure as vm    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.expanduser("~/Desktop/grbaciones de campo/SAMPLEST37/Vibraphone Samples 4")
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FS = hs.FS


def name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def spec_targets():
    d = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
    out = {}
    for k in d["keys"]:
        modes = {m["name"]: m for m in k["modes"]}
        out[k["midi"]] = dict(f0=modes["V1"]["f"], t60=modes["V1"]["t60"],
                              v2=modes.get("V2", {}).get("f"), v2_t60=modes.get("V2", {}).get("t60"))
    return out


def reference(ref_dir):
    """the reference set, measured in place (numbers only), by MIDI note"""
    got = {}
    if not os.path.isdir(ref_dir):
        return got
    for f in sorted(os.listdir(ref_dir)):
        if f.lower().endswith(".wav"):
            r = vm.measure(os.path.join(ref_dir, f))
            if r:
                got[r["midi"]] = r
    return got


def fine_f0(x, f_guess):
    """the fundamental to ~0.1 c: 1.5 s from 0.3 s after the onset, Hann, 2^19-point FFT, parabolic interpolation on
    the log magnitude (the reference procedure's FFT bin is ~5 c at 220 Hz: too coarse for Gate VD's +-1 c)"""
    m = x.mean(axis=1) if x.ndim > 1 else x
    i0 = int(np.argmax(np.abs(m) > 0.1 * np.abs(m).max())) + int(0.3 * FS)
    seg = m[i0:i0 + int(1.5 * FS)]
    N = 1 << 19
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    k0 = int(round(f_guess * N / FS))
    w = max(3, int(0.03 * k0))
    k = k0 - w + int(np.argmax(sp[k0 - w:k0 + w + 1]))
    a, b, c = np.log(sp[k - 1:k + 2] + 1e-12)
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * FS / N


def v1_t60(r):
    rows = [p for p in r["rows"] if abs(p["ratio"] - 1) < 0.03 and p["t60"]]
    return rows[0]["t60"] if rows else None


def write_wav(path, x):
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(FS)
    w.writeframes(np.clip(x, -32768, 32767).astype("<i2").tobytes())
    w.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--keys", default="53-89")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--hold", type=float, default=8.0)
    ap.add_argument("--ref", default=REF)
    a = ap.parse_args()
    lo, hi = (int(v) for v in a.keys.split("-"))
    out = os.path.join(ROOT, "build", "vib_sampleback", f"r{a.round}")
    os.makedirs(out, exist_ok=True)
    rig = hs.Rig()
    spec, ref = spec_targets(), reference(a.ref)
    rows = []
    for m in range(lo, hi + 1, a.step):
        rig.dry()
        rig.out.send(rig.mido.Message("control_change", control=79, value=64))      # VIBES
        rig.out.send(rig.mido.Message("control_change", control=64, value=127))     # pedal down: the free ring
        time.sleep(0.3)
        x = rig.take(a.hold, [(0.1, "on", m, a.vel), (a.hold - 0.6, "off", m, 0)])
        rig.out.send(rig.mido.Message("control_change", control=64, value=0))
        p = os.path.join(out, f"VIBES {name(m)} v{a.vel}.wav")
        write_wav(p, x)
        r = vm.measure(p)
        if not r:
            continue
        s, q = spec.get(m), ref.get(m)
        rows.append(dict(midi=m, cents=1200 * np.log2(fine_f0(x, s["f0"]) / s["f0"]) if s else None, t60=v1_t60(r),
                         t60_spec=s["t60"] if s else None, t60_ref=v1_t60(q) if q else None,
                         pk=r["f0pk_ms"], pk_ref=q["f0pk_ms"] if q else None,
                         level=float(20 * np.log10(np.sqrt(np.mean((x[int(0.1 * FS):int(1.1 * FS)] / 32768) ** 2)) + 1e-12))))
        print(f"{name(m):4s} {rows[-1]}", flush=True)
    rig.dry()
    lv = np.array([q["level"] for q in rows])
    trend = np.convolve(np.pad(lv, 2, mode="edge"), np.ones(5) / 5, "valid") if len(lv) >= 3 else lv
    lines = [f"\n## Round {a.round} ({time.strftime('%Y-%m-%d %H:%M')}): v{a.vel}, pedal down, keys {a.keys} step {a.step}\n",
             "| key | pitch vs spec (c) | V1 T60 device / spec / reference (s) | f0 peak device / ref (ms) | level dB (vs trend) | VD |",
             "| --- | --- | --- | --- | --- | --- |"]
    bad = 0
    for q, tr in zip(rows, trend):
        ok = (q["cents"] is None or abs(q["cents"]) <= 1) and \
             (q["t60"] is None or q["t60_spec"] is None or abs(q["t60"] / q["t60_spec"] - 1) <= 0.10) and \
             abs(q["level"] - tr) <= 1.0
        bad += not ok
        f = lambda v, fmt: "-" if v is None else format(v, fmt)
        lines.append(f"| {name(q['midi'])} | {f(q['cents'], '+.1f')} | {f(q['t60'], '.2f')} / {f(q['t60_spec'], '.2f')} / "
                     f"{f(q['t60_ref'], '.2f')} | {q['pk']:.0f} / {f(q['pk_ref'], '.0f')} | {q['level']:.1f} ({q['level'] - tr:+.1f}) | "
                     f"{'ok' if ok else 'FAIL'} |")
    lines.append(f"\n{len(rows)} notes, {bad} outside Gate VD. Recordings: build/vib_sampleback/r{a.round}/ (the FM-1's own output).")
    md = os.path.join(ROOT, "private", "vibraphone", "research", "P7-sampleback.md")
    if not os.path.exists(md):
        open(md, "w").write("# P7 sample-back rounds (the FM-1's VIBES measured like the reference recordings)\n")
    open(md, "a").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
