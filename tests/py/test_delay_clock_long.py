# SPDX-License-Identifier: GPL-3.0-only
"""The synced delay under an external MIDI clock, the two FM-1 findings of hil_clock (2026-10-25) on the host build:

1. DLY SYNC 1/1 at 120 BPM (2.0 s, over the line's 1.48 s: half, 1.0 s): the first probe gave a 'first echo' at 485 ms
   and a second 513 ms after it. Not the delay: the take's reference note was struck before the clock's Start while
   the FM-1 still held the previous take's 100 BPM (hil_clock restores 120 only after the last take), so its first
   repeat came 1.2 s later (1/1 at 100, halved) and the following ones 1.0 s apart once the clock had locked; its 5th
   repeat fell 485 ms after the probe, above the analysis' echo threshold. The probe's own echo is at 1.0 s.
2. TAPE takes a few beats to reach a new tempo (548 / 593 ms against 600, 2 and 8 beats after 120 -> 100): the
   designed motor glide, the delay following the new time exponentially (tools/gen_fx_tables.py TAU_UP 1.89 s to a
   longer delay, TAU_DN 1.07 s to a shorter one): 547 / 592 ms at 1.2 / 4.8 s."""
import numpy as np

import hostrun

FS = 44100
CC_DL_SW, CC_DL_DEPTH, CC_DL_FDBK, CC_DL_SYNC, CC_RESET = 88, 89, 26, 25, 10
PROBE = 84


def band(x, hi):
    return min(127, -(-x * 128 // (hi + 1)))


def pulses(t0, bpm, n):
    p = 60000.0 / bpm / 24
    return [f"{int(round(t0 + i * p))} midi 0xF8 0 0" for i in range(n)]


def echo_lag(env, tp, guess, lo=120, hi=60):
    """the lag (ms) at which the sound after tp best matches the note struck at tp (1 ms envelope NCC)"""
    x = env[tp - 10:tp + 120]
    lags = np.arange(int(guess) - lo, int(guess) + hi)
    c = [np.dot(x - x.mean(), env[tp - 10 + k:tp + 120 + k] - env[tp - 10 + k:tp + 120 + k].mean()) for k in lags]
    return int(lags[int(np.argmax(c))])


def test_long_sync_from_a_stale_tempo():
    """DIG, DLY SYNC 1/1: the FM-1 at 100 BPM from a previous clock, the line cleared (CC 10), a note before the
    120 BPM clock's Start: its first repeat at the old tempo's 1.2 s, then 1.0 s apart; a probe 8 beats into the
    clock echoes at exactly 1.0 s (the long echo halved from the first lock on, no ramp or stale time left)"""
    ls = ["499 midi 0xFA 0 0"] + pulses(500, 100, 24 * 8) + ["5300 midi 0xFC 0 0"]
    ls += [hostrun.cc(7000, CC_RESET, 127), hostrun.cc(7500, CC_DL_SW, band(1, 3)), hostrun.cc(7500, CC_DL_DEPTH, 100),
           hostrun.cc(7500, CC_DL_FDBK, 50), hostrun.cc(7500, CC_DL_SYNC, band(12, 13))]
    ref, start = 8300, 9000
    probes = [start + 4000, start + 7000]                           # beats 8 and 14
    ls += hostrun.note(ref, PROBE, 110, 50) + [f"{start - 1} midi 0xFA 0 0"] + pulses(start, 120, 24 * 20)
    for p in probes:
        ls += hostrun.note(p, PROBE, 110, 50)
    dumps = [ref, probes[0] - 10]
    r = hostrun.run("dly-long-stale", ls + [f"{t} dump" for t in dumps], end_ms=start + 10500)
    assert int(r.state(ref)["dlysync"]) == 26460 * 4             # 1/1 at 100 (2.4 s; the line plays half)
    assert abs(int(r.state(probes[0] - 10)["dlysync"]) / (22050 * 4) - 1) < 0.001
    env = hostrun.env_rms(r.L + r.R, 44)                           # ~1 ms frames
    e1 = echo_lag(env, ref, 1200)
    assert abs(e1 - 1200) <= 6, e1                                 # the reference's 1st repeat: the old tempo's
    e2 = echo_lag(env, ref + e1, 1000)
    assert abs(e2 - 1000) <= 6, e2                                 # then the new one
    for p in probes:
        e = echo_lag(env, p, 1000)
        assert abs(e - 1000) <= 6, (p, e)
    # the reference's 5th repeat lands ~0.5 s after the first probe (the FM-1's 485 ms), the probe's echo after it
    k5 = ref + e1 + 4 * e2
    assert 400 < k5 - probes[0] < 560, k5 - probes[0]


def test_tape_motor_glide_to_a_new_tempo():
    """TAPE, DLY SYNC 1/4, a 120 BPM clock, then 100 BPM: the echo time glides from 500 to 600 ms as the motor's
    one-pole (TAU_UP 1.89 s), sample for sample the design: within 10 ms of 600 - 100 exp(-t / 1.89 s) from 4 beats
    on, at 600 by 12 beats"""
    ls = [hostrun.cc(20, CC_DL_SW, 112), hostrun.cc(20, CC_DL_DEPTH, 127), hostrun.cc(20, CC_DL_FDBK, 0),
          hostrun.cc(20, CC_DL_SYNC, band(9, 13))]
    tc = 11000
    ls += ["999 midi 0xFA 0 0"] + pulses(1000, 120, 24 * 20) + pulses(tc, 100, 24 * 26)
    beats = (4, 8, 12, 16, 24)
    probes = [tc + 600 * b for b in beats]
    ls += hostrun.note(tc - 2000, PROBE, 110, 40)
    for p in probes:
        ls += hostrun.note(p, PROBE, 110, 40)
    r = hostrun.run("dly-long-tape", ls, end_ms=tc + 600 * 26 + 400)
    env = hostrun.env_rms(r.L + r.R, 44)
    before = echo_lag(env, tc - 2000, 500)
    assert abs(before - 500) <= 4, before
    for b, p in zip(beats, probes):
        e = echo_lag(env, p, 600)
        model = 600 - 100 * np.exp(-(p - tc) / 1890.0)
        assert abs(e - model) <= 10, (b, e, model)
        if b >= 12:
            assert abs(e - 600) <= 4, (b, e)
