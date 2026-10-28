# SPDX-License-Identifier: GPL-3.0-only
"""M6-03, the Move-only mappings, through the plugin (dsp.so, move/tests/test_bridge.c script): channel pressure
(m_at_cc / m_at_lo / m_at_hi) and poly pressure (m_pa_cc / m_pa_lo / m_pa_hi, the most pressed held note) to a CC,
LO + (HI - LO) x pressure / 127. Every mapping is off (CC 0) by default and then sends nothing; a set mapping lands
as the same CC sent by a controller would; the settings live in the instance state (a state from before M6-03 loads
them off). Which mappings are on by default is the user's pick (U9): today none."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_params as tmp  # noqa: E402

EXT = 2                                  # plugin_api_v1.h MOVE_MIDI_SOURCE_EXTERNAL
CC, KEY = 18, "p_tw_depth"               # the target: a CC every engine reads (move/module/move_ui.h)


def run(lines):
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join(lines) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    out = [ln.split("\t") for ln in r.stdout.splitlines() if "\t" in ln]
    assert out, r.stdout[-2000:] + r.stderr[-2000:]
    return out


def midi(st, d1, d2=0):
    return "midi %d %d %d %d" % (EXT, st, d1, d2)


def direct(v):                           # the reference: the CC itself, as a controller sends it
    return [midi(0xB0, CC, v), "render 3", "get " + KEY]


def mapped(*ev):
    return [midi(*e) for e in ev] + ["render 3", "get " + KEY]


SCRIPT = (
    # 1 off by default: the settings, and pressure sends nothing (dev_map out=0); the target keeps its value
    ["get m_at_cc", "get m_at_lo", "get m_at_hi", "get m_pa_cc", "get m_pa_lo", "get m_pa_hi"]
    + direct(33) + mapped((0xD0, 127)) + mapped((0x90, 60, 100), (0xA0, 60, 127), (0x80, 60, 0)) + ["get dev_map"]
    # 2 channel pressure -> CC 18, LO 10, HI 90: 127 -> 90, 0 -> 10, 64 -> 50
    + ["set m_at_cc %d" % CC, "set m_at_lo 10", "set m_at_hi 90"]
    + mapped((0xD0, 127)) + direct(90) + mapped((0xD0, 0)) + direct(10) + mapped((0xD0, 64)) + direct(50)
    + ["set m_at_cc 0"] + mapped((0xD0, 127)) + ["get dev_map"]           # (off again: the target stays at 50)
    # 3 poly pressure -> CC 18 (0..127): the most pressed held note; a note let go ends its pressure
    + ["set m_pa_cc %d" % CC, midi(0x90, 60, 100), midi(0x90, 64, 100)]
    + mapped((0xA0, 60, 40), (0xA0, 64, 100)) + direct(100) + mapped((0x80, 64, 0)) + direct(40)
    + mapped((0x90, 60, 0)) + direct(0)
    # 4 a HI under LO inverts; a CC over 119 is refused (as any key out of its range: the setting stays)
    + ["set m_pa_lo 127", "set m_pa_hi 0", midi(0x90, 62, 100)] + mapped((0xA0, 62, 127)) + direct(0)
    + mapped((0xA0, 62, 0)) + direct(127) + [midi(0x80, 62, 0), "set m_at_cc 200", "get m_at_cc", "set m_at_cc 0"]
    # 5 the state keeps the settings; a state without them (before M6-03) loads them off
    + ["set m_at_cc 21", "set m_at_lo 5", "set m_at_hi 99", "render 2", "state_save", "set m_at_cc 0",
       "set m_at_lo 0", "set m_at_hi 127", "render 2", "state_load", "render 4", "get m_at_cc", "get m_at_lo",
       "get m_at_hi", "get m_pa_cc", "state_load nomap", "render 4", "get m_at_cc", "get m_at_lo", "get m_at_hi",
       "get m_pa_cc", "get m_pa_lo", "get m_pa_hi"]
)


@pytest.fixture(scope="module")
def out():
    return run(SCRIPT)


def test_maps_off_by_default(out):
    assert [v for _, v in out[:6]] == ["0", "0", "127", "0", "0", "127"]
    t = [v for k, v in out if k == KEY]
    assert t[0] == t[1] == t[2], t[:3]                                 # pressure moved nothing
    assert out[9] == ["dev_map", "out=0 drop=0 last=-1,-1"], out[9]


def test_channel_pressure_map(out):
    t = [v for k, v in out if k == KEY][3:10]
    assert t[0] == t[1] and t[2] == t[3] and t[4] == t[5] and t[6] == t[5], t     # 90, 10, 50; off: stays
    assert len({t[1], t[3], t[5]}) == 3, t


def test_poly_pressure_map(out):
    t = [v for k, v in out if k == KEY][10:20]
    assert t[0] == t[1] and t[2] == t[3] and t[4] == t[5], t            # 100, then 40, then 0
    assert t[6] == t[7] and t[8] == t[9], t                            # inverted: 127 -> 0, 0 -> 127
    assert len({t[1], t[3], t[5]}) == 3 and t[7] != t[9], t


def test_map_cc_range_and_state(out):
    g = [(k, v) for k, v in out if k.startswith("m_")][6:]
    assert g[0] == ("m_at_cc", "0"), g                                 # 200: refused
    assert [v for _, v in g[1:5]] == ["21", "5", "99", "18"], g        # the state brought them back
    assert [v for _, v in g[5:]] == ["0", "0", "127", "0", "0", "127"], g   # no "map": off
