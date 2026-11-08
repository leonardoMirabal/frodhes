<!-- DRAFT: the plugin's user-facing release note. When the plugin branches merge, their own docs/changes/plugin.md
(the developer-only "## Tools" note: "not in the downloads") is replaced by this file: keep its "## Tested" lines
here and delete plugin.md. Finalise every [TODO ...] from agent P4 (packaging) and the user's decisions (default
engine, signing). Public text: no DAW or other brand names; format names (CLAP, VST3, Audio Unit) are fine. -->
## New

- **Frodhes as a desktop plugin for macOS**: CLAP, VST3 and Audio Unit, one universal build for Apple silicon and
  Intel Macs, macOS 11 or newer. The same instruments, effects and factory sounds as on the FM-1, computed by the
  same code: [TODO: the default engine: "the higher-quality version with 16 voices, as on the Move; ENGINE switches
  to CLASSIC, the FM-1's own sound bit for bit"]. Download `frodhes-plugin-macos.[TODO: pkg or zip]` from the
  release page; the install is in the README ("Desktop plugin") and the manual's appendix 17.
- The host's tempo drives the synced effects; it runs at any sample rate and reports its latency ([TODO] ms) to
  the host; up to 16 instances in one project. A sound saved in the plugin loads on the Move and the other way
  round. [TODO: confirm each against the shipped build.]
- [TODO: the window: "its own window, the FM-1's pages and cards" or "no window of its own yet: the host's generic
  controls show every parameter with its full name".]
- Windows follows in a later version.

## Tested

- Gate D (`tools/gate_d.py`): the plugin's renders equal the host build's, byte for byte, on every MIDI-driven test
  render ([TODO] renders), with random and 1-frame blocks and CLAP note events; the higher-quality engine against
  the host build with it on; 48 kHz against the FM-1's USB resampler; other rates within 1 LSB; the Intel slice
  under Rosetta.
- clap-validator, pluginval (strictness 10) and `auval -strict` pass.
- [TODO: the hosts it was played in (count only, no names), the install on a clean macOS account (notarized, or
  the Gatekeeper steps of the README), 16 instances, a saved project reopened.]
