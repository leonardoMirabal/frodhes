# SPDX-License-Identifier: GPL-3.0-only
"""The full-rate REVERB TYPEs (FR_REV_HQ, a build switch, off by default: firmware/src/rev_hq.c, rev_hq_hall.c,
rev_hq_plate.c, rev_hq_inf.c) on the firmware's own fx_reverb (tests/c/rev_scan.c built with -DFR_REV_HQ=1):

- every test of the TYPE framework (test_rev_types.py) that runs on fx_reverb, on the HQ build: the T60 per DECAY,
  FREEZE / SUSTAIN held for ten minutes and full-scale bursts while held, a TYPE switch without a click and the old
  tail gone after the fade, stale lines read as empty, OFF rings out, the tails end in exact zero, SIZE / MOD / LOW CUT
  / TONE / PRE-DELAY move what they should, MIX does not bend a tail, the levels against HALL;
- the HQ's own: the band above 11 kHz (the half rate's limit) is there; each TYPE's wet within 1.5 dB of the half-rate
  one's (critical-band loudness) and its T60 within 25 % per octave 500 Hz .. 4 kHz (HALL, PLATE); no overflow at the
  worst input (full scale at MIX 127, DECAY 127, TONE bright); CC 10 (fx_reset_state) brings every TYPE to rest (what
  follows renders as from a fresh start); the cost model's rows.
The switch off (the default) builds the half-rate TYPEs byte-identical: Gate R (tools/gate_r.py) and
test_rev_types.py on the default build."""
import inspect
import os
import sys

import numpy as np
import pytest

import test_rev_types as base
from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import rev_scan as rs  # noqa: E402
import rev_hq_ab as ab  # noqa: E402

T = rs.TYPES
BLK = rs.BLK
HQ = ["-DFR_REV_HQ=1"]
# the framework's tests on fx_reverb (they take the exe fixture), less the half rate's cost gate (HALL + 200 us: the
# full rate costs more by design; its own rows below)
_SKIP = {"test_cost_per_type", "test_cpu_model_rows_follow_the_firmware", "test_infinite_bright_cost"}
for _n, _f in list(vars(base).items()):
    if _n.startswith("test_") and "exe" in inspect.signature(_f).parameters and _n not in _SKIP:
        globals()[_n] = _f
silent = base.silent
needs_types = base.needs_types


# THRESHOLD (0.9.10 rev-grain, coordinator-approved): the 2828..5657 Hz octave on the HQ build 7.0 dB, was 6.0. The
# HQ HALL lost rounding noise there and moved toward the 2x-precise reference (88.72 -> 87.69 dB, reference 83.54;
# PLATE 94.22), so PLATE - HALL widened from 5.50 to 6.52 dB for a good reason. Every other octave stays at 6.0.
# OPEN (2026-11-09, on rc-0910 1dafc37f): rc-0910 alone 6.26 dB (HALL 87.96), rev-grain on it 7.14 (HALL 87.08):
# over this 7.0. The octave is noise-led: HALL's band per input dB falls with the level (92.4 / 87.0 / 82.4 / 80.7
# at chord amp 4000 / 8000 / 16000 / 24000, scaled to 8000), PLATE - HALL at 24000 11.7 dB. For the coordinator.
HQ_WIDE = {(2828, 5657): 7.0}


@needs_types
@pytest.mark.parametrize("typ", base.NEW)
def test_levels_match_hall(exe, silent, typ):
    """test_rev_types.py's, with the HQ's one wider octave (HQ_WIDE)"""
    base.levels_match_hall(exe, silent, typ, HQ_WIDE)


@pytest.fixture(scope="module")
def exe():
    if not base.HAS:
        pytest.skip("no reverb TYPEs in this tree")
    e = rs.build(cflags=HQ, tag="pytest-hq")
    yield e
    os.remove(e)


@pytest.fixture(scope="module")
def old():
    e = rs.build(tag="pytest-hq-old")
    yield e
    os.remove(e)


def _mid(w):
    return np.asarray(w, float)


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_band_above_11k(exe, old, typ):
    """the full rate's point: the tail keeps the band above 11 kHz (the half rate's top), >= 6 dB more than the half
    rate's there (its images and its down / up filters' residue only)"""
    x = rs.burst(rs.FS)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_TONE=100)
    tn = ab.top_band(rs.render(exe, x, p, blocks=200)["wet"], 0.1, 0.5)
    to = ab.top_band(rs.render(old, x, p, blocks=200)["wet"], 0.1, 0.5)
    assert tn >= to + 6, f"{typ}: >11 kHz re 1-4 kHz: HQ {tn:.1f} dB, half rate {to:.1f} dB"


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_level_matches_half_rate(exe, old, typ):
    """the A/B is fair: the wet's critical-band loudness within 1.5 dB of the half-rate TYPE's (a noise burst, MIX
    100); HALL and PLATE: the T60 per octave 500 Hz .. 4 kHz within 25 % of the half rate's"""
    x = rs.burst(rs.FS)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100)
    secs = 25 if typ == "INFINITE" else 8
    wn = rs.render(exe, x, p, blocks=int(secs * rs.FS / BLK))["wet"].astype(float)
    wo = rs.render(old, x, p, blocks=int(secs * rs.FS / BLK))["wet"].astype(float)
    g = 20 * np.log10(ab.match_gain(ab.bark_loud(wo[:2 * rs.FS])[0], ab.bark_loud(wn[:2 * rs.FS])[0]))
    assert abs(g) <= 1.5, f"{typ}: the HQ's wet {-g:+.2f} dB against the half rate's"
    if typ != "INFINITE":
        for f in (500, 1000, 2000, 4000):
            a, b = ab.oct_t60(wn, f), ab.oct_t60(wo, f)
            assert a and b and abs(a / b - 1) <= 0.25, f"{typ} {f} Hz: T60 HQ {a} s, half rate {b} s"


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_worst_input_no_overflow(exe, typ):
    """full-scale square bursts (+-32767 both sides, over the clamp of the send) at MIX 127, DECAY 127, TONE 127, MOD
    127, SIZE 127: the wet stays bounded (the lines clamp; |wet| <= 2^19: the framework's exact return) and never
    wraps (no step of 2^16 or more between samples)"""
    n = BLK * 400
    t = np.arange(n)
    x = np.where((t // 2000) % 2 == 0, 32767, -32767)
    x[n // 2:] = 0
    p = dict(RV_TYPE=T[typ], RV_DEPTH=127, RV_DECAY=127, RV_TONE=127, RV_MOD=127, RV_SIZE=127)
    r = rs.render(exe, x, p)
    w = r["wet"]
    assert np.abs(w).max() <= 1 << 19, f"{typ}: |wet| {np.abs(w).max()}"
    assert np.abs(np.diff(w, axis=0)).max() < 1 << 16, f"{typ}: a step of {np.abs(np.diff(w, axis=0)).max()}"


@needs_types
@pytest.mark.parametrize("typ", ("HALL", "PLATE", "INFINITE"))
def test_hq_cc10_brings_it_to_rest(exe, typ):
    """CC 10 (fx_reset_state, before block B): from there on the output is exactly a fresh start's on what follows
    (the lines, the diffusers, the one-poles, the framework: nothing of the tail before is left)"""
    B = 150
    a = base.held_chord(BLK * B)
    b = rs.chord(BLK * 200, seed=3)
    p = dict(RV_TYPE=T[typ], RV_DEPTH=100)
    r = rs.render(exe, np.concatenate([a, b]), p, blocks=B + 400, reset=B)
    f = rs.render(exe, np.concatenate([np.zeros(BLK, np.int64), b]), p, blocks=401, reset=1)
    assert np.array_equal(r["y"][B * BLK:], f["y"][BLK:]), f"{typ}: the state before CC 10 still sounds"


@needs_types
def test_hq_cost_rows(exe):
    """fx_cost_us while each TYPE runs: the framework's front + the kernel's row (rev_hq.c REV_US_FRONT, REV_US_HQH,
    rev_hq_plate.c / rev_hq_inf.c REV_US_PLATE / REV_US_INF); every TYPE within REV_US_HQ_MAX"""
    src = "".join(open(os.path.join(ROOT, "firmware", "src", f)).read()
                  for f in ("rev_hq.c", "rev_hq_hall.c", "rev_hq_plate.c", "rev_hq_inf.c"))
    import re
    d = {k: int(re.search(rf"#define {k} (\d+)u", src).group(1))
         for k in ("REV_US_FRONT", "REV_US_HQH", "REV_US_PLATE", "REV_US_INF", "REV_US_HQ_MAX")}
    row = dict(HALL=d["REV_US_HQH"], PLATE=d["REV_US_PLATE"], INFINITE=d["REV_US_INF"])
    x = rs.chord(BLK * 40)
    for typ, us in row.items():
        r = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100), blocks=40)
        assert r["cost"] == d["REV_US_FRONT"] + us, (typ, r["cost"])
        assert us <= d["REV_US_HQ_MAX"], typ
