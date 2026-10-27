## Fixed
- A held note dying away no longer leaves a faint, fixed distortion under it (GitHub #16): the engines' mix is
  dithered where it is cut to 16 bits, so its last few bits fall into a steady noise floor (about -96 dBFS, under
  the headphone jack's own hiss) instead of odd harmonics that stay put while the note fades. Silence is still
  exact digital zero. Every engine (Rhodes, VIBES, MARIMBA).

## Tested
- Host: tests/py/test_dither.py (a held Rhodes A2: its 3rd and 5th harmonics at the noise floor where they stood at
  -117 dBFS before; exact zero before and after a note on every engine, SPREAD on); every render's baseline
  re-saved on purpose (tools/gate_r.py).
