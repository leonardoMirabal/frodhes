# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes' steal fade (rhodes.c rh_fade_g, 0.9.13, the audio review D2): a stolen voice fades out over 64 samples
(1.45 ms) before the note that steals it starts, along a smoothstep, not a straight ramp. The ramp's corners spread
the cut of a loud low note into the highs.

The measure separates the cut from the new note's attack. One voice (-DRH_VOICES=1u), the engine alone (CC 119): A is
the old note, then a new one that steals it; D the old note alone. A and D agree up to the steal (t0); the new note
waits for the fade, so A on [t0, t1) is the old note faded, S, and what the steal removed is D - S (D whole from t1 on).
Its peak above 2 kHz (4th-order high-pass) over [t0, t1 + 10 ms) is the cut's click. The old note is C2..C4 at v127,
the new one v30 (a soft note masks the least).

Measured (dBFS above 2 kHz, the worst of the five notes): -54.7 with the ramp (0.9.12), -64.0 with the smoothstep, the
same 1.45 ms. A 4-8 ms ramp also gets there, but it delays the new note by 3-6.5 ms."""
import os

import numpy as np
import pytest

from hostrun import ROOT, build, run

LIMIT_DB = -62.0
OLD = (36, 43, 48, 55, 60)


def _hp(x):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, 2000, "hp", fs=44100, output="sos"), x)


@pytest.fixture(scope="module")
def one_voice():
    return build(os.path.join(ROOT, "build", "host", "v1", "frodhes"), ["-O2", "-DRH_VOICES=1u"])


def click_db(binary, old, new_vel=30):
    def take(tag, ev):
        r = run(tag, ["10 midi 0xB0 119 127"] + ev, 1200, binary=binary)
        return (r.L.astype(float) + r.R.astype(float)) / 2.0
    a = take(f"steal-a-{old}", [f"200 midi 0x90 {old} 127", f"480 midi 0x90 64 {new_vel}"])
    d = take(f"steal-d-{old}", [f"200 midi 0x90 {old} 127"])
    t0 = int(np.argmax(np.abs(a - d) > 0.5))
    t1 = t0 + 64
    s = a.copy()
    s[t1:] = 0
    removed = d - s
    removed[:max(0, t0 - 2000)] = 0
    return 20 * np.log10(np.abs(_hp(removed)[t0:t1 + 441]).max() / 32768 + 1e-12)


def test_steal_cut_stays_out_of_the_highs(one_voice):
    got = {old: click_db(one_voice, old) for old in OLD}
    worst = max(got.values())
    print({k: round(float(v), 1) for k, v in got.items()})
    assert worst <= LIMIT_DB, f"the steal's cut above 2 kHz: {got} (limit {LIMIT_DB} dBFS)"
