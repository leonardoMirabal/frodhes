# SPDX-License-Identifier: GPL-3.0-only
"""tools/table_ab.py's table half: the header parses to the sizes the firmware declares, an identical copy diffs
to nothing, and perturbed words are found with their key and field labels (the render half: run the tool)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import table_ab as ta  # noqa: E402

H = os.path.join(ROOT, "firmware", "gen", "bars_tables.h")


def test_parse_sizes():
    tabs, defs = ta.parse(H)
    nkey = int(defs["BAR_NKEY"])
    assert len(tabs["BAR_KEYS"]["vals"]) == nkey * len(ta.KEY_F)
    assert len(tabs["KN_FAM"]["vals"]) == 128 and len(tabs["BAR_MOT_COS"]["vals"]) == 257


def test_identical_and_perturbed(tmp_path):
    res, dd = ta.diff_tables(H, H)
    assert not dd and all(t["changed"] == 0 for t in res)
    b = tmp_path / "bars_tables.h"
    c4 = 60 - 21
    ta.perturb(H, b, [f"BAR_KEYS:{c4 * 64 + 56}:+1541", f"BAR_KEYS:{(53 - 21) * 64 + 34}:+1", "BAR_TUBEG:100:-1"])
    res, _ = ta.diff_tables(H, str(b))
    t = {r["name"]: r for r in res}
    assert t["BAR_KEYS"]["changed"] == 2 and t["BAR_KEYS"]["maxlsb"] == 1541
    assert {it["label"] for it in t["BAR_KEYS"]["items"]} == {"C4 k_bt", "F3 s4.b (V4)"}
    assert t["BAR_TUBEG"]["changed"] == 1 and t["BAR_TUBEG"]["keys"]          # a band: its keys listed
    assert sum(r["changed"] for r in res) == 3
