#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate VA (VIBES PLAN §10): the golden model (tools/bars_model.py) on the generated, quantised spec
(docs/model/data/bars_spec.json) against the physics data (vibes.json, the reference measurements).

    python3 tools/bars_va.py            -> every check with its number and PASS / FAIL / REPORT (build/bars_va/report.txt)

Targets come from bars_spec.json (designed) and its embedded vibes.json (physics); nothing is hard-coded but the
tolerances of §10. Coordinator decisions (2026-10-17) applied: T1b and the f0-peak times are compared with their
SMOOTHED tables (the raw +-15 % / +-30 % numbers are reported, not gated); the "first 50 ms unmodulated" motor check
is dropped (the tube modulates from the start); the peak is the model measured with the recordings' procedure, the
analytic formula is reported only. tests/py/test_bars_calib.py gates on checks().
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bars_model as gm  # noqa: E402
import bartube as btm    # noqa: E402
import hammer as hm      # noqa: E402
import rh_coefs as rc    # noqa: E402

FS = gm.FS
VA_KEYS = [53, 60, 65, 69, 77, 84, 89]
SOFT, YARN, DEFAULT, CORD, RUBBER = 0, 42, 64, 85, 127   # MALLET at the families (gen_bars FAM_DETENT)
MEDK = dict(kf=5 << 8)   # the reference checks play the generator's calibration law (tools/gen_bars.py CAL: the MED
                         # YARN of before 2026-10-17, which the radiation / torsion / V4 fits use; kept in the spec,
                         # no MALLET value reaches it)
LN1000 = 6.907755279


def cents(a, b):
    return 1200 * math.log2(a / b)


def word_freq(w, fs=FS):
    """Frequency of a slot's MC3 pole from its words (rh_coef at TUNE 0, DECAY 64)."""
    E, eps = rc.coef(w, w["d"])
    E, eps = E / 2.0 ** 30, eps / 2.0 ** 30
    lam = np.linalg.eigvals(np.array([[1, E], [-E, 1 - eps - E * E]]))
    return float(abs(np.angle(lam[0]))) * fs / (2 * math.pi)


def peak_freq(x, f, t0, t1):
    """The spectral peak near f (Hann, zero-padded, parabolic interpolation on the log magnitude)."""
    seg = x[int(t0 * FS):int(t1 * FS)]
    nf = 1 << 22
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nf))
    fr = np.fft.rfftfreq(nf, 1 / FS)
    m = (fr > 0.97 * f) & (fr < 1.03 * f)
    i = np.flatnonzero(m)[np.argmax(S[m])]
    a, b, c = np.log(S[i - 1:i + 2] + 1e-300)
    p = 0.5 * (a - c) / (a - 2 * b + c)
    return float(fr[i] + p * (fr[1] - fr[0]))


def ref_band_t60(x, f0, floor_dbfs=-80.0, seed=0):
    """V1 T60 as tools/vib_ref_measure.measure() reads it from a recording: white noise at floor_dbfs (RMS, Q15) added
    (deterministic), the 5 ms RMS envelope's onset (the first frame within 40 dB of its peak), the f0 band's level
    (band_env) from there, fit_t60 from its peak to 10 dB over the floor (the median of the last tenth, - 6 dB)"""
    import vib_ref_measure as vrm
    x = np.asarray(x, float) + np.random.default_rng(seed).normal(0, 32768 * 10 ** (floor_dbfs / 20), len(x))
    hop = int(0.005 * FS)
    env = vrm.envelope_db(x, FS, hop)
    pk = int(np.argmax(env))
    onset = next((i for i in range(pk + 1) if env[i] > env[pk] - 40), 0)
    floor = float(np.percentile(env[-max(10, len(env) // 10):], 50))
    lv, _ = vrm.band_env(x[onset * hop:], FS, f0, 0, hop)
    return vrm.fit_t60(lv, hop / FS, floor - 6)[0]


def am_depth(env_db, t, t0, t1):
    """Peak-to-trough (dB) of an envelope over [t0, t1] after removing its linear decay."""
    m = (t >= t0) & (t <= t1)
    y = env_db[m]
    y = y - np.polyval(np.polyfit(t[m], y, 1), t[m])
    return float(np.percentile(y, 98) - np.percentile(y, 2))


def frame_env(x, hop=64, win=512):
    n = (len(x) - win) // hop
    e = np.array([np.sqrt(np.mean(x[i * hop:i * hop + win] ** 2)) for i in range(n)])
    return 20 * np.log10(e + 1e-12), (np.arange(n) * hop + win / 2) / FS


def band_env(x, f, rel=0.06, hop=64):
    """Envelope (dB) of the band f +- rel (FFT brick-wall filter, then |analytic signal|)."""
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    X[(fr < (1 - rel) * f) | (fr > (1 + rel) * f)] = 0
    from scipy.signal import hilbert
    y = np.fft.irfft(X, len(x))
    e = np.abs(hilbert(y))[::hop]
    return 20 * np.log10(e + 1e-12), np.arange(len(e)) * hop / FS


def checks(spec=None):
    """[(name, value, ok, kind)] kind: 'gate' or 'report'."""
    spec = spec or gm.load_spec()
    vb = spec["vibes"]
    D = {d["midi"]: d for d in spec["design"]}
    K = {k["midi"]: k for k in spec["keys"]}
    out = []

    def add(name, value, ok, kind="gate"):
        out.append((name, value, bool(ok), kind))

    # ---------------------------------------------------------- frequencies
    keys = VA_KEYS
    r = gm.render_batch(spec, keys, [100] * len(keys), 3.0, None, slots=True, sleep=False)
    worst = 0.0
    for i, n in enumerate(keys):
        f = peak_freq(r["slots"][i, 0], D[n]["f0"], 1.2, 3.0)
        worst = max(worst, abs(cents(f, D[n]["f0"])))
    add("V1 normal mode at f0 (golden, 1.2..3 s), worst |c| at keys %s" % keys, round(worst, 3), worst <= 0.5)
    wv, wt_ = 0.0, 0.0
    for n in range(21, 109):
        for s, m in zip(range(2, 7), D[n]["modes"][:5]):
            w = K[n]["modes"][s]["w"]
            if w["th"]:
                c = abs(cents(word_freq(w), m["f"]))
                if s <= 3:
                    wv = max(wv, c)
                else:
                    wt_ = max(wt_, c)
    add("V2 V3 slots on their designed (detuned) targets, worst |c|", round(wv, 3), wv <= 0.5)
    add("V4 T1 T2 slots on the per-key data, worst |c|", round(wt_, 3), wt_ <= 0.5)
    det = [(cents(D[n]["modes"][0]["f"], 4 * D[n]["f0"]), cents(D[n]["modes"][1]["f"], 10 * D[n]["f0"])) for n in range(21, 109)]
    d2 = max(abs(a) for a, _ in det)
    d3 = (min(b for _, b in det), max(b for _, b in det))
    add("V2 detune within +-15 c of 4x (T11 0..+7 c)", round(d2, 2), d2 <= 15)
    add("V3 detune vs 10x (T11 -50..+110 c; the plan's +-15 c contradicts T11)", [round(x, 1) for x in d3],
        vb["T11"]["V3_c"][0] - 0.01 <= d3[0] and d3[1] <= vb["T11"]["V3_c"][1] + 0.01, "report")

    # ---------------------------------------------------------- decays
    keys = VA_KEYS
    rh = gm.render_batch(spec, keys, [100] * len(keys), 6.0, None, slots=True, sleep=False)
    off = 0.5
    rf = gm.render_batch(spec, keys, [100] * len(keys), off + 1.6, None, slots=True, sleep=False, off_at=off)
    worst_h, worst_f, rows = 0.0, 0.0, []
    for i, n in enumerate(keys):
        for s in range(2, 7):
            m = D[n]["modes"][s - 2]
            if not K[n]["modes"][s]["b"]:
                continue
            tr = rh["slots"][i, s]
            t0, t1 = 0.05, min(0.05 + 1.2 * m["t60"], 5.5)
            th = gm.env_t60(tr, t0, t1)
            e = abs(th / m["t60"] - 1)
            worst_h = max(worst_h, e)
            tf = gm.env_t60(rf["slots"][i, s], off + 0.06, off + 0.06 + min(1.2 * m["t60_rel"], 1.5))
            ef = abs(tf / m["t60_rel"] - 1)
            worst_f = max(worst_f, ef)
            rows.append((n, m["name"], round(m["t60"], 3), round(th, 3), round(m["t60_rel"], 3), round(tf, 3)))
    add("upper-mode T60 held vs design (vibes.json), worst rel. error", round(worst_h, 4), worst_h <= 0.05)
    add("upper-mode T60 felt vs design, worst rel. error", round(worst_f, 4), worst_f <= 0.05)
    capb = max(m["t60"] * m["f"] / vb["cap"]["t60_hz_s"] for d in spec["design"] for m in d["modes"] if m["f"] > 0)
    capb = max(capb, max(d["t1a"] * d["f0"] / vb["cap"]["t60_hz_s"] for d in spec["design"]))
    add("cap T60_n <= 7330 / f_n over every mode and key (max ratio)", round(capb, 4), capb <= 1.0 + 1e-9)
    # V1-with-tube T60 at every key vs T1b (the design / normal mode), flagged keys allowed
    bad = []
    for n in range(21, 109):
        d = D[n]
        e = d["t60_v1"] / d["t1b"] - 1
        if abs(e) > 0.10 and not (d["flags"] & 4):
            bad.append((n, round(e, 3)))
    nfl = sum(1 for d in spec["design"] if d["flags"] & 4)
    add("V1-with-tube T60 within +-10 % of T1b at every key or flagged (unflagged misses)", bad, not bad)
    add("keys flagged (sigma clamped / weak-limited)", nfl, True, "report")
    # golden check: the measured V1 decay of the bar slot 2..5.5 s vs the design
    worst = 0.0
    for i, n in enumerate(keys):
        t = gm.env_t60(rh["slots"][i, 0], 2.0, 5.5)
        worst = max(worst, abs(t / D[n]["t60_v1"] - 1))
    add("golden V1 decay (2..5.5 s) vs the pair design, worst rel.", round(worst, 4), worst <= 0.05)
    # felt V1
    worst = 0.0
    for i, n in enumerate(keys):
        t = gm.env_t60(rf["slots"][i, 0], off + 0.3, off + 1.2)
        worst = max(worst, abs(t / K[n]["modes"][0]["t60_rel"] - 1))
    add("golden V1 felt decay vs the design (normal mode with the felt), worst rel.", round(worst, 4), worst <= 0.10)
    ww = max(d["sigma"] * gm_s127(spec) - vb["T5a"]["weak_margin"] * (d["zeta_r"] - d["zeta_n"]) for d in spec["design"])
    add("weak regime at BELL 127: max of s sigma - 0.9 (zeta_r - zeta_n)", ww, ww <= 1e-12)

    # ---------------------------------------------------------- tube
    import gen_bars as gb
    xs, sm = gb.peaks_smoothed(vb)
    raw = dict(zip(*gb.table(vb["T5c"]["peak_ms"])))
    y = gm.render_batch(spec, xs, [100] * len(xs), 0.8, None, sleep=False)
    fails, rep = [], []
    for n, t, r_ in zip(xs, sm, y):
        pk = btm.f0_peak_ms(r_, D[n]["f0"])
        tol = max(15.0, 0.3 * t)
        if abs(pk - t) > tol:
            fails.append((n, pk, round(t, 1)))
        rep.append((n, pk, round(t, 1), raw[n]))
    add("f0 peak (recordings' procedure) within +-30 %% / +-15 ms of the smoothed table at %d notes: misses" % len(xs),
        fails, not fails)
    rr = [(n, p, r0) for n, p, _, r0 in rep if abs(p - r0) > max(15.0, 0.3 * r0)]
    add("f0 peak vs the RAW per-note times (report): misses", rr, True, "report")
    # elsewhere: within +-25 % of the formula... the coordinator replaced the formula comparison: report it only
    fr = [(d["midi"], round(d["peak_ms"]), round(d["peak_formula_ms"])) for d in spec["design"] if d["midi"] in (40, 72, 84, 100)]
    add("peak: model (procedure) vs analytic formula, report", fr, True, "report")
    # BELL 32 / 64 / 96 / 127: t_peak moves earlier
    for n in (57, 65):
        pks = []
        for b in (32, 64, 96, 127):
            yy = gm.render_batch(spec, [n], [100], 0.8, dict(gm.DEFAULT_KNOBS, bell=b), sleep=False)[0]
            pks.append(btm.f0_peak_ms(yy, D[n]["f0"]))
        add("key %d: f0 peak over BELL 64/96/127 moves earlier (non-increasing); BELL 32 first, report" % n, pks,
            all(a >= b for a, b in zip(pks[1:], pks[2:])))
    # tube / bar ratio at 0.2 s, BELL 64 (T5b)
    p = gm.render_batch(spec, VA_KEYS, [100] * len(VA_KEYS), 0.4, dict(gm.DEFAULT_KNOBS, bark=SOFT), parts=True, sleep=False)
    worst = 0.0
    for i, n in enumerate(VA_KEYS):
        eb = band_env(p["bar"][i], D[n]["f0"], 0.02)
        et = band_env(p["tube"][i], D[n]["f0"], 0.02)
        j = int(0.2 * FS / 64)
        worst = max(worst, abs((et[0][j] - eb[0][j]) - vb["T5b"]["db"]))
    add("tube part over the bar's direct f0 at 0.2 s, BELL 64: worst |dB - T5b|", round(worst, 2), worst <= 3.0)
    # beats: +50 c tube detune at F3 -> envelope modulation at |df|
    d = D[53]
    bt = btm.BarTube(d["f0"], d["f0"] * 2 ** (50 / 1200), d["sigma"], LN1000 / d["t1a"], LN1000 / d["t60_tube"]).retune(d["f0"])
    x = bt.impulse(FS, int(0.8 * FS), wt=d["wt"])
    from scipy.signal import argrelmin, hilbert
    env = np.abs(hilbert(x))[::8]
    mins = [i for i in argrelmin(env, order=100)[0] if i * 8 > 0.02 * FS]
    rate = FS / (mins[0] * 8) if mins else 0.0             # the tube rings ~1 beat: the first envelope minimum
    df = abs(bt.modes()[0][0] - bt.modes()[1][0])
    want = d["f0"] * (2 ** (50 / 1200) - 1)
    add("beats: +50 c tube at F3, envelope rate (Hz) vs |df| %.2f (nominal %.2f)" % (df, want), round(rate, 2),
        abs(rate / want - 1) <= 0.10)

    # ---------------------------------------------------------- reference decays / levels
    xs, ys = gb.t1b_smoothed(vb)
    rawt = dict(zip(*gb.table(vb["T1b"]["t60_s"])))
    bad = [(n, round(D[n]["t60_v1"], 2), round(s, 2)) for n, s in zip(xs, ys) if abs(D[n]["t60_v1"] / s - 1) > 0.15]
    add("V1 T60 at the reference pitches within +-15 % of the smoothed table: misses", bad, not bad)
    # the same quantity the reference table holds: the golden render (v100, the default cards, pedal down, the
    # reference files' 6 s) measured with vib_ref_measure's own procedure (onset, the f0 band's 4096-point STFT,
    # the line from its peak to 10 dB over the noise floor), over a recording-like floor (white noise, -80 dBFS RMS).
    # Without a floor the engine's exact digital zero at the end (the voice ending under 1 LSB; through the effects,
    # the silence gate's fade under -66 dBFS) puts the cliff into the fit and reads ~6 % short (the FM-1 sample-back's
    # raw host / device readings): the floor is the recordings' condition, not a correction
    yb = gm.render_batch(spec, xs, [100] * len(xs), 6.0, None, sleep=False)
    bad, rows = [], []
    for n, sm_, r_ in zip(xs, ys, yb):
        t = ref_band_t60(r_, D[n]["f0"])
        rows.append((n, round(t, 2), round(sm_, 2)))
        if t is None or abs(t / sm_ - 1) > 0.02:
            bad.append((n, t and round(t, 2), round(sm_, 2)))
    add("V1 T60 by the recordings' band fit (6 s, -80 dBFS floor) within +-2 % of the smoothed table: misses", bad, not bad)
    bad = [(n, round(D[n]["t60_v1"], 2), rawt[n]) for n in xs if abs(D[n]["t60_v1"] / rawt[n] - 1) > 0.30]
    add("V1 T60 vs the RAW per-note values +-30 % (report): misses", bad, True, "report")
    for nm, slot in (("V2", 2), ("V3", 3)):
        tb = vb["T2"][nm]["t60_s"]
        bad = [(int(n), round(D[int(n)]["modes"][slot - 2]["t60"], 2), v) for n, v in tb.items()
               if abs(D[int(n)]["modes"][slot - 2]["t60"] / v - 1) > 0.25]
        add("%s T60 within +-25 %% of the per-note values: misses" % nm, bad, not bad)
    ks = sorted(int(n) for n in vb["T2"]["V2"]["level_db"])
    y = gm.render_batch(spec, ks, [100] * len(ks), 0.6, dict(gm.DEFAULT_KNOBS, **MEDK), sleep=False)
    lv = [gm.band_level(r_, D[n]["modes"][0]["f"], 0.03, 0.53, 0.015) - gm.band_level(r_, D[n]["f0"], 0.03, 0.53, 0.015)
          for n, r_ in zip(ks, y)]
    lo, hi = -47.0, -17.0
    add("V2 level re V1 (TONE 0, MED, v100, 30..530 ms) within the measured -17..-47 dB (+-2 dB)",
        [round(v, 1) for v in lv], all(lo - 2 <= v <= hi + 2 for v in lv))
    sl = np.polyfit(ks, lv, 1)[0]
    add("V2 level decreases with pitch (slope dB / key)", round(float(sl), 3), sl < 0)
    # the reference has no V2 above G5 and no V3 above C5: there they are under its -50 dB detection floor with its
    # mallet (vibes.json T2 _doc). The bar keeps them for the harder mallets; MED YARN v100 stays under the floor
    for nm, slot, k0 in (("V2", 2, 80), ("V3", 3, 73)):
        ks = [n for n in range(k0, 109) if K[n]["modes"][slot]["b"]]
        y = gm.render_batch(spec, ks, [100] * len(ks), 0.6, dict(gm.DEFAULT_KNOBS, **MEDK), sleep=False) if ks else []
        hi = [(n, round(gm.band_level(r_, D[n]["modes"][slot - 2]["f"], 0.03, 0.53, 0.015)
                        - gm.band_level(r_, D[n]["f0"], 0.03, 0.53, 0.015), 1)) for n, r_ in zip(ks, y)]
        bad = [x for x in hi if x[1] > -50]
        add("%s above %s (keys >= %d): MED v100 under the reference's -50 dB detection floor re V1 (30..530 ms), "
            "misses" % (nm, "G5" if slot == 2 else "C5", k0), bad, not bad)
    add("radiation trims V2 / V3 / V4 (dB, R1b)", {k: round(v, 1) for k, v in spec["rad_db"].items()}, True, "report")
    # V4 (R1b _doc_v4): MED v100 V4 re V1 against the measured levels F3..G4. The measured V4 is flat while V3 falls,
    # which no mallet pulse gives: V4 follows it from F3 to C4 and stays under it above (the brightest-voicing ceiling)
    ref4 = {int(n): v for n, v in vb["T2"]["V4"]["level_db"].items()}
    ks = list(range(53, 68))
    y = gm.render_batch(spec, ks, [100] * len(ks), 0.6, dict(gm.DEFAULT_KNOBS, **MEDK), sleep=False)
    l4 = {n: gm.band_level(r_, D[n]["modes"][2]["f"], 0.03, 0.53, 0.015) - gm.band_level(r_, D[n]["f0"], 0.03, 0.53, 0.015)
          for n, r_ in zip(ks, y)}
    lo4 = [(n, round(l4[n], 1), ref4[n]) for n in sorted(ref4) if n <= 60]
    add("V4 level re V1 (TONE 0, MED, v100, 30..530 ms) at the F3..C4 reference notes within +-10 dB of the measured "
        "(F3 was -16 vs -45)", lo4, all(abs(a - b) <= 10 for _, a, b in lo4))
    hi4 = max(l4.values())
    add("V4 level F3..G4 never over the measured maximum + 3 dB (%.0f)" % max(ref4.values()), round(hi4, 1),
        hi4 <= max(ref4.values()) + 3)
    add("V4 level vs the measured, every key F3..G4 (report: model, measured)",
        [(n, round(l4[n], 1), ref4.get(n)) for n in ks], True, "report")

    # ---------------------------------------------------------- mallets
    fams = vb["T7c"]["families"]
    bad = []
    for n in range(53, 90):
        for q, fm in enumerate(fams):
            if "blend" in fm:                                    # the default mallet: two laws blended (checked as such)
                continue
            tc = D[n]["mallet"][q]["tc_ms"][1:9]                 # the layers v8..v120
            win = D[n]["mallet"][q]["win_ms"][1:9]
            lo, hi = fm["tc_window_ms"]
            # the contact itself; where T7f's main-lobe cap plays it faster (the yarn families on the top bars), the
            # played window is the cap by design
            if min(tc) < lo - 1e-9 or max(tc) > hi + 1e-9:
                bad.append((n, fm["short"], round(min(tc), 3), round(max(tc), 3)))
            cap = vb["T7f"]["f_tau_max"] / D[n]["f0"] * 1e3
            if any(w_ > min(t_, cap) + 1e-3 for t_, w_ in zip(tc, win)):
                bad.append((n, fm["short"], "played", round(max(win), 3), round(cap, 3)))
    add("contact times per family x layer in the T7c windows (played: the contact, or T7f's cap), F3..F6: misses",
        bad[:12], not bad)
    # independent hammer.contact() at C4 v100
    worst = 0.0
    import gen_bars as gb
    for q, fm in enumerate(fams):
        if "blend" in fm:
            continue
        n = 60
        kd = D[n]
        v = 100
        sp = spec["mallet_v"][v]
        md = [(kd["f0"], LN1000 / kd["t1a"], spec_phi(spec, n, 0))] + \
             [(kd["modes"][i]["f"], LN1000 / kd["modes"][i]["t60"], spec_phi(spec, n, i + 1)) for i in range(3)]
        sim = hm.contact(sp, 30e-3, kd["mallet"][q]["K"], fm["p"], 0.0, md, fs=FS, t_max=0.01, os_=64)
        tc_ind = sim["Tc_first"] * 1e3
        ia = np.interp(v, gm.V_ANCH, kd["mallet"][q]["tc_ms"])
        worst = max(worst, abs(ia / tc_ind - 1))
    add("contact time vs an independent hammer.contact() run at C4 v100, worst rel.", round(worst, 3), worst <= 0.10)
    # pulse spectrum -6 dB point
    worst, yarn = 0.0, []
    for q in range(len(fams)):
        if "blend" in fams[q]:
            continue
        sh = np.array(D[60]["mallet"][q]["shapes"][5], float)    # v88 layer
        tau = D[60]["mallet"][q]["win_ms"][6] * 1e-3
        nf = 1 << 16
        S = np.abs(np.fft.rfft(sh, nf))
        S /= S[0]
        fr = np.fft.rfftfreq(nf, tau / (len(sh) - 1))
        f6 = float(fr[np.argmax(S < 10 ** (-6 / 20))])
        if fams[q]["p"] <= 1.5:
            worst = max(worst, abs(f6 * tau / vb["T7e"]["f_tau_6db"] - 1))
        else:
            yarn.append((fams[q]["short"], round(f6 * tau, 3)))
    add("pulse spectrum: -6 dB at f tau = 0.82 (the half-sine rule), p = 1.5 families, worst rel. (C4 v88)", round(worst, 3),
        worst <= 0.15)
    add("pulse spectrum f tau at -6 dB of the yarn families (p 2, 2.5: a more peaked pulse than a half-sine), report",
        yarn, True, "report")
    # centroid RUBBER >= 2 SOFT at C5 v100: over the attack (0..50 ms, where the mallet is heard), 0..300 ms reported
    y = gm.render_batch(spec, [72, 72], [100, 100], 0.4, [dict(bark=RUBBER), dict(bark=SOFT)], sleep=False)
    for t1, kind in ((0.05, "gate"), (0.3, "report")):
        cen = []
        for r_ in y:
            nn = int(t1 * FS)
            S = np.abs(np.fft.rfft(r_[:nn] * np.hanning(nn), 1 << 15)) ** 2
            fr = np.fft.rfftfreq(1 << 15, 1 / FS)
            cen.append(float(np.sum(S * fr) / np.sum(S)))
        add("C5 v100 spectral centroid RUBBER / SOFT >= 2, 0..%d ms" % (t1 * 1000), round(cen[0] / cen[1], 2),
            cen[0] >= 2 * cen[1], kind)
    # V3/V1 at v127 RUBBER >= V3/V1 at v40 SOFT + 20 dB (C4)
    y = gm.render_batch(spec, [60, 60], [127, 40], 0.4, [dict(bark=RUBBER), dict(bark=SOFT)], sleep=False)
    f3 = D[60]["modes"][1]["f"]
    rr = [gm.band_level(r_, f3, 0.01, 0.3, 0.015) - gm.band_level(r_, D[60]["f0"], 0.01, 0.3, 0.015) for r_ in y]
    add("C4 V3/V1: RUBBER v127 - SOFT v40 >= 20 dB", round(rr[0] - rr[1], 1), rr[0] - rr[1] >= 20)
    worst = 0.0
    for q, fm in enumerate(fams):
        if "blend" in fm:
            continue
        for n in (53, 60, 69):
            tc = D[n]["mallet"][q]["tc_ms"]
            want = 11.67 ** (-(fm["p"] - 1) / (fm["p"] + 1))
            worst = max(worst, abs(tc[-1] / tc[1] / want - 1))
    add("tau(v127) / tau(v8) per family = 11.67^(-(p-1)/(p+1)) +-10 %, worst rel.", round(worst, 3), worst <= 0.10)

    # ---------------------------------------------------------- strike position
    ks = [53, 60, 65]
    y = gm.render_batch(spec, ks, [100] * 3, 0.4, dict(gm.DEFAULT_KNOBS, tone=0), slots=True, sleep=False)
    v2 = max(20 * math.log10(np.abs(y["slots"][i, 2]).max() / np.abs(y["slots"][i, 0]).max() + 1e-30) for i in range(3))
    add("TONE -64: V2 / V1 (slot peaks) <= -40 dB", round(v2, 1), v2 <= -40)
    # TONE 0 vs an independent modal simulation at the key's own FE weights at x = 0.35 L (the band table vs the key)
    y = gm.render_batch(spec, ks, [100] * 3, 0.3, dict(gm.DEFAULT_KNOBS, **MEDK), slots=True, sleep=False)
    worst = 0.0
    for i, n in enumerate(ks):
        g = 20 * math.log10(np.abs(y["slots"][i, 2]).max() / np.abs(y["slots"][i, 0]).max())
        ind = indep_v2v1(spec, n)
        worst = max(worst, abs(g - ind))
    add("TONE 0: V2 / V1 within +-3 dB of the key's own FE strike weights (band-table error)", round(worst, 2), worst <= 3)
    add("band strike tables vs each key's own FE weights, V2 V3 V4 re V1 at TONE raw 0 / 32 / 96 / 127 (worst dB where "
        "the weight is within 20 dB of its maximum; exact at raw 64), report", band_pos_error(spec), True, "report")
    y = gm.render_batch(spec, ks, [100] * 3, 0.03, dict(gm.DEFAULT_KNOBS, tone=127, **MEDK), slots=True, sleep=False)
    worst = 0.0
    for i in range(3):
        t1 = 20 * math.log10(np.abs(y["slots"][i, 5]).max() / np.abs(y["slots"][i, 0]).max())
        worst = max(worst, abs(t1 - vb["T9c"]["t1_db"]))
    add("TONE +63: T1 / V1 = T9c (%.0f dB) +-3 dB, worst" % vb["T9c"]["t1_db"], round(worst, 2), worst <= 3)
    y = gm.render_batch(spec, ks, [100] * 3, 0.1, dict(gm.DEFAULT_KNOBS, tone=64), slots=True, sleep=False)
    z = float(np.abs(y["slots"][:, 5:7]).max())
    add("T1 = T2 = 0 for TONE <= 0", z, z == 0.0)
    tr = [(d["midi"], round(d["fe"]["tors"][0], 2), round(d["fe"]["tors"][1], 2)) for d in spec["design"] if d["midi"] in (53, 65, 77, 89)]
    add("torsion ratios T1, T2 (T9b expected 2.4..3.4, 10..12.5)", tr, True, "report")

    # ---------------------------------------------------------- stability
    g = [d["grid"] for d in spec["design"]]
    add("stability grid: rho < 1 at every point of every key (max rho)", max(x["rho"] for x in g), all(x["rho"] < 1 for x in g))
    bad = [(d["midi"], round(d["grid"]["decay_err"], 3)) for d in spec["design"] if d["grid"]["decay_err"] > 0.02]
    add("stability grid: normal-mode decay rates within +-2 % of the float design (misses, keys), every key 21..108", bad,
        not bad)
    add("stability grid: worst normal-mode decay error, and the discrete coupling correction kc range (report)",
        (round(max(x["decay_err"] for x in g), 4), round(min(d["kc"] for d in spec["design"]), 3),
         round(max(d["kc"] for d in spec["design"]), 3)), True, "report")
    add("stability grid: V1 at f0 +-0.5 c (s = 1, g = 1, TUNE 0), worst", max(abs(x["v1_cents"]) for x in g),
        max(abs(x["v1_cents"]) for x in g) <= 0.5)

    # ---------------------------------------------------------- motor (VA-M)
    out += motor_checks(spec)
    return out


def gm_s127(spec):
    return spec["kn_tube"][127] / 32768.0


def spec_phi(spec, n, m):
    """The mass-normalised V-mode weight at the default strike point from the FE cache numbers in the spec."""
    import gen_bars as gb
    vb = spec["vibes"]
    d = {x["midi"]: x for x in spec["design"]}[n]
    fe_ = gb.fe_all(vb)[d["ng"]]
    return fe_["weights"][8][m]


def band_pos_error(spec):
    import gen_bars as gb
    fe_ = gb.fe_all(spec["vibes"])
    worst = 0.0
    for k in spec["keys"]:
        n = k["midi"]
        if not 53 <= n <= 89:
            continue
        W = np.array(fe_[n]["weights"])
        P = np.array(spec["bar_pos"][k["band"]], float)
        q = spec["pos_exact"][k["band"]]
        cols = {0: 0, 2: 1, 3: 2, 4: 3}
        eff = {s: W[q[s], c] / P[q[s], s] for s, c in cols.items()}       # b per unit table weight
        for p in (0, 4, 12, 16):
            for s, c in cols.items():
                if s == 0 or not k["modes"][s]["b"] or W[p, c] < 0.1 * W[:, c].max() or W[p, 0] <= 0:
                    continue
                e = (eff[s] * P[p, s] / (eff[0] * P[p, 0])) / (W[p, c] / W[p, 0])
                worst = max(worst, abs(20 * math.log10(e)))
    return round(worst, 2)


def indep_v2v1(spec, n):
    """V2 / V1 slot-peak ratio (dB) for TONE 0, MED v100 from the key's OWN FE weights at x = 0.35 L, the same
    radiation weights and trims, and the key's pulse: the golden model's band table should agree."""
    import gen_bars as gb
    vb = spec["vibes"]
    d = {x["midi"]: x for x in spec["design"]}[n]
    W = gb.fe_all(vb)[d["ng"]]["weights"]
    k = {x["midi"]: x for x in spec["keys"]}[n]
    q = spec["pos_exact"][k["band"]]
    P = spec["bar_pos"][k["band"]]
    # the key's b make the band table exact at the point q (gen_bars: b ~ W_key(q) / P_band(q)); the key's own weight
    # at point 8 (TONE raw 64) is then b W(8) P(q) / W(q)
    v1 = k["modes"][0]["b"] * W[8][0] * P[q[0]][0] / W[q[0]][0]
    v2 = k["modes"][2]["b"] * W[8][1] * P[q[2]][2] / W[q[2]][1]
    g1 = k["modes"][0]["b"] * P[8][0] / 16384.0
    g2 = k["modes"][2]["b"] * P[8][2] / 16384.0
    y = gm.render_batch(spec, [n], [100], 0.3, dict(gm.DEFAULT_KNOBS, **MEDK), slots=True, sleep=False)
    gold = 20 * math.log10(np.abs(y["slots"][0, 2]).max() / np.abs(y["slots"][0, 0]).max())
    return gold + 20 * math.log10((v2 / v1) / (g2 / g1))


def motor_checks(spec):
    out = []
    D = {d["midi"]: d for d in spec["design"]}
    vb = spec["vibes"]
    n = 69
    mot = dict(rate=78, depth=127, shape=0)
    r = gm.render_batch(spec, [n, n], [100, 100], 3.0, None, motor=None, sleep=False)[0]
    rm = gm.render_batch(spec, [n], [100], 3.0, None, motor=mot, sleep=False)[0]
    e, t = frame_env(rm, 64, 512)
    full = am_depth(e, t, 1.5, 2.5)
    lo, hi = vb["T6d"]["full_db"]
    out.append(("VA-M A4 depth 127 3.5 Hz: full-band peak-to-trough 1.5..2.5 s (dB) in %s" % [lo, hi], round(full, 2),
                lo <= full <= hi, "gate"))
    e0, t0 = band_env(rm, D[n]["f0"], 0.02)
    f0d = am_depth(e0, t0, 1.5, 2.5)
    out.append(("VA-M A4 f0-band depth (dB, report; >= 10)", round(f0d, 2), True, "report"))
    f3 = D[n]["modes"][1]["f"]
    e3, t3 = band_env(rm, f3, 0.02)
    v3 = am_depth(e3, t3, 0.1, 0.4)
    e3o, _ = band_env(r, f3, 0.02)
    v3o = am_depth(e3o, t3, 0.1, 0.4)
    out.append(("VA-M A4 V3-band AM 0.1..0.4 s (dB, minus the unmodulated measure %.2f) <= 1" % v3o, round(v3 - v3o, 2),
                v3 - v3o <= vb["T6d"]["v3_max_db"], "gate"))
    # rate: the AM's spectral peak
    m = (t >= 0.5) & (t <= 2.9)
    ee = e[m] - np.polyval(np.polyfit(t[m], e[m], 2), t[m])
    S = np.abs(np.fft.rfft(ee * np.hanning(len(ee)), 1 << 16))
    fr = np.fft.rfftfreq(1 << 16, 64 / FS)
    k = (fr > 0.5) & (fr < 12)
    rate = float(fr[k][np.argmax(S[k])])
    want = 0.5 * 24 ** (78 / 127)
    out.append(("VA-M rate (Hz) vs the display %.2f, +-5 %%" % want, round(rate, 3), abs(rate / want - 1) <= 0.05, "gate"))
    # depth at F3 over 1, 3.5, 7, 10 Hz: monotone decreasing
    deps = []
    for v in (31, 78, 105, 120):
        x = gm.render_batch(spec, [53], [100], 3.0, None, motor=dict(rate=v, depth=127, shape=0), sleep=False)[0]
        ee_, tt = frame_env(x, 64, 512)
        deps.append(round(am_depth(ee_, tt, 1.0, 2.5), 2))
    out.append(("VA-M F3 depth over 1 / 3.5 / 7 / 10 Hz decreasing (dB)", deps, all(a >= b for a, b in zip(deps, deps[1:])), "gate"))
    # FM wobble of the f0 component (report, sanity +-10 c)
    p = gm.render_batch(spec, [n], [100], 3.0, None, motor=mot, slots=True, sleep=False)
    from scipy.signal import hilbert
    x = p["out"][0][int(1.0 * FS):int(2.5 * FS)]
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    X[(fr < 0.98 * D[n]["f0"]) | (fr > 1.02 * D[n]["f0"])] = 0
    a = hilbert(np.fft.irfft(X, len(x)))
    ph = np.unwrap(np.angle(a))
    fi = np.diff(ph) * FS / (2 * math.pi)
    fi = fi[2000:-2000]
    wob = float(np.max(np.abs(1200 * np.log2(fi / D[n]["f0"]))))
    out.append(("VA-M V1 instantaneous-frequency wobble (c), sanity <= 10", round(wob, 2), wob <= vb["T6f"]["bound_c"], "report"))
    return out


def main():
    res = checks()
    L = ["# Gate VA on the golden model (tools/bars_va.py)", ""]
    for name, val, ok, kind in res:
        tag = ("PASS" if ok else "FAIL") if kind == "gate" else "REPORT"
        L.append("%-6s %s: %s" % (tag, name, val))
    ng = sum(1 for r in res if r[3] == "gate")
    nf = sum(1 for r in res if r[3] == "gate" and not r[2])
    L += ["", "%d gated checks, %d failed" % (ng, nf)]
    os.makedirs(os.path.join(ROOT, "build", "bars_va"), exist_ok=True)
    open(os.path.join(ROOT, "build", "bars_va", "report.txt"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
