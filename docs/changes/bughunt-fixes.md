## Changes
- The manual: the SCALE page's MODE and TRANSP act on the FM-1's own keys; notes from USB-MIDI and the TRS jack play
  as sent (a DIATONIC / DIA 7TH chord still takes its root onto the scale, from any source).
- TREM/WAH: on WAH the SHAPE card shows "-" (it does nothing there and keeps its value for TREM and PAN).
- The manual: the compressor woken from OFF keeps the volume of what you play about the same (a quiet, dying tail
  comes up, as under any compressor).

## Fixed
- A note played while every voice is busy is never lost: a stolen voice that died away during its steal fade, or
  more new notes in one audio block than voices, dropped the new note (every engine).
- Moving these under ringing notes, echoes or a reverb tail no longer clicks, steps or bends:
  - the Rhodes TYPE (a short crossfade);
  - REVERB MIX turned down (the tail rang up to 6 dB louder at MIX 0);
  - REVERB MOD (HALL, INFINITE);
  - REVERB SIZE on PLATE and INFINITE (the old size fades into the new one, about 0.2 s; it stepped and bent);
  - DRIVE AMOUNT on FDRV and KHLON;
  - TREM <-> WAH;
  - DELAY EFFECT DIG <-> ANA (a crossfade);
  - DELAY TIME on DIG (the old and the new time crossfade) and on ANA (it bends gently, as tape does);
  - a synced delay following a new tempo;
  - the WAH's RATE (the resonance);
  - TAPE's TONE;
  - FX (all effects off) with SPREAD on;
  - LEVEL with FX off;
  - the MASTER knob (it stepped once per audio block).
- PERFORM: turning the DELAY or TREM/WAH knob up from OFF switches on the type you last used, not the first one.
- PLATE: a very hot input (the compressor's full make-up into the plate) no longer overflows its input filter.
- Touch WAH: a very hot input (the compressor's full make-up, the pedal) no longer overflows its filter.
- A SAVE, and the settings' save, with the drive's KHLON and the tremolo on (as F06 with TREM) waited for ever: the
  output was silent, but a tiny idle residue under the silence gate never counted as quiet. Saves go through now.

## Tested
- Host: tests/py/test_steal_race.py, tests/py/test_switch_clicks.py (the cases above; each fails on 0.9.5),
  tests/py/test_control_sweep.py (REVERB SIZE on PLATE and INFINITE: no bend, no click),
  tests/py/test_delay_time_change.py, tests/py/test_overflows.py (under ASan + UBSan), tests/py/test_monkey.py,
  tests/py/test_persistence.py (a save with KHLON + TREM, then a power cycle), tests/scripts/perform_wake.txt,
  scale_midi.txt, wah_shape.txt, quiet_gated.txt, the control sweep (tools/control_sweep.py) and the manual's claims with their
  expected failures removed, make test, make pytest, Gate R.
