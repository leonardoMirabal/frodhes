# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes mode loop without the force (rhodes.c rh_mode_p_t): X, Y at 2^32 (x, y the high words) give the same
integers as the 2^30 form while |x|, |y|, |eps| < 2^29; and the firmware's loops keep their instruction counts
(tools/build.py hot_loops on build/frodhes.dis, when this tree's firmware is built)."""
import os
import random

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIS = os.path.join(ROOT, "build", "frodhes.dis")
M32, M64 = (1 << 32) - 1, (1 << 64) - 1


def s32(v):
    v &= M32
    return v - (1 << 32) if v >> 31 else v


def s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def old_form(x, y, ex, ey, e, eps, n=32):
    """the 2^30 form (before): X = x 2^30 + ex, x = (int32)(X >> 30)"""
    X, Y, out = s64((x << 30) + ex), s64((y << 30) + ey), []
    for _ in range(n):
        X = s64(X + e * y)
        x = s32(X >> 30)
        Y = s64(Y - (eps * y + e * x))
        y = s32(Y >> 30)
        out.append(x)
    return out, x, y, X & 0x3FFFFFFF, Y & 0x3FFFFFFF


def new_form(x, y, ex, ey, e, eps, n=32):
    """the 2^32 form (rh_mode_p_t): 4 y, 4 x, 4 eps in 32 bits, the eps product first"""
    eps4 = s32(eps * 4)
    X, Y, out = s64((x << 32) + (ex << 2)), s64((y << 32) + (ey << 2)), []
    for _ in range(n):
        y4 = s32(y * 4)
        Y = s64(Y - eps4 * y)
        X = s64(X + e * y4)
        x = s32(X >> 32)
        x4 = s32(x * 4)
        Y = s64(Y - e * x4)
        y = s32(Y >> 32)
        out.append(x)
    return out, x, y, (X & M32) >> 2, (Y & M32) >> 2


def test_same_integers_in_range():
    rng = random.Random(7)
    for _ in range(3000):
        amp = 1 << rng.choice((20, 24, 26, 27))
        args = (rng.randrange(-amp, amp), rng.randrange(-amp, amp), rng.randrange(1 << 30), rng.randrange(1 << 30),
                rng.randrange(1, (1 << 31) - 1), rng.randrange(1, 1 << 27))
        a, b = old_form(*args), new_form(*args)
        if max(abs(v) for v in a[0]) >= 1 << 28:      # (an orbit past the sat28 bound: not a case the firmware has)
            continue
        assert a == b, args


def test_differs_past_the_bound():
    """the bound is real: a state at 2^29 wraps 4 x (why the host scan of the in-loop peak matters)"""
    args = (1 << 29, 0, 0, 0, 1 << 29, 1 << 20)
    assert old_form(*args, n=2) != new_form(*args, n=2)


def test_firmware_mode_loops():
    if not os.path.exists(DIS):
        pytest.skip("no firmware build (./build.sh)")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build as B
    text = open(DIS).read()
    if "rh_mode_p1_l:" not in text:
        pytest.skip("the firmware build is not this tree's")
    for fn, lim, sh30 in (("rh_mode_p", 12, 0), ("rh_mode_p1_l", 11, 0), ("rh_mode_f", 20, 2), ("rh_mode_f1_l", 19, 2)):
        lps = B.hot_loops(text, fn)
        assert len(lps) == 1, f"{fn}: {len(lps)} loops"
        lp = lps[0]
        assert lp["n"] <= lim and not lp["st"] and not lp["ld"], f"{fn}: {lp}"
        assert lp["sh30"] == sh30 and lp["mac"] == 3, f"{fn}: {lp}"
