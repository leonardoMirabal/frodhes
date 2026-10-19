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
# the VIBES-only header (tools/gen_bars.py --vibes-only, BAR_NTYPE 1): the 0.9.3 tables, byte for byte (Gate V, MARIMBA
# PLAN §8.1 item 1). Never changes: VIBES's tables are frozen.
VIBES_ONLY_SHA = "8d2ee43dcc41a608384c77a5226beafe616854a892b964b93048ad144229349e"


def defines(txt):
    return {k: int(v) for k, v in re.findall(r"#define (\w+) (-?\d+)\b", txt)}


NT = defines(HDR)["BAR_NTYPE"]                   # 1: the VIBES-only form; 2..: one row per bar TYPE (§8.2)


def arrays(txt):
    """every 'static const T NAME[...] = {...};' of a header as nested int lists"""
    out = {}
    for name, body in re.findall(r"static const \w+ (\w+)\[[^=]*= \{(.*?)\};", txt, re.S):
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        j = re.sub(r",\s*\]", "]", ("{" + body + "}").replace("{", "[").replace("}", "]"))
        out[name] = json.loads(j)
    return out


def arr(name):
    m = re.search(r"static const \w+ %s\b[^=]*= \{(.*?)\};" % re.escape(name), HDR, re.S)   # (not a comment)
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


PER_TYPE = ("BAR_KEYS", "BAR_PRATE", "BAR_PAMP", "KN_MASS", "KN_MASSI", "KN_TUBE", "KN_NZ", "BAR_BODY", "BAR_TYPE")
BANDED = ("BAR_PULSE", "BAR_POS", "BAR_POSG", "BAR_TUBEG")


def check_vibes_rows(multi, single):
    """MARIMBA PLAN §8.1 item 1: type 0 of the multi-type header is the VIBES-only header, field by field: BAR_KEYS[0],
    BAR_PRATE[0], BAR_PAMP[0], KN_MASS / KN_MASSI / KN_TUBE / KN_NZ [0], BAR_BODY[0], BAR_TYPE[0] (its first seven
    fields; flags = motor + damper), bands 0..16 of BAR_PULSE / BAR_POS / BAR_POSG / BAR_TUBEG (BAR_PBAND the identity
    there), KN_FAM, NZ_BQ, BAR_MOT_COS and every define but the counts. A stub type (a copy of VIBES, §8.1 item 4)
    equals type 0 in every per-type row. Returns the parsed multi-type header."""
    A, S = arrays(multi), arrays(single)
    dA, dS = defines(multi), defines(single)
    assert set(A) - {"BAR_RINGG"} == set(S) | {"BAR_PBAND"}       # (BAR_RINGG: a later type's keys with flag bit4)
    assert {k: v for k, v in dA.items() if k not in ("BAR_NTYPE", "BAR_NBAND", "BAR_NPULSE", "BAR_RINGG_ROWS")} == \
        {k: v for k, v in dS.items() if k not in ("BAR_NTYPE", "BAR_NBAND")}
    assert dS["BAR_NTYPE"] == 1 and dA["BAR_NTYPE"] >= 2
    for n in PER_TYPE:
        assert len(A[n]) == dA["BAR_NTYPE"], n
    for n in ("BAR_KEYS", "BAR_PRATE", "BAR_PAMP", "KN_MASS", "KN_MASSI", "KN_TUBE", "KN_NZ"):
        assert A[n][0] == S[n], n
    assert A["BAR_BODY"][0] == S["BAR_BODY"][0]
    assert A["BAR_TYPE"][0][:7] == S["BAR_TYPE"][0] and A["BAR_TYPE"][0][7] == 3 and len(A["BAR_TYPE"][0]) == 9
    nb = dS["BAR_NBAND"]
    for n in BANDED:
        assert A[n][:nb] == S[n], n
    assert len(A["BAR_PULSE"]) == dA["BAR_NPULSE"] and len(A["BAR_POS"]) == dA["BAR_NBAND"] == len(A["BAR_PBAND"])
    assert A["BAR_PBAND"][:nb] == list(range(nb)) and max(A["BAR_PBAND"]) < dA["BAR_NPULSE"]
    for n in ("KN_FAM", "NZ_BQ", "BAR_MOT_COS"):
        assert A[n] == S[n], n
    if "STUB" in multi.split("*/", 3)[2]:                 # the header's type list names the stub
        for n in PER_TYPE:
            assert A[n][1] == A[n][0], n
    return A


def test_defines_match_the_contract():
    want = dict(BAR_GEN_VERSION=1, BAR_KEY_LO=21, BAR_NKEY=88, BAR_NM=8, BAR_NFAM=5, BAR_NLAYER=8,
                BAR_PLEN=64, BAR_NPOS=17, BAR_FADE_STEP=128)
    d = defines(HDR)
    for k, v in want.items():
        assert d[k] == v, k
    assert d["BAR_NBAND"] >= 17 and d.get("BAR_NPULSE", 17) >= 17
    assert "typedef" not in HDR                   # the structs live in bars.h


def test_array_sizes():
    d = defines(HDR)
    assert len(arr("BAR_PULSE")) == d.get("BAR_NPULSE", 17) * 5 * 8 * 66
    assert len(arr("BAR_PRATE")) == NT * 88 * 5 * 10
    assert len(arr("BAR_PAMP")) == NT * 88 * 5 * 10
    assert len(arr("BAR_POS")) == d["BAR_NBAND"] * 17 * 8
    assert len(arr("KN_FAM")) == 128
    for n in ("KN_MASS", "KN_MASSI", "KN_TUBE", "KN_NZ"):
        assert len(arr(n)) == 128 * NT, n
    assert len(arr("BAR_MOT_COS")) == 257 and len(arr("NZ_BQ")) == 10
    keys = re.search(r"BAR_KEYS\[[^=]*= \{(.*?)\n\};", HDR, re.S).group(1)
    assert len(re.findall(r"-?\d+", keys)) == NT * 88 * (8 * 7 + 8)


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
    """A fresh run (into a temporary directory) gives the committed header byte for byte, on any machine (the pair
    stage pinned, docs/model/data/bars_pins.json; the rest machine-independent). bars_spec.json's report floats may
    move by ~1e-14 relative off the recording machine (libm / FFT last bits), never a header word. One run writes both
    forms (--both): the VIBES-only header is the 0.9.3 file (VIBES_ONLY_SHA), every VIBES row of the multi-type
    header equals it field by field (check_vibes_rows), and the committed header is whichever form it is (the
    VIBES-only one until bars.c reads the per-type rows)."""
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_bars.py"), "--both", "--out", str(tmp_path)],
                   check=True, capture_output=True, cwd=ROOT)
    single = open(tmp_path / "bars_tables_vibes.h").read()
    multi = open(tmp_path / "bars_tables.h").read()
    assert hashlib.sha256(single.encode()).hexdigest() == VIBES_ONLY_SHA
    check_vibes_rows(multi, single)
    assert HDR == (single if NT == 1 else multi)


def test_vibes_rows_checker_catches_a_change():
    """check_vibes_rows fails on a changed VIBES word (a guard against a vacuous parser): the committed multi-type
    header against itself cut down to its type 0 (the parser's own VIBES-only form) passes, and fails with one VIBES
    BAR_PAMP word moved"""
    if NT == 1:
        pytest.skip("the committed header is the VIBES-only form")
    a = arrays(HDR)
    assert len(a["BAR_KEYS"]) == NT and len(a["BAR_KEYS"][0]) == 88 and a["BAR_TYPE"][0][:7] == [0, 6554, 32767, 0, 0, 9, 48]
    k = HDR.index("static const uint16_t BAR_PAMP")
    m = re.search(r"\{\{(\d+),", HDR[k:])
    bad = HDR[:k + m.start(1)] + str(int(m.group(1)) + 1) + HDR[k + m.end(1):]
    single = arrays(HDR)
    assert arrays(bad)["BAR_PAMP"][0] != single["BAR_PAMP"][0]


PINS = gb.load_pins()


def _map_v1(w):
    import bartube as btm
    M = btm.pair_map(w)
    return btm.map_modes(M)[btm.map_v1(M)]


def test_pinned_zeta_r_is_the_physics():
    """The pinned tube zeta_r of every key = gen_bars.tube_zetas solved again (eig4): the flags exactly, the value
    within one step of solve_zeta's 24-step geometric bisection (1e-6 relative)."""
    zt = gb.tube_zetas(gb.load_vibes())
    for n in range(gb.KEY_LO, gb.KEY_HI + 1):
        assert zt[n][1] == PINS[n]["zt"][1] and zt[n][0] == pytest.approx(PINS[n]["zt"][0], rel=1e-6), n


def test_pins_are_the_physics():
    """Honesty check of the pins: the pair stage solved again from the pinned zeta_r with tools/eig4.py (decimal
    eig, the same on every machine) agrees with every pin: the floats within 1e-8 relative, the flags exactly, and the
    integer words exactly, or (an EDGE key: pair_words' kc bisection ends on the edge where a coupling word steps,
    so the last-bit eig of the recording machine chose the neighbouring words) the pinned words are an equally good
    solution, checked with eig4 on their exact integer map: V1 within 0.01 cent of f0 and its decay within 2e-4 of
    the design's, kc within 1e-5 and every word within a few LSB of the fresh solve. Prints the counts."""
    vb = gb.load_vibes()
    exact, edge = [], []
    for n in range(gb.KEY_LO, gb.KEY_HI + 1):
        p = PINS[n]
        zr = p["zt"][0]
        bt, cl, weak = gb.make_pair(vb, n, zr, gb.t1b(vb, n))
        assert (cl, weak, bt.clamped) == (p["cl"], p["weak"], p["bt"]["clamped"]), n
        for kk, v in (("f_bar", bt.f_t), ("f_tube", bt.f_b), ("sigma", bt.k_rel), ("sig_bar", bt.sigma_t),
                      ("sig_tube", bt.sigma_b), ("sigma_wanted", bt.sigma_wanted)):
            assert v == pytest.approx(p["bt"][kk], rel=1e-8, nan_ok=True), (n, kk)
        pk, wt = gb.pair_peak_ms(vb, bt, dur=2.5)
        assert wt == pytest.approx(p["wt"], rel=1e-8) and abs(pk - p["peak_ms"]) <= 0.05, n
        felt = min(gb.felt_v1(vb, n), gb.t1a(vb, n))
        k = dict(bt=bt, felt_v1=felt)
        for kk, v in gb.v1_vals(k).items():
            assert v == pytest.approx(p["v1"][kk], rel=1e-8), (n, kk)
        pw, pp = gb.pair_words_safe(bt, wt, felt, gb.midi_hz(n)), gb.pin_words(p)
        ints = [kk for kk in pp if kk not in ("kc", "f_slot0", "f_slot1")]
        if all(pw[kk] == pp[kk] for kk in ints):
            exact.append(n)
            assert pw["kc"] == pytest.approx(pp["kc"], rel=1e-8), n
            continue
        edge.append(n)
        assert pp["G"] == pw["G"] and pw["kc"] == pytest.approx(pp["kc"], rel=1e-5), n
        assert pw["f_slot0"] == pytest.approx(pp["f_slot0"], rel=2e-6), n
        for kk in ("k_tb", "k_bt", "E0", "eps0", "E1", "eps1"):
            assert abs(pw[kk] - pp[kk]) <= max(2, 1e-5 * abs(pp[kk])), (n, kk, pw[kk], pp[kk])
        for s in ("w0", "w1"):
            for kk in pp[s]:
                assert abs(pw[s][kk] - pp[s][kk]) <= max(2, 2e-6 * abs(pp[s][kk])), (n, s, kk, pw[s][kk], pp[s][kk])
        f, sig, _ = _map_v1(pp)
        assert abs(1200 * np.log2(f / gb.midi_hz(n))) <= 0.01, n
        assert abs(sig / bt.v1()[1] - 1) <= 2e-4, (n, sig, bt.v1()[1])
    print("pins: %d keys exact, %d edge keys %s" % (len(exact), len(edge), edge))
    assert len(exact) + len(edge) == gb.NKEY


VB = gb.load_vibes()
FE_PINS = gb.load_fe_pins(VB)
FE_KEYS = range(VB["keys"]["authentic"][0], VB["keys"]["authentic"][1] + 1)
# The pinned FE recomputed at its profile is bit for bit the same on the recording Mac. Elsewhere the eigensolver's last
# bits differ: the Timoshenko K, M (242 dofs) are conditioned ~1e7, so eigh's relative error is ~eps x 1e7; measured
# on GitHub's Apple M1, Neoverse-N2 (Linux arm64) and Xeon 8370C (Linux x86_64): frequencies 1.7e-9 relative, err_c
# 4.1e-6 cent, nodes 3.6e-10 of L, weights 8.8e-10 of the key's largest. The tolerances are ~6x those; a different
# profile (the one GitHub's least_squares found, ~1 um away) moves the weights by 1e-4..1e-1, far outside them.
FE_TOL = dict(eig=1e-8, cents=2e-5, nodes=2e-9, weight=1e-8, exact=1e-12)
FE_ALPHA = 0.02                                           # bar_fe.tune's curvature weight (fe_key keeps the default)
FE_LOCAL = 5e-5                                           # the penalised optimum: no probe lowers its cost by more


def test_fe_pins_are_current():
    """bars_fe_pins.json exists and its fingerprint is the FE inputs' (vibes.json T12 / T9a / T13a / keys, bar_fe.py)."""
    assert FE_PINS is not None, "bars_fe_pins.json missing or stale: tools/gen_bars.py --repin"
    assert sorted(FE_PINS) == list(FE_KEYS)
    for ng in FE_KEYS:
        q = gb.fe_setup(VB, ng)
        assert FE_PINS[ng]["ng"] == ng and (FE_PINS[ng]["L"], FE_PINS[ng]["w"]) == pytest.approx((q["L"], q["w"]), rel=1e-12)


@pytest.mark.parametrize("ng", FE_KEYS)
def test_fe_pins_follow_from_the_profile(ng):
    """Every pinned field is the FE model evaluated at the pinned profile A (forward: bar_fe's Timoshenko bending,
    Saint-Venant torsion, the strike weights at the 17 points), within the eigensolver's accuracy (FE_TOL); mass,
    h_min and curvature (no eigensolver) to 1e-12."""
    p = FE_PINS[ng]
    d = gb.fe_derive(VB, ng, p["A"])
    assert d["A"] == p["A"]
    for k in ("f", "ratios", "tors"):
        assert d[k] == pytest.approx(p[k], rel=FE_TOL["eig"]), (ng, k)
    for k in ("mass", "hmin", "curv"):
        assert d[k] == pytest.approx(p[k], rel=FE_TOL["exact"]), (ng, k)
    assert d["err_c"] == pytest.approx(p["err_c"], abs=FE_TOL["cents"]), ng
    assert d["nodes"] == pytest.approx(p["nodes"], abs=FE_TOL["nodes"]), ng
    W, Wp = np.array(d["weights"]), np.array(p["weights"])
    assert W.shape == Wp.shape and np.abs(W - Wp).max() <= FE_TOL["weight"] * np.abs(Wp).max(), ng


def _fe_resid(ng, A, alpha):
    """bar_fe.tune's residual (cents to f0 and the tuned ratios, + alpha x the curvature term)."""
    import bar_fe as fe
    q = gb.fe_setup(VB, ng)
    bar = fe.Bar(q["L"], q["w"], q["t"], fe.cos_profile(A, q["L"], q["t"], q["h_min"]), **q["mat"])
    tg = np.array([q["f0"]] + [q["f0"] * x for x in q["ratios"]])
    f, _ = bar.bending(len(tg))
    r = 1200 * np.log2(f / tg)
    return np.concatenate([r, [alpha * fe._curvature(A, q["L"])]]) if alpha else r


def _fe_probe(ng, A, h=1e-6, seed=0):
    """The largest relative cost decrease of tune's penalised objective over probes A + s d around A (forward
    evaluations only): d = the 4 axes, the eigenvectors of the cost's finite-difference Hessian (step h, the valley
    directions) and 16 fixed random unit vectors, both signs, s = 0.1, 1 and 10 um."""
    cost = lambda a: (lambda r: 0.5 * float(r @ r))(_fe_resid(ng, a, FE_ALPHA))
    c0 = cost(A)
    E = np.eye(4) * h
    H = np.array([[cost(A + E[i] + E[j]) - cost(A + E[i] - E[j]) - cost(A - E[i] + E[j]) + cost(A - E[i] - E[j])
                   for j in range(4)] for i in range(4)]) / (4 * h * h)
    R = np.random.default_rng(seed).standard_normal((16, 4))
    D = np.vstack([np.eye(4), np.linalg.eigh(H)[1].T, R / np.linalg.norm(R, axis=1)[:, None]])
    return max((c0 - cost(A + sg * s * d)) / c0 for d in D for sg in (1, -1) for s in (1e-7, 1e-6, 1e-5))


@pytest.mark.parametrize("ng", FE_KEYS)
def test_fe_pins_are_a_solution_of_the_tuning(ng):
    """Each pinned profile solves bar_fe.tune's problem, checked forward (no re-optimisation): its thinnest point at or
    above h_min, and either
    - tuned exactly: |err| < 1e-5 cent on f0 and every tuned ratio, the minimum (0) of tune's polish objective. The
      profile has more parameters than targets, so the exact solutions form a family: the pin is one member (why it
      is pinned); or
    - the penalised optimum tune keeps when it is within 0.05 cent (no polish): |err| <= 0.05 cent, and no probe
      within 10 um (_fe_probe) lowers the penalised cost by more than FE_LOCAL (5e-5) of itself. The worst seen is
      1.7e-5 (key 78; the profile's clip to the stock thickness t towards the ends makes the cost non-smooth, so a
      least_squares stop is a local optimum only to that level); keys 68..77 at most 6e-7."""
    A = np.array(FE_PINS[ng]["A"], float)
    q = gb.fe_setup(VB, ng)
    assert q["h_min"] <= FE_PINS[ng]["hmin"] < q["t"], ng
    err = np.abs(_fe_resid(ng, A, 0)).max()
    if err < 1e-5:
        return
    assert err <= 0.05, (ng, err)
    dec = _fe_probe(ng, A)
    print("key %d: penalised optimum, |err| %.3f cent, best probe decrease %.1e" % (ng, err, dec))
    assert dec < FE_LOCAL, (ng, dec)


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
            t8, xv = SPEC["vibes"]["T8"], SPEC["vibes"].get("T7g", {}).get("vel_exp", 0.0)
            spd = lambda v: t8["v_lo"] * (t8["v_hi"] / t8["v_lo"]) ** ((v - 1) / 126.0)
            fv = [(spd(v) / spd(100)) ** (-xv) for v in gm.V_ANCH[1:9]]   # T7g: velocity's own contact scale
            assert all(w <= min(t * f, cap) + 1e-3 for t, w, f in zip(tc, m["win_ms"][1:9], fv)), (n, fm["name"])


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
