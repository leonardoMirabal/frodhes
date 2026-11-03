## Fixed

- On the Move, SUS could not be turned off: set OFF, it went back to ON. The PEDAL cell on the first page became, in Schwung's
  knob grid, a two-option switch whose click left the sustain button held down. SUS is now a plain switch, on or
  off (OFF always lets the notes go, over a MIDI pedal held down too); PEDAL is gone; on Frodhes' own screen each
  press of the Right arrow turns SUS on or off. A pedal on CC 64 works as on the FM-1, and its let-up turns SUS
  off.
- The VOICE pages did not follow TYPE: after a change to VIBES or MARIMBA the knobs still showed and turned the
  piano's parameters, so nothing audible changed. The pages now change with TYPE, a preset or a program change,
  within about half a second.
- PAGES seemed to do nothing: its cell had no value and a click opened Schwung's list of pages. Its cell reads OPEN;
  touch its knob and click the jog wheel to open Frodhes' screen.
- FX drew its switch off while the effects played. It is on while they play.
- Two Frodhes: only the one loaded last plays, and the other now says so (its name reads ONE SLOT ONLY). Frodhes
  plays in one slot at a time.
- The compressor's meter is a real meter: a thick bar of 15 segments lit from the right, the held peak a taller
  segment, the reading small under it, an empty dotted outline when the compressor is off (Schwung's COMPRESSOR page
  and Frodhes' screen).
- Labels: SPREAD in full on the first page; the FM-1's names wherever they fit Schwung's cells (STRIKE, MALLET,
  THRESH, ATTACK, ...); HAMMR, DAMPR and MKUP where they do not.

## Tested

- Host: `tests/py/test_move_sustain.py` (the toggle; fails on the first candidate: SUS OFF read 1),
  `tests/py/test_move_engine_pages.py` (the served pages and is_loading per TYPE, preset and program change),
  `move/ui/test_grid.mjs` (the pages through Schwung's own controller: the candidate's contract keeps PIANO VOICE
  after TYPE to VIBE, the new one follows; PAGES opens; no PEDAL; FX; no label cut), `tests/py/test_move_instances.py`
  (two instances, either destroyed, the re-pick; the survivor's knobs change the sound), `move/ui/test_cell.mjs`,
  `move/ui/test_screen.mjs`, `tools/ci_move.sh` (Gate U, `make test-move`, Gate M, the Move pytest suites),
  `make gate-p`, `tools/gate_s.py` (the FM-1 and SMK-37 files unchanged).
