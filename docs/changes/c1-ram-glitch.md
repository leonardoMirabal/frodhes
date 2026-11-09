## Fixes
- Two cores: the first core's sound code runs from flash, not RAM. With both cores busy, code read from RAM can come
  back with a flipped bit on this chip (the stored code is intact), and the first core stopped on an instruction that
  cannot fail (the reverb TYPE switch soak reset the FM-1 within minutes). `tools/build.py --c1` fails a build that
  puts the first core's code in RAM again.

## Tested
- Host: `tests/py/test_c1_ram_text.py` (the build check passes the flash driver and the second core's idle loop, fails
  the old RAM set); the host output is unchanged (only the code's place moves).
- Not yet tested on the FM-1: the soak row 277 and the CPU numbers with the code in flash.
