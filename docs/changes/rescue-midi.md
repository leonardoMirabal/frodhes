## Fixes
- USB RESCUE no longer stops answering when MIDI arrives: it drops incoming notes and controls instead of letting
  them fill the input, which held the installer's messages back.

## Tested
- Host: `tests/py/test_recovery_drain.py`; the one-core and two-core firmware build with every check ok. Not yet
  tested on the FM-1.
