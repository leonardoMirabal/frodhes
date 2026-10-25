# The physical model

Design decisions for the Rhodes model go here. The research notes (`research/`, R01-R09) and the reviewers'
scratch experiments (`reviews/scratch-*`) that the plans below cite are kept privately since 0.9.5 and are not
part of the public tree; the papers themselves are cited in the source files that use them.

The model lives in [`firmware/src/rhodes.c`](../../firmware/src/rhodes.c), behind the interface described at the
top of that file. It has to follow [the chip's rules](../RULES.md):

- integer and fixed-point arithmetic only: no `float`, no 64-bit division;
- `rh_render()` gets 256 frames at 44.1 kHz and must finish in well under 5.8 ms, ideally under half;
- compute coefficient tables on the host and paste them into the source as integer constants.
