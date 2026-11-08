## Changes

- FM-1 keys, TOUCH LIGHT / MEDIUM / HEAVY: velocity 127 is kept for the hardest hits. Each key learns its firmest
  landing (its floor). A press as firm as that now plays KEY VEL + 0.75 x (127 - KEY VEL) (119 at KEY VEL 96), and
  127 needs a press clearly firmer (half an octave of the reading under the floor). Before, every press at or under
  the floor played 127.
- KEY VEL above 107: the medium press plays at most 107, so a harder press always has 20 steps to 127. The extra KEY
  VEL lifts the soft presses instead (at KEY VEL 127 the lightest press plays 21). Before, at KEY VEL 127 every press
  firmer than a medium one played 127: 54 % of the presses in a free-playing record. Replayed through the new curve,
  0 % play 127 and the median is 109. FIXED, MIDI notes and the SMK-37's keybed are unchanged.
- The dry-distortion gate (`tools/dry_dist.py`) also measures the attack (aH2..aH6, 0..150 ms) and the FM-1's keys
  through the key path at their hardest. H2 is now held to 0.9.9 + 1.5 dB.

## Tested

- Host: `tests/scripts/keyvel.txt` and `keyvel_high.txt` (both fail on 61baedac), `tests/c/keyvel_test.c`,
  `test_keyvel.py` (the curves at KEY VEL 64 / 96 / 107 / 127 at every TOUCH, the user's record replayed),
  `test_smk_app.py`, `test_touch.py`, `test_dry_distortion.py`, every `tests/scripts` script. The MIDI path is
  byte-identical.
- FM-1: not tested on the device in this change.
