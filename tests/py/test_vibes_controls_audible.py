# SPDX-License-Identifier: GPL-3.0-only
"""Every VIBES control outside the VOICE page changes the sound by an audible amount (added 2026-10-17: on the FM-1
the VOICE cards did almost nothing while every test passed, because none checked that a control is heard). Each
control is set at its ends (by MIDI CC, and through the panel where that is the user's way) and the sound is
measured on the host build: keys 53 / 69 / 89 (F3 A4 F6), velocity 100 (and the user's KEY VEL 117).

The limits are what an attentive listener notices without an A/B switch:
- RING (DECAY page K1, CC 102): 0 vs 127 changes the fundamental's T60 at least 2x on every key, in order.
- DAMPER (K2, CC 103): firm (0) vs soft (127) changes the decay after the key-up at least 3x.
- The pedal (CC 64 / REC): down vs up changes the decay after the key-up at least 3x; a half pedal is in between.
- NOISE (K3, CC 104): the mallet's contact noise (what NOISE adds to the strike's first 60 ms) rises 6 dB or more from
  the default to 127, where it is a clear tick (within 35 dB of the note); the damper's knock (what it adds after the
  key-up) rises 6 dB or more; at the default the knock is 18 dB or more under the strike and under the note it damps.
- TUNE (K4, the panel): +-50 cents within 1 cent.
- The MOTOR page (CC 47-51): DEPTH 127 at 3.5 Hz gives 6 dB or more of AM on every key; DEPTH 0 or OFF none; the AM
  runs at the SPEED card's rate within 5 %; switching the motor on or off has no click.
- KEY VEL / MIDI velocity: v40 vs v117 differ by 10 dB or more. (The harder strike should also be brighter: it is
  not yet, test_velocity_brightens is an expected failure for the VOICE work.)
- Each shared effect (drive, reverb, filter, chorus, phaser, delay, compressor, tremolo) still changes VIBES."""
import os
import sys

import numpy as np
import pytest

from hostrun import FS, cc, card_hz, env_rms, rate_of, rms_db, run

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

VIBES = [cc(20, 79, 127)]                     # TYPE VIBES (CC 79)
RAW = [cc(21, 119, 127)]                      # the engine alone (no effects, no silence gate): exact decays
MOTOR_OFF = [cc(22, 47, 0)]                   # the motor comes on with TYPE VIBES: off for the voice tests
KEYS = (53, 69, 89)


def f0(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def partial_db(x, f, hop=441, n=4096):
    """the level (dB) of the partial near f, frames of hop samples"""
    w = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = (fr > f * 0.94) & (fr < f * 1.06)
    out = [20 * np.log10(np.max(np.abs(np.fft.rfft(x[i:i + n] * w))[sel]) / (n / 4) / 32767 + 1e-12)
           for i in range(0, len(x) - n, hop)]
    return np.array(out), np.arange(len(out)) * hop / FS + n / 2 / FS


def t60(x, f, hi=6.0, lo=36.0):
    """the partial's T60 (s), fitted from 6 to 36 dB under its peak"""
    e, t = partial_db(x, f)
    ip = int(np.argmax(e))
    a = np.where((np.arange(len(e)) > ip) & (e < e[ip] - hi))[0]
    b = np.where((np.arange(len(e)) > ip) & (e < e[ip] - lo))[0]
    i0, i1 = a[0], (b[0] if len(b) else len(e) - 1)
    return -60.0 / np.polyfit(t[i0:i1], e[i0:i1], 1)[0]


def after_release(x, f, t_off):
    """the partial's decay rate (dB/s, positive) from 30 to 300 ms after the key-up"""
    e, t = partial_db(x, f, hop=220)
    k = (t >= t_off + 0.03) & (t <= t_off + 0.3) & (e > -110)
    return -np.polyfit(t[k], e[k], 1)[0]


def hp(x, fc=4000.0):
    sp = np.fft.rfft(x)
    sp[np.fft.rfftfreq(len(x), 1 / FS) < fc] = 0
    return np.fft.irfft(sp, len(x))


def am_db(x, guess):
    """the AM of x: depth (dB peak to trough of the fitted periodic envelope, the decay taken out) and rate (Hz)"""
    hop = 220
    e = 20 * np.log10(env_rms(x, hop) / 32767 + 1e-9)
    fps = FS / hop
    r = rate_of(e, fps, guess)
    t = np.arange(len(e)) / fps
    tn = (t - t.mean()) / (t.max() - t.min())
    a = np.column_stack([np.ones_like(t), tn, tn * tn] + [g(2 * np.pi * h * r * t) for h in (1, 2, 3) for g in (np.cos, np.sin)])
    c = np.linalg.lstsq(a, e, rcond=None)[0]
    per = a[:, 3:] @ c[3:]
    return float(per.max() - per.min()), r


def play(tag, setup, note, vel=100, on=200, dur=1500, end=None, raw=True):
    lines = VIBES + (RAW if raw else []) + MOTOR_OFF + setup + [f"{on} midi 0x90 {note} {vel}", f"{on + dur} midi 0x80 {note} 0"]
    return run(tag, lines, end or on + dur + 1500)


# ---- the DECAY page
@pytest.mark.parametrize("note", KEYS)
def test_ring_changes_the_ring_time(note):
    t = [t60(play(f"va-ring{v}-{note}", [cc(30, 102, v), cc(30, 64, 127)], note, dur=30000, end=26000).L, f0(note))
         for v in (0, 64, 127)]
    assert t[0] < t[1] < t[2] and t[2] / t[0] >= 2.0, (note, t)


@pytest.mark.parametrize("note", KEYS)
def test_damper_changes_the_stop(note):
    r = {v: after_release(play(f"va-damp{v}-{note}", [cc(30, 103, v), cc(30, 104, 0)], note).L, f0(note), 1.7)
         for v in (0, 127)}
    assert r[0] / r[127] >= 3.0, (note, r)


@pytest.mark.parametrize("note", KEYS)
def test_pedal_holds_and_half_pedals(note):
    r = {p: after_release(play(f"va-ped{p}-{note}", [cc(30, 64, p), cc(30, 104, 0)], note).L, f0(note), 1.7)
         for p in (0, 50, 127)}
    assert r[0] / r[127] >= 3.0 and r[127] < r[50] < r[0], (note, r)


def test_tune_by_the_panel():
    """the DECAY page's K4 (the panel; TUNE has no CC): -50 / +50 cents"""
    pre = ["100 turn 1 1", "120 turn 1 1", "140 turn 1 1", "160 turn 1 1", "300 btn 6", "400 turn 0 1",
           "450 expect state label.4.3 TUNE"]
    got = {}
    for tu in (-50, 0, 50):
        x = run(f"va-tune{tu}", pre + RAW + MOTOR_OFF + ([f"600 turn 5 {tu}"] if tu else []) +
                ["1000 midi 0x90 69 100", "3500 midi 0x80 69 0"], 3600).L[int(1.3 * FS):int(3.3 * FS)]
        n = 1 << 20
        sp = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
        fr = np.fft.rfftfreq(n, 1 / FS)
        sel = np.where((fr > 400) & (fr < 480))[0]
        i = sel[np.argmax(sp[sel])]
        a, b, c = np.log(sp[i - 1:i + 2])
        got[tu] = (i + 0.5 * (a - c) / (a - 2 * b + c)) * FS / n
    for tu in (-50, 50):
        c = 1200 * np.log2(got[tu] / got[0])
        assert abs(c - tu) < 1.0, (tu, c)
    assert abs(1200 * np.log2(got[0] / 440.0)) < 1.0


@pytest.mark.parametrize("note", KEYS)
def test_noise_changes_the_contact_and_the_knock(note):
    x = {v: play(f"va-nz{v}-{note}", [cc(30, 104, v)], note).L for v in (0, 48, 127)}
    s0, s1 = int(0.2 * FS), int(0.26 * FS)
    contact = {v: rms_db(x[v][s0:s1] - x[0][s0:s1]) for v in (48, 127)}   # what NOISE adds to the strike
    ring = rms_db(x[0][s0:s1])
    assert contact[127] - contact[48] >= 6.0, (note, contact)
    assert contact[127] > ring - 35.0, (note, contact[127], ring)         # NOISE 127: a clear tick on the note
    k0, k1 = int(1.7 * FS), int(1.85 * FS)
    knock = {v: rms_db(x[v][k0:k1] - x[0][k0:k1]) for v in (48, 127)}   # what NOISE adds after the key-up
    assert knock[127] - knock[48] >= 6.0, (note, knock)
    note_lvl = rms_db(x[0][int(1.6 * FS):int(1.7 * FS)])                  # the note just before the key-up
    peak = rms_db(x[0][int(0.25 * FS):int(0.5 * FS)])                     # the strike
    assert knock[48] < peak - 18.0, (note, knock[48], peak)               # the default knock: soft
    assert knock[48] < note_lvl, (note, knock[48], note_lvl)               # never a re-strike of what it damps


# ---- the MOTOR page
def motor(depth=127, rate=78, shape=0, on=127):
    return [cc(30, 47, on), cc(31, 48, depth), cc(32, 49, rate), cc(33, 50, shape), "100 dump"]


@pytest.mark.parametrize("note", KEYS)
def test_motor_depth_is_a_clear_pulse(note):
    t1 = 3.3 if note < 80 else 2.3
    d = {}
    for depth in (0, 127):
        r = play(f"va-mot{depth}-{note}", motor(depth), note, on=300, dur=6000, end=4000)
        d[depth], rate = am_db(r.L[int(0.8 * FS):int(t1 * FS)], 3.5)
        if depth:
            shown = card_hz(r.state(100)["card.21.2"])
            assert abs(rate / shown - 1) < 0.05, (note, rate, shown)
    assert d[127] >= 6.0 and d[0] < 1.0, (note, d)


def test_motor_rate_follows_the_card():
    for v in (20, 78, 110):
        r = play(f"va-motrate{v}", motor(127, v), 69, on=300, dur=6000, end=4300)
        shown = card_hz(r.state(100)["card.21.2"])
        _, rate = am_db(r.L[int(0.8 * FS):int(4.0 * FS)], shown)
        assert abs(rate / shown - 1) < 0.05, (v, rate, shown)


def test_motor_shape_sharpens_the_pulse():
    """SHAPE 0 (a smooth pulse) vs 127 (the discs open briefly: a narrower, more pointed pulse): the envelope's 2nd
    harmonic re its fundamental grows by half or more (measured 0.31 -> 0.57 on F3)"""
    def h21(x):
        hop = 220
        e = 20 * np.log10(env_rms(x, hop) / 32767 + 1e-9)
        fps = FS / hop
        r = rate_of(e, fps, 3.5)
        t = np.arange(len(e)) / fps
        tn = (t - t.mean()) / (t.max() - t.min())
        a = np.column_stack([np.ones_like(t), tn, tn * tn] + [g(2 * np.pi * h * r * t) for h in (1, 2) for g in (np.cos, np.sin)])
        c = np.linalg.lstsq(a, e, rcond=None)[0]
        return np.hypot(c[5], c[6]) / np.hypot(c[3], c[4])
    for note in (53, 69):
        h = {s: h21(play(f"va-motshape{s}-{note}", motor(127, 78, s), note, on=300, dur=6000, end=4000).L[int(0.8 * FS):int(3.3 * FS)])
             for s in (0, 127)}
        assert h[127] >= 1.5 * h[0], (note, h)


def test_motor_switches_without_a_click():
    """the motor switched on and off while a chord rings: the discs fade in and out (no step in the sound): the
    largest sample-to-sample change around each switch is no larger than the note's own"""
    lines = motor(127, 78, 0, on=0) + [cc(1000, 47, 127), cc(2000, 47, 0), cc(3000, 47, 127), cc(3500, 47, 0)]
    r = play("va-motclick", lines + [cc(30, 64, 127)], 57, on=300, dur=5000, end=4500)
    x = r.L
    d2 = np.abs(np.diff(x, 2))
    ref = d2[int(0.6 * FS):int(0.95 * FS)].max()
    for t in (1.0, 2.0, 3.0, 3.5):
        w = d2[int((t - 0.02) * FS):int((t + 0.1) * FS)].max()
        assert w <= 1.2 * ref, (t, w, ref)
    on, off = am_db(x[int(1.2 * FS):int(1.95 * FS)], 3.5)[0], am_db(x[int(2.2 * FS):int(2.95 * FS)], 3.5)[0]
    assert on >= 4.0 and off < 1.0, (on, off)


# ---- velocity
@pytest.mark.parametrize("note", KEYS)
def test_velocity_is_heard(note):
    x = {v: play(f"va-vel{v}-{note}", [], note, vel=v).L[int(0.2 * FS):int(0.7 * FS)] for v in (40, 117)}
    lv = {v: rms_db(x[v]) for v in x}
    assert lv[117] - lv[40] >= 10.0, (note, lv)


@pytest.mark.parametrize("note", (53, 69))
def test_velocity_brightens(note):
    """a harder strike is brighter: v40 -> v117 raises the strike's upper partials (V2 or V3 re V1, 5..300 ms) 6 dB or
    more (the audit, 2026-10-17: it was the same timbre, only louder; vibes.json
    T7g). F6 is left out: a top-octave bar struck with yarn is close to a pure fundamental at any velocity."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bar_cards as bc
    m = bc.measure({}, vels=(40, 117))[0]
    a, b = m[(note, 40)], m[(note, 117)]
    assert max(b["v2"] - a["v2"], b["v3"] - a["v3"]) >= 6.0, (note, a, b)


def test_key_vel_card_is_heard():
    """the FRODHES page's KEY VEL (K4) is the keys' velocity: 40 vs 117 (the user's) on the A4 key"""
    pre = ["100 turn 1 1", "120 turn 1 1", "140 turn 1 1", "160 turn 1 1", "200 btn 8"] + RAW + MOTOR_OFF
    lv = {}
    for kv in (40, 117):
        turns = ["300 turn 5 -14"] if kv == 40 else ["300 turn 5 5", "320 turn 5 1"]   # (more than a detent: x4)
        r = run(f"va-kvel{kv}", pre + turns + ["500 dump", "600 keys 0x10000", "1400 keys 0"], 1500)
        assert r.state(500)["card.2.3"] == str(kv)
        lv[kv] = rms_db(r.L[int(0.62 * FS):int(1.1 * FS)])
    assert lv[117] - lv[40] >= 10.0, lv


# ---- the shared effects on VIBES (a quick check that each still acts)
EFFECTS = {
    "drive": [cc(30, 81, 100)], "reverb": [cc(30, 91, 100)], "filter": [cc(30, 70, 30), cc(31, 74, 40)],
    "chorus": [cc(30, 85, 64), cc(31, 86, 127)], "phaser": [cc(30, 12, 127), cc(31, 13, 127)],
    "delay": [cc(30, 88, 64), cc(31, 89, 100)], "comp": [cc(30, 106, 127), cc(31, 107, 100)],
    "trem": [cc(30, 17, 64), cc(31, 21, 127), cc(32, 18, 127)],
}


def test_effects_act_on_vibes():
    lines = [cc(25, 81, 0), cc(25, 91, 0)]                     # dry
    base = play("va-fx-dry", lines, 69, raw=False, dur=1000, end=2500).L
    for name, fx in EFFECTS.items():
        x = play(f"va-fx-{name}", lines + fx, 69, raw=False, dur=1000, end=2500).L
        d = rms_db(x[int(0.2 * FS):] - base[int(0.2 * FS):]) - rms_db(base[int(0.2 * FS):])
        assert d > -20.0, (name, d)                              # a change of at least 1/10 of the sound
        assert np.abs(x).max() < 32767, name
