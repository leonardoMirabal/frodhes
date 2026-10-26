# Frodhes: user manual

**A physically modelled electric piano, vibraphone and marimba for the M-VAVE FM-1.**

Frodhes turns the FM-1 into an electric piano. Every note is computed from the physics of a real Rhodes: the hammer
strikes a tine, the tine rings against its tonebar, and a magnetic pickup turns the motion into sound. As on a real
instrument, no two keys are quite the same: each one rings a little longer or shorter and a little louder or softer
than its neighbours (every key stays exactly in tune), and a held note from E2 to C6 has the tine's slow whirl: its
level wobbles gently, about 2 dB, once or twice a second, as the tine's two ways of swinging trade energy. Struck
hard, the bass goes a touch sharp and settles as it rings. Two struck-bar
instruments, a vibraphone and a marimba, are computed the same way (section 5). No recordings
are played back. Around the piano sits a stage-keyboard effects chain: compressor, drive (five overdrive types), a resonant filter,
tremolo or wah, phaser, chorus, delay and reverb. For playing there is a PERFORM page with four assignable knobs,
chord mode, a pattern recorder (sequencer), an arpeggiator, scales and tap tempo, USB-MIDI in and out and TRS MIDI in.

Frodhes is an independent open-source project, not affiliated with or endorsed by M-VAVE, the Rhodes or Fender trademark owners or any instrument maker. It comes with no warranty: install it at your own risk (M-VAVE's own firmware can always be put back); see [LICENSING.md](../LICENSING.md).

![The FRODHES page: TYPE (here EARLY FELT), TONE, BELL and KEY VEL](manual/img/piano.png?v=904c35f2)

## Contents

1. [Install](#1-install)
2. [The idea: one page, four knobs](#2-the-idea-one-page-four-knobs)
3. [Buttons and knobs](#3-buttons-and-knobs)
4. [The pages](#4-the-pages)
5. [VIBES and MARIMBA: the bar instruments](#5-vibes-and-marimba-the-bar-instruments) (VIBES: [the vibraphone](#vibes-the-vibraphone), the motor [on and off](#the-motor-on-and-off); [MARIMBA](#marimba))
6. [Presets](#6-presets)
7. [Effects on and off](#7-effects-on-and-off)
8. [Tempo and sync](#8-tempo-and-sync)
9. [Scales](#9-scales)
10. [Sustain and polyphony](#10-sustain-and-polyphony)
11. [Sound and volume tips](#11-sound-and-volume-tips)
12. [MIDI](#12-midi)
13. [If something goes wrong](#13-if-something-goes-wrong)
14. [Specifications](#14-specifications)
15. [Appendix: the SMK-37 (experimental)](#15-appendix-the-smk-37-experimental)

## 1. Install

1. Download [`frodhes.fwsc`](https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes.fwsc), the latest release (its notes and
   earlier versions are on the [releases page](https://github.com/leonardoMirabal/frodhes/releases/latest)).
2. Connect the FM-1 to your computer with USB.
3. Install it from a terminal with the installer in this repository:
   `python3 tools/fm1_install.py frodhes.fwsc` (it needs `pip install mido python-rtmidi`). Once Frodhes is listed
   on [thefirmwarehub.com](https://thefirmwarehub.com), its web installer works too (Chrome or Edge).
4. The FM-1 restarts into Frodhes. If it does not come back by itself, switch it off and on.

While an install writes, the screen shows FRODHES, **WRITING** (do not switch off) and a bar with the percent. If an
install was cut off (the cable pulled, the computer asleep), the FM-1 comes back to that screen at every power-on,
showing **UPDATE MODE** (waiting for the install) or **UPDATE FAILED**: connect it and install again, from
`fm1_install.py` or the hub.

Settings, user presets and the sequencer's pattern saved by earlier versions are read as they are. From a version
with 14 factory presets (before MARIMBA), the saved preset number is moved past the two MARIMBA presets once, so the
FM-1 comes back on the preset it was left on. The factory presets themselves can change from one version to the next;
a user preset you saved keeps its own sound.

<img src="manual/img/splash.png?v=86b49529" alt="The start screen: FRODHES, ELECTRIC PIANO + VIBES + MARIMBA" width="240" align="right">

Frodhes starts with its own screen: the name, its three instruments (ELECTRIC PIANO + VIBES + MARIMBA) and the version installed. It gives way to the PERFORM page after a moment,
or as soon as you touch a button or a knob.
<br clear="right">

To go back to M-VAVE's own firmware, use the hub's "Official firmware" section with the V15 file from m-vave.com.

## 2. The idea: one page, four knobs

The screen shows **one page at a time**, with **four cards**, one per knob, drawn as rows from K1 at the top to K4
at the bottom: **K1–K4 always turn the four cards you see**. Under the top line, the page's name is on the left in
amber and its place in the SELECT order on the right (3/23). Turn **SELECT** to move between pages, or press a
page's button to jump straight there. The pages, in SELECT order:

PERFORM · FRODHES (main) · VOICE 1/2 · VOICE 2/2 · COMPRESSOR · DRIVE · FILTER · TREM/WAH · PHASER · CHORUS ·
DELAY · REVERB · REVERB 2 · TEMPO · SCALE · CHORD · ARPEGGIO · LFO · SEQUENCER · ASSIGN · SETUP · MIDI · DISPLAY

**The VOICE pages belong to the instrument.** Each TYPE brings its own controls, four to a page: the Rhodes types
have two VOICE pages (VOICE 1/2: TONE, BARK, BELL, HAMMER; VOICE 2/2: DECAY, RELEASE, NOISE, TUNE), VIBES has three
(VOICE 1/3: STRIKE, MALLET, TUBE, WEIGHT; 2/3: RING, DAMPER, NOISE, TUNE; 3/3: the motor), MARIMBA two (VOICE 1/2:
STRIKE, MALLET, TUBE, WEIGHT; 2/2: RING, MUTE, NOISE, TUNE). SELECT walks them in order; changing the TYPE changes
the pages with it.

**Reading a card.** Each card shows its knob (K1–K4, small), the control's name, and its value on the right in large
figures, every value the same size and ending at the same place, with the unit beside it in small letters (Hz, kHz,
ms, s, dB, c for cents, %). What is drawn behind the value tells you at a glance what kind of control it is:

- **An amount** (DEPTH, MIX, a time, a frequency ...): the card is a fader. It fills with warm amber from the left up
  to the value, with a bright amber edge where the fill ends (the edge stops short of the letters, so it never cuts a
  number): empty at 0, full at the top.
- **A selector** (TYPE, EFFECT DIG / ANA / TAPE, a filter type, a note value, a scale, a chord type ...): the card is cut
  into one block per option, and the blocks light up to the option it is on, each with a small cap at its foot (amber
  lit, grey not); the option is written out in full (**STAGE 77**, **LP18**, **1/16**). An option called OFF lights no
  block. With more than 16 options (MIDI CHANNEL, ASSIGN) only the caps are drawn, as a ruler. A rate or a time synced
  to the tempo is a selector too: its note value.
- **A switch** (ON / OFF: an effect on its page, the motor, TRS IN): a round light before the word, amber when ON, an
  empty ring when OFF.
- **A knob with named spots** (the bar instruments' STRIKE: CENTRE, L/3, EDGE; MALLET on VIBES: SOFT, YARN, CORD,
  RUBBER, on MARIMBA: SOFT, MED, HARD, RUBBER): a fader, with the name when the knob is on one, else the number
  between two of them.

A few names too long for their card are written shorter, in the same large letters: on ASSIGN **TRM/WAH**, **TRM
RATE**, **TRM SHP**, **PHA SPD**, **PHA FDBK**, **CHO SPD**, **DLY FDBK**, **CMP GAIN** (the compressor's make-up).

**The card you are turning** gets a thin amber frame and its name turns amber (white where it sits on the fill), for about a second after you stop.
The numbers are rounded to what fits on the card (1.486 s shows 1.5 s, +11.8 dB shows +12 dB).

The top line shows the preset, the octave (O+1, O−2...), **SUS** while the sustain is held, **BYP** while the effects
are bypassed, the tempo and, last, the battery. The battery has three segments (the last one left turns the accent colour,
an empty outline means charge it soon); while USB powers the FM-1 it shows a bolt instead. It follows the battery
slowly (over about ten seconds), so it does not flicker. The levels are a first estimate and may be adjusted. The bottom line explains the page's button or shows a live reading; when you change a
preset or switch an effect it briefly shows what happened, in large letters.

## 3. Buttons and knobs

| Control | What it does |
| --- | --- |
| **SELECT** (knob) | Next / previous page |
| **K1 K2 K3 K4** | The four cards on the current page |
| **ALGORITHM** (knob) | The TYPE (the four Rhodes, VIBES and MARIMBA), from any page (on SEQUENCER: the step cursor) |
| **PRESETS** (knob) | Browse the sounds F01–F19 and your U01–U16 (loads when you stop turning; on SEQUENCER: bar by bar) |
| **MASTER** (knob) | Volume |
| **HOME** | PERFORM page; press again for the main page (titled FRODHES), and back |
| **EDIT** | CHORD page; press again there to switch chord mode on / off (it comes back with the last chord type) |
| **ENV** | PHASER page; press again there to switch the phaser on / off |
| **LFO** | TREM/WAH page; press again there to switch it on / off |
| **SEQ** | SEQUENCER page; there it switches recording on / off, held 1 s it clears the pattern |
| **SEL** | DELAY page; press again there to switch the delay on / off. On ARPEGGIO, and on every page while the arpeggiator is on: the arpeggiator's HOLD on / off |
| **ARP** | ARPEGGIO page; press again there to switch the arpeggiator on / off |
| **GLO** | TEMPO page; press again for SETUP, again for MIDI, again for DISPLAY, and back to TEMPO |
| **FX** | All effects off / on at once (see [7](#7-effects-on-and-off)) |
| **PLAY** | Tap tempo, on every page (ARPEGGIO too); on SEQUENCER, start / stop the loop, except while the arpeggiator is on: then it taps the tempo there too |
| **REC** | Sustain pedal |
| **SAVE** | Save the sound to a user preset; hold to undo the last preset load |
| **OCT− / OCT+** | Octave down / up (±2) |

The lights tell you what is on: LFO (tremolo / wah), ENV (phaser) and SEL (delay) light up when their effect is on
(on ARPEGGIO, and on every page while the arpeggiator is on, SEL shows HOLD), ARP while the arpeggiator runs, EDIT while chord mode is on, SEQ while the sequencer
plays (blinking while it records), FX when the effects are
active, REC while the sustain holds (from MIDI CC 64 too), PLAY on the beat when something is synced (and while the
sequencer or the arpeggiator plays), HOME on PERFORM and the main page, GLO on TEMPO, SETUP, MIDI and DISPLAY,
OCT− / OCT+ while the octave is shifted, SAVE blinking in save mode, and each key while it is held, or its note is
held over MIDI (USB or TRS), or the sustain holds it and it still rings (a note let go before the pedal went down
stays dark; see LEDS on the DISPLAY page).

## 4. The pages

### PERFORM

![PERFORM](manual/img/perform.png?v=765b8fe1)

The first page, made for playing: four knobs for the sound while you play. Out of the box they follow the effects
chain from left to right: the drive, the filter's cutoff, and the dry / wet of the delay and the reverb:

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **DRIVE** | How much overdrive (the DRIVE page's AMOUNT) |
| K2 | **FILTER** | The filter's cutoff, from fully open (16 kHz: no filter heard) down (turning it down with the filter off switches it on as LP18) |
| K3 | **DELAY** | How much delay |
| K4 | **REVERB** | How much reverb |

The tremolo / wah, phaser and chorus have their own buttons and pages, and any of them can be put on a knob on
**ASSIGN**. Updating from 0.9.4 or earlier: if you never changed the assignment (or it was the old default, TREM/WAH,
PHASER, FILTER, DELAY), the knobs become the new default; if you changed it, it stays as you set it.

Turning an effect up from here **switches it on**, so you can go from dry to wet with one knob; its light comes on.
Turning it back to 0 makes it dry again (the effect stays switched on; its button switches it off). **FX** still bypasses all of them at once.

### FRODHES (the main page)

The page after PERFORM (press **HOME** twice) carries the instrument's name as its title and holds the essentials.

![The main page](manual/img/piano.png?v=904c35f2)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TYPE** | The instrument: **STAGE 77** (the classic stage piano), **SUITCASE** (with its own amp and four 12-inch speakers in a closed cabinet: warm lows, a gently rolled-off treble), **EARLY FELT** (early felt hammers: rounder, darker), **BELL DYNO** (a modified, bell-like voicing: glassy), **VIBES**, the vibraphone (see [VIBES](#vibes-the-vibraphone)), or **MARIMBA** (see [MARIMBA](#marimba)). Applies to the next notes. |
| K2 | **TONE** | Where the tine sits against the pickup: lower is rounder and warmer, higher is more bell and midrange (the same as VOICE 1/2's TONE). On VIBES and MARIMBA: **MALLET** |
| K3 | **BELL** | The bright metallic overtone at the start of each note (the same as VOICE 1/2's BELL). On VIBES: **MOTOR** on / off; on MARIMBA: **TUBE** |
| K4 | **KEY VEL** | How hard the FM-1's own keys strike: the velocity of a medium press (TOUCH on [SETUP](#setup); harder presses play louder, softer ones quieter), and with TOUCH at **FIXED** the velocity of every press. Around 60–80 soft and round, 96 medium (the default), 108–127 hard with bark. MIDI notes keep their own velocity. |

### VOICE 1/2 and 2/2

The instrument's own pages: on a Rhodes type there are two, right after the main page. On **VIBES** there are three,
with the vibraphone's controls: see [VIBES](#vibes-the-vibraphone). On **MARIMBA** there are two, with the marimba's
controls: see [MARIMBA](#marimba).

![VOICE 1/2](manual/img/voice1.png?v=a12de415)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TONE** | Where the tine sits against the pickup: lower is rounder and warmer, higher is more bell and midrange, brighter all the way to the top at the same loudness |
| K2 | **BARK** | How close the pickup is: more bark (the growl of hard notes) and more output |
| K3 | **BELL** | The bright metallic overtone at the start of each note |
| K4 | **HAMMER** | Hammer hardness: softer is darker, harder is brighter |

![VOICE 2/2](manual/img/voice2.png?v=7b9554bc)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **DECAY** | How long a held note rings |
| K2 | **RELEASE** | How fast the damper stops a note when you let go |
| K3 | **NOISE** | The instrument's mechanical sounds: the damper's soft knock and the felt's sound when you let go, a soft thud when the sustain pedal (or REC's latch) goes down or up, and (on SUITCASE, EARLY FELT and BELL DYNO, not on STAGE 77) the hammer's thunk as a key is struck. The key-up and pedal sounds follow the note: they are always under it, quieter after a soft or a faded note, so letting go never makes the sound louder. 0 is none |
| K4 | **TUNE** | Fine tuning, ±50 cents |

### COMPRESSOR

![COMPRESSOR](manual/img/comp.png?v=14f143cb)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **THRESH** | **OFF** at 0: turn it up to switch the compressor on; then where it starts, down to −40 dB. Lower = more compression |
| K2 | **RATIO** | How strongly it compresses: 1.5:1 to 20:1 |
| K3 | **ATTACK** | How fast it grabs a note: low (0.1 ms) catches the hammer's attack, high (50 ms) lets it through |
| K4 | **MAKEUP** | Gives back the volume the compression takes: 0 to +24 dB |

**THRESH** is its on / off: turn it up from OFF and the compressor comes on at −16 dB (where the presets set it) with
MAKEUP at +2.8 dB, which keeps the volume of what you play about the same (a quiet, dying tail comes up, as under
any compressor; back to OFF switches it off). Switching it on or off fades
over about 46 ms, so it never clicks. The bottom of the page is a gain-reduction meter, as on a hardware compressor: its segments light from 1 dB
to 30 dB of reduction (scale 3, 6, 10, 20, 30), a bright segment holds the peak for a second, and the number on the
right is the reduction now in dB. The compressor
comes first in the chain, right after the piano.

### DRIVE

![DRIVE](manual/img/drive.png?v=60e12a55)

The amp drive, right after the compressor (before the filter). AMOUNT can also be put on a PERFORM knob (DRIVE on ASSIGN). There are five drives, all
overdrive types (none of them a fuzz): **FDRV** is the original Frodhes drive, and the other
four each follow a classic overdrive circuit type faithfully (its filters, its clipping and its tone stage, computed
live like the instruments, never sampled).

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | The drive's type, or **OFF**: **FDRV**, **KHLON**, **TSH8**, **OHCD** or **BHLUE** (below). Switching it on with AMOUNT at 0 gives it a starting amount. A new type takes over smoothly: the one playing fades out, then the new one fades in (about a tenth of a second); only one type plays at a time |
| K2 | **AMOUNT** | How hard the piano drives the type, from clean to its fullest (what each type does on the way is below); at 0 there is no drive |
| K3 | **TONE** | The type's own tone control (below). On FDRV the middle leaves the sound as it is; on the other four TONE is the circuit's own tone knob, so its middle is that circuit's usual voice, not always flat |
| K4 | **MIX** | Dry / driven, the same on every type: 10.0 is all drive (as before), lower blends the clean piano back in, 0 is clean |

The types:

| Type | The sound | AMOUNT | TONE |
| --- | --- | --- | --- |
| **FDRV** | The original Frodhes drive, unchanged: a warm amp-like overdrive | Low settings warm the sound a little, above the middle a single note starts to break up, the top is a full, compressed overdrive. It changes the tone, not the volume (big chords come out a little lower at the top, as an amp squashes them) | Darker (down to the highs gone) or brighter (up to +6 dB of treble); the middle (+0.0) leaves it as it is |
| **KHLON** | A clean-blend overdrive: clean paths run beside the clipped one (soft germanium-style diodes), so the notes keep their body and the low end stays mostly clean until AMOUNT is high | Low: a clean boost, the sound a little thicker, hardly any grit; the middle: a light edge on the attacks; the top: a full overdrive, the bass breaking up too. Turning AMOUNT onto its last step (10.0), or back off it, passes briefly through the clean sound, without a click | An active treble control from about 400 Hz up: a cut at the bottom, a little lift at the middle, a strong treble boost at the top. With AMOUNT and TONE both near the top a fine fizz rides on the high notes |
| **TSH8** | A mid-hump overdrive: the clean sound plus a clipped, mid-focused copy (soft, symmetric clipping). The bass is driven less than the mids, so chords stay clear and single notes sing. Set for a soft input, so it stays smooth | Low: a light edge in the mids; the middle: a warm crunch; the top: a singing, compressed overdrive | Dark through most of its travel; the highs open in the last quarter. It never lifts the treble above flat |
| **OHCD** | A MOSFET overdrive: a firmer edge (a sharper clipping knee) and a mid-focused sound. AMOUNT covers the circuit's own drive knob over its whole range | Low: nearly clean with a little bite; the middle: a crunchy but still open overdrive; the top: a thick, saturated drive | A treble cut: darker counter-clockwise, brightest at the top |
| **BHLUE** | A two-stage soft overdrive, transparent and amp-like: soft diodes that never clip hard, the low end driven much less than the mids | Low: almost clean, a little warmer; the middle: a light crunch; the top: a thick, round overdrive | A treble cut: at 0 the highs are gone above about 400 Hz, the middle rolls off from about 500 Hz, the top is the brightest (open to about 2 kHz) |

Each type is set to about the loudness of the drive switched off on played phrases (chords, a bass line, a melody), so
switching types, or switching the drive on, changes the character of the sound, not its volume. Like any overdrive
they also even the level out: a long note's attack is held down and its fading tail held up, so a held chord's tail
rings a few dB louder than with the drive off. The four circuit types lean to the middle: a single note in the middle
of the keyboard comes out louder than with the drive off (about 4–9 dB at AMOUNT 80); KHLON, TSH8 and OHCD also make
the lowest notes softer (C2 about 3–6 dB).

When the drive is off, a DRIVE knob on PERFORM shows **OFF**; turning it up there switches
the drive on with the type you used last (after the FM-1 is switched off and on: the preset's type, or FDRV if the
drive is off in it). The type is part of the sound and is saved in presets. Moving any of the
knobs glides, so you can turn them while you play. Over MIDI: CC 81 AMOUNT, 82 TONE, 83 MIX, 84 the type:
0–63 OFF, 64–75 KHLON, 76–87 TSH8, 88–99 OHCD, 100–111 BHLUE, 112–127 FDRV (0 and 127 still mean off and the drive as before).

### FILTER

![FILTER](manual/img/filter.png?v=c2de0ecc)

A resonant filter right after the drive (before the tremolo and the other effects), moved by its own LFO. It
filters the driven sound: a low-pass after the drive smooths its edge, a resonant sweep stays clean of the drive's
clipping.

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TYPE** | **OFF**; **LP12** / **LP18** low-pass (12 or 18 dB per octave: the higher, the steeper); **HP12** / **HP18** high-pass; **BP** band-pass; **NOTCH**. The default is **LP18** fully open (16 kHz), so the filter is ready: turn FREQ (or PERFORM's K2) down and it closes |
| K2 | **FREQ** | The cutoff, 30 Hz to 16 kHz |
| K3 | **RESO** | Resonance: from smooth to a whistling peak at the cutoff |
| K4 | **LFO** | How far the LFO moves the cutoff (up to ±4 octaves); the LFO itself is set on the LFO page |

On PERFORM, K2 is the filter's cutoff by default. Fully open (LP12 or LP18 at 16 kHz, no resonance, no LFO) the
sound passes untouched, exactly as with the filter off; turning FREQ down glides into the filter, without a click. The factory presets use the default; your own presets keep what you saved, and so does the
sound the FM-1 was left on. Over MIDI: CC 70 TYPE, 74 cutoff, 71 resonance, 72 LFO amount.

### TREM/WAH

![TREM/WAH](manual/img/trem.png?v=63fe1c7a)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | **OFF**, **PAN** (the Suitcase stereo vibrato), **TREM** (volume tremolo), **WAH** (touch wah: harder playing opens it; it keeps the sound's level at every DEPTH and RATE, so a closed wah on a high chord or a soft high note is not quieter and a low one not louder; MARIMBA's top octave, whose notes are very short, still comes out a little quieter) |
| K2 | **DEPTH** | How deep |
| K3 | **RATE** | How fast; on WAH, the resonance (it glides as you turn it, without a click). When synced it shows a note value |
| K4 | **SHAPE** | Two halves. Up to the middle (0–64): from a smooth sine to a square, choppy pulse. Above it (65–127): an amp's tremolo, the square easing back into a sine: the level falls more slowly than it comes back (at the top the dip takes about two thirds of each cycle), it moves evenly in loudness so a deep setting breathes rather than chopping to silence (the deepest dip stays at −30 dB), and loud playing makes it a little shallower (up to a quarter less depth at full level). On PAN the upper half gives the same slower swing. On WAH the card shows **-**: SHAPE does nothing there and keeps its value for TREM and PAN |


On **VIBES** the vibraphone's motor is not here but on its last VOICE page, VOICE 3/3 (see [the motor: on and off](#the-motor-on-and-off)); TREM/WAH can run on top of it.
### PHASER

![PHASER](manual/img/phaser.png?v=8a35dfa3)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | Off / on |
| K2 | **DEPTH** | How far it sweeps. Below the middle the sweep also moves up as it narrows, to the top of its range at 0: a small DEPTH is a gentle movement in the treble, not a fixed notch in the middle of the sound |
| K3 | **SPEED** | How fast: 0.25–6 Hz (a note value when synced) |
| K4 | **FEEDBK** | Resonance: more pronounced, vocal sweeps (it never builds up on the bass) |

### CHORUS

![CHORUS](manual/img/chorus.png?v=514608a2)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | Off / on |
| K2 | **DEPTH** | Amount and width |
| K3 | **SPEED** | How fast: 0.25–6 Hz (a note value when synced) |
| K4 | **SYNC** | Free, or locked to the tempo as a note value |

Phaser and chorus are two separate effects and can run together (the phaser feeds the chorus). The phaser keeps the
level at every DEPTH (a chord's loudness matches the phaser switched off; its notches sweep through the sound, so a
single note still dips and swells as they pass it; the bass is not lifted) and keeps the sound close to the middle
(its two sides 30° apart). The chorus makes the sound wider in stereo, which on headphones sounds bigger; its level is
set so that it does not also sound louder, and it leaves the bass (below about 200 Hz) as it is, so low notes keep
their weight.

### DELAY

![DELAY](manual/img/delay.png?v=1bebf199)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **EFFECT** | **OFF**, **DIG** (clean digital), **ANA** (darker, with a little wow and flutter), **TAPE** (a tape echo: one playback head, warm saturating repeats, playback-head EQ, wow and flutter) |
| K2 | **MIX** | How loud the echoes are: at the middle (64) the first echo is as loud as the dry sound, at the top a little louder. Turning it up also lowers the dry sound a little (−1 dB at the middle, −6 dB at the top), so a full mix is not louder overall |
| K3 | **TIME** | Echo time, free in milliseconds; when DLY SYNC is set on TEMPO it shows and steps note values |
| K4 | **FEEDBK** | How many echoes, the same at any MIX. On ANA and TAPE the top of the knob (from about 115) lets the echoes build up on their own (below) |

**TAPE** works like a tape echo with one record and one playback head: TIME sets the tape's speed, so a longer
TIME is a slower tape with darker echoes, and turning TIME bends the echoes' pitch while the motor follows (one to two
seconds). Each repeat goes through the tape again: loud repeats compress and soften, the lows gain a small bump and
the highs fall off. TONE moves the high-frequency roll-off (70 = as the tape plays).

**FEEDBK** alone sets how many echoes, on every type and at any MIX (turning MIX changes how loud the echoes are, not
how many). On ANA and TAPE, up to about 113 the echoes always die away; from about 115 to 127 they build up on their
own into a held drone, kept in bounds by the echo's own saturation, as an analog or tape echo does when its
regeneration is turned up. DIG's echoes always die away. Turn FEEDBK down (the drone then dies away) or switch the
delay off (its tail always ends) to stop it.

Switching the delay off or on while echoes ring does not click: the echoes already on their way ring out in their own sound. Switching to or from TAPE while echoes ring fades the old type out, then the new one in. **SEL** switches the delay off and
back on to the type it last used (TAPE too). Over MIDI, CC 88 picks the type in the card's order: 0–31 OFF, 32–63 DIG,
64–95 ANA, 96–127 TAPE.

### REVERB

![REVERB](manual/img/reverb.png?v=9bb34082)

The reverb, the last effect. It has three types: **HALL** (the reverb Frodhes always had), **PLATE** and
**INFINITE**. Its second page, **REVERB 2**, has the pre-delay and three more controls. MIX can also be put on a
PERFORM knob (REVERB on ASSIGN).

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TYPE** | **OFF**, **HALL**, **PLATE** or **INFINITE** (below). OFF stops what goes into the reverb and lets the tail ring out. Switching it on with MIX at 0 gives MIX a starting amount. A new type takes over at once: the old tail fades out in a few milliseconds and the new type starts empty |
| K2 | **MIX** | How much reverb (0 = none, the tail rings out). With SIZE on AUTO (REVERB 2), more MIX also makes the space bigger on HALL and PLATE (on PLATE once the tail has died away; on INFINITE MIX is the level only) |
| K3 | **DECAY** | How long the tail rings. HALL: 5.0 (the default), 0 about half as long, 10.0 about twice. PLATE: from under a second to about 6 s. INFINITE: from about 4 s to a minute; from 120 (**SUSTAIN**) the tail stops fading and what you play piles up on it, and at the very top, 127 (**FREEZE**), the tail holds exactly as it is and what you play stays dry |
| K4 | **TONE** | The tail's treble: darker (more damping) or brighter (less); +0.0 (the default) in between. On PLATE it also darkens what goes in |
<!-- the PLATE / INFINITE DECAY ranges: measured on host, tests/py/test_rev_types.py (T60 PLATE 0.8..6 s, INFINITE 4..60 s, within 15 %) -->

The types:

| Type | The sound |
| --- | --- |
| **HALL** | A concert hall, as before (with a cleaner top octave, below): old presets and saved settings load as HALL with REVERB 2 at its defaults, so they sound the same |
| **PLATE** | A studio plate: dense from the first moment, bright, with no echoes you can pick out. Good for a ballad or a close, polished sound |
| **INFINITE** | A huge space that no room has: very long, slowly moving tails that pile up into a pad. At DECAY SUSTAIN (120–126) the tail no longer fades: every note adds to it, up to a ceiling. At FREEZE (127) what is ringing holds, without growing or fading, while you play on top of it dry. Turn DECAY down from either and the tail dies away again |

When the reverb is OFF, a REVERB knob on PERFORM shows **OFF**; turning it up there switches the reverb on with the
type you used last.

Every type runs at half the sample rate, as the hall always did; the filters into and out of it are now sharp
half-band filters, so bright notes and cymbal-like overtones no longer fold back into the tail as faint, out-of-tune
tones (a 15 kHz overtone used to come back at 7 kHz only 10 dB down; now 37 dB and more). The tail's top octave is
cleaner on every type; its level and length are unchanged.

Over MIDI: CC 95 TYPE (0–31 OFF, 32–63 HALL, 64–95 PLATE, 96–127 INFINITE), 91 MIX, 92 DECAY, 93 TONE. DECAY and
TONE glide when moved; MIX moves in small steps (every 32 samples, too small to hear).

### REVERB 2

![REVERB 2](manual/img/reverb2.png?v=605a1025)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **PRE-DELAY** | A gap before the reverb starts, 0–80 ms, so the notes stay clear in front of the reverb (it was K4 on REVERB before) |
| K2 | **SIZE** | How big the space is. **AUTO** (0, the default) lets MIX set the size, as it always did on HALL. On PLATE the size waits while a tail rings and follows MIX once the plate is silent (a preset sets its size at once), and on INFINITE AUTO is a fixed 5.0, so turning MIX never bends a ringing tail's pitch; 0.1–10.0 fixes it, small to large (on PLATE the plate's size, on INFINITE the space's) |
| K3 | **MOD** | How much the tail moves: a slow drift that keeps long tails from sounding metallic. 5.0 (the default) is HALL as before; 0 holds still, 10.0 moves twice as much. On INFINITE it is the slow wander of its lines |
| K4 | **LOW CUT** | Takes the bass out of what goes into the reverb, so long tails do not muddy the low notes: **OFF** (the default) or 20–800 Hz |

The defaults are the reverb's sound before it had types. Over MIDI: CC 94 PRE-DELAY, 73 SIZE (0 = AUTO), 68 MOD,
69 LOW CUT (0 = OFF).

### TEMPO

![TEMPO](manual/img/tempo.png?v=bb835d48)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **BPM** | Tempo, 40–240. **PLAY** taps it (from the third tap) |
| K2 | **TREM SYNC** | The tremolo locked to the tempo (FREE = its own RATE) |
| K3 | **PHA SYNC** | The phaser locked to the tempo |
| K4 | **DLY SYNC** | The delay locked to the tempo |

### SCALE

![SCALE](manual/img/scale.png?v=6c0bf354)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **MODE** | **OFF**; **SNAP** (every key plays: an out-of-scale note plays the nearest note of the scale); **DEGREE** (the white keys walk the scale, the black keys are silent) |
| K2 | **SCALE** | Major, minor, dorian, mixolydian, lydian, phrygian, harmonic and melodic minor, pentatonic, minor pentatonic, blues |
| K3 | **ROOT** | The scale's key |
| K4 | **TRANSP** | Transpose ±24 semitones |

MODE and TRANSP act on the FM-1's own keys. Notes from USB-MIDI and the TRS jack play as sent: not moved onto
the scale, not transposed. One exception: a **DIATONIC** / **DIA 7TH** chord ([CHORD](#chord)) is built on the
scale from any source, so a MIDI note out of the scale plays the chord of the nearest scale note.

### CHORD

![CHORD](manual/img/chord.png?v=bff15419)

Chord mode: **one key plays a whole chord**. Press **EDIT** to come here, and **EDIT** again to switch chord mode
on / off (it comes back with the last chord type, MAJ the first time; the EDIT light shows it).

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **TYPE** | **OFF**; **DIATONIC** / **DIA 7TH**: the chord (triad / seventh) that belongs to the key and scale set on the SCALE page, so in C major D plays Dm, G plays G, B plays Bdim; or a fixed shape on every key: **MAJ**, **MIN**, **7**, **MAJ7**, **MIN7**, **SUS2**, **SUS4**, **ADD9**, **DIM**, **AUG**, **6**, **MIN6** |
| K2 | **INVERSION** | **ROOT**, **1ST**, **2ND**, **3RD** (the lowest notes moved up an octave; on a three-note chord 3RD is the root position an octave up) |
| K3 | **VOICING** | **CLOSE**; **OPEN** (the second note up an octave, wider); **+BASS** (the root added an octave below) |
| K4 | **STRUM** | 0 to 60 ms between the notes, low to high, like a strum |

It works with the scales (with SNAP the key first moves onto the scale), with notes from USB-MIDI, with the
arpeggiator (which then arpeggiates the chord) and with the sequencer (which records the chord's notes).

### ARPEGGIO

![ARPEGGIO](manual/img/arp.png?v=15999ac3)

Hold a chord and the arpeggiator plays its notes one at a time, in time with the tempo (tap tempo and MIDI clock
too). Press **ARP** to come here, and **ARP** again to switch it on / off (the ARP light shows it).

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **MODE** | **OFF**, **UP**, **DOWN**, **UP-DN** (up and back down), **ORDER** (the order you played the notes), **RANDOM** |
| K2 | **RATE** | The note value: 1/4 (one note per beat), 1/8, 1/8T, 1/16, 1/16T, 1/32 |
| K3 | **OCTAVES** | 1 to 4: the pattern repeated octaves up |
| K4 | **GATE** | How long each note sounds: 10 % short and plucked, 100 % legato |

Up to 16 notes; every note of the arpeggio plays at the velocity of the first note you held.

It plays while you hold the keys. **SEL** switches **HOLD** (the latch) on / off: on this page, and on every page
while the arpeggiator is on (the DELAY shortcut comes back when you switch it off; the DELAY page's hint says
SEL: ARP HOLD meanwhile). The SEL light shows HOLD, and so does the bottom line here. While the arpeggiator is on,
**PLAY** is tap tempo on every page, SEQUENCER included (to start the sequencer then, send MIDI CC 60, or a MIDI Start with CLOCK
on EXT, or switch the arpeggiator off first); **EDIT** stays the CHORD page and chord mode. With HOLD on, play a chord and let go, and the arpeggio keeps going with those notes; the next
chord you play (after letting go of every key) replaces them, and keys you add while others are still held join the
pattern. Switching HOLD off stops the notes you are no longer holding. The sustain (REC) stays the piano's sustain
pedal. It works with the scales (the notes are already on the scale) and with notes from USB-MIDI. The
settings belong to the FM-1, not to a preset; ARP RATE and ARP GATE can also be put on a PERFORM knob.

### LFO

![LFO](manual/img/lfo.png?v=887d2dfb)

The filter's LFO (it moves FREQ by the FILTER page's LFO amount).

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **SHAPE** | **SINE**, **TRI**, **SAW** (rising), **RAMP** (falling), **SQR** (square), **S&H** (a random step each cycle) |
| K2 | **RATE** | Free, 0.5 to 12 Hz; when SYNC is set it shows and steps note values |
| K3 | **SYNC** | **FREE**, or locked to the tempo: 1/32 to 4 bars |
| K4 | **RETRIG** | **FREE** (it runs on its own) or **KEY** (it starts from the beginning with every note) |

### SEQUENCER

![SEQUENCER](manual/img/seq.png?v=7c39df98)

A **pattern recorder**: play notes or chords (chord mode too) and they loop, quantized, while you keep adding and
editing. The screen is made to be read at a glance: the top line shows where the loop is (**BAR 2/4 . 3** = bar 2
of 4, beat 3); the chord or note playing **now** is in big letters (D MIN7, C4...), with what comes **next** and
where under it; and the bar playing is shown as **16 big cells** (16ths, in two rows of 8): a filled cell is where
notes start, a cell with a band is where they are still held, the white one is where the loop is.

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **BARS** | The loop's length, 1 to 16 bars (notes past the end are kept and come back if you make it longer) |
| K2 | **QUANT** | **OFF**, **1/4**, **1/8**, **1/16**, **1/8T**, **1/16T**: the grid the notes snap to as they play. It never changes what you recorded, so you can switch it at any time and hear the pattern tighten or loosen |
| K3 | **SWING** | 50 % (straight) to 75 %: pushes the off-beat 1/8s or 1/16s |
| K4 | **EDIT** | Turn **left: UNDO** the last pass you recorded (again: the one before), or, with the cursor on a step, **DELETE** that step's notes. Turn **right: ERASE** on / off: while it plays, hold keys and their notes are removed as the loop passes them |

- **Look through it, step by step:** on this page **ALGORITHM** moves a cursor (a white frame) one 16th at a time,
  across the bars, and **PRESETS** jumps a whole bar. The top line shows where (**BAR 2/4 . 3.2** = bar 2, beat 3,
  second 16th) and the big letters show what starts on that step ("-": nothing). Turn **EDIT left** there to
  **delete** that step's notes. While the loop plays, the view goes back to following it 4 s after you stop turning.
  (On every other page ALGORITHM is the TYPE and PRESETS the presets, as always.)
- **PLAY** starts and stops the loop. Stopping takes everything back to the start: the loop and the cursor go to
  **bar 1, step 1** (nothing is erased).
- **SEQ** switches recording on / off (the SEQ light blinks).
  - **Playing (live):** every pass adds what you play on top (overdub), quantized; the loop never stops. Arm REC, then
    press PLAY to start.
  - **Stopped (step by step):** what you play goes on the **cursor's step**, as long as the QUANT grid (1/16: one
    step, 1/8: two, 1/4: four); let go and the cursor moves on by that much. Keep the keys held and turn
    **ALGORITHM** to make that note or chord longer (LENGTH n STEPS); turn ALGORITHM without playing to leave a rest.
    UNDO takes back the last step you entered.
- **Hold SEQ** for a second: **clear** the whole pattern.
- **Limits:** 4/4, up to 448 notes per pattern (96 steps per beat inside), 32 sounding at once. A new FM-1 starts
  with 4 bars, QUANT OFF and SWING 50 %; SWING acts on the 1/8 and 1/16 grids.

Press **SEQ** to come here, from any page. The pattern goes through the
arpeggiator when it runs, plays the effects like your hands do, and is kept on the FM-1 when you switch off (it is
stored when the loop is stopped).

### ASSIGN

![ASSIGN](manual/img/assign.png?v=c173fac5)

Choose what each PERFORM knob plays: K1 here sets what K1 on PERFORM does, and so on. ASSIGN sits near the end of
the pages, just before SETUP (the hint on PERFORM says so). Each knob can play any of:
TREM/WAH, PHASER, CHORUS, DELAY, REVERB, DRIVE, COMP THR, COMP MKUP, TREM RATE, TREM SHAPE, PHA SPEED, PHA FEEDBK,
CHO SPEED, DLY TIME, DLY FEEDBK, DLY TONE, TONE, BARK, BELL, HAMMER, DECAY, RELEASE, NOISE, KEY VEL, LEVEL, ARP RATE,
ARP GATE, FILTER, FILT RESO, FILT LFO and LFO RATE. The
choice is kept when you switch off (it belongs to the FM-1, not to a preset). Out of the box the four knobs are
DRIVE, FILTER, DELAY and REVERB.

### SETUP

![SETUP](manual/img/setup.png?v=d3e9de08)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **DLY TONE** | The delay's echo colour |
| K2 | **LEVEL** | This preset's volume, −12 to +11.8 dB, saved with the preset |
| K3 | **USB VOL** | **KNOB**: the USB audio follows the MASTER knob, as the headphones do. **FIXED**: the USB audio always comes at full level, whatever the knob (and headphones plugged in) do; the headphone jack still follows MASTER |
| K4 | **TOUCH** | How the FM-1's keys turn a press into a velocity. **MEDIUM** (the default): a medium press plays KEY VEL, a harder one louder, a softer one quieter. **LIGHT**: the same press plays louder, for a light hand. **HEAVY**: you have to press harder for the same velocity. **FIXED**: every press plays KEY VEL, however hard (no key velocity) |

**Which keys sense velocity.** The keys are rubber domes; the FM-1 reads how firmly each one lands. The 20 keys **F3
to C5** do this. The top 7 keys (**C#5 to G5**) have no way to measure it: they play the average of your recent
presses, so they follow how you are playing (after 5 s without a press, KEY VEL again). A note starts a few
milliseconds after the key closes, while the reading comes in. The notes the keys send over USB-MIDI carry the same
velocity. TOUCH and KEY VEL belong to the FM-1, not to a preset (a preset does not change them). The browser
emulator has no velocity: its keys play at KEY VEL.

The bottom line shows the name and the version installed, the battery's reading (BAT, the raw value the battery
segments are worked out from), and the audio engine's time per block (it must stay under 5800 µs; every voice struck at once with every effect on reaches about 3450 on the Rhodes (3600 while bending, about 3600 with SPREAD at full width and 3750 bending with it), up to about 3950 on VIBES and 3800 on MARIMBA (10 notes each), measured on the FM-1; section 10 gives the whole audio interrupt's time).<!-- FM-1 (2026-10-25, the release package sha256 2ca1d517c430, final-rel/SUMMARY.md), every effect on, TSH8, TAPE, USB 48 kHz, hil_cpu --worst --usb 48000 --voices 10: app worst (the highest of INF / HALL / PLATE) Rhodes 3468 us, --bend-sweep 3588, SPREAD 127 3576, with bend 3752, VIBES 3944, MARIMBA 3812; 0 shed. --><!-- FM-1 (2026-10-25, build 353d023), every effect on, TSH8, TAPE, USB 48 kHz, hil_cpu --worst --usb 48000 --voices 10: app worst INFINITE Rhodes 3692/3640/3624 us, --bend-sweep 3768, SPREAD 127 3784, with bend 3912, VIBES 3796, MARIMBA 3740; HALL 3612/3724/3664, PLATE 3644/3860/3708 (Rhodes/VIBES/MARIMBA), 0 shed, USB clean (build/final-rev/cpu_*.log). --><!-- FM-1 (2026-10-24, build 57e0025), every effect on, the heaviest drive TSH8, delay TAPE, USB 48 kHz, hil_cpu --worst --usb 48000 --voices 10 (x3 on the Rhodes): app time worst Rhodes 3540/3512/3504 us, --bend-sweep 3632, VIBES 3700, MARIMBA 3660, 0 shed, USB clean (build/final2/cpu_*.log). Update with section 10's together. SPREAD 127 on 57e0025 (HALL, every effect, TSH8, TAPE, 10 voices): app worst 3668/3652/3668 us, with bend 3784 (build/final2b/cpu_spread_*.log). -->

### MIDI

![MIDI](manual/img/midi.png?v=8fadd920)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **CHANNEL** | **ALL** (the default: every channel is played) or **1–16**: only that channel is heard (notes, CCs and program changes; clock, Start and Stop always are), from USB-MIDI and from the TRS jack alike, and the FM-1 sends its notes on it (on ALL it sends on 1) |
| K2 | **TRS IN** | **ON** / **OFF**: MIDI from the 3.5 mm TRS MIDI input (for a DAWless setup: a keyboard, a sequencer, a drum machine's clock) |
| K3 | **CLOCK** | **INT**: its own tempo. **EXT** (the default): follows MIDI clock from USB or the TRS jack when it comes (the tempo and the beats: section 8), and a MIDI **Start** / **Continue** starts the sequencer, **Stop** stops it |

Press **GLO** three times to come here (TEMPO, SETUP, MIDI). Everything MIDI does, it does the same from USB and from
the TRS jack: notes, every CC in the table below, program changes, clock, start and stop.

### DISPLAY

![DISPLAY](manual/img/display.png?v=0804e0cd)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **COLOR** | The screen's colour, 27 steps: **AMBER** (the default), then 25 pastel hues around the colour wheel: PEACH, CORAL, ROSE, PINK, ORCHID, MAUVE, PURPLE, VIOLET, LILAC, IRIS, BLUE, DENIM, SKY, OCEAN, CYAN, AQUA, MINT, JADE, GREEN, LEAF, LIME, PEAR, LEMON, GOLD, MANGO, and last a full **RED**. Every page is drawn in it: the title, the fader fills and edges, the lit selector blocks |
| K2 | **BRIGHTNESS** | For a dark room: **10** (full) to **1** dims the whole panel together, the screen (its colours and its text alike) and every light, keys and buttons alike (at 10 the keys are at full, as they always were; below, a little under the buttons, every step a change in both). The backlight itself is fixed, so black stays as it is and the screen's contrast falls at the low steps |
| K3 | **LEDS** | Which keys light: **OFF** none; **PLAYED** a key while its note sounds; **ROOTS** (the default) the played keys, and while a scale is on its root keys glowing faintly as landmarks, never brighter than a played key; **ALL** every key lit, a light for playing in the dark; a key goes dark while its note sounds; **SCALE** as ALL, but only the keys of the scale lit (SCALE page): the keys, white and black, whose own note is in the scale, in SNAP and DEGREE alike; a played scale key goes dark, and the keys outside the scale stay dark even when pressed; with no scale on it looks as ALL |

Press **GLO** four times to come here (TEMPO, SETUP, MIDI, DISPLAY). All three are kept with the settings, not with
a preset, so they stay as you set them whatever sound you load.

## 5. VIBES and MARIMBA: the bar instruments

Two of the TYPEs are struck bars instead of a Rhodes: **VIBES**, the vibraphone, and **MARIMBA**. They share one
engine and the same VOICE cards, each with its own bars, tubes and mallets; VIBES has a damper and a motor, MARIMBA
neither (its VOICE 2/2's second card is **MUTE**, the player's hand). Over MIDI, CC 79 picks between them.

### VIBES: the vibraphone

Turn **ALGORITHM** (or the TYPE card) to **VIBES** and the FM-1 plays a vibraphone: aluminium bars struck with
mallets, each with its tuned tube underneath, and the motor that gives the vibraphone its pulsing shimmer. Like the
Rhodes it is computed, not sampled: the bar's own modes (the fundamental and its tuned overtones, about 4 and 10
times higher), the tube that takes a moment to build up under the bar, and the motor's discs. Switching between a
Rhodes type and VIBES fades out what is still ringing, held notes too; a note still held plays again on the new type
the next time it is struck (a MIDI note-on, or a key on the same note).

![The main page on VIBES](manual/img/piano-vibes.png?v=33e4565d)

The main page's middle cards belong to the engine: on VIBES they are **MALLET** (the same as VOICE 1/3's MALLET)
and **MOTOR** (on / off, the same as VOICE 3/3's first card); on a Rhodes type they are **TONE** and **BELL** (DRIVE
and REVERB have their own pages).

VIBES plays about **2.5 dB louder** than the Rhodes types: a struck bar is peakier than a Rhodes note, so at the same
loudness it sounded weaker and came out closer to the headphone jack's hiss. The limiter still keeps big chords from
clipping.

VIBES has its own VOICE pages, three of them: **VOICE 1/3** (the strike), **VOICE 2/3** (how the bars ring and stop)
and **VOICE 3/3** (the motor). VOICE 1/3 acts on the next strike (a ringing bar keeps the strike that started it, as
a real one does).

![VOICE 1/3 on VIBES](manual/img/voice1-vibes.png?v=9af0d189)

| Knob | Card | What it does on VIBES |
| --- | --- | --- |
| K1 | **STRIKE** | Where the mallet hits the bar: **CENTRE** (left end), a fuller, rounder fundamental with the 10th partial and no 4th; **L/3** (the middle of the knob, the usual spot), the 4th partial back; toward the **EDGE** (right), off the centre line, the bar's twisting modes ring in: metallic and slightly out of tune |
| K2 | **MALLET** | How hard the mallet is: **SOFT** (0) a dark, round, almost pure fundamental with a soft onset, even when you play hard; **YARN** (42) round and warm, the overtones held back; the middle (64, the default) the usual vibes sound; **CORD** (85) clearer, the 10th partial rings; **RUBBER** (127) hard and bright, bell-like, with a click. The card shows the mallet's name, or a number between two of them. The low bars show the soft mallets apart best, the middle and high bars the hard ones |
| K3 | **TUBE** | How much the tube rings with the bar: 0 is a bare bar (about 6-7 dB quieter than the middle, thin, rings longer, no bloom); up is a fuller, warmer, louder note that blooms just after the strike and dies sooner, as on a real vibraphone (the right end about 3-4 dB over the middle) |
| K4 | **WEIGHT** | The mallet's weight: light is brighter, quicker and quieter; heavy is darker, with more body (about 6 dB louder at the top than at the bottom). How hard you play (KEY VEL, MIDI velocity) stays the strike's speed: a harder strike is louder and brighter (the overtones come up), a soft one round |

![VOICE 2/3 on VIBES](manual/img/voice2-vibes.png?v=28da39c5)

| Knob | Card | What it does on VIBES |
| --- | --- | --- |
| K1 | **RING** | How long a struck bar rings: from a quarter to about four times the normal time |
| K2 | **DAMPER** | How firmly the damper stops the notes when you let go: soft lets them fade, firm stops them short |
| K3 | **NOISE** | The mallet's contact and the damper's touch (at the key-up always under the note) |
| K4 | **TUNE** | Fine tuning, ±50 cents (many vibraphones are tuned to A = 442 Hz: +8) |

**The pedal** works like the Rhodes' sustain: **REC** (hold, or tap to latch) or a sustain pedal over MIDI (CC 64,
half-pedal too). Without it the damper stops each note when you let go.

### The motor: on and off

The motor (the vibrato's pulse) is part of the vibraphone, not an effect: it has VIBES' last VOICE page,
**VOICE 3/3**, right after VOICE 2/3 in the SELECT order. The page is there only while the TYPE is VIBES (the Rhodes
types have two VOICE pages: SELECT goes from VOICE 2/2 straight to COMPRESSOR).

- **On by itself:** turning the TYPE to VIBES (ALGORITHM, the TYPE card or CC 79) starts the motor at its default:
  DEPTH 8.7, 3.5 Hz, a clear, regular pulse.
- **Off and on:** on VOICE 3/3, **K1** turns it **OFF** or **ON**. Switched off there, it stays off: changing the
  TYPE to a Rhodes and back to VIBES does not start it again (and the choice is saved), until K1 turns it on.
- **Presets keep theirs:** loading a preset is not a TYPE change: **F13 VIBES** has the motor on, **F14 VIBES
  ECHO** has it off, and a user preset saved with the motor off stays off.
- **The Rhodes types** have no motor: going back to one leaves no tremolo running (TREM/WAH stays as it was).
- **The level** stays the same with the motor on or off, from the strike to the end of the note: the discs close
  the tubes' sound part of each turn and the motor makes up for what they take, while the bar rings exactly as long
  as with the discs open. Switching the motor on or off under a held chord starts or stops the pulse without a jump
  in level.
- **TREM/WAH** (the LFO button) is the same effect on VIBES as on the Rhodes and can run on top of the motor.
- **Over MIDI:** CC 47 ON (≥ 64), 48 DEPTH, 49 SPEED, 50 SHAPE, 51 SYNC.

![VOICE 3/3 on VIBES: the motor](manual/img/voice3-vibes.png?v=651cb1e9)

| Knob | Card | What it does |
| --- | --- | --- |
| K1 | **MOTOR** | **ON** / **OFF**; it fades in and out over about 50 ms, so switching never clicks |
| K2 | **DEPTH** | How far the discs close the tubes: a gentle pulse to a deep throb (the default 8.7 gives about 8 dB from F3 to F6, full depth about 10–11 dB) |
| K3 | **SPEED** | The motor speed, 0.5–10 Hz (about 3.5 Hz is classic). With a SYNC set over MIDI (CC 51) it shows the note value and follows the tempo |
| K4 | **SHAPE** | How sharply the discs open: soft is a smooth pulse, sharp a shorter, more pointed one |

As on a real vibraphone the motor works on the tubes, so it pulses the ringing note and leaves the high overtones
and the strike alone. (Real discs held closed also let a bar ring longer; Frodhes keeps the ring the same with the
motor on, so turning it on or off never changes the level or the length of a note.)

### Range, presets, polyphony

**The range.** A concert vibraphone has three octaves, **F3 to F6**; that is where VIBES is measured against the real
instrument. Every other MIDI note plays too, extended from the same physics. The FM-1's keys (F3–G5) sit in the
vibraphone's range.

**Presets:** **F13 VIBES**, dry (a softer mallet, the motor slower, at 2.6 Hz, and a little shallower, a gentle
compressor, a trace of hall) and **F14 VIBES ECHO** (motor off, a cord mallet, a longer ring, a light chorus, a tape
echo on the tempo and a plate).
**Polyphony:** 10 notes, as the Rhodes.

### MARIMBA

Turn **ALGORITHM** (or the TYPE card) to **MARIMBA** and the FM-1 plays a concert marimba: rosewood bars struck with
yarn mallets, each over its tuned tube. It is the same kind of instrument as the vibraphone, computed the same way
(the bar's own modes, here tuned 1 : 4 : 10, the tube, the mallet's contact), but it sounds very different: wood
instead of aluminium, a warm, short note. A note rings for about **1.7 s at C2, 0.7 s at C4, 0.3 s at C6 and a
tenth of a second at C7** (the default cards, until it is 60 dB down). Below C4 the long tubes make the notes ring
**longer** and **bloom**: the low note swells for a moment after the strike (about 0.15 s at C2, 0.05 s at C4); from
C5 up the note starts at its loudest. A marimba has **no damper and no motor**: a note rings out by itself, and the
player stops it with a hand. Switching between VIBES and MARIMBA (or a Rhodes type) fades out what is still ringing,
held notes too; a note still held plays again on the new type the next time it is struck.

![The main page on MARIMBA](manual/img/piano-marimba.png?v=16e96cd0)

The main page's middle cards on MARIMBA are **MALLET** (the same as VOICE 1/2's MALLET) and **TUBE** (VOICE 1/2's
TUBE). MARIMBA has two VOICE pages, **VOICE 1/2** (the strike) and **VOICE 2/2** (how the bars ring and stop), and no
motor page: SELECT goes from VOICE 2/2 straight to COMPRESSOR. VOICE 1/2 acts on the next strike.

![VOICE 1/2 on MARIMBA](manual/img/voice1-marimba.png?v=424ffdff)

| Knob | Card | What it does on MARIMBA |
| --- | --- | --- |
| K1 | **STRIKE** | Where the mallet hits the bar: **CENTRE** (left end), a fuller, rounder fundamental; **L/3** (the middle of the knob, the usual spot); toward the **EDGE** (right), the bar's twisting modes ring in. It is heard most on the low and middle bars; on the top bars the mallet barely excites anything but the fundamental, wherever it hits |
| K2 | **MALLET** | The mallet: **SOFT** (0) soft yarn, a dark, round note even when you play hard; **MED** (42) medium yarn; the middle (64, the default, shown as 5.0) between medium and hard yarn; **HARD** (85) hard yarn, clearer, the overtones ring; **RUBBER** (127) a bright, hard, almost xylophone-like click. The card shows the mallet's name, or a number between two of them |
| K3 | **TUBE** | How much the tube rings with the bar: 0 is a bare bar (quieter and thinner, no bloom); up is a fuller, louder note. Below C4 the tube also makes the note ring longer and bloom just after the strike; higher up, as on a vibraphone, it makes the note louder and shorter (at the top about a third shorter) |
| K4 | **WEIGHT** | The mallet's weight: light is quieter, heavy louder, with more body. How hard you play (KEY VEL, MIDI velocity) stays the strike's speed: a harder strike is louder and brings the overtones up |

![VOICE 2/2 on MARIMBA](manual/img/voice2-marimba.png?v=18121bb8)

| Knob | Card | What it does on MARIMBA |
| --- | --- | --- |
| K1 | **RING** | How long a struck bar rings: from a quarter to about four times the normal time (the low notes' tube too). Above the middle (64) the notes are also turned down as RING rises, so that the hardest strike still stays just under full scale: the low ones the most (at 127 about 6 to 9 dB from C2 to G3, 3 dB at C4, under 1 dB from C6 up). So from about 72 up RING makes the low notes ring longer rather than louder; at 64 and under nothing changes |
| K2 | **MUTE** | The player's hand when you let go of a key: **0** (MUTE 0.0) lets the note ring out as on a real marimba; up, the hand stops it sooner; at 10.0 it is 60 dB down well within a tenth of a second |
| K3 | **NOISE** | The mallet's contact: a soft tick at the start of the note (no damper, so nothing at the key-up) |
| K4 | **TUNE** | Fine tuning, ±50 cents (many marimbas are tuned to A = 442 Hz: +8) |

**MUTE, not DAMPER.** On VIBES the second card of VOICE 2/3 is the DAMPER, the felt bar that stops the notes when you
let go. A marimba has no damper, so on MARIMBA the same card is **MUTE**: how firmly your hand stops a note at the
key-up. MUTE 0 means no hand: every note rings out. It is the same setting underneath (MIDI CC 103, saved with the
preset), shown and heard for the instrument you play. Turning the TYPE to MARIMBA keeps the card where it was (the
other factory presets have it at 5.0: on MARIMBA a half mute); the MARIMBA presets start at 0.

**The pedal** keeps its meaning: held (**REC**, or CC 64 over MIDI, half-pedal too), the hand never lands, whatever
MUTE says; let go, MUTE decides. **All Notes Off** (CC 123 / 120) stops everything, MUTE 0 too.

**No motor.** The motor belongs to VIBES. On MARIMBA its MIDI CCs (47-51) are accepted and kept for VIBES, and change
nothing in the marimba's sound.

**MARIMBA's level.** MARIMBA is set **2.5 dB louder** than the Rhodes types, as VIBES. But every marimba strike is
held under **−0.5 dBFS** (the top bars are rolled off and RING has its ceiling, so the hardest strike never reaches
the limiter), and that keeps a chord lower: an A3-E4-A4 chord at velocity 100 reads about **1.5 LU under** the
Rhodes F01 STAGE 77, on the USB audio and on the headphone jack alike.<!-- FM-1 hil_scan --marimba, scans 3 and 4 (build/marimba/p5/scan3/marimba-report.md, scan4-report.md): MARIMBA re ST77, A3-E4-A4 v100, LUFS 0..1 s, -1.50 LU USB, -1.48 jack. Level decision "A" (user, 2026-10-20): calib.st77_offset_db stays 2.5, no louder build -->

**The range.** A concert marimba has five octaves, **C2 to C7**; that is where MARIMBA is measured. Every other MIDI
note plays too (A0-C8), extended from the same physics. The FM-1's keys (F3–G5) sit in the marimba's middle.

**Presets:** **F15 MARIMBA**, dry (the default mallet and tube, MUTE 0: rings out, a gentle compressor, a trace of
hall) and **F16 WOOD ECHO** (a hard yarn mallet, heavier, MUTE 0, a light chorus, a digital echo on the tempo and a
hall). **Over MIDI:** CC 79 from 96 to 127 selects
MARIMBA (0–95 VIBES); Program Change 14 / 15 loads F15 / F16. **Polyphony:** 10 notes, as the Rhodes.

## 6. Presets

![Browsing presets](manual/img/browse.png?v=172e1e43)

Turn **PRESETS** to browse: **F01–F19** are the factory sounds (F13 and F14 are VIBES, F15 and F16 MARIMBA), **U01–U16** your own, after F19. A preset loads when you stop
turning (after 0.4 s). If you did not mean to, **hold SAVE** (0.7 s) to go back to the sound you had before the
last load (the top line names it again); hold it again to return (redo). The first time Frodhes
starts (or if its settings are ever lost), it plays **F01 STAGE 77**.

| | | | |
| --- | --- | --- | --- |
| F01 STAGE 77 | F02 SUITCASE | F03 EARLY FELT | F04 BELL DYNO |
| F05 SUIT TREM | F06 DRIVEN 77 | F07 BALLAD | F08 DYNO 80S |
| F09 70S PHASER | F10 FUNK WAH | F11 RESO SWEEP | F12 AUTO WAH |
| F13 VIBES | F14 VIBES ECHO | F15 MARIMBA | F16 WOOD ECHO |
| F17 TAPE ECHO | F18 DREAM | F19 RADIO | |

Each instrument comes twice: **dry**, the model as it plays (F01–F04, one for each Rhodes TYPE, F13 VIBES and F15
MARIMBA: only a gentle compressor and a trace of room), and **with effects** (F05–F12, F14, F16, F17–F19). Every preset has the
compressor on, set for glue rather than squash: a threshold of -16 dB at 2:1 on the Rhodes, its 5 ms attack letting
the hammer and the bark through, 1.5:1 with a slower attack on the mallets. Nearly every one has a little reverb, HALL
or PLATE (the plate on the bright and vintage sounds), with LOW CUT at 150–200 Hz so the bass stays out of the tail.

| Preset | What it is |
| --- | --- |
| F01 STAGE 77 | The stage piano, dry: the compressor and a trace of hall (the sound Frodhes starts with) |
| F02 SUITCASE | The suitcase piano, dry: its cabinet, no vibrato |
| F03 EARLY FELT | The early felt-hammer piano, dry |
| F04 BELL DYNO | The bell voicing, dry, with a short plate |
| F05 SUIT TREM | The suitcase piano with its stereo vibrato (PAN, 4.5 Hz), a little warmth and a vintage plate |
| F06 DRIVEN 77 | A gentle compressor that lets the hammer through, then the KHLON drive (a clean-blend boost): clean when you play softly, it barks when you dig in; a tape slap on the dotted 1/8 |
| F07 BALLAD | Rounder, a slow chorus, a soft quarter-note digital echo and a long plate behind a short pre-delay |
| F08 DYNO 80S | The bell voicing with chorus, a dotted-1/8 digital echo on the tempo and a bright plate |
| F09 70S PHASER | A slow, deep phaser with feedback on a piano with a light BHLUE amp breakup, a dark quarter-note analog echo |
| F10 FUNK WAH | A fast compressor, the TSH8 drive (its mid hump, tight lows) and the touch wah: harder playing opens it; a small room |
| F11 RESO SWEEP | A piano driven through OHCD into the resonant low-pass, its LFO sweeping it over two bars on the tempo, a clean quarter-note echo |
| F12 AUTO WAH | The classic auto-wah: the TSH8 drive into the filter's resonant band-pass, swept deep by the LFO (a sine at 1/4 on the tempo, about 330 Hz to 2.9 kHz), restarted by every note |
| F13 VIBES | Dry: VIBES with a softer mallet and a slower motor, a gentle compressor that lets the mallet through, a trace of hall |
| F14 VIBES ECHO | VIBES without the motor, a cord mallet and a longer ring, a light chorus, a dotted-1/8 tape echo on the tempo and a bright plate |
| F15 MARIMBA | Dry: the marimba as it is, ringing out, a gentle compressor, a trace of hall |
| F16 WOOD ECHO | MARIMBA with a hard, heavy mallet, a light chorus, a dotted-1/8 digital echo on the tempo and a hall |
| F17 TAPE ECHO | A warm piano into the tape echo, dotted 1/8 on the tempo, a plate |
| F18 DREAM | Felt hammers, a slow stereo pan, chorus, a quarter-note analog echo and a long, dark, moving hall |
| F19 RADIO | A hard OHCD drive through a band-pass, like a small speaker, a short tape slap and a small room |

The echoes and sweeps marked "on the tempo" follow the TEMPO page's BPM (120 when you start; PLAY taps it), so they
fit what you play. The Rhodes presets F01–F12 and F17–F19 are level-matched: each one's LEVEL (SETUP) brings it within 1 LU of
F01's loudness on a chord, so browsing does not jump in volume; the driven and filtered ones are set half way between
a chord and a whole phrase (soft and hard notes), as they change more with how hard you play, and both stay within
1.5 LU of F01, but for F12, whose deep band-pass sweep is set about 1.4 LU above F01 on a chord (it reads under F01 on
a phrase). Six of them use a drive type: F06 KHLON, F09 BHLUE, F10 and F12 TSH8, F11 and F19 OHCD. VIBES, F13–F14, sits
about 2–2.5 dB above, as the type does. MARIMBA is set 2.5 dB above as well, but every marimba strike is held just
under full scale (see MARIMBA's level): F16, with its echo, chorus and reverb, comes out about 2 LU above F01, and
F15, the plain marimba, about level with F01 on a chord in the middle of the keyboard (a wider chord, A3-E4-A4, reads
1.5 LU under it on the FM-1).

**The starter user presets.** Until you save your first preset, U01–U09 hold nine more Rhodes sounds to play or to
overwrite (an FM-1 that already has saved user presets keeps exactly those); U10–U16 start empty. They are
level-matched like F01–F12 (within 1 LU of F01 on a chord):

| Preset | What it is |
| --- | --- |
| U01 DARK KEYS | A rounder tine, less bell and a softer hammer, a gentle compressor, a slow chorus, a dark analog echo on the quarter note and a long, dark hall |
| U02 GLASS BELL | The bell voicing, glassier and with less bark, chorus, a dotted-1/8 digital echo on the tempo and a bright plate |
| U03 FUNK COMP | The bright, barky comping piano: a firm compressor for even chords, a touch of the TSH8 drive, a small room |
| U04 DREAM PAD | Felt hammers, a long decay and release, a slow pan, chorus, a dotted-1/4 tape echo and the INFINITE reverb's long bloom (its tail still dies away) |
| U05 LO-FI | Worn tape and a small amp: a mellow OHCD drive, a closed low-pass, a dark analog slap on the 1/16 and a slow AM wobble |
| U06 HARD TREM | The amp tremolo: a near-square 6 Hz chop over a soft BHLUE breakup, a plate |
| U07 SLOW PAN | The suitcase with a wide stereo pan at about 2 Hz, a whisper of chorus, a soft analog echo and a hall |
| U08 REEDY | A reedy electric piano: the tine close to the pickup, a hard hammer, a short decay, the TSH8 drive, an AM tremolo, a tape slap and a short plate |
| U09 VELVET | The softest piano: felt hammers, the dampers softly audible, a gentle compressor, a mid-sized hall |

The first save writes them to the FM-1 with your preset; from then on they are user presets like any other.

**Saving:** press **SAVE**. The bottom line asks where; turn **PRESETS** or **K1** to pick U01–U16; press **SAVE**
again to store it, or **HOME** (or **OCT−**) to cancel. The name is made from the type and the effects that are on
("SUIT TRM PHA").

![Save mode](manual/img/save.png?v=6cd0471d)

The save waits for silence: the bottom line says **SAVING...** until the sound has died away, then **SAVED**. INFINITE's SUSTAIN and FREEZE hold their tail without end, so while one of them is on the save waits and the bottom line says **SAVE WAITS: SUSTAIN ON** or **SAVE WAITS: FREEZE ON**; turn DECAY down (or change the type, or switch the reverb OFF), let the tail die away, and the save goes through. The settings are saved the same way, once it is quiet. A user
preset can be overwritten but not deleted, and browsing skips the empty ones. There is no factory reset: F01–F19 are
always there to start again from.

**What is kept, and when:**

| A preset (U01–U16) | The FM-1 itself (settings) | The pattern |
| --- | --- | --- |
| The sound: TYPE, every VOICE card, every effect and its sync, the filter and LFO, LEVEL | The setup: the preset you last loaded or saved (its number), KEY VEL, TOUCH, TUNE, TRANSP, the octave, the tempo you set by hand, CLOCK, scale, chord mode, arpeggiator, sequencer settings, PERFORM assignments, the MIDI page, USB VOL, the DISPLAY page | The sequencer's notes |
| When you press SAVE (once silent) | About 2 s after your last setup change, once the sound has died away | 1.5 s after a change, while stopped and silent |

**The sound is never saved by itself.** Switched off and on, the FM-1 plays the preset you last loaded or saved,
exactly as it is stored: what you turned since (knobs, effects, MIDI CCs) is gone, on purpose, so after a
performance you always start from the original sound. To keep a sound, SAVE it to a user preset. Turning knobs
never writes to the flash. The tempo comes back as you last set it by hand (TAP, or BPM on the TEMPO page), never as
an external MIDI clock left it (a clock causes no save at all).

So after a setup change, let the sound die away for a couple of seconds before you switch off.

This holds while a computer records the FM-1's USB audio too: the FM-1 writes its flash in short steps, so the
recording goes on undisturbed while the settings or the pattern are saved. The settings save also leaves the keys, the panel and MIDI
(USB and the TRS jack) alone: a note you play while it runs, a Program Change's preset saved a couple of seconds
later for instance, sounds at once.

## 7. Effects on and off

Each effect has its own on / off: its page button pressed twice (LFO: tremolo / wah, ENV: phaser, SEL: delay), its
K1 card on its page (chorus: EFFECT; compressor: THRESH; reverb: TYPE), or turning it up on PERFORM. Switching an effect on whose depth is 0 gives it a useful starting depth, so you always hear it.

**FX** switches **all** effects off at once and back on (the compressor, filter, drive and reverb included, with a
short crossfade), without changing any setting: compare the dry piano with
your sound. The header shows **BYP** while they are off. It is not saved, so the FM-1 always starts with the effects
on.

![FX bypass](manual/img/bypass.png?v=6efc165a)

The order of the chain: **COMP → DRIVE → FILTER → TREM/WAH → PHASER → CHORUS → DELAY → REVERB → limiter.** The limiter at the
end keeps even big chords of ten notes from ever clipping.

## 8. Tempo and sync

Tap **PLAY** three times or more in time to set the tempo, or set **BPM** on the TEMPO page. With **CLOCK** on EXT
(the default; MIDI page), Frodhes follows MIDI clock from USB or the TRS jack, and an external Start / Stop starts and
stops the sequencer (with a clock on both, the first one to run is followed and the other is ignored until the first
has been silent for half a second). The tremolo, phaser and
delay can each lock to the tempo on the TEMPO page (TREM SYNC, PHA SYNC, DLY SYNC), and the filter's LFO on the LFO
page. When one is synced, its RATE / SPEED / TIME card shows a note
value and turning it steps through them: 1/32, 16T, 1/16, 8T, 16D, 1/8, 4T, 8D, 1/4, 4D, 1/2, 1/1, 2 BAR (and 4 BAR
for the chorus, phaser and the LFO). A synced echo longer than the delay's 1.48 s plays at half (or a quarter) of
that time. PLAY blinks on the beat while anything is synced.

MIDI clock in is 24 pulses per beat, from 40 to 240 BPM (a clock outside that range is not followed: the tempo stays
where it was). With CLOCK on EXT and a clock coming in:

- **What syncs:** everything set to a note value follows the clock's tempo: the delay (DLY SYNC), the tremolo
  (TREM SYNC), the phaser and chorus (PHA SYNC, CHO SYNC), the filter's LFO (LFO SYNC; not with RETRIG on, where the
  keys restart it) and the vibraphone's motor (its SYNC), and also the arpeggiator, the sequencer and the PLAY
  light. The tempo locks after one beat of clock and follows a tempo change within about two beats. (The touch WAH
  follows your playing, not a tempo; a TAPE delay's speed settles to a new tempo over a few beats, as a tape's
  does.)
- **The arpeggiator and the sequencer play on the clock's pulses themselves**, not just at its tempo: an
  arpeggiator step on every 6th pulse at 1/16 (every 24th at 1/4), counted from Start; the sequencer moves 4 of its
  96 steps a beat with each pulse (smoothly in between, never ahead of the clock), so a pattern stays with the
  computer's or the drum machine's bars, swing and QUANT included, and records on the same grid.
- **In time with the beats:** the synced LFOs are locked to the clock's beats, not only to its tempo: each
  LFO's cycle starts on the beat (where a reset would start it: a tremolo is loudest a quarter of its cycle later),
  and a cycle longer than a beat (1/2, 1 BAR,
  2 or 4 BAR) starts on beat 1 of a bar counted from the last Start. An LFO that is out of step catches up gradually
  over a beat or a few (its speed changes by at most 1/8, so there is no jump in the sound); while nothing sounds it
  jumps into place at once. A synced delay's time glides to a new tempo without clicks.
- **Start** makes the next clock pulse beat 1 of bar 1, and starts the sequencer there (again from bar 1 if it was
  playing; a Start with no clock at all starts the pattern after 100 ms). **Continue** carries on counting from where Stop left it: the sequencer starts at that point of its
  pattern (the pattern repeating from the last Start). **Stop** stops the sequencer; the synced effects keep running
  at the last tempo. When the clock stops altogether, everything goes on at the last tempo it had. A **Song
  Position** sent while stopped (where a computer's playhead was moved to) is where Continue then goes on. Pulses
  lost on the way (a busy moment) are still counted, so the bars stay in place.
- **Without a clock** (CLOCK INT, or EXT with no clock coming), the synced LFOs lock to the FM-1's own beats the
  same way: a tap on PLAY sets where the beat is (and bar 1, for the longer cycles), Start on the sequencer too, and
  when a clock stops, its last beats go on at its tempo without a jump.
- While a clock leads, tapping **PLAY** and turning **BPM** change nothing (no jump in the tempo); **BPM** shows the
  clock's tempo. The clock's tempo is not saved as a setting: back on CLOCK INT, the FM-1's own BPM is used again.
  If the clock drops out for a moment (under 2 s), the sequencer waits and goes on with the song when it comes back.
  Frodhes does not send MIDI clock.
The tempo also drives the arpeggiator and the sequencer. The tempo you set by hand (TAP or BPM) is kept when you
switch off; the one a MIDI clock sets is not, and a clock never causes a save.

## 9. Scales

On the SCALE page, **SNAP** lets every key play but moves out-of-scale notes onto the scale, and **DEGREE** turns the
white keys into the scale's steps (C plays the root) so you can play any scale with white keys only. Both act on
the FM-1's keys; notes from USB-MIDI play as sent (except as the root of a DIATONIC chord).
While a scale is on, the keys that play its root glow dimly (in DEGREE: the C keys), so you can always find the
keynote.

## 10. Sustain and polyphony

**REC** is the sustain pedal. **Hold** it like a pedal, or **tap** it to latch the sustain on, and tap again to
release. The header shows **SUS** while it holds. Over MIDI, CC 64 works with half-pedalling. Letting a MIDI pedal
up (CC 64 below 64) also releases a latched REC, so a real pedal always works as a pedal; with no CC 64 coming in,
the latch stays on until you tap REC again.

**Polyphony: 10 voices.** Up to 10 notes sound at the same time on every instrument, counting
notes still ringing under the sustain and the notes that chord mode, the arpeggiator and the sequencer play. Striking
a note that is still sounding reuses its voice, so repeated notes do not use up voices. One note more takes over the
quietest released note first, or else the oldest held one, with a short fade (1.5 ms) so there is no click. The
audio also has a safety guard: if it ever ran late, the quietest released note would be faded out rather than
letting the sound break up. In testing the worst case (every voice on low notes at full velocity with the pedal and
every effect on, the reverb on INFINITE, the USB audio at 48 kHz) takes the whole audio interrupt about 3.8 ms on the
Rhodes while bending (3808 µs), 4.0 ms with SPREAD at full width and bending (3972 µs), up to 4.2 ms on VIBES
and 4.0 ms on MARIMBA of the 5.8 ms available per audio block; the longest single
block in minutes of the stress test (every voice held by the pedal, chords across the registers) was 3.8 ms at 48 kHz
and 3.6 ms at 44.1 kHz on the Rhodes, the same or less on VIBES and MARIMBA (measured on the FM-1), and the guard never acts.<!-- FM-1 (2026-10-25, the release package sha256 2ca1d517c430, final-rel/SUMMARY.md), every effect on, TSH8, TAPE, USB 48 kHz, 10 voices: whole ISR Rhodes bending 3808 us, SPREAD 127 + bend 3972, VIBES 4164, MARIMBA 4032 (the highest of the TYPEs); stress_max longest block Rhodes 48 kHz 3812, 44.1 kHz 3612; 0 shed. --><!-- FM-1 (2026-10-25, int-final after the CPU trims and the RAM hot loops, tools/cpu_check.sh), every effect on, TSH8, TAPE, INFINITE, USB 48 kHz, 10 voices, 3 takes each: whole ISR Rhodes SPREAD 127 + bend 3916-3972 us (4224-4268 before), --bend-sweep 3752-3772, VIBES 4040-4120 (4144-4216 before), MARIMBA 3988-4000; the stress_max figures below are 353d023's. --><!-- FM-1 (2026-10-25, build 353d023), every effect on, TSH8, TAPE, USB 48 kHz, 10 voices: whole ISR INFINITE Rhodes 3936/3892/3880 us, --bend-sweep 4028, SPREAD 127 4040, with bend 4172, VIBES 4052, MARIMBA 4000; HALL 3860/3976/3916, PLATE 3896/4116/3968; 0 shed, USB clean (build/final-rev/cpu_*.log); stress_max whole ISR 48 kHz Rhodes 4064 (final-rev3) / 4060 (final-rev), VIBES 3760, MARIMBA 3524; 44.1 kHz Rhodes 3832, VIBES 3524, MARIMBA 3292; 0 events, 0 shed; soak 8 runs 0 events (build/soak/rounds.txt). --><!-- FM-1 (2026-10-24), every effect on, the heaviest drive TSH8, delay TAPE, USB 48 kHz, 10 voices: hil_cpu --worst --usb 48000 x3 on build 57e0025: whole ISR Rhodes 3784/3760/3756 us (app 3504-3540), --bend-sweep 3880, VIBES 3956, MARIMBA 3916, 0 shed, USB clean (build/final2/cpu_*.log); SPREAD 127 (HALL, every effect, TSH8, TAPE): whole ISR 3916/3908/3920 us, with bend 4036 (build/final2b/cpu_spread_*.log); stress_max (hil_crackle.py) on 57e0025: whole ISR at 48 / 44.1 kHz Rhodes 3788 / 3604 us, VIBES 3460 / 3244, MARIMBA 3224 / 3036, 0 shed, 0 events (48 kHz: one marimba-worst2 USB step that the jack shows too, an hil_crackle false step fixed in d053a88) (build/final2/stress_*-report.txt). Update with SETUP's figure together. -->

## 11. Sound and volume tips

- **KEY VEL** on the main page sets the velocity of a medium press (with TOUCH FIXED, of every press). 96–108 is a
  good start; the bark comes in above about 100. If you play softly and the sound stays thin, try TOUCH **LIGHT**
  (SETUP); if your normal playing already barks, **HEAVY**.
- **MASTER is digital.** For the least hiss on the headphone jack, keep MASTER high and turn your speakers or
  headphones down instead. Between notes the output is exact silence, so the jack is silent too.
- **TYPE** changes the whole instrument: try BELL DYNO for glassy ballads and SUITCASE for the classic vibrato with
  the tremolo on PAN.
- Every effect is set to keep about the same volume when you switch it on; the compressor's MAKEUP and each preset's
  LEVEL (SETUP) let you fine-tune it.

## 12. MIDI

Frodhes is a class-compliant USB-MIDI device and also listens to the FM-1's **TRS MIDI input** (3.5 mm; MIDI page:
TRS IN, CHANNEL). Notes play with their velocity (and go through the arpeggiator when it runs); the FM-1's keys
also send their notes to the computer.

**MIDI out is USB only:** the FM-1's keys, chord mode, the arpeggiator and the sequencer send their notes on CHANNEL
(channel 1 when ALL). Notes that come in over MIDI are not sent back, but what the arpeggiator or the sequencer
makes from them is, so switch MIDI thru off in your computer's software. No clock, CCs or program changes are sent.
The TRS jack is MIDI in only. **Program Change** 0–15 loads F01–F16, 16–31 loads U01–U16 (as before 0.9.5), and 32–34 loads F17–F19.

**Pitch bend** (the pitch wheel, USB or TRS, on CHANNEL) bends the Rhodes ±2 semitones: every note that sounds
glides with the wheel and a note struck while the wheel is moved starts at the bent pitch. The bend stays across a
preset change and adds to TUNE. CC 121 (reset all controllers) and pulling the USB cable put it back to 0. VIBES and
MARIMBA ignore pitch bend: their bars always play at their own pitch. (The retuning is genkigenki's work.)

**Release velocity** (the note-off velocity, from a keyboard that sends it) shapes how the Rhodes' damper lands: a
quick lift (127) damps faster and harder, with a slightly louder knock (NOISE); a slow lift (1) lets the felt settle
gently and the note ring on a little longer. 64, or no release velocity (a note-off at 0, a note-on at velocity 0),
is the normal release. The FM-1's own keys have no release speed: they always release normally. VIBES and MARIMBA
ignore it. (The damper's own settle follows the register: a little slower in the bass, quicker in the
treble.)

**SPREAD** (CC 105, saved with the preset; MIDI only, there is no card for it) spreads the Rhodes across the stereo
field by key, low keys left, high keys right, as seen from the player. 0 (the default) is the mono piano; 64 a gentle
width; 127 places the lowest key of an 88-key piano fully left and the highest fully right. It is set at each strike
(turning it moves the next notes). The chorus, phaser, delay and reverb take the spread; COMP, DRIVE, the FILTER and
TREM/WAH work on the mono sum, and SPREAD follows their level (a hard drive narrows the image a little). VIBES and
MARIMBA ignore it.

| CC | Parameter | CC | Parameter |
| --- | --- | --- | --- |
| 64 | Sustain (half-pedal) | 80 | TYPE (0–31 STAGE, 32–63 SUITCASE, 64–95 FELT, 96–127 DYNO) |
| Pitch bend | ±2 semitones, the Rhodes types only (VIBES and MARIMBA ignore it) | 121 | Reset all controllers: the pitch bend back to 0 |
| 47 / 48 | VIBES MOTOR on (≥ 64) / DEPTH (no effect on MARIMBA) | 49 / 50 / 51 | VIBES MOTOR SPEED / SHAPE / SYNC |
| 79 | TYPE of the bar instruments: 0–95 VIBES, 96–127 MARIMBA | 9 | Developer: the bar instruments' mode cap, VIBES and MARIMBA (0 = automatic, 1–49 = 0–48 upper modes, ≥ 50 = none; not saved) |
| 10 | Developer: reset (≥ 64): every LFO, the delay / reverb lines and the noise to their start state (for comparing recordings) | | |
| 81 | DRIVE (AMOUNT) | 91 | REVERB (MIX) |
| 82 / 83 | DRIVE TONE / MIX | 92 / 93 / 94 | REVERB DECAY / TONE / PRE-DELAY (0–80 ms) |
| 84 | DRIVE type: 0–63 OFF, 64–75 KHLON, 76–87 TSH8, 88–99 OHCD, 100–111 BHLUE, 112–127 FDRV | | |
| 95 | REVERB type: 0–31 OFF, 32–63 HALL, 64–95 PLATE, 96–127 INFINITE | 73 | REVERB SIZE (0 = AUTO: from MIX, on INFINITE 5.0) |
| 68 / 69 | REVERB MOD (64 = the default) / LOW CUT (0 = off, 20–800 Hz) | | |
| 17 | TREM/WAH: 0–42 off, 43–85 trem/pan, 86–127 wah | 18 / 19 | TREM DEPTH / RATE |
| 20 / 21 / 22 | TREM SYNC / MODE (0–63 pan, 64–127 am) / SHAPE | 85 | Chorus / phaser switch: 0–42 both off, 43–85 chorus, 86–127 phaser |
| 86 / 87 | CHORUS DEPTH / SPEED (the phaser's, when only the phaser is on) | 23 | CHORUS SYNC |
| 12 / 13 / 14 | PHASER on / DEPTH / SPEED | 15 / 16 | PHASER SYNC / FEEDBACK |
| 88 / 89 / 90 | DELAY type (0–31 off, 32–63 DIG, 64–95 ANA, 96–127 TAPE) / MIX / TIME | 25 / 26 / 27 | DELAY SYNC / FEEDBACK / TONE |
| 106 / 107 | COMP on (≥ 64; with THRESHOLD at 0 it starts at −16 dB with the make-up, as the panel) / THRESHOLD (0 to −40 dB; switch it on with 106) | 112 / 113 | COMP RATIO / MAKEUP |
| 108 / 109 | COMP ATTACK / RELEASE | 110 | LEVEL (64 = 0 dB) |
| 111 | All effects off (≥ 64) / on | 28–31 | TONE / BARK / BELL / HAMMER |
| 114 | USB VOL: knob (< 64) / fixed | 24 | Reserved (accepted and saved, no effect yet) |
| 105 | SPREAD: the Rhodes across the stereo field by key (0 mono, the default; saved with the preset) | | |
| 116 | Developer: CPU load and USB audio health query (replies on CC 115–118; 112 / 113, the bar instruments': the lowest mode cap / the most live upper modes; 107–110, the whole audio interrupt and its tail after the sound; 100–102, the USB audio counters since the last query: underruns, missed packets, overruns, rate adjustments, the ring's fill and its low point, the audio interrupt's spacing, the stream's state; the longest stretch a flash write held the interrupts off, the flash part's suspends, the longest erase; `tools/hil_cpu.py` reads them) | 119 | Developer: raw voice (≥ 64 bypasses every effect and the limiter; FX does not undo it) |
| 4 | Developer: battery query (replies CC 4 / 5 = the reading: value / 8, value mod 8; CC 6 = the segments shown, 4 = USB power) | 8 | Developer: a simulated battery reading (value x 8; 0 = the real one) |
| 102 / 103 / 104 | DECAY / RELEASE (VIBES: DAMPER; MARIMBA: MUTE) / NOISE | 120 / 123 | All notes off: every note held over MIDI is let go and rings out through the dampers (MARIMBA: stopped by the hand), every tail ending within 8 s (keys still held on the FM-1, the arpeggiator's HOLD and the sequencer keep going) |
| 56 / 57 | CHORD TYPE (0–14 as listed) / INVERSION (0–3) | 58 / 59 | CHORD VOICING (0–2) / STRUM (0–12 × 5 ms) |
| 60 / 61 | SEQUENCER play (≥ 64) or stop (< 64) / BARS (1–16) | 62 / 63 | SEQUENCER QUANT (0–5) / recording (≥ 64) |
| 43 / 44 | SEQUENCER CLEAR / UNDO (≥ 64) | 45 / 46 | SEQUENCER ERASE (≥ 64 on) / SWING (50–75) |
| 70 / 74 | FILTER TYPE (7 bands: off, LP12, LP18, HP12, HP18, BP, notch) / CUTOFF | 71 / 72 | FILTER RESONANCE / LFO AMOUNT |
| 75 / 76 | LFO SHAPE (6 bands) / RATE | 77 / 78 | LFO SYNC / RETRIG (free, key) |
| 3 | Developer: a colour test pattern on the screen (≥ 64; < 64 back to the page), to calibrate screenshots against the panel | 2 | Developer: the screen's colour candidate, for choosing on the FM-1: 0 = A black (the default), 1 = B dim outline, 2 (or more) = C model-matched; not saved |
| 11 | Developer: the cards' design, for comparing on the FM-1 (not saved): 3 = the normal look (faders, selector blocks, switch lights); 0 = the rows before the design pass (a choice written large, no fill), 1 = a fill on every card, 2 = a thin meter line under each row, 4 = selectors as small pips under the value, 5 (or more) = a switch lights its whole card | | |
| 41 / 42 | Developer: key velocity (not saved): 41 sets the key converter's test mode (0 normal, 1 off, 2 stress, 4 self-test; 8 / 9 every press's reading out on CC 33–36, on / off), 42 asks for its counters (replies on CC 38–40); `tools/hil_keyvel.py` and `tools/hil_cpu.py --keyadc` use them | | |
| 52 / 53 | ARP MODE (0 off, 1 up, 2 down, 3 up-dn, 4 order, 5 random) / RATE (0–5: 1/4 … 1/32) | 54 / 55 | ARP OCTAVES (0–3 = 1–4) / GATE (10–100 %) |

On the computer the FM-1 is called **Frodhes**, its MIDI port and its audio input alike (the update mode is called
"Felucca Update", after the platform). It has its own USB product id, so a computer that knew the FM-1 under the name
earlier firmware gave it sets it up as a new device, named Frodhes, by itself: in a DAW, switch the Frodhes port on
once (in its MIDI preferences) where the old one was. A Mac keeps the old entry in Audio MIDI Setup's MIDI Studio
window, greyed out (offline): nothing needs deleting, but you can remove it. It appears as a USB audio input (class-compliant, stereo, 16-bit, 44.1 or 48 kHz): you can record exactly
what it plays, digitally, without the headphone jack's hiss. It does not play audio from the computer. Saving the
settings while it is recorded does not disturb the recording (section 6, "What is kept, and when"). The USB audio holds one block (5.8 ms) more
than the jack in hand, so that no render, however long, can run it dry (that was a click now and then at full
polyphony): a frame leaves the FM-1 about 6-11 ms after its block is computed, at either rate; a DAW's latency
compensation takes care of it when you record.

## 13. If something goes wrong

- **No sound:** check MASTER, that FX has not bypassed a sound you relied on (BYP in the header), LEVEL on SETUP,
  and on the MIDI page that CHANNEL is ALL or the channel your keyboard sends on.
- **Keys play odd notes or keep playing:** chord mode (CHORD page), the arpeggiator with HOLD (ARPEGGIO) or the
  sequencer may be on; the EDIT, ARP and SEQ lights show it.
- **The sound is thin or quiet:** raise KEY VEL on the main page, or try TOUCH **LIGHT** on SETUP.
- **A crash** shows FRODHES CRASH and three numbers for 8 s, then the FM-1 restarts. Please report all three.
- **At power-on** Frodhes comes back as you left it, on the PERFORM page: the sound, octave, tempo, scale, chord mode,
  arpeggiator (HOLD too) and MIDI settings, with the effects on and the sustain off. So if keys play chords or an
  arpeggio right away, check the CHORD and ARPEGGIO pages.
  The setup is kept in two places in the flash and a save writes the older one, so if the power goes while it is
  being saved (a fraction of a second, about 2 s after a change), only that last change is lost, never the setup.
- **It does not start:** switch the FM-1 off, hold **OCT−** and switch it on. That is the USB rescue: install again
  from the hub or with `fm1_install.py`. Your FM-1 can always be recovered this way.
- **It shows UPDATE MODE or UPDATE FAILED at power-on:** an install was cut off; install again (section 1).
- **Back to the original firmware:** the hub's "Official firmware" section, with V15 from m-vave.com.

---

Frodhes is free software under the GPL-3.0 licence, built on [Dinghy](https://github.com/zvenson/dinghy),
[Felucca](https://github.com/hugelton/Felucca) and [SLOOP](https://github.com/isod89/sloop-fm1). Source and
issues: [github.com/leonardoMirabal/frodhes](https://github.com/leonardoMirabal/frodhes).

## 14. Specifications

| | |
| --- | --- |
| **Sound** | A physical model computed live, no samples. Rhodes: hammer, tine, tonebar and pickup, every key a little different, the tine's slow whirl on E2–C6, SPREAD across the stereo field (CC 105); 4 types (STAGE 77, SUITCASE, EARLY FELT, BELL DYNO). VIBES: a vibraphone, mallet, bar modes, tuned tube and motor. MARIMBA: rosewood bars, mallet, bar modes and tuned tube, no damper or motor (C2–C7 authentic) |
| **Polyphony** | 10 voices (the Rhodes, VIBES and MARIMBA); an 11th note takes over the quietest released note (else the oldest held one) with a 1.5 ms fade |
| **Range** | 88 notes, A0–C8 over MIDI (notes outside play the nearest end); the 27 keys play F3–G5, OCT ±2 octaves, TRANSP ±24 semitones, TUNE ±50 cents |
| **Velocity** | MIDI 1–127. The FM-1's keys: F3–C5 sense how firmly they land (TOUCH LIGHT / MEDIUM / HEAVY, KEY VEL the velocity of a medium press, default 96), C#5–G5 play the recent average; TOUCH FIXED: every key at KEY VEL (1–127) |
| **Audio** | 44.1 kHz, integer processing, 256-frame blocks (5.8 ms); output limiter; exact digital silence 200 ms after the sound falls below −66 dBFS |
| **Latency** | About 14 ms from a MIDI note to the USB audio, including the computer's buffers (measured median 13.7 ms at 44.1 kHz, 12.9 ms at 48 kHz); the headphone jack about 10 ms |<!-- FM-1, 57e0025, tools/hil_usb_onset.py, 9 notes: 44.1 kHz USB median 13.7 ms (11.2-15.8), jack 10.0; 48 kHz USB 12.9 (10.7-14.6), jack 10.3 (build/final2/usb_onset.log) -->
| **Outputs** | Stereo headphone jack (MASTER); USB audio to the computer, stereo 16-bit, 44.1 or 48 kHz, following MASTER or fixed (USB VOL) |
| **MIDI in** | USB-MIDI and the TRS jack (on / off); CHANNEL ALL or 1–16 |
| **MIDI out** | USB only: notes from the keys, chord mode, arpeggiator and sequencer. No clock, CC or program change out |
| **Clock** | CLOCK INT or EXT: 24 PPQN in from USB or TRS, 40–240 BPM; the synced effects, the arpeggiator and the sequencer locked to its pulses; Start plays from bar 1, Continue from where Stop left it, Stop stops |
| **Tempo** | 40–240 BPM; tap tempo on PLAY |
| **Presets** | 19 factory (F01–F19) + 16 user (U01–U16); Program Change 0–15 (F01–F16), 16–31 (U01–U16), 32–34 (F17–F19) |
| **Effects** | COMP → DRIVE → FILTER → TREM/WAH → PHASER → CHORUS → DELAY → REVERB → limiter |
| Compressor | Threshold 0 to −40 dB, ratio 1.5:1–20:1, attack 0.1–50 ms, release 20 ms–1.5 s (CC 109), makeup 0 to +24 dB |
| Filter | LP12, LP18, HP12, HP18, BP, NOTCH; 30 Hz–16 kHz; resonance Q 0.7–20; LFO ±4 octaves |
| LFO | SINE, TRI, SAW, RAMP, SQR, S&H; 0.5–12 Hz or synced; FREE or KEY retrigger |
| Tremolo / wah | PAN, TREM (SHAPE: sine to square, then an amp tremolo), WAH (the wah keeps the level); 0.5–12 Hz or synced. VIBES: its own motor, 0.5–10 Hz |
| Phaser / chorus | Stereo phaser with feedback, stereo chorus; 0.25–6 Hz or synced |
| Delay | DIG, ANA or TAPE (a tape echo); 20 ms–1.48 s or synced; FEEDBK independent of MIX, ANA and TAPE self-oscillating from FEEDBK ≈ 115 |
| **Scales** | 11 scales, any root; OFF / SNAP / DEGREE |
| **Chords** | 15 chord types, inversions, CLOSE / OPEN / +BASS voicing, strum 0–60 ms |
| **Sequencer** | 1–16 bars of 4/4, up to 448 notes; QUANT OFF, 1/4, 1/8, 1/16, 1/8T, 1/16T; SWING 50–75 %; overdub and step recording |
| **Arpeggiator** | UP, DOWN, UP-DN, ORDER, RANDOM; 1/4–1/32; 1–4 octaves; gate 10–100 %; HOLD; up to 16 notes |
| **Sustain** | REC (hold, or tap to latch) and CC 64 with half-pedalling |
| **Recovery** | Hold OCT− while switching on: the USB rescue. No factory reset |

## 15. Appendix: the SMK-37 (experimental)

Frodhes also builds for the SMK-37 (SMK-37 Pro, SMK-37 Elite, MKE-P37: `frodhes-smk37-experimental.fwsc`,
`frodhes-smk37-elite-experimental.fwsc`, `frodhes-smk37-mke-experimental.fwsc`, on the
[release page](https://github.com/leonardoMirabal/frodhes/releases/latest)). **These builds are experimental: they are not tested on
hardware by us.** Back up your flash first (the steps are in [SMK37.md](SMK37.md)); the way back is your model's stock
package through the stock updater.

Everything in this manual applies, with these differences:

- The FM-1's buttons are on the SMK buttons of the same meaning: PATCH = HOME, PARA = EDIT, CHORD = ENV,
  NOTE REPEAT = LFO, SCALE = SEL, GLOBE = GLO, BT = SAVE; the others keep their names. The screen's hints use these
  names.
- K1 is MASTER; K2 SELECT, K3 ALGORITHM, K4 PRESETS; K5..K8 are the four cards (the FM-1's K1..K4). < > step through
  the pages; KNOB BANK held shows a panel check (the raw readings).
- 37 keys, C3..C6 at OCTAVE 0, each at its own velocity: KEY VEL is the velocity of a medium press.
- The pads: 1..6 TYPE, 7 / 8 the previous / next preset, 9 DRIVE, 10 COMP, 11 FILTER, 12 TREM/WAH (VIBES: the motor),
  13 PHASER, 14 CHORUS, 15 DELAY (on / off), 16 SUSTAIN (held, or a tap latches).
- The faders: FADER BANK A plays the instrument's first VOICE page, bank B the four PERFORM knobs. A fader takes over
  once it reaches its card's value; until then the footer says where to move it.
- Pitch wheel: pitch bend, ±2 semitones (the Rhodes). Mod wheel: the TREM/WAH depth (VIBES: the motor's depth). The
  pedal jack takes a sustain pedal of either polarity (plug it in before switching on).
- 7 voices. No TRS MIDI input (no TRS IN card), no key LEDs (no LEDS card on DISPLAY); BRIGHTNESS dims the pads and
  the button LEDs.
