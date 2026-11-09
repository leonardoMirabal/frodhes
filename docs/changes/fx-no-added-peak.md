## Changes
- **The output level is 0.9.9's again** (the default LEVEL and the limiter's threshold, LIM_T 24000): the factory
  presets F01-F19 within 0.5 dB of 0.9.9 by loudness (F16, below, excepted), on the FM-1, the Move and the plugin.
- **No effect adds peak level.** Each effect switched on keeps the loudest moments within 2 dB of the dry sound
  (`tools/fx_pristine.py`'s new peak check), so none of them drives the output limiter:
  - PHASER: its level trimmed by DEPTH so a chord's peak stays within 0.5 dB of the phaser off (single notes within
    2 dB); DEPTH 0 matches by loudness, the deep settings are 2-3 dB quieter on average. The sound itself is unchanged
    (a pure gain).
  - PAN at DEPTH 127: neither side goes above unity (it had a +3 dB side peak).
  - Touch wah: the make-up follows the input's and output's peaks and waits for the attack to pass, so it no longer
    overshoots a note's onset (+1.6 .. +1.8 dB at most; at RATE 127 the peak 5.3 dB lower than before).
  - Filter LP12 / LP18: resonance gain compensation as the cutoff falls (a resonant sweep stays within about 1 dB of
    the unfiltered level).
  - ANA delay: on a silent line its time no longer glides from the last setting, which had bunched the first repeats
    together (a chord's peak +3.1 dB before, +1.7 now; the echoes as loud as DIG's).
  - HQ HALL (Move / plugin) at MIX 127: the switch between its quiet and loud paths moved lower, so its non-linear
    residual falls from -73.7 to -79.9 dBFS.
- **The compressor is clean on chords.** It holds its detector across a note's cycle, smooths its reduction's release
  (about 0.1 s) and interpolates its gain: a v127 chord's intermodulation at F01's settings -68 .. -79 dB (was -46 ..
  -56; the dry engine -76 .. -86). A soft note right after a loud chord comes back to full level within about 0.3 s;
  loud sustained chords sit about 0.8 dB lower.
- Factory presets with effects re-levelled for the quieter effects: F05 +7, F09 +14, F10 +2, F11 +21, F18 +4 LEVEL
  steps (the Move's preset records the same); U04 +3, U05 +9, U07 +10.
- F16 stays 4.7 dB under 0.9.9: 0.9.9 reached its level only through the limiter, and matching it now would clip.

## Tested
- Host: `tests/py/test_fx_pristine.py` (OPEN list empty on the one-core, two-core and HQ builds, presets included),
  the new `tests/py/test_comp_clean.py` (fails before, passes after), `make test` (65 scripts), `make test-c1`, the
  full `make pytest`, `tests/py/test_rev_inf.py`, `tools/gen_move_presets.py --check`. Character before / after by
  level-normalised critical-band spectra (the phaser, pan, filter and hall shapes unchanged; the wah within 0.2 dB).
- Not tested on the FM-1 (the user was playing it).
