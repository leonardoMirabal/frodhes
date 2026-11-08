## Changes

- Electric piano, the hardest strikes: notes near velocity 127 no longer overdrive the pickup. Above a knee that
  depends on the key and TYPE, the tine's swing is cut a little more with each velocity step (up to about 22 dB at
  v127 on the DYNO top keys, none on most FELT keys) and the output gain takes it back, so the fundamental keeps
  its level. The second harmonic at v127 is now at or below -12 dB re the fundamental on every TYPE, sustain and
  attack (0.9.9: up to -5 dB sustain and -2 dB attack on DYNO, -8 / -4 dB on STAGE 77 and SUITCASE). v127 is still
  louder than v110 (+3.4 to +3.9 LU K-weighted) and brighter (the attack's spectral centroid higher). Every velocity
  under the knee, and VIBES and MARIMBA, sound exactly as before.

## Tested

- Host: H2 and the attack's H2 (0-150 ms) at v110 / v127, the K-weighted loudness and the centroids on every TYPE,
  RESPONSE A and B (tests/py/test_rhodes_top.py, tools/rh_top.py); byte-identity under the knee and on the bars
  against a build without the cut; tools/dry_dist.py with the attack cells, 0 values over 0.9.9; `make test`;
  `make pristine` (only the 12 known effects-on v40 cells remain, as before this change); the TONE level fit
  (tools/tone_level.py) re-run for TONE above 64.
