#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Is a release-diff move of the two-core / HQ builds the full-rate reverb's level (0.9.13 A3) and nothing else?

0.9.13 set the full-rate reverb (FR_REV_HQ: the FM-1's two-core package, the Move, the plugin, the MPC) to the half-rate
reverb's level in band (K-weighted, 100 Hz .. 8 kHz): HALL + 1.5 dB at the kernel input (rev_hq.c RQ_IN_HALL), INFINITE
+ 0.6 dB at its output (rev_hq_inf.c RI_OG). Their taps had been matched on the Bark loudness, which counts the band
above 11 kHz the half rate does not have. A louder wet reads more reverb energy in the harmonic bands (H2..H6), off
them (inh, imd), after the key-up, so some cells move past their tolerance or over a pristine ceiling.

The proof, every run (no stored verdict): the candidate is built a second time with the two gains at their 0.9.12
values (OLD_LEVEL: RQ_IN_HALL = fx.c HL_IN, RI_OG 3215; nothing else differs) and both are measured on the full grid
(tools/release_diff.py's cells and judge, against the last release built from git, with this tool's own entries left
out of the expected-changes list). The old-level build must pass with no failure at all; then every failure of the
candidate is the reverb's level alone: the same code, the same notes, one gain on the wet. A failure that is not a
number (silent <-> sounding, a missing value) or that is on the one-core FM-1 build (which does not run the full-rate
reverb) is unproven: the verdict FAILs.

  tools/rev_level_proof.py [--out tests/rev_level_proof.json] [--entries FILE]
  (--entries writes the expected-changes entries for the proven cells: exact cells for a ceiling or a cell an exact
  entry already governs, else one entry per build pair, TYPE / preset, config and metric)"""
import argparse
import fnmatch
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_diff as rd  # noqa: E402

ROOT = rd.ROOT
TAG = "A3 two-core reverb level"   # this tool's entries start their reason with it (left out of its own judging)
RI_OG_OLD = 3215                   # rev_hq_inf.c's RI_OG in 0.9.12


def old_level():
    hl = re.search(r"#define HL_IN (\d+)", open(os.path.join(ROOT, "firmware", "src", "fx.c")).read()).group(1)
    return [f"-DRQ_IN_HALL={hl}", f"-DRI_OG={RI_OG_OLD}"]


def own(e):
    """this tool's own entries (left out of its judging); a chained entry stays in (see chained())"""
    return str(e.get("reason", "")).startswith(TAG) and not e.get("chain")


def chained(e):
    """an entry another proof (`chain`) proved on 0.9.12's reverb level at `chain_value`: the old-level build must read
    that value again (CHAIN_TOL), so that proof holds there and the candidate's move from it is the wet's level"""
    return str(e.get("reason", "")).startswith(TAG) and e.get("chain")


CHAIN_TOL = 0.1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join("tests", "rev_level_proof.json"))
    ap.add_argument("--entries")
    a = ap.parse_args()
    out = os.path.join(rd.OUT, "revlevel-bin")
    bins = rd.tree_bins(ROOT, out, c1=True)
    old = {"fm1": bins["fm1"], "hq": rd.cc_build(ROOT, os.path.join(out, "hq-old"), rd.HQ_DEFS + old_level()),
           "c1": rd.cc_build(ROOT, os.path.join(out, "c1-old"), rd.C1_DEFS + old_level())}
    exp = [e for e in rd.load_expected(rd.EXPECTED) if not own(e)]
    base = rd.last_cells("full")
    cand = rd.measure_all(bins, "full", "revlevel-cand")
    oldc = rd.measure_all(old, "full", "revlevel-old")
    ofails, _, _ = rd.judge(oldc, base, exp)
    fails, _, _ = rd.judge(cand, base, exp)
    cells, bad = {}, 0
    for cid, lab, m, v, b, lim, why in fails:
        k = (f"{cid}/{lab}", m)
        ok = cid.split("/")[0] in ("hq", "c1") and isinstance(v, float) and isinstance(b, float) and not ofails
        row = cells.setdefault(k, {"cell": k[0], "metric": m, "last": None if not isinstance(b, float) else round(b, 2),
                                   "cand": v if not isinstance(v, float) else round(v, 2),
                                   "old_level": oldc.get(cid, {}).get(lab, {}).get(m), "ceiling": None,
                                   "why": [], "proven": ok})
        if isinstance(row["old_level"], float):
            row["old_level"] = round(row["old_level"], 2)
        if lim is not None and why.startswith("CEILING"):
            row["ceiling"] = round(lim, 2)
        row["why"].append(why)
        row["proven"] = row["proven"] and ok
    chains = []
    for e in rd.load_expected(rd.EXPECTED):
        if chained(e):
            cid, lab = e["match"].rsplit("/", 1)
            m, vo, vc = str(e["metric"]), oldc.get(cid, {}).get(lab, {}).get(str(e["metric"])), \
                cand.get(cid, {}).get(lab, {}).get(str(e["metric"]))
            chains.append({"cell": e["match"], "metric": m, "chain": e["chain"], "chain_value": e["chain_value"],
                           "old_level": None if not isinstance(vo, float) else round(vo, 2),
                           "cand": None if not isinstance(vc, float) else round(vc, 2),
                           "proven": isinstance(vo, float) and abs(vo - e["chain_value"]) <= CHAIN_TOL})
    bad = sum(1 for r in cells.values() if not r["proven"]) + sum(1 for r in chains if not r["proven"])
    doc = {"tool": "tools/rev_level_proof.py", "change": "0.9.13 A3: the full-rate reverb at the half rate's level in "
           "band (rev_hq.c RQ_IN_HALL, rev_hq_inf.c RI_OG)", "old_level_defs": old_level(),
           "old_level_failures": len(ofails), "verdict": "PASS" if not bad and not ofails else "FAIL",
           "failing_cells": len(cells), "ceiling_cells": sum(1 for r in cells.values() if r["ceiling"] is not None),
           "unproven": bad, "chained": chains, "cells": sorted(cells.values(), key=lambda r: (r["cell"], r["metric"]))}
    json.dump(doc, open(os.path.join(ROOT, a.out), "w"), indent=1)
    print(f"rev level proof: the old-level build {len(ofails)} failures; the candidate {len(cells)} failing cells "
          f"({doc['ceiling_cells']} over a ceiling), {bad} unproven -> {a.out}: {doc['verdict']}")
    for f in ofails[:10]:
        print("  old level:", f[0], f[1], f[2], f[6])
    if a.entries and doc["verdict"] == "PASS":
        write_entries(doc, exp, a.out, a.entries)
    return 0 if doc["verdict"] == "PASS" else 1


def write_entries(doc, exp, proof, path):
    """exact entries (ceilings, exact-governed cells; the RESPONSE pair merged as [AB] when both fit one window),
    else one wildcard per build pair / TYPE / config / metric over the moves"""
    def governed(cell, m):
        return any(rd.exact_entry(e) and fnmatch.fnmatch(cell, e.get("match", "")) and
                   any(fnmatch.fnmatch(m, p) for p in str(e.get("metric", "*")).split()) for e in exp)
    exact, wild = {}, {}
    for r in doc["cells"]:
        d = r["cand"] - r["last"]
        b, t, cfg, meth, cell = r["cell"].split("/")
        if r["ceiling"] is not None or governed(r["cell"], r["metric"]):
            exact.setdefault((f"{b}/{t}/{cfg}/[AB]/{cell}", r["metric"], r["ceiling"] is not None), []).append(
                (r["cell"], d))
        else:
            lo, hi = wild.get((t, cfg, r["metric"]), (d, d))
            wild[(t, cfg, r["metric"])] = (min(lo, d), max(hi, d))
    lines = []
    for (pat, m, ceil), ds in sorted(exact.items()):
        groups = [(pat, [x[1] for x in ds])] if len(ds) == 2 and abs(ds[0][1] - ds[1][1]) <= 0.6 else \
                 [(c, [x]) for c, x in ds]
        for p, xs in groups:
            lo, hi = math.floor((min(xs) - 0.3) * 10) / 10, math.ceil((max(xs) + 0.3) * 10) / 10
            why = "the wet''s level only: the same build at 0.9.12''s level passes" + (" this ceiling" if ceil else "")
            lines.append(f"  - {{match: '{p}', metric: {m}, min: {lo}, max: {hi}"
                         + (f", ceiling_proof: {proof}" if ceil else "") + f", reason: '{TAG} ({proof}): {why}'}}")
    for (t, cfg, m), (lo, hi) in sorted(wild.items()):
        lines.append(f"  - {{match: '[ch][1q]/{t}/{cfg}/*', metric: {m}, min: {math.floor((lo - 0.3) * 10) / 10}, "
                     f"max: {math.ceil((hi + 0.3) * 10) / 10}, reason: '{TAG} ({proof}): the wet''s level only: the same "
                     "build at 0.9.12''s level passes'}")
    open(path, "w").write("\n".join(lines) + "\n")
    print(f"{path}: {len(lines)} entries")


if __name__ == "__main__":
    sys.exit(main())
