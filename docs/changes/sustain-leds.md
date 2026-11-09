## New

- DISPLAY 2, a new page right after DISPLAY (SELECT one page on): K1 **SUSTAIN LEDS**. With LEDS on ALL or SCALE, a
  key let go while the sustain keeps its note ringing stays dark until the sound stops (**DARK**, the default, as
  before) or lights again at once (**LIT**), so the map of the keys stays readable with the sustain on (issue #35).
  Kept with the settings; LEDS PLAYED and ROOTS are unchanged. The SMK-37 has no key lights: its DISPLAY 2 is empty.

## Tested

- Host: `tests/scripts/sus_leds.txt` (the card reached, turned both ways, the key LED under the REC latch with DARK
  and LIT, the settings saved when silent); `tests/py/test_sus_leds.py` (the record's layout on both boards, every
  stored value including old and bad ones under ALL / SCALE / ROOTS / PLAYED, the card saved and back after a power
  cycle).
