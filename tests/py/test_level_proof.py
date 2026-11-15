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
    # the tool's own failure first, with its stderr (CI 43723126582: a checkout without the last release's tag made the
    # tool exit before writing its record, and the test showed only the missing file)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}"
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


def test_the_two_named_cell_rules_fail_past_their_bounds():
    """the floor and twin rules (the coordinator's sign-off 2026-11-14, T2 0.9.12) hold one named cell each, never a
    pattern, and fail as soon as their bound is exceeded"""
    import level_proof as lp
    assert lp.CELL_RULES == {("c1/FUNK_WAH/PRESET/A/k33v96", "H4"): "floor",
                             ("c1/MARIMBA/DECAY0/A/k53v110", "clk"): "twin"}
    assert lp.FLOOR_ABS == -90.0 and lp.TWIN_TOL == 0.05
    # floor: the absolute power under -90 dBFS in both releases, the one-core twin under its own ceiling
    assert lp.floor_ok(-92.82, -93.45, -52.48, -50.0)
    assert not lp.floor_ok(-89.9, -93.45, -52.48, -50.0)        # the candidate over the floor
    assert not lp.floor_ok(-92.82, -89.9, -52.48, -50.0)        # the last release over it
    assert not lp.floor_ok(-92.82, -93.45, -49.9, -50.0)        # the one-core twin over its ceiling
    # twin: at most the last release's one-core value + 0.05, the candidate's one-core value within 0.05
    assert lp.twin_ok(15.56, 15.56, 15.56)
    assert lp.twin_ok(15.61, 15.56, 15.60)
    assert not lp.twin_ok(15.62, 15.56, 15.62)                  # over the released one-core + 0.05
    assert not lp.twin_ok(15.56, 15.56, 15.40)                  # the candidate's one-core reads otherwise
    # each named cell has its exact entry naming this proof (test_every_level_proof_entry_is_proven_now proves it)
    ents = {(e["match"], str(e["metric"])) for e in rd.load_expected(rd.EXPECTED)
            if e.get("ceiling_proof") == "tests/level_proof.json"}
    assert set(lp.CELL_RULES) <= ents
