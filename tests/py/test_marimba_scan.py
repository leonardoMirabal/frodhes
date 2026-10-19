# SPDX-License-Identifier: GPL-3.0-only
"""tools/marimba_scan.py: the MARIMBA scan profile, its stress cases and the musicality gate M1-M7 (MARIMBA PLAN §10).

The plumbing is judged now: the stub detection, MARIMBA selected by CC 79 127 and by PC 14 / 15 (F15 / F16), the
pristine checks of the stress cases (clicks, sat28, the exact end, the gate's fade, steals, the swap), the report's
format. The musicality gate's verdicts (M1-M7, the MUTE stop) are MARIMBA's numbers: expected failures while the
tables are the sync-1 stub (xfail, strict=False, "awaiting sync 2 real tables"), judged as soon as they are real."""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import marimba_scan as ms  # noqa: E402
from hostrun import build  # noqa: E402

AWAIT = pytest.mark.xfail(ms.stub_detected(), reason=ms.STUB_REASON, strict=False)
_ROWS = {}


def rows(part):
    if part not in _ROWS:
        build()
        _ROWS[part] = {"stress": ms.stress, "SEL": ms.selector_rows}.get(part, lambda: ms.gate(False, {part}))()
    return _ROWS[part]


def fails(rs, checks=None):
    return [r for r in rs if (checks is None or r[0] in checks) and r[2] is False]


# ---------------------------------------------------------------- the stub detection
HDR = """#define BAR_NTYPE 2
static const bar_type_t BAR_TYPE[BAR_NTYPE] = {   /* x */
  {0, 6554, 32767, 0, 0, 9, 48, 3, 145},
  {0, 6554, 32767, 0, 0, 9, 48, %s, 145},
};
static const uint16_t KN_TUBE[BAR_NTYPE][128] = {
  {1,2,3},
  {1,2,%s},
};
"""


@pytest.mark.parametrize("a,b,stub", [("3", "3", True), ("0", "3", False), ("3", "4", False)])
def test_stub_detection(tmp_path, monkeypatch, a, b, stub):
    monkeypatch.delenv("MARIMBA_STUB", raising=False)
    p = tmp_path / "h.h"
    p.write_text(HDR % (a, b))
    assert ms.stub_detected(str(p)) is stub
    p.write_text("#define BAR_NTYPE 1\n")
    assert ms.stub_detected(str(p)) is True                       # no MARIMBA rows at all
    monkeypatch.setenv("MARIMBA_STUB", "0")
    assert ms.stub_detected(str(p)) is False


def test_the_tree_header_is_read():
    """the real header parses: every per-type array the detector compares has a row per type"""
    src = open(ms.HDR).read()
    n = int(__import__("re").search(r"#define BAR_NTYPE (\d+)", src).group(1))
    got = {k: ms._rows(src, k) for k in ms.PER_TYPE}
    assert all(v is not None and len(v) == n for v in got.values()), {k: v and len(v) for k, v in got.items()}


def test_profile():
    """the profile selects TYPE 5 with a CC 79 value in MARIMBA's band (frodhes.c: 96..127), the bar_measure helper
    agrees, and every card names its CC from params.c"""
    import re
    t = ms.PROFILE["types"][0]
    assert t["type"] == 5 and t["select"] == [("cc", 79, 127)] and 96 <= ms.CC79 <= 127
    assert ms.bm.type_cc(5) == ms.CC79 and ms.bm.type_name(5) == "MARIMBA"
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    for c in ms.PROFILE["cards"]:
        m = re.search(r'\[P_%s\] = \{"[^"]*", \d+, \d+, F_\w+, (\d+)' % c["param"], src)
        assert m and int(m.group(1)) == c["cc"], c


# ---------------------------------------------------------------- the plumbing (now)
def test_selection():
    assert not fails(rows("SEL")), fails(rows("SEL"))


@pytest.mark.parametrize("check", ["A1", "H1", "S4", "B7", "A3", "A5"])
def test_stress_pristine(check):
    rs = [r for r in rows("stress") if r[0] == check]
    assert rs and not fails(rs), fails(rs)


def test_report_format(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "OUT", str(tmp_path))
    r = [("A1", "case a", True, 0, "clicks 0", False), ("M1", "case b", False, 2.0, "2.0 dB", True),
         ("M5", "case c", None, 1.0, "information", True)]
    st, lines = ms.report(r, True, "smoke", 1.0)
    assert st == ["ok", "AWAIT", "info"]
    st2, _ = ms.report(r, False, "smoke", 1.0)
    assert st2 == ["ok", "FAIL", "info"]                           # real tables: the same row fails
    j = json.load(open(tmp_path / "scan.json"))
    assert j["type"] == 5 and j["cc79"] == 127 and {"check", "case", "ok", "metric", "detail", "specific", "status"} <= set(j["rows"][0])
    txt = open(tmp_path / "report.txt").read()
    assert "TYPE 5 MARIMBA" in txt and "per check:" in txt


# ---------------------------------------------------------------- MARIMBA's numbers (sync 2)
@AWAIT
@pytest.mark.parametrize("part,checks", [("stress", {"M5", "B2"}), ("M1", None), ("M2", None), ("M3", None),
                                         ("M4", None), ("M6", None), ("M7", None)])
def test_musicality_gate(part, checks):
    rs = rows(part)
    assert rs and not fails(rs, checks), [r[1] + ": " + r[4] for r in fails(rs, checks)]
