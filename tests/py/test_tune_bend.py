# SPDX-License-Identifier: GPL-3.0-only
"""TUNE under held notes and the pitch bend (0.9.13, the audio review C2). TUNE, DECAY and RELEASE act on new notes; a
bend retunes the sounding voices (rhodes.c rh_retune) from the TUNE each one started with. It took TUNE's value of the
moment: a held C3, TUNE -50 under it (nothing heard, by design), then the smallest bend step jumped the note by -50.8
cents at once. And a DECAY turned under a held note, then a bend: E took the new DECAY's eps while the voice ran the
note-on's (a pair that did not match: the note detuned, 0.02 cents). The engine alone (CC 119 RAW)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import run  # noqa: E402
from test_pitch_bend import bend_ev, cents, f0_of, ratio  # noqa: E402

N = 48                                   # C3
F_ET = 440.0 * 2 ** ((N - 69) / 12)
MEAS = (2.7, 3.9)                        # s: after the bend at 2.5 s


def take(tag, extra, end=4000):
    ev = ["40 midi 0xB0 119 127", f"500 midi 0x90 {N} 100", f"{end - 50} midi 0x80 {N} 0"] + list(extra)
    return run(f"tunebend-{tag}", sorted(ev, key=lambda e: int(e.split()[0])), end)


def f0(r, guess=F_ET):
    return f0_of(r.seg(*MEAS), guess)


def rms_db(r, t0, t1):
    return 20 * np.log10(np.sqrt(np.mean(r.seg(t0, t1) ** 2)))


def test_tune_under_a_held_note_no_jump_at_the_next_bend():
    ref = take("ref", [bend_ev(2500, 1)])
    r = take("tune", ["1500 tune -50", bend_ev(2500, 1)])
    err = cents(f0(r), f0(ref))
    assert abs(err) < 0.05, f"TUNE -50 under the held note, then a 1-LSB bend: {err:+.3f} cents (the jump: -50.8)"


def test_full_bend_from_the_note_on_tune():
    """TUNE -50 under the held note, then +2 semitones: the note-on's TUNE (0) times the bend"""
    ref = take("ref0", [])
    r = take("tune-up", ["1500 tune -50", bend_ev(2500, 8191)])
    err = cents(f0(r, F_ET * ratio(8191)), f0(ref)) - 1200 * np.log2(ratio(8191))
    assert abs(err) < 0.5, f"bent +8191 after TUNE -50 under it: {err:+.3f} cents from the note-on tune x the bend"


def test_new_note_takes_tune_and_the_bend_adds():
    """a note struck after TUNE -50 plays -50 cents; bent +8191 it plays -50 + 200 (MANUAL: the bend adds to TUNE)"""
    ref = take("ref0b", [])
    ev = ["40 midi 0xB0 119 127", "100 tune -50", f"500 midi 0x90 {N} 100", bend_ev(2500, 8191), f"3950 midi 0x80 {N} 0"]
    r = run("tunebend-new", ev, 4000)
    err = cents(f0(r, F_ET * ratio(8191) * 2 ** (-50 / 1200)), f0(ref)) - (-50 + 1200 * np.log2(ratio(8191)))
    assert abs(err) < 0.5, f"struck at TUNE -50, bent +8191: {err:+.3f} cents off -50 + 200"


def test_decay_under_a_held_note_then_a_bend_keeps_the_note():
    """DECAY turned under the held note (it acts on new notes), then a full bend: the render is the one without the
    DECAY turn, sample for sample (the pair E / eps both from the note-on; before, DECAY 0 detuned the bent note by
    0.022 cents and DECAY 127 by 0.004)"""
    ref = take("dref", [bend_ev(2500, 8191)])
    for d in (0, 127):
        r = take(f"decay{d}", [f"1500 midi 0xB0 102 {d}", bend_ev(2500, 8191)])
        assert np.array_equal(r.L, ref.L) and np.array_equal(r.R, ref.R), \
            f"DECAY {d} under the note, then a bend: {cents(f0(r, F_ET * ratio(8191)), f0(ref, F_ET * ratio(8191))):+.4f} cents"
