# SPDX-License-Identifier: GPL-3.0-only
"""FDRV at 2x (firmware/src/drive.c dr_os2; 0.9.9) and KHLON's anti-aliasing on (drv_khlon.c KH_AA), on the
firmware's own code (tests/c/drive_scan.c through tools/drive_scan.py):

- the half-band's constants are tools/drive_gen/halfband.py's designs (4 coefficients, the default: flat to
  18.96 kHz, -47.7 dB from 25.14 kHz; 6: -71.5 dB);
- the folded energy (the stepped sweep C5 .. C8, AMOUNT 127, TONE 64), worst note: at -12 dBFS -30.6 dB at 1x, now
  -58.5 (6 coefficients: -64.1); at -6 dBFS -21.1, now -38.2; at -2 dBFS -18.0, now -29.5; KHLON at -12 dBFS -11.9 plain, now -31.8 (TONE
  127: -18.3, now -33.2);
- silence in is silence out at every row and AMOUNT (the 2x state rests at the curve's bias: from 0 it made a
  ~250 LSB click at each bring-up, and again after each silence reset);
- a tail ends and stays 0 (no limit cycle in the half-bands);
- KHLON crossing AMOUNT 77 (its plain clipper to the anti-aliased one) under a chord: no click; FDRV's AMOUNT swept
  end to end at one step a block: no click."""
import os
import re
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "drive_gen"))
import drive_scan as ds  # noqa: E402
import halfband  # noqa: E402

FS = 44100


@pytest.fixture(scope="module")
def exe():
    e = ds.build([], "os")
    yield e
    os.remove(e)


def chord(n):
    t = np.arange(n) / FS
    x = 6000 * np.sin(2 * np.pi * 41.2 * t) + sum(4000 * np.sin(2 * np.pi * f * t + i) for i, f in
                                                    enumerate((261.63, 329.63, 392.0)))
    return np.round(x).astype(np.int64)


def jumps(y, a=0, b=None):
    return float(np.max(np.abs(np.diff(y[a:b].astype(float), 2))))


def test_half_band_is_the_design():
    src = open(os.path.join(ROOT, "firmware", "src", "drive.c")).read()
    assert re.search(r"#ifndef DRV_HB_NC\n#define DRV_HB_NC 4\n", src)
    for nc, stop in ((4, -47.5), (6, -71.0)):
        got = [int(re.search(rf"#define DRV_HB{nc}_{i} (\d+)", src).group(1)) for i in range(nc)]
        assert got == halfband.q30(nc), nc
        cf = [v / 2 ** 30 for v in got]
        assert min(halfband.response(cf, f) for f in range(10, 18960, 50)) > 10 ** (-0.0001 / 20), nc
        assert max(halfband.response(cf, f) for f in range(25140, 44100, 25)) < 10 ** (stop / 20), nc


@pytest.mark.parametrize("typ,amp,worst", [("FDRV", 8000, -58.0), ("FDRV", 16000, -37.7), ("FDRV", 26000, -29.0),
                                           ("KHLON", 8000, -31.3)])
def test_folded_energy(exe, typ, amp, worst):
    rows, _, _ = ds.aliasing_tones(exe, ds.params_for(typ, 127, 64), amp=amp)
    assert max(r["folded_db"] for r in rows) <= worst, (typ, amp, max(r["folded_db"] for r in rows))


def test_silence_stays_silent(exe):
    z = np.zeros(256 * 48, np.int64)
    for row in range(4):
        for a in (1, 20, 64, 127):
            y, _, _ = ds.render(exe, z, ds.params_for("FDRV", a, 64, 127, row))
            assert not y.any(), (row, a, np.abs(y).max())


def test_tail_ends_and_stays_zero(exe):
    x = np.concatenate([chord(256 * 20), np.zeros(256 * 600, np.int64)])
    for row in range(4):
        for a in (20, 127):
            y, _, _ = ds.render(exe, x, ds.params_for("FDRV", a, 64, 127, row))
            nz = np.flatnonzero(y)
            assert nz[-1] < 256 * 20 + int(0.05 * FS), (row, a, nz[-1])


def test_no_click_on_amount_moves(exe):
    n = 256 * 80
    x = chord(n)
    y, _, _ = ds.render(exe, x, ds.params_for("KHLON", 60), [(10 + k, "DRIVE", 60 + k) for k in range(41)])
    a, _, _ = ds.render(exe, x, ds.params_for("KHLON", 60))
    b, _, _ = ds.render(exe, x, ds.params_for("KHLON", 100))
    assert jumps(y, 2560) <= 1.05 * max(jumps(a, 2560), jumps(b, 2560))                 # (measured 138 / 140)
    assert jumps(y, 256 * 25, 256 * 29) <= 1.05 * max(jumps(y, 256 * 20, 256 * 24), jumps(y, 256 * 30, 256 * 34))
    n = 256 * 150
    x = chord(n)
    y, _, _ = ds.render(exe, x, ds.params_for("FDRV", 0), [(10 + k, "DRIVE", k) for k in range(128)])
    a, _, _ = ds.render(exe, x, ds.params_for("FDRV", 127))
    assert jumps(y, 2560) <= 1.05 * jumps(a, 2560)                                      # (measured 126 / 140)
