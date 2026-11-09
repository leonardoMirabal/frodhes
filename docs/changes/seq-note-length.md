## Fixed

- SEQUENCER: a note let go under the sustain (REC, or a MIDI pedal on CC 64) was recorded as long as the key was
  held and played back short. It is now recorded as long as it rang: until the sustain lets it go, or until the
  same note is struck again (issue #34). The sustain itself is still not recorded. With the arpeggiator on, its
  keys are recorded as held, as before.
- SEQUENCER: a note held longer than the whole loop was recorded as the part past one loop (held 2.6 s on a 2 s
  loop: 0.6 s). It now plays the whole loop.

## Tested

- Host: `tests/py/test_seq_length.py` records notes of known lengths over MIDI (short, long, overlapping, across
  the loop's end, longer than the loop, QUANT OFF and 1/16 off the grid, CC 64 and REC held, a re-strike under the
  pedal, chord mode with STRUM) and checks every played-back length on MIDI out within 12 ms; it failed 3 of 7
  before the fix. `tests/scripts/seq_sustain.txt` hears the same on the sound (4 expects failed before).
