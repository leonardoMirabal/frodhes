# SPDX-License-Identifier: GPL-3.0-only
"""VIBES / MARIMBA start at most BAR_NV notes in one audio block (0.9.8, bars.c bar_strike's bar_starts).

The FM-1 (the 0.9.8 candidate, monkey seed 3037, 3 runs) peaked at 4460-4692 us whole ISR (target 4500): a Rhodes
preset rang with the pedal, a Program Change loaded F14 VIBES ECHO (the TYPE change stops the Rhodes: the bar engine
starts with every voice free), and 26 ms later a sequencer step sent 26 note-ons at once. bar_strike struck the 10 free
voices (bar_init each: 16 coefficient sets and the pulse) and then stole those same voices for the next 10 notes,
whose bar_init ran again at the block's end: 20 starts in one block (the host's twin of that block: 56 us against 24
steady, 41 with the limit). With voices ringing the steals alone fill a block (every voice waits), so 10 starts was
already the most there. The note past the limit now waits for the next block, in order, as a waiting note always did.

The checks, on a 3.5 s host render of that event (a Rhodes chord under the pedal, the Program Change, the burst):
at most BAR_NV starts a block; the cost model's worst block (bars.c bar_cost_fixed: 40 us a start; 3788 us VIBES /
3751 MARIMBA before the limit, 3580 / 3555 with it) within 100 us of the same burst on voices already ringing (where
the limit never binds: 3763 / 3488); the burst's first block sample for sample a plain chord of its first BAR_NV
notes; and no click: the stolen notes' fade block below the attack block, and no block above 1.15 x the loudest
attack (largest second difference) of the burst's notes struck as plain chords (VIBES 3567 against 3930, MARIMBA
2811 against 2580: the later notes start one block later, over the contact noise of the first ones). tools/hil_cpu.py
--worst has the FM-1 row ("program change ... burst")."""
import os
import re

import numpy as np
import pytest

from hostrun import ROOT, run, voices

FS = 44100
RING = [(48, 90), (52, 60), (55, 110), (59, 40), (60, 127), (64, 75), (67, 100), (71, 50), (72, 120), (76, 85)]
BURST = [(47, 1), (23, 10), (26, 10), (29, 10), (42, 11), (45, 11), (127, 93), (86, 91), (123, 93), (59, 107),
         (120, 93), (28, 88), (40, 11), (34, 49), (37, 11), (41, 49), (21, 83), (68, 83), (117, 93), (60, 107),
         (33, 1), (36, 1), (27, 83), (19, 85), (22, 88), (25, 88)]          # seed 3037's step (2.873 s)
RING_MORE = [(79, 70), (81, 95), (83, 55), (84, 105), (89, 65), (91, 115)]   # builds with more voices (FR_C1: 14):
PC_AT, BURST_AT = 1500, 1530                                                # the chord still fills them all


def ring_notes():
    return RING + RING_MORE[:max(0, voices("bars") - len(RING))]


def lines(pc, notes):
    """F18 DREAM (Rhodes, effects on) with a pedalled 10-note chord (every voice, at least), then Program Change pc,
    then notes at once"""
    ls = ["50 midi 0xC0 17 0", "80 midi 0xB0 64 127"]
    ls += [f"{100 + 4 * i} midi 0x90 {n} {v}" for i, (n, v) in enumerate(ring_notes())]
    ls.append(f"{PC_AT} midi 0xC0 {pc} 0")
    ls += [f"{BURST_AT} midi 0x90 {n} {v}" for n, v in notes]
    ls += [f"2500 midi 0x80 {n} 0" for n, _ in notes]
    ls.append("2600 midi 0xB0 64 0")
    return ls


def starts(r):
    m = re.search(r"bar starts max per block (\d+)", r.out)
    assert m, "the host report has no bar starts line"
    return int(m.group(1))


def d2_blocks(r, t0, nb):
    """the largest |second difference| of the mono mix in each of nb blocks from the block holding t0 ms"""
    m = (r.L + r.R) / 2
    b0 = int(t0 * FS / 1000) // 256 * 256
    return [float(np.abs(np.diff(m[b0 + k * 256 - 2:b0 + (k + 1) * 256], 2)).max()) for k in range(nb)]


def ringing(pc, notes):
    """the same, the bar preset loaded first (the chord rings on the bar engine): the burst steals ringing voices"""
    ls = [l for l in lines(pc, notes) if not l.startswith(f"{PC_AT} ")]
    ls[0] = f"50 midi 0xC0 {pc} 0"
    return ls


@pytest.fixture(scope="module", params=[13, 15], ids=["vibes", "marimba"])
def runs(request):
    pc, nv = request.param, voices("bars")
    r = dict(burst=run(f"bar_starts_burst_{pc}", lines(pc, BURST), end_ms=3500),
             ring=run(f"bar_starts_ring_{pc}", ringing(pc, BURST), end_ms=3500))
    r["chords"] = [run(f"bar_starts_chord_{pc}_{k}", lines(pc, BURST[k:k + nv]), end_ms=3500)
                   for k in range(0, len(BURST), nv)]
    return r


def test_at_most_bar_nv_starts_a_block(runs):
    nv = voices("bars")
    for k in ("burst", "ring"):
        assert starts(runs[k]) == nv, f"{k}: {starts(runs[k])} notes started in one block (at most {nv})"
        assert "ev_drop 0" in runs[k].out


def test_the_burst_costs_as_on_ringing_voices(runs):
    """within 100 us at 10 voices; with more (FR_C1: 14) the burst block's starts are 14, the margin 200 (MARIMBA 3182
    against 3002 on the two-core build: 14 starts where the ringing case steals; both far under the cost model's 4650)"""
    b, r = runs["burst"].cpu_proxy_bars, runs["ring"].cpu_proxy_bars
    assert b <= r + (100 if voices("bars") <= 10 else 200), (b, r)
    assert b <= 4650, b


def test_the_first_block_is_the_chord(runs):
    burst, chord = runs["burst"], runs["chords"][0]
    b1 = (int(BURST_AT * FS / 1000) // 256 + 1) * 256          # the block after the one the burst lands in
    assert np.array_equal(burst.L[:b1], chord.L[:b1]) and np.array_equal(burst.R[:b1], chord.R[:b1])


def test_no_click(runs):
    a = d2_blocks(runs["burst"], BURST_AT, 10)
    attack = max(max(d2_blocks(c, BURST_AT, 10)) for c in runs["chords"])
    assert a[1] < a[0], f"the steal fade's block {a[1]:.0f} over the attack's {a[0]:.0f}"
    assert max(a) <= 1.15 * attack, (a, attack)
    assert max(np.abs(runs["burst"].L).max(), np.abs(runs["burst"].R).max()) <= 24000   # the limiter's ceiling


def test_the_newest_notes_sound():
    """the notes past the limit are not lost: after the burst every voice sounds (the steals keep the newest)"""
    r = run("bar_starts_alive", lines(13, BURST) + ["2000 dump"], end_ms=2100)
    assert int(r.state(2000)["voices"]) == voices("bars")
