# SPDX-License-Identifier: GPL-3.0-only
"""SUSTAIN LEDS (issue #35): settings_t.sus_leds, what a key shows under LEDS ALL / SCALE (where a sounding key goes
dark) once it is let go while the sustain rings its note on: 0 DARK (every record before: dark until the sound stops)
or 1 LIT (the map again at once). A former reserved byte, so every older record reads 0 = DARK; above 1 sanitized to
DARK. LEDS ROOTS / PLAYED (the sounding keys lit) are the same either way. The record stays 256 B.

The card: DISPLAY 2's K1, one SELECT detent on from DISPLAY (tests/scripts/sus_leds.txt turns it); here it is saved
and back after a power cycle. The other tests write the byte into a saved record and switch on with it (every value,
the old and bad ones too). The pedal is REC tapped (the latch); key 0 = F3 = LED 14."""
import pytest

from hostrun import run
from test_persistence import boot, flash_file, put_settings, read_img, settings_record
from test_play_led import DISPLAY, offsets

PEDAL = "btn 13"
PG2 = 25                                                        # ui.c PG_DISPLAY2 (host: card.<page>.<k>)
# LEDS card (DISPLAY K3) from ROOTS: +1 ALL, +2 SCALE (no scale on: SCALE lights every key, as ALL); -1 PLAYED
LEDS_TURN = {"ALL": 1, "SCALE": 2, "ROOTS": 0, "PLAYED": -1}
# the pedal latched, F3 struck and let go under it (ringing), F3's state sampled; the pedal let go, sampled again
PLAY = [f"300 {PEDAL}", "400 keys 0x1", "450 dump", "600 keys 0", "700 dump", "900 dump", f"1000 {PEDAL}", "1300 dump"]


def lay():
    o = offsets([], ["vel_resp", "sus_leds", "leds"])
    return o


def test_layout():
    """256 B on both boards; sus_leds after vel_resp, out of the reserve"""
    o = offsets([], ["vel_resp", "sus_leds"])
    assert o == {"vel_resp": 236, "sus_leds": 237, "size": 256}, o
    o = offsets(["-DBOARD_SMK37=1", "-DFR_VOICES=7u"], ["play_off", "vel_resp", "sus_leds"])
    assert o == {"play_off": 237, "vel_resp": 238, "sus_leds": 239, "size": 256}, o


def with_byte(tag, leds, byte):
    """a saved record with LEDS set on the card, then sus_leds written as byte"""
    fl = flash_file(f"susled-{tag}")
    turns = [f"{500 + 50 * i} turn 4 {1 if LEDS_TURN[leds] > 0 else -1}" for i in range(abs(LEDS_TURN[leds]))]
    run(f"susled-{tag}-0", DISPLAY + turns + ["450 turn 2 1", "4500 dump"], 4700, flash=fl)   # (COLOR: a record)
    img = read_img(fl)
    rec = bytearray(settings_record(img))
    o = lay()
    assert len(rec) == 256 and rec[o["sus_leds"]] == 0
    rec[o["sus_leds"]] = byte
    a = bytearray(img); put_settings(a, rec); open(fl, "wb").write(a)
    r = boot(f"susled-{tag}", fl, PLAY, 1400, check_preset=False)
    assert r.state(450)["sus"] == "1" and r.state(1300)["sus"] == "0"
    assert r.state(100)["card.21.2"] == leds, r.state(100)["card.21.2"]
    return r


def led(r, t):
    return r.state(t)["led.14"]


@pytest.mark.parametrize("leds", ["ALL", "SCALE"])
@pytest.mark.parametrize("byte, want", [(0, "0"), (1, "1"), (2, "0"), (255, "0")])
def test_map_keys(leds, byte, want):
    """ALL / SCALE: held, dark either way; let go under the pedal: DARK (0, and anything above 1) stays dark while it
    rings, LIT (1) lit at once; the pedal up: lit either way"""
    r = with_byte(f"{leds}-{byte}", leds, byte)
    assert led(r, 450) == "0", "held: dark"
    assert int(r.state(700)["voices"]) >= 1, "the note ended under the pedal: the test shows nothing"
    assert (led(r, 700), led(r, 900)) == (want, want), (leds, byte)
    assert led(r, 1300) == "1"


@pytest.mark.parametrize("leds", ["ROOTS", "PLAYED"])
@pytest.mark.parametrize("byte", [0, 1])
def test_sounding_keys_unchanged(leds, byte):
    """ROOTS / PLAYED: lit while held and while the pedal rings it, dark after, whatever the byte"""
    r = with_byte(f"{leds}-{byte}", leds, byte)
    assert (led(r, 450), led(r, 700), led(r, 900), led(r, 1300)) == ("1", "1", "1", "0"), (leds, byte)


def test_card_saved_and_back_after_a_power_cycle():
    """DISPLAY 2: SELECT on from DISPLAY, K1 SUSTAIN LEDS DARK; turned to LIT, saved (the byte 1), LIT after a power
    cycle, and the key lit at once when let go under the pedal (LEDS ALL, saved with it)"""
    fl = flash_file("susled-card")
    r1 = run("susled-card1", DISPLAY + ["500 turn 4 1", "600 turn 0 1", "650 dump", "700 turn 2 1", "750 dump",
                                        "4500 dump"], 4700, flash=fl)
    s = r1.state(650)
    assert (s["page"], s["label0"], s["card0"], s["label1"], s["card1"]) == ("DISPLAY 2", "SUSTAIN LEDS", "DARK", "", ""), s
    assert r1.state(750)["card0"] == "LIT" and r1.saves, "turned and saved"
    rec = settings_record(read_img(fl))
    assert rec[lay()["sus_leds"]] == 1
    r2 = boot("susled-card2", fl, PLAY, 1400, check_preset=False)
    assert (r2.state(100)[f"card.{PG2}.0"], r2.state(100)["card.21.2"]) == ("LIT", "ALL")
    assert (led(r2, 450), led(r2, 700), led(r2, 1300)) == ("0", "1", "1")
