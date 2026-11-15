# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py marimba_decay's pedal row: m_stop_slope (the slope of a stop's first 20 dB, from where it starts)
and pedal_order_ok (down < half < up, up / down >= 3x, USB and the jack judged alike). Synthetic MARIMBA-like notes
(C3's partials, held at 46 dB/s, the stop at a given rate after a key-up delay that moves on the damper's 32-frame
grid), clean as USB and as the jack (x -12.65 dB, white hiss 40 and 25 dB under the note before its key-up; the FM-1's
jack takes stand 40-51 dB over their hiss). The rates the FM-1 plays (USB and jack takes of 2026-10-19, 10-20, 11-14):
up ~1000, half ~770, down 45 dB/s on C3; m_drop_rate read up on the jack 815-846 against USB's 923-952, and on the
0.9.12 two-core T2 702 / 702 for up / half (USB 845 / 759), a false inversion; m_stop_slope reads those takes' jack
within 4 % of their USB."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

FS = h.FS
T_OFF = 0.4
RATES = (1000.0, 770.0, 45.0)                   # dB/s: pedal up, half, down (MUTE 127)
SNR = {"usb": None, "jack40": 40.0, "jack25": 25.0}


def note(rate, delay, snr=None, seed=0, f0=130.81, dur=2.3):
    """a held C3 at -46 dB/s whose stop (rate dB/s) starts delay s after the key-up T_OFF; snr: the jack (x -12.65 dB,
    white hiss snr dB under the note's level before the key-up)"""
    t = np.arange(int(dur * FS)) / FS
    e = -46.0 * t
    k = t > T_OFF + delay
    e[k] = -46.0 * (T_OFF + delay) - rate * (t[k] - T_OFF - delay)
    x = 10 ** (e / 20) * (0.25 * np.sin(2 * np.pi * f0 * t) + 0.05 * np.sin(2 * np.pi * 3.93 * f0 * t + 1)
                          + 0.02 * np.sin(2 * np.pi * 9.9 * f0 * t + 2))
    if snr is None:
        return x, np.zeros(FS // 4)
    x = x * 10 ** (-12.65 / 20)
    i0 = int(T_OFF * FS)
    sd = h.rdb(x[i0 - FS // 50:i0]) - snr
    rng = np.random.default_rng(seed)
    return x + rng.normal(0, 10 ** (sd / 20), len(x)), rng.normal(0, 10 ** (sd / 20), FS // 4)


def read(rates, src, seed=0):
    out = []
    for i, r in enumerate(rates):
        x, fl = note(r, 0.0015 + (32 * ((seed + i) % 3)) / FS, SNR[src], seed=10 * seed + i)
        out.append(h.m_stop_slope(x, T_OFF, fl))
    return out


@pytest.mark.parametrize("src", sorted(SNR))
def test_the_stop_slope_reads_the_rate_on_every_source(src):
    for seed in range(3):
        v = read(RATES, src, seed)
        assert h.pedal_order_ok(v), (src, seed, v)
        tol = 0.05 if src != "jack25" else 0.10
        for got, want in zip(v, RATES):
            assert abs(got / want - 1) <= tol, (src, seed, v)


@pytest.mark.parametrize("src", sorted(SNR))
def test_an_inversion_fails_on_every_source(src):
    up, half, down = RATES
    for seed in range(3):
        assert not h.pedal_order_ok(read((half, up, down), src, seed)), src     # half faster than up
        assert not h.pedal_order_ok(read((up, down, half), src, seed)), src     # down faster than half
        assert not h.pedal_order_ok(read((120.0, 100.0, down), src, seed)), src  # up / down under 3x


def test_the_key_up_grid_moves_the_start_not_the_slope():
    """the same stop, its start 0 .. 2 x 32 frames after the key-up: the slope within 2 % (m_drop_rate, the time from the
    key-up, read 779-923 dB/s on USB for one setting)"""
    v = [h.m_stop_slope(note(RATES[0], 0.0015 + 32 * j / FS)[0], T_OFF) for j in range(3)]
    assert max(v) / min(v) <= 1.02, v


def test_pedal_order_ok():
    assert h.pedal_order_ok([1000, 770, 45])
    assert not h.pedal_order_ok([770, 770, 45])                # a tie
    assert not h.pedal_order_ok([770, 1000, 45])               # half over up
    assert not h.pedal_order_ok([1000, 40, 45])                # down over half
    assert not h.pedal_order_ok([130, 100, 45])                # up / down under 3x
