# Frodhes on the Ableton Move: for developers and testers

This page is the build, the tests and the device evidence of the Move port. The user manual is
[MOVE.md](MOVE.md); the FM-1's is [MANUAL.md](MANUAL.md). The repository's rules are in `AGENTS.md` (section 4b:
the two targets).

## Contents

1. [The screenshots](#the-screenshots)
2. [Install over ssh](#install-over-ssh)
3. [Build, release and gates](#build-release-and-gates)
4. [Device evidence for the manual](#device-evidence-for-the-manual)

## The screenshots

Every picture of the Move's screen in [MOVE.md](MOVE.md), the README and the manual's appendix 16 is drawn on a
computer by the file the Move runs, `move/module/ui_chain.js` (Schwung's `chain_ui`, the whole Move UI for Frodhes),
with `tools/move_ui_shots.mjs`. It runs the script on a 128 x 64 one-bit framebuffer in the screen's own two colours
(white on black), on the harness's model of the plugin (`move/ui/harness.mjs`), and scales each screen up 4 times
with nearest neighbour (512 x 256, square pixels). Never edit the PNGs by hand.

```sh
node tools/move_ui_shots.mjs              # every page and card into build/move/ui-shots/ (and sheet.png)
node tools/move_ui_shots.mjs --docs       # docs/manual/img/move/move-*.png and every link's ?v=
node tools/move_ui_shots.mjs --check      # exit 1 on a stale, missing or orphan picture, or a wrong ?v=
```

- The pictures the docs show are the tool's `DOC_SHOTS` list: one overview per page (the VOICE pages with their
  engine: ST77 on F01, VIBE on F13, MRMB on F15), three large cards (a number, an option, the preset), REVERB at
  FREEZE, the header with FX off and SUS on, the reset toast, the page list, the menu and the save card. `--docs`
  writes them as `move-<name>.png`, removes any other `move-*.png` there, and sets the `?v=` (the first 8 hex digits
  of the file's sha256, `tools/doc_links.py`'s rule) on every `docs/manual/img/move/` link in `docs/MOVE.md`, this
  page, `README.md` and `docs/MANUAL.md`. It touches no other link.
- `tests/py/test_move_ui.py` runs `--check` (`test_move_doc_shots_current`), so a change to the screen's code or
  the pages without `--docs` fails `tools/ci_move.sh`. `tools/doc_links.py` checks the README's and the manual's
  `?v=` too.
- The values are the defaults (the FM-1's): `chain_params.json`'s, which `tools/gen_move_module.py` takes from
  `params.c` for a parameter and, for a setting, from the FM-1's first boot (`ui.c` `slot_val` on `params_defaults`'
  record, `arp.c` `arp_defaults`; `test_bridge boot` checks them against a new instance). There is no picture after SAVE TO USER's click: the
  plugin names the slot (`preset_autoname`), which the model does not compute.
- Schwung's own knob grid is not shown to users any more (the plugin answers no `ui_hierarchy`, `MB_OWN_UI` 1), so
  the docs have no picture of it. `SCHWUNG_SRC=<Schwung's source> node move/ui/test_cell.mjs --png FILE` still draws
  its COMPRESSOR page for review (write it outside `docs/manual/img/move/`, or `--check` reports an orphan).
- To compare with the real screen: `tools/move_ui_drive.py` reads Schwung's display mirror (`shot:NAME` after
  `arm`; a shot before `arm` is the mirror's last frame and is marked STALE). Leave `display_mirror` and
  `inject_as_hardware` at 0 afterwards.

## Install over ssh

Users install through Schwung Manager ([MOVE.md](MOVE.md#install)). For a build of your own, or without the manager,
the module can be copied over ssh (the Move answers as `move.local`). Unload Frodhes from every track first, if an
older one is loaded (a module that plays must not be replaced under it), then:

```sh
ssh ableton@move.local 'tar -xzf - -C /data/UserData/schwung/modules/sound_generators' < frodhes-module.tar.gz
```

*(not yet verified on a device: this plain `tar` route; `tools/move_install.py` below is the tested one)*

To remove it this way, unload it from every track and delete `/data/UserData/schwung/modules/sound_generators/frodhes`.

`tools/move_install.py` does the same with a check and a way back:

```sh
python3 tools/move_install.py install            # prints what it would do (dry run)
python3 tools/move_install.py install --go       # copies the module over ssh and switches to it
python3 tools/move_install.py info --go          # the installed version, the sha256 of dsp.so and engine.so
python3 tools/move_install.py rollback --go      # back to the module installed before
```

It installs `build/move/frodhes/` (`--src DIR` for another folder, such as the unpacked download), checks the new
module next to the old one before it switches, and keeps the two modules installed before it (`--keep`). `--host`
names the Move (default `ableton@move.local`).

## Build, release and gates

- **The build**: `make move` builds `build/move/frodhes/` (dsp.so, engine.so, module.json with the version, the screen scripts,
  LICENSE) and `build/move/frodhes-module.tar.gz` in the arm64 Debian bookworm image (`make move-image`, Docker on
  macOS; natively with `MOVE_RUN=` on Linux arm64). `MOVE_VER=X.Y.Z` sets the version. The tarball is reproducible
  (sorted names, no times or owners, `gzip -n`): the release compares its sha256 with the one tested on the Move.
  The build defines `FR_VOICES=16u` and `FR_HQ=1`.
- **The release**: `tools/release.py` builds the package in step 3. `release.yml`'s job `move` rebuilds it from the
  tag, and `tools/move_asset.sh` attaches it (`frodhes-X.Y.Z-move.tar.gz` and `frodhes-module.tar.gz`) only when the
  release commit records that exact sha256 as tested on the Move (`Move package sha256 <sha>`). Without the record
  the release ships no Move package; a different sha256 stops the release. Before that, `tools/move_catalog.py
  check` stops the release unless the tag's tarball unpacks to `frodhes/` alone with module.json's version the tag's.
- **Schwung Manager and the catalog**: the manager reads `release.json` from main
  (`{"version", "download_url"}`) for the latest version and its tarball, for a catalog install and for Install
  Custom Module > From GitHub URL. `release.yml`'s job `release-json` writes it after a release that carries the
  Move package (`tools/move_catalog.py release-json X.Y.Z`: the tag's `frodhes-module.tar.gz`, the repository from
  the origin remote; an older tag never moves it back), checks that the URL downloads, and pushes it to main. main's
  protection refuses that push from the workflow's own token: the job needs the secret `RELEASE_JSON_DEPLOY_KEY` (a
  deploy key with write access that may bypass main's rules), or the file is committed by hand from the job's log.
  No `release.json` is committed before the first release with the Move package: without it the manager's catalog
  install falls back to `releases/latest/download/frodhes-module.tar.gz` and the GitHub URL install refuses the
  repository, and a placeholder would point at a file that does not exist. The catalog entry
  (`module-catalog.json` in Schwung's repository, `asset_name` `frodhes-module.tar.gz`) waits for that release.
- **The gates**: `tools/ci_move.sh` is the Move's MT0 (Gate U: the generated module is current, `make test-move`,
  Gate M: the plugin replays the host renders, and the Move pytest suites). `make gate-p` (ASan + UBSan, the
  seccomp and no-allocation guards), `make gate-h` (the HQ renders) and `tools/gate_s.py` (the FM-1 and SMK binaries
  unchanged by the Move work) complete it. `tools/test_map.py` names the gates and device tiers a change needs, per
  target.
- `tools/gen_move_module.py` writes `move/module/` (module.json, the pages, help.json, `move_ui.h`,
  `screen.js`: the pages from the FM-1's tables + the screen's code, `move/ui/screen_core.js`, and `canvas.js`: the
  COMPRESSOR page's meter, `move/ui/gr_cell.js`);
  `--check` fails when the files are out of date with the FM-1's tables. Never edit `move/module/` by hand.
- `tools/gen_move_presets.py` writes `move/move_presets.h`, the Move-only factory presets F20-F57 (the factory
  preset review's B and C variants of F01-F19, the user's pick of 2026-11-01), from `move/presets/proposed_records.txt`
  (every variant's full id = value list as `tools/preset_review.py` read it back) and `move/presets/variants.json`
  (what each changes: the generator stops when a record differs from its A in an id the variant does not name, LEVEL
  aside). The names are the generator's `NAMES` table (generic, at most 12 characters). `--check` (in
  `tools/ci_move.sh`'s Gate U) fails on a stale header. The header is a table of its own, `MB_PX_DATA`, included by
  `move/move_bridge.h` only: `presets.c` and its `PR_FDATA` are untouched (Gate S), so a change of `PR_FDATA`'s
  format never touches it. The bridge numbers them after the FM-1's indices (0-18 F01-F19, 19-34 U01-U16 as on the
  FM-1, so store.bin and saved states keep their meaning; 35-72 F20-F57; `preset_count` 73, `u_first` 19), loads
  them itself (`mb_pr_load`, presets.c `preset_load`'s steps) by the `preset` key and by program change 35-72, and
  Frodhes' screen browses F01-F57, then U01-U16. To redo them after a new review: copy its `variants.json` and
  `proposed_records.txt` into `move/presets/`, name any new variant in `NAMES`, run the generator, then
  `node tools/move_ui_shots.mjs --docs` and the MOVE.md list (`test_plugin_presets_and_hierarchy_per_type` reads
  every preset back against the records).
- **Old saves.** `move/tests/main097/` holds a state and a store.bin (as hex) saved by the main branch's plugin before
  the EQ (64 ids), made with `test_bridge script`'s `state_dump` from that build's `dsp.so` in a `TB_DIR` data folder.
  `tests/py/test_move_eq.py` loads both into today's plugin: the sound and the settings as saved, the EQ flat (the
  bridge's `mb_state_set` takes a base of 64 or 80 ids). The EQ's cards show the FM-1's texts from
  `tools/gen_move_module.py`'s `FMT` (a port of params.c's F_EQDB / F_EQHZ), checked against the C at every value
  (`tests/c/eq_text.c`).
- `tools/ci_move.sh` runs the Move's checks on the host. `node move/ui/test_screen.mjs` checks Frodhes' screen
  (`move/ui/harness.mjs` runs it on a model of the Move's screen and the plugin), `node move/ui/test_chain.mjs`
  the same screen as the whole Move UI (`ui_chain.js`); the pictures: [The screenshots](#the-screenshots).
- **The screen reader** ([MOVE.md](MOVE.md#screen-reader)): what is said and when is `move/ui/screen_core.js`
  (its section "the screen reader": `say`, `pageSay`, `rowSay`, `srTouch`, `srTurn` / `srFlush`), from the event
  hooks only, never from `draw`; a turn says at most one value per 120 ms a knob (Schwung's grid,
  `page_controller.mjs` `ANNOUNCE_THROTTLE_MS`), and the value it ended on from the next live tick. The words are
  `tools/gen_move_module.py`'s `SAY_NAMES` (every control on a page, by key: the generator stops on a new control
  without one), `SAY_PAGES` and `SAY_WORDS` (options), written into `ui_chain.js`'s `FR_CONF.say`. The speaking is
  `move/ui/chain_adapter.js`: `ctx.announce` is Schwung's `shared/screen_reader.mjs` `announce` (imported at the top
  of `ui_chain.js`, as the knob LEDs' `knob_leds.mjs`; it speaks only while the shadow UI is on screen), and
  `ctx.srOn` asks `tts_get_enabled()` first, so with the screen reader off no text is built and nothing is written
  to Schwung's screen-reader memory. `screen.js` (the canvas cell) has no announce and says nothing.
  `move/ui/test_chain.mjs` records every announcement (its last section). On the device, an announcement is the
  text and sequence number in `/dev/shm/schwung-screenreader` (`host_send_screenreader`).
- The meter on the COMPRESSOR page is a Schwung custom widget (Schwung's docs/MODULES.md, "Custom widgets"). Two
  read-only keys, `r_cmp_gr` (GR) and `r_cmp_pk` (PEAK), form one picture group with the kind `custom:frodhes_gr`.
  Schwung draws it with `drawCell` from `canvas.js`, which `module.json` names (`capabilities.canvas_script`).
- The VOICE pages: `get_param("ui_hierarchy")` answers the sounding engine's pages only (one text per engine in
  `move_ui.h`, `hier_for_engine` in the generator; `move/module/ui_hierarchy.json` is all of them, gated on
  `ui_engine`). Schwung 1.8.1 re-plans a `visible_if` only when its grid writes the gate key or a pad press marks the
  gates due, so a gate that moves with TYPE was never re-read. The plugin answers `is_loading` 1 for 600 ms after the
  engine changes; Schwung re-reads the contract on the 1 to 0 edge (and after a preset pick) and re-plans.
  `node move/ui/test_grid.mjs` runs the pages through Schwung's own controller (`SCHWUNG_SRC`).
- Instances: the app is one set of globals a loaded file, and Schwung loads `dsp.so` once for every slot. So
  `dsp.so` is a thin loader (`move/move_loader.c`) and the plugin ships beside it as `engine.so`. Each create (on
  Schwung's loader thread) copies `engine.so` to `module_data/frodhes/inst/<k>/dsp-<crc>.so`, again when its size or
  CRC-32 changed (an update), and dlopens that copy: its own globals. Destroy dlcloses it and frees index k (on
  the host the copy is unloaded; inside Schwung 1.8.1 it stays mapped, so the next create of k continues that app,
  as one file always did; glibc finds a loaded object by its name, so each copy's name carries its CRC and an
  updated `engine.so` really loads). A
  re-pick creates before it destroys, so it takes the next index for a moment. Each instance writes its own
  `boot.txt` (its copy's CRC, `at=` its start), `tap/`, `cpu-ring.log` and `stats.log` in `inst/<k>`; `dev_inst`
  reads k. `store.bin` stays one file in the data folder: a write merges under `flock` (the presets on disk win
  unless this instance changed its own), and each worker reads a store.bin another instance wrote and hands its
  presets to the app when quiet. A copy that cannot load gives a silent instance named NO ENGINE. Without the
  loader (`engine.so` on its own, the tests) the newest instance owns the app, the others are silent and say ONE
  SLOT ONLY. `move/tests/test_multi.c` (in `make test-move` and Gate P) and `tests/py/test_move_instances.py` test
  it; `tools/move_multi_cpu.py --n 2 3` plays the worst take in N slots at once on the Move. It reads
  Schwung's `spi_timing` lines from `debug.log`, which Schwung writes only while
  `/data/UserData/schwung/debug_log_on` exists and never trims (43 MB after two days on). So the tool makes that
  file for its run and removes it after, unless it was there before.
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
  model of the device). `usb-clock` always plays over the tap: no MIDI clock reaches a slot from USB-C (see
  [MIDI and tempo](MOVE.md#midi-and-tempo)). On the Move first: Settings, USB-C audio out = Main Out (the Move sets it back to Mic at
  every boot). `d2` runs them all for each engine (`usb-clock` once, with the Rhodes) and writes one table
  (`summary.md`). First it runs the preflight (below), then for each engine it checks over Schwung's WebSocket
  (`tools/move_ws.py`) that slot 1 holds Frodhes and loads the engine's preset there. A failed preflight stops
  the run. `move_ws.py` connects to the Move's IPv4 address first (and then to the one that answered last time):
  `move.local` also resolves to an IPv6 link-local address, and a connect to it that hangs used up the whole open
  timeout, so the VIBES `usb-host` of 2026-10-28 timed out. It tries the open 3 times.
- `tools/move_jack.py` is the Move's `hil_jack.py`: while MIDI plays Frodhes over the Standalone port, it records
  the Move's headphone / line jack on the M6's input 5 together with the USB-C audio. Per engine: the jack's floor
  and hiss against USB, a loud chord's peak and RMS on both, `usb-stress`'s worst takes judged on the jack
  (hil_crackle's detectors, plus bursts over 12 kHz: a one-sample click of the Move's reaches the jack
  band-limited, between the M6's samples), and the jack against USB aligned by cross-correlation (the level
  difference, the residual after the analog path's own response, per octave, and any click in the residual that
  USB does not have). Before any verdict it proves its jack detector on the rig: a clean take, then a take with
  six real clicks put into the plugin's output by the dev key `dev_fault` (`click N EVERY_MS LSB`, `zero N
  EVERY_MS FRAMES` or `off`, over Schwung's WebSocket; no page has it, off at load). The detector must stay
  silent on the first and find every click on the second, or the run stops without a verdict. `--prove` runs the
  proof only, `--dry-run` runs everything against the host build with a simulated jack (no device, no lock).
  The jack on the M6 has a floor of -65.7 dBFS (the first device run, 2026-11-04; the FM-1's jack: about -91), so
  the jack-vs-USB correlation is judged against the floor measured in each take's lead, and the jack is aligned
  along the two recordings' clocks, not on a held chord's period. A jack-only level step (20 ms windows, 3 dB
  off USB) is flagged too.
  Results: `build/move/hil/<time>-jack/summary.md`.
- **The preflight** (`tools/move_hil.py preflight`) runs before every command that gives a sound or level verdict:
  `usb-host`, `usb-note`, `usb-vel`, `usb-sus`, `usb-clock`, `usb-stress`, `d2` (over USB-C and over the tap) and
  `usb-levels`. `--no-preflight` skips it. A failure stops the command (exit 2). It needs USB-C audio and MIDI,
  Schwung's WebSocket and ssh, also for `--path tap`. The 2026-10-29 case it catches: a Move sampler on Frodhes'
  track whose sample held recorded noise played with every note, and the tap tests passed because the tap was
  exact zero. Its checks:
  1. **Main Out idle.** It waits for silence, then records Main Out for 1 s with no note. The RMS must stay under
     -80 dBFS and the peak under 128 LSB. Clean, the Move's Main Out at idle is exact zero (0 LSB, measured
     2026-10-29); the bad case was -54.6 dBFS RMS. The microphone has its own signature: rumble under 20 Hz, the
     two channels mostly equal, about -55 dBFS RMS from the room. It stops with "USB-C audio is the microphone:
     set Settings > USB-C audio out > Main Out (the Move resets it at boot)". Any other sound stops it with "Main
     Out not silent with no note: something else is playing on the Move (another instrument on the track, a clip,
     input monitoring)".
  2. **Main Out against the tap.** The tool arms a tap take with no events (`dev_play`: the plugin writes what it
     plays), then plays C4 at v100 over the Move's MIDI path and records Main Out. It aligns the two by cross-
     correlation and fits one gain a channel by least squares: the trim (`MB_TRIM_Q12`, +2.2 dB) times the slot's
     and the track's volume. Then it judges the residual, Main Out minus the gain times the tap. Over -40 dB
     against the note, or over -66 dBFS RMS, is "sound on Main Out that is not Frodhes", with the residual's
     strongest frequencies and the third-octave bands where it comes within 20 dB of the note. Clean on the Move:
     correlation 1.00000, residual -70.4 dB against the note, -100.3 dBFS, at most 1 LSB. Noise added to that
     take fails from -70 dBFS RMS up. A correlation under 0.9 is the microphone again (0.71 measured), and a tap
     that stays silent means the note never reached Frodhes.
  3. **What the slot holds.** Over the WebSocket: the slot's chain (synth, audio FX, MIDI FX), its receive and
     forward channel, volume and mute, and Frodhes' CHAN. Over ssh: Schwung's `shadow_chain_config.json`, and
     the Move's saved set (`active_set.txt`, then its `Song.abl`) for track 1's MIDI input and instruments,
     including whether a sampler has a sample loaded. The saved set can differ from the live one. A source that
     cannot be read is named. Also over ssh: the set's scene locks on the slot (`set_state/<set>/scenes.json`),
     listed as "Schwung's scene locks hold ... on this slot".
- **The channel.** The tools send on the slot's own receive channel: Schwung's WebSocket (`slot:receive_channel`),
  else `shadow_chain_config.json`. If neither can be read, they stop, name channel 1 as the one they would have
  assumed, and tell you to pass `--ch N`. If `--ch` names a channel the slot does not receive, they stop. If
  Frodhes' CHAN is not the channel the slot hands it (the forward channel), they stop too. When the Move's track
  takes MIDI in on another channel than the slot, both are reported. `--path tap` sends its messages on the
  channel Frodhes sees.
- `--path tap` (the `usb-*` tests and `d2`) uses USB-C only for the preflight. Each take goes over Schwung's WebSocket to the
  plugin's own player (`dev_play` and `dev_play_add`), so no Move track hears it. The plugin writes its own output
  to `module_data/frodhes/inst/<k>/tap/<take>.wav`, and the tool fetches the file over ssh. The take starts on the host
  build's block grid (every 2560 ms), so `usb-host` compares it sample for sample. The Move's mix, its volumes and
  its USB-C setting play no part.
- `d2 --path tap` also reads the plugin's callback times while `usb-stress` plays, which is the worst case. The
  plugin logs them while `dev_cpu_log` is 1, and the tool fetches the newest `inst/<k>/cpu-ring.log` over ssh. The `cpu` row checks
  them against the working limit. The plugin loads with the HQ Rhodes on. `--hq 0` plays Classic for the run, for
  a test only, through the plugin's `p_hq` key (no page has it; the next load is HQ again), and `--hq 1` sets HQ.
  HQ applies to new notes only: a sounding note keeps the path it started with. Unless `--hq 0` is given,
  `usb-host` compares against the host build with HQ on. The phrase has a note that only HQ changes (96 at v90).
  If that note plays as Classic while HQ is due, the test fails as CLASSIC. Gate M and the bridge's `eq` tests
  set `p_hq` 0 first, so they compare the Classic path with the host renders; `make test-move` also compares the
  plugin as it loads with the host build with HQ on.
- **How `usb-host` lines a note up.** Each note of the take is aligned on the host's by the cross-correlation of
  its held second half. A steady tone correlates almost as well a whole period later, so candidates within a tie
  (`ALIGN_TIE`) are told apart by the attack: the 1 ms RMS envelope of the note's first 0.3 s, then the best lag
  within 1 ms of it. Without that, the Move's 96 v90 of 2026-10-30 lined up 953 frames late, not 658, and read as
  ANOTHER SOUND (-29.7 dB extra); aligned, it is -36 dB under the note, at the take's floor. The test rebuilds that
  case from the host build (`test_usb_host_96v90_of_the_2026_10_30_take`: the render 17.1 dB down and 658 frames
  late, rounded, its tail mostly 14 periods later, so the tail alone peaks at 953). The HQ probe (96 v90 against the Classic host) gives CLASSIC only
  where the take can tell the two apart: its own floor, the nearer of its two residuals, must be
  `HQ_RESOLVE_DB` (3 dB) under the two hosts' difference there. A USB-C take through the Move's volume (Main Out
  17 dB under the tap) cannot: it passes and says so, and the tap path checks HQ.
- **Scene locks.** A Schwung scene that locks a Frodhes parameter puts it back after every program change and
  preset load: on the Move of 2026-10-30, BARK and TONE (and FX) were locked on slot 1, so F13's BARK 52 played as
  the held 64 and VIBES read DIFFERS. `usb-host` reads the locked keys' live values after its program change
  (`held_reader`) and gives them to the host as CCs after its program change too (a latch such as FX has no CC
  the host takes), and its verdict names them. The tools never change the locks.
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
  instance (`move/tests/boot_keys.txt`, checked by `test_bridge boot`). The Move has no sequencer or chords,
  and the bridge drops their CCs, so the tests do not send them; a new instance starts with the arpeggiator off. A note that the host strikes once
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
  counters, Schwung's errors, and every instance's `boot.txt` (a reload fails the run). The click detector
  runs on every take: a repeated or stale block fails; zero, clip and click are counted, as on the FM-1's monkey.
  `--dry-run` prints the plan. `tests/py/test_move_monkey_device.py` checks the tool offline.
- `move/bench/classic_bench.c` times the plugin's callbacks on the Move, off the audio core. Each case is a
  `dsp.so` with suffixes: `:top` plays the chord on keys 85 to 108, `:classic` sets `p_hq` 0 after the load, and
  `:dV` sends CC 84 V first (the drive's TYPE). The callbacks run back to back on a core the Move shares, so the
  bench's tail is higher than the plugin's own ring in `d2`. Use it to compare cases within one run.
- `clock-probe` shows where the clock comes from when the Move's own transport plays. The tool asks for Play
  on the Move, waits until the transport runs, records 10 s, then asks for Stop. With `--auto` the tool presses
  Play and Stop itself, through Schwung's test bus (`tools/move_ui_drive.py`), and sets the bus back afterwards.
  `move_ui_drive.py`'s `shot:NAME` reads Schwung's display mirror: before `arm` (display_mirror 0) the mirror holds
  its last frame, maybe long gone, so such a shot is saved and marked STALE.
  Meanwhile the plugin logs its counters to `stats.log` in its instance's folder (`inst/<k>/`), 10 times a second,
  while `dev_stats_log` is 1. The counters are the
  F8, FA and FC that came in, the pulses the bridge made itself, the host's transport and tempo, and the app's
  tempo. It passes when the Move's own F8 arrive at the host's tempo and the bridge makes no pulses after the
  first 3 s. It fails when only the bridge's fallback runs, or when the transport never ran.
- `usb-clock` sends MIDI clock (Start, then F8) at 100 BPM, or `--bpm`, through the plugin's own player (the
  tap), on either path. Schwung gives a slot no clock from USB-C, so the same clock sent there was never seen:
  that take read a jitter of 82 ms and failed (2026-10-30). A rig whose clock cannot reach the slot is refused
  before any take, and the fake Move drops the clock over USB-C too. The tremolo is on (AM, full depth,
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
  volume. *(not yet verified on a device)* Several Frodhes and a Move track play together: on 2026-10-30 a chord
  on channel 1 (slot 1, F01), channel 2 (slot 2, F14) and channel 4 (the Move's track 4, its electric piano), alone
  and all at once, all three on Main Out, the levels adding as independent sounds, no USB-C gap, the two slots'
  render at most 355 us and 217 us a block (Schwung's `spi_timing`).
- The buttons: `module.json` claims the Right and Left arrows (`capabilities.claims_ccs`, `SUS_BTN_CC` 63 and
  `FX_BTN_CC` 62 in `tools/gen_move_module.py`; the Left arrow's press is `s_fxbtn` 1, which flips FX, and the Move's
  bypass keeps the compressor: `FR_BYP_KEEP_CMP` in `MOVE_DEFS`, fx.c's dry taken after the compressor). The sustain
  control: the Right arrow (`SUS_BTN_CC` in
  `tools/gen_move_module.py`); Schwung gives a claimed button only to the module's screen script, so `screen.js`
  passes its press to the plugin (`s_susbtn` 1), which flips SUS (`s_sustain`). `s_susbtn` is no page's knob and has
  no `chain_params` entry: Schwung's metadata merge lets a `chain_params` entry win over a hierarchy item, and the
  0.9.8 candidate's PEDAL cell became an UP / DOWN enum whose click left the button held (SUS could not be
  turned off). The direct route,
  a knob or jog-wheel touch (`capabilities.touch_observe`), is off unless `dev_sus_touch` names the control
  (0-7 a knob, 9 the jog wheel).

## Device evidence for the manual

The measurements behind the statements in [MOVE.md](MOVE.md), on a Move with Schwung 1.8.x, the screen read back
from Schwung's display mirror and the controls sent through Schwung's test bus unless a line says otherwise.

- **The screen** (2026-10-30): the pages, the cards and the bars draw as the host renders them; the large card goes
  0.52-0.56 s after the knob is let go; both arrows turn SUS and FX on and off (also checked by the user); the
  menu's MODULE rows (Module Help, the module picker) open Schwung's screens and Back comes back to the list. SAVE
  TO USER saved into U16 when quiet, and a second Frodhes loaded afterwards listed it; a save from the second listed
  in the first within seconds. A double tap on SPREAD set it back to 0 (50 to 0, with the toast). Remove Module from
  the menu emptied slot 2.
- **The pads' lights** (2026-10-30, presses through the test bus, not a finger): the Move writes the pad white (126)
  at the press and off-white (122) at the release, and the root's colour comes back only when another pad is played.
  This happens on Frodhes' track with its screen open or closed, and on the Move's track 4 with its own electric
  piano and no Schwung screen. Schwung passes the Move's writes through unchanged.
- **Several slots**: two, three and four Frodhes at once, 16 voices and every effect heavy in each for 30 s: no
  glitch on the taps or on Main Out in any of them; with four the margin is small, so the manual's warning stands.
  On 2026-10-30 a chord on channel 1 (slot 1, F01), channel 2 (slot 2, F14) and channel 4 (the Move's track 4, its
  electric piano), alone and all at once: all three on Main Out, the levels adding as independent sounds, no USB-C
  gap, the two slots' render at most 355 us and 217 us a block (Schwung's `spi_timing`).
- **REVERB FREEZE** (2026-10-30): at FREEZE the frozen tail alone was -37.0 dB RMS on Main Out, and with a v100 note
  -34.8 dB: the note played dry at its usual level.
- **Output level** (USB-C, slot volume 100%): 16 voices and every effect heavy at v127 for 30 s peak at -0.5 dBFS
  with no glitch; F01 at v127 is within 0.6 dB of the Move's stock electric piano by critical-band loudness, which
  barely changes with velocity, so Frodhes is quieter at softer playing.
- **The set** (2026-10-30): Frodhes in slots 1 and 2, the set saved by Schwung, each slot's sound changed, then both
  slots loaded again from the saved set (Schwung's set restore, `load_file`): the preset, SPREAD, TUNE and the delay
  came back on both; a slot emptied stays empty in the saved set. SUS comes back off and FX on.
- **Velocity** (2026-10-30): with Full Velocity on, every pad hit and every repeat arrived at 127, and each note from
  the Standalone port arrived twice, as played and at 127. With the copy dropped (`echo_drop` in `dev_stats`: a
  note-on at 127 within one block of the same note's first note-on), notes at v20, 40, 64, 100 and 127 played at
  -49, -45, -39, -30 and -24 dBFS on Main Out. With Full Velocity off, the pads' hits arrived at 1 to 127.
- **The clock** (2026-10-30): with the Move playing at 82 BPM, the Move's clock reached Frodhes at 32.8 pulses a
  second (82 x 24 / 60), with one Start and one Stop. The bridge made no pulses of its own, and the tremolo's dips
  came every 366 ms (1/8 at 82 BPM). Two Frodhes, in slots 1 and 2, both got every pulse. The clock sent over USB-C
  never arrived: the plugin counted 0 pulses.
- **Not verified on a device yet**: the plain `tar` install (the installer is the tested route), the MAPPINGS
  (pressure), the CHAN setting with the Move's tracks, the HQ sound's described differences, the 128-frame blocks and the 725 us working
  limit, and the large pictures added for this manual's pages (FREEZE, the X header, the
  toast, the menu rows), which are the same drawing code the device check covered.
