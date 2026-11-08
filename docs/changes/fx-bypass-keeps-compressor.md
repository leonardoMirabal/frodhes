## Changes
- **FX** (the bypass, CC 111) keeps the compressor on: it switches off every other effect. The bypass had dropped the
  compressor and its make-up, so FX off sounded 1.0-1.2 dB weaker on F01-F04 (up to 2.6 dB on other factory
  presets) and changed the piano's dynamics. With every other effect at depth 0, FX on and off now match within
  0.5 dB in level and in width (both mono), on the one-core and the two-core builds. The compressor's meter stays live
  while bypassed. (The Move had this already, `FR_BYP_KEEP_CMP`; it is now the default everywhere, `-DFR_BYP_KEEP_CMP=0`
  for the old bypass.)

## Tested
- Host: `tests/py/test_bypass_level.py` (fails before, passes after: FX on vs off with the compressor's make-up
  +7.5 dB before, 0.00 after; F01-F04 +1.0 .. +1.15 dB before, -0.2 .. -0.08 after), `test_move_bypass_comp.py`,
  `make test`, `make test-c1`.
- Not tested on the FM-1 (the user was playing it).
