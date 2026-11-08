# SPDX-License-Identifier: GPL-3.0-only
"""tools/c1_monkey.py: the two-core build (FR_C1) against the one-core 14-voice full-rate-reverb build on monkey
streams. Two seeds each way (the full runs: tools/c1_monkey.py --seeds 1-300):

- the oracle build (C1M_ORACLE=1: one chunk a block, the two estimates put back to the one-core formulas) equals the
  reference sample for sample: nothing but the estimates differs on the two-core path (--strict);
- at the real chunk size (C1M_ORACLE=8: both builds start COMP from rest on the block before's peak alone) the two
  are equal on seeds where the real build differs (1, 24: COMP's estimate and its residue under BYPASS);
- the real build (C1_SUB 8) differs on seed 1 (a preset switches COMP on from rest), and every stretch is put down to
  a known estimate: the detector would name it, and it fails on none."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(ROOT, "tools", "c1_monkey.py")

pytestmark = pytest.mark.xdist_group("c1_monkey")   # (one worker: the tool's own binaries under build/)


def tool(env=None, *args):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, TOOL, "-j", "2", *args], capture_output=True, text=True, env=e, cwd=ROOT,
                          timeout=900)


def test_oracle_equals_the_reference():
    p = tool({"C1M_ORACLE": "1"}, "--seeds", "1-2", "--strict")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "2 of 2 seeds sample for sample equal" in p.stdout, p.stdout


def test_oracle8_equals_the_reference():
    p = tool({"C1M_ORACLE": "8"}, "--seeds", "1,24", "--strict")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "2 of 2 seeds sample for sample equal" in p.stdout, p.stdout


def test_the_estimates_are_named():
    p = tool(None, "--seeds", "1-1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "diff  seed     1" in p.stdout and "cause: comp" in p.stdout, p.stdout
    assert "UNEXPLAINED" not in p.stdout, p.stdout
