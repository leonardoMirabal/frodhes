# Frodhes on the SMK-37 (experimental)

The SMK-37 (sold as SMK-37 Pro, SMK-37 Elite and MKE-P37) is built on the same chip as the FM-1, so Frodhes
builds for it from the same source. **These builds are experimental: we have not tested them on an SMK-37 ourselves.**
Everything below was checked on the host build only (the same code, run on a computer against a simulated keyboard,
`make test-smk`). If you try one, back up your flash first, and please tell us what works and what does not.

The SMK-37 board support (keybed, pads, LEDs, flash map, update path) is genkigenki's, from his SMK-37 port and the
fm1-to-smk37 kit, as it came into Dinghy. The SMK-37 app layer of Frodhes (pads, fader banks, wheels, pedal, panel
check) follows his PR #3 to Frodhes, rewritten for the current firmware.

## The files

| Model | File | Package identity |
| --- | --- | --- |
| SMK-37 Pro | `frodhes-smk37-experimental.fwsc` | `SMK-37 Pro_470` |
| SMK-37 Elite | `frodhes-smk37-elite-experimental.fwsc` | `SMK-37 Elite_470` |
| MKE-P37 | `frodhes-smk37-mke-experimental.fwsc` | `MKE-P37_470` |

Use the file for your model: the stock updater only accepts a package with the unit's own name. The identity stays the
same for every Frodhes version. Each release attaches the three files under these names
(the [release page](https://github.com/leonardoMirabal/frodhes/releases/latest)). The SMK builds play 7 voices
(the FM-1 plays 9).

## Before you install: back up your flash

The SMK-37 keeps a factory calibration of its keys, pads and wheels in its flash. Frodhes never writes there (its
flash allow-list and the flash driver both refuse it, and the host tests check every write), but a full backup is the
only real safety net. The fm1-to-smk37 kit describes how: start an install, send the update loader the soft key that
restarts the keyboard into the chip's mask-ROM bootloader, read the whole 1 MiB twice with jl-uboot-tool, compare the
two reads and check that the calibration record (`trimdat`) is in them. Do this before anything else.

## Install

```sh
python3 tools/fm1_install.py frodhes-smk37-experimental.fwsc
```

The installer knows the SMK-37's names. Frodhes keeps its settings, presets and pattern in a part of the flash that no
stock firmware region uses: it never writes the stock firmware's settings area (versions before 0.9.6 cleared it and saved there), and the calibration, Bluetooth pairing and
the stock resources are left alone.

## The way back to stock

Install your model's stock update package again with the stock updater (or from the
[Firmware Hub](https://thefirmwarehub.com/smk37/)). Frodhes does not touch the areas the stock firmware needs, so it
comes back with its calibration. If the keyboard does not start, the backup above and the kit's recovery steps are the
way back.

## The controls

The FM-1's 14 buttons are on the SMK buttons that mean the same; the ones with another printed name:

| SMK-37 | FM-1 | Does |
| --- | --- | --- |
| PATCH | HOME | PERFORM <-> the main page |
| PARA | EDIT | CHORD page; again: chord mode on / off |
| CHORD | ENV | PHASER page; again: on / off |
| NOTE REPEAT | LFO | TREM/WAH page; again: on / off |
| SCALE | SEL | DELAY page; again: on / off (arp HOLD) |
| GLOBE | GLO | TEMPO -> SETUP -> MIDI -> DISPLAY |
| BT | SAVE | save mode |

FX, SEQ, PLAY, REC, ARP and OCT- / OCT+ are as on the FM-1. The screen's hints use the SMK's names.

| Control | Does |
| --- | --- |
| K1 | MASTER volume |
| K2 / K3 / K4 | SELECT (pages), ALGORITHM (TYPE), PRESETS |
| K5..K8 | the four cards of the page (the FM-1's K1..K4) |
| < > | the previous / next page |
| KNOB BANK (held) | the panel check: the keyboard's raw readings (faders, wheels, pedal, pads, battery, flash) |
| FADER BANK | the faders' bank, A or B (lit on B) |
| 37 keys | C3..C6 at OCTAVE 0 (the middle 27 are the FM-1's F3..G5), each at its own velocity |
| faders, bank A | the instrument's first VOICE page (Rhodes: TONE BARK BELL HAMMER; VIBES / MARIMBA: STRIKE MALLET TUBE WEIGHT) |
| faders, bank B | the four PERFORM knobs (set them on ASSIGN) |
| pitch wheel | pitch bend, +-2 semitones (the Rhodes types, as MIDI pitch bend; VIBES and MARIMBA ignore it) |
| mod wheel | more TREM/WAH depth on top of DEPTH, full at the top (VIBES: the motor's); switches it on if off, off again at rest |
| pedal jack | sustain pedal, either polarity (plug it in before switching on) |

**Faders: soft takeover.** A fader takes over only once it reaches the value its card has (after a bank change, a
preset, a knob or MIDI moved it), so nothing jumps; until then the footer says where to move it (`TONE UP TO +0.0`).

**Velocity.** A key's velocity comes from the keyboard's own factory curve, through the same touch curve as the FM-1's
keys: KEY VEL (FRODHES page) is the velocity of a medium press, a light touch plays softer, a hard one louder up to
127. TOUCH (SETUP K4) works as on the FM-1: LIGHT, MEDIUM (the default), HEAVY or FIXED (every key at KEY VEL).

**The pads** (the top row is 1..8, the bottom row 9..16):

| Pad | Does | Pad | Does |
| --- | --- | --- | --- |
| 1..6 | TYPE: ST77, SUIT, FELT, DYNO, VIBE, MRMB | 9 | DRIVE on / off |
| 7 | previous preset | 10 | COMP on / off |
| 8 | next preset | 11 | FILTER on / off |
| | | 12 | TREM/WAH on / off (VIBES: the motor) |
| | | 13 / 14 / 15 | PHASER / CHORUS / DELAY on / off |
| | | 16 | SUSTAIN: held, or a tap latches (as REC) |

Pad colours: dark orange off, light blue on, brighter while notes sound. DISPLAY BRIGHTNESS dims the pads and the
button LEDs too.

## What the SMK-37 does not have

- No TRS MIDI input: MIDI is USB only, and the MIDI page has no TRS IN card.
- No LEDs on the keys: the DISPLAY page has no LEDS card.

## For developers

`./build.sh --board smk37 [--model elite|mke]` builds a package; `make test-smk` runs every host script on the SMK
build (tests/smk/ has the SMK's own: knobs, wheels, faders, pads, pedal, keys, its buttons and lights; the FM-1-only
ones are listed in tests/smk/known.txt), and `BOARD=smk37 tests/run_host.sh [script]` renders one. Script lines for
the SMK: `vel V`, `xkey K V`, `pad P V`, `fader F V`, `wheel W RAW`, `pedal RAW`, `ids smk` (tests/host.c). The app
layer is `firmware/src/frodhes_smk.h`; Frodhes' SMK flash places are in `firmware/src/frodhes_store.h`.
