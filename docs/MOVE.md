# Frodhes on the Move (draft, in development)

Frodhes also builds as a sound generator for the Move, a standalone groove box, through Schwung, the community module
host that runs on it. The engines (PIANO, VIBES, MARIMBA), the effects, the arpeggiator, the chords and the sequencer
are the FM-1's own code, built for the Move's processor. **This port is in development. Nothing on this page has been
checked on a Move yet:** every part below was checked on the host build only (the same code run on a computer,
`tools/ci_move.sh`). The parts marked *(not yet verified on a device)* are the ones a Move must still confirm.

## Install

You need a Move with Schwung installed. The module is a folder, `frodhes/`, with `dsp.so`, `module.json`,
`help.json`, `sustain.js` (the PEDAL screen), `screen.js` (Frodhes' own screen) and `LICENSE` in it, and it goes in Schwung's sound generator folder.

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

The first page, **FRODHES**, has the sound type, the stereo spread, the sustain latch, the PEDAL screen, the effects
switch (FX), Frodhes' own screen (PAGES) and the preset browser: the 19 factory sounds (F01-F19) and 16 user sounds
(U01-U16), as on the FM-1. Each page below holds up to eight
controls. The VOICE pages follow the engine of the TYPE: only the pages of the engine that plays are shown.

| Page | Controls |
| --- | --- |
| **FRODHES** | TYPE · SPRD · SUS · PEDAL · FX · PAGES |
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
| **MAPPINGS** | AT CC · AT LO · AT HI · PA CC · PA LO · PA HI |

The controls are the FM-1's, with the same ranges, under shorter names (the Move's screen fits about five
characters). An option name longer than five letters is shortened the same way everywhere, to four letters: the
word's first syllable and the consonant after it (DORIAN shows as DORN, DEGREE as DEGR, LYDIAN as LYDN, HARMON as
HARM, MELODIC as MELO, MPENTA as MPNT, DIATONIC as DIAT, DIA 7TH as DIA7, RANDOM as RAND). The manual (`docs/MANUAL.md`) describes what each one does. Each page has a help text in `help.json`.
The pages are generated from the FM-1's own parameter tables (`tools/gen_move_module.py`), so the two stay alike.
How the pages look and respond on the Move's screen and knobs is *(not yet verified on a device)*.

## Mappings

The FM-1 has no pressure. On the Move, **MAPPINGS** turns the pads' pressure into one of the FM-1's controls:

- **AT CC / AT LO / AT HI**: channel pressure (aftertouch) drives the control on that MIDI CC number, from LO (no
  pressure) to HI (full pressure).
- **PA CC / PA LO / PA HI**: the same for poly pressure, from the most pressed held pad. Letting a pad go ends its
  pressure.

An HI under LO turns the direction round. A CC of 0 is off, and both are off by default: then pressure changes
nothing. The control moves as if a controller sent that CC (the CC numbers are the FM-1's, in `docs/MANUAL.md`). The
settings are kept with the track's state. *(not yet verified on a device)*

## Frodhes' own screen (PAGES)

The pages above are on the Move's own knob screen. **PAGES** on the first page opens a screen of Frodhes' own with the
same pages, drawn as on the FM-1: one page is the eight knobs.

![The DRIVE/FILTER page: eight cards, each with its name, its value and a bar](manual/img/move-drive-filter.png?v=0e8d6fd0)

- **The overview**: eight cards, knobs 1-4 on the top row and 5-8 below, each with its name, its value and a bar.
  The header names the page and its number (`4/12`); an `S` before the number shows that the sustain holds.
- **One large card**: touch a knob, or turn it, and its card fills the screen: the name, the value in large
  letters, a wide bar (a selector shows one block per option) and a row of eight marks for the knob it is. Turning
  changes the value as you watch. About a second after you let go the overview comes back. Touch another knob and
  its card shows at once.
- **Pages**: turn the jog wheel for the next or the previous page. Click the jog wheel for the list of pages, turn
  to one, click to open it; Back closes the list. Back on a page leaves Frodhes' screen (Shift + jog wheel too).
  As on the other screen, the VOICE pages are the ones of the instrument that plays.
- **The sustain pedal**: on this screen the Capture button is the sustain pedal on every page, as on the PEDAL
  screen (hold it, or tap to latch and tap again to let go).
- The first page shows the sound that plays in full, and its fifth knob (PRSET) picks the sound.
- Fine steps: hold Shift while turning BPM for steps of 0.1. A fast turn of a wide range moves four steps a detent.

![Knob 2 touched: AMT as a large card](manual/img/move-drive-filter-k2.png?v=3da25fcf)
![Knob 6 on TEMPO/SCALE: the scale, one block per option](manual/img/move-tempo-scale-k6.png?v=e2fb0eb3)
![The first page: the sound, its type, the spread, the sustain and the effects](manual/img/move-frodhes.png?v=2352bd88)
![The list of pages](manual/img/move-pages.png?v=a7bf9f81)

The pictures are drawn by the screen's own code on a computer (`tools/move_ui_shots.mjs`), in the screen's two
colours. Everything in this section is *(not yet verified on a device)*: the knobs' feel, the time the card stays,
the jog wheel and the button on the Move itself.

## The effects on the Move

Frodhes' effects (the compressor, the drive, the filter, the tremolo / wah, the phaser, the chorus, the delay, the
reverb and the LFO) are inside the module, on the pages above, with the FM-1's ranges and defaults. **FX** on the
first page is the FM-1's FX button: OFF turns every effect off at once and keeps their settings; it is ON when the
module starts, as on the FM-1 (also by MIDI, CC 111).

On the Move, Frodhes is the sound generator of a Schwung track. Its sound, effects included, then goes through the
audio effects you put after it on that track, and then the Master FX, like any other sound generator. To use one of
Schwung's reverbs or delays instead of Frodhes' own, turn Frodhes' one off on its page (REVERB, CHORUS/DELAY), or all
of them with FX. *(not yet verified on a device: the levels against the Move's own instruments)*

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

- `tools/gen_move_module.py` writes `move/module/` (module.json, the pages, help.json, `move_ui.h`, `sustain.js`
  and `screen.js`: the pages from the FM-1's tables + the screen's code, `move/ui/screen_core.js`);
  `--check` fails when the files are out of date with the FM-1's tables.
- `tools/ci_move.sh` runs the Move's checks on the host. `node move/ui/test_screen.mjs` checks Frodhes' screen
  (`move/ui/harness.mjs` runs it on a model of the Move's screen and the plugin); `node tools/move_ui_shots.mjs`
  draws every page into `build/move/ui-shots/` (`--docs`: the pictures on this page).
- `tools/target_closure.py` lists the sources of each target (FM-1, SMK-37, host, web, Move).
- `tools/move_hil.py` and `tools/device_null.py` are the device tests (CPU, crackle, the null test against the host).
  Over USB-C, as the FM-1 is tested: `usb-devices`, `usb-note`, `usb-vel`, `usb-sus`, `usb-clock`, `usb-stress`
  (MIDI to the Move's Standalone port, its USB audio recorded, the FM-1's analysis; `--fake` runs them against a
  model of the device). On the Move first: Settings, USB-C audio out = Main Out (the Move sets it back to Mic at
  every boot). `d2` runs them all for each engine and writes one table (`summary.md`). First it checks over
  Schwung's WebSocket (`tools/move_ws.py`) that slot 1 holds Frodhes, and it loads the engine's preset there.
  A failed preflight stops the run.
- `--path tap` (the `usb-*` tests and `d2`) skips USB-C entirely. Each take goes over Schwung's WebSocket to the
  plugin's own player (`dev_play` and `dev_play_add`), so no Move track hears it. The plugin writes its own output
  to `module_data/frodhes/tap/<take>.wav`, and the tool fetches the file over ssh. The take starts on the host
  build's block grid (every 2560 ms), so `usb-host` compares it sample for sample. The Move's mix, its volumes and
  its USB-C setting play no part.
- `d2 --path tap` also reads the plugin's callback times while `usb-stress` plays, which is the worst case. The
  plugin logs them while `dev_cpu_log` is 1, and the tool fetches `cpu-ring.log` over ssh. The `cpu` row checks
  them against the working limit. `--hq 1` or `--hq 0` sets the HQ Rhodes on or off for the run, through the
  plugin's `p_hq` parameter. HQ applies to new notes only: a sounding note keeps the path it started with.
- The criteria follow each engine's manual behaviour. VIBES plays with its motor off (CC 47 0), because the
  motor's pulse moves every level the tests read. Its velocity is checked as the FM-1 scan checks it: no drop from
  v24 on, and at least +10 dB from v40 to v117. MARIMBA's velocity must rise 26 to 32 dB from v8 to v127. A
  MARIMBA bar rings for less than a second, so `usb-sus` reads it within 0.2 s of key-up, with MUTE on (CC 103
  127). The pedal holds the bar, and letting the pedal up mutes it as fast as a key-up does. In `usb-clock`, an
  attack that the detector finds 5 ms or more late is counted, not taken as timing: a bar's ring can hide the
  next step's attack. A few such attacks are allowed; the jitter of the other attacks must stay under 4 ms.
- MIDI into the Standalone port also plays the Move's own instrument on the track. Schwung passes the notes to
  the Move's firmware and to the slot alike. For a clean comparison, give that Move track a silent instrument.
  Muting the track or turning it down also silences the slot, because the slot follows the track's mute and
  volume. *(not yet verified on a device)*
- The sustain control: `module.json` claims the Capture button (`capabilities.claims_ccs`, `SUS_BTN_CC` in
  `tools/gen_move_module.py`); Schwung gives a claimed button only to the module's screen script, so `sustain.js`
  passes its press and release to the plugin (`s_susbtn` 1 / 0), which runs the FM-1's REC code. The direct route,
  a knob or jog-wheel touch (`capabilities.touch_observe`), is off unless `dev_sus_touch` names the control
  (0-7 a knob, 9 the jog wheel).
