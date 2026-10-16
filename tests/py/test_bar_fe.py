# SPDX-License-Identifier: GPL-3.0-only
"""tools/bar_fe.py: the free-free bar must give the textbook ratios, the Saint-Venant torsion the analytic frequency,
the C4 outline T1/V1 = 3.14 (F-VAL #44), and the optimised undercut must hit 1:4:10 (1:4 in the top octave)."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import bar_fe as fe  # noqa: E402


def test_slender_free_free_ratios():
    f, _ = fe.Bar(1.0, 0.02, 0.002).bending(3)          # t/L = 0.002: Timoshenko = Euler-Bernoulli
    assert abs(f[1] / f[0] / 2.7565 - 1) < 1e-3, f[1] / f[0]
    assert abs(f[2] / f[0] / 5.4039 - 1) < 1e-3, f[2] / f[0]


def test_slender_absolute():
    L, w, t = 1.0, 0.02, 0.002
    f, _ = fe.Bar(L, w, t).bending(1)
    fa = 4.7300 ** 2 / (2 * math.pi * L * L) * math.sqrt(fe.E_AL * t * t / (12 * fe.RHO_AL))
    assert abs(f[0] / fa - 1) < 1e-3


def test_shear_lowers_thick_bar():
    # a thick bar's upper modes sit below the thin-bar ratios (Timoshenko: shear and rotary inertia)
    f, _ = fe.Bar(0.333, 0.057, 0.013).bending(3)
    assert f[2] / f[0] < 5.40 and f[1] / f[0] < 2.756


def test_uniform_torsion_analytic():
    L, w, t = 0.333, 0.057, 0.013
    f, _ = fe.Bar(L, w, t).torsion(1)
    J = fe.rect_J(w, t)
    Ip = fe.RHO_AL * w * t * (w * w + t * t) / 12
    fa = math.sqrt(fe.G_AL * J / Ip) / (2 * L)
    assert abs(f[0] / fa - 1) < 2e-3


def test_c4_outline_t1_ratio():
    bar = fe.Bar(0.333, 0.057, 0.013)                  # the Beaton C4 outline (F-VAL #44: 3.14)
    fb, _ = bar.bending(1)
    ft, _ = bar.torsion(1)
    assert abs(ft[0] / fb[0] / 3.14 - 1) < 0.03, ft[0] / fb[0]


def test_tuned_c4_hits_1_4_10():
    L, w = fe.outline(60)
    p = fe.tune(fe.midi_hz(60), L, w, 0.0132, h_min=1.5e-3)
    assert max(abs(c) for c in p["err_c"]) < 1.0, p["err_c"]
    bar = p["bar"]
    f, sh = bar.bending(3)
    assert abs(1200 * math.log2(f[0] / fe.midi_hz(60))) < 1.0
    # V2 is antisymmetric (zero at mid-bar), V1 and V3 symmetric
    amp = max(abs(sh(x, 1)) for x in np.linspace(0, L, 50))
    assert abs(sh(L / 2, 1)) < 1e-6 * amp
    assert abs(sh(0.25 * L, 0) - sh(0.75 * L, 0)) < 1e-6 * amp
    # an undercut: thinner in the middle than at the ends, ends at the stock thickness
    assert bar.H[len(bar.H) // 2] < 0.6 * 0.0132 and abs(bar.H[0] - 0.0132) < 1e-9
    # V1's nodes near 0.22 L
    nd = bar.v1_nodes()
    assert len(nd) == 2 and 0.18 < nd[0] / L < 0.26


def test_top_octave_tunes_1_4():
    L, w = fe.outline(89)
    p = fe.tune(fe.midi_hz(89), L, w, 0.0132, ratios=(4.0,), h_min=1.5e-3)
    assert max(abs(c) for c in p["err_c"]) < 1.0, p["err_c"]


def test_strike_weights():
    bar = fe.Bar(0.333, 0.057, 0.013)
    w0 = fe.strike_weights(bar, 0.35 * 0.333, 0.0)
    w1 = fe.strike_weights(bar, 0.35 * 0.333, 0.01)
    w2 = fe.strike_weights(bar, 0.35 * 0.333, 0.02)
    assert w0[4] == 0 and w0[5] == 0                       # no torsion on the centreline
    assert abs(w2[4] / w1[4] - 2) < 1e-9                   # torsion ~ the off-axis offset
    assert w0[:4] == w1[:4]                                # bending does not depend on y
    wc = fe.strike_weights(bar, 0.5 * 0.333, 0.0)
    assert wc[1] < 1e-6 * max(wc)                          # V2 null at mid-bar


def test_outline_interpolation():
    L, w = fe.outline(53)
    assert abs(L - 0.381) < 1e-9 and abs(w - 0.057) < 1e-9
    L, w = fe.outline(89)
    assert abs(L - 0.175) < 1e-9 and abs(w - 0.038) < 1e-9
    assert fe.outline(30) == fe.outline(53) and fe.outline(100) == fe.outline(89)
