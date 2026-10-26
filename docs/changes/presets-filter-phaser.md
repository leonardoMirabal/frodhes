## Fixed
- Loading a preset while a chord or a tail rings no longer clicks: the effects it switches fade over instead of
  switching in one sample, and the Rhodes TYPE crossfades.
- FILTER: RESO glides; a jump of the cutoff (a MIDI CC 74 jump, a preset) moves at most 1/8 octave a step, so a
  band-pass no longer clicks and rings over; one TYPE to another crossfades on the running filter instead of
  passing through the dry sound (HP12 -> HP18 jumped about 21 dB).
- PHASER: DEPTH and FEEDBK glide; its sweep keeps time while the phaser is off, so it comes back in step.

## Tested
- Host: preset loads under a held, pedalled chord (F01 to F02, F06, F07, F09, F11 and F12, F07 / F09 to F01, F11 to
  F19): the largest second difference around the load 0.8-6.5x the same chord without it (the click limit is 10x);
  the control sweep's FILTER and PHASER cases.
