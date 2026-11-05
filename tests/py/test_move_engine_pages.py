# SPDX-License-Identifier: GPL-3.0-only
"""The VOICE pages on Schwung's knob grid follow TYPE (the user, 2026-10-28: "the ui doesn't change when I change to
vibes or marimba, different parameter names"; the knobs turned the previous engine's parameters and nothing audible
changed). Schwung v1.8.1 re-plans a visible_if only when the grid writes the gate key or a pad press marks the gates
due; ui_engine moves because TYPE moved, so it was never re-read, and reloadIfChanged keeps a plan whose contract
bytes did not change. Now the plugin serves the sounding engine's hierarchy (its VOICE levels only, no visible_if) and
answers is_loading 1 for a moment after the engine changes: Schwung's knob grid re-reads ui_hierarchy on the 1 -> 0
edge and re-plans (shadow_ui_param_pages.mjs tickParamPages), and a preset pick re-reads it on its own
(armContractSettle). Through the plugin (dsp.so, move/tests/test_bridge.c script)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_params as tmp  # noqa: E402
import test_move_sustain as tms  # noqa: E402

VOICE = {0: "PIANO VOICE", 1: "VIBES VOICE", 2: "MARIMBA VOICE"}
ALL = {"PIANO VOICE", "VIBES VOICE", "VIBES MOTOR", "MARIMBA VOICE"}
OWN = {0: {"PIANO VOICE"}, 1: {"VIBES VOICE", "VIBES MOTOR"}, 2: {"MARIMBA VOICE"}}
TYPE_ENG = {0: 0, 1: 0, 4: 1, 5: 2}       # eng_pages.h TYPE_UI (ST77, SUIT ... VIBE, MRMB)


# Frodhes' own screen is the Move UI (move_bridge.h MB_OWN_UI): the plugin serves its pages as ui_pages and refuses
# ui_hierarchy, so that Schwung loads ui_chain.js; external surfaces read ui_pages
PAGES_KEY = "ui_pages"


def titles(h):
    return {lv["name"] for lv in json.loads(h)["levels"].values()}


def window(change):
    """a change, then is_loading twice and the pages, then is_loading after the window"""
    return change + ["render 4", "get is_loading", "get ui_engine", "get " + PAGES_KEY, "get is_loading", "sleep 700",
                     "get is_loading"]


def reads(out):
    """per window: (is_loading x2, ui_engine, the pages, is_loading after)"""
    it = iter(out[2:])
    res = []
    for k, v in it:
        a = v
        _, e = next(it)
        _, h = next(it)
        _, b = next(it)
        _, c = next(it)
        res.append((a, b, int(e), h, c))
    return res


@pytest.fixture(scope="module")
def presets():
    """the engine of every preset (program change / set preset)"""
    out = tms.run(sum((["set preset %d" % i, "render 4", "get ui_engine"] for i in range(35)), []))
    return [int(v) for k, v in out if k == "ui_engine"]


def test_the_served_pages_follow_the_engine(presets):
    other = next(i for i, e in enumerate(presets) if e != presets[0])
    script = ["get is_loading", "get " + PAGES_KEY]
    for t in (4, 5, 1, 0):                  # TYPE on its knob: VIBES, MARIMBA, another piano type, the first again
        script += window(["set p_type %d" % t])
    script += window(["set preset %d" % other])            # a preset of another engine (the preset page)
    script += window(["midi 2 %d 0 0" % 0xC0])             # a program change back to preset 0 (external MIDI)
    out = tms.run(script)
    assert out[0] == ["is_loading", "0"], "nothing moved: not loading"
    assert titles(out[1][1]) & ALL == OWN[presets[0]]
    last = presets[0]
    steps = [TYPE_ENG[t] for t in (4, 5, 1, 0)] + [presets[other], presets[0]]
    got = reads(out)
    assert len(got) == len(steps), out
    for (a, b, e, h, c), want in zip(got, steps):
        assert e == want
        assert titles(h) & ALL == OWN[want], (want, titles(h))
        assert '"visible_if"' not in h
        if want != last:
            assert a == "1" and b == "1", (want, "the engine moved: is_loading 1 (the grid's edge)")
        else:
            assert a == "0" and b == "0", (want, "the same engine: nothing to re-plan")
        assert c == "0", (want, "the 1 ends on its own: the 1 -> 0 edge re-reads the pages")
        last = want


def test_every_engine_text_is_the_full_hierarchy_cut_to_one_engine():
    gen = tmp.gen
    full = json.load(open(os.path.join(gen.OUT, "ui_hierarchy.json")))
    for e in range(3):
        h = gen.hier_for_engine(full, e)
        gated = {k for k, lv in full["levels"].items() if "visible_if" in lv}
        keep = {k for k in gated if full["levels"][k]["visible_if"]["equals"] == e}
        assert keep and set(h["levels"]) == (set(full["levels"]) - gated) | keep
        links = [p["level"] for lv in h["levels"].values() for p in lv["params"] if isinstance(p, dict) and "level" in p]
        assert set(links) <= set(h["levels"]), "no link to a level that is not served"
        for k in h["levels"]:
            assert h["levels"][k]["knobs"] == full["levels"][k]["knobs"]
