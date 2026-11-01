#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The modulation / filter effects' artefacts, measured on the stages alone (tests/c/fxq_stage.c: fx.c's own integers
on a synthetic signal), before and after a change:

    tools/fxq_metrics.py [--base DIR] [--json FILE] [NAME ...]

--base DIR: also build the harness against DIR/firmware (e.g. a `git archive` of the commit before) and print both.
NAME selects metrics by substring. Each metric is a level in dB (lower is better) or, where marked, a count:

  spur      a sine through the stage: the energy outside the tone's own band (the band: the tone +- what the stage's
            modulation moves it by by design, Doppler / the LFO's sidebands), re the tone's. Interpolation error of a
            modulated delay read, a coefficient stepped at the control rate, quantisation noise: all land there
  ctl       the energy at the control rate's images (the tone +- k x 1378 Hz, k = 1..3, +- 60 Hz), re the tone's:
            what a coefficient held for 32 samples adds (ctl8: at +- k x 5512 Hz, k = 1, 2: held for 8)
  floor     a quiet sine (re its own level): the stage's noise and distortion, everything outside +- 30 Hz
  tail      after the input stops, the largest |sample| in the last 0.5 s (LSB): a limit cycle or a stuck offset
  dc        the output's mean over the sine (LSB)
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "fxq")
FS = 44100


def build(fw, exe):
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", f"-I{fw}/src", f"-I{fw}/gen",
                    f"-I{fw}/hal", os.path.join(ROOT, "tests", "c", "fxq_stage.c"), "-o", exe, "-lm"], check=True)
    return exe


def render(exe, stage, secs, sig, *params):
    out = os.path.join(OUT, f"r{os.getpid()}_{abs(hash((exe, stage, secs, sig, params)))}.raw")
    subprocess.run([exe, stage, str(secs), sig, out, *params], check=True)
    x = np.fromfile(out, dtype="<i4").astype(float).reshape(-1, 2)
    os.remove(out)
    return x[:, 0], x[:, 1]


def spec(x):
    w = np.blackman(len(x))   # (sidelobes under -110 dB: the floors measured here reach -100)
    X = np.abs(np.fft.rfft(x * w)) ** 2
    return np.fft.rfftfreq(len(x), 1 / FS), X


def db(a, b):
    return float(10 * np.log10((a + 1e-30) / (b + 1e-30)))


def spur(x, f0, band):
    """energy outside |f - f0| < band (and its DC / the last 20 Hz), re the energy inside"""
    f, X = spec(x)
    tone = np.abs(f - f0) < band
    rest = (~tone) & (f > 20)
    return db(X[rest].sum(), X[tone].sum())


def ctl(x, f0, rate=FS / 32, ks=(1, 2, 3), bw=60):
    f, X = spec(x)
    tone = np.abs(f - f0) < bw
    img = np.zeros_like(f, bool)
    for k in ks:
        for s in (-1, 1):
            img |= np.abs(f - (f0 + s * k * rate)) < bw
    return db(X[img].sum(), X[tone].sum())


def seg(x, t0, t1=None):
    return x[int(t0 * FS):(int(t1 * FS) if t1 else len(x))]


# metric name -> function(exe) -> value
def metrics():
    M = {}
    for f in (5000, 10000, 15000):
        # chorus at its deepest / fastest-but-one: Doppler +- ~3 % (SPEED 64) .. 12 % (127); the band +- 4 % / 13 %
        M[f"chorus spur {f // 1000}k d127 s64"] = lambda e, f=f: spur(seg(render(e, "chorus", 3, f"sine:{f}:8000", "CP_SW=1", "CP_DEPTH=127", "CP_SPEED=64")[0], 0.5), f, 0.04 * f + 40)
        M[f"chorus spur {f // 1000}k d64 s20"] = lambda e, f=f: spur(seg(render(e, "chorus", 3, f"sine:{f}:8000", "CP_SW=1", "CP_DEPTH=64", "CP_SPEED=20")[0], 0.5), f, 0.02 * f + 40)
    for t, sw in (("ANA", 2), ("TAPE", 3)):
        for f in (3000, 10000):
            # TIME 100 (~0.59 s): the wow's +-0.3 % sweeps the fraction ~330 times a second (Doppler +- 0.8 %)
            M[f"delay {t} spur {f // 1000}k"] = lambda e, f=f, sw=sw: spur(seg(render(e, "delay", 4, f"late:{f}:1000:500", f"DL_SW={sw}", "DL_DEPTH=127", "DL_TIME=100", "DL_FDBK=0", "DL_TONE=127")[0], 1.6), f, 0.035 * f + 60)
    for typ, name in ((1, "LP12"), (5, "BP")):
        M[f"filter ctl {name} lfo-sine fast"] = lambda e, typ=typ: ctl(seg(render(e, "filter", 3, "sine:2000:8000", f"FL_TYPE={typ}", "FL_FREQ=90", "FL_RESO=70", "FL_LFO=127", "LFO_SHAPE=0", "LFO_RATE=127")[0], 0.5), 2000)
        M[f"filter ctl8 {name} lfo-sine fast"] = lambda e, typ=typ: ctl(seg(render(e, "filter", 3, "sine:2000:8000", f"FL_TYPE={typ}", "FL_FREQ=90", "FL_RESO=70", "FL_LFO=127", "LFO_SHAPE=0", "LFO_RATE=127")[0], 0.5), 2000, rate=FS / 8, ks=(1, 2))
        M[f"filter ctl {name} lfo-tri mid"] = lambda e, typ=typ: ctl(seg(render(e, "filter", 3, "sine:2000:8000", f"FL_TYPE={typ}", "FL_FREQ=90", "FL_RESO=100", "FL_LFO=100", "LFO_SHAPE=1", "LFO_RATE=80")[0], 0.5), 2000)
    M["filter tail BP reso127"] = lambda e: float(np.abs(seg(render(e, "filter", 3, "burst:500:20000:1000", "FL_TYPE=5", "FL_FREQ=60", "FL_RESO=127")[0], 2.5)).max())
    M["phaser ctl 3k fast"] = lambda e: ctl(seg(render(e, "phaser", 3, "sine:3000:8000", "PH_SW=1", "PH_DEPTH=127", "PH_SPEED=127", "PH_FDBK=64")[0], 0.5), 3000)
    M["phaser tail"] = lambda e: float(np.abs(seg(render(e, "phaser", 3, "burst:300:20000:1000", "PH_SW=1", "PH_DEPTH=127", "PH_SPEED=60", "PH_FDBK=127")[0], 2.5)).max())
    M["trem AM ctl 1k fast"] = lambda e: ctl(seg(render(e, "trem", 3, "sine:1000:8000", "TW_SW=1", "TW_MODE=1", "TW_DEPTH=127", "TW_RATE=127")[0], 0.5), 1000)
    M["trem PAN ctl 1k fast"] = lambda e: ctl(seg(render(e, "trem", 3, "sine:1000:8000", "TW_SW=1", "TW_MODE=0", "TW_DEPTH=127", "TW_RATE=127")[0], 0.5), 1000)
    M["trem AM-amp ctl 1k fast"] = lambda e: ctl(seg(render(e, "trem", 3, "sine:1000:8000", "TW_SW=1", "TW_MODE=1", "TW_DEPTH=127", "TW_RATE=127", "TW_SHAPE=127")[0], 0.5), 1000)
    for f, a in ((300, 3000), (1000, 3000), (3000, 3000), (1000, 300), (1000, 20000)):
        M[f"wah floor {f}Hz A{a}"] = lambda e, f=f, a=a: spur(seg(render(e, "trem", 3, f"sine:{f}:{a}", "TW_SW=2", "TW_DEPTH=100", "TW_RATE=127")[0], 1.0), f, 30)
    M["wah tail"] = lambda e: float(np.abs(seg(render(e, "trem", 4, "burst:700:20000:1000", "TW_SW=2", "TW_DEPTH=100", "TW_RATE=127")[0], 3.5)).max())
    for name, st, p in (("chorus", "chorus", ("CP_SW=1", "CP_DEPTH=127")), ("phaser", "phaser", ("PH_SW=1", "PH_DEPTH=127")),
                        ("filter LP12", "filter", ("FL_TYPE=1", "FL_FREQ=80", "FL_RESO=100")),
                        ("wah", "trem", ("TW_SW=2", "TW_DEPTH=100", "TW_RATE=127"))):
        M[f"{name} dc"] = lambda e, st=st, p=p: float(abs(np.mean(seg(render(e, st, 2, "sine:441:8000", *p)[0], 0.5, 1.5))))
    return M


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--base")
    ap.add_argument("--json")
    a = ap.parse_args()
    exes = {"new": build(os.path.join(ROOT, "firmware"), os.path.join(OUT, "stage_new"))}
    if a.base:
        exes = {"base": build(os.path.join(os.path.abspath(a.base), "firmware"), os.path.join(OUT, "stage_base")), **exes}
    res = {}
    print(f"{'metric':34s}" + "".join(f"{k:>10s}" for k in exes))
    for name, fn in metrics().items():
        if a.names and not any(s in name for s in a.names):
            continue
        res[name] = {k: round(fn(e), 2) for k, e in exes.items()}
        print(f"{name:34s}" + "".join(f"{res[name][k]:10.2f}" for k in exes), flush=True)
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
