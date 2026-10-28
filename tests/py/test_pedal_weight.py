# SPDX-License-Identifier: GPL-3.0-only
"""The damper weight with the pedal up is the same before and after the first pedal event (0.9.8). The engines boot
with full damping, rh_pedal_w / bar_pedal_w = 32768 (rhodes.c, bars.h), but PEDAL_W[0..41] was 32767 (tools/
gen_rhodes.py capped the table at 32767), so a damped release used 1.0 until the first CC 64 and 32767/32768 after
any, even a CC 64 0 or an engine switch (engine.c eng_switch sends the pedal to the new engine). The table now holds
32768 (a uint16): a phrase renders the same with or without a CC 64 0 first. On 0.9.7 the Rhodes phrase was up to 8
LSB apart (test_rhodes_cc64_0_changes_nothing fails there). The bar engine never used its boot value: every switch to
it, the one at power-on included, sends it the pedal (PEDAL_W[CC 64]); its test passed on 0.9.7 too and guards that."""
import os
import re
import shutil

import numpy as np
import pytest

from hostrun import OUT, ROOT, cc, run

import test_persistence as tp

PHRASE = []
for i, (t, notes) in enumerate([(300, (48, 52, 55)), (900, (60, 64, 67, 71)), (1500, (36, 43))]):
    for n in notes:
        PHRASE += [f"{t} midi 0x90 {n} {100 - 10 * i}", f"{t + 350} midi 0x80 {n} 0"]   # let go, pedal up: damped
PHRASE.sort(key=lambda s: int(s.split()[0]))
END = 4000


def test_full_damping_is_the_boot_value():
    """PEDAL_W below the half pedal (CC 64 0..41) is the boot weight of both engines, 32768"""
    tab = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    w = [int(x) for x in re.search(r"PEDAL_W\[128\] = \{([^}]*)\}", tab).group(1).split(",")]
    assert len(w) == 128 and w[:42] == [32768] * 42 and w[64:] == [0] * 64
    rh = open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()
    bars = open(os.path.join(ROOT, "firmware", "src", "bars.h")).read()
    assert re.search(r"static int32_t rh_pedal_w = 32768;", rh)
    assert re.search(r"static int32_t bar_pedal_w = 32768;", bars)


def test_rhodes_cc64_0_changes_nothing():
    """the Rhodes at power-on: the phrase with a CC 64 0 first renders sample for sample as without it"""
    a = run("pw-rh-plain", PHRASE, END)
    b = run("pw-rh-cc64", [cc(1, 64, 0)] + PHRASE, END)
    assert np.abs(a.L).max() > 1000                                  # (it plays)
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R), np.abs(a.L - b.L).max()


@pytest.mark.parametrize("eng,sel,name", [("vibes", 0, "VIBE"), ("marimba", 127, "MRMB")])
def test_bars_boot_cc64_0_changes_nothing(eng, sel, name):
    """VIBES / MARIMBA as the power-on engine (a user preset saved with it, switched on again): the phrase with a CC
    64 0 first renders as without it"""
    fl = tp.flash_file(f"pw-{eng}")
    r = run(f"pw-{eng}-save", [cc(10, 79, sel), cc(11, 104, 0),          # the engine, its NOISE 0 (quiet: it saves)
                               "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9", "3500 dump"],   # SAVE to U03
            4000, flash=fl)
    assert r.bank_saves, r.bank_saves
    runs = []
    for tag, first in (("plain", []), ("cc64", [cc(150, 64, 0)])):
        f2 = os.path.join(OUT, f"pw-{eng}-{tag}.flash")
        shutil.copyfile(fl, f2)                                      # (each boot from the same saved flash)
        runs.append(run(f"pw-{eng}-{tag}", ["100 dump"] + first + PHRASE, END, flash=f2))
    a, b = runs
    assert a.state(100)["header"].startswith("U03 " + name), a.state(100)["header"]   # (the bar engine at power-on)
    assert np.abs(a.L).max() > 1000
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R), np.abs(a.L - b.L).max()
