# SPDX-License-Identifier: GPL-3.0-only
"""The key LEDs under the sustain pedal (frodhes.c's key-LED loop, notes.c ped.sus): a key is lit while it is held,
and under the pedal while the pedal holds its note: the note was held when the pedal went down, or started while the
pedal was down, and a voice still sounds it. A note let go before the pedal (its voice in the damped tail, or a
MARIMBA bar ringing on, no damper) does not light when the pedal goes down (the user, FM-1, 2026-10-20: "the key
lights again although nothing sounds"). The pedal is REC tapped (the latch). Rhodes, VIBES and MARIMBA alike.

And no flicker: with the pedal down and 5-6 notes (fewer than the voices: 9 on every engine) every key, once lit by its press, stays
lit to the end; with 12-16 notes (voices stolen) a key may go dark once (its voice taken), and never lights again
without a new press of it. Sampled every 5 ms from the host dump (led.N: the key's LED as ui_leds sets it; the
BRIGHTNESS duty is the HAL's table, tests/py/test_led_tables.py), at BRIGHTNESS 10 and 3."""
import pytest

from hostrun import run, voices

ENGINES = {"rhodes": [], "vibes": ["20 midi 0xB0 79 0"], "marimba": ["20 midi 0xB0 79 127"]}
PEDAL = "btn 13"                                             # REC: a tap latches the sustain


def led(r, t, k):
    return r.state(t)[f"led.{14 + k}"]


@pytest.mark.parametrize("eng", ENGINES)
def test_a_note_let_go_before_the_pedal_stays_dark(eng):
    """the user's sequence: F3 (key 0) struck and let go, the pedal tapped while its voice is still active (the
    release tail, silent or nearly): the key stays dark; the others too"""
    r = run(f"ledsus-before-{eng}", ENGINES[eng] + ["100 keys 0x1", "150 dump", "300 keys 0", "330 dump", f"340 {PEDAL}",
                                                    "360 dump", "400 dump", "450 dump"], 500)
    assert led(r, 150, 0) == "1" and r.state(330)["sus"] == "0" and led(r, 330, 0) == "0"
    assert r.state(360)["sus"] == "1"
    assert int(r.state(360)["voices"]) >= 1, "the voice ended before the pedal: the test shows nothing"
    for t in (360, 400, 450):
        assert all(led(r, t, k) == "0" for k in range(27)), (eng, t, r.state(t)["voices"])


@pytest.mark.parametrize("eng", ENGINES)
def test_held_then_pedal_and_pedal_then_let_go_light(eng):
    """F3 held when the pedal goes down, then let go: lit while the pedal holds it; A3 (key 4) struck and let go
    with the pedal down: lit; the pedal up: both dark at once (the pedal no longer holds them)"""
    r = run(f"ledsus-held-{eng}", ENGINES[eng] + ["100 keys 0x1", f"200 {PEDAL}", "300 keys 0", "400 dump",
                                                  "500 keys 0x10", "600 keys 0", "700 dump", "900 dump",
                                                  f"1000 {PEDAL}", "1040 dump"], 1100)
    assert r.state(400)["sus"] == "1" and led(r, 400, 0) == "1"
    assert led(r, 700, 0) == "1" and led(r, 700, 4) == "1"
    assert led(r, 900, 0) == "1" and led(r, 900, 4) == "1"
    assert r.state(1040)["sus"] == "0" and led(r, 1040, 0) == "0" and led(r, 1040, 4) == "0"


def strike(keys, t0, gap=120, dur=60):
    """keys struck one after another (each let go after dur ms): the script lines and each key's press time"""
    ls, at = [], {}
    for i, k in enumerate(keys):
        t = t0 + i * gap
        ls += [f"{t} keys {1 << k:#x}", f"{t + dur} keys 0"]
        at.setdefault(k, []).append(t)
    return ls, at


def sampled(eng, tag, keys, dim_turns, end):
    pre = ["100 btn 7", "150 btn 7", "200 btn 7", "250 btn 7"] + [f"{300 + 20 * i} turn 3 -1" for i in range(dim_turns)]
    ls, at = strike(keys, 600)
    ls = ENGINES[eng] + pre + [f"500 {PEDAL}"] + ls + [f"{t} dump" for t in range(520, end, 5)]
    r = run(f"ledsus-{tag}-{eng}-{dim_turns}", ls, end + 50)
    assert r.state(520)["sus"] == "1" and r.state(520)["card1"] == str(10 - dim_turns)
    return r, at


def changes(r, k, end):
    """(time, value) at each change of key k's LED, from 520 ms"""
    out, last = [], None
    for t in range(520, end, 5):
        v = led(r, t, k)
        if v != last:
            out.append((t, v))
            last = v
    return out


def check_key(r, eng, k, at, pressed_all, end):
    """key k: lit within 10 ms of each press of it; relit only at a press of it; dark only where a voice ended
    (the voice count fell) or a voice was taken for a new note (a press within 20 ms): never a blink"""
    ch = changes(r, k, end)
    assert ch[0][1] == "0", (eng, k, ch)
    for t, v in ch[1:]:
        if v == "1":
            assert any(0 <= t - p <= 10 for p in at[k]), (eng, k, ch, at[k], "lit without a press: a blink")
        else:
            fell = int(r.state(t)["voices"]) < max(int(r.state(t - d)["voices"]) for d in (5, 10))
            stolen = any(0 <= t - p <= 20 for p in pressed_all)
            assert fell or stolen, (eng, k, ch, "dark with its voice still there")
    return ch


@pytest.mark.parametrize("dim_turns", [0, 7])                   # BRIGHTNESS 10 and 3
@pytest.mark.parametrize("eng", ENGINES)
def test_few_notes_under_the_pedal_never_blink(eng, dim_turns):
    """6 keys struck under the pedal (fewer than the voices): each lights at its press and stays lit until its own note
    ends (a Rhodes or VIBES note outlasts the 2.3 s here: the Rhodes keys all stay lit; a MARIMBA bar dies sooner)"""
    keys = [0, 4, 7, 11, 14, 19]
    end = 600 + 120 * len(keys) + 1500
    r, at = sampled(eng, "few", keys, dim_turns, end)
    for k in keys:
        ch = check_key(r, eng, k, at, [p for v in at.values() for p in v], end)
        if eng == "rhodes":
            assert [v for _, v in ch] == ["0", "1"], (eng, k, ch)


@pytest.mark.parametrize("dim_turns", [0, 7])
@pytest.mark.parametrize("eng", ENGINES)
def test_many_notes_under_the_pedal_go_dark_once_and_only_at_a_steal(eng, dim_turns):
    """16 keys struck under the pedal (8 or 9 voices: notes stolen): a key lights at its press; it goes dark only when its
    voice is taken by a later note or its note ends, then stays dark (it never relights without a press); the
    Rhodes: the last 9 struck (its voices, hostrun.voices) stay lit"""
    keys = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24, 26]
    end = 600 + 120 * len(keys) + 1500
    r, at = sampled(eng, "many", keys, dim_turns, end)
    for i, k in enumerate(keys):
        ch = check_key(r, eng, k, at, [p for v in at.values() for p in v], end)
        assert len(ch) <= 3, (eng, k, ch)
        if eng == "rhodes" and i >= len(keys) - voices("rhodes"):
            assert [v for _, v in ch] == ["0", "1"], (eng, k, ch, "one of the last notes (one per voice) went dark")
