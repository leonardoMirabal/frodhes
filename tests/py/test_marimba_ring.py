# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA key flag bit3 (bars.c bar_init: the tube slot's held words at bar_k_decay where k->flags & 8): RING moves
the low notes, whose strongly coupled tube carries the long decay, as the golden model (tools/bars_model.py) says, on
the host build (RAW, CC 119); the type records agree with params.c TYPE_FLAGS; VIBES never sets the bit."""
import json
import os
import re
import sys

import numpy as np

import hostrun

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bars_model as gm   # noqa: E402
import marimba_va as mva  # noqa: E402

SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "marimba_spec.json")))
HDR = open(os.path.join(ROOT, "firmware", "gen", "bars_tables.h")).read()


def host_note(key, decay, dur_ms=6000):
    """MARIMBA (CC 79 127), RAW, RING = DECAY (CC 102), one v100 note held (pedal down: no key-up)"""
    ls = ["50 midi 0xB0 119 127", "60 midi 0xB0 79 127", "61 midi 0xB0 64 127", f"62 midi 0xB0 102 {decay}",
          f"100 midi 0x90 {key} 100"]
    r = hostrun.run(f"mring-{key}-{decay}", ls, end_ms=dur_ms + 200)
    return r.seg(0.1, 0.1 + dur_ms / 1000).astype(float)


def test_ring_moves_c2_as_the_golden_model():
    """C2: the time to -30 dB at RING 0 and 127 on the host within 10 % of the golden model's, their ratio >= 2 (the
    plan's RING job: without bit3 the bar alone moved C2 by x1.3)"""
    rows = []
    for d in (0, 64, 127):
        th = mva.decay_time(host_note(36, d))
        tg = mva.decay_time(gm.render_batch(SPEC, [36], [100], 6.0, dict(gm.DEFAULT_KNOBS, decay=d), sleep=False)[0])
        rows.append((d, th, tg))
        assert abs(th / tg - 1) <= 0.10, (d, th, tg)
    print("C2 time to -30 dB (RING, host, golden):", [(d, round(a, 3), round(b, 3)) for d, a, b in rows])
    assert rows[2][1] / rows[0][1] >= 2.0, rows


def test_key_flag_bit3_only_on_marimba_strong_keys():
    keys = re.search(r"BAR_KEYS\[BAR_NTYPE\]\[BAR_NKEY\] = \{(.*?)\n\};", HDR, re.S).group(1)
    rows = re.findall(r"\}, (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (\d+), (\d+), (\d+)\},", keys)
    assert len(rows) == 2 * 88
    vib, mar = [int(r[7]) for r in rows[:88]], [int(r[7]) for r in rows[88:]]
    assert not any(f & 8 for f in vib)
    D = {d["midi"]: d for d in SPEC["design"]}
    for n, f in zip(range(21, 109), mar):
        assert bool(f & 8) == (D[n]["regime"] == "strong"), n


def test_type_flags_agree_with_the_type_records():
    """params.c TYPE_FLAGS[t] == BAR_TYPE[TYPE_SUB[t]].flags for every bar TYPE (a C _Static_assert cannot read a
    const array's elements; this is its check)"""
    pc = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()

    def arr(name):
        return [int(x) for x in re.search(r"%s\[N_TYPE_N\] = \{([^}]*)\}" % name, pc).group(1).split(",")]
    eng, sub, fl = arr("TYPE_ENGINE"), arr("TYPE_SUB"), arr("TYPE_FLAGS")
    bt = re.search(r"BAR_TYPE\[BAR_NTYPE\] = \{[^\n]*\n(.*?)\n\};", HDR, re.S).group(1)
    recs = [[int(x) for x in re.findall(r"-?\d+", ln)] for ln in bt.splitlines()]
    for t, e in enumerate(eng):
        if e == 1:
            assert fl[t] == recs[sub[t]][7], (t, fl[t], recs[sub[t]])
