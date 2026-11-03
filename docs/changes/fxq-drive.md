## New

- **The EQ before the drive** (`firmware/src/fx_eq.c`). The chain is now COMP -> EQ -> DRIVE.
  - Bands: a LOW shelf at 120 Hz, a MID peak (Q 0.9) at MID FREQ 200 Hz..5 kHz, a HIGH shelf at 5 kHz.
  - Gains: -15..+15 dB, 15/63 dB a step, 64 = 0 dB (0 counts as 1).
  - Filters: RBJ biquads in the well-conditioned form (1 - cos), direct form I with error feedback, Q28 coefficients.
    The tables are from `tools/gen_fx_tables.py`.
  - Glides: a knob glides 2 quarter-steps per 32-sample sub-block, with the coefficients ramped per sample.
  - Flat: every gain at 64 returns before any loop, so the output is the input bit for bit, at 0 us. A band that glides
    back to 0 dB rests again once its output meets its input.
  - Page and controls: one page (LOW, MID, MID FREQ, HIGH) between COMPRESSOR and DRIVE. CC 34-37.
  - Records: preset v3 `p[63..66]`, settings `panel3[15]` + `panel4` / `undo4` (taken from `seq_rsv`), `flags2` bit 2.
    Old records load it flat.
  - Cost: 34 us per band running (`eq_df1`, 21 instructions a sample, x 1.6), +22 us per band while it glides; 102 us
    with three bands; 0 flat.

## Changes

- **FDRV at 2x** (`firmware/src/drive.c` `dr_os2`). The classic drive's curve now runs at 88.2 kHz between a
  polyphase IIR half-band up and down (`tools/drive_gen/halfband.py`, two allpass sections a branch, `DRV_HB_NC 4`).
  - The high-pass, the gain and its ramp, the low-passes, the make-up, TONE and MIX are unchanged at 1x. The level per
    AMOUNT and the low band are unchanged.
  - Folded energy, worst note of the C5..C8 sweep at AMOUNT 127, TONE 64: -12 dBFS in, -30.6 dB becomes -58.5 dB;
    -6 dBFS, -21.1 becomes -38.2 dB; -2 dBFS, -18.0 becomes -29.5 dB.
  - The half-band's group delay is 1.8 samples at 88.2 kHz each way, about 42 us at 1 kHz. There is no block of added
    latency.
  - The 2x state rests at the curve's bias. A rest at 0 made a ~250 LSB click at each start, and again after each
    silence reset. It was found by `test_drive_tail` and fixed before commit.
  - Two compile-time options were measured and are off. `DRV_HB_NC 6`: 5.6 dB better at -12 dBFS, the same from -6 dBFS
    up, +56 us a block. `DR_OS_ADAA 1`: ADAA-1 at 2x without a divide (clz, reciprocal table, one Newton step, a 32-bit
    modular integral): -59.5 dB at -6 dBFS, about +105 us a block. Neither fits core 0's budget with the EQ.
  - Cost: `FX_US_DRIVE` 80 becomes 248 (155 instructions a sample on the longest path: front 20, up 25, curve 2 x 23,
    down 34, TONE/MIX 30; 139 at the defaults, 222 us).
- **SPREAD's side follows FDRV's delay** (`fx.c` `fx_spread`). The driven mono is 1.84 samples later than before; the
  side takes z^-1 and a first-order allpass (within -32 dB of the half-bands to 8 kHz), in FDRV's wet share, so L / R
  stay M +- S at every MIX and through the fades. Unaligned, C7 at SPREAD 127 and DRIVE 90 came out +7.2 dB against
  +15.9 dry (before 2x: +14.4); now +14.7. Off, the side is the same integers as before. Cost +20 us (`FX_US_SPAL`), only
  with SPREAD and FDRV on.
- **KHLON's anti-aliasing on** (`KH_AA 1`). Folded energy at -12 dBFS: TONE 64, -11.9 becomes -31.8 dB; TONE 127,
  -18.3 becomes -33.2 dB. `DRV_US_KHLON` 129 becomes 177 and `DRV_US_MAX` 180 becomes 192. Its block scratch shares
  `drv_scr` with FDRV's 2x buffer (-2 KB RAM).
- **TSH8, OHCD, BHLUE stay at 1x** with their ADAA-1. At 2x each kernel would cost about twice its 106-165 us, plus
  about 95 us for the filters: over core 0's budget.
- Everything runs in the chain on the one core, 10 voices on every engine.
- Measured on the FM-1 (`tools/hil_cpu.py --worst`, every effect, the EQ's three bands running), whole ISR worst:
  Rhodes 4080 us, VIBES 4344 us, MARIMBA 4436 us (gate 4500).

## Fixed

- An EQ table read past its end (`eq_lerp` at MID 0: the mirrored position 512 read `FX_EQ_A[129]`), found by the
  monkey's sanitizer seed 0 before commit. The value was never used (the step's fraction is 0); now not read.

## Tools

- `tools/hil_cpu.py`: CC `EQ_LO` .. `EQ_HI` (34-37); the heavy scenarios set LOW 127, MID 0, HIGH 127 (three bands
  running); `--fx-each` has an `eq` row.
- `tools/drive_gen/halfband.py`: the half-band design (Valenzuela-Constantinides), 4 or 6 coefficients.
- `tools/manual_shots.py`: the EQ page shot (`docs/manual/img/eq.png`); every page counter is one higher.
- `tools/gen_move_module.py`: the EQ is FM-1 only for now (CC 34-37 MIDI-only on the Move).
- **The Move (hand-off to the Move session, which owns the module).**
  - The DSP is shared: `fx_eq.c` runs in the Move build as on the FM-1 (`fx_process`, COMP -> EQ -> DRIVE). FDRV's 2x,
    KHLON's AA and SPREAD's alignment are in the shared `drive.c` / `fx.c` too, so the Move's drive renders change the
    same way.
  - Ids: `P_EQ_LO`, `P_EQ_MID`, `P_EQ_FREQ`, `P_EQ_HI` = 63..66, CC 34-37, default 64 (flat). Id 63 is panel3's last
    byte; 64.. are in `panel4` / `undo4` and preset `p[64..]`. `P_SLOTS` is 80.
  - State: `move_bridge.h` `mb_state_set` takes a base of 80 ids or the old 64; an old state (no `flags2` bit 2) loads
    the EQ flat. `mb_user_save` writes every slot up to `P_SLOTS` (the zeroing of `rsv2` is gone: those bytes are
    `p[64..]` now).
  - UI: no Move page yet. `tools/gen_move_module.py` lists the EQ in `FM1_ONLY_PAGES` and its four ids in `MIDI_ONLY`;
    `move/module/help.json` lists CC 34-37. To add the page, drop those entries and regenerate; flat, the output is the
    same bit for bit as before.
  - CPU: the EQ costs 0 flat and up to 102 us a block with three bands (FM-1 instruction model). FDRV's `FX_US_DRIVE`
    is 248 (was 80). The Move's own CPU check (`move_multi_cpu`) has not been run.

## Tested

- Host, EQ (`tests/py/test_eq.py`, harness `tests/c/eq_test.c` under ASan + UBSan, 17 tests):
  - Flat (any MID FREQ) is the input bit for bit, at cost 0.
  - The response matches float RBJ within 0.05 dB from 20 Hz to 20 kHz on 10 settings.
  - Full-speed sweeps of LOW, MID, HIGH and MID FREQ: the energy above 3 kHz off the sine's harmonics is -98.6 to
    -106 dB, the rounding floor, with no line above -118 dB per bin (coefficients stepped once a block: -76 dB).
  - A band switched on or off under a sound: second difference <= 1.05x the steady renders'. Back at flat the output is
    the input bit for bit.
  - Tails reach exact 0 within 50 ms and stay there; silence in, silence out: no limit cycle, no DC.
  - Every knob at 0, 1 and 127, set and gliding: no out-of-range read, output finite.
  - An old v3 preset (np 63) and an old settings record (flags2 bit 2 clear, garbage in the EQ's bytes) load flat. This
    caught id 63, panel3's last byte, 0 in old records (LOW at -15 dB): now defaulted, on the FM-1 and in
    `move_bridge.h`.
  - A saved EQ comes back after a power cycle, at the record offsets.
- Host, drive (`tests/py/test_drive_os.py`, 8 tests): the half-band constants for 4 and 6 coefficients; folded energy as
  above, KHLON included; silence exact at every row and AMOUNT; tails end within 50 ms and stay 0; no click on KHLON
  crossing AMOUNT 77 or on FDRV's AMOUNT swept end to end (second difference <= 1.05x the held renders').
  `test_drive_types.py` (FDRV's hash, -58.6 dB, costs) and `test_drive_tail.py` (F05 fades from 32 LSB in 729 ms, as
  before) pass.
- Host, SPREAD (`tests/py/test_spread.py`): the image through DRIVE 90 keeps 85 % at C7; a held C7 at SPREAD 127
  through DRIVE and MIX fades has no step in the side (new test).
- Host, renders: Gate R differs only on drive-on renders and the edited scripts; Gate H differs only through the boot's
  FDRV fade (the HQ build starts with FDRV at full wet share fading out over 46 ms; with that fade removed in both
  builds the renders are bit-identical).
- Host, scripts: new `tests/scripts/eq_page.txt`; SELECT counts +1 after COMPRESSOR in `drive_select`, `fx_panel`,
  `perform`, `reverb_pages`, `state`; `make test` passes.
- Firmware: `./build.sh` passes; the image fits with the packed pulse banks (see flash-pack.md).
- FM-1: `tools/hil_cpu.py --worst` on the three engines (above); `tools/hil_crackle.py` on every engine (USB and the
  jack): 0 events. TO RE-MEASURE on the release build (the monkey's whole-ISR row, one take at 4668 us).
