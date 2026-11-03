## Fixed

- DELAY ANA / TAPE: a loud click when the 4-point read overshot full scale (the echo gain's product wrapped 32 bits).
  The product is 64-bit now; the output is unchanged wherever nothing overflowed.

## Tested

- Host: the monkey's sanitizer build on the seed that found it; Gate R.
