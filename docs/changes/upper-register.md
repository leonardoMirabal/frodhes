## Changes
- A cleaner upper register on every electric piano type (every package: FM-1 one-core and two-core, the Move, the
  plugin, the SMK-37). Above C5 (key 72) the pickup's second harmonic no longer grows with velocity past a ceiling:
  it stays about 22 to 34 dB under the fundamental at every velocity (it reached about 11 dB under at v127 on the
  boot preset). The level is the same: each key's v64 and v127 level within 0.2 dB. Bass and middle keys, VIBES and
  MARIMBA are unchanged (byte-identical below key 73).
- BELL above 64 lifts the tine's bell partial less in the upper register: full from C4 down, fading to no lift at
  C5 and above (a smooth curve over the octave). BELL 64 and under is unchanged everywhere. The top keys' first 20 ms
  are clean of the ringing that BELL 127 put over them (on the boot preset, keys 72 / 84 / 96: about 12 dB quieter
  in that band; keys 66 / 69: 4 / 9 dB).
- The upper keys' quiet tail ends as before: the tine's upper modes stop at the same level, so no faint steady tone
  stays under a long note.

## Tools
- `tools/rh_top.py --write` also writes `RH_TOP_S` (the ceiling's span per key, above key 72) and holds the ceiling's
  loudness gain back to 0.3 dB at v64.
