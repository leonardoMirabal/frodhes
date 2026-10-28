## Fixed

- VIBES and MARIMBA: a released note now always ends once it has died away. One of a bar's upper partials could get caught in a rounding cycle near the last bit and hold its level instead of decaying, so the voice stayed just above the level where it is let go: it kept its voice (and its processing time) for up to 15 s and changed which voice the next notes took. On a released, damped note such a partial is now put to sleep once it is under 2 LSB and stops falling; what is heard while the note rings, and every note held or under the pedal, is unchanged.

## Tested

- Host: `tests/py/test_bar_kill.py` (the burst script: no released upper partial holds its level; the recorded case with 0.9.7's damping table and every third key on VIBES and MARIMBA at three velocities: each released voice ends within 80 blocks of falling under 4 LSB; three of the four fail without the fix), `make test-move` (the Move's HQ voices keep their float partials), Gate R, Gate H and Gate M re-saved (the moved renders listed in the commit).
