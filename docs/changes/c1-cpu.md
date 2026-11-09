## Fixed
- Two cores: a program change while many notes start (a 26-note burst) no longer takes the audio to its time limit.
  VIBES and MARIMBA now work out a new note's strike when the note arrives, outside the audio interrupt, and the
  wah, the delay and the HQ hall run in fewer steps; the sound is unchanged, sample for sample. The worst block on an
  FM-1 is now 4004-4228 us of the 4500 us limit on every instrument.

## Tested
- Host: every script with and without the new note-on cache, and on both images: identical output (984 WAVs);
  `tests/py/test_bar_cc.py` (a RING, DAMPER or TYPE change between a note's arrival and its strike); Gate R.
- FM-1 (two-core image): `tools/hil_cpu.py --worst` on every engine, 4004-4228 us, 0 voices shed.
