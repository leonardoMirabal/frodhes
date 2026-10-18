# SPDX-License-Identifier: GPL-3.0-only
"""All notes off (CC 120 / 123) against the voice allocator, the minimal cases of three fuzz seeds (test_fuzz.py,
2026-10-18; they failed at 8 voices as well):

  - a note struck on full voices waits for a steal's 1.5 ms fade (pend); an all-off in that window let go of every
    sounding voice but not the waiting note, which then started held for good: no note-off ever comes for it (the
    all-off sends one EV_ALL_OFF instead of the note-offs). Seeds vibes 780 (bars.c) and rhodes 513 (rhodes.c):
    the waiting note now starts let go and damped.
  - the Rhodes: a low key's tone-bar mode (V2) hardly feels the felt (damped T60 about 50 s, x3.9 at RELEASE 127), so
    after an all-off the voice kept a 1-4 LSB hum and its slot for its whole life (up to ~40 s; rh_quiet() false,
    the flash saves waiting). Seed rhodes 628 (rhodes.c): an all-off caps a voice's life at 8 s, and a voice's
    life ends through the 1.5 ms fade.

The voice count comes from FR_VOICES (rhodes.c, or HOST_DEFS -DFR_VOICES=N)."""
import os
import re

import numpy as np
import pytest

from hostrun import FS, ROOT, cc, run

def voices():
    m = re.search(r"-DFR_VOICES=(\d+)", os.environ.get("HOST_DEFS", ""))
    if m:
        return int(m.group(1))
    return int(re.search(r"#define FR_VOICES (\d+)u", open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()).group(1))


def engine_lines(eng):
    return [cc(50, 79, 64), cc(51, 47, 0)] if eng == "vibes" else []   # VIBES, its motor off


@pytest.mark.parametrize("eng,num", [
    ("rhodes", 123), ("rhodes", 120), ("vibes", 123), ("vibes", 120)])
def test_alloff_during_a_steal(eng, num):
    """every voice busy, one more note (it waits for a steal's fade), the all-off in the same block: nothing is left
    held, and it all goes quiet"""
    nv = voices()
    ls = engine_lines(eng)
    ls += [f"100 midi 0x90 {48 + 2 * i} 100" for i in range(nv)]
    ls += ["500 midi 0x90 84 100", cc(500, num, 0)]
    ls += ["400 dump", "700 dump", "6000 dump"]
    r = run(f"alloff-steal-{eng}-{num}", ls, 6500)
    assert r.state(400)["held"] == str(nv) and r.state(400)["voices"] == str(nv)
    for t in (700, 6000):
        s = r.state(t)
        assert (s["held"], s["pitches"], s["srcs"]) == ("0", "0", "0"), f"{t} ms: {s['held']} voices held"
    assert np.abs(r.L[int(5.5 * FS):]).max() < 2


@pytest.mark.parametrize("eng", ["rhodes", "vibes"])
def test_alloff_tail_ends(eng):
    """a low note at RELEASE 127, let go, then the all-off: within 12 s no voice is left and the engine is silent
    (RAW: the engine alone)"""
    ls = engine_lines(eng) + [cc(60, 119, 127), cc(70, 103, 127), "100 midi 0x90 40 127", "600 midi 0x80 40 0",
                              cc(1000, 123, 0), "13000 dump"]
    r = run(f"alloff-tail-{eng}", ls, 13500)
    assert r.state(13000)["voices"] == "0", "a voice still alive 12 s after the all-off"
    assert np.abs(r.L[int(12.5 * FS):]).max() == 0
