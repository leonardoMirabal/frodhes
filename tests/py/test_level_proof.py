# SPDX-License-Identifier: GPL-3.0-only
"""The level proof (tools/level_proof.py) behind the expected-changes entries that lift a pristine ceiling with
`ceiling_proof: tests/level_proof.json`: it re-measures the candidate and the last release on every run (no stored
verdict), and every such entry's cell must be proven now (abs / level / raw / drive), its move inside the entry's
window. A cell that stops being proven fails here, not only in the release diff."""
import json
import os
import subprocess
import sys

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_diff as rd  # noqa: E402


def test_every_level_proof_entry_is_proven_now():
    out = os.path.join("build", "release_diff", "level_proof-" + os.environ.get("PYTEST_XDIST_WORKER", "w") + ".json")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "level_proof.py"), "--out", out], cwd=ROOT,
                       capture_output=True, text=True, env=dict(os.environ, PYTEST_N="2"))
    doc = json.load(open(os.path.join(ROOT, out)))
    assert p.returncode == 0 and doc["verdict"] == "PASS", p.stdout[-3000:] + p.stderr[-2000:]
    rows = {(r["cell"], r["metric"]): r for r in doc["rows"]}
    ents = [e for e in rd.load_expected(rd.EXPECTED) if e.get("ceiling_proof") == "tests/level_proof.json"]
    assert ents
    for e in ents:
        r = rows[(e["match"], str(e["metric"]))]
        assert r["proven"], r
        d = r["cand"] - r["last"]
        assert e["min"] - 0.05 <= d <= e["max"] + 0.05, (e["match"], e["metric"], d, e["min"], e["max"])
    # the committed record matches what the tool proves today
    rec = json.load(open(os.path.join(ROOT, "tests", "level_proof.json")))
    assert {(r["cell"], r["metric"]) for r in rec["rows"]} == set(rows) and rec["verdict"] == "PASS"
