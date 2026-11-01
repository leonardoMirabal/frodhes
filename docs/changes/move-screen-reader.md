## New

- Move: Frodhes' screen speaks through Schwung's screen reader (Global Settings > Screen Reader), in words: the
  screen opening (Frodhes and the preset), a page (its name, where it is, how many controls), a knob touched (the
  control's whole name and its value) or turned (the new value, at most one every 0.12 s while turning fast, then the
  value it ends on), the arrows (Effects on / off, Sustain on / off), a double-tap reset, the messages, the list's
  rows, the SAVE TO USER card (the slot, saved or not saved) and a preset change. With the screen reader off it says
  nothing and costs nothing. The list is in MOVE.md, "Screen reader".

## Tools

- `tools/gen_move_module.py`: the spoken names (`SAY_NAMES`, every control on a page, by key; the generator stops on
  a new control without one), `SAY_PAGES` and `SAY_WORDS`, into `ui_chain.js`'s `FR_CONF.say`; `ui_chain.js` imports
  Schwung's `shared/screen_reader.mjs` announce. `tools/move_ui_shots.mjs` leaves that import out, as the LED ones.

## Tested

- Host: `move/ui/test_chain.mjs` records every announcement: the screen opening, the jog's pages (none at the first
  page), a touch, a fast turn (one value, then the last) and a slow one (each value), the words (Ratio 4 to 1,
  Compressor Off, Reverb type Infinite, Decay Freeze, Sustain), the arrows, the double tap (Mix reset), the list (the
  row, the section entered, Schwung's row and value, a page row's click, Back), the save card (the slot, Not saved,
  Saved to user 2), a row done in place, the PRESET knob and a preset loaded elsewhere; 30 idle ticks say nothing and
  never ask the screen reader; with it off nothing is said for anything. The checks fail on the previous ui_chain.js
  (29 of them).
