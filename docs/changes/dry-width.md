## Changes
- Width: with SPREAD at 0 every sound gets one side-wall reflection (570 Hz .. 4 kHz, 10 ms, about -14 dB, on the
  side only). The plain piano was a point in the middle on headphones; now it has a little room width. The mono sum is
  unchanged, the loudness by critical band within 0.05, the v127 chord peaks within 0.02 dB. Off while SPREAD is on.
- COMPRESSOR: the auto make-up re-fitted for the width (+0.1 .. +0.5 dB from THRESH -20 dB down; the factory presets,
  -14.5 .. -17.6 dB, unchanged).

## Tested
- Host: tests/py/test_dry_width.py (width, the mono sum by band, binaural loudness, peaks, steady side); test_spread,
  test_every_control (CHORUS off, FX-BYPASS, SPREAD 0, TREM-WAH) and test_display_sound (LEVEL, THRESH) read the mono
  sum where they assumed L == R; the pristine fast grid (MARIMBA's key-up windows listed in tests/expected_changes.yaml).
