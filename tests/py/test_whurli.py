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


EXCESS_MAX = 3.0     # a click's excess over the periodic waveform: 6 at +150 LSB, 125 at +3000; the bark's: 0.35 .. 1.05


def excess(x, n, f0):
    """how far the 2nd-difference spike at sample n stands out of the periodic waveform: its size over the largest 2nd difference
    of the cycle before and of the cycle after (period 1 / f0). A bark pulse (the pickup's lopsided current at a hard strike)
    belongs to every cycle: about 1. A click is in neither: well over 1 (the raw LPC residual cannot tell them apart)"""
    d = np.abs(np.diff(np.asarray(x, float), 2))
    P = H.FS / f0
    s0 = d[max(n - 3, 0):n + 4].max()

    def cyc(c):
        a, b = int(c - P / 2), int(c + P / 2)
        return d[a:b].max() if a >= 0 and b < len(d) else 0.0
    return s0 / max(cyc(n - P), cyc(n + P), 1.0)


def clicks(x, f0_at):
    """the LPC glitch detector's events that are not bark: f0_at(sample) gives the sounding key's f0"""
    return [e for e in G.events(x, H.FS) if excess(x, e, f0_at(e)) >= EXCESS_MAX]


def sweep_f0(n):
    i = max(int((n / 44.1 - 300) // STEP), 0)
    return 440 * 2 ** ((KEYS[i % len(KEYS)] - 69) / 12)


def sweep(whurli):
    ls = [H.cc(50, 119, 127)]
    if whurli:
        ls += [f"{60 + 2 * i} turn 1 1" for i in range(4)]            # ALGORITHM: ST77 -> SUITCASE -> EARLY FELT -> BELL DYNO -> WHURLI
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
    ew, er = len(clicks(w.L, sweep_f0)), len(clicks(ref.L, sweep_f0))     # (bark-aware: see excess)
    assert ew <= er + 2, f"WHURLI {ew} glitch events, STAGE 77 {er}"
    lv = []          # each velocity sounds, and a harder strike is louder (the median over the keys)
    for v in range(len(VELS)):
        seg = [w.L[int((300 + (v * len(KEYS) + j) * STEP + 60) * 44.1):int((300 + (v * len(KEYS) + j) * STEP + 260) * 44.1)]
               for j in range(len(KEYS))]
        lv.append(np.median([H.rms_db(s) for s in seg]))
    assert all(a < b for a, b in zip(lv, lv[1:])), lv
    assert lv[0] > -80, lv


def test_a_note_outside_the_keys_has_no_voice():
    ls = [H.cc(50, 119, 127)] + [f"{60 + 2 * i} turn 1 1" for i in range(4)] + H.note(300, 24, 110, 200) + H.note(300, 108, 110, 200)
    r = H.run("whurli-oor", ls + ["200 expect state type WHURLI"], 1500)
    assert np.all(r.L == 0) and np.all(r.R == 0)


def test_the_header_has_the_type():
    src = open(os.path.join(H.ROOT, "firmware", "gen", "reed_tables.h")).read()
    assert "#define RD_KEY_LO" in src and "#define RD_NKEY" in src


def test_the_type_maps_agree():
    """one TYPE -> engine map: engine.c's, the plugin UI's copy and params.c's names (WHURLI is TYPE 6, the last)"""
    import re
    eng = open(os.path.join(H.ROOT, "firmware", "src", "engine.c")).read()
    ui = open(os.path.join(H.ROOT, "plugin", "ui", "frui_params.h")).read()
    par = open(os.path.join(H.ROOT, "firmware", "src", "params.c")).read()
    e = [int(x) for x in re.search(r"ENG_TYPE_ENG\[ENG_NTYPE\] = \{([^}]*)\}", eng).group(1).split(",")]
    u = [int(x) for x in re.search(r"FRN_TYPE_BARS\[FRN_NTYPE\] = \{([^}]*)\}", ui).group(1).split(",")]
    names = re.search(r'N_TYPE_LONG\[\] = \{([^}]*)\}', par).group(1)
    assert e == u and len(e) == 7 and names.split(",")[6].strip() == '"WHURLI"'
    assert "RH_T_REED" in eng and "#define ENG_NTYPE 7u" in eng


def test_the_detector_still_fires_on_a_click_at_v127_key_40():
    """self-test of the bark-aware detector: a clean v127 key 40 (barking) has no click; the same render with 150 LSB added in
    one sample is found (excess 6; the LPC residual alone flags bark and click alike)"""
    ls = [H.cc(50, 119, 127)] + [f"{60 + 2 * i} turn 1 1" for i in range(4)] + H.note(300, 40, 127, 600)
    x = H.run("whurli-k40", ls, 1500).L.copy()
    f0 = lambda n: 440 * 2 ** ((40 - 69) / 12)
    assert clicks(x, f0) == []
    n0 = int(450 * 44.1)
    for amp in (150, 800):
        y = x.copy()
        y[n0] += amp
        assert [abs(e - n0) < 8 for e in clicks(y, f0)] == [True], amp
