# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes mechanical noises under NOISE (CC 104): the key-down knock (S11, rhodes.c rh_knock_strike / rh_noise).

The knock is the hammer's force pulse heard through the mechanism, added to the mix at the NOISE level - 10 dB
(gen_fx_tables.py KN_KNOCK). Checked here, the engine alone (CC 119 RAW):
- STAGE 77 has none: NOISE 0 and 127 render the same samples up to the key-up (a DI harp; the default is unchanged);
- SUITCASE, EARLY FELT and BELL DYNO have it: NOISE 127 adds a short knock (its peak -28 .. -45 dBFS at v127, the
  first 10 ms), NOISE 48 (the default) about 22 dB under that, NOISE 0 none; it follows the velocity (the force);
- it is over within 30 ms (what it leaves after is rounding, under -100 dBFS) and adds no click (hil_scan's detector
  counts the same with and without it);
- a chord's strikes make one knock (the forces add: louder than one note's), not one per voice."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

RAW = "40 midi 0xB0 119 127"
T_ON = 200
SUITCASE, FELT, DYNO = 1, 2, 3


def take(tag, ty, noise, notes, vel=127, end=800, extra=()):
    ev = [RAW, f"42 midi 0xB0 80 {ty * 32}", f"44 midi 0xB0 104 {noise}", *extra]
    ev += [f"{T_ON} midi 0x90 {n} {vel}" for n in notes]
    return run(f"noise-{tag}", ev, end).L.astype(float)


def clicks(x):
    """tools/hil_scan.py clicks(): second-difference spikes far above the local level (as test_release_vel.py)"""
    d2 = np.abs(np.diff(x, 2))
    lvl = np.convolve(np.abs(x), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def dbfs(x):
    return 20 * np.log10(np.max(np.abs(x)) / 32768 + 1e-12)


def rms(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) / 32768 + 1e-12)


def onset(x):
    return int(np.nonzero(x)[0][0])


def test_stage_has_no_knock():
    for n in (28, 57, 84):
        a = take(f"st0-{n}", 0, 0, [n])
        b = take(f"st127-{n}", 0, 127, [n])
        assert np.array_equal(a, b), f"key {n}: STAGE 77 changed with NOISE before any key-up"


@pytest.mark.parametrize("ty", [SUITCASE, FELT, DYNO])
def test_knock_level_and_shape(ty):
    for n in (28, 57, 84):
        a = take(f"k0-{ty}-{n}", ty, 0, [n])
        b = take(f"k127-{ty}-{n}", ty, 127, [n])
        c = take(f"k48-{ty}-{n}", ty, 48, [n])
        d, e = b - a, c - a
        on = onset(a)
        pk, pk48 = dbfs(d), dbfs(e)
        assert -45 < pk < -28, f"TYPE {ty} key {n}: the knock at NOISE 127 peaks at {pk:.1f} dBFS"
        assert 19 < pk - pk48 < 25, f"TYPE {ty} key {n}: NOISE 48's knock {pk48:.1f} vs 127's {pk:.1f}"
        assert np.max(np.abs(d[:int(T_ON / 1000 * FS) - 256])) == 0, "a knock before the strike"
        assert rms(d[on + int(0.03 * FS):on + int(0.3 * FS)]) < -100, f"TYPE {ty} key {n}: the knock lasts"
        assert clicks(b) == clicks(a), f"TYPE {ty} key {n}: the knock adds a click"


def test_knock_follows_velocity():
    a = take("v0-127", SUITCASE, 0, [45]); b = take("v1-127", SUITCASE, 127, [45])
    c = take("v0-60", SUITCASE, 0, [45], vel=60); d = take("v1-60", SUITCASE, 127, [45], vel=60)
    hi, lo = dbfs(b - a), dbfs(d - c)
    assert 10 < hi - lo < 25, f"the knock v127 {hi:.1f} vs v60 {lo:.1f} dBFS"


def test_chord_is_one_louder_knock():
    one = take("c1-0", SUITCASE, 0, [48]), take("c1-127", SUITCASE, 127, [48])
    ch = take("c4-0", SUITCASE, 0, [48, 52, 55, 59]), take("c4-127", SUITCASE, 127, [48, 52, 55, 59])
    k1, k4 = one[1] - one[0], ch[1] - ch[0]
    assert 6 < dbfs(k4) - dbfs(k1) < 13, f"a 4-note chord's knock {dbfs(k4):.1f} vs one note's {dbfs(k1):.1f}"


def test_noise_zero_has_no_knock():
    """NOISE 0 on SUITCASE plays what NOISE 0 plays on the knock's absence: the same samples as with the knock level
    forced to 0 by NOISE 0 at the strike and NOISE 127 after it (the knock is decided at the strike)"""
    a = take("z0", SUITCASE, 0, [57])
    b = take("z1", SUITCASE, 0, [57], extra=(f"{T_ON + 20} midi 0xB0 104 127",))
    assert np.array_equal(a[:int(0.6 * FS)], b[:int(0.6 * FS)])


# ---- the pedal's thud (S12, rhodes.c rh_events EV_PEDAL / rh_noise): a 10 ms noise burst at the NOISE level + 6 dB
# when the pedal goes down (CC 64 >= 64) or up, on every TYPE; none at NOISE 0, none for a repeat or a half pedal


def pedal(tag, noise, events, ty=0, note=None, end=1000):
    ev = [RAW, f"42 midi 0xB0 80 {ty * 32}", f"44 midi 0xB0 104 {noise}"]
    if note is not None:
        ev.append(f"100 midi 0x90 {note} 100")
    ev += [f"{t} midi 0xB0 64 {v}" for t, v in events]
    return run(f"pedal-{tag}", ev, end).L.astype(float)


def bursts(x):
    """(start, end) samples of the nonzero runs, runs closer than 20 ms joined"""
    nz = np.nonzero(x)[0]
    if not len(nz):
        return []
    cut = np.nonzero(np.diff(nz) > int(0.02 * FS))[0]
    starts, ends = np.r_[nz[0], nz[cut + 1]], np.r_[nz[cut], nz[-1]]
    return list(zip(starts, ends))


@pytest.mark.parametrize("ty", [0, SUITCASE])
def test_pedal_thud_down_and_up(ty):
    x = pedal(f"du-{ty}", 127, [(300, 127), (600, 0)], ty=ty)
    b = bursts(x)
    assert len(b) == 2, f"{len(b)} bursts, not one at the pedal's down and one at its up"
    for (s, e), t in zip(b, (300, 600)):
        assert abs(s / FS * 1000 - t) < 8, f"a thud at {s / FS * 1000:.0f} ms, the pedal moved at {t}"
        assert e - s < int(0.03 * FS), f"the thud lasts {(e - s) / FS * 1000:.0f} ms"
        pk = dbfs(x[s:e + 1])
        assert -38 < pk < -28, f"the thud peaks at {pk:.1f} dBFS"


def test_pedal_thud_follows_noise():
    hi = dbfs(pedal("n127", 127, [(300, 127)]))
    lo = dbfs(pedal("n48", 48, [(300, 127)]))
    assert 19 < hi - lo < 25, f"NOISE 127's thud {hi:.1f} vs 48's {lo:.1f} dBFS"
    assert not np.any(pedal("n0", 0, [(300, 127), (600, 0)])), "a thud at NOISE 0"


def test_pedal_thud_only_on_a_change():
    one = bursts(pedal("rep", 127, [(300, 127), (500, 127), (700, 100), (800, 64)]))
    assert len(one) == 1, f"{len(one)} thuds for one press re-sent"
    half = pedal("half", 127, [(300, 50), (500, 30), (700, 63)])
    assert not np.any(half), "a thud from a half pedal that never reached the sustain"


def test_pedal_thud_no_click():
    for ty in (0, SUITCASE):
        a = pedal(f"c0-{ty}", 0, [(300, 127), (600, 0)], ty=ty, note=57)
        b = pedal(f"c1-{ty}", 127, [(300, 127), (600, 0)], ty=ty, note=57)
        assert clicks(b) == clicks(a) == 0, f"TYPE {ty}: a click with the pedal's thud"
        assert not np.array_equal(a, b)
