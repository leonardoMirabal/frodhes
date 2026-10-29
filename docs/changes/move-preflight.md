## Changes

- Move device tests: a preflight (`tools/move_hil.py preflight`) now runs before every test that gives a sound or
  level verdict (`usb-host`, `usb-note`, `usb-vel`, `usb-sus`, `usb-clock`, `usb-stress`, `d2` on either path,
  `usb-levels`). It checks three things. Main Out must be silent with no note playing, and the microphone is named
  when it is the USB-C source. One note through the Move's MIDI path must reach Main Out as Frodhes' own output,
  with nothing else mixed in. And it shows what the slot and the Move's track hold. A failure stops the test.
- The tests send on the slot's receive channel, read from Schwung, and stop when it cannot be read or does not
  match `--ch` or Frodhes' CHAN.
- The state restore waits for Schwung's re-read before it closes its connection. Without that wait, the next
  connection was cut ("no close frame") and the `state` row failed.

## Tested

- Host: `tests/py/test_move_preflight.py` (a pass, an idle floor at -54.6 dBFS, the microphone, a sound with every
  note that is not in the tap, the channel lookup, the CLI), `tests/py/test_move_hil.py`, `make test-move`.
- On the Move (USB-C audio out = Main Out, the user's sampler cleared): the preflight passed. Idle was exact zero.
  Main Out against the tap: correlation 1.00000, residual -70.4 dB against the note, -100.3 dBFS, gain +2.46 dB
  (the trim and a slot volume of 1.03). With the USB-C source left on the microphone it stopped and said so. Also
  passed: `usb-note` over USB-C, and `d2 --path tap --tests usb-note` on the Rhodes, both with the state restored.
