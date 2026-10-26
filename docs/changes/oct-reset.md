## Changes
- OCT− and OCT+ together go back to octave 0: hold one and press the other (GitHub issue #15). The first button
  still steps the octave as before; letting go steps nothing. The same on the SMK-37.

## Tested
- Host: tests/scripts/oct_reset.txt (both orders, from either end, single presses still step; fails on 0.9.5) on the
  FM-1 and the SMK-37 builds, make test, make test-smk.
