<!-- The two-core build ships as a second file, frodhes-c1.fwsc, next to the one-core frodhes.fwsc (README / MANUAL
install). VERIFY: the FM-1 Tested line from the rc4 device tier (both units) before the release commit. -->
## Changes

- **A two-core package for the FM-1, `frodhes-c1.fwsc`**, next to the one-core `frodhes.fwsc`. The FM-1's chip has a
  second processor core: this package plays the voices on the first and the effects and the reverb on the second,
  each with room to spare, with no added latency (a note sounds as soon as on the one-core package).
- **14 voices** with the two-core package (10 with the one-core one), on every instrument, counting the notes under
  the sustain and the ones chord mode, the arpeggiator and the sequencer play.
- **The reverb at the full sample rate** with the two-core package, as on the Move: its tails keep their top octave.
  Every reverb type, the same controls and presets.

## Tools

- `tools/build.py --c1` builds the two-core image; the default build is byte-identical with or without the two-core
  code. `tools/hil_c1_soak.py` soaks a two-core image on the FM-1 and judges the second core's counters;
  `tools/hil_cpu.py` and `tools/hil_crackle.py` play up to 16 voices (`--voices`).

## Tested

- Host: the effects and both voice engines processed in chunks render sample for sample what the one-core 14-voice
  build renders, with no added delay; the hand-off orders, refused jobs and the one-core fallback
  (`tests/py/test_c1_pipeline.py`); the default build unchanged (Gate R, the firmware image's sha256).
- FM-1 (release build): <!-- VERIFY: the K6b device gates: hil_cpu --worst --voices 14 x3 per engine (worst block
  and whole ISR, limit 4500 us, 0 shed), MIDI-in to jack onset equal to the one-core build, the 30 min and 2 h soaks,
  20 restarts, the crackle test at 44.1 and 48 kHz, T1/T2. --> the CPU worst case per core, the latency against the
  one-core build, the soaks with no hang, no crackle and no lost note, the restarts and the scans.
