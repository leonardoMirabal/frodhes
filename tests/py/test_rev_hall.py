# SPDX-License-Identifier: GPL-3.0-only
"""The HALL reverb's CPU rewrite (0.9.5: one shared ring for the 8 lines, the matrix written straight into it, the
upsample fused with the return, the return's / 254 as a multiply) is the same reverb, sample for sample, and since
rev-upsample with the framework's 11-tap half-bands in place of the (1, 2, 1) / 4 down and the linear up (every
TYPE's top octave: tests/py/test_rev_images.py):

- tests/c/rev_fuzz.c drives fx_reverb directly (random MIX / DECAY / TONE / PRE-DELAY, silence to several times full
  scale, MIX 0 tails that ring out to exact silence, fx_reset_state) and its hash is rev-upsample's;
- the return's multiply equals (w * (254 - v)) / 254 for every MIX v and every |w| <= 2^19 (the mid / side are sums
  of 8 int16 line outputs: |w| <= 8 * 32768, and the up's half-band overshoots by at most 1632 / 1024)."""
import os
import re
import subprocess

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "rev_hall", os.environ.get("PYTEST_XDIST_WORKER", ""))
# rev-upsample (the half-bands; release-0.9.5 3d35dc2 before: 2cd22f790d2f260d / a6d4b38e5071d8c4 / 1971cfe258fe7bb0):
# seed, blocks -> hash, blocks the reverb ran, tails that ended in silence
WANT = {(1, 20000): "710fbc593e7e3197 12049 5", (7, 20000): "64a61da6e44ef738 10480 7",
        (95, 20000): "2d548c85742a52a6 15482 4"}


@pytest.fixture(scope="module")
def fuzz_bin():
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, "rev_fuzz")
    subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-I" + os.path.join(ROOT, "firmware", "src"),
                    "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", exe,
                    os.path.join(ROOT, "tests", "c", "rev_fuzz.c"), "-lm"], check=True)
    return exe


@pytest.mark.parametrize("seed,blocks", sorted(WANT))
def test_reverb_sample_for_sample(fuzz_bin, seed, blocks):
    got = subprocess.run([fuzz_bin, str(seed), str(blocks)], check=True, capture_output=True, text=True).stdout.split()
    h, runs, ends = got[0], int(got[1]), int(got[2])
    assert runs > blocks // 2 and ends >= 1, "the fuzz no longer reaches the reverb or a tail's end"
    assert " ".join(got) == WANT[(seed, blocks)], f"fx_reverb moved: {' '.join(got)}"


def test_return_divide_exact():
    src = open(os.path.join(ROOT, "firmware", "src", "rev_types.c")).read()   # (fx_reverb: the reverb TYPEs' framework)
    assert "int32_t rk = (254 - v) * 4227330 + (4 * (254 - v)) / 254 + 1" in src
    assert re.search(r"#define RV_RET\(w\) \(\(int32_t\)\(\(\(int64_t\)\(w\) \* rk\) >> 30\) \+ "
                     r"\(int32_t\)\(\(uint32_t\)\(w\) >> 31\)\)", src)
    w = np.arange(-(1 << 19), (1 << 19) + 1, dtype=np.int64)
    for v in range(128):
        c = 254 - v
        rk = c * 4227330 + (4 * c) // 254 + 1
        assert rk == (c << 30) // 254 + 1 and rk < (1 << 31)
        n = w * c
        want = np.where(n < 0, -((-n) // 254), n // 254)          # C's truncating division
        got = ((w * rk) >> 30) + (w < 0)
        assert np.array_equal(got, want), f"v {v}"
