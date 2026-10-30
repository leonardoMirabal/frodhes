## Changes

- On the Move: a root pad that stays white after you play it, until you play another note, is the Move's own
  last-note light and not caused by Frodhes. The Move does the same with its own instruments. Frodhes' screen
  never lights a pad or a step button: its only lights are the knob rings and the two arrows. This is written in
  docs/MOVE.md, and a host test now holds it.

## Tested

- move/ui/test_chain.mjs: pad and step notes (note-on, pressure, note-off, velocity 0) on a page, with a knob
  held, with SUS on, in the list and on the save card. The screen claims none of them, sends no LED, writes no
  param and keeps its state. Its only button LEDs are CC 62 and 63. The test fails when a pad note-off writes an
  LED. test_chain.mjs was in no gate before. It now runs in the Move pytest suite (tests/py/test_move_ui.py
  test_chain_ui_behaviour, so in tools/ci_move.sh).
- On the Move, with presses sent through Schwung's test bus and Schwung's MIDI_OUT log before and after its own
  LED work: Frodhes' screen open, its screen closed, and the Move's track 4 electric piano with no Schwung screen.
  In all three, the Move writes the pad 126 at the press and 122 at the release, and the root's colour comes back
  only when another pad is played. A two-pad chord behaves the same. Schwung's output matches the Move's. Frodhes'
  screen wrote LEDs only when it opened (the arrows and the knobs, once) and at Back (the arrows off, then Schwung's
  replay of the Move's knob and track lights). It wrote none while pads were played.
