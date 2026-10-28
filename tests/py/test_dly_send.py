# SPDX-License-Identifier: GPL-3.0-only
"""The delay send's stereo-power make-up is steady from block to block (0.9.8). fx.c fx_delay_t scales the mono send
(L + R) / 2 by sqrt(mean(L^2 + R^2) / 2 / mean(sum^2)) when the chorus, the phaser or the pan make L and R differ,
computed per block in log2 (Q12) and glided. From 2104019c (0.9.3) the log2 was the whole-number one (ilog2_q12), so
the target tg could only be 0 or 2048 (+3 dB) and flipped between them from block to block under a steady chord: the
echoes' level jittered by up to ~2 dB every 5.8 ms. With the fractional log2 (log2_q12) the target follows the true
ratio. The host build with -DFX_DLY_SG_LOG prints the target and the glided gain once per block."""
import os
import re

import numpy as np
import pytest

from hostrun import HOST, build, cc, run

BIN = os.path.join(HOST, "frodhes-dlysg")

CASES = {
    "chorus": [cc(50, 85, 64), cc(55, 86, 64)],
    "phaser": [cc(50, 12, 127), cc(55, 13, 90)],
    "pan": [cc(50, 17, 64), cc(55, 18, 100), cc(60, 21, 0)],
}


def sg_log(tag, fx):
    build(BIN, extra=("-DFX_DLY_SG_LOG",))
    ev = fx + [cc(70, 88, 48), cc(75, 89, 80), cc(80, 90, 40), cc(90, 64, 127)]
    ev += [f"300 midi 0x90 {n} 100" for n in (48, 55, 60, 64, 67)]
    r = run(f"dlysg-{tag}", ev, 4300, binary=BIN)
    v = np.array([[int(a), int(b)] for a, b in re.findall(r"^dly_sg (-?\d+) (-?\d+)$", r.out, re.M)])
    assert len(v) > 600, len(v)                                      # (one line per block: ~5.8 ms)
    return v[200:700]                                                # 1.2 .. 4.1 s: the held chord, its echoes


@pytest.mark.parametrize("tag", list(CASES))
def test_send_gain_is_steady(tag):
    """under a held chord the send's target takes many values, not two (it switched 0 <-> 2048, +3 dB, before), and
    the glided gain's block-to-block steps stay small: their standard deviation under 150 Q12 (0.22 dB; 0.9.7: 220 -
    250, the 0 / +3 dB switch; with the fix 75 - 110, what is left is the per-block estimate's own spread)"""
    v = sg_log(tag, CASES[tag])
    tg, sg = v[:, 0], v[:, 1]
    assert tg.max() > 0, "the effect leaves L == R: nothing to test"
    assert len(set(tg.tolist())) > 16, sorted(set(tg.tolist()))[:10]
    jit = float(np.std(np.diff(sg)))
    assert jit < 150, jit


def test_mono_send_is_exactly_one():
    """with L == R (no chorus, phaser or pan) the make-up stays exactly 0 (x1: the plain send, sample for sample)"""
    v = sg_log("mono", [])
    assert not v.any(), v[v.any(axis=1)][:5]
