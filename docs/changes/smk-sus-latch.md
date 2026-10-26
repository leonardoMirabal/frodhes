## Fixed
- SMK-37 (GitHub #13): the SUSTAIN pad and REC share one latch. A tap on either switches the sustain you hear on or
  off, holding either one works as a pedal and letting go releases the latch, and a MIDI pedal let up (CC 64)
  releases it too. Before, each kept its own latch: REC could not let go of a sustain the pad had latched, nor the
  other way round.

## Tested
- Host: tests/smk/sus_shared.txt (both orders, holds, CC 64, a note ringing and released), tests/smk/pedal.txt,
  make test-smk, make test; the FM-1 firmware is byte for byte the same (sha256 of the fwsc before and after).
