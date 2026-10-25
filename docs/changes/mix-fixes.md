## Changes
- The manual: DELAY MIX also lowers the dry sound a little (−1 dB at the middle, −6 dB at the top), as it always
  did; REVERB MIX moves in small steps (DECAY and TONE glide); the drive types match the drive off on played
  phrases, and how a held chord's tail and single notes compare.

## Fixed
- PHASER: it keeps the level at every DEPTH. At a low DEPTH (0, the default, and up to about 48) the sweep sat
  still in the middle of the sound as a fixed notch: a chord came out about 4 dB quieter and a single A4 almost 8 dB.
  Below the middle of DEPTH the sweep now moves up as it narrows (to the top of its range at 0), and the phaser's
  level follows DEPTH so a chord's loudness matches the phaser switched off everywhere (within 0.1 LU on the host).
  Turning DEPTH glides, without a zipper.
- CHORUS: the bass stays full. The chorus' short delay cancelled part of the lowest notes (C2 about 10 dB quieter
  at DEPTH 64); the chorus now leaves everything below about 200 Hz as it is, and its level is a little steadier
  across DEPTH (a chord within about 1 dB of the chorus off).
- CHORUS: turning DEPTH no longer zippers (the delay tap jumped and the mix stepped with each step of the knob).
- DELAY: turning MIX no longer steps the echoes' level (DIG, ANA and TAPE): it now glides sample by sample.

## Tested
- Host: tests/py/test_mix_levels.py (the chorus DEPTH, phaser DEPTH and delay MIX zipper, the chorus' bass and level,
  the phaser's level at every DEPTH, the chorus' pitch while DEPTH moves; it fails on the code before), make test,
  make pytest, Gate R (re-saved: the chorus and phaser renders move on purpose), ./build.sh.
