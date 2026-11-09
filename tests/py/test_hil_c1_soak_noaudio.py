# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_c1_soak.py --no-audio --location: a second FM-1 soaked over USB-MIDI alone while another tool records the
first unit. With fakes for CoreMIDI's location map and python-rtmidi (no device, no port opened): the ports opened are
the unit's at that location and no other, sounddevice is never imported, a location with no FM-1 / another firmware /
two units is refused before any open; the stress rows' whole-ISR bar is 4800 us. 2026-11-09: CoreMIDI keeps unit 2's
old name ("Dualcore Test") while its USB product is "Frodhes FM-1": the unit is taken by its USB product (ioreg) and
its firmware's identity reply (FM-1_958), whatever its MIDI port is called; anything else is refused."""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def ident_reply(text):
    import fm1_install as I
    t = text.encode()
    return list(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + t + bytes(28 - len(t))) + b"\xF7")


@pytest.fixture
def fake(monkeypatch):
    pending = []
    opened, reply = [], {"id": "FM-1_958", "pending": pending}
    names = ["IAC", "Frodhes FM-1", "Other", "Dualcore Test", "Sonic Test"]

    class Base:
        def get_ports(self):
            return names

        def open_port(self, i):
            opened.append((type(self).__name__, i))

        def close_port(self):
            opened.append(("closed", type(self).__name__))

    class MidiIn(Base):
        def ignore_types(self, **kw):
            pass

        def get_message(self):
            return (pending.pop(0), 0.0) if pending else None

    class MidiOut(Base):
        def send_message(self, b):
            import fm1_install as I
            opened.append(("sent", tuple(b)))
            if bytes(b) == I.HS_QUERY and reply["id"]:
                pending.append(ident_reply(reply["id"]))

    monkeypatch.setitem(sys.modules, "rtmidi", types.SimpleNamespace(MidiIn=MidiIn, MidiOut=MidiOut))
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    import fm1_ports
    where = {0x20130000: ([(3, "Dualcore Test")], [(3, "Dualcore Test")]),     # (CoreMIDI's cached old name)
             0x00100000: ([(1, "Frodhes FM-1")], [(1, "Frodhes FM-1")]),
             0x1: ([(2, "Textura")], [(2, "Textura")]), 0x2: ([], []),
             0x4: ([(4, "Sonic Test")], [(4, "Sonic Test")])}
    usb = [("Frodhes FM-1", 0x20130000), ("Frodhes FM-1", 0x00100000), ("Textura", 0x1), ("Sonic Test", 0x4)]
    monkeypatch.setattr(fm1_ports, "midi_at", lambda loc: where.get(loc, ([], [])))
    monkeypatch.setattr(fm1_ports, "usb_devices", lambda: usb)
    import hil_c1_soak
    return hil_c1_soak, opened, reply


def test_status_opens_the_unit_at_the_location_only(fake, monkeypatch):
    soak, opened, reply = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.5) or t[0])
    assert soak.main() == 1                               # (no reply from the fake: "not an FR_C1 build?")
    assert ("MidiIn", 3) in opened and ("MidiOut", 3) in opened
    assert not [o for o in opened if o[0] in ("MidiIn", "MidiOut") and o[1] != 3], opened
    assert "sounddevice" not in sys.modules


def test_run_no_audio_never_imports_sounddevice(fake, monkeypatch):
    soak, opened, reply = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--row", "277", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.5) or t[0])
    with pytest.raises(SystemExit) as e:                  # (the fake answers nothing: "no core-1 reply")
        soak.main()
    assert "no core-1 reply" in str(e.value)
    assert "sounddevice" not in sys.modules and ("MidiOut", 3) in opened


@pytest.mark.parametrize("loc,why", [("0x1", "not a Frodhes"), ("0x2", "not one FM-1"), ("0x3", "not one FM-1"),
                                     ("0x4", "not a Frodhes")])
def test_refused_before_any_open(fake, monkeypatch, loc, why):
    soak, opened, reply = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", loc])
    with pytest.raises(SystemExit) as e:
        soak.main()
    assert why in str(e.value) and not opened


def test_stress_rows_bar(fake):
    soak, _, _ = fake
    assert soak.bars("277")["whole_us"] == soak.bars("276")["whole_us"] == 4800
    assert soak.bars("274")["whole_us"] == 4500 and soak.bars(None)["whole_us"] == 4500


def test_stale_name_taken_by_usb_product_and_identity(fake, monkeypatch, capsys):
    soak, opened, reply = fake
    import fm1_install as I
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.01) or t[0])
    assert soak.main() == 1                               # (identity ok; no CC 116 reply from the fake)
    assert ("sent", tuple(I.HS_QUERY)) in opened and ("closed", "MidiIn") not in opened
    assert "FM-1_958" in capsys.readouterr().out
    assert not [o for o in opened if o[0] in ("MidiIn", "MidiOut") and o[1] != 3], opened


@pytest.mark.parametrize("ident", ["SMK-37 Pro_958", "ota-FM-1_900", None])
def test_identity_must_say_fm1(fake, monkeypatch, ident):
    soak, opened, reply = fake
    reply["id"] = ident
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", "0x20130000"])
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(soak.time, "monotonic", lambda: t.__setitem__(0, t[0] + 0.01) or t[0])
    with pytest.raises(SystemExit) as e:
        soak.main()
    assert "not a running FM-1 firmware" in str(e.value)
    assert ("closed", "MidiIn") in opened and ("closed", "MidiOut") in opened
    assert not [o for o in opened if o[0] == "sent" and o[1][0] != 0xF0], opened     # (no note / CC sent)


def test_port_name_filter(fake, monkeypatch):
    soak, opened, reply = fake
    monkeypatch.setattr(sys, "argv", ["hil_c1_soak.py", "--status", "--no-audio", "--location", "0x20130000",
                                      "--port", "Frodhes"])
    with pytest.raises(SystemExit) as e:
        soak.main()
    assert "not 'Frodhes'" in str(e.value) and not opened


def test_input_reads_through_a_real_mido_parser(fake, monkeypatch):
    """2026-11-09 device run: In.iter_pending called Parser.iter_pending, which mido.Parser does not have (only
    feed / pending / get_message): the CC replies go through the real mido.Parser here, not a fake"""
    soak, opened, reply = fake
    import mido
    monkeypatch.setattr(soak.time, "sleep", lambda s: None)
    m, out, inp = soak.loc_ports(0x20130000)
    assert m is mido and isinstance(inp.p, mido.Parser)
    reply["pending"] += [[0xB0, 116, 30], [0xB0, 1, 64, 0xB0, 2]]      # (one CC, then one with a split tail)
    got = [(x.type, x.control, x.value) for x in inp.iter_pending()]
    assert got == [("control_change", 116, 30), ("control_change", 1, 64)]
    reply["pending"].append([0x7F])
    assert [(x.control, x.value) for x in inp.iter_pending()] == [(2, 127)]
    assert list(inp.iter_pending()) == []
