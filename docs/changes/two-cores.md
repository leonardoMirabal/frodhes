## Tools
- `tools/build.py --c1` builds a developer two-core image (`frodhes-c1.fwsc`, off by default, not the release
  build): 14 voices on the first core, the whole effects chain and the full-rate REVERB TYPEs on the second, one
  block (5.8 ms) later. The default image is byte-identical with or without it.
- `tools/hil_c1_soak.py` soaks a two-core image on the FM-1 (rows 274: 30 min, 275: 2 h) and judges the second
  core's counters (CC 116 = 30 / 31); `tools/hil_cpu.py` and `tools/hil_crackle.py` play up to 16 voices
  (`--voices`).

## Tested
- Two cores on the host: the two hand-off orders, refused jobs and the one-core fallback render as designed
  (`tests/py/test_c1_pipeline.py`); the default build is unchanged (Gate R, the firmware image's sha256).
- Two cores on an FM-1 (a second unit, the image under its own USB name, `tools/build.py --usb-name`): core 1 live,
  the rails at 7 / 15; `hil_cpu.py --worst --voices 14` with the drive TSH8 and SPREAD, 3 runs per engine: the worst
  block 3104-3412 us, the whole ISR 3340-3652 us, 0 voices shed, the USB counters clean; the soaks 274 (30 min) and
  275 (2 h, every engine and REVERB TYPE) PASS: 0 bad reads, refusals, late jobs or holds in 2.16 M jobs, no fault
  record. Not run: the crackle test and the boots (to be run on the next two-core build).
