# SPDX-License-Identifier: GPL-3.0-only
"""Switching an effect, a TYPE or a preset under a held chord does not click (the bug hunt on 0.9.5, 2026-10-25).

A differential check: a pedalled chord C2 C3 E3 G3 C4 at v110, one change at 1500 ms; the same run without the change
is the reference. The largest second difference |x[n+1] - 2 x[n] + x[n-1]| from -3 to +40 ms around the change must
stay under 10x the reference's in the same span (that is 5-11 LSB there; the clicks were 150-1700). The shared click
detector (6 x the local level + 2000) found none of them."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import hostrun as H  # noqa: E402

TE = 1500
CLEAN = [H.cc(20, 81, 0), H.cc(21, 91, 0), H.cc(21, 70, 0), H.cc(22, 106, 0), H.cc(22, 107, 0), H.cc(22, 113, 0)]


def chord(t=300, notes=(36, 48, 52, 55, 60), v=110, dur=3000):
    ls = [H.cc(t - 5, 64, 127)]
    for n in notes:
        ls += H.note(t, n, v, dur)
    return ls


def cc(n, v, t=TE):
    return [H.cc(t, n, v)]


def d2(x):
    return np.abs(np.diff(np.asarray(x, float), 2))


def ratio(tag, setup, change, win=(-3, 40), end=2600, pre=()):
    """the largest second difference around the change over the reference's (both channels)"""
    a = H.run(tag + "-a", list(pre) + CLEAN + setup + chord() + change, end)
    b = H.run(tag + "-b", list(pre) + CLEAN + setup + chord(), end)
    i0, i1 = int((TE + win[0]) * 44.1), int((TE + win[1]) * 44.1)
    return max(d2(xa[i0:i1]).max() / max(d2(xb[i0:i1]).max(), 1.0) for xa, xb in ((a.L, b.L), (a.R, b.R)))


CASES = {
    # #2 the phaser and the chorus switched on and off (were 41-307x)
    "phaser-on": (cc(13, 80, 30) + cc(16, 50, 31), cc(12, 127)),
    "phaser-off": (cc(13, 80, 30) + cc(16, 50, 31) + cc(12, 127, 32), cc(12, 0)),
    "chorus-on": (cc(86, 64, 30), cc(85, 64)),
    "chorus-off": (cc(86, 64, 30) + cc(85, 64, 31), cc(85, 0)),
    # #4 the FILTER TYPE and OFF (were 70-260x; LP12 -> HP12 -11 dB in one sample)
    "filter-lp12-hp12": (cc(70, 20, 30) + cc(74, 50, 31), cc(70, 60)),
    "filter-off-lp12": (cc(70, 0, 30) + cc(74, 50, 31), cc(70, 20)),
    "filter-lp12-off": (cc(70, 20, 30) + cc(74, 50, 31), cc(70, 0)),
    # #5 DRIVE AMOUNT to 0 and back (were 23-51x)
    "drive-fdrv-1-0": (cc(84, 127) + cc(81, 1, 30) + cc(83, 127, 31), cc(81, 0)),
    "drive-fdrv-20-0": (cc(84, 127) + cc(81, 20, 30), cc(81, 0)),
    "drive-khlon-20-0": (cc(84, 69) + cc(81, 20, 30), cc(81, 0)),
    "drive-tsh8-0-30": (cc(84, 81) + cc(81, 0, 30), cc(81, 30)),
    # #6 the compressor on / off (a gain step: 8-14x, +2.8 dB in 0.36 ms)
    "comp-on": (cc(107, 64, 30) + cc(113, 15, 31), cc(106, 127)),
    "comp-off": (cc(107, 64, 30) + cc(113, 15, 31) + cc(106, 127, 32), cc(106, 0)),
    "comp-thr1-on": (cc(107, 1, 30) + cc(113, 15, 31), cc(106, 127)),
    # #7 the WAH on / off (68x on)
    "wah-on": (cc(18, 90, 30), cc(17, 127)),
    "wah-off": (cc(18, 90, 30) + cc(17, 127, 31), cc(17, 0)),
    # #7's other half: TREM (AM, deep) <-> WAH: the tremolo's dip stepped out in one sample (16.5x; 0.9.5 90x)
    "trem-wah": (cc(18, 127, 30) + cc(21, 127, 31) + cc(17, 64, 32), cc(17, 127)),
    "wah-trem": (cc(18, 127, 30) + cc(21, 127, 31) + cc(17, 127, 32), cc(17, 64)),
    "pan-wah": (cc(18, 127, 30) + cc(21, 0, 31) + cc(17, 64, 32), cc(17, 127)),
    # #17 DELAY EFFECT DIG <-> ANA under ringing echoes: the tone, the softclip and the wow in one sample (14x)
    "delay-ana-dig": (cc(89, 100, 30) + cc(26, 90, 31) + cc(90, 30, 32) + cc(88, 80, 33), cc(88, 48)),
    "delay-dig-ana": (cc(89, 100, 30) + cc(26, 90, 31) + cc(90, 30, 32) + cc(88, 48, 33), cc(88, 80)),
    # #23 TAPE TONE 0 <-> 127 under ringing echoes: the loop's HF corner stepped at once (31x)
    "tape-tone-0-127": (cc(95, 0, 30) + cc(89, 90, 31) + cc(26, 60, 32) + cc(27, 0, 33) + cc(88, 127, 34), cc(27, 127)),
    "tape-tone-127-0": (cc(95, 0, 30) + cc(89, 90, 31) + cc(26, 60, 32) + cc(27, 127, 33) + cc(88, 127, 34), cc(27, 0)),
    # #25 FX (the bypass, CC 111) with SPREAD on: the crossfade went to the plain mono, then SPREAD's side stepped in
    "fx-bypass-spread": (cc(105, 107, 30), cc(111, 127)),
    "fx-back-spread": (cc(105, 107, 30) + cc(111, 127, 31), cc(111, 0)),
    # #9 LEVEL in one step (48x)
    "level-64-0": ([], cc(110, 0)),
    # #9's rest: LEVEL with FX off (bypassed) stepped in one sample (340x in the monkey's stream)
    "level-bypassed": (cc(111, 127, 30), cc(110, 127)),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_switch_does_not_click(case):
    setup, change = CASES[case]
    r = ratio("sw-" + case, setup, change)
    assert r < 10, f"{case}: the largest second difference {r:.1f}x the run without the change"


def test_comp_comes_on_at_the_same_volume():
    """CC 106 (and THRESH up from OFF on the panel) switches the compressor on at -16 dB with the engine's make-up:
    the chord's first 300 ms within 0.75 LU of the compressor off (0.9.5: CC 106 without the make-up; the panel at
    THRESH 1, -0.3 dB, with +2.8 dB). A THRESH set over MIDI first is kept, without a make-up"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
    import bar_measure as bm
    off = H.run("cw-off", CLEAN + chord(), 1000)
    for tag, ch in (("cc106", cc(106, 127, 30)), ("thr1", cc(107, 1, 29) + cc(106, 127, 30))):
        on = H.run("cw-" + tag, CLEAN + ch + chord() + ["900 dump"], 1000)
        i0, i1 = int(300 * 44.1), int(600 * 44.1)
        d = bm.lufs(np.stack([on.L, on.R], 1)[i0:i1]) - bm.lufs(np.stack([off.L, off.R], 1)[i0:i1])
        assert abs(d) < 0.75, f"{tag}: {d:+.2f} LU re the compressor off"
