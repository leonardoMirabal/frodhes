# Frodhes on the Akai MPC

Frodhes runs on Akai MPC standalone hardware as an instrument plugin: the desktop plugin's engine (the same
electric piano, vibraphone and marimba as on the FM-1, the same effects, the higher-quality version of each) in the
MPC's plugin format, a VST 2.4 plugin for its 32-bit and 64-bit ARM models, built with the
[MPC Plugin SDK](https://github.com/inmusic-software/mpc-plugin-builder-sdk). It plays 16 voices on every instrument
(as on the Move) and has the factory presets F01-F57 as its programs.

**Status: runs on an Akai MPC** (checked by a tester on their MPC: it loads, plays and its pages and Q-Links work).
Everything below is also tested on a computer: in the SDK's host simulator (a host that behaves like the MPC's), in
our own VST 2.4 host, and on Linux ARM in Docker. If you try it on an MPC, please report back (the list is in the
zip's `INSTALL.txt` and [below](#what-to-report-back)).

![The VOICE page on the MPC's screen: the aluminium plate, two panels of four knobs](manual/img/mpc/mpc-voice.png?v=ab028bbc)

## Contents

1. [What you need](#what-you-need)
2. [Install and remove](#install-and-remove)
3. [The pages and the Q-Links](#the-pages-and-the-q-links)
4. [What it does](#what-it-does)
5. [Build it](#build-it)
6. [How it is built](#how-it-is-built)
7. [Tests](#tests)
8. [CPU and memory](#cpu-and-memory)
9. [What to report back](#what-to-report-back)

## What you need

- A standalone MPC: the 32-bit models (MPC X, X SE, Live, Live II, One, One+, Key 61, Key 37, and the Force) load
  `frodhes-arm32.so` (checked by a tester on an MPC Live); the 64-bit models (MPC Live III, MPC XL) load
  `frodhes-arm64.so`. The MPC Studio and the desktop MPC software are not targets.
- MPC OS 3.10.1 or later.
- **Preferences > Enhancements (Beta)**: switch on **Enable Enhancements** and **External Plugin Support**.
- A USB drive, an SD card or the MPC's internal drive.

## Install and remove

1. Download
   [`frodhes-mpc.zip`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-mpc.zip) and
   unzip it. Copy the folder `Frodhes - VST - Frodhes` into a folder named `VST2 Plugins` at the top level of the
   drive (create it if needed):
   `<drive>/VST2 Plugins/Frodhes - VST - Frodhes/plugin.json`. Keep the folder's name as it is: the MPC finds the
   plugin's pages by it.
2. Insert the drive; if Frodhes does not show, restart the MPC (it scans at start).
3. Make a plugin program or track and choose **Frodhes** under the external plugins.

To remove it, delete the folder and restart the MPC. If the MPC freezes when the plugin loads, power it off, take out
the drive that holds the folder, start the MPC and delete the folder on a computer. Save your work before you try it:
a plugin runs inside the MPC software.

## The pages and the Q-Links

The screen is the desktop plugin's look, drawn for the MPC: a rounded brushed-aluminium plate with the FRODHES
wordmark and the page's name on its rail, round a black body that holds two panels, one above the other. Each panel
is one group of four Q-Links: its title (with a lamp when the section has a switch), its switches in its header as on
the plugin (touch them; a menu opens with a tap), and four cards in a row, each a large knob (the plugin's knob: a
black skirt, a spun-aluminium cap, a scale whose ticks light up to the value) with its name above and its value
below, or a segmented switch. The MPC draws its own header (track, plugin, preset) above and its function keys below.

Eight pages on the MPC's six function keys (press a key again for its next page):

| Key | Page | Top panel (Q-Links 13-16) | Lower panel (Q-Links 9-12) |
|---|---|---|---|
| 1 | VOICE | VOICE, how the note is struck: TONE, BARK, BELL, HAMMER | VOICE, how notes die away: DECAY, RELEASE, NOISE, TUNE |
| 1 | PLAY | INSTRUMENT: TYPE, RESPONSE, SPREAD, LEVEL | PLAY: PRESET (a wide card), SUSTAIN, EFFECTS BYPASS |
| 2 | COMP EQ | COMP (OFF/ON, RATIO): THRESHOLD, ATTACK, RELEASE, MAKE-UP | EQ: LOW, MID, MID FREQ, HIGH |
| 2 | DRIVE | DRIVE: TYPE, AMOUNT, TONE, MIX | MOTOR (OFF/ON): DEPTH, SPEED, SHAPE, SYNC |
| 3 | FILTER | FILTER: TYPE, FREQ, RESO, LFO AMOUNT | FILTER LFO: SHAPE, RATE, SYNC, TRIGGER |
| 4 | MOD | TREM/WAH (OFF/TRM/WAH, PAN/AM): DEPTH, RATE, SHAPE, SYNC | PHASER (OFF/ON): DEPTH, SPEED, FEEDBACK, SYNC |
| 5 | DELAY | CHORUS (OFF/ON): DEPTH, SPEED, FEEDBACK, SYNC | DELAY (OFF/DIG/ANA/TAPE, SYNC): MIX, TIME, FEEDBACK, TONE |
| 6 | REVERB | REVERB: TYPE, MIX, DECAY, TONE | REVERB, the room: PRE-DELAY, SIZE, MODULATION, LOW CUT |

Every one of the 72 parameters is on a page. On an MPC with 16 Q-Links the top two rows of knobs play the two panels
(the knob next to a card turns it); on one with four, the knobs play the top panel first, and touching a card brings
its panel under them (the focus rectangle wraps the panel). A double tap on a card opens the MPC's large editor (a
list for TYPE, SYNC, PRESET and the other named choices).

<p align="center">
  <img src="manual/img/mpc/mpc-play.png?v=ecc85df1" width="384" alt="PLAY: the instrument, the preset, SUSTAIN and EFFECTS BYPASS">
  <img src="manual/img/mpc/mpc-comp_eq.png?v=1e691902" width="384" alt="COMP EQ: the compressor with its switch and ratio, the EQ">
  <img src="manual/img/mpc/mpc-drive.png?v=7f1d8c3a" width="384" alt="DRIVE: the drive and the motor">
  <img src="manual/img/mpc/mpc-filter.png?v=832bbdec" width="384" alt="FILTER: the filter and its LFO">
  <img src="manual/img/mpc/mpc-mod.png?v=0d472ba1" width="384" alt="MOD: tremolo / wah and the phaser">
  <img src="manual/img/mpc/mpc-delay.png?v=7bc9cbad" width="384" alt="DELAY: the chorus and the delay">
  <img src="manual/img/mpc/mpc-reverb.png?v=c4faa54c" width="384" alt="REVERB: the reverb and the room">
</p>

(The pictures are the pages composed on a computer, with the MPC's header and keys sketched round them; on the MPC the
values are in its own font.)

In Program mode the 16 Q-Links play the most-used controls: Q-Link 1-4 reverb mix, delay mix, tremolo depth,
level; 5-8 drive, filter cutoff, chorus depth, reverb decay; 9-12 decay, release, hammer, bark; 13-16 type, tone,
bell, noise.

The skin is hand-made, not generated by the SDK: `mpc/skin.json` holds the layout (the keys, the pages, the panels,
the cards, the Program-mode Q-Links) and `mpc/tools/make_skin.py` draws it into `mpc/skins/` (`TUI.json`,
`Q-Links.json` and the pictures), which `mpc/build.py` copies into the plugin folder as it is, after checking it still
matches the layout and the plugin's parameters. The pictures are painted with the desktop plugin's own drawing code
(`plugin/ui/frui_gfx.c`, its fonts, and copies of its plate, panel, knob, switch and lamp in
`mpc/tools/skin_draw.c`) at twice the plugin's size, so the MPC shows the plugin's parts; the same inputs give the
same files, byte for byte. To change the layout, edit `mpc/skin.json` and run
`python3 mpc/tools/make_skin.py --preview build/mpc/skin-preview` (after `make mpc`, which writes the parameters it
reads; Pillow for the previews). The previews are the pictures in `docs/manual/img/mpc/`, with `mpc-catalog.png`, the
VOICE page's 1280 x 640 crop for the plugin catalogue.

## What it does

- **Parameters**: the desktop plugin's 72, with the same names, ranges and order (`plugin/gen/params_map.h`): the
  app's 67 with their FM-1 values and texts, then TUNE, PRESET, SUSTAIN, EFFECTS BYPASS and VELOCITY RESPONSE. Their
  ids (`p<FM-1 id>`) are permanent, so saved projects stay valid. A change the app makes itself (a CC, a program
  change, a preset) moves the MPC's knob too.
- **Programs**: F01-F57. The MPC's program select, a MIDI program change and the PRESET parameter load the same
  preset.
- **MIDI**: notes and velocity, the sustain pedal, pitch bend, the FM-1's CC map, program changes, each at its
  sample in the block.
- **Tempo**: the MPC transport's tempo drives the synced effects (with no transport tempo the plugin plays 120).
- **State**: saved with the project: every parameter plus the desktop plugin's state text (the sound, the settings,
  MIDI learn, the preset's name). A project brings back the same sound, byte for byte.
- **Audio**: 44.1 kHz on the MPC (other rates through the plugin's resampler). The latency the plugin reports is
  258 frames (5.9 ms), as the desktop plugin's.
- MPC host details it handles: the host nudges parameter 0 (TYPE) on every prepare, so a TYPE write only acts when
  it holds for two blocks; the program 0 the MPC selects before restoring a project does not overwrite the restored
  sound; a resume after playing clears the notes and the effects' tails. A host write of the PRESET it already has
  changes nothing (a VST 2.4 parameter is a value, not an event); the desktop plugin would load that preset again.

## Build it

```sh
make mpc          # build/mpc: the plugin folder (both ARM builds, validated), frodhes-mpc.zip, the test build
make test-mpc     # + the desktop plugin, the 24-voice comparison build, tests/py/test_mpc.py
```

`mpc/build.py` fetches the SDK at its pinned commit into `.local/mpc-sdk` on first use (or uses `$MPC_SDK`) and finds
zig the SDK's way (`$ZIG`, `zig` on the PATH, or the SDK's own private venv; checked with zig 0.15.2). The three
framework headers compiled into the plugin are vendored in `mpc/sdk/` (MIT, with the SDK's licence and the commit in
`mpc/sdk/NOTICE.txt`); the build stops if they differ from the pinned SDK. Options: `--target all|native|arm32|arm64`,
`--version X.Y.Z`, `--voices N`, `--tools` (the ARM test hosts, for Docker), `-j N`.
The skin in `mpc/skins/` is made separately (above) and committed.

Outputs in `build/mpc/`: `Frodhes - VST - Frodhes/` (`plugin.json`, `version.xml`, `VST/frodhes-arm32.so`,
`VST/frodhes-arm64.so`, `Plugin Skins/`), `frodhes-mpc.zip` (the folder, `INSTALL.txt`, `LICENSE.txt`,
`SOURCE.txt`, `THIRD-PARTY-NOTICES.txt`; the same inputs give the same zip, byte for byte), `native/` (this
machine's build and test hosts) and `params.json`.

## How it is built

The engine is not forked: `mpc/src/frodhes_mpc.cpp` wraps `plugin/src/bridge.c` and the 16 slots of
`plugin/src/engine_unit.c` (the app `firmware/src/frodhes.c` unchanged) as `plugin/src/frodhes_clap.c` does for
CLAP. The sources and flags are the Linux desktop plugin's (`plugin/cmake/slots.cmake`, `tools/plugin_linux.sh`):
`-O2 -std=gnu11 -ffp-contract=off -fvisibility=hidden`, full LTO on the slots, glibc 2.31 (the libraries ask for
2.29 at most), the C++ runtime linked in, `-z noexecstack`, one exported symbol (`VSTPluginMain`). Where the SDK
makes it differ:

| | Desktop plugin (Linux) | MPC |
|---|---|---|
| Compiler | clang 12 + lld | zig 0.15.2 (clang 20, lld) |
| Targets | x86_64, aarch64 | `arm-linux-gnueabihf.2.31 -mcpu=cortex_a17`, `aarch64-linux-gnu.2.31 -mcpu=cortex_a55` |
| C++ runtime | libstdc++, static | LLVM libc++, static (zig's) |
| Coroutine | `coro_x86_64.S`, `coro_arm64.S` | + `plugin/src/coro/coro_arm32.S` (new: the 32-bit ARM build only) |
| Voices | 24 on every engine | 16 (`-DFR_VOICES=16u -DFRP_VOICES=16u`, the Move's `MOVE_DEFS`) |
| Format | CLAP (+ VST3 / AU through clap-wrapper) | VST 2.4 through the SDK's framework (`mpc/sdk/mpc_plugin.h`) |

The test build on macOS uses Apple clang without LTO (zig cannot link against macOS's own libraries there).

The 32-bit build is the one part with nothing to compare against on real hardware: the desktop plugin has no
32-bit ARM build. Its coroutine switch saves r4-r11, sp, lr and d8-d15 (the AAPCS callee-saved set) and is tested
under QEMU (1 million switches); its sound is the host build's, byte for byte, under QEMU (below).

## Tests

`tests/py/test_mpc.py` and `tests/py/test_mpc_skin.py` (`make test-mpc`; each test skips cleanly when its part is
not built):

- **The skin**: up to date with `mpc/skin.json` and the plugin's parameters; every control bound to a parameter
  that exists, under its name; every parameter on a page; every picture present, used, and fitting the bounds it is
  drawn in (the MPC does not scale pictures), every part of a control inside it; each page two blocks of four cards
  filling their focus rectangles, which the Q-Link maps agree with (13-16, 9-12, banked top row first); the panels
  covering three quarters of the screen; the pictures and the JSON drawn again byte for byte the same.

- **The SDK's checks**: `validate.py` on the folder (0 errors, 0 warnings); the host simulator `host_sim test`
  (the MPC's load sequence, the parameter-0 quirk, block sizes 32-512, a random parameter sweep, the state round
  trip with the re-saved chunk byte-identical, all notes off, timing): PASS, 0 warnings, on this machine's build and
  on both ARM builds in Docker.
- **Engines, sustain, programs, state, tempo**: a note on every TYPE (the four electric pianos, VIBES, MARIMBA) and
  the pedal holding it; the program select, a MIDI program change and the PRESET parameter giving the same sound;
  the state loaded over an instance in another state and saved again: the same bytes and values, and two loaded
  instances playing the same samples; a delay synced to 1/4 echoing a quarter note later at 100, 120 and 150 BPM.
- **Bit-exactness** (`mpc/tests/bitexact.py`): 84 scripts (Gate M's MIDI-driven entries: `tests/scripts`, the
  effect and calibration scripts, Gate D's list; and `tests/hq`), every one byte for byte:
  - the MPC build at 24 voices = the desktop plugin = the host build (in the plugins' host-time test mode), and the
    MPC build as it ships (16 voices) = the host build at the Move's switches;
  - as they ship: the MPC build at 24 voices = the desktop plugin at 44.1 and at 48 kHz, also with random block
    sizes on the MPC;
  - the shipped `frodhes-arm64.so` and `frodhes-arm32.so` on Linux (Docker; the 32-bit one under QEMU) = the host
    build.
- **Soak** (`mpc/tests/soak.py`): 30 minutes of a seeded random stream (notes, chords, runs, lost note-offs, the
  pedal, pitch bend, every CC the app knows, program changes, the host's writes of every parameter) in 5-minute
  segments, each with its own tempo, random block sizes and the parameter-0 nudge: no crash, every sample finite,
  exact silence after the closing panic (no stuck voice, tail or denormal residue), no output stuck on a rail, no
  stall; the same render on the shipped arm64 and arm32 `.so` (Docker, `--arm 6`) byte for byte the same; the same
  stream through the desktop plugin byte for byte the same, and its MIDI through the host build the same (so a click
  there is the firmware's own, which `tools/monkey.py` hunts).

On an MPC: a tester ran an earlier build (with the SDK's generated pages) on their MPC: it loads and plays, its pages
and Q-Links work. This skin has not been seen on an MPC yet (its widgets and file format are the ones the MPC's own
skins and the SDK's generator use); the MPC's CPU and memory figures are not measured.

## CPU and memory

`mpc/tests/cpu.py` measures one instance at the desktop plugin's worst case (`tools/plugin_cpu.py`) at 16 voices:
16 keys at velocity 127 with the pedal down, struck again every 2 s, every effect on at its heaviest, the strike at
its edge, on every TYPE and every reverb type, in 128-frame blocks at 44.1 kHz.

| Where | Average load (the worst case) | Per call, p99.9 |
|---|---|---|
| macOS arm64 laptop (2024), this machine's build | 0.9 % of a core (1.05 % on a busy machine) | 115 us of the block's 2902 us (203 us busy) |
| the shipped `frodhes-arm64.so`, Linux in Docker on that laptop | 1.0-1.1 % of a core | 335 us (Docker's scheduling noise in it) |

No MPC was measured. An estimate for the slowest one (the 32-bit models, Cortex-A17 class): the Move runs this
engine at 16 voices on a Cortex-A72, where 0.9.9's worst 128-frame callback took 573 us (20 % of the block); a
Cortex-A17 is roughly 1.5 to 2 times slower, so about 860 to 1150 us at the worst moment (30 to 40 % of a block)
and, from the ratio of average to p99.9 measured here (about 0.25 to 0.3), about 8 to 12 % of one core on average.
The SDK's guidance is "well under 15 % on a 32-bit MPC at 128-frame blocks". The engine computes 256 frames at a
time, so at a 128-frame buffer every second call carries the cost. Please report the MPC's CPU meter (below).

Memory: the library maps about 60 MB (10-12 MB of code and tables, 48.5 MB of zero-filled state for the 16 instance
slots, about 3 MB each, touched only when an instance takes its slot), as the desktop plugin's 16 slots.

## What to report back

1. The MPC model and MPC OS version (Preferences > Info).
2. Does Frodhes appear and load; do the pages show and the Q-Links move them?
3. The CPU meter with one Frodhes: about 10 voices (a held chord), then 16 (the pedal down, 16 keys or more) with
   REVERB TYPE HALL, the delay and the chorus on; if you can, 2 or 3 Frodhes at once.
4. Any clicks, crackles, dropouts, hanging notes or freezes, and what was playing (the preset, the keys, the audio
   buffer size).
5. Does a saved project bring the sound back exactly?

Reports: [issues](https://github.com/leonardoMirabal/frodhes/issues).
