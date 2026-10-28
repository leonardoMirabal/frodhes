## Fixed

- A held note's last seconds through the FDRV drive (F05 SUIT TREM and every preset with the drive on) were a staircase. The drive lost 2 bits before its gain, so under about -60 dBFS the note moved in steps of about 3 LSB: a coarse buzz that the Move's clean output let through. The drive now keeps those bits. The FM-1 had the same fault under its jack's hiss, and gets the same fix.
- The Move: a tail now fades out over about half a second instead of stopping with a 46 ms cut, and its very end no longer holds at -1 LSB. The output trim is rounded, so it treats positive and negative samples the same. The FM-1's silence gate is unchanged.

## Tested

- Host: `tests/py/test_drive_tail.py` (FDRV alone, C4 v100 with the pedal, on the FM-1 and the Move HQ builds: in the tail, steps of 3 LSB or more fell from 14-15 % to under 1 %; the Move's fade from the last 16 LSB frame to silence went from 262 ms to over 300 ms; both tests fail without the fix), `make test`, the drive pytests, Gate R / H / M re-saved (the moved renders are listed in the commits), the FM-1 build (581476 B of 581564).
