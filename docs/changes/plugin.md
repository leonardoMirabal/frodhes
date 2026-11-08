## Tools
- A desktop plugin build for macOS (CLAP, VST3, Audio Unit), for developers: `make plugin`, docs/PLUGIN.md. The
  FM-1's engine compiled unchanged: HQ (16 voices) or CLASSIC (the FM-1's build, 10 voices); presets F01-F57 and
  user preset files, UNDO, program changes; MIDI CHANNEL, velocity curves, tempo source, SYNC ALL; MIDI learn;
  SUSTAIN latch and effects bypass; everything saved with the project. No window of its own yet; not in the
  downloads.

## Tested
- Gate D (`tools/gate_d.py`): the plugin's renders equal the host build's, byte for byte, on every MIDI-driven test
  render (78), with random and 1-frame blocks and CLAP note events; HQ against the host build with HQ on; 48 kHz
  against the FM-1's USB resampler; other rates within 1 LSB; the Intel slice under Rosetta.
- Gate D core: CLASSIC 10 / HQ 16 voices, the state round trip (every setting, older and broken states), presets,
  automation (= the device's CC path, byte for byte), MIDI learn, the tempo sources and a synced delay at the host's
  tempo.
- clap-validator, pluginval (strictness 10) and `auval -strict` pass.
