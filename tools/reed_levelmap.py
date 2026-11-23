#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""WHURLI's soft-end level evenness: docs/model/data/reed_levelmap.json from the host engine's own levels.

  tools/reed_levelmap.py [--rounds 3]   measure, fit, regenerate the tables (tools/gen_reed.py), measure again, repeat

The level of the keys at v40 (BS.1770 K-weighted, 0..1 s, the scan's LEV measure) is made smooth: the target keeps the measured
curve at the middle (key 60) and limits its slope to SLOPE dB a key both ways (a boundary between two pulse groups no longer steps, the
top octave rolls off without a cliff). The correction is an output gain at the soft anchors (RD_PGAIN v0 / v32; the key's own gain takes any
boost, the louder anchors the matching cut, so v64 and above are untouched: gen_reed.py). The v40 gain is 0.75 g32 + 0.25 (the
anchors' interpolation), so g32 = (g40 - 0.25) / 0.75; each round re-measures and refines g32 on the residual."""
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAP = os.path.join(ROOT, "docs", "model", "data", "reed_levelmap.json")
KEYS = list(range(33, 97))
SLOPE = 0.75          # dB a key at v40 (the scan's limit is a 1.0 dB neighbour step)
PIVOT = 60


def measure(vel):
    out = os.path.join(ROOT, "build", f"levels-{vel}.json")
    subprocess.run([sys.executable, os.path.join(HERE, "reed_measure_levels.py"), str(vel), out], check=True,
                   stdout=subprocess.DEVNULL)
    return {int(k): v for k, v in json.load(open(out))[str(vel)].items()}


def target(L):
    d = {k: float(np.clip(L[k + 1] - L[k], -SLOPE, SLOPE)) for k in KEYS[:-1]}
    T = {PIVOT: L[PIVOT]}
    for k in range(PIVOT, KEYS[-1]):
        T[k + 1] = T[k] + d[k]
    for k in range(PIVOT - 1, KEYS[0] - 1, -1):
        T[k] = T[k + 1] - d[k]
    return T


def main():
    rounds = int(sys.argv[sys.argv.index("--rounds") + 1]) if "--rounds" in sys.argv else 3
    g32 = {k: 1.0 for k in KEYS}
    if os.path.exists(MAP):
        g32.update({int(k): v for k, v in json.load(open(MAP))["g32"].items()})
    T = None
    for r in range(rounds):
        L = measure(40)
        if T is None:
            T = target(L)                       # the target comes from the table as generated WITHOUT the map (round 0)
            assert not os.path.exists(MAP) or r == 0
        steps = max(abs(L[k + 1] - L[k]) for k in KEYS[:-1])
        print(f"round {r}: v40 max neighbour step {steps:.2f} dB, worst |T - L| {max(abs(T[k] - L[k]) for k in KEYS):.2f} dB", flush=True)
        for k in KEYS:
            g40 = (0.75 * g32[k] + 0.25) * 10 ** ((T[k] - L[k]) / 20)       # the gain v40 needs now
            g32[k] = min(max((g40 - 0.25) / 0.75, 0.05), 12.0)
        json.dump(dict(slope_db=SLOPE, pivot=PIVOT, g32={str(k): round(g32[k], 4) for k in KEYS}), open(MAP, "w"), indent=0)
        subprocess.run([sys.executable, os.path.join(HERE, "gen_reed.py")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    L = measure(40)
    print(f"final: v40 max neighbour step {max(abs(L[k + 1] - L[k]) for k in KEYS[:-1]):.2f} dB, worst |T - L| "
          f"{max(abs(T[k] - L[k]) for k in KEYS):.2f} dB")


if __name__ == "__main__":
    main()
