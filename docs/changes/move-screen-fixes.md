## Fixed

- On the Move, a double tap now resets every knob but PRESET. TUNE, the TREM switch, the four ARPEGGIO knobs, CHAN
  and SUS had no default, so a double tap on them did nothing. Their defaults are the FM-1's first boot: TUNE 0, the
  TREM switch OFF, the arpeggiator OFF at RATE 1/16, OCT 1 and GATE 60 %, CHAN ALL, SUS OFF.
- On Frodhes' own screen, a two-way switch (SUS, FX, COMP, MOTOR, PHASER, CHORUS, RETRIG) filled half its bar at OFF,
  which looked like a value. Its bar is now empty at OFF and full at ON. Bars with three or more options and bars
  with a centre are unchanged.
- The overview's card names ran into the next card (`AT CCAT LO`, `THRSHRATIO`). Each card name is now four letters
  at most (DPTH, RTIO, ATCC ...), so there is a clear gap before the next card. The large card still shows the full
  name.

## Tested

- Host: `move/ui/test_screen.mjs` (new: every two-option switch is empty at its first option and full at its second,
  on every page and engine; TYPE's notched bar and TONE's centre mark are unchanged; every overview name is at most
  its card's width less 1 px, on every page and engine), `tests/py/test_move_module.py` (new: every knob but PRESET
  has a default equal to the firmware's), `test_bridge boot` (new: every setting's chain_params default equals a new
  plugin instance's value), `tests/py/test_move_hil.py` (the usb-host alignment case is now built from the host
  render instead of a recorded take, and it still fails on the old alignment), `tools/ci_move.sh`,
  `tests/py/test_repo_hygiene.py`, `tools/doc_links.py`, `node tools/move_ui_shots.mjs --check`.
- Not tested on the Move.
