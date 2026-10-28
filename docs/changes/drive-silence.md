## Fixed

- The drive (KHLON and the classic drive): after a panic or when every note had died away, the drive could hold a small constant offset (about -58 dBFS) instead of returning to silence, and the tremolo turned it into a faint 2-6 Hz pulse. Integer rounding left the drive's filters at a resting value that was not zero. Now the drive rests at exact zero once its input is silent.
- KHLON's reciprocal table was read through a pointer placed before the table's start (formally undefined in C, though every read was inside the table). It is now indexed from the table's start; the FM-1 and SMK-37 files are unchanged by this.

## Tested

- Host: `tests/scripts/drive_silence.txt` (both drives: a panic, then exact silence; it fails on 0.9.7), Gate R (12 renders with the effects' tails after silence changed, each by the removed offset), `make test`, the crackle and monkey tests (the injected-copy count re-measured on 300 takes).
- The FM-1, with this change built as a release (`frodhes-0.9.8.fwsc` from c52cff36, sha256 5b3bd74a...): `tools/cpu_check.sh` 4/4 PASS (the whole audio interrupt's maximum 4060 us, limit 4200); a KHLON and a classic-drive phrase each played to a panic: peaks 7902 and 23050 while playing, then from 30.05 to 32.05 s USB exact 0 and the jack at the muted floor (-90.8 and -93.0 dBFS); `tools/hil_crackle.py --engine all` at 48 and 44.1 kHz: PASS.
