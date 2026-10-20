# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA key flag bit3 (bars.c bar_init: the tube slot's held words at bar_k_decay where k->flags & 8): RING moves
the low notes, whose strongly coupled tube carries the long decay, as the golden model (tools/bars_model.py) says, on
the host build (RAW, CC 119); the type records agree with params.c TYPE_FLAGS; VIBES never sets the bit. Key flag bit4
(BAR_RINGG, gen_bars.ring_ceiling): RING over 64 keeps the single-strike ceiling."""
import json
import os
import re
import sys

import numpy as np
import pytest

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


# ---- the single-strike ceiling over RING (gen_bars.ring_ceiling, BAR_RINGG, key flag bit4)
CORNERS = [dict(bark=127, bell=127, tone=0, hammer=127), dict(bark=127, bell=127, tone=127, hammer=127),
           dict(bark=127, bell=0, tone=127, hammer=127), dict(bark=0, bell=127, tone=0, hammer=0)]


def test_ring_gain_table():
    """BAR_RINGG: one row per key, 1.0 (Q14) up to RING 64 (point 8: RING 64 exactly, so the default is untouched),
    never above 1.0 and non-increasing in RING; flag bit4 exactly on the keys whose row goes under 1.0; never VIBES"""
    rows = re.search(r"BAR_RINGG\[BAR_NKEY\]\[BAR_NPOS\] = \{(.*?)\n\};", HDR, re.S).group(1)
    R = [[int(v) for v in r.split(",")] for r in re.findall(r"\{([^{}]*)\}", rows)]
    assert len(R) == 88 and all(len(r) == 17 for r in R)
    keys = re.search(r"BAR_KEYS\[BAR_NTYPE\]\[BAR_NKEY\] = \{(.*?)\n\};", HDR, re.S).group(1)
    fl = [int(r[7]) for r in re.findall(r"\}, (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (\d+), (\d+), (\d+)\},", keys)]
    vib, mar = fl[:88], fl[88:]
    assert not any(f & 16 for f in vib)
    for r, f in zip(R, mar):
        assert r[:9] == [16384] * 9 and all(b <= a for a, b in zip(r, r[1:])), r
        assert bool(f & 16) == (min(r) < 16384)


@pytest.mark.parametrize("ring", [80, 100, 127])
def test_ring_keeps_the_single_strike_ceiling(ring):
    """the golden model (the C's gain words, the RING gain included): every key's v127 single strike at the four
    loud corners of the voicing grid (bar_scan's S2 corners) stays under the -0.5 dBFS ceiling at RING over 64
    (before: C2..B3 up to +7 dBFS at RING 127, RUBBER TUBE 127: the RAW output clipped 35,176 samples)"""
    keys = list(range(21, 109))
    ceil_ = 10 ** (-0.5 / 20) * 32768 * 10 ** (0.15 / 20)        # (+0.15 dB: the corners' interpolation between points)
    for c in CORNERS:
        y = gm.render_batch(SPEC, keys, [127] * len(keys), 1.0, knobs=dict(gm.DEFAULT_KNOBS, decay=ring, **c))
        pk = np.abs(y).max(axis=1)
        over = [(n, round(20 * np.log10(p / 32768), 2)) for n, p in zip(keys, pk) if p > ceil_]
        assert not over, (ring, c, over)


def test_ring_corner_does_not_clip_raw():
    """the C on the host, RAW: bar_scan's corner 1 (RUBBER, TUBE 127, centre, WEIGHT 127, RING 127) on C3 and C2, v127,
    under full scale (before: clipped)"""
    for key in (36, 48):
        ls = ["50 midi 0xB0 119 127", "50 midi 0xB0 104 0", "60 midi 0xB0 79 127", "61 midi 0xB0 102 127",
              "61 midi 0xB0 29 127", "61 midi 0xB0 30 127", "61 midi 0xB0 28 0", "61 midi 0xB0 31 127",
              f"100 midi 0x90 {key} 127"]
        x = hostrun.run(f"mring-corner-{key}", ls, end_ms=1600).L.astype(float)
        assert 20 * np.log10(np.abs(x).max() / 32767) <= -0.4, (key, np.abs(x).max())
