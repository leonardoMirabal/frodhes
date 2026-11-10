# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE TYPEs on a quiet bass note (0.9.10): a 41.2 Hz sine (key 28) at small inputs through TSH8 and OHCD
(DRIVE 20, F05's), on the firmware's own code (tests/c/drive_scan.c through tools/drive_scan.py).

A state or an output kept in whole units was a staircase there, its error a set of odd harmonics: TSH8's clipper output
V in Q15 into the tone (now Q15 + TSH8_VSH), OHCD's stage 1 in whole u (now 2^OHCD_S1F finer inside, u rounded out).
Before: TSH8 H3 -44 / -50 / -59 dB re H1 at inputs 160 / 320 / 640, OHCD -38 / -54 / -57 at 40 / 160 / 320; the
limits sit 4..10 dB under those and 3..10 dB over the fixed kernels' measure."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import drive_scan as ds  # noqa: E402

FS, F = 44100, 41.2


@pytest.fixture(scope="module")
def exe():
    return ds.build(tag="quiet")


def h3_h5(exe, typ, amp):
    t = np.arange(2 * FS) / FS
    y, _, _ = ds.render(exe, np.round(amp * np.sin(2 * np.pi * F * t)).astype(np.int64), ds.params_for(typ, 20))
    y = y[FS:].astype(float)
    p = np.abs(np.fft.rfft(y * np.blackman(len(y)))) ** 2
    fr = np.fft.rfftfreq(len(y), 1 / FS)
    pk = [p[np.abs(fr - h * F) < 1.5].max() for h in (1, 3, 5)]
    return 10 * np.log10(pk[1] / pk[0]), 10 * np.log10(pk[2] / pk[0])


@pytest.mark.parametrize("typ,amp,lim", [("TSH8", 160, -60.0), ("TSH8", 320, -70.0), ("TSH8", 640, -80.0),
                                         ("OHCD", 40, -48.0), ("OHCD", 160, -63.0), ("OHCD", 320, -70.0)])
def test_quiet_bass_odd_harmonics(exe, typ, amp, lim):
    h3, h5 = h3_h5(exe, typ, amp)
    assert h3 <= lim and h5 <= lim, f"{typ} input {amp}: H3 {h3:.1f} H5 {h5:.1f} dB re H1 (limit {lim})"
