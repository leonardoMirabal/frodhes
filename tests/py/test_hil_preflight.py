# SPDX-License-Identifier: GPL-3.0-only
"""hil_scan's preflight (0.9.13): before any suite it sets the saved settings the scan assumes (RESPONSE A: CC 40 0 on
all 16 channels), reads them back acoustically and stops the scan when one is wrong (the rc's second T2 ran with
RESPONSE B, left by the device monkey). On the host rig (tools/hil_scan.py HostRig): A passes; a device left on B
(the preflight's CC 40 made to send B) stops it."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as hs  # noqa: E402


def test_response_a_passes():
    ok, lines = hs.preflight(hs.HostRig(1), host=True)
    assert ok, lines


def test_response_b_stops(monkeypatch):
    monkeypatch.setattr(hs, "PRE_SET", ((40, 127),))
    ok, lines = hs.preflight(hs.HostRig(1), host=True)
    assert not ok and "RESPONSE" in lines[-1], lines
    with pytest.raises(SystemExit):
        hs.preflight_or_stop(hs.HostRig(2), host=True)
