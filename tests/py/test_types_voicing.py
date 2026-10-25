# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes TYPE voicing (0.9.5, tools/types_ab.py measures): the SUITCASE cabinet (a 250 Hz box bump and a high
shelf, no high-pass), the bark against velocity, and the TONE knob's top (short of the pole axis, where the
fundamental cancels). The coefficients from firmware/gen/rhodes_tables.h; the sound from the host build."""
import json
import math
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402

ROOT = hostrun.ROOT
sys.path.insert(0, os.path.join(ROOT, "tools"))
import types_ab as ta  # noqa: E402

HDR = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
P = json.load(open(os.path.join(ROOT, "docs", "model", "data", "params.json")))


def body(ti):
    rows = re.search(r"RH_BODY\[4\]\[2\]\[5\] = \{(.*?)\n\};", HDR, re.S).group(1).strip().split("\n")
    nums = [int(x) for x in re.findall(r"-?\d+", rows[ti])]
    return [nums[0:5], nums[5:10]]


def resp_db(bqs, f):
    z = np.exp(-1j * 2 * math.pi * f / 44100)
    h = 1.0
    for b0, b1, b2, a1, a2 in bqs:
        h *= (b0 + b1 * z + b2 * z * z) / (2 ** 28 + a1 * z + a2 * z * z)
    return 20 * math.log10(abs(h))


def test_suitcase_cabinet_shelf():
    bq = body(1)
    assert P["types"]["SUITCASE"]["body"][1][0] == "hshelf"
    assert abs(resp_db(bq, 30)) < 0.5                    # no 50 Hz high-pass any more (the load + DC blocker)
    assert 2.5 < resp_db(bq, 250) < 4.0                  # the box bump kept
    assert -5.5 < resp_db(bq, 12000) < -4.0              # the cones' roll-off
    assert resp_db(bq, 12000) < resp_db(bq, 5500) < resp_db(bq, 2000) < 0.5
    assert len(P["types"]["SUITCASE"]["body"]) == 2      # the same two biquads: no CPU


@pytest.fixture(scope="module")
def binr():
    ta.BIN = hostrun.build()
    return ta


def test_suitcase_level_and_timbre(binr):
    lines = [([(80, ti * 32)], n, 100) for ti in (0, 1) for n in (48, 72)]
    s = [ta.note_stats(x, n) for x, n in zip(ta.render(hostrun.out("types_suit"), lines, 800), (48, 72) * 2)]
    for a, b in ((0, 2), (1, 3)):
        assert abs(s[a]["L"] - s[b]["L"]) < 1.5, (s[a]["L"], s[b]["L"])      # level-matched to STAGE 77


def test_bark_grows_with_velocity(binr):
    vels = (60, 96, 127)
    for n in (45, 57, 69):
        r = [ta.note_stats(x, n) for x in ta.render(hostrun.out(f"bark_{n}"), [([], n, v) for v in vels], 800)]
        h = [x["H21"] for x in r]
        assert h[0] < h[1] - 3 < h[2] - 6, (n, h)                          # the bark grows at every step
        g = (h[2] - h[1]) / (r[2]["L"] - r[1]["L"])
        assert 0.7 < g < 1.2, (n, g)                    # ~1 dB of H2/H1 per dB of level above v96 (0.84..0.94)
        assert h[2] < -3.0                              # still mostly fundamental at v127 (the tip in its range)


@pytest.mark.parametrize("ti", [0, 3])                  # STAGE 77, DYNO (its +0.1 gap meets the offset cap)
def test_tone_top_short_of_the_axis(binr, ti):
    vals = (0, 32, 64, 80, 96, 112, 127)
    segs = ta.render(hostrun.out(f"tone_{ti}"), [([(80, ti * 32), (28, v)], 57, 100) for v in vals], 800)
    s = [ta.note_stats(x, 57) for x in segs]
    h1, h21, lv = [x["H1"] for x in s], [x["H21"] for x in s], [x["L"] for x in s]
    assert np.all(np.diff(h1) <= 0.3), h1                # the fundamental falls steadily: no cancel, no come-back
    assert np.all(np.diff(h21) >= -0.3), h21             # brighter all the way up
    assert h21[-1] < 0.0, h21                            # the fundamental still leads at the top
    assert lv[2] - lv[-1] < 12.0, lv                     # 0.9.4: -14 dB at TONE 104 (the axis), then louder again


def test_tone_law_in_tables():
    up = int(re.search(r"#define RH_TONE_UP (\d+)", HDR).group(1))
    cap = int(re.search(r"#define RH_OFF_MAX \((-?\d+)\)", HDR).group(1))
    off = P["pickup"]["off_gap"] * 2 ** 25
    assert off + 63 * up <= cap + 64                     # STAGE 77 at TONE 127 reaches the cap, not past it
    assert cap < -0.05 * 2 ** 25
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    assert "v <= 64 ? (v - 64) * (1 << 18) : (v - 64) * RH_TONE_UP" in src   # the lower half as before
