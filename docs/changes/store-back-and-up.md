## Fixed
- An update from 0.9.5 takes in what 0.9.5 saved (settings, user presets, the pattern), also after going back to
  0.9.5 and up again: whatever 0.9.5 saved since the last time is taken in again, and only that.

## Tested
- Host: tests/py/test_store_migration.py (up from 0.9.5, back to 0.9.5 with a save there, up again: the setup and
  the pattern 0.9.5 saved come back; what 0.9.5 did not change stays), tests/py/test_hil_store_migration.py.
