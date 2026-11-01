# Frodhes on the Ableton Move

Frodhes also runs on the Ableton Move, a standalone groove box, as a sound generator of Schwung, the community module
host that runs on it. The instruments (the electric piano types, VIBES, MARIMBA) and the effects are the FM-1's own
code, built for the Move's processor, with 16 voices on every instrument and the HQ version of the three engines.
Frodhes draws its own screen on the Move: the eight knobs as eight cards, a page at a time.

**This port is in development and not released yet.** Everything on this page is checked on the host build (the same
code run on a computer), and the screen, the knobs, the buttons, the presets, the levels and the clock on a Move.
What still waits for its check on a Move is marked *(not yet verified on a device)*.

This is the user manual for the Move. What the controls do to the sound is described once, for every device, in the
[manual](MANUAL.md) (sections 2-14, the [pages](MANUAL.md#4-the-pages) in section 4); this page says only how to reach
them on the Move and what is different, with a link to the manual's section for each page. The build, the tests and
the device measurements are in [MOVE-DEV.md](MOVE-DEV.md).

![The first page on the Move: the sound, its type, the spread, the sustain, the effects and the delay's mix](manual/img/move/move-frodhes.png?v=a6c53d5f)

## Contents

1. [What you need](#what-you-need)
2. [Install](#install)
3. [Open Frodhes' screen](#open-frodhes-screen)
4. [The screen](#the-screen)
5. [Buttons and knobs](#buttons-and-knobs)
6. [The pages](#the-pages)
7. [The menu](#the-menu)
8. [Presets and saving](#presets-and-saving)
9. [Sustain](#sustain)
10. [The effects](#the-effects)
11. [Velocity](#velocity)
12. [MIDI and tempo](#midi-and-tempo)
13. [The arpeggiator](#the-arpeggiator)
14. [Several Frodhes at once](#several-frodhes-at-once)
15. [What differs from the FM-1](#what-differs-from-the-fm-1)
16. [Troubleshooting](#troubleshooting)

## What you need

- An Ableton Move with Schwung installed (Frodhes is built and tested against Schwung 1.8).
- The Move on your network (it answers as `move.local`) and a computer with `ssh`, to copy the module once.
- The Move's **Full Velocity** setting **off**, so the pads play soft and loud (see [Velocity](#velocity)).

## Install

The module is one file, **`frodhes-module.tar.gz`**: a folder `frodhes/` with `dsp.so` and `engine.so` (the
instrument), `module.json` and `help.json` (its description and help text), `ui_chain.js` (Frodhes' screen),
`screen.js` and `canvas.js` (the same screen and meter for Schwung's other screens) and `LICENSE`. A release carries it, under that name and as `frodhes-X.Y.Z-move.tar.gz`, only when that exact file passed the tests on a
Move; until a release has it, build it yourself ([MOVE-DEV.md](MOVE-DEV.md)). Frodhes is not in Schwung's module
catalog: install it by hand.

1. Unload Frodhes from every track, if an older one is loaded (a module that plays must not be replaced under it).
2. Copy the folder into Schwung's sound generator folder, over the network:

   ```sh
   ssh ableton@move.local 'tar -xzf - -C /data/UserData/schwung/modules/sound_generators' < frodhes-module.tar.gz
   ```

3. In Schwung's chain editor for a track (see [Open Frodhes' screen](#open-frodhes-screen)), pick **Frodhes** as
   the track's sound generator (the synth). Its short name on Schwung's screens is `FRDH`.

*(not yet verified on a device: this plain `tar` route; the installer below is the tested one)*

An update is the same steps with the new file. Frodhes keeps its settings and user presets in Schwung's data folder
(`/data/UserData/schwung/module_data/frodhes`), not in the module's folder, so an update keeps them. To remove
Frodhes, unload it from every track and delete `/data/UserData/schwung/modules/sound_generators/frodhes`.

With this repository at hand, `tools/move_install.py` does the same with a check and a way back:

```sh
python3 tools/move_install.py install            # prints what it would do (dry run)
python3 tools/move_install.py install --go       # copies the module over ssh and switches to it
python3 tools/move_install.py info --go          # the installed version, the sha256 of dsp.so and engine.so
python3 tools/move_install.py rollback --go      # back to the module installed before
```

It installs `build/move/frodhes/` (`--src DIR` for another folder, such as the unpacked download), checks the new
module next to the old one before it switches, and keeps the two modules installed before it (`--keep`). `--host`
names the Move (default `ableton@move.local`).

## Open Frodhes' screen

1. Hold **Shift**, touch the **volume knob** and press the **Track button** of Frodhes' track (Track 1-4). Schwung's
   chain editor for that track opens.
2. Turn the jog wheel to the synth (Frodhes) and **click** the jog wheel. Frodhes' screen opens on its first page.

**Back** on a page leaves Frodhes' screen for the chain editor. Schwung's own shortcuts (Shift + volume touch + a
Track button, or Menu) work as everywhere and leave it too. While the screen is open, the knobs, the jog wheel, Back
and the Left and Right arrows are Frodhes'; the pads, the step buttons, Up and Down (the octave), Play and the rest
stay the Move's.

## The screen

The Move's screen is 128 x 64 pixels in one colour. Every picture on this page is that screen, drawn by the file the
Move runs and shown 4 times larger.

![The DRIVE/FILTER page: eight cards, each with its name, its value and a bar](manual/img/move/move-drive-filter.png?v=afb6680e)

- **The overview** is one page: eight cards, knobs 1-4 on the top row and 5-8 below, each with its name, its value
  and a bar. A knob with no control on that page has no card.
- **The bar** is an outlined box filled from the left up to the value. A choice of three or more options shows a
  notch between its steps; a value with a centre (TONE, TUNE) shows a notch at 0. A two-way switch (SUS, FX, COMP,
  MOTOR, PHASER, CHORUS, RETRIG) is an empty box at OFF (FREE for RETRIG) and a full one at ON (KEY).
- **The header** names the page and its number among the pages you can reach (`4/12`). An **`X`** before the number
  shows that the effects are off (FX OFF, the Left arrow), and an **`S`** that the sustain holds (SUS ON, the Right
  arrow).

![The header with the effects off (X) and the sustain on (S); the FX and SUS cards agree](manual/img/move/move-header-fx-sus.png?v=9b4fcc6e)

- **The large card**: touch a knob, or turn it, and its card fills the screen: the name, the value in large letters,
  a wide bar of the same kind and, along the bottom, a mark for each of the page's knobs, the one you hold filled.
  The header shows the page and the knob (`K2`). Turning changes the value as you watch.
  Half a second after you let go the overview comes back. Touch another knob and its card shows at once.

![Knob 2 on DRIVE/FILTER touched: AMOUNT as a large card](manual/img/move/move-drive-filter-k2.png?v=cd6baa2d)
![Knob 1 on REVERB: the type, a notch between its options](manual/img/move/move-reverb-k1.png?v=21b3c723)

- **A message** shows in a box for about a second: `SPRD RESET` after a double tap, `SAVED U05` after a save,
  `NOT SAVED` when you leave the save card with Back.

![A double tap on SPREAD: back to 0, SPRD RESET](manual/img/move/move-reset.png?v=ae0bd720)

- **The knob lights** show each knob's value, as on Schwung's own screens: knobs 1-4 white and 5-8 amber, brighter
  as the value grows, dark where the page has no control. **The Left arrow** is lit while the effects are on, **the
  Right arrow** while the sustain holds. Back gives the lights back to the Move.
- **The pads' lights are the Move's.** Frodhes never lights a pad or a step button. A pad you play lights white and
  stays white after you let it go, until you play another note: that is how the Move marks the last note you played,
  on every track and with its own instruments too. It shows most on a root pad (in the track's colour).

## Buttons and knobs

| Control | On Frodhes' screen |
| --- | --- |
| Knobs 1-8, **turn** | change the knob's control on this page, its large card shown |
| Knobs 1-8, **touch** | show the knob's large card without changing it |
| Knobs 1-8, **touch twice quickly** | the control back to its default (two touches within 0.4 s, no turn between) |
| **Jog wheel**, turn | the next or the previous page; on the list and the save card, the cursor |
| **Jog wheel**, click | open the list (the pages and the menu); on the list, open the row; on the save card, save |
| **Back** | close the list or the save card; on a page, leave Frodhes' screen |
| **Left arrow** | the effects off or on (FX; the compressor stays), on every page |
| **Right arrow** | the sustain on or off (SUS), on every page |
| Shift + jog wheel | leave Frodhes' screen (Schwung's shortcut) |
| Pads, Up / Down, Play ... | the Move's: notes, octave, transport |

- A fast turn of a wide range (more than 24 steps) moves four steps a detent.
- The double tap works on every knob but PRESET. The defaults are the FM-1's: a parameter's as on a fresh FM-1, and
  the settings as a new FM-1 boots (TUNE 0, the TREM switch OFF, the arpeggiator OFF at RATE 1/16, OCT 1 and GATE 60 %,
  CHAN ALL, SUS OFF). A double tap on FX turns the effects on.
- An arrow press sets the state the screen shows next (FX OFF, say); it does not toggle. A press that Schwung
  delivers twice, or plays back later from an automation lane, cannot turn it back. Only before the screen has read
  SUS or FX for the first time does a press toggle it.
- While one of Schwung's snapshots is armed for editing (Shift + the - or + button), Schwung draws its badge
  (`EDIT A1` ...) over the top right corner of every screen, Frodhes' too, and records every knob turn into that
  snapshot. Shift and the same button again leave the edit.

## The pages

Each page holds up to eight controls, one for each knob, in the FM-1's order and with the FM-1's ranges and
defaults. The overview shows a name of at most four letters on each card, so a card's name ends clear of the next
(SPRD for SPREAD, DPTH for DEPTH, ATCC for AT CC); the large card shows the full name. The VOICE pages are the instrument's that plays: PIANO VOICE for ST77, SUIT, FELT and DYNO,
VIBES VOICE and VIBES MOTOR for VIBE, MARIMBA VOICE for MRMB. They change with TYPE, a preset or a program change,
within about half a second; that is why the page count is 12 or 13.

| Page | Controls |
| --- | --- |
| **FRODHES** | TYPE · SPREAD · SUS · FX · PRESET · DELAY |
| **PIANO VOICE** | TONE · BARK · BELL · HAMMR · DECAY · REL · NOISE · TUNE |
| **VIBES VOICE** | STRIKE · MALLET · TUBE · WEIGHT · RING · DAMPR · NOISE · TUNE |
| **VIBES MOTOR** | MOTOR · DEPTH · SPEED · SHAPE · SYNC |
| **MARIMBA VOICE** | STRIKE · MALLET · TUBE · WEIGHT · RING · MUTE · NOISE · TUNE |
| **COMPRESSOR** | THRESH · RATIO · ATTACK · MKUP · COMP · REL |
| **DRIVE/FILTER** | DRIVE · AMOUNT · TONE · MIX · FILTER · FREQ · RESO · LFO |
| **TREM/PHASER** | TREM · DEPTH · RATE · SHAPE · PHASER · DEPTH · SPEED · FEEDBK |
| **CHORUS** | CHORUS · DEPTH · SPEED · SYNC |
| **DELAY** | DELAY · MIX · TIME · FEEDBK |
| **REVERB** | REVERB · MIX · DECAY · TONE · PREDLY · SIZE · MOD · LOWCUT |
| **TEMPO** | TRSYNC · PHSYNC · DLSYNC |
| **ARPEGGIO** | MODE · RATE · OCT · GATE |
| **LFO/SETUP** | SHAPE · RATE · SYNC · RETRIG · DLTONE · LEVEL · CHAN |
| **MAPPINGS** | AT CC · AT LO · AT HI · PA CC · PA LO · PA HI |

The names are the FM-1's where they fit and shorter where they do not (HAMMR, DAMPR, MKUP; an option the same way
everywhere, so the reverb's INFINITE is INF). CHAN is the FM-1's MIDI CHANNEL. The pictures below show each page with
its default values; each page's link goes to the manual's description of its controls.

### FRODHES

TYPE (ST77, SUIT, FELT, DYNO, VIBE, MRMB: the [FRODHES page](MANUAL.md#frodhes-the-main-page)'s TYPE), SPREAD
(the keys across the stereo field: the manual's [MIDI](MANUAL.md#12-midi), CC 105), SUS ([Sustain](#sustain)), FX ([The
effects](#the-effects)), PRESET (knob 5: the sound, its full name across knobs 5-7; [Presets and
saving](#presets-and-saving)) and the delay's MIX (DELAY, knob 8, the FM-1's PERFORM name for it). Knobs 6 and 7 have
no control here.

![FRODHES](manual/img/move/move-frodhes.png?v=a6c53d5f)
![Knob 5 touched: the preset, its name above and its number large](manual/img/move/move-frodhes-k5.png?v=caea2c9e)

### PIANO VOICE, VIBES VOICE, VIBES MOTOR, MARIMBA VOICE

The instrument's own controls: the Rhodes types' [VOICE pages](MANUAL.md#voice-12-and-22), the
[vibraphone](MANUAL.md#vibes-the-vibraphone) and [its motor](MANUAL.md#the-motor-on-and-off), the
[marimba](MANUAL.md#marimba). The FRODHES page's TONE and BELL (MALLET, MOTOR, TUBE on the bars) are here.

![PIANO VOICE, with ST77](manual/img/move/move-piano-voice.png?v=301bdb0e)
![VIBES VOICE, with VIBE](manual/img/move/move-vibes-voice.png?v=b77ff6fa)
![VIBES MOTOR, with VIBE](manual/img/move/move-vibes-motor.png?v=7f62f232)
![MARIMBA VOICE, with MRMB](manual/img/move/move-marimba-voice.png?v=40d4dfc8)

### COMPRESSOR

The [compressor](MANUAL.md#compressor)'s controls, its on / off switch (COMP) on knob 5. Knobs 7 and 8 have no
control: the gain reduction is drawn in their place, as on the FM-1's COMP page. GR and the
reading in dB are on the top line, under it a thick bar of 15 segments lit from the right as the reduction grows, on
the FM-1's scale (up to 30 dB), and the peak held for a second as a taller segment. It moves while you play. When the
compressor is off the bar is a dotted outline and reads COMP OFF. *(not yet verified on a device: the meter moving
while you play)*

![COMPRESSOR, the compressor off: COMP OFF](manual/img/move/move-compressor.png?v=57f2cac0)
![COMPRESSOR on: the meter at 12 dB of gain reduction, the peak held at 20 dB](manual/img/move/move-compressor-gr.png?v=af2373a6)

### DRIVE/FILTER, TREM/PHASER

Two of the FM-1's pages side by side: the [drive](MANUAL.md#drive) and the [filter](MANUAL.md#filter), the
[tremolo / pan / wah](MANUAL.md#tremwah) and the [phaser](MANUAL.md#phaser).

![DRIVE/FILTER](manual/img/move/move-drive-filter.png?v=afb6680e)
![TREM/PHASER](manual/img/move/move-trem-phaser.png?v=f6b87239)

### CHORUS, DELAY

A page each, with the FM-1's cards: the [chorus](MANUAL.md#chorus), the [delay](MANUAL.md#delay). The delay's mix
is also on the first page (DELAY, knob 8).

![CHORUS](manual/img/move/move-chorus.png?v=c4ac474b)
![DELAY](manual/img/move/move-delay.png?v=2d0c449e)

### REVERB

The FM-1's [REVERB](MANUAL.md#reverb) and [REVERB 2](MANUAL.md#reverb-2) on one page. **With REVERB at INF**
(INFINITE), DECAY shows SUSTAIN from 120 to 126 and FREEZE at 127, SUST and FREZ on the overview (what they do: the
manual's REVERB). At FREEZE new notes play dry; turn DECAY under 120 to get the tail moving again.

![REVERB](manual/img/move/move-reverb.png?v=2fbfbe5a)
![REVERB at INF, DECAY at 127: FREZ on the overview](manual/img/move/move-reverb-freeze.png?v=83417b6d)
![The DECAY card at 127: FREEZE](manual/img/move/move-reverb-freeze-k3.png?v=ecf3a9f3)

### TEMPO, ARPEGGIO

The synced rates of the tremolo, the phaser and the delay (the FM-1's [TEMPO](MANUAL.md#tempo) page without BPM and
CLOCK), and the arpeggiator ([The arpeggiator](#the-arpeggiator)). They follow the Move's tempo ([MIDI and
tempo](#midi-and-tempo)).

![TEMPO](manual/img/move/move-tempo.png?v=f53c923e)
![ARPEGGIO, as a new Frodhes starts: OFF, 1/16, 1 octave, 60 %](manual/img/move/move-arpeggio.png?v=6647d24f)

### LFO/SETUP, MAPPINGS

The filter's [LFO](MANUAL.md#lfo), the delay's tone, the output LEVEL and the MIDI channel (CHAN, ALL by default;
[SETUP](MANUAL.md#setup) and [MIDI](MANUAL.md#midi)); the pressure mappings (Move only):

- **AT CC / AT LO / AT HI**: channel pressure (aftertouch) drives the control on that MIDI CC number, from LO (no
  pressure) to HI (full pressure).
- **PA CC / PA LO / PA HI**: the same for poly pressure, from the most pressed held pad. Letting a pad go ends its
  pressure.

An HI under LO turns the direction round. A CC of 0 is off, and both are off by default. The control moves as if a
controller sent that CC (the CC numbers are the FM-1's, MANUAL.md section 12). *(not yet verified on a device)*

![LFO/SETUP](manual/img/move/move-lfo-setup.png?v=9855f40d)
![MAPPINGS](manual/img/move/move-mappings.png?v=1a4cf615)

## The menu

**Click the jog wheel** for the list. It holds the pages you can reach (the one you are on marked `*`), and below them
the menu, each section under its name. Turn the jog wheel to a row and click to open it; Back closes the list. The
header counts the rows (`4/20`).

![The list: the pages, DRIVE/FILTER picked](manual/img/move/move-pages.png?v=b80ab4f6)
![Below the pages: USER PRESETS (SAVE TO USER) and MY PRESETS](manual/img/move/move-menu.png?v=26c25c10)
![The MODULE rows: Module Help, Swap Module ...](manual/img/move/move-menu-module.png?v=d8669489)

- **USER PRESETS, SAVE TO USER**: saves the sound into one of Frodhes' 16 user slots ([Presets and
  saving](#presets-and-saving)).
- **MY PRESETS**: Schwung's presets of the whole module: the preset you are on (a click opens Schwung's preset
  browser), Save, Save As (Schwung's keyboard) and Delete. Schwung keeps these, apart from U01-U16.
- **MODULE**: Schwung's rows for a module: Module Help (the help text of each page), Add to List, Clear Automation,
  Swap Module (another module in this slot) and Remove Module.

After Module Help, a preset load, Delete or a picker you leave, Back brings you to this list.

## Presets and saving

PRESET on the first page loads the 19 factory sounds (F01-F19, described in the manual's
[presets](MANUAL.md#6-presets)) and the 16 user slots (U01-U16); program change loads them as on the FM-1 (0-15
F01-F16, 16-31 U01-U16, 32-34 F17-F19).

**To save the sound** into a user slot:

1. Click the jog wheel, turn to **SAVE TO USER** and click.
2. Turn the jog wheel (or any knob) to the slot. The card shows what it holds: EMPTY, or the name of the sound there.
3. Click to save (`CLICK: SAVE`, or `CLICK: REPLACE` over a used slot), or press Back to leave without saving
   (`BACK: NO`).

![SAVE TO USER: U03 picked, empty](manual/img/move/move-save.png?v=909188a5)

The slot then plays, named from the TYPE and the effects that are on, as on the FM-1, and `SAVED U03` shows. It is
written when Frodhes is quiet. The user slots are one set for every Frodhes on the Move: a sound saved in one shows
in the others within seconds.

The sound you set is also saved with the Move's set: Schwung keeps it with the track, and loading the set brings it
back. SUS is not saved with it: a set that loads starts with SUS off and FX on, as the FM-1 does at power-on.

## Sustain

The Move has no pedal jack. **SUS** is the sustain ([sustain and polyphony](MANUAL.md#10-sustain-and-polyphony)),
a switch: ON holds the notes as a pedal held down does, OFF lets them go, and OFF always wins (over a MIDI pedal held
down too). To set it:

- **SUS** on the first page (knob 3).
- **The Right arrow**, on every page of Frodhes' screen: each press turns SUS on or off. The header's `S` and the
  arrow's light show it. On Schwung's other screens the arrow does its usual job.
- **A sustain pedal or keyboard on CC 64** (on the Move's USB-A port, or MIDI from a computer): a pedal as on the
  FM-1, half pedalling included. Letting the pedal up also turns SUS off.

## The effects

The effects are inside Frodhes, on the pages above, with the FM-1's ranges and defaults (the manual's
[pages](MANUAL.md#4-the-pages)). **FX** on the first page is the FM-1's FX button ([effects on and
off](MANUAL.md#7-effects-on-and-off)), with one difference: on the Move the compressor stays on (the FM-1 bypasses it
too). FX is ON when Frodhes loads (also by MIDI, CC 111).

**The Left arrow**, on every page of Frodhes' screen, does the same: each press turns the effects off or on, and the
compressor stays. The header's `X` shows that they are off; the arrow is lit while they are on.

Frodhes' sound, effects included, then goes through the audio effects you put after it on the track and the Master
FX, like any other sound generator. To use one of Schwung's reverbs or delays instead, turn Frodhes' own off on its
page (REVERB, DELAY), or all of them with FX.

**Output level.** On the Move, Frodhes plays 2.2 dB louder than on the FM-1, the most it can without a clip: its
loudest peak is at -0.5 dBFS. Keep Schwung's slot volume at 100%: above it, Schwung's own gain can clip.

## Velocity

**Turn the Move's Full Velocity setting off** (in the Move's settings) to play the pads soft and loud. Schwung gives
Frodhes the pads' notes with the velocity the Move gives them. With Full Velocity on, every pad plays at 127, on every
track and with the Move's own instruments too, so Frodhes cannot play soft.

Notes from a keyboard on USB-A or from a computer keep their velocity either way. (With Full Velocity on, the Move
also sends each such note a second time at 127; Frodhes drops that copy.)

## MIDI and tempo

The MIDI of Schwung's track reaches Frodhes as on the FM-1 ([MIDI](MANUAL.md#12-midi)): notes with velocity and
release velocity, pitch bend, CC 64, the parameter CCs and program change. CHAN on LFO/SETUP picks the channel it listens to
(ALL by default). *(not yet verified on a device: CHAN with the Move's tracks)*

The sequencer's and the chords' CCs (43-46, 56-63) do nothing on the Move. The arpeggiator's (52-55) work, as on the
FM-1.

**The tempo is the Move's.** Frodhes follows the Move's transport: its clock and Start / Stop or, when no clock comes,
its transport and beat position. The synced rates (TEMPO's TRSYNC, PHSYNC, DLSYNC, the chorus SYNC, the VIBES
motor's SYNC), the LFO's SYNC and the arpeggiator lock to it ([tempo and sync](MANUAL.md#8-tempo-and-sync)). While the Move is stopped they use the Move's tempo
setting (120 BPM if the Move reports none). Frodhes has no BPM or CLOCK setting on the Move, and its tempo is kept
within the FM-1's 40 to 240 BPM. When the Move starts, a synced LFO takes about 2 s to come into phase with the beat
(it moves at most 1/8 faster or slower, so there is no jump).

**MIDI clock sent to the Move over USB-C does not reach Frodhes.** Schwung passes only the channel messages from
USB-C to a slot. To play Frodhes in time with a computer or another device, sync the Move to it: Frodhes then follows
the Move.

## The arpeggiator

**ARPEGGIO** is the FM-1's [arpeggiator page](MANUAL.md#arpeggio) (MODE, RATE, OCT, GATE) and its CCs 52-55. It
plays the notes you hold, on the Move's clock while the Move plays and on its tempo setting while it is stopped. SUS
sustains the notes it plays, as a pedal does, but does not hold the pattern: letting the pads go stops it (the FM-1's
HOLD is not on the Move). Each Frodhes
has its own, kept with the track; a Frodhes loaded new starts with it OFF.

Schwung has its own arpeggiator, a MIDI FX before the sound generator. Use one or the other: with both on, every note
is arpeggiated twice.

## Several Frodhes at once

Load Frodhes on as many tracks as Schwung has slots (four today): each one is its own instrument, with its own sound,
pages and screen, and all of them share the user presets U01-U16. Up to three play with no dropouts. A fourth works,
but very heavy playing with every effect on in all four can drop audio, and so can loading a set that holds several
of them.

## What differs from the FM-1

- **16 voices** on every engine (the FM-1 plays 10).
- **HQ, the Move's one version.** The FM-1 and the SMK-37 play the Classic engine; the Move plays HQ, with no
  setting. The electric piano types have the exact hammer strike at every velocity, a cleaner top octave and a truer
  re-strike of a key that still rings. VIBES and MARIMBA have more of the bar's and the tube's modes, and the
  mallet's contact computed live when a bar that still rings is struck again (MARIMBA's top keys, C6 up, always).
  *(not yet verified on a device)*
- **The screen**: one page is eight knobs (two of the FM-1's pages side by side), and the FM-1's knob card is the
  large card.
- **Buttons**: the Left arrow is FX and the Right arrow is SUS, on every page. SUS is a switch, on or off, in place
  of the FM-1's REC hold and tap.
- **Not on the Move**: the PERFORM, ASSIGN and DISPLAY pages (the Move has its own pads, knobs and screen); the
  sequencer, the chords and the scales (the Move has its own; their CCs do nothing, and a setup saved with them on
  loads with them off); the arpeggiator's HOLD; the BPM and CLOCK settings; KEY VEL, TOUCH, USB VOL, TRS, COLOR,
  BRIGHT and LEDS.
- **Only on the Move**: the pressure [mappings](#lfosetup-mappings), SAVE TO USER from the menu, Schwung's own
  presets, and several Frodhes at once.
- SPREAD and the compressor's RELEASE, set on the FM-1 by MIDI or a page of their own, are on the pages here.
- The sound is part of the Move's set.
- Audio runs in blocks of 128 frames at 44.1 kHz (the FM-1 uses 256). *(not yet verified on a device: the working
  limit per block)*

## Troubleshooting

- **The pads play only loud**: turn the Move's Full Velocity setting off ([Velocity](#velocity)).
- **Every note plays as two arpeggios**: Schwung's arpeggiator and Frodhes' are both on. Turn one off.
- **The arrows do not turn SUS or FX**: they do so only while Frodhes' screen is open ([Open Frodhes'
  screen](#open-frodhes-screen)).
- **The header is cut at the top right (`EDIT A1` ...)**: one of Schwung's snapshots is armed for editing and records
  your knob turns. Shift and the same - or + button leave the edit.
- **A control goes back to its old value after a preset or a program change**: a Schwung scene lock holds it on that
  slot. Change or remove the lock in Schwung.
- **The reverb does not answer new notes**: REVERB is at INF with DECAY at FREEZE (127). Turn DECAY under 120.
- **The VOICE page changed by itself**: the VOICE pages follow TYPE; a preset or a program change of another
  instrument brings its pages.
- **A pad stays white after you let it go**: the Move marks the last note you played; Frodhes does not light pads.
- **Clipping**: keep Schwung's slot volume at 100%.
- **Dropouts with four Frodhes**: use three, or fewer effects in each.
- **Frodhes does not follow a computer's MIDI clock**: the clock over USB-C does not reach it. Sync the Move to the
  computer instead.
- **A slot is silent and its preset reads NO ENGINE**: its engine could not load. Install the module again
  (unload it from every track first).
