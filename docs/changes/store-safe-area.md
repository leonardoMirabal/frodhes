## Fixed
- Settings, user presets and the sequencer pattern are kept in a part of the flash that none of the stock
  firmware's regions use, on the FM-1 and the SMK-37. On the SMK-37, earlier versions cleared and rewrote the stock
  firmware's settings area, and the stock firmware installed again over Frodhes could then stay silent; Frodhes no
  longer writes there. The first start after the update moves your settings, user presets and pattern over once
  (a power cut during that move just runs it again at the next start).

## Tested
- Host: set_ab_test (FM-1 and SMK-37: every cut of a save and of the move, the two copies taking turns, no write in
  a stock region), smk_flash_test, flash_split, tests/py/test_persistence.py, tests/py/test_store_migration.py (a
  full bank of user presets saved by 0.9.5, older formats, none, a corrupt one, a cut move), tests/py/test_smk_host.py.
