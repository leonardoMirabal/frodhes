# SPDX-License-Identifier: GPL-3.0-only
"""RESPONSE (CC 40, the MIDI page's 4th card): what A and B do to the electric piano (rhodes.c rh_dyn, rh_touch_mode).

A (DEEP) is the dynamic response: the soft end goes down to -6 dB at v16 (v20 -> v127 ~31.4 dB over the keys);
B (EVEN) is the light touch: 0.9.9's velocity law (v20 -> v127 ~25.5 dB). Only the level of the soft end differs:
the same drive, swing and harmonics at every key and velocity, so B is A times a gain (to the integers' rounding),
the same from v60 up, and never under A. Measured on the host, the engine alone or effects off, LEVEL 64:

- the ranges: the mean over keys 28..93 of the LUFS (0..1 s) at v127 minus v20: A 31.4 dB measured (>= 30.4, the
  user: as dynamic as it stays pristine; its depth is held by fx.c's output gate, rh_dyn's comment), B 25.5 +- 1.5;
- B >= A at every key and velocity (0.05 dB), equal from v64 (0.05 dB), so v127 and the limiter's load are A's;
- pristine: A is B times one gain, the residual at most 4 LSB peak (the 16-bit rounding; harmonics re H1 the same:
  a measure of H3..H6 at a soft note reads the 16-bit floor, so the residual is the check);
- CC 40 turned while a note sounds does not touch it (taken at the strike): the same samples;
- VIBES and MARIMBA do not read RESPONSE: the same samples with A and B.
Never louder after the key-up with either response: tests/py/test_release_never_louder.py (its -EVEN rows); TONE's
level compensation with either: tests/py/test_tone_level.py."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from hostrun import FS, run  # noqa: E402
from bar_measure import lufs  # noqa: E402

KEYS = (28, 33, 40, 45, 52, 57, 64, 69, 76, 81, 88, 93)
VELS = (20, 40, 64, 96, 127)
A_RANGE_MIN = 30.4                     # 31.4 measured (2026-11-08), less 1 dB
B_RANGE = (24.0, 27.0)                 # 0.9.9's 25.5 +- 1.5


def note(resp, k, v, extra=(), raw=False, tag=""):
    ev = ["50 midi 0xB0 10 127", f"100 midi 0xB0 40 {resp}",
          "150 midi 0xB0 119 127" if raw else "150 midi 0xB0 111 127", "160 midi 0xB0 110 64",
          f"400 midi 0x90 {k} {v}", *extra, f"1400 midi 0x80 {k} 0"]
    r = run(f"vresp-{resp}-{k}-{v}-{int(raw)}-{tag}", ev, 1500)
    return np.stack([r.L, r.R], 1).astype(float)


def level(resp, k, v):
    return lufs(note(resp, k, v)[int(0.4 * FS):int(1.4 * FS)].mean(1))


@pytest.fixture(scope="module")
def grid():
    return {(r, k, v): level(r, k, v) for r in (0, 127) for k in KEYS for v in VELS}


def test_ranges(grid):
    rng = {r: float(np.mean([grid[(r, k, 127)] - grid[(r, k, 20)] for k in KEYS])) for r in (0, 127)}
    assert rng[0] >= A_RANGE_MIN, f"A: v20 -> v127 {rng[0]:.1f} dB (31.4 measured)"
    assert B_RANGE[0] <= rng[127] <= B_RANGE[1], f"B: v20 -> v127 {rng[127]:.1f} dB (0.9.9 25.5)"


def test_b_never_under_a_and_equal_from_mf(grid):
    for k in KEYS:
        for v in VELS:
            d = grid[(127, k, v)] - grid[(0, k, v)]
            assert d >= -0.05, f"key {k} v{v}: B {d:+.2f} dB under A"
            if v >= 64:
                assert abs(d) <= 0.05, f"key {k} v{v}: B {d:+.2f} dB re A (the same from v60)"


@pytest.mark.parametrize("k", (33, 57, 81))
@pytest.mark.parametrize("v", (1, 20, 40))
def test_b_is_a_times_a_gain(k, v):
    a, b = note(0, k, v, raw=True).mean(1), note(127, k, v, raw=True).mean(1)
    g = float(np.dot(a, b) / np.dot(b, b))
    r = a - g * b
    assert 20 * np.log10(g) < -0.5, f"key {k} v{v}: A {20 * np.log10(g):+.2f} dB re B (A's soft end is deeper)"
    assert np.abs(r).max() <= 4.0, f"key {k} v{v}: A - g B peaks at {np.abs(r).max():.1f} LSB (not one sound)"


def test_response_taken_at_the_strike():
    for resp, to in ((0, 127), (127, 0)):
        a = note(resp, 57, 30, tag="held")
        b = note(resp, 57, 30, extra=(f"900 midi 0xB0 40 {to}",), tag="turned")
        assert np.array_equal(a, b), f"RESPONSE {resp} -> {to} under a sounding note changed it"


@pytest.mark.parametrize("cc79", (0, 110), ids=["VIBES", "MARIMBA"])
def test_bars_ignore_response(cc79):
    def bar(resp):
        ev = ["50 midi 0xB0 10 127", f"100 midi 0xB0 40 {resp}", f"150 midi 0xB0 79 {cc79}",
              "400 midi 0x90 60 20", "450 midi 0x90 72 100", "1400 midi 0x80 60 0", "1400 midi 0x80 72 0"]
        r = run(f"vresp-bar-{cc79}-{resp}", ev, 2000)
        return np.stack([r.L, r.R], 1)
    assert np.array_equal(bar(0), bar(127)), "a bar engine reads RESPONSE"
