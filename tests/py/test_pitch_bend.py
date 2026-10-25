# SPDX-License-Identifier: GPL-3.0-only
"""MIDI pitch bend (0xE0, +-2 semitones) on the Rhodes engine (engine.c eng_bend_set, rhodes.c rh_retune): a held C4 /
A1 / C6 bent to +8191 / -8192 lands within 1 cent of the target pitch, a note struck while bent is in tune, a sweep
does not click (the largest sample step stays at the unbent note's, scaled by the highest pitch reached) and leaves the
level where it was (0.1 dB), CC 121 resets the bend. The retune is spread (rh_retune): a block retunes at most a
quarter of the voices, round-robin, so a full chord reaches the bend within 4 blocks, every note in tune, and a sweep
over a full chord neither clicks nor moves the level. The engine alone (CC 119 RAW): the pitch, not the effects."""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run, voices  # noqa: E402

NOTES = {"C4": 60, "A1": 33, "C6": 84}
T_ON, T_BEND = 100, 1000                 # ms: the strike, the bend
MEAS = (1.6, 3.6)                        # s: the window the pitch is read in
UP, DOWN = 8191, -8192


def bend_ev(t, b):
    v = b + 8192
    return f"{t} midi 0xE0 {v & 127} {v >> 7}"


def ratio(b):
    return 2.0 ** (b / 8192 * 2 / 12)


def cents(a, b):
    return 1200 * np.log2(a / b)


def take(tag, n, extra=(), on=T_ON, end=4000):
    ev = ["40 midi 0xB0 119 127", f"{on} midi 0x90 {n} 100", f"{end - 100} midi 0x80 {n} 0"] + list(extra)
    return run(f"bend-{tag}", ev, end)


def f0_of(x, guess, span=0.05):
    """the fundamental's frequency: the peak of the Hann-windowed spectrum near guess, refined by golden-section search
    on the windowed DTFT (better than 0.001 Hz on a 2 s window)"""
    x = np.asarray(x, float)
    x = x - x.mean()
    w = np.hanning(len(x))
    t = np.arange(len(x)) / FS
    xw = x * w

    def mag(f):
        return -abs(np.dot(xw, np.exp(-2j * np.pi * f * t)))

    fs = np.linspace(guess * (1 - span), guess * (1 + span), 401)
    f = fs[int(np.argmin([mag(q) for q in fs]))]
    a, b = f - (fs[1] - fs[0]), f + (fs[1] - fs[0])
    g = (np.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(40):
        if mag(c) < mag(d):
            b, d = d, c
            c = b - g * (b - a)
        else:
            a, c = c, d
            d = a + g * (b - a)
    return (a + b) / 2


def pitch(r, n, b=0):
    """the fundamental's frequency in the measuring window (the guess: equal temperament, bent by b)"""
    return f0_of(r.seg(*MEAS), 440.0 * 2 ** ((n - 69) / 12) * ratio(b))


_ref = {}


def ref(name):
    if name not in _ref:
        _ref[name] = take(f"ref-{name}", NOTES[name])
    return _ref[name]


@pytest.mark.parametrize("name", list(NOTES))
@pytest.mark.parametrize("b", [UP, DOWN], ids=["up", "down"])
def test_held_note_bends_to_the_target(name, b):
    n = NOTES[name]
    f_ref = pitch(ref(name), n)
    r = take(f"held-{name}-{b}", n, [bend_ev(T_BEND, b), f"{T_BEND + 10} expect state bend {b}"])
    f = pitch(r, n, b)
    err = cents(f / f_ref, ratio(b))
    assert abs(err) < 1.0, f"{name} bent {b}: {f:.4f} Hz vs {f_ref:.4f} x {ratio(b):.6f}: {err:+.3f} cents"


@pytest.mark.parametrize("name", list(NOTES))
def test_new_note_while_bent_is_in_tune(name):
    n = NOTES[name]
    f_ref = pitch(ref(name), n)
    r = take(f"new-{name}", n, [bend_ev(50, UP)])
    err = cents(pitch(r, n, UP) / f_ref, ratio(UP))
    assert abs(err) < 1.0, f"{name} struck at +{UP}: {err:+.3f} cents"


def sweep(t0, b_from, b_to, ms=500, step=5):
    """a bend from b_from to b_to and back, an event every step ms"""
    ev, k = [], ms // step
    for i in range(k + 1):
        ev.append(bend_ev(t0 + i * step, round(b_from + (b_to - b_from) * i / k)))
    for i in range(1, k + 1):
        ev.append(bend_ev(t0 + ms + i * step, round(b_to + (b_from - b_to) * i / k)))
    return ev


def max_step(x):
    return float(np.max(np.abs(np.diff(np.asarray(x, float)))))


@pytest.mark.parametrize("name", list(NOTES))
@pytest.mark.parametrize("b", [UP, DOWN], ids=["up", "down"])
def test_sweep_no_click_level_back(name, b):
    n = NOTES[name]
    a = ref(name)
    r = take(f"sweep-{name}-{b}", n, sweep(T_BEND, 0, b) + [f"{T_BEND + 1100} expect state bend 0"])
    win = (T_BEND / 1000 - 0.05, T_BEND / 1000 + 1.1)
    s_ref, s = max_step(a.seg(*win)), max_step(r.seg(*win))
    lim = s_ref * max(1.0, ratio(b)) * 1.01 + 1
    assert s <= lim, f"{name} sweep to {b}: max step {s:.0f} vs unbent {s_ref:.0f} (limit {lim:.0f})"
    lv = 20 * np.log10(np.sqrt(np.mean(r.seg(2.5, 3.5) ** 2)) / np.sqrt(np.mean(a.seg(2.5, 3.5) ** 2)))
    assert abs(lv) < 0.1, f"{name} after the sweep to {b}: level {lv:+.3f} dB"


def test_cc121_resets_and_back_in_tune():
    n = NOTES["C4"]
    f_ref = pitch(ref("C4"), n)
    r = take("cc121", n, [bend_ev(T_BEND, UP), f"{T_BEND + 200} midi 0xB0 121 0", f"{T_BEND + 210} expect state bend 0"])
    err = cents(pitch(r, n), f_ref)
    assert abs(err) < 0.2, f"after CC 121: {err:+.3f} cents"


def test_bend_zero_is_the_unbent_render():
    """a bend to +8191 and back to 8192 before the note: the render is the unbent one, sample for sample"""
    r = take("zero", NOTES["C4"], [bend_ev(20, UP), bend_ev(30, 0)])
    a = ref("C4")
    assert np.array_equal(r.L, a.L) and np.array_equal(r.R, a.R)


def test_vibes_ignores_the_bend():
    ev = ["30 midi 0xB0 79 0"]
    a = run("bend-vibes-ref", ev + ["40 midi 0xB0 119 127", "200 midi 0x90 60 100", "2500 midi 0x80 60 0"], 3000)
    b = run("bend-vibes", ev + ["40 midi 0xB0 119 127", "200 midi 0x90 60 100", bend_ev(600, UP), bend_ev(700, DOWN),
                                 "2500 midi 0x80 60 0"], 3000)
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R)


# ---- the retune spread: a full chord (one note per voice; no note's low partials near another note's fundamental)
CHORD = [36, 40, 43, 47, 50, 53, 57, 58]
BLOCK_MS = 256 * 1000 / FS


def chord(tag, extra=(), end=4000):
    ev = ["40 midi 0xB0 119 127"] + [f"{T_ON + i} midi 0x90 {n} 100" for i, n in enumerate(CHORD)]
    ev += [f"{end - 100} midi 0x80 {n} 0" for n in CHORD] + list(extra)
    return run(f"bend-chord-{tag}", sorted(ev, key=lambda e: int(e.split()[0])), end, check=False)


_chord_ref = {}


def chord_ref():
    if "r" not in _chord_ref:
        _chord_ref["r"] = chord("ref")
    return _chord_ref["r"]


def test_spread_a_quarter_per_block_all_within_4_blocks():
    """the pending voices after a bend, read every ms: each block retunes at most ceil(V/4) voices, and none are left
    4 blocks after the bend's first block"""
    nv = voices()
    per = (nv + 3) // 4
    probes = [f"{t} expect state rtpend ?" for t in range(T_BEND + 1, T_BEND + 40)]
    r = chord("spread", [bend_ev(T_BEND, UP)] + probes, end=1500)
    seen = [(int(t), int(v)) for t, v in re.findall(r"^\s*(\d+) ms  FAIL expect state rtpend = \"\?\" \(is \"(\d+)\"\)",
                                                    r.out, re.M)]
    assert len(seen) == len(probes)
    masks = [m for _, m in seen]
    assert masks[0] == 0 or bin(masks[0]).count("1") >= nv - per, "the bend reached the ISR before the probes"
    first = next(t for t, m in seen if m)
    done = next(t for t, m in seen if t > first and m == 0)
    prev = (1 << nv) - 1
    for _, m in seen:
        if m:
            assert bin(prev & ~m).count("1") <= per, f"a block retuned more than {per} voices: {prev:b} -> {m:b}"
            prev = m
    assert done - first <= 3 * BLOCK_MS + 1, f"retuned {done - first} ms after the first block (4 blocks: 3 more)"


@pytest.mark.parametrize("b", [UP, DOWN], ids=["up", "down"])
def test_chord_every_note_bends_to_the_target(b):
    a = chord_ref()
    r = chord(f"held-{b}", [bend_ev(T_BEND, b)])
    for n in CHORD:
        g = 440.0 * 2 ** ((n - 69) / 12)
        f_ref = f0_of(a.seg(*MEAS), g, 0.02)
        f = f0_of(r.seg(*MEAS), g * ratio(b), 0.02)
        err = cents(f / f_ref, ratio(b))
        assert abs(err) < 1.0, f"note {n} in the chord bent {b}: {err:+.3f} cents"


@pytest.mark.parametrize("b", [UP, DOWN], ids=["up", "down"])
def test_chord_sweep_no_click_level_back(b):
    a = chord_ref()
    r = chord(f"sweep-{b}", sweep(T_BEND, 0, b))
    win = (T_BEND / 1000 - 0.05, T_BEND / 1000 + 1.1)
    s_ref, s = max_step(a.seg(*win)), max_step(r.seg(*win))
    lim = s_ref * max(1.0, ratio(b)) * 1.01 + 1
    assert s <= lim, f"chord sweep to {b}: max step {s:.0f} vs unbent {s_ref:.0f} (limit {lim:.0f})"
    lv = 20 * np.log10(np.sqrt(np.mean(r.seg(2.5, 3.5) ** 2)) / np.sqrt(np.mean(a.seg(2.5, 3.5) ** 2)))
    assert abs(lv) < 0.1, f"the chord after the sweep to {b}: level {lv:+.3f} dB"
