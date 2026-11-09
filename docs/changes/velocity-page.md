## Changes

- RESPONSE (CC 40) has two settings: **FULL** (the default), the full dynamic range, and **LIGHT**, a light touch
  with soft notes louder. FULL goes from velocity 20 to 127 over about 34.5 dB (39.4 dB from velocity 1), in steps
  of at most 0.65 dB a velocity; LIGHT keeps 0.9.9's 25.5 dB. Both play at the same level from medium velocities up,
  with the same tone at every key and velocity. VIBES and MARIMBA ignore it.
- The output's silence gate opens and closes 6 dB lower (16 LSB, -66 dBFS, was 32), so FULL's softest notes and
  long tails are not cut sooner than in 0.9.9 (notes faded by the gate within 1 s, keys 21-96: velocity 1 10, 10 8,
  20 5, 30 4, each at most 0.9.9's).
- The pedal's thud follows the note's level as heard (with RESPONSE's soft end): never louder than the note.
- The velocity page: every TYPE's last VOICE page (VOICE 3/3 on the electric piano types and MARIMBA, VOICE 4/4 on
  VIBES) holds RESPONSE, TOUCH and KEY VEL. They were FRODHES K4 (KEY VEL), SETUP K4 (TOUCH) and MIDI K4 (RESPONSE);
  those cards are empty now. The CCs are unchanged.
- Two layers, kept apart: TOUCH and KEY VEL set the FM-1's own keys' velocity only (a MIDI note's velocity reaches
  the engine as sent); RESPONSE changes the sound only, never the velocity (the USB-MIDI out, the sequencer's
  recording and the arpeggiator keep the velocity played).
- Move: RESP has its own page, VELOCITY, right after the VOICE pages (it was on LFO/SETUP), on Frodhes' screen and on
  Schwung's page list alike; standard and HQ (one module).
- SMK-37: TOUCH and KEY VEL on the velocity page too (K6, K7).
- VERSION: the MIDI page's K4 shows the version installed (a release's number; a development build DEV and the
  first digits of its commit), read only. The Move shows it on its last page (MAPPINGS, VER).

## Tested

- Host: `tests/py/test_vel_layers.py` (MIDI v1..127 reach the engine unchanged at every TOUCH and KEY VEL 1 / 64 /
  127, on USB and TRS; the keys' velocity is the one sent out and struck under FULL and LIGHT; the sequencer and the
  arpeggiator keep the velocity; fails when the MIDI path clips the velocity), `test_vel_resp_curves.py` (ranges,
  steps, gate counts vs 0.9.9), `test_release_never_louder.py`, `test_every_control.py` (a new RESPONSE case),
  the UI scripts and tests moved to the new page (`touch.txt`, `vel_resp.txt`, `test_touch.py`, `test_vel_resp.py`,
  `test_ui_meters.py`, `test_smk_app.py`, the Move's `test_screen.mjs` / `test_chain.mjs` / `test_move_*`),
  `tests/scripts/version.txt` (VERSION reads DEV on the host, a turn changes nothing), `make test`, `make pytest`.
- FM-1, Move, plugin: not tested on the devices in this change.
