# SPDX-License-Identifier: GPL-3.0-only
"""A held note's last seconds through the FDRV drive are smooth (the Move bug report, 2026-10-28: F05 SUIT TREM, one
note with the pedal down, "the last part of the tail is super glitchy").

FDRV's shaper took its input as (x >> 2) * g >> 10: the 2 bits dropped before the gain put the quiet tail on a grid
of 4 input LSB, ~3 LSB at the output (AMOUNT 20). Under ~40 LSB (the tail's last ~2 s before the silence gate) the
note became a staircase: held 3-6 samples, then a jump of 3-4 LSB, a 3-4 bit tone (a buzz) that the FM-1's jack hiss
hides and the Move's clean output does not. The fix multiplies first ((int64) x * g >> 12): the same scale, the
rounding of one step only.

The check: C4 v100, the pedal down, FDRV alone (F05 with its compressor, tremolo and reverb off), on the FM-1 host
build and on the Move's (16 voices, HQ on). In the frames whose peak is 8..40 LSB, the share of sample steps of 3 LSB
or more (a 261 Hz tone of 40 LSB moves at most 1.5 LSB a sample) and the residual above 2 kHz (the staircase's
buzz) stay at the level of the dry signal (no drive). Before the fix: 14.4 % / 0.89 LSB at AMOUNT 20."""
import os
import pathlib
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostrun  # noqa: E402

sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
import gate_h  # noqa: E402

import pytest  # noqa: E402

FS = hostrun.FS
MOVE = os.path.join(hostrun.ROOT, "build", "drive_tail", "move-hq")


def binary(target):
    if target == "fm1":
        return hostrun.build()
    return hostrun.once(MOVE, lambda p: gate_h.build(True, pathlib.Path(p)))


def take(target, amount, drive=True):
    """F05, its compressor (106), tremolo (17) and reverb (91) off, FDRV at amount (CC 84 127) or no drive (CC 81 0),
    LEVEL (110) 46"""
    ls = ["0 midi 0xC0 4 0", hostrun.cc(100, 106, 0), hostrun.cc(105, 17, 0), hostrun.cc(110, 91, 0),
          hostrun.cc(115, 84, 127), hostrun.cc(120, 81, amount if drive else 0), hostrun.cc(125, 110, 46),
          hostrun.cc(400, 64, 127),   # (2026-11-08: LEVEL 46, F05's before the headroom trims: the tail judged as before)
          *hostrun.note(500, 60, 100, 500)]
    r = hostrun.run(f"drive_tail_{target}_{amount if drive else 'dry'}", ls, end_ms=20000, binary=binary(target))   # (2026-11-08: 14 s -> 20 s, F05 +2.6 dB and the longer C4 decay ring past 14 s)
    return r.L


def tail_stats(x):
    """(the share of |steps| >= 3 in %, the residual above 2 kHz in LSB rms) over the frames (50 ms, from 1.5 s on)
    whose peak is 8..40 LSB"""
    w = int(0.05 * FS)
    n = len(x) // w
    pk = np.abs(x[:n * w]).reshape(n, w).max(1)
    sel = [i for i in range(int(1.5 / 0.05), n) if 8 <= pk[i] <= 40]
    assert len(sel) >= 20, f"the tail has {len(sel)} frames at 8..40 LSB (no tail to judge)"
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1 / FS) < 2000] = 0
    hi = np.fft.irfft(spec, len(x))
    idx = np.concatenate([np.arange(i * w, (i + 1) * w) for i in sel])
    steps = np.abs(np.diff(x))[idx[:-1]]
    return 100 * np.mean(steps >= 3), float(np.sqrt(np.mean(hi[idx] ** 2)))


@pytest.mark.parametrize("target", ["fm1", "move"])
@pytest.mark.parametrize("amount", [20, 64])
def test_fdrv_tail_is_smooth(target, amount):
    big, res = tail_stats(take(target, amount))
    big0, res0 = tail_stats(take(target, amount, drive=False))
    assert big < 3.0, f"{target} AMOUNT {amount}: {big:.1f} % steps >= 3 LSB in the tail (dry {big0:.1f} %): a staircase"
    assert res < res0 + 0.25, f"{target} AMOUNT {amount}: {res:.2f} LSB above 2 kHz in the tail (dry {res0:.2f})"



@pytest.mark.parametrize("target", ["fm1", "move"])
def test_tail_fades_from_32_lsb_to_silence(target):
    """the silence gate (fx.c GATE_LO 32, GATE_HOLD, GATE_FALL / GATE_RND; the user, 2026-10-29: on the FM-1 and the
    Move): full F05, C4 v100, the pedal down. From the last 50 ms frame at 32 LSB or more to exact zero takes 0.55-1.0 s
    (406 ms under 32, then the ~0.45 s fall): before, the tail went on to 16 LSB and stopped with a 46 ms cut, 1.4 s
    after that frame (the Move: 1.7 s, its 0.45 s fall from 16). After the last frame at 32 nothing exceeds 32 LSB, and
    the last 10 ms are not a run of -1 (the truncated product held every negative sample at -1: 97 % negative on R)"""
    ls = ["0 midi 0xC0 4 0", hostrun.cc(400, 64, 127), *hostrun.note(500, 60, 100, 500)]
    r = hostrun.run(f"drive_tail_{target}_gate", ls, end_ms=20000, binary=binary(target))   # (2026-11-08: 14 s -> 20 s, F05 +2.6 dB and the longer C4 decay ring past 14 s)
    x = np.stack([r.L, r.R], 1)
    end = int(np.nonzero(np.abs(x).max(1))[0][-1])
    w = int(0.05 * FS)
    pk = np.abs(x[:end // w * w]).max(1).reshape(-1, w).max(1)
    last32 = (int(np.nonzero(pk >= 32)[0][-1]) + 1) * w
    fade_ms = (end - last32) * 1000 / FS
    assert 550 <= fade_ms <= 1000, f"{target}: the tail ends {fade_ms:.0f} ms after its last frame at 32 LSB"
    assert np.abs(x[last32:]).max() < 32
    last = x[end - 441:end + 1].ravel()
    nz = last[last != 0]
    assert len(nz) and np.mean(nz < 0) <= 0.85, f"the last 10 ms: {np.mean(nz < 0) * 100:.0f} % of the samples negative"
