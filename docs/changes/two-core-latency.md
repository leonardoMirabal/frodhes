## Changes
- The two-core package (`frodhes-c1.fwsc`) answers a note sooner: it plays each 5.8 ms audio block in two halves of
  2.9 ms, and a note starts at the next half instead of the next block. MIDI (USB) to the USB audio: median 13.0 ->
  10.5 ms, p90 15.6 -> 11.5 ms on the same FM-1. The sound is the same; only when a note starts moves. A note that
  starts in a block's second half now reaches the output without the silence gate's 0.7 ms fade-in.
- On VIBES and MARIMBA in the two-core package, more notes than voices at the same instant start 5 at a time, 2.9 ms
  apart (they were 14 at a time, 5.8 ms apart).
- The FM-1's keys and MIDI notes are read while the screen is being drawn too (every package).

## Tools
- `tools/hil_latency.py`: MIDI (USB) to USB audio latency on the FM-1 (median, p10, p90).
- `make test-c1-lat`: every script on the two-core package's host build, in step with the 256-frame blocks and in
  real time.
- `tools/hil_c1_soak.py`, `tools/hil_crackle.py`: `FRODHES_LAT_HALF=128` scales their limits to the 128-frame
  interrupts (as `tools/hil_cpu.py`).
