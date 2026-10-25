# SPDX-License-Identifier: GPL-3.0-only
"""The SMK-37 build's app layer on the host (firmware/src/frodhes_smk.h; no SMK-37 to try it on). The scripted parts
are tests/smk/*.txt (make test-smk: the knobs, the wheels, the faders' soft takeover, the pads, the pedal, the keys,
the SMK's own buttons and lights); here what needs numbers: the touch curve (keyvel.c's, at every TOUCH) against its formula, the notes'
velocities and pitches on USB-MIDI out, a bend half way up, the hidden cards, and the FM-1 build without any of it."""
import os
import re

from hostrun import ROOT, build, run
from test_smk_host import smk_build

SMK_H = os.path.join(ROOT, "firmware", "src", "frodhes_smk.h")
UMID = 15073


GAMMA = (115, 179, 282)         # keyvel.c KV_GAMMA, Q8: LIGHT MEDIUM HEAVY


def vel_map(u, kvel):           # keyvel.c kv_vmap
    if u <= UMID:
        v = 1 + ((kvel - 1) * u + UMID // 2) // UMID
    else:
        v = kvel + ((127 - kvel) * (u - UMID) + (32768 - UMID) // 2) // (32768 - UMID)
    return max(1, min(127, v))


def smk_vel(v, touch, kvel=96):  # frodhes_smk.h smk_vel in floating point: s = (v - 1) / 126, u = s ^ gamma
    if touch == 3:
        return kvel
    u = round(32768 * ((v - 1) / 126) ** (GAMMA[touch] / 256))
    return vel_map(u, kvel)


def sweep(lines, t, vels):
    for v in vels:
        lines += [f"{t} vel {v}", f"{t + 10} keys 0x80", f"{t + 40} keys 0"]
        t += 60
    return t


def test_touch_curve_is_keyvel_c():
    """the SMK's keybed velocity through keyvel.c's curve (kv_upow, kv_vmap) at every TOUCH (SETUP K4, as on the
    FM-1): within 1 of the formula, rising, the ends 1 and 127, FIXED = KEY VEL"""
    b = smk_build()
    lines = ["100 btn 7", "200 btn 7"]             # GLO (FM-1 id 7): TEMPO, then SETUP (K4 = TOUCH)
    t, vels, want = 300, list(range(1, 128)), []
    for touch, d in ((1, 0), (0, -1), (2, 2), (3, 1)):   # MEDIUM (the default), LIGHT, HEAVY, FIXED
        for i in range(abs(d)):                    # one step a turn (ui.c S_TOUCH), 20 ms apart
            lines.append(f"{t + 20 * i} turn 5 {1 if d > 0 else -1}")
        lines.append(f"{t + 50} dump")
        t = sweep(lines, t + 100, vels)
        want.append(touch)
    r = run("smk-touch", lines, t + 200, binary=b)
    ons = [vel for _, st, n, vel in r.midi_out if st == 0x90 and n == 60]
    assert len(ons) == 4 * len(vels), len(ons)
    names = [r.state(ts)["card3"] for ts in sorted(r.dumps)]   # SETUP K4, the TOUCH card
    assert all(r.state(ts)["page"] == "SETUP" and r.state(ts)["label3"] == "TOUCH" for ts in r.dumps)
    for i, touch in enumerate(want):
        got = ons[i * len(vels):(i + 1) * len(vels)]
        ref = [smk_vel(v, touch) for v in vels]
        assert all(abs(g - w) <= 1 for g, w in zip(got, ref)), (touch, got, ref)
        assert all(x <= y for x, y in zip(got, got[1:])), (touch, got)
        if touch < 3:
            assert got[0] == 1 and got[-1] == 127, (touch, got)
        else:
            assert set(got) == {96}, got
    assert ons[42] == 96                           # MEDIUM: a medium press (43) plays KEY VEL
    assert ons[127 + 42] > 96 > ons[254 + 42]      # the same press: LIGHT louder, HEAVY softer
    assert names == ["MEDIUM", "LIGHT", "HEAVY", "FIXED"], names


def test_key_velocity_on_usb_midi_out():
    """every key at its own velocity: 1 -> 1, a medium press (43) -> KEY VEL (96), 127 -> 127; KEY VEL 60 moves the
    middle (the curve's anchor) and leaves the ends"""
    b = smk_build()
    lines = []
    t = 200
    for v in (1, 20, 43, 80, 127):
        lines += [f"{t} vel {v}", f"{t + 10} keys 0x80", f"{t + 100} keys 0"]
        t += 300
    lines += [f"{t} turn 0 1"]                  # SELECT: the FRODHES page, K4 = KEY VEL (FM-1 ids: K4 = 5)
    for i in range(36):                          # 96 -> 60 one detent at a time
        lines.append(f"{t + 10 + 5 * i} turn 5 -1")
    t += 400
    for v in (1, 43, 127):
        lines += [f"{t} vel {v}", f"{t + 10} keys 0x80", f"{t + 100} keys 0"]
        t += 300
    r = run("smk-vel", lines, t + 200, binary=b)
    ons = [vel for _, st, n, vel in r.midi_out if st == 0x90 and n == 60]
    want = [smk_vel(v, 1, 96) for v in (1, 20, 43, 80, 127)] + [smk_vel(v, 1, 60) for v in (1, 43, 127)]
    assert all(abs(g - w) <= 1 for g, w in zip(ons, want)) and len(ons) == len(want), (ons, want)
    assert ons[:5][0] == 1 and ons[2] == 96 and ons[4] == 127 and ons[5:] == [1, 60, 127], ons


def test_outer_keys_pitches():
    """the outer ten keys: C3..E3 (keybed 0..4) and G#5..C6 (32..36) at OCTAVE 0, on USB-MIDI out as the keys are;
    OCTAVE + moves them too"""
    b = smk_build()
    lines, t = [], 200
    for k in (0, 4, 32, 36):
        lines += [f"{t} xkey {k} 90", f"{t + 50} xkey {k} 0"]
        t += 100
    lines += [f"{t} btn 1"]                      # OCT+ (FM-1 id 1)
    lines += [f"{t + 100} xkey 0 90", f"{t + 150} xkey 0 0"]
    r = run("smk-outer", lines, t + 300, binary=b)
    ons = [n for _, st, n, _ in r.midi_out if st == 0x90]
    assert ons == [48, 52, 80, 84, 60], ons


def test_bend_half_way():
    """the pitch wheel half way up: about a semitone (+-10 %), back at rest: exactly 0"""
    b = smk_build()
    r = run("smk-bend", ["400 wheel 0 1000", "600 wheel 0 535", "800 wheel 0 770", "1000 dump", "1100 wheel 0 535",
                         "1300 dump"], 1400, binary=b)
    bend = int(r.state(1000)["bend"])
    assert 3700 <= bend <= 4500, bend
    assert r.state(1300)["bend"] == "0"


def test_hidden_cards_and_words():
    """the SMK: no TRS IN (MIDI K2), no LEDS (DISPLAY K3), the hints in its printed names; the FM-1: as ever"""
    b = smk_build()
    s = run("smk-cards", ["300 dump"], 400, binary=b).state(300)
    assert s["label.18.1"] == "" and s["label.21.2"] == "" and s["card.18.1"] == "" and s["card.21.2"] == ""
    f = run("fm1-cards", ["300 dump"], 400, binary=build()).state(300)
    assert f["label.18.1"] == "TRS IN" and f["label.21.2"] == "LEDS" and f["card.18.1"] == "ON"


def test_smk_texts_no_longer_than_the_fm1s():
    """ui.c UI_SMK_TEXT: each SMK text fits where the FM-1's did (FONT_S, 240 px: at most 29 characters)"""
    src = open(os.path.join(ROOT, "firmware", "src", "ui.c")).read()
    tab = re.search(r"UI_SMK_TEXT\[\]\[2\] = \{(.*?)\n\};", src, re.S).group(1)
    pairs = re.findall(r'\{"([^"]*)", "([^"]*)"\}', tab)
    assert len(pairs) >= 12
    for fm1, smk in pairs:
        assert len(smk) <= max(len(fm1), 29), (fm1, smk)
        assert fm1 in src.replace(tab, ""), fm1  # (the FM-1 text is one the UI really shows)
