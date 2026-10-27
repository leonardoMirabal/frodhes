## Fixed
- SMK-37 (GitHub #17): the CHORD button opens the CHORD page (press again: chord mode on / off) and PARA opens the
  PHASER page (again: on / off). They were the other way round. Their lights and the screen's hints follow.

## Tested
- Host: tests/py/test_smk_app.py (the buttons' matrix codes, their LEDs and the hints), make test-smk; the FM-1
  build is byte for byte the same with and without the change. Not on an SMK-37 (none here).
