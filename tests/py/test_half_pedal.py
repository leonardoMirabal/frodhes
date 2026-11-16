# SPDX-License-Identifier: GPL-3.0-only
"""A half pedal (CC 64 42..63: the dampers touch lightly, rhodes_tables PEDAL_W x0.6 .. 0.9) lands the felt as lightly
as it damps (0.9.13, the audio review D3): the key-up knock and the felt noise are scaled by the pedal's weight, on the
electric pianos (rhodes.c) and VIBES (bars.c). Before, a half pedal gave them at full NOISE, as the pedal up.

- the knock's force and the felt's gain (host_state "keyup") at CC 64 50 are the pedal-up ones x PEDAL_W[50] / 32768
  (the felt scaled once: after a knock it reads the knock's force, already scaled);
- the pedal up is unchanged (Gate R: every render without a half pedal is byte-identical);
- no louder after the key-up at a half pedal (test_release_never_louder.py's rule and windows, every TYPE, NOISE 48
  and 127, the six velocities x three keys)."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, ROOT, run  # noqa: E402,F401
from test_release_never_louder import KEYS, ONSET_DB, TOL_DB, TYPES, VELS, check_at, slots  # noqa: E402

HALF = 50


def pedal_w(cc):
    src = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    return int(re.search(r"PEDAL_W\[128\] = \{([^}]*)\}", src).group(1).split(",")[cc])


def keyup(r, t):
    """host_state "keyup" at t ms (the script's "expect state keyup x" reports it as it fails)"""
    m = re.search(r"^\s*%d ms  FAIL expect state keyup = \"x\" \(is \"(-?\d+)/(-?\d+)\"\)" % t, r.out, re.M)
    assert m, r.out[-800:]
    return int(m.group(1)), int(m.group(2))


@pytest.mark.parametrize("ty", [t for t in TYPES if t[0] != "MARIMBA"], ids=lambda t: t[0])
def test_knock_and_felt_follow_the_pedal_weight(ty):
    name, ccn, ccv = ty
    k = 60 if ccn == "80" else 72
    ev = [f"10 midi 0xB0 {ccn} {ccv}", "12 midi 0xB0 104 127", "14 midi 0xB0 47 0"]
    if name.endswith("-LIGHT"):
        ev.append("13 midi 0xB0 40 127")
    for i, ped in enumerate((0, HALF, 0)):
        t = 300 + i * 1500
        ev += [f"{t - 100} midi 0xB0 64 {ped}", f"{t} midi 0x90 {k} 127", f"{t + 1000} midi 0x80 {k} 64",
               f"{t + 1100} expect state keyup x"]
    r = run(f"hp-{name}", ev, 5000, check=False)
    up, half, up2 = (keyup(r, t) for t in (1400, 2900, 4400))
    w = pedal_w(HALF)
    assert abs(up[0] - up2[0]) <= 2 and abs(up[1] - up2[1]) <= 2, (up, up2)
    for a, b, what in ((up[0], half[0], "knock"), (up[1], half[1], "felt")):
        want = a * w // 32768
        assert abs(b - want) <= max(2, a // 64), f"{name}: {what} {b} at a half pedal, want {want} ({a} x {w}/32768)"


@pytest.mark.parametrize("ty,noise", [(t, n) for t in TYPES for n in (48, 127)], ids=lambda v: str(v))
def test_half_pedal_never_louder_after_key_up(ty, noise):
    name, ccn, ccv = ty
    keys = KEYS["bars" if ccn == "79" else "rhodes"]
    ev = ["40 midi 0xB0 119 127", f"42 midi 0xB0 {ccn} {ccv}", f"44 midi 0xB0 104 {noise}", f"48 midi 0xB0 64 {HALF}"]
    if name.endswith("-LIGHT"):
        ev.append("43 midi 0xB0 40 127")
    if ccn == "79":
        ev.append("46 midi 0xB0 47 0")
    sl = slots(200, False)
    notes = [(v, k) for v in VELS for k in keys]
    for (t_on, t_up, _), (v, k) in zip(sl, notes):
        ev += [f"{t_on} midi 0x90 {k} {v}", f"{t_up} midi 0x80 {k} 0"]
    r = run(f"hpl-{name}-{noise}", ev, sl[-1][1] + 1400)
    x = (r.L.astype(float) + r.R.astype(float)) / 2.0
    bad = []
    for (_, t_up, _), (v, k) in zip(sl, notes):
        rr, rp, on = check_at(x, t_up, 600, k)
        if rr > TOL_DB or rp > TOL_DB or on > ONSET_DB:
            bad.append(f"v{v} key {k}: 10 ms RMS {rr:+.1f} dB, peak {rp:+.1f} dB, onset {on:+.1f} dB")
    assert not bad, f"{name} NOISE {noise} half pedal:\n" + "\n".join(bad)
