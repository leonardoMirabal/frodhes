# SPDX-License-Identifier: GPL-3.0-only
"""Developer CC 10: every voice ended, every free-running part (effect LFOs, the tape's wow, the reverb's modulation, the delay / chorus /
pre-delay / reverb lines, the phaser, the engines' noise, the motor, the smoothers, the gate) back to its boot state,
so before / after recordings compare sample for sample (the user, 2026-10-18). Two takes of the same chord, each after
CC 10, a whole number of audio blocks apart (15360 ms = 2646 blocks), come out identical; without it they do not."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import BIN, FS, build, cc, note, run  # noqa: E402

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


@pytest.mark.parametrize("sel", [[], ["40 midi 0xB0 80 64"], ["40 midi 0xB0 80 110"], ["40 midi 0xB0 79 0"]],
                         ids=["ST77", "FELT", "DYNO", "VIBES"])
def test_reset_makes_takes_identical(sel):
    a, b = takes(sel, True)
    assert np.array_equal(a, b), (np.count_nonzero(a - b), np.abs(a - b).max())
    a, b = takes(sel, False)
    assert not np.array_equal(a, b)          # (the test is not vacuous: without CC 10 the LFOs have moved on)


def _chord(t, d=600):
    return sum((note(t, n, 110, d) for n in (48, 55, 60, 64, 67)), [])


_BASE = [cc(5, 12, 127), cc(6, 13, 90), cc(7, 85, 127), cc(8, 86, 90), cc(9, 91, 90), cc(10, 17, 127), cc(11, 18, 80)]
_PLAY = _chord(100) + _chord(1500)                      # the lines full of sound when CC 10 comes (at 3000)


def _dl(sw, tm):
    return [cc(12, 88, sw), cc(13, 89, 110), cc(14, 90, tm), cc(15, 26, 110)]


CHUNKED = {
    "digital-long": _dl(127, 127) + [cc(3000, 10, 127)] + _chord(3020) + _chord(4600),
    "tape-short-same-ms": _dl(64, 60) + [cc(3000, 10, 127)] + _chord(3000),
    "twice": _dl(127, 60) + [cc(3000, 10, 127), cc(3040, 10, 127)] + _chord(3060),
    "off-on": _dl(64, 127) + [cc(3000, 10, 127), cc(3005, 88, 0), cc(3090, 88, 64)] + _chord(3100),
    "predelay": _dl(127, 60) + [cc(16, 94, 100), cc(3000, 10, 127), cc(3600, 94, 0), cc(3900, 94, 127)] + _chord(3020),
    "vibes": ["1 midi 0xB0 80 64"] + _dl(64, 90) + [cc(3000, 10, 127)] + _chord(3020),
}


@pytest.mark.parametrize("name", list(CHUNKED))
def test_chunked_reset_same_as_one_block(name):
    """the reset clears the delay line over ~16 blocks (its reads behind the cleared span return 0) and the pre-delay
    line at its next use, instead of ~150 KB of memset in one audio block (~5 ms on the FM-1): the same render, sample
    for sample, as the one-block clear (FX_RESET_ONE_BLOCK). (Non-vacuous: with the masking off these differ in
    ~220000 samples, checked by hand 2026-10-18.)"""
    ref = os.path.join(os.path.dirname(BIN), "frodhes-reset1")
    build(ref, ("-O2", "-DFX_RESET_ONE_BLOCK"))
    ev = _BASE + _PLAY + CHUNKED[name]
    a, b = run("rstc-" + name, ev, 8000), run("rstc1-" + name, ev, 8000, binary=ref)
    assert len(a.L) == len(b.L) > 7 * FS
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R), (np.count_nonzero(a.L - b.L), np.count_nonzero(a.R - b.R))


@pytest.mark.parametrize("sel", [[], ["40 midi 0xB0 80 64"]], ids=["RHODES", "VIBES"])
def test_reset_ends_every_voice(sel):
    """CC 10 ends every voice and zeroes the pickup / body states (the tails under the gate met the next take on the
    FM-1, up to 5 LU): a held chord with every effect on is exact silence from a block after the reset"""
    ev = list(sel) + FX + [f"500 midi 0x90 {n} 100" for n in (48, 55, 60, 64)] + ["1500 midi 0xB0 10 127"]
    r = run(f"rst-kill-{len(sel)}", ev, 2500)
    i = int(1500 * FS / 1000)
    assert np.abs(r.L[i - 4410:i]).max() > 100                   # (it sounded up to the reset)
    assert not np.any(r.L[i + 1024:]) and not np.any(r.R[i + 1024:])
