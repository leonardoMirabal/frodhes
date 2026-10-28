## Changes

- On the Move, Frodhes' own screen (PAGES) takes the Right and Left arrows: Right turns SUS on or off, Left turns
  the effects off or on. Up and Down stay the Move's octave. On Schwung's
  knob screen, SUS and FX on the first page do the same (Schwung gives a module's buttons to its own screen only).
- On the Move, FX (the first page's switch and the Left arrow) keeps the compressor on: every other effect goes
  off. The FM-1's FX button still bypasses everything.

## Tested

- move/ui tests (the arrows' presses), make test-move (s_fxbtn flips FX, a release does nothing), Gate M (one
  render moved, the FX bypass with the compressor on: move16.sha256 re-saved), Gate S (the FM-1 and SMK-37
  packages byte-identical).
