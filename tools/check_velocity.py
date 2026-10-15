#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Velocity response of the golden model per key: sustain RMS (0.1-0.6 s) at v = 20..127 must rise monotonically
(check C6). Prints the non-monotonic keys; exit code 1 if any."""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rhodes_model as gm
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = json.load(open(os.path.join(ROOT, "docs/model/data/rhodes_spec.json")))
VS = [20, 40, 64, 80, 100, 115, 127]
keys = S["keys"][::int(sys.argv[1]) if len(sys.argv) > 1 else 1]
bad = 0
for k in keys:
    ys = gm.render_batch([k] * len(VS), S["pickup"], VS, 0.6, gains=[k["gain"]] * len(VS))
    r = [20 * np.log10(np.sqrt(np.mean(y[4410:] ** 2)) / 32767 + 1e-12) for y in ys]
    mono = all(b > a - 0.5 for a, b in zip(r, r[1:]))
    bad += not mono
    if not mono or k["midi"] % 12 == 9:
        print(k["midi"], "mono" if mono else "NOT ", "Tc %.1f ms" % k.get("tc_ms", 0), " ".join(f"{x:6.1f}" for x in r))
print("non-monotonic keys:", bad, "of", len(keys))
sys.exit(1 if bad else 0)
