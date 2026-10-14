# Review 03: UI/UX, product and effects behaviour

Reviewer lens: UI/UX, the product, and how the effects behave. This review covers [PLAN.md](../PLAN.md) §4–§5 and
Phases 4–7. It cites research files R05–R08, Felucca's source, the Frodhes source and the reface manual.

**Sources.**
- `PLAN` = docs/model/PLAN.md. `R05`..`R08` = docs/model/research/0N-*.md. `API` = docs/API.md.
- `FEL/` = docs/model/research/sources/06-felucca/Felucca/.
- `FEL/docs/controls.jpg` shows the physical FM-1 panel:
  - MASTER, SELECT, PRESETS and ALGORITHM are **left** of the screen, with OCT−/OCT+ under them.
  - KNOB 1–4 sit in a row **right** of the screen.
  - The buttons are in two rows on the right: `FX SEL ENV LFO EDIT GLO` above, and `HOME SAVE ARP SEQ PLAY REC` below.
  - The 27 keys span the full width under the panel.
- `MAN` = the Yamaha *reface CS/DX/CP/YC Owner's Manual* (ZT92080), pp. 26–31 and 41.
  - Fetched from <https://data.yamaha.com/files/download/other_assets/6/438816/ZT92080_reface_En_OM_C0.pdf> and read with
    `pdftotext`.
  - The usa.yamaha.com product and feature pages returned HTTP 403, as the plan warned.
- `CCMAP` = the reface CP MIDI CC list at <https://midi.guide/d/yamaha/reface-cp/>.
- Numbers marked **ESTIMATE** are mine.

---

## Verdict

**Approve with changes.** The core of the plan holds up:
- one `PARAMS[]` table;
- a reface-style PANEL;
- REC as the sustain pedal;
- PLAY as tap tempo;
- one sync table;
- SNAP/DEGREES scales;
- "the panel is the sound".

The R07/R08 conflicts were resolved sensibly:
- ARP focuses the delay column, and the arp moves to SEQ in Phase 8.
- The SCALE page order is MODE · SCALE · ROOT · TRANSPOSE.
- SELECT is BPM.

Before Phase 4 starts, **ten issues need fixing**. Four of them would show up as product bugs:
1. **The FM-1 keys have no velocity.** `fm1_in.notes` is a bitmask, and `frodhes.c:114` plays every key at 100. The
   plan's "realistic Rhodes" bark and dynamics, and its VEL CURVE knob, cannot be reached from the instrument itself.
2. **The 3-way switch gesture (OFF→A→B→OFF) passes through the B effect to turn A off.** For example, turning the
   tremolo off goes through WAH.
3. **The CC map clashes with the reface CP's own published CCs.**
4. **A full-screen redraw costs about 77 ms of SPI time** (12 MHz, `lcd.c:10`), and the main loop scans keys between
   draws. Page changes will make notes late.

The plan also departs from the user's brief and from the reface manual in four places:
- It draws **5 columns where the user asked for 7** (TYPE, DRIVE, …, VOLUME).
- It lets **TYPE overwrite the effects**. The reface never does this; TYPE changes only the tremolo's character and the
  drive's voicing (MAN p.27).
- Its **WAH** has the wrong knob meanings. The reface's is a touch wah: DEPTH is the sensitivity and RATE is the
  resonance (MAN p.28).
- It **saves to flash while delay/reverb tails still ring**: `rh_quiet()` only looks at the voices.

All of these are cheap to fix. §4 gives a complete spec with the fixes applied.

---

## Critique

Each item has a severity, the evidence, and a concrete fix.

### 1. MAJOR: the PANEL has 5 columns; the brief asks for 7

- **Evidence.**
  - PLAN:134-145 draws TYPE (with DRIVE inside it) · TREM · CHO · D.DLY · REVERB, with no VOLUME column. R07's mockup
    (R07:445-470) does the same.
  - The user's brief, and the reface panel itself (MAN p.26-29), read left to right: TYPE, DRIVE, TREMOLO/WAH,
    CHORUS/PHASER, D.DELAY/A.DELAY, REVERB, VOLUME.
- **Fix.**
  - Draw **7 columns of 34 px**: x = 1 + 34·i, which fits 4 characters of FONT_S (8 px each).
  - VOLUME shows the MASTER pot read-only, through `fm1_adc_read(FM1_ADC_MASTER)` (API "Keys…").
  - Pixel layout: §4.2.

### 2. MAJOR: the on-board keys have no velocity, so realism and the VEL CURVE knob are unreachable from the instrument

- **Evidence.**
  - `fm1_in.notes` is "the 27 keys held now", a bitmask (API:64).
  - `frodhes.c:114` calls `rh_note_on(held[k], 100)`.
  - The plan's velocity law, its bark at v ≳ 96 (PLAN §3 check 6, R05:274) and the VOICING page's VEL CURVE
    (PLAN:157) only ever affect USB-MIDI input.
  - A Rhodes' character is mostly its dynamics (R05: velocity drives the excitation, and the pickup turns loudness into
    bark).
- **Fix.**
  - Add a global **KEY VEL** (1..127, default 96): the velocity the on-board keys play at.
  - Move VEL CURVE to the MIDI page, since it only applies to MIDI in.
  - Make **SELECT = KEY VEL ("DYN")** a MENU choice, and recommend it as the default. SELECT is the only encoder on the
    left of the screen besides PRESETS and ALGORITHM (`FEL/docs/controls.jpg`). With it, the left hand rides
    dynamics and bark while the right hand plays. This is open question Q2.
  - Optional: a HUMANIZE amount (±0..16 random velocity) per note.

### 3. MAJOR: the column-button gesture OFF→A→B→OFF goes through the other effect

- **Evidence.**
  - PLAN:156: "Tap the focused one again: OFF→A→B".
  - Turning a tremolo off goes TREM→WAH→OFF, so the WAH sounds for a moment.
  - Going from PHASER to OFF and back to PHASER takes 1 tap plus 2 more, passing through CHORUS.
  - The reface switch is a physical up/middle/down lever (MAN p.28-29) that moves straight to OFF.
- **Fix.**
  - **Tap on the focused column toggles OFF ↔ the last-used position (A or B)**, remembered per column.
  - Pick A or B in the column's **hold layer on K1 (SW: OFF/A/B)**, or over MIDI CC.
  - A tap on an unfocused column only focuses it. It never changes the sound.

### 4. MAJOR: the MIDI CC numbers clash with the reface CP's published map

- **Evidence.**
  - PLAN:195 gives 91 reverb, 92 trem depth, 93 chorus depth and 94 drive.
  - The reface CP uses 80 TYPE, 81 DRIVE, 17/18/19 TREM/WAH switch/depth/rate, 85/86/87 CHO/PHA switch/depth/speed,
    88/89/90 delay switch/depth/time and 91 reverb (CCMAP).
  - jRhodes uses CC 92 for *tremolo frequency* (R05:160). So the plan's 92 means "depth" to a Frodhes and "rate" to a
    jRhodes patch.
- **Fix.**
  - **Adopt the reface CP map exactly**, including its switch bands: 0–42 OFF, 43–85 A, 86–127 B.
  - Put Frodhes-only params on undefined CCs (20–31, 102–105).
  - Make CC transmit a setting, off by default, as on the reface (MAN p.41 "MIDI Control … Factory default setting:
    off").
  - Full table: §4.1.

### 5. MAJOR: TYPE must not rewrite the effects

- **Evidence.**
  - PLAN:71-80: "Each type is a parameter set (… default effects)", and for SUITCASE, "stereo autopan defaulted on".
  - On the reface, TYPE is a knob next to the effect knobs. Turning it never moves them. What TYPE *does* change:
    - the **tremolo's character**: Rd I, Rd II and CP pan left and right, while Wr, Clv and Toy vary the volume
      (MAN p.27-28);
    - the **drive's voicing** ("distortion matched to the current voice", Yamaha product-page summary; MAN p.27).
- **Fix.**
  - TYPE changes the voice tables and the drive's bias, gain and tone table only.
  - TREM PAN/AM becomes its own preset param (layer K3, default PAN, as the reface does for every Rd type).
  - Effect defaults belong to **presets**, not to types.
  - A TYPE change applies to **new notes only**. Sounding voices keep their tables, so nothing clicks.
  - The drive make-up gain per type is level-matched, so switching types never jumps the volume (a host test,
    §4.10).

### 6. MAJOR: WAH has the wrong controls

- **Evidence.** The reface's WAH "modulates the filter cutoff frequency based on the input volume". With wah selected,
  DEPTH "adjusts the sensitivity of the wah filter" and RATE "adjusts the resonance offset value" (MAN p.27-28). The
  plan and the research disagree with this:
  - PLAN:137-142 labels the column's second knob "RATE (sync)".
  - R06:395 maps RATE to the follower's release.
  - R07:373 adds a "RANGE" knob.
- **Fix.**
  - WAH is a touch wah: DEPTH = sensitivity, and K2 = **RESO** (the same stored byte as RATE, as the reface's knob is
    one physical knob).
  - The K2 label changes to RESO, and SYNC does not apply while WAH is selected.
  - Use R06's envelope follower and SVF, with resonance k = 0.60 → 0.15 (Q ≈ 1.7 → 6.7, ESTIMATE).
  - With fixed key velocity (#2), the wah still follows each note's attack and decay.

### 7. MAJOR: redraw cost blocks key scanning; the plan has no draw scheduler

- **Evidence.**
  - The LCD runs on SPI at **12 MHz** (`lcd.c:10`, `LCD_BAUD 4`), about 1.5 MB/s. A full 240×240×2 frame takes
    **~77 ms**, a 34×180 column ~8 ms, and the footer ~12 ms (ESTIMATE: payload only).
  - `cv_begin` calls `lcd_sync()` and waits for the previous DMA (`gfx.c` cv_begin). Two strips in a row therefore
    block for the first one's transfer.
  - The main loop reads the keys once per pass (`frodhes.c:105-118`). A page change drawn in one pass delays every
    note-on in that window by up to ~80 ms.
  - `fm1_input_note_edges()` keeps short presses from being lost, but not from being late.
- **Fix.**
  1. Each screen element has a dirty bit.
  2. Each loop pass draws **at most one element** (≤ 240×64 px, the canvas limit, `platform.c:57`), and only once the
     previous blit has had time to finish. Use `bytes / 1500` ms as the estimate, or expose `lcd_busy` and check it
     without blocking.
  3. Priority: the hot cell, then the header, then the footer, then the columns.
  4. In Phase 0, also test `-DLCD_BAUD=2` (about 20 MHz). It is `#ifndef`, so no platform file needs editing.
  5. A host test asserts the maximum blit bytes per pass (§4.10, `ui_redraw_budget`).

### 8. MAJOR: scale modes break the key LEDs and the keyboard drawing

- **Evidence.** `lit = rh_lit(base, NKEYS)` (`frodhes.c:133`) lights key k when note `base + k` sounds.
  - With SNAP or DEGREES the key that sounds is not the key that was pressed. In DEGREES, key 9 (D4) plays the 3rd
    degree.
  - The wrong LEDs light, and two keys snapped to one note light a third key.
- **Fix.**
  - Light **per key**: key k is lit when `held_on[k]`, or when the note it started still sounds under sustain.
    Keep `held[k]` (it exists already) and add a per-key "sounding" bit, cleared when its voice ends.
  - Draw `rh_lit` from MIDI-in notes separately, on the screen keyboard only.
  - Add the per-note reference count from PLAN:186 (`cnt[128]`).

### 9. MAJOR: "save when silent" ignores the effect tails

- **Evidence.**
  - `frodhes.c:146` saves when `rh_quiet()`, which checks only the voices.
  - An erase "holds the interrupts off for tens of milliseconds: the sound drops out" (API "Settings").
  - Once the delay and reverb exist, a save 2 s after a knob turn will cut a ringing hall tail or delay repeats. A
    preset write (Phase 7) has the same problem.
- **Fix.**
  - Add `fx_quiet()`: the output peak stays under −66 dBFS (|s| < 16 in Q15) for 500 ms, measured in the ISR as a
    volatile.
  - Save only when `rh_quiet() && fx_quiet()`.
  - Never refuse a user save. Queue it, show "SAVING WHEN QUIET", and write it at the next silence.

### 10. MAJOR: the host harness cannot exercise this UI

- **Evidence.** `tests/host.c`:
  - `fm1_input_edges` returns press edges only: `released` is always 0, and `fm1_in.buttons` is never set, so there
    is no hold.
  - `fm1_enc_take` always returns 0.
  - `fm1_led_key` is a no-op.
  - The screenshot times are fixed (`SHOT[] = {1000, 3000}`).
  - The settings store does not persist between runs.
- PLAN Phase 4 promises "host UI tests (screen PNGs)" without listing these harness changes.
- **Fix.** Extend `tests/host.c` as the first task of Phase 4. Commands and scripts: §4.10.

### 11. MINOR (UX risk): browsing presets loads on every detent and can lose an edit

- **Evidence.** PRESETS "loads on each detent" with one level of undo on SAVE held (R07:437-438).
  - Bump the PRESETS knob by accident and the edited panel is gone.
  - After two or more detents, a one-step undo only goes back to the previous *preset*.
- **Fix.**
  - Capture the undo buffer **only on the first load after an edit**: when `edited` is set, or after a non-preset
    change. Holding SAVE then always goes back to *your* panel, however far you browsed.
  - SAVE held again redoes.
  - The header shows `*` while the panel differs from the loaded preset, and the SAVE LED is lit while there are
    unsaved changes.

### 12. MINOR: BPM resolution on SELECT

- **Evidence.** PLAN:151 gives "BPM (40.0–240.0)" on SELECT and PLAN:173 stores `bpm10`. At 0.1 per detent, a
  full sweep takes 2000 detents.
- **Fix.**
  - SELECT moves 1.0 BPM per detent, with acceleration.
  - **GLO held + SELECT** moves 0.1 BPM.
  - Tap and MIDI-clock follow keep the 0.1 resolution.

### 13. MINOR: duplicated and missing sync params

- **Evidence.**
  - PLAN:160 puts TREM SYNC and DLY SYNC on the GLO page as well as in the columns (R07:391 calls them "duplicate
    … as a shortcut").
  - CHO/PHA sync is mentioned in PLAN:178 but appears on no page.
- **Fix.**
  - Each column owns one SYNC param, in its hold layer on K2: `FREE` or a division.
  - When SYNC ≠ FREE, the column's K2 on the PANEL steps the divisions. When it is FREE, K2 sets Hz or ms.
  - The GLO page holds globals only.

### 14. MINOR: globals mixed into the sound pages

- **Evidence.** VEL CURVE (PLAN:157) and TUNE (PLAN:158) are preferences of the player or the room, not part of a
  sound. If they are stored in a preset, loading a preset retunes the instrument.
- **Fix.**
  - GLO page: BPM, CLOCK, KEY VEL, TUNE.
  - GLO tapped again: the MIDI page (CH, CC TX, VEL CURVE, CLK OUT).
  - That frees the voicing pages for TONE, BARK, BELL, HAMMER / DECAY, RELEASE, NOISE, SPREAD.

### 15. MINOR: delay "fold down an octave" hides what you hear

- **Evidence.**
  - PLAN:179: "long delays fold down an octave to fit the buffer".
  - At 120 BPM, 1/1 is 88,200 samples, more than 65,536, so it plays 1/2 while the screen says 1/1.
- **Fix.** Keep the fold, but **show the effective division with a trailing `!`**, for example `1/2!`. The footer's hot
  readout says "1/1 > 1/2 (BUFFER)". A host test pins the behaviour.

### 16. MINOR: the PLAY LED beat flash is always on

- **Evidence.** PLAN:165 says "its LED flashes on the beat". Felucca flashes ARP only while an arp is playing
  (`FEL/firmware/src/ui_input.c:207-215`).
- **Fix.** PLAY flashes the beat only while any effect is synced (SYNC ≠ FREE and its switch is on), while the clock
  is external, or while clock out is on. Otherwise it is off.

### 17. MINOR: the hold layers repeat K1/K2 and need two hands

- **Evidence.**
  - PLAN:156: "Hold it: all 4 of that column's params on K1–K4". DEPTH and RATE are already on K1/K2 when the
    column is focused.
  - The buttons sit directly under the knobs (controls.jpg), so holding LFO and turning K3 takes both hands. That is
    fine for setup, but not while playing.
- **Fix.** The layer shows the column's **hidden** params only: SW, SYNC, and two extras. Leave the playing knobs on
  the PANEL. Do not add Felucca's double-tap lock (one gesture fewer).

### 18. MINOR: left-hand tweaking, right-hand playing

- **Evidence.**
  - Only MASTER, SELECT, PRESETS, ALGORITHM and OCT± are left of the screen (controls.jpg).
  - Every effect knob and every button is on the right. A player who keeps the right hand on the keys can, with the
    left hand, change volume, TYPE, preset, octave and whatever SELECT does.
  - The plan spends SELECT on BPM, which you set once per song and which PLAY tap and the GLO page also set.
- **Fix.**
  - MENU `SELECT ROLE` = DYN (KEY VEL, recommended), BPM, or LAST (rides the last-turned parameter).
  - Sustain is already one-handed (the REC tap latch).
  - Document the posture in the manual: left hand plays the low keys, right hand tweaks the effects.

### 19. MINOR (correction to R07): text on filled cells needs no colour-ramp port

- **Evidence.**
  - R07:120-122 says Frodhes text "can only be blended onto black … shows a black fringe" on filled cells.
  - `firmware/gen/felucca_font.h` comes from Terminus bitmaps (`ter-u16n.bdf`), and its alpha nibbles are only 0x0 or
    0xF (counted: FONT_S bytes are only 0x00/0x0f/0xf0/0xff, FONT_L bytes only 0x00/0xff).
  - `cv_text` writes only alpha ≠ 0, at full ink.
- **Fix.** Inverted cells (accent fill, black text) work today. Drop the `ramp(fg, bg)` port from Phase 4.

### 20. MINOR: the settings record size must be fixed

- **Evidence.** `settings_load` rejects a stored length ≠ n (`platform.c:152`; API: "A change of size … reads as
  nothing saved"). Adding one field in a later version would wipe the user's state.
- **Fix.** Always save **256 B**, starting with a version byte. Reserved bytes are zero. Migrate by version, and append
  new fields only. Layout: §4.5.

### 21. MINOR: naming presets with the keys is expensive

- **Evidence.** The phone-style naming of R07:439-440 comes from `ui_name.c`, about 470 lines.
- **Fix.**
  - v1 builds an **automatic name** from the content: TYPE plus the active effects, e.g. `MK1 TRM PHA DD`.
  - User renaming moves to the Phase 8 web editor.

### 22. MINOR: the button order does not match the screen order

- **Evidence.** FX (row 1, position 1) is left of LFO (row 1, position 4), but the screen shows TREM left of CHO. ARP
  sits in the other row.
- **Fix.** Keep the printed-label meanings (LFO→tremolo, FX→chorus/phaser, ARP→delay), which are easier to remember
  than positions. The PANEL footer shows the map (`LFO:TRM FX:CHO ARP:DLY PLY:TAP`). The alternative, mapping the top
  row to the six columns in order, is open question Q6.

### 23. MINOR: what happens on effect switch-off and preset load is not specified

- **Fix (spec).**
  - **Delay off:** cut its *input* and let its repeats die out (spillover), as reface/Yamaha delays do.
  - **Reverb at 0:** the same, the send goes to 0 and the tail rings.
  - **Trem/wah and chorus/phaser:** crossfade over 10 ms.
  - **D.DLY↔A.DLY:** keep the buffer and crossfade the read over 32 samples.
  - **Preset load:** the same rules apply, so effects never click on a load.

### 24. MINOR: in DEGREES, the root is the 5th white key

- **Evidence.** Felucca's WHITE mode puts the root on C4 (R08:288-291). On F3–G5 that leaves 4 white keys below the
  root and 12 above.
- **Fix.** Keep it: a root on C is familiar, and the code is tested (`FEL/tests/scale_test.c`). Show the degree numbers
  over the white keys on the SCALE page, so it is easy to see where 1 is.

### 25. MINOR: tap tempo and the clock source

- **Fix.**
  - With an external clock locked, PLAY shows "EXT CLOCK" and ignores the tap, as Felucca does (R08:93).
  - From the 3rd tap, synced LFOs **re-phase** so that the beat lands on the tap. Felucca does not re-phase
    (R08:104), but it costs one write and is what a player expects.
  - CLOCK defaults to AUTO: follow USB F8 while it arrives, and fall back after 500 ms (PLAN:176).

---

## Ideas ledger

Status: **IN PLAN**, **MISSING** (worth adding; phase given), or **REJECT** (with the reason).

| # | Idea | Source | Status | Reason / note |
|---|---|---|---|---|
| 1 | One `PARAMS[]` table drives UI, settings, presets, CC | R07:195-215 | IN PLAN | Keep it. Ids append-only (R07:214) |
| 2 | 7-column reface PANEL with a VOLUME meter | user brief, MAN p.26 | MISSING (P4) | Critique #1 |
| 3 | Values shown on the reface's 0.0–10.0 knob scale | MAN p.30-31 ("DEPTH … 5", "TIME … 1.5") | MISSING (P4) | Factory presets can quote the manual. Real units in the hot readout |
| 4 | reface CP CC map (17–19, 80–81, 85–91) | CCMAP | MISSING (P7) | Critique #4 |
| 5 | KEY VEL (on-board velocity) on SELECT, the "DYN" role | API:64, frodhes.c:114 | MISSING (P4) | Critique #2 |
| 6 | Humanize (random ±velocity) | — | MISSING (P4, optional) | 1 LFSR; makes the fixed velocity less mechanical |
| 7 | Half-pedal on CC64 (continuous) | R05:109 (jRhodes curve 11), MAN rear-panel FOOT CONTROLLER/SUSTAIN jack text: FC3 half-damper | MISSING (P7, engine hook in P2) | `rh_pedal(level)` scales the damper T60. REC stays on/off |
| 8 | Tremolo delay / fade-in after note-on | R05:108,161-162 (jRhodes CC93/94) | MISSING (P5, optional) | Retriggered on the first note after silence. Layer extra only if room |
| 9 | Separate autopan and tremolo | R05:163-164 | REJECT | The reface merges them into one TREM with PAN/AM (MAN p.27). Our TW_MODE covers it |
| 10 | Tremolo shape sine→square (Suitcase) | R05:226, EP-MK1 Shape R05:199 | MISSING (P5) | Layer K4 SHPE. Do not copy EP-MK1's φ² "sine" bug (R05:285) |
| 11 | EP-MK1 voicing params (pickup gain/symmetry/LPF, tine level, noteoff level, buzz) | R05:172-201 | IN PLAN (as macros) | Map to TONE/BARK/BELL/HAMMER/DECAY/RELEASE/NOISE (§4.1). The raw dB/Q params: REJECT, too many for 4 knobs |
| 12 | EP-MK1 micro-tuning (divisions, interval) | R05:175-177 | REJECT | Out of scope for a Rhodes. TUNE ±50 ct is enough |
| 13 | Stereo key spread (low keys left, high right) | jRhodes "st" (R05:78) | MISSING (P5, optional) | ENV K4 SPRD, default 0 (the Suitcase was mono before the tremolo) |
| 14 | Felucca hold layers (tap = page, hold = layer, combo) | R07:166-176, `ui_layer.c` | IN PLAN (changed) | Layer = hidden params only (critique #17). Keys keep playing in column layers. Silent keys only on SEL+key |
| 15 | Double-tap layer lock | R07:36 | REJECT | One gesture fewer. The layers are setup-only |
| 16 | Hot value (accented cell) + big readout | R07:182-184 | MISSING spec (P4) | Inverted cell plus a FONT_L footer line for 1.5 s (§4.2) |
| 17 | Help rows / key hints in the footer | R07:288-291, `ui_help.c` | MISSING (P4) | Cheap. Strings ≤ 30 chars (§4.2.6) |
| 18 | First-run hints ("HOLD [LFO] MORE") | R07:292-293 | MISSING (P4, optional) | 1 bit per hint in settings |
| 19 | Rolling digits | R07:87-89 | REJECT | A 1-bit mono font, 34-px cells and the SPI cost (critique #7). No value for a piano |
| 20 | Palettes / theme tokens | `gfx.c` PALETTES (already in the tree), R07:128-132 | MISSING (P4, cheap) | MENU COLOR uses the existing `PALETTES[]`. Amber default (the current C_ACC) |
| 21 | Anti-aliased fonts, icons, rounded cards | R07:109-123,134-136 | REJECT | Flash and time. The Terminus 1-bit faces are crisp at 240 px |
| 22 | Encoder acceleration | R07:178-180, `ui_input.c:470-498` | MISSING (P4) | **On by default** for ranges > 32 steps (127 detents per sweep is too slow on a panel). MENU can turn it off |
| 23 | Web editor (SysEx DESC/GET/SET/WATCH) | R07:295-316 | IN PLAN (P8) | Use tag `F0 7D 46 52` ("FR"). Needs the `app_sysex` hook |
| 24 | Preset names from the keys | `ui_name.c` | REJECT for v1 | Auto-names (critique #21). Renaming in the web editor |
| 25 | Undo of the last load (SAVE hold) | R07:268 | IN PLAN (changed) | Capture rule, critique #11 |
| 26 | Favourites/categories in the browser | R07:266-268 | REJECT | 12 + 16 entries do not need filters |
| 27 | Arp on SEQ | R08:523-530, PLAN P8 | IN PLAN (P8) | Main-loop on 24 PPQN. Low value for a Rhodes. Keep it after the looper |
| 28 | Strum | R08:532-535 | REJECT | It needs chords; a Rhodes player strums with the hand |
| 29 | Chord keys (DIA3/DIA7) | R08:521 | REJECT for v1 | Fights the "realistic Rhodes" goal. Revisit with the arp |
| 30 | Phrase looper (MIDI events) on SEQ | reface CS/DX have one (MAN p.14,24); the CP does not | MISSING (P8, recommended over the arp) | 2,000 notes × 4 B = 8 KB RAM. PLAY = play/stop when the looper is armed |
| 31 | MIDI clock out | R08:138-141,459-468 | IN PLAN | Off by default, GLO/MIDI page |
| 32 | Swing | R08:143-153 | REJECT | Nothing to swing without arp or looper |
| 33 | A/B compare (stored vs edited) | common synth UX | MISSING (P7, optional) | SAVE held already toggles undo/redo. That is enough |
| 34 | Speaker EQ / BASS+ for the small speaker | R06:269-271 | MISSING (P5, optional) | MENU row "SPEAKER EQ" if the FM-1 speaker needs it. Check by ear |
| 35 | Battery / CPU on an INFO row | API:70 BATT; `fm1_ticks` | MISSING (P4, cheap) | MENU > INFO: version, battery raw, ISR µs max. Useful for Phase 0 and 3 tuning |
| 36 | Settings "write only if changed" (memcmp) | R07:256-258 | MISSING (P4) | Saves flash wear for nothing |
| 37 | Reverb SPRING variant | R06:437 | REJECT for v1 | No reverb button or layer. The reface has one reverb |
| 38 | Reverb/delay spillover across preset loads | — | MISSING (P5) | Critique #23 |
| 39 | Velocity → pitch on hard bass notes | R05:30 | IN PLAN (P3) | It only matters with real velocity (#5) |
| 40 | Program Change → preset | PLAN:195 | IN PLAN | Mapping in §4.7 |
| 41 | MIDI IN quantize option | R08:516 | MISSING (P6, MENU, default off) | Cheap |
| 42 | LFO re-phase on tap | R08:442 | MISSING (P6) | Critique #25 |
| 43 | Layout lint in host tests (text fits its box) | R07:146-148 | MISSING (P4) | Lint every label and value string against its cell width (§4.10) |
| 44 | Screen off / dim after N minutes | R07:138-139 | REJECT for v1 | Small screen. Wake-swallow logic is complexity for little gain |
| 45 | **Bad idea in the plan:** TYPE sets default effects | PLAN:71-80 | REJECT | Critique #5 |
| 46 | **Bad idea in the plan:** OFF→A→B→OFF cycle | PLAN:156 | REJECT | Critique #3 |
| 47 | **Bad idea in the plan:** CC 92/93/94 | PLAN:195 | REJECT | Critique #4 |
| 48 | **Bad idea in the plan:** TREM SYNC/DLY SYNC duplicated on GLO | PLAN:160 | REJECT | Critique #13 |

---

## Detailed implementation spec (Phases 4–7)

Conventions:
- Stored values are bytes: `uint8_t`, or `int8_t` for bipolar params.
- `v` is the stored value. "R10" is the reface 0.0–10.0 display: `tenths = (v*100 + 63)/127`, shown as `"%u.%u"`.
- Every law runs in the main loop, through a host-generated table or 32-bit integer maths, and publishes volatiles
  to the ISR (RULES).
- `fmt` codes:
  - `ENUM`: a name list;
  - `R10`: shown 0.0–10.0;
  - `BIP`: −5.0..+5.0;
  - `HZ`: Hz from a table, shown as R10 in the cell and `x.xx HZ` in the hot readout;
  - `MS`;
  - `DIV`: a sync name;
  - `BPM10`, `CENT`, `SEMI`, `NOTE`, `INT`.

### 4.1 PARAMS[] table

**Sound params.** These are stored in the panel (settings) and in presets. The ids are append-only; `P_COUNT` = 28,
and slots up to 32 are reserved.

| id | key | cell label | where (control) | min | max | def | fmt | CC | knob law → engine |
|---|---|---|---|---|---|---|---|---|---|
| 0 | TYPE | TYPE | TYPE col, **ALGORITHM** | 0 | 3 | 0 | ENUM `MK1 MK2 SUIT BRT` (long `MK I`,`MK II`,`SUITCASE`,`BRIGHT`) | 80 (bands of 32; TX 16+32·t) | Selects the voice tables and the drive voicing row. New notes only |
| 1 | DRIVE | DRV | DRIVE col, **K3** on PANEL | 0 | 127 | 13 | R10 | 81 | Felucca `track_dist` (R06:239-250): `g = 4096 + v²·2·s[type]>>4` Q12, `k = 32000 − 95v`, `mk = (30000 − 120v)·mu[type]>>12`. v = 0 bypasses and keeps the state |
| 2 | TW_SW | TREM | TREM col, **LFO** tap / layer K1 | 0 | 2 | 0 | ENUM `OFF TRM WAH` | 17 (0–42/43–85/86–127; TX 0/64/127) | 10 ms crossfade on change |
| 3 | TW_DEPTH | DPTH | TREM col **K1** (focused) | 0 | 127 | 64 | R10 | 18 | PAN: `θ = π/4·(1 + (v/127)·lfo)`, `gL = cos θ`, `gR = sin θ` (Q15 table, R06:386). AM: `g = 1 − (v/127)·(1 − lfo)/2`. WAH: sensitivity, `sweep = env·(v+16)>>7` into a 64-row cutoff table, 300 Hz–2.5 kHz (R06:390-396) |
| 4 | TW_RATE | RATE / RESO | TREM col **K2** | 0 | 127 | 90 | HZ (TRM) / R10 (WAH) | 19 | TRM: `f = 0.5·24^(v/127)` Hz, 0.5–12 Hz, `LFO_INC[128]` Q32/sample (ESTIMATE range; v = 90 → 4.75 Hz). WAH: resonance `k = 0.60 − 0.45·v/127` (Q 1.7–6.7, ESTIMATE) |
| 5 | TW_SYNC | SYNC | TREM layer K2 | 0 | 13 | 0 | DIV (§4.8) | 20 | ≠ 0: one LFO cycle per division (R08:493-494). Ignored in WAH |
| 6 | TW_MODE | MODE | TREM layer K3 | 0 | 1 | 0 | ENUM `PAN AM` | 21 | reface: the Rd types pan (MAN p.27) |
| 7 | TW_SHAPE | SHPE | TREM layer K4 | 0 | 127 | 0 | R10 | 22 | `lfo = sat(sin φ · (1 + 7v/127))`: sine to rounded square (Suitcase, R05:226) |
| 8 | CP_SW | CHO | CHO col, **FX** tap / layer K1 | 0 | 2 | 0 | ENUM `OFF CHO PHA` | 85 | 10 ms crossfade |
| 9 | CP_DEPTH | DPTH | CHO col **K1** | 0 | 127 | 50 | R10 | 86 | CHO: 2 taps, delay 7 ms ± 3.5·(v/127) ms, wet 0.5·min(1, 2v/127) (R06:406-410). PHA: sweep span `q = 32768 + (tri − 32768)·v/127` (R06:216), out = (x + ap)/2 |
| 10 | CP_SPEED | SPD | CHO col **K2** | 0 | 127 | 50 | HZ | 87 | `f = 0.05·160^(v/127)` Hz, 0.05–8 Hz (v = 50 → 0.37 Hz) (ESTIMATE) |
| 11 | CP_SYNC | SYNC | CHO layer K2 | 0 | 13 | 0 | DIV | 23 | As TW_SYNC |
| 12 | CP_FDBK | FDBK / WIDE | CHO layer K3 | 0 | 127 | 40 | R10 | 24 | PHA: `fb = v·180` Q15 (0–0.7, R06:217). CHO: L/R LFO offset 0–180° |
| 13 | DL_SW | DLY | DLY col, **ARP** tap / layer K1 | 0 | 2 | 0 | ENUM `OFF DIG ANA` | 88 | Off: input cut, tail rings. D↔A: 32-sample crossfade |
| 14 | DL_DEPTH | DPTH | DLY col **K1** | 0 | 127 | 40 | R10 | 89 | `wet = v·258` Q15. `fb = 0.85·(v/127)·(FDBK/127)`, capped at 0.85 (R06:430) |
| 15 | DL_TIME | TIME | DLY col **K2** (SYNC = FREE) | 0 | 127 | 85 | MS | 90 | `t = 20·50^(v/127)` ms, 20–1000 ms (v = 19 → 36 ms "short", MAN p.30; v = 85 → 274 ms) (ESTIMATE). D: glide ≤ 1 sample/sample. A: one-pole slew ~100 ms plus wow/flutter (R06:420-426) |
| 16 | DL_SYNC | SYNC | DLY layer K2; PANEL K2 steps the divisions when ≠ FREE | 0 | 13 | 0 | DIV | 25 | `sync_samples(i)`, folded (`>>1`) while ≥ 65536. The display adds `!` when folded (critique #15) |
| 17 | DL_FDBK | FDBK | DLY layer K3 | 0 | 127 | 90 | R10 | 26 | See DL_DEPTH |
| 18 | DL_TONE | TONE | DLY layer K4 | 0 | 127 | 70 | R10 | 27 | Feedback LP `col = 2000 + v·240` Q15 (R06:102). ANA multiplies col by 0.6 and adds softclip |
| 19 | RV_DEPTH | REV | REV col, **K4** on PANEL | 0 | 127 | 25 | R10 | 91 | HALL: `SIZE = 40 + v·70/127`, `send = v·258` (R06:438). v = 0: send 0, tail rings. Lamp lit while > 0 (MAN p.29) |
| 20 | TONE | TONE | VOICING (EDIT) K1 | −64 | 63 | 0 | BIP | 28 | Blend of the pickup Φ tables across L_ver (−: mellow, off-axis; +: overtone) (PLAN §2) |
| 21 | BARK | BARK | VOICING K2 | 0 | 127 | 64 | R10 | 29 | Pickup drive `2^((v−64)/32)`, ×0.25..×2, dB-linear |
| 22 | BELL | BELL | VOICING K3 | 0 | 127 | 64 | R10 | 30 | O1/O2 level ±12 dB, `2^((v−64)/32)` |
| 23 | HAMMER | HAMR | VOICING K4 | 0 | 127 | 64 | R10 | 31 | Tip band shift −1..+1 band, interpolated pulse width (soft ↔ hard) |
| 24 | DECAY | DCAY | VOICING 2 (ENV) K1 | 0 | 127 | 64 | R10 (hot: `×1.00`) | 102 | T60 scale `2^((v−64)/32)`, ×0.25..×2 |
| 25 | RELEASE | REL | VOICING 2 K2 | 0 | 127 | 64 | R10 | 103 | Release T60 scale, same law |
| 26 | NOISE | NOIS | VOICING 2 K3 | 0 | 127 | 48 | R10 | 104 | 0 = off. Else damper thump and key noise at `−60 + 36·v/127` dB (R05:270) |
| 27 | SPREAD | SPRD | VOICING 2 K4 | 0 | 127 | 0 | R10 | 105 | Pan by key ±(v/127)·45° before the tremolo |

**Global params.** These live in settings, never in presets.

| id | key | label | where | min | max | def | fmt | CC/MIDI |
|---|---|---|---|---|---|---|---|---|
| G0 | BPM | BPM | SELECT (role BPM), GLO K1, PLAY tap | 400 | 2400 | 1200 | BPM10 `120.0` | MIDI clock in |
| G1 | CLOCK | CLK | GLO K2 | 0 | 2 | 1 | ENUM `INT AUTO USB` | — |
| G2 | KEYVEL | VEL | GLO K3, SELECT (role DYN) | 1 | 127 | 96 | INT | — |
| G3 | TUNE | TUNE | GLO K4 | −50 | 50 | 0 | CENT | — |
| G4 | MIDICH | CH | MIDI page K1 | 0 | 16 | 0 | `ALL`, 1..16 | in filter and out channel (ALL → out on 1) |
| G5 | CCTX | CC TX | MIDI K2 | 0 | 1 | 0 | ENUM `OFF ON` | send a CC when a param changes from the panel |
| G6 | VELCRV | VCRV | MIDI K3 | 0 | 3 | 1 | ENUM `SOFT MED HARD FIX` | MIDI-in velocity only |
| G7 | CLKOUT | CLKO | MIDI K4 | 0 | 1 | 0 | ENUM | F8 at 24 PPQN (R08:459-468) |
| G8 | SC_MODE | MODE | SCALE (SEL) K1 | 0 | 2 | 0 | ENUM `OFF SNAP DEG` | — |
| G9 | SC_SCALE | SCAL | SCALE K2 | 0 | 10 | 0 | ENUM `MAJ MIN DOR MIX LYD PHRY HARM MEL PEN MPEN BLUS` | masks `FEL/firmware/src/seq.c:16-33` |
| G10 | SC_ROOT | ROOT | SCALE K3, SEL+key | 0 | 11 | 0 | NOTE `C..B` | — |
| G11 | SC_TRANS | TRNS | SCALE K4 | −24 | 24 | 0 | SEMI | — |
| G12 | SELROLE | SELECT | MENU | 0 | 2 | 0 | ENUM `DYN BPM LAST` | (Q2) |
| G13 | ACCEL | ACCEL | MENU | 0 | 1 | 1 | ENUM | — |
| G14 | HELP | HELP | MENU | 0 | 1 | 1 | ENUM | — |
| G15 | LEDS | LEDS | MENU | 0 | 2 | 0 | ENUM `FULL DIM OFF` | — |
| G16 | SCLLED | SCALE LEDS | MENU | 0 | 1 | 1 | ENUM | — |
| G17 | COLOR | COLOR | MENU | 0 | 5 | 1 | ENUM = `PALETTES[]` (gfx.c) | — |
| G18 | MIDIQNT | MIDI QNT | MENU | 0 | 1 | 0 | ENUM | quantize MIDI in to the scale |
| G19 | HALFPED | HALF PEDAL | MENU | 0 | 1 | 1 | ENUM | CC64 continuous vs ≥ 64 |
| G20 | HUMAN | HUMANIZE | MENU | 0 | 16 | 0 | INT | ± random velocity, on-board keys |

**Other MIDI.**

| Message | Action |
|---|---|
| CC 64 | Sustain: continuous when HALF PEDAL is on (jRhodes curve, R05:109), else on at ≥ 64 |
| CC 120, CC 123 | All sound off, all notes off |
| CC 1 | Optional, MENU: adds to TW_DEPTH |
| PC 0..11 | Factory F01..F12 |
| PC 16..31 | User U01..U16 |
| Other PC | Ignored |
| CC TX values | The stored v, with switch and TYPE bands as above |

**Descriptor struct (C).**

```c
typedef struct { const char *lab4, *name; int8_t lo; uint8_t hi; int8_t def; uint8_t fmt, cc, where;
                 const char *const *names; } pdesc_t;
```

`where` packs the page (PANEL col 0–6 / VOICING / VOICING2 / SCALE / GLO / MIDI / MENU) and the knob slot. A host
test checks that every `lab4` is ≤ 4 characters (32 px), every long `name` is ≤ 13 characters, and every CC appears
once.

### 4.2 Screen layouts

**Font and canvas facts.**
- FONT_S is monospace, 8 px advance × 16 px, ASCII 32..126.
- FONT_L is 16 × 32, ASCII 32..95: capitals only, no lowercase (`felucca_font.h:388-390,1419-1421`).
- So a row holds **30 characters of S** or **15 of L**.
- The canvas is ≤ 240×64 = 15,360 px (`platform.c:57`). Every element below fits one canvas.

**Colours.** `pal[]` is the palette (gfx.c):
- `C_LINE` pal[0]: rules
- `C_DIM` pal[1]: inactive
- `C_GRAY` pal[2]: labels
- `C_AMB` pal[3]: secondary
- `C_HI` pal[4]: values
- `C_ACC` = pal[4] of the selected palette: focus and hot fills, with black text on them

#### 4.2.1 PANEL (the home screen)

```
y   0 ┌──────────────────────────────────────────────┐
    2 │F03 SUITCASE*   120.0 ■ SNP PED               │ header, FONT_S
   20 ├──────────────────────────────────────────────┤ 1 px rule
   22 │TYPE│DRV │TREM│CHO │DLY │REV │VOL │            column titles (focused: inverted)
   40 │MK1 │    │TRM │CHO │DIG │    │    │  switch row A / TYPE row 0
   56 │MK2 │    │    │    │    │    │    │  TYPE row 1
   64 │    │    │ ●  │ ●  │ ●  │ ●  │    │  OFF lamp (3-way cols); REV lamp
   72 │SUIT│    │    │    │    │    │    │  TYPE row 2
   88 │BRT │    │WAH │PHA │ANA │    │    │  switch row B / TYPE row 3
  105 ├────┼────┼────┼────┼────┼────┼────┤ rule
  108 │    │AMT │DPTH│DPTH│DPTH│DPTH│LVL │  slot 1 label
  126 │    │▬▬··│▬▬▬·│▬···│▬▬··│▬···│▬▬▬·│  gauge 28×6
  136 │    │1.0 │5.0 │3.9 │3.1 │2.0 │7.5 │  value
  154 │    │    │RATE│SPD │TIME│    │    │  slot 2 label
  172 │    │    │▬▬▬·│▬▬··│▬▬··│    │    │
  182 │    │    │4.8 │2.9 │1/8D│    │    │
  201 ├──────────────────────────────────────────────┤
  204 │K1 DPTH K2 TIME K3 DRV K4 REV                 │ footer row 1: knob map
  222 │LFO:TRM FX:CHO ARP:DLY PLY:TAP                │ footer row 2: hints / messages
  239 └──────────────────────────────────────────────┘
```

| Element | x | y | w × h | Content / rule |
|---|---|---|---|---|
| Header | 0 | 0 | 240×20 | Text at y = 2. Cols 0–14 (x 2): preset `F03` / `U07` + name, then `*` if edited. Cols 16–20 (x 130): BPM `120.0` (C_HI; inverted 1 s after a change). Beat square 6×6 at (172, 7), lit on the beat when PLAY would flash (§4.4). Cols 23–25 (x 186): `SNP`/`DEG` when the scale is on. Cols 27–29 (x 218): `PED` while sustain is on (REC, latch or CC64) |
| Rule | 0 | 20 | 240×1 | C_LINE |
| Column i | 1 + 34i | 22 | 34×178 | i = 0 TYPE, 1 DRIVE, 2 TREM, 3 CHO, 4 DLY, 5 REV, 6 VOL |
| Title | col x + 1 | 22 | 32×16 | Centred. Focused column (2–4): C_ACC fill, black text |
| TYPE rows | col0 | 40, 56, 72, 88 | 32×16 | The selected type is inverted |
| Switch A / B | cols 2–4 | 40 / 88 | 32×16 | Selected: inverted. Unselected: C_DIM |
| OFF lamp | col centre −3 | 64 | 6×6 | C_HI when OFF is selected, else C_LINE outline. REV col: a lamp lit while RV_DEPTH > 0 (MAN p.29) |
| Rule | 0 | 105 | 240×1 | |
| Slot 1 | col x + 1 | 108 | 32×44 | Label y 108 (C_GRAY). Gauge at (x+3, 126) 28×6: C_LINE frame, fill `v·26/127` C_AMB. Value y 136 (C_HI, R10 or MS/DIV). Hot: value inverted for 1.5 s |
| Slot 2 | col x + 1 | 154 | 32×44 | Label y 154, gauge y 172, value y 182 |
| VOL | col 6 | slot 1 | | MASTER ADC 0..1023 → R10. Redrawn only when it moves ≥ 8 counts |
| Footer | 0 | 202 | 240×38 | Row 1 at y 204: the knob map for the focus (K2 label RATE/RESO/SPD/TIME). Row 2 at y 222: help/message (§4.2.6). **Hot readout** replaces both rows: FONT_L at y 205, centred, e.g. `TIME 274MS`, `RATE 4.75HZ`, `1/1>1/2 BUF`, for 1.5 s after the last turn |

**Value strings, ≤ 4 characters.**
- R10: `0.0`..`10.0`.
- MS: `36`..`999`, then `1.0S`.
- DIV: the names from §4.8.
- BIP: `-5.0`..`+5.0`.

#### 4.2.2 Hold layer overlay (LFO/FX/ARP held ≥ 400 ms, or a combo)

The layer replaces y = 106..199 with 4 cards, then restores the 7 columns' slot area on release.

| Card k | x | y | w×h | Content |
|---|---|---|---|---|
| k = 0..3 | 60k | 106 | 59×94 | Label at y 110 (`SW`,`SYNC`,`MODE`,`SHPE` / `SW`,`SYNC`,`FDBK`,`—` / `SW`,`SYNC`,`FDBK`,`TONE`). Value FONT_L at y 132 (≤ 3 characters: `TRM`, `1/8`, `PAN`, `3.0`). If a value needs 4 characters, use FONT_S at y 140. Gauge (x+4, 170) 51×6. `K1`..`K4` at y 180 (C_DIM) |

The footer reads row 1 `[LFO] TREM LAYER`, row 2 `LET GO: PANEL`. The column title stays inverted.

Cost: 240×94 drawn in 2 strips (240×47 each) = 45 KB ≈ 30 ms over 2 passes (ESTIMATE).

#### 4.2.3 Pages with 4 knob cards (VOICING, VOICING 2, SCALE, GLO, MIDI)

| Element | x | y | w×h | Content |
|---|---|---|---|---|
| Header | 0 | 0 | 240×20 | `VOICING` (cols 0–14), then BPM, scale and PED as on the PANEL. TYPE is shown on VOICING pages: `MK1` at cols 23–25 instead of the scale tag |
| Card k | 60k | 24 | 59×80 | Label FONT_S y 28 (≤ 7 chars, centred). Value FONT_S y 52 (≤ 7 chars, C_HI; hot = inverted). Gauge (x+4, 78) 51×6. Knob tag `K1`..`K4` y 86 (C_DIM) |
| Graph | 0 | 106 | 240×94 | 2 strips of 240×47. Per page below |
| Footer | 0 | 202 | 240×38 | As on the PANEL; hint row per page |

**Graph contents.**
- **VOICING:** the pickup curve Φ(x) for the current TONE (240 points of the generated table, C_HI), plus a velocity marker for KEY VEL. Redrawn on a TONE or BARK change.
- **VOICING 2:** a decay envelope sketch: T60 × DECAY for the last note played, and its release.
- **SCALE:**
  - degree numbers `1`–`7` above the white keys at y 112 (FONT_S, C_AMB);
  - the existing `draw_keys` keyboard at **y 140** (`frodhes.c:52-67`, 240×56): root C_ACC, scale tones C_KEY, out-of-scale C_OFF, black keys silent in DEG drawn as C_LINE;
  - the text `C DOR SNAP` at y 120 is optional.
- **GLO:**
  - BPM in FONT_L `120.0` centred at y 112;
  - 4 beat squares 16×16 at x = 72, 100, 128, 156, y 152 (the current beat filled);
  - the source `INT` / `USB 119.8` / `AUTO>USB` in FONT_S at y 176.
- **MIDI:** a monitor showing the last message received, as text at y 120 (`CC 18 = 64  TW DEPTH`), and the clock state.

#### 4.2.4 SAVE page

| Element | y | Content |
|---|---|---|
| Header | 0 | `SAVE TO USER` |
| Rows | 28 + 20r (r = 0..7) | `U01 MK1 TRM PHA DD` (FONT_S, 30 chars). The selected row is inverted. Empty slots read `U05 ----` |
| Footer | 204/222 | `[PRESETS]SLOT [OCT+]SAVE` / `[OCT-]CANCEL` or `SAVING WHEN QUIET` |

The list shows 8 rows and scrolls by 8. With 16 slots there are 2 views.

#### 4.2.5 MENU (HOME held 700 ms)

Rows are FONT_S at y = 24 + 18r, r = 0..9, laid out as `LABEL` at column 0 and the value right-aligned at column 29.

Rows:
1. SELECT ROLE
2. KNOB ACCEL
3. HELP
4. LEDS
5. SCALE LEDS
6. COLOR
7. MIDI IN QNT
8. HALF PEDAL
9. HUMANIZE
10. INFO
11. FACTORY RESET

There are 11 rows, so the list scrolls. PRESETS moves the row; K1–K4 or OCT± change the value; OCT+ on INFO or
FACTORY RESET opens it (RESET asks `SURE? OCT+`); HOME tap exits and saves. INFO shows `v<ver>`, `BAT <raw>`,
`ISR <max µs>`, `POOL <KB>`.

#### 4.2.6 Footer hint strings (HELP on: shown for 3 s on page entry, then the idle content)

Each string is ≤ 30 characters, and a test checks it.

| Page | Row 1 | Row 2 |
|---|---|---|
| PANEL | `K1 DPTH K2 TIME K3 DRV K4 REV` | `LFO:TRM FX:CHO ARP:DLY PLY:TAP` |
| PANEL, first boot | | `HOLD LFO/FX/ARP: MORE` |
| VOICING | `K1TONE K2BARK K3BELL K4HAMR` (27) | `[ENV]DECAY  [HOME]PANEL` |
| VOICING 2 | `K1 DCAY K2 REL K3 NOIS K4 SPRD` | `[EDIT]VOICING [HOME]PANEL` |
| SCALE | `K1 MODE K2 SCAL K3 ROOT K4 TRN` | `HOLD SEL + KEY: ROOT` |
| GLO | `K1 BPM K2 CLK K3 VEL K4 TUNE` | `[PLAY]TAP [GLO]MIDI PAGE` |
| MIDI | `K1 CH K2 CCTX K3 VCRV K4 CLKO` | `[GLO]PANEL` |
| Layer | `[LFO] TREM LAYER` | `LET GO: PANEL` |

Messages use row 2 for 1.5 s. Examples: `TYPE MK II`, `EXT CLOCK`, `UNDO`, `REDO`, `SAVED U05`, `SAVING WHEN QUIET`,
`PRESET F04 80S BALLAD` (cut to 30 characters).

#### 4.2.7 Redraw cost

At 12 MHz SPI, 1.5 MB/s, payload only; all figures ESTIMATE.

| Event | Elements | Bytes | Time |
|---|---|---|---|
| Knob turn (PANEL) | 1 slot 32×44 + hot footer 240×38 | 2.8 KB + 18.2 KB | 1.9 + 12.2 ms |
| Switch tap | 1 column 34×178 + LED | 12.1 KB | 8.1 ms |
| Focus change | 2 titles 32×16 + footer | 2 KB + 18.2 KB | 13.5 ms |
| TYPE turn | col 0 + hot footer | 12.1 + 18.2 KB | 20 ms over 2 passes |
| Preset load | header + 6 columns + footer | ~100 KB | ~67 ms over ~9 passes |
| Page change | full 240×240 | 115 KB | ~77 ms over ~12 passes |
| Beat square | 6×6 | 72 B | < 0.1 ms |

The scheduler draws one element per loop pass, so the worst key latency added by drawing is about one element:
≤ 12 ms (footer), ≤ 8 ms (column).

### 4.3 Button state machine

Timing constants:
- `TAP_MS` = 250 (REC, unchanged, `frodhes.c:71`);
- `HOLD_MS` = 400 (column layers, SEL peek);
- `LONG_MS` = 700 (HOME, SAVE; Felucca `btn_hold`, `FEL/firmware/src/ui_input.c:1296-1314`);
- `QUIET_MS` = 250: knob input ignored after a layer closes (R07:170).

The column below "Any other input while held" covers knobs, keys and buttons. Those make the press a **combo**: the
release is not a tap, and the layer opens at once.

| Button | Context | Tap (released < threshold, no combo) | Hold (≥ threshold) | Any other input while held | Release after hold/combo |
|---|---|---|---|---|---|
| LFO / FX / ARP | PANEL, column unfocused | Focus the column (TREM / CHO / DLY). The sound does not change | Open the column LAYER | K1–K4 edit the layer params. **Keys play normally** | Close the layer. Ignore knobs for QUIET_MS |
| LFO / FX / ARP | PANEL, column focused | Toggle the switch OFF ↔ last-on (A/B); `last` is saved | As above | As above | As above |
| LFO / FX / ARP | any page, MENU, SAVE | Go to the PANEL and focus the column (no toggle). In MENU/SAVE: cancel first | Layer, over the PANEL | — | Back to the PANEL |
| EDIT | any | VOICING. On VOICING → PANEL | — | — | — |
| ENV | any | VOICING 2. On VOICING 2 → PANEL | — | — | — |
| SEL | any | SCALE. On SCALE → PANEL | Peek: key LEDs show the scale, header `C DOR SNAP` | **Key k: ROOT = (k+5) % 12** (silent, no MIDI; R08:276). K1–K4 edit MODE/SCALE/ROOT/TRANS | Back to the previous screen |
| GLO | any | GLO page. On GLO → MIDI page. On MIDI → PANEL | — | **SELECT: BPM ±0.1** per detent | — |
| HOME | any but MENU | PANEL (cancels SAVE and dialogs) | ≥ LONG_MS: MENU | — | — |
| HOME | MENU | Exit the MENU (save settings) | — | — | — |
| SAVE | PANEL / pages | SAVE page | ≥ LONG_MS: **UNDO/REDO** the preset load (critique #11) | — | — |
| SAVE | SAVE page | Cancel → back | — | — | — |
| PLAY | any | **Tap tempo** (§4.8). Ignored with "EXT CLOCK" when the clock is locked | — | — | — |
| REC | any | Sustain on at press. Released < TAP_MS: flip the latch. ≥ TAP_MS: release and clear the latch (unchanged, `frodhes.c:69-85`) | | Keys play | |
| OCT− / OCT+ | PANEL / pages | Octave ±1, clamped ±2. Both pressed within 100 ms: octave 0 | — | — | — |
| OCT− / OCT+ | SAVE, dialog, MENU | Cancel / confirm (MENU: OCT− = back, OCT+ = open INFO/RESET) | — | — | — |
| SEQ | any | v1: no action (`SEQ: LATER` message). Phase 8: looper | — | — | — |

**Encoders.** Any non-zero detent count is input, and counts as a combo for a held button.

| Encoder | PANEL | Layer | Pages | SAVE | MENU |
|---|---|---|---|---|---|
| ALGORITHM | TYPE (clamped, no wrap; message `TYPE MK II`) | TYPE | TYPE | — | — |
| PRESETS | Browse F01..F12, U01..U16 (clamped). Loads on each detent | — | Browse (the header shows the name) | Slot | Row |
| SELECT | Role: DYN (KEY VEL ±1 + accel) / BPM (±1.0) / LAST | as PANEL | as PANEL | — | — |
| K1, K2 | Focused column DEPTH and RATE/RESO/SPD/TIME (or DIV when synced) | Layer cards 1, 2 | Cards 1, 2 | K1 = slot | Value |
| K3, K4 | DRIVE, REVERB | Layer cards 3, 4 | Cards 3, 4 | — | Value |

**Edge cases.**
- A layer's button released during the 250 ms quiet period ignores K1–K4 detents.
- The layer opens on a combo even before HOLD_MS.
- Changing pages never changes the focus; the focus is saved.
- The PANEL's default focus is TREM.

### 4.4 LED rules

Rules are evaluated once per loop pass, from state. The whole picture is built first and then written, which avoids
flicker (R07:189-190). "Dim" = `fm1_led_dim` (~1/24).

| LED | Rule |
|---|---|
| LFO / FX / ARP | Full = the column's switch is not OFF (reface lamp semantics, MAN p.28-29). Dim = OFF and focused. Off = OFF and unfocused |
| EDIT / ENV / SEL / GLO | Full on their page (GLO on GLO or MIDI). SEL full while held. Otherwise off |
| HOME | Full on the PANEL, dim in the MENU |
| SAVE | Full while the panel differs from the loaded preset (`*`). Blinks at 4 Hz while a queued save waits for quiet |
| PLAY | Beat flash (lit for 1/6 beat, 1/2 beat on beat 1; R08:543) **only** while any synced effect is on, the clock is external and locked, or clock out is on. Otherwise off. Tap feedback: full for 80 ms on each tap |
| REC | Full while sustain is on from any source (`rh_hold`). Latched: full. Held: full. CC64 half-pedal > 0: dim |
| OCT− / OCT+ | Full when octave < 0 / > 0 (unchanged) |
| SEQ | Off in v1 |
| Keys, scale OFF | Key k full while it holds a note or its note rings under sustain (per key, critique #8) |
| Keys, scale ON + SCALE LEDS | Scale tones dim, the root full, held keys full. DEG: black keys off. SNAP: out-of-scale keys off |
| Keys, SEL held | As scale ON, whatever SCALE LEDS says |
| Keys, MENU LEDS = DIM / OFF | Every "full" becomes dim / every LED off, except REC and the beat |

### 4.5 Settings record

The record is always **256 B**, saved through `settings_save` (critique #20). It is saved 2 s after the last change,
if it differs from the last saved copy (memcmp), when `rh_quiet() && fx_quiet()`.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `ver` = 1 | Migrate by version, append-only |
| 1 | 1 | `flags` | b0 edited (`*`), b1 CC TX, b2 CLK OUT, b3 HELP, b4 ACCEL, b5 SCALE LEDS, b6 MIDI QNT, b7 HALF PEDAL |
| 2 | 1 | `oct` (int8 −2..2) | |
| 3 | 1 | `preset` | 0..11 F01..F12, 16..31 U01..U16, 255 none |
| 4 | 2 | `bpm10` (LE) | 400..2400 |
| 6 | 1 | `clock` | INT/AUTO/USB |
| 7 | 1 | `midi_ch` | 0 = ALL |
| 8 | 1 | `key_vel` | |
| 9 | 1 | `vel_curve` | |
| 10 | 1 | `tune` (int8) | |
| 11 | 1 | `sel_role` | |
| 12 | 1 | `focus` | 2..4 |
| 13 | 1 | `color` | |
| 14 | 1 | `leds` | |
| 15 | 1 | `humanize` | |
| 16 | 1 | `sc_mode` | |
| 17 | 1 | `sc_scale` | |
| 18 | 1 | `sc_root` | |
| 19 | 1 | `sc_trans` (int8) | |
| 20 | 3 | `last_on[3]` | Last ON position (1/2) for TREM, CHO, DLY |
| 23 | 1 | `hints_seen` | First-run hint bits |
| 24 | 32 | `panel[32]` | Sound params id 0..31 (§4.1); 28 used |
| 56 | 32 | `undo[32]` | The undo buffer (survives a power-off) |
| 88 | 1 | `undo_valid` | |
| 89 | 167 | reserved, 0 | |

Load rules:
- Every byte is range-checked against PARAMS; an out-of-range byte falls back to its default.
- `ver > 1` loads as defaults.
- `ver < 1` cannot occur.

### 4.6 Preset record and bank

**Record: 48 B.**

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `ver` = 1 |
| 1 | 1 | `np`: params stored (28). On load, ids ≥ np take their defaults (append-only, R07:214) |
| 2 | 1 | `used` (0/1) |
| 3 | 1 | reserved |
| 4 | 12 | `name`: ASCII 32..95, space-padded, so FONT_L can show it |
| 16 | 32 | `p[32]`: raw bytes, signed stored as int8 |

**Bank.**
- 16 records = 768 B, one object in Felucca `storage.c` A/B form: a 32-byte header with magic `FRPB`, seq and CRC,
  written last and read back (R07:249-254).
- Sectors 0x97000 / 0x98000. This needs `FL_RANGE_OK` widened (`platform.c:71`), the one platform change of Phase 7
  (R07:434-436).
- Mirrored in RAM, 768 B, so browsing never reads flash.

**Factory presets.** `const` records of the same layout.

**Flows.**
- **Load (PRESETS detent):**
  1. If `edited` and `!undo_valid`, copy the panel to `undo` and set `undo_valid`.
  2. Copy the preset's p[] to the panel, set `preset`, clear `edited`, and show the message.
  3. Effect tails spill over (critique #23).
- **Edit:** any sound-param change sets `edited` when the panel differs from the preset (memcmp). It clears
  `undo_valid` only when the user *saves*.
- **Undo (SAVE held):** swap the panel with `undo`. Show `UNDO`, then `REDO` on the next hold.
- **Save:**
  1. SAVE → the SAVE page. PRESETS or K1 picks U01..U16.
  2. OCT+ on a used slot shows `OVERWRITE? OCT+`.
  3. A second OCT+ queues the write with the auto-name (critique #21): `TYPE3 + " " + TRM/WAH + CHO/PHA + DD/AD`,
     ≤ 12 characters.
  4. The write runs when quiet, then `SAVED U05`; `preset` becomes that slot and `edited` is cleared.

### 4.7 Factory presets

Values are stored bytes. R10 conversion: `v = round(x·12.7)`.
- `—` = the default from §4.1.
- Switch values: 0 OFF, 1 A, 2 B.
- Sources: MAN = the reface voice setting examples (pp. 30-31, text hints only; the panel pictures could not be read
  as text); ESTIMATE = mine.

| # | Name | TYPE | DRV | TW sw/dep/rate/sync/mode/shp | CP sw/dep/spd/sync/fb | DL sw/dep/time/sync/fb/tone | REV | Voicing deltas | Source |
|---|---|---|---|---|---|---|---|---|---|
| F01 | MK1 STAGE | 0 | 13 | 0/—/—/0/0/0 | 0 | 0 | 25 | — | ESTIMATE (the dry reference for A/B against jRhodes) |
| F02 | 70S PHASER | 0 | 25 | 0 | 2/83/38/0/50 | 0 | 32 | — | MAN p.30 Rd I: "phaser DEPTH … between 6 and 7" |
| F03 | SUITCASE | 2 | 13 | 1/64/64/0/0/40 | 0 | 0 | 25 | — | MAN p.30 Rd I: "tremolo … DEPTH and RATE … around 5" |
| F04 | 80S BALLAD | 1 | 0 | 0 | 1/64/50/0/40 | 1/38/85/0/90/70 | 64 | BELL 80 | MAN p.30 Rd II: "wet-sounding … chorus" |
| F05 | SLAPBACK | 1 | 13 | 0 | 0 | 2/64/19/0/60/50 | 13 | — | MAN p.30 Wr: "A.DELAY … DEPTH … 5 and TIME … 1.5" |
| F06 | BRIGHT CHORUS | 3 | 32 | 0 | 1/32/38/0/40 | 1/19/19/0/60/90 | 25 | TONE +16 | MAN p.31 CP: "chorus … light … D.DELAY … DEPTH and TIME to 1.5" |
| F07 | FUNK WAH | 0 | 51 | 2/89/64/0/0/0 | 0 | 0 | 13 | BARK 80 | MAN p.31 Clv: "wah … raise the DRIVE" |
| F08 | BARK | 0 | 76 | 0 | 0 | 0 | 13 | BARK 100, HAMR 90 | ESTIMATE |
| F09 | TAPE ECHO | 2 | 19 | 0 | 0 | 2/57/—/8 (1/8D)/100/50 | 38 | — | ESTIMATE |
| F10 | DREAM | 1 | 0 | 1/38/—/6 (1/8)/0/0 | 2/51/25/0/60 | 1/51/—/9 (1/4)/100/70 | 89 | DCAY 80 | ESTIMATE |
| F11 | RADIO | 3 | 115 | 0 | 0 | 0 | 0 | TONE +30 | MAN p.31 Toy: "DRIVE … 5 or even 10 … radio-like" |
| F12 | AM TREMOLO | 0 | 13 | 1/70/100/0/1 (AM)/90 | 0 | 0 | 25 | — | R05:226 (pre-1969 Suitcase: square, AM) |

### 4.8 Tempo, sync and scale flows

**Sync table.** In 1/96-quarter units (R08:484-488). Every name is ≤ 4 characters.

| idx | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| name | FREE | 1/32 | 16T | 1/16 | 8T | 16D | 1/8 | 4T | 8D | 1/4 | 4D | 1/2 | 1/1 | 2BAR |
| units | 0 | 12 | 16 | 24 | 32 | 36 | 48 | 64 | 72 | 96 | 144 | 192 | 384 | 768 |

The hot readout gives the long names: `1/8 DOTTED`, `1/16 TRIPLET`. Display order is short to long, so turning
clockwise makes things slower for a delay. For the LFO, `RATE` turned clockwise makes it **faster**, so the TREM/CHO
K2 walks the table backwards: 2BAR → 1/32. One rule: **clockwise = more effect / faster / longer echo**.

**Tempo flows.**
1. **SELECT (role BPM) or GLO K1.** ±1.0 BPM per detent, with acceleration. Header BPM inverted for 1 s.
   `beat_samples_v = 26,460,000 / bpm10` (32-bit, R08:432).
2. **GLO held + SELECT.** ±0.1 BPM.
3. **PLAY tap.**
   - Taps 1 and 2: LED blink only.
   - From the 3rd tap: `bpm10 = 600000·(n−1)/(t[n−1]−t[0])`, over the last 4 taps; 2 s without a tap resets
     (R08:434-443).
   - Synced LFOs re-phase to 0 at this tap.
   - The hot readout shows `TAP 118.5`.
   - Ignored with the message `EXT CLOCK` when CLOCK is USB, or AUTO and locked.
4. **MIDI clock (AUTO/USB).**
   - Stamp each F8 with `fm1_micros()` while draining `midi_get`.
   - Average over 24 pulses with a one-pole, and reject dt outside 10–70 ms (R08:446-458).
   - Lock after 24 good pulses: header `USB`, the BPM tracks.
   - Unlock 500 ms after the last pulse: back to INT at the last BPM; message `CLOCK LOST`.
   - FA/FB/FC re-phase synced LFOs to 0 on FA, and are otherwise ignored (no transport in v1).
5. **Delay sync.** With DL_SYNC ≠ FREE, `delay = sync_samples(i)`, halved while ≥ 65536 (the cell shows `!`). A BPM
   change glides D.DLY over ≤ 1 sample/sample, and A.DLY through its slew, so you hear the tape pitch bend.
6. **LFO sync.** `inc = 0xFFFFFFFF / sync_samples(i)`, recomputed only on a BPM or knob change and published as a
   volatile.

**Scale flows.**
1. **SEL tap → SCALE page.** K1 MODE (OFF default), K2 SCALE, K3 ROOT, K4 TRANS. The graph shows the keyboard with
   the scale.
2. **SEL held + key.** ROOT = that key's pitch class. The key makes no sound and sends no MIDI. Message
   `ROOT D  DOR`. Works from any screen.
3. **Note mapping.** `kb_pitch` is ported from Felucca (`FEL/firmware/src/seq.c:132-163`):
   - SNAP rounds down, with TRANS applied *before* the snap.
   - DEG uses C4 = root and black keys silent, with TRANS and the octave applied *after*.
   - `held[k]` stores the resulting note.
   - `cnt[note]` counts references: a second key on the same note does not retrigger, and the first key-up does not
     cut it.
4. **A change while keys are held.** Held notes keep sounding until their key-up, which ends exactly the note the key
   started (`held[k]`, as `frodhes.c:111-118` does now).
5. **MIDI in.** Not quantized unless MIDI IN QNT is on.
6. **Header.** `SNP` or `DEG` while MODE ≠ OFF.

### 4.9 Code placement and phase gates

- **Phase 4:**
  - `params.c` (§4.1, format and turn functions);
  - `ui.c` (§4.2–4.4, the scheduler from critique #7);
  - settings v1 (§4.5);
  - the `tests/host.c` extensions;
  - scripts 1–7 and 15–19 below.
  - Effects are stubs: params are stored, the screen and CC TX work, and the audio is unchanged.
- **Phase 5:**
  - `fx.c` wired to the params, with the §4.1 laws;
  - `fx_quiet()`;
  - the spillover and crossfade rules;
  - scripts 8 and 20.
- **Phase 6:**
  - `tempo.c` and `scale.c`;
  - the per-key LEDs;
  - scripts 9–14.
- **Phase 7:**
  - `presets.c`, the bank and the `FL_RANGE_OK` change;
  - PC, CC RX and TX, the half-pedal;
  - scripts 21–24.

### 4.10 Host UI tests

**Harness extensions to `tests/host.c`** (Phase 4, task 1). New script commands, one per line as now:

| Command | Effect |
|---|---|
| `T down ID` / `T up ID` | Sets or clears the bit in `fm1_in.buttons`, and the press or release edge (`fm1_input_edges` returns `released`) |
| `T btn ID` | Kept: a press now and a release 50 ms later (a tap) |
| `T enc ID N` | Adds N detents to that encoder (`fm1_enc_take`) |
| `T adc N` | MASTER reading |
| `T shot NAME` | Writes `build/host/<NAME>.ppm` → PNG |
| `T dump` | Calls `app_dump()` (host only), which prints `P TYPE=0 DRIVE=13 … G BPM=1200 … FOCUS=2 PAGE=PANEL` |
| `T expect KEY=VAL` | Compares against the last dump. Prints `PASS`/`FAIL`, and the exit code counts failures |
| `T clock BPM10 MS` | Generates F8 at the exact fractional period for MS ms (Bresenham over µs) |
| — | `fm1_led_key` logs `LED id 0/1/dim` changes. `HOST_STORE=path` persists the settings and the preset bank between runs. `HOST_BLITMAX=1` prints the largest per-pass blit |

**Scripts to add under `tests/scripts/`, with their checks.**

| # | Script | Steps | Checks |
|---|---|---|---|
| 1 | `ui_panel_boot.txt` | boot; shot | PNG of the 7 columns. `expect FOCUS=2 TW_SW=0`. Lint: no text outside its cell |
| 2 | `ui_focus_switch.txt` | `btn 5` (LFO, focused) → TREM on; `btn 5` → OFF; `btn 2` (FX) → focus CHO only; `btn 2` → CHO on | TW_SW 1→0 with **no** WAH in between; CP_SW stays 0 after the first FX tap; `LED 5` full/off |
| 3 | `ui_layer.txt` | `down 10` (ARP), wait 500, `enc 2 1` (K1: SW → DIG), `enc 3 9` (K2: SYNC → 1/4), `up 10`, `enc 2 5` at +100 ms | DL_SW=1, DL_SYNC=9; the K1 detents in the quiet period are ignored (DL_DEPTH unchanged); shot of the layer |
| 4 | `ui_type_keeps_fx.txt` | set TREM on with depth 80; `enc 1 3` (TYPE → BRT) | All effect params unchanged; TYPE=3; message `TYPE BRIGHT` |
| 5 | `ui_knob_clamp.txt` | for each K and SELECT: `enc ±300` | Every param stays within min..max; DIV and ENUM never wrap |
| 6 | `ui_tap_tempo.txt` | `btn 12` at 1000, 1500, 2000, 2500 | BPM=1200 after the 3rd and 4th tap; a tap at 5000 starts over |
| 7 | `ui_bpm_select.txt` | role BPM: `enc 0 5`; `down 7` `enc 0 3` `up 7` | BPM=1250 then 1253 |
| 8 | `ui_delay_sync_fold.txt` | DLY sync 1/1 at 120 BPM; shot | The cell shows `1/2!`; the WAV echo is at 1.000 s ±1 ms (needs Phase 5) |
| 9 | `ui_clock_follow.txt` | `clock 1000 3000` (100 BPM), then stop | BPM=1000 ±5 within 2 beats; `CLOCK LOST` and INT at 500 ms after the last pulse; PLAY tap during the clock → `EXT CLOCK` |
| 10 | `ui_scale_snap.txt` | MODE SNAP, C MAJ; keys C#4 (k8), then C#4 + C4 | MIDI out notes 60 (not 61); second key: no retrigger; release order: the note ends on the last key-up |
| 11 | `ui_scale_degrees.txt` | MODE DEG, D DOR; press every white key | MIDI out matches Felucca's `scale_test.c` EXPECTED table (ported); black keys silent |
| 12 | `ui_scale_root_combo.txt` | `down 3` (SEL), keys E4, `up 3` | ROOT=4; **no** MIDI out; key LEDs show the scale while SEL is held |
| 13 | `ui_scale_leds.txt` | SNAP, hold C#4 | LED log: k8 lit (the key pressed), not k7 |
| 14 | `ui_select_dyn.txt` | role DYN: `enc 0 -40`; play C4 | MIDI out velocity = 56 |
| 15 | `ui_settings_roundtrip.sh` | run 1: change 10 params, wait 3 s silent; run 2 with the same `HOST_STORE` | Dump equal; record length 256; `ver=1` |
| 16 | `ui_save_quiet.txt` | hold a chord with REC latched + reverb 127, change a param | No `settings saved` line until `fx_quiet` (Phase 5: tail below −66 dBFS) |
| 17 | `sustain.txt` | existing | Unchanged behaviour (regression) |
| 18 | `ui_lint` (host-only main) | Iterate PARAMS, sync names, hint strings, preset names | `text_w ≤` its cell (32 px for cells, 240 for footer rows, 12 characters for names); CCs unique; defaults within range |
| 19 | `ui_redraw_budget.txt` | page changes, preset loads, fast knob turns | `HOST_BLITMAX` ≤ 240×64×2 B per pass; no pass has > 1 element |
| 20 | `ui_fx_switch_click.txt` | Delay on, play, switch OFF mid-tail; D↔A swap | WAV: the tail continues after OFF; no sample step > 2000 at the swap (Phase 5) |
| 21 | `ui_preset_browse_undo.txt` | edit DRIVE; `enc 6 +5`; `down 9` 800 ms `up 9` | Panel == the edited panel (not F05); a second hold → F06 |
| 22 | `ui_preset_save.txt` | SAVE, slot U03, OCT+, notes ringing | `SAVING WHEN QUIET`, then saved; name `MK1 TRM`; header `U03` |
| 23 | `ui_midi_cc.txt` | `midi 0xB0 17 100`, `midi 0xB0 90 0`, `midi 0xB0 80 70`, `midi 0xC0 3 0` | TW_SW=2 (WAH); DL_TIME=0; TYPE=2; preset F04 loaded |
| 24 | `ui_cc_tx.txt` | CC TX on; `enc 4 3` (K3 DRIVE) | MIDI out `B0 81 v` once per change, rate-limited to ≥ 10 ms |

`tests/run_host.sh` gains a loop: it runs every `tests/scripts/ui_*.txt`, keeps the PNGs, and fails on any `FAIL`.

---

## Open questions for the user

1. **Seven columns.** Should the PANEL show all 7 reface columns, including a read-only VOLUME meter for MASTER
   (recommended), or keep TYPE+DRIVE merged and drop VOLUME as the plan does?
2. **SELECT role.** The FM-1 keys have no velocity. Should SELECT default to **DYN** (on-board key velocity, so the
   left hand rides the bark; recommended) or to **BPM**, as the plan, R07 and Felucca have it? Tempo stays on PLAY
   tap and the GLO page either way.
3. **reface CP CC map.** Adopt the reface CP's CC numbers (17–19, 80–81, 85–91; recommended) in place of the plan's
   92/93/94?
4. **Switch gesture.** Tap the focused column = OFF ↔ the last position, with A/B chosen in the hold layer
   (recommended). Or a strict cycle like the plan, or a different gesture?
5. **TYPE list.**
   - Are four Rhodes types right (MK I, MK II, SUITCASE, BRIGHT)?
   - Should any type change the tremolo's character, as the reface does for Wr/Clv/Toy (volume instead of pan)? The
     recommendation is no: TREM MODE is a preset param.
6. **Button map.** Keep the printed-label semantics (LFO = tremolo, FX = chorus/phaser, ARP = delay, as the plan
   does), or map the top button row (`FX SEL ENV LFO EDIT GLO`) left to right onto the six columns, matching the
   screen? The second matches the screen but loses SEL/EDIT/GLO as page buttons.
7. **Preset browsing.** Is "loads on every detent, with an undo back to your edit" acceptable? Or should PRESETS only
   preview the name, with a press-to-load step (there is no encoder push, so a button would confirm)?
8. **User presets.** 16 slots need one platform change (widening `FL_RANGE_OK`) and two flash sectors. Is that OK?
   Or would 4 slots inside the 256 B settings record, with no platform change, be enough?
9. **SEQ in Phase 8.** A MIDI phrase looper (as on the reface CS/DX; recommended) or the arp?
10. **Delay length.** A free TIME of 20–1000 ms, with synced values folded to fit the 1.49 s buffer and shown with
    `!` (recommended). Or clamp the free TIME to 1.49 s and hide the divisions that don't fit?
