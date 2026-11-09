# Frodhes on the Ableton Move

Frodhes runs on the Ableton Move as a sound generator for Schwung, the community module host for the Move. It is the
same electric piano, vibraphone and marimba as on the FM-1, with the same effects, and it plays 16 voices on every
instrument. Frodhes draws its own screen: the Move's eight knobs as eight cards, one page at a time.

What each control does to the sound is described in the [FM-1 manual](MANUAL.md#4-the-pages). This manual covers
the Move's controls and what is different.

![The first page on the Move: the sound, its type, the spread, the sustain, the effects and the delay's mix](manual/img/move/move-frodhes.png?v=ba262a7f)

## Contents

1. [What you need](#what-you-need)
2. [Getting started](#getting-started)
3. [Controls](#controls)
4. [The screen](#the-screen)
5. [The pages](#the-pages)
6. [The list](#the-list)
7. [Presets and saving](#presets-and-saving)
8. [Sustain](#sustain)
9. [The effects](#the-effects)
10. [Velocity](#velocity)
11. [MIDI and tempo](#midi-and-tempo)
12. [The arpeggiator](#the-arpeggiator)
13. [Several Frodhes at once](#several-frodhes-at-once)
14. [Updating and removing](#updating-and-removing)
15. [What differs from the FM-1](#what-differs-from-the-fm-1)
16. [Troubleshooting](#troubleshooting)
17. [Appendix: the factory presets F20-F57](#appendix-the-factory-presets-f20-f57)

## What you need

- An Ableton Move with Schwung installed (Frodhes is tested with Schwung 1.8.2).
- A computer or phone on the same network as the Move, with a web browser.
- The Move's **Full Velocity** setting **off**, so the pads play soft and loud (see [Velocity](#velocity)).

## Getting started

### Install

1. On the Move, open **Global Settings > System > Web Manager**. It shows the Move's address and a QR code.
2. On your computer or phone, open that address with port 7700: `http://<move-ip>:7700` (`http://move.local:7700`
   often works too). This is Schwung Manager.
3. Open **Install Custom Module**. Under **From GitHub URL**, enter `leonardoMirabal/frodhes`
   (or `https://github.com/leonardoMirabal/frodhes`) and install.

Schwung Manager downloads the latest release. To install from a file instead, download **`frodhes-module.tar.gz`**
from the [release page](https://github.com/leonardoMirabal/frodhes/releases/latest) and upload it under **Install
Custom Module > From Tarball**. Once Frodhes is listed in Schwung's module catalog, you can also install and update it
from there.

### Put Frodhes on a track

1. Hold **Shift**, touch the **volume knob** and press the **Track button** (1-4) of the track you want. The chain
   editor for that track opens.
2. Choose **Frodhes** as the track's sound generator (the synth). On Schwung's screens its short name is `FRDH`.

### Open Frodhes' screen

In the chain editor, turn the jog wheel to Frodhes and **click** it. Frodhes' screen opens on its first page,
FRODHES. **Back** on a page leaves the screen for the chain editor.

### Your first sound

Play the pads. A new Frodhes plays **F01 STAGE 77**, a stage electric piano. If every pad plays loud, turn the Move's
Full Velocity setting off.

While Frodhes' screen is open, the knobs, the jog wheel, Back and the Left and Right arrows belong to Frodhes. The
pads, the step buttons, Up and Down (the octave), Play and the other buttons stay the Move's.

### Pages and knobs

- Turn the **jog wheel** to go to the next or the previous page.
- Turn a **knob** to change the control on its card. The card fills the screen while you turn.
- **Touch** a knob to see its value without changing it. **Touch it twice quickly** to reset it to its default.
- **Click** the jog wheel for a list of all the pages and the menu.
- **Left arrow**: the effects off or on (FX). **Right arrow**: the sustain on or off (SUS).

### Presets

On the FRODHES page, turn **knob 5 (PRESET)**. **F01-F19** are the FM-1's factory sounds, **F20-F57** are two more
versions of each, made for the Move, and **U01-U16** are your own.

### Saving a sound

1. Click the jog wheel, turn to **SAVE TO USER** and click.
2. Turn the jog wheel to a slot, U01-U16. The card shows EMPTY or the sound already there.
3. Click to save, or press **Back** to cancel.

The screen shows `SAVED U03` (for example). Every Frodhes on the Move shares the same user slots. Your sound is also
saved with the Move's set: load the set and the track's sound comes back.

## Controls

| Control | On Frodhes' screen |
| --- | --- |
| Knobs 1-8, **turn** | change the knob's control on this page, its large card shown |
| Knobs 1-8, **touch** | show the knob's large card without changing it |
| Knobs 1-8, **touch twice quickly** | the control back to its default (two touches within 0.4 s, no turn between) |
| **Jog wheel**, turn | the next or the previous page; on the list and the save card, the cursor |
| **Jog wheel**, click | open the list (the pages and the menu); on the list, open the row; on the save card, save |
| **Back** | close the list or the save card; on a page, leave Frodhes' screen |
| **Left arrow** | the effects off or on (FX; the compressor stays on), on every page |
| **Right arrow** | the sustain on or off (SUS), on every page |
| Pads, Up / Down, Play ... | the Move's: notes, octave, transport |

- Turn a knob quickly and a control with a wide range (more than 24 steps) moves four steps a detent.
- The double tap works on every knob but PRESET. The defaults are the FM-1's: a parameter's as on a new FM-1, and
  the settings as a new FM-1 starts (TUNE 0, the TREM switch OFF, the arpeggiator OFF at RATE 1/16, OCT 1, GATE 60 %
  and HOLD OFF, CHAN ALL, SUS OFF). A double tap on FX turns the effects on.
- Schwung's shortcut, Shift + a touch on the volume knob + a Track button, works as everywhere and also leaves
  Frodhes' screen.
- While one of Schwung's snapshots is armed for editing (Shift + the - or + button), Schwung draws its badge
  (`EDIT A1` ...) over the top right corner of every screen, Frodhes' too, and records every knob turn into that
  snapshot. Shift and the same button again leave the edit.

## The screen

The Move's screen is 128 x 64 pixels in one colour. Every picture in this manual is that screen, drawn by the file
the Move runs and shown 4 times larger.

![The DRIVE/FILTER page: eight cards, each with its name, its value and a bar](manual/img/move/move-drive-filter.png?v=18fa53cf)

- **The overview** is one page: eight cards, knobs 1-4 on the top row and 5-8 below, each with its name, its value
  and a bar. A knob with no control on that page has no card.
- **The bar** is an outlined box filled from the left up to the value. A choice of three or more options shows a
  notch between its steps. A value with a centre (TONE, TUNE) shows a notch at 0. A two-way switch (SUS, FX, COMP,
  MOTOR, PHASER, CHORUS, RETRIG) is an empty box at OFF (FREE for RETRIG) and a full one at ON (KEY).
- **The header** names the page and its number among the pages you can reach (`4/13`). An **`X`** before the number
  shows that the effects are off (FX OFF, the Left arrow). An **`S`** shows that the sustain holds (SUS ON, the Right
  arrow).

![The header with the effects off (X) and the sustain on (S); the FX and SUS cards agree](manual/img/move/move-header-fx-sus.png?v=333d757c)

- **The large card**: touch a knob, or turn it, and its card fills the screen: the name, the value in large letters,
  a wide bar of the same kind and, along the bottom, a mark for each of the page's knobs, the one you hold filled.
  The header shows the page and the knob (`K2`). Turning changes the value as you watch.
  Half a second after you let go the overview comes back. Touch another knob and its card shows at once.

![Knob 2 on DRIVE/FILTER touched: AMOUNT as a large card](manual/img/move/move-drive-filter-k2.png?v=cd6baa2d)
![Knob 1 on REVERB: the type, a notch between its options](manual/img/move/move-reverb-k1.png?v=21b3c723)

- **A message** shows in a box for about a second: `SPRD RESET` after a double tap, `SAVED U05` after a save,
  `NOT SAVED` when you leave the save card with Back.

![A double tap on SPREAD: back to 0, SPRD RESET](manual/img/move/move-reset.png?v=c42be043)

- **The knob lights** show each knob's value, as on Schwung's own screens: knobs 1-4 white and 5-8 amber, brighter
  as the value grows, dark where the page has no control. **The Left arrow** is lit while the effects are on, **the
  Right arrow** while the sustain holds. Back gives the lights back to the Move.
- **The pads' lights are the Move's.** Frodhes never lights a pad or a step button. A pad you play lights white and
  stays white after you let it go, until you play another note: that is how the Move marks the last note you played,
  on every track and with its own instruments too. It shows most on a root pad (in the track's colour).

## The pages

Each page holds up to eight controls, one for each knob, in the FM-1's order and with the FM-1's ranges and
defaults. The overview shows a name of at most four letters on each card (SPRD for SPREAD, DPTH for DEPTH, ATCC for
AT CC); the large card shows the full name. The VOICE pages are those of the instrument that plays: PIANO VOICE for
ST77, SUIT, FELT and DYNO, VIBES VOICE and VIBES MOTOR for VIBE, MARIMBA VOICE for MRMB. The VOICE pages follow TYPE,
so the page count is 13 or 14.

| Page | Controls |
| --- | --- |
| **FRODHES** | TYPE · SPREAD · SUS · FX · PRESET · DELAY |
| **PIANO VOICE** | TONE · BARK · BELL · HAMMR · DECAY · REL · NOISE · TUNE |
| **VIBES VOICE** | STRIKE · MALLET · TUBE · WEIGHT · RING · DAMPR · NOISE · TUNE |
| **VIBES MOTOR** | MOTOR · DEPTH · SPEED · SHAPE · SYNC |
| **MARIMBA VOICE** | STRIKE · MALLET · TUBE · WEIGHT · RING · MUTE · NOISE · TUNE |
| **VELOCITY** | RESP |
| **COMPRESSOR** | THRESH · RATIO · ATTACK · MKUP · COMP · REL |
| **EQ** | LOW · MID · FREQ · HIGH |
| **DRIVE/FILTER** | DRIVE · AMOUNT · TONE · MIX · FILTER · FREQ · RESO · LFO |
| **TREM/PHASER** | TREM · DEPTH · RATE · SHAPE · PHASER · DEPTH · SPEED · FEEDBK |
| **CHORUS** | CHORUS · DEPTH · SPEED · SYNC |
| **DELAY** | DELAY · MIX · TIME · FEEDBK |
| **REVERB** | REVERB · MIX · DECAY · TONE · PREDLY · SIZE · MOD · LOWCUT |
| **TEMPO** | TRSYNC · PHSYNC · DLSYNC |
| **ARPEGGIO** | MODE · RATE · OCT · GATE · HOLD |
| **LFO/SETUP** | SHAPE · RATE · SYNC · RETRIG · DLTONE · LEVEL · CHAN |
| **MAPPINGS** | AT CC · AT LO · AT HI · PA CC · PA LO · PA HI · VER |

The names are the FM-1's where they fit and shorter where they do not (HAMMR, DAMPR, MKUP; an option the same way
everywhere, so the reverb's INFINITE is INF). CHAN is the FM-1's MIDI CHANNEL; FREQ on the EQ page is its MID
FREQ. The pictures below show each page with its default values. Each page's link goes to the manual's description
of its controls.

### FRODHES

TYPE (ST77, SUIT, FELT, DYNO, VIBE, MRMB: the [FRODHES page](MANUAL.md#frodhes-the-main-page)'s TYPE), SPREAD
(the keys across the stereo field: the manual's [MIDI](MANUAL.md#12-midi), CC 105), SUS ([Sustain](#sustain)), FX ([The
effects](#the-effects)), PRESET (knob 5: the sound, its full name across knobs 5-7; [Presets and
saving](#presets-and-saving)) and the delay's MIX (DELAY, knob 8, the FM-1's PERFORM name for it). Knobs 6 and 7 have
no control here.

![FRODHES](manual/img/move/move-frodhes.png?v=ba262a7f)
![Knob 5 touched: the preset, its name above and its number large](manual/img/move/move-frodhes-k5.png?v=caea2c9e)

### PIANO VOICE, VIBES VOICE, VIBES MOTOR, MARIMBA VOICE

The instrument's own controls: the electric piano types' [VOICE pages](MANUAL.md#voice-13-and-23), the
[vibraphone](MANUAL.md#vibes-the-vibraphone) and [its motor](MANUAL.md#the-motor-on-and-off), the
[marimba](MANUAL.md#marimba). The FRODHES page's TONE and BELL (MALLET, MOTOR, TUBE on the bars) are here.

![PIANO VOICE, with ST77](manual/img/move/move-piano-voice.png?v=feae1542)
![VIBES VOICE, with VIBE](manual/img/move/move-vibes-voice.png?v=ff0e605e)
![VIBES MOTOR, with VIBE](manual/img/move/move-vibes-motor.png?v=6ef36494)
![MARIMBA VOICE, with MRMB](manual/img/move/move-marimba-voice.png?v=f1604c31)

### VELOCITY (RESP)

RESP, how the electric piano answers velocity: **FULL** (the default), the full dynamic range, or **LIGHT**, a
lighter touch with soft notes louder. It changes the sound only, for every note (pads, a keyboard, the arpeggiator);
the velocity itself reaches Frodhes as played. It is the FM-1's last VOICE page ([RESPONSE](MANUAL.md#voice-33-velocity),
CC 40). The FM-1's TOUCH and KEY VEL, for its own keys, are not on the Move: the pads' sensitivity is the Move's own
setting ([Velocity](#velocity)).

![VELOCITY: RESP FULL](manual/img/move/move-velocity.png?v=8ff77254)

### COMPRESSOR

The [compressor](MANUAL.md#compressor)'s controls, with its on / off switch (COMP) on knob 5. Knobs 7 and 8 have no
control: the gain reduction is drawn in their place, as on the FM-1's COMP page. GR and the reading in dB are on the
top line. Under it is a thick bar of 15 segments, lit from the right as the reduction grows, on the FM-1's scale (up
to 30 dB). The peak is held for a second as a taller segment. The meter moves while you play. When the compressor is
off the bar is a dotted outline and reads COMP OFF.

![COMPRESSOR, the compressor off: COMP OFF](manual/img/move/move-compressor.png?v=0d813518)
![COMPRESSOR on: the meter at 12 dB of gain reduction, the peak held at 20 dB](manual/img/move/move-compressor-gr.png?v=f4275cfe)

### EQ

The [EQ](MANUAL.md#eq), between the compressor and the drive as on the FM-1: LOW (the low shelf), MID (the bell),
FREQ (the FM-1's MID FREQ, where MID acts) and HIGH (the high shelf), on knobs 1-4. The cards show the FM-1's
values: LOW, MID and HIGH in dB, from -15 to +15, with +0.0 (flat) in the middle of the bar, marked by a notch; FREQ
in Hz, from 200 to 5.0K (5 kHz), 1.0K in the middle. Every one starts flat (+0.0, FREQ 1.0K), and with the three
gains at +0.0 the EQ is out of the sound. A double tap sets a knob back to that. Over MIDI: CC 34-37, 64 flat.

![EQ, flat: every gain +0.0, FREQ 1.0K](manual/img/move/move-eq.png?v=58783b61)
![EQ set as in the manual's picture: LOW +3.1, MID -4.0 at 551 Hz, HIGH +1.9](manual/img/move/move-eq-set.png?v=dbba4bda)
![Knob 3 touched: FREQ at 551 Hz](manual/img/move/move-eq-set-k3.png?v=9ce90eaa)

### DRIVE/FILTER, TREM/PHASER

Two of the FM-1's pages side by side: the [drive](MANUAL.md#drive) and the [filter](MANUAL.md#filter), the
[tremolo / pan / wah](MANUAL.md#tremwah) and the [phaser](MANUAL.md#phaser).

![DRIVE/FILTER](manual/img/move/move-drive-filter.png?v=18fa53cf)
![TREM/PHASER](manual/img/move/move-trem-phaser.png?v=855147c5)

### CHORUS, DELAY

A page each, with the FM-1's cards: the [chorus](MANUAL.md#chorus), the [delay](MANUAL.md#delay). The delay's mix
is also on the first page (DELAY, knob 8).

![CHORUS](manual/img/move/move-chorus.png?v=fd8f98f8)
![DELAY](manual/img/move/move-delay.png?v=a82450f7)

### REVERB

The FM-1's [REVERB](MANUAL.md#reverb) and [REVERB 2](MANUAL.md#reverb-2) on one page. **With REVERB at INF**
(INFINITE), DECAY shows SUSTAIN from 120 to 126 and FREEZE at 127, SUST and FREZ on the overview (what they do: the
manual's REVERB). At FREEZE new notes play dry; turn DECAY under 120 to get the tail moving again.

On the Move the reverb runs at the full sample rate: its tails keep their top octave. The controls and the presets
are the same as on the FM-1.

![REVERB](manual/img/move/move-reverb.png?v=3114e888)
![REVERB at INF, DECAY at 127: FREZ on the overview](manual/img/move/move-reverb-freeze.png?v=69f335ab)
![The DECAY card at 127: FREEZE](manual/img/move/move-reverb-freeze-k3.png?v=ecf3a9f3)

### TEMPO, ARPEGGIO

The synced rates of the tremolo, the phaser and the delay (the FM-1's [TEMPO](MANUAL.md#tempo) page without BPM and
CLOCK), and the arpeggiator ([The arpeggiator](#the-arpeggiator)). They follow the Move's tempo ([MIDI and
tempo](#midi-and-tempo)).

![TEMPO](manual/img/move/move-tempo.png?v=c90ab784)
![ARPEGGIO, as a new Frodhes starts: OFF, 1/16, 1 octave, 60 %, HOLD OFF](manual/img/move/move-arpeggio.png?v=a491c235)

### LFO/SETUP, MAPPINGS

The filter's [LFO](MANUAL.md#lfo), the delay's tone, the output LEVEL, the MIDI channel (CHAN, ALL by default;
[SETUP](MANUAL.md#setup) and [MIDI](MANUAL.md#midi)), and the pressure mappings (Move only):

- **AT CC / AT LO / AT HI**: channel pressure (aftertouch) drives the control on that MIDI CC number, from LO (no
  pressure) to HI (full pressure).
- **PA CC / PA LO / PA HI**: the same for poly pressure, from the most pressed held pad. Letting a pad go ends its
  pressure.
- **VER**: the Frodhes version installed (a release's number; a development build shows DEV and the first digits
  of its commit), as on the FM-1's [MIDI](MANUAL.md#midi) page. Read only: turning the knob does nothing.

An HI under LO turns the direction round. A CC of 0 is off, and both mappings are off by default. The control moves
as if a controller sent that CC (the CC numbers are the FM-1's, the manual's [MIDI](MANUAL.md#12-midi)). A mapping
to one of the FM-1's developer and test CCs (2-4, 8-11, 41, 42, 114, 116 and 119) does nothing.

![LFO/SETUP](manual/img/move/move-lfo-setup.png?v=ef9ddf28)
![MAPPINGS](manual/img/move/move-mappings.png?v=c3e1bfdb)

## The list

**Click the jog wheel** for the list. It holds the pages you can reach (the one you are on marked `*`), and below them
the menu, each section under its name. Turn the jog wheel to a row and click to open it; Back closes the list. The
header counts the rows (`5/21`).

![The list: the pages, DRIVE/FILTER picked](manual/img/move/move-pages.png?v=7f4bde0a)
![Below the pages: USER PRESETS (SAVE TO USER) and MY PRESETS](manual/img/move/move-menu.png?v=f9915bc0)
![The MODULE rows: Module Help, Swap Module ...](manual/img/move/move-menu-module.png?v=cdae7dc8)

- **USER PRESETS, SAVE TO USER**: saves the sound into one of Frodhes' 16 user slots ([Presets and
  saving](#presets-and-saving)).
- **MY PRESETS**: Schwung's presets of the whole module: the preset you are on (a click opens Schwung's preset
  browser), Save, Save As (Schwung's keyboard) and Delete. Schwung keeps these, apart from U01-U16.
- **MODULE**: Schwung's rows for a module: Module Help (the help text of each page), Add to List, Clear Automation,
  Swap Module (another module in this slot) and Remove Module.

After Module Help, a preset load, Delete or a picker you leave, Back brings you to this list.

## Presets and saving

**To save the sound** into a user slot:

1. Click the jog wheel, turn to **SAVE TO USER** and click.
2. Turn the jog wheel (or any knob) to the slot. The card shows what it holds: EMPTY, or the name of the sound there.
3. Click to save (`CLICK: SAVE`, or `CLICK: REPLACE` over a used slot), or press Back to leave without saving
   (`BACK: NO`).

![SAVE TO USER: U03 picked, empty](manual/img/move/move-save.png?v=909188a5)

The slot then plays, named from the TYPE and the effects that are on, as on the FM-1, and `SAVED U03` shows. It is
written when Frodhes is quiet. The user slots are one set for every Frodhes on the Move: a sound saved in one shows
in the others within seconds.

The EQ is part of the sound, as on the FM-1: a user slot keeps it. The factory presets have it flat.

The sound you set is also saved with the Move's set: Schwung keeps it with the track, and loading the set brings it
back. SUS is not saved with it: a set that loads starts with SUS off and FX on, as the FM-1 does at power-on.

PRESET on the first page turns through the factory sounds and the 16 user slots, in this order:

- **F01-F19**, the FM-1's factory presets, the same sounds (described in the manual's [presets](MANUAL.md#6-presets)).
- **F20-F57, on the Move only**: two more versions of each FM-1 preset, made for the Move ([the
  list](#appendix-the-factory-presets-f20-f57)).
- **U01-U16**, the user slots.

Program change loads them as on the FM-1 (0-15 F01-F16, 16-31 U01-U16, 32-34 F17-F19). Program change 35-72 loads
F20-F57: the preset number plus 15 (F20 is 35, F57 is 72).

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
also sends each such note a second time at 127; Frodhes ignores that copy.)

Frodhes never changes a velocity it receives. RESP on the [VELOCITY](#velocity-resp) page changes how the electric piano
sounds at each velocity (FULL or LIGHT), not the velocity.

## MIDI and tempo

The MIDI of Schwung's track reaches Frodhes as on the FM-1 ([MIDI](MANUAL.md#12-midi)): notes with velocity and
release velocity, pitch bend, CC 64, the parameter CCs and program change. CHAN on LFO/SETUP picks the channel it
listens to (ALL by default).

The sequencer's and the chords' CCs (43-46, 56-63) do nothing on the Move. The arpeggiator's (52-55) work, as on the
FM-1.

**The tempo is the Move's.** Frodhes follows the Move's transport: its clock and Start / Stop or, when no clock comes,
its transport and beat position. The synced rates (TEMPO's TRSYNC, PHSYNC, DLSYNC, the chorus SYNC, the VIBES
motor's SYNC), the LFO's SYNC and the arpeggiator lock to it ([tempo and sync](MANUAL.md#8-tempo-and-sync)).

- While the Move is stopped they use the Move's tempo setting (120 BPM if the Move reports none).
- Frodhes has no BPM or CLOCK setting on the Move. Its tempo is kept within the FM-1's 40 to 240 BPM.
- When the Move starts, a synced LFO takes about 2 s to come into phase with the beat. It moves at most 1/8 faster
  or slower, so there is no jump.

**MIDI clock sent to the Move over USB-C does not reach Frodhes.** Schwung passes only the channel messages from
USB-C to a slot. To play Frodhes in time with a computer or another device, sync the Move to it: Frodhes then follows
the Move.

## The arpeggiator

**ARPEGGIO** is the FM-1's [arpeggiator page](MANUAL.md#arpeggio) (MODE, RATE, OCT, GATE) and its CCs 52-55. It
plays the notes you hold, on the Move's clock while the Move plays and on its tempo setting while it is stopped.

**HOLD** (knob 5) is the FM-1's HOLD, which the FM-1 sets with SEL:

- ON: the notes keep playing after you let the pads go. A chord you start after letting every pad go replaces them;
  pads added while others are held join the pattern.
- OFF: the notes you no longer hold stop.

HOLD has no CC, as on the FM-1. SUS sustains the notes the arpeggiator plays, as a pedal does, but does not hold the
pattern: with HOLD OFF, letting the pads go stops it.

Each Frodhes has its own arpeggiator settings, HOLD included, kept with the track. A Frodhes loaded new starts with
the arpeggiator and HOLD OFF.

Schwung has its own arpeggiator, a MIDI FX before the sound generator. Use one or the other: with both on, every note
is arpeggiated twice.

## Several Frodhes at once

Load Frodhes on as many tracks as Schwung has slots (four): each one is its own instrument, with its own sound, pages
and screen, and all of them share the user presets U01-U16. Up to three play with no dropouts. A fourth works, but
very heavy playing with every effect on in all four can drop audio, and so can loading a set that holds several of
them.

## Updating and removing

**Updating.** Unload Frodhes from every track first: a module that plays must not be replaced under it. Then install
the new version the same way as the first (From GitHub URL or From Tarball). Once Frodhes is in the catalog, Schwung
Manager shows a new version next to it (**Update**, or **Update All**). If the manager asks for a restart, restart.
An update keeps your settings and user presets.

**Removing.** Unload Frodhes from every track, open Frodhes in Schwung Manager and press **Uninstall**. Your settings
and user presets stay on the Move. To remove them too, delete the folder
`/data/UserData/schwung/module_data/frodhes`.

## What differs from the FM-1

- **16 voices** on every engine (the FM-1 plays 10). On VIBES and MARIMBA, more notes than voices at the same instant
  start 16 at a time, 5.8 ms apart, in order (the FM-1: 10 at a time; the manual's
  [sustain and polyphony](MANUAL.md#10-sustain-and-polyphony)).
- **The instruments are computed in more detail** than on the FM-1. The electric piano types have a more exact hammer
  strike at every velocity, a cleaner top octave and a truer repeated strike of a key that still rings. VIBES and
  MARIMBA have more of the bar's and the tube's modes, and a more exact mallet when a bar that still rings is struck
  again.
- **The reverb at the full sample rate** ([REVERB](#reverb)): the tails keep their top octave.
- **The screen**: one page is eight knobs (two of the FM-1's pages side by side), and the FM-1's knob card is the
  large card.
- **Buttons**: the Left arrow is FX and the Right arrow is SUS, on every page. SUS is a switch, on or off, in place
  of the FM-1's REC hold and tap.
- **Not on the Move**: the PERFORM, ASSIGN and DISPLAY pages (the Move has its own pads, knobs and screen); the
  sequencer, the chords and the scales (the Move has its own; their CCs do nothing); the BPM and CLOCK settings;
  KEY VEL, TOUCH, USB VOL, TRS, COLOR, BRIGHT and LEDS.
- **Only on the Move**: the factory presets F20-F57 ([Presets and saving](#presets-and-saving)), the pressure
  [mappings](#lfosetup-mappings), SAVE TO USER from the menu, Schwung's own presets, and several Frodhes at once.
- SPREAD and the compressor's RELEASE, set on the FM-1 by MIDI or a page of their own, are on the pages here.
- The sound is part of the Move's set.

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

## Appendix: the factory presets F20-F57

Two more versions (B and C) of each FM-1 preset, made for the Move.

| Preset | Name | Version of: what changes |
| --- | --- | --- |
| F20 | STAGE77 ECHO | F01 B: a soft dark analog echo on the quarter note, a little more hall |
| F21 | STAGE77 TAPE | F01 C: a tape echo on the dotted 1/8, a slightly bigger hall |
| F22 | SUITCASE DRY | F02 B: dry, a touch less cabinet noise, the level re-set |
| F23 | SUITCASE AMP | F02 C: the amp's warmth (BHLUE drive) and a short dark analog slap |
| F24 | FELT HALL | F03 B: dry, a bigger and softer hall |
| F25 | FELT ECHO | F03 C: the bigger hall and a soft dark analog echo on the quarter note |
| F26 | BELL SOFT | F04 B: dry, the top edge softer, a little more plate |
| F27 | BELL ECHO | F04 C: a short digital echo on the dotted 1/8, a little more plate |
| F28 | TREM ECHO | F05 B: a soft analog echo on the quarter note, the level re-set |
| F29 | TREM DEEP | F05 C: a slower, deeper vibrato, more warmth and plate |
| F30 | DRIVEN77 LVL | F06 B: as F06, the level re-set (half way between chord and phrase) |
| F31 | DRIVEN BODY | F06 C: more drive and body, a longer tape slap |
| F32 | BALLAD TIGHT | F07 B: less boom in the long plate's tail (LOW CUT 80) |
| F33 | BALLAD DARK | F07 C: a longer, darker analog echo tail, a slower chorus |
| F34 | DYNO80S LVL | F08 B: as F08, the level re-set |
| F35 | DYNO80S SOFT | F08 C: the echo softer and darker, less chorus |
| F36 | PHASER LVL | F09 B: as F09, the level re-set |
| F37 | PHASER SLOW | F09 C: a slower, more liquid sweep, a longer analog echo |
| F38 | FUNK WAH LVL | F10 B: as F10, the level re-set |
| F39 | FUNK PLUCK | F10 C: less squash, more pluck (2:1, slower attack), a short dark slap |
| F40 | RESO SPACE | F11 B: more echo and hall |
| F41 | RESO SMOOTH | F11 C: a smoother peak (less resonance), a wider sweep |
| F42 | AUTOWAH ECHO | F12 B: a digital echo on the quarter note |
| F43 | AUTOWAH TAPE | F12 C: a tape echo on the dotted 1/8 and a plate |
| F44 | VIBES HALL | F13 B: dry, a little more hall |
| F45 | VIBES MOTOR | F13 C: the slower, deeper motor, more hall |
| F46 | VIBES CLEAN | F14 B: a cleaner echo (fewer repeats) |
| F47 | VIBES DRIFT | F14 C: the motor on, slow and shallow, under the echo |
| F48 | MARIMBA HALL | F15 B: dry, more hall and pre-delay |
| F49 | MARIMBA ECHO | F15 C: a soft analog echo on the quarter note |
| F50 | WOOD PLAIN | F16 B: the chorus off |
| F51 | WOOD LIGHT | F16 C: a lighter chorus, less echo and hall |
| F52 | TAPE WIDE | F17 B: tamer repeats and a touch of chorus |
| F53 | TAPE DARK | F17 C: the echo on the quarter note, darker repeats |
| F54 | DREAM CLEAR | F18 B: less chorus wobble under the hall |
| F55 | DREAM DEEP | F18 C: a slower, deeper pan, the echo on the dotted 1/4 |
| F56 | RADIO LVL | F19 B: as F19, the level re-set |
| F57 | RADIO LOW | F19 C: the band a little lower: more body |

Building Frodhes for the Move, its tests and installing over ssh are in [MOVE-DEV.md](MOVE-DEV.md).
