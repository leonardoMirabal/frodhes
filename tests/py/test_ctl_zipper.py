# SPDX-License-Identifier: GPL-3.0-only
"""The control zippers and clicks fixed in 0.9.10 (tools/control_sweep.py's material: the pedalled chord struck again
every second, every other effect off), each with its own bound, tighter than the sweep's general limits; each fails
on rc-0910 before its fix (the numbers: before -> after). The renders are shared with test_control_sweep.py
(build/host/ctl_sweep, cached by the binary's hash)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import control_sweep as cs  # noqa: E402

import hostrun  # noqa: E402


@pytest.fixture(scope="module")
def binary():
    return hostrun.build()


def flip_click(name, a, b, binary, wet=False):
    c = cs.BY_NAME[name]
    mv = cs.render(cs.script(c, value=a, flip=b), binary)
    n = len(mv)
    sa, sb = cs.render(cs.script(c, value=a), binary)[:n], cs.render(cs.script(c, value=b), binary)[:n]
    if wet:
        o = cs.render(cs.script(c, off=True), binary)[:n]
        mv, sa, sb = mv - o, sa - o, sb - o
    return cs.click_ratio(mv, sa, sb)


def sweep_zipper(name, binary):
    c = cs.BY_NAME[name]
    off = cs.render(cs.script(c, off=True), binary)
    st = [cs.render(cs.script(c, value=v), binary) for v in c.statics]
    sw = cs.render(cs.script(c, sweep=True), binary)
    return cs.zipper_ratio(sw, st), cs.zipper_ratio(sw - off, [s - off for s in st])


def test_comp_makeup_glides(binary):
    """MAKEUP 0 <-> 127 in one CC under the ringing chord: no click (17.3x before the glide)"""
    for a, b in ((0, 127), (127, 0)):
        assert flip_click("comp MAKEUP", a, b, binary) <= 3.0, (a, b)


def test_tape_time_heads_follow_the_motor(binary):
    """TAPE TIME swept: the head bump and the high-frequency loss move with the motor's speed (2.53x before)"""
    z, zw = sweep_zipper("delay TAPE TIME", binary)
    assert z <= 2.0, z
    assert zw <= 2.3, zw          # (the echoes' own varispeed: 1.63x the pitch at the sweep's end, r^2 ~ 2.6 in d2)


def test_hall_size_auto_glides(binary):
    """HALL SIZE 1 <-> 0 (AUTO, MIX 64's size: 35 steps away) under a ringing tail: no click in the wet (2.07x)"""
    for a, b in ((1, 0), (0, 1), (127, 0)):
        assert flip_click("rev HALL SIZE", a, b, binary, wet=True) <= 1.5, (a, b)
    z, zw = sweep_zipper("rev HALL SIZE", binary)
    assert max(z, zw) <= 1.5, (z, zw)


def test_plate_size_fade_is_smooth(binary):
    """PLATE SIZE swept: the reads' cross-fade moves every sample (a staircase of 8 steps a site: 2.59x, wet 3.35x)"""
    z, zw = sweep_zipper("rev PLATE SIZE", binary)
    assert z <= 2.0 and zw <= 2.0, (z, zw)
