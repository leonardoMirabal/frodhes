## New

- Move: the delay has a page of its own (DELAY: DELAY, MIX, TIME, FEEDBK) and so does the chorus (CHORUS: CHORUS,
  DEPTH, SPEED, SYNC), each with the FM-1's cards in the FM-1's order. The first page has the delay's mix on knob 8
  (DELAY, its PERFORM name on the FM-1); knobs 6 and 7 stay free under the preset's name.
- Move: the arpeggiator (ARPEGGIO: MODE, RATE, OCT, GATE; CC 52-55), on the Move's clock or, with the Move
  stopped, its tempo. Each Frodhes has its own, saved with the set; a Frodhes loaded new starts with it OFF. No
  HOLD. Use it or Schwung's arpeggiator (a MIDI FX), not both.
- Move: on REVERB with TYPE INF, DECAY reads SUSTAIN (120-126) and FREEZE (127) on Frodhes' screen, as on the FM-1.

## Fixed

- Move: notes from a keyboard or a computer kept their velocity only with the Move's Full Velocity off. With it on,
  the Move sends each note to the slot a second time at 127, and Frodhes struck the note again at 127. That copy is
  now dropped. The pads' velocity is the Move's: with Full Velocity on, every pad plays at 127 (turn it off in the
  Move's settings).
- Move: the Left and Right arrows on Frodhes' screen set FX and SUS to the state they show next, in place of a
  toggle, so a press that arrives twice cannot turn it back.
- Move: FX or SUS set to a value under OFF read ON.

## Tested

- Host: `make test-move` (`test_bridge`: the echo drop, every key's top and bottom over 82 keys, the arpeggiator's
  keys, its CCs passed and the sequencer's and chords' dropped, a new instance's arpeggiator OFF over a store.bin
  with it on; `arp_eq.txt`, the arpeggiator through the plugin equal to the host build sample for sample), `node
  move/ui/test_screen.mjs`, `test_chain.mjs`, `test_grid.mjs`, `tests/py/test_move_*.py`, `tools/ci_move.sh`.
- The Move, 2026-10-30: notes from the Standalone port at v20 to v127 on Main Out (-49 to -24 dBFS) with the 127
  copy dropped; the pads' velocity with Full Velocity off (1 to 127 reached the plugin); FX writes logged by source;
  every tested key at its max and one past it; INF at FREEZE plays new notes dry; the arpeggiator at 1/16 on the
  Move's 82 BPM, back after a reload, OFF in a new instance and its own in two slots; usb-stress (16 voices, every
  effect heavy) worst callback 499 us, p99 399 us, under the 725 us limit, no glitch.
- Not tested on the Move: the arpeggiator with the Move's transport playing (start and stop) and with Schwung's
  arpeggiator; the arrows pressed by hand.
