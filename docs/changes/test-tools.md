## Tools
- `tools/control_sweep.py`: every control (115 of them) swept and moved under a ringing chord, judged for clicks
  (against the same render without the move), zipper, level jumps, pitch and stuck states;
  `tests/py/test_manual_claims.py` checks the manual's promises of glides and steady levels the same way.
- `tools/monkey.py`: seeded random play on the host (notes, the pedal, knobs, buttons, presets, MIDI), with checks
  for lost notes, clicks, overflows (sanitizer builds), silence after all notes off, never louder after the key-up,
  and settings that survive a power cycle; failures are shrunk to a few events. `--focus-xfade` aims at the
  controls with a crossfade, `--shed N` adds the overload's voice shedding. `tools/hil_monkey.py` plays the same
  streams on the FM-1 and judges the USB recording with the glitch detector.
- `tools/coverage.sh` (with `coverage_sum.py`): line and condition coverage of the host tests.
- `tools/hil_usb_bench.py`: a bench build (`APP_DEFS=-DFELUCCA_UAC_TONE=1` or `2`, never in a normal build) that
  sends a known wave through the USB audio path, to tell a lost or repeated USB frame from the music.
- `tools/hil_crackle.py`: a repeated-frame event is counted only where the wave around it is predictable and well
  over the music's own quiet plateaus (fewer false alarms on noisy music; real copies are still found).
