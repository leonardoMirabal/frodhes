## Fixed

- The drive (KHLON and the classic drive): after a panic or when every note had died away, the drive could hold a small constant offset (about -58 dBFS) instead of returning to silence, and the tremolo turned it into a faint 2-6 Hz pulse. Integer rounding left the drive's filters at a resting value that was not zero. Now the drive rests at exact zero once its input is silent.
- KHLON's reciprocal table was read through a pointer placed before the table's start (formally undefined in C, though every read was inside the table). It is now indexed from the table's start; the FM-1 and SMK-37 files are unchanged by this.

## Tested

- Host: `tests/scripts/drive_silence.txt` (both drives: a panic, then exact silence; it fails on 0.9.7), Gate R (12 renders with the effects' tails after silence changed, each by the removed offset), `make test`, the crackle and monkey tests (the injected-copy count re-measured on 300 takes).
- The FM-1: CPU, the crackle test and a KHLON / classic-drive panic to exact zero on the jack and USB (pending).
