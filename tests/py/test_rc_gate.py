# SPDX-License-Identifier: GPL-3.0-only
"""tools/rc_gate.sh --dry-run: the release candidate's queue lists every gate AGENTS.md section 9 asks for, in order,
and --from / --only pick the steps."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def plan(*extra):
    r = subprocess.run(["bash", "tools/rc_gate.sh", "9.9.9", "/nonexistent.fwsc", "--dry-run", *extra], cwd=ROOT,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_full_queue():
    out = plan()
    steps = [ln.split("]")[0][1:] for ln in out]
    assert list(dict.fromkeys(steps)) == ["install", "t1", "crackle44", "crackle48", "cpu", "monkey", "s3",
                                          "t2", "autosave"]
    text = "\n".join(out)
    assert "hil_scan.py --tier must --fwsc" in text and "--prev /nonexistent.fwsc" in text
    assert sum("--worst --usb 48000" in ln for ln in out) == 9
    assert sum("hil_monkey.py --minutes 20" in ln and "--rate 48000" in ln for ln in out) == 4
    assert sum("--tier must --scenarios S3" in ln for ln in out) == 3
    assert "--rate 44100" in text and "--rate 48000" in text


def test_from_and_only():
    assert {ln.split("]")[0][1:] for ln in plan("--from", "t2")} == {"t2", "autosave"}
    assert {ln.split("]")[0][1:] for ln in plan("--only", "t1,s3")} == {"t1", "s3"}
