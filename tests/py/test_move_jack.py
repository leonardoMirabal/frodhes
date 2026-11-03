# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_jack.py's dry run (no device: the host build plays, the jack simulated from it): the proof passes on
the simulated dev_fault and fails without it; a click put on the jack alone is found by the jack's detectors and
flagged as on the jack only; a click on USB and the jack (a device glitch) is found on both and not flagged as the
jack's own; the clean takes give no event."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
import move_jack as mj  # noqa: E402


def run(tmp_path, *extra, engines="rhodes"):
    out = str(tmp_path / "jack")
    code = mj.main(["--dry-run", "--engines", engines, "--secs", "8", "--out", out, *extra])
    return code, json.load(open(os.path.join(out, "summary.json"))), open(os.path.join(out, "summary.md")).read()


def test_clean_dry_run_proves_and_passes(tmp_path):
    code, R, md = run(tmp_path)
    assert code == 0, R["verdict"]
    assert R["proof"]["verdict"] == "PROVEN"
    assert R["proof"]["fault"]["matched"] == mj.PROVE_N and not R["proof"]["clean"]["gated_jack"]
    E = R["engines"][0]
    assert E["verdict"] == "PASS"
    for t in E["worst"]:
        assert t["gated_jack"] == [] and t["compare"]["jack_only"] == [], t["take"]
        assert t["compare"]["ok"] and abs(t["compare"]["jack_re_usb_db"] + 6.02) < 0.2   # (DRY_GAIN 0.5)
    assert "PASS (DRY RUN)" in md and "## The proof" in md and "dev_fault" in md


def test_click_on_the_jack_only_is_flagged(tmp_path):
    code, R, md = run(tmp_path, "--dry-inject", "jclick@2.0")
    assert code == 1
    for t in R["engines"][0]["worst"]:
        (_, at), = t["injected"]
        near = lambda evs: [e for e in evs if abs(e[0] - at) < 0.02]          # noqa: E731
        assert near(t["gated_jack"]), (t["take"], t["gated_jack"])          # (c): the jack's detectors fire
        assert [x for x in t["compare"]["jack_only"] if abs(x[0] - at) < 0.02], t["compare"]   # (d): jack only
        assert not near(t["usb_events"])                                    # USB never had it
    assert "click(s) on the jack only" in R["engines"][0]["verdict"]


def test_device_click_is_on_both_not_the_jacks_own(tmp_path):
    code, R, _ = run(tmp_path, "--dry-inject", "click@2.0")
    assert code == 1                                                        # (a click is a FAIL on the jack, (c))
    for t in R["engines"][0]["worst"]:
        (_, at), = t["injected"]
        assert [e for e in t["usb_events"] if e[1] == "click" and abs(e[0] - at) < 0.02], t["usb_events"]
        assert [e for e in t["gated_jack"] if e[1] == "hfclick" and abs(e[0] - at) < 0.02], t["gated_jack"]
        assert t["compare"]["jack_only"] == [], t["compare"]["jack_only"]


def test_no_pass_without_the_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(mj.DryJack, "fault", lambda self, spec, url=None, slot=0: None)   # (a build without dev_fault)
    code, R, md = run(tmp_path)
    assert code == 2 and R["verdict"].startswith("STOPPED") and R["engines"] == []
    assert "no fault reached the output" in R["proof"]["verdict"]
    assert "PASS" not in md.splitlines()[0]


def test_never_the_other_midi_ports():
    for name in ("Frodhes", "M6", "MIDISPORT 2x2", "IAC Driver", "AF16Rig"):
        with pytest.raises(SystemExit):
            mj.parse(["--midi", name])
