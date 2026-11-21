# SPDX-License-Identifier: GPL-3.0-only
"""tools/type_scan.py, the calibration scan of a piano TYPE: its detectors fire on injected defects (a click, a
glitch, clipping, a runaway tail, a level jump, a note louder after the key-up, a noisy idle, an aliased tone, a
white floor) and stay quiet on the clean note and on pure harmonic growl, and the smoke profile (a handful of
renders: three keys, two velocities, two knobs, a sweep, the pedal, repeats, a chord, two effects) passes on
STAGE 77 and on SUITCASE."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import type_scan as ts  # noqa: E402


@pytest.fixture(scope="module")
def bins():
    os.makedirs(ts.OUT, exist_ok=True)
    return {"one": ts.build_bin("one", ts.BUILDS["one"])}


def test_detectors_fire_on_injected_defects(bins):
    rows = ts.selftest_rows(bins["one"])
    bad = [r for r in rows if not r["ok"]]
    assert not bad, [(r["case"], r["info"]) for r in bad]
    assert len(rows) >= 12


@pytest.mark.parametrize("idx", [0, 1])
def test_smoke_scan(bins, idx):
    ty = ts.resolve_type(idx)
    rows, _ = ts.scan_type(ty, "smoke", 2, set(), bins, quiet=True)
    s = ts.judge(rows, ty)
    fails = {c: [(r["case"], r["info"]) for r in v["rows"]] for c, v in s.items() if v["fail"] > v["known"]}
    assert not fails, fails
    assert {"CLK", "GLT", "CLP", "ALI", "IDL", "REL", "ZIP", "FXS"} <= set(s)


def test_report_is_json(bins, tmp_path):
    ty = ts.resolve_type(0)
    rows, _ = ts.scan_type(ty, "smoke", 2, {"CLK"}, bins, quiet=True)
    json.dumps(rows)
    assert any(r["check"] == "CLK" for r in rows)


def test_unknown_type_needs_its_cc():
    with pytest.raises(SystemExit):
        ts.resolve_type(6)
    t = ts.resolve_type(6, "80:112")
    assert (t.ccnum, t.ccval) == (80, 112)
