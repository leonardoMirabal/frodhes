## Fixed

- VIBES and MARIMBA start at most 10 notes in one audio block. A Program Change to a VIBES or MARIMBA preset while a
  Rhodes chord rang, then more notes than voices at once (a sequencer step, a computer's chord), struck every free
  voice and stole the same voices again in that block: twice the note-on work in one block, the audio interrupt's
  peak on the FM-1 (4.5-4.7 ms in a long random test). The notes past the tenth now start one block (5.8 ms) later,
  in order.

## Tools

- `tools/hil_cpu.py --worst`: a Program Change row: the mid + top chord at v127 with the pedal and every effect, a
  preset of another engine loaded while it rings, then 26 notes at once.

## Tested

- Host: tests/py/test_bar_starts.py (at most 10 starts a block, the cost model's worst block as on ringing voices,
  the first block equal to a plain chord, no click); Gate R (scripts/burst moves: 40 note-ons at once on VIBES),
  Gate H, make test, make pytest.
