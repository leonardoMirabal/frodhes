# SPDX-License-Identifier: GPL-3.0-only
"""The sustain on the Move, through the plugin (dsp.so, move/tests/test_bridge.c script). The user, 2026-10-28 18:25:
"we just need a toggle option, sustain pedal on or off". SUS (s_sustain, the first page) is the toggle: ON holds the
notes as a pedal held down, OFF lets everything go and always wins (the latch, a button hold, a MIDI pedal held
down). The Capture button (s_susbtn, screen.js; no page's knob) flips SUS on its press, its release does nothing. A
touch flips it only when dev_sus_touch names that control. A MIDI pedal (CC 64) works as on the FM-1, half pedal
included, and its let-up releases SUS too (frodhes.c midi_cc: the FM-1's latch rule, unchanged).

The bug this replaces (0.9.8 candidate): "SUS can not be disabled: set OFF, it goes back to ON". PEDAL was a knob
cell whose chain_params entry (an enum UP / DOWN) overrode its canvas item in Schwung's merge, so a click on it
flipped s_susbtn to DOWN and left it there (Schwung's page_controller.mjs onClick, two options: the click sets it);
the plugin took DOWN as the REC button held, and SUS read held || latched: ON whatever SUS was set to. Step 3 below
is that sequence."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_params as tmp  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import dry_dist  # noqa: E402

BLK_MS = 128 * 1000 / 44100            # one render_block
HOLD = int(600 / BLK_MS)               # 600 ms
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


def pedal(v):
    return "midi %d %d 64 %d" % (EXT, 0xB0, v)


def touch(n, on):
    return "midi %d %d %d %d" % (TOUCH, 0x90 if on else 0x80, n, 127 if on else 0)


def struck():
    """a note struck and let go: its level while held, then 200 ms after key-up"""
    return [note(), "render 10", "rms 10", note(on=False), "render 200", "rms 20"]


# each step's reads in order: "get s_sustain" lines, "get dev_sus" lines, "rms" lines
SCRIPT = (
    # the voicing at its defaults (dry_dist.TYPE_BASE), not F01's: v2's F01 rings longer
    ["midi %d %d %d %d" % (EXT, 0xB0, c, v) for c, v in dry_dist.TYPE_BASE] + ["render 5"] +
    # 1 SUS ON holds, SUS OFF lets go
    ["set s_sustain 1", "render 5", "get s_sustain"] + struck()
    + ["set s_sustain 0", "render 5", "get s_sustain", "get dev_sus", "render 400", "rms 20"]
    # 2 Capture: a press flips SUS on, its release does nothing, the next press flips it off
    + ["set s_susbtn 1", "render 5", "set s_susbtn 0", "render %d" % HOLD, "get s_sustain"] + struck()
    + ["set s_susbtn 1", "render 5", "get s_sustain", "set s_susbtn 0", "render 400", "rms 20"]
    # 3 the 0.9.8 candidate's bug: s_susbtn left at 1 (the PEDAL cell's click), then SUS OFF
    + ["set s_susbtn 1", "render %d" % HOLD, "get s_sustain", "set s_sustain 0", "render 5", "get s_sustain",
       "get dev_sus", "render %d" % HOLD, "get s_sustain"] + struck()
    # 4 SUS OFF wins over a MIDI pedal held down
    + [pedal(127), "render 5"] + struck() + ["set s_sustain 0", "render 5", "get dev_sus", "render 400", "rms 20"]
    # 5 a MIDI pedal let up releases SUS (the FM-1's latch rule)
    + ["set s_sustain 1", "render 5", pedal(127), "render 5", pedal(0), "render 5", "get s_sustain"]
    # 6 touches: nothing by default; the chosen touch flips SUS on its press; another knob's touch does nothing
    + [touch(9, True), "render 5", touch(9, False), "render 5", "get s_sustain",
       "set dev_sus_touch 9", touch(9, True), "render 5", touch(9, False), "render 5", "get s_sustain",
       touch(3, True), "render 5", touch(3, False), "render 5", "get s_sustain",
       touch(9, True), "render 5", touch(9, False), "render 5", "get s_sustain", "get dev_sus"]
)


@pytest.fixture(scope="module")
def out():
    return run(SCRIPT)


def test_sus_is_a_toggle_and_off_always_wins(out):
    s = [v for k, v in out if k == "s_sustain"]
    d = [sus(v) for k, v in out if k == "dev_sus"]
    rms = [db(v) for k, v in out if k == "rms"]
    assert len(s) == 12 and len(d) == 4 and len(rms) == 11, out
    # 1
    assert s[0] == "1" and s[1] == "0"
    assert rms[1] > -60, "SUS ON: the note rings after key-up (%.1f dBFS)" % rms[1]
    assert rms[1] <= rms[0] + 0.5, "never louder after key-up (%.1f after, %.1f held)" % (rms[1], rms[0])
    assert d[0]["held"] == "0" and d[0]["latched"] == "0"
    assert rms[2] < rms[1] - 30, "SUS OFF damps it (%.1f against %.1f)" % (rms[2], rms[1])
    # 2
    assert s[2] == "1", "a Capture press turns SUS on; its release leaves it on"
    assert rms[4] > -60 and rms[4] <= rms[3] + 0.5
    assert s[3] == "0", "the next press turns it off"
    assert rms[5] < rms[4] - 30
    # 3 (the bug)
    assert s[4] == "1"
    assert s[5] == "0" and s[6] == "0", "SUS OFF wins: it reads OFF and stays OFF (it went back to ON)"
    assert d[1]["held"] == "0" and d[1]["latched"] == "0"
    assert rms[7] < -70, "SUS OFF: nothing rings after key-up (%.1f dBFS)" % rms[7]
    # 4
    assert rms[9] > -60, "a MIDI pedal down holds (%.1f)" % rms[9]
    assert d[2]["midi"] == "0" and d[2]["latched"] == "0"
    assert rms[10] < rms[9] - 30, "SUS OFF releases the MIDI pedal's hold too"
    # 5
    assert s[7] == "0", "a MIDI pedal let up releases SUS (as on the FM-1)"
    # 6
    assert s[8] == "0", "a touch does nothing by default"
    assert s[9] == "1", "the chosen touch flips SUS"
    assert s[10] == "1", "another knob's touch does nothing"
    assert s[11] == "0" and d[3]["held"] == "0", "the chosen touch again: off"


def test_capture_key_is_on_no_page():
    """s_susbtn is the Capture button's channel only: a C row, in no level and not in chain_params (where Schwung's
    merge would make it a knob of its own); screen.js sends it on a press only"""
    gen = tmp.gen
    rows = {k: kind for k, _, _, kind, _, _ in tmp.ROWS}
    assert rows["s_susbtn"] == "MK_BTN" and rows["s_sustain"] == "MK_LATCH"
    hier = __import__("json").load(open(os.path.join(gen.OUT, "ui_hierarchy.json")))["levels"]
    chain = __import__("json").load(open(os.path.join(gen.OUT, "chain_params.json")))
    for name, lvl in hier.items():
        assert "s_susbtn" not in lvl["knobs"], name
        assert not any(p.get("key") == "s_susbtn" for p in lvl["params"]), name
    assert not any(c["key"] == "s_susbtn" for c in chain)
    assert "s_sustain" in hier["root"]["knobs"]
    js = open(os.path.join(gen.OUT, "screen.js")).read()
    assert 'ctx.setParam("s_susbtn", "1")' in js and 'ctx.setParam("s_susbtn", "0")' not in js
