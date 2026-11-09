## Changes

- SMK-37 (Pro, Elite, MKE-P37): 10 voices on every instrument, as the FM-1 (it was 7): the same chip and the same
  audio budget.

## Tested

- Host: make test-smk (every script on the SMK build at 10 voices, tests/scripts10 for the steal scripts),
  test_smk_host.py; the SMK package builds (./build.sh --board smk37). Not tested on an SMK-37.
