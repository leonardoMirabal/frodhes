## Changes
- COMPRESSOR: THRESH and RATIO bring their own make-up (none up to -17.6 dB, where every factory preset sits): it gives
  back the first dB of reduction, never more than the compressor takes at that moment, so no peak rises over the
  compressor-off level. MAKEUP is a trim on top.
- COMPRESSOR: past -25 dB (faded in from -17.6) a held note relaxes to about two thirds of its reduction over 0.2 s
  (a strike gets the full RATIO), so heavy compression brings the sustain up against the attack. The factory presets
  (THRESH -14.5 .. -17.6 dB) are unaffected by both.

## Fixed
- COMPRESSOR: a release the next peak catches again (a chord's beating) no longer pumps the gain: RATIO 4:1 with the
  default ATTACK 40 and RELEASE 48-50 (RELEASE 60 and under: a re-catch under 3.5 dB is slowed) put the loud mid chord's intermodulation at -43 dB (the dry piano -79); now
  -60 or better. F01's compressor: -72.6 .. -73.8 dB on the mid chord (was -68.8 .. -69.1). F10 (4:1, ATTACK 20, RELEASE 40): -52 (was -35).

## Tested
- Host: tools/dry_dist.py's chords at COMP 4:1 ATK 40 REL 48 (no failure against 0.9.9 played the same);
  tests/py/test_comp_clean.py; the compressor's level, fatness and peaks by critical band (C2..C6 x v40/80/127,
  chords); test_every_control, test_display_sound, test_switch_clicks updated.
