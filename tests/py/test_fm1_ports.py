# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_ports.py on fake port and device lists: with two FM-1s on the computer the Frodhes one is picked in
either order; Felucca / FM-1 only as a fallback; a Textura unit never. hil_must.Dev and find_usb use it.
(The installer's two-unit refusal: tests/install_test.py two_units.)"""
import itertools
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fm1_ports as fp  # noqa: E402
import hil_must as m  # noqa: E402

OTHERS = ["Felucca", "AMB-1 FM-1", "Textura", "Textura FM-1", "M6", "IAC Driver Bus 1"]


@pytest.mark.parametrize("order", list(itertools.permutations(["Felucca", "Frodhes FM-1", "AMB-1 FM-1"])))
def test_frodhes_wins_in_any_order(order):
    assert fp.pick(order) == ["Frodhes FM-1"]
    assert fp.first(order) == "Frodhes FM-1"


def test_frodhes_wins_among_all_others():
    for k in range(len(OTHERS) + 1):
        names = OTHERS[:k] + ["Frodhes FM-1"] + OTHERS[k:]
        assert fp.pick(names) == ["Frodhes FM-1"]


def test_fallback_only_without_frodhes():
    assert fp.pick(["M6", "Felucca", "AMB-1 FM-1"]) == ["Felucca", "AMB-1 FM-1"]
    assert fp.first(["IAC Driver Bus 1", "FM-1"]) == "FM-1"
    assert fp.first(["IAC Driver Bus 1", "M6"]) is None


def test_textura_never():
    for n in ("Textura", "Textura FM-1", "TEXTURA fm-1 MIDI 1"):
        assert not fp.is_fm1(n)
        assert fp.pick([n]) == [] and fp.first([n, "M6"]) is None
    assert fp.pick(["Textura FM-1", "Felucca"]) == ["Felucca"]
    assert fp.is_fm1("Frodhes FM-1") and fp.is_fm1("Felucca") and not fp.is_fm1("M6")


def dev(name, ins=2):
    return {"name": name, "max_input_channels": ins}


@pytest.mark.parametrize("order", list(itertools.permutations(["AMB-1 FM-1", "Frodhes FM-1", "Textura FM-1"])))
def test_audio_devices(order):
    devs = [dev("MacBook Pro Microphone", 1), dev("M6", 6)] + [dev(n) for n in order]
    want = 2 + order.index("Frodhes FM-1")
    assert fp.devices(devs, 2) == [want]

    class SD:
        @staticmethod
        def query_devices():
            return devs
    assert m.find_usb(SD) == want


def test_audio_fallback_and_channels():
    assert fp.devices([dev("AMB-1 FM-1"), dev("Textura FM-1")], 2) == [0]
    assert fp.devices([dev("Frodhes FM-1", 0), dev("AMB-1 FM-1")], 2) == [1]   # an output-only entry is not a candidate
    assert fp.devices([dev("AMB-1 FM-1"), dev("Felucca")], 2, fp.PORT_KEYS[:2]) == [1]


@pytest.mark.parametrize("names", [["Felucca", "Frodhes FM-1", "Textura"], ["Textura", "Frodhes FM-1", "Felucca"]])
def test_dev_opens_only_frodhes(monkeypatch, names):
    opened = []

    class Port:
        def close(self):
            pass

    class Mido:
        @staticmethod
        def get_output_names():
            return names

        get_input_names = get_output_names

        @staticmethod
        def open_output(n):
            opened.append(n)
            return Port()

        open_input = open_output
    monkeypatch.setitem(sys.modules, "mido", Mido)
    m.Dev()
    assert opened == ["Frodhes FM-1", "Frodhes FM-1"]


def test_force_takes_only_the_named_unit():
    """--port / FM1_PORT (a loaned unit with its own USB name): only that name, never the Frodhes FM-1"""
    try:
        fp.force("Dualcore", devs=[])
        names = ["Frodhes FM-1", "Dualcore Test", "Textura FM-1", "M6"]
        assert fp.pick(names) == ["Dualcore Test"] and fp.first(names) == "Dualcore Test"
        assert fp.devices([dev("Frodhes FM-1"), dev("Dualcore Test")], 2) == [1]
        assert fp.is_fm1("Dualcore Test") and not fp.is_fm1("Frodhes FM-1")
        assert fp.pick(["Frodhes FM-1"]) == [] and os.environ.get("FM1_PORT") == "Dualcore"
    finally:
        fp.force(None)
    assert "FM1_PORT" not in os.environ and fp.pick(["Frodhes FM-1", "Dualcore Test"]) == ["Frodhes FM-1"]


IOREG = """+-o Root  <class IORegistryEntry>
  +-o AppleT8132USBXHCI@00000000  <class AppleT8132USBXHCI>
  | +-o Felucca Update@00100000  <class IOUSBHostDevice>
  |     {
  |       "USB Product Name" = "Felucca Update"
  |       "locationID" = 1048576
  |     }
  |   +-o Dualcore Test@20130000  <class IOUSBHostDevice>
  |       "USB Product Name" = "Dualcore Test"
  |       "locationID" = 538116096
  | +-o Frodhes FM-1@20140000  <class IOUSBHostDevice>
  |       "locationID" = 538181632
  |       "USB Product Name" = "Frodhes FM-1"
"""


def test_ioreg_and_match():
    devs = fp.parse_ioreg(IOREG)
    assert devs == [("Felucca Update", 0x00100000), ("Dualcore Test", 0x20130000), ("Frodhes FM-1", 0x20140000)]
    assert fp.usb_match("Dualcore", devs) == [("Dualcore Test", 0x20130000)]
    assert fp.usb_match("Frodhes FM-1", devs) == [("Frodhes FM-1", 0x20140000)]
    assert fp.parse_loc("0x00100000") == 1048576 and fp.parse_loc("1048576") == 1048576


def test_force_refuses_a_shared_name_and_a_wrong_location():
    two = [("Frodhes FM-1", 0x20130000), ("Frodhes FM-1", 0x00100000)]
    try:
        with pytest.raises(SystemExit, match="cannot be told apart"):
            fp.force("Frodhes FM-1", devs=two)
        one = [("Frodhes FM-1", 0x20130000), ("Dualcore Test", 0x00100000)]
        with pytest.raises(SystemExit, match="not there"):
            fp.force("Dualcore", 0x20130000, devs=one)
        assert fp.FORCED is None
        fp.force("Dualcore", 0x00100000, devs=one)
        assert fp.pick(["Frodhes FM-1", "Dualcore Test"]) == ["Dualcore Test"]
    finally:
        fp.force(None)


def test_ports_by_location():
    """2026-11-09: unit 2's CoreMIDI ports keep the old name "Dualcore Test" while its USB product is "Frodhes FM-1"
    (as the user's unit): the ports at a location by CoreMIDI's indices into python-rtmidi's lists (mido reorders
    its output list), the USB product there by ioreg"""
    ins = ["IAC Driver Bus 1", "M6", "Frodhes FM-1", "Dualcore Test"]
    outs = ["IAC Driver Bus 1", "MIDISPORT 2x4 Port 3", "M6", "Frodhes FM-1", "Dualcore Test"]
    at = ([(3, "Dualcore Test")], [(4, "Dualcore Test")])
    assert fp.midi_names_at(0x20130000, ins, outs, at) == (["Dualcore Test"], ["Dualcore Test"])
    assert fp.midi_names_at(0x20130000, ins, outs[:4], at) is None          # (a port went meanwhile)
    devs = [("Frodhes FM-1", 0x00100000), ("Frodhes FM-1", 0x20130000), ("Textura", 0x1), ("Sonic Test", 0x2),
            ("FM-1", 0x3), ("FM-1", 0x3)]
    assert fp.fm1_usb_at(0x20130000, devs) == "Frodhes FM-1"
    assert fp.fm1_usb_at(0x1, devs) is None and fp.fm1_usb_at(0x2, devs) is None
    assert fp.fm1_usb_at(0x3, devs) is None and fp.fm1_usb_at(0x4, devs) is None   # (two there / nothing there)
