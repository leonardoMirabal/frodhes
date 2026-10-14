# The physical model

Notes, papers and design decisions for the Rhodes model go here.

The model lives in [`firmware/src/rhodes.c`](../../firmware/src/rhodes.c), behind the interface described at the
top of that file. It has to follow [the chip's rules](../RULES.md):

- integer and fixed-point arithmetic only: no `float`, no 64-bit division;
- `rh_render()` gets 256 frames at 44.1 kHz and must finish in well under 5.8 ms, ideally under half;
- compute coefficient tables on the host and paste them into the source as integer constants.
