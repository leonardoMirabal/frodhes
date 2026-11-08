## Changes

- Rhodes, tine ring: the tine's inharmonic second mode (at 6.3-6.9 times the note) is turned down per key from
  A3 up, so a hard attack in the upper register no longer rings with it: at v127 its band in the first 20 ms is
  -40 dB re the fundamental at C5, -55 at C6 and -65 at C7 (it was -12, -33 and -32; -12 to -24 on C#6-G#6).
  The tables generator fits it per key (`tools/gen_rhodes.py` strike_fit, `params.json` strike_fit); keys below
  A3, the bark and the levels are unchanged.

## Tested

- Host: keys 28-100 at v20 / 40 / 64 / 90 / 110 / 127, before and after: the tine band (0-20 ms), H2 re H1
  (30-300 ms), the > 1 kHz attack excess, the level (50-500 ms) and the level after key-up (never louder).
  VIBES and MARIMBA byte-identical (Gate R); the HQ pulse and contact headers unchanged.
