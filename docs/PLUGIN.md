# Frodhes as a desktop plugin

The FM-1 firmware's sound engine as a CLAP, VST3 and Audio Unit (v2) instrument for macOS. The plugin is the app
itself, `firmware/src/frodhes.c` compiled unchanged (as for the browser emulator and the Move), so it plays what the
FM-1 plays: the same parameters, the same factory sounds, the same state.

This is a developer build. On macOS it opens its own window (the CLAP, VST3 and Audio Unit alike); on other systems
the host shows its generic parameter view.

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
notes). Play it from a MIDI track; set its parameters in its window, from the host's generic view or by MIDI CC, as on
the FM-1.

## The window

960 x 600 points by default, resizable at 16:10 from 720 x 450 to 1920 x 1200, drawn at the screen's pixel size
(Retina and high-DPI Windows / Linux screens too), on macOS (Cocoa), Windows (a child window) and Linux (X11, run
by the host's event loop). The plugin's own renderer (`plugin/ui/`) draws it; the fonts are inside the binary.

| | |
| --- | --- |
| Knobs | drag up / down (200 points for the whole range), Shift for ten times finer, the wheel, double-click (or Command-click; Control-click on Windows and Linux) for the default, right-click (or Control-click on macOS) for MIDI LEARN, FORGET and DEFAULT VALUE |
| Switches, menus, tabs | click; the wheel steps a switch or a menu |
| Keyboard focus | click a control or Tab / Shift-Tab; the arrows step the value (Shift: by 10), Page Up / Down by 10, Home / End, Delete the default, Enter opens a menu, Esc closes a menu, SETTINGS or the focus. Other keys go to the host |
| The keys | play notes (lower on the key: louder); held notes light up, the host's too |
| SUSTAIN, EFFECTS | latch the sustain (CC 64); bypass every effect (the app's FX button, CC 111) |
| The gear | SETTINGS |

Every change goes to the host as a parameter change with a gesture (its undo and automation recording see one
move per drag); host automation moves the knobs. Not yet connected (the next milestone's backends): the presets'
names and buttons, the SETTINGS values, MIDI learn's listening, TUNE: the window shows them and keeps them for the
session only.

## What it does

| | |
| --- | --- |
| Engine | **ENGINE** parameter: HQ (the default: the Move's float paths) or CLASSIC (the FM-1's integer engine, bit for bit). 16 voices on every instrument |
| Parameters | the app's 67 parameters with their FM-1 ids (stable: saved sessions and automation keep them), ranges and texts, grouped by module, plus ENGINE. A change the app makes itself (a program change, a CC) goes back to the host |
| State | the sound as the Move saves it (the 256-byte settings record and the sound it came from) plus ENGINE: a sound moves between a Move track and a desktop session |
| Tempo | the host's transport tempo (the delay's sync), 120 BPM without one |
| Sample rates | 44.1 kHz: the app's own samples. 48 kHz: the FM-1's own USB resampler, the FM-1's USB stream bit for bit. Any other rate from 1 to 768 kHz: a windowed-sinc resampler (error under 1 LSB of 16 bits in the passband) |
| Latency | reported to the host: 10 ms (441 frames at 44.1 kHz, 487 at 48 kHz, 11 ms or more at other rates). Events are placed on the millisecond the FM-1 would see them |
| Instances | up to 16 in one host process; a 17th stays silent and says so in the host's log |

Not in this build: the FM-1's sequencer, arpeggiator and chord buttons (their CCs are ignored, as on the Move), the
host's transport start / stop, presets in the host's browser.

## How it is checked

- **Gate D** (`tools/gate_d.py`): every MIDI-driven render of the test suite, played through the CLAP plugin by a
  headless host (`plugin/tests/clap_render.c`), is byte-identical to the host build's (the same list as the Move's
  Gate M); with random block sizes, 1-frame blocks and CLAP note events too; HQ against the host build with HQ on;
  48 kHz against the FM-1's resampler compiled unchanged; the other rates against an FFT reference; the Intel slice
  under Rosetta.
- The headless host's selftest: every parameter value's text reads back as itself, the state saves, loads and saves
  to the same bytes, the rates and latencies, 16 instances play and the 17th is silent.
- clap-validator (the CLAP bundle), pluginval at strictness 10 (the VST3) and `auval -strict` (the Audio Unit).
- The window (`tests/py/test_plugin_gui.py`): `frp_gui_shot` opens it in a real window, drives it as a user does,
  automates and plays as a host does, resizes, closes and opens it; every picture it showed, read back from its own
  surface, is pixel for pixel what the reference renderer (`plugin/ui/tools/frui_render.c`) draws from the same state.
  pluginval's editor tests open and close it. The window's core alone (`plugin/tests/test_gui_ctl.c`) and the
  Windows and Linux views in their own windows under wine and Xvfb (`plugin/tests/gui_win_demo.c`, `gui_x11_demo.c`,
  through `tools/plugin_win.sh test` and `tools/plugin_linux.sh test`) get the same input checks.
- The firmware build never sees `plugin/`: the plugin only reads `firmware/src`.

## Layout

| | |
| --- | --- |
| `plugin/CMakeLists.txt`, `plugin/cmake/` | the build: the pins, the 16 engine slots, the formats through clap-wrapper |
| `plugin/src/engine_unit.c`, `slot_bridge.h` | one engine slot: the app over the browser's platform layer, with every symbol private to the slot |
| `plugin/src/bridge.c`, `bridge.h` | the instance: slots, time, events, the resamplers |
| `plugin/src/frodhes_clap.c`, `clap_entry.cpp` | the CLAP plugin; clap-wrapper turns it into the VST3 and the Audio Unit |
| `plugin/src/coro/` | the coroutine the app's main loop runs in (arm64, x86_64) |
| `plugin/gen/params_map.h` | the parameter table, generated by `tools/gen_plugin_params.py` from the app's own |
| `plugin/gui_core.c`, `gui_clap.c` | the window: its model, input and partial repaints; CLAP's gui extension |
| `plugin/gui_platform.h`, `gui_mac.m`, `gui_win.c`, `gui_x11.c` | the platform views behind one interface: macOS, Windows, Linux |
| `plugin/frp_ui_iface.h` | what the window asks of the plugin (the backends plug in there) |
| `plugin/ui/` | the renderer, its fonts reader and value texts, the headless render tool |
| `plugin/tests/` | the headless host, the coroutine test, the parameter dump, the window's test host |
| `tools/plugin_deps.sh`, `plugin/deps.lock` | the pinned dependencies |
