## Tools
- `tools/build.py --c1` builds a developer two-core image (`frodhes-c1.fwsc`, off by default, not the release
  build): 14 voices on the first core, the whole effects chain and the full-rate REVERB TYPEs on the second, one
  block (5.8 ms) later. The default image is byte-identical with or without it.
- `tools/hil_c1_soak.py` soaks a two-core image on the FM-1 (rows 274: 30 min, 275: 2 h) and judges the second
  core's counters (CC 116 = 30 / 31); `tools/hil_cpu.py` and `tools/hil_crackle.py` play up to 16 voices
  (`--voices`).

## Tested
- Two cores on the host: the two hand-off orders, refused jobs and the one-core fallback render as designed
  (`tests/py/test_c1_pipeline.py`); the default build is unchanged (Gate R, the firmware image's sha256). Not yet on
  the FM-1.
