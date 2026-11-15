#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Is a pristine-ceiling cell of the release diff a level move (the note quieter), not added distortion?

0.9.13 SND3 put RESPONSE FULL's curve D on the electric piano (a gain after the engine: the same drive, swing and
harmonics) and the bars 3 dB down into the mix; SND2 cleaned the upper register's pickup H2. The release diff's
harmonic and off-harmonic measures are ratios re H1, judged against the last release + 3 dB: a quieter note reads the
fixed int16 floor, the reverb's grain or an unchanged compressor / drive at a lower input level higher re H1. Per cell
this tool re-measures the candidate and the last release (tools/release_diff.py's cells, both built from source; no
stored verdict) and proves the cell by one rule:

  abs    the absolute power is not higher: the harmonic (H re H1 + H1, dBFS) or the off-harmonic power (inh + H1) at
         most the last release's + 0.5 dB;
  level  the last release played at the velocity whose H1 is nearest the candidate's (the same key, case and setup;
         velocities 1 .. the cell's) reads the metric within 1 dB of the candidate's (an unchanged chain at that level);
  raw    a chord's imd with the effects bypassed (CC 119, the limiter out) at most the last release's + 0.5 dB (the
         last release's value was the output limiter acting on the attack: VIBES chord-high, its peaks at LIM_T);
  drive  the harmonic is the DRIVE's own product (DRIVE is the one effect allowed to distort, AGENTS.md section 1):
         with the drive off (CC 84 0) it is under -110 dBFS in both releases, and the cell's total harmonic
         distortion H2..H6 re H1 is lower than the last release's (SND2's cleaner input: FUNK_WAH key 81 H5).

Two rules hold for one named cell each (CELL_RULES; the coordinator's sign-off 2026-11-14, T2 0.9.12):

  floor  c1/FUNK_WAH/PRESET/A/k33v96 H4: the harmonic's absolute power under FLOOR_ABS (-90 dBFS, below the 16-bit
         floor) in both releases, and the one-core twin (fm1, the same cell) under its own ceiling (int16-grain
         content);
  twin   c1/MARIMBA/DECAY0/A/k53v110 clk: the c1 value at most the last release's one-core value of the same cell
         + TWIN_TOL, and the candidate's one-core value within TWIN_TOL (the released one-core build reads the same:
         a 6- against a 3-LSB second-difference ratio).

A cell no rule proves is a defect. The cells: the expected-changes entries naming this file as their ceiling_proof
(default), or --cells FILE (lines "cid label metric").

  tools/level_proof.py [--cells FILE] [--out tests/level_proof.json] [--entries FILE]
(the coordinator's sign-off 2026-11-14; tests/py/test_level_proof.py runs it on every make pytest)"""
import argparse
import json
import math
import os
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_diff as rd  # noqa: E402

ROOT = rd.ROOT
OUT = os.path.join("tests", "level_proof.json")
ABS_TOL, LEVEL_TOL, RAW_TOL, DRIVE_FLOOR = 0.5, 1.0, 0.5, -110.0
FLOOR_ABS, TWIN_TOL = -90.0, 0.05
CELL_RULES = {("c1/FUNK_WAH/PRESET/A/k33v96", "H4"): "floor",          # (one cell each: never a pattern)
              ("c1/MARIMBA/DECAY0/A/k53v110", "clk"): "twin"}
LEVELLED = lambda m: m == "inh" or (m[0] == "H" and m[1:].isdigit())   # (the metrics re H1: abs / level apply)


def floor_ok(abs_cand, abs_last, twin_v, twin_lim):
    """the floor rule: the absolute power under FLOOR_ABS in both releases, the one-core twin under its ceiling"""
    return abs_cand <= FLOOR_ABS and abs_last <= FLOOR_ABS and twin_v <= twin_lim + 1e-9


def twin_ok(v, last_fm1, cand_fm1):
    """the twin rule: at most the last release's one-core value + TWIN_TOL, the candidate's one-core value the same"""
    return v <= last_fm1 + TWIN_TOL + 1e-9 and abs(v - cand_fm1) <= TWIN_TOL + 1e-9


def twin_of(cid):
    return "fm1/" + cid.split("/", 1)[1]
RAW_CC = (131, 0xB0, 119, 127)
DRIVE_OFF = (950, 0xB0, 84, 0)


def _cases(tree):
    return {c[0]: c for c in rd.cases("full", tree)}


def _meas(a):
    binary, case, tag = a
    return case[0], rd.measure(binary, case, "full", os.path.join(rd.OUT, "levelproof-" + tag))


def _harm(binary, case, key, vels, shift, tag):
    """{v: (h1 dBFS, {H2..H6 re H1, inh})} of one key at the velocities vels (one script)"""
    keep = rd.GRID["full"]["vels"]
    rd.GRID["full"]["vels"] = tuple(vels)
    try:
        lines = rd.script("full", case[2], (key,), shift)
        x = rd.render(binary, os.path.join(rd.OUT, "levelproof-" + tag, f"{os.getpid()}-{case[0].replace('/', '_')}-{key}"),
                      lines)
        ev, _ = rd.events("full", (key,), shift)
    finally:
        rd.GRID["full"]["vels"] = keep
    mono, out = x.mean(1), {}
    for lab, t, ns, v, ped in ev:
        if not lab.startswith(f"k{key}v"):
            continue
        i = int(t / 1000 * rd.FS)
        seg = mono[i + int(rd.W0 * rd.FS):i + int(rd.W1 * rd.FS)]
        p, fr = rd.spectrum(seg)
        f = rd.f0(key)
        pk = [p[(fr > k * f * (1 - rd.BAND)) & (fr < k * f * (1 + rd.BAND))].max() for k in range(1, 7)]
        h1 = 20 * np.log10(2 * np.sqrt(pk[0]) / len(seg) / 0.5 / 32768.0 + 1e-15)
        m = {f"H{k}": rd.db(pk[k - 1] / pk[0]) for k in range(2, 7)}
        m["inh"] = rd.offharm(p, fr, [f])
        out[v] = (h1, m)
    return out


def _sweep(a):
    binary, case, key, vels, tag, extra = a
    case = (case[0], case[1], case[2] + list(extra), case[3], case[4])
    return _harm(binary, case, key, vels, 0, tag)


def _chord_imd(a):
    binary, case, lab, tag = a
    case = (case[0], case[1], case[2] + [RAW_CC], case[3], case[4])
    # (a folder per worker: two chord cells of one case render the same script name; in one shared folder one worker
    # removed the other's files, CI 43726047832's FileNotFoundError)
    return rd.measure_once(binary, case, "full", os.path.join(rd.OUT, "levelproof-" + tag, str(os.getpid())), 0)[lab]["imd"]


def thd(c):
    return 10 * math.log10(sum(10 ** (c[f"H{k}"] / 10) for k in range(2, 7)))


def targets(path):
    if path:
        return [tuple(l.split()[:3]) for l in open(path) if l.strip() and not l.startswith("#")]
    out = []
    for e in rd.load_expected(rd.EXPECTED):
        if e.get("ceiling_proof") == OUT.replace(os.sep, "/"):
            cid, lab = e["match"].rsplit("/", 1)
            out.append((cid, lab, str(e["metric"])))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--entries")
    a = ap.parse_args()
    cells = targets(a.cells)
    if not cells:
        raise SystemExit("level_proof: no cells")
    ltree = rd.last_tree()
    c1 = any(c[0].startswith("c1/") for c in cells)     # (the two-core FM-1's cells: its build in both trees)
    cb = rd.tree_bins(ROOT, os.path.join(rd.OUT, "levelproof-cand-bin"), c1=c1)
    lb = rd.tree_bins(ltree, os.path.join(rd.OUT, "last-bin"), c1=c1)
    cc, lc = _cases(ROOT), _cases(ltree)
    cids = sorted({c[0] for c in cells} | {twin_of(c[0]) for c in cells if (f"{c[0]}/{c[1]}", c[2]) in CELL_RULES})
    n = int(os.environ.get("PYTEST_N", os.environ.get("PRISTINE_N", "2")) or 2)
    with Pool(max(1, n)) as pool:
        r = pool.map(_meas, [(cb[cc[c][1]], cc[c], "cand") for c in cids] + [(lb[lc[c][1]], lc[c], "last") for c in cids])
        cand, last = dict(r[:len(cids)]), dict(r[len(cids):])
        jobs = []
        for cid, lab, m in cells:
            v = cand[cid][lab].get(m)
            b = last[cid][lab].get(m)
            lim = rd.ceiling(cid, lab, m, v, b, cand[cid][lab])
            if lab.startswith("k") and LEVELLED(m):
                key, vel = (int(t) for t in lab[1:].split("v"))
                jobs.append(("level", cid, lab, m, (lb[lc[cid][1]], lc[cid], key, tuple(range(1, vel + 1)), "lsweep", ())))
                if m[0] == "H" and "/PRESET/" in cid:
                    for t, bins, cs in (("cand", cb, cc), ("last", lb, lc)):
                        jobs.append(("drive-" + t, cid, lab, m, (bins[cs[cid][1]], cs[cid], key, (vel,), "drv" + t,
                                                                  (DRIVE_OFF,))))
        sw = pool.map(_sweep, [j[4] for j in jobs], chunksize=1)
        raw = {}
        chords = [(cid, lab) for cid, lab, m in cells if m == "imd"]
        rr = pool.map(_chord_imd, [(cb[cc[c][1]], cc[c], l, "rawc") for c, l in chords] +
                      [(lb[lc[c][1]], lc[c], l, "rawl") for c, l in chords])
        for k, (c, l) in enumerate(chords):
            raw[(c, l)] = (rr[k], rr[len(chords) + k])
    res = {(j[0], j[1], j[2], j[3]): s for j, s in zip(jobs, sw)}
    rows, bad = [], 0
    for cid, lab, m in cells:
        cl, ll = cand[cid][lab], last[cid][lab]
        v, b = cl.get(m), ll.get(m)
        lim = rd.ceiling(cid, lab, m, v, b, cl)
        row = {"cell": f"{cid}/{lab}", "metric": m, "last": round(b, 2), "cand": round(v, 2),
               "limit": round(lim[0], 2), "over_ceiling": bool(v > lim[0] + 1e-9), "proven": False}
        if "h1" in cl and "h1" in ll and LEVELLED(m):
            row["abs_cand"], row["abs_last"] = round(v + cl["h1"], 2), round(b + ll["h1"], 2)
            if row["abs_cand"] <= row["abs_last"] + ABS_TOL:
                row["proven"] = "abs"
        if not row["proven"] and ("level", cid, lab, m) in res:
            sw = res[("level", cid, lab, m)]
            vp = min(sw, key=lambda q: abs(sw[q][0] - cl["h1"]))
            row.update(level_v=vp, level_h1_last=round(sw[vp][0], 2), level_h1_cand=round(cl["h1"], 2),
                       level_metric_last=round(sw[vp][1][m], 2))
            if v <= sw[vp][1][m] + LEVEL_TOL:
                row["proven"] = "level"
        if not row["proven"] and (cid, lab) in raw:
            row["raw_cand"], row["raw_last"] = (round(x, 2) for x in raw[(cid, lab)])
            if raw[(cid, lab)][0] <= raw[(cid, lab)][1] + RAW_TOL:
                row["proven"] = "raw"
        if not row["proven"] and ("drive-cand", cid, lab, m) in res:
            vel = int(lab.split("v")[1])
            dc, dl = res[("drive-cand", cid, lab, m)][vel], res[("drive-last", cid, lab, m)][vel]
            row["driveoff_abs_cand"], row["driveoff_abs_last"] = round(dc[1][m] + dc[0], 2), round(dl[1][m] + dl[0], 2)
            row["thd_cand"], row["thd_last"] = round(thd(cl), 2), round(thd(ll), 2)
            if max(row["driveoff_abs_cand"], row["driveoff_abs_last"]) <= DRIVE_FLOOR and row["thd_cand"] < row["thd_last"]:
                row["proven"] = "drive"
        rule = CELL_RULES.get((f"{cid}/{lab}", m))
        if not row["proven"] and rule:
            tw = twin_of(cid)
            tc, tl = cand[tw][lab], last[tw][lab]
            row["twin_cand"], row["twin_last"] = round(tc[m], 2), round(tl[m], 2)
            if rule == "floor":
                tlim = rd.ceiling(tw, lab, m, tc[m], tl[m], tc)[0]
                row["twin_limit"] = round(tlim, 2)
                row.setdefault("abs_cand", round(v + cl["h1"], 2))
                row.setdefault("abs_last", round(b + ll["h1"], 2))
                if floor_ok(v + cl["h1"], b + ll["h1"], tc[m], tlim):
                    row["proven"] = "floor"
            elif twin_ok(v, tl[m], tc[m]):
                row["proven"] = "twin"
        bad += not row["proven"]
        rows.append(row)
    doc = {"tool": "tools/level_proof.py", "against": rd.LAST, "verdict": "PASS" if not bad else "FAIL",
           "cells": len(rows), "unproven": bad, "rows": rows}
    json.dump(doc, open(os.path.join(ROOT, a.out), "w"), indent=1)
    print(f"level proof: {len(rows)} cells, {bad} unproven -> {a.out}: {doc['verdict']}")
    for r in rows:
        print(f"  {r['cell']:40s} {r['metric']:4s} {r['cand']:7.2f} last {r['last']:7.2f}  {r['proven'] or 'UNPROVEN'}")
    if a.entries:
        import yaml
        why = {"abs": "the absolute power is not higher than the last release's (the reference H1 fell)",
               "level": "the last release at the velocity that plays the candidate's level reads the same (within 1 dB)",
               "raw": "with the effects bypassed the imd equals the last release's (its value was the limiter on the attack)",
               "drive": "the DRIVE's own product (drive off: under -110 dBFS in both); total distortion re H1 lower",
               "floor": "the harmonic's absolute power under -90 dBFS in both releases (the 16-bit floor) and the one-core "
                        "twin under its ceiling: int16-grain content, inaudible",
               "twin": "at most the last release's one-core value of this cell + 0.05 and equal to the candidate's one-core "
                       "value: the released one-core reads the same (6 against 3 LSB)"}
        ent = [{"match": r["cell"], "metric": r["metric"],
                "min": math.floor((r["cand"] - r["last"] - 0.3) * 10) / 10, "max": math.ceil((r["cand"] - r["last"] + 0.3) * 10) / 10,
                "ceiling_proof": a.out.replace(os.sep, "/"),
                "reason": f"level proof ({r['proven']}): {why[r['proven']]}; coordinator sign-off 2026-11-14"}
               for r in rows if r["proven"]]
        yaml.safe_dump(ent, open(a.entries, "w"), sort_keys=False, width=100000)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
