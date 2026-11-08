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
    # level-matched at the calibration velocity (gen_rhodes: v100); with the pickup drive each register has its own
    # velocity knee (params.json pickup_drive), so away from v100 the keys part by design (v104: under 1 dB)
    lv = cr.measure_velocity(SPEC, KEYS, [100])["L"][:, 0]
    assert np.ptp(lv) < 0.5, np.ptp(lv)


def test_bark_grows_with_velocity(vel):
    # the pickup drive (params.json pickup_drive): the soft colour holds up to each register's knee, then the bark
    # grows; the knee rises with the register and the top has none
    h = vel["H21"]
    med = lambda v: float(np.median(h[:, jv(v)]))
    assert med(64) < -18 and med(127) > med(104) + 3, (med(64), med(104), med(127))
    bass = [KEYS.index(n) for n in range(28, 53)]          # knee ~v58
    assert np.abs(h[bass, jv(48)] - h[bass, jv(1)]).max() < 1.0      # flat below the knee
    assert np.all(h[bass, jv(127)] - h[bass, jv(64)] > 20)           # and well up above it
    top = [KEYS.index(n) for n in range(86, 104)]
    assert np.abs(h[top, jv(127)] - h[top, jv(40)]).max() < 2.0      # no rise on the top keys
    assert np.all(np.diff(h[:, jv(40):], axis=1) > -0.5)  # every key's H2/H1 rises with velocity


def test_timbre_continuous_across_keys(vel):
    f0 = np.array([BY[n]["f0"] for n in KEYS])
    # below the knees 0.1 octave / 1.5 dB a key; at v104 and v127 the knee moves 18 velocity steps from key 64 to
    # key 69 (pickup_drive.knee_points, as measured), so the bark (and the centroid) falls up to 2.9 dB a key there
    for v, cm, hm in ((64, 0.1, 1.5), (104, 0.2, 3.2), (127, 0.2, 3.2)):
        c = np.log2(vel["CEN"][:, jv(v)] / f0)
        assert np.abs(np.diff(c)).max() < cm, v
        assert np.abs(np.diff(vel["H21"][:, jv(v)])).max() < hm, v


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
