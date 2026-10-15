# Frodhes: user manual

**A physically modelled Fender Rhodes for the M-VAVE FM-1.** Version 0.8.

Frodhes turns the FM-1 into an electric piano. Every note is computed from the physics of a real Rhodes: the hammer
strikes a tine, the tine rings against its tonebar, and a magnetic pickup turns the motion into sound. No recordings
are played back. Around the piano sits an effects chain modelled on the Yamaha reface CP: compressor, drive,
tremolo or wah, phaser, chorus, delay and reverb.

![The PIANO, VOICE, PHASER and COMPRESSOR pages](manual/img/overview.png)

## Contents

1. [Install](#1-install)
2. [The idea: one page, four knobs](#2-the-idea-one-page-four-knobs)
3. [Buttons and knobs](#3-buttons-and-knobs)
4. [The pages](#4-the-pages)
5. [Presets](#5-presets)
6. [Effects on and off](#6-effects-on-and-off)
7. [Tempo and sync](#7-tempo-and-sync)
8. [Scales](#8-scales)
9. [Sustain](#9-sustain)
10. [Sound and volume tips](#10-sound-and-volume-tips)
11. [MIDI](#11-midi)
12. [If something goes wrong](#12-if-something-goes-wrong)

## 1. Install

1. Download `frodhes-0.8.fwsc` from the
   [releases page](https://github.com/leonardoMirabal/frodhes/releases/latest).
2. Connect the FM-1 to your computer with USB.
3. Install it from a terminal with the installer in this repository:
   `python3 tools/fm1_install.py frodhes-0.8.fwsc` (it needs `pip install mido python-rtmidi`). Once Frodhes is listed
   on [thefirmwarehub.com](https://thefirmwarehub.com), its web installer works too (Chrome or Edge).
4. The FM-1 restarts into Frodhes. If it does not come back by itself, switch it off and on.

To go back to M-VAVE's own firmware, use the hub's "Official firmware" section with the V15 file from m-vave.com.

## 2. The idea: one page, four knobs

The screen shows **one page at a time**, with **four cards**, one per knob: **K1–K4 always turn the four cards you
see**. The page name is at the top, with a row of marks under it showing where you are. Turn **SELECT** to move
between pages, or press a page's button to jump straight there.

The top line shows the preset, the octave (O+1, O−2...), **SUS** while the sustain is held, **BYP** while the effects
are bypassed, and the tempo. The bottom line explains the page's button or shows a live reading; when you change a
preset or switch an effect it briefly shows what happened, in large letters.

## 3. Buttons and knobs

| Control | What it does |
| --- | --- |
| **SELECT** (knob) | Next / previous page |
| **K1 K2 K3 K4** | The four cards on the current page |
| **ALGORITHM** (knob) | The Rhodes TYPE, from any page |
| **PRESETS** (knob) | Browse the sounds F01–F12 and your U01–U16 (loads when you stop turning) |
| **MASTER** (knob) | Volume |
| **HOME** | PIANO page |
| **EDIT** | VOICE page |
| **ENV** | DECAY page; press again for COMPRESSOR; on COMPRESSOR it switches the compressor on / off |
| **LFO** | TREM/WAH page; press again there to switch it on / off |
| **SEQ** | PHASER page; press again there to switch it on / off |
| **SEL** | CHORUS page; press again there to switch it on / off |
| **ARP** | DELAY page; press again there to switch it on / off |
| **GLO** | TEMPO page; press again for SETUP |
| **FX** | All effects off / on at once (see [6](#6-effects-on-and-off)) |
| **PLAY** | Tap tempo |
| **REC** | Sustain pedal |
| **SAVE** | Save the sound to a user preset; hold to undo the last preset load |
| **OCT− / OCT+** | Octave down / up (±2) |

The lights tell you what is on: LFO, SEQ, SEL, ARP and ENV light up when their effect is on, FX when the effects are
active, REC while the sustain holds, PLAY on the beat when something is synced, and HOME / EDIT on their page.

## 4. The pages

### PIANO

![PIANO](manual/img/piano.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TYPE** | The instrument: **STAGE 77** (the classic Mark I), **SUITCASE** (with its Peterson amp and 4×12 speakers), **EARLY FELT** (1970 felt hammers: rounder, darker), **BELL DYNO** (Dyno-My-Piano voicing: glassy, bell-like). Applies to the next notes. |
| K2 | **DRIVE** | Amp drive, from clean to growl. It changes the tone, not the volume. |
| K3 | **REVERB** | Hall reverb amount |
| K4 | **KEY VEL** | How hard the FM-1's own keys strike. The keys are not velocity-sensitive, so this decides the touch: around 60–80 soft and round, 96 medium, 108–127 hard with bark. MIDI notes keep their own velocity. |

### VOICE

![VOICE](manual/img/voice.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TONE** | Where the tine sits against the pickup: lower is rounder and warmer, higher is more bell and midrange |
| K2 | **BARK** | How close the pickup is: more bark (the growl of hard notes) and more output |
| K3 | **BELL** | The bright metallic overtone at the start of each note |
| K4 | **HAMMER** | Hammer hardness: softer is darker, harder is brighter |

### DECAY

![DECAY](manual/img/decay.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **DECAY** | How long a held note rings |
| K2 | **RELEASE** | How fast the damper stops a note when you let go |
| K3 | **NOISE** | The damper's soft knock and the felt's sound when you let go |
| K4 | **TUNE** | Fine tuning, ±50 cents |

### COMPRESSOR

![COMPRESSOR](manual/img/comp.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **THRESH** | Where the compression starts: 0 to −40 dB. Lower = more compression |
| K2 | **RATIO** | How strongly it compresses: 1.5:1 to 20:1 |
| K3 | **ATTACK** | How fast it grabs a note: low lets the hammer's attack through, high catches it |
| K4 | **MAKEUP** | Gives back the volume the compression takes: 0 to +24 dB |

Press **ENV** on this page to switch it on or off. When you switch it on it starts at −20 dB, 4:1 and +2.8 dB, which
keeps the volume about the same. The bottom of the page is a gain-reduction meter, as on a hardware compressor: its segments light from 1 dB
to 30 dB of reduction (scale 3, 6, 10, 20, 30), a bright segment holds the peak for a second, and the number on the
right is the reduction now in dB. The compressor
comes first in the chain, right after the piano.

### TREM/WAH

![TREM/WAH](manual/img/trem.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | **OFF**, **PAN** (the Suitcase stereo vibrato), **TREM** (volume tremolo), **WAH** (touch wah: harder playing opens it) |
| K2 | **DEPTH** | How deep |
| K3 | **RATE** | How fast; on WAH, the resonance. When synced it shows a note value |
| K4 | **SHAPE** | From a smooth sine to a square, choppy pulse |

### PHASER

![PHASER](manual/img/phaser.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | Off / on |
| K2 | **DEPTH** | How far it sweeps |
| K3 | **SPEED** | How fast (a note value when synced) |
| K4 | **FEEDBK** | Resonance: more pronounced, vocal sweeps |

### CHORUS

![CHORUS](manual/img/chorus.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | Off / on |
| K2 | **DEPTH** | Amount and width |
| K3 | **SPEED** | How fast (a note value when synced) |
| K4 | **SYNC** | Free, or locked to the tempo as a note value |

Phaser and chorus are two separate effects and can run together (the phaser feeds the chorus). Both make the sound
wider in stereo, which on headphones sounds bigger; their level is set so that it does not also sound louder.

### DELAY

![DELAY](manual/img/delay.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | **OFF**, **DIG** (clean digital), **ANA** (tape-like: darker, with a little wow and flutter) |
| K2 | **MIX** | How loud the echoes are |
| K3 | **TIME** | Echo time, free in milliseconds; when DLY SYNC is set on TEMPO it shows and steps note values |
| K4 | **FEEDBK** | How many echoes |

### TEMPO

![TEMPO](manual/img/tempo.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **BPM** | Tempo, 40–240. **PLAY** taps it (from the third tap) |
| K2 | **TREM SYNC** | The tremolo locked to the tempo (FREE = its own RATE) |
| K3 | **PHA SYNC** | The phaser locked to the tempo |
| K4 | **DLY SYNC** | The delay locked to the tempo |

### SCALE

![SCALE](manual/img/scale.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **MODE** | **OFF**; **SNAP** (every key plays, out-of-scale notes move down onto the scale); **DEGREE** (the white keys walk the scale, the black keys are silent) |
| K2 | **SCALE** | Major, minor, dorian, mixolydian, lydian, phrygian, harmonic and melodic minor, pentatonic, minor pentatonic, blues |
| K3 | **ROOT** | The scale's key |
| K4 | **TRANSP** | Transpose ±24 semitones |

### SETUP

![SETUP](manual/img/setup.png)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **CLOCK** | Tempo source: **INT** (internal), **AUTO** (follows USB MIDI clock when it arrives), **USB** |
| K2 | **DLY TONE** | The delay's echo colour |
| K3 | **LEVEL** | This preset's volume, ±12 dB, saved with the preset |

The bottom line shows the audio engine's time per block (it must stay under about 2900 µs).

## 5. Presets

![Browsing presets](manual/img/browse.png)

Turn **PRESETS** to browse: **F01–F12** are the factory sounds, **U01–U16** your own. A preset loads when you stop
turning (after 0.4 s). If you did not mean to, **hold SAVE** to go back to what you had (undo).

| | | | |
| --- | --- | --- | --- |
| F01 STAGE 77 | F02 70S PHASER | F03 SUITCASE | F04 80S BALLAD |
| F05 SLAPBACK | F06 BRIGHT CHO | F07 FUNK WAH | F08 BARK |
| F09 TAPE ECHO | F10 DREAM | F11 RADIO | F12 AM TREMOLO |

**Saving:** press **SAVE**. The bottom line asks where; turn **PRESETS** or **K1** to pick U01–U16; press **SAVE**
again to store it, or **HOME** to cancel. The name is made from the type and the effects that are on
("SUIT TRM PHA").

![Save mode](manual/img/save.png)

## 6. Effects on and off

Each effect has its own on / off: its page button pressed twice (LFO, SEQ, SEL, ARP, and ENV for the compressor), or
its EFFECT card. Switching an effect on whose depth is 0 gives it a useful starting depth, so you always hear it.

**FX** switches **all** effects off at once and back on, without changing any setting: compare the dry piano with
your sound. The header shows **BYP** while they are off. It is not saved, so the FM-1 always starts with the effects
on.

![FX bypass](manual/img/bypass.png)

The order of the chain: **COMP → DRIVE → TREM/WAH → PHASER → CHORUS → DELAY → REVERB → limiter.** The limiter at the
end keeps even big chords of eight notes from ever clipping.

## 7. Tempo and sync

Tap **PLAY** three times or more in time to set the tempo, or set **BPM** on the TEMPO page. With **CLOCK** on AUTO
(the default), Frodhes follows MIDI clock from the USB host. The tremolo, phaser and delay can each lock to the
tempo on the TEMPO page (TREM SYNC, PHA SYNC, DLY SYNC). When one is synced, its RATE / SPEED / TIME card shows a note
value (1/16, 8T, 1/4 ...) and turning it steps through note values. PLAY blinks on the beat while anything is synced.

## 8. Scales

On the SCALE page, **SNAP** lets every key play but moves out-of-scale notes onto the scale, and **DEGREE** turns the
white keys into the scale's steps (C plays the root) so you can play any scale with white keys only.

## 9. Sustain

**REC** is the sustain pedal. **Hold** it like a pedal, or **tap** it to latch the sustain on, and tap again to
release. The header shows **SUS** while it holds. Over MIDI, CC 64 works with half-pedalling.

## 10. Sound and volume tips

- **KEY VEL** on the PIANO page is your touch. 96–108 is a good start; the bark comes in above about 100.
- **MASTER is digital.** For the least hiss on the headphone jack, keep MASTER high and turn your speakers or
  headphones down instead. Between notes the output is exact silence, so the jack is silent too.
- **TYPE** changes the whole instrument: try BELL DYNO for glassy ballads and SUITCASE for the classic vibrato with
  the tremolo on PAN.
- Every effect is set to keep about the same volume when you switch it on; the compressor's MAKEUP and each preset's
  LEVEL (SETUP) let you fine-tune it.

## 11. MIDI

Frodhes is a class-compliant USB-MIDI device. Notes play with their velocity; the FM-1's keys also send their notes
to the computer. **Program Change** 0–11 loads F01–F12, 16–31 loads U01–U16.

| CC | Parameter | CC | Parameter |
| --- | --- | --- | --- |
| 64 | Sustain (half-pedal) | 80 | TYPE (0–31 STAGE, 32–63 SUITCASE, 64–95 FELT, 96–127 DYNO) |
| 81 | DRIVE | 91 | REVERB |
| 17 | TREM/WAH: 0–42 off, 43–85 trem/pan, 86–127 wah | 18 / 19 | TREM DEPTH / RATE |
| 20 / 21 / 22 | TREM SYNC / MODE (pan, am) / SHAPE | 85 | Chorus / phaser (reface): off / chorus / phaser |
| 86 / 87 | CHORUS DEPTH / SPEED (the phaser's, when only the phaser is on) | 23 | CHORUS SYNC |
| 12 / 13 / 14 | PHASER on / DEPTH / SPEED | 15 / 16 | PHASER SYNC / FEEDBACK |
| 88 / 89 / 90 | DELAY type / MIX / TIME | 25 / 26 / 27 | DELAY SYNC / FEEDBACK / TONE |
| 106 / 107 | COMP on / THRESHOLD | 112 / 113 | COMP RATIO / MAKEUP |
| 108 / 109 | COMP ATTACK / RELEASE | 110 | LEVEL (64 = 0 dB) |
| 111 | All effects off (≥ 64) / on | 28–31 | TONE / BARK / BELL / HAMMER |
| 102 / 103 / 104 | DECAY / RELEASE / NOISE | 120 / 123 | All sound off / all notes off |

The FM-1 also appears as a USB audio input: you can record exactly what it plays, digitally, without the headphone
jack's hiss.

## 12. If something goes wrong

- **No sound:** check MASTER, that FX has not bypassed a sound you relied on (BYP in the header), and LEVEL on SETUP.
- **The sound is thin or quiet:** raise KEY VEL on the PIANO page.
- **A crash** shows a message with a PC number, then the FM-1 restarts. Please report the number.
- **It does not start:** switch the FM-1 off, hold **OCT−** and switch it on. That is the USB rescue: install again
  from the hub or with `fm1_install.py`. Your FM-1 can always be recovered this way.
- **Back to the original firmware:** the hub's "Official firmware" section, with V15 from m-vave.com.

---

Frodhes is free software under the GPL-3.0 licence, built on [Dinghy](https://github.com/zvenson/dinghy),
[Felucca](https://github.com/hugelton/Felucca) and [SLOOP](https://github.com/isod89/sloop-fm1). Source and
issues: [github.com/leonardoMirabal/frodhes](https://github.com/leonardoMirabal/frodhes).
