# SPDX-License-Identifier: GPL-3.0-only
"""The reverb-level proof (tools/rev_level_proof.py) behind the expected-changes entries of the full-rate reverb's level
(A3 0.9.13, section 7; the ceiling cells with `ceiling_proof: tests/rev_level_proof.json`): it builds the candidate a
second time at 0.9.12's two reverb gains and re-measures both on every run (no stored verdict). The old-level build must
pass the full grid with no failure, every exact entry's cell must be a proven failing cell of the candidate now, its
move inside the entry's window. A cell that stops being proven fails here, not only in the release diff."""
import json
import os
import subprocess
import sys

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_diff as rd  # noqa: E402
import rev_level_proof as rl  # noqa: E402


def test_the_old_level_is_0912s():
    """the second build differs in the two gains alone: RQ_IN_HALL at fx.c's HL_IN (the half-rate input, 0.9.12's
    full-rate HALL input too) and RI_OG at 0.9.12's 3215"""
    assert rl.old_level() == ["-DRQ_IN_HALL=10320", "-DRI_OG=3215"]
    src = open(os.path.join(ROOT, "firmware", "src", "rev_hq.c")).read()
    assert "#ifndef RQ_IN_HALL" in src and "#define RQ_IN_HALL 12266" in src
    src = open(os.path.join(ROOT, "firmware", "src", "rev_hq_inf.c")).read()
    assert "#ifndef RI_OG" in src and "#define RI_OG 3445" in src


def test_every_rev_level_entry_is_proven_now():
    out = os.path.join("build", "release_diff", "rev_level_proof-" + os.environ.get("PYTEST_XDIST_WORKER", "w") + ".json")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "rev_level_proof.py"), "--out", out], cwd=ROOT,
                       capture_output=True, text=True, env=dict(os.environ, PYTEST_N="2"))
    # the tool's own failure first, with its stderr (CI 43723126582: a checkout without the last release's tag made the
    # tool exit before writing its record, and the test showed only the missing file)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}"
    doc = json.load(open(os.path.join(ROOT, out)))
    assert p.returncode == 0 and doc["verdict"] == "PASS" and doc["old_level_failures"] == 0, \
        p.stdout[-3000:] + p.stderr[-2000:]
    rows = {(r["cell"], r["metric"]): r for r in doc["cells"]}
    ents = [e for e in rd.load_expected(rd.EXPECTED) if rl.own(e)]
    assert ents and all(e.get("ceiling_proof") in (None, "tests/rev_level_proof.json") for e in ents)
    for e in ents:
        if not rd.exact_entry(e):
            assert e["match"].startswith("[ch][1q]/"), e        # (the one-core FM-1 build never)
            continue
        for meth in "AB":
            cell = e["match"].replace("[AB]", meth)
            if cell.split("/")[3] != meth:
                continue
            r = rows[(cell, str(e["metric"]))]
            assert r["proven"], r
            d = r["cand"] - r["last"]
            assert e["min"] - 0.05 <= d <= e["max"] + 0.05, (cell, e["metric"], d, e["min"], e["max"])
            assert (r["ceiling"] is not None) == bool(e.get("ceiling_proof")), (cell, e["metric"])
    # a chained entry (another proof's cell at 0.9.12's reverb level): the old-level build reads that proof's value
    ch = [e for e in rd.load_expected(rd.EXPECTED) if rl.chained(e)]
    assert {(r["cell"], r["metric"]) for r in doc["chained"]} == {(e["match"], str(e["metric"])) for e in ch}
    for r in doc["chained"]:
        assert r["proven"] and abs(r["old_level"] - r["chain_value"]) <= rl.CHAIN_TOL, r
    # the committed record matches what the tool proves today
    rec = json.load(open(os.path.join(ROOT, "tests", "rev_level_proof.json")))
    assert {(r["cell"], r["metric"]) for r in rec["cells"]} == set(rows) and rec["verdict"] == "PASS"
