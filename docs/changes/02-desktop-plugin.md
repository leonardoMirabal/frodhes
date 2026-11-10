<!-- Public text: no DAW or other brand names; format and platform names are fine.
VERIFY: the platforms in the release commit's "Plugin tested:" line (tools/plugin_asset.sh) = the platforms named
here; the user's real-host checklist (docs/PLUGIN.md "Try it in a DAW") as a count of hosts, no names. -->
## New

- **Frodhes as a desktop plugin**: an instrument for your music software, as CLAP and VST3 on macOS, Windows and
  Linux, and as an Audio Unit on macOS. The same instruments, effects and factory sounds as on the FM-1, computed by
  the same code. It opens in the higher-quality version with 16 voices, as on the Move; its ENGINE control switches
  to CLASSIC, the FM-1's own sound bit for bit with its 10 voices.
- Its own window on every system: the FM-1's pages as cards with knobs, resizable, sharp on high-resolution screens.
  Presets (the 57 factory sounds and your own, saved as files): click the preset's name for the whole list, grouped
  by instrument, and SAVE with a name of your own. MIDI learn, the effects' bypass (a bypassed effect's knobs are
  drawn inactive) and the sustain latch are in it; everything, the window's size too, is saved with your project.
- The host's tempo drives the synced effects. It runs at any sample rate (44.1 and 48 kHz exactly as the FM-1),
  reports its latency (10 ms) and plays up to 16 instances in one project.
- Downloads: `frodhes-plugin-macos.pkg` (installer) or `frodhes-plugin-macos.zip` (macOS 11 or newer, Apple silicon
  and Intel), `frodhes-plugin-windows.zip` (Windows x64), `frodhes-plugin-linux-x86_64.zip` and
  `frodhes-plugin-linux-aarch64.zip` (glibc 2.31 or newer: Ubuntu 20.04, Debian 11 and later). Each is
  self-contained: nothing else to install.
- The plugin is not signed by Apple or Microsoft yet. On macOS, open the installer with right-click > **Open** (or
  System Settings > Privacy & Security > **Open Anyway**); for the zip, clear the download flag once in Terminal with
  `xattr -dr com.apple.quarantine ~/Library/Audio/Plug-Ins/*/Frodhes.*`. On Windows, unblock the zip
  (right-click > Properties > **Unblock**) before you extract it. The steps are in the README.

## Tested

- Gate D (`tools/gate_d.py`): the plugin's renders equal the host build's, byte for byte, on every MIDI-driven test
  render (78), with random and 1-frame blocks and CLAP note events; the higher-quality engine against the host build
  with it on; 48 kHz against the FM-1's USB resampler; other rates within 1 LSB; the Intel slice under Rosetta.
- The core: CLASSIC 10 and HQ 16 voices, the state round trip (every setting, older and broken states), presets,
  automation (the device's CC path, byte for byte), MIDI learn, the tempo sources and a synced delay at the host's
  tempo; the tail reported to the host.
- Validators: clap-validator (35 passed, 0 failed), pluginval at strictness 10 with its editor tests, `auval
  -strict`, the VST3 SDK validator (47 passed, 0 failed); the Audio Unit in a reference AU host sample-identical to
  the CLAP render.
- A 30-minute stress run: 3 instances at real time, engine and preset switches, 818 000 automation events, the window
  opened and closed, no event dropped and no memory growth.
- Windows: the build under wine (the coroutine, the controller, the window, the CLAP host and a Gate D subset).
  Linux: x86_64 and aarch64 in Ubuntu 20.04 under Xvfb (the window and the tests); the dependency check (only the
  system's own libraries, no symbol newer than glibc 2.31).
