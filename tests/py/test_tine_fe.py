# SPDX-License-Identifier: GPL-3.0-only
"""tools/tine_fe.py: the bare cantilever must give the textbook ratios, and a spring mass must reproduce the
measured F1 partial ratio of ~7.1 (Gabrielli et al., quoted in research/02)."""
import os
import sys

import numpy as np

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import tine_fe as t  # noqa: E402

LENGTHS = t.load_lengths(os.path.join(HERE, "..", "..", "docs", "model", "data", "tine_lengths.csv"))


def test_bare_cantilever_ratios():
    f, _ = t.Tine(0.15, spring_mass=0.0).modes(3)
    assert abs(f[1] / f[0] / 6.2669 - 1) < 1e-3
    assert abs(f[2] / f[0] / 17.5475 - 1) < 1e-3


def test_bare_cantilever_absolute():
    # analytic: f1 = (1.8751^2 / 2pi) sqrt(EI / (rho A L^4)); E1 tine 156.4 mm -> ~55.0 Hz (review 01 #6)
    f, _ = t.Tine(0.1564).modes(1)
    assert abs(f[0] - 55.0) < 0.3


def test_lengths_table():
    assert len(LENGTHS) == 73 and min(LENGTHS) == 28 and max(LENGTHS) == 100
    assert LENGTHS[28] > LENGTHS[100]


def test_f1_spring_reproduces_measured_ratio():
    L, f0 = LENGTHS[29], t.midi_hz(29)
    ms = t.tune_spring(L, f0, 0.65)
    f, _ = t.Tine(L, 0.65, ms).modes(3)
    assert abs(f[0] - f0) < 0.01
    assert 1.5e-3 < ms < 3.5e-3                    # ~2.3 g (review 01 #6)
    assert abs(f[1] / f[0] - 7.11) < 0.15          # measured 7.11 (SLDV, F1)


def test_ratio_falls_towards_treble():
    r = []
    for n in (29, 52, 69, 96):
        L, f0 = LENGTHS[n], t.midi_hz(n)
        try:
            ms = t.tune_spring(L, f0, 0.65)
        except ValueError:
            ms = 0.0
        f, _ = t.Tine(L, 0.65, ms).modes(2)
        r.append(f[1] / f[0])
    assert all(a > b for a, b in zip(r, r[1:])), r


def test_tip_shape_and_effective_mass():
    L, f0 = LENGTHS[45], t.midi_hz(45)
    tine = t.Tine(L, 0.65, t.tune_spring(L, f0, 0.65))
    f, sh = tine.modes(2)
    assert abs(sh(L, 0)) > abs(sh(0.5 * L, 0)) > 0
    assert np.isfinite(tine.effective_mass(L, 0, sh))
