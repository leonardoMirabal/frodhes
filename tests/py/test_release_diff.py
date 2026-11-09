# SPDX-License-Identifier: GPL-3.0-only
"""The release diff's own logic (tools/release_diff.py; the gate itself runs as `make pristine`, the first stage of
make test and make pytest, and `make release-diff`): the pristine ceilings hold whatever the expected-changes file
lists, an unlisted move fails in either direction, a listed one needs a reason, and the reference files belong to
the last release."""
import json
import os
import re
import sys

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_diff as rd  # noqa: E402

CID = "fm1/ST77/FXOFF/A"


def cells(**kw):
    c = {"level": -20.0, "peak": -6.0, "H2": -20.0, "H3": -45.0, "H4": -70.0, "H5": -80.0, "H6": -85.0, "inh": -40.0,
         "cent": 400.0, "decay": 6.0, "keyup": -12.0, "clk": 10.0, "clk_off": 10.0}
    c.update(kw)
    return {CID: {"k33v127": c, "script": {"clip": 0, "noise": -120.0}}}


def verdict(cand, expected=()):
    fails, moves, stale = rd.judge(cand, cells(), list(expected))
    return fails


def test_the_same_sound_passes():
    assert verdict(cells()) == []


def test_the_0910_distortion_fails_even_when_listed():
    """H3 at H1's level at v127 (the 0.9.10 fault): a ceiling, whatever the list says"""
    f = verdict(cells(H3=7.9), [{"match": "*", "metric": "*", "reason": "the sonic improvements"}])
    assert any(x[2] == "H3" and x[6].startswith("CEILING") for x in f)


def test_quiet_harmonics_may_move_below_the_floor():
    assert verdict(cells(H5=-65.0, H6=-70.0)) == []


def test_unlisted_moves_fail_both_ways():
    assert verdict(cells(level=-17.0)) and verdict(cells(level=-23.0)) and verdict(cells(H2=-26.0))


def test_a_listed_move_needs_a_reason_and_its_range():
    e = {"match": f"{CID}/k33*", "metric": "level", "min": 2.0, "max": 4.0}
    assert verdict(cells(level=-17.0), [dict(e, reason="louder on purpose (commit x)")]) == []
    assert verdict(cells(level=-17.0), [dict(e, reason="TODO")])
    assert verdict(cells(level=-15.0), [dict(e, reason="louder on purpose")])        # beyond its max


def test_clips_and_key_up():
    assert verdict({CID: dict(cells()[CID], script={"clip": 3, "noise": -120.0})})
    raw = {"fm1/ST77/RAW/A": cells()[CID]}
    raw["fm1/ST77/RAW/A"] = dict(raw["fm1/ST77/RAW/A"], script={"clip": 3, "noise": -120.0})
    assert not [f for f in rd.judge(raw, {"fm1/ST77/RAW/A": cells()[CID]}, [])[0] if f[6].startswith("CEILING")]
    f = verdict(cells(keyup=1.0), [{"match": "*", "metric": "keyup", "reason": "x"}])
    assert any(x[6].startswith("CEILING") for x in f)


def test_the_baseline_is_the_last_release_and_covers_every_case():
    doc = json.load(open(rd.BASE_FAST))
    assert doc["release"] == rd.LAST
    assert {c[0] for c in rd.cases("fast")} == set(doc["cases"])


def test_the_expected_changes_are_against_the_last_release():
    import yaml
    doc = yaml.safe_load(open(rd.EXPECTED))
    assert doc["against"] == rd.LAST
    for i, e in enumerate(doc.get("changes") or []):
        assert e.get("match") and str(e.get("reason", "")).strip() and not str(e["reason"]).upper().startswith("TODO"), i


def test_the_hq_build_is_the_makefiles():
    mk = open(os.path.join(ROOT, "Makefile")).read()
    move = re.search(r"^MOVE_DEFS \?= (.*)$", mk, re.M).group(1).split()
    assert rd.HQ_DEFS == move + ["-DHQ_DEFAULT=1"]
