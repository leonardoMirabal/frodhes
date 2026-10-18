# SPDX-License-Identifier: GPL-3.0-only
"""Developer CC 10: every free-running part (effect LFOs, the tape's wow, the reverb's modulation, the delay / chorus /
pre-delay / reverb lines, the phaser, the engines' noise, the motor, the smoothers, the gate) back to its boot state,
so before / after recordings compare sample for sample (the user, 2026-10-18). Two takes of the same chord, each after
CC 10, a whole number of audio blocks apart (15360 ms = 2646 blocks), come out identical; without it they do not."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

FX = ["50 midi 0xB0 12 127", "51 midi 0xB0 13 80", "52 midi 0xB0 85 64", "53 midi 0xB0 86 64", "54 midi 0xB0 88 64",
      "55 midi 0xB0 89 80", "56 midi 0xB0 91 60", "57 midi 0xB0 17 64", "58 midi 0xB0 18 64"]


def takes(sel, reset):
    ev = list(sel) + FX
    for t0 in (1000, 1000 + 15360):
        ev += ([f"{t0} midi 0xB0 10 127"] if reset else []) + [f"{t0 + 20} midi 0x90 {n} 100" for n in (48, 55, 60, 64)]
        ev += [f"{t0 + 2020} midi 0x80 {n} 0" for n in (48, 55, 60, 64)]
    r = run(f"rst-{len(sel)}-{int(reset)}", ev, 31000)
    seg = lambda t0: np.stack([r.L[int((t0 + 20) * FS / 1000):][:int(8 * FS)], r.R[int((t0 + 20) * FS / 1000):][:int(8 * FS)]])
    return seg(1000), seg(1000 + 15360)


@pytest.mark.parametrize("sel", [[], ["40 midi 0xB0 80 64"], ["40 midi 0xB0 80 110"], ["40 midi 0xB0 79 64"]],
                         ids=["ST77", "FELT", "DYNO", "VIBES"])
def test_reset_makes_takes_identical(sel):
    a, b = takes(sel, True)
    assert np.array_equal(a, b), (np.count_nonzero(a - b), np.abs(a - b).max())
    a, b = takes(sel, False)
    assert not np.array_equal(a, b)          # (the test is not vacuous: without CC 10 the LFOs have moved on)
