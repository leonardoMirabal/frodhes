# SPDX-License-Identifier: GPL-3.0-only
"""Two defects of the settings scan (tools/type_scan.py, 2026-11-21), fixed at their cause in rhodes.c (2026-11-22):
- EARLY FELT: a TYPE's tip scale (RH_TYPE ham) lowered the fundamental more and more up each hammer band and much
  less on the next band's first key: level steps at 50/51, 60/61, 70/71 and 84/85 (+1.3, +1.5, +1.9, +3.0 dB at v40;
  +1.5 at 84/85 at v100). rh_ty_ham's RH_HAM_M bridges each edge: every neighbour step of keys 46..88 under 1 dB at
  v20, v40 and v100;
- BELL DYNO: a dry v127 note reached the rail on the top key (35.7k unclipped) and at TONE 0 in the bass (36.2k). Its
  keys 100..108 follow STAGE 77's key tracking (rh_dyn RH_DYN_K3) and its level trim fades to unity below TONE 64:
  the peaks stay under 30000, the top octave rises no more than 2 dB over STAGE 77's from key 99 to 108, and at TONE
  0 DYNO keeps its TONE-64 level relation to STAGE 77 within 0.7 dB; with BELL 127 (DYNO's x1.8 on BELL's x3.9)
  keys 28..31 stay under 30000 (rh_ty_bell).
The voicing of tools/dry_dist.py (TYPE_BASE, LEVEL 52 as the scan), the engine alone (CC 119 RAW), RMS 50-500 ms after the note-on; fails on 8ce104d7a."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from hostrun import FS, run  # noqa: E402
import dry_dist as dd  # noqa: E402

TYPE_CC = {"ST77": 0, "FELT": 64, "DYNO": 96}


def notes(name, ty, keys, vel, tone=64, bell=64):
    ev = [f"30 midi 0xB0 {c} {v}" for c, v in dd.TYPE_BASE] + ["40 midi 0xB0 119 127", f"45 midi 0xB0 80 {TYPE_CC[ty]}", f"50 midi 0xB0 28 {tone}", f"55 midi 0xB0 30 {bell}"]
    for i, k in enumerate(keys):
        t = 300 + i * 1500
        ev += [f"{t} midi 0x90 {k} {vel}", f"{t + 900} midi 0x80 {k} 0"]
    r = run(f"tfix-{name}", ev, 300 + len(keys) * 1500 + 500)
    x = (r.L.astype(float) + r.R.astype(float)) / 2.0
    out = []
    for i in range(len(keys)):
        t = 300 + i * 1500
        s = x[int((t + 50) * FS / 1000):int((t + 500) * FS / 1000)]
        p = x[int(t * FS / 1000):int((t + 900) * FS / 1000)]
        out.append((20 * np.log10(np.sqrt(np.mean(s * s)) + 1e-9), float(np.abs(p).max())))
    return out


def test_felt_no_step_at_band_edges():
    keys = list(range(46, 89))
    for v in (20, 40, 100):
        lv = [a for a, _ in notes(f"felt-{v}", "FELT", keys, v)]
        st = np.abs(np.diff(lv))
        assert st.max() < 1.0, (v, dict(zip(keys[1:], np.round(st, 2))))


def test_dyno_top_keys():
    keys = [99, 102, 105, 108]
    d = notes("dyno-top", "DYNO", keys, 127)
    s = notes("st77-top", "ST77", keys, 127)
    assert max(p for _, p in d) < 30000, d
    rise = (d[-1][0] - d[0][0]) - (s[-1][0] - s[0][0])
    assert rise < 2.0, (rise, d, s)


def test_dyno_bell127():
    keys = [28, 29, 30, 31, 33, 36]
    d = notes("dyno-bell127", "DYNO", keys, 127, bell=127)
    assert max(p for _, p in d) < 30000, d                    # 8ce104d7a: keys 28..31 at the rail (39.4k unclipped)


def test_dyno_tone0():
    keys = [28, 34, 36, 55, 60]
    d0, s0 = notes("dyno-t0", "DYNO", keys, 127, 0), notes("st77-t0", "ST77", keys, 127, 0)
    d64, s64 = notes("dyno-t64", "DYNO", keys, 127), notes("st77-t64", "ST77", keys, 127)
    assert max(p for _, p in d0) < 30000, d0
    for i, k in enumerate(keys):
        rel0, rel64 = d0[i][0] - s0[i][0], d64[i][0] - s64[i][0]
        assert abs(rel0 - rel64) < 0.7, (k, rel0, rel64)
