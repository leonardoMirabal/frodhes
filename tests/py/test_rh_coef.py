# SPDX-License-Identifier: GPL-3.0-only
"""rhodes.c rh_coef (the resonator coefficients at note-on and on a bend; VIBES / MARIMBA call it too) against a
double-precision reference of its own formula: every mode of every key (RH_KEYS, BAR_KEYS) x TUNE -200..+200 cents
(1-cent steps) and the pitch bend's +-2 semitones on top of TUNE -50 / 0 / +50 (rh_tune_now, engine.c ENG_BEND_T) x
five DECAY / RELEASE k (DECAY_K), both decays (d, drel); VIBES / MARIMBA only where bars.c can go (reach). The C
function itself runs (copied out of rhodes.c into tests/c/rh_coef.c), so the test sees the firmware's integers.

The cr term (cr (1 - 2t + 3t^2), cr = 1 / (8 sin^2(theta / 2)) in Q16) exceeds int32 for the lowest modes tuned
down (A0 V1 from -6 cents, A#0 V1 from -106, B0 V1 from -212; VIBES A0, MARIMBA A0's tube): it used to wrap, which
moved those modes' release frequency (A0 V1: 11 cents at RELEASE 64, up to 200 at the fastest release; MARIMBA A0
tube's mute: 16 semitones from TUNE -23). Checked: the resonator's frequency from (E, eps) (the MC3 recursion's pole
angle) is within 0.1 cent of the reference formula's everywhere (0.07 at most: the Q30 rounding, the same at TUNE 0),
E within 1e-4, eps within 1e-4 (or 2 LSB); tools/rh_coefs.py coef (the golden model's copy) is bit-identical to the C;
no UB on the whole grid (UBSan). cr and corr are int64 now; at TUNE >= 0 cr <= the table's word and corr < 1.5
(test_corr_fits), so there the integers are the old int32 ones (Gate R: every render byte-identical).

  python tests/py/test_rh_coef.py [--before REV]   the cents report: this rhodes.c against REV's (default HEAD)"""
import os
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import rh_coefs  # noqa: E402
import table_ab  # noqa: E402

OUT = os.path.join(ROOT, "build", "rh_coef")
Q30, Q16 = float(1 << 30), float(1 << 16)
MODE_F = table_ab.MODE_F                                   # sh ch th d drel cr b


def core(src):
    """rh_coef's text out of rhodes.c, its parts first (rh_coef_eps's signature to rh_coef's closing brace at column 0)"""
    a = src.index("static __attribute__((noinline)) int32_t rh_coef_eps(")
    return src[a:src.index("\n}\n", src.index("static void rh_coef(const rh_mode_t *m")) + 3]


def build(src_text, tag, extra=()):
    os.makedirs(OUT, exist_ok=True)
    c = os.path.join(OUT, f"core_{tag}.c")
    open(c, "w").write(core(src_text))
    exe = os.path.join(OUT, f"rh_coef_{tag}")
    subprocess.run(["cc", "-O2", "-Wall", *extra, "-I" + os.path.join(ROOT, "firmware", "src"),
                    f'-DRH_COEF_CORE="{c}"', "-o", exe, os.path.join(ROOT, "tests", "c", "rh_coef.c")], check=True)
    return exe


def modes():
    """every live mode word set (th != 0): RH_KEYS (5 per key) and BAR_KEYS (8 slots per key per type)"""
    rt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h"))
    bt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "bars_tables.h"))
    out, names = [], []
    rk = rt["RH_KEYS"]["vals"]
    per = len(rk) // 88
    for k in range(88):
        for m in range(5):
            w = rk[k * per + 7 * m: k * per + 7 * m + 7]
            if w[2]:
                out.append(w); names.append(f"{table_ab.note_name(21 + k)} {['V1', 'V2', 'O1', 'O2', 'X'][m]}")
    bk = bt["BAR_KEYS"]["vals"]
    per = len(table_ab.KEY_F)
    for i in range(len(bk) // per):
        for s in range(8):
            w = bk[i * per + 7 * s: i * per + 7 * s + 7]
            if w[2]:
                out.append(w); names.append(f"bar{i // 88} {table_ab.note_name(21 + i % 88)} {table_ab.SLOTS[s]}")
    return np.array(out, dtype=np.int64), names


def t_grid():
    """Q30 2^(c/1200) - 1: c = -200..+200 cents, and rh_tune_now(TUNE -50 / 0 / +50, every bend step of 1/4 the
    ENG_BEND_T interval) as the firmware forms it"""
    rt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h"))
    tune = rt["TUNE_T"]["vals"]
    eng = open(os.path.join(ROOT, "firmware", "src", "engine.c")).read()
    bend = [int(x) for x in re.search(r"ENG_BEND_T\[17\] = \{[^\n]*\n(.*?)\};", eng, re.S).group(1).replace(
        "\n", " ").split(",")]
    ts = {int(round((2 ** (c / 1200) - 1) * Q30)): f"{c:+d} c" for c in range(-200, 201)}
    for tu in (-50, 0, 50):
        for q in range(0, 16384, 256):
            i, f = q >> 10, q & 1023
            b = bend[i] + ((bend[i + 1] - bend[i]) * f >> 10)
            t = tune[tu + 50]
            ts.setdefault(t + b + (t * b >> 30), f"TUNE {tu:+d} bend {q - 8192:+d}")
    return np.array(sorted(ts), dtype=np.int64), ts


def k_grid():
    rt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h"))
    dk = rt["DECAY_K"]["vals"]
    return np.array(sorted({dk[0], dk[32], 65536, dk[96], dk[127]}), dtype=np.int64)


def run_c(exe, W, T, K):
    hdr = np.array([len(W), len(T), len(K)], dtype=np.int32)
    inp = hdr.tobytes() + W.astype(np.int32).tobytes() + T.astype(np.int32).tobytes() + K.astype(np.int32).tobytes()
    o = np.frombuffer(subprocess.run([exe], input=inp, capture_output=True, check=True).stdout, dtype=np.int32)
    o = o.reshape(len(W), len(T), len(K), 2, 2)                # mode, t, k, (d, drel), (E, eps)
    return o[..., 0].astype(np.float64), o[..., 1].astype(np.float64)


def ref(W, T, K):
    """rh_coef's formula in doubles, from the same table words (E, eps as Q30 reals), shaped as run_c's"""
    w = {f: W[:, i].astype(np.float64)[:, None, None, None] for i, f in enumerate(MODE_F)}
    t = (T / Q30)[None, :, None, None]
    k = (K / Q16)[None, None, :, None]
    d = np.concatenate([w["d"], w["drel"]], axis=3) / Q30            # (mode, 1, 1, 2)
    a = k * (k - 1); c = a * (k - 2)
    dp = k * d - a * d * d / 2 + c * d ** 3 / 6                      # 1 - (1 - d)^k, three terms
    dp = np.maximum(dp, 1 / Q30)
    q = 1 - dp / 4 - 3 * dp * dp / 32
    om = dp / 2 + dp * dp / 8 + dp ** 3 / 16
    dl = w["th"] / (1 << 29) * t / 2                                 # half the frequency change
    sp = w["sh"] / Q30 * (1 - dl * dl / 2 + dl ** 4 / 24) + w["ch"] / Q30 * (dl - dl ** 3 / 6 + dl ** 5 / 120)
    cr = w["cr"] / Q16 * (1 - 2 * t + 3 * t * t)
    E = 2 * q * sp * (1 + om * om * cr)
    return E, dp + 0 * E


def theta(E, eps):
    """the MC3 recursion's pole angle (x += E y; y += -eps y - E x): 1 - cos = (E^2 - (1 - s)^2) / 2s, s = sqrt(1-eps)"""
    s = np.sqrt(1 - eps)
    u = (E * E - (1 - s) ** 2) / (2 * s)
    return 2 * np.arcsin(np.sqrt(np.maximum(u, 0) / 2))


def reach(names, T, K, tune_t):
    """the cases the firmware can form: the Rhodes every t and k; VIBES / MARIMBA (bars.c) only TUNE (no bend: the
    TUNE_T values), the decay scaled except in the TUBE slot, the mute (drel) scaled only on VIBES (the damper) and
    never in the TUBE slot (the TUBE's decay: scaled on MARIMBA, key flag bit3, here every MARIMBA key). Shaped as run_c's output (mode, t, k, decay)."""
    r = np.ones((len(names), len(T), len(K), 2), bool)
    tune = np.isin(T, tune_t)
    for i, nm in enumerate(names):
        if not nm.startswith("bar"):
            continue
        r[i, ~tune] = False
        if nm.endswith(" TUBE") or nm.startswith("bar1"):
            r[i][:, K != 65536, 1] = False
        if nm.endswith(" TUBE") and nm.startswith("bar0"):
            r[i][:, K != 65536, 0] = False
    return r


@pytest.fixture(scope="module")
def grid():
    W, names = modes()
    T, tnames = t_grid()
    K = k_grid()
    rt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h"))
    exe = build(open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read(), "now")
    Ei, ei = run_c(exe, W, T, K)
    Er, er = ref(W, T, K)
    return dict(W=W, names=names, T=T, tnames=tnames, K=K, Ei=Ei / Q30, ei=ei / Q30, Er=Er, er=er,
                ok=reach(names, T, K, rt["TUNE_T"]["vals"]))


def test_grid_covers_the_overflow(grid):
    """the cases that broke are in the grid: cr (1 - 2t + 3t^2) above int32 for some Rhodes mode x tune"""
    W, T = grid["W"], grid["T"]
    t = T / Q30
    crt = W[:, 5][:, None] * (1 - 2 * t + 3 * t * t)[None, :]
    assert (crt[grid["ok"][:, :, 0, 0]] > 2 ** 31 - 1).sum() > 100
    assert len(W) > 1000 and len(T) > 500 and grid["ok"].sum() > 2_000_000


def test_corr_fits(grid):
    """corr = 1 + om^2 cr stays a Q30 int32 (< 2) in every case the firmware forms (the MARIMBA mute: 0.23 at most)"""
    k = (grid["K"] / Q16)[None, None, :, None]
    W = grid["W"]
    d = np.stack([W[:, 3], W[:, 4]], -1)[:, None, None, :] / Q30
    dp = np.maximum(k * d - k * (k - 1) * d * d / 2 + k * (k - 1) * (k - 2) * d ** 3 / 6, 1 / Q30)
    om = dp / 2 + dp * dp / 8 + dp ** 3 / 16
    t = (grid["T"] / Q30)[None, :, None, None]
    x = om * om * W[:, 5][:, None, None, None] / Q16 * (1 - 2 * t + 3 * t * t)
    assert x[grid["ok"]].max() < 0.5


def test_eps(grid):
    """eps is the decay alone: 1 - (1 - d)^k to the series' three terms (k(k-1), k(k-1)(k-2) in Q16): within 2 LSB or
    1e-4"""
    ok = grid["ok"]
    d = np.abs(grid["ei"] - grid["er"])
    assert (d <= np.maximum(2 / Q30, 1e-4 * grid["er"]))[ok].all()


def test_e_matches_the_formula(grid):
    rel = np.abs(grid["Ei"] / grid["Er"] - 1)[grid["ok"]]
    assert rel.max() < 1e-4, rel.max()


def test_frequency_cents(grid):
    """the resonator's frequency from the integer (E, eps) within 0.1 cent of the reference formula's, every case"""
    with np.errstate(divide="ignore"):
        err = np.abs(1200 * np.log2(theta(grid["Ei"], grid["ei"]) / theta(grid["Er"], grid["er"])))
    err[~grid["ok"]] = 0
    i = np.unravel_index(int(np.argmax(err)), err.shape)
    assert err.max() < 0.1, (err.max(), grid["names"][i[0]], grid["tnames"][int(grid["T"][i[1]])], i)


def test_ubsan_clean(grid):
    """the whole grid through rh_coef built with UBSan (no signed overflow anywhere: it stops at the first) gives the
    same integers"""
    exe = build(open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read(), "ubsan",
                ["-fsanitize=undefined", "-fno-sanitize-recover=undefined", "-fno-sanitize=shift-base"])
    Ei, ei = run_c(exe, grid["W"], grid["T"], grid["K"])
    assert np.array_equal(Ei / Q30, grid["Ei"]) and np.array_equal(ei / Q30, grid["ei"])


def test_python_mirror_bit_identical(grid):
    """tools/rh_coefs.py coef (the golden model) gives the C's integers: every mode at -200 / -50 / 0 / +200 cents,
    every k, both decays"""
    W, T, K = grid["W"], grid["T"], grid["K"]
    pick = [int(np.argmin(np.abs(T - round((2 ** (c / 1200) - 1) * Q30)))) for c in (-200, -50, 0, 200)]
    for mi in range(len(W)):
        w = dict(zip(MODE_F, (int(x) for x in W[mi])))
        for ti in pick:
            for ki, k in enumerate(K):
                for di, d in enumerate((w["d"], w["drel"])):
                    E, eps = rh_coefs.coef(w, d, int(T[ti]), int(k))
                    assert (E, eps) == (int(round(grid["Ei"][mi, ti, ki, di] * Q30)),
                                        int(round(grid["ei"][mi, ti, ki, di] * Q30))), (grid["names"][mi], ti, k, di)


if __name__ == "__main__":                                   # the cents report: this rhodes.c against REV's
    import argparse
    from collections import defaultdict
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default="HEAD", help="the git revision of rhodes.c to compare with")
    a = ap.parse_args()
    W, names = modes(); T, tnames = t_grid(); K = k_grid()
    rt, _ = table_ab.parse(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h"))
    ok = reach(names, T, K, rt["TUNE_T"]["vals"])
    old = subprocess.run(["git", "-C", ROOT, "show", f"{a.before}:firmware/src/rhodes.c"], capture_output=True,
                         text=True, check=True).stdout
    Er, er = ref(W, T, K)
    th_ref = theta(Er, er)
    cents = 1200 * np.log2(1 + T / Q30)
    for tag, src in (("before", old), ("after", open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read())):
        Ei, ei = run_c(build(src, tag), W, T, K)
        with np.errstate(divide="ignore"):
            e = np.abs(1200 * np.log2(theta(Ei / Q30, ei / Q30) / th_ref))
        e[~ok] = 0
        pos = cents >= -0.01
        print(f"{tag}: the frequency vs the double formula, max {e.max():.4f} c (tune >= 0: {e[:, pos].max():.4f} c)")
        worst, onset = defaultdict(float), {}
        for mi, ti, ki, di in np.argwhere(e > 0.1):
            key = (names[mi], ["d", "drel"][di])
            worst[key] = max(worst[key], e[mi, ti, ki, di])
            onset[key] = max(onset.get(key, -1e9), cents[ti])
        for key, v in sorted(worst.items(), key=lambda kv: -kv[1]):
            print(f"   {key[0]:14} {key[1]:4}  worst {v:8.3f} c; above 0.1 c from {onset[key]:+.0f} cents down")
