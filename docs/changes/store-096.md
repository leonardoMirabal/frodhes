## Changes
- Where Frodhes keeps its data: only in flash that no region of the stock firmware's own table uses. The FM-1: the
  settings and the 16 user presets are one record in two copies (0xFC000, 0xFD000), the pattern at 0xFE000. The
  SMK-37: 0xEB000-0xF3FFF (the record, the pattern and the update staging), nothing of the stock settings area
  0x9D000-0xBFFFF any more (no owner-tag clear). Going back to stock now finds the stock settings as they were.
- The first start after the update copies 0.9.5's settings, user presets (every slot, every older preset format) and
  pattern once to the new places, the pattern first and the record last; a power cut before the end repeats the copy
  at the next start. 0.9.5's sectors are read, never written.
- A user preset SAVE writes the record (settings and presets) to the copy not in use, then reads it back: a power cut
  during a SAVE keeps every preset as it was before it.
- Going back to 0.9.5: it reads its presets and pattern from its old places, as they were when 0.9.6 was installed
  (presets saved under 0.9.6 are not there). Its settings: as they were, or its defaults once 0.9.6 has saved twice
  (0.9.5 does not read 0.9.6's record). 0.9.6's record stays, unless 0.9.5 writes over both copies.

## Tested
- Host: tests/set_ab_test.c (FM-1 and SMK-37: a power cut at every call of a save and of the migration's commit,
  three ways, from every start; the copies alternating; a save plus a power cycle; the flash model fails the test on
  any write outside the store), tests/smk_flash_test.c, tests/py/test_store_migration.py (a full 0.9.5 bank in v3,
  v2 and v1 format, every field compared; no old data; a corrupt bank; a migration cut two ways; the SMK-37 only
  under its owner tag), tests/py/test_persistence.py, tests/py/test_smk_host.py, make test, make test-smk.
- Not tested on hardware:
  - The FM-1 from V15: the update staging stays at 0xE0000 (the end of V15's settings area, which its factory settings
    leave erased; 0.9.5 and Felucca staged there too). Whether V15's own updater stages exactly there is not proven.
    From V16 (no V15 settings marker at 0x93000) it stages at 0xA9000-0xADFFF, a gap in V16's table.
  - The SMK-37: the staging moved to 0xEF000-0xF3FFF. Where the chip's boot code looks for the update record after a
    power cut during an install is not known for the SMK-37 (docs/SMK37.md).
  - MKE-P37: every install erases 0x9C000, the first sector of its stock settings area (docs/SMK37.md).
