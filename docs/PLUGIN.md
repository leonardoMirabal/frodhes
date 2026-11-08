# Frodhes as a desktop plugin

The FM-1 firmware's sound engine as a CLAP, VST3 and Audio Unit (v2) instrument for macOS. The plugin is the app
itself, `firmware/src/frodhes.c` compiled unchanged (as for the browser emulator and the Move), so it plays what the
FM-1 plays: the same parameters, the same factory sounds, the same state.

This is a developer build (milestone B-M0 of the plan). It has **no window of its own**: every host shows its own
generic parameter view. The plugin's own interface comes later.

## Build

Needs Xcode's command line tools and CMake 3.21 or newer (Ninja is used when installed). Everything downloads into
the project (`.local/plugin-deps`), pinned by commit and checked at configure time (`plugin/deps.lock`).

    make plugin-deps          # the pinned sources (CLAP, clap-wrapper, VST3 SDK, AudioUnitSDK) and test tools
    make plugin               # build/plugin/bundles/Frodhes.clap, Frodhes.vst3, Frodhes.component
    make test-plugin          # the coroutine, the headless host's selftest, the parameter table, a quick Gate D
    make gate-d               # Gate D: every mode (tools/gate_d.py)

The bundles are universal (Apple silicon and Intel). `make plugin` installs nothing.

## Try it in a DAW

Copy the bundle your host uses into your plug-in folder, clear the download flag, and rescan:

    cp -R build/plugin/bundles/Frodhes.clap      ~/Library/Audio/Plug-Ins/CLAP/
    cp -R build/plugin/bundles/Frodhes.vst3      ~/Library/Audio/Plug-Ins/VST3/
    cp -R build/plugin/bundles/Frodhes.component ~/Library/Audio/Plug-Ins/Components/
    xattr -dr com.apple.quarantine ~/Library/Audio/Plug-Ins/*/Frodhes.*

For the Audio Unit, `killall -9 AudioComponentRegistrar` makes macOS see it at once; `auval -v aumu Frd1 Frdh`
checks it. To remove the plugin, delete the three bundles again.

The plugin appears as **Frodhes** (vendor Frodhes), an instrument with a stereo output and a note input (MIDI or CLAP
notes). Play it from a MIDI track; set its parameters from the host's generic view or by MIDI CC, as on the FM-1.

## What it does

| | |
| --- | --- |
| Engine | **ENGINE** parameter: HQ (the default: the Move's float paths, 16 voices) or CLASSIC (the FM-1's own build, bit for bit: its integer engine and its 10 voices). Switching while notes sound lets them ring out on the old engine |
| Parameters | the app's 67 parameters with their FM-1 ids (stable: saved sessions and automation keep them), ranges and texts, grouped by module, plus ENGINE, TUNE, PRESET (F01-F57, for the host's own view), SUSTAIN (the latch) and EFFECTS BYPASS. A change the app makes itself (a program change, a CC, a preset) goes back to the host. Automation takes the device's own path: a sweep renders the same bytes as the same sweep sent as MIDI CCs, so the smoothing is the FM-1's |
| Presets | F01-F19 (the FM-1's) and F20-F57 (the Move's variants), then the user's own, one file each (`*.frpreset`) in `~/Library/Application Support/Frodhes/Presets` (Linux `~/.local/share/frodhes/presets`, Windows `%APPDATA%\Frodhes\Presets`; `FRODHES_PRESET_DIR` overrides). Browse with wrap-around, UNDO as on the device. Program changes: 0-15 F01-F16, 32-34 F17-F19, 35-72 F20-F57, 16-31 the first 16 user files (where there is none, the FM-1's own user bank: its starter sounds) |
| Settings | MIDI CHANNEL (ALL or 1-16), the velocity curve (MEDIUM as played, LIGHT, HEAVY: the device's TOUCH curves; FIXED with its velocity), program changes on / off, the tempo source (HOST: the DAW's tempo, 120 without one, a MIDI clock sent to the plugin still leads; INTERNAL: the BPM setting; MIDI CLOCK: an incoming clock, else the BPM setting), SYNC ALL (every tempo effect's SYNC at one division, also on every preset load) |
| MIDI learn | any knob (and TUNE, PRESET, SUSTAIN, BYPASS) learns the next CC; a learned CC comes before the device's CC map; forget one or all; saved with the project |
| State | the sound as the Move saves it (the 256-byte settings record and the sound it came from) plus ENGINE and the plugin's own keys (settings, learned CCs, SUSTAIN, the preset's name; version "pv" 2). A state without them (the first builds, a Move track) loads them as defaults; a broken or cut state is refused and changes nothing |
| Tempo | the tempo source above (the delay's and the LFOs' sync) |
| Sample rates | 44.1 kHz: the app's own samples. 48 kHz: the FM-1's own USB resampler, the FM-1's USB stream bit for bit. Any other rate from 1 to 768 kHz: a windowed-sinc resampler (error under 1 LSB of 16 bits in the passband) |
| Latency | reported to the host: 10 ms (441 frames at 44.1 kHz, 487 at 48 kHz, 11 ms or more at other rates). Events are placed on the millisecond the FM-1 would see them |
| Instances | up to 16 in one host process; a 17th stays silent and says so in the host's log |
| The UI core | `plugin/frp_ui_iface.h`: everything the plugin's own window shows and does (parameters with gestures, presets, settings, MIDI learn, SUSTAIN, BYPASS, meters, held notes), main thread only, never blocking the audio |

Not in this build: the FM-1's sequencer, arpeggiator and chord buttons (their CCs are ignored, as on the Move), the
host's transport start / stop, presets in the host's browser, a window.

## How it is checked

- **Gate D** (`tools/gate_d.py`): every MIDI-driven render of the test suite, played through the CLAP plugin by a
  headless host (`plugin/tests/clap_render.c`), is byte-identical to the host build's (the same list as the Move's
  Gate M); with random block sizes, 1-frame blocks and CLAP note events too; HQ against the host build with HQ on;
  48 kHz against the FM-1's resampler compiled unchanged; the other rates against an FFT reference; the Intel slice
  under Rosetta.
- The headless host's selftest: every parameter value's text reads back as itself, the state saves, loads and saves
  to the same bytes, the rates and latencies, 16 instances play and the 17th is silent.
- Gate D's core mode (`tools/gate_d.py --only core`, also `tests/py/test_plugin_core.py`): the headless host's
  `--coretest` drives the UI core through a CLAP extension (CLASSIC 10 voices, HQ 16 and the switch while playing;
  the state with every setting, older and broken states; presets, user files, UNDO, program changes; automation and
  gestures; MIDI learn; the tempo sources and SYNC ALL; SUSTAIN, BYPASS, velocity, MIDI CHANNEL); an automation sweep
  equals the same sweep as MIDI CCs byte for byte (CLASSIC and HQ); a 1/4 delay's echo lands at 600 / 500 ms at the
  transport's 100 / 120 BPM.
- clap-validator (the CLAP bundle), pluginval at strictness 10 (the VST3) and `auval -strict` (the Audio Unit).
- The firmware build never sees `plugin/`: the plugin only reads `firmware/src`.

## Layout

| | |
| --- | --- |
| `plugin/CMakeLists.txt`, `plugin/cmake/` | the build: the pins, the 32 engine slots (16 HQ, 16 CLASSIC), the formats through clap-wrapper |
| `plugin/src/engine_unit.c`, `slot_bridge.h` | one engine slot: the app over the browser's platform layer, with every symbol private to the slot; its presets, MIDI learn, settings, state |
| `plugin/src/bridge.c`, `bridge.h` | the instance: its two slots and the ENGINE switch, time, events, the UI queue, the resamplers, the user preset files |
| `plugin/frp_ui_iface.h` | the UI core's interface (implemented in `frodhes_clap.c`) |
| `plugin/src/frodhes_clap.c`, `clap_entry.cpp` | the CLAP plugin; clap-wrapper turns it into the VST3 and the Audio Unit |
| `plugin/src/coro/` | the coroutine the app's main loop runs in (arm64, x86_64) |
| `plugin/gen/params_map.h` | the parameter table, generated by `tools/gen_plugin_params.py` from the app's own |
| `plugin/tests/` | the headless host, the coroutine test, the parameter dump |
| `tools/plugin_deps.sh`, `plugin/deps.lock` | the pinned dependencies |
