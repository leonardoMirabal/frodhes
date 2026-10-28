#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""HQ VIBES and MARIMBA (private plan 05 M4-01, plan 07): the audible size of each candidate before any design.

The bar voice today (bars.c, bars_model.py) runs 8 slots: V1 + TUBE as a pair, V2 V3 V4 (bending), T1 T2 (torsion),
X (unused). Its mallet pulse is the contact against a bar at rest, simulated at 10 velocity anchors with V1..V4 at
TONE 0's point (gen_bars.contacts), blended per note from 8 velocity layers (and two families at a blend detent).
Three studies, per type, on the FE bar of each geometric key (the pinned profiles), with the default mallet (MALLET
64's family, HAMMER 64's mass):

  modes    the modes the voice leaves out: bending V5 V6 V7, torsion T3 T4, lateral L1 L2. Each mode's amplitude
           after the contact (hammer.contact_batch with every mode in), at the output as bars.c reads it (the modal
           displacement at the strike point), decaying at the type's law (VIBES: the T2 upper-mode law; MARIMBA: the
           Rayleigh law). Printed: frequency and energy re V1..V4's, over the first 10 ms and over the whole note,
           at TONE 64 (on the centreline: torsion is not struck) and TONE 127 (off-axis: torsion is). Lateral modes
           take no force from a vertical strike; they are printed with a 10 degree oblique strike as a bound.
  contact  the table pulse against the contact computed per strike: (a) velocity between two anchors (the tables
           blend the anchors' results; per strike is exact): each mode's amplitude error; (b) a re-strike while the
           bar rings (the tables add a fresh at-rest strike to the ringing state; per strike the mallet meets the
           moving bar): the difference's energy re the note, over re-strike delays and phases.
  all      both.

    python3 tools/bars_hq_study.py modes|contact|all [--type vibes|marimba|both] [--keys ...] [--v 40,100,127]
"""
import argparse
import contextlib
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_fe as fe  # noqa: E402
import bars_model as gm  # noqa: E402
import gen_bars as gb  # noqa: E402
import hammer as hm  # noqa: E402

FS = gb.FS
LN1000 = math.log(1000.0)
OBLIQUE = math.sin(math.radians(10.0))
SPECS = {"VIBES": os.path.join(gb.ROOT, "docs", "model", "data", "bars_spec.json"),
         "MARIMBA": os.path.join(gb.ROOT, "docs", "model", "data", "marimba_spec.json")}


@contextlib.contextmanager
def the_type(name):
    if name == "vibes":
        yield gb.load_vibes()
        return
    vb = json.load(open(gb.MARIMBA))
    with gb.type_globals(vb):
        yield vb


def fe_bar(vb, fec, ng):
    q = gb.fe_setup(vb, ng)
    A = np.array(fec[ng]["A"], float)
    return fe.Bar(q["L"], q["w"], q["t"], fe.cos_profile(A, q["L"], q["t"], q["h_min"]), **q["mat"]), q


def mallet(vb):
    """the default mallet: MALLET 64's family (its first law), HAMMER 64's mass"""
    q = gb.FAM_DETENT.index(64)
    law = gb.fam_laws(vb["T7c"]["families"][q])[0]
    return dict(m_h=gb.mass_kg(vb, 64), K=gb.law_K(vb, law), p=law["p"])


def t60(vb, n, ratio, name):
    if name in ("V2", "V3", "V4") and not gb.is_mar(vb):
        return gb.upper_t60(vb, n, name, ratio)
    return gb.law_t60(vb, n, ratio)


def key_modes(vb, fec, ng, tone):
    """[(name, f, sigma, weight)] V1..V7, T1..T4, L1, L2 at TONE tone's strike point"""
    bar, q = fe_bar(vb, fec, ng)
    x, y = gb.strike_xy(vb, tone, q["L"], q["w"], bar.v1_nodes())
    fb, sb = bar.bending(7)
    ft, st = bar.torsion(4)
    fl, sl = bar.bending(2, lateral=True)
    f0 = fb[0]
    out = []
    for m in range(7):
        nm = "V%d" % (m + 1)
        s = LN1000 / (gb.t1a(vb, ng) if m == 0 else t60(vb, ng, fb[m] / f0, nm))
        out.append((nm, fb[m], s, abs(sb(x, m))))
    for m in range(4):
        out.append(("T%d" % (m + 1), ft[m], LN1000 / t60(vb, ng, ft[m] / f0, "T"), abs(y * st(x, m))))
    for m in range(2):
        out.append(("L%d" % (m + 1), fl[m], LN1000 / t60(vb, ng, fl[m] / f0, "L"), OBLIQUE * abs(sl(x, m))))
    return out


VOICINGS = [("DEF v100", 64, 64, 100), ("RUBR h0 v127", 127, 0, 127)]   # (name, MALLET raw, HAMMER raw, v)
NEIGH = {"V5": "V4", "V6": "V4", "V7": "V4", "T3": "T2", "T4": "T2", "L1": "V2", "L2": "V2"}
SLOT = {"V2": 2, "V3": 3, "V4": 4, "T1": 5, "T2": 6}


def voicing_mallet(vb, bark, hammer):
    """the contact law of MALLET raw bark (the family at that detent, its first law) and HAMMER raw hammer's mass"""
    law = gb.fam_laws(vb["T7c"]["families"][gb.FAM_DETENT.index(bark)])[0]
    return dict(m_h=gb.mass_kg(vb, hammer), K=gb.law_K(vb, law), p=law["p"])


def onset_db(vb, md, r):
    """each mode's level re V1 at the onset (dB), untrimmed: the modal amplitude at the strike point after the
    contact times the R1 radiation law (f / f1)^exp. None above 20 kHz"""
    ex = vb["R1"]["exp"]
    f1 = md[0][1]
    a = [w * math.hypot(r["q"][m], r["qd"][m] / (2 * math.pi * f)) * (f / f1) ** ex for m, (_, f, _, w) in enumerate(md)]
    return [20 * math.log10(max(x, 1e-30) / a[0]) if md[m][1] < 20000.0 else None for m, x in enumerate(a)]


def engine_slots(vb, keys, bark, hammer, tone, v):
    """the voice's own slot levels re V1 (dB): the golden model (bars_model, the type's spec), each slot's peak
    over 5..50 ms (the prune rule's window), V1's slot the reference"""
    spec = gm.load_spec(SPECS[vb.get("type", "VIBES")])
    kn = dict(gm.DEFAULT_KNOBS, bark=bark, hammer=hammer, tone=tone)
    r = gm.render_batch(spec, keys, [v] * len(keys), 0.05, kn, sleep=False, slots=True)["slots"]
    pk = np.abs(r[:, :, int(0.005 * FS):]).max(axis=2)
    return {n: {nm: 20 * math.log10(max(pk[i, sl], 1e-30) / pk[i, 0]) for nm, sl in SLOT.items()}
            for i, n in enumerate(keys)}


def study_modes(vb, keys, vels, all_keys):
    """per left-out mode, its level re V1 at the onset, anchored on the voice's own neighbour slot: the engine's
    level of that slot (golden model: the R1b trims, the T9c torsion level, the prune) + the FE + contact difference
    between the two modes (the same R1 law). Every geometric key, two voicings x TONE 64 / 127; the count of keys
    over the prune rule's -60 dB, and the loudest key"""
    fec = gb.fe_all(vb)
    ty = vb.get("type", "VIBES")
    pr = vb["prune"]["db"]
    rows = []
    print(f"{ty}: left-out modes, onset level re V1 (dB) = the engine's neighbour slot + the FE/contact difference")
    for tone in (64, 127):
        for vname, bark, hammer, v in VOICINGS:
            eng = engine_slots(vb, all_keys, bark, hammer, tone, v)
            ml = voicing_mallet(vb, bark, hammer)
            est = {}
            for n in all_keys:
                md = key_modes(vb, fec, n, tone)
                names = [m[0] for m in md]
                r = hm.contact_batch([dict(v=gb.speed(vb, v), modes=[(f, s_, w) for _, f, s_, w in md], **ml)],
                                     fs=FS, t_max=0.012)[0]
                L = dict(zip(names, onset_db(vb, md, r)))
                fr = {nm: md[i][1] for i, nm in enumerate(names)}
                for nm, nb in NEIGH.items():
                    ok = L[nm] is not None and L[nb] is not None and eng[n][nb] > -900
                    est.setdefault(nm, {})[n] = (eng[n][nb] + L[nm] - L[nb]) if ok else None
                rows.append(dict(type=ty, tone=tone, voicing=vname, key=n, f=fr, untrimmed=L, engine=eng[n],
                                 est={nm: est[nm][n] for nm in NEIGH}))
            print(f" TONE {tone}, {vname}: engine V4 / T2 at the printed keys; per mode: keys over {pr:.0f} dB "
                  f"(of {len(all_keys)}), loudest (dB, key)")
            print("   " + "  ".join(f"key {n}: V4 {eng[n]['V4']:.0f} T2 {eng[n]['T2']:.0f}" for n in keys))
            for nm in NEIGH:
                xs = {n: x for n, x in est[nm].items() if x is not None}
                over = sum(x > pr for x in xs.values())
                top = max(xs, key=xs.get) if xs else None
                print(f"   {nm} ({NEIGH[nm]}'s trim): over {over:2d}" + (f", loudest {xs[top]:6.1f} at {top}" if xs else "")
                      + "  " + " ".join(f"{n}:{'-' if est[nm][n] is None else '%.0f' % est[nm][n]}" for n in keys))
    return rows


def free(q, qd, w, s, t):
    """modal states after t s of free decay (each mode a damped oscillator)"""
    wd = np.sqrt(np.maximum(w * w - s * s, 1e-9))
    e = np.exp(-s * t)
    c, sn = np.cos(wd * t), np.sin(wd * t)
    q1 = e * (q * c + (qd + s * q) / wd * sn)
    qd1 = e * (qd * c - (w * w * q + s * qd) / wd * sn)
    return q1, qd1


def study_contact(vb, keys, delays=(0.03, 0.06, 0.12, 0.25, 0.5)):
    fec = gb.fe_all(vb)
    ml = mallet(vb)
    print(f"{vb.get('type', 'VIBES')}: (a) velocity between anchors: per-mode amplitude error of the anchors' blend "
          "re the exact (dB, V1..V4 energy at the output, the R1 law); (b) re-strike on the ringing bar: the live "
          "contact against ringing + a fresh strike, the difference re their energy (dB), worst and median over "
          "delays x phases); (c) HAMMER 0 / 127 as the tables play it (the HAMMER 64 contact scaled by Hertz's law) "
          "against the contact at that mass: mode balance re V1, the error's energy (dB)")
    out = []
    for n in keys:
        md = key_modes(vb, fec, n, 64)[:4]                  # the tables' modes: V1..V4 at the centreline
        modes = [(f, s, w) for _, f, s, w in md]
        w = np.array([2 * math.pi * f for _, f, _, _ in md])
        s = np.array([x for _, _, x, _ in md])
        ph = np.array([x for _, _, _, x in md])
        anch = gb.V_ANCH
        mids = [(a, b, (a + b) // 2) for a, b in zip(anch[2:-1], anch[3:])]
        cases = [dict(v=gb.speed(vb, v), modes=modes, **ml) for v in anch] + \
                [dict(v=gb.speed(vb, m), modes=modes, **ml) for _, _, m in mids]
        res = hm.contact_batch(cases, fs=FS, t_max=0.012)
        rw = (w / w[0]) ** vb["R1"]["exp"] / np.sqrt(4 * s)        # output: the R1 law; energy: the whole decay
        cplx = lambda r: rw * ph * (r["q"] + 1j * r["qd"] / w)       # each mode's output phasor
        A = {v: np.abs(cplx(r)) for v, r in zip(anch, res)}       # magnitudes: the contact's own delay (each
        errs = []                                                   # velocity's phase) is a time shift, not heard
        for (a, b, m), r in zip(mids, res[len(anch):]):
            f = (m - a) / (b - a)
            bl = A[a] + (A[b] - A[a]) * f
            ex = np.abs(cplx(r))
            errs.append(10 * math.log10(np.sum((bl - ex) ** 2) / np.sum(ex ** 2) + 1e-30))
        # (c): HAMMER 0 / 127: the HAMMER 64 contact scaled in time and force (KN_MASS, KN_MASSI: Hertz's law on a
        # rigid wall) against the contact computed at that mass; the modes' magnitudes re V1, energy-weighted
        def spec_of(F, dt, tscale):
            t = (np.arange(len(F)) + 0.5) * dt * tscale
            return np.abs(np.array([np.sum(F * np.exp(-1j * wm * t)) for wm in w])) * rw * ph
        hres = hm.contact_batch([dict(v=gb.speed(vb, 100), modes=modes, m_h=gb.mass_kg(vb, h), K=ml["K"], p=ml["p"])
                                 for h in (64, 0, 127)], fs=FS, t_max=0.012)
        herr = []
        for h, r in zip((0, 127), hres[1:]):
            sc = spec_of(hres[0]["F"], hres[0]["dt"], (gb.mass_kg(vb, h) / gb.mass_kg(vb, 64)) ** (1.0 / 3.0))
            ex = spec_of(r["F"], r["dt"], 1.0)
            sc, ex = sc / sc[0], ex / ex[0]
            herr.append(10 * math.log10(np.sum((sc - ex) ** 2) / np.sum(ex ** 2) + 1e-30))
        # (b): a v100 strike, then after each delay a second v100 strike at 8 phases (delays spread a period)
        r1 = hm.contact_batch([dict(v=gb.speed(vb, 100), modes=modes, **ml)], fs=FS, t_max=0.012)[0]
        cs, keep = [], []
        T1 = 1.0 / md[0][1]
        for d in delays:
            for k in range(8):
                t = d + k * T1 / 8
                q0, qd0 = free(r1["q"], r1["qd"], w, s, t)
                cs.append(dict(v=gb.speed(vb, 100), modes=modes, q0=q0, qd0=qd0, xh0=float(np.sum(ph * q0)), **ml))
                keep.append((q0, qd0))
        rr = hm.contact_batch(cs, fs=FS, t_max=0.012)
        diffs = []
        for (q0, qd0), r in zip(keep, rr):                          # every state at one time T after the contact
            T = max(r["t_end"], r1["t_end"])
            at = lambda q, qd, t: cplx(dict(zip(("q", "qd"), free(q, qd, w, s, T - t))))
            live = at(r["q"], r["qd"], r["t_end"])                  # the contact with the moving bar
            ring = at(q0, qd0, 0.0)
            fresh = at(r1["q"], r1["qd"], r1["t_end"])
            tab = ring + fresh                                      # the table: ringing + a fresh at-rest strike
            ref = np.sum(np.abs(ring) ** 2) + np.sum(np.abs(fresh) ** 2)   # (incoherent: no phase cancellation)
            diffs.append(10 * math.log10(np.sum(np.abs(live - tab) ** 2) / ref + 1e-30))
        out.append(dict(key=n, vel_err_db=max(errs), hammer_err_db=max(herr), restrike_worst=max(diffs), restrike_median=float(np.median(diffs))))
        print(f"  key {n:3}: (a) worst {max(errs):6.1f} dB  (c) HAMMER 0/127 {herr[0]:6.1f} / {herr[1]:6.1f} dB  (b) worst {max(diffs):6.1f} dB, median {np.median(diffs):6.1f} dB")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("modes", "contact", "all"))
    ap.add_argument("--type", default="both", choices=("vibes", "marimba", "both"))
    ap.add_argument("--keys", default=None)
    ap.add_argument("--v", default="40,100,127")
    ap.add_argument("--json", default=None, help="write the rows here")
    a = ap.parse_args()
    rows = {}
    for t in (("vibes", "marimba") if a.type == "both" else (a.type,)):
        with the_type(t) as vb:
            lo, hi = vb["keys"]["authentic"]
            keys = [int(x) for x in a.keys.split(",")] if a.keys else sorted({lo, lo + 7, (lo + hi) // 2, hi - 7, hi})
            keys = [k for k in keys if lo <= k <= hi]
            if a.what in ("modes", "all"):
                rows[t + "_modes"] = study_modes(vb, keys, [int(x) for x in a.v.split(",")], list(range(lo, hi + 1)))
            if a.what in ("contact", "all"):
                rows[t + "_contact"] = study_contact(vb, keys)
    if a.json:
        json.dump(rows, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
