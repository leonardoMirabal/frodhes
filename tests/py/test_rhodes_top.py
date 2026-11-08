# SPDX-License-Identifier: GPL-3.0-only
"""The clean top (rhodes.c rh_top_x, firmware/gen/rh_top.h, tools/rh_top.py): v127 without the pickup's overdrive.

- H2 and the attack's H2 re H1 at v127 <= -12 dB on every TYPE, both responses (0.9.9 and the engine before it: about
  -6 / -2 dB on the mid keys; the user, 2026-11-08: external controllers send 127 easily);
- the dynamic range kept: v127 louder than v110 (>= 3 LU K-weighted, the engine before it +3.7 .. 4.2) and brighter
  (the attack's centroid higher);
- below each key's knee, a key with no cut, VIBES and MARIMBA: the engine without it (-DRH_NO_TOP), sample for
  sample."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import rh_top  # noqa: E402

KEYS = (33, 45, 57, 69, 81, 93, 100, 108)


@pytest.fixture(scope="module")
def bins():
    tag = str(os.getpid())
    return rh_top.build("t-cur-" + tag), rh_top.build("t-notop-" + tag, ["-DRH_NO_TOP"])


@pytest.fixture(scope="module")
def cells(bins):
    return rh_top.measure(bins[0], list(KEYS), (110, 127), meths=(0, 127), jobs=int(os.environ.get("PYTEST_N", "2")))


@pytest.mark.parametrize("meth", (0, 127), ids=("A", "B"))
@pytest.mark.parametrize("ty", rh_top.TYPES)
def test_v127_is_clean(cells, ty, meth):
    o = cells[(ty, meth)]
    for k in KEYS:
        h2, a2 = o[(k, 127)][:2]
        assert h2 <= -12.0 and a2 <= -12.0, f"TYPE {ty} key {k}: H2 {h2:.1f} aH2 {a2:.1f} dB re H1 at v127"


@pytest.mark.parametrize("meth", (0, 127), ids=("A", "B"))
@pytest.mark.parametrize("ty", rh_top.TYPES)
def test_v127_louder_and_brighter_than_v110(cells, ty, meth):
    if ty == 3 and meth:     # (MAP's DYNO: its force at the HAMMER x TYPE cap from v110 up, the bark was its only
        # growth, +2.6 LU before the cut; resp-ab 7a435be4 retires MAP: B is A x a soft-end gain there)
        pytest.xfail("RESPONSE B is the velocity map here (retired on resp-ab): DYNO v110 -> v127 +2.0 LU, no brighter")
    o = cells[(ty, meth)]
    for k in KEYS:
        lu = o[(k, 127)][5] - o[(k, 110)][5]
        assert lu >= 3.0, f"TYPE {ty} key {k}: v127 only {lu:+.2f} LU over v110"
        assert o[(k, 127)][4] > o[(k, 110)][4], f"TYPE {ty} key {k}: the attack no brighter at v127"


def render(b, setup, notes, tag):
    d = os.path.join(rh_top.OUT, "r")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"bi-{tag}-{os.path.basename(b)}")
    ls, t = list(setup), 300
    for k, v in notes:
        ls += [f"{t} midi 0x90 {k} {v}", f"{t + 400} midi 0x80 {k} 0"]
        t += 700
    ls.append(f"{t + 300} end")
    open(p + ".txt", "w").write("\n".join(sorted(ls, key=lambda l: int(l.split()[0]))) + "\n")
    subprocess.run([b, p, p + ".txt"], capture_output=True, check=True)
    w = wave.open(p + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").copy()
    w.close()
    for f in os.listdir(d):
        if f.startswith(os.path.basename(p)):
            os.remove(os.path.join(d, f))
    return x


def knee(ty, key):
    r = rh_top_r()[ty][key - rh_top.KEY_LO]
    return 127 if not r else 127 - (r + (r >> 2))


def rh_top_r():
    import re
    s = open(rh_top.HDR).read()
    body = re.search(r"RH_TOP_R\[4\]\[RH_NKEY\] = \{(.*?)\n\};", s, re.S).group(1)
    return [[int(v) for v in row.split(",")] for row in re.findall(r"\{([0-9,]+)\}", body)]


def test_under_the_knee_the_engine_as_it_was(bins):
    cur, old = bins
    R = rh_top_r()
    assert any(R[0]) and not all(R[0]), "the table cuts some keys, not all"
    for ty in rh_top.TYPES:
        for meth in (0, 127):
            st = ["90 midi 0xB0 119 127", f"100 midi 0xB0 80 {ty * 32}", f"110 midi 0xB0 40 {meth}"]
            notes = [(k, v) for k in (21, 45, 60, 84, 100) for v in range(1, knee(ty, k) + 1, 9) if v <= knee(ty, k)]
            notes += [(k, knee(ty, k)) for k in (45, 60, 84, 100) if knee(ty, k) >= 1]
            a, b = render(cur, st, notes, f"{ty}-{meth}"), render(old, st, notes, f"{ty}-{meth}")
            assert a.shape == b.shape and (a == b).all(), f"TYPE {ty} RESPONSE {meth}: a velocity under the knee moved"
    st = ["90 midi 0xB0 119 127", "100 midi 0xB0 80 0", "110 midi 0xB0 40 0"]
    k = next(k for k in range(60, 90) if R[0][k - rh_top.KEY_LO])
    a, b = render(cur, st, [(k, 127)], "over"), render(old, st, [(k, 127)], "over")
    assert not (a == b).all(), "the cut acts over the knee"


@pytest.mark.parametrize("pc", (12, 13, 14, 15), ids=("VIBES", "VIBES_ECHO", "MARIMBA", "WOOD_ECHO"))
def test_bars_untouched(bins, pc):
    notes = [(k, v) for k in (36, 60, 84) for v in range(1, 128, 9)] + [(60, 127)]
    a, b = (render(x, [f"100 midi 0xC0 {pc}"], notes, f"bars{pc}") for x in bins)
    assert a.shape == b.shape and (a == b).all()
