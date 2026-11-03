## New

- **The reverb at the full sample rate on the Move** (`FR_REV_HQ`, on in the Move's build only: Makefile
  `MOVE_DEFS`). Every REVERB TYPE runs at 44.1 kHz, so the tails keep their band up to about 20 kHz (the half-rate
  TYPEs stop near 11 kHz), with no down / up sampling filter in the path.
  - HALL: a feedback delay network of 8 lines of 32 to 91 ms behind 4 allpass diffusers (`firmware/src/rev_hq_hall.c`).
  - PLATE: the plate tank at 44.1 kHz (`rev_hq_plate.c`). INFINITE: the bright tank in full (`rev_hq_inf.c`).
  - The panel, the ranges, the records, SUSTAIN / FREEZE and the exact-silence tails are the same; each TYPE's wet is
    within 1.5 dB of the half-rate one (critical-band loudness).
- The FM-1, the SMK-37, the host and the web keep the half-rate TYPEs, byte for byte as before.

## Tools

- `tools/rev_hq_ab.py`, `tools/rev_hq_ab_render.py`: the full-rate TYPEs against the half-rate ones (measures, A/B
  renders).
- `move/tests/gate_m.sh`, `move/tests/run_eq.sh`, `tools/move_hil.py`, `tools/target_closure.py`: the Move's host
  builds with `-DFR_REV_HQ=1`. `tools/move_levels.py`: every build with the Move's reverb.
- Gate M: `tests/baselines/move16.sha256` re-saved, 66 of 78 MIDI-driven renders moved, every one through the reverb
  only.

## Tested

- Host: `tests/py/test_rev_hq.py` (65: the TYPE framework's tests on the full-rate build, the band above 11 kHz, the
  levels and T60s against the half-rate TYPEs, no overflow at the worst input, CC 10 to rest); Gate R 122 of 122 and
  Gate H 5 of 5 unchanged (the switch off); Gate M 78 of 78 after the re-save; with the reverb's MIX held at 0 the 66
  moved renders are byte-identical with the switch on and off; `tools/ci_move.sh`.
- Host CPU (macOS): +1.4 to +1.6 us a 128-frame block over the half-rate reverb.
- Not yet on a Move: the sound and the CPU time (the device pass).
