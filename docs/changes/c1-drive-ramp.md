## Fixed
- Two cores: the drive's fade to silence after the last note (the step that removes its small leftover DC without a
  tick) read samples the second core had not made yet and could reach into the next note. It now works piece by
  piece and gives the one-core image's samples, whenever the second core takes its pieces.

## Tested
- Host: `tests/py/test_c1_subblock.py`; the one-core build byte-identical on every script; the two-core build moves
  only toward the one-core 14-voice build (the worst script, 16470 LSB apart, now 3). Not yet tested on the FM-1.
