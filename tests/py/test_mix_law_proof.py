# SPDX-License-Identifier: GPL-3.0-only
"""The MIX law proof's record (tests/mix_law_proof.json, tools/mix_law_proof.py) and its rule for the cells outside the
law (level_proven): a cell the balance does not explain counts as proven only through the level proof, directly or by
the reverb-level proof chained to it. A broken chain or a missing anchor must leave the cell unproven. No rendering:
the record is checked against the other proofs' committed records, which their own tests re-measure."""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mix_law_proof as mp  # noqa: E402
import release_diff as rd  # noqa: E402

CELL, MET = "c1/RESO_SWEEP/PRESET/A/k45v40", "inh"


def chain_case():
    """an intact chained proof: (entry, level record, reverb-level record)"""
    e = {"match": CELL, "metric": MET, "min": 4.2, "max": 4.8, "ceiling_proof": mp.REV_PROOF, "chain": mp.LEVEL_PROOF,
         "chain_value": -36.44}
    lp = {"verdict": "PASS", "rows": []}
    rp = {"verdict": "PASS", "chained": [{"cell": CELL, "metric": MET, "chain": mp.LEVEL_PROOF, "chain_value": -36.44,
                                          "old_level": -36.44, "cand": -36.36, "proven": True}]}
    return e, lp, rp


def test_an_intact_chain_proves_the_cell():
    e, lp, rp = chain_case()
    assert mp.level_proven(CELL, MET, 4.5, [e], lp, rp).startswith("reverb-level proof chained to the level proof")


@pytest.mark.parametrize("broken", ["old_level_off", "chain_unproven", "rev_record_fail", "rev_record_missing",
                                    "chain_value_differs", "move_outside"])
def test_a_broken_chain_leaves_the_cell_unproven(broken):
    e, lp, rp = chain_case()
    r, move = rp["chained"][0], 4.5
    if broken == "old_level_off":
        r["old_level"] = -36.44 + mp.CHAIN_TOL + 0.05
    elif broken == "chain_unproven":
        r["proven"] = False
    elif broken == "rev_record_fail":
        rp["verdict"] = "FAIL"
    elif broken == "rev_record_missing":
        rp = None
    elif broken == "chain_value_differs":
        r["chain_value"] = -36.0
    elif broken == "move_outside":
        move = 5.0
    assert mp.level_proven(CELL, MET, move, [e], lp, rp) is None


@pytest.mark.parametrize("missing", ["no_chain", "chain_elsewhere", "no_chain_value", "chain_value_text",
                                     "level_record_missing", "level_record_fail", "no_entry"])
def test_a_missing_anchor_leaves_the_cell_unproven(missing):
    e, lp, rp = chain_case()
    if missing == "no_chain":
        del e["chain"]
    elif missing == "chain_elsewhere":
        e["chain"] = rp["chained"][0]["chain"] = "tests/other_proof.json"
    elif missing == "no_chain_value":
        del e["chain_value"]
    elif missing == "chain_value_text":
        e["chain_value"] = "-36.44"
    elif missing == "level_record_missing":
        lp = None
    elif missing == "level_record_fail":
        lp["verdict"] = "FAIL"
    elif missing == "no_entry":
        e["match"] = "c1/RESO_SWEEP/PRESET/A/k45v64"
    assert mp.level_proven(CELL, MET, 4.5, [e], lp, rp) is None


def test_the_level_proof_directly():
    e = {"match": "fm1/RESO_SWEEP/PRESET/[AB]/k45v40", "metric": MET, "min": 3.4, "max": 4.1,
         "ceiling_proof": mp.LEVEL_PROOF}
    lp = {"verdict": "PASS", "rows": [{"cell": "fm1/RESO_SWEEP/PRESET/A/k45v40", "metric": MET, "proven": "abs"}]}
    assert mp.level_proven("fm1/RESO_SWEEP/PRESET/A/k45v40", MET, 3.8, [e], lp, None) == "level proof (abs)"
    lp["rows"][0]["proven"] = False
    assert mp.level_proven("fm1/RESO_SWEEP/PRESET/A/k45v40", MET, 3.8, [e], lp, None) is None


def test_the_record_passes_and_each_outside_cell_holds_on_the_committed_proofs():
    doc = json.load(open(os.path.join(ROOT, "tests", "mix_law_proof.json")))
    assert doc["verdict"] == "PASS" and doc["unproven"] == 0 and doc["cells"]
    expected, lp, rp = rd.load_expected(rd.EXPECTED), mp.record(mp.LEVEL_PROOF), mp.record(mp.REV_PROOF)
    outside = [c for c in doc["cells"] if str(c["proven"]).startswith(("level proof", "reverb-level proof"))]
    assert doc.get("outside_the_law") == len(outside)
    for c in outside:
        assert mp.level_proven(c["cell"], c["metric"], round(c["cand"] - c["last"], 2), expected, lp, rp) == c["proven"], c
