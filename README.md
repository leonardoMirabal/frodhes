# Frodhes

**A physically modelled Fender Rhodes and vibraphone for the M-VAVE FM-1.**

![Frodhes on the FM-1: the PERFORM, main, PHASER and COMPRESSOR pages](docs/manual/img/overview.png)

Frodhes turns the FM-1 into an electric piano and a vibraphone. Every note is computed live from the mechanics of the
instrument: on a Rhodes a hammer strikes a tine, the tine rings against its tonebar and a magnetic pickup turns the
motion into sound, with the bark of hard notes and the bell of the attack; on the vibraphone a mallet strikes a bar,
its tuned tube rings along and the motor makes it pulse. Five instrument types (Stage 77, Suitcase, Early Felt, Bell
Dyno and Vibes), a
reface CP-style effects chain (compressor, a resonant filter with its LFO, drive, tremolo / wah, phaser, chorus, delay,
reverb), tap tempo with MIDI
clock sync, USB-MIDI in and out and TRS MIDI in, chord mode, a pattern recorder (1–16 bar loops, quantize, overdub, undo / erase), an arpeggiator, scales, a sustain pedal, 8-voice polyphony and 14 factory plus 16 user presets. The screen shows one page at a time with
four cards, one for each of the FM-1's four knobs, drawn as meter rows (an amount fills its row, a choice lights one
segment per option); the instrument brings its own VOICE pages; the first page, PERFORM, puts the effects' dry / wet (or
anything you assign) on those four knobs for playing live.

**Try it in your browser: [the Frodhes emulator](https://leonardomirabal.github.io/frodhes/)**: the same firmware
code compiled to WebAssembly, with the FM-1's screen, knobs, buttons and keys (computer keyboard and Web MIDI too).

## Download

**Latest release: [Frodhes 0.9.2](https://github.com/leonardoMirabal/frodhes/releases/latest)**: the firmware file is
`frodhes-0.9.2.fwsc` under *Assets* on the release page (package identity `FM-1_958`, the same for every version).

Install it over USB:

```sh
pip install mido python-rtmidi
python3 tools/fm1_install.py frodhes-0.9.2.fwsc
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
| **FRODHES** (main) | TYPE | TONE (VIBES: MALLET) | BELL (VIBES: MOTOR) | KEY VEL | HOME twice |
| **VOICE 1/2** (VIBES: 1/3) | TONE (VIBES: STRIKE) | BARK (MALLET) | BELL (TUBE) | HAMMER (WEIGHT) | (SELECT) |
| **VOICE 2/2** (VIBES: 2/3) | DECAY (VIBES: RING) | RELEASE (DAMPER) | NOISE | TUNE | (SELECT) |
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
| **ARPEGGIO** | OFF / UP / DOWN / UP-DN / ORDER / RANDOM | RATE | OCTAVES | GATE | ARP (there: on / off; SEL: HOLD) |
| **LFO** | SHAPE | RATE | SYNC | RETRIG | (SELECT) |
| **SEQUENCER** | BARS 1–16 | QUANT | SWING | EDIT (UNDO / ERASE) | SEQ (there: REC, hold: CLEAR; PLAY: start / stop) |
| **ASSIGN** | what K1 plays | what K2 plays | what K3 plays | what K4 plays | (SELECT) |
| **SETUP** | DELAY TONE | LEVEL | USB VOL (KNOB / FIXED) | | GLO twice |
| **MIDI** | CHANNEL (ALL / 1–16) | TRS IN (ON / OFF) | CLOCK (INT / EXT) | | GLO three times |

**SELECT** turns the pages, **K1–K4** turn the cards (on **PERFORM**: the effects' dry / wet, or what you assign), **ALGORITHM** picks the TYPE, **PRESETS** browses sounds,
**FX** switches all effects off / on, **EDIT** chord mode, **SEQ** the sequencer, **ARP** the arpeggiator, **PLAY** taps the tempo (everywhere but SEQUENCER, where it starts / stops), **REC** is the sustain pedal, **SAVE** stores a preset. The top line shows the battery next to the tempo.

## Build

Frodhes is built on [Dinghy](https://github.com/zvenson/dinghy) (zvenson), which is built on the platform of
[Felucca](https://github.com/hugelton/Felucca) (Leo Kuroshita, Hügelton Instruments) as
[SLOOP](https://github.com/isod89/sloop-fm1) (isod89) carries it. That platform handles updates, the USB rescue,
USB-MIDI, USB audio, the panel, the screen and settings.

```
firmware/src/frodhes.c    <- the app: keys, MIDI, sustain, settings, the main loop
firmware/src/rhodes.c     <- the voice engine: tine and tonebar, hammer, pickup, damper
firmware/src/fx.c         <- the effects and the master (limiter, silence gate)
firmware/src/ui.c         <- the pages;  params.c the parameters;  tempo.c tempo and scales;  presets.c
firmware/gen/             <- tables generated by tools/gen_rhodes.py and tools/gen_fx_tables.py
firmware/src/platform.c   <- Dinghy's plumbing (boot, USB, updates, audio ISR, settings)
```

You need Python 3, JieLi's toolchain and the JieLi AC79 SDK; on macOS also Docker (the toolchain is Linux x86-64
only). Everything installs inside the project, in `.local/` (git-ignored):

```sh
make setup        # .local/venv, the JieLi toolchain, the AC79 SDK files (sha256-checked)
make test         # the host scripts in tests/scripts
make pytest       # the Python tests (generators, the C engine against its reference, effects)
make host         # render build/host/frodhes.wav and screen PNGs on the computer, no FM-1 needed
./build.sh        # build/frodhes.fwsc;  ./build.sh --release 0.9.2 -> build/frodhes-0.9.2.fwsc (always FM-1_958)
```

Testing on the real FM-1 over USB: `tools/hil_scan.py` plays it over MIDI and analyses its USB audio (keys,
velocity, every effect, presets); `tools/hil_jack.py` compares the headphone jack, recorded with an audio interface,
against the USB audio. `tools/manual_shots.py` renders the manual's screenshots. `web/build.sh` builds the browser
emulator (Emscripten; `web/web_hal.c` stands in for the hardware) into `web/dist/`.

Frodhes changes Dinghy's platform files in a few small places: the app may allow its flash sectors (presets and the
sequencer's pattern, `APP_FL_RANGE`), drive the codec to full scale (`APP_OUT_SHIFT`), send the USB audio before the
MASTER knob (`usb_pre_master`), the MASTER knob reading is smoothed, the TRS MIDI input is on (`FELUCCA_UART`, with
Felucca's `midi_uart.c`), it builds for the browser (`DINGHY_WEB`), and a host-mode fix in `gfx.c`. Dinghy stays the
`upstream` remote: `git fetch upstream && git merge upstream/main`.

[docs/API.md](docs/API.md) describes what the platform provides; [docs/RULES.md](docs/RULES.md) lists the chip's
rules (no FPU, no 64-bit division, a 5.8 ms audio budget per 256 frames) and the checks the build runs.

## License

GPL-3.0-only, the same as Dinghy, Felucca and SLOOP. See [LICENSING.md](LICENSING.md).
