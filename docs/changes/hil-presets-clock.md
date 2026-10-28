## Tools

- `tools/hil_scan.py presets` sends a 120 BPM MIDI clock during its takes (Start then Stop first) and starts each take on bar 1 of a 2-bar cycle. The tempo-synced presets (F11 RESO SWEEP's 2BAR LFO, the synced echoes) read at the FM-1's saved BPM before, so F11 read +2.1 LU re F01 on one day and -1.7 on another; now -0.4 on every run. F11's LEVEL stays.

## Tested

- `tests/py/test_hil_scan_clock.py` (3 new); `hil_scan.py --host --only presets` ok (F11 +0.0); on the FM-1 (candidate 5b3bd74a) `--only presets` 4 runs, F11 -0.4, take spread <= 0.21 LU (3 ok, 1 FAIL with no preset off target, not caught); T2 on cd3a9d60 with it: presets ok.
