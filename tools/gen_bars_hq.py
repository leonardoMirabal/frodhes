#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The HQ bars' extra modes (the Move only, FR_HQ; private plan 07 item B2) -> firmware/gen/hq/bars_hq_tables.h

VIBES and MARIMBA on the Move run five more slots per voice (firmware/src/hq/bars_hq.c): bending V5 V6 V7 and
torsion T3 T4. The Classic tables (tools/gen_bars.py, firmware/gen/bars_tables.h) stay as they are; this reads them
through the committed specs (docs/model/data/bars_spec.json, marimba_spec.json) and the pinned FE profiles
(bars_fe_pins.json, marimba_fe_pins.json), and writes per type and key:

- the words of each extra mode (tools/rh_coefs.py mode_coefs: a mode over 18 kHz is absent): its frequency the FE
  bar's own ratio to V1 (key n's f0 times the geometric key's FE ratio: no tuned or detuned ratio, the bar is not
  tuned there); its held decay the type's law for a mode outside T2's table (VIBES: T60_1,free (1 / ratio)^q, the
  cap; torsion x T9d's mult as T1 T2; MARIMBA: the Rayleigh law), the felt's decay as the other upper slots
  (min(held, felt_v1 x upper_mult));
- its force weight b at the 17 TONE points (the strike positions of gen_bars.strike_xy): b per unit force is
  gen_bars's bphys law, (F per unit) / fs |phi(x)| (f / f1)^R1 / omega, with the key's own FE weight at each
  point, and the trims of the neighbour slot the voice already has (R1b's radiation and V4's trim for V5..V7 on
  V4, the T9c torsion level on T2): b_new(p) = b_nb(q) |phi_new(p)| f_new^(R1 - 1) / (|phi_nb(q)| f_nb^(R1 - 1)), q
  the neighbour's exact point (pos_exact: there its b is the key's own weight). A key whose neighbour slot is off
  takes the neighbour's trim (re V1) of the nearest key that has it. ESTIMATE: above V4 no measurement covers the
  level (plan 07 section 2.1); listening settles it;
- the prune rule (vibes.json / marimba.json "prune"): a slot whose onset (5 ms, the pulse's spectrum at the mode,
  the key's strike weights) stays under -60 dB re V1 at every voicing of the grid (the five families at their
  detents, v127, HAMMER 0 / 64 / 127, TONE 0 / 32 / 64 / 96 / 127, BELL 64) is switched off (b = 0).

    python3 tools/gen_bars_hq.py [--check] [--json OUT]
"""
import argparse
import hashlib
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_fe as fe  # noqa: E402
import bars_hq_study as hs  # noqa: E402
import bars_model as gm  # noqa: E402
import gen_bars as gb  # noqa: E402
import rh_coefs as rc  # noqa: E402

ROOT = gb.ROOT
OUT = os.path.join(ROOT, "firmware/gen/hq/bars_hq_tables.h")
SPECS = {"vibes": os.path.join(ROOT, "docs/model/data/bars_spec.json"),
         "marimba": os.path.join(ROOT, "docs/model/data/marimba_spec.json")}
HQ_DATA = os.path.join(ROOT, "docs/model/data/bars_hq.json")   # the HQ bars' own rows (R1b_hq)
NAMES = ["V5", "V6", "V7", "T3", "T4"]
NEIGH = {"V5": (4, 3, 2), "V6": (4, 3, 2), "V7": (4, 3, 2), "T3": (6, 5), "T4": (6, 5)}   # the anchor slots, in order
WCOL = {0: 0, 2: 1, 3: 2, 4: 3, 5: 7, 6: 8}            # slot -> column of the 11-weight list (V1..V7, T1..T4)
NEWCOL = {"V5": 4, "V6": 5, "V7": 6, "T3": 9, "T4": 10}
Q14 = 1 << 14
GRID_TONE = (0, 32, 64, 96, 127)
GRID_HAMMER = (0, 64, 127)
QDEF = 2                                               # the DEFAULT family (T7c; the R1b lines' mallet, at v100)


def key_fe(vb, fec, ng):
    """per geometric key: the FE bar's bending (7) and torsion (4) ratios to its V1, the weights at the 17 points
    (V1..V7, T1..T4)"""
    bar, q = hs.fe_bar(vb, fec, ng)
    fb, _ = bar.bending(7)
    ft, _ = bar.torsion(4)
    nodes = bar.v1_nodes()
    W = []
    for i in range(gb.NPOS):
        x, y = gb.strike_xy(vb, gm.pos_point(i, gb.NPOS), q["L"], q["w"], nodes)
        W.append(fe.strike_weights(bar, x, y, 7, 4))
    W = np.array(W)
    old = np.array(fec[ng]["weights"])                  # gen_bars's own (V1..V4, T1, T2): the same FE
    assert np.allclose(W[:, [0, 1, 2, 3, 7, 8]], old, rtol=1e-6, atol=1e-6), ng
    return dict(rb=[f / fb[0] for f in fb], rt=[f / fb[0] for f in ft], W=W)


def pulse_at(spec, key, q, v, hammer):
    """the table pulse of family q at velocity v, HAMMER raw hammer, as bars.c bar_load_pulse plays it: the force per
    sample (shape units)"""
    k = spec["keys"][key]
    sh = spec["pulse"][k["band"]][q]
    l, w = gm.vlayer(v)
    l1 = min(l + 1, gb.NLAYER - 1)
    s = [a + (((b - a) * w) >> 15) for a, b in zip(sh[l], sh[l1])]
    vi, vf = gm.vpos(v)
    pr = spec["prate"][key][q]
    p = pr[vi] + (((pr[vi + 1] - pr[vi]) * vf) >> 8)
    step = (p * 16 * spec["kn_mass"][hammer] >> 12) / 65536.0       # points a sample (bars.c: Q16 x mass)
    t = np.arange(0.0, gb.PLEN, step)
    return np.interp(t, np.arange(gb.PLEN + 2), np.array(s[:gb.PLEN + 2], float))


def b_at(tab, tone):
    """b at TONE raw tone from a 17-point row (bars.c's interpolation)"""
    qq = tone * 16 * 256 // 127
    i, f = qq >> 8, qq & 255
    if i >= gb.NPOS - 1:
        return tab[-1]
    if isinstance(tab[i], int) and isinstance(tab[i + 1], int):
        return tab[i] + ((tab[i + 1] - tab[i]) * f >> 8)       # (bars.c's integers: the 64-bit product, >> 8)
    return tab[i] + (tab[i + 1] - tab[i]) * f / 256.0


def build_type(name):
    with hs.the_type(name) as vb:
        spec = json.load(open(SPECS[name]))
        fec = gb.fe_all(vb)
        mar = gb.is_mar(vb)
        lo, hi = vb["keys"]["authentic"]
        rexp = vb["R1"]["exp"]
        up = vb["T3m"]["upper_mult"] if mar else vb["T3"]["upper_mult"]
        prune_db = vb["prune"]["db"]
        tors_db = spec["type"]["tors_db"]
        hqd = json.load(open(HQ_DATA)).get(name, {})
        r1b = hqd["R1b_hq"]["level_db"] if "R1b_hq" in hqd else None   # MARIMBA: the extra bending modes' lines
        r1b_floor = vb["R1b"]["trim_db"][0] if r1b else 0.0
        fes = {ng: key_fe(vb, fec, ng) for ng in range(lo, hi + 1)}
        keys, trims = [], {}
        for i, n in enumerate(range(gb.KEY_LO, gb.KEY_HI + 1)):
            k = spec["keys"][i]
            ng = min(max(n, lo), hi)
            F = fes[ng]
            f0 = gb.midi_hz(n)
            band = k["band"]
            pos = spec["bar_pos"][band]
            pex = spec["pos_exact"][band]
            fv1 = gb.felt_v1(vb, n)

            def scale(slot):                           # the slot's trimmed b per unit (|phi| f^(R1 - 1)) at its exact point
                md = k["modes"][slot]
                q = pex[slot]
                br = md["b"] * pos[q][slot] / Q14
                w = F["W"][q, WCOL[slot]]
                f = md["f"] if slot else f0
                return br / (w * f ** (rexp - 1)) if br and w > 0 and f > 0 else 0.0
            s1 = scale(0)
            row = dict(n=n, modes=[])
            for nm in NAMES:
                tor = nm[0] == "T"
                ratio = F["rt"][int(nm[1]) - 1] if tor else F["rb"][int(nm[1]) - 1]
                f = f0 * ratio
                t60 = gb.law_t60(vb, n, ratio) * (vb["T9d"]["mult"] if tor else 1.0)
                words = rc.mode_coefs(f, t60, min(t60, fv1 * up))
                anchor, sc = None, 0.0
                for a in NEIGH[nm]:
                    sc = scale(a)
                    if sc:
                        anchor = a
                        break
                row["modes"].append(dict(name=nm, f=f, t60=t60, words=words, anchor=anchor,
                                         trim=(sc / s1 if sc and s1 else None), w=F["W"][:, NEWCOL[nm]].tolist()))
            row["s1"] = s1
            keys.append(row)
        # a key whose anchor is off: the nearest key's trim re V1
        for j, nm in enumerate(NAMES):
            have = [r["n"] for r in keys if r["modes"][j]["trim"]]
            for r in keys:
                md = r["modes"][j]
                if md["trim"] is None and have:
                    near = min(have, key=lambda m: (abs(m - r["n"]), m))
                    md["trim"], md["borrowed"] = keys[near - gb.KEY_LO]["modes"][j]["trim"], near
        # b at the 17 points; then over the voicing grid the ceiling (never louder at its onset than the slot under
        # it: V4 for V5..V7, T2 for T3 T4) and the prune rule
        LN10 = math.log(10.0)
        for i, r in enumerate(keys):
            k = spec["keys"][i]
            pos = spec["bar_pos"][k["band"]]
            th1 = 2 * math.pi * k["modes"][0]["f_slot"] / gb.FS
            sg1 = math.log(1000.0) / k["modes"][0]["t60"]
            b1 = [k["modes"][0]["b"] * pos[p][0] / Q14 for p in range(gb.NPOS)]
            pulses = [(q, h, pulse_at(spec, i, q, 127, h)) for q in range(gb.NFAM) for h in GRID_HAMMER]
            pdef = pulse_at(spec, i, QDEF, 100, 64) if r1b else None

            def spec_at(P, th):
                return abs(np.sum(P * np.exp(-1j * th * np.arange(len(P))))) / math.cos(th / 2)

            def levels(b17, f, t60, tor, pls=None):
                """{(q, h, tone): onset dB re V1} where the slot and V1 are struck (pls: those pulses, else the grid's)"""
                out = {}
                if not any(b17) or f <= 0 or f > 18000:
                    return out
                th, sg = 2 * math.pi * f / gb.FS, math.log(1000.0) / t60
                for q, h, P in (pls or pulses):
                    S1, Sm = spec_at(P, th1), spec_at(P, th)
                    for tone in GRID_TONE:
                        bm = b_at(b17, tone)
                        if tor:
                            bm = bm * tors_db / 32768.0 if tone > 64 else 0.0
                        a1 = abs(b_at(b1, tone)) * S1
                        if bm and a1:
                            out[q, h, tone] = 20 * math.log10(abs(bm) * Sm / a1) - (sg - sg1) * 0.005 * 20 / LN10
                return out
            ceil = {}
            for slot in (4, 3, 2, 6, 5):
                md = k["modes"][slot]
                ceil[slot] = levels([md["b"] * pos[p][slot] / Q14 for p in range(gb.NPOS)], md["f"], md["t60"], slot >= 5)
            for md in r["modes"]:
                w = md["words"]
                md["level_db"] = md["ceiling_db"] = None
                if not w["th"] or not md["trim"] or not r["s1"]:
                    md["b"] = [0] * gb.NPOS
                    continue
                tor = md["name"][0] == "T"
                line = r1b.get(md["name"]) if r1b else None
                if line is not None:                   # MARIMBA (R1b_hq): the bare R1 law, then down to the line
                    md["trim"] = 1.0
                bf = [md["trim"] * r["s1"] * md["w"][p] * md["f"] ** (rexp - 1) for p in range(gb.NPOS)]
                if line is not None:
                    xs, ys = gb.table(line)
                    want = float(np.interp(r["n"], xs, ys))
                    have = max(levels(bf, md["f"], md["t60"], tor, [(QDEF, 64, pdef)]).values(), default=None)
                    md["def_db"], md["line_db"] = have, want
                    if have is not None and have > want:   # attenuation only, at most R1b's trim_db floor
                        a = max(want - have, r1b_floor)
                        bf = [x * 10 ** (a / 20) for x in bf]
                        md["r1b_db"] = a
                L = levels(bf, md["f"], md["t60"], tor)
                cs = next((c for c in NEIGH[md["name"]] if ceil[c]), None)
                if cs is not None:
                    over = max((L[x] - ceil[cs][x] for x in L if x in ceil[cs]), default=0.0)
                    if over > 0.0:
                        bf = [x * 10 ** (-over / 20) for x in bf]
                        L = {x: y - over for x, y in L.items()}
                        md["ceiling_db"] = -over
                md["b"] = [int(round(max(-2 ** 31 + 1, min(2 ** 31 - 1, x)))) for x in bf]
                md["level_db"] = max(L.values()) if L else None
                if md["level_db"] is None or md["level_db"] < prune_db:
                    md["b"] = [0] * gb.NPOS
                    md["pruned"] = True
        return keys


def f32(x):
    return repr(float(np.float32(x))) + "f"


def emit(types):
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* GENERATED by tools/gen_bars_hq.py from docs/model/data (the bar specs, the FE pins) - do not edit. The HQ",
         " * bars' extra slots (FR_HQ, the Move): V5 V6 V7 T3 T4 per type and key, their words and their b at the 17",
         " * TONE points (firmware/src/hq/bars_hq.c hqb_init) */",
         "_Static_assert(HQB_NX == 5u, \"bars_hq_tables.h: V5 V6 V7 T3 T4\");",
         "typedef struct {",
         "    rh_mode_t m[HQB_NX];                 /* V5 V6 V7 T3 T4 (th 0: absent; b unused) */",
         "    int32_t b[HQB_NX][BAR_NPOS];         /* the force weight at the 17 TONE points (0: pruned or absent) */",
         "} hqb_key_t;",
         "static const hqb_key_t HQB_KEYS[BAR_NTYPE][BAR_NKEY] = {"]
    for name, keys in types:
        L.append("  {   /* %s */" % name.upper())
        for r in keys:
            ws = ",".join("{%d,%d,%d,%d,%d,%d,0}" % tuple(md["words"][x] for x in ("sh", "ch", "th", "d", "drel", "cr"))
                          for md in r["modes"])
            bs = ",".join("{" + ",".join(str(x) for x in md["b"]) + "}" for md in r["modes"])
            L.append("    {{%s},{%s}},   /* %d */" % (ws, bs, r["n"]))
        L.append("  },")
    L.append("};")
    body = "\n".join(L) + "\n"
    return body + f"/* sha256(body) = {hashlib.sha256(body.encode()).hexdigest()} */\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", default=None, help="write the per-key design (levels, anchors, prune) here")
    a = ap.parse_args()
    types = [(t, build_type(t)) for t in ("vibes", "marimba")]
    for name, keys in types:
        for j, nm in enumerate(NAMES):
            live = [r["n"] for r in keys if any(r["modes"][j]["b"])]
            lv = [r["modes"][j]["level_db"] for r in keys if any(r["modes"][j]["b"])]
            print("%s %s: %d keys lit%s" % (name, nm, len(live), (", keys %d..%d, onset %.0f..%.0f dB re V1 (the grid's loudest)"
                                                                   % (live[0], live[-1], min(lv), max(lv))) if live else ""),
                  file=sys.stderr)
    hdr = emit(types)
    if a.json:
        json.dump({n: k for n, k in types}, open(a.json, "w"), indent=0, default=float)
    if a.check:
        old = open(OUT).read() if os.path.exists(OUT) else ""
        print("equal" if old == hdr else "DIFFERS")
        return 0 if old == hdr else 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(hdr)
    print(f"wrote {OUT} ({len(hdr)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
