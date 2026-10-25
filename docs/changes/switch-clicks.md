## Changes
- COMPRESSOR: turning THRESH up from OFF starts it at −16 dB (the presets' setting) with the +2.8 dB make-up, so it
  comes on at about the same volume (it started at −0.3 dB with the make-up: 2.8 dB louder); CC 106 does the same
  when THRESHOLD is 0 (it came on without the make-up), and keeps a THRESHOLD already sent with CC 107.

## Fixed
- Switching effects under a held chord no longer clicks: PHASER and CHORUS on / off, the WAH on / off, the
  COMPRESSOR on / off, every FILTER TYPE change and OFF, DRIVE AMOUNT to 0 and back: each fades over about 46 ms
  with the effect still running (as the drive's and the delay's switches always did).
- LEVEL glides instead of stepping at each detent.

## Tested
- Host: tests/py/test_switch_clicks.py, a differential click check (the largest second difference around the change
  against the same render without it, under 10x; the old ones were 14-307x), and the compressor's wake level.
