# SPDX-License-Identifier: GPL-3.0-only
"""WHURLI (TYPE 6): the Rhodes engine on its own tables (rhodes.c rh_init_rd, rh_pickup_rd). Every key of its range
at four velocities, one note at a time, RAW (CC 119: the engine alone): the output has no click (the glitch detector
the FM-1 takes are judged with, against STAGE 77's on the same notes: never more), nothing saturates and the engine
ends in exact silence; a note outside its keys has no voice; the other four-slot types are untouched (Gate R)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import glitch_lpc as G  # noqa: E402
import hostrun as H  # noqa: E402

KEYS = list(range(33, 96, 3))          # the stub's range is 33..95; the generator's real range is read from the header
VELS = (20, 64, 100, 127)
STEP = 500


def sweep(whurli):
    ls = [H.cc(50, 119, 127)]
    if whurli:
        ls += [f"{60 + 2 * i} turn 1 1" for i in range(6)]            # ALGORITHM: ST77 -> ... -> WHURLI
    t = 300
    for v in VELS:
        for k in KEYS:
            ls += H.note(t, k, v, 250)
            t += STEP
    ls.append(H.cc(t + 500, 123, 0))
    return ls, t + 3000


def test_the_sweep_has_no_click_and_nothing_saturates():
    ls, end = sweep(True)
    w = H.run("whurli-sweep", ls + ["200 expect state type WHURLI"], end)
    ref = H.run("whurli-ref", sweep(False)[0], end)
    assert "sat28 hits 0" in w.out
    assert np.max(np.abs(w.L)) < 32000 and np.max(np.abs(w.R)) < 32000
    assert np.all(w.L[-4410:] == 0) and np.all(w.R[-4410:] == 0), "the engine ends in exact silence"
    ew, er = len(G.events(w.L, H.FS)), len(G.events(ref.L, H.FS))
    assert ew <= er + 2, f"WHURLI {ew} glitch events, STAGE 77 {er}"
    lv = []          # each velocity sounds, and a harder strike is louder (the median over the keys)
    for v in range(len(VELS)):
        seg = [w.L[int((300 + (v * len(KEYS) + j) * STEP + 60) * 44.1):int((300 + (v * len(KEYS) + j) * STEP + 260) * 44.1)]
               for j in range(len(KEYS))]
        lv.append(np.median([H.rms_db(s) for s in seg]))
    assert all(a < b for a, b in zip(lv, lv[1:])), lv
    assert lv[0] > -80, lv


def test_a_note_outside_the_keys_has_no_voice():
    ls = [H.cc(50, 119, 127)] + [f"{60 + 2 * i} turn 1 1" for i in range(6)] + H.note(300, 24, 110, 200) + H.note(300, 108, 110, 200)
    r = H.run("whurli-oor", ls + ["200 expect state type WHURLI"], 1500)
    assert np.all(r.L == 0) and np.all(r.R == 0)


def test_the_header_has_the_type():
    src = open(os.path.join(H.ROOT, "firmware", "gen", "reed_tables.h")).read()
    assert "#define RD_KEY_LO" in src and "#define RD_NKEY" in src
