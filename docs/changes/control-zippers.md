## Fixed

- COMPRESSOR MAKEUP: turning it fast (or a jump over MIDI) no longer clicks. The make-up glides to the knob's value
  in a few milliseconds; 0 to 127 in one CC was a +24 dB step over 16 samples.
- DELAY TAPE: turning TIME no longer adds a zipper to the echoes. The playback head's tone (its low bump and its
  high-frequency loss) now follows the tape's real speed as the motor gets there, not the knob at once.
- REVERB HALL: moving SIZE between AUTO and a fixed size (up to 35 steps apart) no longer steps the tail; HALL's size
  glides a quarter step every 0.7 ms.
- REVERB PLATE: turning SIZE under a ringing tail is smooth: the cross-fade between the old and new reads moves every
  sample (it moved in eight steps per read, a faint buzz while SIZE turned).
- REVERB TYPE: a new TYPE no longer starts on a step. What goes into it fades in over 6 ms (it started at full level,
  in the middle of a ringing chord's waveform; INFINITE after PLATE was grainy for its first 0.2 s).

## Tested

- Host: tests/py/test_ctl_zipper.py (one test per fix, each failing on rc-0910 before it) and
  tests/py/test_control_sweep.py: comp MAKEUP click 17.3 -> 1.2, TAPE TIME zipper 2.53 -> 1.9 (wet 2.83 -> 2.02),
  HALL SIZE wet zipper 2.15 -> 1.0, PLATE SIZE zipper 2.59 -> 1.75 (wet 3.35 -> 1.87), rev TYPE wet zipper
  2.9 -> 1.93. Every static render of the control sweep (686) is sample for sample as before; the HQ and two-core
  host builds improve the same way. `make quick` unchanged (127 of 149 lines, the same lines as rc-0910).
