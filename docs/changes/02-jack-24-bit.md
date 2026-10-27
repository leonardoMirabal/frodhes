## Changes
- The headphone jack keeps the full resolution after MASTER: the volume was applied and then cut to 16 bits, which
  left a tiny rounding error with a small offset (about -95 dBFS, under the FM-1's own hiss). USB audio is
  unchanged.

## Tested
- Host: tests/py/test_master_click.py (the MASTER code from platform.c at four knob positions, with USB VOL fixed
  and following: within one 24-bit step of the exact product, no offset), tests/py/test_isr_tail.py.
- FM-1: the jack's idle floor as 0.9.6 (-92.5 dBFS): unchanged where the hiss dominates.
