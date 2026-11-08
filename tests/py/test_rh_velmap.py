# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes velocity map (0.9.10, S1b; rhodes.c rh_vm, tools/gen_velmap.py, docs/model/data/velmap.json): the bark
(H2..H5 re H1, 30-150 ms) flat below a knee that rises with the register and steep above it, the fundamental
saturating in the bass, the tables in range, and a re-strike without a click (the ringing note's states rescaled to
the new gain). Host build, CC 119 RAW, CC 40 127 (RESPONSE: the map), NOISE 0, one note per run (a fresh voice)."""
import json
import os
import re
import sys

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, ROOT, run  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_velmap as gv  # noqa: E402

SOS = butter(4, 5000, "highpass", fs=FS, output="sos")
CFG = json.load(open(os.path.join(ROOT, "docs", "model", "data", "velmap.json")))


def note(tag, key, vel):
    r = run(tag, ["20 midi 0xB0 119 127", "25 midi 0xB0 40 127", "30 midi 0xB0 104 0", f"100 midi 0x90 {key} {vel}"], end_ms=560)
    x = r.L / 32768.0
    a = np.abs(x)
    i = int(np.argmax(a > 0.02 * a.max()))
    return gv.metrics(x[max(0, i - 22):], key)


def test_tables_in_range():
    t = open(os.path.join(ROOT, "firmware", "gen", "rh_velmap.h")).read()
    for name in ("RH_VM_X", "RH_VM_G"):
        body = t[t.index(name + "[RH_NKEY][17]"):]
        body = body[:body.index("};")]
        rows = re.findall(r"\{([0-9,]+)\}", body)
        assert len(rows) == 88
        for r in rows:
            v = [int(q) for q in r.split(",")]
            assert len(v) == 17 and all(0 < q < 65536 for q in v)


@pytest.mark.parametrize("key", [33, 45, 57, 64, 69, 76, 88])
def test_bark_knee(key):
    """flat below the knee (within 2 dB from v16 to 8 below it), +0.45 dB / velocity above (to v127, within 25 %),
    none from C6 up; the measured bark within 2 dB of the target at v32, the knee + 24 and v127"""
    kn = gv.pts(CFG["knee_points"], key)
    b = {v: note(f"vm_{key}_{v}", key, v)[0] for v in sorted({16, 32, 127, int(min(kn, 120)) - 8, int(min(kn + 24, 127))})}
    for v, got in b.items():
        assert abs(got - gv.bark_target(CFG, key, v)) < 2.0, (key, v, got, gv.bark_target(CFG, key, v))
    lo = int(min(kn, 120)) - 8
    assert abs(b[lo] - b[16]) < 2.0, b
    if kn < 120:
        rise = (b[127] - b[lo]) / (127 - kn)
        assert 0.33 < rise < 0.6, (key, rise, b)
    else:
        assert b[127] - b[16] < 2.0, b


@pytest.mark.parametrize("key,top", [(33, 5.6), (45, 5.9), (60, 8.3)])
def test_h1_saturates(key, top):
    """H1(v127) - H1(v64): the map's saturation (velmap.json h1_top_db: +3 dB in the bass, +6 dB E3..D#4; 0.9.9: 14 dB)
    plus the level make-up (rh_velmap_mk.h: the map at the 0.9.9 loudness from v48 up, +2.3..2.6 dB more at v127 than
    at v64 at these keys; re-baselined 2026-11-08 for the loudness rule, the bark's knee unchanged: test_bark_knee)"""
    h64 = note(f"vm_h1_{key}_64", key, 64)[1]
    h127 = note(f"vm_h1_{key}_127", key, 127)[1]
    assert abs((h127 - h64) - top) < 1.5, (key, h127 - h64)


@pytest.mark.parametrize("key", [33, 52, 64, 69])
@pytest.mark.parametrize("v1,v2", [(30, 127), (127, 30), (127, 1)])
@pytest.mark.parametrize("pedal", [0, 1])
def test_restrike_no_click(key, v1, v2, pedal):
    """a note let go and struck again 100 ms later at another velocity (the voice still rings: the re-strike path),
    with the pedal up (the felt on it) and down (ringing at full level): the >5 kHz peak in the 1.5 ms before to 6 ms
    after the strike under 10 dB over the old note alone + a fresh strike (the pickup is nonlinear, so the two together
    may be brighter than the sum; a step in the old note's swing would be far more). 0.9.9: up to +0 dB. The map: the
    felt's ramp under the strike +15.6 dB (E3 30 -> 127, pedal up), the old note rescaled 7.5x +13 dB (pedal down);
    the felt lifted at once and the rescale capped at X_new / X_old (rhodes.c rh_vm_restrike): up to +8 dB"""
    pre = ["20 midi 0xB0 119 127", "25 midi 0xB0 40 127", "30 midi 0xB0 104 0"]
    ped = ["150 midi 0xB0 64 127"] if pedal else []
    x = run(f"vm_rs_{key}_{v1}_{v2}_{pedal}", pre + ped + [f"200 midi 0x90 {key} {v1}", f"1100 midi 0x80 {key} 0",
                                                         f"1200 midi 0x90 {key} {v2}"], end_ms=1400).L
    c = run(f"vm_rc_{key}_{v1}_{pedal}", pre + ped + [f"200 midi 0x90 {key} {v1}", f"1100 midi 0x80 {key} 0"], end_ms=1400).L
    f = run(f"vm_rf_{key}_{v2}", pre + [f"1200 midi 0x90 {key} {v2}"], end_ms=1400).L
    i = int(1.2 * FS)
    w = slice(i - 64, i + int(0.006 * FS))
    pk = lambda y: np.abs(sosfilt(SOS, y)[w]).max()
    r = 20 * np.log10(pk(x) / (pk(c) + pk(f) + 1e-9))
    assert r < 10.0, (key, v1, v2, pedal, r)
