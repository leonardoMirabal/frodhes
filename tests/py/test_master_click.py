# SPDX-License-Identifier: GPL-3.0-only
"""GitHub #12: on the SMK-37, turning K1 (MASTER) clicks on the 3.5 mm jack; USB stays smooth (usb_pre_master: the
computer records before MASTER). The host build replaces platform.c, so its MASTER pass is run here through
tests/master_click_test.c (platform.c's code copied verbatim, checked below) on the app's own output: a pedalled
chord C2 C3 E3 G3 C4 at v110 from the SMK host build. K1 moves as smk_input.h makes it (24 of 1023 a detent): single
detents and fast spins (8 detents in 100 ms), up and down, at low (k 150), mid (500) and full (1000) volume; main-loop
passes every 0.05, 0.2 and 1 ms; USB before MASTER on and off.

A differential check per case: the largest second difference of the jack's samples while the knob moves (and 0.4 s
after) over the same, gained at the window's top MASTER gain without moving (a smooth gain change stays at or under
1: the gain only falls from there; a step adds |dg x| at once); the reference is rounded as the ISR rounds, so at
low volume (a few LSB of second difference) 1.5 is one LSB. 0.9.5's pass (one gain a block, BUGHUNT #20: the same
knob path, the gain constant over each block) gives 5-28, 0.9.6's (master_ramp, the gain a frame) 1.0-1.5."""
import os
import re
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import hostrun as H  # noqa: E402
from test_smk_host import smk_build  # noqa: E402

ROOT = H.ROOT
FS = 44100
LIMIT = 1.5
T0 = 1000                                                   # the first detent (ms); the chord from 200 ms
CASES = {
    "single": [(T0 + 300 * i, d) for i, d in enumerate((1, 1, 1, -1, -1, -1))],
    "spin": [(T0 + 12 * i, 1) for i in range(8)] + [(T0 + 800 + 12 * i, -1) for i in range(8)],
}
LEVELS = {"low": 150, "mid": 500, "full": 1000}


def test_copies_are_platform_c():
    plat = open(os.path.join(ROOT, "firmware/src/platform.c")).read()
    test = open(os.path.join(ROOT, "tests/master_click_test.c")).read()
    blocks = re.findall(r"/\* platform\.c begin \*/\n(.*?)/\* platform\.c end \*/", test, re.S)
    assert len(blocks) == 3
    for b in blocks:
        assert b in plat, b[:200]


def harness(tmp):
    exe = os.path.join(tmp, "master_click_test")
    subprocess.run(["cc", "-O2", "-Wall", "-o", exe, os.path.join(ROOT, "tests/master_click_test.c")], check=True)
    return exe


@pytest.fixture(scope="module")
def chord_raw(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("mclick"))
    lines = [H.cc(195, 64, 127)]
    for n in (36, 48, 52, 55, 60):
        lines += H.note(200, n, 110, 3600)
    r = H.run("master-chord", lines, 4000, binary=smk_build())
    x = np.stack([np.asarray(r.L), np.asarray(r.R)], 1).astype(np.int16)
    raw = os.path.join(tmp, "in.raw")
    x.tofile(raw)
    return tmp, raw, harness(tmp)


def jack(fix, mode, pre, pass_us, k0, events):
    tmp, raw, exe = fix
    out = os.path.join(tmp, f"o{mode}{pre}{pass_us}{k0}.raw")
    subprocess.run([exe, raw, out, str(mode), str(pre), str(pass_us), str(k0)] + [f"{t}:{d}" for t, d in events],
                   check=True)
    return np.fromfile(out, dtype=np.int32).reshape(-1, 2).astype(float)


def click_ratio(fix, mode, pre, pass_us, k0, events):
    tmp, raw, exe = fix
    y = jack(fix, mode, pre, pass_us, k0, events)
    x = np.fromfile(raw, dtype=np.int16).reshape(-1, 2).astype(float)
    i0, i1 = int((events[0][0] - 20) * FS / 1000), int((events[-1][0] + 400) * FS / 1000)
    # the top MASTER gain in the window (master_q12 = k^2 >> 8), applied as the ISR does, without movement
    kmax = max(k0, min(1023, k0 + 24 * max(np.cumsum([d for _, d in events]).max(), 0)))
    ref = np.floor(x[i0:i1] * ((kmax * kmax) >> 8) / 4096.0)
    d2y = np.abs(np.diff(y[i0:i1], 2, axis=0)).max()
    d2r = np.abs(np.diff(ref, 2, axis=0)).max()
    return d2y / d2r


@pytest.mark.parametrize("pass_us", [50, 200, 1000])
@pytest.mark.parametrize("pre", [0, 1])
@pytest.mark.parametrize("level", list(LEVELS))
@pytest.mark.parametrize("case", list(CASES))
def test_master_does_not_click(chord_raw, case, level, pre, pass_us):
    ev = CASES[case]
    new = click_ratio(chord_raw, 1, pre, pass_us, LEVELS[level], ev)
    old = click_ratio(chord_raw, 0, pre, pass_us, LEVELS[level], ev)
    print(f"#12 {case:6s} {level:4s} pre {pre} pass {pass_us:4d} us: 0.9.5 {old:6.2f}  0.9.6 {new:6.2f}")
    assert new <= LIMIT, (case, level, pre, pass_us, old, new)
    assert old > 2 * LIMIT, ("the check sees 0.9.5's step", case, level, old)
