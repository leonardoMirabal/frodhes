# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py's round-2 measures on synthetic takes (no FM-1): each finds what is there, and a control that
does nothing still fails (an echo that is not there, a stream that did not slip, a pre-delay of 0)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

FS = h.FS


def bar(n, f=440.0, rise=0.07, tau=0.05, off=0.15):
    """a vibraphone-like staccato note: it keeps rising for rise s (the tube), damped after off s"""
    t = np.arange(n) / FS
    env = np.minimum(1, t / rise) * np.where(t > off, np.exp(-(t - off) / tau), 1.0)
    return 0.2 * env * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 4.0 * f * t))


def test_echo_found_and_not_invented():
    n = int(3.2 * FS)
    d = bar(n)
    for ms in (20.0, 357.0, 1480.0):
        k = int(ms * FS / 1000)
        x = 0.7 * d.copy()
        x[k:] += 0.5 * d[:n - k]
        got = h.echo_ms(x, 0.7 * d, ms)
        assert abs(got - ms) <= max(3.0, 0.01 * ms), (ms, got)
        assert abs(h.echo_ms(x, 0.7 * d) - ms) <= max(3.0, 0.01 * ms) or ms <= 60
    assert not np.isfinite(h.echo_ms(0.7 * d, 0.7 * d))            # no echo: none found
    assert not np.isfinite(h.echo_ms(0.7 * d, 0.7 * d, 20.0))


def test_align_follows_a_slipped_stream():
    sched = [0.2, 2.7, 5.2]
    n = int(6.5 * FS)
    m = np.zeros(n)
    for s, slip in zip(sched, (0.0, 0.232, -0.025)):                 # round 1's VOICE takes on USB
        i = int((s + 0.07 + slip) * FS)
        m[i:i + int(1.2 * FS)] += bar(int(1.2 * FS), off=1.0)
    on, lat, lats = h.align(m, sched)
    want = [int((s + 0.07 + sl) * FS) for s, sl in zip(sched, (0.0, 0.232, -0.025))]
    assert all(abs(a - b) <= 2 * FS // 1000 for a, b in zip(on, want)), (on, want)
    steady = np.zeros(n)
    for s in sched:
        i = int((s + 0.07) * FS)
        steady[i:i + int(1.2 * FS)] += bar(int(1.2 * FS), off=1.0)
    on, lat, lats = h.align(steady, sched)
    assert max(lats) - min(lats) < 0.002                               # no slip: none reported


def test_a_residue_before_the_first_event_is_not_the_onset():
    sched = [0.1]
    n = int(1.0 * FS)
    m = np.zeros(n)
    m[:int(0.1 * FS)] = 0.01 * np.sin(np.arange(int(0.1 * FS)) * 0.1)  # the last take's echoes, -40 dB
    i = int(0.165 * FS)
    m[i:] += bar(n - i)
    on, lat, lats = h.align(m, sched)
    assert abs(on[0] - i) <= 2 * FS // 1000 and abs(lat - 0.065) < 0.003, (on, lat)


# ---- K4: rc-0.9.9h's flaky MARIMBA jack rows (private/BACKLOG-after-099.md)
def test_floor_sub():
    """the floor taken off a band's power, at most FLOOR_SUB_MAX_DB; none (USB's exact zeros) leaves it"""
    assert h.floor_sub(2.0, None) == 2.0 and h.floor_sub(2.0, 1.0) == 1.0
    assert abs(h.floor_sub(1.0, 1.0) - 10 ** (-h.FLOOR_SUB_MAX_DB / 10)) < 1e-12
    # WAH DEPTH 0 / 127 on the jack: 8 dB apart under the hiss (0.6 of DEPTH 0's band in each take, dry's too): read
    # raw 6.4 dB (under the 6 dB row's margin once the run's spread adds in); less the floor, the 8 dB that is there
    hiss, x0, x127, dry = 0.6, 1.0, 10 ** 0.8, 1.0
    raw = 10 * np.log10((x127 + hiss) / (dry + hiss)) - 10 * np.log10((x0 + hiss) / (dry + hiss))
    sub = (10 * np.log10(h.floor_sub(x127 + hiss, hiss) / h.floor_sub(dry + hiss, hiss)) -
           10 * np.log10(h.floor_sub(x0 + hiss, hiss) / h.floor_sub(dry + hiss, hiss)))
    assert raw < 6.5 and abs(sub - 8.0) < 1e-9, (raw, sub)


def test_noise_floor_flags():
    """MARIMBA NOISE contact's floor flag: rc-0.9.9h T2's key 48 (the jack -51 dB, USB -64, the hiss estimate a
    window low at -60: within 6 dB missed) is the floor by the jack against USB; USB never is (no hiss); a jack
    reading near USB's and over its hiss is compared"""
    de = 48
    t2 = {"usb": {"c": {de: -64.0}, "cfl": -240.0}, "jack": {"c": {de: -51.0}, "cfl": -60.0}}
    assert h.noise_floor_flags(t2, de) == {"usb": False, "jack": True}
    near = {"usb": {"c": {de: -40.0}, "cfl": -240.0}, "jack": {"c": {de: -41.0}, "cfl": -60.0}}
    assert h.noise_floor_flags(near, de) == {"usb": False, "jack": False}
    hiss = {"jack": {"c": {de: -52.0}, "cfl": -55.0}, "usb": None}
    assert h.noise_floor_flags(hiss, de) == {"jack": True, "usb": False}


def test_phaser_fdbk_row_judges_without_motion_and_says_why():
    """PHASER FDBK 0 vs 127: motion_f is NaN when no band stands 10 dB over the source's floor (the jack's hiss under a
    notched take); the rc-0.9.12 T2 read the jack 'no value' with USB -0.3 / 4.1 dB: one NaN voided the row. Each
    branch now judges on its own (the rule unchanged: the frame-by-frame branch must still pass), and a measure that
    raised says what it raised"""
    import hil_scan as hs
    V = hs.VRig("both", dry_run=True)
    S = hs.VSuite("vibes_fx", V)
    ok = lambda v: (hs.finite(v[0]) and v[0] >= 1.5) or hs.apart_of_spread(v[1], v[2])   # noqa: E731
    fmt = lambda v: (f"{v[0]:+.1f}" if hs.finite(v[0]) else "motion n/a") + f" / {v[1]:.1f} dB (spread {v[2]:.1f})"  # noqa: E731
    valid = lambda v: hs.finite(v[1:])                    # noqa: E731
    S.check("PHASER FDBK", "", {"usb": (-0.3, 4.1, 0.2), "jack": (float("nan"), 4.0, 0.9)}, ok, "", fmt=fmt, valid=valid)
    S.check("PHASER FDBK", "", {"usb": (-0.3, 4.1, 0.2), "jack": (float("nan"), 1.0, 0.9)}, ok, "", fmt=fmt, valid=valid)
    r1, r2 = S.rows
    assert r1["ok"] and r1["jack"].startswith("motion n/a / 4.0")
    assert not r2["ok"] and r2["src_ok"]["jack"] is False      # (the frame branch alone short of 2 dB: still a FAIL)
    vals = S.per(lambda q: 1 / 0 if q == "jack" else 1.0)
    S.check("raised", "", vals, lambda v: True, "")
    assert S.rows[-1]["jack"] == "no value (ZeroDivisionError: division by zero)"


@pytest.mark.parametrize("engine", ["marimba", "vibes"])
def test_lfo_trig_while_the_note_stands(monkeypatch, engine):
    """LFO TRIG KEY on the host's 4 A4 strikes (LP12, LFO 127, SINE, RATE de): KEY 1.00 and FREE far under the 0.8 bar
    (a key that does not restart the LFO fails), and MARIMBA's trace held to the frames where every strike stands (its
    A4 is 60 dB down by 0.4 s: the old 0.7 s window read the centroid of silence, the rc-0.9.12 T2 KEY 0.76 / 0.77)"""
    import hil_scan as hs
    monkeypatch.setattr(hs, "ENGINE", engine)
    P = hs.vp()
    lb = [("FL_TYPE", 1), ("FL_FREQ", 64), ("FL_RESO", 0), ("FL_LFO", 127), ("LFO_SHAPE", 0),
          ("LFO_RATE", P["LFO_RATE"]["de"]), ("LFO_SYNC", 0)]
    extra = [("RELEASE", 0)] if engine == "marimba" else []
    rt = [0.1, 1.47, 2.93, 4.31]
    st = (5.7, sum((hs.chord((69,), 108, s, s + 1.2) for s in rt), []))
    V = hs.VRig("usb", host=3)
    T = {}
    for x in (0, 1):
        V.reset(lb + [("LFO_RETRIG", x)] + extra, label=f"retrig {x}")
        T[x] = V.take(f"t-retrig-{x}", *st)
    key, free = hs.retrig_phase(T[1], "usb"), hs.retrig_phase(T[0], "usb")
    assert key >= 0.95 and free <= 0.5, (key, free)
    if engine == "marimba":                              # (the trace stops where the note has gone)
        assert np.isnan(hs.retrig_phase(T[1], "usb", least=0.5))
