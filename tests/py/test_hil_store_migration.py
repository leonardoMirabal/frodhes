# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_store_migration.py's read-back on synthetic playbacks (its self-test: the patterns A / B, SWING, BARS
told apart at 90, 120 and 160 BPM; a wrong pattern, a second migration, the defaults FAIL), its plan and its guard
on the 0.9.5 package"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import hil_store_migration as S  # noqa: E402


def test_self_test_passes():
    assert S.self_test(log=lambda s: None)


@pytest.mark.parametrize("swing", [50, 58, 64, 70])
def test_swing_read(swing):
    x, fs, t0 = S.synth(S.PAT_A, swing, 2, bpm=110.0, seed=swing)
    r = S.read_playback(x, fs, t0)
    assert abs(r["swing"] - swing) <= 1.0 and abs(r["bars"] - 2) < 0.05 and [m for _, m in r["notes"]] == list(S.PAT_A)


def test_step7_notes_the_setup():
    x, fs, t0 = S.synth(S.PAT_A, 50, 4, seed=3)
    rows = S.judge(S.read_playback(x, fs, t0), S.PAT_A, None, "7")
    sw, bars = (float(v) for v in rows[1][1].split(" / "))
    assert rows[0][3] is True and rows[1][3] is None and abs(sw - 50) < 1 and abs(bars - 4) < 0.05


def test_silence_is_a_fail():
    x, fs, t0 = S.synth((), 50, 2)
    rows = S.judge(S.read_playback(x, fs, t0), S.PAT_A, S.SET_A, "x")
    assert rows == [("x: the playback", "0 notes heard", "readable", False)]


def test_dry_run_plan(capsys):
    assert S.main(["--fwsc", "cand.fwsc", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "OVERWRITES" in out and all(f"  {n}  " in out for n in "123456789") and "end" in out


def test_old_package_checked(tmp_path):
    old, new = tmp_path / "old.fwsc", tmp_path / "new.fwsc"
    old.write_bytes(b"not 0.9.5")
    new.write_bytes(b"x")
    with pytest.raises(SystemExit, match="not the 0.9.5 release asset"):
        S.main(["--fwsc", str(new), "--old", str(old)])


def test_dry_run_says_the_mode(capsys):
    assert S.main(["--fwsc", "cand.fwsc", "--fresh", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "mode --fresh" in out and "  1f two 0.9.5 settings saves" in out
    assert S.main(["--fwsc", "cand.fwsc", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "mode as found" in out and "1f" not in out


def _run_to_step2(monkeypatch, tmp_path, extra):
    """the run on a fake FM-1, stopped at step 2's read-back: the log of installs, CCs and waits"""
    import types
    log = []

    class Dev:
        def cc(self, c, v): log.append(("cc", c, v))
        def send(self, *m): log.append(("send",) + m)

    class Must:
        not_new = True
        def __init__(self, a): self.dev = Dev()
        def info(self): return "fake"
        def install(self, p): log.append(("install", os.path.basename(p)))
        def open_usb(self, *a, **k): raise RuntimeError("no audio here")

    monkeypatch.setitem(sys.modules, "hil_must", types.SimpleNamespace(Must=Must))
    monkeypatch.setattr(S, "OUT", str(tmp_path))
    monkeypatch.setattr(S, "sha256", lambda p: S.OLD_SHA256)
    monkeypatch.setattr(S.time, "sleep", lambda t: log.append(("wait", t)))
    old, new = tmp_path / "old.fwsc", tmp_path / "new.fwsc"
    old.write_bytes(b"o")
    new.write_bytes(b"n")
    assert S.main(["--fwsc", str(new), "--old", str(old)] + extra) == 1     # (stopped: no audio)
    first = log.index(("install", "old.fwsc"))
    step2 = log.index(("cc", S.CC_QUANT, S.QUANT_8))
    return log[first + 1:step2], (tmp_path / "report.txt").read_text()


def test_fresh_saves_twice_on_old_before_the_setup(monkeypatch, tmp_path):
    between, rep = _run_to_step2(monkeypatch, tmp_path, ["--fresh"])
    sw = [i for i, e in enumerate(between) if e[:2] == ("cc", S.CC_SWING)]
    assert [between[i][2] for i in sw] == list(S.FRESH_SWING) and len(set(S.FRESH_SWING)) == 2
    for i in sw:                                       # each change, then a wait past 0.9.5's 2 s save delay
        assert ("wait", S.SAVE_WAIT_S) in between[i + 1:i + 3]
    assert "mode --fresh" in rep and "1f two 0.9.5 settings saves" in rep


def test_without_fresh_no_extra_saves(monkeypatch, tmp_path):
    between, rep = _run_to_step2(monkeypatch, tmp_path, [])
    assert not [e for e in between if e[:2] == ("cc", S.CC_SWING)] and "mode as found" in rep
