# SPDX-License-Identifier: GPL-3.0-only
"""The MARIMBA VOICE cards are heard (MARIMBA PLAN §0 item 3, §10.2 B2; the VIBES lessons of §16: cards that passed
every damage check while doing nothing audible, families bunched together). Each card set from the panel as a player
does (VOICE 1/2, K1..K4 turned from 64: tools/marimba_scan.panel), keys C3 / C4 / C6 (48 60 84; TUBE also C2) at
v100, v117 (the user's KEY VEL) and v127, RAW, NOISE 0, MARIMBA by CC 79 127 (tools/marimba_scan.py measures):

  STRIKE   0 vs 64 and 64 vs 127: >= 3 dB spectral distance (bar_cards.dist) on every key and velocity
  MALLET   the four families SOFT MED HARD RUBBER (0 42 85 127) pairwise >= 3 dB on every key and velocity (§0 item
           2); RUBBER's attack centroid >= 4x SOFT's on C4 at v117 (§2.4)
  TUBE     >= +4 LU from 0 to 64, >= +0.5 LU from 64 to 127; the V1 T60 x1.3 or more from 0 to 64 on C2 and C3
  WEIGHT   >= +2.5 LU from 0 to 127, the light mallet brighter (attack centroid ratio >= 1.05)

While firmware/gen/bars_tables.h is the sync-1 stub (MARIMBA = VIBES) these numbers are VIBES's: each MARIMBA-specific
assertion is an expected failure (xfail, strict=False, "awaiting sync 2 real tables") then, and only then; the
thresholds are the plan's. The card names on the panel (display = sound) and the clipping ceiling are judged now."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import marimba_scan as ms  # noqa: E402
from hostrun import build, cc, run  # noqa: E402

AWAIT = pytest.mark.xfail(ms.stub_detected(), reason=ms.await_reason(), strict=False)
KEYS, VELS = (48, 60, 84), (100, 117, 127)
DIST = 3.0
FAMS = dict(SOFT=ms.SOFT, MED=ms.MED, HARD=ms.HARD, RUBBER=ms.RUBBER)
_R = {}


def meas(card, v, keys=KEYS):
    k = (card, v, keys)
    if k not in _R:
        build()
        _R[k] = ms.strike_set(ms.bm_tag("cards", card, v, *keys), ms.base() + ms.panel({card: v}), keys, VELS)
    return _R[k]


def d(card, a, b, n, vel):
    return ms.dist(meas(card, a)[(n, vel)]["bands"], meas(card, b)[(n, vel)]["bands"])


def lv(card, a, b, n, vel, keys=KEYS):
    return meas(card, b, keys)[(n, vel)]["lufs"] - meas(card, a, keys)[(n, vel)]["lufs"]


# ---------------------------------------------------------------- display = sound (now)
@pytest.mark.parametrize("card,enc,vals", [("STRIKE", 2, {0: "CENTRE", 64: "L/3", 127: "EDGE"}),
                                           ("MALLET", 3, {0: "SOFT", 42: "MED", 85: "HARD", 127: "RUBBER"})])
def test_card_names_on_the_panel(card, enc, vals):
    """the card shows the family / the strike point exactly at its detent (turned on the panel from 64)"""
    for v, name in vals.items():
        r = run(f"mc-name-{card}-{v}", [cc(20, 79, 127)] + [f"{t} {s}" for t, s in ms.panel(
            {dict(STRIKE="TONE", MALLET="BARK")[card]: v})] + ["400 dump"], 500)
        st = r.state(400)
        assert st["card.2.0"] == "MARIMBA" and st[f"vlabel.0.{enc - 2}"] == card, st
        assert st[f"vcard.0.{enc - 2}"] == name, (card, v, st[f"vcard.0.{enc - 2}"])


# ---------------------------------------------------------------- STRIKE
C6_STRIKE = pytest.mark.xfail(reason=ms.KNOWN_CARDS["STRIKE C6"], strict=False)


@AWAIT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", [48, 60, pytest.param(84, marks=C6_STRIKE)])
def test_strike(n, vel):
    assert d("TONE", 0, 64, n, vel) >= DIST, (n, vel, d("TONE", 0, 64, n, vel))
    assert d("TONE", 64, 127, n, vel) >= DIST, (n, vel, d("TONE", 64, 127, n, vel))


# ---------------------------------------------------------------- MALLET
FAM_Q = dict(SOFT=0, MED=1, HARD=3, RUBBER=4)          # the families' index (marimba.json T7c; DEFAULT is 2)
_SPEC = None


def capped(n, vel, a, b):
    """both contacts at T7f's cap (marimba_va.capped, Gate VA-M9's rule: the same pulse shape played at the same rate,
    VIBES's F6-class exceptions, PLAN §10.2 B2 'documented per key')"""
    global _SPEC
    if _SPEC is None:
        _SPEC = ms.va().load_spec()
    return ms.va().capped(_SPEC, n, vel, FAM_Q[a]) and ms.va().capped(_SPEC, n, vel, FAM_Q[b])


@AWAIT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_mallet_families_pairwise(n, vel):
    """every pair >= 3 dB apart, except a pair whose two contacts are both capped (listed: on C6 SOFT-MED at v100 /
    v117 / v127, exactly VA-M9's exceptions)"""
    names = list(FAMS)
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    exc = [(a, b) for a, b in pairs if capped(n, vel, a, b)]
    assert exc == ([("SOFT", "MED")] if n == 84 else []), (n, vel, exc)
    bad = [(a, b, round(d("BARK", FAMS[a], FAMS[b], n, vel), 2)) for a, b in pairs
           if (a, b) not in exc and d("BARK", FAMS[a], FAMS[b], n, vel) < DIST]
    assert not bad, (n, vel, bad)


@AWAIT
def test_rubber_brighter_than_soft():
    q = meas("BARK", ms.RUBBER)[(60, 117)]["cen_a"] / meas("BARK", ms.SOFT)[(60, 117)]["cen_a"]
    assert q >= 4.0, q


# ---------------------------------------------------------------- TUBE
@AWAIT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", (36,) + KEYS)
def test_tube_level(n, vel):
    k = (36,) + KEYS
    assert lv("BELL", 0, 64, n, vel, k) >= 4.0, (n, vel, lv("BELL", 0, 64, n, vel, k))
    assert lv("BELL", 64, 127, n, vel, k) >= 0.5, (n, vel, lv("BELL", 64, 127, n, vel, k))


@AWAIT
@pytest.mark.parametrize("n", (36, 48))
def test_tube_lengthens_the_low_notes(n):
    build()
    t = {b: ms.ring_set(ms.bm_tag("cards-tube-ring", b, n), ms.base(mute=0) + ms.panel({"BELL": b}), (n,))[n]["t60"]
         for b in (0, 64)}
    assert t[64] >= 1.3 * t[0], (n, t)


# ---------------------------------------------------------------- WEIGHT
@AWAIT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", KEYS)
def test_weight(n, vel):
    assert lv("HAMMER", 0, 127, n, vel) >= 2.5, (n, vel, lv("HAMMER", 0, 127, n, vel))


@AWAIT
@pytest.mark.parametrize("vel", VELS)
@pytest.mark.parametrize("n", [48, 60, pytest.param(84, marks=pytest.mark.xfail(
    reason=ms.KNOWN_CARDS["WEIGHT C6 centroid"], strict=False))])
def test_weight_light_is_brighter(n, vel):
    q = meas("HAMMER", 0)[(n, vel)]["cen_a"] / meas("HAMMER", 127)[(n, vel)]["cen_a"]
    assert q >= 1.05, (n, vel, q)


# ---------------------------------------------------------------- never clipping (now)
def test_no_card_extreme_clips():
    """every VOICE card's extremes at v127 stay under full scale on the engine's own output (RAW)"""
    for card in ("TONE", "BARK", "BELL", "HAMMER"):
        for v in (0, 127):
            for k, r in meas(card, v).items():
                assert r["peak"] <= 0.0, (card, v, k, r["peak"])
