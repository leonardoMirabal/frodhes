## New

- Move: HOLD on the ARPEGGIO page (knob 5), the FM-1's arpeggiator HOLD: ON, the notes keep playing after the pads
  are let go and a chord started after every pad was let go replaces them; OFF lets the released notes go. Saved
  with the set; a Frodhes loaded new starts with it OFF; a double tap sets it OFF. No CC, as on the FM-1.
- Move: 38 factory presets of its own, F20-F57, after F01-F19: the second and third takes (B and C) of every factory
  preset from the review of the factory presets, each with its own name (the list is in MOVE.md). Program change
  35-72 loads them. The FM-1's presets are unchanged; F01-F19 on the Move are the FM-1's.

## Changes

- Move: PRESET on Frodhes' screen turns through F01-F57, then the user slots U01-U16. The user slots keep their
  numbers (program change 16-31, and the saved sets and store.bin as before).

## Tools

- `tools/gen_move_presets.py` writes `move/move_presets.h` (the Move-only presets) from the review's records in
  `move/presets/`; `--check` is in `tools/ci_move.sh`'s Gate U.

## Tested

- Host: `tools/ci_move.sh` PASS (Gate U with the presets check, `make move test-move`, Gate M 78/78, 203 Move
  pytests); `move/tests/test_bridge.c` HOLD (latch, a new chord replaces, SUS, OFF releases, saved with the state, a
  new instance OFF) and every F20-F57 read back against the review's records, F01-F19 equal to the FM-1's, U01-U16
  saved and loaded; `move/ui/test_screen.mjs` (the HOLD card, the PRESET order); the plugin monkey 16 seeds, with
  program changes and preset loads over all 73.
- Gate S: the four FM-1 packages byte-identical to main. `tools/doc_links.py`: 0 problems. The shots: `--check` 0.
- Not tested: the Move itself (it could not be reached on the network).
- Test fix: the plugin monkey no longer maps pressure onto the developer CCs (119, 41), as `monkey.gen` never sends
  them (CC 119 bypasses the limiter by design).
