# SPDX-License-Identifier: GPL-3.0-only
"""FR_C1 (two cores): the bar engine (VIBES, MARIMBA) rendered in chunks equals the one-core 14-voice build sample for
sample. bars.c bar_begin / bar_chunk / bar_end; BAR_C1_RSUB cuts bar_render's block into 1, 4 or 8 chunks (256, 64,
32 frames). Takes: chords under the pedal with the motor, key-ups without it (the felt noise and knock on VIBES, the
hand mute on MARIMBA), more notes than voices (steals, waiting notes), loud contact noise, all-notes-off, every event
time spread over the block's phase."""
import os
import numpy as np
import pytest
from hostrun import build, cc, note, run, ROOT

B = os.path.join(ROOT, "build", "c1bars")
REF = ["-DFR_VOICES=14u", "-DFR_REV_HQ=1", "-DFX_COST_C0()=40u"]


def c1b(sub):
    return build(f"{B}/c1_{sub}/frodhes", ["-DFR_C1=1", "-DC1_SUB=8u", f"-DBAR_C1_RSUB={sub}u"])


def refb():
    return build(f"{B}/ref/frodhes", REF)


def take(engine, seed):
    rng = np.random.default_rng(seed)
    ls = [cc(20, 79, 0 if engine == 1 else 127), cc(25, 104, 127), cc(26, 103, 64)]
    if engine == 1:
        ls += [cc(27, 47, 127), cc(28, 48, 100)]
    ls.append(cc(300, 64, 127))                      # pedal: a chord, then more notes than voices
    for i, k in enumerate((36, 40, 43, 47, 50, 53, 57, 60, 64, 67, 71, 74, 77, 81)):
        ls += note(400 + 3 * i, k, 60 + 4 * (i % 16), 900)
    for i in range(8):
        ls += note(1200 + 37 * i, 84 - 3 * i, 100 + i, 200)
    ls.append(cc(1700, 64, 0))
    t = 1800                                         # no pedal: fast key-ups (felt), steals, re-strikes
    for i in range(60):
        k = int(rng.integers(48, 96))
        ls += note(t, k, int(rng.integers(20, 128)), int(rng.integers(3, 120)))
        t += int(rng.integers(1, 23))
    ls.append(cc(t + 50, 64, 100))
    for i in range(20):
        ls += note(t + 60 + 7 * i, 50 + 2 * i, 127, 400)
    ls.append(cc(t + 300, 64, 0))
    ls.append(cc(t + 420, 123, 0))
    if seed == 2:                                    # the other bar type while notes ring (the fade, then the change)
        ls.append(cc(t + 423, 79, 127 if engine == 1 else 0))
    for i in range(16):
        ls += note(t + 425 + 2 * i, 60 + i, 90, 60)
    return ls, t + 2500


def ren(tag, ls, b, end):
    r = run(tag, ls, end_ms=end, binary=b)
    return np.stack([r.L, r.R])


@pytest.mark.parametrize("engine", [1, 2])
@pytest.mark.parametrize("seed", [1, 2])
def test_c1_bars_exact(engine, seed):
    ls, end = take(engine, seed)
    r = ren(f"c1b_ref_{engine}_{seed}", ls, refb(), end)
    assert np.sqrt(np.mean(r.astype(float) ** 2)) > 100
    for sub in (1, 4, 8):
        a = ren(f"c1b_{sub}_{engine}_{seed}", ls, c1b(sub), end)
        assert a.shape == r.shape
        d = np.flatnonzero(np.any(a != r, axis=0))
        assert d.size == 0, f"chunks {sub}: differs from frame {d[0]} ({d.size} frames)"
