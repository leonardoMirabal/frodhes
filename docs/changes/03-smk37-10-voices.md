<!-- DRAFT: finalise the [TODO ...] from the SMK-37 branch (FR_VOICES 7u -> 10). If the two-core build ships and
the SMK gets 14 voices too, rewrite this item from 04-two-cores.md.ifships instead. -->
## Changes

- **The SMK-37 Pro, SMK-37 Elite and MKE-P37 play 10 voices** (were 7), the same as the FM-1, on every instrument.
  One note more takes over the quietest released note first, with a short fade, as on the FM-1.

## Tested

- Host: `make test-smk` at 10 voices (tests/scripts7/ retired or moved to 10: [TODO]); the SMK build's CPU model at
  the worst case (10 voices, every effect, the pedal) [TODO] us.
- [TODO: device: "an SMK-37 unit: CPU worst [TODO] us, crackle test" or "not tested on an SMK-37 unit: the same chip
  and clock as the FM-1, whose worst case at 10 voices is [TODO] us".]
