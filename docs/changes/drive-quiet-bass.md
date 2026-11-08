## Changes

- DRIVE TSH8 and OHCD on quiet bass notes: both kept one internal signal in whole units, and on a soft low note its
  steps were a faint buzz of odd harmonics. TSH8's clipper output now carries 4 more bits into the tone stage (the
  same instructions on its longest paths); OHCD's first stage runs 16 times finer inside (2 instructions a sample,
  its budget 160 us a block, under the drive's 177). A 41 Hz sine through either at DRIVE 20: the 3rd harmonic
  re the fundamental falls from -44 / -50 / -59 dB to -67 / -79 / -90 dB (TSH8, inputs 160 / 320 / 640) and from
  -38 / -54 / -57 dB to -54 / -71 / -79 dB (OHCD, inputs 40 / 160 / 320). Loud playing is unchanged. On F05 with
  TSH8, key 28 at velocity 20: the 3rd harmonic is now -60 dB re H1 (0.9.9: -46). OHCD and BHLUE there sit on the
  output's own floor (harmonics near -115 dBFS).

## Tested

- Host: tests/py/test_drive_quiet.py (fails on the kernels before, passes now), the drive kernel tests (the
  reference loops in tests/c/drv_kern_ref.c and tools/drive_gen/ohcd.py updated to the same integers), the drive
  types / tail / core / OS tests, `make test`, `make pristine` (no change: the same 12 known effects-on v40 cells);
  instruction counts with tools/drive_insns.py (TSH8's longest paths unchanged, OHCD's stage 1 42 a two-sample turn,
  was 38).
