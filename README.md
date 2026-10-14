# Frodhes

**A Fender Rhodes electric piano, physically modelled, for the M-VAVE FM-1.**

> **Status: bootstrap.** The firmware builds and plays, but the voice is a **placeholder**: a decaying sine,
> enough to try the voice handling, the damper and the sustain pedal. The physical model (hammer, tine,
> tonebar, pickup) comes next and goes into [`firmware/src/rhodes.c`](firmware/src/rhodes.c).

Frodhes is built on [Dinghy](https://github.com/zvenson/dinghy) (zvenson), which is built on the platform of
[Felucca](https://github.com/hugelton/Felucca) (Leo Kuroshita, Hügelton Instruments) as
[SLOOP](https://github.com/isod89/sloop-fm1) (isod89) carries it. That platform handles updates, the USB rescue,
USB-MIDI, USB audio, the panel, the screen and settings.

```
firmware/src/frodhes.c    <- the app: panel, MIDI (notes, CC 64 sustain), screen, settings
firmware/src/rhodes.c     <- the engine: the voices. Placeholder now, the physical model later
firmware/src/platform.c   <- Dinghy's plumbing (boot, USB, updates, audio ISR, settings). Don't touch
firmware/src/*.c, hal/    <- Felucca's platform, unchanged
docs/model/               <- notes and references for the physical model
```

## Playing it

| | |
| --- | --- |
| **Keys** | Play the piano, 8 voices. Notes are also sent out over USB-MIDI. |
| **OCT- / OCT+** | Set the octave (±2). It's saved to flash. |
| **REC** | Sustain pedal. Hold it to sustain, or tap it to latch sustain on and tap again to release. The LED shows the state. |
| **MASTER** | Set the volume. |
| **USB-MIDI in** | Note on/off with velocity. CC 64 is the sustain pedal. |

## Build

You need Python 3 with Pillow, JieLi's toolchain, and the JieLi AC79 SDK. On macOS you also need Docker, because
the toolchain is Linux x86-64 only and the build runs it in a container.

Everything installs inside the project, in `.local/` (git-ignored):

```sh
make setup        # .local/venv (requirements.txt), JieLi toolchain, the 3 AC79 SDK files (sha256-checked by the build)
make test         # every tests/scripts/*.txt on the host; their "expect" lines must pass
make host         # render build/host/frodhes.wav and screen PNGs
make firmware     # build/frodhes.fwsc (Docker on macOS)
```

`JIELI_TOOLCHAIN` and `AC79_SDK` override the tool locations.

### On your computer (no FM-1 needed)

```sh
tests/run_host.sh                           # -> build/host/frodhes.wav and screen PNGs
```

This runs the same `frodhes.c` with a fake FM-1: the sound goes to a WAV file and the screen to PNG images. It
needs only a C compiler. Try sound ideas here first.

### Onto the FM-1

```sh
./build.sh                                       # -> build/frodhes.fwsc (fails on what would brick the FM-1)
python3 tools/fm1_install.py build/frodhes.fwsc  # over USB; asks before it writes
python3 tools/fm1_install.py --info              # what runs on the connected FM-1
./build.sh --release 0.1                         # -> build/frodhes-0.1.fwsc, identity FM-1_951
```

The package identity is `FM-1_95N`, where N is the minor version. Before the first public release, check
[the hub](https://thefirmwarehub.com) to make sure `95N` is still free.

**If it goes wrong:** a crash shows its PC on screen, then the FM-1 restarts. Two crashes at boot send it to the
USB rescue. You can also force the rescue by holding **OCT-** while switching on. Look up the PC in
`build/frodhes.dis`. [docs/RULES.md](docs/RULES.md) lists the chip's rules: no FPU, no 64-bit division, and a
5.8 ms audio budget per 256 frames.

## Engine contract

`rhodes.c` exposes `rh_note_on`, `rh_note_off`, `rh_pedal`, `rh_render`, `rh_quiet` and `rh_lit`. The app uses
nothing else, so the model can be rewritten without touching `frodhes.c`. The main loop and the audio ISR hand
voices over through `volatile` fields: the main loop writes the parameters first and bumps `strike` last.

## Updating the platform from Dinghy

This repo keeps Dinghy's history, with Dinghy as the `upstream` remote:

```sh
git fetch upstream && git merge upstream/main
```

Frodhes changes upstream files only where it has to: the names in `tools/build.py` and the docs, and a host-mode
fix in `gfx.c` so the build works on macOS.

## Docs

- [docs/API.md](docs/API.md): what `platform.c` provides (panel IDs, screen, audio, MIDI, settings, flash map)
- [docs/RULES.md](docs/RULES.md): the chip's rules and the checks the build runs
- [docs/model/IMPLEMENTATION.md](docs/model/IMPLEMENTATION.md): the implementation plan (v2, current)
- [docs/model/PLAN.md](docs/model/PLAN.md) and [docs/model/reviews/](docs/model/reviews/): plan v1 and its four reviews
- [docs/model/research/](docs/model/research/): the research behind it

## License

GPL-3.0-only, the same as Dinghy, Felucca and SLOOP. See [LICENSING.md](LICENSING.md).
