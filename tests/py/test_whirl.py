# SPDX-License-Identifier: GPL-3.0-only
"""The whirl (S1 of the rhodes-sonic plan): on keys 40-84 the V2 slot holds a beat partner of V1, so a held note's
level wobbles slowly (two detuned vertical modes: the energy exchange of a real tine's two polarisations) while its
pitch stays put. The C engine on the host (tools/rh_whirl.py: each note alone, held 4 s, the effects bypassed):
A3, C4 and A4 at v100 ripple 1-3 dB at 0.5-1.5 Hz with the pitch within +-1 cent; every partner key within the law
(w <= 0.25, beat 0.2-1.5 Hz: no chorus; the bass keys' beat is lowered to keep the pitch within 1 c); V1 still exactly on f0. The 0.9.5 tables ripple under 0.5 dB (no beat)."""
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402
import rh_whirl as rw  # noqa: E402

SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
P = SPEC["params"]
BY = {k["midi"]: k for k in SPEC["keys"]}


def f0(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def test_partner_law():
    lo, hi = P["whirl"]["keys"]
    assert (lo, hi) == (40, 84)
    for n in range(21, 109):
        v1, v2 = BY[n]["modes"][:2]
        assert abs(v1["f"] / f0(n) - 1) < 1e-9, n                       # V1's pitch is never jittered
        if lo <= n <= hi:
            beat = v2["f"] - v1["f"]
            assert 0.2 <= abs(beat) <= 1.5 and abs(beat - v2["beat_hz"]) < 1e-9, (n, beat)
            assert 0.02 <= v2["w_rel"] <= 0.25 and abs(v2["w"] / v1["w"] - v2["w_rel"]) < 1e-9, n
            assert 0.79 <= v2["t60"] / v1["t60"] <= 1.11 and v2["t60_rel"] == v1["t60_rel"], n
        else:
            assert "beat_hz" not in v2, n
            if any(a <= n <= b for a, b in P["v2_drop"]["keys"]):
                assert v2["f"] == 0 and v2["w"] == 0, n                 # O1: absent
            else:
                assert v2["f"] < 0.9 * v1["f"], n                       # the tone-bar mode (keys 100-108)


@pytest.fixture(scope="module")
def notes():
    b = hostrun.build()
    return {n: rw.measure(x, f0(n)) for n, x in rw.render(b, [57, 60, 69], 100, 4.0, out=hostrun.out("whirl"))}


@pytest.mark.parametrize("n", [57, 60, 69])
def test_ripple_and_pitch(notes, n):
    r = notes[n]
    assert 1.0 <= r["ripple_db"] <= 3.0, r
    assert 0.5 <= r["rate_hz"] <= 1.5, r
    assert r["cents_max"] <= 1.0 and abs(r["cents_mean"]) <= 0.2, r
