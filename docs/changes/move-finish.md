## Fixed

- The Move's `usb-host` test: a note whose tail is a steady tone lined up a whole period late (96 v90 of
  2026-10-30: 953 frames, not 658), and the misfit read as ANOTHER SOUND (-29.7 dB extra) where the Move plays
  Frodhes alone. The tie between such lags is now broken on the attack. Its HQ probe gives CLASSIC only where the
  take can tell HQ from Classic (a USB-C take through the Move's volume cannot; it passes and says so).
- `usb-host` with Schwung's scene locks: a lock puts its parameter back after a program change, so F13's BARK 52
  played as the held 64 and VIBES read DIFFERS. The test now reads the held values and gives them to the host
  build too, and names them. The preflight lists the slot's locks.
- `tools/move_ws.py`: the WebSocket open tried the Move's IPv6 link-local address first and could hang there for
  the whole timeout (the VIBES `usb-host` timeout of 2026-10-28). It now tries IPv4 first, then the address that answered last,
  and opens up to 3 times.
- `tools/move_multi_cpu.py` makes Schwung's `debug_log_on` for its run and removes it after. The flag had been left
  on since 2026-10-28, and Schwung's `debug.log`, which nothing trims, had grown to 43 MB. It is off again.
- `tools/move_ui_drive.py`: a `shot` taken before `arm` is the display mirror's last frame, not the screen; it is
  now marked STALE.

## Tested

- Host: `tests/py/test_move_hil.py` (the 2026-10-30 take's 96 v90 excerpt aligns at 658 and passes; a tie broken on
  the attack; the HQ probe resolved or not; the scene locks parsed and turned into CCs; a take with a held BARK
  fails without the held values and passes with them, also when the saved run is analysed again),
  `tests/py/test_move_ws.py` (a hanging IPv6 address skipped within 1 s; the open tried again),
  `tests/py/test_move_multi_cpu.py`, `tests/py/test_move_ui_drive.py`, `move/tests/test_bridge.c` (SPREAD in the
  state, and with a user preset: U05 saved with SPREAD 77, F01 then U05 brings it back), the Move pytests,
  `tools/ci_move.sh`.
- The Move (Schwung 1.8.2), the preflight first each time (PASS, residual -57 dB):
  - The second sound at 96 v90: none on the Move. Song.abl's tracks hold empty samplers (tracks 1-3) and an
    electric piano on track 4 (channel 4), no clips; slots 2-4 empty, no FX on slot 1. Two takes were bit for bit
    the same; the analysis again with the fix passes.
  - VIBES (F13) `usb-host`: no timeout; over the tap the host's sound exactly (residual under -227 dB) with the held
    BARK and TONE; over USB-C PASS, gain -17.11 dB (the user's track volume).
  - The delay: the DELAY page (CHORUS/DELAY, 6/10) is there on F01, where the delay is off by design (MIX 0). With
    FX on and the delay switched on (DIG, MIX 64, TIME 85, FEEDBK 90) a short note's echoes come every ~360 ms,
    2.3 dB down each, the same on the tap and on Main Out (the tail 0.9-3.3 s: -15 dB against -85 dB without).
    Every preset with the delay on (F06-F09, F11, F14, F16-F19, U01, U02, U04, U05, U07, U08) against the same
    preset with the delay off: louder tails 0.9-3.3 s after the note (by 4.7 dB, U04, to 51 dB, F11; F19's tail
    without the delay is digital silence) and echoes on the tempo (F16: 630, 1190, 1740 ms).
  - Frodhes in slots 1 and 2 with the Move's own track 4: each alone and all at once on Main Out, no gap; Schwung's
    autosave wrote both slots; changed, then loaded again from the saved files (Schwung's `load_file`, the set
    restore), both came back (new instances); SUS comes back off (not in the state, as documented). Slot 2
    emptied: the set's slot_1.json is `{}` again.
  - SPREAD: 0 is mono (L = R exactly with the reverb off; F01's stereo reverb alone gives a side 24 dB down); 64
    and 127 pan the keys from left to right (C2 -13.5 dB, C7 +15.9 dB L over R at 127), the same on Main Out;
    saved with the slot (SPREAD 100 in the saved slot_1.json and back after the reload).
- Not tested: a set change on the Move's own screen (the set restore was run through Schwung's `load_file`, the
  call a set change makes); a reboot; the track's mute and volume on the slot.
