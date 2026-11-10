# SPDX-License-Identifier: GPL-3.0-only
"""DELAY at DEPTH 0 after a delay take is exactly the bypass level (2026-11-10). The dry's ease-down glides
dg += (dgt - dg) >> 9: rising back toward 32768 it stopped up to 511 short (x 0.98441, -0.137 dB), so after any
take with DEPTH up, DEPTH 0 played 0.14 dB under the bypass, for good. Each TYPE (DIG, ANA, TAPE): a take at DEPTH 64,
then DEPTH 0 and a chord 8 s later: its samples equal (within 2 LSB) a run whose DEPTH stayed 0 (the bypass
level: the dry x 1, the wet 0; the take's echoes have died by then)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

CHORD = (57, 60, 64)


def take(tag, sw, depth):
    ev = ["50 midi 0xB0 10 127", "60 midi 0xB0 91 0", f"100 midi 0xB0 88 {sw}", f"110 midi 0xB0 89 {depth}"]
    ev += [f"200 midi 0x90 {n} 100" for n in CHORD] + [f"700 midi 0x80 {n} 0" for n in CHORD]
    ev += ["900 midi 0xB0 89 0"]                                   # DEPTH 0, the delay still on
    ev += [f"9000 midi 0x90 {n} 100" for n in CHORD] + [f"10000 midi 0x80 {n} 0" for n in CHORD]
    r = run(f"dly0-{tag}-{depth}", ev, 10200)
    x = (r.L.astype(float) + r.R.astype(float)) / 2
    return x[int(9.0 * FS):int(9.9 * FS)]


@pytest.mark.parametrize("sw", (40, 80, 120), ids=["DIG", "ANA", "TAPE"])   # (CC 88 in 4 bands)
def test_depth0_is_bypass(sw):
    a, b = take(f"sw{sw}", sw, 64), take(f"sw{sw}", sw, 0)      # a take at DEPTH 64 first / DEPTH 0 all along
    g = np.dot(a, b) / np.dot(b, b)
    assert abs(g - 1) < 2e-4, f"DEPTH 0 at x{g:.5f} ({20 * np.log10(g):+.3f} dB) of DEPTH 0 all along"
    assert np.max(np.abs(a - b)) <= 2, f"DEPTH 0 differs from DEPTH 0 all along by {np.max(np.abs(a - b)):.0f} LSB"
