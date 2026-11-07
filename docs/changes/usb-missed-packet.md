<!-- review draft (usb-review): the Fixed line follows the usb-fix branch's final change; matches the usb-fix diff
reviewed 2026-11-06 (FELUCCA_UAC_PHASE, FELUCCA_UAC_ALIGN: 79847d82). Measured basis: both 0.9.9 candidate failures had a late audio ISR entry (122 / 144 us)
and the USB ring's high point one packet up; tests/py/test_uac_timing.py has the margins. -->
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
