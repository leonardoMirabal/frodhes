#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The full-rate REVERB TYPEs (FR_REV_HQ, firmware/src/rev_hq*.c) against the half-rate ones, on the firmware's own
fx_reverb (tests/c/rev_scan.c through tools/rev_scan.py): what limits each TYPE and what the full rate changes.

  python3 tools/rev_hq_ab.py measure     the table: per TYPE x build, the wet's level (broadband and per critical
                                         band group), T60 per octave, the tail's top band, the echo density over
                                         time, the late tail's spectral roughness (ringing modes) -> build/rev-ab/measure.txt
  python3 tools/rev_hq_ab.py ab          the listening A/B (build/rev-ab/*.wav + INDEX.md): Rhodes and VIBES phrases
                                         rendered dry on the host build (CC 119 RAW: the engine's own output, the
                                         reverb's input), then each TYPE old vs new, default and long DECAY

Both builds are integer-exact; the WAVs are 16-bit at 44.1 kHz (dry + wet, as fx_reverb adds them), the old and the
new of a pair loudness-matched by the critical-band (Bark) loudness of their wet (the gain applied is listed)."""
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
import rev_scan as rs  # noqa: E402

FS = rs.FS
OUT = os.path.join(ROOT, "build", "rev-ab")
TYPES = ("HALL", "PLATE", "INFINITE")
OCT = (250, 500, 1000, 2000, 4000, 8000, 16000)


def exes():
    return rs.build(tag="old"), rs.build(cflags=["-DFR_REV_HQ=1"], tag="hq")


# ---- analysis

def bark_edges():
    """the critical bands' edges (Zwicker), Hz"""
    return np.array([20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150,
                     3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500, 20500])


def bark_loud(x):
    """Zwicker-style loudness proxy: per critical band the power (FFT), compressed (power^0.23), summed; and the
    per-band levels (dB)"""
    x = np.asarray(x, float)
    m = x.mean(axis=1) if x.ndim == 2 else x
    X = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / FS)
    e = bark_edges()
    p = np.array([X[(f >= e[i]) & (f < e[i + 1])].sum() + 1e-9 for i in range(len(e) - 1)])
    return float((p ** 0.23).sum()), 10 * np.log10(p)


def match_gain(l_ref, l_x):
    """the amplitude gain that brings loudness l_x to l_ref (bark_loud: L ~ amplitude^0.46)"""
    return (l_ref / l_x) ** (1 / 0.46)


def oct_t60(wet, f0):
    """T60 (s) of the octave around f0 (EDC, -5 .. -25 dB)"""
    return rs.t60_from_edc(rs.edc_db(rs.band(wet, f0 / np.sqrt(2), min(f0 * np.sqrt(2), FS / 2 - 1))))


def echo_density(wet, win_ms=20.0):
    """the normalised echo density profile (Abel and Huang, 2006): per window the fraction of samples beyond one
    standard deviation over erfc(1 / sqrt 2); 1 = Gaussian (fully diffuse). Returns (t, ned)"""
    m = np.asarray(wet, float)
    m = m[:, 0] + m[:, 1] if m.ndim == 2 else m
    n = int(win_ms * FS / 1000)
    t, d = [], []
    for a in range(0, len(m) - n, n // 2):
        s = m[a:a + n]
        sd = s.std()
        if sd <= 0:
            continue
        t.append((a + n / 2) / FS)
        d.append(np.mean(np.abs(s) > sd) / 0.3173)
    return np.array(t), np.array(d)


def roughness(wet, t0=0.5, t1=1.5, f0=500, f1=4000):
    """the late tail's spectral roughness: the std (dB) of its fine magnitude spectrum about a 1/3-octave smoothed
    one, 500 Hz .. 4 kHz (a ringing, metallic tail has sharp modes: a larger value; a dense one ~5.6 dB, the
    Rayleigh value)"""
    m = np.asarray(wet, float)
    m = m[:, 0] + m[:, 1] if m.ndim == 2 else m
    s = m[int(t0 * FS):int(t1 * FS)]
    if not np.any(s):
        return None
    X = 20 * np.log10(np.abs(np.fft.rfft(s * np.hanning(len(s)))) + 1e-9)
    f = np.fft.rfftfreq(len(s), 1 / FS)
    sel = (f >= f0) & (f < f1)
    sm = np.convolve(X, np.ones(61) / 61, mode="same")
    return float(np.std((X - sm)[sel]))


def top_band(wet, t0=0.3, t1=1.0):
    """the tail's level above 11 kHz re its 1..4 kHz level, dB"""
    m = np.asarray(wet, float)
    m = m[:, 0] + m[:, 1] if m.ndim == 2 else m
    s = m[int(t0 * FS):int(t1 * FS)]
    X = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(len(s), 1 / FS)
    hi = X[(f >= 11000) & (f < 20000)].mean() + 1e-9
    mid = X[(f >= 1000) & (f < 4000)].mean() + 1e-9
    return 10 * np.log10(hi / mid)


def measure(old, hq, out_lines):
    x = rs.burst(FS)
    for typ in TYPES:
        for dec in (64, 110):
            p = dict(RV_TYPE=rs.TYPES[typ], RV_DEPTH=100, RV_DECAY=dec)
            secs = 9 if typ != "INFINITE" else 25
            res = {}
            for name, e in (("old", old), ("hq", hq)):
                r = rs.render(e, x, p, blocks=int(secs * FS / 256))
                w = r["wet"].astype(float)
                ld, _ = bark_loud(w[:2 * FS])
                t60 = [oct_t60(w, f) for f in OCT]
                t, ned = echo_density(w)
                ned_at = [float(np.interp(tt, t, ned)) for tt in (0.05, 0.1, 0.2, 0.5)]
                res[name] = (ld, t60, top_band(w), ned_at, roughness(w), r["last"], r["cost"])
            lo, lh = res["old"][0], res["hq"][0]
            out_lines.append(f"{typ} DECAY {dec}: the wet's Bark loudness hq / old {lh / lo:.3f}: the hq's level "
                             f"{20 * np.log10(match_gain(lo, lh)):+.2f} dB to match")
            for name in ("old", "hq"):
                ld, t60, tb, ned_at, ro, last, cost = res[name]
                ts = " ".join("-" if v is None else f"{v:.2f}" for v in t60)
                out_lines.append(f"  {name:3s} T60 {OCT} = {ts}; top(>11k re 1-4k) {tb:+.1f} dB; "
                                 f"NED@50/100/200/500ms {' '.join(f'{v:.2f}' for v in ned_at)}; "
                                 f"rough {ro if ro is None else round(ro, 2)} dB; last block {last}; cost {cost}")
            print("\n".join(out_lines[-3:]), flush=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "measure"
    os.makedirs(OUT, exist_ok=True)
    old, hq = exes()
    try:
        if cmd == "measure":
            lines = []
            measure(old, hq, lines)
            open(os.path.join(OUT, "measure.txt"), "w").write("\n".join(lines) + "\n")
        elif cmd == "ab":
            import rev_hq_ab_render as ab   # noqa: E402  (the phrases: tools/rev_hq_ab_render.py)
            ab.main(old, hq, OUT)
    finally:
        os.remove(old), os.remove(hq)
    return 0


if __name__ == "__main__":
    sys.exit(main())
