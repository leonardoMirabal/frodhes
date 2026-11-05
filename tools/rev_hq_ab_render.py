#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The listening A/B of the full-rate REVERB TYPEs (tools/rev_hq_ab.py ab): Rhodes and VIBES phrases rendered dry on
the host build (CC 119 RAW: the engine's output, what fx_reverb gets with every other effect off), then fx_reverb of
the half-rate build (old) and of FR_REV_HQ (new) on it, per TYPE at DECAY 64 (default) and a long DECAY.

Each WAV: the three phrases one after the other (a chord held on the pedal, staccato notes, one long decay), dry +
wet as fx_reverb adds them, 16-bit 44.1 kHz. Within a pair the new's wet is scaled to the old's critical-band (Bark)
loudness (the gain in INDEX.md); every file of an engine shares one level scale (no clipping, the pairs untouched)."""
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
import hostrun as hr  # noqa: E402
import rev_scan as rs  # noqa: E402
import rev_hq_ab as ab  # noqa: E402

MIX = 64
LONG = dict(HALL=110, PLATE=110, INFINITE=110)
ENGINES = dict(rhodes=None, vibes=13)                 # program change: F14 VIBES DRY (TYPE 4)


def phrases(pc):
    """the script: (pc), RAW on, the three phrases; returns (lines, end_ms, the phrases' start times in s)"""
    L = []
    if pc is not None:
        L.append(f"100 midi 0xC0 {pc} 0")
    L.append(hr.cc(300, 119, 127))
    t0 = 1000                                          # chord + pedal: Cmaj9 voicing, pedal held 3 s
    L.append(hr.cc(t0 - 100, 64, 127))
    for n in (48, 55, 59, 62, 64, 67):
        L += hr.note(t0, n, 92, 1200)
    L.append(hr.cc(t0 + 3500, 64, 0))
    t1 = t0 + 8000                                     # staccato
    for i, n in enumerate((60, 64, 67, 72, 76, 72, 67, 64, 60)):
        L += hr.note(t1 + i * 260, n, 100, 90)
    t2 = t1 + 7000                                     # one long decay
    L += hr.note(t2, 45, 112, 6000)
    end = t2 + 11000
    return L, end, (t0 / 1000, t1 / 1000, t2 / 1000)


def dry(engine, pc):
    lines, end, ts = phrases(pc)
    r = hr.run(f"revab_{engine}", lines, end_ms=end)
    x = np.stack([r.L, r.R], axis=1)
    return np.round(x).astype(np.int64), ts


def write(path, y, scale):
    v = np.clip(np.round(y * scale), -32767, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(rs.FS)
        w.writeframes(v.tobytes())


def main(old, hq, out):
    rows = []
    for engine, pc in ENGINES.items():
        x, ts = dry(engine, pc)
        print(f"{engine}: dry peak {np.abs(x).max()}", flush=True)
        renders = {}
        for typ in ab.TYPES:
            for tag, dec in (("default", 64), ("long", LONG[typ])):
                p = dict(RV_TYPE=rs.TYPES[typ], RV_DEPTH=MIX, RV_DECAY=dec)
                wo = rs.render(old, x, p)["wet"].astype(float)
                wn = rs.render(hq, x, p)["wet"].astype(float)
                lo, ln = ab.bark_loud(wo)[0], ab.bark_loud(wn)[0]
                g = ab.match_gain(lo, ln)
                renders[(typ, tag)] = (wo, wn * g, g, dec)
        peak = max(np.abs(x.astype(float) + w).max() for v in renders.values() for w in v[:2])
        scale = min(1.0, 29000.0 / peak)
        write(os.path.join(out, f"{engine}_dry.wav"), x.astype(float), scale)
        for (typ, tag), (wo, wn, g, dec) in renders.items():
            base = f"{engine}_{typ.lower()}_{tag}"
            write(os.path.join(out, base + "_A_old.wav"), x + wo, scale)
            write(os.path.join(out, base + "_B_new.wav"), x + wn, scale)
            rows.append((engine, typ, tag, dec, 20 * np.log10(g), 20 * np.log10(scale)))
            print(f"{base}: new's wet x{g:.3f} ({20 * np.log10(g):+.2f} dB)", flush=True)
    with open(os.path.join(out, "levels.txt"), "w") as f:
        f.write(f"MIX {MIX}; phrases at 1 s (chord + pedal), 9 s (staccato), 16 s (long decay)\n")
        for r in rows:
            f.write("%-6s %-8s %-7s DECAY %3d  new's wet %+.2f dB (Bark match), file scale %+.2f dB\n" % r)
    return 0
