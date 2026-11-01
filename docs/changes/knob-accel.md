## Changes

- The knobs K1-K4 accelerate (issue #25): turned fast, an amount moves up to 8 steps per detent, so one quick turn goes from 0 to 100 %. Under 40 ms per detent it moves 2 steps, under 20 ms 4 steps, under 10 ms 8 steps. A slow turn still moves one step per detent, for fine settings. Choices, note values and short ranges (under 32 steps) always move one step. BPM and TUNE accelerate too. The Move is unchanged (it sets values directly).

## Tested

- Host: `tests/py/test_knob_accel.py` (with FRODHES_KNOB_ACCEL: the host build keeps it off for the exact-detent scripts; slow turns at 50-300 ms per detent move exactly one step each; fast turns at 30 / 15 / 8 ms move more, and 20 detents at 8 ms cover 0-127 up and back down; a choice moves one option per detent), `make test`, `make pytest`.
- FM-1: the feel of the physical knobs can't be driven over MIDI. The user checks it by hand.
