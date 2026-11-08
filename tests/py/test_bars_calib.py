# SPDX-License-Identifier: GPL-3.0-only
"""VIBES calibration (P2) and Gate VA on the golden model (tools/bars_model.py, tools/bars_va.py): the generated
tables play even across the keyboard and velocity, at the measured balance against the Rhodes ST77, with continuous timbre across
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

# Gate VA checks that fail on the present data, with the reason (PLAN §10 vs the measurements and the bounds). Empty
# since 2026-10-17 (the tube tuned within a few cents, T4b; the T60-first zeta_r trade, T4a; sigma up to 0.007, T5a).
KNOWN = {}


@pytest.fixture(scope="module")
def va_checks():
    return va.checks(SPEC)


def test_gate_va(va_checks):
    failed = [n for n, v, ok, kind in va_checks if kind == "gate" and not ok]
    unknown = [n for n in failed if not any(n.startswith(k) for k in KNOWN)]
    assert not unknown, [(n, v) for n, v, ok, kind in va_checks if n in unknown]
    gated = [n for n, v, ok, kind in va_checks if kind == "gate"]
    assert len(gated) >= 42


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


def st77_makeup(n, v):
    """rhodes.c rh_drv_mk at STAGE 77 (firmware/gen/rh_drive_mk.h, rh_mk_q12 in integers) as a gain: the golden
    model has the pickup drive but not its level make-up, which brings the firmware back to the 0.9.9 level"""
    import re
    if not hasattr(st77_makeup, "rows"):
        h = open(os.path.join(ROOT, "firmware", "gen", "rh_drive_mk.h")).read()
        body = h[h.index("RH_DRV_MK[4]"):]
        st77_makeup.rows = [[int(x) for x in r.split(",")] for r in re.findall(r"\{([0-9,]+)\}", body)[:88]]
    f = [32768, 33486, 34219, 34968, 35734, 36516, 37316, 38133, 38968, 39821, 40693, 41584, 42495, 43425, 44376, 45348,
         46341, 47356, 48393, 49452, 50535, 51642, 52773, 53928, 55109, 56316, 57549, 58809, 60097, 61413, 62757, 64132]
    lin = lambda c: (f[c & 31] << (c >> 5)) >> 7
    u = max(0, v - 40)
    m = st77_makeup.rows[n - 21][u >> 3:]
    c = (m[0] << 3) + (m[1] - m[0]) * (u & 7)
    x0, x1 = lin(c >> 3), lin((c >> 3) + 1)
    return (x0 + (((x1 - x0) * (c & 7)) >> 3)) / 4096.0


# 0.9.10: VIBES stays byte-identical (user, 2026-11-08), and the Rhodes is new: louder in the middle and the top
# relative to its bass, so the 0.9.9 balance (VIBES 2.5 dB over the Rhodes in every region, user 2026-10-17) moved.
# These two tests now hold the measured 0.9.10 balance (golden models, the Rhodes x its drive make-up), so a level
# change in either engine still shows: VIBES minus the Rhodes, single notes at v100, K-weighted over 0..1 s, per key
# region (the firmware's own chord / register measure: docs/MANUAL.md, VIBES).
CHORD_DB = 0.2                                     # the A3-E4-A4 chord at v100 (0.9.9: 2.5)
REGION_LU = {(21, 40): 0.3, (41, 52): 2.5, (53, 59): 2.9, (60, 71): 1.5, (72, 83): -2.4, (84, 89): -1.8,
             (90, 99): -0.1, (100, 108): -2.2}    # (0.9.9: 2.5 in every region)


def test_level_matched_to_st77():
    """The A3-E4-A4 chord at v100: VIBES minus the Rhodes STAGE 77 (RMS over 0..1 s, both golden, no FX) at the
    0.9.10 balance, CHORD_DB within 0.5 dB."""
    chord = [57, 64, 69]
    yb = gm.render_batch(SPEC, chord, [100] * 3, 1.0, None, sleep=False).sum(axis=0)
    rs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
    rk = {k["midi"]: k for k in rs["keys"]}
    yr = rm.render_batch([rk[n] for n in chord], rs["pickup"], [100] * 3, 1.0,   # (2026-11-08: x the drive's make-up,
                         gains=[rk[n]["gain"] * st77_makeup(n, 100) for n in chord]).sum(axis=0)   # the firmware level)
    lb, lr = gm.rms_db(yb, 0.0, 1.0), gm.rms_db(yr, 0.0, 1.0)
    assert abs(lb - lr - CHORD_DB) <= 0.5, (lb, lr, CHORD_DB)


def lufs_k(x, t0=0.0, t1=1.0):
    from scipy.signal import sosfilt
    import bar_measure as bm
    y = sosfilt(bm.k_weight_sos(gm.FS), np.asarray(x, float) / 32767.0)[int(t0 * gm.FS):int(t1 * gm.FS)]
    return -0.691 + 10 * math.log10(float(np.mean(y * y)) + 1e-15)


def test_rhodes_level_per_region():
    """The hiss / SNR rule's balance: single notes at v100, K-weighted loudness over 0..1 s (S9), golden VIBES minus
    golden ST77 per key region at the 0.9.10 balance (REGION_LU, within 0.5 LU); VIBES neighbours within 1.5 dB."""
    rs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
    rk = {k["midi"]: k for k in rs["keys"]}
    lb = np.array([lufs_k(r) for r in gm.render_batch(SPEC, KEYS, [100] * len(KEYS), 1.0, None, sleep=False)])
    lr = np.array([lufs_k(r) for r in rm.render_batch([rk[n] for n in KEYS], rs["pickup"], [100] * len(KEYS), 1.0,
                                                      gains=[rk[n]["gain"] * st77_makeup(n, 100) for n in KEYS])])
    # (2026-11-08: the Rhodes x the drive's make-up, rh_drive_mk.h: the firmware's level, as before the drive)
    for (a, b), want in REGION_LU.items():
        i = [j for j, n in enumerate(KEYS) if a <= n <= b]
        d = float(np.mean(lb[i]) - np.mean(lr[i]))
        assert abs(d - want) <= 0.5, (a, b, d, want)
    assert np.abs(np.diff(lb)).max() <= 1.5, np.abs(np.diff(lb)).max()


def centroid(y, f0, t1=0.05):
    n = int(t1 * gm.FS)
    S = np.abs(np.fft.rfft(y[:n] * np.hanning(n), 1 << 15)) ** 2
    fr = np.fft.rfftfreq(1 << 15, 1 / gm.FS)
    return float(np.sum(S * fr) / np.sum(S)) / f0


def test_centroid_continuous_across_bands():
    """Continuous timbre across the pulse bands. The band tables' own share, at every band boundary in F3..F6 (v100
    and v127 RUBBER, the attack 0..50 ms where the pulse is heard): playing each boundary key with its neighbour's
    pulse band changes its centroid (re f0) by <= 10 % (the strike table is exact per key at the default TONE, gen_bars
    POS_DEF). The whole step between the two keys <= 25 %: on top of the bands, the per-key data moves the attack
    centroid by up to ~20 % between neighbours on its own (RUBBER's attack is V4-dominated at 13.76 f0 and V4 carries
    the T11 per-key detune of +-70 c on the pulse's steep slope; the measured per-note V2 / V3 decays)."""
    import copy
    for v, kn in ((100, None), (127, dict(gm.DEFAULT_KNOBS, bark=127))):
        y = gm.render_batch(SPEC, KEYS, [v] * len(KEYS), 0.06, kn, sleep=False)
        c = np.array([centroid(r, D[n]["f0"]) for n, r in zip(KEYS, y)])
        for i in range(len(KEYS) - 1):
            a, b = KEYS[i], KEYS[i + 1]
            if K[a]["band"] != K[b]["band"] and a in AUTH and b in AUTH:
                # (E5 / F#5, 77 / 78: RUBBER's 0.15 ms pulse puts its first null on V4 (13.6 f0, 9.5 -> 10.5 kHz) and
                # the attack centroid falls ~40 % by the per-key data alone; a band boundary there since the
                # 2026-10-17 families: the bands' own share stays the 10 % below)
                assert abs(c[i + 1] / c[i] - 1) <= (0.42 if a == 77 else 0.25), (v, a, b, c[i], c[i + 1])
                for x, other in ((a, b), (b, a)):
                    sp = copy.deepcopy(SPEC)
                    sp["pulse"][K[x]["band"]] = SPEC["pulse"][K[other]["band"]]
                    cx = centroid(gm.render_batch(sp, [x], [v], 0.06, kn, sleep=False)[0], D[x]["f0"])
                    assert abs(cx / c[KEYS.index(x)] - 1) <= 0.10, (v, x, other, c[KEYS.index(x)], cx)


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
