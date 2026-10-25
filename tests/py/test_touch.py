# SPDX-License-Identifier: GPL-3.0-only
"""SETUP K4 TOUCH (key velocity, keyvel.h): stored in the settings record's vel_curve byte (offset 9, a byte every
record before key velocity carries as 0) so that 0 reads MEDIUM, the default after the update: stored (touch + 3) & 3,
LIGHT 3, MEDIUM 0, HEAVY 1, FIXED 2; params_sanitize turns anything above 3 into 0 (MEDIUM). A device setting: kept
across a power cycle, not in a preset."""
import pytest

import test_persistence as tp
from hostrun import run

O_VEL_CURVE = 9                                         # settings_t: clock 6, midi_ch 7, key_vel 8, vel_curve 9
STORED = {"LIGHT": 3, "MEDIUM": 0, "HEAVY": 1, "FIXED": 2}
GO_SETUP = ["20 btn 7", "40 btn 7"]                     # GLO twice: TEMPO, SETUP


def saved_record(tag, lines=(), end=3500):
    """a record as this firmware saves it (a change, 2 s of quiet): the flash file and the record"""
    fl = tp.flash_file(tag)
    run(tag + "-1", list(lines) or [tp.cc(20, 114, 127)], end, flash=fl)
    img = tp.read_img(fl)
    return fl, img, tp.settings_record(img)


@pytest.mark.parametrize("old", [0, 4, 7, 0xFF])
def test_old_record_reads_medium(old):
    """a record saved before key velocity (vel_curve 0), or one with a value no version wrote: TOUCH is MEDIUM, KEY
    VEL is kept, and the next save writes 0"""
    fl, img, rec = saved_record(f"touch-old{old}")
    rec[O_VEL_CURVE], rec[8] = old, 104                 # (key_vel 104: it must survive the upgrade)
    tp.put_settings(img, rec)
    open(fl, "wb").write(img)
    r = tp.boot(f"touch-old{old}-2", fl, GO_SETUP + ["100 expect state card.17.3 MEDIUM", tp.cc(200, 114, 0)], 3500,
                check_preset=False)                     # (the saved panel is an edit of F01)
    s = r.state(100)
    assert not r.fails, r.fails
    assert s["card.17.3"] == "MEDIUM" and s["label.17.3"] == "TOUCH", s["card.17.3"]
    assert s["card.2.3"] == "104", s["card.2.3"]        # FRODHES K4 KEY VEL
    assert r.saves, r.saves
    assert tp.settings_record(tp.read_img(fl))[O_VEL_CURVE] == 0


def test_version1_record_reads_medium():
    """a version 1 record (0.1-0.3, converted at boot): MEDIUM too"""
    fl = tp.flash_file("touch-v1")
    img = bytearray(b"\xff" * 5 * tp.SECTOR)
    tp.put_settings(img, tp.v1_record({0: 1}, preset=0))
    open(fl, "wb").write(img)
    assert tp.boot("touch-v1", fl, check_preset=False).state(100)["card.17.3"] == "MEDIUM"


@pytest.mark.parametrize("touch,turns", [("LIGHT", -1), ("HEAVY", 1), ("FIXED", 2)])
def test_touch_saved_and_kept(touch, turns):
    """TOUCH turned on SETUP K4 is saved with the settings (the stored byte as in keyvel.h) and is back after a power
    cycle; a factory preset loaded then does not change it"""
    lines = GO_SETUP + [f"{100 + 20 * i} turn 5 {1 if turns > 0 else -1}" for i in range(abs(turns))] + ["300 dump"]
    fl = tp.flash_file(f"touch-{touch}")
    r1 = run(f"touch-{touch}-1", lines, 3500, flash=fl)
    assert r1.state(300)["card3"] == touch and r1.saves, (r1.state(300)["card3"], r1.saves)
    assert tp.settings_record(tp.read_img(fl))[O_VEL_CURVE] == STORED[touch]
    r2 = tp.boot(f"touch-{touch}-2", fl, ["200 midi 0xC0 3 0", "400 dump"], 600)
    assert r2.state(100)["card.17.3"] == touch and r2.state(400)["card.17.3"] == touch, (r2.state(100)["card.17.3"], r2.state(400)["card.17.3"])
