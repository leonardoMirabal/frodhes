# SPDX-License-Identifier: GPL-3.0-only
"""The top keys 104-108 (C#7..C8) are generated like the rest (S3 of the rhodes-sonic plan): V1 is the tine mode
(the 2-DOF retune once put the bar mode on f0 there: a 10^-4 tip weight, gain 1505 against 3.0 on key 103, a pure
sine with no bark), so the gain, V1's weight, the level and H2/H1 continue smoothly from key 103. Golden model.
RH_SPEC=path checks another rhodes_spec.json (the 0.9.5 one fails every test here)."""
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import calib_report as cr  # noqa: E402

SPEC = json.load(open(os.environ.get("RH_SPEC", os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json"))))
BY = {k["midi"]: k for k in SPEC["keys"]}
KEYS = list(range(96, 109))
VELS = [40, 64, 104, 127]


@pytest.fixture(scope="module")
def vel():
    return cr.measure_velocity(SPEC, KEYS, VELS)


def test_v1_is_the_tine_mode():
    for n in KEYS:
        m = BY[n]["modes"]
        assert abs(m[0]["f"] / BY[n]["f0"] - 1) < 1e-6, n
        assert abs(m[0]["b"]) > 100 * abs(m[1]["b"]), (n, m[0]["b"], m[1]["b"])     # V2 (the bar) is the small one
        assert m[1]["f"] < m[0]["f"], n


def test_gain_and_weight_continuous():
    g = [math.log(BY[n]["gain"]) for n in KEYS]
    b = [math.log(abs(BY[n]["modes"][0]["b"])) for n in KEYS]
    # no 500x step at 103/104; the per-key jitter (pickup offset +-0.05 gap) moves a key's tip scale and gain by up
    # to ~2x against its neighbours (the voicing and the level calibration compensate it: H2/H1 and level are even)
    assert np.abs(np.diff(g)).max() < math.log(2.5), np.round(np.diff(g), 3)
    assert np.abs(np.diff(b)).max() < math.log(2.5), np.round(np.diff(b), 3)


def test_level_and_bark_continuous(vel):
    for j, v in enumerate(VELS):
        assert np.abs(np.diff(vel["L"][:, j])).max() < 1.0, (v, np.round(vel["L"][:, j], 2))
        assert np.abs(np.diff(vel["H21"][:, j])).max() < 1.5, (v, np.round(vel["H21"][:, j], 2))
    h = vel["H21"]
    assert np.all(np.diff(h, axis=1) > -0.5)                    # the bark grows with velocity on the top keys too
    assert h[:, VELS.index(127)].min() > -40                    # there is a second harmonic at all (was < -60)
