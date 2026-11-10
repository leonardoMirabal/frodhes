## Changes

- **New factory presets** for the new electric piano: the 19 on the FM-1 (and their 38 variants on the Move and in
  the desktop plugin) re-voiced, each at its predecessor's loudness within 1 dB, with the output limiter idle on
  every one. Six of them are quieter than before (BELL DYNO, LUSH CHO, VIBES, VIBES ECHO, MARIMBA, WOOD ECHO, by
  2.7 to 8 dB): they used to reach the limiter, which no preset may do now.
- **VIBES and MARIMBA's damper knock** stays under the note it ends at every BARK: with a hard mallet, letting go of
  a chord made it about 0.5 dB louder for a moment; now the knock sits 37-41 dB under the note at any BARK.

## Tested

- Host: the presets by critical-band loudness and peaks against 0.9.9 (`tools/quick.py` level gate, `tools/preset_lists.py`);
  `tests/py/test_bars_knock_mallet.py` (VIBES and MARIMBA at BARK 0 / 64 / 96 / 127 and four velocities, a 16-note
  chord) and the never-louder-after-key-up checks on every sound and engine; `make quick`; Gate R, Gate H and Gate M
  renewed for the intended changes; local CI on arm64 and amd64.
- FM-1 (release build): the crackle self-test (every injected glitch found on every engine).
  <!-- VERIFY: the rc4 device tier (both units) result here before the release commit. -->
