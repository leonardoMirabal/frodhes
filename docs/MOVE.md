# Frodhes on the Move (draft, in development)

Frodhes also builds as a sound generator for the Move, a standalone groove box, through Schwung, the community module
host that runs on it. The engines (PIANO, VIBES, MARIMBA) and the effects
are the FM-1's own code, built for the Move's processor. **This port is in development. Nothing on this page has been
checked on a Move yet:** every part below was checked on the host build only (the same code run on a computer,
`tools/ci_move.sh`). The parts marked *(not yet verified on a device)* are the ones a Move must still confirm.

## Install

You need a Move with Schwung installed. The module is a folder, `frodhes/`, with `dsp.so`, `module.json`,
`help.json`, `sustain.js` (the PEDAL screen), `screen.js` (Frodhes' own screen), `canvas.js` (the gain-reduction meter
on the COMPRESSOR page) and `LICENSE` in it, and it goes in Schwung's sound generator folder.

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
| **COMPRESSOR** | THRSH · RATIO · ATK · MKUP · COMP · REL · GR · PEAK |
| **DRIVE/FILTER** | DRIVE · AMT · TONE · MIX · FILT · FREQ · RESO · LFO |
| **TREM/PHASER** | TREM · DEPTH · RATE · SHAPE · PHASE · DEPTH · SPEED · FDBK |
| **CHORUS/DELAY** | CHOR · DEPTH · SPEED · SYNC · DELAY · MIX · TIME · FDBK |
| **REVERB** | RVB · MIX · DECAY · TONE · PDLY · SIZE · MOD · LCUT |
| **TEMPO** | TRSYN · PHSYN · DLSYN |
| **LFO/SETUP** | SHAPE · RATE · SYNC · TRIG · DLTON · LEVEL · CHAN |
| **MAPPINGS** | AT CC · AT LO · AT HI · PA CC · PA LO · PA HI |

The controls are the FM-1's, with the same ranges, under shorter names (the Move's screen fits about five
characters). An option name longer than five letters is shortened the same way everywhere, to four letters: the
word's first syllable and the consonant after it (DORIAN shows as DORN, DEGREE as DEGR, LYDIAN as LYDN, HARMON as
HARM, MELODIC as MELO, MPENTA as MPNT, DIATONIC as DIAT, DIA 7TH as DIA7, RANDOM as RAND). The manual (`docs/MANUAL.md`) describes what each one does. Each page has a help text in `help.json`.
The pages are generated from the FM-1's own parameter tables (`tools/gen_move_module.py`), so the two stay alike.
How the pages look and respond on the Move's screen and knobs is *(not yet verified on a device)*.

**COMPRESSOR** shows the gain reduction in the place of knobs 7 and 8 (GR and PEAK), as the FM-1's COMP page does:
one meter across both cells, a bar that fills from the right on the FM-1's scale (up to 30 dB), the peak held for a
second as a mark, and the reading in dB under it. It moves while you play. When the compressor is off the bar is
dotted and reads COMP OFF. Knobs 7 and 8 change nothing there. *(not yet verified on a device: how it looks on the
Move's screen)*

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

![The DRIVE/FILTER page: eight cards, each with its name, its value and a bar](manual/img/move/move-drive-filter.png?v=7fb7a2cc)

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
- **COMPRESSOR** shows the gain reduction as the FM-1's COMP page does: GR and the reading in dB, a bar that fills
  from the right on the FM-1's scale (up to 30 dB), and the peak held for a second. COMP OFF shows when the compressor
  is off. *(not yet verified on a device)*
- A fast turn of a wide range moves four steps a detent.

![Knob 2 touched: AMT as a large card](manual/img/move/move-drive-filter-k2.png?v=3da25fcf)
![Knob 1 on REVERB: the type, one block per option](manual/img/move/move-reverb-k1.png?v=c1dc8a5f)
![The first page: the sound, its type, the spread, the sustain and the effects](manual/img/move/move-frodhes.png?v=d424b3d8)
![The list of pages](manual/img/move/move-pages.png?v=e463578b)

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

## MIDI

The MIDI of Schwung's track reaches Frodhes as on the FM-1 (`docs/MANUAL.md`, section 12): notes with velocity and
release velocity, pitch bend, CC 64, the parameter CCs and program change (0-15 F01-F16, 16-31 U01-U16,
32-34 F17-F19). *(not yet verified on a device)*

The sequencer's, the arpeggiator's and the chords' CCs (43-46, 52-63) do nothing on the Move.

**The tempo is the Move's.** Frodhes follows the Move's transport: the Move's clock reaches it as MIDI clock and
Start / Stop, or, when no clock comes, from the Move's transport and beat position. The synced rates (TRSYN,
PHSYN, DLSYN, the chorus SYNC) and the LFO's SYNC lock to it. While the Move is stopped, they use the Move's
tempo setting (120 BPM if the Move reports none). Frodhes has no BPM or CLOCK setting on the Move, and its
tempo is kept within the FM-1's range of 40 to 240 BPM. *(not yet verified on a device)*

## What differs from the FM-1

- **16 voices** on every engine (the FM-1 plays 10).
- No PERFORM, ASSIGN or DISPLAY pages: the Move has its own pads, knobs and screen. The FM-1's KEY VEL, TOUCH,
  USB VOL, TRS, COLOR, BRIGHT and LEDS settings are not on the Move.
- No sequencer, arpeggiator, chords or scales (MODE, SCALE, ROOT, TRANSPOSE): the Move has its own. Their pages
  are not on the Move, and their CCs do nothing. A setup saved with any of them on still loads, with them off.
  A pattern saved by an older build is not loaded.
- No BPM or CLOCK setting: the Move's tempo leads (see MIDI).
- SPREAD and the compressor's RELEASE, set on the FM-1 by MIDI or a page of their own, are on the pages here.
- The sound is part of the Move's set: Schwung saves and restores it with the track. *(not yet verified on a device)*
- Audio runs in blocks of 128 frames at 44.1 kHz (the FM-1 uses 256), with a working limit of 725 us of processing
  per block. *(not yet verified on a device)*

## Coming

- **HQ**: a higher-quality mode that uses the Move's faster processor (more detail in the engines and the effects).
  Not in this build.
- A screen of Frodhes's own.

## For developers

- `tools/gen_move_module.py` writes `move/module/` (module.json, the pages, help.json, `move_ui.h`, `sustain.js`,
  `screen.js`: the pages from the FM-1's tables + the screen's code, `move/ui/screen_core.js`, and `canvas.js`: the
  COMPRESSOR page's meter, `move/ui/gr_cell.js`);
  `--check` fails when the files are out of date with the FM-1's tables.
- `tools/ci_move.sh` runs the Move's checks on the host. `node move/ui/test_screen.mjs` checks Frodhes' screen
  (`move/ui/harness.mjs` runs it on a model of the Move's screen and the plugin); `node tools/move_ui_shots.mjs`
  draws every page into `build/move/ui-shots/` (`--docs`: the pictures on this page).
- The meter on the COMPRESSOR page is a Schwung custom widget (Schwung's docs/MODULES.md, "Custom widgets"). Two
  read-only keys, `r_cmp_gr` (GR) and `r_cmp_pk` (PEAK), form one picture group with the kind `custom:frodhes_gr`.
  Schwung draws it with `drawCell` from `canvas.js`, which `module.json` names (`capabilities.canvas_script`).
  `r_cmp_gr` is `"live"`, so the grid reads `r_cmp_gr:effective` every tick. It reads `OFF`, or the reduction and the
  held peak in tenths of a dB (`120 200`). The plugin keeps the FM-1's meter timing (ui.c `meter_poll`) in
  `render_block`, which the host keeps calling through silence (`requires_continuous_processing`). A host without
  the widget shows the two keys as plain read-only cells. `node move/ui/test_cell.mjs` checks the drawing. With
  Schwung's source next to the repo (or `SCHWUNG_SRC`), it also runs the page through Schwung's own planner and
  renderer (`--png FILE` writes the page).
- `tools/target_closure.py` lists the sources of each target (FM-1, SMK-37, host, web, Move).
- `tools/release_devices.py X.Y.Z` runs a release's device tests on the FM-1 and the Move at the same time,
  unattended. `--dry-run` prints the plan and its minutes. The plan follows the changes since the last tag
  (`tools/test_map.py`, per target). The FM-1 gets an install and T1, plus T2 when an engine or the DSP changed.
  The Move gets an install and the release minimum. That minimum is `d2` over the tap, HQ on and off, with only
  `usb-host`, `usb-stress` (the crackle and the CPU) and `usb-restrike` (`d2 --tests`). When the Move's own code
  changed, the Move gets the whole `d2` instead. A target whose package sha256 already passed those tiers runs
  nothing; its record is reused. The tool takes the FM-1's and the Move's locks (DEVICE, MOVE) before it starts.
  It writes one record with a section per target. `--fake` runs the plan with stand-ins, for tests.
- `tools/move_hil.py` and `tools/device_null.py` are the device tests (CPU, crackle, the null test against the host).
  Over USB-C, as the FM-1 is tested: `usb-devices`, `usb-note`, `usb-vel`, `usb-sus`, `usb-clock`, `usb-stress`
  (MIDI to the Move's Standalone port, its USB audio recorded, the FM-1's analysis; `--fake` runs them against a
  model of the device). On the Move first: Settings, USB-C audio out = Main Out (the Move sets it back to Mic at
  every boot). `d2` runs them all for each engine (`usb-clock` once, with the Rhodes) and writes one table
  (`summary.md`). First it checks over
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
  With `--hq 1`, `usb-host` compares against the host build with HQ on. The phrase has a note that only HQ
  changes (96 at v90). If that note plays as Classic while HQ is asked for, the test fails as CLASSIC.
- `d2 --path tap --hq 1` also runs `usb-restrike` on the Rhodes. The pedal is down and a chord of 8 notes is
  held on keys 28 to 59. Every 30 to 50 ms, one of 8 keys from 60 to 108 is struck again while it still rings,
  at velocities 127, 64 and 30 in turn. The key goes up 20 ms before each new strike. One time in six, 3 or 4
  keys are struck at once. That is more than the HQ live contact's 2 per block, so the rest use the table. The
  take lasts half of `--secs` (30 s by default). It passes when the worst callback is 600 us or less, nothing
  glitches, and the sound is not louder after the pedal comes up. The test also writes the first 8 s with HQ
  on and with HQ off (`restrike-listen-hq1.wav`, `restrike-listen-hq0.wav`) for listening.
- The plugin keeps its saved setup, but the host build boots clean. So before its take, `usb-host` turns off
  the pedal, FX bypass and RAW by CC. Over the tap it also writes each setting that differs from a fresh
  instance (`move/tests/boot_keys.txt`, checked by `test_bridge boot`). The Move has no arpeggiator, sequencer
  or chords, and the bridge drops their CCs, so the tests do not send them. A note that the host strikes once
  but the Move strikes again while it is held fails as STRUCK AGAIN. That happened in one run, before the
  arpeggiator was removed, when an arpeggiator left on re-struck every note.
- The tests leave the plugin as they found it. The plugin saves its settings and its panel, so a test's CCs
  came back at the next load: once the arpeggiator (before it was removed), once every effect with the
  feedbacks at 127 (`usb-stress`). So `d2`, every `usb-*` run and `usb-levels` first read all of the slot's params
  over Schwung's WebSocket. At the end they write back each one that differs, the preset first. This also
  happens after a failure, ctrl-C or SIGTERM. HQ goes back to Classic, its value at boot, and the dev player and the CPU log are stopped. Then the
  tool reads the params again: `d2`'s summary has a `state` row, which fails when any param differs from before.
- A take plays once: the plugin refuses a `begin` with the id of the take that went last. Each `dev_play_add`
  piece carries its number (`#K`), and the plugin refuses a piece that comes out of turn. If the WebSocket
  drops while a take is being sent, the tool stops the take. It sends the take again, under a new id, only
  when the plugin never started it. A take that started is never sent again; the test fails instead.
- The criteria follow each engine's manual behaviour. VIBES plays with its motor off (CC 47 0), because the
  motor's pulse moves every level the tests read. Its velocity is checked as the FM-1 scan checks it: no drop from
  v24 on, and at least +10 dB from v40 to v117. MARIMBA's velocity must rise 26 to 32 dB from v8 to v127. A
  MARIMBA bar rings for less than a second, so `usb-sus` reads it within 0.2 s of key-up, with MUTE on (CC 103
  127). The pedal holds the bar, and letting the pedal up mutes it as fast as a key-up does.
- `clock-probe` shows where the clock comes from when the Move's own transport plays. The tool asks for Play
  on the Move, waits until the transport runs, records 10 s, then asks for Stop. Meanwhile the plugin logs its
  counters to `stats.log` in its folder, 10 times a second, while `dev_stats_log` is 1. The counters are the
  F8, FA and FC that came in, the pulses the bridge made itself, the host's transport and tempo, and the app's
  tempo. It passes when the Move's own F8 arrive at the host's tempo and the bridge makes no pulses after the
  first 3 s. It fails when only the bridge's fallback runs, or when the transport never ran.
- `usb-clock` sends MIDI clock (Start, then F8) at 100 BPM, or `--bpm`. The tremolo is on (AM, full depth,
  SYNC 1/8) and one Rhodes note is held. Its level then dips every half beat. The test finds the dips from 3 s on
  and fits a straight line through them. The period must be within 1% of the clock's, and the jitter must stay
  under 4 ms. The synced LFO catches up with a new clock gradually, which takes about 2 s from 120 to 100 BPM,
  so the first 3 s are not judged. The default is 100 BPM, not the internal 120, so a tremolo that ignores the
  clock fails. The tremolo is the effects' code, the same for every engine, so `d2` runs the test once.
- `usb-levels` compares how loud Frodhes's F01 is with the Move's own instrument on the selected track. Both
  play through the Move's mix, at the track's volume, and the tool records them over USB-C. The pattern is C2 to
  C6, one note at a time, then a held chord, at v100, v64 and v127. First the stock instrument plays it over
  MIDI. Frodhes's CHANNEL is moved off the notes for that take, and its tap must stay silent the whole time.
  Then Frodhes plays the same pattern on its own player, which no Move track hears. The report gives the gain
  that would match the two, by LUFS and by critical-band loudness. It changes nothing. A USB-C stream that is
  the microphone stops it.
- MIDI into the Standalone port also plays the Move's own instrument on the track. Schwung passes the notes to
  the Move's firmware and to the slot alike. For a clean comparison, give that Move track a silent instrument.
  Muting the track or turning it down also silences the slot, because the slot follows the track's mute and
  volume. *(not yet verified on a device)*
- The sustain control: `module.json` claims the Capture button (`capabilities.claims_ccs`, `SUS_BTN_CC` in
  `tools/gen_move_module.py`); Schwung gives a claimed button only to the module's screen script, so `sustain.js`
  passes its press and release to the plugin (`s_susbtn` 1 / 0), which runs the FM-1's REC code. The direct route,
  a knob or jog-wheel touch (`capabilities.touch_observe`), is off unless `dev_sus_touch` names the control
  (0-7 a knob, 9 the jog wheel).
