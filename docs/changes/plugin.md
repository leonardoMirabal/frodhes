## Tools
- A desktop plugin build for macOS (CLAP, VST3, Audio Unit), for developers: `make plugin`, docs/PLUGIN.md. The
  FM-1's engine compiled unchanged, HQ or CLASSIC, 16 voices, no window of its own yet; not in the downloads.

## Tested
- Gate D (`tools/gate_d.py`): the plugin's renders equal the host build's, byte for byte, on every MIDI-driven test
  render (78), with random and 1-frame blocks and CLAP note events; HQ against the host build with HQ on; 48 kHz
  against the FM-1's USB resampler; other rates within 1 LSB; the Intel slice under Rosetta.
- clap-validator, pluginval (strictness 10) and `auval -strict` pass.
