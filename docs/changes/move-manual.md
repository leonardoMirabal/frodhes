## Changes

- The Move's manual (`docs/MOVE.md`) is a user manual of its own: install, opening Frodhes' screen, the screen, the
  buttons and knobs, every page with its picture (each VOICE page with its engine), the menu, presets and saving,
  sustain, the effects, velocity (the Move's Full Velocity setting off), MIDI and tempo, the arpeggiator, several
  Frodhes at once, what differs from the FM-1 and troubleshooting. The build, the tests and the device measurements
  moved to `docs/MOVE-DEV.md`. The FM-1 manual's appendix 16 is a short summary that points there.
- The Move's pictures show today's screen: 27 of them, drawn by `move/module/ui_chain.js`, the file the Move runs
  (the page list counts the menu rows; the arpeggiator at a new instance's settings; FREEZE, the header with FX off
  and SUS on, the reset message, the menu's module rows). The picture of Schwung's knob grid is gone: Frodhes no
  longer uses that screen.

## Tools

- `tools/move_ui_shots.mjs` runs `ui_chain.js` (Schwung's chain_ui) in place of `screen.js`, draws the docs'
  states by name, and has `--check`: a stale, missing or unlinked picture, or a wrong `?v=` in `docs/MOVE.md`,
  `docs/MOVE-DEV.md`, `README.md` or `docs/MANUAL.md`, fails it. `--docs` removes pictures the docs no longer use.

## Tested

- Host: `tests/py/test_move_ui.py` (`test_move_doc_shots_current` runs `--check`; it fails on a changed, extra or
  unlinked picture), `tests/py/test_move_doc.py` (the page table is the screen's pages, every page has a picture,
  the developer part is apart), `tools/doc_links.py`, `tools/ci_move.sh`.
- Not tested on the Move: the pictures added here (FREEZE, the X header, the reset message, the menu's module rows)
  against the display mirror; they are drawn by the same code that matched it on 2026-10-30.
