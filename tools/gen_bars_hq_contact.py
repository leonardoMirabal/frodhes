#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The HQ bars' mallet contact data (the Move only, FR_HQ; private plan 07 items B3, B4) -> firmware/gen/hq/bars_hq_contact.h

A strike of a ringing bar (and every strike on MARIMBA's top keys) runs the mallet-bar contact from the bar's state
(firmware/src/hq/bars_contact.c) instead of the table pulse. Per type and key this writes the contact's description,
the one tools/gen_bars.py's tables come from (tools/hammer.py: the mallet's mass at HAMMER 64, each family's Hertz
law(s), the bar's modes at TONE 64's strike point), extended to V1 .. V7 (the HQ voice's bending slots:
tools/gen_bars_hq.py), and what the voice needs around it:

- st[13][8][8]: per time step 1 / (fs os), os = 0.5 .. 32 in half octaves, per mode the exact step of the damped mode
  under a force linear across the step (the matrix exponential, as tools/gen_rhodes_hq_contact.py);
- gq[8]: the voice's slot state (Q25 x) -> the mode's coordinate (m sqrt(kg)): x_real = S T (f / f1)^R1 q / cos(th / 2)
  with bphys's law (tools/gen_bars.py: b per unit force = (F per unit) / fs phi (f / f1)^R1 / omega), so
  q = x cos(th / 2) phi lambda 2^20 / (fs omega b64), b64 the slot's b at TONE 64 (where phi is taken), lambda the
  key's newtons per force unit (fref / pamp_max, gen_bars's own);
- per law the window's multiple (gen_bars.contact_tables' ratio, from the tables' RK4 contacts at the anchors) and
  the rigid-wall contact time per velocity (the step's scale);
- HQB_CK_FPK: the at-rest contact's force scale at every velocity, from bars_contact.c itself (tests/hq/
  bars_contact_gen.c, compiled and run here): the live pulse's force against it, equal at rest by construction.

    python3 tools/gen_bars_hq_contact.py [--check]
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import near_header  # noqa: E402
import bars_hq_study as hs  # noqa: E402
import gen_bars as gb  # noqa: E402
import gen_bars_hq as gq_  # noqa: E402
import gen_rhodes_hq_contact as grc  # noqa: E402
import hammer as hm  # noqa: E402

ROOT = gb.ROOT
OUT = os.path.join(ROOT, "firmware/gen/hq/bars_hq_contact.h")
GEN_C = os.path.join(ROOT, "tests/hq/bars_contact_gen.c")
OSS = tuple(2.0 ** (k / 2.0) for k in range(-2, 11))     # 0.5 .. 32, half octaves
NV = 8
NLAW = 6
REST_LO = {"vibes": 128, "marimba": 84}                    # B4: every strike from this key up (MARIMBA C6 .. C8)
LN1000 = math.log(1000.0)
FV = tuple(range(1, 127, 8)) + (127,)                     # HQB_CK_FPK's velocities


def laws_of(vb):
    """the played families' contact laws, flattened: [law], [(i_lo, i_hi or 255, a)] per family"""
    laws, fam = [], []
    for fm in vb["T7c"]["families"]:
        ix = []
        for law in gb.fam_laws(fm):
            laws.append(dict(K=gb.law_K(vb, law), p=law["p"]))
            ix.append(len(laws) - 1)
        fam.append((ix[0], ix[1] if len(ix) > 1 else 255, fm["blend"]["a"] if "blend" in fm else 0))
    assert len(laws) <= NLAW
    return laws, fam


def table_contacts(vb, spec, n, i, fec):
    """the tables' own at-rest contacts of key n (gen_bars.contacts: V1..V4 at TONE 64's point, HAMMER 64), every law
    of the played families + CAL at the anchors: (per law the window ratio, fref = gen_bars's max family fpk)"""
    k = spec["keys"][i]
    d = spec["design"][i]
    W = fec[d["ng"]]["weights"][8]
    fs_ = [d["f0"]] + [md["f"] for md in d["modes"][:3]]
    ts = [d["t1a"]] + [md["t60"] for md in d["modes"][:3]]
    modes = [(f, LN1000 / t, W[j]) for j, (f, t) in enumerate(zip(fs_, ts))]
    m0 = gb.mass_kg(vb, 64)
    fams = gb.fams_int(vb)
    cases, idx = [], []
    for q, fm in enumerate(fams):
        for law in gb.fam_laws(fm):
            for v in gb.V_ANCH:
                cases.append(dict(v=gb.speed(vb, v), m_h=m0, K=gb.law_K(vb, law), p=law["p"], modes=modes))
            idx.append(q)
    return cases, idx, d["f0"]


def contact_tables_stats(vb, sims, f0):
    """gen_bars.contact_tables' ratio and fpk (per anchor) of one law from its anchor sims"""
    seg = {v: gb.contact_segments(sims[v]["F"]) for v in gb.V_ANCH}
    ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg.values()))
    dt = sims[gb.V_ANCH[0]]["dt"]
    cap_ = vb["T7f"]["f_tau_max"] / (f0 * dt)
    win = {v: ratio * seg[v][0][1] for v in gb.V_ANCH}
    t8, xv = vb["T8"], vb.get("T7g", {}).get("vel_exp", 0.0)
    spd = lambda v: t8["v_lo"] * (t8["v_hi"] / t8["v_lo"]) ** ((v - 1) / 126.0)   # noqa: E731
    play0 = {v: min(win[v], cap_) for v in gb.V_ANCH}
    play = {v: min(win[v] * (spd(v) / spd(100)) ** (-xv), cap_) for v in gb.V_ANCH}
    fpk = [float(sims[v]["F"].max()) * (play0[v] / play[v]) ** 0.5 for v in gb.V_ANCH]
    return ratio, fpk


def build_type(name):
    design = gq_.build_type(name)                           # the extra modes (f, t60, words, b)
    with hs.the_type(name) as vb:
        spec = json.load(open(gq_.SPECS[name]))
        fec = gb.fe_all(vb)
        lo, hi = vb["keys"]["authentic"]
        laws, fam = laws_of(vb)
        fams = gb.fams_int(vb)
        nlaw_int = sum(len(gb.fam_laws(fm)) for fm in fams)
        fes = {ng: gq_.key_fe(vb, fec, ng) for ng in range(lo, hi + 1)}
        pamp_max = math.floor(32767 * 4096 / max(spec["kn_massi"]))
        all_cases, meta = [], []
        for i, n in enumerate(range(gb.KEY_LO, gb.KEY_HI + 1)):
            cs, idx, f0 = table_contacts(vb, spec, n, i, fec)
            all_cases += cs
            meta.append((idx, f0))
        print(f"{name}: {len(all_cases)} RK4 contacts (the tables' own, at the anchors)", file=sys.stderr)
        res = []
        for c0 in range(0, len(all_cases), 1500):
            res += hm.contact_batch(all_cases[c0:c0 + 1500], fs=gb.FS, t_max=0.012)
        keys = []
        na = len(gb.V_ANCH)
        for i, n in enumerate(range(gb.KEY_LO, gb.KEY_HI + 1)):
            idx, f0 = meta[i]
            r = res[i * nlaw_int * na:(i + 1) * nlaw_int * na]
            per = [contact_tables_stats(vb, dict(zip(gb.V_ANCH, r[j * na:(j + 1) * na])), f0) for j in range(nlaw_int)]
            fpk_f = {}
            j = 0
            for q, fm in enumerate(fams):                   # the family fpk (a blend family: its laws blended)
                ls = gb.fam_laws(fm)
                if len(ls) == 1:
                    fpk_f[q] = per[j][1]
                else:
                    a = fm["blend"]["a"]
                    fpk_f[q] = [x + (y - x) * a / 256.0 for x, y in zip(per[j][1], per[j + 1][1])]
                j += len(ls)
            fref = max(max(v) for v in fpk_f.values())
            pp, pa = np.array(spec["pamp_phys"][i]), np.array(spec["pamp"][i])
            assert abs(pp.max() / pamp_max - 1) < 1e-6, (name, n, pp.max(), pamp_max)   # (gen_bars's own fref)
            # N per force unit as played: bphys's fref / pamp_max, times what the velocity fit and the range scale
            # (S) did to pamp (b took the inverse): the geometric mean over the families and anchors
            lam = fref / pamp_max * float(np.exp(np.mean(np.log(pp / pa))))
            ratios = [per[j][0] for j in range(nlaw_int)][:len(laws)]   # (the played laws first, CAL last)
            k = spec["keys"][i]
            d = spec["design"][i]
            Wk = fes[d["ng"]]["W"][8]                            # TONE 64's point: V1..V7, T1..T4
            pos = spec["bar_pos"][k["band"]]
            ex = design[i]["modes"]
            lanes = []
            for s, nm in enumerate(("V1", "V2", "V3", "V4", "V5", "V6", "V7")):
                if s == 0:
                    f, t60, b64 = d["f0"], d["t1a"], k["modes"][0]["b"] * pos[8][0] / gq_.Q14
                elif s < 4:
                    md = d["modes"][s - 1]
                    f, t60, b64 = md["f"], md["t60"], k["modes"][s + 1]["b"] * pos[8][s + 1] / gq_.Q14
                else:
                    md = ex[s - 4]
                    f, t60, b64 = md["f"], md["t60"], gq_.b_at(md["b"], 64)
                if not (0 < f <= 18000.0):
                    lanes.append(None)
                    continue
                th = 2 * math.pi * f / gb.FS
                w = 2 * math.pi * f
                phi = float(Wk[s])
                g = math.cos(th / 2) * phi * lam * 2 ** 20 / (gb.FS * w * b64) if b64 else 0.0
                lanes.append(dict(f=f, sg=LN1000 / t60, phi=phi, gq=g, th=th))
            ftop = max(l["f"] for l in lanes if l)
            st = []
            for o in OSS:
                rows = [[0.0] * NV for _ in range(8)]
                for s, l in enumerate(lanes):
                    if l:
                        ms = grc.mode_step(l["f"], l["sg"], l["phi"], 1.0 / (gb.FS * o))
                        for c in range(8):
                            rows[c][s] = ms[c]
                st.append(rows)
            keys.append(dict(n=n, st=st, lanes=lanes, mh=gb.mass_kg(vb, 64), f0=d["f0"], ftop=ftop, lam=lam,
                             ratio=ratios + [1.0] * (NLAW - len(ratios))))
        m64 = gb.mass_kg(vb, 64)
        t8, xv = vb["T8"], vb.get("T7g", {}).get("vel_exp", 0.0)
        tc = [[hm.hertz_tc(gb.speed(vb, v), m64, L["K"], L["p"]) for v in range(1, 128)] for L in laws]
        tc += [[1e-3] * 127] * (NLAW - len(laws))
        ty = dict(laws=laws, fam=fam, vh=[gb.speed(vb, v) for v in range(1, 128)],
                  vx=[(gb.speed(vb, v) / gb.speed(vb, 100)) ** (-xv) for v in range(1, 128)], tc=tc,
                  ftau=vb["T7f"]["f_tau_max"], rest_lo=REST_LO[name])
        return keys, ty


f32 = grc.f32


def emit(types, fpk=None):
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* GENERATED by tools/gen_bars_hq_contact.py from docs/model/data (the bar specs, the FE pins) - do not edit.",
         " * The HQ bars' mallet contact per type and key (FR_HQ, the Move: firmware/src/hq/bars_contact.c) */",
         f"#define HQB_CK_NV {NV}u                    /* V1 .. V7 and a spare lane */",
         f"#define HQB_CK_NOS {len(OSS)}u",
         f"#define HQB_CK_NLAW {NLAW}u",
         "static const float HQB_CK_OS[HQB_CK_NOS] = {" + ",".join(f32(o) for o in OSS) + "};   /* dt = 1 / (fs os) */",
         "typedef struct {",
         "    float st[HQB_CK_NOS][8][HQB_CK_NV];    /* per os, per coefficient, per mode: q1 = a q + b qd + g0 F0 + g1 F1,",
         "                                         * qd1 = c q + d qd + h0 F0 + h1 F1 (a b g0 g1 c d h0 h1) */",
         "    float w2[HQB_CK_NV], pc[HQB_CK_NV];    /* omega^2, phi at the strike point (0: no such mode) */",
         "    float gq[HQB_CK_NV];                   /* the slot's x (Q25) -> the mode's coordinate */",
         "    float cth[HQB_CK_NV], sth[HQB_CK_NV], th[HQB_CK_NV];   /* cos, sin of the mode's step angle, the angle */",
         "    float mh, f0, ftop;                    /* mallet mass (HAMMER 64, kg), the key's f0, its top mode (Hz) */",
         "    float ratio[HQB_CK_NLAW];              /* per law: the window's multiple of the first contact */",
         "} hqb_ck_t;"]
    for nm, ar, fmt in (("HQB_CK_LK", "float", lambda ty: [f32(L["K"]) for L in ty["laws"]] + ["0.0f"] * (NLAW - len(ty["laws"]))),
                        ("HQB_CK_LP2", "uint8_t", lambda ty: [str(int(L["p"] == 2.0)) for L in ty["laws"]] + ["1"] * (NLAW - len(ty["laws"])))):
        L.append(f"static const {ar} {nm}[BAR_NTYPE][HQB_CK_NLAW] = {{" + ",".join("{" + ",".join(fmt(ty)) + "}" for _, (_, ty) in types) + "};")
    for _, (_, ty) in types:
        assert all(L_["p"] in (1.5, 2.0) for L_ in ty["laws"])
    L.append("static const uint8_t HQB_CK_FLAW[BAR_NTYPE][BAR_NFAM][3] = {   /* per family: its law, the blend's second (255: "
             "none), the blend's a (Q8) */")
    L.append("  " + ",".join("{" + ",".join("{%d,%d,%d}" % f for f in ty["fam"]) + "}" for _, (_, ty) in types) + "};")
    L.append("static const uint8_t HQB_CK_REST_LO[BAR_NTYPE] = {" + ",".join(str(ty["rest_lo"]) for _, (_, ty) in types)
             + "};   /* every strike from this key up (B4) */")
    L.append("static const float HQB_CK_FTAU[BAR_NTYPE] = {" + ",".join(f32(ty["ftau"]) for _, (_, ty) in types) + "};   /* T7f */")
    for nm, key in (("HQB_CK_VH", "vh"), ("HQB_CK_VX", "vx")):
        L.append(f"static const float {nm}[BAR_NTYPE][127] = {{   /* v = index + 1: {'the mallet speed (m/s)' if key == 'vh' else 'T7g: (V / V(v100))^-vel_exp'} */")
        L.append(",\n".join("  {" + ",".join(f32(x) for x in ty[key]) + "}" for _, (_, ty) in types) + "};")
    L.append("static const float HQB_CK_TC[BAR_NTYPE][HQB_CK_NLAW][127] = {   /* the rigid-wall contact time (s) at HAMMER 64 */")
    L.append(",\n".join("  {" + ",".join("{" + ",".join(f32(x) for x in r) + "}" for r in ty["tc"]) + "}" for _, (_, ty) in types) + "};")
    L.append("static const hqb_ck_t HQB_CK[BAR_NTYPE][BAR_NKEY] = {")
    for name, (keys, _) in types:
        L.append("  {   /* %s */" % name.upper())
        for k in keys:
            ln = k["lanes"] + [None] * (NV - len(k["lanes"]))
            z = lambda fn: "{" + ",".join(f32(fn(l)) if l else "0.0f" for l in ln) + "}"   # noqa: E731
            L.append("   {{" + ",".join("{" + ",".join("{" + ",".join(f32(x) for x in row) + "}" for row in so) + "}"
                                        for so in k["st"]) + "},")
            L.append("    " + ",".join([z(lambda l: (2 * math.pi * l["f"]) ** 2), z(lambda l: l["phi"]), z(lambda l: l["gq"]),
                                        z(lambda l: math.cos(l["th"])), z(lambda l: math.sin(l["th"])), z(lambda l: l["th"])])
                     + f",{f32(k['mh'])},{f32(k['f0'])},{f32(k['ftop'])},{{" + ",".join(f32(x) for x in k["ratio"]) + f"}}}},   /* {k['n']} */")
        L.append("  },")
    L.append("};")
    L.append(f"#define HQB_CK_NFV {len(FV)}u")
    L.append("static const uint8_t HQB_CK_FV[HQB_CK_NFV] = {" + ",".join(map(str, FV)) + "};   /* the velocities of HQB_CK_FPK */")
    L.append("static const float HQB_CK_FPK[BAR_NTYPE][BAR_NKEY][HQB_CK_NLAW][HQB_CK_NFV] = {   /* N: the at-rest contact's "
             "force scale (bars_contact.c's own; linear between: <= 0.2 dB) */")
    if fpk is None:
        L.append("  {{{0}}}};   /* (the generator's first pass: tests/hq/bars_contact_gen.c fills it) */")
    else:
        L.append(",\n".join("  {" + ",\n".join("   {" + ",".join("{" + ",".join(f32(fpk[t][k][l][v - 1]) for v in FV) + "}"
                                                              for l in range(NLAW)) + "}" for k in range(88)) + "}"
                            for t in range(len(types))) + "};")
    body = "\n".join(L) + "\n"
    return body + f"/* sha256(body) = {hashlib.sha256(body.encode()).hexdigest()} */\n"


def run_fpk(hdr0):
    """the first pass's header -> tests/hq/bars_contact_gen.c -> the at-rest force scale [type][key][law][v]"""
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "build")) as d:
        os.makedirs(os.path.join(d, "hq"))
        open(os.path.join(d, "hq", "bars_hq_contact.h"), "w").write(hdr0)
        exe = os.path.join(d, "gen")
        subprocess.run([os.environ.get("CC", "cc"), "-O2", "-ffp-contract=off", "-I" + d, "-Ifirmware/src", "-o", exe, GEN_C, "-lm"],
                       cwd=ROOT, check=True)
        out = subprocess.run([exe], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    vals = np.array([float(x) for x in out]).reshape(2, 88, NLAW, 127)
    return vals.tolist()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rel", type=float, default=0.0, help="with --check: near is enough (tools/near_header.py: "
                    "each number within REL of its size; another machine than the committed header's)")
    a = ap.parse_args()
    types = [(t, build_type(t)) for t in ("vibes", "marimba")]
    hdr0 = emit(types)
    hdr = emit(types, run_fpk(hdr0))
    if a.check:
        old = open(OUT).read() if os.path.exists(OUT) else ""
        verdict, rc = near_header.check(old, hdr, a.rel)
        print(verdict)
        return rc
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(hdr)
    print(f"wrote {OUT} ({len(hdr)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
