## Changes
- The Move, the desktop plugin and the Akai MPC (the HQ electric piano) go further than the FM-1 build on soft notes.
  The pickup below C#6 is now computed exactly instead of read from the FM-1's table. So a soft note's tone can keep
  changing as it rings, past the point where the FM-1's table stops it. At v20 to v40 the second harmonic now
  decays at about 0.65 to 0.9 of the fundamental's rate (FM-1 build: about 0.55), so a held soft note stays round
  and keeps its warmth instead of thinning out within the first seconds. The fundamental's decay and every note's
  level are unchanged. The effect fades out between v40 and v64, and from v64 up the sound is the FM-1 build's.
- HQ above v120: the strike keeps the exact hammer contact's shape at the fundamental level of the FM-1 build
  (within 0.02 dB), so the dynamics slope at the top matches the FM-1 build's.
- The FM-1, the SMK-37 and the HQ build's Classic path are unchanged (byte-identical).
