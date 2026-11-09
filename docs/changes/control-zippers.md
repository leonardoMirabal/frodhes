## Fixed

- COMPRESSOR MAKEUP: turning it fast (or a jump over MIDI) no longer clicks. The make-up glides to the knob's value
  in a few milliseconds; 0 to 127 in one CC was a +24 dB step over 16 samples.
- DELAY TAPE: turning TIME no longer adds a zipper to the echoes. The playback head's tone (its low bump and its
  high-frequency loss) now follows the tape's real speed as the motor gets there, not the knob at once.
- REVERB HALL: moving SIZE between AUTO and a fixed size (up to 35 steps apart) no longer steps the tail; HALL's size
  glides a quarter step every 0.7 ms.
- REVERB PLATE: turning SIZE under a ringing tail is smooth: the cross-fade between the old and new reads moves every
  sample (it moved in eight steps per read, a faint buzz while SIZE turned), and it now takes 1 s (it was 0.13 s;
  0.25 s on the HQ and two-core builds), so the tail no longer bends in pitch while SIZE turns.
- REVERB PLATE: its modulation no longer adds grain. The two modulated reads in the plate jumped 2 samples each
  time the modulation crossed a whole sample, about 20 times a second. That added a faint gritty noise to every PLATE
  tail, 8 to 10 dB of extra energy at 2 to 12 kHz. The plate now sounds as smooth at any MOD as at MOD 0, and its
  level is unchanged (within 0.1 dB).
- CHORUS / PHASER: switching between them (CC 85) or either on or off now fades over about 0.1 s instead of 46 ms. The
  phaser now sits a few dB under the chorus, and the faster fade put that drop into one step.
- REVERB TYPE: a new TYPE no longer starts on a step. What goes into the new TYPE fades in over 6 ms (it started at
  full level, in the middle of a ringing chord's waveform; INFINITE after PLATE was grainy for its first 0.2 s). The
  old TYPE's fade now ends at silence (it ended at 1/128 of its level, then dropped to 0 in one sample).

## Tested

- Host: tests/py/test_ctl_zipper.py (one test per fix, each failing on rc-0910 before it) and
  tests/py/test_control_sweep.py: comp MAKEUP click 17.3 -> 1.2, TAPE TIME zipper 2.53 -> 1.9 (wet 2.83 -> 2.02),
  HALL SIZE wet zipper 2.15 -> 1.0, PLATE SIZE zipper 2.59 -> 1.36 (wet 3.35 -> 1.63, pitch 1.3 cents, jump 0.93),
  rev TYPE wet zipper 2.9 -> 1.22, cho/pha EFFECT jump 1.59 -> 0.93. A held PLATE's d2 4.0x -> 0.96x its MOD 0's.
  tests/py/test_ctl_zipper.py also runs the reverb SIZE and TYPE cases on the HQ and two-core builds.
- Every static render of the control sweep (686) is sample for sample as before except the 38 PLATE renders with
  MOD on (the grain fix). The full release diff moves only the PLATE presets (F04-F09, F14, F17): their level within
  0.13 dB, the grain down. `make quick`: the same lines as rc-0910.
