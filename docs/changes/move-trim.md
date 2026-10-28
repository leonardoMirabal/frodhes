## Changes

- On the Move, Frodhes is 2.2 dB louder, the most it can be without clipping: its loudest peak is now -0.5 dBFS
  (it was -2.7). Keep Schwung's slot volume at 100%: above it, Schwung's own gain can clip. The FM-1 is unchanged.

## Tested

- make test-move (the trim's key and the plugin equal to the host build at unity), Gate M 78 of 78.
- On the Move over USB-C: usb-levels (F01 at v100 -24.0 -> -21.8 LUFS), usb-stress on Main Out (16 voices, every
  effect heavy, v127, 30 s: peak -0.5 dBFS, 0 glitches).
