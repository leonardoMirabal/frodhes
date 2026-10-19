# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py's MARIMBA measures on synthetic takes (no FM-1), each failing on the code before its fix:
the jack's onsets from USB's (align() alone read the jack 2-15 ms late under its hiss, and the keys take's level
span / step / tuning off), a roll is not a slipped stream, the MUTE drop depth stays over the jack's floor, the
marimba_fx roll is the same in every take, and the VIBES suites keep their stimuli."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

FS = h.FS


def note(n, f, t0, tau, amp=0.1, rise=0.004):
    t = np.arange(n) / FS - t0
    env = np.where(t >= 0, np.minimum(1.0, np.maximum(t, 0) / rise) * np.exp(-np.maximum(t, 0) / tau), 0.0)
    return amp * env * np.sin(2 * np.pi * f * np.maximum(t, 0))


def keys_take(keys=tuple(range(36, 97)), gap=1.4, lag=0.0032, drift=-1.4e-4, gain=10 ** (-12.65 / 20),
              hiss=10 ** (-92 / 20)):
    """the keys take (C2..C7, a strike every 1.4 s): USB (exact zero between the notes, the gate) and the jack (USB
    x gain, inverted, lag s later, over its hiss, on a clock drift ppm off the schedule's: the FM-1's 2026-10-19
    take had the jack's latency -140 ppm, -12 ms over its 86 s, and align() predicts every onset from the first
    note's latency, so its +-8 ms search fell inside the late notes' attacks: they read 2-15 ms late)"""
    sched = [0.2 + gap * k for k in range(len(keys))]
    n = int((sched[-1] + 1.5) * FS)
    u, j = np.zeros(n), np.zeros(n)
    for s, k in zip(sched, keys):
        f = 440 * 2 ** ((k - 69) / 12)
        tau = 0.1                                       # (gone, under 1 LSB, before the next strike)
        u += note(n, f, s + 0.0056, tau)
        j += note(n, f, (s + 0.0056) * (1 + drift) + lag, tau)
    u[np.abs(u) < 1 / 32768] = 0.0
    j = -gain * j + hiss * np.random.default_rng(3).standard_normal(n)
    want = [int(round(((s + 0.0056) * (1 + drift) + lag) * FS)) - int(round((s + 0.0056) * FS)) for s in sched]
    return u, j, sched, keys, want                     # (want: each strike's jack onset re its USB onset, samples)


def test_jack_onsets_from_usb():
    u, j, sched, keys, want = keys_take()
    ou = h.align(u, sched)[0]
    oj0 = h.align(j, sched)[0]
    def err(oj):
        return max(abs((b - a) - w) for a, b, w in zip(ou, oj, want))
    assert err(oj0) > 5 * FS // 1000                                          # align() alone: late (the bug)
    oj = h.jack_by_usb(u, ou, j, oj0)
    assert err(oj) <= FS // 1000, err(oj)
    # the level of each key: the jack = USB x gain on every key once the onsets are right
    d = [h.lu(h.cut(j, b, 0, 1.0)) - h.lu(h.cut(u, a, 0, 1.0)) for a, b in zip(ou, oj)]
    assert np.ptp(d) <= 0.1, np.ptp(d)


def test_take_uses_usb_for_the_jack_on_marimba(monkeypatch):
    u, j, sched, keys, want = keys_take(keys=tuple(range(36, 97, 4)))
    ev = [e for s, k in zip(sched, keys) for e in ((s, "on", k, 100), (s + 0.9, "off", k, 0))]
    monkeypatch.setattr(h, "ENGINE", "marimba")
    T = h.Take("t", len(u) / FS, ev, np.stack([u, u], 1), j)
    assert max(abs((b - a) - w) for a, b, w in zip(T.on("usb"), T.on("jack"), want)) <= FS // 1000
    monkeypatch.setattr(h, "ENGINE", "vibes")                                 # VIBES: align() as before
    T2 = h.Take("t", len(u) / FS, ev, np.stack([u, u], 1), j)
    assert T2.on("jack") == h.align(j, T2.sched)[0]


def test_tuning_window_ends_at_the_hiss():
    u, j, sched, keys, want = keys_take(keys=tuple(range(36, 97, 6)) + (96,))
    ou = h.align(u, sched)[0]
    oj = h.jack_by_usb(u, ou, j, h.align(j, sched)[0])
    fl = j[:int(0.19 * FS)]
    for a, b, k in zip(ou, oj, keys):
        f = 440 * 2 ** ((k - 69) / 12)
        end = h.m_audible_s(j, b, fl)
        c = 1200 * np.log2(h.f0_of(h.cut(j, b, 0.05, end), f) / f)
        assert abs(c) <= 1.0, (k, end, c)
    assert h.m_audible_s(u, ou[0], u[:0]) == 0.6                             # USB: no floor, the whole window


def test_a_roll_is_not_a_slipped_stream():
    """re-strikes of a ringing bar need not rise 3 dB in 1 ms frames: align() searched each one +-0.35 s for a 15 dB
    rise, took a later, louder strike for it, and the FM-1's 12 Hz roll takes read 'the USB stream slipped' 217-338
    ms. Here the roll's re-strikes are buried in the ring and a loud strike follows 0.3 s later"""
    sched = [0.2 + k / 12 for k in range(4)]
    n = int(2.0 * FS)
    m = note(n, 130.8, 0.206, tau=2.0, amp=0.05) + note(n, 196.0, 0.55, tau=0.3, amp=0.9)
    T = h.Take("roll", 2.0, [(s, "on", 48, 100) for s in sched], np.stack([m, m], 1), None)
    assert abs(T.on("usb")[1] - int(0.289 * FS)) <= 10 * FS // 1000            # predicted, not the strike at 0.55 s
    assert h.max_jump_ms(T, "usb") <= h.SLIP_MS


def test_drop_depth_over_the_floor():
    n = int(2.0 * FS)
    hiss = 10 ** (-92 / 20) * np.random.default_rng(1).standard_normal(n)
    x = note(n, 523.3, 0.0, tau=0.078, amp=10 ** (-42 / 20) * np.exp(0.2 / 0.078))   # C5: 111 dB/s, -45 dB at 0.2 s
    fl = hiss[:FS // 5]
    d = h.m_drop_db([(x + hiss, 0.2, fl)])
    assert 10.0 <= d < 30.0                                                   # the C5 case: less than 30 dB
    assert h.m_drop_db([(x, 0.2, np.zeros(FS))]) == 30.0                       # USB: no floor, 30 dB


def test_drop_rate_slope_shift_invariant():
    """an exponential decay: the envelope's slope reads the same rate whatever 1 ms its onset falls on"""
    n = int(2.0 * FS)
    rates = []
    for sh in range(0, 6):
        x = note(n, 523.3, sh / 1000, tau=0.078)
        rates.append(h.m_drop_rate(x, 0.2, 30.0, fit=True))
    assert np.ptp(rates) / np.mean(rates) <= 0.005, rates


def test_marimba_fx_roll_is_fixed():
    a = h.m_fx_roll(h.FXCH, 80, 0.1, 5.6)
    assert a == h.m_fx_roll(h.FXCH, 80, 0.1, 5.6)                             # the same events in every take
    ons = [e for e in a if e[1] == "on"]
    assert {e[2] for e in ons} == set(h.FXCH)
    assert max(e[0] for e in a) <= 5.6 and min(e[0] for e in ons) == 0.1
    per = len([e for e in ons if e[2] == h.FXCH[0]]) / 5.5
    assert abs(per - h.M_FX_RATE) <= 0.5


def test_vibes_stimuli_unchanged():
    """the VIBES suites' stimuli are the module's (marimba_fx passes its own)"""
    assert h.ST_CH == (3.6, h.chord(h.FXCH, 108, 0.1, 2.1))
    assert h.ST_LONG == (6.0, h.chord(h.FXCH, 108, 0.1, 5.6))
    assert h.ST_NOTE == (2.0, h.chord((69,), 108, 0.1, 1.6))


class _V:
    want_usb = True

    @staticmethod
    def sources():
        return ["usb", "jack"]


def test_known_rows_are_reported_known():
    """a failing marimba_* row that matches tools/marimba_scan.py KNOWN / KNOWN_CARDS (m_known) is KNOWN with its
    reason, not FAIL, and not counted as failed; the same row elsewhere (a VIBES suite) or one that does not match
    (another key) still fails; a USB-jack mismatch on a KNOWN row still flags it"""
    import marimba_scan as ms
    S = h.VSuite("marimba_vel", _V())
    S.check("C4 MED: v40 -> v117 brighter", "attack centroid 0..50 ms (flat start, VA-M5)", {"usb": 1.04, "jack": 1.03},
            lambda v: v >= 1.4, ">= 1.4x")
    S.check("WEIGHT light brighter", "key 84 v100", {"usb": 1.02, "jack": 1.02}, lambda v: v >= 1.05, ">= 1.05")
    S.check("WEIGHT light brighter", "key 60 v100", {"usb": 1.02, "jack": 1.02}, lambda v: v >= 1.05, ">= 1.05")
    S.check("pumping", "roll 8 Hz C4 v100", {"usb": 3.0, "jack": 9.0}, lambda v: v <= 1.5, "<= 1.5 LU", level=True)
    st = [r["status"] for r in S.rows]
    assert st == ["KNOWN", "KNOWN", "FAIL", "KNOWN MISMATCH"], st
    assert S.rows[0]["known"] == ms.KNOWN_CARDS["VELOCITY centroid"]
    assert S.rows[1]["known"] == ms.KNOWN_CARDS["WEIGHT C6 centroid"]
    assert [r["check"] for r in S.fails()] == ["WEIGHT light brighter", "pumping"]
    V = h.VSuite("vibes_vel", _V())
    V.check("C4 MED: v40 -> v117 brighter", "", {"usb": 1.04, "jack": 1.03}, lambda v: v >= 1.4, ">= 1.4x")
    assert V.rows[0]["status"] == "FAIL" and "known" not in V.rows[0]
