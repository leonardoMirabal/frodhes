# SPDX-License-Identifier: GPL-3.0-only
"""The FILTER is on by default (LP18, TODO #5), and fully open it is transparent: LP12 / LP18 at FREQ max with no
RESO and no LFO pass the sound untouched (the LP18 at 16 kHz trimmed the top: -1.4 dB at 12 kHz, the jack's SNR 1 dB
lower). Turning FREQ down from the top glides into the filter (46 ms): no click."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

CH = [f"300 midi 0x90 {n} 110" for n in (48, 55, 60, 64, 67, 72)] + [f"2300 midi 0x80 {n} 0" for n in (48, 55, 60, 64, 67, 72)]


@pytest.mark.parametrize("sel", [[], ["40 midi 0xB0 79 64"]], ids=["rhodes", "vibes"])
@pytest.mark.parametrize("typ", [None, 1])        # the default (LP18) and LP12, both fully open
def test_open_filter_is_transparent(sel, typ):
    on = run(f"flo-on-{len(sel)}-{typ}", sel + ([f"30 midi 0xB0 70 {typ * 19}"] if typ else []) + CH, 2600)
    off = run(f"flo-off-{len(sel)}-{typ}", sel + ["30 midi 0xB0 70 0"] + CH, 2600)
    assert np.array_equal(on.L, off.L) and np.array_equal(on.R, off.R)


def test_leaving_the_top_does_not_click():
    ev = CH + [f"{t} midi 0xB0 74 {v}" for t, v in ((900, 126), (1000, 110), (1100, 90), (1500, 127))]   # FREQ
    r = run("flo-sweep", ev, 2600)
    x = r.L.astype(float)
    d2 = np.abs(np.diff(x, 2))
    lvl = np.convolve(np.abs(x), np.ones(441) / 441, "same")[1:-1] + 50
    assert int((d2 > 6 * lvl + 2000).sum()) == 0
