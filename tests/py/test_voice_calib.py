# SPDX-License-Identifier: GPL-3.0-only
"""Voice calibration (tools/calib_report.py measures; these hold the line): the generated tables must play even
across the keyboard and velocity, with one clean hammer strike per key, a bark that grows with velocity, smooth
timbre from key to key, and the 4 TYPEs level-matched. Golden model (tools/rhodes_model.py) unless noted."""
import json
import os
import re
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import calib_report as cr  # noqa: E402
import rhodes_model as gm  # noqa: E402

SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
KEYS = list(range(28, 104))                      # E1..G7
BY = {k["midi"]: k for k in SPEC["keys"]}


@pytest.fixture(scope="module")
def vel():
    return cr.measure_velocity(SPEC, KEYS, cr.VELS)


def jv(v):
    return cr.VELS.index(v)


def test_one_clean_strike_per_key():
    # the hammer bounces off once (FM-1 scan 0.6: 2..12 contacts made the fundamental come and go from key to key)
    multi = [n for n in KEYS if max(BY[n]["pulse"]["ncont"]) > 1]
    assert len(multi) <= 2, multi
    for n in KEYS:                                   # the contact stays inside the fundamental's main lobe
        assert BY[n]["pulse"]["tc_ms"][7] * 1e-3 * BY[n]["f0"] < 0.6, n


def test_velocity_trim_is_small():
    t = np.array([[20 * np.log10(a / b) for a, b in zip(BY[n]["pulse"]["pamp"], BY[n]["pulse"]["pamp_phys"])]
                  for n in KEYS])
    assert np.abs(t).max() < 3.5 and np.median(np.abs(t)) < 0.5, (np.abs(t).max(), np.median(np.abs(t)))


def test_monotonic_at_every_velocity():
    # anchors, the anchor interpolation and the layer crossfade together: louder at every MIDI velocity step
    L, drops = cr.measure_all_velocities(SPEC, KEYS)
    assert drops.max() < 0.25, (KEYS[int(np.argmax(drops))], drops.max())
    assert np.diff(L, axis=1).max() < 1.0               # and no jumps
    rng = L[:, -1] - L[:, 7]                             # v8..v127 (IMPLEMENTATION-v3 C6: 20..40 dB)
    assert 24 < rng.min() and rng.max() < 36, (rng.min(), rng.max())


def test_neighbour_keys_even(vel):
    for v in (40, 64, 104, 127):
        c = vel["L"][:, jv(v)]
        assert np.abs(np.diff(c)).max() < 1.0, (v, np.abs(np.diff(c)).max())
    assert np.ptp(vel["L"][:, jv(104)]) < 0.5


def test_bark_grows_with_velocity(vel):
    h = vel["H21"]
    med = lambda v: float(np.median(h[:, jv(v)]))
    assert med(64) < -18 and med(104) < -10 and med(127) > med(104) + 3, (med(64), med(104), med(127))
    bass = [KEYS.index(n) for n in range(28, 41)]
    assert h[bass, jv(104)].max() < -14                  # the bass is mostly fundamental at mf (R05 W3)
    assert np.all(np.diff(h[:, jv(40):], axis=1) > -0.5)  # every key's H2/H1 rises with velocity


def test_timbre_continuous_across_keys(vel):
    f0 = np.array([BY[n]["f0"] for n in KEYS])
    for v in (64, 104, 127):
        c = np.log2(vel["CEN"][:, jv(v)] / f0)
        assert np.abs(np.diff(c)).max() < 0.1, v
        assert np.abs(np.diff(vel["H21"][:, jv(v)])).max() < 1.5, v


def test_attack_has_no_spike(vel):
    # the strike's peak against the settled waveform's (chatter of a hard re-striking hammer made 10..12 dB)
    assert vel["OV"][:, jv(104)].max() < 3.5
    assert vel["OV"][:, jv(127)].max() < 7.0


def test_layer_crossfade_matches_tables():
    hdr = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    li = [int(x) for x in re.search(r"RH_VLAY_I\[128\] = \{([^}]*)\}", hdr).group(1).split(",")]
    lw = [int(x) for x in re.search(r"RH_VLAY_W\[128\] = \{([^}]*)\}", hdr).group(1).split(",")]
    for v in range(1, 128):
        assert gm.layer_of(v) == (li[v], lw[v]), v


@pytest.mark.parametrize("name", ["SUITCASE", "FELT", "DYNO"])
def test_types_level_matched(name):
    keys = KEYS[::3]
    t = cr.host_types(keys) if not hasattr(test_types_level_matched, "t") else test_types_level_matched.t
    test_types_level_matched.t = t
    d = np.array(t[name]["L"]) - np.array(t["STAGE77"]["L"])
    assert abs(np.median(d)) < 1.5, (name, np.median(d))
    assert np.abs(np.diff(d)).max() < 3.0                 # no register where a TYPE jumps
