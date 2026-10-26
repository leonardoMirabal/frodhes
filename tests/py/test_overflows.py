# SPDX-License-Identifier: GPL-3.0-only
"""int32 overflows the sanitized monkey found (0.9.6): each shrunk repro runs clean under ASan + UBSan (the FM-1
wraps where the host reports). The repros: tests/py/monkey/."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import monkey as mk  # noqa: E402

HERE = os.path.dirname(__file__)


def san_run(name):
    r = mk.run(open(os.path.join(HERE, "monkey", name)).read(), mk.build(san=True))
    assert r.code == 0 and "runtime error" not in r.out, r.out[-2000:]


def test_bughunt22_plate_input():
    """BUGHUNT #22 (monkey seed 427470704, shrunk): PLATE's input one-pole (rev_plate.c pl_in) took (b - u) x pb in 32
    bits; with COMP's +24 dB make-up the plate's input passes ~1.2x full scale and it overflowed (0.9.5: "signed
    integer overflow: -104040 * 26000")"""
    san_run("bughunt22-plate-overflow.txt")


def test_bughunt26_wah_filter():
    """BUGHUNT #26 (monkey seed 995464060, shrunk): touch WAH's filter on a hot input (preset 33's COMP make-up, the
    pedal, CC 113 120: ~12x full scale): q x bp overflowed (0.9.5 too: "8090 * 274782"); the wah's input is clipped to
    2x full scale (fx.c WAH_XM)"""
    san_run("bughunt26-wah-overflow.txt")
