# SPDX-License-Identifier: GPL-3.0-only
"""The reverb's top octave (0.9.5): every TYPE's tank runs at 22.05 kHz, and the framework's 2:1 down and 1:2 up are
11-tap half-bands (fx.c rev_down, rev_types.c RV_UPI; tools/rev_images.py measures them on the firmware's own code).

- the images: a tone at f in the wet has its image at 22.05 kHz - f; for every tone to 6 kHz it is 40 dB and more
  under the tone (the old (last + this) / 2 left it 21 dB down at 4 kHz, 14 at 6 kHz);
- the aliasing: a 15 kHz tone sent in folds to 7.05 kHz 35 dB and more down (the old (1, 2, 1) / 4: 10 dB);
- a bright chord's wet above 11.6 kHz is 53 dB and more under its band (the old: 51 HALL / PLATE);
- the half-bands themselves: unity at DC, the up's images and the down's fold 41 dB down to 6.9 kHz (the integer
  taps, computed)."""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import rev_images as ri  # noqa: E402
import rev_scan as rs  # noqa: E402

pytestmark = pytest.mark.skipif(not rs.has_types(), reason="no reverb TYPEs in this tree")


@pytest.fixture(scope="module")
def exe():
    return rs.build(tag="images")


def taps():
    """RV_HB's odd taps (fx.c): (a1, a2, a3) at +-1, 3, 5 half-samples"""
    src = open(os.path.join(rs.ROOT, "firmware", "src", "fx.c")).read()
    m = re.search(r"#define RV_HB\(c, d\) \((\d+) \* .*? - (\d+) \* .*? \+ (\d+) \* ", src)
    return int(m.group(1)), -int(m.group(2)), int(m.group(3))


def test_half_band_taps():
    a = taps()
    assert sum(a) == 512                                  # the up's odd phase: unity at DC (the down: 1024 + 2 x 512)
    w = np.linspace(0, 2 * np.pi * 6900 / rs.FS, 2000)
    s = sum(2 * c / 1024 * np.cos((2 * k + 1) * w) for k, c in enumerate(a))
    assert 20 * np.log10(np.max(np.abs(1 - s) / np.abs(1 + s))) < -41.0


@pytest.mark.parametrize("typ", ri.TYPES)
def test_tone_images(exe, typ):
    got = ri.sweep_images(exe, typ, freqs=(1000, 2000, 3000, 4000, 5000, 6000))
    print(typ, {k: round(v, 1) for k, v in got.items()})
    assert all(v < -40.0 for v in got.values()), got


@pytest.mark.parametrize("typ", ri.TYPES)
def test_input_alias(exe, typ):
    a = ri.tone_ratio(exe, typ, 15000, rs.FS / 2 - 15000)
    print(typ, round(a, 1))
    assert a < -35.0


@pytest.mark.parametrize("typ", ri.TYPES)
def test_chord_top_octave(exe, typ):
    wet = rs.render(exe, rs.chord(int(2.5 * rs.FS)), dict(ri.PARAMS, RV_TYPE=rs.TYPES[typ]))["wet"]
    d = ri.image_db(wet)
    print(typ, round(d, 1))
    assert d < -53.0
