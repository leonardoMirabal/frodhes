#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""A generated header against the committed one, for a --check on another machine than the committed header's (macOS
arm64): its LAPACK (the FE eigen-solves) and its compiler (a generator's C helper, FMA contraction) round differently.
Near: the same text, the same numbers in the same places, each number within max(1 LSB for an integer, rel x its
size), the body's sha256 aside."""
import re

SHA = re.compile(r"sha256\(body\) = [0-9a-f]{64}")
NUM = re.compile(r"(-?\d+\.?\d*(?:[eE][-+]?\d+)?)")


def near(a, b, rel):
    ta, tb = NUM.split(SHA.sub("", a)), NUM.split(SHA.sub("", b))
    if rel <= 0 or len(ta) != len(tb):
        return False
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x == y:
            continue
        if i % 2 == 0:
            return False
        fx, fy = float(x), float(y)
        tol = rel * max(abs(fx), abs(fy))
        if x.lstrip("-").isdigit() and y.lstrip("-").isdigit():
            tol = max(1, tol)
        if abs(fx - fy) > tol:
            return False
    return True


def check(old, hdr, rel):
    """--check's verdict and exit code: equal, near (rel) or DIFFERS"""
    if old == hdr:
        return "equal", 0
    if near(old, hdr, rel):
        return "near (rel %g)" % rel, 0
    return "DIFFERS", 1
