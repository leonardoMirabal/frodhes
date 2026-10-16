# Draft: VIBES in the manual and README (P8, not applied yet)

Draft text for when the bar engine is merged and has passed its scan and the FM-1 tests. Each block says where it
goes. Nothing here is in `docs/MANUAL.md` or `README.md` yet. Numbers marked *(check)* depend on P7 (the FM-1
measurements and listening): confirm them before this goes in. Screenshots already rendered by
`tools/manual_shots.py`: `voice-vibes.png`, `decay-vibes.png`, `trem-motor.png`.

---

## docs/MANUAL.md

### Title line (replaces line 3)

> **A physically modelled Fender Rhodes and vibraphone for the M-VAVE FM-1.** Version X.Y.Z.

### Intro paragraph (replaces the first sentence)

> Frodhes turns the FM-1 into an electric piano and a vibraphone. Every note is computed from the physics of the real
> instrument: on a Rhodes the hammer strikes a tine, the tine rings against its tonebar and a magnetic pickup turns
> the motion into sound; on the vibraphone a mallet strikes an aluminium bar, a tuned tube under it rings along, and
> the motor's spinning discs open and close the tube. No recordings are played back.

### Contents

Add after "4. The pages": `5. [VIBES: the vibraphone](#5-vibes-the-vibraphone)` and renumber the rest (Presets
becomes 6 ... If something goes wrong becomes 13). Check the anchors.

### 3. Buttons and knobs

| Control | What it does |
| --- | --- |
| **ALGORITHM** (knob) | The TYPE (the four Rhodes and VIBES), from any page (on SEQUENCER: the step cursor) |
| **PRESETS** (knob) | Browse the sounds F01–F14 and your U01–U16 (loads when you stop turning; on SEQUENCER: bar by bar) |

### FRODHES (the main page): the TYPE row

| K1 | **TYPE** | The instrument: **STAGE 77** (the classic Mark I), **SUITCASE** (with its Peterson amp and 4×12 speakers), **EARLY FELT** (1970 felt hammers: rounder, darker), **BELL DYNO** (Dyno-My-Piano voicing: glassy, bell-like), **VIBES** (a vibraphone: see [VIBES](#5-vibes-the-vibraphone)). Applies to the next notes; switching between a Rhodes and VIBES fades out what is still ringing. |

### VOICE and DECAY: one line under each table

> On **VIBES** these cards are the vibraphone's: see [VIBES](#5-vibes-the-vibraphone).

### TREM/WAH: the EFFECT row

| K1 | **EFFECT** | **OFF**, **PAN** (the Suitcase stereo vibrato), **TREM** (volume tremolo), **WAH** (touch wah: harder playing opens it), and on VIBES **MOTOR** (the vibraphone's motor: see VIBES) |

And under the table:

> **MOTOR** only appears on VIBES. A preset with MOTOR played on a Rhodes TYPE gives the TREM tremolo (the card says
> TREM). With MOTOR, RATE shows the motor speed in Hz (0.5–10 Hz) and TREM SYNC on the TEMPO page locks it to the
> tempo.

### New section: 5. VIBES: the vibraphone

> ## 5. VIBES: the vibraphone
>
> Turn **ALGORITHM** (or the TYPE card) to **VIBES** and the FM-1 plays a vibraphone: aluminium bars struck with
> mallets, each with its tuned tube underneath, and the motor that gives the vibraphone its pulsing shimmer. Like the
> Rhodes it is computed, not sampled: the bar's own modes (the fundamental and its tuned overtones, about 4 and 10
> times higher), the tube that takes a moment to build up under the bar, and the motor's discs.
>
> The pages stay the same: on VIBES, the VOICE and DECAY cards change their names and what they do.
>
> ![VOICE on VIBES](manual/img/voice-vibes.png)
>
> | Knob | Card | What it does on VIBES |
> | --- | --- | --- |
> | K1 | **STRIKE** | Where the mallet hits the bar. **CENTRE** (left end): the purest tone, the overtones quiet; toward **L/3** (the middle of the knob, the usual spot) more of the bright overtone; to the right, toward the **EDGE**: off the centre line, a slightly metallic, less pure tone |
> | K2 | **MALLET** | How hard the mallet is: **SOFT** and **YARN** (round, warm; jazz), **CORD** (clearer), **RUBBER** (hard, bright, with a click). The card shows the mallet's name where it is pure, a number between |
> | K3 | **TUBE** | How much the tube rings with the bar: 0 is a bare bar (dry, thin, rings longer), up is a fuller, warmer note that blooms a little after the strike and dies sooner, as on a real vibraphone |
> | K4 | **WEIGHT** | The mallet's weight: light is brighter and quicker, heavy is rounder with more body. How hard you play (KEY VEL, MIDI velocity) stays the strike's speed |
>
> ![DECAY on VIBES](manual/img/decay-vibes.png)
>
> | Knob | Card | What it does on VIBES |
> | --- | --- | --- |
> | K1 | **RING** | How long a struck bar rings (about half to twice the normal time) *(check)* |
> | K2 | **DAMPER** | How firmly the damper bar stops the notes when you let go: soft lets them fade, firm stops them short |
> | K3 | **NOISE** | The mallet's contact and the damper's touch |
> | K4 | **TUNE** | Fine tuning, ±50 cents (a real vibraphone is often tuned to A = 442 Hz: +8 cents) *(check)* |
>
> **The pedal.** The vibraphone's damper pedal works like the Rhodes' sustain: **REC** (hold, or tap to latch) or a
> sustain pedal over MIDI (CC 64, half-pedal too). Without it the damper stops each note when you let go.
>
> **The motor.** On the TREM/WAH page, turn **EFFECT** to **MOTOR**:
>
> ![MOTOR on TREM/WAH](manual/img/trem-motor.png)
>
> | Knob | Card | What it does with MOTOR |
> | --- | --- | --- |
> | K1 | **EFFECT** | **MOTOR** (after WAH; only on VIBES) |
> | K2 | **DEPTH** | How far the discs close the tubes: a gentle pulse to a deep throb |
> | K3 | **RATE** | The motor speed, 0.5–10 Hz (classic: about 3.5 Hz; slow for ballads, fast for an old-school shimmer). Synced to the tempo when TREM SYNC (TEMPO page) is set |
> | K4 | **SHAPE** | How sharply the discs open: soft is a smooth pulse, sharp a shorter, more pointed one |
>
> As on a real vibraphone the motor works on the tubes, so it is strongest on the long, ringing middle of a note
> and gentler on the attack and the high overtones; low notes pulse deeper than high ones, and at fast speeds the
> pulse gets rounder.
>
> **The range.** A concert vibraphone has three octaves, **F3 to F6**; that is where VIBES is measured and tuned
> against the real instrument. The notes below and above still play (every MIDI note does), extended from the same
> physics. The FM-1's own keys (F3–G5) sit right in the vibraphone's range.
>
> **Presets.** **F13 VIBE MOTOR** (the motor on at 3.5 Hz, a little hall) and **F14 VIBES DRY** (motor off, close
> and dry) *(check: values tuned by ear on the FM-1)*.
>
> **Polyphony.** VIBES plays up to N notes at once *(check: 12 or 8, from the FM-1 measurement)*; with the pedal
> down, the oldest notes give way to new ones with a short fade.

### 6. Presets (was 5): the text and the table

> Turn **PRESETS** to browse: **F01–F14** are the factory sounds (F13 and F14 are VIBES), **U01–U16** your own.

| | | | |
| --- | --- | --- | --- |
| F01 STAGE 77 | F02 70S PHASER | F03 SUITCASE | F04 80S BALLAD |
| F05 SLAPBACK | F06 BRIGHT CHO | F07 FUNK WAH | F08 BARK |
| F09 TAPE ECHO | F10 DREAM | F11 RADIO | F12 AM TREMOLO |
| F13 VIBE MOTOR | F14 VIBES DRY | | |

> The name of a saved preset is made from the type and the effects on ("VIBE MOT", "SUIT TRM PHA").
>
> Your user presets keep their place: updating from a version before VIBES moves nothing (U01 is still U01).

### 11. MIDI (12 after the renumbering)

> **Program Change** 0–13 loads F01–F14, 16–31 loads U01–U16.

Rows to change in the CC table:

| CC | Parameter |
| --- | --- |
| 80 | TYPE (0–31 STAGE, 32–63 SUITCASE, 64–95 FELT, 96–111 DYNO, 112–127 VIBES) |
| 20 / 21 / 22 | TREM SYNC / MODE (0–63 pan, 64–127 am; on VIBES 112–127 motor) / SHAPE |

### 10. Sound and volume tips (11 after the renumbering): one more line

> - **VIBES**: YARN or CORD with TUBE around the middle and the motor at 3–4 Hz is the classic jazz vibraphone; the
>   pedal held and DAMPER soft for washes; RUBBER and STRIKE toward the edge for a harder, more metallic attack.

---

## README.md

### Title line (line 3)

> **A physically modelled Fender Rhodes and vibraphone for the M-VAVE FM-1.**

### First paragraph (replaces the first two sentences and the types)

> Frodhes turns the FM-1 into an electric piano and a vibraphone. Every note is computed live from the mechanics of
> the instrument: on a Rhodes a hammer strikes a tine, the tine rings against its tonebar and a magnetic pickup
> turns the motion into sound, with the bark of hard notes and the bell of the attack; on the vibraphone a mallet
> strikes a bar, its tube rings along and the motor makes it pulse. Five instrument types (Stage 77, Suitcase, Early
> Felt, Bell Dyno and Vibes), a reface CP-style effects chain ...

and later in the same paragraph: "... a sustain pedal and **14** factory plus 16 user presets."

### Page table rows

| Page | K1 | K2 | K3 | K4 | Button |
| --- | --- | --- | --- | --- | --- |
| **VOICE** | TONE (VIBES: STRIKE) | BARK (MALLET) | BELL (TUBE) | HAMMER (WEIGHT) | EDIT |
| **DECAY** | DECAY (VIBES: RING) | RELEASE (DAMPER) | NOISE | TUNE | (SELECT) |
| **TREM/WAH** | OFF / PAN / TREM / WAH (VIBES: + MOTOR) | DEPTH | RATE | SHAPE | LFO |

(No research or paper references in the README.)

---

## web/app.js: the quick guide (one line)

> TYPE VIBES: the VOICE / DECAY cards become STRIKE, MALLET, TUBE, WEIGHT / RING, DAMPER, NOISE, TUNE; TREM/WAH gets
> MOTOR.

---

## What changed in the code for this (for the reviewer, not for the manual)

- TYPE 4 VIBES (`N_TYPE` "VIBE", `N_TYPE_LONG` "VIBES"); `TYPE_ENGINE` / `TYPE_SUB` / `TYPE_DRIVE_ROW` in params.c.
- TW_MODE 2 = MOTOR (`N_TWM` "MOT"); the TREM/WAH card's kinds OFF PAN TREM WAH MOTOR (MOTOR only on a bar TYPE).
- Factory presets F13 / F14 (PR_NF 14); the saved preset index moves once (settings `flags & 4`).
- CC 80: the four Rhodes types, unchanged (96–127 DYNO); CC 79: the bar types (VIBES; later types get bands); CC 21: 0–63 PAN, 64–127 AM as before, 112–127 MOTOR on VIBES only; PC 12 / 13.
