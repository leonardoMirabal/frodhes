## New

- **The EQ page on the Move**, between COMPRESSOR and DRIVE/FILTER as on the FM-1: LOW, MID, FREQ (the FM-1's MID
  FREQ) and HIGH on knobs 1-4, CC 34-37 as before.
  - The cards show the FM-1's values: the gains in dB (-15 to +15, +0.0 flat, a notch at the middle of the bar), FREQ
    in Hz (200 to 5.0K).
  - The screen reader says the names and the values with their unit (*Low, plus 3.1 decibels*, *Mid frequency, 1.0
    kilohertz*); the page is *EQ*.
  - The Move's page count is one higher (13 pages, 14 with VIBES). The module help lists the page.

## Changes

- The Move's presets, sets and settings from before the EQ load as they were, with the EQ flat: the factory presets
  F01-F57, the user slots and Schwung's saved state.
- `docs/MOVE.md` and the README's Move section for the first public release: the EQ page, the install routes, the
  screen reader's words. Every Move screenshot is drawn again (the page counts) and three are new (`move-eq.png`,
  `move-eq-set.png`, `move-eq-set-k3.png`).

## Tools

- `move/tests/test_bridge.c` script: `state_dump FILE` / `state_file FILE`, and `TB_DIR` for a data folder of its own.
- `move/tests/main097/`: a state and a store.bin (as hex) saved by the main branch's Move plugin before the EQ.
- `tools/gate_m.py --save`: `tests/baselines/move16.sha256` re-saved for the ship-1c sound changes (40 of 42 renders;
  their 10-voice renders moved in Gate R's re-save too).

## Tested

- Host: `tests/py/test_move_eq.py` (the page and its order; the texts against the firmware's at all 128 values;
  main's state and store.bin load with the EQ flat, U05 as saved, F01 / F20 / F57 flat after the EQ was moved, a user
  preset saved with the EQ keeps it), `move/ui/test_screen.mjs` (the EQ page, its texts and bars),
  `move/ui/test_chain.mjs` (the screen reader on the EQ), `make test-move`, Gate M.
- Not yet on a Move: the EQ page (the device pass).
