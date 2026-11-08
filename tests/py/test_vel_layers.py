# SPDX-License-Identifier: GPL-3.0-only
"""The two velocity layers (0.9.10, the user's decision).

1. Input: TOUCH (SETUP) and KEY VEL (FRODHES) turn a press of the FM-1's own keys into a velocity. They apply to
   those keys only: a MIDI note's velocity (USB or TRS) reaches the engine as it was sent, because the keyboard that
   sent it has its own sensitivity.
2. Sound: RESPONSE (FULL / LIGHT, CC 40) is how the electric piano answers a velocity, the same for every source. It
   is a gain inside the engine (rhodes.c rh_dyn) and never rewrites the velocity: the engine, the MIDI out, the
   sequencer's recording and the arpeggiator all keep the velocity played.

The host states (frodhes.c host_state): engvel = the velocity of the Rhodes's last strike (rhodes.c rh_strike, host
only); lastvel = the last FM-1 key's velocity (keyvel.c); sqvel = the sequencer's last recorded velocity; arpv = the
arpeggiator's step velocity. The MIDI out is the host's "MIDI out" lines (tests/host.c midi_send).
"""
import re

import pytest

import hostrun

TOUCH = ["LIGHT", "MEDIUM", "HEAVY", "FIXED"]   # SETUP K4, from MEDIUM (the default): LIGHT is one step down


def setup_lines(touch, key_vel):
    """the velocity page (the last VOICE page: HOME, SELECT x3 on the Rhodes): K2 TOUCH (encoder 3, one step a turn),
    K3 KEY VEL (encoder 4; 96 by default), each checked on its card"""
    ls = ["100 btn 8", "150 expect state page FRODHES", "160 turn 0 1", "170 turn 0 1", "180 turn 0 1",
          "250 expect state vpage 3/3", "250 expect state touch MEDIUM"]
    d = TOUCH.index(touch) - 1
    for k in range(abs(d)):
        ls.append(f"{300 + 10 * k} turn 3 {1 if d > 0 else -1}")
    ls += [f"350 expect state touch {touch}", "400 turn 4 -200", "450 expect state card2 1"]   # (fast turns speed up)
    t = 500
    if key_vel == 127:
        ls.append(f"{t} turn 4 200")
    else:
        for k in range(key_vel - 1):                                # one detent at a time: no acceleration
            ls.append(f"{t + 40 * k} turn 4 1")
        t += 40 * key_vel
    ls.append(f"{t + 50} expect state card2 {key_vel}")
    return ls, t + 100


@pytest.mark.parametrize("touch", TOUCH)
@pytest.mark.parametrize("key_vel", [1, 64, 127])
def test_midi_velocity_reaches_the_engine_unchanged(touch, key_vel):
    """every MIDI velocity 1..127 at every TOUCH and KEY VEL: the engine strikes with exactly that velocity"""
    ls, t = setup_lines(touch, key_vel)
    for v in range(1, 128):
        ls += [f"{t} midi 0x90 60 {v}", f"{t + 10} expect state engvel {v}", f"{t + 15} midi 0x80 60 0"]
        t += 30
    hostrun.run(f"vlay-midi-{touch}-{key_vel}", ls, end_ms=t + 50)


def test_midi_velocity_on_the_trs_jack_unchanged():
    """the same on the TRS jack (TRS IN on by default), at the extreme TOUCH and KEY VEL settings"""
    ls, t = setup_lines("FIXED", 1)
    for v in (1, 2, 20, 63, 64, 100, 126, 127):
        ls += [f"{t} trs 0x90 60 {v}", f"{t + 10} expect state engvel {v}", f"{t + 15} trs 0x80 60 0"]
        t += 30
    hostrun.run("vlay-trs", ls, end_ms=t + 50)


MIDI_OUT = re.compile(r"MIDI out 90 (\d+) (\d+)")


def keys_and_out(resp):
    """the FM-1's C4 at four presses (the driven readings: firm, medium, soft, the host's none = CONTEXT) and a MIDI
    note at v1..127 under RESPONSE resp (CC 40): what reached the engine, what went out over USB-MIDI"""
    ls = [f"100 midi 0xB0 40 {resp}", f"150 expect state vresp {resp >> 6}/{resp >> 6}"]
    t, keys = 300, []
    for raw in (500, 743, 900, 0):
        if raw:
            ls.append(f"{t} vkey 7 {raw}")
        ls += [f"{t + 10} keys 0x80", f"{t + 200} keys 0"]
        keys.append(t + 40)
        t += 300
    for v in range(1, 128):
        ls += [f"{t} midi 0x90 60 {v}", f"{t + 10} expect state engvel {v}", f"{t + 15} midi 0x80 60 0"]
        t += 30
    r = hostrun.run(f"vlay-keys-{resp}", ls, end_ms=t + 50)
    return [int(m.group(2)) for m in MIDI_OUT.finditer(r.out)]


def test_response_changes_the_sound_not_the_velocity():
    """FULL and LIGHT: the FM-1's keys send the same velocities out (the MIDI notes are not echoed), and each key's
    velocity is the one the engine struck with (checked against lastvel inside the run)"""
    full, light = keys_and_out(0), keys_and_out(100)
    assert len(full) == 4 and full == light, (full, light)
    assert full[0] > full[1] > full[2], full                      # firm > medium > soft: TOUCH's curve, untouched


@pytest.mark.parametrize("resp", [0, 100])
def test_key_velocity_reaches_the_engine_as_sent_out(resp):
    """each FM-1 key press: the velocity sent out over USB-MIDI is the one the engine struck with, under both
    RESPONSEs (a first run reads the MIDI out, a second expects those velocities in the engine)"""
    def lines(want=None):
        ls, t = [f"100 midi 0xB0 40 {resp}"], 300
        for i, raw in enumerate((450, 600, 743, 850, 950)):
            ls += [f"{t} vkey 7 {raw}", f"{t + 10} keys 0x80", f"{t + 200} keys 0"]
            if want:
                ls.append(f"{t + 40} expect state engvel {want[i]}")
            t += 300
        return ls, t
    ls, t = lines()
    sent = [int(m.group(2)) for m in MIDI_OUT.finditer(hostrun.run(f"vlay-kv-{resp}", ls, end_ms=t + 50).out)]
    assert len(sent) == 5 and len(set(sent)) == 5, sent
    ls, t = lines(sent)
    hostrun.run(f"vlay-kv2-{resp}", ls, end_ms=t + 50)


@pytest.mark.parametrize("resp", [0, 100])
def test_sequencer_and_arpeggiator_keep_the_played_velocity(resp):
    """the sequencer records a MIDI note's velocity and the arpeggiator steps at it, under FULL and LIGHT"""
    ls = [f"50 midi 0xB0 40 {resp}", "100 btn 11", "300 midi 0xB0 61 1", "400 btn 11", "950 btn 12"]
    t = 1000
    for v in (1, 33, 90, 127):
        ls += [f"{t} midi 0x90 64 {v}", f"{t + 100} midi 0x80 64 0", f"{t + 150} expect state sqvel {v}"]   # (at key-up)
        t += 200
    ls += [f"{t} btn 12", f"{t + 50} btn 11"]
    t += 300
    ls += [f"{t} midi 0xB0 52 1"]                                   # ARP MODE up
    for v in (1, 47, 127):
        ls += [f"{t + 50} midi 0x90 67 {v}", f"{t + 150} expect state arpv {v}", f"{t + 250} midi 0x80 67 0"]
        t += 300
    hostrun.run(f"vlay-seq-{resp}", ls, end_ms=t + 100)
