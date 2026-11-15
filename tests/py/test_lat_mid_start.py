# SPDX-License-Identifier: GPL-3.0-only
"""The two-core package (FR_C1 + FR_C1_LAT: 128-frame interrupts, FR_LAT_EV: a note-on with a free voice starts at the
next interrupt) against one core, notes from silence played back to back in ONE run, as the FM-1 plays a suite's
takes: the state carried from note to note, the blocks' phase running on (tests/py/hostrun.py runs; tools/hil_scan.py
--host renders each take in a fresh run, which never showed it). A note that starts at the block's second interrupt
must sound as the same note started at a block's start, but for the residue below.

Found 2026-11-14 (the 0.9.12 two-core T2, marimba_decay: NOISE 48 read as loud as 127 on keys 60 and 84; old, v0.9.11
the same): with the gate shut (406 ms of silence), one core opens it at the note's block start (the note's first 64
frames ramped up), the package at the frames sent before the note (FR_LAT_TAIL: the ramp over the silent frames 64 ..
127, the note at 128 unramped). So a note's attack was ramped or not by chance of its start, up to 230 LSB on its first
chunk. fx.c now opens the gate from the note's frame (engine.c eng_mid_at), as one core does.

Per engine and seed: separate notes (random key, velocity, hold; 2.5 - 4 s apart: each from silence, the gate shut,
or open after a CC 10 as a suite's reset leaves it), DRY, NOISE 0 on the bar engine (its contact noise is one LFSR
whose sequence moves with every start's frame: the engine is deterministic apart from it). Each note's largest sample
difference, the two takes aligned on their onsets (+-4 frames), from 64 frames before the onset to 60 ms after.
THRESHOLD (measured residue, fixed code, seeds 1-6): Rhodes <= 2 LSB (0-1: the dither); MARIMBA <= 16 LSB (0-11: the
bar engine's per-block work seen from a start 128 frames into the block); VIBES with its motor off <= 16 LSB (0-8, the
same). The pre-fix code fails both: Rhodes up to 1029 LSB, MARIMBA up to 798.

VIBES with its motor on is not judged, and not for the motor's controls: the main loop's writes (CCs 47-51, the TYPE
change's motor on, CC 10's motor phase reset) already land between blocks under FR_LAT (frodhes.c lat_mid,
midi_get_h), so the motor runs on the 256-frame block grid in both builds (measured 2026-11-15, seeds 1-6, no CC 10
between the notes: every note that starts at a block's start 0-1 LSB). What differs is the note: started at the
block's second interrupt it is struck 128 frames later on the motor's turn than one core strikes it (13-111 LSB on
those notes), and a CC 10 that reaches the main loop in a block's first interrupt resets the motor's phase one block
later than one core does (the 256-frame build's main loop runs half a block later on the host's clock; up to 173
LSB). Both are when the note and the reset happen, not how they sound (the coordinator's decision 2026-11-14:
timing, not a defect); one core cannot start a note at frame 128 to compare."""
import os
import sys

import numpy as np
import pytest

from hostrun import ROOT, build, cc, run

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

LIMIT = {"rhodes": 2, "marimba": 16, "vibes": 16}   # LSB (THRESHOLD, above; VIBES: its motor off)
SEEDS = range(1, 7)
N = 14


def bins():
    return {"lat": build(os.path.join(ROOT, "build", "host", "midst_lat", "frodhes"), ["-DFR_C1=1", "-DFR_C1_LAT=1"]),
            "one": build(os.path.join(ROOT, "build", "host", "midst_one", "frodhes"), ["-DFR_VOICES=14u", "-DFR_REV_HQ=1"])}


def script(eng, seed):
    """the settings, a CC 10, then N separate notes; returns (lines, note-on times ms, end ms)"""
    rng = np.random.default_rng(seed)
    ls = [cc(4, c, v) for c, v in h.param_defaults() if c not in (79, 80)]   # (a suite's reset: every default,
    ls += [cc(5 + i // 16, h.CC[k], v) for i, (k, v) in enumerate(h.DRY.items())]   # then DRY)
    if eng in ("marimba", "vibes"):
        was, h.ENGINE = h.ENGINE, eng               # (hil_scan's spec of the bar panel)
        try:
            ls += [cc(8, 79, h.MARIMBA_CC79 if eng == "marimba" else h.VIBES_CC79), cc(9, h.vp()["MOT_ON"]["cc"], 0)]
            ls += [cc(10, c, v) for c, v in h.ccs([("NOISE", 0)])]
        finally:
            h.ENGINE = was
    ls.append(cc(1000, 10, 127))
    t, ons = 2000.0, []
    for _ in range(N):
        if rng.random() < 0.3:
            ls.append(cc(int(t - 50), 10, 127))      # (a suite's reset before its take: the gate open)
        key, vel, hold = int(rng.integers(48, 85)), int(rng.integers(60, 121)), float(rng.uniform(150, 600))
        ls += [f"{int(t)} midi 0x90 {key} {vel}", f"{int(t + hold)} midi 0x80 {key} 0"]
        ons.append(int(t))
        t += hold + float(rng.uniform(2500, 4000)) + float(rng.uniform(0, 6))
    return ls, ons, int(t + 500)


def worst_diff(a, b, ons):
    """per note: the largest |a - b|, aligned on the onsets (+-4 frames), 64 frames before .. 60 ms after"""
    n, out = int(0.06 * 44100), []
    for t in ons:
        i0 = int((t - 5) * 44.1)
        ia = int(np.argmax(np.abs(a[i0:]) > 100)) + i0
        ib = int(np.argmax(np.abs(b[i0:]) > 100)) + i0
        k = min(range(-4, 5), key=lambda k: np.abs(a[ia + k:ia + k + n] - b[ib:ib + n]).sum())
        out.append(float(np.abs(a[ia + k - 64:ia + k + n] - b[ib - 64:ib + n]).max()))
    return out


@pytest.mark.parametrize("eng", sorted(LIMIT))
def test_a_mid_block_start_sounds_as_a_block_start(eng):
    B = bins()
    bad = []
    for seed in SEEDS:
        ls, ons, end = script(eng, seed)
        a = run(f"midst_{eng}_{seed}_lat", ls, end_ms=end, binary=B["lat"]).L.astype(float)
        b = run(f"midst_{eng}_{seed}_one", ls, end_ms=end, binary=B["one"]).L.astype(float)
        d = worst_diff(a, b, ons)
        bad += [(seed, t, round(x)) for t, x in zip(ons, d) if x > LIMIT[eng]]
    assert not bad, f"{eng}: notes over {LIMIT[eng]} LSB (seed, ms, LSB): {bad[:12]}"
