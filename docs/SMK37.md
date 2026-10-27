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

The installer knows the SMK-37's names. On the computer the unit is called **Frodhes SMK-37 Pro** (Frodhes SMK-37
Elite, Frodhes MKE-P37), its MIDI port and its audio input alike, with a USB product id of its own for each model:
a computer that knew it under the stock name sets it up as a new device by itself. The community firmware flasher
finds it under that name for its DOWNLOAD MODE. From 0.9.6 Frodhes keeps its settings, user presets and pattern, and stages
its updates, only in 0xEB000-0xF3FFF: flash that no region of the stock firmware's own table uses (between its user
data, 0xC2000-0xEAFFF, and its resources at 0xF4000). It writes nothing of the stock firmware's settings area
(0x9D000-0xBFFFF), nor the calibration, the Bluetooth pairing or the stock resources.

## What 0.9.5 overwrote

0.9.5 kept its data inside the stock firmware's settings area (its VM, 0x9D000-0xBFFFF): on the first start it erased
0x9D000 (the settings) and 0xAF000 (its owner tag), then wrote its presets and pattern at 0x9E000-0xA1FFF and staged
updates at 0xBB000-0xBFFFF. The stock settings that were in those sectors are gone. 0.9.6 reads 0.9.5's data from
there once (only when 0.9.5's owner tag is at 0xAF000), copies it to its own place, and never writes that area again.
If you go back to 0.9.5 later, whatever it saves there (its setup, a preset, the pattern) comes over again at the next
update to 0.9.6.

If you made the flash backup above before installing 0.9.5, you can put the stock settings back: write your backup's
0x9D000-0xBFFFF over the unit's (with the kit's jl-uboot-tool, as for the backup). Only that
range: never write the calibration from a backup of another unit. Without a backup, the stock settings that 0.9.5
overwrote cannot be put back (how the stock firmware then starts up is not known to us); the calibration was never
touched.

## The way back to stock

Install your model's stock update package again with the stock updater (or from the
[Firmware Hub](https://thefirmwarehub.com/smk37/)). Frodhes does not touch the areas the stock firmware needs, so it
comes back with its calibration (and, after 0.9.5, see above). If the keyboard does not start, the backup above and
the kit's recovery steps are the way back.

**MKE-P37:** its stock firmware's settings area starts one sector lower, at 0x9C000. Every Frodhes install (0.9.5
and 0.9.6) writes the whole program area up to 0x9CFFF, so on an MKE-P37 it erases that first sector of the stock
settings. Back up first; the backup's 0x9C000-0x9CFFF puts it back.

**Not tested on an SMK-37:** from 0.9.6 an update stages in 0xEF000-0xF3FFF instead of 0xBB000-0xBFFFF. The normal
install hands the chip's boot code the staging place in RAM. Where it looks for that record after a power cut during
an install is not known for the SMK-37. If the power goes during an install and the keyboard does not start, the
kit's recovery steps (the mask-ROM bootloader) are the way back.

## The controls

The FM-1's 14 buttons are on the SMK buttons that mean the same; the ones with another printed name:

| SMK-37 | FM-1 | Does |
| --- | --- | --- |
| PATCH | HOME | PERFORM <-> the main page |
| CHORD | EDIT | CHORD page; again: chord mode on / off |
| PARA | ENV | PHASER page; again: on / off |
| NOTE REPEAT | LFO | TREM/WAH page; again: on / off |
| SCALE | SEL | DELAY page; again: on / off (arp HOLD) |
| GLOBE | GLO | TEMPO -> SETUP -> MIDI -> DISPLAY |
| BT | SAVE | save mode |

FX, SEQ, PLAY, REC, ARP and OCT- / OCT+ are as on the FM-1 (hold one OCT button and press the other: octave 0). The screen's hints use the SMK's names.

| Control | Does |
| --- | --- |
| K1 | MASTER volume; it is kept across power cycles (in the settings, saved 2 s after the last turn, in silence) |
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
| | | 16 | SUSTAIN: held, or a tap latches; one latch with REC (a tap on either lets go, a hold on either releases it) |

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
