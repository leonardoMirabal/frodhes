## Changes

- Move device tests: `usb-clock` now always plays over the plugin's own player (the tap), also in `d2` over USB-C.
  Schwung passes only channel messages from USB-C to a slot. The clock bytes a slot gets come from the Move's own
  transport. So the clock that the test sent over USB-C never reached Frodhes. The tremolo then ran free at the
  Move's tempo, and the test failed with a jitter of 82 ms. Frodhes' sound and code are unchanged. A rig whose
  clock cannot reach the slot is now refused before any take. The fake Move drops the clock over USB-C, as Schwung
  does.
- `clock-probe` reads the plugin's counters from its instance's folder (`inst/<k>/stats.log`). Since the
  multi-instance loader it had read the module folder and found nothing. `--auto` presses the Move's Play and
  Stop itself, through Schwung's test bus.
- `docs/MOVE.md`: MIDI clock sent over USB-C does not reach Frodhes. To play in time with a computer or another
  device, sync the Move to it. The Move's own transport, which Frodhes follows, is now tested on a device.

## Tested

- Host: `tests/py/test_move_hil.py` (the three new tests fail on the old tool: the clock over USB-C is refused,
  `d2` and the command run it over the tap, the counters' path, `--auto`), `tests/py/test_move_preflight.py`,
  `tests/py/test_move_doc.py`. A host render of the same take passes with the same numbers as the Move's tap
  (jitter 3.66 ms).
- On the Move (main's `dsp.so` and `engine.so`, unchanged): the old `usb-clock` over USB-C failed (jitter 82 ms),
  and the plugin counted 0 clock pulses. Over the tap it passed. The new `usb-clock` over USB-C passed through the
  tap (jitter 3.66 ms). `d2 --path tap --engines rhodes` passed, 12 of 12 (usb-host sample-exact at TUNE 0).
  `d2 --engines rhodes` over USB-C passed 11 of 12, usb-clock included. Its usb-host found another sound at
  key 96 v90 in the Move's mix (extra -29.7 dB); the tap's usb-host is sample-exact, so this is not Frodhes.
  `clock-probe --auto` passed: the Move's F8 arrived at 32.8 a second at 82 BPM, with one FA and one FC and no
  pulses made by the bridge. Two Frodhes, in slots 1 and 2, both got every pulse.
