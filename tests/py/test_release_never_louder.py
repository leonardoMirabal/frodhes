# SPDX-License-Identifier: GPL-3.0-only
"""No louder after the key-up (private TEST-PLAN, release must-pass, 2026-10-25): letting go of a note must never make
it louder. 0.9.5's first test build gave a soft note (v15) a key-up damper thump 9-12 dB over the note itself (the
thump had a fixed level; real key velocity let the notes go soft under it): "I press a key and get 2 sounds".

The grid: every engine x TYPE (the four Rhodes with either RESPONSE, VIBES, MARIMBA) x NOISE (0, 48 the default, 127) x velocity (1, 15,
30, 60, 100, 127) x key (bass, mid, top), the note held 0.2 s and 2 s, the engine alone (CC 119 RAW), then:
- let go with the pedal up;
- the pedal down 100 ms before the key-up, then up 300 ms after it (the dampers land at the pedal-up): the key-up
  checked until the pedal-up, the pedal-up for 600 ms.
The rule, at the key-up and at the pedal-up: the level 0..600 ms after (10 ms RMS windows sliding at 1 ms, and the
peak) never exceeds the level of the 50 ms just before (or two periods of the note, if longer) by more than +0.5 dB,
and no new onset (a 10 ms window more than ONSET_DB over the window one lag earlier, lag = max(10 ms, one period of
the note), above the floor) appears after it. One render per engine x TYPE x NOISE x pedal x hold, the 18 notes in a
row.

The windowing (2026-11-08): a hard low note whose bark puts one pulse per period (G1: 20.4 ms) has 10 ms windows that
swing 6.6-9.9 dB inside one period while held, untouched (0.1 dB over a whole period). Non-overlapping 10 ms windows
then read the phase, not the level: on the S1a / S1B velocity-law prototypes the old rule failed 33 of the 72 cases
of an unfaulted build (72 / 77 events, onsets at key 31 and +1.4 dB "rises" that sliding windows show as -0.2 dB).
Sliding windows take the level at every phase, before and after alike; an onset compares a window with the one a
period earlier (the same point of the waveform), so the swing cancels and the 10 ms resolution stays. Measured on
main, S1a and S1b renders (every case; fault builds through the engine): 0 false positives, worst margins RMS +0.08
dB, peak +0.39, onset +1.57 (limits 0.5 / 0.5 / 3.0); every event the old rule caught on main's faults
(RH_THUMP_CAP=32767, the thump x2, the pedal thud x4, the thump uncapped and 40 / 80 ms late) still caught, plus 22
more. The two rejected alternatives: one period windows at a period's hop (s1b) missed late thumps at G1 (median
threshold -5.2 dB re the note against -10.4 here, 12 note-level misses against 0); 20 ms windows everywhere with the
onset over the louder of two windows before (s1a) hid onsets under the damper's decay on every key (a 40 ms late
thump: 138 events caught against 185 here). test_windowing_* below keep these cases."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

VELS = (1, 15, 30, 60, 100, 127)
TYPES = [("STAGE77", "80", 0), ("SUITCASE", "80", 32), ("FELT", "80", 64), ("DYNO", "80", 96),
         ("VIBES", "79", 0), ("MARIMBA", "79", 96)]
TYPES += [(n + "-LIGHT", c, v) for n, c, v in TYPES[:4]]   # 0.9.10: the Rhodes' second velocity response (CC 40 LIGHT)
KEYS = {"rhodes": (31, 60, 96), "bars": (55, 72, 88)}
NOISES = (0, 48, 127)
TOL_DB = 0.5
ONSET_DB = 3.0                                  # a new onset: one 10 ms window this much over the one before
FLOOR_DB = 30.0                                 # ... while within this of the level before (not the tail's rounding)
W = FS // 100                                   # 10 ms
H = FS // 1000                                  # the windows' hop: 1 ms
GAP_MS = 1400                                   # the release, then the next note


def slots(hold_ms, pedal):
    """(t_on, t_up, t_ped_up or None) per note, in VELS x keys order"""
    t, out = 300, []
    for _ in range(len(VELS) * 3):
        t_up = t + hold_ms
        out.append((t, t_up, t_up + 300 if pedal else None))
        t = t_up + (300 if pedal else 0) + GAP_MS
    return out


def render(ty, noise, pedal, hold_ms):
    name, ccn, ccv = ty
    keys = KEYS["bars" if ccn == "79" else "rhodes"]
    ev = ["40 midi 0xB0 119 127", f"42 midi 0xB0 {ccn} {ccv}", f"44 midi 0xB0 104 {noise}"]
    if name.endswith("-LIGHT"):
        ev.append("43 midi 0xB0 40 127")        # RESPONSE LIGHT (the light touch; FULL, the default, otherwise)
    if ccn == "79":
        ev.append("46 midi 0xB0 47 0")          # VIBES's motor off (its AM pulse is no key-up; MARIMBA has none)
    sl = slots(hold_ms, pedal)
    notes = [(v, k) for v in VELS for k in keys]
    for (t_on, t_up, t_pu), (v, k) in zip(sl, notes):
        ev.append(f"{t_on} midi 0x90 {k} {v}")
        if pedal:
            ev.append(f"{t_up - 100} midi 0xB0 64 127")
        ev.append(f"{t_up} midi 0x80 {k} 0")
        if pedal:
            ev.append(f"{t_pu} midi 0xB0 64 0")
    end = sl[-1][2 if pedal else 1] + GAP_MS
    r = run(f"nl-{name}-{noise}-{int(pedal)}-{hold_ms}", ev, end)
    return (r.L.astype(float) + r.R.astype(float)) / 2.0, sl, notes


def db(x):
    return 20 * np.log10(x / 32768.0 + 1e-12)


def rms10(x):
    n = len(x) // W
    return np.sqrt(np.mean(np.square(x[:n * W].reshape(n, W)), axis=1))


def lag(key):
    """the onset's lag (samples): one period of the note, at least 10 ms"""
    return max(W, int(round(FS / (440.0 * 2 ** ((key - 69) / 12.0)))))


def slide(x, a, n):
    """n 10 ms RMS windows starting at a, a + H, ... (H = 1 ms)"""
    c = np.concatenate([[0.0], np.cumsum(np.square(np.asarray(x, float)))])
    s = a + H * np.arange(n)
    return np.sqrt(np.maximum(c[s + W] - c[s], 0.0) / W)


def check_at(x, t_ms, span_ms=600, key=69):
    """(rms rise dB, peak rise dB, onset rise dB) of 0..span_ms after t_ms over the 50 ms (at least two periods of key)
    before. The event reaches the engine at its audio block's start, up to one block (5.8 ms) before its time in the
    script: the before ends 10 ms early and the after starts there"""
    i = int(t_ms * FS / 1000) - W
    L = lag(key)
    pl = max(5 * W, 2 * L)
    rp = slide(x, i - pl, (pl - W) // H + 1)
    lb_r, lb_p = db(rp.max()), db(np.abs(x[i - pl:i]).max())
    if lb_r < -90:                              # (the note under 1 LSB RMS, in the cut's dither noise (rhodes.c
                                                # RH_DITH: ~-96 dBFS) or ended: nothing to be louder than; the pedal's
                                                # thud into silence is S12's own. Before the dither: -100)
        return 0.0, 0.0, 0.0
    n = span_ms * FS // 1000 // H + 1
    ra = slide(x, i, n)
    rise_r = db(ra.max()) - lb_r
    rise_p = db(np.abs(x[i:i + span_ms // 10 * W + W]).max()) - lb_p
    d, dp = db(ra), db(slide(x, i - L, n))      # a window over the one a period earlier (the same waveform point)
    on = float(np.max(np.where(d > lb_r - FLOOR_DB, d - dp, -99.0)))
    return rise_r, rise_p, on


GRID = [(ty, nz, ped, h) for ty in TYPES for nz in NOISES for ped in (False, True) for h in (200, 2000)]


@pytest.mark.parametrize("ty,noise,pedal,hold", GRID,
                         ids=[f"{t[0]}-N{n}-{'ped' if p else 'up'}-{h}" for t, n, p, h in GRID])
def test_never_louder_after_key_up(ty, noise, pedal, hold):
    x, sl, notes = render(ty, noise, pedal, hold)
    bad = []
    for (t_on, t_up, t_pu), (v, k) in zip(sl, notes):
        for what, t, span in (("key-up", t_up, 290 if pedal else 600),) + ((("pedal-up", t_pu, 600),) if pedal else ()):
            rr, rp, on = check_at(x, t, span, k)
            if rr > TOL_DB or rp > TOL_DB or on > ONSET_DB:
                bad.append(f"v{v} key {k} {what}: 10 ms RMS {rr:+.1f} dB, peak {rp:+.1f} dB, onset {on:+.1f} dB")
    assert not bad, f"{ty[0]} NOISE {noise} {'pedal' if pedal else 'no pedal'} held {hold} ms:\n" + "\n".join(bad)


def _fails(r):
    return r[0] > TOL_DB or r[1] > TOL_DB or r[2] > ONSET_DB


def _g1(hold_s=1.0, tail_s=0.7, damp_db_s=60.0):
    """a held G1 (key 31) as one pulse per period (a hard bass bark: 10 ms windows swing ~10 dB), let go at hold_s
    and damped at damp_db_s; (signal, the key-up in ms, the key-up's sample, f0)"""
    f0 = 440.0 * 2 ** ((31 - 69) / 12.0)
    t = np.arange(int((hold_s + tail_s) * FS)) / FS
    ph = (t * f0) % 1.0
    x = 8000.0 * np.exp(-0.5 * ((ph - 0.5) / 0.06) ** 2)
    x -= x.mean()
    x *= np.where(t < hold_s, 1.0, 10 ** (-damp_db_s * (t - hold_s) / 20))
    return x, hold_s * 1000, int(hold_s * FS), f0


def _ref(x, n0):
    return np.sqrt(np.mean(np.square(x[n0 - 5 * W:n0])))


def _tone(x, n0, f0, d_ms, tau, level_db):
    """x plus a decaying sine at f0 from d_ms after n0, its loudest 10 ms at level_db re the 50 ms before n0"""
    a = n0 + int(d_ms * FS / 1000)
    tt = np.arange(len(x) - a) / FS
    y = np.sin(2 * np.pi * f0 * tt) * np.exp(-tt / tau) * np.minimum(1.0, tt / 0.0005)
    r10 = max(np.sqrt(np.mean(y[j:j + W] ** 2)) for j in range(0, len(y) - W, W // 4))
    out = x.copy()
    out[a:] += y * _ref(x, n0) * 10 ** (level_db / 20) / r10
    return out


def test_windowing_no_false_onset_held_g1():
    """a held G1 pulse train (10 ms windows swing ~10 dB inside its 20.4 ms period) let go and damped, nothing
    added: no rise, no onset (the old non-overlapping 10 ms rule read the swing as onsets)"""
    x, t_ms, n0, _ = _g1()
    r10 = rms10(x[n0 - 10 * W:n0])
    assert db(r10.max()) - db(r10.min()) > 6.0     # (the swing is there to fool a 10 ms window)
    for damp in (60.0, 6.0, 0.0):                   # damped, slow, the pedal down (held on)
        x, t_ms, n0, _ = _g1(damp_db_s=damp)
        r = check_at(x, t_ms, 600, 31)
        assert not _fails(r), (damp, r)


def test_windowing_catches_late_thump_g1():
    """a knock (300 Hz, 10 ms decay) 80 ms after the key-up of a G1 (the felt landing late), 3 dB under the note
    before, on a fast and a slow damping: a new onset on the decay. (One period windows at a period's hop read it as
    +1.3 / +1.0 dB and pass it; this rule +6.8 / +4.8)"""
    for damp in (60.0, 20.0):
        x, t_ms, n0, _ = _g1(damp_db_s=damp)
        r = check_at(_tone(x, n0, 300.0, 80, 0.010, -3.0), t_ms, 600, 31)
        assert _fails(r), (damp, r)


def test_windowing_catches_restrike_tail():
    """the note re-excited 20 ms after the key-up at its own level (a re-strike tail, 150 ms decay): louder after the
    key-up, at G1 and at C4"""
    x, t_ms, n0, f0 = _g1()
    assert _fails(check_at(_tone(x, n0, f0, 20, 0.150, 0.0), t_ms, 600, 31))
    t = np.arange(int(1.7 * FS)) / FS
    c4 = 6000.0 * np.sin(2 * np.pi * 261.63 * t) * np.where(t < 1.0, 1.0, 10 ** (-60.0 * (t - 1.0) / 20))
    n0 = FS
    assert not _fails(check_at(c4, 1000, 600, 60))
    assert _fails(check_at(_tone(c4, n0, 261.63, 20, 0.150, 0.0), 1000, 600, 60))
