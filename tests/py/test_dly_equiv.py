# SPDX-License-Identifier: GPL-3.0-only
"""The delay's own core (firmware/src/dly_own.c) against the old loops: tests/c/dly_equiv.c runs both from the same
random state and compares every output sample and the whole state after each call (default mode, 10^6 calls).
Skipped while dly_own.c does not exist."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OWN = os.path.join(ROOT, "firmware", "src", "dly_own.c")
OUT = os.path.join(ROOT, "build", "dly_equiv", f"p{os.getpid()}")


@pytest.mark.skipif(not os.path.exists(OWN), reason="firmware/src/dly_own.c does not exist yet")
def test_dly_own_equals_old_loops():
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([os.path.join(ROOT, "tests", "c", "dly_equiv_build.sh"), OUT], check=True)
    r = subprocess.run([os.path.join(OUT, "dly_equiv")], capture_output=True, text=True)
    assert r.stdout.strip() == "PASS 1000000 calls", r.stdout
