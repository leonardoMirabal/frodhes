#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The whirl (S1, the beat partner in V2) measured on the C engine: the level's slow wobble and the pitch.

  tools/rh_whirl.py [--notes 57,60,69] [--vel 100] [--dur 4] [--bin build/host/frodhes]

Each note is struck alone and held, the effects bypassed (CC 119 RAW, NOISE 0). Over 0.3 s .. dur:
- ripple_db: the level (RMS in 20 ms frames, dB) minus its fitted decay (a quadratic in dB), peak to peak (the
  5th to 95th percentile, so one frame's noise does not count); rate_hz: the strongest rate of that residual in
  0.2-3 Hz;
- cents_max / cents_mean: the fundamental's frequency (the signal moved down by f0 and low-passed by a 100 ms Hann
  window, its phase's slope over 50 ms steps) re the equal-tempered f0.
"""
import argparse
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FS = 44100


def render(binary, notes, vel=100, dur=4.0, gap=2.0, out=None):
    """[(note, mono float signal of dur s from the strike)]"""
    out = out or os.path.join(os.path.dirname(binary), "whirl")
    lines = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0"]
    t, starts = 300, []
    for n in notes:
        starts.append((n, t))
        lines += [f"{t} midi 0x90 {n} {vel}", f"{t + int(dur * 1000)} midi 0x80 {n} 0"]
        t += int((dur + gap) * 1000)
    lines.append(f"{t} end")
    open(out + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([binary, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
    res = []
    for n, t in starts:
        seg = x[int(t * FS / 1000):int((t + dur * 1000) * FS / 1000)]
        on = np.flatnonzero(np.abs(seg) > 0)
        seg = seg[on[0]:] if len(on) else seg
        res.append((n, seg))
    return res


def baseband(x, f0, win=0.1):
    """x moved down by f0 (complex) and low-passed by a Hann window of win s: the fundamental's complex amplitude"""
    t = np.arange(len(x)) / FS
    h = np.hanning(int(win * FS))
    return np.convolve(x * np.exp(-2j * np.pi * f0 * t), h / h.sum(), "valid")


def measure(x, f0, t0=0.3):
    x = x[int(t0 * FS):len(x) - int(0.2 * FS)]          # (the key-off lands a block after the segment's end)
    z = baseband(x, f0)
    fr = int(0.02 * FS)
    nf = len(x) // fr
    lv = 10 * np.log10(np.mean(x[:nf * fr].reshape(nf, fr) ** 2, axis=1) + 1e-9)
    t = (np.arange(nf) + 0.5) * fr / FS
    res = lv - np.polyval(np.polyfit(t, lv, 2), t)
    ripple = float(np.percentile(res, 95) - np.percentile(res, 5))
    S = np.abs(np.fft.rfft(res * np.hanning(nf), 1 << 14))
    fq = np.fft.rfftfreq(1 << 14, fr / FS)
    k = (fq >= 0.2) & (fq <= 3.0)
    rate = float(fq[k][np.argmax(S[k])])
    ph = np.unwrap(np.angle(z[::int(0.05 * FS)]))
    c = 1200 * np.log2(1 + np.diff(ph) / (2 * np.pi * 0.05) / f0)
    return dict(ripple_db=round(ripple, 2), rate_hz=round(rate, 2), cents_max=round(float(np.max(np.abs(c))), 2),
                cents_mean=round(float(np.mean(c)), 3))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", default="57,60,69")
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--dur", type=float, default=4.0)
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "host", "frodhes"))
    a = ap.parse_args()
    for n, x in render(a.bin, [int(q) for q in a.notes.split(",")], a.vel, a.dur):
        r = measure(x, 440.0 * 2 ** ((n - 69) / 12))
        print("key %3d v%d  ripple %5.2f dB at %4.2f Hz  pitch max %5.2f c, mean %+6.3f c" % (
            n, a.vel, r["ripple_db"], r["rate_hz"], r["cents_max"], r["cents_mean"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
