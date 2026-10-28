## Fixed

- A held note's last seconds through the FDRV drive (F05 SUIT TREM and every preset with the drive on) were a staircase. The drive lost 2 bits before its gain, so under about -60 dBFS the note moved in steps of about 3 LSB: a coarse buzz that the Move's clean output let through. The drive now keeps those bits. The FM-1 had the same fault under its jack's hiss, and gets the same fix.
- The end of every tail, on the FM-1 and the Move: below about -60 dBFS (32 LSB) the last part of a note is mostly the grain of the integer arithmetic and the reverb, so once a tail has stayed under that level for 0.4 s it now fades out smoothly over about half a second to silence. Before, it went on to -66 dBFS and stopped with a 46 ms cut, and its very end held at -1 LSB. Anything louder is unchanged, and a note played during the fade comes in at once with its full attack.
- The Move: the output trim is rounded, so it treats positive and negative samples the same.

## Tested

- Host: `tests/py/test_drive_tail.py` (FDRV alone, C4 v100 with the pedal, on the FM-1 and the Move HQ builds: in the tail, steps of 3 LSB or more fell from 14-15 % to under 1 %; the tail now ends 0.55-1.0 s after its last 32 LSB frame on both builds, 1.4 s / 1.7 s before; every test fails without its fix; 152 renders (F01, F05, F13, F15, three keys, v10-v127, every reverb) are sample for sample the same up to the last 32 LSB frame, and a note struck during the fade has the same attack), `make test`, the drive pytests, Gate R / H / M re-saved (the moved renders are listed in the commits), the FM-1 build (581476 B of 581564).
