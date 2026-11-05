# SPDX-License-Identifier: GPL-3.0-only
"""hil_scan's pinned tempo (2026-10-28): the presets takes run with a 120 BPM clock (Start then Stop first) and each
take starts on bar 1 of F11's 2BAR sweep; the host needs neither (it plays at its boot 120 BPM)"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan  # noqa: E402


class _Out:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append((time.monotonic(), m.type))


class _Msg:
    def __init__(self, t):
        self.type = t


class _Mido:
    Message = _Msg


def _rig():
    r = hil_scan.Rig.__new__(hil_scan.Rig)
    r.out = type("O", (), {})()
    r.out.raw = _Out()
    r.mido = _Mido
    return r


def test_host_clock_is_nothing():
    r = hil_scan.HostRig.__new__(hil_scan.HostRig)
    with r.clock(120):
        r.clock_align(192)                               # returns at once


def test_device_clock_starts_stops_then_pulses_at_120():
    r = _rig()
    with r.clock(120):
        time.sleep(0.3)
    kinds = [k for _, k in r.out.raw.sent]
    assert kinds[:2] == ["start", "stop"]
    ts = [t for t, k in r.out.raw.sent if k == "clock"]
    assert len(ts) > 40
    per = (ts[-1] - ts[0]) / (len(ts) - 1)
    assert abs(per - 60.0 / (120 * 24)) < 0.002
    assert r._clk is None


def test_align_lands_on_the_lap():
    r = _rig()
    with r.clock(120):
        r.clock_align(24)                                # one beat: 0.5 s at 120
        t0, per = r._clk
        ph = (time.monotonic() - t0) % (24 * per)
        assert min(ph, 24 * per - ph) < 0.03
