## Fixed

- USB audio: while the computer recorded, a settings or preset save (or another short pause of the interrupts) could
  leave one 1 ms packet empty, a short gap in the recording. The FM-1 now queues each packet right after the computer
  takes the last one and catches up at once after a pause, so a pause of up to ~0.9 ms costs nothing (before:
  ~0.5 ms). A save while recording starts each short flash step right after a packet has gone out. 44.1 and 48 kHz.

## Tools

- `tools/rc_gate.sh`: the release candidate's device gates in one queue, each step's log and exit code kept.
- `tests/py/uac_timing.py`: a timing model of the USB packet hand-off, to show the margin a change leaves.

## Tested

- Host: `tests/py/test_uac_timing.py` (margin ~520 -> ~920 us), `test_uac_phase.py`, `test_uac_seq.py`,
  `test_flash_split.py`, `test_rc_gate.py`.
- FM-1 over USB at 44.1 and 48 kHz: with a 600 us pause of the interrupts injected, every packet is sent (before:
  243-268 empty packets in 120 s); settings saves while recording and clean-piano takes show no empty packet and no
  glitch; the audio interrupt's worst case stays under 4500 us on every engine.
