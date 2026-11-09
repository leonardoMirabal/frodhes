# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_c1_soak.py --no-audio --location: a second FM-1 soaked over USB-MIDI alone while another tool records the
first unit. With fakes for CoreMIDI's location map and python-rtmidi (no device, no port opened): the ports opened are
the unit's at that location and no other, sounddevice is never imported, a location with no FM-1 / another firmware /
two units is refused before any open; the stress rows' whole-ISR bar is 4800 us."""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))


@pytest.fixture
def fake(monkeypatch):
    opened = []
    names = ["IAC", "Frodhes FM-1", "Other", "Frodhes FM-1"]

    class Base:
        def get_ports(self):
            return names

        def open_port(self, i):
            opened.append((type(self).__name__, i))

    class MidiIn(Base):
        def get_message(self):
            return None

    class MidiOut(Base):
        def send_message(self, b):
            opened.append(("sent", tuple(b)))

    monkeypatch.setitem(sys.modules, "rtmidi", types.SimpleNamespace(MidiIn=MidiIn, MidiOut=MidiOut))
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    import fm1_ports
    where = {0x20130000: ([(3, "Frodhes FM-1")], [(3, "Frodhes FM-1")]),
             0x00100000: ([(1, "Frodhes FM-1")], [(1, "Frodhes FM-1")]),
             0x1: ([(2, "Textura")], [(2, "Textura")]), 0x2: ([], [])}
    monkeypatch.setattr(fm1_ports, "midi_at", lambda loc: where.get(loc, ([], [])))
    import hil_c1_soak
    return hil_c1_soak, opened


def test_status_opens_the_unit_at_the_location_only(fake, monkeypatch):
    soak, opened = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.5) or t[0])
    assert soak.main() == 1                               # (no reply from the fake: "not an FR_C1 build?")
    assert ("MidiIn", 3) in opened and ("MidiOut", 3) in opened
    assert not [o for o in opened if o[0] in ("MidiIn", "MidiOut") and o[1] != 3], opened
    assert "sounddevice" not in sys.modules


def test_run_no_audio_never_imports_sounddevice(fake, monkeypatch):
    soak, opened = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--row", "277", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.5) or t[0])
    with pytest.raises(SystemExit) as e:                  # (the fake answers nothing: "no core-1 reply")
        soak.main()
    assert "no core-1 reply" in str(e.value)
    assert "sounddevice" not in sys.modules and ("MidiOut", 3) in opened


@pytest.mark.parametrize("loc,why", [("0x1", "not a Frodhes"), ("0x2", "not one FM-1"), ("0x3", "not one FM-1")])
def test_refused_before_any_open(fake, monkeypatch, loc, why):
    soak, opened = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", loc])
    with pytest.raises(SystemExit) as e:
        soak.main()
    assert why in str(e.value) and not opened


def test_stress_rows_bar(fake):
    soak, _ = fake
    assert soak.bars("277")["whole_us"] == soak.bars("276")["whole_us"] == 4800
    assert soak.bars("274")["whole_us"] == 4500 and soak.bars(None)["whole_us"] == 4500
