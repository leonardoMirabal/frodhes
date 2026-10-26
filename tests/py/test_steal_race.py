# SPDX-License-Identifier: GPL-3.0-only
"""BUGHUNT #21 (0.9.6): a played note must never be lost to a voice steal.

- A stolen voice fades out (64 samples) before the note waiting for it starts. A voice that died away during that
  fade ended there (rhodes.c, the kill check) and the waiting note never started: the host's own check printed
  "RACE LOST". Found by tools/monkey.py (seed 22); the clean repro is 10 notes held at DECAY 18, the oldest about to
  die, then a new note. The bar engine has the same paths (bars.c, the kill check and the life counter); its fade ends
  within the block, so they are defended, and checked here the same way.
- More new notes in one audio block than voices: the last one overwrote voice 0's waiting note (rhodes.c, bars.c
  "pick = 0"): the first new note never sounded. Now a note-on that finds every voice waiting stays in the ring and
  starts the next block, in order.

The host state vnN counts the voices sounding note N (alive, not fading for a steal)."""
import pytest

from hostrun import run

ENG = {"rhodes": [], "vibes": ["50 midi 0xB0 79 0", "51 midi 0xB0 47 0"],
       "marimba": ["50 midi 0xB0 79 127", "51 midi 0xB0 47 127"]}


def lost(r):
    return [ln.strip() for ln in r.out.splitlines() if "RACE LOST" in ln]


def test_rhodes_steal_of_a_voice_dying_in_its_fade():
    """the monkey's clean repro: the new note (52) starts in the oldest voice, which dies away in its steal fade"""
    L = ["10 midi 0xB0 102 18", "100 midi 0x90 105 92"]
    L += [f"{105 + 5 * i} midi 0x90 {n} 100" for i, n in enumerate([102, 99, 96, 93, 90, 87, 84, 81, 78])]
    L += ["513 midi 0x90 52 100", "530 expect state vn52 1", "813 end"]
    r = run("steal_race_rhodes", L, check=False)
    assert not lost(r), lost(r)
    assert not r.fails, r.fails


@pytest.mark.parametrize("eng", list(ENG))
def test_a_steal_near_the_oldest_voice_end(eng):
    """every engine: ten voices held, the oldest (a high, short note) near its end; a new note struck at every
    millisecond around that end: it always starts, no note is lost (the Rhodes at DECAY 18)"""
    hi = 96 if eng != "rhodes" else 105
    for t in range(300, 520, 7):
        L = list(ENG[eng]) + (["10 midi 0xB0 102 18"] if eng == "rhodes" else [])
        L += [f"100 midi 0x90 {hi} 100"] + [f"{110 + 2 * i} midi 0x90 {n} 100"
                                             for i, n in enumerate([48, 50, 52, 53, 55, 57, 59, 60, 62])]
        L += [f"{t} midi 0x90 40 100", f"{t + 20} expect state vn40 1", f"{t + 30} end"]
        r = run(f"steal_race_end_{eng}", L, check=False)
        assert not lost(r), (t, lost(r))
        assert not r.fails, (t, r.fails)


@pytest.mark.parametrize("eng", list(ENG))
def test_more_new_notes_than_voices_in_one_block(eng):
    """ten notes held, then eleven new ones in the same millisecond (the fifth let go at once): every voice already
    waits when the eleventh comes; it starts the next block (stealing the let-go fifth), and the first new note
    still sounds (it was overwritten: 0.9.5 lost it)"""
    L = list(ENG[eng]) + [f"100 midi 0x90 {n} 100" for n in [36, 38, 40, 41, 43, 45, 47, 48, 50, 52]]
    for i, n in enumerate([60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77]):
        L.append(f"500 midi 0x90 {n} 100")
        if i == 4:
            L.append(f"500 midi 0x80 {n} 0")
    L += ["560 expect state vn60 1", "560 expect state vn77 1", "560 expect state vn67 0", "560 expect state drops 0",
          "600 end"]
    r = run(f"steal_race_over_{eng}", L, check=False)
    assert not lost(r), lost(r)
    assert not r.fails, r.fails
