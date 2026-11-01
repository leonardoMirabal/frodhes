#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""M5-02 (private plan 05): the Move's output levels, per factory preset, on the host builds (no device).

Three builds: the FM-1's (10 voices), the Move's Classic (tools/gate_h.py DEFS: 16 voices, FR_HQ, HQ off) and the
Move's HQ (HQ on for every note). Per factory preset (program change: F01..F16 = 0..15, F17..F19 = 32..34), as the
preset sets it (its effects on):

  level   C2 .. C6 one note at a time (v100, 0.5 s each), then the chord C3 E3 G3 C4 E4 held by the pedal (v100,
          2 s): integrated LUFS (BS.1770 K-weighting, tools/bar_measure.lufs), the peak (dBFS), and the share of
          the energy in three FFT bands (< 250 Hz, 250 Hz .. 2.5 kHz, > 2.5 kHz; dB re the total)
  worst   the pedal down, two 16-note chords at v127, then a 40-note glissando at v127 (24 steals at 16 voices): the
          peak (dBFS) and the samples at full scale (|x| >= 32767)

Printed: one row per preset and build, then the FM-1's preset spread (LUFS min .. max) and every Move preset's place
in it. The bridge (move/move_bridge.h) hands these samples to the Move unchanged (the FM-1's clip, MASTER at Q12
4096): its output gain is the one thing M5-02 may set, and only against the Move's own instruments' level (a device
measurement).

    python3 tools/move_levels.py [--jobs 2] [--json out.json]
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import bar_measure as bm  # noqa: E402
import gate_h  # noqa: E402

OUT = ROOT / "build" / "move_levels"
FS = 44100
PCS = list(range(16)) + [32, 33, 34]
CHORD16 = (36, 40, 43, 47, 50, 53, 57, 60, 64, 67, 71, 74, 77, 81, 84, 88)


def builds():
    OUT.mkdir(parents=True, exist_ok=True)
    fm = OUT / "fm1"
    # (the FM-1's reverb as the Move's: FR_REV_HQ, on for the FM-1 since c1-rev, until the Move's MOVE_DEFS flip it)
    mk = (ROOT / "Makefile").read_text()
    rq = "-DFR_REV_HQ=1" if "-DFR_REV_HQ=1" in next(l for l in mk.splitlines() if l.startswith("MOVE_DEFS")) else \
        "-DFR_REV_HQ=0"
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', rq, "-Ifirmware/src",
                    "-Ifirmware/gen", "-o", str(fm), "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)
    return {"FM-1": fm, "Move": gate_h.build(False, OUT / "move"), "Move HQ": gate_h.build(True, OUT / "move-hq")}


def script(pc, what):
    ev = ["40 midi 0xB0 10 127", f"100 midi 0xC0 {pc}"]        # (the developer reset, then the preset)
    if what == "level":
        t = 400
        for k in range(36, 85, 4):
            ev += [f"{t} midi 0x90 {k} 100", f"{t + 450} midi 0x80 {k} 0"]
            t += 500
        ev.append(f"{t} midi 0xB0 64 127")
        ev += [f"{t + 10} midi 0x90 {k} 100" for k in (48, 52, 55, 60, 64)]
        ev += [f"{t + 20} midi 0x80 {k} 0" for k in (48, 52, 55, 60, 64)]
        ev += [f"{t + 2000} midi 0xB0 64 0", f"{t + 3500} end"]
    else:
        ev.append("300 midi 0xB0 64 127")
        for t in (400, 1400):
            ev += [f"{t} midi 0x90 {k} 127" for k in CHORD16] + [f"{t + 200} midi 0x80 {k} 0" for k in CHORD16]
        for i, k in enumerate(range(36, 76)):
            ev += [f"{2400 + 30 * i} midi 0x90 {k} 127", f"{2440 + 30 * i} midi 0x80 {k} 0"]
        ev += ["4500 midi 0xB0 64 0", "6000 end"]
    return ev


def render(b, tag, ev):
    s = OUT / (tag + ".txt")
    s.write_text("\n".join(ev) + "\n")
    p = subprocess.run([str(b), str(OUT / tag), str(s)], cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"{tag}: exit {p.returncode}: {p.stderr[-500:]}")
    w = wave.open(str(OUT / tag) + ".wav")
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)


def bands(x):
    m = x.mean(axis=1)
    X = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1.0 / FS)
    tot = X.sum() + 1e-30
    return [10 * np.log10(X[(f >= a) & (f < b)].sum() / tot + 1e-30) for a, b in ((0, 250), (250, 2500), (2500, 1e9))]


def one(args):
    name, b, pc = args
    x = render(b, f"{name.replace(' ', '_')}-{pc}-level", script(pc, "level"))
    y = render(b, f"{name.replace(' ', '_')}-{pc}-worst", script(pc, "worst"))
    pk = lambda z: 20 * np.log10(np.abs(z).max() / 32768.0 + 1e-12)
    return dict(build=name, pc=pc, lufs=float(bm.lufs(x)), peak=float(pk(x)), bands=bands(x),
                worst_peak=float(pk(y)), full_scale=int(np.sum(np.abs(y) >= 32767)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    bs = builds()
    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, [(n, b, pc) for n, b in bs.items() for pc in PCS]))
    name = lambda pc: f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}"
    print("preset build      LUFS   peak  bands <250/mid/>2.5k dB   worst peak  full-scale samples")
    for r in rows:
        print(f"{name(r['pc']):6} {r['build']:8} {r['lufs']:6.1f} {r['peak']:6.1f}   "
              + " ".join(f"{v:5.1f}" for v in r["bands"]) + f"        {r['worst_peak']:6.1f}   {r['full_scale']}")
    fm = [r["lufs"] for r in rows if r["build"] == "FM-1"]
    lo, hi = min(fm), max(fm)
    print(f"FM-1 preset spread: {lo:.1f} .. {hi:.1f} LUFS")
    for n in ("Move", "Move HQ"):
        mv = [r for r in rows if r["build"] == n]
        out = [name(r["pc"]) for r in mv if not lo - 0.05 <= r["lufs"] <= hi + 0.05]
        print(f"{n}: LUFS {min(r['lufs'] for r in mv):.1f} .. {max(r['lufs'] for r in mv):.1f}; outside the FM-1's "
              f"spread: {', '.join(out) or 'none'}; worst peak {max(r['worst_peak'] for r in mv):.2f} dBFS; "
              f"full-scale samples {sum(r['full_scale'] for r in mv)}")
    if a.json:
        json.dump(rows, open(a.json, "w"), indent=1)
    return 1 if any(r["full_scale"] for r in rows if r["build"] != "FM-1") else 0


if __name__ == "__main__":
    sys.exit(main())
