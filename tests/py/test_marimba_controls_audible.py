# SPDX-License-Identifier: GPL-3.0-only
"""Every MARIMBA control outside VOICE 1/2 changes the sound by its stated job (MARIMBA PLAN §9.5, §10.2 B2, §3.4),
on the host build, MARIMBA by CC 79 127, RAW unless the effects are the subject:

- RING (VOICE 2/2 K1, CC 102): 0 < 64 < 127 in the V1 T60 (30 dB fit), 127 / 0 >= 2x, on C2 / C4 / C6.
- MUTE (K2, CC 103; P_RELEASE's slot, the player's hand): MUTE 0 = rings out: the decay after the key-up within 1 % of
  the held note's; 0 < 64 < 127 in the stop; 127 stops >= 3x faster than 0 (C3, C5).
- The pedal (CC 64): held, the hand never lands, whatever MUTE says (MUTE 127, pedal down: within 1 % of held); a
  half pedal in between. All Notes Off (CC 123) still stops a ringing MUTE 0 note (the panic path).
- NOISE (K3, CC 104): the mallet's contact +6 dB or more from 64 to 127, at 127 a clear tick (within 35 dB of the
  note); gone by 100 ms (<= -80 dB re the note's peak, §2.4); no knock at the key-up (no damper on this type).
- TUNE (K4, the panel): +-50 cents within 1 cent.
- Velocity: v40 vs v117 >= 10 dB; harder is brighter: the attack centroid >= 1.4x v40 -> v117 on C4 MED, V2/V1
  +8 dB v40 -> v127 on C3 MED (§2.4).
- The MOTOR CCs 47-51: accepted and inaudible (no motor on this type).
- Each shared effect still changes MARIMBA.

The MARIMBA-specific numbers are expected failures (xfail, strict=False, "awaiting sync 2 real tables") only while
bars_tables.h is the sync-1 stub (MARIMBA = VIBES, which has a damper and a motor); the thresholds are the plan's."""
import os
import sys

import numpy as np
import pytest

from hostrun import FS, cc, run

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import marimba_scan as ms  # noqa: E402
import bar_measure as bm  # noqa: E402

AWAIT = pytest.mark.xfail(ms.stub_detected(), reason=ms.STUB_REASON, strict=False)
MARIMBA = [cc(20, 79, 127)]
RAW = [cc(21, 119, 127)]


def f0(n):
    return ms.freqs(n)["V1"]


def play(tag, setup, note, vel=100, on=200, dur=1500, end=None, raw=True):
    lines = MARIMBA + (RAW if raw else []) + setup + [f"{on} midi 0x90 {note} {vel}", f"{on + dur} midi 0x80 {note} 0"]
    return run(tag, lines, end or on + dur + 1500)


def after_release(x, n, t_off):
    return ms.release_rate(np.asarray(x, float), f0(n), t_off)


# ---- the DECAY page
@AWAIT
@pytest.mark.parametrize("note", (36, 60, 84))
def test_ring_changes_the_ring_time(note):
    t = [ms.t60(play(f"mca-ring{v}-{note}", [cc(30, 102, v), cc(30, 103, 0), cc(30, 104, 0)], note, dur=12000,
                     end=12500).L[int(0.2 * FS):], f0(note)) for v in (0, 64, 127)]
    assert t[0] < t[1] < t[2] and t[2] / t[0] >= 2.0, (note, t)


def _rates(tag, setups, note):
    return {k: after_release(play(f"{tag}{k}-{note}", s + [cc(30, 104, 0)], note).L, note, 1.7) for k, s in setups.items()}


@AWAIT
@pytest.mark.parametrize("note", (48, 72))
def test_mute_is_the_hand(note):
    r = _rates("mca-mute", {v: [cc(30, 103, v)] for v in (0, 64, 127)}, note)
    held = after_release(play(f"mca-held-{note}", [cc(30, 104, 0)], note, dur=3000, end=3500).L, note, 1.7)
    assert abs(r[0] / held - 1) <= 0.01, (note, r[0], held)          # MUTE 0 = rings out
    assert r[0] < r[64] < r[127] and r[127] / r[0] >= 3.0, (note, r)


@AWAIT
@pytest.mark.parametrize("note", (48, 72))
def test_pedal_holds_whatever_mute_says(note):
    r = _rates("mca-ped", {p: [cc(30, 103, 127), cc(30, 64, p)] for p in (0, 50, 127)}, note)
    held = after_release(play(f"mca-pheld-{note}", [cc(30, 104, 0)], note, dur=3000, end=3500).L, note, 1.7)
    assert abs(r[127] / held - 1) <= 0.01, (note, r[127], held)
    assert r[127] < r[50] < r[0] and r[0] / r[127] >= 3.0, (note, r)


def test_all_notes_off_stops_a_ringing_note():
    """the panic path: CC 123 mutes fully even at MUTE 0 (pedal down too)"""
    x = run("mca-panic", MARIMBA + RAW + [cc(30, 103, 0), cc(30, 64, 127), "200 midi 0x90 60 100",
                                          "1000 midi 0xB0 123 0"], 4000).L
    assert rms_db(x[int(0.5 * FS):int(0.9 * FS)]) > -40
    assert not np.any(x[int(3.0 * FS):]), "still sounding 2 s after All Notes Off"


def rms_db(x):
    return ms.rms_db(x)


@AWAIT
@pytest.mark.parametrize("note", (48, 60, 84))
def test_noise_is_the_contact_and_nothing_at_the_key_up(note):
    x = {v: play(f"mca-nz{v}-{note}", [cc(30, 104, v), cc(30, 103, 64)], note).L for v in (0, 64, 127)}
    s0 = ms.onset(x[0], 200)
    seg = lambda v, a, b: x[v][s0 + int(a * FS):s0 + int(b * FS)]          # noqa: E731
    contact = {v: rms_db(seg(v, 0, 0.06) - seg(0, 0, 0.06)) for v in (64, 127)}
    ring = rms_db(seg(0, 0, 0.06))
    assert contact[127] - contact[64] >= 6.0, (note, contact)
    assert contact[127] > ring - 35.0, (note, contact[127], ring)
    peak = 20 * np.log10(np.abs(seg(0, 0, 0.05)).max() / 32767 + 1e-12)
    late = rms_db(seg(64, 0.1, 0.12) - seg(0, 0.1, 0.12))
    assert late <= peak - 80.0, (note, late, peak)                         # gone by 100 ms
    k0 = int(1.7 * FS)                                                    # the key-up (on 200 ms + 1500)
    knock = rms_db(x[127][k0:k0 + int(0.15 * FS)] - x[0][k0:k0 + int(0.15 * FS)])
    note_lvl = rms_db(x[0][int(1.6 * FS):int(1.7 * FS)])
    assert knock <= note_lvl - 60.0, (note, knock, note_lvl)              # no damper: no knock


def test_tune_by_the_panel():
    """VOICE 2/2's K4 (the panel; TUNE has no CC): -50 / +50 cents within 1 cent"""
    got = {}
    for tu in (-50, 0, 50):
        lines = MARIMBA + RAW + [f"{t} {s}" for t, s in bm.tune_lines(300, tu)] + ["450 dump", "1000 midi 0x90 69 100",
                                                                                   "3500 midi 0x80 69 0"]
        r = run(f"mca-tune{tu}", lines, 3600)
        if tu:
            assert r.state(450)["vlabel.1.3"] == "TUNE"
        x = r.L[int(1.3 * FS):int(3.3 * FS)]
        n = 1 << 20
        sp = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
        fr = np.fft.rfftfreq(n, 1 / FS)
        sel = np.flatnonzero((fr > 400) & (fr < 480))
        i = sel[np.argmax(sp[sel])]
        a, b, c = np.log(sp[i - 1:i + 2])
        got[tu] = (i + 0.5 * (a - c) / (a - 2 * b + c)) * FS / n
    for tu in (-50, 50):
        assert abs(1200 * np.log2(got[tu] / got[0]) - tu) < 1.0, (tu, got)


# ---- velocity
@pytest.mark.parametrize("note", (48, 60, 84))
def test_velocity_is_heard(note):
    lv = {v: rms_db(play(f"mca-vel{v}-{note}", [], note, vel=v).L[int(0.2 * FS):int(0.7 * FS)]) for v in (40, 117)}
    assert lv[117] - lv[40] >= 10.0, (note, lv)


@AWAIT
def test_velocity_brightens():
    r = ms.strike_set(ms.bm_tag("mca-velbright"), ms.base(BARK=ms.MED), (48, 60), (40, 117, 127), gap=2000, hold=900)
    q = r[(60, 117)]["cen_a"] / r[(60, 40)]["cen_a"]
    up = r[(48, 127)]["v2"] - r[(48, 40)]["v2"]
    assert q >= 1.4 and up >= 8.0, (q, up)


# ---- no motor
@AWAIT
def test_motor_ccs_are_inert():
    """CC 47-51 (ON, DEPTH, SPEED, SHAPE, SYNC) at their ends: the same sound as without them, and nothing breaks"""
    mot = [cc(30, 47, 127), cc(31, 48, 127), cc(32, 49, 100), cc(33, 50, 127)]
    a = play("mca-mot0", [], 57, dur=3000).L
    b = play("mca-mot1", mot, 57, dur=3000).L
    assert np.array_equal(a, b), float(np.abs(a - b).max())


def test_motor_ccs_are_accepted():
    """the plumbing: the motor CCs on MARIMBA leave TYPE and the VOICE pages as they are (no MOTOR page)"""
    r = run("mca-motpage", MARIMBA + [cc(30, 47, 127), cc(31, 48, 127), "100 dump", "200 btn 8", "250 turn 0 1",
                                      "300 turn 0 1", "350 turn 0 1", "400 dump"], 500)
    assert r.state(100)["card.2.0"] == "MARIMBA"
    assert r.state(400)["page"] == "COMPRESSOR", r.state(400)["page"]


# ---- the shared effects on MARIMBA
EFFECTS = {
    "drive": [cc(30, 81, 100)], "reverb": [cc(30, 91, 100)], "filter": [cc(30, 70, 30), cc(31, 74, 40)],
    "chorus": [cc(30, 85, 64), cc(31, 86, 127)], "phaser": [cc(30, 12, 127), cc(31, 13, 127)],
    "delay": [cc(30, 88, 64), cc(31, 89, 100)], "comp": [cc(30, 106, 127), cc(31, 107, 100)],
    "trem": [cc(30, 17, 64), cc(31, 21, 127), cc(32, 18, 127)],
}


def test_effects_act_on_marimba():
    lines = [cc(25, 81, 0), cc(25, 91, 0)]
    base = play("mca-fx-dry", lines, 69, raw=False, dur=1000, end=2500).L
    for name, fx in EFFECTS.items():
        x = play(f"mca-fx-{name}", lines + fx, 69, raw=False, dur=1000, end=2500).L
        dd = rms_db(x[int(0.2 * FS):] - base[int(0.2 * FS):]) - rms_db(base[int(0.2 * FS):])
        assert dd > -20.0, (name, dd)
        assert np.abs(x).max() < 32767, name
