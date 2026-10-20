# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA PLAN §3.12 / §8.2 C1: a change between the two bar types (VIBES <-> MARIMBA) while notes ring fades the
bar engine out over one block and stops its voices, as a change between engines does (a voice never re-strikes with
the other type's tables); the same TYPE again changes nothing; the next note plays on the new type. RAW (CC 119):
the engine alone, so "stopped" is exact digital zero. The host report names the TYPE that rendered last (C11)."""
import re

import numpy as np

import hostrun


SEL = {"VIBES": "79 0", "MARIMBA": "79 127"}


def ring(first, then, note_after=False):
    ls = ["50 midi 0xB0 119 127", f"60 midi 0xB0 {SEL[first]}", "61 midi 0xB0 47 0", "100 midi 0x90 60 100",
          "100 midi 0x90 67 100", f"600 midi 0xB0 {SEL[then]}"]
    if note_after:
        ls.append("700 midi 0x90 72 100")
    return hostrun.run(f"mswap-{first}-{then}-{int(note_after)}", ls, end_ms=1200)


def test_bar_type_change_stops_the_old_voices():
    for a, b in (("VIBES", "MARIMBA"), ("MARIMBA", "VIBES")):
        r = ring(a, b)
        assert np.abs(r.seg(400 / 1000, 590 / 1000)).max() > 100                  # it rang
        tail = r.seg(620 / 1000, 1200 / 1000)
        assert not np.any(tail), f"{a} -> {b}: still sounding after the fade block"
        x = r.seg(600 / 1000, 620 / 1000)                                         # the fade: one block, no step to zero
        assert np.abs(np.diff(x)).max() < 2000


def test_same_type_changes_nothing():
    """the same TYPE sent again: sample for sample the render without it (the marimba's notes are near silent by 1 s,
    so 'still ringing' alone would not tell)"""
    for t in ("VIBES", "MARIMBA"):
        r = ring(t, t)
        ls = ["50 midi 0xB0 119 127", f"60 midi 0xB0 {SEL[t]}", "61 midi 0xB0 47 0", "100 midi 0x90 60 100",
              "100 midi 0x90 67 100"]
        r0 = hostrun.run(f"mswap-{t}-none", ls, end_ms=1200)
        assert np.abs(r.seg(620 / 1000, 800 / 1000)).max() > 100, f"{t} -> {t}: the notes stopped"
        assert np.array_equal(r.seg(0, 1200 / 1000), r0.seg(0, 1200 / 1000)), f"{t} -> {t}: the render changed"


def test_next_note_plays_on_the_new_type():
    for a, b, want in (("VIBES", "MARIMBA", 5), ("MARIMBA", "VIBES", 4)):
        r = ring(a, b, note_after=True)
        assert np.abs(r.seg(800 / 1000, 1200 / 1000)).max() > 100
        assert int(re.search(r"^type (\d+)$", r.out, re.M).group(1)) == want
