# Frodhes as a desktop plugin

The FM-1 firmware's sound engine as a CLAP, VST3 and Audio Unit (v2) instrument for macOS, and a CLAP and VST3
instrument for Windows and Linux. The plugin is the app
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
| Engine | **ENGINE** parameter: HQ (the default: the Move's float paths) or CLASSIC (the FM-1's integer engine, bit for bit). 16 voices on every instrument |
| Parameters | the app's 67 parameters with their FM-1 ids (stable: saved sessions and automation keep them), ranges and texts, grouped by module, plus ENGINE. A change the app makes itself (a program change, a CC) goes back to the host |
| State | the sound as the Move saves it (the 256-byte settings record and the sound it came from) plus ENGINE: a sound moves between a Move track and a desktop session |
| Tempo | the host's transport tempo (the delay's sync), 120 BPM without one |
| Sample rates | 44.1 kHz: the app's own samples. 48 kHz: the FM-1's own USB resampler, the FM-1's USB stream bit for bit. Any other rate from 1 to 768 kHz: a windowed-sinc resampler (error under 1 LSB of 16 bits in the passband) |
| Latency | reported to the host: 10 ms (441 frames at 44.1 kHz, 487 at 48 kHz, 11 ms or more at other rates). Events are placed on the millisecond the FM-1 would see them |
| Instances | up to 16 in one host process; a 17th stays silent and says so in the host's log |

Not in this build: the FM-1's sequencer, arpeggiator and chord buttons (their CCs are ignored, as on the Move), the
host's transport start / stop, presets in the host's browser, a window.

## Windows and Linux builds

Both need only CMake 3.21+ and Ninja on the build machine; the compilers are pinned like the other dependencies.

    make plugin-win      # Windows x64: build/plugin-win, build/plugin-pkg/frodhes-plugin-windows.zip
    make plugin-linux    # Linux (this machine's architecture) in Docker: build/plugin-linux, ...-linux-<arch>.zip

- **Windows** builds with [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) (clang, lld and the Universal C
  Runtime), the same compiler natively on Windows (CI) and as a cross compiler on macOS or Linux, so a local build
  is the release's build (`plugin/cmake/mingw-w64-x86_64.cmake`). The C and C++ runtimes are linked in: the binaries
  import only Windows' own DLLs, nothing to install beside them (no Visual C++ redistributable). The app's main loop
  runs in a fiber there (`plugin/src/coro/coro.h`).
- **Linux** builds in `ubuntu:20.04` with clang 12 (`tools/plugin_linux_env.sh`, `plugin/cmake/linux-clang.cmake`):
  glibc 2.31 is the oldest the bundles need, so they load on Ubuntu 20.04, Debian 11, Fedora 32 and everything
  newer. libstdc++ and libgcc are linked in.

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
DLLs (`kernel32`, `user32` ... and the UCRT's `api-ms-win-crt-*`, part of Windows 10 and 11); Linux only `libc`,
`libm`, `libdl`, `libpthread`, `librt`, `libX11` and `libxcb`, no symbol newer than glibc 2.31. It also checks that
each format exports its entry point.

**CI** (`.github/workflows/plugin.yml`, on every pull request that touches the plugin or the code it compiles): macOS
builds, runs `make test-plugin`, packages, installs the `.pkg` with `installer` into the runner's home and runs the
dependency check, clap-validator, pluginval (strictness 10) and `auval -strict` on what was installed; Windows
builds, packages, unpacks the zip and runs the dependency check, the coroutine test, the headless host's selftest,
clap-validator and pluginval; Linux builds x86_64 and aarch64 in Ubuntu 20.04 with the dependency check, the
coroutine test and the selftest, then loads the x86_64 zip on a current Ubuntu with clap-validator (and pluginval,
not blocking). A release tag runs the same workflow (release.yml's job `plugin`) and attaches the packages after the
release is public, only for the platforms the release commit records as tested, in a line
`Plugin tested: macos windows linux-x86_64 linux-aarch64` (`tools/plugin_asset.sh`): no line, no plugin asset.
The plugin never holds back the firmware's or the Move's release.

## Signing (macOS)

Today every bundle is signed **ad hoc** (`codesign -s -`) and the `.pkg` is unsigned. macOS then treats them as
coming from an unidentified developer: the user opens the installer once from System Settings > Privacy & Security >
Open Anyway, or, for the zip, runs `xattr -dr com.apple.quarantine` on the copied bundles. Hosts load the bundles
either way (Logic loads the ad-hoc Audio Unit).

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
  Gate M); with random block sizes, 1-frame blocks and CLAP note events too; HQ against the host build with HQ on;
  48 kHz against the FM-1's resampler compiled unchanged; the other rates against an FFT reference; the Intel slice
  under Rosetta.
- The headless host's selftest: every parameter value's text reads back as itself, the state saves, loads and saves
  to the same bytes, the rates and latencies, 16 instances play and the 17th is silent.
- clap-validator (the CLAP bundle), pluginval at strictness 10 (the VST3) and `auval -strict` (the Audio Unit).
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
| `plugin/tests/` | the headless host, the coroutine test, the parameter dump |
| `tools/plugin_deps.sh`, `plugin/deps.lock` | the pinned dependencies, test tools and the Windows compiler |
| `tools/plugin_pack.sh`, `plugin_depcheck.py`, `plugin_asset.sh` | the packages, the self-contained check, the release's choice of packages |
| `tools/plugin_linux_env.sh`, `plugin_linux_docker.sh` | the Linux build image (Ubuntu 20.04) and the local Docker build |
| `plugin/cmake/mingw-w64-x86_64.cmake`, `linux-clang.cmake` | the Windows and Linux toolchains |
| `.github/workflows/plugin.yml` | the plugin's CI, also run by release.yml |
