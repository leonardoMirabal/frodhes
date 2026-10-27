# SPDX-License-Identifier: GPL-3.0-only
"""No louder after the key-up (private TEST-PLAN, release must-pass, 2026-10-25): letting go of a note must never make
it louder. 0.9.5's first test build gave a soft note (v15) a key-up damper thump 9-12 dB over the note itself (the
thump had a fixed level; real key velocity let the notes go soft under it): "I press a key and get 2 sounds".

The grid: every engine x TYPE (the four Rhodes, VIBES, MARIMBA) x NOISE (0, 48 the default, 127) x velocity (1, 15,
30, 60, 100, 127) x key (bass, mid, top), the note held 0.2 s and 2 s, the engine alone (CC 119 RAW), then:
- let go with the pedal up;
- the pedal down 100 ms before the key-up, then up 300 ms after it (the dampers land at the pedal-up): the key-up
  checked until the pedal-up, the pedal-up for 600 ms.
The rule, at the key-up and at the pedal-up: the level 0..600 ms after (10 ms RMS windows, and the peak) never
exceeds the level of the 50 ms just before by more than +0.5 dB, and no new onset (a 10 ms window more than ONSET_DB
over the one before it, above the floor) appears after it. One render per engine x TYPE x NOISE x pedal x hold, the
18 notes in a row."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

VELS = (1, 15, 30, 60, 100, 127)
TYPES = [("STAGE77", "80", 0), ("SUITCASE", "80", 32), ("FELT", "80", 64), ("DYNO", "80", 96),
         ("VIBES", "79", 0), ("MARIMBA", "79", 96)]
KEYS = {"rhodes": (31, 60, 96), "bars": (55, 72, 88)}
NOISES = (0, 48, 127)
TOL_DB = 0.5
ONSET_DB = 3.0                                  # a new onset: one 10 ms window this much over the one before
FLOOR_DB = 30.0                                 # ... while within this of the level before (not the tail's rounding)
W = FS // 100                                   # 10 ms
GAP_MS = 1400                                   # the release, then the next note


def slots(hold_ms, pedal):
    """(t_on, t_up, t_ped_up or None) per note, in VELS x keys order"""
    t, out = 300, []
    for _ in range(len(VELS) * 3):
        t_up = t + hold_ms
        out.append((t, t_up, t_up + 300 if pedal else None))
        t = t_up + (300 if pedal else 0) + GAP_MS
    return out


def render(ty, noise, pedal, hold_ms):
    name, ccn, ccv = ty
    keys = KEYS["bars" if ccn == "79" else "rhodes"]
    ev = ["40 midi 0xB0 119 127", f"42 midi 0xB0 {ccn} {ccv}", f"44 midi 0xB0 104 {noise}"]
    if ccn == "79":
        ev.append("46 midi 0xB0 47 0")          # VIBES's motor off (its AM pulse is no key-up; MARIMBA has none)
    sl = slots(hold_ms, pedal)
    notes = [(v, k) for v in VELS for k in keys]
    for (t_on, t_up, t_pu), (v, k) in zip(sl, notes):
        ev.append(f"{t_on} midi 0x90 {k} {v}")
        if pedal:
            ev.append(f"{t_up - 100} midi 0xB0 64 127")
        ev.append(f"{t_up} midi 0x80 {k} 0")
        if pedal:
            ev.append(f"{t_pu} midi 0xB0 64 0")
    end = sl[-1][2 if pedal else 1] + GAP_MS
    r = run(f"nl-{name}-{noise}-{int(pedal)}-{hold_ms}", ev, end)
    return (r.L.astype(float) + r.R.astype(float)) / 2.0, sl, notes


def db(x):
    return 20 * np.log10(x / 32768.0 + 1e-12)


def rms10(x):
    n = len(x) // W
    return np.sqrt(np.mean(np.square(x[:n * W].reshape(n, W)), axis=1))


def check_at(x, t_ms, span_ms=600):
    """(rms rise dB, peak rise dB, onset rise dB) of 0..span_ms after t_ms over the 50 ms before. The event reaches
    the engine at its audio block's start, up to one block (5.8 ms) before its time in the script: the 50 ms end 10 ms
    early and the after starts there"""
    i = int(t_ms * FS / 1000) - W
    pre, post = x[i - 5 * W:i], x[i:i + span_ms // 10 * W + W]
    rp, ra = rms10(pre), rms10(post)
    lb_r, lb_p = db(rp.max()), db(np.abs(pre).max())
    if lb_r < -90:                              # (the note under 1 LSB RMS, in the cut's dither noise (rhodes.c
                                                # RH_DITH: ~-96 dBFS) or ended: nothing to be louder than; the pedal's
                                                # thud into silence is S12's own. Before the dither: -100)
        return 0.0, 0.0, 0.0
    rise_r = db(ra.max()) - lb_r
    rise_p = db(np.abs(post).max()) - lb_p
    d = db(ra)
    ok = d[1:] > lb_r - FLOOR_DB
    on = float(np.max(np.where(ok, d[1:] - d[:-1], -99.0))) if len(d) > 1 else -99.0
    return rise_r, rise_p, on


GRID = [(ty, nz, ped, h) for ty in TYPES for nz in NOISES for ped in (False, True) for h in (200, 2000)]


@pytest.mark.parametrize("ty,noise,pedal,hold", GRID,
                         ids=[f"{t[0]}-N{n}-{'ped' if p else 'up'}-{h}" for t, n, p, h in GRID])
def test_never_louder_after_key_up(ty, noise, pedal, hold):
    x, sl, notes = render(ty, noise, pedal, hold)
    bad = []
    for (t_on, t_up, t_pu), (v, k) in zip(sl, notes):
        for what, t, span in (("key-up", t_up, 290 if pedal else 600),) + ((("pedal-up", t_pu, 600),) if pedal else ()):
            rr, rp, on = check_at(x, t, span)
            if rr > TOL_DB or rp > TOL_DB or on > ONSET_DB:
                bad.append(f"v{v} key {k} {what}: 10 ms RMS {rr:+.1f} dB, peak {rp:+.1f} dB, onset {on:+.1f} dB")
    assert not bad, f"{ty[0]} NOISE {noise} {'pedal' if pedal else 'no pedal'} held {hold} ms:\n" + "\n".join(bad)
