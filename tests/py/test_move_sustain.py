# SPDX-License-Identifier: GPL-3.0-only
"""M1-13b, sustain on the Move as on the FM-1, through the plugin (dsp.so, move/tests/test_bridge.c script): the
sustain control (s_susbtn, the PEDAL screen's claimed button, or a touch with dev_sus_touch) is the FM-1's REC
button: held = the pedal (the note rings after key-up, the control let go damps it), a tap latches (a second tap
releases), a MIDI pedal let up (CC 64 < 64) frees the latch, a held control keeps the pedal down whatever CC 64 says;
SUS (s_sustain) reads ON while the control holds; a touch plays nothing and is the control only on the chosen note."""
import math
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_params as tmp  # noqa: E402

BLK_MS = 128 * 1000 / 44100            # one render_block
TAP = int(60 / BLK_MS)                 # 60 ms: a tap (frodhes.c SUS_TAP_MS 250)
HOLD = int(600 / BLK_MS)               # 600 ms: a hold
EXT, TOUCH = 2, 5                      # plugin_api_v1.h MOVE_MIDI_SOURCE_EXTERNAL / _TOUCH


def run(lines):
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join(lines) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    out = [ln.split("\t") for ln in r.stdout.splitlines() if "\t" in ln]
    assert out, r.stdout[-2000:] + r.stderr[-2000:]
    return out


def db(ms):
    return 10 * math.log10(float(ms) / 32768 ** 2) if float(ms) > 0 else -240.0


def sus(v):
    return dict(kv.split("=") for kv in v.split())


def note(n=60, on=True):
    return "midi %d %d %d %d" % (EXT, 0x90 if on else 0x80, n, 100 if on else 0)


# the script: each step's reads are named by a "get dev_sus" / "rms" line in order
SCRIPT = [
    # 1 hold = the pedal
    note(), "render 20", "set s_susbtn 1", "render %d" % HOLD, "get dev_sus", "get s_sustain", "get s_susbtn",
    note(on=False), "render 200", "rms 20",                                        # (a) rings after key-up
    "set s_susbtn 0", "render 5", "get dev_sus", "render 400", "rms 20",           # (b) let go: damped, no latch
    # 2 a tap latches, a second tap releases
    "set s_susbtn 1", "render %d" % TAP, "set s_susbtn 0", "render 5", "get dev_sus", "get s_sustain",
    note(), "render 40", note(on=False), "render 200", "rms 20",                   # (c) rings: latched
    "set s_susbtn 1", "render %d" % TAP, "set s_susbtn 0", "render 5", "get dev_sus", "render 400", "rms 20",
    # 3 the latch and a MIDI pedal: the pedal let up frees the latch
    "set s_susbtn 1", "render %d" % TAP, "set s_susbtn 0", "render 5",
    "midi %d %d 64 127" % (EXT, 0xB0), "render 5", "midi %d %d 64 0" % (EXT, 0xB0), "render 5", "get dev_sus",
    # 4 the control held, CC 64 up: still held (the control is the pedal)
    "set s_susbtn 1", "render %d" % HOLD, "midi %d %d 64 0" % (EXT, 0xB0), note(), "render 40", note(on=False),
    "render 200", "rms 20", "get dev_sus", "set s_susbtn 0", "render 400", "get dev_sus",
    # 5 touches: nothing by default; the chosen touch is the control
    "midi %d %d 9 127" % (TOUCH, 0x90), "render %d" % HOLD, "get dev_sus", "midi %d %d 9 0" % (TOUCH, 0x80),
    "render 5", "set dev_sus_touch 9", "midi %d %d 9 127" % (TOUCH, 0x90), "render %d" % TAP,
    "midi %d %d 9 0" % (TOUCH, 0x80), "render 5", "get dev_sus",                    # a tap on the jog: latched
    "midi %d %d 3 127" % (TOUCH, 0x90), "render %d" % TAP, "midi %d %d 3 0" % (TOUCH, 0x80), "render 5",
    "get dev_sus",                                                                   # knob 4's touch: not the control
]


@pytest.fixture(scope="module")
def out():
    return run(SCRIPT)


def test_sustain_control_as_the_fm1(out):
    s = [sus(v) for k, v in out if k == "dev_sus"]
    rms = [db(v) for k, v in out if k == "rms"]
    reads = {i: v for i, (k, v) in enumerate(out)}
    assert len(s) == 10 and len(rms) == 5, out
    # 1 hold
    assert s[0]["held"] == "1" and s[0]["latched"] == "0"
    assert reads[1] == "1" and reads[2] == "1"                     # SUS reads ON while held; s_susbtn reads down
    assert rms[0] > -60, "a held control: the note rings after key-up (%.1f dBFS)" % rms[0]
    assert s[1]["held"] == "0" and s[1]["latched"] == "0", "a hold is not a tap: no latch"
    assert rms[1] < rms[0] - 30, "the control let go damps the note (%.1f against %.1f)" % (rms[1], rms[0])
    # 2 tap
    assert s[2]["latched"] == "1" and s[2]["held"] == "0"
    assert [v for k, v in out if k == "s_sustain"][1] == "1"
    assert rms[2] > -60, "latched: the note rings (%.1f)" % rms[2]
    assert s[3]["latched"] == "0" and rms[3] < rms[2] - 30, "a second tap releases"
    # 3 the MIDI pedal let up frees the latch
    assert s[4]["latched"] == "0" and s[4]["midi"] == "0"
    # 4 the control held overrides CC 64 up
    assert rms[4] > -60 and s[5]["held"] == "1"
    assert s[6]["held"] == "0" and s[6]["latched"] == "0"
    # 5 touches
    assert s[7]["held"] == "0" and s[7]["touch"] == "-1", "a touch is not the control by default"
    assert s[8]["latched"] == "1" and s[8]["touch"] == "9", "the chosen touch, tapped: latched"
    assert s[9]["latched"] == "1" and s[9]["held"] == "0", "another knob's touch does nothing"
    assert int(s[9]["down"]) == int(s[9]["up"]) == 6          # (5 presses by set_param + 1 chosen touch, each released)


def test_sustain_page_and_claim_match():
    """the PEDAL screen's key is on the first page and the generated script sends what the bridge reads"""
    gen = tmp.gen
    rows = {k: kind for k, _, _, kind, _, _ in tmp.ROWS}
    assert rows["s_susbtn"] == "MK_BTN" and rows["s_sustain"] == "MK_LATCH"
    js = open(os.path.join(gen.OUT, "sustain.js")).read()
    assert re.search(r"const SUS_CC = %d;" % gen.SUS_BTN_CC, js)
