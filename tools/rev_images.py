#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The reverb's images above 11 kHz (the half-rate tank's 2x upsample) and its input's aliasing (the 2x down), on the
firmware's own code (tools/rev_scan.py), for every TYPE.

  python3 tools/rev_images.py [TREE]     the table: image level per TYPE (sweep, chord), alias per TYPE

The tanks run at 22.05 kHz, so an ideal upsample leaves nothing above 11.025 kHz: every bit of the wet there is an
image. image_db = the wet's energy 11.6 .. 22.05 kHz over its energy 0 .. 10.5 kHz (dB; the ideal: -inf). A sine
at f in the wet has its image at 22.05 kHz - f: sweep_images gives the worst image re its tone, f = 1 .. 8 kHz.
alias_db: a 15 kHz sine sent in (above the half rate's band) against a 7.05 kHz one (where it folds): the wet at
7.05 kHz, dB.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rev_scan as rs  # noqa: E402

FS = rs.FS
TYPES = ("HALL", "PLATE", "INFINITE")
PARAMS = dict(RV_DEPTH=90)


def _spec(x):
    x = np.asarray(x, float)
    if x.ndim == 2:
        x = x.sum(axis=1)
    w = np.hanning(len(x))
    return np.abs(np.fft.rfft(x * w)) ** 2, np.fft.rfftfreq(len(x), 1 / FS)


def image_db(wet, lo=10500.0, hi=11600.0):
    p, f = _spec(wet)
    return 10 * np.log10(p[f >= hi].sum() / max(p[f <= lo].sum(), 1e-30) + 1e-30)


def chord_wav(name):
    """a dry chord rendered by the host build (build/ab-upsample/<name>.wav: Rhodes rh_dry, VIBES vb_dry)"""
    import wave
    with wave.open(os.path.join(rs.ROOT, "build", "ab-upsample", name + ".wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())
    return x.astype(np.int64)


def tone_ratio(exe, typ, f_in, f_out, secs=1.5):
    """the wet at f_out (dB re the wet of a sine at f_out itself) for a sine at f_in"""
    n = int(secs * FS)
    a = n // 3                                   # (the tail settled: the last two thirds)

    def lvl(fi):
        wet = rs.render(exe, rs.sine(n, fi), dict(PARAMS, RV_TYPE=rs.TYPES[typ]))["wet"][a:n]
        p, f = _spec(wet)
        k = np.argmin(np.abs(f - f_out))
        return p[k - 3:k + 4].sum()
    return 10 * np.log10(lvl(f_in) / lvl(f_out) + 1e-30)


def sweep_images(exe, typ, freqs=(1000, 2000, 4000, 6000, 8000), secs=1.0):
    """per tone f: its image (22.05 kHz - f) in the wet, dB re the tone in the wet"""
    n = int(secs * FS)
    a = n // 3
    out = {}
    for fi in freqs:
        wet = rs.render(exe, rs.sine(n, fi), dict(PARAMS, RV_TYPE=rs.TYPES[typ]))["wet"][a:n]
        p, f = _spec(wet)
        k0, k1 = np.argmin(np.abs(f - fi)), np.argmin(np.abs(f - (FS / 2 - fi)))
        out[fi] = 10 * np.log10(p[k1 - 3:k1 + 4].sum() / p[k0 - 3:k0 + 4].sum() + 1e-30)
    return out


def logsweep(secs=3.0, f0=50.0, f1=20000.0, amp=6000.0):
    t = np.arange(int(secs * FS)) / FS
    k = np.log(f1 / f0)
    return np.round(amp * np.sin(2 * np.pi * f0 * secs / k * (np.exp(t / secs * k) - 1))).astype(np.int64)


def table(exe, chords=("rh_dry", "vb_dry")):
    rows = []
    sw = logsweep()
    for t in TYPES:
        r = dict(type=t, sweep=image_db(rs.render(exe, sw, dict(PARAMS, RV_TYPE=rs.TYPES[t]),
                                                  blocks=len(sw) // 256 + 300)["wet"]))
        for c in chords:
            try:
                x = chord_wav(c)
            except FileNotFoundError:
                continue
            r[c] = image_db(rs.render(exe, x, dict(PARAMS, RV_TYPE=rs.TYPES[t]))["wet"])
        r["tones"] = sweep_images(exe, t)
        r["alias15k"] = tone_ratio(exe, t, 15000, FS / 2 - 15000)
        rows.append(r)
    return rows


def main():
    tree = sys.argv[1] if len(sys.argv) > 1 else None
    exe = rs.build(tag="img", tree=tree)
    for r in table(exe):
        tones = " ".join(f"{k // 1000}k:{v:6.1f}" for k, v in r["tones"].items())
        ch = " ".join(f"{c} {r[c]:6.1f}" for c in ("rh_dry", "vb_dry") if c in r)
        print(f"{r['type']:9s} sweep {r['sweep']:6.1f}  {ch}  | tone images {tones} | alias 15k {r['alias15k']:6.1f}")


if __name__ == "__main__":
    main()
