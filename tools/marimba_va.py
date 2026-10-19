#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate VA-M (MARIMBA PLAN §7): the golden model (tools/bars_model.py) on the generated, quantised marimba spec
(docs/model/data/marimba_spec.json) against the numbers of the plan's §0 / §2 and the data (marimba.json), BEFORE any
C (the cards audible in the model first: the VIBES lesson 7046af2 / d3e61f1).

    python3 tools/marimba_va.py      -> every check with its number and PASS / FAIL / REPORT (build/marimba_va/report.txt)
    (also: tools/bars_va.py --type marimba)

VA-M1 frequencies, M2 bar decays and the V1-with-tube brackets, M3 the coupling regime, M4 the tube's bloom, M5 the
mallets, M6 the strike position, M7 the contact noise (bars.c's integer noise path, emulated: the golden model has
none), M8 the hand mute, M9 every card audible, M10 the level, M11 the stability grid, M12 the FE. Each check is a
number with its tolerance; nothing here claims "better" (that is the listening's: packs A-D, gen_bars --listen).
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bars_model as gm  # noqa: E402
import bartube as btm    # noqa: E402

FS = gm.FS
SPEC_PATH = os.path.join(ROOT, "docs", "model", "data", "marimba_spec.json")
LN1000 = 6.907755279
SOFT, MED, DEFAULT, HARD, RUBBER = 0, 42, 64, 85, 127   # MALLET at the families (the shared detents)
REG = (36, 48, 54, 60, 72, 84, 96)                     # the §2.2 register keys


def load_spec(path=SPEC_PATH):
    return json.load(open(path))


def render(spec, keys, vels, dur, knobs=None, **kw):
    kw.setdefault("sleep", False)
    return gm.render_batch(spec, list(keys), list(vels), dur, knobs, **kw)


def lufs(x, t0=0.0, t1=1.0):
    import bar_measure as bmz
    from scipy.signal import sosfilt
    y = sosfilt(bmz.k_weight_sos(FS), np.asarray(x, float) / 32767.0)[int(t0 * FS):int(t1 * FS)]
    return -0.691 + 10 * math.log10(float(np.mean(y * y)) + 1e-20)


def flat_start(n):
    """a window flat from the onset, a raised-cosine fade over its last quarter: a Hann window from the strike is
    -48 dB at 10 ms of a 0.5 s segment, blind to the marimba's attack (its upper modes are gone by 20-50 ms)"""
    w = np.ones(n)
    m = n // 4
    w[n - m:] = 0.5 * (1 + np.cos(np.pi * np.arange(m) / m))
    return w


def centroid(x, t0=0.0, t1=0.3):
    seg = np.asarray(x[int(t0 * FS):int(t1 * FS)], float)
    S = np.abs(np.fft.rfft(seg * flat_start(len(seg)))) ** 2
    fr = np.fft.rfftfreq(len(seg), 1 / FS)
    return float(np.sum(fr * S) / (np.sum(S) + 1e-30))


def third_oct(seg):
    """bar_cards.third_oct's bands (50 Hz .. 16 kHz) with the flat-start window"""
    import bar_cards as bc
    seg = np.asarray(seg, float)
    S = np.abs(np.fft.rfft(seg * flat_start(len(seg)))) ** 2
    fr = np.fft.rfftfreq(len(seg), 1 / FS)
    return np.array([10 * math.log10(float(S[(fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))].sum()) + 1e-30)
                     for c in bc.BANDS])


def dist(a, b, t1=0.5):
    """bar_cards.dist (the spectral distance, dB RMS, level-aligned) on 0..t1 s of two renders"""
    import bar_cards as bc
    n = int(t1 * FS)
    return bc.dist(third_oct(a[:n]), third_oct(b[:n]))


def partial_db(x, f, t0, t1, rel=0.03):
    """a partial's energy (dB) within +-rel of f over [t0, t1], flat-start window (gm.band_level's Hann would weigh
    the middle of the segment, where the marimba's upper modes are long gone)"""
    seg = np.asarray(x[int(t0 * FS):int(t1 * FS)], float)
    nf = 1 << max(14, int(math.ceil(math.log2(len(seg)))) + 2)
    S = np.abs(np.fft.rfft(seg * flat_start(len(seg)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    m = (fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)
    return 10 * math.log10(max(float(np.sum(S[m])), 1e-30))


def band_env_db(x, f, rel=0.03, hop=64):
    """the f band's envelope (dB): FFT brick-wall +-rel, |analytic signal|, every hop samples"""
    from scipy.signal import hilbert
    X = np.fft.rfft(np.asarray(x, float))
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    X[(fr < (1 - rel) * f) | (fr > (1 + rel) * f)] = 0
    e = np.abs(hilbert(np.fft.irfft(X, len(x))))[::hop]
    return 20 * np.log10(e + 1e-12), np.arange(len(e)) * hop / FS


def t60_fit(x, f, drop=30.0, start_db=3.0, rel=None):
    """T60 of the f band by a straight-line fit of its envelope from start_db under its peak down drop dB more (the
    plan's '30 dB fit': a 60 dB fit runs into the floor on a 0.1 s note), x 60 / drop.

    rel None (P4): the band +-clip(16 Hz / f, 3 %, 15 %) and x zero-padded to 3x its length (tools/mrb_ref_measure.py's
    unbiased fit). The +-3 % brick-wall band of before is narrower than a low note's decay line (C2: +-2 Hz against a
    Lorentzian 1.4 Hz wide) and, being circular, wraps the strike onto the tail: it read a pure exponential 26 % long
    at C2, 8 % at C3, 6 % at C4 (0-1 % from C5 up)"""
    if rel is None:
        rel = float(np.clip(16.0 / f, 0.03, 0.15))
        x = np.concatenate([np.asarray(x, float), np.zeros(2 * len(x))])
    e, t = band_env_db(x, f, rel)
    i = int(np.argmax(e))
    hi, lo = e[i] - start_db, e[i] - start_db - drop
    j0 = i + int(np.argmax(e[i:] <= hi))
    below = np.flatnonzero(e[j0:] <= lo)
    j1 = j0 + (below[0] if len(below) else len(e) - 1 - j0)
    if j1 - j0 < 4:
        return float("nan")
    sl = np.polyfit(t[j0:j1], e[j0:j1], 1)[0]
    return -60.0 / sl if sl < 0 else float("inf")


def pitch_hz(x, guess, t0, t1):
    """tools/hil_scan.py f0_of on x[t0:t1]: the FFT peak near guess (Hann, x8 zero padding, parabolic refinement)"""
    seg = np.asarray(x[int(t0 * FS):int(t1 * FS)], float)
    n = len(seg)
    sp = np.abs(np.fft.rfft(seg * np.hanning(n), 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / FS)
    m = (fr > guess * 0.94) & (fr < guess * 1.06)
    i = np.flatnonzero(m)[np.argmax(sp[m])]
    a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
    return (i + 0.5 * (a - c) / (a - 2 * b + c)) * FS / (8 * n)


def noise_render(spec, noise, bark, vel, n):
    """bars.c's contact noise for one strike, integers as the C (engine.c: bar_nz = KN_NZ[NOISE] << 8; bar_strike:
    e += bar_nz x bar_fam_nz() x vel >> 22; the LFSR, NZ_BQ, e -= (e + 1023) >> 10, y e >> 13 into bar_mono Q20),
    in Q15 output units (/ 32)"""
    FAM_NZ = [2048, 8192, 14336, 22528, 32767]                # bars.h BAR_FAM_NZ
    kf = spec["kn_fam"][bark]
    f, a = kf >> 8, kf & 255
    f2 = min(f + 1, 4)
    fz = FAM_NZ[f] + (((FAM_NZ[f2] - FAM_NZ[f]) * a) >> 8)
    nz = spec["kn_nz"][noise] << 8
    e = (nz * fz * vel) >> 22
    q = spec["nz_bq"]
    r = 0x1D872
    s0 = s1 = s2 = s3 = 0
    out = np.zeros(n)
    for j in range(n):
        if e <= 0:
            break
        r = (r >> 1) ^ ((-(r & 1)) & 0xB400)
        x = ((r & 0xFFFF) - 32768) >> 2
        y = ((q[0][0] * x) >> 14) + s0
        s0 = ((q[0][1] * x - q[0][3] * y) >> 14) + s1
        s1 = (q[0][2] * x - q[0][4] * y) >> 14
        x = y
        y = ((q[1][0] * x) >> 14) + s2
        s2 = ((q[1][1] * x - q[1][3] * y) >> 14) + s3
        s3 = (q[1][2] * x - q[1][4] * y) >> 14
        out[j] = ((y * e) >> 13) / 32.0
        e -= (e + 1023) >> 10
    return out


def mute_drop_s(slots, off, db=60.0, hop=64):
    """seconds from key-up (off) until the V1 slot + the tube's radiated slot (slots 0 + 1) stay db under their level
    just before it (64-sample peak envelope; a band filter's ringing would hide a fast stop)"""
    x = np.abs(slots[0] + slots[1])
    m = len(x) // hop
    e = 20 * np.log10(x[:m * hop].reshape(m, hop).max(axis=1) + 1e-12)
    i0 = int(off * FS) // hop
    ref = e[i0 - 4:i0].max()
    above = np.flatnonzero(e[i0:] > ref - db)
    return (above[-1] + 1) * hop / FS if len(above) and above[-1] + 1 < m - i0 else 9.9


ATT = 0.05                              # the attack window (s) of the centroid gates: the marimba's upper modes are gone
#                                         by 20-50 ms; over the plan's 0.3 s the fundamental (and the tube) own the centroid


def decay_time(x, db=30.0, hop=64):
    """seconds from the 64-sample peak envelope's maximum until it stays db under it"""
    x = np.abs(np.asarray(x, float))
    m = len(x) // hop
    e = 20 * np.log10(x[:m * hop].reshape(m, hop).max(axis=1) + 1e-12)
    i = int(np.argmax(e))
    above = np.flatnonzero(e[i:] > e[i] - db)
    return (above[-1] + 1) * hop / FS


def checks(spec=None, quick=False):
    """[(name, value, ok, kind)] kind 'gate' / 'report'"""
    spec = spec or load_spec()
    vb = spec["vibes"]
    D = {d["midi"]: d for d in spec["design"]}
    K = {k["midi"]: k for k in spec["keys"]}
    out = []

    def add(name, value, ok, kind="gate"):
        out.append((name, value, bool(ok), kind))
    kn0 = dict(gm.DEFAULT_KNOBS)
    a, b = vb["keys"]["authentic"]
    auth = list(range(a, b + 1))

    # ---------------------------------------------------------- VA-M1 frequencies
    import bars_va as bva
    r = render(spec, REG, [100] * len(REG), 3.0, None, slots=True)
    ks1 = [n for n in REG if D[n]["t60_v1"] > 0.4 and D[n]["split_hz"] >= 2.0]
    worst = max(abs(bva.cents(bva.peak_freq(r["slots"][REG.index(n), 0], D[n]["f0"], 1.0, 3.0), D[n]["f0"])) for n in ks1)
    add("M1 V1 normal mode at f0 (golden, 1..3 s; keys %s: T60 > 0.4 s, the pair resolvable, split >= 2 Hz), worst |c|"
        % ks1, round(worst, 3), worst <= 0.5)
    wc = max(abs(d["grid"]["v1_cents"]) for d in spec["design"])
    add("M1 V1 normal mode on its target on every key (the integer map's exact eigenvalue: the strongly coupled pairs, "
        "0.3-1 Hz apart, are not resolved by a 2 s render), worst |c|", round(wc, 4), wc <= 0.5)
    add("M1 the V1 targets (gen_bars.v1_target: the pair's radiated pitch centred on f0), cents re f0 at the register "
        "keys", [(n, round(D[n].get("v1_target_cents", 0.0), 2)) for n in REG], True, "report")
    pk = list(range(36, 73, 2))
    yp = render(spec, pk, [100] * len(pk), 1.4, None)
    pc = [(n, round(bva.cents(pitch_hz(x, D[n]["f0"], 0.05, 0.6), D[n]["f0"]), 2),
           round(bva.cents(pitch_hz(x, D[n]["f0"], 0.05, 1.3), D[n]["f0"]), 2)) for n, x in zip(pk, yp)]
    wp = max(abs(c[1]) for c in pc)
    add("M1 the radiated pitch on f0, C2..C5 (the f0 band's FFT peak over 0.05..0.6 s, as tools/hil_scan.py reads the "
        "FM-1; the two normal modes heard as one tone; an FFT peak of a beating pair reads to ~1 c), worst |c| (<= 1.5)", round(wp, 2), wp <= 1.5)
    add("M1 the radiated pitch per key: (key, c 0.05..0.6 s, c 0.05..1.3 s)", pc, True, "report")
    wv = 0.0
    for n in range(21, 109):
        for s, m in zip(range(2, 7), D[n]["modes"][:5]):
            w = K[n]["modes"][s]["w"]
            if w["th"]:
                wv = max(wv, abs(bva.cents(bva.word_freq(w), m["f"])))
    add("M1 V2 V3 V4 T1 T2 slots on their designed targets, worst |c|", round(wv, 3), wv <= 0.5)
    r2 = [D[n]["modes"][0]["ratio"] for n in auth if n <= 84]
    r3 = [D[n]["modes"][1]["ratio"] for n in auth if n <= vb["T9a"]["v3_tuned_max_key"]]
    add("M1 V2 / V1 in 4.00 +-0.02 (C2..C6)", [round(min(r2), 4), round(max(r2), 4)], 3.98 <= min(r2) and max(r2) <= 4.02)
    add("M1 V3 / V1 in 9.4..10.0 up to key %d" % vb["T9a"]["v3_tuned_max_key"], [round(min(r3), 3), round(max(r3), 3)],
        9.4 <= min(r3) and max(r3) <= 10.0 + 1e-9)
    r3t = [(n, round(D[n]["modes"][1]["ratio"], 2)) for n in (85, 90, 96)]
    add("M1 V3 / V1 above the tuned range (the FE's untuned profile, reported)", r3t, True, "report")
    r4 = [round(D[n]["modes"][2]["ratio"], 2) for n in REG]
    add("M1 V4 / V1 at the register keys (the FE, 16.8-17.1 x on the low bars)", r4, True, "report")

    # ---------------------------------------------------------- VA-M2 decays
    worst = 0.0
    for n in range(21, 109):
        for m in D[n]["modes"][:5]:
            if m["f"] > 0 and m["present"]:
                law = min(LN1000 / (vb["T2r"]["alpha"] * float(np.interp(n, *zip(*vb["T2r"]["alpha_reg"]))) / 2 +
                                    vb["T2r"]["beta"] * (2 * math.pi * m["f"]) ** 2 / 2), vb["cap"]["t60_max_s"])
                mult = vb["T9d"]["mult"] if m["name"] in ("T1", "T2") else 1.0
                worst = max(worst, abs(m["t60"] / (law * mult) - 1))
    add("M2 upper-mode T60 = the Rayleigh law (design), worst rel.", round(worst, 5), worst <= 0.05)
    rh = render(spec, REG, [100] * len(REG), 3.0, None, slots=True)
    worst = 0.0
    for i, n in enumerate(REG):
        for s in range(2, 7):
            m = D[n]["modes"][s - 2]
            if not K[n]["modes"][s]["b"] or m["t60"] < 0.02:
                continue
            th = gm.env_t60(rh["slots"][i, s], 0.02, 0.02 + min(1.0 * m["t60"], 2.8))
            worst = max(worst, abs(th / m["t60"] - 1))
    add("M2 golden upper-mode decays vs the law (slots), worst rel.", round(worst, 4), worst <= 0.05)
    br = {int(k): v for k, v in vb["T1b"]["bracket"].items()}
    tg = {int(k): v for k, v in vb["T1b"]["t60_s"].items()}
    rows, bad, off10 = [], [], []
    y = render(spec, REG, [100] * len(REG), 4.0, None)
    for i, n in enumerate(REG):
        t_d = D[n]["t60_v1"]
        t_g = t60_fit(y[i], D[n]["f0"])
        rows.append((n, round(t_d, 3), round(t_g, 3), br[n]))
        if not (br[n][0] <= t_d <= br[n][1]):
            bad.append((n, round(t_d, 3), br[n]))
        if abs(t_d / tg[n] - 1) > 0.10 and not (D[n]["flags"] & 4):
            off10.append((n, round(t_d, 3), tg[n]))
    add("M2 V1-with-tube T60 (design normal mode) within the §2.2 bracket at the register keys", bad, not bad)
    add("M2 V1-with-tube T60 within +-10 % of T1b or flagged (sigma clamped / limited)", off10, not off10)
    add("M2 per key: (key, design T60, golden output 30 dB fit, bracket)", rows, True, "report")
    gbad = [(n, d, g) for n, d, g, br_ in rows if not (0.8 * br_[0] <= g <= 1.2 * br_[1])]
    add("M2 golden output's f0-band T60 (30 dB fit) within the bracket +-20 % (the pair's two modes and the tube)",
        gbad, not gbad)
    m = vb["T3m"]
    worst = max(abs(D[n]["t60_rel_v1"] / m["v1_s"] - 1) for n in auth if D[n]["t60_rel_v1"] > 0)
    add("M2 the mute pair's V1 T60 vs T3m v1_s (design), worst rel. (the tube keeps ringing a little)", round(worst, 3),
        True, "report")

    # ---------------------------------------------------------- VA-M3 regime
    wrong = [n for n in range(21, 109) if D[n]["regime"] != ("weak" if D[n]["zeta_r"] > D[n]["zeta_n"] else "strong")]
    add("M3 the printed regime matches the sign of zeta_r - zeta_n", wrong, not wrong)
    strong = [n for n in auth if D[n]["regime"] == "strong" and n < 60]
    diff = [(n, round(abs(D[n]["normal_modes"][0]["t60"] / D[n]["normal_modes"][1]["t60"] - 1), 3)) for n in strong]
    bad = [x for x in diff if x[1] > 0.15]
    add("M3 below C4 (strong): the two normal modes' T60 within 15 %% at BELL 64 (%d keys)" % len(strong), bad, not bad)
    add("M3 below C4: the normal modes' split (Hz) at the register keys (the slow beat of the strongly coupled pair)",
        [(n, round(D[n]["split_hz"], 2)) for n in REG if D[n]["regime"] == "strong"], True, "report")
    s16 = spec["kn_tube"][16] / 32768.0
    lng = []
    for n in strong:
        d = D[n]
        bt = btm.BarTube(d["f0"], d["f_tube"], d["sigma"], LN1000 / d["t1a"], LN1000 / d["t60_tube"]).with_bell(s16)
        lng.append(max(LN1000 / sg for _, sg, _, _ in bt.modes2()))
    add("M3 below C4 at BELL 16: the longest normal mode (the tube alone) <= 4 s", round(max(lng), 2), max(lng) <= 4.0)
    s127 = spec["kn_tube"][127] / 32768.0
    ww = max(s127 * D[n]["sigma"] - vb["T5a"]["weak_margin"] * (D[n]["zeta_r"] - D[n]["zeta_n"])
             for n in range(72, 109))
    add("M3 weak regime from C5 up at BELL 127: max of s sigma - 0.9 (zeta_r - zeta_n)", ww, ww <= 1e-12)
    over = []
    import gen_bars as gb
    for n in auth:
        d = D[n]
        if d["regime"] != "strong" or d["sigma"] <= vb["T5a"]["lo"] * 1.0001:
            continue
        lim = vb["T5a"]["split_max_hz"] + d["f0"] * (2 ** (gb.tube_cents(vb, n) / 1200) - 1)
        if d["split_hz"] > 1.02 * lim:
            over.append((n, round(d["split_hz"], 2), round(lim, 2)))
    add("M3 C2..C7, strong regime: the pair split at BELL 64 within split_max_hz past the detune", over, not over)
    add("M3 per key: (key, regime, sigma, zeta_n %, zeta_r %, flags)",
        [(n, D[n]["regime"], round(D[n]["sigma"], 4), round(100 * D[n]["zeta_n"], 2), round(100 * D[n]["zeta_r"], 2),
          D[n]["flags"]) for n in REG], True, "report")

    # ---------------------------------------------------------- VA-M4 bloom
    bt_ = {int(k): v for k, v in vb["T5b"]["bloom_ms_targets"].items()}
    ks = sorted(bt_)
    y = render(spec, ks, [100] * len(ks), 1.2, None)
    rows, bad = [], []
    for n, x in zip(ks, y):
        pk = btm.f0_peak_ms(x, D[n]["f0"])
        tol = max(0.3 * bt_[n], 15.0) if n < 72 else None
        ok = (abs(pk - bt_[n]) <= tol) if tol else pk <= max(15.0, bt_[n])
        rows.append((n, pk, bt_[n]))
        if not ok:
            bad.append((n, pk, bt_[n]))
    add("M4 the f0-band peak (vib_ref_measure procedure, ms) within +-30 % of T5b (<= 15 ms from C6 up)", rows, not bad)
    mono = []
    for n in (36, 48, 60):
        pks = [btm.f0_peak_ms(render(spec, [n], [100], 1.2, dict(kn0, bell=bl))[0], D[n]["f0"]) for bl in (64, 96, 127)]
        mono.append((n, pks))
    nm = [x for x in mono if any(p2 > p1 + 5.0 for p1, p2 in zip(x[1], x[1][1:]))]
    add("M4 the peak moves earlier (or stays) over TUBE 64 / 96 / 127 (5 ms resolution; at TUBE 32 the bar's direct f0 "
        "leads and the peak reads 0)", mono, not nm)
    ks = [n for n in REG if n < 72]                    # (C5: the tube's own T60 0.16 s, the ratio moves ~4 dB in the window: reported)
    pr = render(spec, ks, [100] * len(ks), 0.4, None, parts=True)
    rr, bad = [], []
    for i, n in enumerate(ks):
        lb = partial_db(pr["bar"][i], D[n]["f0"], 0.18, 0.22)
        lt = partial_db(pr["tube"][i], D[n]["f0"], 0.18, 0.22)
        rr.append((n, round(lt - lb, 1)))
        if abs(lt - lb - vb["T5b"]["db"]) > 3.0:
            bad.append((n, round(lt - lb, 1)))
    add("M4 tube / bar at 0.2 s, BELL 64 = %g +-3 dB (keys < 72)" % vb["T5b"]["db"], rr, not bad)

    # ---------------------------------------------------------- VA-M5 mallets
    fams = vb["T7c"]["families"]
    bad = []
    for n in (48, 60, 84):
        for q, fm in enumerate(fams):
            tc = D[n]["mallet"][q]["tc_ms"][1:9]
            lo, hi = fm["tc_window_ms"]
            if not (lo <= min(tc) and max(tc) <= hi):
                bad.append((n, fm["short"], round(min(tc), 2), round(max(tc), 2)))
    add("M5 contact times per family inside tc_window_ms (v8..v127)", bad, not bad)
    ov = []
    for n in (48, 60, 84):
        for q in range(len(fams) - 1):
            for a_, b_ in zip(D[n]["mallet"][q]["tc_ms"][1:], D[n]["mallet"][q + 1]["tc_ms"][1:]):
                if b_ >= a_:
                    ov.append((n, fams[q]["short"], fams[q + 1]["short"]))
                    break
    add("M5 adjacent families: the softer always the longer contact at equal velocity", ov, not ov)
    rt = []
    for q, fm in enumerate(fams):
        p = fm["p"]
        t8 = vb["T8"]
        sp_ = lambda v_: t8["v_lo"] * (t8["v_hi"] / t8["v_lo"]) ** ((v_ - 1) / 126.0)  # noqa: E731
        want = (sp_(127) / sp_(8)) ** (-(p - 1) / (p + 1))
        tc = D[60]["mallet"][q]["tc_ms"]
        rt.append((fm["short"], round(tc[-1] / tc[1], 3), round(want, 3)))
    bad = [x for x in rt if abs(x[1] / x[2] - 1) > 0.10]
    add("M5 tau(v127) / tau(v8) = (v127 / v8)^-(p-1)/(p+1) +-10 % (C4; Hertz, the T8 speeds)", rt, not bad)
    ys = render(spec, [60, 60], [117, 117], 0.4, [dict(kn0, bark=RUBBER), dict(kn0, bark=SOFT)])
    cr = centroid(ys[0], 0, ATT) / centroid(ys[1], 0, ATT)
    add("M5 RUBBER's attack centroid >= 4x SOFT's (C4 v117)", round(cr, 2), cr >= 4.0)
    rows, bad = [], []
    for n in (48, 60):
        for v in (117, 127):
            x = render(spec, [n], [v], 0.4, dict(kn0, bark=SOFT))[0]
            l1 = partial_db(x, D[n]["f0"], 0.005, 0.3)
            v2 = partial_db(x, D[n]["modes"][0]["f"], 0.005, 0.3) - l1
            v3 = partial_db(x, D[n]["modes"][1]["f"], 0.005, 0.3) - l1
            rows.append((n, v, round(v2, 1), round(v3, 1)))
            if (n == 48 and (v2 > -20 or v3 > -40)) or (n == 60 and v3 > -35):
                bad.append((n, v, round(v2, 1), round(v3, 1)))
    add("M5 SOFT soft: C3 V2/V1 <= -20, V3/V1 <= -40 dB; C4 V3/V1 <= -35 dB (v117, v127)", rows, not bad)
    vels = (8, 24, 40, 56, 72, 88, 100, 117, 127)
    bad, rows = [], []
    for n in (36, 48, 60, 72, 84):
        ys = render(spec, [n] * len(vels), vels, 0.35, dict(kn0, bark=MED))
        c = [centroid(x, 0, ATT) for x in ys]
        if any(c2 < 0.97 * c1 for c1, c2 in zip(c, c[1:])):
            bad.append((n, [round(x) for x in c]))
        rows.append((n, round(c[vels.index(117)] / c[vels.index(40)], 2)))
    add("M5 attack centroid monotone in velocity (no fall > 3 %; MED)", bad, not bad)
    r60 = dict(rows)[60]
    add("M5 attack centroid v40 -> v117 on C4 MED >= 1.4x", rows, r60 >= 1.4)
    x = render(spec, [48, 48], [40, 127], 0.35, dict(kn0, bark=MED))
    d2 = [partial_db(z, D[48]["modes"][0]["f"], 0.005, 0.3) - partial_db(z, D[48]["f0"], 0.005, 0.3) for z in x]
    add("M5 V2/V1 rises >= 8 dB from v40 to v127 (C3 MED)", round(d2[1] - d2[0], 1), d2[1] - d2[0] >= 8.0)

    # ---------------------------------------------------------- VA-M6 strike position
    rows, bad = [], []
    for n in (48, 60):
        ys = render(spec, [n] * 3, [100] * 3, 0.4, [dict(kn0, tone=t) for t in (0, 64, 127)], slots=True)
        sl = ys["slots"]
        v2v1 = [20 * math.log10(np.abs(sl[i, 2]).max() / np.abs(sl[i, 0]).max() + 1e-30) for i in range(3)]
        t1 = 20 * math.log10(np.abs(sl[2, 5, :int(0.02 * FS)]).max() / np.abs(sl[2, 0, :int(0.02 * FS)]).max() + 1e-30)
        tz = float(np.abs(sl[:2, 5:7]).max())
        rows.append((n, [round(v, 1) for v in v2v1], round(t1, 1), tz))
        if v2v1[0] > -40 or abs(t1 - vb["T9c"]["t1_db"]) > 3 or tz != 0.0:
            bad.append(n)
    add("M6 TONE 0: V2/V1 <= -40 dB; TONE 127: T1/V1 = %g +-3 dB; T1 = T2 = 0 for TONE <= 64" % vb["T9c"]["t1_db"],
        rows, not bad)

    # ---------------------------------------------------------- VA-M7 contact noise (bars.c's path, emulated)
    note = render(spec, [60], [100], 0.2, dict(kn0))[0]
    pk = np.abs(note[:int(0.05 * FS)]).max()
    rows = {}
    for nz in (0, 64, 127):
        z = noise_render(spec, nz, DEFAULT, 100, int(0.2 * FS))
        p0 = np.abs(z[:int(0.05 * FS)]).max()
        p100 = np.abs(z[int(0.095 * FS):int(0.105 * FS)]).max()
        rows[nz] = (20 * math.log10(p0 / pk + 1e-30), 20 * math.log10(p100 / pk + 1e-30) if p100 else -999.0)
    add("M7 NOISE 0: exactly none (peak, Q15 units)", float(np.abs(noise_render(spec, 0, DEFAULT, 127, 4410)).max()),
        np.abs(noise_render(spec, 0, DEFAULT, 127, 4410)).max() == 0)
    add("M7 NOISE 64: -50 +-3 dB re the note's 0-50 ms peak (C4 DEFAULT v100)", round(rows[64][0], 1),
        abs(rows[64][0] + 50) <= 3)
    add("M7 NOISE 64: <= -80 dB re the note by 100 ms", round(rows[64][1], 1), rows[64][1] <= -80)
    add("M7 NOISE 127 at 100 ms re the note (bars.c's fixed 23 ms envelope)", round(rows[127][1], 1), True, "report")
    add("M7 NOISE 127 re 64 (+6 dB or more: the card)", round(rows[127][0] - rows[64][0], 1), rows[127][0] - rows[64][0] >= 6)

    # ---------------------------------------------------------- VA-M8 the hand mute
    rows, bad = [], []
    for n, off in ((48, 0.4), (72, 0.1)):
        ys = render(spec, [n] * 2, [100] * 2, off + 1.0, [dict(kn0, release=127), dict(kn0, release=0)], off_at=off,
                    slots=True)
        held = render(spec, [n], [100], off + 1.0, kn0)[0]
        t60 = mute_drop_s(ys["slots"][0], off)
        same = float(np.max(np.abs(ys["out"][1] - held)) / (np.max(np.abs(held)) + 1e-30))
        rows.append((n, round(t60, 3), same))
        if t60 > 0.12 or same > 0.01:
            bad.append(n)
    add("M8 MUTE 127 key-up: V1 + tube down 60 dB within 0.12 s; MUTE 0: the held decay (<= 1 % apart)", rows, not bad)
    ped, off = [], 0.4
    for pw in (0, 16384, 32768):
        y1 = render(spec, [48], [100], off + 0.6, dict(kn0, release=127), off_at=off, pedal_w=pw)[0]
        ped.append(lufs(y1, off + 0.1, off + 0.6))
    add("M8 the pedal over MUTE 127: held (w 0) > half > none (LUFS 0.1..0.6 s after key-up)", [round(x, 1) for x in ped],
        ped[0] > ped[1] > ped[2])

    # ---------------------------------------------------------- VA-M9 cards audible (S14 before any C)
    if not quick:
        out += cards(spec)

    # ---------------------------------------------------------- VA-M10 level
    keys = list(range(21, 109))
    y = render(spec, keys, [100] * len(keys), 1.0, None)
    L = np.array([lufs(x, *vb["calib"]["lufs_t"]) for x in y])
    st = np.array(spec["st77_lufs"])
    dev = L - st - vb["calib"]["st77_offset_db"] - np.array(spec.get("rolloff_db", [0.0] * len(keys)))
    add("M10 the top's peak roll-off (card_level family_ceiling; dB, from the key it starts)",
        [(n, round(r, 1)) for n, r in zip(keys, spec.get("rolloff_db", [])) if r < 0][::6], True, "report")
    ia = [keys.index(n) for n in auth]
    add("M10 v100 LUFS on the ST77 curve + offset, worst |dev| in C2..C7 (LU)", round(float(np.abs(dev[ia]).max()), 2),
        np.abs(dev[ia]).max() <= 0.5)
    stp = float(np.abs(np.diff(L[ia])).max())
    add("M10 neighbour steps C2..C7 (dB)", round(stp, 2), stp <= 1.0)
    vels = (8, 24, 40, 56, 72, 88, 104, 120, 127)
    worst, rng = 0.0, []
    for n in range(a, b + 1, 4):
        ys = render(spec, [n] * len(vels), vels, 0.6, None)
        lv = [lufs(x, 0, 0.6) for x in ys]
        worst = max(worst, max(l1 - l2 for l1, l2 in zip(lv, lv[1:])))
        rng.append(lv[-1] - lv[0])
    add("M10 velocity monotone: the largest drop between anchors (dB)", round(worst, 2), worst <= 0.3)
    add("M10 velocity range v8 -> v127 (dB), min / max over the keys", [round(min(rng), 1), round(max(rng), 1)],
        min(rng) >= 26 and max(rng) <= 32)
    pk = max(spec["peak_dbfs"])
    add("M10 single-strike peak ceiling over the voicing grid (dBFS)", round(pk, 2), pk <= -0.5 + 1e-9)

    # ---------------------------------------------------------- VA-M11 stability
    g = [d["grid"] for d in spec["design"]]
    add("M11 rho < 1 at every grid point of every key (max)", max(x["rho"] for x in g), max(x["rho"] for x in g) < 1.0)
    tol = vb["T5a"].get("grid_decay_tol", 0.02)
    worst = max((x for x in g if a <= x["midi"] <= b), key=lambda x: x["decay_err"])
    add("M11 decay error outside C2..C7 (the extrapolated keys; worst, key)",
        max((round(x["decay_err"], 4), x["midi"]) for x in g if not a <= x["midi"] <= b), True, "report")
    add("M11 decay error <= %g in C2..C7 (worst, key)" % tol, (round(worst["decay_err"], 4), worst["midi"]), worst["decay_err"] <= tol)
    add("M11 keys over 2 %", [x["midi"] for x in g if x["decay_err"] > 0.02], True, "report")
    add("M11 V1 <= 0.5 c (worst)", round(max(abs(x["v1_cents"]) for x in g), 4), max(abs(x["v1_cents"]) for x in g) <= 0.5)
    kc = max(d["kc"] for d in spec["design"])
    add("M11 kc <= 1.5 (worst)", round(kc, 3), kc <= 1.5)

    # ---------------------------------------------------------- VA-M12 FE
    an = vb["T12"]["anchor"]
    fe = D[an["key"]]["fe"]
    add("M12 the anchor bar's FE mass (g) in ISMA's %s" % an["mass_g"], round(fe["mass"] * 1e3, 1),
        an["mass_g"][0] <= fe["mass"] * 1e3 <= an["mass_g"][1])
    t1, t2 = fe["tors"]
    add("M12 T1 / V1 in %s, T2 / V1 in %s at the anchor" % (vb["T9b"]["expected_T1"], vb["T9b"]["expected_T2"]),
        [round(t1, 3), round(t2, 3)], vb["T9b"]["expected_T1"][0] <= t1 <= vb["T9b"]["expected_T1"][1] and
        vb["T9b"]["expected_T2"][0] <= t2 <= vb["T9b"]["expected_T2"][1])
    v4 = fe["ratios"][3]
    add("M12 V4 / V1 at the anchor in 16..18", round(v4, 3), 16 <= v4 <= 18)
    return out


CARD_KEYS, CARD_VELS = (48, 60, 84), (100, 117, 127)


def capped(spec, n, v, q):
    """family q's contact at key n and velocity v is played at T7f's cap (f_tau_max / f0): the design's window at the
    nearest velocity anchor within 10 % of the cap"""
    import gen_bars as gb
    d = {x["midi"]: x for x in spec["design"]}[n]
    a = min(range(len(gb.V_ANCH)), key=lambda i: abs(gb.V_ANCH[i] - v))
    cap = spec["vibes"]["T7f"]["f_tau_max"] / d["f0"] * 1e3
    return d["mallet"][q]["win_ms"][a] >= 0.9 * cap     # within 10 %: the same shape at nearly the same rate


def cards(spec):
    """VA-M9: every card moves the sound by its job and threshold (MARIMBA PLAN §10.2) in the golden model, at
    v100 / v117 / v127 on 48 / 60 / 84 (the panel path's values; NOISE in bars.c's emulated path)"""
    out = []
    D = {d["midi"]: d for d in spec["design"]}
    kn0 = dict(gm.DEFAULT_KNOBS)

    def add(name, value, ok, kind="gate"):
        out.append((name, value, bool(ok), kind))

    def grid(card, vals, dur=1.0, keys=CARD_KEYS, vels=CARD_VELS):
        ks, vs, kns, idx = [], [], [], []
        for v_ in vals:
            for n in keys:
                for ve in vels:
                    ks.append(n), vs.append(ve), kns.append(dict(kn0, **{card: v_})), idx.append((v_, n, ve))
        y = render(spec, ks, vs, dur, kns)
        return {i: x for i, x in zip(idx, y)}
    # STRIKE (TONE): spectral distance >= 3 dB 0 vs 64 and 64 vs 127
    R = grid("tone", (0, 64, 127), 0.5)
    w = min(min(dist(R[0, n, v], R[64, n, v]), dist(R[64, n, v], R[127, n, v])) for n in (48, 60) for v in CARD_VELS)
    add("M9 STRIKE: spectral distance 0 vs 64 and 64 vs 127 on C3 / C4, the smallest (dB)", round(w, 2), w >= 3.0)
    w0 = min(dist(R[0, 84, v], R[64, 84, v]) for v in CARD_VELS)
    ys = render(spec, [84] * 2, [100] * 2, 0.1, [dict(kn0, tone=64), dict(kn0, tone=127)], slots=True)["slots"]
    t1 = 20 * math.log10(np.abs(ys[1, 5, :int(0.02 * FS)]).max() / np.abs(ys[1, 0, :int(0.02 * FS)]).max() + 1e-30)
    add("M9 STRIKE on C6: the edge (127) lights T1 at %g +-3 dB re V1 (T1 is 1.07 f0 there, inside f0's third-octave "
        "band: the distance cannot see it)" % spec["vibes"]["T9c"]["t1_db"], round(t1, 1),
        abs(t1 - spec["vibes"]["T9c"]["t1_db"]) <= 3)
    add("M9 STRIKE on C6: 0 vs 64 spectral distance (dB; the DEFAULT contact is at T7f's cap there and V2 / V3 are "
        "30-40 dB under V1 either way: reported)", round(w0, 2), True, "report")
    # MALLET: the detents pairwise >= 3 dB
    R = grid("bark", (0, 42, 64, 85, 127), 0.5)
    det = (0, 42, 64, 85, 127)
    worst, exc, w6 = (99.0, None), set(), (99.0, None)
    for n in CARD_KEYS:
        for v in CARD_VELS:
            for i in range(5):
                for j in range(i + 1, 5):
                    if capped(spec, n, v, i) and capped(spec, n, v, j):
                        exc.add((n, v, det[i], det[j]))
                        continue
                    d_ = dist(R[det[i], n, v], R[det[j], n, v])
                    if n == 84:
                        w6 = min(w6, (d_, (n, v, det[i], det[j])), key=lambda x: x[0])
                    elif d_ < worst[0]:
                        worst = (d_, (n, v, det[i], det[j]))
    add("M9 MALLET: the detents pairwise on C3 / C4, the smallest spectral distance (dB) and where",
        (round(worst[0], 2), worst[1]), worst[0] >= 3.0)
    add("M9 MALLET on C6: the smallest pairwise distance (dB; the softer contacts near T7f's cap play nearly the same "
        "shape: reported, as VIBES's F6 exceptions)", (round(w6[0], 2), w6[1]), True, "report")
    add("M9 MALLET: pairs left out, both contacts capped by T7f (f0 tau <= f_tau_max: the same shape, played at the "
        "same rate; VIBES's F6-class exceptions)", sorted(exc), True, "report")
    # TUBE: level >= +4 LU 0 -> 64, >= +0.5 LU 64 -> 127; C2 / C3 T60 x1.3 0 -> 64
    R = grid("bell", (0, 64, 127), 1.0)
    l = {k: lufs(x) for k, x in R.items()}
    a = min(l[64, n, v] - l[0, n, v] for n in CARD_KEYS for v in CARD_VELS)
    b = min(l[127, n, v] - l[64, n, v] for n in CARD_KEYS for v in CARD_VELS)
    add("M9 TUBE: level 0 -> 64 (smallest, LU)", round(a, 2), a >= 4.0)
    add("M9 TUBE: level 64 -> 127 (smallest, LU)", round(b, 2), b >= 0.5)
    rows = []
    for n in (36, 48):
        y = render(spec, [n, n], [100, 100], 4.0, [dict(kn0, bell=0), dict(kn0, bell=64)])
        t0, t1 = t60_fit(y[0], D[n]["f0"]), t60_fit(y[1], D[n]["f0"])
        rows.append((n, round(t0, 2), round(t1, 2), round(t1 / t0, 2)))
    add("M9 TUBE: C2 T60 0 -> 64 x1.3 or more (VA-M9: the marimba's own signature)", rows[:1], rows[0][3] >= 1.3)
    add("M9 TUBE: C3 T60 0 -> 64 (the profile's t60_keys; x1.3 conflicts with VA-M3's 15 % at C3)", rows[1:], True, "report")
    rows = []
    for n in (84, 96):
        y = render(spec, [n, n], [100, 100], 1.0, [dict(kn0, bell=0), dict(kn0, bell=127)])
        t0, t1 = t60_fit(y[0], D[n]["f0"]), t60_fit(y[1], D[n]["f0"])
        rows.append((n, round(t0, 3), round(t1, 3), round(lufs(y[1]) - lufs(y[0]), 1)))
    add("M9 TUBE at C6 / C7 (a reflector): (key, T60 0, T60 127, LU 0 -> 127)", rows, True, "report")
    # WEIGHT: >= 2.5 LU 0 -> 127 and light brighter (centroid ratio >= 1.05)
    R = grid("hammer", (0, 127), 1.0)
    lw = min(abs(lufs(R[127, n, v]) - lufs(R[0, n, v])) for n in CARD_KEYS for v in CARD_VELS)
    cw = min(centroid(R[0, n, v], 0, ATT) / centroid(R[127, n, v], 0, ATT) for n in CARD_KEYS for v in CARD_VELS)
    add("M9 WEIGHT: |level| 0 vs 127 (smallest, LU)", round(lw, 2), lw >= 2.5)
    add("M9 WEIGHT: light brighter, attack centroid 0 / 127 (smallest)", round(cw, 3), cw >= 1.05)
    # RING (DECAY): T60 x2 or more 0 vs 127
    rows = []
    for n in (36, 60, 84):
        y = render(spec, [n, n], [100, 100], 6.0, [dict(kn0, decay=0), dict(kn0, decay=127)])
        rows.append((n, round(decay_time(y[1]) / decay_time(y[0]), 2)))
    add("M9 RING: the time to -30 dB, RING 127 / 0 (smallest)", rows, min(r[1] for r in rows) >= 2.0)
    # MUTE (RELEASE): the note stopped >= 3x faster after key-up, 127 vs 0 (V1 + tube, 40 dB)
    rows = []
    for n, off in ((48, 0.3), (72, 0.1)):
        y = render(spec, [n, n], [100, 100], off + 1.5, [dict(kn0, release=0), dict(kn0, release=127)], off_at=off,
                   slots=True)
        t0, t1 = mute_drop_s(y["slots"][0], off, 40), mute_drop_s(y["slots"][1], off, 40)
        rows.append((n, round(t0, 3), round(t1, 3), round(t0 / t1, 1)))
    add("M9 MUTE: time to -40 dB after key-up, MUTE 0 / MUTE 127 (smallest ratio)", rows, min(r[3] for r in rows) >= 3.0)
    # NOISE: +6 dB 64 -> 127, at 127 within 35 dB of the note
    note = render(spec, [60], [100], 0.1, kn0)[0]
    pk = np.abs(note[:int(0.05 * FS)]).max()
    z64 = np.abs(noise_render(spec, 64, DEFAULT, 100, 2205)).max()
    z127 = np.abs(noise_render(spec, 127, DEFAULT, 100, 2205)).max()
    add("M9 NOISE: 64 -> 127 (dB)", round(20 * math.log10(z127 / z64), 1), z127 / z64 >= 2.0)
    add("M9 NOISE 127 re the note's peak (within 35 dB)", round(20 * math.log10(z127 / pk), 1), z127 / pk >= 10 ** (-35 / 20))
    return out


def report(res, path):
    L = ["# Gate VA-M on the golden model (tools/marimba_va.py)", ""]
    for name, val, ok, kind in res:
        tag = ("PASS" if ok else "FAIL") if kind == "gate" else "REPORT"
        L.append("%-6s %s: %s" % (tag, name, val))
    ng = sum(1 for r in res if r[3] == "gate")
    nf = sum(1 for r in res if r[3] == "gate" and not r[2])
    L += ["", "%d gated checks, %d failed" % (ng, nf)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("\n".join(L) + "\n")
    return "\n".join(L)


def main():
    spec = load_spec(sys.argv[sys.argv.index("--spec") + 1]) if "--spec" in sys.argv else None
    print(report(checks(spec, quick="--quick" in sys.argv), os.path.join(ROOT, "build", "marimba_va", "report.txt")))


if __name__ == "__main__":
    main()
