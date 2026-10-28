## Fixed

- The damper with the pedal up is the same before and after the first pedal move. The keys boot fully damped, but after any CC 64 (even a CC 64 0) or an engine switch, a released note was damped a hair less (32767 of 32768): a phrase rendered up to 8 LSB apart depending on whether a pedal message had arrived. Not audible; the sound is now the same either way.

## Tested

- `tests/py/test_pedal_weight.py`: the table's full damping equals both engines' boot value; a Rhodes phrase with and without a CC 64 0 first renders byte-identical (8 LSB apart on 0.9.7); VIBES and MARIMBA booted from a saved preset, the same.
