# SPDX-License-Identifier: GPL-3.0-only
"""RESPONSE (CC 40, the MIDI page's 4th card): what A and B do to the electric piano (rhodes.c rh_dyn, rh_touch_mode).

A (FULL) is the dynamic response: the soft end goes down to -9 dB at v20 (v20 -> v127 ~34.5 dB over the keys);
B (LIGHT) is the light touch: 0.9.9's velocity law (v20 -> v127 ~25.5 dB). Only the level of the soft end differs:
the same drive, swing and harmonics at every key and velocity, so B is A times a gain (to the integers' rounding),
the same from v60 up, and never under A. Measured on the host, the engine alone or effects off, LEVEL 64:

- the ranges: the mean over keys 28..93 of the LUFS (0..1 s) at v127 minus v20: A 34.5 dB measured (>= 33.5, the
  user: as dynamic as it stays pristine; its depth is held by fx.c's output gate, rh_dyn's comment), B 25.5 +- 1.5;
- A's steps: key 60, v1..61, every velocity step louder and by at most 1 dB (0.65 measured);
- the output gate (fx.c GATE_LO 16 LSB since A went deeper; 0.9.9 32): per velocity (1, 10, 20, 30), keys 21..96
  every 6th x the four TYPEs, no more A notes drop under it within 1 s (and so fade 0.4 s later) than 0.9.9's did
  under its own (0.9.9 measured: 10 / 8 / 5 / 4 of 52; this build 10 / 8 / 5 / 0);
- B >= A at every key and velocity (0.05 dB), equal from v64 (0.05 dB), so v127 and the limiter's load are A's;
- pristine: A is B times one gain, the residual at most 4 LSB peak (the 16-bit rounding; harmonics re H1 the same:
  a measure of H3..H6 at a soft note reads the 16-bit floor, so the residual is the check);
- CC 40 turned while a note sounds does not touch it (taken at the strike): the same samples;
- VIBES and MARIMBA do not read RESPONSE: the same samples with A and B.
Never louder after the key-up with either response: tests/py/test_release_never_louder.py (its -LIGHT rows); TONE's
level compensation with either: tests/py/test_tone_level.py."""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from hostrun import FS, run  # noqa: E402
from bar_measure import lufs  # noqa: E402

KEYS = (28, 33, 40, 45, 52, 57, 64, 69, 76, 81, 88, 93)
VELS = (20, 40, 64, 96, 127)
A_RANGE_MIN = 33.5                     # 34.5 measured (2026-11-08, A -9 dB at v20, the gate at 16), less 1 dB
GATE = int(re.search(r"#define GATE_LO (\d+)", open(os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "src",
                                                                 "fx.c")).read()).group(1))   # fx.c's output gate (16)
GATE_099 = {1: 10, 10: 8, 20: 5, 30: 4}   # 0.9.9 (456b5da4), its gate at 32: notes under it within 1 s, of 52
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
    assert rng[0] >= A_RANGE_MIN, f"A: v20 -> v127 {rng[0]:.1f} dB (34.5 measured)"
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


def test_a_steps_smooth():
    lv = [level(0, 60, v) for v in range(1, 62)]
    d = np.diff(lv)
    assert d.min() > 0.0, f"A at key 60: v{int(np.argmin(d)) + 1} -> v{int(np.argmin(d)) + 2} {d.min():+.2f} dB (not monotonic)"
    assert d.max() <= 1.0, f"A at key 60: v{int(np.argmax(d)) + 1} -> v{int(np.argmax(d)) + 2} {d.max():+.2f} dB (a step)"


@pytest.mark.parametrize("v", sorted(GATE_099))
def test_soft_notes_not_gated_sooner_than_099(v):
    def above(k, ty):
        ev = ["50 midi 0xB0 10 127", "150 midi 0xB0 111 127", "160 midi 0xB0 110 64", f"170 midi 0xB0 80 {ty}",
              f"400 midi 0x90 {k} {v}", f"3400 midi 0x80 {k} 0"]
        r = run(f"vresp-gate-{k}-{ty}-{v}", ev, 3500)
        x = np.maximum(np.abs(r.L), np.abs(r.R))[int(0.4 * FS):int(3.4 * FS)]
        w = x[:len(x) // 1024 * 1024].reshape(-1, 1024).max(1)
        a = np.nonzero(w >= GATE)[0]
        return (a[-1] + 1) * 1024 / FS if len(a) else 0.0
    ts = [above(k, ty) for k in range(21, 97, 6) for ty in (0, 32, 64, 96)]
    n = sum(t < 1.0 for t in ts)
    assert n <= GATE_099[v], f"v{v}: {n} of {len(ts)} notes under the gate within 1 s (0.9.9: {GATE_099[v]})"
    assert min(ts) > 0.0, f"v{v}: a note starts under the gate"
