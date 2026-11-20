# SPDX-License-Identifier: GPL-3.0-only
"""firmware/src/fx_own.h against the fx.c helpers it replaces: tests/own_dsp_test.c (exp2_neg, hl_c16, sh_tz over
every 32-bit input; both DC blockers over long random and edge sequences), bit-exact."""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "own_dsp")


def test_own_dsp_equal():
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, "own_dsp_test")
    subprocess.run(["cc", "-O2", "-fwrapv", "-Wall", "-o", exe, os.path.join(ROOT, "tests", "own_dsp_test.c")], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
