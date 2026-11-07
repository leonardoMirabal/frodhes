## Fixed

- USB audio: while the computer recorded, a settings or preset save (or another short pause of the interrupts) could
  leave one 1 ms packet empty, a short gap in the recording. The FM-1 now queues each packet right after the computer
  takes the last one (its poll follows the computer's timing) and catches up at once after a pause, so a pause of up
  to ~0.9 ms costs nothing (before: ~0.5 ms). A save while the computer records now starts each of its short flash
  steps right after a packet has gone out, with most of the 1 ms frame left. 44.1 and 48 kHz; saves with no
  recording run as before.

## Tools

- `tools/rc_gate.sh`: the release candidate's device gates in one queue (install, T1, crackle at both rates, CPU,
  monkey, the preset switching scenario 3 more times, T2, the save test), each step's log and exit code kept.
- A timing model of the USB packet hand-off (`tests/py/uac_timing.py`): the IN tokens, the 2 kHz poll, a pause of
  the interrupts at any place; it shows the margin a change leaves.

## Tested

- Host: `tests/py/test_uac_timing.py` (the margins before and after the change: ~520 -> ~920 us),
  `tests/py/test_uac_phase.py`, `tests/py/test_uac_seq.py`, `tests/py/test_flash_split.py` (the slices aligned), `tests/py/test_rc_gate.py`.
- FM-1 over USB at 44.1 and 48 kHz: with a 600 us pause of the interrupts injected, 0.9.9 sends every packet (the code before:
  243-268 empty packets in 120 s); the save test (settings saves while recording) and clean-piano takes with the
  70S PHASER preset and the INFINITE reverb show no empty packet and no digital glitch; the audio interrupt's worst
  case stays under 4500 us on every engine.
