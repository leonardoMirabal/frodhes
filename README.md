# Frodhes

**An electric piano, vibraphone and marimba, computed live, for the M-VAVE FM-1, the Ableton Move and your computer.**

No samples: every note is calculated as you play it, from how the real instrument works. A hammer strikes a tine
and a pickup turns its motion into sound; a mallet strikes a bar and its tube rings along. Play softly and it is
warm; play hard and it barks or rings.

Frodhes is free. If you enjoy it, you can [buy me a coffee](https://ko-fi.com/leomirabal).

<table align="center">
  <tr>
    <td align="center"><img src="docs/manual/img/piano.png?v=d41944c5" width="260" alt="Frodhes on the FM-1: the FRODHES page, four cards for the four knobs"></td>
    <td align="center"><img src="docs/manual/img/move/move-frodhes.png?v=ba262a7f" width="300" alt="Frodhes on the Move: the first page, eight cards for the eight knobs"></td>
    <td align="center"><img src="docs/manual/img/plugin/plugin-main.png?v=a6729600" width="300" alt="The desktop plugin's window: the VOICE knobs, two effect sections and a keyboard"></td>
  </tr>
  <tr>
    <td align="center"><b>On the M-VAVE FM-1</b>: colour screen, four knobs</td>
    <td align="center"><b>On the Ableton Move</b>: its own screen, eight knobs</td>
    <td align="center"><b>On your computer</b>: a plugin with its own window</td>
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
  (1-16 bars, overdub, undo, a chance per step), tap tempo or MIDI clock.
- **Connections**: MIDI over USB and the TRS input, pitch bend, audio over USB (to a computer, phone or tablet) as well as the headphone jack.
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

**One core or two.** `frodhes.fwsc` plays 10 voices on one of the FM-1's two processor cores. The release also has
[`frodhes-c1.fwsc`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-c1.fwsc), which runs
the voices on one core and the effects on the other: 14 voices on every instrument, the reverb at the full sample
rate, and a quicker answer to a note (about 6.5 ms from MIDI to the headphone jack, against about 10 ms).
Install it from a terminal (`python3 tools/fm1_install.py frodhes-c1.fwsc`; the emulator page installs `frodhes.fwsc`). You can switch between the two at any time; your
settings, presets and pattern stay.

Your settings and presets stay when you update. 0.9.10 changes the factory presets and some of their program numbers
(four new, four gone, SUIT TREM now F08): [the list](docs/MANUAL.md#6-presets). You can always go back to M-VAVE's own
firmware:
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
  <img src="docs/manual/img/move/move-drive-filter.png?v=18fa53cf" width="384" alt="The DRIVE/FILTER page on the Move: eight cards, each with its name, its value and a bar">
  <img src="docs/manual/img/move/move-pages.png?v=7f4bde0a" width="384" alt="The list of pages on the Move">
</p>

## On your computer: the desktop plugin

Frodhes also runs in your music software as an instrument plugin: CLAP and VST3 on macOS, Windows and Linux, and an
Audio Unit on macOS.

- The same instruments, effects and factory sounds, computed by the same code. It plays the higher-quality
  version of every instrument and effect, with 24 voices.
- Its own window: the FM-1's pages as cards with knobs, resizable. Presets, MIDI learn, the effects' bypass and the
  sustain latch are in it, and everything is saved with your project.
- The host's tempo drives the synced effects. Any sample rate. Up to 16 instances in one project.
- macOS 11 or newer (Apple silicon or Intel), Windows x64, Linux x86_64 or aarch64 (glibc 2.31 or newer). Nothing
  else to install.

**macOS.** Download
[`frodhes-plugin-macos.pkg`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-macos.pkg)
and open it: it installs the three formats into your own Library. The plugin is not signed by Apple yet, so macOS
refuses it the first time: right-click the file and choose **Open** (or System Settings > Privacy & Security >
**Open Anyway**). Or download
[`frodhes-plugin-macos.zip`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-macos.zip)
and copy the format your host uses into its folder (create the folder if it is not there; in the Finder,
**Go > Go to Folder...** and paste the path):

| Format | File | Folder |
| --- | --- | --- |
| CLAP | `Frodhes.clap` | `~/Library/Audio/Plug-Ins/CLAP/` |
| VST3 | `Frodhes.vst3` | `~/Library/Audio/Plug-Ins/VST3/` |
| Audio Unit | `Frodhes.component` | `~/Library/Audio/Plug-Ins/Components/` |

Then clear the download flag once, in Terminal:

```sh
xattr -dr com.apple.quarantine ~/Library/Audio/Plug-Ins/*/Frodhes.*
```

**Windows.** Download
[`frodhes-plugin-windows.zip`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-windows.zip).
It is not signed yet: before you extract it, right-click it, **Properties**, tick **Unblock**. Copy `Frodhes.clap`
into `C:\Program Files\Common Files\CLAP\` and `Frodhes.vst3` into `C:\Program Files\Common Files\VST3\`.

**Linux.** Download
[`frodhes-plugin-linux-x86_64.zip`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-linux-x86_64.zip)
(or `-aarch64`) and copy `Frodhes.clap` into `~/.clap/` and `Frodhes.vst3` into `~/.vst3/`.

Then restart your music software, or let it rescan its plugins: it appears as **Frodhes**, an instrument. For the
Audio Unit, `killall -9 AudioComponentRegistrar` in Terminal (or a restart of the Mac) makes macOS see it at once.
To remove it, delete the `Frodhes.*` files again. More:
[the manual](docs/MANUAL.md#17-appendix-the-desktop-plugin).

<p align="center">
  <img src="docs/manual/img/plugin/plugin-presets.png?v=cec41ef5" width="384" alt="The plugin's PRESETS list: every factory and user sound, grouped by instrument">
  <img src="docs/manual/img/plugin/plugin-effects.png?v=0564369e" width="384" alt="The plugin playing: the compressor and the tape delay on, their knobs and meters live">
</p>

## Manual

**[The user manual](docs/MANUAL.md)**: every page, knob and button, the presets, the effects and the MIDI map.

![The PERFORM, VOICE, PHASER and COMPRESSOR pages on the FM-1](docs/manual/img/overview.png?v=93a2d41c)

## Other keyboards

Frodhes also runs on the SMK-37 Pro, SMK-37 Elite and MKE-P37, with 10 voices as the FM-1's one-core package. Read
[docs/SMK37.md](docs/SMK37.md) before you install one.

The files on the [release page](https://github.com/leonardoMirabal/frodhes/releases/latest):

| Device | File |
| --- | --- |
| M-VAVE FM-1 | `frodhes.fwsc` |
| M-VAVE FM-1, two cores | `frodhes-c1.fwsc` |
| Ableton Move | `frodhes-module.tar.gz` |
| SMK-37 Pro | `frodhes-smk37-pro.fwsc` |
| SMK-37 Elite | `frodhes-smk37-elite.fwsc` |
| MKE-P37 | `frodhes-mke-p37.fwsc` |
| Desktop plugin, macOS | `frodhes-plugin-macos.pkg` or `frodhes-plugin-macos.zip` |
| Desktop plugin, Windows | `frodhes-plugin-windows.zip` |
| Desktop plugin, Linux | `frodhes-plugin-linux-x86_64.zip`, `frodhes-plugin-linux-aarch64.zip` |

## Credits

Frodhes is built on [Dinghy](https://github.com/zvenson/dinghy) by zvenson, which uses the platform of
[Felucca](https://github.com/hugelton/Felucca) by Leo Kuroshita (Hügelton Instruments) as
[SLOOP](https://github.com/isod89/sloop-fm1) by isod89 carries it. That platform handles updates, the USB rescue,
USB MIDI and audio, the panel, the screen and the settings. MIDI pitch bend is genkigenki's work
([PR #3](https://github.com/leonardoMirabal/frodhes/pull/3)). Key velocity on the FM-1's keys follows Charles
Vestal's [fm1-velocity](https://github.com/charlesvestal/fm1-velocity).

## For developers

How to build, test and contribute: [docs/BUILD.md](docs/BUILD.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## Support

Frodhes is free. If you enjoy it, you can [buy me a coffee](https://ko-fi.com/leomirabal).

## License and disclaimer

GPL-3.0-only, the same as Dinghy, Felucca and SLOOP ([LICENSING.md](LICENSING.md)).

Frodhes is an independent open-source project, not affiliated with or endorsed by M-VAVE,
Ableton, the Rhodes or Fender trademark owners or any instrument maker. It comes with no warranty: install it at your own risk.
