# Frodhes on the Move (draft, in development)

Frodhes also builds as a sound generator for the Move, a standalone groove box, through Schwung, the community module
host that runs on it. The engines (PIANO, VIBES, MARIMBA), the effects, the arpeggiator, the chords and the sequencer
are the FM-1's own code, built for the Move's processor. **This port is in development. Nothing on this page has been
checked on a Move yet:** every part below was checked on the host build only (the same code run on a computer,
`tools/ci_move.sh`). The parts marked *(not yet verified on a device)* are the ones a Move must still confirm.

## Install

You need a Move with Schwung installed. The module is a folder, `frodhes/`, with `dsp.so`, `module.json`,
`help.json`, `sustain.js` (the PEDAL screen) and `LICENSE` in it, and it goes in Schwung's sound generator folder.

```sh
python3 tools/move_install.py install            # prints what it would do (dry run)
python3 tools/move_install.py install --go       # copies the module over ssh and switches to it
python3 tools/move_install.py info --go          # the installed version and the sha256 of dsp.so
python3 tools/move_install.py rollback --go      # back to the module installed before
```

The installer copies the new module next to the old one, checks it, then switches by renaming. It keeps the two
modules installed before it (`--keep`). The Move's address (`--host`) and the sound generator folder
(`/data/UserData/schwung/modules/sound_generators`) are *(not yet verified on a device)*.

In Schwung, load **Frodhes** as the sound generator of a track. Its abbreviation on the screen is `FRDH`.

## The pages

The first page, **FRODHES**, has the sound type, the stereo spread, the sustain latch, the PEDAL screen and the preset
browser:
the 19 factory sounds (F01-F19) and 16 user sounds (U01-U16), as on the FM-1. Each page below holds up to eight
controls. The VOICE pages follow the engine of the TYPE: only the pages of the engine that plays are shown.

| Page | Controls |
| --- | --- |
| **FRODHES** | TYPE · SPRD · SUS · PEDAL |
| **PIANO VOICE** | TONE · BARK · BELL · HAMMR · DECAY · REL · NOISE · TUNE |
| **VIBES VOICE** | STRK · MALLT · TUBE · WGHT · RING · DAMPR · NOISE · TUNE |
| **VIBES MOTOR** | MOTOR · DEPTH · SPEED · SHAPE · SYNC |
| **MARIMBA VOICE** | STRK · MALLT · TUBE · WGHT · RING · MUTE · NOISE · TUNE |
| **COMPRESSOR** | THRSH · RATIO · ATK · MKUP · COMP · REL |
| **DRIVE/FILTER** | DRIVE · AMT · TONE · MIX · FILT · FREQ · RESO · LFO |
| **TREM/PHASER** | TREM · DEPTH · RATE · SHAPE · PHASE · DEPTH · SPEED · FDBK |
| **CHORUS/DELAY** | CHOR · DEPTH · SPEED · SYNC · DELAY · MIX · TIME · FDBK |
| **REVERB** | RVB · MIX · DECAY · TONE · PDLY · SIZE · MOD · LCUT |
| **TEMPO/SCALE** | BPM · TRSYN · PHSYN · DLSYN · MODE · SCALE · ROOT · TRNSP |
| **CHORD/ARP** | CHORD · INV · VOICE · STRUM · MODE · RATE · OCT · GATE |
| **SEQUENCER** | BARS · QUANT · SWING · EDIT · PLAY · REC · CLEAR · UNDO |
| **LFO/SETUP** | SHAPE · RATE · SYNC · TRIG · DLTON · LEVEL · CHAN · CLOCK |

The controls are the FM-1's, with the same ranges, under shorter names (the Move's screen fits about five
characters). An option name longer than five letters is shortened the same way everywhere, to four letters: the
word's first syllable and the consonant after it (DORIAN shows as DORN, DEGREE as DEGR, LYDIAN as LYDN, HARMON as
HARM, MELODIC as MELO, MPENTA as MPNT, DIATONIC as DIAT, DIA 7TH as DIA7, RANDOM as RAND). The manual (`docs/MANUAL.md`) describes what each one does. Each page has a help text in `help.json`.
The pages are generated from the FM-1's own parameter tables (`tools/gen_move_module.py`), so the two stay alike.
How the pages look and respond on the Move's screen and knobs is *(not yet verified on a device)*.

## Sustain

The Move has no pedal jack. There are three ways to sustain, and they work together as on the FM-1:

- **A Move button as the pedal**, as REC on the FM-1: open **PEDAL** on the first page, then the **Capture** button
  is the sustain pedal. Hold it like a pedal; tap it to latch the sustain on, tap it again to let go. The screen
  shows SUS while the notes are held. While the PEDAL screen is open, Capture does not do its usual job on the
  Move. *(not yet verified on a device: which button, and whether it also works on the other pages)*
- **SUS** on the first page is the latch on its own: ON holds the notes as a held pedal does, OFF lets them go. It
  shows ON while the button holds the notes too.
- **A sustain pedal or keyboard on CC 64** (on the Move's USB-A port, or MIDI from a computer): half pedalling as
  on the FM-1. Letting the pedal up also releases the latch.

The arpeggiator's HOLD, the sequencer and the scales react to the sustain as on the FM-1. *(not yet verified on a
device)*

## MIDI

The MIDI of Schwung's track reaches Frodhes as on the FM-1 (`docs/MANUAL.md`, section 12): notes with velocity and
release velocity, pitch bend, CC 64, the parameter CCs and program change (0-15 F01-F16, 16-31 U01-U16,
32-34 F17-F19). *(not yet verified on a device)*

## What differs from the FM-1

- **16 voices** on every engine (the FM-1 plays 10).
- No PERFORM, ASSIGN or DISPLAY pages: the Move has its own pads, knobs and screen. The FM-1's KEY VEL, TOUCH,
  USB VOL, TRS, COLOR, BRIGHT and LEDS settings are not on the Move.
- SPREAD and the compressor's RELEASE, set on the FM-1 by MIDI or a page of their own, are on the pages here.
- The sound is part of the Move's set: Schwung saves and restores it with the track. *(not yet verified on a device)*
- Audio runs in blocks of 128 frames at 44.1 kHz (the FM-1 uses 256), with a working limit of 725 us of processing
  per block. *(not yet verified on a device)*

## Coming

- **HQ**: a higher-quality mode that uses the Move's faster processor (more detail in the engines and the effects).
  Not in this build.
- A screen of Frodhes's own and the pads laid out as a keyboard (chromatic, or in the SCALE page's scale).

## For developers

- `tools/gen_move_module.py` writes `move/module/` (module.json, the pages, help.json, `move_ui.h`);
  `--check` fails when the files are out of date with the FM-1's tables.
- `tools/ci_move.sh` runs the Move's checks on the host.
- `tools/target_closure.py` lists the sources of each target (FM-1, SMK-37, host, web, Move).
- `tools/move_hil.py` and `tools/device_null.py` are the device tests (CPU, crackle, the null test against the host).
  Over USB-C, as the FM-1 is tested: `usb-devices`, `usb-note`, `usb-vel`, `usb-sus`, `usb-clock`, `usb-stress`
  (MIDI to the Move's Standalone port, its USB audio recorded, the FM-1's analysis; `--fake` runs them against a
  model of the device). On the Move first: Settings, USB-C audio out = Main Out (the Move sets it back to Mic at
  every boot).
- The sustain control: `module.json` claims the Capture button (`capabilities.claims_ccs`, `SUS_BTN_CC` in
  `tools/gen_move_module.py`); Schwung gives a claimed button only to the module's screen script, so `sustain.js`
  passes its press and release to the plugin (`s_susbtn` 1 / 0), which runs the FM-1's REC code. The direct route,
  a knob or jog-wheel touch (`capabilities.touch_observe`), is off unless `dev_sus_touch` names the control
  (0-7 a knob, 9 the jog wheel).
