# SPDX-License-Identifier: GPL-3.0-only
"""tools/pickup.py: closed-form flux against R03's eq. 25, evenness, table range, and the measured waveform
signature (research/01: two spikes per tine cycle, unequal when the tine sits off the coil axis; pure overtone
on axis). Check C5 of IMPLEMENTATION §4, float version."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import pickup as p  # noqa: E402

FS = 44100


def r03_bracket(u, Lh=1.0, Rp=1.0):
    f1 = (u - Rp) ** 2 + Lh**2
    f2 = (u + Rp) ** 2 + Lh**2
    return (f1 - 2 * Lh**2) / f1**2 - (f2 - 2 * Lh**2) / f2**2


def test_g_matches_r03_eq25():
    u = np.linspace(-3, 3, 601)
    assert np.allclose(p.g(u), r03_bracket(u), atol=1e-12)


def test_phi_derivative_is_g():
    u = np.linspace(-3, 3, 6001)
    num = np.gradient(p.phi(u), u)
    assert np.max(np.abs(num[5:-5] - p.g(u)[5:-5])) < 1e-4


def test_phi_even_and_vanishes_far_away():
    u = np.linspace(0, 5, 51)
    assert np.allclose(p.phi(u), p.phi(-u))
    d = p.dphi_ds(np.array([0.0, p.S_MAX]))
    assert abs(d[1]) < 3e-3 * np.max(np.abs(p.dphi_ds(np.linspace(0, p.S_MAX, 4000))))


def _peaks(y, period):
    """The two largest positive local maxima within one period (sorted descending)."""
    seg = y[2 * period:3 * period]
    loc = [i for i in range(1, len(seg) - 1) if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0]
    vals = sorted((seg[i] for i in loc), reverse=True)
    return vals


def test_two_unequal_spikes_off_axis():
    f = 441.0
    t = np.arange(FS) / FS
    x = 1.5 * np.sin(2 * np.pi * f * t)
    y = p.pickup_out(x, off=-0.2)
    period = int(FS / f)
    pk = _peaks(y, period)
    big = [v for v in pk if v > 0.2 * max(y)]
    assert len(big) == 2, pk
    assert 0.3 <= big[1] / big[0] <= 0.95, big
    assert abs(max(y) / -min(y) - 1) < 0.02        # antisymmetric about each extremum


def test_pure_overtone_on_axis():
    f = 441.0
    n = FS
    t = np.arange(n) / FS
    y = p.pickup_out(1.5 * np.sin(2 * np.pi * f * t), off=0.0)
    Y = np.abs(np.fft.rfft(y * np.hanning(n)))
    k1, k2 = int(round(f)), int(round(2 * f))
    assert 20 * np.log10(Y[k1] / Y[k2] + 1e-30) < -40


def test_radial_reduces_to_1d():
    x = np.linspace(-1, 1, 100)
    assert np.allclose(p.pickup_out(x, -0.3), p.pickup_out(x, -0.3, xh=np.zeros(100)))
