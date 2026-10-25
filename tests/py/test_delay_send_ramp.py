# SPDX-License-Identifier: GPL-3.0-only
"""fx.c fx_delay: the send's stereo make-up (dly_sg) moved once per 256-frame block. When L != R (pan, phaser,
chorus) it is not 1, and each block's new value put a one-sample step into the send, so the echoes carried a step one
delay time after every block boundary (B's stress_max detector, 2026-10-24: up to ~700 LSB on VIBES). The make-up now
ramps sample by sample from the last block's value.

The measure: the output's third difference |d3| by the sample's phase in the block. A step sits at phase
(delay time + 32) mod 256 (32: the look-ahead limiter's sub-block of latency). Before the ramp the worst |d3| there
was 8.6-24x the median phase's (the Rhodes pan 22x, phaser 8.6x; VIBES pan 24x; MARIMBA phaser 20x); now 1.2-1.4x."""
import os
import re

import numpy as np
import pytest

import hostrun as hr

T = [int(v) for v in re.search(r"FX_DLY_T\[128\] = \{([^}]*)\}",
                               open(os.path.join(hr.ROOT, "firmware", "gen", "fx_tables.h")).read()).group(1).split(",")
     if v.strip()]


def step_ratio(tag, fx, time, bar=None):
    ls = [hr.cc(5, 120, 0)] + ([hr.cc(10, 79, bar)] if bar is not None else [])
    t = 20
    for c, v in fx + [(88, 48), (89, 64), (90, time), (26, 60)]:          # D.DELAY MIX 64, TIME, FDBK 60
        ls.append(hr.cc(t, c, v))
        t += 2
    for k, n in enumerate((48, 55, 60, 64, 67, 72)):
        ls += hr.note(300 + 40 * k, n, 90, 3500)
    r = hr.run(tag, ls, 5000)
    p0 = (T[time] + 32) % 256
    out = []
    for x in (r.L, r.R):
        i0 = int(0.6 * hr.FS)
        d3 = np.abs(np.diff(x[i0:int(4.0 * hr.FS)], 3))
        ph = (np.arange(len(d3)) + i0 + 2) % 256
        pr = np.array([d3[ph == p].max() for p in range(256)])
        near = max(pr[(p0 + k) % 256] for k in range(-3, 4))
        out.append(near / np.median(pr))
    return max(out)


@pytest.mark.parametrize("name, fx, time, bar", [
    ("rhodes-pan", [(17, 64), (18, 100), (21, 0)], 40, None),
    ("rhodes-phaser", [(12, 127), (13, 90)], 60, None),
    ("vibes-pan", [(17, 64), (18, 100), (21, 0)], 20, 64),
    ("marimba-phaser", [(12, 127), (13, 90)], 30, 127),
])
def test_no_step_one_delay_after_the_block(name, fx, time, bar):
    q = step_ratio(f"dlysend-{name}", fx, time, bar)
    assert q < 3.0, (name, round(q, 2))


def chorus_ratio(tag, bar=None, depth=80):
    """the chorus alone: its taps moved once per 32-sample sub-block (a step in the delay time); the worst |d3| on the
    sub-block grid re the median phase's. Before the glide 8.7-14x (the Rhodes DEPTH 80 / 127, VIBES), now 1.2-1.4x"""
    ls = [hr.cc(5, 120, 0)] + ([hr.cc(10, 79, bar)] if bar is not None else []) + [hr.cc(20, 85, 64), hr.cc(22, 86, depth)]
    for k, n in enumerate((48, 55, 60, 64, 67, 72)):
        ls += hr.note(300 + 40 * k, n, 90, 3500)
    r = hr.run(tag, ls, 5000)
    out = []
    for x in (r.L, r.R):
        i0 = int(0.6 * hr.FS)
        d3 = np.abs(np.diff(x[i0:int(4.0 * hr.FS)], 3))
        ph = (np.arange(len(d3)) + i0 + 2) % 256
        pr = np.array([d3[ph == p].max() for p in range(256)])
        grid = max(pr[(p + k) % 256] for p in range(0, 256, 32) for k in (-1, 0, 1))
        out.append(grid / np.median(pr))
    return max(out)


@pytest.mark.parametrize("name, bar, depth", [("rhodes", None, 80), ("rhodes127", None, 127), ("vibes", 64, 80)])
def test_chorus_taps_glide(name, bar, depth):
    q = chorus_ratio(f"chosteps-{name}", bar, depth)
    assert q < 3.0, (name, round(q, 2))
