# SPDX-License-Identifier: GPL-3.0-only
"""bars.c's strike coefficient cache (BAR_CC, 0.9.10): bar_cc_fill computes a note-on's rh_coef pairs at its queueing
(the main loop, engine.c eng_post), stamped with every input (type, key, TUNE, RING, DAMPER); bar_init takes a slot
only when the stamp is its strike's inputs, else rh_coef in the ISR. The output is byte for byte the build without
the cache (-DBAR_CC=0), one core and two (FR_C1), on the bar scripts; bar_cc_knob.txt moves RING, DAMPER and the TYPE
between the queueing and the strike, so the fallback runs there (FRODHES_BARCC_STAT counts the hits and misses).
The whole tree: every host script the same with the cache on and off (checked with this change, 984 WAVs)."""
import filecmp
import glob
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402

OUT = os.path.join(ROOT, "build", "bar_cc", os.environ.get("PYTEST_XDIST_WORKER", "main"))
SCRIPTS = ["bar_cc_knob", "burst", "marimba_swap", "pc_stall"]


def exe(name, defs):
    path = os.path.join(OUT, name, "frodhes")

    def make(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', *defs,
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", p, os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    return hostrun.once(path, make)


def run(binary, script, tag):
    d = os.path.join(OUT, tag)
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(os.path.join(d, script + "*.wav")):
        os.remove(f)
    r = subprocess.run([binary, os.path.join(d, script), os.path.join(ROOT, "tests", "scripts", script + ".txt")],
                       capture_output=True, text=True, cwd=ROOT, env=dict(os.environ, FRODHES_BARCC_STAT="1"))
    m = re.search(r"bar_cc hit (\d+) miss (\d+)", r.stderr)
    return sorted(glob.glob(os.path.join(d, script + "*.wav"))), (int(m[1]), int(m[2])) if m else None


@pytest.mark.parametrize("c1", [0, 1])
@pytest.mark.parametrize("script", SCRIPTS)
def test_same_as_without_cache(c1, script):
    d = ["-DFR_C1=1"] if c1 else []
    on = exe(f"on{c1}", d)
    off = exe(f"off{c1}", d + ["-DBAR_CC=0"])
    w_on, st = run(on, script, f"r_on{c1}")
    w_off, st0 = run(off, script, f"r_off{c1}")
    assert w_off and len(w_on) == len(w_off)
    for a, b in zip(w_on, w_off):
        assert filecmp.cmp(a, b, shallow=False), (a, b)
    assert st0 is None                                    # (the build without the cache counts nothing)
    if script == "bar_cc_knob":
        assert st[1] >= 5 and st[0] >= 26, st             # the fallback ran, the burst came from the slots
    elif script in ("burst", "marimba_swap"):
        assert st[0] > 0 and st[1] == 0, st               # one value throughout: every strike from a slot
