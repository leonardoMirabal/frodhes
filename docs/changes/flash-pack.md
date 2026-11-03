## Changes

- The firmware image is 79 KB smaller with the same sound: the pulse banks of the Rhodes and the bars are stored packed
  (third differences, int16 head and int8 tail; `tools/gen_pulse_pz.py` round-trips every pulse) and decoded at the
  strike, and the main-loop code is compiled for size. The renders are bit-identical.

## Tools

- `tools/gen_pulse_pz.py` writes `firmware/gen/pulse_pz.h`; `tests/py/test_gen_pulse_pz.py` checks the header is current
  and that the decoder gives every pulse back exactly.
- `tools/isr_diff.py` follows jump tables inside a function.

## Tested

- Gate R and Gate H byte-identical before and after; `make test`, `make pytest`.
