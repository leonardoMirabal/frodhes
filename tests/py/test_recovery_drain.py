# SPDX-License-Identifier: GPL-3.0-only
"""USB RESCUE (recovery.c) drops the MIDI ring every step: nothing plays there, and a full ring makes usb.c hold
EP1 OUT (NAK, ep1_take: fewer than EP1_ROOM slots free) so the installer's SysEx never arrives (the 0.9.10 RC soak
left the FM-1 in USB RESCUE answering nothing after 40 notes / CCs)."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def src(name):
    return open(os.path.join(ROOT, "firmware", "src", name)).read()


def test_recovery_step_drains_the_midi_ring():
    step = re.search(r"^static void recovery_step\(void\)\n\{(.*?)^\}\n", src("recovery.c"), re.S | re.M).group(1)
    assert re.search(r"while \(midi_get\(&st, &d1, &d2\)\)\s*(/\*.*?\*/\s*)?;", step, re.S), step


def test_ep1_holds_back_when_the_ring_is_full():
    usb = src("usb.c")                                  # (what the drain is for: still the app's back-pressure)
    assert re.search(r"if \(MQ - \(mi_w - mi_r\) < EP1_ROOM\)\n\s+return 0;", usb)
