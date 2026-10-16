# SPDX-License-Identifier: GPL-3.0-only
"""VIBES calibration (P2) and Gate VA on the golden model (tools/bars_model.py, tools/bars_va.py): the generated
tables play even across the keyboard and velocity, level-matched to the Rhodes ST77, with continuous timbre across
the pulse bands; every Gate VA check passes except the documented data conflicts (KNOWN), which stay visible."""
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bars_model as gm   # noqa: E402
import bars_va as va      # noqa: E402
import rhodes_model as rm  # noqa: E402

SPEC = gm.load_spec()
K = {k["midi"]: k for k in SPEC["keys"]}
D = {d["midi"]: d for d in SPEC["design"]}
KEYS = list(range(21, 109))
AUTH = list(range(53, 90))

# Gate VA checks that fail on the present data, with the reason (PLAN §10 vs the measurements and the bounds):
KNOWN = {
    "f0 peak (recordings' procedure)": "the model peaks earlier than the smoothed table at D5 / G5 / A5 even at the "
                                       "0.5 % zeta_r floor (the tube cannot be slower without a lower floor or more "
                                       "tube share T5b)",
    "V1 T60 at the reference pitches within +-15 % of the smoothed table": "sigma clamped at 0.006 at G3 / D4 / F6 "
                                       "(zeta_r is set by the peak time first); the keys are flagged (bit2)",
}


@pytest.fixture(scope="module")
def va_checks():
    return va.checks(SPEC)


def test_gate_va(va_checks):
    failed = [n for n, v, ok, kind in va_checks if kind == "gate" and not ok]
    unknown = [n for n in failed if not any(n.startswith(k) for k in KNOWN)]
    assert not unknown, [(n, v) for n, v, ok, kind in va_checks if n in unknown]
    gated = [n for n, v, ok, kind in va_checks if kind == "gate"]
    assert len(gated) >= 38


def test_known_va_conflicts_reported(va_checks):
    """The known conflicts are still measured (a check that disappears must be removed from KNOWN by hand)."""
    names = [n for n, *_ in va_checks]
    for k in KNOWN:
        assert any(n.startswith(k) for n in names), k


def levels(keys, vels, knobs=None, t0=0.05, t1=0.30, dur=0.32):
    y = gm.render_batch(SPEC, keys, vels, dur, knobs, sleep=False)
    return np.array([gm.rms_db(r, t0, t1) for r in y])


def test_velocity_monotonic_every_key():
    vels = list(range(1, 128, 6)) + [127]
    for bark in (0, 42, 64, 85, 127):
        L = np.array([levels(KEYS, [v] * len(KEYS), dict(gm.DEFAULT_KNOBS, bark=bark)) for v in vels]).T
        drops = -np.diff(L, axis=1)
        assert drops.max() < 0.5, (bark, KEYS[int(np.argmax(drops.max(axis=1)))], drops.max())
        rng = L[:, -1] - L[:, 1]                                  # v7 .. v127
        assert 15 < rng.min() and rng.max() < 35, (bark, rng.min(), rng.max())


def test_neighbour_levels():
    L = levels(KEYS, [100] * len(KEYS))
    d = np.abs(np.diff(L))
    auth = [i for i in range(len(KEYS) - 1) if KEYS[i] in AUTH and KEYS[i + 1] in AUTH]
    assert d[auth].max() <= 1.5, d[auth].max()
    assert d.max() <= 3.0, (KEYS[int(np.argmax(d))], d.max())
    want = SPEC["vibes"]["calib"]["gain_target_dbfs"] + SPEC["st77_trim_db"] + 20 * math.log10(32768)
    assert abs(np.median(L) - want) < 0.5


def test_level_matched_to_st77():
    """The A3-E4-A4 chord at v100: VIBES within 1 dB (RMS over 0..1 s) of the Rhodes STAGE 77 (both golden, no FX)."""
    chord = [57, 64, 69]
    yb = gm.render_batch(SPEC, chord, [100] * 3, 1.0, None, sleep=False).sum(axis=0)
    rs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
    rk = {k["midi"]: k for k in rs["keys"]}
    yr = rm.render_batch([rk[n] for n in chord], rs["pickup"], [100] * 3, 1.0,
                         gains=[rk[n]["gain"] for n in chord]).sum(axis=0)
    lb, lr = gm.rms_db(yb, 0.0, 1.0), gm.rms_db(yr, 0.0, 1.0)
    assert abs(lb - lr) <= 1.0, (lb, lr)


def centroid(y, f0, t1=0.05):
    n = int(t1 * gm.FS)
    S = np.abs(np.fft.rfft(y[:n] * np.hanning(n), 1 << 15)) ** 2
    fr = np.fft.rfftfreq(1 << 15, 1 / gm.FS)
    return float(np.sum(S * fr) / np.sum(S)) / f0


def test_centroid_continuous_across_bands():
    """Centroid (re f0) of the attack (0..50 ms, where the pulse shape is heard; later the measured per-note decays
    dominate, e.g. V2 3.4 s at E4 -> 1.0 s at F4) step <= 15 % between neighbouring keys in different pulse bands,
    at v100 and v127 RUBBER."""
    for v, kn in ((100, None), (127, dict(gm.DEFAULT_KNOBS, bark=127))):
        y = gm.render_batch(SPEC, KEYS, [v] * len(KEYS), 0.32, kn, sleep=False)
        c = np.array([centroid(r, D[n]["f0"]) for n, r in zip(KEYS, y)])
        for i in range(len(KEYS) - 1):
            a, b = KEYS[i], KEYS[i + 1]
            if K[a]["band"] != K[b]["band"] and a in AUTH and b in AUTH:
                assert abs(c[i + 1] / c[i] - 1) <= 0.15, (v, a, b, c[i], c[i + 1])


def test_trim_small_in_the_real_range():
    """The velocity trim and the family level match stay modest on F3..F6: BAR_PAMP vs the physical peak-force curve
    (the contact simulation), each key's common factor removed, within 12 dB everywhere, median under 2 dB."""
    allr = []
    for n in AUTH:
        pa = np.array(SPEC["pamp"][n - 21], float)
        ph = np.array(SPEC["pamp_phys"][n - 21], float)
        r = 20 * np.log10(pa / ph)
        r -= np.median(r)
        allr.append(np.abs(r))
        assert np.abs(r).max() < 12, (n, np.abs(r).max())
    assert np.median(np.concatenate(allr)) < 2.0
