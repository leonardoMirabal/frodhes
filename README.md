# Frodhes

**An electric piano, vibraphone and marimba, computed live, for the M-VAVE FM-1 and the Ableton Move.**

No samples: every note is calculated as you play it, from how the real instrument works. A hammer strikes a tine
and a pickup turns its motion into sound; a mallet strikes a bar and its tube rings along. Play softly and it is
warm; play hard and it barks or rings.

<table align="center">
  <tr>
    <td align="center"><img src="docs/manual/img/piano.png?v=b2c98a85" width="360" alt="Frodhes on the FM-1: the FRODHES page, four cards for the four knobs"></td>
    <td align="center"><img src="docs/manual/img/move/move-frodhes.png?v=ed05fe9c" width="420" alt="Frodhes on the Move: the first page, eight cards for the eight knobs"></td>
  </tr>
  <tr>
    <td align="center"><b>On the M-VAVE FM-1</b>: colour screen, four knobs</td>
    <td align="center"><b>On the Ableton Move</b>: its own screen, eight knobs</td>
  </tr>
</table>

**[Try it in your browser](https://leonardomirabal.github.io/frodhes/)** before you install: the same firmware, with
the FM-1's screen, knobs and keys. Play it from your computer keyboard or a MIDI keyboard.

## What you get

- **Six instruments**: four electric pianos (STAGE 77, SUITCASE, EARLY FELT, BELL DYNO), a vibraphone with its
  motor (VIBES) and a marimba (MARIMBA).
- **Effects**: compressor, 3-band EQ, drive with five overdrive types, resonant filter with its own LFO, tremolo /
  pan / wah, phaser, chorus, delay (digital, analog, tape) and reverb (hall, plate, infinite).
- **For playing**: sustain pedal, velocity from the FM-1's own keys, chords, scales, arpeggiator, a pattern recorder
  (1-16 bars, overdub, undo), tap tempo or MIDI clock.
- **Connections**: MIDI over USB and the TRS input, pitch bend, audio over USB as well as the headphone jack.
- **Presets**: 19 factory sounds and 16 slots for your own.
- **A screen you can read**: one page at a time, one card per knob.

What is new in each version: [the release notes](https://github.com/leonardoMirabal/frodhes/releases).

## Install on the FM-1

1. Download [`frodhes.fwsc`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes.fwsc).
   This link always gives the newest version.
2. Connect the FM-1 with USB and install it from the browser (Chrome or Edge): open the
   [emulator page](https://leonardomirabal.github.io/frodhes/#install) and use **Install on your FM-1 or SMK-37**.
   Or from a terminal, in a copy of this repository:
   ```sh
   pip install mido python-rtmidi
   python3 tools/fm1_install.py frodhes.fwsc
   ```
3. If something goes wrong, switch the FM-1 off, hold **OCT−** and switch it on. This starts the USB rescue, and
   you can install again from there.

Your settings and presets stay when you update. You can always go back to M-VAVE's own firmware:
[how](docs/MANUAL.md#going-back-to-the-original-firmware).

## On the Ableton Move

Frodhes runs on the Move as a sound generator in Schwung, the community module host for the Move.

- The same instruments and effects, with 16 voices on every instrument and the higher-quality version of each.
- The reverb runs at the full sample rate, so its tails keep their top octave.
- Its own screen: the eight knobs as eight cards, a page at a time.
- 57 factory presets (the FM-1's 19 plus 38 more) and 16 slots for your own.
- The Move's sequencer, scales and tempo drive it. The arpeggiator is Frodhes' own.
- One Frodhes on each track, up to four, each with its own sound. Three play without dropouts.

Install it with Schwung Manager in a web browser: **Install Custom Module > From GitHub URL**, then
`leonardoMirabal/frodhes`, or the downloaded `frodhes-module.tar.gz` under **From Tarball**. Once Frodhes is listed in
Schwung's module catalog, you can also install and update it from there. The steps, and every page:
[docs/MOVE.md](docs/MOVE.md).

<p align="center">
  <img src="docs/manual/img/move/move-drive-filter.png?v=5388b9c5" width="384" alt="The DRIVE/FILTER page on the Move: eight cards, each with its name, its value and a bar">
  <img src="docs/manual/img/move/move-pages.png?v=fcd9ce1d" width="384" alt="The list of pages on the Move">
</p>

## Manual

**[The user manual](docs/MANUAL.md)**: every page, knob and button, the presets, the effects and the MIDI map.

![The PERFORM, VOICE, PHASER and COMPRESSOR pages on the FM-1](docs/manual/img/overview.png?v=40425df3)

## Other keyboards

Frodhes also builds for the SMK-37 Pro, SMK-37 Elite and MKE-P37. These builds are **experimental and not tested on
hardware**. Read [docs/SMK37.md](docs/SMK37.md) before you install one.

The files on the [release page](https://github.com/leonardoMirabal/frodhes/releases/latest):

| Device | File |
| --- | --- |
| M-VAVE FM-1 | `frodhes.fwsc` |
| Ableton Move | `frodhes-module.tar.gz` |
| SMK-37 Pro (experimental) | `frodhes-smk37-experimental.fwsc` |
| SMK-37 Elite (experimental) | `frodhes-smk37-elite-experimental.fwsc` |
| MKE-P37 (experimental) | `frodhes-smk37-mke-experimental.fwsc` |

## Credits

Frodhes is built on [Dinghy](https://github.com/zvenson/dinghy) by zvenson, which uses the platform of
[Felucca](https://github.com/hugelton/Felucca) by Leo Kuroshita (Hügelton Instruments) as
[SLOOP](https://github.com/isod89/sloop-fm1) by isod89 carries it. That platform handles updates, the USB rescue,
USB MIDI and audio, the panel, the screen and the settings. MIDI pitch bend is genkigenki's work
([PR #3](https://github.com/leonardoMirabal/frodhes/pull/3)). Key velocity on the FM-1's keys follows Charles
Vestal's [fm1-velocity](https://github.com/charlesvestal/fm1-velocity).

## For developers

How to build, test and contribute: [docs/BUILD.md](docs/BUILD.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License and disclaimer

GPL-3.0-only, the same as Dinghy, Felucca and SLOOP ([LICENSING.md](LICENSING.md)).

Frodhes is an independent open-source project, not affiliated with or endorsed by M-VAVE,
Ableton, the Rhodes or Fender trademark owners or any instrument maker. It comes with no warranty: install it at your own risk.
