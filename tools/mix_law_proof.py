#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Is a preset's pristine-ceiling move under the MIX law (0.9.13 E2) only the new wet / dry balance?

The MIX law changed the gains at a given MIX value, not the effects: a preset whose delay / reverb MIX now gives more
wet against the dry reads more reverb / echo energy in the harmonic bands (H2..H6), off them (inh, imd), after the
key-up. Per preset case this tool measures the candidate (tools/release_diff.py's cells) twice: at the preset's own
MIX values, and with its delay and reverb MIX moved to the values that give the last release's wet / dry ratio under
the new law (MIX = round(old ratio x 64), the ratio <= 1 on every preset: the law's lower half, dry whole). A ceiling
cell broken at the preset's MIX that passes the ceiling at the equivalent MIX is the balance alone, not distortion.
A cell the balance does not explain is measured once more with the preset's delay and reverb at MIX 0 (the same in both
laws: the effects off), in the candidate and in the last release: where both read the candidate's value (within
0.3 dB), the harmonic is the engine's own, which the last release's delay dry gain (1 - MIX^2 / 32768, truncated)
masked at the int16 grain; the new law passes the dry whole, untouched. Any other cell is a defect.

  tools/mix_law_proof.py [PRESET ...] [--out tests/mix_law_proof.json] [--entries FILE]
  (default presets: DREAM RESO_SWEEP; --entries writes the exact-cell expected-changes entries for the proven cells)

A cell neither rule explains may still be proven outside the MIX law, by the proof its own exact expected-changes entry
names (level_proven below; 2026-11-15, the RESO_SWEEP soft-note `inh` cells, which moved with RESPONSE FULL's curve D
before the MIX law's record was taken): the level proof (tests/level_proof.json lists the cell proven, its verdict
PASS), or the two-core reverb-level proof chained to it (tests/rev_level_proof.json's `chained` record: the entry's
`chain` is the level proof, its `chain_value` a number, the old-level build read that value within CHAIN_TOL, both
records PASS). The records are the ones tests/py/test_level_proof.py and test_rev_level_proof.py re-measure on every
make pytest; this tool reads them, it proves nothing new. A broken chain or a missing anchor leaves the cell unproven."""
import argparse
import json
import math
import os
import re
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_diff as rd  # noqa: E402

ROOT = rd.ROOT
CC_DLY, CC_RV = 89, 91
LEVEL_PROOF, REV_PROOF = "tests/level_proof.json", "tests/rev_level_proof.json"
CHAIN_TOL = 0.1                    # tools/rev_level_proof.py's (the old-level build reads the chain's value again)


def record(rel):
    """a proof's committed record, or None"""
    try:
        return json.load(open(os.path.join(ROOT, rel)))
    except (OSError, ValueError):
        return None


def level_proven(cell, metric, move, expected, lp, rp):
    """the proof outside the MIX law of one ceiling cell ("<case id>/<label>"), or None: an exact entry for the cell and
    metric, the move inside its min / max, naming the level proof (lp lists the cell proven) or the reverb-level proof
    chained to the level proof (rp's chained record, its anchor and value intact); lp / rp: the records (record())"""
    ok_lp = isinstance(lp, dict) and lp.get("verdict") == "PASS"
    ok_rp = isinstance(rp, dict) and rp.get("verdict") == "PASS"
    var = cell.split("/")[3] if cell.count("/") >= 4 else ""
    for e in expected:
        if str(e.get("match", "")).replace("[AB]", var) != cell or str(e.get("metric", "")) != metric:
            continue
        lo, hi = e.get("min"), e.get("max")
        if (lo is not None and move < lo) or (hi is not None and move > hi):
            continue
        if e.get("ceiling_proof") == LEVEL_PROOF and ok_lp:
            for r in lp.get("rows", []):
                if r.get("cell") == cell and r.get("metric") == metric and r.get("proven"):
                    return f"level proof ({r['proven']})"
        cv = e.get("chain_value")
        if e.get("ceiling_proof") == REV_PROOF and e.get("chain") == LEVEL_PROOF and ok_rp and ok_lp \
                and isinstance(cv, (int, float)) and not isinstance(cv, bool):
            for r in rp.get("chained", []):
                if r.get("cell") == cell and r.get("metric") == metric and r.get("chain") == LEVEL_PROOF \
                        and r.get("chain_value") == cv and r.get("proven") and isinstance(r.get("old_level"), float) \
                        and abs(r["old_level"] - cv) <= CHAIN_TOL:
                    return f"reverb-level proof chained to the level proof at {cv}"
    return None


def preset_params(name):
    """{param id name: value} of a factory preset (presets.c PR_FDATA over the defaults, which keep delay off, reverb
    MIX 0)"""
    src = open(os.path.join(ROOT, "firmware", "src", "presets.c")).read()
    nb = src.split("PR_FNAME[", 1)[1].split("{", 1)[1].split("}", 1)[0]
    names = [n.replace(" ", "_") for n in re.findall(r'"([^"]+)"', nb)]
    body = re.sub(r"/\*.*?\*/", "", src.split("static const uint8_t PR_FDATA[] = {", 1)[1].split("};", 1)[0], flags=re.S)
    toks = [t.strip() for t in body.split(",") if t.strip()]
    recs, cur, i = [], {}, 0
    while i < len(toks):
        if toks[i] == "0xFF":
            recs.append(cur)
            cur, i = {}, i + 1
            continue
        cur[toks[i]] = int(toks[i + 1])
        i += 2
    return recs[names.index(name)]


def old_ratio_dly(d):
    """the last release's delay wet / dry at MIX d (wet d x 448 to 64, dry 1 - d^2 / 32768)"""
    w = d * 448 if d <= 64 else 28672 + (d - 64) * 65
    return w / (32768 - d * d)


def old_ratio_rv(v):
    """the last release's reverb send re its full (RV_SEND v x 258 x (254 - v) / 127), the dry whole"""
    return v * 258 * (254 - v) / 127 / 32766


def equivalent(p):
    """[(t, status, cc, value)]: the delay / reverb MIX at the last release's wet / dry ratio under the new law"""
    ev = []
    if p.get("P_DL_SW", 0) and p.get("P_DL_DEPTH", 0):
        ev.append((950, 0xB0, CC_DLY, min(64, round(old_ratio_dly(p["P_DL_DEPTH"]) * 64))))
    if p.get("P_RV_TYPE", 1) and p.get("P_RV_DEPTH", 0):
        ev.append((950, 0xB0, CC_RV, min(64, round(old_ratio_rv(p["P_RV_DEPTH"]) * 64))))
    return ev


def broken(cid, lab, m, v, ms, base):
    """a pristine ceiling broken by this candidate value (release_diff.judge's rule, the ceiling only)"""
    b = base.get(cid, {}).get(lab, {}).get(m)
    if m in rd.INFO or not isinstance(v, float) or not isinstance(b, float):
        return False
    if m[0] == "H" and m[1:].isdigit() and rd.floor_bin(v, ms):
        return False
    c = rd.ceiling(cid, lab, m, v, b, ms)
    return bool(c) and v > c[0] + 1e-9


def _job(a):
    binary, case, tmp = a
    return case[0], rd.measure(binary, case, "full", tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("presets", nargs="*", default=["DREAM", "RESO_SWEEP"])
    ap.add_argument("--out", default=os.path.join("tests", "mix_law_proof.json"))
    ap.add_argument("--entries")
    a = ap.parse_args()
    bins = rd.tree_bins(ROOT, os.path.join(rd.OUT, "mixproof-bin"), c1=True)   # (the full grid: the c1 cases too)
    base = rd.last_cells("full")
    cs = [c for c in rd.cases("full", ROOT) if c[4] == "preset" and c[0].split("/")[1] in a.presets]
    eqs = {c[0]: equivalent(preset_params(c[0].split("/")[1])) for c in cs}
    tmp = os.path.join(rd.OUT, "mixproof")
    off = [(950, 0xB0, CC_DLY, 0), (950, 0xB0, CC_RV, 0)]
    lbins = rd.tree_bins(rd.last_tree(), os.path.join(rd.OUT, "last-bin"), c1=True)
    jobs = [(bins[c[1]], c, tmp + "-own") for c in cs] + \
           [(bins[c[1]], (c[0], c[1], c[2] + eqs[c[0]], c[3], c[4]), tmp + "-eq") for c in cs]
    with Pool(int(os.environ.get("PYTEST_N", "2") or 2)) as pool:
        res = pool.map(_job, jobs, chunksize=1)
        own, eq = dict(res[:len(cs)]), dict(res[len(cs):])
        left = sorted({cid for cid in own for lab, ms in own[cid].items() for m, v in ms.items()
                       if broken(cid, lab, m, v, ms, base) and broken(cid, lab, m, eq[cid].get(lab, {}).get(m), ms, base)})
        byc = {c[0]: c for c in cs}
        r2 = pool.map(_job, [(b[byc[cid][1]], (cid, byc[cid][1], byc[cid][2] + off, byc[cid][3], byc[cid][4]), tmp + t)
                             for cid in left for b, t in ((bins, "-off"), (lbins, "-lastoff"))], chunksize=1)
    offc = {cid: (r2[2 * i][1], r2[2 * i + 1][1]) for i, cid in enumerate(left)}
    expected, lp, rp = rd.load_expected(rd.EXPECTED), record(LEVEL_PROOF), record(REV_PROOF)
    cells, bad = [], 0
    for cid in sorted(own):
        for lab, ms in own[cid].items():
            for m, v in ms.items():
                b = base.get(cid, {}).get(lab, {}).get(m)
                if not broken(cid, lab, m, v, ms, base):
                    continue
                c = rd.ceiling(cid, lab, m, v, b, ms)
                ve = eq[cid].get(lab, {}).get(m)
                row = {"cell": f"{cid}/{lab}", "metric": m, "last": round(b, 2), "cand": round(v, 2),
                       "limit": round(c[0], 2), "at_equivalent_mix": None if ve is None else round(ve, 2),
                       "equivalent_mix": {str(e[2]): e[3] for e in eqs[cid]}}
                ok = isinstance(ve, float) and ve <= c[0] + 1e-9
                if ok:
                    row["proven"] = "balance"
                elif cid in offc:
                    co, lo = (offc[cid][k].get(lab, {}).get(m) for k in (0, 1))
                    row.update({"effects_off_cand": co, "effects_off_last": lo})
                    if isinstance(co, float) and isinstance(lo, float) and abs(co - v) <= 0.3 and abs(lo - v) <= 0.3:
                        ok, row["proven"] = True, "engine's own harmonic (delay / reverb MIX 0, both releases)"
                if not ok:
                    lv = level_proven(f"{cid}/{lab}", m, round(v - b, 2), expected, lp, rp)
                    if lv:
                        ok, row["proven"] = True, lv
                row.setdefault("proven", False)
                bad += not ok
                cells.append(row)
    doc = {"tool": "tools/mix_law_proof.py", "law": "0.9.13 E2 MIX law (fx.c DLY_DRY / RV_SEND / RV_DRY)",
           "verdict": "PASS" if not bad else "FAIL", "broken": len(cells), "unproven": bad, "cells": cells}
    doc["outside_the_law"] = sum(1 for c in cells if str(c["proven"]).startswith(("level proof", "reverb-level proof")))
    json.dump(doc, open(os.path.join(ROOT, a.out), "w"), indent=1)
    print(f"mix law proof: {len(cells)} ceiling cells, {doc['outside_the_law']} proven outside the MIX law (level proof), "
          f"{bad} unproven -> {a.out}: {doc['verdict']}")
    if a.entries:
        import yaml
        ent = [{"match": c["cell"], "metric": c["metric"], "min": math.floor((c["cand"] - c["last"] - 0.3) * 10) / 10,
                "max": math.ceil((c["cand"] - c["last"] + 0.3) * 10) / 10, "ceiling_proof": a.out,
                "reason": ("E2 MIX law: the preset's wet / dry balance (under the ceiling at the last release's ratio, MIX "
                           f"{c['equivalent_mix']}), not distortion" if c["proven"] == "balance" else
                           "E2 MIX law: the engine's own harmonic, as with the delay / reverb off in both releases (the "
                           "last release's truncated delay dry gain masked it at the int16 grain)")} for c in cells
               if c["proven"] and not str(c["proven"]).startswith(("level proof", "reverb-level proof"))]
        yaml.safe_dump(ent, open(a.entries, "w"), sort_keys=False, width=200)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
