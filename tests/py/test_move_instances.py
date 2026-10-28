# SPDX-License-Identifier: GPL-3.0-only
"""Two Frodhes on the Move (the user, 2026-10-28: "i load 2 frodhes and lost sound", then, after removing one,
"frodhes knobs seem not wired, I can not change the sounds, not even TUNE"). One app a process: the host may hold
several instances of the plugin (two slots, or a re-pick, which creates the new instance before it destroys the old),
and the newest one owns the app (move_plugin.c). Through the plugin (dsp.so, move/tests/test_bridge.c script, whose
"new" / "use I" / "destroy I" / "renderall N" are the host's slots):
  - with two, the slot that does not own the app renders silence, ignores MIDI and writes, and says so where the
    Move shows a sound's name (preset_name: "ONE SLOT ONLY");
  - when either goes (the newest or the oldest), the one left plays, and its knobs (set_param) change the sound;
  - the re-pick (create the new, then destroy the old) plays on, its knobs too."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_sustain as tms  # noqa: E402

EXT = 2


def db(ms):
    return 10 * math.log10(float(ms) / 32768 ** 2) if float(ms) > 0 else -240.0


def hit(n=60):
    """a note on the current instance: its level while held (every slot rendered), then let go"""
    return ["midi %d %d %d 110" % (EXT, 0x90, n), "renderall 4", "rms 20", "midi %d %d %d 0" % (EXT, 0x80, n),
            "renderall 300"]


def knobs():
    """the survivor's knobs: LEVEL down to 0 quietens a note, back to 64 and TUNE +50 read back"""
    return (["set p_level 0", "renderall 20"] + hit(62) + ["set p_level 64", "set s_tune 50", "renderall 20",
                                                          "get p_level", "get s_tune", "get preset_name"] + hit(62)
            + ["set s_tune 0", "renderall 20"])


SCRIPT = (["get preset_name"] + hit()                       # 0 one Frodhes: it plays
          + ["new", "renderall 20", "get preset_name"] + hit()              # 1 the new slot plays (it owns the app)
          + ["use 0", "get preset_name", "get p_level", "set p_level 0", "renderall 10"] + hit()   # 2 the old: silent, ignored
          + ["use 1", "get p_level"]
          + ["destroy 1", "use 0", "renderall 20", "get preset_name"] + hit() + knobs()   # 3 the newest goes
          + ["new", "renderall 20", "destroy 0", "use 2", "renderall 20", "get preset_name"] + hit() + knobs()
          #   4 the re-pick: create the new (2), destroy the old (0)
          + ["new", "renderall 20", "use 2", "destroy 3", "renderall 20", "get preset_name"] + hit() + knobs())
#   5 a second slot again, and that newest one goes: the older survivor (2) plays and its knobs work


@pytest.fixture(scope="module")
def out():
    return tms.run(SCRIPT)


def test_one_app_many_slots(out):
    rms = [db(v) for k, v in out if k == "rms"]
    names = [v for k, v in out if k == "preset_name"]
    gets = [(k, v) for k, v in out if k in ("p_level", "s_tune")]
    assert len(rms) == 12 and len(names) == 9, out
    assert rms[0] > -40, "one Frodhes plays (%.1f dBFS)" % rms[0]
    assert rms[1] > -40, "the new slot plays: it owns the app (%.1f)" % rms[1]
    assert rms[2] < -100, "the old slot is silent (%.1f)" % rms[2]
    assert names[0] != "ONE SLOT ONLY" and names[1] == names[0]
    assert names[2] == "ONE SLOT ONLY", "the slot that does not play says so: %r" % names[2]
    assert gets[0] == gets[1], "the silent slot's write is not applied (%r, %r)" % (gets[0], gets[1])
    for at, what in ((3, "the newest goes"), (6, "the re-pick"), (9, "a second slot goes again")):
        n0, quiet, loud = rms[at:at + 3]
        assert n0 > -40, "%s: the one left plays (%.1f)" % (what, n0)
        assert quiet < loud - 6, "%s: LEVEL 0 quietens it (%.1f against %.1f)" % (what, quiet, loud)
        assert loud > -40
    for i in range(3):
        assert gets[2 + 2 * i:4 + 2 * i] == [("p_level", "64"), ("s_tune", "50")], gets
    assert all(n != "ONE SLOT ONLY" for n in names[3:]), "the one left names its sound: %r" % names
