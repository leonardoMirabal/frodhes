# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py marimba_decay's pedal row (pedal_order_ok): the jack's half vs up is not judged at the jack's
decay ceiling (its MUTE 127 reading - 30 dB/s), and nothing else is loosened: an order inversion on USB, or on the jack
under its ceiling, still fails."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

INF = 1e9                                       # (USB: no ceiling)


def test_the_jack_at_its_ceiling_is_not_judged_on_half_vs_up():
    assert h.pedal_order_ok([702, 702, 46, 719])            # (the 0.9.12 two-core T2, key 48, the jack)
    assert h.pedal_order_ok([775, 790, 112, 775])           # (half over up, both at the cap)


def test_usb_is_judged_in_full():
    assert h.pedal_order_ok([845, 759, 46, INF])            # (the same take, USB)
    assert not h.pedal_order_ok([845, 845, 46, INF])        # a tie
    assert not h.pedal_order_ok([759, 845, 46, INF])        # half over up: a real inversion
    assert not h.pedal_order_ok([845, 40, 46, INF])         # down over half
    assert not h.pedal_order_ok([130, 100, 46, INF])        # up / down under 3x


def test_the_jack_under_its_ceiling_is_judged_in_full():
    assert h.pedal_order_ok([600, 500, 46, 719])
    assert not h.pedal_order_ok([600, 650, 46, 719])        # half over up, the jack well under its cap
    assert not h.pedal_order_ok([702, 702, 46, 900])        # a tie under the cap
    assert not h.pedal_order_ok([702, 30, 46, 719])         # down < half still judged at the cap
