# SPDX-License-Identifier: GPL-3.0-only
"""tools/gen_bars.py (VIBES PLAN §4.4, P2): the header is the §4.4 contract with bars.c, deterministic and up to date,
every word out of the ROM window and in int32, the Q-format bounds and the stability grid hold, one clean mallet
contact, the band pulse shapes stand for every key, the vibes.json schema and --strict."""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bars_model as gm  # noqa: E402
import gen_bars as gb    # noqa: E402

HDR_PATH = os.path.join(ROOT, "firmware", "gen", "bars_tables.h")
HDR = open(HDR_PATH).read()
SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
D = {d["midi"]: d for d in SPEC["design"]}
K = {k["midi"]: k for k in SPEC["keys"]}
FS = 44100.0


def arr(name):
    m = re.search(r"\b%s\b[^=]*= \{(.*?)\};\n" % re.escape(name), HDR, re.S)
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def test_defines_match_the_contract():
    want = dict(BAR_GEN_VERSION=1, BAR_KEY_LO=21, BAR_NKEY=88, BAR_NM=8, BAR_NFAM=5, BAR_NBAND=17, BAR_NLAYER=8,
                BAR_PLEN=64, BAR_NPOS=17, BAR_NTYPE=1, BAR_FADE_STEP=128)
    for k, v in want.items():
        assert re.search(r"#define %s %d\b" % (k, v), HDR), k
    assert "typedef" not in HDR                   # the structs live in bars.c


def test_array_sizes():
    assert len(arr("BAR_PULSE")) == 17 * 5 * 8 * 66
    assert len(arr("BAR_PRATE")) == 88 * 5 * 10
    assert len(arr("BAR_PAMP")) == 88 * 5 * 10
    assert len(arr("BAR_POS")) == 17 * 17 * 8
    for n in ("KN_FAM", "KN_MASS", "KN_MASSI", "KN_TUBE", "KN_NZ"):
        assert len(arr(n)) == 128, n
    assert len(arr("BAR_MOT_COS")) == 257 and len(arr("NZ_BQ")) == 10
    keys = re.search(r"BAR_KEYS\[BAR_NKEY\] = \{(.*?)\n\};", HDR, re.S).group(1)
    assert len(re.findall(r"-?\d+", keys)) == 88 * (8 * 7 + 8)


def test_sha256_footer():
    body, foot = HDR.rsplit("/* sha256(body) = ", 1)
    assert hashlib.sha256(body.encode()).hexdigest() == foot.split()[0]


def test_rom_window_and_int32():
    for v in (int(x) for x in re.findall(r"(?<![\w.])-?\d+(?![\w.])", HDR.rsplit("/* sha256", 1)[0])):
        assert -2 ** 31 <= v < 2 ** 32, v
        assert not (0xFFC00000 <= (v & 0xFFFFFFFF) <= 0xFFCFFFFF), v
    for k in SPEC["keys"]:
        for x in [k["gain"], k["kill"], k["k_bt"], k["k_tb"], k["wt"]] + [m["b"] for m in k["modes"]]:
            assert -2 ** 31 < x < 2 ** 31


def test_deterministic_and_up_to_date(tmp_path):
    """A fresh run (into a temporary directory) gives the committed header byte for byte."""
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_bars.py"), "--out", str(tmp_path)], check=True,
                   capture_output=True, cwd=ROOT)
    assert open(tmp_path / "bars_tables.h").read() == HDR


def test_pair_bounds():
    for k in SPEC["keys"]:
        assert abs(k["wt"]) <= 4 << 28                                  # |wt| <= 4.0 in Q28
        assert k["G"] in (1, 2, 4, 8, 16, 32, 64)                        # G up to 64 (lane B; the plan had 8)
        assert k["modes"][1]["b"] == 0 and k["modes"][1]["w"]["drel"] == k["modes"][1]["w"]["d"]
        assert k["k_bt"] > 0 and k["k_tb"] > 0                           # one sign: a symmetric spring
        assert 0 < k["life"] <= 65535


def test_life_covers_the_decay():
    for n, d in D.items():
        assert K[n]["life"] * 256 / FS >= 2 * d["t60_v1"] + 1.0 - 1e-6, n


def test_slot_and_sum_bounds_at_the_worst_voicing():
    """RUBBER v127 HAMMER 127 BELL 127, TONE 0 / 64 / 127: every slot's single-strike peak <= 2^25 (a x2 re-strike
    stays under 2^26), the voice sum (with the tube via wt) <= 2^28."""
    keys = list(range(21, 109, 3))
    for tone in (0, 64, 127):
        kn = dict(gm.DEFAULT_KNOBS, tone=tone, bark=127, hammer=127, bell=127)
        _, pk = gm.render_batch(SPEC, keys, [127] * len(keys), 0.5, kn, sleep=False, peaks=True)
        assert pk.max() <= 1.0 + 1e-9, (tone, pk.max())
        out = gm.render_batch(SPEC, keys, [127] * len(keys), 0.5, kn, sleep=False)
        g = np.array([K[n]["gain"] for n in keys]) / 16.0
        assert (np.abs(out).max(axis=1) / g).max() < 8.0          # rh_x <= 2^28 (8.0 real)


def test_stability_grid_every_key():
    """rho < 1 and both normal modes within +-2 % of the float design at every grid point of every key 21..108; the
    discrete coupling correction kc stays near 1 (with the tube tuned within a few cents, T4b: the old +100 c top
    made the pair anti-damping above D7, see gen_bars.KC_MAX)."""
    for d in SPEC["design"]:
        g = d["grid"]
        assert g["rho"] < 1.0, d["midi"]
        assert g["points"] == 270 and abs(g["v1_cents"]) <= 0.5
        assert g["decay_err"] <= 0.02, (d["midi"], g["decay_err"])
        assert 0.95 <= d["kc"] <= 1.1, (d["midi"], d["kc"])


def test_slot_x_off_and_pruned_slots_never_lit():
    """Slot X (the spare, T15) is off everywhere (th = 0, b = 0: bars.c never lights it); a pruned slot (vibes.json
    prune: under -60 dB re V1 at every voicing) has b = 0; every lit upper slot reaches -60 dB at some voicing."""
    thr = SPEC["vibes"]["prune"]["db"]
    for i, k in enumerate(SPEC["keys"]):
        assert k["modes"][7]["w"]["th"] == 0 and k["modes"][7]["b"] == 0, k["midi"]
        for s in SPEC["pruned"].get(str(k["midi"]), []):
            assert k["modes"][s]["b"] == 0 and not k["modes"][s]["present"], (k["midi"], s)
        for s in range(2, 7):
            if k["modes"][s]["b"]:
                assert SPEC["prune_rel_db"][i][s] >= thr, (k["midi"], s)


def test_weak_regime_every_key():
    s = SPEC["kn_tube"][127] / 32768
    for d in SPEC["design"]:
        assert s * d["sigma"] <= 0.9 * (d["zeta_r"] - d["zeta_n"]) + 1e-12, d["midi"]


def test_cap_every_mode():
    for d in SPEC["design"]:
        for m in d["modes"]:
            if m["f"] > 0:
                assert m["t60"] * m["f"] <= 7330.0 + 1e-6 and m["t60_rel"] <= m["t60"] + 1e-9
        assert d["felt_v1"] <= d["t1a"]


def test_one_clean_contact():
    for d in SPEC["design"]:
        for q, m in enumerate(d["mallet"]):
            assert max(m["ncont"]) == 1, (d["midi"], m["name"])


def test_contact_time_windows():
    fams = SPEC["vibes"]["T7c"]["families"]
    for n in range(53, 90):
        for q, fm in enumerate(fams):
            if "blend" in fm:                                     # the default mallet (MALLET 64): two laws blended
                continue
            m = D[n]["mallet"][q]
            tc = m["tc_ms"][1:9]                                  # the contact (T7f may play it faster: the cap)
            lo, hi = fm["tc_window_ms"]
            assert lo <= min(tc) and max(tc) <= hi, (n, fm["name"], min(tc), max(tc))
            cap = SPEC["vibes"]["T7f"]["f_tau_max"] / D[n]["f0"] * 1e3
            assert all(w <= min(t, cap) + 1e-3 for t, w in zip(tc, m["win_ms"][1:9])), (n, fm["name"])


def excitation(shape, prate_q12, freqs):
    """|spectrum| of a 64-point pulse played at prate (Q12 shape points per sample) at the given frequencies."""
    sh = np.array(shape[:64], float)
    step = prate_q12 / 4096.0
    t = np.arange(0, 64, step)
    F = np.interp(t, np.arange(64), sh)
    n = np.arange(len(F))
    return np.array([abs(np.sum(F * np.exp(-2j * np.pi * f * n / FS))) for f in freqs]), abs(np.sum(F))


def test_band_shape_error():
    """The band's pulse shape stands for each key's own (§4.4): <= 0.5 dB on V1..V3's excitation at v40 / v100 /
    v127 (layer blends), where the excitation is within 30 dB of the pulse's DC (below, it is inaudible)."""
    worst = 0.0
    for n in range(21, 109):
        b = K[n]["band"]
        rep = SPEC["band_rep"][b]
        fr = [D[n]["f0"], D[n]["modes"][0]["f"], D[n]["modes"][1]["f"]]
        for q in range(len(D[n]["mallet"])):
            own, band = D[n]["mallet"][q]["shapes"], D[rep]["mallet"][q]["shapes"]
            for v in (40, 100, 127):
                l, w = gm.vlayer(v)
                l1 = min(l + 1, 7)
                mix = lambda S: [x + (((y - x) * w) >> 15) for x, y in zip(S[l], S[l1])]  # noqa: E731
                ia, f = gm.vpos(v)
                pr = D[n]["mallet"][q]["prate"]
                p = pr[ia] + (((pr[ia + 1] - pr[ia]) * f) >> 8)
                eo, dco = excitation(mix(own), p, fr)
                eb, dcb = excitation(mix(band), p, fr)
                for a, c in zip(eo, eb):
                    if a > dco * 10 ** (-30 / 20):
                        worst = max(worst, abs(20 * np.log10(c / a)))
    assert worst <= 0.5, worst


def test_knob_tables():
    fam = SPEC["kn_fam"]
    assert [fam[v] for v in (0, 42, 64, 85, 127)] == [0, 1 << 8, 2 << 8, 3 << 8, 4 << 8]   # the UI's MALLET detents and
    # the default mallet (64)
    assert all(a <= b for a, b in zip(fam, fam[1:]))
    t = SPEC["kn_tube"]
    assert t[0] == 0 and t[64] == 32768 and abs(t[127] - 1.45 * 32768) <= 1
    assert all(a < b for a, b in zip(t, t[1:]))
    assert SPEC["kn_mass"][64] == 4096 and SPEC["kn_massi"][64] == 4096
    assert SPEC["kn_mass"][0] > 4096 > SPEC["kn_mass"][127]                     # heavier: a longer contact
    assert SPEC["kn_nz"][0] == 0 and SPEC["type"]["sleep_rel"] == 9 and SPEC["type"]["budget"] == 48
    c = SPEC["mot_cos"]
    assert c[0] == 32767 and c[256] == 0 and c[128] == round(32767 * np.cos(np.pi / 4))   # |cos(pi i / 512)|


def test_bar_pos_rows():
    for b, rows in enumerate(SPEC["bar_pos"]):
        r = np.array(rows)
        assert r.max() <= 16384 and r.min() >= 0
        assert r[0, 2] == 0                                       # TONE -64 (mid-bar): no V2
        assert np.all(r[:9, 5:7] == 0)                            # no torsion on the centreline (TONE <= 0)
        assert np.all(r[:, 1] == 0) and np.all(r[:, 7] == 0)      # TUBE and X are not struck


def test_vibes_schema_and_strict():
    vb = json.load(open(gb.VIBES))
    assert gb.check_vibes(vb, strict=True) == []
    for path, r in gb.rows_of(vb):
        assert r["src"] and r["status"] in gb.STATUSES, path
    bad = copy.deepcopy(vb)
    del bad["T9c"]["settle"]
    assert any("T9c" in e for e in gb.check_vibes(bad, strict=True))
    assert gb.check_vibes(bad, strict=False) == []
    bad["T7a"]["status"] = "GUESS"
    assert gb.check_vibes(bad, strict=False)


def test_spec_shape_for_the_scan_tools():
    """lane D: keys[{midi, modes[{name, f, t60}]}] for every key 21..108, as rhodes_spec.json."""
    assert [k["midi"] for k in SPEC["keys"]] == list(range(21, 109))
    for k in SPEC["keys"]:
        assert [m["name"] for m in k["modes"]] == gb.SLOT_NAMES
        for m in k["modes"]:
            assert "f" in m and "t60" in m and "present" in m
        assert abs(k["modes"][0]["f"] / (440 * 2 ** ((k["midi"] - 69) / 12)) - 1) < 1e-9
