## Changes
- **The output level is 0.9.9's again** (the default LEVEL and the limiter's threshold, LIM_T 24000): the factory
  presets F01-F19 within 0.5 dB of 0.9.9 by loudness (F16, below, excepted), on the FM-1, the Move and the plugin.
- **No effect adds peak level.** Each effect switched on keeps the loudest moments within 2 dB of the dry sound
  (`tools/fx_pristine.py`'s new peak check), so none of them drives the output limiter:
  - PHASER: its level trimmed by DEPTH so a chord's peak stays within 0.5 dB of the phaser off (single notes within
    2 dB); DEPTH 0 matches by loudness, the deep settings are 2-3 dB quieter on average. The sound itself is unchanged
    (a pure gain).
  - PAN at DEPTH 127: neither side goes above unity (it had a +3 dB side peak).
  - Touch wah: the make-up capped at twice the ratio of the input's and output's peaks, so it no longer overshoots a
    note's onset (+1.6 dB at most, +7 dB before) and a soft high note under a closed resonant wah keeps its level.
  - Filter LP12 / LP18: resonance gain compensation as the cutoff falls (a resonant sweep stays within about 1 dB of
    the unfiltered level).
  - ANA delay: on a silent line its time no longer glides from the last setting, which had bunched the first repeats
    together (a chord's peak +3.1 dB before, +1.7 now; the echoes as loud as DIG's).
  - HQ HALL (Move / plugin) at MIX 127: the switch between its quiet and loud paths moved lower, so its non-linear
    residual falls from -73.7 to -79.9 dBFS.
- **The compressor is clean on chords.** It holds its detector over a note's cycle (11.6 ms), smooths its reduction's
  release (about 0.1 s, never slower than half the RELEASE setting) and interpolates its gain: a v127 chord's
  intermodulation with FX off (the compressor stays on) at most -68.9 dB on every TYPE (was -45 .. -56; the limit -60).
  Loud v127 chords sit about 0.45 dB lower; v64 / v96 within 0.15 dB.
- Factory presets with effects re-levelled for the quieter effects: F05 +7, F09 +14, F10 +2, F11 +21, F18 +4 LEVEL
  steps (the Move's preset records the same); U04 +3, U05 +9, U07 +10. Then F10 +5, F12 +9, F19 +8 to 0.9.9's v127
  chord and note level (F12 and F19 had lost 2-3 dB with the clean top register).
- F16 stays about 3 dB (chord) / 4.8 dB (note) under 0.9.9: 0.9.9 reached its level only through the limiter (its
  peaks at the ceiling, its crest 13 dB against F15's 17.5 dB), and matching it now would put it back on the limiter.
- A filter TYPE change no longer clicks with RESO up (the compensation is applied on every TYPE, so the crossfade's two
  TYPEs share one state).

## Tested
- The boot sound F01 STAGE 77 LEVEL 50 -> 52 (+0.4 dB) and F11 RESO SWEEP with it: with every effect now holding its peak,
  every TYPE x RESPONSE note and chord at v96 / v127 sits within 1 dB of 0.9.9 on the FM-1 and the Move's HQ build
  (before: -1.4 .. +0.6 dB). The other presets keep 0.9.9's level.
- Host: `tests/py/test_fx_pristine.py` (OPEN list empty on the one-core, two-core and HQ builds, presets included),
  the new `tests/py/test_comp_clean.py` (fails before, passes after), `make test` (65 scripts), `make test-c1`, the
  full `make pytest`, `tests/py/test_rev_inf.py`, `tools/gen_move_presets.py --check`. Character before / after by
  level-normalised critical-band spectra (the phaser, pan, filter and hall shapes unchanged; the wah within 0.2 dB).
- Not tested on the FM-1 (the user was playing it).
