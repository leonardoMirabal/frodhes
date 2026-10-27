# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_midi.py: the device tools' one MIDI channel (8 by default, FRODHES_MIDI_CH=1..16), and that every tool
that opens the FM-1's MIDI output sends through it (or picks its channels on purpose)."""
import glob
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fm1_midi  # noqa: E402

mido = pytest.importorskip("mido")


class Port:
    def __init__(self):
        self.sent, self.name = [], "Frodhes"

    def send(self, m):
        self.sent.append(m)


def test_channel_default_and_env():
    assert fm1_midi.channel({}) == 8
    assert fm1_midi.channel({"FRODHES_MIDI_CH": "1"}) == 1
    assert fm1_midi.channel({"FRODHES_MIDI_CH": " 16 "}) == 16
    for bad in ("0", "17", "x"):
        with pytest.raises(SystemExit):
            fm1_midi.channel({"FRODHES_MIDI_CH": bad})


def test_channel_out_moves_channel_messages_only():
    p = Port()
    o = fm1_midi.ChannelOut(p, 8)
    M = mido.Message
    for m in (M("note_on", note=60, velocity=100), M("note_off", note=60), M("control_change", control=116, value=127),
              M("program_change", program=3), M("pitchwheel", pitch=100), M("control_change", control=1, channel=4)):
        o.send(m)
    assert [m.channel for m in p.sent] == [7] * 6
    for m in (M("clock"), M("start"), M("stop"), M("sysex", data=[1, 2])):
        o.send(m)
    assert [m.type for m in p.sent[6:]] == ["clock", "start", "stop", "sysex"]
    assert o.raw is p and o.name == "Frodhes"
    assert fm1_midi.ChannelOut(o, 3).raw is p           # never wrapped twice


def test_channel_out_env(monkeypatch):
    monkeypatch.setenv("FRODHES_MIDI_CH", "1")
    p = Port()
    fm1_midi.ChannelOut(p).send(mido.Message("note_on", note=60))
    assert p.sent[0].channel == 0


def test_rotate():
    assert fm1_midi.rotate([0x90, 60, 100], 8) == [0x97, 60, 100]
    assert fm1_midi.rotate([0xB9, 64, 0], 8) == [0xB0, 64, 0]        # channel 10 -> 1 (mod 16)
    assert fm1_midi.rotate([0xF8], 8) == [0xF8]
    assert fm1_midi.rotate([0xF2, 1, 2], 8) == [0xF2, 1, 2]
    assert fm1_midi.rotate([0x90, 60, 100], 1) == [0x90, 60, 100]
    # the 16-channel loops (the monkey's panic and probe) stay all 16 channels
    assert sorted(fm1_midi.rotate([0xB0 | c, 123, 0], 8)[0] for c in range(16)) == [0xB0 | c for c in range(16)]


# the output ports opened on purpose without the channel: the TRS interface's (not the FM-1), the installer (SysEx)
RAW_OK = {"hil_clock.py": "the TRS interface's port", "hil_midi_parity.py": "the TRS interface's port",
          "fm1_install.py": "SysEx only"}


def test_every_fm1_output_goes_through_the_channel():
    bad = []
    for f in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py"))):
        src = open(f).read()
        for m in re.finditer(r".*open_output\(.*", src):
            line = m.group(0)
            if "ChannelOut(" not in line and os.path.basename(f) not in RAW_OK:
                bad.append(f"{os.path.basename(f)}: {line.strip()}")
    assert not bad, "an FM-1 output without fm1_midi.ChannelOut:\n" + "\n".join(bad)


def test_channel_finders_unwrap():
    """hil_clock / hil_midi_parity find the FM-1's channel themselves (a note on each): they use the port itself"""
    for f in ("hil_clock.py", "hil_midi_parity.py"):
        src = open(os.path.join(ROOT, "tools", f)).read()
        assert re.search(r"rig = H\.Rig\(\)\n\s*rig\.out = getattr\(rig\.out, \"raw\", rig\.out\)", src), f
