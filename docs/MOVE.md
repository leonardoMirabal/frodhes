# Frodhes on the Ableton Move

Frodhes also runs on the Ableton Move, a standalone groove box, as a sound generator of Schwung, the community module host
that runs on it. The instruments (the electric piano types, VIBES, MARIMBA) and the effects are the FM-1's own code,
built for the Move's processor, with 16 voices on every instrument and the HQ version of the three engines.

**This port is in development and not released yet.** Every part of this page is checked on the host build (the
same code run on a computer, `tools/ci_move.sh`). The parts marked *(not yet verified on a device)* still wait for
their check on a Move: the screen, the knobs and buttons, the levels against the Move's own instruments.

The user part comes first (install, the pages, what differs from the FM-1); [For developers](#for-developers) is at
the end.

## Install

You need a Move with Schwung installed (built and tested against Schwung 1.8.1).

The module is one file, **`frodhes-module.tar.gz`**: a folder `frodhes/` with `dsp.so`, `module.json`, `help.json`,
`sustain.js` (the PEDAL screen), `screen.js` (Frodhes' own screen), `canvas.js` (the gain-reduction meter on the
COMPRESSOR page) and `LICENSE`. A release carries it, under that name and as `frodhes-X.Y.Z-move.tar.gz`, only when
that exact file passed the tests on a Move; until a release has it, build it yourself (`make move`, see
[For developers](#for-developers)). Frodhes is not in Schwung's module catalog: how it will be offered there is not
decided yet, so install it by hand.

1. Unload Frodhes from every track (a module that plays must not be replaced under it).
2. Copy the folder into Schwung's sound generator folder, over the network (the Move answers as `move.local`):

   ```sh
   ssh ableton@move.local 'tar -xzf - -C /data/UserData/schwung/modules/sound_generators' < frodhes-module.tar.gz
   ```

3. In Schwung, load **Frodhes** as the sound generator of a track. Its abbreviation on the screen is `FRDH`.

An update is the same steps with the new file. Frodhes keeps its settings and user presets in Schwung's data folder
(`/data/UserData/schwung/module_data/frodhes`), not in the module's folder, so an update keeps them. To remove
Frodhes, unload it and delete `/data/UserData/schwung/modules/sound_generators/frodhes`. *(not yet verified on a
device: the plain `tar` route; the installer below is the tested one)*

With this repository at hand, `tools/move_install.py` does the same with a check and a way back:

```sh
python3 tools/move_install.py install            # prints what it would do (dry run)
python3 tools/move_install.py install --go       # copies the module over ssh and switches to it
python3 tools/move_install.py info --go          # the installed version and the sha256 of dsp.so
python3 tools/move_install.py rollback --go      # back to the module installed before
```

It installs `build/move/frodhes/` (`--src DIR` for another folder, such as the unpacked download). It copies the new
module next to the old one, checks it, then switches by renaming, and keeps the two modules installed before it
(`--keep`). `--host` names the Move (default `ableton@move.local`).

## The pages

The first page, **FRODHES**, has the sound type, the stereo spread, the sustain latch, the PEDAL screen, the effects
switch (FX), Frodhes' own screen (PAGES) and the preset browser: the 19 factory sounds (F01-F19) and 16 user sounds
(U01-U16), as on the FM-1. Each page below holds up to eight
controls, one for each of the Move's eight knobs. The VOICE pages follow the engine of the TYPE: only the pages of the
engine that plays are shown.

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
characters; an option name is shortened the same way everywhere, so the reverb's INFINITE shows as INF). The manual
(`docs/MANUAL.md`) describes what each one does; CHAN is the FM-1's MIDI CHANNEL. Each page has a help text in
`help.json`. The pages are generated from the FM-1's own parameter tables (`tools/gen_move_module.py`), so the two
stay alike. How the pages look and respond on the Move's screen and knobs is *(not yet verified on a device)*.

![Schwung's COMPRESSOR page: six knobs, and the gain-reduction meter in the place of knobs 7 and 8](manual/img/move/move-knobs-compressor.png?v=18ceee97)

**COMPRESSOR** shows the gain reduction in the place of knobs 7 and 8 (GR and PEAK), as the FM-1's COMP page does:
one meter across both cells, a bar that fills from the right on the FM-1's scale (up to 30 dB), the peak held for a
second as a mark, and the reading in dB under it. It moves while you play. When the compressor is off the bar is
dotted and reads COMP OFF. Knobs 7 and 8 change nothing there. *(not yet verified on a device: how it looks on the
Move's screen)*

## Frodhes' own screen (PAGES)

The pages above are on Schwung's own knob screen. **PAGES** on the first page opens a screen of Frodhes' own with the
same pages, drawn as on the FM-1: one page is the eight knobs.

![The DRIVE/FILTER page: eight cards, each with its name, its value and a bar](manual/img/move/move-drive-filter.png?v=7fb7a2cc)

- **The overview**: eight cards, knobs 1-4 on the top row and 5-8 below, each with its name, its value and a bar.
  The header names the page and its number (`4/10`); an `S` before the number shows that the sustain holds.
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
![COMPRESSOR with the compressor on: the meter at 12 dB of gain reduction](manual/img/move/move-compressor-gr.png?v=1e8174f8)
![The first page: the sound, its type, the spread, the sustain and the effects](manual/img/move/move-frodhes.png?v=d424b3d8)
![The list of pages](manual/img/move/move-pages.png?v=e463578b)

The pictures are drawn by the screens' own code on a computer, in the screen's two colours: Frodhes' screen by
`tools/move_ui_shots.mjs`, Schwung's COMPRESSOR page by Schwung's own renderer (`move/ui/test_cell.mjs --png`).
Everything in this section is *(not yet verified on a device)*: the knobs' feel, the time the card stays, the jog
wheel and the button on the Move itself.

## Sustain

The Move has no pedal jack. There are three ways to sustain, and they work together as on the FM-1:

- **A Move button as the pedal**, as REC on the FM-1: open **PEDAL** on the first page (or Frodhes' own screen),
  then the **Capture** button is the sustain pedal. Hold it like a pedal; tap it to latch the sustain on, tap it
  again to let go. The screen shows SUS while the notes are held. While one of these screens is open, Capture does
  not do its usual job on the Move. *(not yet verified on a device)*
- **SUS** on the first page is the latch on its own: ON holds the notes as a held pedal does, OFF lets them go. It
  shows ON while the button holds the notes too.
- **A sustain pedal or keyboard on CC 64** (on the Move's USB-A port, or MIDI from a computer): half pedalling as
  on the FM-1. Letting the pedal up also releases the latch.

## The effects

Frodhes' effects (the compressor, the drive, the filter, the tremolo / wah, the phaser, the chorus, the delay, the
reverb and the LFO) are inside the module, on the pages above, with the FM-1's ranges and defaults. **FX** on the
first page is the FM-1's FX button: OFF turns every effect off at once and keeps their settings; it is ON when the
module starts, as on the FM-1 (also by MIDI, CC 111).

On the Move, Frodhes is the sound generator of a Schwung track. Its sound, effects included, then goes through the
audio effects you put after it on that track, and then the Master FX, like any other sound generator. To use one of
Schwung's reverbs or delays instead of Frodhes' own, turn Frodhes' one off on its page (REVERB, CHORUS/DELAY), or all
of them with FX. *(not yet verified on a device: the levels against the Move's own instruments)*

## Presets

The preset browser on the first page (and PRSET on Frodhes' own screen) loads the 19 factory sounds (F01-F19) and
the 16 user slots (U01-U16), and program change loads them as on the FM-1 (0-15 F01-F16, 16-31 U01-U16, 32-34
F17-F19). The Move has no SAVE page: the sound you set is saved with the Move's set instead (Schwung keeps it with
the track). The user slots keep what the module's data folder holds (empty on a new install). *(not yet verified on
a device)*

## Mappings

The FM-1 has no pressure. On the Move, **MAPPINGS** turns the pads' pressure into one of the FM-1's controls:

- **AT CC / AT LO / AT HI**: channel pressure (aftertouch) drives the control on that MIDI CC number, from LO (no
  pressure) to HI (full pressure).
- **PA CC / PA LO / PA HI**: the same for poly pressure, from the most pressed held pad. Letting a pad go ends its
  pressure.

An HI under LO turns the direction round. A CC of 0 is off, and both are off by default: then pressure changes
nothing. The control moves as if a controller sent that CC (the CC numbers are the FM-1's, in `docs/MANUAL.md`). The
settings are kept with the track's state. *(not yet verified on a device)*

## MIDI and tempo

The MIDI of Schwung's track reaches Frodhes as on the FM-1 (`docs/MANUAL.md`, section 12): notes with velocity and
release velocity, pitch bend, CC 64, the parameter CCs and program change. CHAN on LFO/SETUP picks the channel it
listens to (ALL by default). *(not yet verified on a device)*

The sequencer's, the arpeggiator's and the chords' CCs (43-46, 52-63) do nothing on the Move.

**The tempo is the Move's.** Frodhes follows the Move's transport: the Move's clock reaches it as MIDI clock and
Start / Stop, or, when no clock comes, from the Move's transport and beat position. The synced rates (TRSYN,
PHSYN, DLSYN, the chorus SYNC, the VIBES motor's SYNC) and the LFO's SYNC lock to it. While the Move is stopped,
they use the Move's tempo setting (120 BPM if the Move reports none). Frodhes has no BPM or CLOCK setting on the
Move, and its tempo is kept within the FM-1's range of 40 to 240 BPM. *(not yet verified on a device)*

## What differs from the FM-1

- **16 voices** on every engine (the FM-1 plays 10).
- **HQ, the Move's one version.** The FM-1 and the SMK-37 play the Classic engine (integer only); the Move plays HQ,
  with no setting: a preset or a saved set loads with it too. The electric piano types (ST77, SUIT, FELT, DYNO) have
  the exact hammer strike at every velocity, a cleaner top octave, and a truer re-strike of a key that still rings.
  VIBES and MARIMBA have more of the bar's and the tube's modes, and the mallet's contact computed live when a bar
  that still rings is struck again (MARIMBA's top keys, C6 up, always). *(not yet verified on a device)*
- No PERFORM, ASSIGN or DISPLAY pages: the Move has its own pads, knobs and screen. The FM-1's KEY VEL, TOUCH,
  USB VOL, TRS, COLOR, BRIGHT and LEDS settings are not on the Move.
- No sequencer, arpeggiator, chords or scales (MODE, SCALE, ROOT, TRANSPOSE): the Move has its own. Their pages
  are not on the Move, and their CCs do nothing. A setup saved with any of them on still loads, with them off.
  A pattern saved by an older build is not loaded.
- No BPM or CLOCK setting: the Move's tempo leads (see [MIDI and tempo](#midi-and-tempo)).
- Only on the Move: the pressure [mappings](#mappings), the PEDAL screen and Frodhes' own screen.
- SPREAD and the compressor's RELEASE, set on the FM-1 by MIDI or a page of their own, are on the pages here.
- The sound is part of the Move's set: Schwung saves and restores it with the track. *(not yet verified on a device)*
- Audio runs in blocks of 128 frames at 44.1 kHz (the FM-1 uses 256), with a working limit of 725 us of processing
  per block. *(not yet verified on a device)*

## For developers

- **The build**: `make move` builds `build/move/frodhes/` (dsp.so, module.json with the version, the screen scripts,
  LICENSE) and `build/move/frodhes-module.tar.gz` in the arm64 Debian bookworm image (`make move-image`, Docker on
  macOS; natively with `MOVE_RUN=` on Linux arm64). `MOVE_VER=X.Y.Z` sets the version. The tarball is reproducible
  (sorted names, no times or owners, `gzip -n`): the release compares its sha256 with the one tested on the Move.
  The build defines `FR_VOICES=16u` and `FR_HQ=1`.
- **The release**: `tools/release.py` builds the package in step 3. `release.yml`'s job `move` rebuilds it from the
  tag, and `tools/move_asset.sh` attaches it (`frodhes-X.Y.Z-move.tar.gz` and `frodhes-module.tar.gz`) only when the
  release commit records that exact sha256 as tested on the Move (`Move package sha256 <sha>`). Without the record
  the release ships no Move package; a different sha256 stops the release.
- **The gates**: `tools/ci_move.sh` is the Move's MT0 (Gate U: the generated module is current, `make test-move`,
  Gate M: the plugin replays the host renders, and the Move pytest suites). `make gate-p` (ASan + UBSan, the
  seccomp and no-allocation guards), `make gate-h` (the HQ renders) and `tools/gate_s.py` (the FM-1 and SMK binaries
  unchanged by the Move work) complete it. `tools/test_map.py` names the gates and device tiers a change needs, per
  target.
- `tools/gen_move_module.py` writes `move/module/` (module.json, the pages, help.json, `move_ui.h`, `sustain.js`,
  `screen.js`: the pages from the FM-1's tables + the screen's code, `move/ui/screen_core.js`, and `canvas.js`: the
  COMPRESSOR page's meter, `move/ui/gr_cell.js`);
  `--check` fails when the files are out of date with the FM-1's tables. Never edit `move/module/` by hand.
- `tools/ci_move.sh` runs the Move's checks on the host. `node move/ui/test_screen.mjs` checks Frodhes' screen
  (`move/ui/harness.mjs` runs it on a model of the Move's screen and the plugin); `node tools/move_ui_shots.mjs`
  draws every page into `build/move/ui-shots/` (`--docs`: the pictures on this page, and every `?v=` here).
  `SCHWUNG_SRC=<Schwung's source> node move/ui/test_cell.mjs --png docs/manual/img/move/move-knobs-compressor.png`
  draws Schwung's COMPRESSOR page; run `--docs` after it for its `?v=`.
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
  them against the working limit. The plugin loads with the HQ Rhodes on. `--hq 0` plays Classic for the run, for
  a test only, through the plugin's `p_hq` key (no page has it; the next load is HQ again), and `--hq 1` sets HQ.
  HQ applies to new notes only: a sounding note keeps the path it started with. Unless `--hq 0` is given,
  `usb-host` compares against the host build with HQ on. The phrase has a note that only HQ changes (96 at v90).
  If that note plays as Classic while HQ is due, the test fails as CLASSIC. Gate M and the bridge's `eq` tests
  set `p_hq` 0 first, so they compare the Classic path with the host renders; `make test-move` also compares the
  plugin as it loads with the host build with HQ on.
- The HQ bars (VIBES and MARIMBA, `firmware/src/hq/bars_hq.c`, `FR_HQ` only) follow the same rule: HQ on is the
  sound every Move user hears, HQ off is the FM-1's integers. HQ runs the bar's upper modes in float, adds three
  more bar modes and two more tube modes (their levels from `docs/model/data/bars_hq.json`, under the top mode's
  ceiling), and computes the mallet's contact live when a key is struck while its bar rings (at most 2 a block; the
  rest use the tables). MARIMBA's top keys (C6 up) always strike through the live contact. With the extra modes and
  the contact off, the HQ bars are Classic within 2 LSB. `tests/py/test_bars_hq.py` checks that, the levels against
  Classic and the tables, never louder after key-up and the velocity range; Gate H holds their renders.
  `tools/gen_bars_hq.py` and `tools/gen_bars_hq_contact.py` write the two headers in `firmware/gen/hq/`
  (`--check` compares).
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
  happens after a failure, ctrl-C or SIGTERM. HQ goes back on, its value at a load, and the dev player and the CPU log are stopped. Then the
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
- `tools/move_monkey.py` runs the plugin monkey on the Move for `--minutes` (30 by default). Each seed is one take of
  about 34 s: `tests/py/test_move_monkey.py`'s stream, as the host's plugin monkey plays it. Its MIDI goes to the
  plugin's own player over the tap. Its parameter writes and preset loads go over Schwung's WebSocket at their time
  in the take, at most 8 in any 250 ms, so the manager's set ring (32 entries) never fills. The state reads and
  loads have no WebSocket path, so only the host runs them. The checks are the host monkey's: no crash, no stuck
  note, exact silence after the panic, the probe never louder after its key-up. Also checked: the player's
  counters, Schwung's errors, and the plugin's `starts` in `boot.txt` (a reload fails the run). The click detector
  runs on every take: a repeated or stale block fails; zero, clip and click are counted, as on the FM-1's monkey.
  `--dry-run` prints the plan. `tests/py/test_move_monkey_device.py` checks the tool offline.
- `move/bench/classic_bench.c` times the plugin's callbacks on the Move, off the audio core. Each case is a
  `dsp.so` with suffixes: `:top` plays the chord on keys 85 to 108, `:classic` sets `p_hq` 0 after the load, and
  `:dV` sends CC 84 V first (the drive's TYPE). The callbacks run back to back on a core the Move shares, so the
  bench's tail is higher than the plugin's own ring in `d2`. Use it to compare cases within one run.
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
