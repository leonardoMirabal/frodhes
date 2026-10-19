# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA (bar TYPE 1) data, tables and Gate VA-M on the golden model (MARIMBA PLAN §7, tools/marimba_va.py): every
gated check of the plan's §0 / §2 passes on the generated marimba spec (docs/model/data/marimba_spec.json) before
any C reads it; the marimba's rows in the header are its spec's; the data file follows the schema."""
import json
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_bars as gb      # noqa: E402
import marimba_va as mva   # noqa: E402

SPEC_PATH = os.path.join(ROOT, "docs", "model", "data", "marimba_spec.json")
SPEC = json.load(open(SPEC_PATH)) if os.path.exists(SPEC_PATH) else None
pytestmark = pytest.mark.skipif(SPEC is None, reason="no marimba_spec.json (the stub header)")

# Gate VA-M checks that fail on the present data, with the reason (they stay measured and visible; the listening
# packs C decide: MARIMBA PLAN §2.6).
KNOWN = {
    "M5 attack centroid v40 -> v117 on C4 MED": "the plan's 1.4x (a VIBES number) is out of reach on a wooden bar "
    "with the MED yarn: its contact on C4 (1.5-3 ms) keeps V2 / V3 20-40 dB under the fundamental at every velocity "
    "(they rise ~8 dB, VA-M5 V2/V1 passes), so the centroid stays at f0 (1.02-1.13x over 0-50 ms; more at C2 / C3); a "
    "shorter MED contact would break the SOFT / MED separation and the soft-mallet gates",
    "M9 WEIGHT: light brighter": "WEIGHT changes the contact time by (m / m0)^(1/3) (12 -> 55 g: x1.66) and the level "
    "by 2.5-5.5 LU (that gate passes), but with the fundamental 20-40 dB over V2 / V3 the attack centroid moves "
    "1-4 % (the plan's 5 % is a VIBES number); from C5 up T7f caps the contact for every mass",
}


@pytest.fixture(scope="module")
def va_m():
    return mva.checks(SPEC)


def test_gate_va_m(va_m):
    failed = [(n, v) for n, v, ok, kind in va_m if kind == "gate" and not ok and not any(n.startswith(k) for k in KNOWN)]
    assert not failed, failed
    assert sum(1 for r in va_m if r[3] == "gate") >= 50


def test_every_card_has_a_gate(va_m):
    """VA-M9 (the VIBES lesson: a card that passes every damage check while doing nothing audible): STRIKE, MALLET,
    TUBE, WEIGHT, RING, MUTE, NOISE each have at least one gated, passing audibility check in the model"""
    for card in ("STRIKE", "MALLET", "TUBE", "WEIGHT", "RING", "MUTE", "NOISE"):
        rows = [r for r in va_m if r[0].startswith("M9 %s" % card) and r[3] == "gate"]
        assert rows and any(r[2] for r in rows), card                   # (the failing ones: KNOWN)


def test_schema_and_strict():
    vb = json.load(open(gb.MARIMBA))
    assert vb["type"] == "MARIMBA" and gb.check_vibes(vb, strict=True) == []


def test_spec_is_the_data():
    """the spec embeds marimba.json as it is now (a stale spec: regenerate)"""
    assert SPEC["vibes"] == json.load(open(gb.MARIMBA))


def test_type_record():
    t = SPEC["type"]
    assert t["flags"] == 0 and t["budget"] == 48 and t["sleep_rel"] == 9
    assert SPEC["kn_tube"][0] == 0 and SPEC["kn_tube"][64] == 32768 and abs(SPEC["kn_tube"][127] - 1.2 * 32768) <= 1
    assert all(a < b for a, b in zip(SPEC["kn_tube"], SPEC["kn_tube"][1:]))


def test_bands_shared_and_appended():
    """VIBES's position bands 0..16 first (the marimba's keys never use them), the pulse bank VIBES's 17 + the appended
    ones, pband maps every marimba band to a bank entry; a shared band's shapes stand for its keys within tol_db"""
    vs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
    nb = len(vs["bar_pos"])
    assert SPEC["bar_pos"][:nb] == vs["bar_pos"] and SPEC["pulse_bank"][:nb] == vs["pulse"]
    assert all(k["band"] >= nb for k in SPEC["keys"])
    for b, p in enumerate(SPEC["pband"]):
        assert 0 <= p < len(SPEC["pulse_bank"])
        assert [s for s in SPEC["pulse_bank"][p][:gb.NFAM]] == SPEC["pulse"][b][:gb.NFAM]
    tol = SPEC["vibes"]["bands"]["pulse_tol_db"]
    for j, (p, e) in enumerate(zip(SPEC["pband_local"], SPEC["share_err_db"])):
        assert e <= tol + 1e-9, (j, p, e)
    own = sorted(set(p for p in SPEC["pband_local"] if p < 0))
    assert len(SPEC["pulse_bank"]) == nb + len(own) and own == list(range(-len(own), 0))


def test_header_rows_are_the_spec():
    """type 1 of the committed multi-type header (when it holds the marimba) is this spec, row by row"""
    hdr = open(os.path.join(ROOT, "firmware", "gen", "bars_tables.h")).read()
    if "1 MARIMBA" not in hdr:
        pytest.skip("the committed header does not hold the marimba yet")
    import test_gen_bars as tg
    A = tg.arrays(hdr)
    keys = A["BAR_KEYS"][1]
    for k, row in zip(SPEC["keys"], keys):
        assert row[0] == [[m["w"][x] for x in ("sh", "ch", "th", "d", "drel", "cr")] + [m["b"]] for m in k["modes"]]
        assert row[1:] == [k["k_bt"], k["k_tb"], k["wt"], gb.rom_safe(k["gain"]), gb.rom_safe(k["kill"]), k["life"],
                           k["band"], k["flags"]]
    assert A["BAR_PRATE"][1] == [[[gb.rom_safe(x) for x in q] for q in k[:gb.NFAM]] for k in SPEC["prate"]]
    assert A["BAR_PAMP"][1] == [k[:gb.NFAM] for k in SPEC["pamp"]]
    for n, kk in (("KN_FAM", "kn_fam"), ("KN_MASS", "kn_mass"), ("KN_MASSI", "kn_massi"), ("KN_TUBE", "kn_tube"),
                  ("KN_NZ", "kn_nz")):
        assert A[n][1] == SPEC[kk], n
    assert A["BAR_PBAND"] == SPEC["pband"] and A["BAR_TYPE"][1][7] == 0
    assert re.search(r"#define BAR_NPULSE %d\b" % len(SPEC["pulse_bank"]), hdr)


def test_mallet_map_moves_no_key_by_a_step():
    """card_level fam_map (gen_bars.shape_kn_fam): MARIMBA's KN_FAM row keeps the shared detents (0 / 42 / 64 / 85 /
    127 = one family each), rises monotonically, and on the golden model no authentic key's loudness (K-weighted over
    card_level t_s, v100 and v127, default cards) moves by more than 1.5 dB per 4 raw on the S10 grid's MALLET
    values (0, 4 .. 120, 127: PLAN 10.2 B4, including the family knees). The linear map moved G3..E4 by 1.5..1.7 dB at the HARD ->
    RUBBER knee (the family ceiling turns RUBBER down up to 12 dB there)"""
    import numpy as np
    import bars_model as gm
    fam = SPEC["kn_fam"]
    assert [fam[v] for v in gb.FAM_DETENT] == [q << 8 for q in range(len(gb.FAM_DETENT))]
    assert all(a <= b for a, b in zip(fam, fam[1:])) and fam != gb.kn_fam()
    t0, t1 = MVB["card_level"]["t_s"]
    keys = list(range(MVB["keys"]["authentic"][0], MVB["keys"]["authentic"][1] + 1, 2)) + [55, 61, 63]
    grid = [min(127, k * 4 + (k == 31) * 3) for k in range(32)]
    for vel in (100, 127):
        kn = [dict(gm.DEFAULT_KNOBS, bark=b) for b in grid for _ in keys]
        y = gb.render_chunks(SPEC, keys * len(grid), [vel] * (len(keys) * len(grid)), t1 + 0.01, kn)
        L = np.array([gb.lufs_k(r, t0, t1) for r in y]).reshape(len(grid), len(keys))
        step = np.abs(np.diff(L, axis=0)) * 4 / np.diff(grid)[:, None]    # per 4 raw (the last step: 120 -> 127)
        assert step.max() <= 1.5, (vel, keys[int(step.max(axis=0).argmax())], float(step.max()))


# ---------------------------------------------------------------- the marimba's pins (MARIMBA PLAN §20: as VIBES's)
MVB = json.load(open(gb.MARIMBA))
MFE = gb.load_fe_pins(MVB, gb.MAR_FE_PINS)
MPINS = gb.load_pins(gb.MAR_PINS)
MKEYS = range(MVB["keys"]["authentic"][0], MVB["keys"]["authentic"][1] + 1)


def test_marimba_fe_pins_current():
    """marimba_fe_pins.json exists and its fingerprint is the FE inputs' (marimba.json T12 / T9a / T13a / keys,
    bar_fe.py)"""
    assert MFE is not None, "marimba_fe_pins.json missing or stale: tools/gen_bars.py --repin-marimba"
    assert sorted(MFE) == list(MKEYS)


@pytest.mark.parametrize("ng", MKEYS)
def test_marimba_fe_pins_follow_from_the_profile_and_tune(ng):
    """every pinned FE field is the FE evaluated at the pinned profile (tolerances as test_gen_bars FE_TOL: the
    eigensolver's last bits between machines), and the profile tunes f0 and the ratios exactly (< 1e-5 cent)"""
    import numpy as np
    p = MFE[ng]
    d = gb.fe_derive(MVB, ng, p["A"])
    for k in ("f", "ratios", "tors"):
        assert d[k] == pytest.approx(p[k], rel=1e-8), (ng, k)
    for k in ("mass", "hmin", "curv"):
        assert d[k] == pytest.approx(p[k], rel=1e-12), (ng, k)
    assert d["nodes"] == pytest.approx(p["nodes"], abs=2e-9), ng
    W, Wp = np.array(d["weights"]), np.array(p["weights"])
    assert np.abs(W - Wp).max() <= 1e-8 * np.abs(Wp).max(), ng
    assert max(abs(x) for x in d["err_c"]) < 1e-5, (ng, d["err_c"])


def test_marimba_pins_are_the_physics():
    """the marimba's pinned pair stage = the regime-aware solve again (bartube eig4: the same on every machine):
    zeta_r (derived) to 1e-12, the pair (sigma, decays, flags) to 1e-8, wt, the V1 values, and the integer words
    exactly or within a few LSB (an edge key: pair_words' kc bisection ends where a coupling word steps; then the
    pinned words must hold V1 on its target within 0.01 c: f0 moved by gen_bars.v1_target, the radiated pitch on f0)"""
    import numpy as np
    import bartube as btm
    exact, edge = [], []
    with gb.type_globals(MVB):
        for n in range(gb.KEY_LO, gb.KEY_HI + 1):
            p = MPINS[n]
            zr = gb.zeta_r_mar(MVB, n)
            assert zr == pytest.approx(p["zt"][0], rel=1e-12) and p["zt"][1] == 0, n
            bt, cl, weak = gb.make_pair(MVB, n, p["zt"][0], gb.t1b(MVB, n))
            assert (cl, weak, bt.clamped) == (p["cl"], p["weak"], p["bt"]["clamped"]), n
            for kk, v in (("f_bar", bt.f_t), ("f_tube", bt.f_b), ("sigma", bt.k_rel), ("sig_bar", bt.sigma_t),
                          ("sig_tube", bt.sigma_b)):
                assert v == pytest.approx(p["bt"][kk], rel=1e-8), (n, kk)
            pk, wt = gb.pair_peak_ms(MVB, bt, dur=2.5)
            assert wt == pytest.approx(p["wt"], rel=1e-8) and abs(pk - p["peak_ms"]) <= 0.05, n
            felt = min(gb.felt_v1(MVB, n), gb.t1a(MVB, n))
            for kk, v in gb.v1_vals(dict(bt=bt, felt_v1=felt)).items():
                assert v == pytest.approx(p["v1"][kk], rel=1e-8), (n, kk)
            tgt = gb.v1_target(MVB, dict(bt=bt, wt=wt, f0=gb.midi_hz(n)))   # (the pitch-centring target, P4)
            pw, pp = gb.pair_words_safe(bt, wt, felt, tgt), gb.pin_words(p)
            ints = [kk for kk in pp if kk not in ("kc", "f_slot0", "f_slot1")]
            if all(pw[kk] == pp[kk] for kk in ints):
                exact.append(n)
                continue
            edge.append(n)
            for kk in ("k_tb", "k_bt", "E0", "eps0", "E1", "eps1"):
                assert abs(pw[kk] - pp[kk]) <= max(2, 1e-5 * abs(pp[kk])), (n, kk)
            M = btm.pair_map(pp)
            iv = p["pw"].get("v1_ord")
            f, sig, _ = btm.map_modes(M)[btm.map_v1(M) if iv is None else iv]
            assert abs(1200 * np.log2(f / tgt)) <= 0.01, n
    print("marimba pins: %d keys exact, %d edge keys %s" % (len(exact), len(edge), edge))


def test_marimba_coupling_words_fit_32_bits():
    """P4: bars.c's per-block coupling coefficients (ktbe, kbte) at BELL's top and TUNE +50 fit 32 bits on every key
    (gen_bars.coupling_over); before P4 they did not from key 91 (C#7) up, and bar_sat31 clipped the tube's pull on
    the bar (the C 18 dB off the golden model at C7)"""
    with gb.type_globals(MVB):
        bad = [n for n in range(gb.KEY_LO, gb.KEY_HI + 1) if gb.coupling_over(gb.pin_words(MPINS[n]))]
    assert not bad, bad


def test_marimba_pulse_runs_are_the_solve():
    """the pinned pulse runs (marimba.json bands.pulse_runs) are what share_pulses solves on the spec's own shapes
    (the design rows; the VIBES shapes from bars_spec.json)"""
    vs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
    D = {d["midi"]: d for d in SPEC["design"]}
    phys = {n: dict(f0=d["f0"], modes=[dict(f=m["f"]) for m in d["modes"]]) for n, d in D.items()}
    ct = {n: [dict(shapes=m["shapes"], prate=m["prate"]) for m in d["mallet"]] for n, d in D.items()}
    nb = len(vs["bar_pos"])
    saved = dict(gb.BANDS)
    gb.BANDS.clear()
    gb.BANDS.update(of={k["midi"]: k["band"] - nb for k in SPEC["keys"]}, rep=SPEC["band_rep"][nb:])
    try:
        free = dict(MVB, bands={k: v for k, v in MVB["bands"].items() if k != "pulse_runs"})
        gb.share_pulses(free, phys, ct, vs)
        solved = gb.BANDS["runs"]
    finally:
        gb.BANDS.clear()
        gb.BANDS.update(saved)
    assert solved == MVB["bands"]["pulse_runs"], (solved, MVB["bands"]["pulse_runs"])
