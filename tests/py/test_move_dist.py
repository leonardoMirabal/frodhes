# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_dist.py's prep (no device): the state it puts the Move in before a take is the host build's boot state.
The host renders the script once as it boots and once after prep's messages; the cells must agree. Before COMP_ON
(2026-11-15) prep left the compressor off, and every FXOFF cell read H1 1.45 dB under the host's."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
import dry_dist as dd  # noqa: E402
import move_dist as md  # noqa: E402
import move_hil as mh  # noqa: E402

STATUS = {"cc": 0xB0, "pc": 0xC0}


def prep_lines(t0_ms=2):
    """prep's messages one a ms before the script's first at 100 ms (the app's MIDI ring holds 64: more in one ms
    drop the rest, the compressor's switch among them)"""
    p = md.prep_ccs()
    assert t0_ms + len(p) < 100
    return ["%d midi 0x%02X %d %d" % (t0_ms + i, STATUS[k], a, b) for i, (k, a, b) in enumerate(p)]


def cells(tmp_path, tag, pre):
    x = dd.render(mh.host_binary(1), tag + ("-prep" if pre else ""), (prep_lines() if pre else []) + md.script(tag),
                  str(tmp_path))
    return dd.measure_wave(x, 0.0, dd.FS, keys=False)


def test_prep_is_the_host_boot_state_with_fx_off(tmp_path):
    for tag in ("FXOFF-ST77-B", "FXOFF-VIBE-B"):
        boot, prepped = cells(tmp_path, tag, False), cells(tmp_path, tag, True)
        d = md.h1_diffs(prepped, boot)
        assert d and max(abs(v) for _, v in d) <= 0.2, (tag, max(d, key=lambda x: abs(x[1])))


def test_prep_ends_with_the_compressor_on():
    p = md.prep_ccs()
    assert p[-1] == ("cc",) + md.COMP_ON and md.COMP_ON == (106, 127)


def test_h1_is_judged_over_the_16_bit_floor_only():
    """MARIMBA's key 93 is over its bars' range: H1 -134 .. -180 dB on both, rounding; a 2.1 dB difference there
    failed the run (2026-11-15)"""
    dev = {"93/64": {"h1": -178.18}, "81/64": {"h1": -86.53}, "69/64": {"h1": -56.0}}
    host = {"93/64": {"h1": -180.28}, "81/64": {"h1": -86.58}, "69/64": {"h1": -56.0}}
    d = dict(md.h1_diffs(dev, host))
    assert "93/64" not in d and abs(d["81/64"] - 0.05) < 1e-9 and d["69/64"] == 0.0
