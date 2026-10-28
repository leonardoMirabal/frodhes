# SPDX-License-Identifier: GPL-3.0-only
"""Several Frodhes on the Move (the user, 2026-10-28: "i load 2 frodhes and lost sound"; then no cap on the slots).
The module's dsp.so is a loader (move/move_loader.c) that gives every instance its own copy of the plugin
(engine.so), so every slot plays on its own. Through the shipping dsp.so (move/tests/test_bridge.c script, whose
"new" / "use I" / "destroy I" / "renderall N" are the host's slots):
  - two slots: both play, each names its own sound, a knob in one leaves the other alone (dev_inst: two copies);
  - when either goes (the newest or the oldest), the one left plays and its knobs work;
  - the re-pick (create the new, then destroy the old) plays on, its knobs too;
  - four slots at once (every slot Schwung has), each its own copy, all playing.
move/tests/test_multi.c checks the loader itself (dlclose, the update check, the shared store)."""
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
    """the current slot's knobs: LEVEL down to 0 quietens a note, back to 64 and TUNE +50 read back"""
    return (["set p_level 0", "renderall 20"] + hit(62) + ["set p_level 64", "set s_tune 50", "renderall 20",
                                                          "get p_level", "get s_tune", "get preset_name"] + hit(62)
            + ["set s_tune 0", "renderall 20"])


SCRIPT = (["get dev_inst", "get preset_name"] + hit()                        # 0 one Frodhes: it plays
          + ["new", "sleep 300", "renderall 400", "get dev_inst", "get preset_name"] + hit()   # 1 the second slot plays
          + ["use 0", "get preset_name", "set p_level 0", "renderall 20"] + hit()  # 2 the first, LEVEL 0
          + ["use 1", "get p_level", "use 0", "set p_level 64", "renderall 10"]   # (the second's LEVEL is its own)
          + ["destroy 1", "use 0", "renderall 20", "get preset_name"] + hit() + knobs()   # 3 the newest goes
          + ["new", "sleep 300", "renderall 400", "destroy 0", "use 2", "renderall 20", "get preset_name"] + hit() + knobs()
          #   4 the re-pick: create the new (2), destroy the old (0)
          + ["new", "sleep 300", "renderall 400", "use 2", "destroy 3", "renderall 20", "get preset_name"] + hit() + knobs()
          #   5 a second slot again, and that newest one goes: the older survivor (2) plays and its knobs work
          + ["new", "sleep 300", "renderall 400", "new", "sleep 300", "renderall 400", "new", "sleep 300", "renderall 400"]
          + sum((["use %d" % i, "get dev_inst"] + hit(60 + i) for i in (2, 4, 5, 6)), []))
#   6 four slots at once


@pytest.fixture(scope="module")
def out():
    return tms.run(SCRIPT)


def test_every_slot_its_own(out):
    rms = [db(v) for k, v in out if k == "rms"]
    names = [v for k, v in out if k == "preset_name"]
    insts = [v for k, v in out if k == "dev_inst"]
    gets = [(k, v) for k, v in out if k in ("p_level", "s_tune")]
    assert len(rms) == 16 and len(names) == 6 + 3, out
    assert insts[:2] == ["0", "1"], "two slots, two copies: %r" % insts
    assert rms[0] > -40, "one Frodhes plays (%.1f dBFS)" % rms[0]
    assert rms[1] > -40, "the second slot plays (%.1f)" % rms[1]
    assert rms[2] < rms[0] - 6, "the first slot plays, LEVEL 0 quietens it (%.1f against %.1f)" % (rms[2], rms[0])
    assert gets[0] != ("p_level", "0"), "the second slot's LEVEL is its own: %r" % (gets[0],)
    assert all(n != "ONE SLOT ONLY" for n in names), names
    for at, what in ((3, "the newest goes"), (6, "the re-pick"), (9, "a second slot goes again")):
        n0, quiet, loud = rms[at:at + 3]
        assert n0 > -40, "%s: the one left plays (%.1f)" % (what, n0)
        assert quiet < loud - 6, "%s: LEVEL 0 quietens it (%.1f against %.1f)" % (what, quiet, loud)
        assert loud > -40
    for i in range(3):
        assert gets[1 + 2 * i:3 + 2 * i] == [("p_level", "64"), ("s_tune", "50")], gets
    four = insts[2:]
    assert len(set(four)) == 4, "four slots, four copies: %r" % four
    assert all(r > -40 for r in rms[12:16]), "all four play: %r" % rms[12:16]
