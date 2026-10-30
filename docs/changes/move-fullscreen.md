## Changes

- On the Move, Frodhes' own screen is the whole UI for Frodhes (Schwung's own-screen path: the plugin serves its
  pages as `ui_pages`, for external surfaces, and no `ui_hierarchy`). Open it with Shift + volume touch + the
  track's Track button, then a jog click on the synth. The overview, the large card (half a second after the knob is
  let go), the page list and the COMPRESSOR meter are as before.
- The bars are one height along their length: an outlined box filled up to the value (5 px on the overview, 6 px on
  the large card), with notches for a selector's steps and a bipolar value's centre.
- The knob LEDs show each knob's value (Schwung's knob ring colours). The Left arrow's light is on while FX is on,
  and the Right arrow's while SUS holds. Back gives the lights back to the Move.
- The header shows `X` when the effects are off.
- The menu (the page list) goes on below the pages: SAVE TO USER saves the sound into U01-U16 (Frodhes' own user
  presets, shared by every Frodhes on the Move), and Schwung's own rows for a module (its presets, Module Help, Add
  to List, Clear Automation, Swap Module, Remove Module).
- A double tap on a knob resets it to its default.
- Schwung's web remote and the device tools get the preset, its name and the count again (chain_params
  `extra_keys`).

## Tested

- Host: move/ui test_chain (the knob and arrow LEDs, the menu rows, SAVE TO USER, Schwung's rows, restorePage,
  the double-tap reset, the bars), test_screen, test_cell, test_grid; make test-move (u_save: U05 saved, named,
  loaded, and back in a new process; u_names); the Move pytests.
- On the Move (the screen from Schwung's display mirror, the controls through Schwung's test bus): the pages, the
  cards and the bars as the host renders them; the card goes 0.52-0.56 s after the knob is let go; the arrows
  (by the user); the menu's MODULE rows with the real host (Module Help opens and Back comes back to the list; the
  module picker opens and Back comes back to the list).
