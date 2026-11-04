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
    assert "event(s) on the jack only" in R["engines"][0]["verdict"]


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


def test_dropout_and_level_step_on_the_jack_are_caught(tmp_path):
    """the jack's own floor (DRY_FLOOR_DBFS, the Move's) does not hide a real dropout or a level step on the jack"""
    code, R, _ = run(tmp_path, "--dry-inject", "jdrop@1.5", "--dry-inject", "jstep@2.6")
    assert code == 1
    for t in R["engines"][0]["worst"]:
        at = dict(t["injected"])
        drop = [e for e in t["gated_jack"] if abs(e[0] - at["jdrop"]) < 0.06]
        assert drop and {e[1] for e in drop} <= {"zero", "track", "hfclick", "step"}, (t["take"], t["gated_jack"])
        assert [x for x in t["compare"]["jack_only"] if abs(x[0] - at["jstep"]) < 0.03 and "level -" in x[1]], \
            (t["take"], t["compare"]["jack_only"])
        assert not [e for e in t["usb_events"] if abs(e[0] - at["jdrop"]) < 0.06 or abs(e[0] - at["jstep"]) < 0.06]


def test_the_plain_ncc_gate_fails_the_clean_proof_on_the_moves_floor(tmp_path, monkeypatch):
    """the device case of 2026-11-04: with the jack's floor at -65.7 dBFS, hil_crackle.track's plain 0.97 gate fires
    on the clean chord as it decays toward the floor; judged against the floor (the default), the proof holds"""
    monkeypatch.setattr(mj, "track_noise_aware", lambda events, j, noise: (list(events), []))
    code, R, _ = run(tmp_path, "--prove")
    assert code == 2 and "the clean take gave" in R["proof"]["verdict"]
    assert all(e[1] == "track" for e in R["proof"]["clean"]["gated_jack"]) and R["proof"]["clean"]["gated_jack"]


def test_align_follows_the_clocks_not_a_held_chords_period():
    """a held chord repeats every 15.3 ms (C3 G3 C4 E4); the jack 0.4 ms after USB, its clock 60 ppm off: align
    searches only ALIGN_TOL around the clocks' line (here 1 ms off the truth) and finds the truth, never a period
    away"""
    import numpy as np
    from scipy.signal import resample_poly
    sr, dur = 44100, 6.0
    t = np.arange(int(sr * dur)) / sr
    f = [130.81, 196.0, 261.63, 329.63]
    u = sum(np.sin(2 * np.pi * x * t) for x in f) * 3000 * np.exp(-t / 3) * (t > 0.3)
    u[int(3.0 * sr) + 37] += 1500
    tj = np.arange(int(48000 * dur)) / 48000
    up = resample_poly(u, 640, 147)
    j = 0.4 * np.interp(((tj - 0.0004) * (1 + 60e-6)) * 192000, np.arange(len(up)), up, left=0, right=0)
    j += np.random.default_rng(1).normal(0, 17, len(j))
    q = np.stack([u, u], 1)
    cu, cj = (0.3, 0.0), (0.3014, -60e-6)                                # (the clocks' line 1 ms off)
    al = mj.align(np.round(q), j, sr, cu, cj, noise=17.0 ** 2)
    assert al is not None and al[4] == "clocks"
    L0, r = al[2], al[3]                     # (the truth: USB's frame = the jack's (a - 19.2) (1 + 60 ppm))
    assert abs(L0 + 0.0004 * (1 + 60e-6) * 48000) < 1.0 and abs(r - 60e-6) < 20e-6, (L0, r)
