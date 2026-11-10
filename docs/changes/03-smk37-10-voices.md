<!-- VERIFY: if the two-core build ships for the SMK-37 too, rewrite this item from 04-two-cores.md.ifships; if an
SMK-37 unit is tested, replace the last Tested line with its CPU worst case and crackle result. -->
## Changes

- **The SMK-37 Pro, SMK-37 Elite and MKE-P37 play 10 voices** (were 7), the same as the FM-1, on every instrument:
  the same chip and the same audio budget. One note more takes over the quietest released note first, with a short
  fade, as on the FM-1.

## Tested

- Host: `make test-smk` (every script on the SMK build at 10 voices, `tests/scripts10` for the voice-steal
  scripts), `test_smk_host.py`; the three SMK packages build (`./build.sh --board smk37`).
- Not tested on an SMK-37 unit: the same chip and clock as the FM-1, whose worst case at 10 voices is measured in
  the FM-1 release gates.
