# SPDX-License-Identifier: GPL-3.0-only
"""The VIBES VOICE cards are heard (user, 2026-10-17: "voice parameters seem like doing nothing"; "I never am able to get
a soft mallet hit, just hard hits like a bell"). Each card set from the panel as a player does (EDIT, K1..K4 turned
from 64: tools/bar_cards.py), keys F3 / A4 / F6 (53 69 89) at v100 and v127 (and v117, the user's KEY VEL, for the
soft mallets), RAW, NOISE 0, against minimum differences:

  dist     spectral distance: 1/3-octave band levels 0..0.5 s, each spectrum re its own total, floored 60 dB under the
           louder one's strongest band (tools/bar_cards.dist). 3 dB RMS is a clear timbre change (one octave's bands
           moved ~6 dB, or a partial ~15 dB): two settings under it sound like the same instrument voicing.
  V2, V3   the 4th / 10th partial re the fundamental over the attack (5..300 ms): the mallet's and the strike's job.
  level    LUFS 0..1 s: TUBE's and WEIGHT's job on a real vibraphone (the tube's bloom, the mallet's mass).

Where a key cannot change by physics the test says why (e.g. on F6 every yarn mallet's pulse is longer than the 4th
partial's period, so SOFT, YARN and the default all give a pure fundamental there). Before the 2026-10-17 pass these
fail: STRIKE centre vs L/3 on F6 (0.0 dB), TUBE's level (2.5..5 LU), the soft mallets at v117 / v127 (F3 V3 -18 dB),
YARN vs CORD (1.5 dB at F3), MALLET on F6 (0.0 dB). Run with BAR_CARDS_BIN / BAR_CARDS_SPEC set to an older build to
see them fail there."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bar_cards as bc  # noqa: E402

KEYS, VELS = (53, 69, 89), (100, 127)
DIST = 3.0                      # dB RMS: a clear timbre change
SOFT, YARN, DEF, CORD, RUBBER = 0, 42, 64, 85, 127
_R = {}


def meas(card, v, vels=VELS):
    k = (card, v, vels)
    if k not in _R:
        if not os.environ.get("BAR_CARDS_BIN"):
            bc.bm.build()
        _R[k] = bc.measure({card: v} if v != 64 else {}, vels=vels)[0]
    return _R[k]


def d(card, a, b, n, vel):
    return bc.dist(meas(card, a)[(n, vel)]["bands"], meas(card, b)[(n, vel)]["bands"])


def lv(card, a, b, n, vel):
    return meas(card, b)[(n, vel)]["lufs"] - meas(card, a)[(n, vel)]["lufs"]


# ---------------------------------------------------------------- MALLET
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_mallet_ends_differ(n, vel):
    assert d("BARK", SOFT, RUBBER, n, vel) >= DIST, (n, vel, d("BARK", SOFT, RUBBER, n, vel))


@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_mallet_ends_vs_the_default(n, vel):
    """RUBBER is brighter than the default mallet everywhere (on F3 at v127 the default's pulse already reaches every
    modelled partial: >= 2 dB there); SOFT darker where the default has upper partials at all (on F6 the default is
    already a pure fundamental: its 4th partial is ~-95 dB, and nothing is darker than that)"""
    assert d("BARK", DEF, RUBBER, n, vel) >= (2.0 if (n, vel) == (53, 127) else DIST), (n, vel)
    if n != 89:
        assert d("BARK", SOFT, DEF, n, vel) >= DIST, (n, vel, d("BARK", SOFT, DEF, n, vel))


@pytest.mark.parametrize("vel", (100, 117, 127))
@pytest.mark.parametrize("n", KEYS)
def test_soft_mallets_are_soft_at_any_velocity(n, vel):
    """SOFT and YARN never ring the bell: the 10th partial >= 20 dB under RUBBER's wherever RUBBER has one (above
    -60 dB) and under -25 dB re the fundamental; the 4th partial no louder than RUBBER's. SOFT is darker still."""
    vs = (100, 117, 127)
    r = meas("BARK", RUBBER, vs)[(n, vel)]
    for fam in (SOFT, YARN):
        m = meas("BARK", fam, vs)[(n, vel)]
        if r["v3"] > -60:
            assert m["v3"] <= r["v3"] - 20, (n, vel, fam, m["v3"], r["v3"])
        assert m["v3"] <= -25 and m["v2"] <= r["v2"] + 0.5, (n, vel, fam, m["v2"], m["v3"], r["v2"])
    s, y = meas("BARK", SOFT, vs)[(n, vel)], meas("BARK", YARN, vs)[(n, vel)]
    assert s["v2"] <= max(y["v2"], -90) + 0.5 and s["v3"] <= max(y["v3"], -90) + 0.5, (n, vel)   # (-90: the floor)
    assert s["cen_a"] <= y["cen_a"] * 1.02, (n, vel)


@pytest.mark.parametrize("vel", VELS)
def test_mallet_detents_pairwise_distinct(vel):
    """SOFT YARN CORD RUBBER: every pair >= 3 dB apart on at least one of F3 / A4 / F6 (the soft mallets part on the
    low bars, where their pulses still reach the 4th and 10th partials; the hard ones on the middle and high bars: on F3
    every modelled partial is inside both CORD's and RUBBER's pulse)"""
    det = (SOFT, YARN, CORD, RUBBER)
    for i, a in enumerate(det):
        for b in det[i + 1:]:
            best = max(d("BARK", a, b, n, vel) for n in KEYS)
            assert best >= DIST, (vel, a, b, [round(d("BARK", a, b, n, vel), 2) for n in KEYS])


# ---------------------------------------------------------------- STRIKE
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_strike(n, vel):
    """centre vs edge, and the edge (the torsion modes, metallic) vs L/3, apart in timbre on every key; the centre vs
    L/3 in timbre (no 4th partial, a strong 10th) or, on the top bars where the default mallet leaves no upper
    partial, in its fuller fundamental (>= 2 LU)"""
    assert d("TONE", 0, 127, n, vel) >= DIST, (n, vel)
    assert d("TONE", 64, 127, n, vel) >= DIST, (n, vel, d("TONE", 64, 127, n, vel))
    assert d("TONE", 0, 64, n, vel) >= DIST or lv("TONE", 64, 0, n, vel) >= 2.0, \
        (n, vel, d("TONE", 0, 64, n, vel), lv("TONE", 64, 0, n, vel))


# ---------------------------------------------------------------- TUBE
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_tube(n, vel):
    """the bare bar (0) is the quieter, thinner one: 6..12.5 LU from 0 to 127 (vibes.json card_level bell_span_db 12;
    on a real vibraphone the tube is mostly level and bloom), >= 4 LU from 0 to 64, more tube still adds (>= 0.5 LU),
    and without the tube the upper partials stand out (V2 re V1 up >= 3 dB where it is above -60 dB)"""
    full = lv("BELL", 0, 127, n, vel)
    assert 6.0 <= full <= 12.5, (n, vel, full)
    assert lv("BELL", 0, 64, n, vel) >= 4.0 and lv("BELL", 64, 127, n, vel) >= 0.5, (n, vel)
    a, b = meas("BELL", 0)[(n, vel)], meas("BELL", 64)[(n, vel)]
    if b["v2"] > -60:
        assert a["v2"] >= b["v2"] + 3, (n, vel, a["v2"], b["v2"])


# ---------------------------------------------------------------- WEIGHT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_weight(n, vel):
    """light (0) brighter, heavy (127) darker with more body: >= 2.5 LU from 0 to 127, >= 1.5 LU light to 64,
    >= 0.5 LU 64 to heavy (on F6 the heavy mallet's longer contact is played at T7f's cap); the attack's centroid falls from light to heavy and the timbre moves >= 3 dB on F3 / A4 (on F6 the default
    mallet's pulse leaves no upper partial to move)"""
    assert lv("HAMMER", 0, 127, n, vel) >= 2.5, (n, vel)
    assert lv("HAMMER", 0, 64, n, vel) >= 1.5 and lv("HAMMER", 64, 127, n, vel) >= 0.5, (n, vel)
    if n != 89:
        assert d("HAMMER", 0, 127, n, vel) >= DIST, (n, vel, d("HAMMER", 0, 127, n, vel))
        assert meas("HAMMER", 0)[(n, vel)]["cen_a"] > 1.05 * meas("HAMMER", 127)[(n, vel)]["cen_a"], (n, vel)


# ---------------------------------------------------------------- never clipping, the default where it was
def test_no_card_extreme_clips():
    """every card extreme at v127 stays under the generator's -0.5 dBFS per-voice ceiling (+0.5 dB rounding)"""
    for card in ("TONE", "BARK", "BELL", "HAMMER"):
        for v in (0, 127):
            for k, r in meas(card, v).items():
                assert r["peak"] <= 0.0, (card, v, k, r["peak"])
