# SPDX-License-Identifier: GPL-3.0-only
"""The bars' key-up never louder at any MALLET (BARK) hardness (2026-11-10). The damper's knock is the SOFT pulse,
capped by the strike's force left; a harder mallet's strike gives the fundamental less for that force, so at BARK 96 ..
127 the knock came out over the note it ended (VIBES, a 16-note chord, the Move's build, RAW: +0.4 .. +0.8 dB in the
key-up's block; F14 VIBES ECHO at BARK 82 too). bars.c bar_knock_reach scales the cap by the two pulses' reach at the
fundamental. VIBES and MARIMBA, BARK 0 .. 127, v20 .. 127, the 16-note chord let go (pedal up): never over the held
chord by +0.5 dB (test_move_voices' rule) on any 10 ms window; and the knock still sounds (BARK 0 v40 unchanged)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_voices as mv  # noqa: E402

CASES = [(e, b, v) for e in ("vibes", "marimba") for b in (0, 64, 96, 127) for v in (20, 60, 100, 127)]


@pytest.mark.parametrize("eng,bark,vel", CASES, ids=[f"{e}-b{b}-v{v}" for e, b, v in CASES])
def test_key_up_never_louder_any_mallet(eng, bark, vel):
    ev = [f"46 midi 0xB0 29 {bark}"] + mv.chord(300, vel)
    _, x = mv.run(eng, f"knm-{bark}-{vel}", ev + mv.up(1300), 2200, raw=True)
    _, ref = mv.run(eng, f"knm-{bark}-{vel}-ref", ev, 2200, raw=True)
    rr, rp = mv._diff(x, ref, 1300, 600)
    assert rr <= mv.TOL_DB and rp <= mv.TOL_DB, f"{eng} BARK {bark} v{vel}: RMS {rr:+.2f} / peak {rp:+.2f} dB over held"
