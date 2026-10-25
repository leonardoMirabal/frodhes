# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py's MARIMBA measures on synthetic takes (no FM-1), each failing on the code before its fix:
the jack's onsets from USB's (align() alone read the jack 2-15 ms late under its hiss, and the keys take's level
span / step / tuning off), a roll is not a slipped stream, the MUTE drop depth stays over the jack's floor, the
marimba_fx roll is the same in every take, and the VIBES suites keep their stimuli; PHASER SPEED 0 in a take of
2.4 periods, NOISE's contact as the gate's aligned difference, DRIVE TONE's jack V3 on the jack's own distortion;
DRIVE TONE's V3 clear of the 44.1 -> 48 kHz resampler's image of V1, DELAY DIG vs ANA by the tape's wow / flutter
(the echoes' spectrum can not move on a marimba A4), and VIBES' PHASER SPEED 0 on a long take run last (every other
VIBES take and row in place); CHORUS SPEED 0 the same way on both engines, its long takes after PH0's."""
import os
import sys

import numpy as np
import pytest

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


def band_trace(f, L, ph, seed=0):
    """a phaser band's level (dB, the hil frames) as a triangle LFO at f Hz sweeps the notches: no repeat at half a
    period, harmonics strong"""
    fps = FS / 240
    t = np.arange(int(L * fps)) / fps
    p = 2 * np.abs(((t * f + ph) % 1) - 0.5)
    return -8 * np.sin(np.pi * 1.3 * p) ** 2 + 0.3 * np.random.default_rng(seed).standard_normal(len(t)), fps


def test_phaser_speed_0_needs_a_long_take():
    """SPEED 0 (0.25 Hz) in the 5.1 s trace of ST_LONG: the period sits at the autocorrelation search's end and the
    rate reads 0.33-0.40 Hz depending on the LFO's phase (the FM-1's scan 3 and the host: 0.50); marimba_fx's PH0
    take gives 2.4 periods, read within 1 %"""
    f = h.lfo_hz(0, True)
    short = [h.lfo_rate(*band_trace(f, 5.1, ph), f * 0.4, f * 2.6) for ph in (0.2, 0.4, 0.6)]
    assert max(abs(r / f - 1) for r in short) > 0.10, short                  # the take before PH0
    got = {}
    real = h.vibes_fx
    try:
        h.vibes_fx = lambda V, S, stim=None, extra=(): got.update(stim)
        h.marimba_fx(None, None)
    finally:
        h.vibes_fx = real
    dur, ev = got["PH0"]
    trace = dur - 0.7 - 0.2                                                  # phrate's window: 0.2 .. dur - 0.7
    assert trace * f >= 2.2 and max(e[0] for e in ev) <= dur - 0.4
    for ph in np.linspace(0, 1, 7, endpoint=False):
        r = h.lfo_rate(*band_trace(f, trace, ph, seed=int(10 * ph)), f * 0.4, f * 2.6, pearson=True)
        assert abs(r / f - 1) <= 0.01, (ph, r)
    assert got["JFLOOR"] is True


def test_phaser_speed_0_on_the_host(monkeypatch):
    """the host build, marimba_fx's PH0 takes (the roll, PHASER DEPTH 127 SPEED 0, and DEPTH 0): the 300-3000 Hz band
    dips at 0.75, 2.75 and 3.75 s of each 4 s cycle, so half the period correlates 0.63 over its overlap and the
    period 0.99, but 0.50 / 0.58 by the whole trace's energy (lfo_rate's default), where the 0.85 rule took 2 s:
    0.50 Hz, as the FM-1 read. pearson: 0.25 Hz"""
    monkeypatch.setattr(h, "ENGINE", "marimba")
    V = h.VRig("usb", host=1)
    P = h.vp()
    got = {}
    real = h.vibes_fx
    try:
        h.vibes_fx = lambda V_, S, stim=None, extra=(): got.update(stim)
        h.marimba_fx(None, None)
    finally:
        h.vibes_fx = real
    PH0 = got["PH0"]
    pb = [("PH_SW", 1), ("PH_DEPTH", 80), ("PH_SPEED", P["PH_SPEED"]["de"]), ("PH_FDBK", P["PH_FDBK"]["de"]),
          ("PH_SYNC", 0)]
    V.reset(h.with_(pb, ("PH_DEPTH", 127), ("PH_SPEED", 0)) + [("RELEASE", 0)], label="speed 0")
    t = V.take("t-phaser-speed-0-long", *PH0)
    V.reset(h.with_(pb, ("PH_DEPTH", 0)) + [("RELEASE", 0)], label="depth 0")
    r = V.take("t-phaser-depth-0-long", *PH0)
    d, _, fps = h.rel_trace(t, r, "usb", 0.2, PH0[0] - 0.7, 300, 3000)
    f = h.lfo_hz(0, True)
    assert abs(h.lfo_rate(d, fps, f * 0.4, f * 2.6) / (2 * f) - 1) <= 0.02   # (the bug: twice the rate)
    assert abs(h.lfo_rate(d, fps, f * 0.4, f * 2.6, pearson=True) / f - 1) <= 0.01


def c6_takes(noise_db=-33.8, lag=2.37, hiss=None, seed=4):
    """two takes of a C6 strike (V1 + V2 + V3, a fast decay): NOISE 0, and the same + a contact burst noise_db re the
    note's RMS over 0..60 ms (tau 23 ms, white), the second take delayed lag samples (the FM-1's takes are not sample
    aligned) and both over a hiss (the jack)"""
    n = int(1.0 * FS)
    t = np.arange(n) / FS
    f = 1046.5

    def ring(t):
        tt = np.maximum(t, 0)
        return (np.where(t >= 0, 1.0, 0.0) * (np.exp(-tt / 0.12) * np.sin(2 * np.pi * f * tt)
                + 0.03 * np.exp(-tt / 0.03) * np.sin(2 * np.pi * 3.93 * f * tt)
                + 0.01 * np.exp(-tt / 0.01) * np.sin(2 * np.pi * 9.8 * f * tt)))
    t0 = 0.2
    r0 = 0.1 * ring(t - t0)
    r1 = 0.1 * ring(t - t0 - lag / FS)
    w = slice(int(t0 * FS), int((t0 + 0.06) * FS))
    rms = np.sqrt(np.mean(r0[w] ** 2))
    tt = t - t0 - lag / FS
    wn = np.random.default_rng(seed).standard_normal(n)
    Z = np.fft.rfft(wn)
    fr = np.fft.rfftfreq(n, 1 / FS)
    wn = np.fft.irfft(Z / (1 + ((fr - f) / 150) ** 2), n)                   # (the burst coloured round V1, as the
    z = np.where(tt >= 0, np.exp(-np.maximum(tt, 0) / 0.023), 0) * wn      # bar's own modes colour it)
    zw = slice(int((t0 + lag / FS) * FS), int((t0 + lag / FS + 0.06) * FS))
    z *= rms * 10 ** (noise_db / 20) / np.sqrt(np.mean(z[zw] ** 2))
    x0, x1 = r0.copy(), r1 + z
    if hiss is not None:
        g = np.random.default_rng(seed + 1)
        x0 += hiss * g.standard_normal(n)
        x1 += hiss * g.standard_normal(n)
    return x0, x1, int(t0 * FS), int(t0 * FS + round(lag))


def test_noise_contact_is_the_gates_difference():
    """NOISE 127's contact on C6 at the gate's -33.8 dB (the host's sample-exact rectangular difference): the old
    spectral excess (flat-start window, 1.26 x the ring taken off per bin) read it 1+ dB low, under -35 (the FM-1's
    scan 3: -35.4 on USB and the jack, the host too); the aligned difference reads it within 0.3 dB, a fractional lag
    and the jack's hiss included"""
    for hiss in (None, 10 ** (-92 / 20)):
        x0, x1, o0, o1 = c6_takes(hiss=hiss)
        old = h.m_excess_db(h.cut(x1, o1, 0, 0.06), h.cut(x0, o0, 0, 0.06)) - h.m_ms_db(h.cut(x0, o0, 0, 0.06))
        assert old < -34.8, old                                              # (the bug)
        c, d = h.m_contact_db(x1, o1, x0, o0)
        assert abs(c + 33.8) <= 0.3, (hiss, c, d)
        assert abs(d + (2.37 - 2)) <= 0.05, d               # x moved back by the fraction (o1 holds the whole 2)
    x0, x1, o0, o1 = c6_takes(noise_db=-200)                                 # no contact: the residue is far under
    assert h.m_contact_db(x1, o1, x0, o0)[0] <= -60


def test_drive_tone_jack_floor(monkeypatch):
    """A4: V1, V3 79 dB under it (TONE 0, as USB reads it) and, on the jack only, the jack's own 10th harmonic of V1
    70 dB under V1 (23 Hz off V3) over its hiss: the jack's V3 band is floored, USB's is not"""
    monkeypatch.setattr(h, "ENGINE", "marimba")
    sp = h.bspec()[69]
    assert abs(sp["V3"] / sp["V1"] - 10) * sp["V1"] > 15                     # (MARIMBA's V3: not on the 10th)
    n = int(1.5 * FS)
    t = np.arange(n) / FS
    f1 = sp["V1"] * 1.002                                                    # (the interface's clock)
    usb = np.sin(2 * np.pi * f1 * t) + 10 ** (-79 / 20) * np.sin(2 * np.pi * sp["V3"] * 1.002 * t)
    jack = usb + 10 ** (-70 / 20) * np.sin(2 * np.pi * 10 * f1 * t) + 10 ** (-95 / 20) * \
        np.random.default_rng(2).standard_normal(n)
    assert not h.m_line_floored(usb, sp)
    assert h.m_line_floored(jack, sp)
    # V3 up 7 dB (TONE 127, USB +6.8 on the FM-1): the jack's band moves 2 dB at most (the row read +0.2)
    up = (10 ** (-72 / 20) - 10 ** (-79 / 20)) * np.sin(2 * np.pi * sp["V3"] * 1.002 * t)
    assert h.pk_db(usb + up, sp["V3"]) - h.pk_db(usb, sp["V3"]) >= 6
    assert h.pk_db(jack + up, sp["V3"]) - h.pk_db(jack, sp["V3"]) < 2


def roll_a4(n, sp, v3_db, fs=44100, rate=8.0):
    """the A4 roll as the FM-1 renders it (44.1 kHz): V1 (with the tube's line 4 Hz over it) swelling at rate Hz,
    and V3 v3_db under V1 (its mean level), shorter swells (smooth: no strike clicks, whose broadband the shelf moves
    as it moves V3)"""
    t = np.arange(n) / fs
    c = 0.5 + 0.5 * np.cos(2 * np.pi * rate * t)
    env, e3 = 0.6 + 0.4 * c, c ** 4 / np.sqrt(np.mean(c ** 8))
    return env * (np.sin(2 * np.pi * sp["V1"] * t) + 0.3 * np.sin(2 * np.pi * sp["TUBE"] * t)) + \
        10 ** (v3_db / 20) * e3 * np.sin(2 * np.pi * sp["V3"] * t + 0.3)


def test_drive_tone_v3_line_clear_of_the_resampler_image(monkeypatch):
    """DRIVE TONE 0 -> 127 raises A4's V3 12 dB re V1 (the shelf); rendered at 44.1 kHz and resampled to 48 as the
    capture is (resample_poly 160 / 147), V1's image lands at 3900 + 440 = 4340 Hz, ~80 dB under V1, inside V3's +-3 %
    band: with MARIMBA's V3 ~98 dB under V1 at TONE 0 the band reads the image, +6 dB at most (the FM-1 / host:
    +5.6 .. +6.7). m_line_db (+-12 Hz round V3) reads the shelf"""
    from scipy.signal import resample_poly
    monkeypatch.setattr(h, "ENGINE", "marimba")
    sp = h.bspec()[69]
    n = int(1.4 * 44100)
    t0, t127 = (resample_poly(roll_a4(n, sp, db), 160, 147)[int(0.005 * FS):int(1.3 * FS)] for db in (-98, -86))
    band = h.pk_rel(t127[:int(0.3 * FS)], sp, ("V3",))[0] - h.pk_rel(t0[:int(0.3 * FS)], sp, ("V3",))[0]
    assert band < 6, band                                                   # (the bug)
    line = h.m_line_db(t127, sp) - h.m_line_db(t0, sp)
    assert abs(line - 12) <= 1.5, line
    # the image is the resampler's: at 44.1 kHz the band reads the shelf too
    a0, a1 = (roll_a4(n, sp, db)[:int(0.3 * 44100)] for db in (-98, -86))
    S = [np.abs(np.fft.rfft(x * np.hanning(len(x)), 1 << 16)) ** 2 for x in (a0, a1)]
    fr = np.fft.rfftfreq(1 << 16, 1 / 44100)
    m = (fr >= 0.97 * sp["V3"]) & (fr <= 1.03 * sp["V3"])
    assert 10 * np.log10(S[1][m].sum() / S[0][m].sum()) >= 10


def echoes(T_ms, n, wobble=None, fs=FS, f=440.0, k=8):
    """a marimba A4 staccato through a delay: the note (V1, 37 dB of V2) and k echoes T apart, each -2.3 dB; wobble:
    the tape's delay modulation (s, as a function of t), as fx.c's A.DELAY (wow ~0.7 Hz +-0.29 %, flutter ~7 Hz
    +-0.05 % of the time)"""
    t = np.arange(n) / fs

    def note(tt):
        tt = np.maximum(tt, 0) * (tt >= 0)
        return (tt > 0) * np.exp(-tt / 0.11) * (np.sin(2 * np.pi * f * tt) + 0.014 * np.sin(2 * np.pi * 4.02 * f * tt))
    x = note(t - 0.1)
    for j in range(1, k + 1):
        d = j * T_ms / 1000 + (wobble(t) * j if wobble else 0)
        x = x + 10 ** (-2.3 * j / 20) * note(t - 0.1 - d)
    return x


def test_delay_dig_vs_ana_is_the_wow_not_the_spectrum():
    """A.DELAY's darker loop (fx.c: TONE's one-pole x0.6) takes 0.07 dB a pass off A4's V1, 1.0 off V2: the echoes of
    a marimba A4 (V1 alone) keep their spectrum (the FM-1's scan 3: 1.6 dB / x1.00), but they waver in time and
    pitch. m_echo_wobble_ms: ANA re DIG >= 0.15 ms, a 2nd DIG take ~0"""
    T = 357.0
    n = int(3.3 * FS)
    wob = lambda t: 15722 / 44100 * (3 * 32767 * np.sin(2 * np.pi * 0.708 * t) +      # noqa: E731
                                     32767 / 2 * np.sin(2 * np.pi * 7.08 * t)) / 2 ** 25
    dig, ana = echoes(T, n), echoes(T, n, wob)
    w = slice(int((0.1 + T / 1000 - 0.02) * FS), int((0.1 + 3 * T / 1000) * FS))
    assert h.sdist(h.third(dig[w]), h.third(ana[w])) < 2                    # the spectrum: the old row's FAIL
    on = int(0.1 * FS)
    a = h.m_echo_wobble_ms(ana[on:], dig[on:], 440.0, T)
    d = h.m_echo_wobble_ms(echoes(T, n)[on:], dig[on:], 440.0, T)
    assert a >= 0.3 and d <= 0.01, (a, d)


def test_vibes_phaser_speed_0_long_take(monkeypatch):
    """VIBES: SPEED 0 (0.25 Hz) read 0.33 Hz on the FM-1 and the host from ST_LONG's 5.1 s trace. ST_PH0: the chord
    struck 3 times (held, it is under the gate by ~6.5 s), a 9.6 s trace; the host build reads 0.25 Hz"""
    dur, ev = h.ST_PH0
    f = h.lfo_hz(0, True)
    assert (dur - 0.9) * f >= 2.2 and max(e[0] for e in ev) <= dur - 0.4
    assert len([e for e in ev if e[1] == "on"]) == 3 * len(h.FXCH)
    monkeypatch.setattr(h, "ENGINE", "vibes")
    V = h.VRig("usb", host=2)
    P = h.vp()
    pb = [("PH_SW", 1), ("PH_DEPTH", 80), ("PH_SPEED", P["PH_SPEED"]["de"]), ("PH_FDBK", P["PH_FDBK"]["de"]),
          ("PH_SYNC", 0)]
    V.reset(h.with_(pb, ("PH_DEPTH", 127), ("PH_SPEED", 0)), label="speed 0")
    t = V.take("tv-phaser-speed-0-long", *h.ST_PH0)
    V.reset(h.with_(pb, ("PH_DEPTH", 0)), label="depth 0")
    r = V.take("tv-phaser-depth-0-long", *h.ST_PH0)
    d, _, fps = h.rel_trace(t, r, "usb", 0.2, dur - 0.7, 300, 3000)
    assert abs(h.lfo_rate(d, fps, f * 0.4, f * 2.6, pearson=True) / f - 1) <= 0.02
    V.reset(h.with_(pb, ("PH_DEPTH", 127), ("PH_SPEED", 0)), label="speed 0 short")
    t = V.take("tv-phaser-speed-0", *h.ST_LONG)
    V.reset(h.with_(pb, ("PH_DEPTH", 0)), label="depth 0 short")
    r = V.take("tv-phaser-depth-0", *h.ST_LONG)
    d, _, fps = h.rel_trace(t, r, "usb", 0.2, 5.3, 300, 3000)
    assert h.lfo_rate(d, fps, f * 0.4, f * 2.6) / f - 1 > 0.2                # (the bug: 0.33 Hz)


def test_vibes_fx_takes_and_rows_kept(monkeypatch, capsys):
    """VIBES (--dry-run): the PH0 takes run last and replace the 6 s SPEED 0 take; every other take keeps its place
    (the host's seeded jitter and CC history: every other row unchanged); the SPEED 0 rows stand where they did"""
    monkeypatch.setattr(h, "VDEBUG", True, raising=False)
    monkeypatch.setattr(h, "ENGINE", "vibes")
    V = h.VRig("both", dry_run=True)
    S = h.VSuite("vibes_fx", V)
    h.vibes_fx(V, S)
    tags = [t["tag"] for t in V.takes]
    assert tags[-4:-2] == ["phaser-speed-0-long", "phaser-depth-0-long"] and "phaser-speed-0" not in tags
    i = tags.index("phaser-speed-40")
    assert tags[i - 1] == "phaser-depth-127"                                 # (where phaser-speed-0 was)
    rows = [r["check"] for r in S.rows]
    k = rows.index("PHASER SPEED PH_SPEED (CC 14) 40")
    assert rows[k - 1] == "PHASER SPEED PH_SPEED (CC 14) 0"
    assert rows[k + 1] == "PHASER SPEED PH_SPEED (CC 14) 127" and rows[k + 2] == "PHASER SPEED 0 slower than 40"
    assert not any("wow" in r for r in rows)                                 # (MARIMBA's rows only)
    dl = [r for r in S.rows if r["check"] == "DELAY DL_SW (CC 88) DIG vs ANA"][0]
    assert dl["status"] != "info" and dl["crit"] == ">= 2 dB or centroid x1.15 either way"
    assert [r["setting"] for r in S.rows if r["check"].startswith("DRIVE TONE")] == \
        ["DRIVE 64: A4's V2 / V3 re V1 (5..300 ms)"]


def _fx_stim(engine):
    """the PH0 / LONG stimuli and extra settings vibes_fx gets on the engine (marimba_fx's roll, or VIBES' own)"""
    if engine == "vibes":
        return h.ST_PH0, h.ST_LONG, []
    got = {}
    real = h.vibes_fx
    try:
        h.vibes_fx = lambda V, S, stim=None, extra=(): got.update(stim, extra=list(extra))
        h.marimba_fx(None, None)
    finally:
        h.vibes_fx = real
    return got["PH0"], got["LONG"], got["extra"]


@pytest.mark.parametrize("engine,seed", [("vibes", 4), ("marimba", 2)])
def test_chorus_speed_0_long_take(monkeypatch, engine, seed):
    """CHORUS SPEED 0 (0.25 Hz): ST_LONG's 5.1 s centroid trace by the harmonic sum (rate_of) read VIBES 0.36 Hz
    (FM-1 0.36 / 0.42), MARIMBA 0.39 (or 0.25, by the host's jitter alone). CS0: the PH0 take re a CHORUS DEPTH 0 take
    of the same strikes, as the phaser's SPEED 0 (300-3000 Hz, lfo_rate pearson): 0.25 Hz. VIBES seed 4: the
    centroids' difference read 0.28 (the strikes' own centroid steps, 3.3 s apart, differ with the chorus on)"""
    monkeypatch.setattr(h, "ENGINE", engine)
    PH0, LONG, extra = _fx_stim(engine)
    V = h.VRig("usb", host=seed)
    P = h.vp()
    f = h.lfo_hz(0, True)
    fps = h.FS / 240
    chb = [("CP_SW", 1), ("CP_DEPTH", 64), ("CP_SPEED", P["CP_SPEED"]["de"]), ("CP_FDBK", P["CP_FDBK"]["de"]),
           ("CP_SYNC", 0)]
    s0 = h.with_(chb, ("CP_DEPTH", 127), ("CP_SPEED", 0))
    V.reset(s0 + extra, label="speed 0 short")
    t6 = V.take("tc-chorus-speed-0", *LONG)
    c6 = h.cen_trace(h.cut(t6.m("usb"), t6.on("usb")[0], 0.2, 5.3))[0]
    assert h.rate_of(c6, fps, f) / f - 1 > 0.2                               # (the bug: 0.36 / 0.39 Hz)
    V.reset(s0 + extra, label="speed 0")
    t = V.take("tc-chorus-speed-0-long", *PH0)
    V.reset(h.with_(chb, ("CP_DEPTH", 0)) + extra, label="depth 0")
    r = V.take("tc-chorus-depth-0-long", *PH0)
    d, _, fps_ = h.rel_trace(t, r, "usb", 0.2, PH0[0] - 0.7, 300, 3000)
    assert abs(h.lfo_rate(d, fps_, f * 0.4, f * 2.6, pearson=True) / f - 1) <= 0.02


@pytest.mark.parametrize("engine", ["vibes", "marimba"])
def test_chorus_speed_0_takes_last_rows_in_place(monkeypatch, engine):
    """(--dry-run) the CS0 takes run last, after PH0's, and replace the 6 s chorus-speed-0 take; the chorus' other
    takes keep their places, and the CHORUS SPEED 0 rows (within 10 %, slower than the default) stand where they
    belong, before SPEED 50 and after SPEED 127"""
    monkeypatch.setattr(h, "VDEBUG", True, raising=False)
    monkeypatch.setattr(h, "ENGINE", engine)
    V = h.VRig("both", dry_run=True)
    S = h.VSuite(f"{engine}_fx", V)
    (h.vibes_fx if engine == "vibes" else h.marimba_fx)(V, S)
    tags = [t["tag"] for t in V.takes]
    assert tags[-4:] == ["phaser-speed-0-long", "phaser-depth-0-long", "chorus-speed-0-long", "chorus-depth-0-long"]
    assert "chorus-speed-0" not in tags
    de = h.vp()["CP_SPEED"]["de"]
    assert tags[tags.index(f"chorus-speed-{de}") - 1] == "chorus-depth-127"   # (where chorus-speed-0 was)
    rows = [r["check"] for r in S.rows]
    k = rows.index(f"CHORUS SPEED CP_SPEED (CC 87) {de}")
    assert rows[k - 1] == "CHORUS SPEED CP_SPEED (CC 87) 0"
    assert rows[k + 1] == "CHORUS SPEED CP_SPEED (CC 87) 127" and rows[k + 2] == f"CHORUS SPEED 0 slower than {de}"
    assert rows[k + 3].startswith("CHORUS FDBK")
    pk = rows.index("PHASER SPEED PH_SPEED (CC 14) 0")                       # (PH0's rows still in place)
    assert rows[pk + 3] == f"PHASER SPEED 0 slower than {h.vp()['PH_SPEED']['de']}"
    assert len(rows) == len(set(rows))


def test_marimba_chorus_speed_by_the_band(monkeypatch):
    """MARIMBA CHORUS SPEED 50 / 127 (CS_BAND): the roll's centroid by the harmonic sum (rate_of) read SPEED 50 1.31 Hz
    for 0.87 on host seed 4 (seed 13 before 95b21ac: the jitter alone). The 300-3000 Hz band re the suite's CHORUS
    DEPTH 0 take, by its period (lfo_rate pearson), reads 0.87-0.88 on seeds 1-15 with the same takes; VIBES keeps
    the centroid"""
    monkeypatch.setattr(h, "ENGINE", "marimba")
    V = h.VRig("usb", host=4)
    got, take = {}, V.take

    def keep(tag, dur, events):
        got[tag] = take(tag, dur, events)
        return got[tag]
    monkeypatch.setattr(V, "take", keep)
    S = h.VSuite("marimba_fx", V)
    h.marimba_fx(V, S)
    rows = {r["check"]: r for r in S.rows}
    for x in (50, 127):
        r = rows[f"CHORUS SPEED CP_SPEED (CC 87) {x}"]
        assert r["status"] == "ok" and "re DEPTH 0" in r["setting"], r
    f = h.lfo_hz(50, True)
    t, r0 = got["chorus-speed-50"], got["chorus-depth-0"]
    # (the old measure, the centroid, read 1.31 Hz here on 0.9.5's chorus; on 0.9.6's, its wet high-passed at ~220 Hz,
    # it happens to read 0.87; the row keeps the band measure, right on both)
    d, _, fps_ = h.rel_trace(t, r0, "usb", 0.2, 5.3, 300, 3000)
    assert abs(h.lfo_rate(d, fps_, f * 0.4, f * 2.6, pearson=True) / f - 1) <= 0.02
    f = h.lfo_hz(127, True)                     # SPEED 127: two periods correlated better than one on 0.9.6 (3.00 Hz
    t = got["chorus-speed-127"]                 # for 6.00, seed 4); the octave check reads the wave's own period
    d, _, fps_ = h.rel_trace(t, r0, "usb", 0.2, 5.3, 300, 3000)
    assert abs(h.lfo_rate(d, fps_, f * 0.4, f * 2.6, pearson=True) / f - 0.5) <= 0.02     # (without it: the octave)
    assert abs(h.lfo_rate(d, fps_, f * 0.4, f * 2.6, pearson=True, octave=True) / f - 1) <= 0.02
