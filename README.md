# Frodhes

**A physically modelled Fender Rhodes, vibraphone and marimba for the M-VAVE FM-1.**

![Frodhes on the FM-1: the FRODHES page (TYPE, TONE, BELL, KEY VEL)](docs/manual/img/piano.png?v=0.9.4)

Frodhes turns the FM-1 into an electric piano, a vibraphone and a marimba. Every note is computed live from the mechanics of the
instrument: on a Rhodes a hammer strikes a tine, the tine rings against its tonebar and a magnetic pickup turns the
motion into sound, with the bark of hard notes and the bell of the attack; on the vibraphone a mallet strikes a bar,
its tuned tube rings along and the motor makes it pulse; on the marimba a yarn mallet strikes a rosewood bar whose
long tube makes the low notes bloom. Six instrument types (Stage 77, Suitcase, Early Felt, Bell Dyno, Vibes and
Marimba), a
reface CP-style effects chain (compressor, a resonant filter with its LFO, drive, tremolo / wah, phaser, chorus, delay,
reverb), tap tempo with MIDI
clock sync, USB-MIDI in and out and TRS MIDI in, chord mode, a pattern recorder (1–16 bar loops, quantize, overdub, undo / erase), an arpeggiator, scales, a sustain pedal, 9-voice polyphony and 16 factory plus 16 user presets. The screen shows one page at a time with
four cards, one for each of the FM-1's four knobs, each read at a glance by its kind (an amount fills its row like a
fader, a selector lights one block per option up to its choice, an ON / OFF switch shows a lit dot), the value always
in the same large letters; the instrument brings its own VOICE pages; the first page, PERFORM, puts the effects' dry / wet (or
anything you assign) on those four knobs for playing live.

**Try it in your browser: [the Frodhes emulator](https://leonardomirabal.github.io/frodhes/)**: the same firmware
code compiled to WebAssembly, with the FM-1's screen, knobs, buttons and keys (computer keyboard and Web MIDI too).

## Screens

![The PERFORM, VOICE, PHASER and COMPRESSOR pages](docs/manual/img/overview.png?v=0.9.3)

Every page is in the
[manual](docs/MANUAL.md), with its screenshot.

## Download

**Latest release: [Frodhes 0.9.3](https://github.com/leonardoMirabal/frodhes/releases/latest)**: the firmware file is
`frodhes-0.9.3.fwsc` under *Assets* on the release page (package identity `FM-1_958`, the same for every version).

Install it over USB:

```sh
pip install mido python-rtmidi
python3 tools/fm1_install.py frodhes-0.9.3.fwsc
```

If anything goes wrong, switch the FM-1 off, hold **OCT−** and switch it on: that is the USB rescue, from which you
can always install again. To return to M-VAVE's firmware, use the "Official firmware" section of
[thefirmwarehub.com](https://thefirmwarehub.com) with V15 from m-vave.com.

## Manual

**[The user manual](docs/MANUAL.md)**: every page, button and knob, presets, effects, tempo, scales, sustain and
the MIDI map.

| Page | K1 | K2 | K3 | K4 | Button |
| --- | --- | --- | --- | --- | --- |
| **PERFORM** | TREM/WAH | PHASER | FILTER | DELAY | HOME (assignable on ASSIGN) |
| **FRODHES** (main) | TYPE | TONE (VIBES / MARIMBA: MALLET) | BELL (VIBES: MOTOR; MARIMBA: TUBE) | KEY VEL | HOME twice |
| **VOICE 1/2** (VIBES: 1/3) | TONE (VIBES / MARIMBA: STRIKE) | BARK (MALLET) | BELL (TUBE) | HAMMER (WEIGHT) | (SELECT) |
| **VOICE 2/2** (VIBES: 2/3) | DECAY (VIBES / MARIMBA: RING) | RELEASE (VIBES: DAMPER; MARIMBA: MUTE) | NOISE | TUNE | (SELECT) |
| **VOICE 3/3** (VIBES only: the motor) | MOTOR (ON / OFF) | DEPTH | SPEED | SHAPE | (SELECT) |
| **COMPRESSOR** | THRESHOLD (OFF / on) | RATIO | ATTACK | MAKEUP | (SELECT) |
| **FILTER** | OFF / LP12 / LP18 (default) / HP12 / HP18 / BP / NOTCH | FREQ | RESO | LFO | (SELECT) |
| **DRIVE** | OFF / ON | AMOUNT | TONE | MIX | (SELECT) |
| **TREM/WAH** | OFF / PAN / TREM / WAH | DEPTH | RATE | SHAPE | LFO (there: on / off) |
| **PHASER** | OFF / ON | DEPTH | SPEED | FEEDBACK | ENV (there: on / off) |
| **CHORUS** | OFF / ON | DEPTH | SPEED | SYNC | (SELECT) |
| **DELAY** | OFF / DIG / ANA | MIX | TIME | FEEDBACK | SEL (there: on / off) |
| **REVERB** | MIX | DECAY | TONE | PRE-DELAY | (SELECT) |
| **TEMPO** | BPM | TREM SYNC | PHA SYNC | DLY SYNC | GLO |
| **SCALE** | OFF / SNAP / DEGREE | SCALE | ROOT | TRANSPOSE | (SELECT) |
| **CHORD** | OFF / DIATONIC / DIA 7TH / MAJ ... MIN6 | INVERSION | VOICING | STRUM | EDIT (there: chord mode on / off) |
| **ARPEGGIO** | OFF / UP / DOWN / UP-DN / ORDER / RANDOM | RATE | OCTAVES | GATE | ARP (there: on / off; SEL: HOLD, on every page while the arp is on) |
| **LFO** | SHAPE | RATE | SYNC | RETRIG | (SELECT) |
| **SEQUENCER** | BARS 1–16 | QUANT | SWING | EDIT (UNDO / ERASE) | SEQ (there: REC, hold: CLEAR; PLAY: start / stop, tap tempo while the arp is on) |
| **ASSIGN** | what K1 plays | what K2 plays | what K3 plays | what K4 plays | (SELECT) |
| **SETUP** | DELAY TONE | LEVEL | USB VOL (KNOB / FIXED) | | GLO twice |
| **MIDI** | CHANNEL (ALL / 1–16) | TRS IN (ON / OFF) | CLOCK (INT / EXT) | | GLO three times |

**SELECT** turns the pages, **K1–K4** turn the cards (on **PERFORM**: the effects' dry / wet, or what you assign), **ALGORITHM** picks the TYPE, **PRESETS** browses sounds,
**FX** switches all effects off / on, **EDIT** chord mode, **SEQ** the sequencer, **ARP** the arpeggiator, **PLAY** taps the tempo (everywhere but SEQUENCER, where it starts / stops; while the arpeggiator is on it taps there too and **SEL** is its HOLD on every page), **REC** is the sustain pedal, **SAVE** stores a preset. The top line shows the battery next to the tempo.

## Build

Frodhes is built on [Dinghy](https://github.com/zvenson/dinghy) (zvenson), which is built on the platform of
[Felucca](https://github.com/hugelton/Felucca) (Leo Kuroshita, Hügelton Instruments) as
[SLOOP](https://github.com/isod89/sloop-fm1) (isod89) carries it. That platform handles updates, the USB rescue,
USB-MIDI, USB audio, the panel, the screen and settings.

```
firmware/src/frodhes.c    <- the app: keys, MIDI, sustain, settings, the main loop
firmware/src/rhodes.c     <- the voice engine: tine and tonebar, hammer, pickup, damper
firmware/src/bars.c       <- the bar instruments (VIBES, MARIMBA): bar modes, tube, mallet, damper / hand, motor
firmware/src/fx.c         <- the effects and the master (limiter, silence gate)
firmware/src/ui.c         <- the pages;  params.c the parameters;  tempo.c tempo and scales;  presets.c
firmware/gen/             <- tables generated by tools/gen_rhodes.py, tools/gen_bars.py and tools/gen_fx_tables.py
firmware/src/platform.c   <- Dinghy's plumbing (boot, USB, updates, audio ISR, settings)
```

You need Python 3, JieLi's toolchain and the JieLi AC79 SDK; on macOS also Docker (the toolchain is Linux x86-64
only). Everything installs inside the project, in `.local/` (git-ignored):

```sh
make setup        # .local/venv, the JieLi toolchain, the AC79 SDK files (sha256-checked)
make test         # the host scripts in tests/scripts
make pytest       # the Python tests (generators, the C engine against its reference, effects); in parallel
                  # with pytest-xdist (PYTEST_N=auto; PYTEST_N=0 or no xdist: serial)
make host         # render build/host/frodhes.wav and screen PNGs on the computer, no FM-1 needed
./build.sh        # build/frodhes.fwsc;  ./build.sh --release X.Y.Z -> build/frodhes-X.Y.Z.fwsc (always FM-1_958)
```

Testing on the real FM-1 over USB: `tools/hil_scan.py` plays it over MIDI and analyses its USB audio (keys,
velocity, every effect, presets); `tools/hil_jack.py` compares the headphone jack, recorded with an audio interface,
against the USB audio; `tools/hil_usb_onset.py` checks that every note reaches the USB recording as soon as the jack
(after a knob turn and a pause too: the automatic saves wait while a computer records). `tools/manual_shots.py` renders the manual's screenshots. `web/build.sh` builds the browser
emulator (Emscripten; `web/web_hal.c` stands in for the hardware) into `web/dist/`.

Releases are one command: `tools/release.py [patch|minor|X.Y.Z] [--dry-run] [--no-device] [--no-emulator] [--yes]`
picks the next version from the latest tag, checks the tree (on `main`, clean, not behind `origin/main`, the notes
in `docs/releases/vX.Y.Z.md` written and no longer marked DRAFT; a missing file gets a draft from the git log),
runs the host tests and the release build, installs it on the FM-1 and runs the CPU, scan and jack tests on the
Rhodes and VIBES (not yet MARIMBA), points this README and the manual at the new file, commits, asks before publishing, then pushes `main`,
waits for CI to pass on that commit, tags, waits for the release workflow and checks that the published `.fwsc` is
byte for byte the one tested, publishes the emulator and checks the live files. It stops at the first failure and
ends with a table of every step (done, skipped and why, failed); `--dry-run` runs the local checks only and stops
before anything is pushed.

Frodhes changes Dinghy's platform files in a few small places: the app may allow its flash sectors (presets and the
sequencer's pattern, `APP_FL_RANGE`), drive the codec to full scale (`APP_OUT_SHIFT`), send the USB audio before the
MASTER knob (`usb_pre_master`), the MASTER knob reading is smoothed, the TRS MIDI input is on (`FELUCCA_UART`, with
Felucca's `midi_uart.c`), it builds for the browser (`DINGHY_WEB`), and a host-mode fix in `gfx.c`. Dinghy stays the
`upstream` remote: `git fetch upstream && git merge upstream/main`.

[docs/API.md](docs/API.md) describes what the platform provides; [docs/RULES.md](docs/RULES.md) lists the chip's
rules (no FPU, no 64-bit division, a 5.8 ms audio budget per 256 frames) and the checks the build runs.

## License

GPL-3.0-only, the same as Dinghy, Felucca and SLOOP. See [LICENSING.md](LICENSING.md).
