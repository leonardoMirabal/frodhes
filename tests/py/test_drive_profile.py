# SPDX-License-Identifier: GPL-3.0-only
"""tests/baselines/drive_own_target.json (tools/drive_profile.py): the black-box profile of FDRV loads, has the whole
grid, and a sample of it re-measures to the same numbers (--check; the full re-measure is `--check --full`)."""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import drive_profile as dp  # noqa: E402


def test_json_loads_and_is_complete():
    js = json.load(open(dp.JSON))
    assert len(js["main"]) == 4 * 128
    assert len(js["corner"]) == 4 * len(dp.CORNER_AMOUNTS) * len(dp.CORNERS)
    assert len(js["aliasing"]) == len(dp.AL_GRID) and len(js["alias_amp"]) == 12 and len(js["steps"]) == 3
    r = js["main"]["r0/a64/t64/m127"]
    assert len(r["h"][0]) == 8 and len(r["bark"]) == len(dp.BARK_EDGES) - 1
    assert js["main"]["r0/a0/t64/m127"]["g"] == [0.0, 0.0]            # AMOUNT 0: unity


def test_scan_reproduces_the_json():
    assert dp.check(False) == 0
