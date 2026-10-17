#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate the VIBES bar-engine tables from the physics (VIBES PLAN §4.4, §4.6).

    tools/gen_bars.py [--strict] [--out DIR]   -> firmware/gen/bars_tables.h  +  docs/model/data/bars_spec.json
                                                  (or both into DIR)

Per key 21..108: the undercut bar (bar_fe: Timoshenko bending, Saint-Venant torsion, the profile optimised for
1:4:10) gives the strike-position weights and the torsion ratios; the decays come from vibes.json (the reference
measurements in F3..F6, the laws outside, the material cap); the V1 <-> tube pair (bartube: tube zeta_r solved from
the measured f0-peak time with the recordings' own procedure, coupling sigma solved for the measured V1-with-tube
T60, retuned so the V1-like mode is exactly f0, the integer words and the stability grid). The mallet contacts
(hammer) per key x family x velocity anchor give the pulse shapes (per band), rates and amplitudes. The golden model
(bars_model) then calibrates, in gen_rhodes' order: the velocity trim (median curve, monotonic; the four mallet
families level-matched), the b scale (slot peaks <= 2^26 with x2 re-strike headroom at the worst voicing), the
per-key gain (v100 RMS 50..300 ms = the Rhodes' -26 dBFS), a global v127 peak cap, kill and life.

Output is deterministic (byte-identical every run). --strict fails on an ESTIMATE row without a settle entry
(release builds). The FE profiles are cached in build/gen_bars/fe_cache.json (keyed by their inputs).
"""
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bar_fe as fe        # noqa: E402
import bars_model as gm    # noqa: E402
import bartube as btm      # noqa: E402
import hammer as hm        # noqa: E402
import rh_coefs as rc      # noqa: E402
from gen_rhodes import area_resample, contact_segments, rom_safe, vlayers, vpos, pedal_w  # noqa: E402

GEN_VERSION = 1
FS = 44100.0
KEY_LO, KEY_HI = 21, 108
NKEY = KEY_HI - KEY_LO + 1
NM = 8
SLOT_NAMES = ["V1", "TUBE", "V2", "V3", "V4", "T1", "T2", "X"]
NFAM, NBAND, NLAYER, PLEN, NPOS, NTYPE = 4, 16, 8, 64, 17, 1
FADE_STEP = 128
V_ANCH = gm.V_ANCH
LAYER_V = gm.LAYER_V
LN1000 = 6.907755279
Q30, Q28, Q25, Q16, Q15, Q14, Q12 = 1 << 30, 1 << 28, 1 << 25, 1 << 16, 1 << 15, 1 << 14, 1 << 12
STATUSES = {"CONFIRMED", "CORRECTED", "DERIVED", "MEASURED-REF", "ESTIMATE"}
VIBES = os.path.join(ROOT, "docs", "model", "data", "vibes.json")
FAM_DETENT = [0, 42, 85, 127]          # BARK values that are exactly one family (KN_FAM)
PEAK_TOL_MS, PEAK_TOL_REL = 15.0, 0.30  # Gate VA: the f0 peak within +-15 ms or +-30 % (the larger)
POS_DEF = 8                            # BAR_POS point of TONE raw 64 (x_bal on the centreline): b is exact per key there
POS_EXACT_MIN = 0.02                   # ... unless the band's table is under 2 % of its maximum there (near a node)
PEAK_SLOT = 0.9                        # single-strike slot peak (real units, 1.0 = 2^25): x2 re-strike stays < 2^26


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


# ---------------------------------------------------------------- vibes.json
def rows_of(d, path=""):
    """Every row (a dict with a status) in the tree, with its path."""
    out = []
    if isinstance(d, dict):
        if "status" in d:
            out.append((path, d))
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                out += rows_of(v, path + "/" + str(k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out += rows_of(v, path + "/" + str(i))
    return out


def check_vibes(vb, strict=False):
    """Schema: every top-level T-row has src and a known status; --strict: every ESTIMATE has a settle entry."""
    errs = []
    for k, v in vb.items():
        if k.startswith("_") or not isinstance(v, dict) or k in ("keys",):
            continue
        if "status" not in v or "src" not in v:
            errs.append("%s: no src/status" % k)
    for path, r in rows_of(vb):
        if r["status"] not in STATUSES:
            errs.append("%s: unknown status %s" % (path, r["status"]))
        if "src" not in r:
            errs.append("%s: no src" % path)
        if strict and r["status"] == "ESTIMATE" and not r.get("settle"):
            errs.append("%s: ESTIMATE without settle" % path)
    return errs


def load_vibes(path=VIBES, strict=False):
    vb = json.load(open(path))
    errs = check_vibes(vb, strict)
    if errs:
        raise SystemExit("vibes.json: " + "; ".join(errs))
    return vb


def table(d):
    """{"53": v, ...} -> (sorted keys, values)."""
    ks = sorted(int(k) for k in d)
    return ks, [float(d[str(k)]) for k in ks]


def interp_log(n, xs, ys):
    return float(math.exp(np.interp(n, xs, np.log(ys))))


# ---------------------------------------------------------------- decays (T1a, T1b, T2, T3, cap)
def cap(vb, f):
    return vb["cap"]["t60_hz_s"] / f


def t1a(vb, n):
    r = vb["T1a"]
    f = midi_hz(n)
    return min(r["a_s"] * (r["f_ref"] / f) ** r["exp"], cap(vb, f), r["max_s"])


def t1b_smoothed(vb):
    """The reference list smoothed as bartube.smooth_median: the median of +-smooth_keys neighbouring reference notes,
    the window shrinking at the ends (F3 and F6 keep their raw values)."""
    xs, ys = table(vb["T1b"]["t60_s"])
    return xs, btm.smooth_median(ys, vb["T1b"]["smooth_keys"])


def t1b_raw(vb, n):
    """T1b before the free-bar cap: the smoothed table (semitone interpolation) in F3..F6; outside, the end value
    times (f_end / f)^extrap_exp (D's law, anchored so the curve is continuous), clamped."""
    r = vb["T1b"]
    xs, sm = t1b_smoothed(vb)
    if xs[0] <= n <= xs[-1]:
        return float(np.interp(n, xs, sm))
    e, v = (xs[0], sm[0]) if n < xs[0] else (xs[-1], sm[-1])
    return min(v * (midi_hz(e) / midi_hz(n)) ** r["extrap_exp"], r["max_s"])


def t1b(vb, n):
    """The V1-with-tube target: never longer than the free bar (coordinator decision 6, keys above ~98)."""
    return min(t1b_raw(vb, n), t1a(vb, n))


def peaks_smoothed(vb):
    xs, ys = table(vb["T5c"]["peak_ms"])
    return xs, btm.smooth_median(ys, vb["T5c"]["smooth_keys"])


def upper_t60(vb, n, mode, ratio):
    """T2: held T60 of V2/V3/V4 at key n: the measured table (log-interpolated), past its ends the law
    T60_1,free (f1/fn)^q anchored at the end note, then the cap."""
    r = vb["T2"]
    xs, ys = table(r[mode]["t60_s"])
    f = midi_hz(n) * ratio
    if xs[0] <= n <= xs[-1]:
        t = interp_log(n, xs, ys)
    else:
        e = xs[0] if n < xs[0] else xs[-1]
        te = ys[0] if n < xs[0] else ys[-1]
        t = te * t1a(vb, n) / t1a(vb, e)                   # (f1/fn)^q with a fixed ratio: the T1a trend
    return min(t, cap(vb, f))


def law_t60(vb, n, ratio):
    """The upper-mode law alone (torsion): T60_1,free (1/ratio)^q, capped."""
    f = midi_hz(n) * ratio
    return min(t1a(vb, n) * (1.0 / ratio) ** vb["T2"]["q"], cap(vb, f))


def felt_v1(vb, n):
    r = vb["T3"]
    xs = [p[0] for p in r["mult_points"]]
    ys = [p[1] for p in r["mult_points"]]
    return r["v1_s"] * interp_log(n, xs, ys)


# ---------------------------------------------------------------- the tube (T4a, T4b, T5)
def e_curve(vb, n):
    xs = [p[0] for p in vb["T4a"]["E_curve_pct"]]
    ys = [p[1] / 100 for p in vb["T4a"]["E_curve_pct"]]
    if n > xs[-1]:
        sl = (math.log(ys[-1]) - math.log(ys[-2])) / (xs[-1] - xs[-2])
        return ys[-1] * math.exp(sl * (n - xs[-1]))
    return interp_log(n, xs, ys)


def zeta_lo(vb, n):
    """The zeta_r floor (coordinator decision 3: 0.5 % where the measured peaks need it; the E curve is not a bound)."""
    return vb["T4a"]["lo_pct"] / 100


def tube_cents(vb, n):
    pts = vb["T4b"]["cents_points"]
    return float(np.interp(n, [p[0] for p in pts], [p[1] for p in pts]))


def make_pair(vb, n, zr, target):
    """BarTube for key n with tube zeta_r: sigma solved for the V1-with-tube T60 target (bounded, flagged),
    weak-regime limited at BELL's top, retuned to f0. Returns (bt, clamp, weak)."""
    f0 = midi_hz(n)
    ft = f0 * 2 ** (tube_cents(vb, n) / 1200)
    lo, hi = vb["T5a"]["lo"], vb["T5a"]["hi"]
    bt = btm.BarTube(f0, ft, lo, LN1000 / t1a(vb, n), zr * 2 * math.pi * ft)
    if target >= t1a(vb, n):                          # the free bar is already shorter (top keys): minimum sigma
        cl, want = 0, lo
    else:
        bt = bt.solve_sigma(target, lo, hi)
        cl, want = bt.clamped, bt.sigma_wanted
    weak = 0
    smax = bt.sigma_weak_max(s_max=KN_TUBE_TOP, margin=vb["T5a"]["weak_margin"])
    if bt.sigma > smax:
        bt = bt.with_sigma(max(smax, 1e-5))
        weak = 1
    bt = bt.retune(f0)
    bt.clamped, bt.sigma_wanted = cl, want
    return bt, cl, weak


def pair_peak_ms(vb, bt, dur=0.8):
    """The model's f0-envelope peak, measured exactly as the recordings were (bartube.f0_peak_ms), and wt (T5b).
    dur: the render (s); 0.8 s holds every reference note's peak (<= 145 ms), the design record of the low keys
    outside F3..F6 (A0's tube builds up for ~0.9 s) needs longer: there 0.8 s read the window's end."""
    wt = btm.wt_for_ratio(bt, vb["T5b"]["db"], vb["T5b"]["t_s"])
    x = bt.impulse(FS, int(dur * FS), wt=wt)
    return btm.f0_peak_ms(x, bt.v1()[0]), wt


KC_MAX = 1.5      # bartube.pair_words' kc (the discrete coupling's decay correction): 1.00..1.06 over 21..108 with the
#                   tube tuned within a few cents (T4b). Why it can fail: the MC3 pair's coupling acts with a per-sample
#                   phase lag of about theta = 2 pi f / fs, which rotates the coupling's complex decay shift
#                   sigma^2 / (4 (i (zr - zn) + delta)) by ~theta. With the old +100 c tube (delta = 5.9 %, the shift's
#                   angle ~56 deg at zr 4 %) the rotation reached 90 deg at theta ~0.6 rad (C8): the coupling stopped
#                   shortening V1 (and turned anti-damping beyond), so the bisection ran into its 4.0 bound and the grid
#                   missed +-2 % by up to 57 %. With a few cents the angle is small and kc stays ~1.


def pair_words_safe(bt, wt, felt, f0):
    """bartube.pair_words with the discrete retune; the kc correction must stay under KC_MAX (else the integer pair
    cannot follow the continuous design within the grid's +-2 %: fail loudly instead of capping)."""
    w = btm.pair_words(bt, wt, felt, f0=f0)
    if not w["kc"] <= KC_MAX:
        raise SystemExit("pair: kc %.3f > %.1f at f0 %.1f Hz (the discrete coupling cannot reach the design's V1 decay; "
                         "see KC_MAX)" % (w["kc"], KC_MAX, f0))
    return w


def map_check_words(k):
    """bartube.map_check on the key's own words (pair_words_safe): the §4.5 grid s x g x DECAY / RELEASE x TUNE,
    held and felt. Returns (rho_max, worst normal-mode decay error vs the float design, V1 cents vs f0, points)."""
    bt, wt, f0, felt, w_ref = k["bt"], k["wt"], k["f0"], k["felt_v1"], k["pw"]
    dk = [int(round(rc.Q16 / 2 ** ((v - 64) / 32))) for v in (0, 64, 127)]
    tune = [(c, int(round((2 ** (c / 1200) - 1) * rc.Q30))) for c in (-50, 0, 50)]
    rho, derr, n = 0.0, 0.0, 0
    btn = bt.scaled(w_ref["f_slot0"] / bt.slot_freqs()[0])
    sig_rel = LN1000 / felt
    for s in (0.0, 0.25, 0.5, 1.0, KN_TUBE_TOP):
        for g in (0.15, 0.5, 1.0):
            for k16 in dk:
                for c, t30 in tune:
                    for rel in (False, True):
                        w = btm.scaled_words(w_ref, btm.pair_words(btn, wt, felt, t30=t30, k16=k16, release=rel,
                                                                   G=w_ref["G"], kc=w_ref["kc"]), s, g)
                        M = btm.pair_map(w)
                        rho = max(rho, float(max(abs(np.linalg.eigvals(M)))))
                        tt = 2 ** (c / 1200)
                        d = bt.with_bell(s).with_motor(g)._with(sig_bar=(sig_rel if rel else bt.sig_bar) * k16 / 65536.0)
                        d = d.scaled(tt).with_sigma(bt.sigma / tt)
                        for (fa, sa, _), (fb, sb, _) in zip(btm.map_modes(M), d.modes()):
                            derr = max(derr, abs(sa / sb - 1))
                        n += 1
    M = btm.pair_map(w_ref)
    cents = 1200 * math.log2(btm.map_modes(M)[btm.map_v1(M)][0] / f0)
    return rho, derr, cents, n


def peak_band(vb, target_ms):
    """The f0-peak readings the T60 trade may move to: the Gate VA tolerance (+-15 ms or +-30 %, the larger) less
    trade_margin_ms; a reading cannot go below 0, so when the target is within the tolerance of 0 every reading from 0
    up passes the gate whatever the golden render reads, and the band runs down to 0."""
    tol = max(PEAK_TOL_MS, PEAK_TOL_REL * target_ms)
    m = vb["T4a"]["trade_margin_ms"]
    return (0.0 if target_ms <= tol else target_ms - tol + m), target_ms + tol - m


def solve_zeta(vb, n, target_ms):
    """zeta_r in [zeta_lo, hi] for a reference note (T4a), in three steps:
    1. the peak: the smallest zeta_r whose model peak reads <= the target (the peak falls with zeta_r);
    2. resolution: for targets <= PEAK_TOL_MS (the build-up is not resolved by the 93 ms window) the radiation estimate
       max(E_floor_pct, the E curve) when it is larger and reads within peak_band;
    3. T60 first: where sigma clamps there (V1 rings too long: sigma would exceed hi; too short: under lo), zeta_r moves
       (down / up) until T1b is met, as far as the peak reading stays within peak_band.
    Returns (zeta_r, flag): flag bit0..: -1 / +1 = the peak clamped at the floor / top, -2 = the E curve (step 2);
    +16 = traded for T60 and met, +32 = traded to the band edge and T1b still not met."""
    lo, hi = zeta_lo(vb, n), vb["T4a"]["hi_pct"] / 100
    tb = t1b(vb, n)
    memo = {}

    def ev(z):
        """(peak reading ms, sigma clamp: -1 / 0 / +1, the weak-regime limit counting as +1)"""
        if z not in memo:
            bt, cl, weak = make_pair(vb, n, z, tb)
            memo[z] = (pair_peak_ms(vb, bt)[0], 1 if weak else cl)
        return memo[z]

    def bisect(a, b, good, it=24):
        """a satisfies good, b does not: the boundary point on a's side (geometric bisection)."""
        for _ in range(it):
            m = math.sqrt(a * b)
            a, b = (m, b) if good(m) else (a, m)
        return a

    # 1. the peak
    if ev(lo)[0] <= target_ms:
        z, flag = lo, -1
    elif ev(hi)[0] > target_ms:
        z, flag = hi, +1
    else:
        z, flag = bisect(hi, lo, lambda m: ev(m)[0] <= target_ms), 0
    b_lo, b_hi = peak_band(vb, target_ms)
    # 2. the resolution limit: the radiation estimate where the reading cannot tell
    if target_ms <= PEAK_TOL_MS:
        ze = min(hi, max(vb["T4a"]["E_floor_pct"] / 100, e_curve(vb, n)))
        if ze > z and b_lo <= ev(ze)[0] <= b_hi:
            z, flag = ze, -2
    # 3. T60 first
    cl = ev(z)[1]

    def in_band(m):
        return b_lo <= ev(m)[0] <= b_hi
    if cl and in_band(z):
        far = lo if cl > 0 else hi          # too long: less tube damping (a stronger, later tube); too short: more
        edge = far if in_band(far) else bisect(z, far, in_band)
        if ev(edge)[1] == cl:               # even the band edge cannot reach T1b: the best the band allows
            return edge, flag + 32
        return bisect(edge, z, lambda m: ev(m)[1] != cl), flag + 16
    return z, flag + (32 if cl else 0)


def tube_zetas(vb):
    """zeta_r per key: solved at the reference notes, log-interpolated between them, outside F3..F6 the E curve's
    trend scaled to the end note (bounded)."""
    xs, ts = peaks_smoothed(vb)
    zr, flag = {}, {}
    for n, t in zip(xs, ts):
        zr[n], flag[n] = solve_zeta(vb, n, t)
    out = {}
    hi = vb["T4a"]["hi_pct"] / 100
    for n in range(KEY_LO, KEY_HI + 1):
        if n in zr:
            out[n] = (zr[n], flag[n])
        elif xs[0] < n < xs[-1]:
            out[n] = (interp_log(n, xs, [zr[k] for k in xs]), 0)
        else:
            e = xs[0] if n < xs[0] else xs[-1]
            out[n] = (min(hi, max(vb["T4a"]["lo_pct"] / 100, e_curve(vb, n) * zr[e] / e_curve(vb, e))), 0)
    return out


# ---------------------------------------------------------------- the bar (FE), strike map
def strike_xy(vb, raw, L, w, nodes):
    s = vb["T13a"]
    if raw <= 64:
        x = (s["x_dull"] + (s["x_bal"] - s["x_dull"]) * raw / 64.0) * L
        y = 0.0
    else:
        x, y = s["x_bal"] * L, s["y_edge"] * w * (raw - 64) / 63.0
    lo = min(nodes) + s["node_margin"] * L                 # off the V1 node (both halves are symmetric)
    return max(x, lo), y


def fe_key(vb, ng, A_init=None):
    """FE of the geometric key ng (53..89): the optimised profile, ratios, torsion, strike weights at the 17 points."""
    r = vb["T12"]
    L, w = fe.outline(ng, vb["keys"]["authentic"][0], vb["keys"]["authentic"][1], r["L_mm"], r["w_mm"])
    t = r["t_mm"] * 1e-3
    mat = dict(E=r["E_pa"], rho=r["rho"], G=r["G_pa"], kappa=r["kappa"])
    ratios = (vb["T9a"]["V2"], vb["T9a"]["V3"]) if ng <= vb["T9a"]["v3_tuned_max_key"] else (vb["T9a"]["V2"],)
    p = fe.tune(midi_hz(ng), L, w, t, ratios=ratios, h_min=r["h_min_mm"] * 1e-3, A_init=A_init, **mat)
    bar = p["bar"]
    ft, _ = bar.torsion(2)
    nodes = bar.v1_nodes()
    W = []
    for i in range(NPOS):
        x, y = strike_xy(vb, gm.pos_point(i, NPOS), L, w, nodes)
        W.append(fe.strike_weights(bar, x, y, 4, 2))
    return dict(ng=ng, L=L, w=w, A=p["A"], f=p["f"], ratios=p["ratios"], err_c=p["err_c"],
                tors=[float(x / p["f"][0]) for x in ft], weights=W, nodes=[x / L for x in nodes], mass=bar.mass(),
                hmin=float(bar.H.min()), curv=p["curv"])


def fe_all(vb):
    """FE for every geometric key, cached by the inputs' hash."""
    key = hashlib.sha256(json.dumps([vb["T12"], vb["T9a"], vb["T13a"], vb["T2"]["V3"]["max_key"], vb["keys"],
                                     open(os.path.join(HERE, "bar_fe.py")).read(), "pos=127i/16", "continuation"], sort_keys=True).encode()).hexdigest()
    path = os.path.join(ROOT, "build", "gen_bars", "fe_cache.json")
    try:
        c = json.load(open(path))
        if c.get("key") == key:
            return {int(k): v for k, v in c["fe"].items()}
    except (OSError, ValueError):
        pass
    a, b = vb["keys"]["authentic"]
    out = {}
    prev = None
    for ng in range(a, b + 1):                     # continuation: each key starts from its lower neighbour's profile
        out[ng] = fe_key(vb, ng, prev)
        prev = out[ng]["A"] if len(out[ng]["A"]) else None
        print("fe %d: ratios %s torsion %s hmin %.1f mm" % (ng, " ".join("%.3f" % x for x in out[ng]["ratios"]),
              " ".join("%.3f" % x for x in out[ng]["tors"]), out[ng]["hmin"] * 1e3), file=sys.stderr)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(dict(key=key, fe={str(k): v for k, v in out.items()}), open(path, "w"))
    return out


# ---------------------------------------------------------------- per-key physics
def hash01(n, salt, seed):
    h = (n * 2654435761 + salt * 40503 + seed * 97) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2246822519) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 3266489917) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 2.0 ** 32


def detune(vb, n, mode):
    r = vb["T11"]
    lo, hi = r[mode + "_c"]
    return lo + (hi - lo) * hash01(n, {"V2": 2, "V3": 3, "V4": 4}[mode], r["seed"])


BANDS = {}          # the assigned bands: BANDS["of"][n], BANDS["rep"][b] (set by assign_bands, uniform until then)


def band_of(n):
    if BANDS:
        return BANDS["of"][n]
    return min(NBAND - 1, (n - KEY_LO) * NBAND // NKEY)


def band_keys(b):
    return [n for n in range(KEY_LO, KEY_HI + 1) if band_of(n) == b]


def band_rep(b):
    if BANDS:
        return BANDS["rep"][b]
    ks = band_keys(b)
    return ks[len(ks) // 2]


def pulse_excitation(shape, prate_q12, freqs):
    """|spectrum| of a 64-point pulse played at prate (Q12 shape points per output sample) at freqs, and its DC."""
    sh = np.array(shape[:PLEN], float)
    t = np.arange(0, PLEN, prate_q12 / 4096.0)
    F = np.interp(t, np.arange(PLEN), sh)
    k = np.arange(len(F))
    return np.array([abs(np.sum(F * np.exp(-2j * np.pi * f * k / FS))) for f in freqs]), abs(float(np.sum(F)))


BAND_VELS, BAND_FLOOR_DB = (40, 100, 127), -30.0


def shape_error(phys, ct, n, r):
    """Worst |dB| between key n's own pulse and key r's shape played at n's rate, on V1..V3's excitation at
    v40 / v100 / v127 (layer blends), where the excitation is within 30 dB of the pulse's DC (§4.4 band test)."""
    k = phys[n]
    fr = [k["f0"], k["modes"][0]["f"], k["modes"][1]["f"]]
    worst = 0.0
    for q in range(NFAM):
        own, oth = ct[n][q]["shapes"], ct[r][q]["shapes"]
        pr = ct[n][q]["prate"]
        for v in BAND_VELS:
            l, w = gm.vlayer(v)
            l1 = min(l + 1, NLAYER - 1)
            mix = lambda S: [x + (((y - x) * w) >> 15) for x, y in zip(S[l], S[l1])]  # noqa: E731
            ia, f = gm.vpos(v)
            p = pr[ia] + (((pr[ia + 1] - pr[ia]) * f) >> 8)
            eo, dc = pulse_excitation(mix(own), p, fr)
            eb, _ = pulse_excitation(mix(oth), p, fr)
            for a, c in zip(eo, eb):
                if a > dc * 10 ** (BAND_FLOOR_DB / 20):
                    worst = max(worst, abs(20 * math.log10(c / a)))
    return worst


def assign_bands(phys, ct, reach=12):
    """NBAND contiguous key groups and a representative each, minimising the worst shape error (binary search on
    the threshold, greedy longest segments). The band is a per-key field of BAR_KEYS, so groups need not be even."""
    keys = list(range(KEY_LO, KEY_HI + 1))
    E = {}
    for n in keys:
        for r in range(max(KEY_LO, n - reach), min(KEY_HI, n + reach) + 1):
            E[n, r] = 0.0 if n == r else shape_error(phys, ct, n, r)

    def seg_cost(i, j):                                   # keys[i..j]: (best rep, its worst error)
        best = (None, 1e9)
        for r in keys[i:j + 1]:
            c = max(E.get((n, r), 1e9) for n in keys[i:j + 1])
            if c < best[1]:
                best = (r, c)
        return best

    def greedy(T):
        segs, i = [], 0
        while i < len(keys):
            j = i
            while j + 1 < len(keys) and seg_cost(i, j + 1)[1] <= T:
                j += 1
            segs.append((i, j))
            i = j + 1
        return segs
    lo, hi = 0.0, 6.0
    for _ in range(14):
        m = 0.5 * (lo + hi)
        if len(greedy(m)) <= NBAND:
            hi = m
        else:
            lo = m
    segs = greedy(hi)
    while len(segs) < NBAND:                              # split the longest until exactly NBAND
        i = max(range(len(segs)), key=lambda q: segs[q][1] - segs[q][0])
        a, b = segs[i]
        mid = (a + b) // 2
        segs[i:i + 1] = [(a, mid), (mid + 1, b)]
    of, rep, err = {}, [], []
    for bi, (a, b) in enumerate(segs):
        r, c = seg_cost(a, b)
        rep.append(r)
        err.append(c)
        for q in range(a, b + 1):
            of[keys[q]] = bi
    return dict(of=of, rep=rep, err=err)


def key_physics(vb, n, fec, zt):
    """Float design of key n: slot frequencies, T60 held / felt, strike-weight source, the pair."""
    a, b = vb["keys"]["authentic"]
    ng = min(max(n, a), b)
    F = fec[ng]
    f0 = midi_hz(n)
    flags = 0 if a <= n <= b else 1
    rv2, rv3, rv4 = (vb["T9a"]["V2"] * 2 ** (detune(vb, n, "V2") / 1200), vb["T9a"]["V3"] * 2 ** (detune(vb, n, "V3") / 1200),
                     vb["T10"]["ratio"] * 2 ** (detune(vb, n, "V4") / 1200))
    tr1, tr2 = F["tors"]
    fv1 = felt_v1(vb, n)
    up = vb["T3"]["upper_mult"]
    zr, zflag = zt[n]
    target = t1b(vb, n)
    bt, cl, weak = make_pair(vb, n, zr, target)
    pk, wt = pair_peak_ms(vb, bt, dur=2.5)
    if cl or weak:
        flags |= 4
    modes = []
    t60s = {}
    for name, ratio in (("V2", rv2), ("V3", rv3), ("V4", rv4)):
        t60s[name] = upper_t60(vb, n, name, ratio)
    for name, ratio in (("T1", tr1), ("T2", tr2)):
        t60s[name] = law_t60(vb, n, ratio) * vb["T9d"]["mult"]
    present = {"V2": n <= vb["T2"]["V2"]["max_key"], "V3": n <= vb["T2"]["V3"]["max_key"],
               "V4": n <= vb["T10"]["max_key"], "T1": True, "T2": True, "X": False}
    maxk = {"V2": vb["T2"]["V2"]["max_key"], "V3": vb["T2"]["V3"]["max_key"], "V4": vb["T10"]["max_key"]}
    tk = vb["T2"]["taper_keys"]
    for name, ratio in (("V2", rv2), ("V3", rv3), ("V4", rv4), ("T1", tr1), ("T2", tr2)):
        t = t60s[name]
        tp = 1.0
        if name in vb["T2"]["taper_modes"] and maxk[name] - tk < n <= maxk[name]:     # fade out towards the last key it is present
            tp = (maxk[name] + 1 - n) / (tk + 1.0)
        modes.append(dict(name=name, ratio=ratio, f=f0 * ratio, t60=t, t60_rel=min(t, fv1 * up), present=present[name],
                          taper=tp))
    modes.append(dict(name="X", ratio=0.0, f=0.0, t60=1.0, t60_rel=1.0, present=False))
    return dict(midi=n, f0=f0, ng=ng, flags=flags, band=band_of(n), modes=modes, bt=bt, wt=wt, zeta_r=zr,
                zeta_flag=zflag, t1b=target, t1a=t1a(vb, n), felt_v1=min(fv1, t1a(vb, n)), peak_ms=pk, sigma_clamp=cl,
                weak=weak, tube_cents=tube_cents(vb, n))


# ---------------------------------------------------------------- mallets (T7, T8)
def fam_K(vb, q):
    fm = vb["T7c"]["families"][q]
    if "K" in fm:
        return fm["K"]
    m = vb["T7b"]["m_g"][1] * 1e-3
    tc1 = hm.hertz_tc(1.0, m, 1.0, fm["p"])                # Tc at K = 1: Tc ~ K^(-1/(p+1))
    return (tc1 / (fm["tau_ms_1ms"] * 1e-3)) ** (fm["p"] + 1)


def speed(vb, v):
    r = vb["T8"]
    return r["v_lo"] * (r["v_hi"] / r["v_lo"]) ** ((v - 1) / 126.0)


def mass_kg(vb, h):
    m = vb["T7b"]["m_g"]
    if h <= 64:
        g = m[0] * (m[1] / m[0]) ** (h / 64.0)
    else:
        g = m[1] * (m[2] / m[1]) ** ((h - 64) / 63.0)
    return g * 1e-3


def contacts(vb, phys, fec):
    """Every key x family x velocity anchor: the coupled mallet-bar contact (V1..V4 at the default strike point)."""
    m0 = mass_kg(vb, 64)
    cases = []
    for n in range(KEY_LO, KEY_HI + 1):
        k = phys[n]
        W = fec[k["ng"]]["weights"][8]                    # TONE 0: x_bal on the centreline
        fs_ = [k["f0"]] + [md["f"] for md in k["modes"][:3]]
        ts = [k["t1a"]] + [md["t60"] for md in k["modes"][:3]]
        modes = [(f, LN1000 / t, W[i]) for i, (f, t) in enumerate(zip(fs_, ts))]
        for q, fm in enumerate(vb["T7c"]["families"]):
            K = fam_K(vb, q)
            for v in V_ANCH:
                cases.append(dict(v=speed(vb, v), m_h=m0, K=K, p=fm["p"], modes=modes))
    res = hm.contact_batch(cases, fs=FS, t_max=0.012)
    out = {}
    i = 0
    for n in range(KEY_LO, KEY_HI + 1):
        out[n] = []
        for q in range(NFAM):
            sims = dict(zip(V_ANCH, res[i:i + len(V_ANCH)]))
            i += len(V_ANCH)
            seg = {v: contact_segments(sims[v]["F"]) for v in V_ANCH}
            ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg.values()))
            dt = sims[V_ANCH[0]]["dt"]
            cap_ = vb["T7f"]["f_tau_max"] / (phys[n]["f0"] * dt)        # the main-lobe cap (T7f), 16x samples
            win = {v: ratio * seg[v][0][1] for v in V_ANCH}                # the whole contact: the shape
            play = {v: min(win[v], cap_) for v in V_ANCH}                  # played faster where capped
            shapes = []
            for v in LAYER_V:
                Fv = sims[v]["F"]
                nn = max(2, int(round(win[v])))
                Fv = np.concatenate([Fv, np.zeros(max(0, nn - len(Fv)))])[:nn]
                shapes.append(area_resample(Fv))
            out[n].append(dict(shapes=shapes, prate=[int(round(64.0 / (play[v] * dt * FS) * Q12)) for v in V_ANCH],
                               fpk=[float(sims[v]["F"].max()) for v in V_ANCH],
                               tc_ms=[seg[v][0][1] * dt * 1e3 for v in V_ANCH],
                               win_ms=[play[v] * dt * 1e3 for v in V_ANCH],
                               ncont=[len(seg[v]) for v in V_ANCH],
                               impulse=[float(sims[v]["impulse"]) for v in V_ANCH]))
    return out


# ---------------------------------------------------------------- knob tables
def kn_fam():
    out = []
    for v in range(128):
        j = 0 if v < FAM_DETENT[1] else 1 if v < FAM_DETENT[2] else 2 if v < FAM_DETENT[3] else 3
        if j == 3:
            out.append(3 << 8)
            continue
        a = int(round(256 * (v - FAM_DETENT[j]) / (FAM_DETENT[j + 1] - FAM_DETENT[j])))
        out.append(j << 8 | min(a, 255))
    return out


def kn_mass(vb):
    """HAMMER -> contact-rate scale Q12: Tc ~ M^(1/(p+1)) with p = 2 (the families' middle; 1.5 -> 0.4, 2.5 -> 0.29:
    one table serves all four families within 8 %), 1.0 at HAMMER 64."""
    m0 = mass_kg(vb, 64)
    return [int(round(Q12 * (mass_kg(vb, h) / m0) ** (-1.0 / 3.0))) for h in range(128)]


def kn_massi(vb):
    """HAMMER -> force (pamp) scale Q12: the impulse ~ M over the contact time ~ M^(1/3) gives a peak force ~ M^(2/3);
    level_comp of the V1 level change (~ M) is taken back: x (M / M0)^(-level_comp)."""
    m0 = mass_kg(vb, 64)
    c = vb["T7b"]["level_comp"]
    return [int(round(Q12 * (mass_kg(vb, h) / m0) ** (2.0 / 3.0 - c))) for h in range(128)]


def kn_tube():
    """BELL -> tube size s Q15: 0 at 0, -24 dB at 1 rising log-linearly to 1.0 at 64, then 1.0 -> 1.2 (log) at 127."""
    out = [0]
    for v in range(1, 128):
        s = 10 ** (-24.0 * (64 - v) / 63.0 / 20) if v <= 64 else 1.2 ** ((v - 64) / 63.0)
        out.append(int(round(s * 32768)))
    return out


KN_TUBE_TOP = kn_tube()[127] / 32768.0


def kn_nz(vb):
    lo, hi = vb["T13b"]["lvl_db"]
    return [0] + [int(round(32767 * 10 ** ((lo + (hi - lo) * v / 127.0) / 20))) for v in range(1, 128)]


def nz_bq(vb):
    """The contact-noise biquads (Q14 b0 b1 b2 a1 a2): pole pair i over zero pair i, unity gain at the pole."""
    out = []
    for (fp, rp), (fz, rz) in zip(vb["T13b"]["poles"], vb["T13b"]["zeros"]):
        tp, tz = 2 * math.pi * fp / FS, 2 * math.pi * fz / FS
        b = np.array([1.0, -2 * rz * math.cos(tz), rz * rz])
        a = np.array([1.0, -2 * rp * math.cos(tp), rp * rp])
        z = np.exp(-1j * tp * np.arange(3))
        g = abs(np.dot(a, z)) / abs(np.dot(b, z))
        b *= g
        row = [int(round(c * Q14)) for c in (b[0], b[1], b[2], a[1], a[2])]
        assert all(-32768 <= c <= 32767 for c in row), row
        out.append(row)
    return out


# ---------------------------------------------------------------- spec assembly
def strike_tables(fec):
    """BAR_POS[band][17][8] (Q14, each slot normalised to its maximum over the 17 points of the band's key) and per
    band and slot the point where that maximum is."""
    pos, arg = [], []
    for b in range(NBAND):
        n = band_rep(b)
        ng = min(max(n, 53), 89)
        W = np.array(fec[ng]["weights"])                   # [17][V1 V2 V3 V4 T1 T2]
        cols = [0, None, 1, 2, 3, 4, 5, None]
        rows = np.zeros((NPOS, NM), int)
        am = [8] * NM
        for s, c in enumerate(cols):
            if c is None:
                continue
            mx = W[:, c].max()
            if mx > 0:
                rows[:, s] = np.round(W[:, c] / mx * Q14).astype(int)
                am[s] = int(np.argmax(W[:, c]))
        pos.append(rows.tolist())
        arg.append(am)
    return pos, arg


def build_spec(vb, phys, fec, ct, st):
    """The integer spec (what the header holds and the golden model reads) from the design state st:
    st[n] = dict(S (b scale), pamp (float [fam][anch], relative), tors (T1/T2 b factor), gain, kill)."""
    pos, arg = st["pos"], st["pos_arg"]
    keys = []
    for n in range(KEY_LO, KEY_HI + 1):
        k = phys[n]
        s = st["keys"][n]
        F = fec[k["ng"]]
        pw = k["pw"]
        modes = []
        # slot 0 / 1: the pair words
        for slot, wd in ((0, pw["w0"]), (1, pw["w1"])):
            b = 0
            if slot == 0:
                b = s["S"] * s["bphys"][0]
            modes.append(dict(name=SLOT_NAMES[slot], f=pw["f_slot%d" % slot], w=dict(wd), bf=b))
        cols = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        for slot in range(2, NM):
            md = k["modes"][slot - 2]
            if not md["present"] and md["name"] == "X":
                modes.append(dict(name="X", f=0.0, w=rc.mode_coefs(0, 1, 1), bf=0.0))
                continue
            w = rc.mode_coefs(md["f"], md["t60"], md["t60_rel"])
            bf = s["S"] * s["bphys"][slot] * md.get("taper", 1.0) if md["present"] else 0.0
            if slot in s.get("off", ()):                   # never sounds (vibes.json prune): never lit
                bf = 0.0
            if slot in (5, 6):
                bf *= s["tors"]
            modes.append(dict(name=md["name"], f=md["f"], w=w, bf=bf))
        # the design targets per mode (the scan tools read keys[].modes[].f / t60 as in rhodes_spec.json): V1 = the
        # V1-like normal mode (f0, the V1-with-tube T60 at BELL 64), TUBE = the tube slot, the others their slot
        bt = k["bt"]
        modes[0].update(f=k["f0"], t60=bt.t60_v1(), t60_rel=bt.with_damper(LN1000 / k["felt_v1"] - bt.sig_bar).t60_v1(),
                        f_slot=pw["f_slot0"])
        modes[1].update(t60=LN1000 / bt.sig_tube, t60_rel=LN1000 / bt.sig_tube)
        for slot in range(2, NM):
            md = k["modes"][slot - 2]
            modes[slot].update(t60=md["t60"], t60_rel=md["t60_rel"], present=bool(md["present"] and modes[slot]["w"]["th"]
                                                                                   and slot not in s.get("off", ())))
        modes[0]["present"] = modes[1]["present"] = True
        for m in modes:
            m["b"] = rom_safe(int(max(-2 ** 31 + 1, min(2 ** 31 - 1, round(m["bf"] * Q30 * 2 ** 15))))) if m["w"]["th"] else 0
            m["w"] = {kk: rom_safe(int(v)) for kk, v in m["w"].items()}
        keys.append(dict(midi=n, band=k["band"], flags=k["flags"] | (2 if (modes[5]["w"]["th"] or modes[6]["w"]["th"]) else 0),
                         modes=modes, k_bt=rom_safe(pw["k_bt"]), k_tb=rom_safe(pw["k_tb"]), wt=rom_safe(pw["wt_q28"]),
                         G=pw["G"], gain=int(round(s.get("gain", 65536))), kill=int(s.get("kill", 1)),
                         life=int(s.get("life", 4096))))
    prate, pamp = [], []
    for n in range(KEY_LO, KEY_HI + 1):
        prate.append([ct[n][q]["prate"] for q in range(NFAM)])
        pa = st["keys"][n]["pamp"]
        pamp.append([[max(1, min(65535, int(round(x)))) for x in pa[q]] for q in range(NFAM)])
    pulse = []
    for b in range(NBAND):
        r = band_rep(b)
        pulse.append([[list(sh) + [0, 0] for sh in ct[r][q]["shapes"]] for q in range(NFAM)])
    ty = vb["type_rec"]
    li, lw = vlayers()
    vi, vf = vpos()
    flat = [[[Q14] * NPOS for _ in range(NFAM)] for _ in range(NBAND)]
    return dict(gen_version=GEN_VERSION, fs=FS, key_lo=KEY_LO, keys=keys, bar_pos=pos, pos_arg=arg,
                pos_gain=st.get("pos_gain", flat), tube_gain=st.get("tube_gain", flat),
                pos_exact=st["pos_exact"], pulse=pulse,
                prate=prate, pamp=pamp, kn_fam=kn_fam(), kn_mass=kn_mass(vb), kn_massi=kn_massi(vb), kn_tube=kn_tube(),
                kn_nz=kn_nz(vb), nz_bq=nz_bq(vb), mot_cos=list(btm.MOT_COS),
                fam_names=[f["name"] for f in vb["T7c"]["families"]], fam_detent=FAM_DETENT,
                type=dict(tube_cents=0, mot_gc=int(round(vb["T6c"]["g_c"] * 32768)), tors_db=ty["tors_q15"],
                          nbody=ty["nbody"], drive_row=ty["drive_row"], sleep_rel=ty["sleep_rel"], budget=ty["budget"]),
                decay_k=[int(round(Q16 / 2 ** ((v - 64) / 32))) for v in range(128)],
                tune_t=[rom_safe(int(round((2 ** (c / 1200) - 1) * Q30))) for c in range(-50, 51)],
                v_anchors=V_ANCH, layer_v=LAYER_V, vlay_i=li, vlay_w=lw, vpos_i=vi, vpos_f=[min(255, x) for x in vf])


# ---------------------------------------------------------------- calibration with the golden model
def render_chunks(spec, keys, vels, dur, knobs, chunk=400, **kw):
    outs = []
    for i in range(0, len(keys), chunk):
        kn = knobs[i:i + chunk] if isinstance(knobs, list) else knobs
        outs.append(gm.render_batch(spec, keys[i:i + chunk], vels[i:i + chunk], dur, kn, sleep=False, **kw))
    if kw.get("parts") or kw.get("slots"):
        return outs
    return np.concatenate(outs)


def levels(y, t0=0.05, t1=0.30):
    return np.array([gm.rms_db(r, t0, t1) for r in y])


def lufs_k(x, t0=0.0, t1=1.0, fs=FS):
    """BS.1770 K-weighted loudness (LUFS re Q15 full scale) of one mono render over [t0, t1] (the S9 measure,
    bar_measure.lufs, at the golden model's 44.1 kHz)."""
    from scipy.signal import sosfilt
    import bar_measure as bmz
    y = sosfilt(bmz.k_weight_sos(fs), np.asarray(x, float) / 32767.0)[int(t0 * fs):int(t1 * fs)]
    return -0.691 + 10 * math.log10(float(np.mean(y * y)) + 1e-15)


def card_points():
    """the card value (raw 0..127) that lands exactly on each of the 17 points (bar_init: q = raw 4096 / 127, the
    point q >> 8): the smallest raw with q >> 8 = p"""
    return [next(r for r in range(128) if (r * 4096 // 127) >> 8 == p) for p in range(NPOS)]


def card_gains(vb, spec, keys):
    """BAR_POSG / BAR_TUBEG [band][family][17] (Q14): the output gain per band and mallet family at each TONE / BELL
    point, from the golden model's loudness (K-weighted over card_level t_s, v100, HAMMER 64, the family at its
    detent, the other cards at their defaults, the mean over the band's keys) re the default point (TONE 64 / BELL
    64 = point 8, exactly 1.0). Per family because the cards' level change depends on the mallet: at the bar's
    centre a RUBBER strike's V3 (an antinode there) lifts the level 5..10 LU where SOFT YARN's falls 0..4. The level
    the card moves is scaled to the allowed span (tone_span_db / bell_span_db): 0 = held, s = the physical curve's
    shape over s dB."""
    cl = vb["card_level"]
    t0, t1 = cl["t_s"]
    pts = card_points()
    bands = [BANDS["of"][n] for n in keys]
    out = {}
    rep = []
    for card, span in (("tone", cl["tone_span_db"]), ("bell", cl["bell_span_db"])):
        tab = [[None] * NFAM for _ in range(NBAND)]
        spans = []
        for q, bark in enumerate(FAM_DETENT):
            kn = [dict(gm.DEFAULT_KNOBS, bark=bark, **{card: r}) for r in pts for _ in keys]
            y = render_chunks(spec, keys * NPOS, [100] * (len(keys) * NPOS), t1 + 0.01, kn, chunk=440)
            L = np.array([lufs_k(r, t0, t1) for r in y]).reshape(NPOS, len(keys))
            D = L - L[8:9, :]                              # re the default point (8 = raw 64, its fraction 16 / 256)
            for b in range(NBAND):
                ks = [i for i, bb in enumerate(bands) if bb == b]
                d = D[:, ks].mean(axis=1) if ks else np.zeros(NPOS)
                sp = float(d.max() - d.min())
                sc = min(1.0, span / sp) if sp > 0 else 1.0
                g = 10 ** ((sc * d - d) / 20)               # the level becomes sc x the physical delta
                row = [int(round(min(65535, x * Q14))) for x in g]
                row[8] = Q14
                tab[b][q] = row
                spans.append(sp)
        out[card] = tab
        rep.append("%s span %.1f..%.1f dB -> %.1f" % (card.upper(), min(spans), max(spans), span))
    return out["tone"], out["bell"], "; ".join(rep)


def voicing_peaks(vb, spec, keys):
    """every key's largest single-strike output peak (Q15 units) at v127 over card_level peak_grid (the golden model,
    no contact noise), and the voicing that gives it"""
    g = vb["card_level"]["peak_grid"]
    pk = np.zeros(len(keys))
    worst = [""] * len(keys)
    for tone in g["tone"]:
        for bell in g["bell"]:
            for bark in g["bark"]:
                for ham in g["hammer"]:
                    kn = dict(gm.DEFAULT_KNOBS, tone=tone, bell=bell, bark=bark, hammer=ham)
                    y = render_chunks(spec, keys, [127] * len(keys), g["dur_s"], kn, chunk=440)
                    p = np.abs(y).max(axis=1)
                    for i in np.flatnonzero(p > pk):
                        pk[i] = p[i]
                        worst[i] = "TONE %d BELL %d MALLET %d HAMMER %d" % (tone, bell, bark, ham)
    return pk, worst


def build():
    vb = load_vibes(strict="--strict" in sys.argv)
    fec = fe_all(vb)
    print("tube: solving zeta_r at the reference notes", file=sys.stderr)
    zt = tube_zetas(vb)
    phys = {}
    for n in range(KEY_LO, KEY_HI + 1):
        for _ in range(20):                         # a pair whose integer map is not contracting: more tube damping
            k = key_physics(vb, n, fec, zt)
            k["pw"] = pair_words_safe(k["bt"], k["wt"], k["felt_v1"], k["f0"])
            k["grid"] = map_check_words(k)
            if k["grid"][0] < 1.0:
                break
            zt[n] = (min(vb["T4a"]["hi_pct"] / 100, zt[n][0] * 1.25), zt[n][1] | 8)
            print("key %d: unstable map, zeta_r -> %.4f" % (n, zt[n][0]), file=sys.stderr)
        phys[n] = k
    print("mallets: contact simulations", file=sys.stderr)
    ct = contacts(vb, phys, fec)
    BANDS.clear()
    BANDS.update(assign_bands(phys, ct))
    for n in range(KEY_LO, KEY_HI + 1):
        phys[n]["band"] = BANDS["of"][n]
    print("bands: reps %s, worst shape error %.2f dB" % (BANDS["rep"], max(BANDS["err"])), file=sys.stderr)
    pos, arg = strike_tables(fec)
    pex = [[POS_DEF if s in (0, 2, 3, 4) and pos[b][POS_DEF][s] >= POS_EXACT_MIN * Q14 else arg[b][s] for s in range(NM)]
           for b in range(NBAND)]
    rexp = vb["R1"]["exp"]
    st = dict(pos=pos, pos_arg=arg, pos_exact=pex, keys={})
    for n in range(KEY_LO, KEY_HI + 1):
        k = phys[n]
        W = np.array(fec[k["ng"]]["weights"])
        fref = max(max(c["fpk"]) for c in ct[n])
        pamp_max = math.floor(32767 * 4096 / max(kn_massi(vb)))
        pamp = [[x / fref * pamp_max for x in ct[n][q]["fpk"]] for q in range(NFAM)]
        # b per unit Q15 force (real y units): (F_N per F unit) dt |phi(x_q)| / P_band(q) (f/f1)^R1 / omega: the
        # band's strike table (BAR_POS, the band's representative key) times b gives the key's OWN weight exactly at
        # the point q = pos_exact[band][slot]: the default strike (TONE raw 64, point POS_DEF) for V1..V4, the band's
        # maximum point for the torsion (zero on the centreline). Elsewhere the band's shape stands for the key's.
        cols = {0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        fs_ = {0: k["f0"]}
        for slot in range(2, 7):
            fs_[slot] = k["modes"][slot - 2]["f"]
        bph = [0.0] * NM
        for slot, c in cols.items():
            q = pex[k["band"]][slot]
            phi = W[q, c] * Q14 / pos[k["band"]][q][slot]
            bph[slot] = (fref / pamp_max) / FS * phi * (fs_[slot] / k["f0"]) ** rexp / (2 * math.pi * fs_[slot])
        st["keys"][n] = dict(S=1.0, bphys=bph, pamp=pamp, tors=1.0)
        st.setdefault("pamp_phys", []).append([list(r) for r in pamp])
    keys = list(range(KEY_LO, KEY_HI + 1))
    spec = build_spec(vb, phys, fec, ct, st)

    def rescale_b(target=PEAK_SLOT):
        """b scale: the worst voicing's slot peaks (RUBBER v127 HAMMER 127 BELL 127, TONE raw 0 / 64 / 127) at target
        (real state units, 1.0 = 2^25: a x2 re-strike stays under 2^26)."""
        nonlocal spec
        pk = np.zeros(len(keys))
        for tone in (0, 64, 127):
            kn = dict(gm.DEFAULT_KNOBS, tone=tone, bark=127, hammer=127, bell=127)
            for i0 in range(0, len(keys), 44):
                ks = keys[i0:i0 + 44]
                _, p = gm.render_batch(spec, ks, [127] * len(ks), 0.6, kn, sleep=False, peaks=True)
                pk[i0:i0 + len(ks)] = np.maximum(pk[i0:i0 + len(ks)], p.max(axis=1))
        for i, n in enumerate(keys):
            st["keys"][n]["S"] *= target / pk[i]
        spec = build_spec(vb, phys, fec, ct, st)
        return pk

    rescale_b()
    # torsion level (T9c): T1 slot peak re V1 slot peak in the first 20 ms at TONE +63, MED YARN, v100
    kn = dict(gm.DEFAULT_KNOBS, tone=127, bark=FAM_DETENT[1])
    r = gm.render_batch(spec, keys, [100] * len(keys), 0.02, kn, slots=True, sleep=False)
    for i, n in enumerate(keys):
        v1 = np.abs(r["slots"][i, 0]).max()
        t1 = np.abs(r["slots"][i, 5]).max()
        if t1 > 0 and v1 > 0:
            st["keys"][n]["tors"] = 10 ** ((vb["T9c"]["t1_db"] - 20 * math.log10(t1 / v1)) / 20)
    spec = build_spec(vb, phys, fec, ct, st)
    # radiation efficiency per upper mode (R1b): one constant per mode over all keys, the median offset of the model's
    # level re V1 (TONE 0, MED YARN, v100, 30..530 ms, the reference procedure's window) from the measured levels
    rad = {}
    for it in range(2):
        for slot, nm in ((2, "V2"), (3, "V3"), (4, "V4")):
            ref = {int(k): v for k, v in vb["T2"][nm]["level_db"].items()}
            ks = [n for n in sorted(ref) if phys[n]["modes"][slot - 2]["present"]]
            y = gm.render_batch(spec, ks, [100] * len(ks), 0.6, dict(gm.DEFAULT_KNOBS, bark=FAM_DETENT[1]), sleep=False)
            off = []
            for n, r in zip(ks, y):
                l1 = gm.band_level(r, phys[n]["f0"], 0.03, 0.53, 0.015)
                lm = gm.band_level(r, phys[n]["modes"][slot - 2]["f"], 0.03, 0.53, 0.015)
                off.append(ref[n] - (lm - l1))
            d = float(np.median(off))
            rad[nm] = rad.get(nm, 0.0) + d
            for n in keys:
                st["keys"][n]["bphys"][slot] *= 10 ** (d / 20)
        spec = build_spec(vb, phys, fec, ct, st)
    print("radiation trims (dB): " + " ".join("%s %+.1f" % kv for kv in sorted(rad.items())), file=sys.stderr)
    st["rad_db"] = rad
    # V4 per key (R1b _doc_v4): a straight-line trim in key that puts MED YARN v100 V4 (re V1, 30..530 ms) on the
    # straight-line fit of the measured levels over v4_fit_keys, never brighter at the brightest voicing than the
    # single-constant model ever is in F3..F6 (the ceiling); held below the fit keys, returned to the constant above
    fa, fb = vb["R1b"]["v4_fit_keys"]
    ret = vb["R1b"]["v4_return_db_per_key"]
    ref = {int(k): v for k, v in vb["T2"]["V4"]["level_db"].items()}
    tline = np.polyfit(sorted(ref), [ref[n] for n in sorted(ref)], 1)
    ks = [n for n in range(fa, fb + 1) if phys[n]["modes"][2]["present"]]
    y = gm.render_batch(spec, ks, [100] * len(ks), 0.6, dict(gm.DEFAULT_KNOBS, bark=FAM_DETENT[1]), sleep=False)
    m0 = [gm.band_level(r, phys[n]["modes"][2]["f"], 0.03, 0.53, 0.015) - gm.band_level(r, phys[n]["f0"], 0.03, 0.53, 0.015)
          for n, r in zip(ks, y)]
    cl4 = np.polyfit(ks, [float(np.polyval(tline, n)) - m for n, m in zip(ks, m0)], 1)
    bright = np.full(len(keys), -999.0)                   # V4 onset (free ringing) re V1 at the brightest voicing
    for tone in (0, 64, 127):
        kn = dict(gm.DEFAULT_KNOBS, bark=FAM_DETENT[3], hammer=0, tone=tone)
        r = gm.render_batch(spec, keys, [127] * len(keys), 0.05, kn, sleep=False, slots=True)["slots"]
        p = np.abs(r[:, :, int(0.005 * FS):]).max(axis=2)
        bright = np.maximum(bright, 20 * np.log10(p[:, 4] / p[:, 0] + 1e-30))
    live4 = [i for i, n in enumerate(keys) if spec["keys"][i]["modes"][4]["b"]]
    sm4 = {keys[i]: float(np.mean([bright[j] for j in live4 if abs(keys[j] - keys[i]) <= 3])) for i in live4}
    ceil4 = max(sm4[n] for n in sm4 if 53 <= n <= 89) + vb["R1b"]["v4_ceiling_db"]
    b4 = {n: st["keys"][n]["bphys"][4] for n in keys}

    def v4_trim(n):
        d = float(np.polyval(cl4, min(max(n, fa), fb)))
        if n > fb:
            d = math.copysign(max(0.0, abs(d) - ret * (n - fb)), d)
        return min(d, ceil4 - sm4[n]) if n in sm4 else d
    v4tr = {n: v4_trim(n) for n in keys}
    for n in keys:
        st["keys"][n]["bphys"][4] = b4[n] * 10 ** (v4tr[n] / 20)
    spec = build_spec(vb, phys, fec, ct, st)
    print("V4 trim per key (dB re the R1b constant): %+.1f at %d .. %+.1f at %d (line %+.1f, the brightest-voicing ceiling "
          "%+.1f dB re V1), 0 from %d" % (v4tr[fa], fa, v4tr[fb], fb, float(np.polyval(cl4, fb)), ceil4,
                                         min(n for n in keys if n > fb and v4tr[n] == 0)), file=sys.stderr)
    st["v4_trim_db"] = [rad["V4"] + v4tr[n] for n in keys]
    # velocity trim per family + family level match (MED YARN is the reference)
    cal = vb["calib"]
    ref_a = V_ANCH.index(104)
    for it in range(int(cal["trim_iterations"])):
        L = np.zeros((len(keys), NFAM, len(V_ANCH)))
        allk, allv, allkn, idx = [], [], [], []
        for q in range(NFAM):
            for a, v in enumerate(V_ANCH):
                for i, n in enumerate(keys):
                    allk.append(n); allv.append(v); allkn.append(dict(gm.DEFAULT_KNOBS, bark=FAM_DETENT[q])); idx.append((i, q, a))
        y = render_chunks(spec, allk, allv, 0.32, allkn, chunk=440)
        lv = levels(y)
        for (i, q, a), l in zip(idx, lv):
            L[i, q, a] = l
        rel = L - L[:, :, ref_a:ref_a + 1]
        corr = np.zeros_like(L)
        for q in range(NFAM):
            tgt = np.maximum.accumulate(np.median(rel[:, q, :], axis=0))
            corr[:, q, :] = tgt[None, :] - rel[:, q, :]
            corr[:, q, :] += (L[:, 1, ref_a] - L[:, q, ref_a])[:, None]       # families level-matched at v104
        corr = np.clip(corr, -cal["trim_max_db"], cal["trim_max_db"])
        print("velocity / family fit %d: worst %.2f dB, median |err| %.2f dB" % (it, np.abs(corr).max(), np.median(np.abs(corr))),
              file=sys.stderr)
        for i, n in enumerate(keys):
            pa = st["keys"][n]["pamp"]
            for q in range(NFAM):
                for a in range(len(V_ANCH)):
                    pa[q][a] *= 10 ** (cal["trim_gain"] * corr[i, q, a] / 20)
        # keep pamp in range: the key's largest at pamp_max, the factor moved into b
        pamp_max = math.floor(32767 * 4096 / max(kn_massi(vb)))
        for n in keys:
            pa = st["keys"][n]["pamp"]
            m = max(max(r_) for r_ in pa)
            f = pamp_max / m
            st["keys"][n]["pamp"] = [[x * f for x in r_] for r_ in pa]
            st["keys"][n]["S"] /= f
        spec = build_spec(vb, phys, fec, ct, st)
    rescale_b()
    # the TONE / BELL level compensation per band (card_level): a card that is not a volume knob keeps the level
    st["pos_gain"], st["tube_gain"], cl_rep = card_gains(vb, spec, keys)
    spec = build_spec(vb, phys, fec, ct, st)
    print("card level: " + cl_rep, file=sys.stderr)
    # gain: v100 at the default knobs, RMS 50..300 ms -> gain_target_dbfs; a global cap for the v127 RUBBER peak
    y = render_chunks(spec, keys, [100] * len(keys), 0.32, dict(gm.DEFAULT_KNOBS))
    g_now = np.array([spec["keys"][n - KEY_LO]["gain"] for n in keys], float)
    rms = np.array([math.sqrt(np.mean(r[2205:13230] ** 2)) for r in y]) + 1e-12
    target = 10 ** (cal["gain_target_dbfs"] / 20) * 32768
    g_raw = g_now * target / rms
    yp = render_chunks(spec, keys, [127] * len(keys), 0.3, dict(gm.DEFAULT_KNOBS, bark=127, hammer=127, bell=127))
    pk = np.array([np.max(np.abs(r)) for r in yp]) * g_raw / g_now + 1e-12
    capg = min(1.0, float(np.percentile(10 ** ((cal["peak_dbfs"] + 3) / 20) * 32768 / pk, 10)))
    print("gain: target %.1f dBFS, global peak cap %.2f dB" % (cal["gain_target_dbfs"], 20 * math.log10(capg)), file=sys.stderr)
    for i, n in enumerate(keys):
        g = min(g_raw[i] * capg, 2 ** 31 - 1)
        k = phys[n]
        st["keys"][n]["gain"] = g
        wt = abs(k["pw"]["wt_q28"]) / Q28 * KN_TUBE_TOP
        st["keys"][n]["kill"] = max(1, int(round(4.0 / (g * max(1.0, wt)) * Q25)))
        t60d = k["bt"]._with(sig_bar=k["bt"].sig_bar / 2 ** (63 / 32)).t60_v1()     # DECAY 127
        st["keys"][n]["life"] = int(min(65535, math.ceil((2 * max(t60d, k["bt"].t60_v1()) + 1.0) * FS / 256)))
    spec = build_spec(vb, phys, fec, ct, st)
    # the Rhodes level per key (calib _doc_st77): each key's v100 loudness (K-weighted, lufs_t) = the STAGE 77 golden
    # render's at the same key (both no effects), the ST77 curve moving-averaged over +-lufs_smooth_keys
    import rhodes_model as rm
    rs = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
    rk = {k["midi"]: k for k in rs["keys"]}
    l0, l1 = cal["lufs_t"]
    lb = np.array([lufs_k(r, l0, l1) for r in render_chunks(spec, keys, [100] * len(keys), l1, dict(gm.DEFAULT_KNOBS))])
    lr = np.array([lufs_k(r, l0, l1) for r in rm.render_batch([rk[n] for n in keys], rs["pickup"], [100] * len(keys), l1,
                                                               gains=[rk[n]["gain"] for n in keys])])
    sk = int(cal["lufs_smooth_keys"])
    lrs = np.array([np.mean(lr[max(0, i - sk):i + sk + 1]) for i in range(len(keys))])
    corr = lrs - lb
    for i, n in enumerate(keys):
        g = min(st["keys"][n]["gain"] * 10 ** (corr[i] / 20), 2 ** 31 - 1)
        st["keys"][n]["kill"] = max(1, int(round(st["keys"][n]["kill"] * st["keys"][n]["gain"] / g)))
        st["keys"][n]["gain"] = g
    st["st77_trim_db"] = float(np.median(corr))
    st["st77_lufs"] = [float(x) for x in lrs]
    spec = build_spec(vb, phys, fec, ct, st)
    print("ST77 level per key: corrections %+.2f .. %+.2f dB (median %+.2f)" % (corr.min(), corr.max(), np.median(corr)),
          file=sys.stderr)
    # the per-voice peak ceiling over the whole voicing space (card_level peak_grid): every key, v127, every
    # TONE x BELL x MALLET x HAMMER corner of the grid; a key over the ceiling is turned down (reported)
    pk, worst = voicing_peaks(vb, spec, keys)
    ceil_ = 10 ** (vb["card_level"]["peak_ceiling_dbfs"] / 20) * 32768
    over = [(n, 20 * math.log10(pk[i] / 32768)) for i, n in enumerate(keys) if pk[i] > ceil_]
    for i, n in enumerate(keys):
        if pk[i] > ceil_:
            f = ceil_ / pk[i]
            st["keys"][n]["gain"] *= f
            st["keys"][n]["kill"] = max(1, int(round(st["keys"][n]["kill"] / f)))
    st["peak_dbfs"] = [float(20 * math.log10(min(pk[i], ceil_) / 32768)) for i in range(len(keys))]
    st["peak_worst"] = worst
    spec = build_spec(vb, phys, fec, ct, st)
    print("voicing peaks: worst %.2f dBFS (key %d, %s), ceiling %.1f dBFS, keys turned down: %s" % (
        max(20 * math.log10(x / 32768) for x in pk), keys[int(np.argmax(pk))], worst[int(np.argmax(pk))],
        vb["card_level"]["peak_ceiling_dbfs"], ", ".join("%d (%+.1f)" % o for o in over) or "none"), file=sys.stderr)
    # slots that never sound (vibes.json prune): the loudest single strike of the voicing grid, first t_s
    pr = vb["prune"]
    rel = np.zeros((len(keys), NM))
    for q in FAM_DETENT:
        for h in (0, 64, 127):
            for tone in (0, 32, 64, 96, 127):
                kn = dict(gm.DEFAULT_KNOBS, bark=q, hammer=h, tone=tone)
                r = gm.render_batch(spec, keys, [127] * len(keys), pr["t_s"], kn, sleep=False, slots=True)["slots"]
                p = np.abs(r[:, :, int(pr["t0_s"] * FS):]).max(axis=2)      # free ringing, after the contact
                rel = np.maximum(rel, p / p[:, :1])
    npr = 0
    for i, n in enumerate(keys):
        off = [s for s in range(2, NM) if spec["keys"][i]["modes"][s]["b"] and rel[i, s] < 10 ** (pr["db"] / 20)]
        st["keys"][n]["off"] = off
        npr += len(off)
    spec = build_spec(vb, phys, fec, ct, st)
    print("pruned %d key x slot pairs (under %.0f dB re V1 at every voicing)" % (npr, pr["db"]), file=sys.stderr)
    st["prune_rel_db"] = [[float(20 * math.log10(x)) if x > 0 else -999.0 for x in row] for row in rel]
    # the stability grid (§4.5) for every key
    print("stability grid", file=sys.stderr)
    grid = []
    for n in keys:
        k = phys[n]
        rho, derr, cents, npts = k["grid"]
        assert rho < 1.0, ("unstable pair (raise zeta_r there)", n, rho)
        grid.append(dict(midi=n, rho=rho, decay_err=derr, v1_cents=cents, points=npts))
    # the design floats the tests read
    design = []
    for n in keys:
        k = phys[n]
        bt = k["bt"]
        d = dict(midi=n, f0=k["f0"], flags=spec["keys"][n - KEY_LO]["flags"], ng=k["ng"], t1a=k["t1a"], t1b=k["t1b"],
                 t60_v1=bt.t60_v1(), t60_v1_bell127=bt.with_bell(KN_TUBE_TOP).t60_v1(), felt_v1=k["felt_v1"],
                 zeta_r=k["zeta_r"], zeta_flag=k["zeta_flag"], zeta_n=bt.zetas()[0], sigma=bt.sigma,
                 sigma_wanted=bt.sigma_wanted, sigma_clamp=k["sigma_clamp"], weak_limited=k["weak"],
                 sigma_weak_max=bt.sigma_weak_max(s_max=KN_TUBE_TOP, margin=vb["T5a"]["weak_margin"]),
                 peak_ms=k["peak_ms"], peak_formula_ms=1000 * bt.t_peak_formula(), wt=k["wt"], G=k["pw"]["G"],
                 kc=k["pw"]["kc"], tube_cents=k["tube_cents"], f_tube=bt.f_tube, t60_tube=LN1000 / bt.sig_tube,
                 modes=[dict(name=m["name"], f=m["f"], ratio=m["ratio"], t60=m["t60"], t60_rel=m["t60_rel"],
                             present=m["present"]) for m in k["modes"]],
                 fe=dict(ratios=fec[k["ng"]]["ratios"], tors=fec[k["ng"]]["tors"], err_c=fec[k["ng"]]["err_c"],
                         nodes=fec[k["ng"]]["nodes"], hmin_mm=fec[k["ng"]]["hmin"] * 1e3, A=fec[k["ng"]]["A"],
                         L=fec[k["ng"]]["L"], w=fec[k["ng"]]["w"], mass=fec[k["ng"]]["mass"]),
                 mallet=[dict(name=vb["T7c"]["families"][q]["name"], K=fam_K(vb, q), tc_ms=ct[n][q]["tc_ms"],
                              win_ms=ct[n][q]["win_ms"], ncont=ct[n][q]["ncont"], shapes=ct[n][q]["shapes"],
                              prate=ct[n][q]["prate"]) for q in range(NFAM)],
                 grid=grid[n - KEY_LO])
        design.append(d)
    spec["design"] = design
    spec["rad_db"] = st["rad_db"]
    spec["band_rep"] = list(BANDS["rep"])
    spec["st77_trim_db"] = st["st77_trim_db"]
    spec["st77_lufs"] = st["st77_lufs"]
    spec["v4_trim_db"] = st["v4_trim_db"]
    spec["peak_dbfs"] = st["peak_dbfs"]
    spec["peak_worst"] = st["peak_worst"]
    spec["pruned"] = {str(n): st["keys"][n]["off"] for n in keys if st["keys"][n]["off"]}
    spec["prune_rel_db"] = st["prune_rel_db"]
    spec["pamp_phys"] = st["pamp_phys"]
    spec["band_err_db"] = list(BANDS["err"])
    spec["vibes"] = vb
    spec["mallet_v"] = [speed(vb, v) for v in range(128)]
    return spec


# ---------------------------------------------------------------- the header
def emit(spec):
    L = []
    w = L.append
    ty = spec["type"]
    w("/* SPDX-License-Identifier: GPL-3.0-only */")
    w("/* GENERATED by tools/gen_bars.py from docs/model/data/vibes.json - do not edit. */")
    w("/* The VIBES bar engine tables (VIBES PLAN §4.4). Include after rhodes_tables.h (rh_mode_t, RH_NANCH). Run-time")
    w(" * arithmetic the golden model (tools/bars_model.py) assumes:")
    w(" *   BAR_POS: q = raw * 4096 / 127, point q >> 8, w = P[i] + (((P[i+1] - P[i]) * (q & 255)) >> 8); b = km->b * w >> 14;")
    w(" *     T1/T2: b = b * tors_db >> 15, and b = 0, not live, for TONE raw <= 64.")
    w(" *   pulse: per family the two velocity layers (Q15 RH_VLAY_W), then the two families (Q8 KN_FAM & 255) into pbuf;")
    w(" *     prate = (blended BAR_PRATE << 4) * KN_MASS[HAMMER] >> 12, pamp = blended BAR_PAMP * KN_MASSI[HAMMER] >> 12")
    w(" *     (<= 32767); the anchors as rh_load_pulse (RH_VPOS_I / RH_VPOS_F), each family first, then the family blend.")
    w(" *   pair: y0 += k_tb x1 >> 30 (a symmetric spring: tools/bartube.py COUPLE_SIGN), y1 += k_bt x0 >> 30 after slot 0's")
    w(" *     step; rh_x = x0 + (wt x1 >> 28) + the other slots; k_bt, k_tb, wt x KN_TUBE[BELL] >> 15 at note-on.")
    w(" *   BAR_MOT_COS[i] = |cos(pi i / 512)|: t = ph >> 23, c = t < 256 ? COS[t] : COS[512 - t] (one AM period per phase")
    w(" *     cycle, so the AM rate is the FX_LFO_INC rate). */")
    w(f"#define BAR_GEN_VERSION {GEN_VERSION}")
    w(f"#define BAR_KEY_LO {KEY_LO}            /* every MIDI note 21..108 plays its own pitch */")
    w(f"#define BAR_NKEY {NKEY}")
    w(f"#define BAR_NM {NM}                 /* V1 TUBE V2 V3 V4 T1 T2 X */")
    w(f"#define BAR_NFAM {NFAM}               /* SOFT YARN, MED YARN, CORD, RUBBER */")
    w(f"#define BAR_NBAND {NBAND}")
    w(f"#define BAR_NLAYER {NLAYER}")
    w(f"#define BAR_PLEN {PLEN}")
    w(f"#define BAR_NPOS {NPOS}")
    w(f"#define BAR_NTYPE {NTYPE}")
    w(f"#define BAR_FADE_STEP {FADE_STEP}")
    w("")
    w("/* bar_key_t, bar_type_t: defined in firmware/src/bars.c (as rhodes.c holds rh_key_t) */")
    w("static const bar_key_t BAR_KEYS[BAR_NKEY] = {")
    for k in spec["keys"]:
        ms = ",".join("{%d,%d,%d,%d,%d,%d,%d}" % (m["w"]["sh"], m["w"]["ch"], m["w"]["th"], m["w"]["d"], m["w"]["drel"],
                                                   m["w"]["cr"], m["b"]) for m in k["modes"])
        w("    {{%s}, %d, %d, %d, %d, %d, %d, %d, %d}," % (ms, k["k_bt"], k["k_tb"], k["wt"], rom_safe(k["gain"]),
                                                       rom_safe(k["kill"]), k["life"], k["band"], k["flags"]))
    w("};")
    w("static const int16_t BAR_PULSE[BAR_NBAND][BAR_NFAM][BAR_NLAYER][BAR_PLEN + 2] = {")
    for b in spec["pulse"]:
        w("  {" + ",".join("{" + ",".join("{" + ",".join(map(str, sh)) + "}" for sh in fam) + "}" for fam in b) + "},")
    w("};")
    w("static const uint32_t BAR_PRATE[BAR_NKEY][BAR_NFAM][RH_NANCH] = {")
    for k in spec["prate"]:
        w("  {" + ",".join("{" + ",".join(str(rom_safe(x)) for x in q) + "}" for q in k) + "},")
    w("};")
    w("static const uint16_t BAR_PAMP[BAR_NKEY][BAR_NFAM][RH_NANCH] = {")
    for k in spec["pamp"]:
        w("  {" + ",".join("{" + ",".join(map(str, q)) + "}" for q in k) + "},")
    w("};")
    w("static const int16_t BAR_POS[BAR_NBAND][BAR_NPOS][BAR_NM] = {")
    for b in spec["bar_pos"]:
        w("  {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in b) + "},")
    w("};")
    w("/* the output gain per band and mallet family at the 17 TONE / BELL points (Q14; point 8 = 1.0): per family as")
    w(" * BAR_POS, g = R[i] + ((R[i+1] - R[i]) (q & 255) >> 8), then the families as the pulse (KN_FAM's Q8 fraction);")
    w(" * gain = key gain x g(TONE) >> 14 x g(BELL) >> 14 at note-on (vibes.json card_level) */")
    for nm, key in (("BAR_POSG", "pos_gain"), ("BAR_TUBEG", "tube_gain")):
        w("static const uint16_t %s[BAR_NBAND][BAR_NFAM][BAR_NPOS] = {" % nm)
        for b in spec[key]:
            w("  {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in b) + "},")
        w("};")
    w("static const int32_t BAR_BODY[BAR_NTYPE][2][5] = {")
    w("  {{%d,0,0,0,0},{%d,0,0,0,0}}," % (Q28, Q28))
    w("};")
    w("static const bar_type_t BAR_TYPE[BAR_NTYPE] = {   /* tube_cents, mot_gc, tors_db, nbody, drive_row, sleep_rel, budget */")
    w("  {%d, %d, %d, %d, %d, %d, %d}," % (ty["tube_cents"], ty["mot_gc"], ty["tors_db"], ty["nbody"], ty["drive_row"],
                                          ty["sleep_rel"], ty["budget"]))
    w("};")
    w("static const uint16_t KN_FAM[128] = {" + ",".join(map(str, spec["kn_fam"])) + "};")
    w("static const uint16_t KN_MASS[128] = {" + ",".join(map(str, spec["kn_mass"])) + "};")
    w("static const uint16_t KN_MASSI[128] = {" + ",".join(map(str, spec["kn_massi"])) + "};")
    w("static const uint16_t KN_TUBE[128] = {" + ",".join(map(str, spec["kn_tube"])) + "};")
    w("static const uint16_t KN_NZ[128] = {" + ",".join(map(str, spec["kn_nz"])) + "};")
    w("static const int16_t NZ_BQ[2][5] = {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in spec["nz_bq"]) + "};")
    w("static const int16_t BAR_MOT_COS[257] = {" + ",".join(map(str, spec["mot_cos"])) + "};")
    fams = spec["vibes"]["T7c"]["families"]
    w("/* mallet families (K N/m^p, p): " + ", ".join("%s %.3g %.1f" % (f["name"], fam_K(spec["vibes"], q), f["p"])
                                                      for q, f in enumerate(fams)) + " */")
    body = "\n".join(L) + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return body + f"/* sha256(body) = {digest} */\n"


def all_words(spec):
    """Every 32-bit word the header holds (for the ROM-window check)."""
    out = []
    for k in spec["keys"]:
        for m in k["modes"]:
            out += [m["w"][x] for x in ("sh", "ch", "th", "d", "drel", "cr")] + [m["b"]]
        out += [k["k_bt"], k["k_tb"], k["wt"], k["gain"], k["kill"]]
    for k in spec["prate"]:
        for q in k:
            out += q
    return out


def summary(spec):
    D = {d["midi"]: d for d in spec["design"]}
    K = {k["midi"]: k for k in spec["keys"]}
    for n in (40, 53, 60, 69, 77, 89, 100):
        d, k = D[n], K[n]
        print("key %3d f0 %7.1f flags %d | T1b %.2f got %.2f (T1a %.1f) zr %.2f%% sig %.4f%s | pk %3.0f ms | G %d wt %.1f | "
              "V2 %.0fHz %.2fs V3 %.0fHz %.2fs T1 %.2fx | gain %d life %d" % (
                  n, d["f0"], d["flags"], d["t1b"], d["t60_v1"], d["t1a"], 100 * d["zeta_r"], d["sigma"],
                  " CLAMP" if d["sigma_clamp"] else "", d["peak_ms"], d["G"], d["wt"], d["modes"][0]["f"], d["modes"][0]["t60"],
                  d["modes"][1]["f"], d["modes"][1]["t60"], d["modes"][3]["ratio"], k["gain"], k["life"]))
    g = [d["grid"] for d in spec["design"]]
    print("stability: max rho %.9f, worst decay error %.3g, V1 cents %+.3f..%+.3f, %d points" % (
        max(x["rho"] for x in g), max(x["decay_err"] for x in g), min(x["v1_cents"] for x in g),
        max(x["v1_cents"] for x in g), sum(x["points"] for x in g)))


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    spec = build()
    for v in all_words(spec):
        assert not (0xFFC00000 <= (v & 0xFFFFFFFF) <= 0xFFCFFFFF), v
    hdr = emit(spec)
    hp = os.path.join(out, "bars_tables.h") if out else os.path.join(ROOT, "firmware/gen/bars_tables.h")
    jp = os.path.join(out, "bars_spec.json") if out else os.path.join(ROOT, "docs/model/data/bars_spec.json")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    open(hp, "w").write(hdr)
    js = json.dumps(spec, indent=0, sort_keys=True, default=float, separators=(",", ":"))
    open(jp, "w").write(js + "\n")
    summary(spec)


if __name__ == "__main__":
    main()
