# Frodhes as a desktop plugin

The FM-1 firmware's sound engine as a CLAP, VST3 and Audio Unit (v2) instrument for macOS, and a CLAP and VST3
instrument for Windows and Linux. The plugin is the app
itself, `firmware/src/frodhes.c` compiled unchanged (as for the browser emulator and the Move), so it plays what the
FM-1 plays: the same parameters, the same factory sounds, the same state. A desktop computer has no CPU limit like
the devices', so the plugin plays the high-quality version of everything: every engine and every effect on its HQ
path, with 24 voices on every engine.

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

![The plugin's window: the VOICE knobs, two effect sections and a keyboard](manual/img/plugin/plugin-main.png?v=a6729600)

<p align="center">
  <img src="manual/img/plugin/plugin-presets.png?v=cec41ef5" width="384" alt="PRESETS: every factory and user sound, grouped by instrument">
  <img src="manual/img/plugin/plugin-save.png?v=96849c7d" width="384" alt="SAVE: the name field in place of the preset's name">
</p>

960 x 600 points by default, resizable at 16:10 from 720 x 450 to 1920 x 1200, drawn at the screen's pixel size
(Retina and high-DPI Windows / Linux screens too), on macOS (Cocoa), Windows (a child window) and Linux (X11, run
by the host's event loop). The plugin's own renderer (`plugin/ui/`) draws it; the fonts are inside the binary. It repaints only what
changed; the plate and the section panels are drawn once per window size and kept, so even a whole repaint at 2x
(1920 x 1200 pixels) takes about 8 ms on an M-series Mac (`frp_test_gui_ctl` prints it), with the same pixels as one
uncached draw.

| | |
| --- | --- |
| Knobs | drag up / down (200 points for the whole range), Shift for ten times finer, the wheel, double-click the value text to type a value (Enter sets the value whose text is nearest the number, Esc or a click elsewhere leaves it), double-click the knob itself (or Command-click; Control-click on Windows and Linux) for the default, right-click (or Control-click on macOS) for MIDI LEARN, FORGET and DEFAULT VALUE |
| Switches, menus, tabs | click; the wheel steps a switch or a menu |
| Keyboard focus | click a control or Tab / Shift-Tab; the arrows step the value (Shift: by 10), Page Up / Down by 10, Home / End, Delete the default, Enter opens a menu, Esc closes a menu, SETTINGS or the focus. Other keys go to the host |
| The keys | play notes (lower on the key: louder); held notes light up, the host's too |
| SUSTAIN, EFFECTS | latch the sustain (CC 64); bypass every effect (the app's FX button, CC 111). An effect that is off, and every effect under BYPASS, draws its knobs inactive (dimmed; they still answer) and its tab's lamp out; MOTOR, the instrument's own, is never bypassed |
| The preset's name | PRESETS: every preset grouped by instrument (STAGE 77, SUITCASE, EARLY FELT, BELL DYNO, VIBES, MARIMBA), each group the factory presets then the user's, the one playing marked; a click loads one and leaves the sheet open, so the keys, SUSTAIN and EFFECTS try it at once; the wheel or the pager turns the pages of a long list; Esc, the close key or the preset's name close it. The arrows step through the presets as before |
| SAVE | a name field in the preset box, the playing preset's name selected: type to replace it (capitals, up to 24 characters), Backspace / Delete edit, Enter (or SAVE again) saves the sound as a user preset of that name, Esc or a click elsewhere leaves it. REPLACE in the field: a user preset of that name exists and is overwritten. While the field is open the window keeps every key |
| The gear | SETTINGS |

Every change goes to the host as a parameter change with a gesture (its undo and automation recording see one
move per drag); host automation moves the knobs. The preset box, its arrows and UNDO, SAVE, the SETTINGS values,
MIDI learn, TUNE, SUSTAIN and EFFECTS are the plugin core's own (`plugin/frp_ui_iface.h`): saved with the project,
the same as the host's view and MIDI show. SAVE stores the sound as a user preset under the name typed in its field
(a factory sound saved under its own name becomes a user file of that name). The window's size is saved with the
project once the window was resized, and the window reopens at it (a project from an older build opens at the size
the window has).

## What it does

| | |
| --- | --- |
| Engine | HQ only: every engine (the Rhodes types, VIBES, MARIMBA) on its HQ float path and every effect on its HQ path (the full-rate reverb TYPEs, the compressor kept through EFFECTS BYPASS: the Move's and the two-core FM-1's build switches), with **24 voices on every engine**. 24 keys held all sound; a 25th steals a voice (faded out over one 5.8 ms block: no click). The FM-1's own 10-voice integer build (CLASSIC, an ENGINE parameter until 2026-11) is not in the plugin any more: a saved state or project that asks for it loads its sound on HQ |
| Parameters | the app's 67 parameters with their FM-1 ids (stable: saved sessions and automation keep them), ranges and texts, grouped by module, plus TUNE, PRESET (F01-F57, for the host's own view), SUSTAIN (the latch), EFFECTS BYPASS and VELOCITY RESPONSE (the settings' RESPONSE, as CC 40: DEEP or EVEN) (72 in all; the id 0x10000, ENGINE's, stays reserved). A change the app makes itself (a program change, a CC, a preset) goes back to the host. Automation takes the device's own path: a sweep renders the same bytes as the same sweep sent as MIDI CCs, so the smoothing is the FM-1's |
| Presets | F01-F19 (the FM-1's) and F20-F57 (the Move's variants), then the user's own, one file each (`*.frpreset`) in `~/Library/Application Support/Frodhes/Presets` (Linux `$XDG_DATA_HOME/frodhes/presets`, else `~/.local/share/frodhes/presets`; Windows `%APPDATA%\Frodhes\Presets`; `FRODHES_PRESET_DIR` overrides; the folders are made when the first preset is saved). In a sandboxed Audio Unit host the plugin uses the account's own folder when the sandbox lets it write there (the same presets as every other host), else the host's container (that host's presets only). The plugin-wide settings file has its place beside the presets (`settings.json`, `plugin/src/frp_paths.h`). Browse with wrap-around, UNDO as on the device. Program changes follow the FM-1's map, continued past F19 so that every factory preset can be reached: 0-15 F01-F16, 16-31 the first 16 user files (where there is none, the FM-1's own user bank: its starter sounds), 32-72 F17-F57 (32-34 are F17-F19 as on the FM-1, which has no preset after F19; 35-72 reach F20-F57 by the same rule). 73-127 load nothing |
| Settings | MIDI CHANNEL (ALL or 1-16), the velocity curve (MEDIUM as played, LIGHT, HEAVY: the device's TOUCH curves; FIXED with its velocity), program changes on / off, the tempo source (HOST: the DAW's tempo, 120 without one, a MIDI clock sent to the plugin still leads; INTERNAL: the BPM setting; MIDI CLOCK: an incoming clock, else the BPM setting), SYNC ALL (every tempo effect's SYNC at one division, also on every preset load). The sheet's title band shows VERSION, read only: a release's X.Y.Z, a developer build DEV and 4 hex digits of its commit, as the FM-1's MIDI page |
| MIDI learn | any knob (and TUNE, PRESET, SUSTAIN, BYPASS, VELOCITY RESPONSE) learns the next CC; a learned CC comes before the device's CC map; forget one or all; saved with the project |
| State | the sound as the Move saves it (the 256-byte settings record and the sound it came from) plus the plugin's own keys (settings, learned CCs, SUSTAIN, EFFECTS BYPASS, the preset's name; version "pv" 3: "win", the window's size, once it was resized). A state without them (the first builds, a Move track) loads them as defaults (without "win": the window keeps its size); older builds ignore the keys they do not know; a broken or cut state is refused and changes nothing. The "engine" key is always written as HQ (an older build then loads the sound on its HQ engine) and never read: a state saved with ENGINE CLASSIC loads on HQ, 24 voices, and saves again as HQ |
| Tempo | the tempo source above (the delay's and the LFOs' sync) |
| Sample rates | the engine always runs at 44.1 kHz, the same integers as the FM-1. 44.1 kHz: its own samples. 88.2, 176.4 and 352.8 kHz: 2x half-band stages; 48, 96, 192 and 384 kHz: a 160/147 polyphase stage to 48 kHz, then 2x half-band stages (`plugin/src/frp_os.h`, linear phase). Flat to 20 kHz within 0.0001 dB; every image and alias of a tone up to 20 kHz at least 117 dB under it (`tools/plugin_os.py`: targets 0.05 dB and 100 dB). The engine's own top band (20 to 22.05 kHz) is rolled off by the first stage, as on any 44.1 kHz source. Any other rate from 1 to 1536 kHz: a windowed-sinc resampler (error under 1 LSB of 16 bits in the passband). The FM-1's own 48 kHz USB stream, bit for bit, is kept as a test mode (`FRODHES_PLUGIN_TEST=fm1`) |
| Tail | reported to the host (CLAP tail; the VST3's tail samples and the Audio Unit's tail time follow), and the host is told when it changes: how long the sound can go on after the last event at the current settings, an upper bound measured per setting: the notes' release (under 2 s), the reverb by TYPE and DECAY (HALL up to 8 s, PLATE 7 s, INFINITE up to 59 s below SUSTAIN), the delay by FEEDBACK scaled by its TIME (a synced time counts as the longest), added up with a quarter more. INFINITE at SUSTAIN / FREEZE and the ANA / TAPE echoes that build up (FEEDBACK 115 / 114 and over) report an infinite tail. FX BYPASS: the notes' only |
| Latency | reported to the host, exact: 258 frames at 44.1 kHz (5.9 ms), 325 at 48 (6.8 ms), 602 at 88.2, 698 at 96, 1222 at 176.4, 1416 at 192 kHz (6.8 to 7.4 ms). Every note sounds exactly that long after its own sample, wherever it falls in the host's buffer and whatever the buffer size (`tools/plugin_timing.py`: within 1 frame at every rate; measured within 0.3 frame). The engine computes 256 frames at a time, and a note starts at its own sample inside that block; all of a block's events must be known before it is computed, so the latency is one block (5.8 ms) + 2 frames + the oversampler's delay (none at 44.1 kHz, 0.9 to 1.6 ms at the others). Controllers, parameter changes, note-offs and the pedal act from the start of the block that holds their sample (within 5.8 ms), as on the FM-1. A note that must wait for a stolen voice's fade starts with the next block, as on the FM-1. A host can ask at any time: before the first activation the answer is 48 kHz's (325; a plugin learns the rate only when it is activated), after one the last rate's; activating at another rate changes it there and tells the host (CLAP latency.changed; the VST3 then gets kLatencyChanged, the Audio Unit a Latency property change). The VST3 answers while inactive too (plugin/cmake/deps.cmake builds clap-wrapper's VST3 with that one change, in a copy) |
| Instances | up to 16 in one host process; a 17th stays silent and says so in the host's log |
| CPU | one instance at its worst (24 keys struck at velocity 127 with the pedal down, struck again every 2 s, every effect on at its heaviest, each REVERB TYPE, every engine) takes under 2 % of one core of the maintainer's arm64 laptop (2024) at 44.1 and 48 kHz, in every format: CLAP 1.7 %, VST3 1.9 %, Audio Unit 1.7 % (`tools/plugin_cpu.py`; the 16-voice HQ build before: CLAP 1.2 %). Per rate (CLAP, the worst scenario; measured on a busy machine, so higher than the figures above): 44.1 kHz 2.4 %, 48 kHz 2.8 %, 88.2 kHz 2.8 %, 96 kHz 3.1 %, 176.4 kHz 2.8 %, 192 kHz 3.1 %: the oversampler itself takes 0.3 to 0.8 % of a core (`tools/plugin_os.py`). The app computes 256 frames at a time, so a host buffer under 256 frames gets the whole cost in one call of four or eight: at 32 frames the worst call took 0.75 of its 0.67 ms there (64 frames: 0.17); a slower machine wants a host buffer of 64 frames or more |
| Floods | a host may send thousands of parameter, program or controller changes in one block (pluginval does): none is dropped. When the event queue runs low, the changes to one parameter (or one controller of a channel) wait in one entry that keeps the latest value, which is all the app would have played: it applies everything due before each block. Notes are never merged |
| The UI core | `plugin/frp_ui_iface.h`: everything the plugin's own window shows and does (parameters with gestures, presets, settings, MIDI learn, SUSTAIN, BYPASS, meters, held notes), main thread only, never blocking the audio |

Not in this build: the FM-1's sequencer, arpeggiator and chord buttons (their CCs are ignored, as on the Move), the
host's transport start / stop, presets in the host's browser.

## Windows and Linux builds

    tools/plugin_win.sh setup   # once: clang-cl + lld-link (Homebrew llvm@20, lld@20) and the Windows SDK + MSVC CRT
                                # through xwin into .local/win-toolchain (xwin asks you to accept Microsoft's licence)
    make plugin-win      # Windows x64: build, dependency check, the tests under wine (Docker), frodhes-plugin-windows.zip
    make plugin-linux    # Linux x86_64 + aarch64 in Docker: build, dependency check, the Xvfb tests, the zips

- **Windows** builds with clang-cl + lld-link and the static C runtime (`/MT`, `plugin/cmake/win-x64-clangcl.cmake`):
  cross from macOS over the xwin SDK, natively on Windows (CI) with the installed LLVM and Visual Studio's SDK. The
  binaries import only Windows' own DLLs: nothing to install beside them (no Visual C++ redistributable). The app's
  main loop runs in a fiber there (`plugin/src/coro/coro.h`); `plugin/compat/win/` holds the few POSIX shims. The
  wine tests (`tools/plugin_win.sh test`) run the coroutine, the window's controller and view, the headless host's
  selftest and a Gate D subset: a smoke test, not Windows itself (CI runs the same on windows-2022).
- **Linux** builds in Docker on Ubuntu 20.04 with clang 12 (`tools/plugin_linux.sh`, `plugin/tests/docker/linux.Dockerfile`):
  glibc 2.31 is the oldest the bundles need, so they load on Ubuntu 20.04, Debian 11, Fedora 32 and everything
  newer. libstdc++ and libgcc are linked in; only the entry points are exported. `FRP_LINUX_ARCH=aarch64` builds
  the ARM version.

## Packages

`tools/plugin_pack.sh` makes the packages a release attaches, under fixed names (the version is only in the GitHub
release, as for the firmware):

| Package | Contents | Installs to |
| --- | --- | --- |
| `frodhes-plugin-macos.pkg` | the three bundles, universal; one choice a format | `~/Library/Audio/Plug-Ins/CLAP`, `VST3`, `Components` (the user's own Library: no administrator password) |
| `frodhes-plugin-macos.zip` | the same bundles, `INSTALL.txt` | copied by hand to the same folders |
| `frodhes-plugin-windows.zip` | `CLAP\Frodhes.clap`, `VST3\Frodhes.vst3\` | `C:\Program Files\Common Files\CLAP` and `VST3` (CLAP also `%LOCALAPPDATA%\Programs\Common\CLAP`) |
| `frodhes-plugin-linux-x86_64.zip`, `-aarch64.zip` | `Frodhes.clap`, `Frodhes.vst3/` | `~/.clap`, `~/.vst3` (or `/usr/lib/clap`, `/usr/lib/vst3`) |

Every package carries `LICENSE.txt` (the GPL), `SOURCE.txt` (where the source is) and `THIRD-PARTY-NOTICES.txt`
(the licence texts of everything compiled into the binaries, from the pinned checkouts); the macOS bundles and the
VST3 bundles also carry the two licence files in `Contents/Resources`.

    make plugin-pack     # macOS: build/plugin-pkg/frodhes-plugin-macos.pkg and .zip (after make plugin)

**Self-contained.** `tools/plugin_depcheck.py` fails a build whose binaries need anything beyond the operating
system: macOS only `libSystem`, `libc++`, `libobjc` and system frameworks, universal, macOS 11; Windows only its own
DLLs (`kernel32`, `user32`, `gdi32` ...; the C runtime is linked in); Linux only `libc`,
`libm`, `libdl`, `libpthread`, `librt`, `libX11` and `libxcb`, no symbol newer than glibc 2.31. It also checks that
each format exports its entry point. The binaries carry no path of the machine that built them (the SDKs' source
paths are written as `deps/...`, the `.pdb` by its name only) and the Linux ones are stripped. The bundles' version
(Info.plist, the Audio Unit's component version, which hosts compare to rescan an update) is the release's X.Y.Z.

**CI** (`.github/workflows/plugin.yml`, on every pull request that touches the plugin or the code it compiles): macOS
builds, runs `make test-plugin`, packages, installs the `.pkg` with `installer` into the runner's home and runs the
dependency check, clap-validator, pluginval (strictness 10) and `auval -strict` on what was installed; Windows
builds with clang-cl, packages, unpacks the zip and runs the dependency check, the coroutine test, the window's
controller and view self-tests, the headless host's selftest, clap-validator and pluginval; Linux runs
`tools/plugin_linux.sh` for x86_64 and aarch64 (build, check, the Xvfb tests with a Gate D subset, the zip), then loads the x86_64 zip on a current Ubuntu with clap-validator (and pluginval,
not blocking). A release tag runs the same workflow (release.yml's job `plugin`) and attaches the packages after the
release is public, only for the platforms the release commit records as tested, in a line
`Plugin tested: macos windows linux-x86_64 linux-aarch64` (`tools/plugin_asset.sh`): no line, no plugin asset.
The plugin never holds back the firmware's or the Move's release.

## Signing (macOS)

Today every bundle is signed **ad hoc** (`codesign -s -`) and the `.pkg` is unsigned. macOS then treats them as
coming from an unidentified developer: the user opens the installer once from System Settings > Privacy & Security >
Open Anyway, or, for the zip, runs `xattr -dr com.apple.quarantine` on the copied bundles. Hosts load the bundles
either way (Audio Unit hosts load the ad-hoc signed Audio Unit too).

Signing with an Apple Developer ID removes those steps. It needs the Apple Developer Program (individual, 99 USD a
year) on the maintainer's own Apple account, so it is not set up yet. When it is:

1. In the Apple Developer account create two certificates: **Developer ID Application** (the bundles) and
   **Developer ID Installer** (the `.pkg`). Export both, with their private keys, from Keychain Access as one `.p12`
   with a password.
2. Create an app-specific password for the Apple ID (appleid.apple.com > Sign-In and Security) for `notarytool`.
3. Locally: `xcrun notarytool store-credentials frodhes --apple-id <id> --team-id <TEAMID> --password <app password>`,
   then
   `FRP_SIGN_ID="Developer ID Application: <Name> (<TEAMID>)" FRP_INSTALLER_ID="Developer ID Installer: <Name> (<TEAMID>)" FRP_NOTARY_PROFILE=frodhes make plugin-pack`:
   the bundles are signed with the hardened runtime and a secure timestamp, the `.pkg` is signed, and both the `.pkg`
   and the zip's bundles are notarized and stapled (`tools/plugin_pack.sh`).
4. For releases, add the repository secrets `MACOS_DEVID_P12_BASE64` (`base64 -i devid.p12`),
   `MACOS_DEVID_P12_PASSWORD`, `MACOS_SIGN_ID`, `MACOS_INSTALLER_ID`, `MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_TEAM_ID`
   and `MACOS_NOTARY_APP_PASSWORD`. release.yml's job `plugin` then signs and notarizes (plugin.yml's step
   "Developer ID"); without them it stays ad hoc and says so in the log.
5. Check a signed package: `pkgutil --check-signature frodhes-plugin-macos.pkg`, `spctl -a -vv -t install
   frodhes-plugin-macos.pkg`, `codesign -dv --verbose=4 Frodhes.component`, `xcrun stapler validate`.

Windows binaries are not code-signed (a zip without an installer rarely meets SmartScreen; Windows may mark the
files as downloaded: Properties > Unblock). Linux needs no signing.

## How it is checked

- **Gate D** (`tools/gate_d.py`): every MIDI-driven render of the test suite, played through the CLAP plugin by a
  headless host (`plugin/tests/clap_render.c`), is byte-identical to the host build's (the same list as the Move's
  Gate M, and the HQ scripts) against the host build at the plugin's switches (24 voices, HQ on every note); with
  random block sizes, 1-frame blocks and CLAP note events too; 48 kHz against the FM-1's resampler compiled
  unchanged; every other rate (the oversampler, the windowed sinc) against an FFT reference within the band; the
  Intel slice under Rosetta. These comparisons run the plugin in its test mode with the host build's event time (the
  millisecond grid); the core mode runs it as it ships.
- The oversampler (`tools/plugin_os.py`, `plugin/tests/os_test.c`): 64 tones from 20 Hz to 20 kHz at every rate of
  the 44.1 and 48 kHz families: the level within 0.05 dB, every other component 100 dB or more under the tone, the
  tone exactly where the reported latency puts it.
- The event timing (`tools/plugin_timing.py`): a Rhodes, a VIBES and a MARIMBA note played again 1 to 301 frames
  later (across the engine's blocks) at 44.1 to 192 kHz comes exactly that much later (within 1 frame); at each rate
  it sounds where it does at 44.1 kHz (the reported latency is the true one) and nulls against the 44.1 kHz render
  within the band; random host buffer sizes give the same bytes.
- The headless host's selftest: every parameter value's text reads back as itself, the state saves, loads and saves
  to the same bytes, the rates and latencies, 16 instances play and the 17th is silent.
- Gate D's core mode (`tools/gate_d.py --only core`, also `tests/py/test_plugin_core.py`): the headless host's
  `--coretest` drives the UI core through a CLAP extension (24 voices on STAGE 77, VIBES and MARIMBA: 24 keys
  struck one by one, each one a new voice sounding and held, none stolen; the 25th steals one; the state with every
  setting, older, CLASSIC and broken states; presets, user files, UNDO, program changes; automation and gestures; MIDI
  learn; the tempo sources and SYNC ALL; SUSTAIN, BYPASS, velocity, MIDI CHANNEL); an automation sweep equals the
  same sweep as MIDI CCs byte for byte; a 1/4 delay's echo lands at 600 / 500 ms at the transport's 100 / 120 BPM.
- The 24 voices heard (`tests/py/test_plugin_voices.py`, the engine alone): a 24-key chord is the 23-key chord plus
  its top key alone (nothing stolen for it), and a 25th key over 24 held steals cleanly: against the same music
  without the steal, its largest second difference stays under 1.5 x (a voice cut dead fails it, proven on the same
  renders). On the host build, `make testv V=24` (`tests/scripts24`) holds 24 keys per engine with the same counts.
- The user folders per OS (`plugin/tests/test_paths.c`, `tests/py/test_plugin_paths.py`): macOS and its App
  Sandbox, Linux XDG, Windows APPDATA, drive and UNC roots, all tested on any OS; mkdir -p, the atomic replace (also
  over an existing file) and the listing ran on macOS, on Linux (Docker) and on Windows (Wine).
- clap-validator (the CLAP bundle), pluginval at strictness 10 (the VST3 and the Audio Unit, with its editor tests;
  the macOS CI runs them too, the Windows and Linux CI without),
  `auval -strict` (the Audio Unit) and the VST3 SDK's own validator (`tools/plugin_vst3_validator.sh`, built from the
  pinned SDK with the command line tools: 47 tests). `auval` passes with one warning, "Preset name is not retained in
  retrieved class data": it comes from clap-wrapper, not the plugin. Its Audio Unit writes the saved state's name from
  its own field, which only a restored state sets, and a name the host gives through the present preset never reaches
  it, so the class data it hands back is named "Program". The plugin's state (the CLAP state) carries no name; the
  sound and the settings come back whole. A host that names its own presets (most keep the name in their own file)
  is not affected.
- The tail (`tests/py/test_plugin_tail.py`, in `make test-plugin`): `clap_render --tail-check` is a host that stops at
  the tail the plugin reports; with the measured worst corners and seeded random settings, it renders 20 s
  past the tail and nothing sounds there; the settings that hold the sound report an infinite tail. A changed tail is
  told once it has held for two checks, in process (CLAP's audio thread); as clap-wrapper's host (the VST3 and the
  Audio Unit) from the main thread, where their restart and property-change calls belong.
- In other hosts (`make plugin-hosts`, `tools/plugin_hosts.py`, macOS): the Audio Unit in Apple's own AU host
  (AVAudioEngine offline, `tools/plugin_au_render.swift`, every event at its frame) renders a host script sample for
  sample as the CLAP render does, at 44.1 and 48 kHz; the VST3 and the Audio Unit in a JUCE-based host (pedalboard)
  match its level and loudness envelope (that host plays a probe note into every instrument it loads, so not sample
  for sample), and their state saves, loads and saves to the same bytes.
- The dry distortion (`make plugin-dist`, `tools/plugin_dist.py`, macOS; `tests/py/test_plugin_dist.py`): the 16
  scripts of the firmware's dry-distortion gate (`tools/dry_dist.py`: FXOFF / RAW x the four TYPEs x RESPONSE A / B,
  keys 28-93 at v64-127 and loud chords) played offline through the CLAP (the headless host), the VST3 (a minimal
  host on the VST3 SDK's hosting classes, `plugin/tests/vst3_render.cpp`: controllers through the plugin's MIDI
  mapping, as a DAW sends them) and the Audio Unit (Apple's AU host, registered for the run only, nothing installed);
  each format's H2..H6, inharmonic residue and chord intermodulation within 0.9.9's + 3 dB and H3 at most -25 dB at
  v127, and the three formats sample-identical, on the plugin's HQ sound. The WAVs and each format's cells (`tools/dry_dist.py --judge` reads them) land in `build/plugin-dist/out`.
- The stress run (`make plugin-stress`, `plugin/tests/clap_stress.m`, macOS): 30 minutes in a host that behaves like a
  DAW, 3 instances processed at real time on an audio thread while the main thread plays chords (full ones of 20 to 28
  keys too: every voice and steals),
  changes presets, floods 2000 automation changes into one block (every parameter then reads back the last value
  sent, except the pairs the app couples itself: an effect's depth and its switch, the compressor's THRESH and make-up),
  opens the window in a real NSWindow and closes it again, and round-trips the state between instances; every sample
  finite, every instance heard, no warning in the log, and the memory flat after a 3-minute warm-up.
- The window (`tests/py/test_plugin_gui.py`): `frp_gui_shot` opens it in a real window, drives it as a user does,
  automates and plays as a host does, resizes, closes and opens it; every picture it showed, read back from its own
  surface, is pixel for pixel what the reference renderer (`plugin/ui/tools/frui_render.c`) draws from the same state.
  It reads the window through the plugin's private gui-shot extension, offered only when `FRP_TEST_EXT=1` is set when
  the plugin loads (the test sets it, after checking a load without it offers none); a host never sees it.
  pluginval's editor tests open and close it. The window's core alone (`plugin/tests/test_gui_ctl.c`) and the
  Windows and Linux views in their own windows under wine and Xvfb (`plugin/tests/gui_win_demo.c`, `gui_x11_demo.c`,
  through `tools/plugin_win.sh test` and `tools/plugin_linux.sh test`) get the same input checks.
- The firmware build never sees `plugin/`: the plugin only reads `firmware/src`.

## Checking it in a DAW (by hand)

What a script cannot do: a real DAW's scan, its browser, its project save and its audio device. Five minutes a host,
with the packages a release attaches (the `.pkg` on macOS, the zips elsewhere). Close other audio software first; on
the Mac, a DAW that opens the audio interface may change its sample rate.

1. **Install and scan.** Install the package; rescan plug-ins (some Audio Unit hosts check new Audio Units only at
   start). Frodhes shows as an instrument, vendor Frodhes, version the release's: in a CLAP host's CLAP list, in a
   VST3 host's VST3 list (on each system), and in the Audio Unit host's list on macOS (Instruments > Frodhes >
   Frodhes).
2. **Play.** Put it on a MIDI / instrument track and play: the first sound is F01 STAGE 77 on the HQ engine. Hold a
   chord with the sustain pedal (or SUSTAIN in the window), let go: the notes ring out and stop. No click at the start
   of playback or when the track is armed.
3. **The window.** Open it: 960 x 600 points, sharp on a Retina / high-DPI screen. Drag a knob, double-click its value
   and type a number, step a preset with the arrows, open PRESETS from the preset's name and load one while playing
   the keys, SAVE under a typed name, switch EFFECTS to BYPASS (the effect knobs dim). Hold 24 keys (or the sustain
   and a glissando): every key sounds. Resize the window (16:10, 720 x 450 to 1920 x 1200). Close and reopen it
   twice: nothing is lost.
4. **Automation.** Record a knob move while the transport runs (the host shows one gesture); play it back: the knob
   follows. Draw an automation curve for TONE, play: the sound follows smoothly, no zipper noise.
5. **Presets and MIDI.** Choose F13 VIBES, then F15 MARIMBA from the window's PRESETS and from the host's view of
   PRESET. Send a program change (0 = F01). Right-click a knob > MIDI LEARN, move a controller: the knob follows.
6. **The project.** Save the project with a changed sound, a learned CC, SUSTAIN on and the window resized; close
   it, reopen it: the sound, the learned CC, the setting and the window's size are back. Duplicate the track: both instances play the same sound.
7. **Render.** Bounce / export 10 s with a chord at the end: the file is not silent, has no clicks, and the reverb
   tail is not cut at the end of the last note (extend the render by 3 s if the host asks for a tail length).
8. **Sample rates.** Switch the project between 44.1, 48 and 96 kHz while it plays: the pitch stays right, no noise.

Hosts on the maintainer's Mac (2026-11): two DAWs, one that loads VST3 and AU (no CLAP support) and one that loads
AU only (its version predates VST3 and CLAP support). The list above is the same in any other host; a host with
CLAP support may need CLAP enabled in its plug-in preferences first.

## Layout

| | |
| --- | --- |
| `plugin/CMakeLists.txt`, `plugin/cmake/` | the build: the pins, the 16 engine slots (the HQ build, 24 voices), the formats through clap-wrapper |
| `plugin/src/engine_unit.c`, `slot_bridge.h` | one engine slot: the app over the browser's platform layer, with every symbol private to the slot; its presets, MIDI learn, settings, state |
| `plugin/src/bridge.c`, `bridge.h` | the instance: its slot, time, events, the UI queue, the resamplers, the user preset files |
| `plugin/src/frp_os.h` | the oversampler: 160/147 polyphase and 2x half-band stages |
| `plugin/frp_ui_iface.h` | the UI core's interface (implemented in `frodhes_clap.c`) |
| `plugin/src/frodhes_clap.c`, `clap_entry.cpp` | the CLAP plugin; clap-wrapper turns it into the VST3 and the Audio Unit |
| `plugin/src/coro/` | the coroutine the app's main loop runs in (arm64, x86_64) |
| `plugin/gen/params_map.h` | the parameter table, generated by `tools/gen_plugin_params.py` from the app's own |
| `plugin/gui_core.c`, `gui_clap.c` | the window: its model, input and partial repaints; CLAP's gui extension |
| `plugin/gui_platform.h`, `gui_mac.m`, `gui_win.c`, `gui_x11.c` | the platform views behind one interface: macOS, Windows, Linux |
| `plugin/gui_host.h` | what the window's controller asks of its host (bound to `frp_ui_iface.h` in `gui_clap.c`) |
| `plugin/ui/` | the renderer, its fonts reader and value texts, the headless render tool |
| `plugin/tests/` | the headless host, the coroutine test, the parameter dump, the window's test host, the stress host |
| `tools/plugin_hosts.py`, `plugin_au_render.swift`, `plugin_vst3_validator.sh` | the plugin in other hosts (Apple's AU host, a JUCE host), the VST3 SDK's validator |
| `tools/plugin_cpu.py` | the CPU per instance at the worst case, per format and rate |
| `tools/plugin_os.py`, `plugin/tests/os_test.c` | the oversampler's passband, rejection and timing |
| `tools/plugin_timing.py` | the event timing at every rate (notes within 1 frame, the latency, the null against 44.1 kHz) |
| `tools/plugin_dist.py`, `plugin/tests/vst3_render.cpp` | the dry-distortion gate through every format (the VST3 render host) |
| `tools/plugin_deps.sh`, `plugin/deps.lock` | the pinned dependencies and test tools |
| `tools/plugin_pack.sh`, `plugin_depcheck.py`, `plugin_asset.sh` | the packages, the self-contained check, the release's choice of packages |
| `tools/plugin_win.sh`, `plugin/cmake/win-x64-clangcl.cmake`, `plugin/compat/win/` | the Windows build (clang-cl), its checks and wine tests |
| `tools/plugin_linux.sh`, `plugin/tests/docker/` | the Linux build in Docker (Ubuntu 20.04), its checks and Xvfb tests; the wine image |
| `.github/workflows/plugin.yml` | the plugin's CI, also run by release.yml |
