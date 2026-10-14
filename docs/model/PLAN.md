# Frodhes implementation plan (v1)

> **Superseded where they differ by [IMPLEMENTATION.md](IMPLEMENTATION.md) (v2)**, which merges this plan with the four
> reviews in [reviews/](reviews/).

This plan pulls together the eight research files in [`research/`](research/). It covers a realistic
physically-modelled Rhodes, a reface CP-style effects chain and panel, tempo, scales and presets, all on the
M-VAVE FM-1. References like (R03 §Pickup) point to `research/03-…md`, section *Pickup*.

**Hard constraints** (see [RULES.md](../RULES.md)):
- integers only: no float, no 64-bit division;
- audio at 44.1 kHz in 256-frame blocks, with **< 2.9 ms per block** for everything;
- about 96 KiB of RAM, about 336 KiB of pool, and a 256 B settings sector;
- GPL-3.0-only.

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Modal synthesis, not finite differences or port-Hamiltonian.** Each tine is a small bank of 2-pole resonators with host-computed per-key coefficients. | Finite differences cost 0.5–5 G ops/s for 8 voices (R01, R02). Port-Hamiltonian needs a Newton solve every sample, and its passivity is lost in fixed point anyway (R03). Every source converges on modal synthesis. |
| D2 | **The pickup is the physical heart.** It is a nonlinear flux function Φ(x) of tip displacement, and the output is its derivative, **y = Φ(x[n]) − Φ(x[n−1])**, read from a host-generated LUT. | "The distinctive evolution of the tone is due to the pickup only" (R03). The bark and the two spikes per cycle come from the pickup weighting (R01). The antiderivative form needs no separate velocity and band-limits mildly (R03). |
| D3 | **Use measured partial ratios, not uniform-beam ratios.** Use ~7.1·f0 and ~20·f0 (SLDV measurement, quoted in R02; EP-MK1 uses 7 and 20, R05), not the cantilever's 6.27/17.5. | The bare cantilever is wrong for a real tine with its tuning spring (R02, R03). |
| D4 | **The tine and tonebar are two close normal modes**, slightly detuned, each with its own decay. | That gives the beating and double decay. The tonebar was the authors' own "most critical" omission (R03). |
| D5 | **Exact pole placement** (impulse-invariant r = e^(−σ/fs), θ = 2πf/fs), with **Q30 coefficients and int32 state using a 64-bit multiply**. | The trapezoidal rule detunes upper modes by 7–25 % (R03). Q15 can't represent T60s of tens of seconds, where 1 − r ≈ 3·10⁻⁶ (R05). |
| D6 | **The hammer is a host-precomputed force pulse.** Its width depends on the hammer-tip hardness band per register; its amplitude follows a dB-linear velocity law (30 dB default). | The Hunt–Crossley contact is too stiff for fixed point (R02). Velocity should drive hammer strength, so the bark appears through the pickup (R05). |
| D7 | **Per-key tables come from measurements.** Fit T60s, release times and partial levels to the jRhodes3d recordings (analysis only), then generate C tables with a host script. | No source gives real per-key decay data. A constant Q is 2.5× too long in the bass and 2× too short in the mid (R05). |
| D8 | **Effects chain = reface CP order:** DRIVE → TREM/WAH → CHO/PHA → D.DLY/A.DLY → REVERB → master. Port Felucca where it exists. | R06. Everything recommended for porting is GPL. |
| D9 | **The UI is a reface-style PANEL with 5 columns.** One parameter table drives the UI, settings, presets and MIDI CC. | R07. This is Felucca's proven pattern. |
| D10 | **One tempo engine** (BPM, tap, MIDI clock follow/out) with **one sync table** shared by the delay, LFOs and arp. | R08. |
| D11 | **Sustain is on REC** (hold, or tap to latch), plus CC 64. | This is already implemented. |

---

## 2. The voice (signal flow)

```
note-on(key, vel)
   │  hammer pulse  h[n] = A(vel) · P_band[n·rate(key)]      (table per tip band; 1–3 ms)
   ▼
┌──────────────── modal tine (per voice, int32 Q30) ────────────────┐
│  V1  tine fundamental   f0·(1+δ_tb)   T60_1(key)     ┐ beating +   │
│  V2  tonebar mode       f0·(1−δ_tb)   T60_2(key)     ┘ double decay│
│  H1  2nd polarisation   f0·(1+δ_h)    weak coupling ← V1 (whirl)   │
│  O1  overtone ~7.1·f0   fast decay  (key-scaled level, < Nyquist)  │
│  O2  overtone ~20·f0    very fast   (dropped above Nyquist)        │
│  x = Σ w_m · y_m   (strike weights per key; tip mode shapes)       │
└───────────────────────────────────────────────────────────────────┘
   ▼  x (tip displacement, Q15, clamped to the table window)
┌──────── pickup ────────┐
│ Φ_voicing(x) LUT  →  y = Φ(x[n]) − Φ(x[n−1])  ×  g(key) │  (TONE knob blends 2 tables)
└────────────────────────┘
   ▼
 RLC load biquad (~5 kHz resonance) → per-voice level → mix
note-off: crossfade each mode's decay to release T60(key) over ~5 ms; damper thump
          (−36 dB half-sine into V2); optional LFSR felt noise. Sustain holds it all.
```

- **Mode count:** the cost scales with modes. Start with V1+V2+O1 (3 resonators), then add H1 and O2 if the
  budget allows (§6). Modes above about 18 kHz are never instantiated.
- **Hammer bands:** the Rhodes manual gives 5 tip hardnesses (30/50/70/90 duro, then wrapped wood). Shorter
  contact means a brighter excitation (R05).
- **Pickup geometry:** the voicing is set by L_hor (gap: 1/16" bass, 1/32" mid; R05) and L_ver (axis offset;
  about 0.5 mm is "ideal", 0 gives "pure overtone", 1.5 mm gives "pure tone"; R03). The code's geometry gives
  the strong non-monotonic curve and the paper's is almost linear (R03): use the code's family.
- **Aliasing:** a hard hit through Φ makes harmonics up to Nyquist. If the host tests show audible aliasing in
  the top 2 octaves, run 2× oversampling of the pickup stage only (R01, R03), or keep Φ smooth.
- **Velocity → pitch:** low notes go slightly sharp on hard hits (R05). This is a small initial pitch offset that
  decays, and is optional (phase 3).
- **Voice management:** steal the oldest released voice first, with a 1 ms fade before a retrigger, clearing the
  state only at retrigger, and skip silent voices (EP-MK1, R05).

### TYPE variants (ALGORITHM encoder)

Each type is a parameter set (pickup table pair, overtone levels, load filter, default effects), not new code:

| TYPE | Character | Differences |
|---|---|---|
| MK I | early Stage, more bell | closer pickups, higher O1 level, felt-ish tips |
| MK II | rounder, neoprene tips | wider gap, less bell, darker load |
| SUITCASE | MK I + Peterson preamp | preamp EQ (bass/treble), stereo autopan defaulted on |
| BRIGHT | "Dyno"-style | on-axis voicing, bright load, more bark |

---

## 3. Host tooling (the realism loop)

| Tool | Does | Output |
|---|---|---|
| `tools/fit_reference.py` | Analyses reference recordings: T60 (two-stage) per key and velocity layer, partial frequencies/ratios/levels, release time, attack spectrum vs velocity. | `docs/model/data/reference.json` |
| `tools/gen_rhodes.py` | Computes per-key f0, mode freqs/ratios, Q30 coefficients, decay and release factors, strike weights, hammer band, pickup Φ tables (from the R03 eq. 25 family), per-key gain normalisation and velocity curves. | `firmware/gen/rhodes_tables.h` (committed; regenerable) |
| `tests/run_host.sh` + scripts | Renders notes, chords, velocity ramps and pedal cases to WAV. | `build/host/*.wav` |
| `tools/analyze.py` | Runs the same analysis as `fit_reference.py` on our renders and reports the error against the reference. | a pass/fail report plus plots |

**Reference data:** jRhodes3d is a 1977 Mark I Stage 73: 15 roots, 3–5 velocity layers, unlooped, 44.1 kHz
(R05). Its licence string conflicts (CC0 vs CC-BY-SA vs NC), which is fine for analysis. We commit **derived
numbers only, never the audio**. The FLACs live in `docs/model/research/sources/05-…/audio/`, which is
git-ignored for size.

**Validation targets** (each becomes an automated check in `tools/analyze.py`):
1. **Tuning:** every key within ±2 cents of its target (R02 shows how easily a physical model ends up mistuned).
2. **Decay:** T60 per key within ±20 % of the fitted reference curve (about 18 s at ≤ D4, then falling to about
   1.5 s at C7; R05).
3. **Overtones:** O1/O2 decay much faster than the fundamental (R02's model failed this).
4. **Release:** T60 on note-off of about 0.5/0.4/0.3/0.2 s from bass to treble (R05).
5. **Pickup:** a pure sinusoid through Φ gives 2 asymmetric spikes per period with an offset rest position (R01).
6. **Velocity:** about 30 dB of level range, with bark (rising 2nd–5th harmonics) appearing at v ≳ 96 (R05).
7. **Listening:** A/B with the reference at matched pitch and velocity. This is the final judge.

---

## 4. Effects (reface CP chain)

The chain is mono until the tremolo and stereo from there. Each block runs per 16–32-frame sub-block for its
coefficients, with gains ramped per sample (R06).

| Column | Switch | Knobs | Implementation | Memory | Est. cost |
|---|---|---|---|---|---|
| DRIVE | none | AMOUNT | port Felucca `track_dist` (fx.c:96-114), our Q15 tanh LUT, per-TYPE bias/tone table | 12 B | 0.35 % |
| TREM/WAH | OFF·TREM·WAH | DEPTH, RATE (sync) | **new**: equal-power autopan / AM tremolo, sine↔square (Suitcase was square-ish, R05). WAH = envelope follower → resonant SVF (port `pf_svf` with a new k < 1 table) | 0.4 KiB | 0.5 % |
| CHO/PHA | OFF·CHORUS·PHASER | DEPTH, SPEED (sync) | phaser: port stereo `ph_side` (perform.c:695-739) with a free LFO. Chorus: Felucca core extended to 2 taps (stereo) | 2 KiB pool | 1.5 % |
| DELAY | OFF·D.DLY·A.DLY | DEPTH, TIME (sync) | port the fx.c delay plus a **time glide fix**. A.DLY adds wow/flutter, an LP in the feedback, saturation and a slewed time | 94–128 KiB pool | 0.7 % |
| REVERB | none | AMOUNT | port HALL (fx.c), one knob mapped to size and send. SPRING is an optional variant | 12.5 KiB pool | 1.8 % |
| master | none | (MASTER) | port `master_out`: DC block, limiter, soft clip | 40 B | 0.4 % |

The chain totals about **5 %** of real time (about 0.31 ms per block) and 110–145 KiB of pool (R06, an estimate
converted at Felucca's 1.7 % per 100 instructions; it must be measured on Frodhes).

---

## 5. UI, tempo, scales, presets

### Panel (R07)

```
+----------------------------------------+
| F03 SUITCASE TREM*      120  ●  PEDAL  |   preset (* edited), BPM + beat dot, sustain
+------+-------+-------+--------+--------+
| TYPE | TREM  |  CHO  | D.DLY  | REVERB |   switch positions; focused column highlighted
| MK I |  ---  |  ---  |  ---   |        |
| ...  |  WAH  |  PHA  | A.DLY  |        |
+------+-------+-------+--------+--------+
| DRIVE| DEPTH | DEPTH | DEPTH  | AMOUNT |   knob arcs + values
|      | RATE  | SPEED | TIME   |        |
+------+-------+-------+--------+--------+
| K1 DEPTH  K2 TIME  K3 DRIVE  K4 REVERB |
+----------------------------------------+
```

| Control | Role |
|---|---|
| ALGORITHM | TYPE (MK I · MK II · SUITCASE · BRIGHT) |
| SELECT | BPM (40.0–240.0) |
| PRESETS | browse factory then user presets |
| K1 / K2 | DEPTH / RATE-SPEED-TIME of the focused column |
| K3 / K4 | DRIVE / REVERB, always |
| MASTER | volume (platform) |
| **LFO / FX / ARP** | focus TREM/WAH, CHO/PHA or DELAY. Tap the focused one again: OFF→A→B. Hold it: all 4 of that column's params on K1–K4 |
| EDIT | VOICING page: TONE/BARK, HAMMER, BELL, VEL CURVE |
| ENV | VOICING 2: DECAY, RELEASE, KEY-OFF NOISE, TUNE |
| SEL | SCALE page: MODE, SCALE, ROOT, TRANSPOSE. Hold SEL + a key sets ROOT |
| GLO | TEMPO page: BPM, CLOCK (INT/USB), TREM SYNC, DLY SYNC |
| PLAY | tap tempo; its LED flashes on the beat |
| HOME | PANEL (hold: MENU) |
| SAVE | save preset (hold: undo the last load) |
| OCT− / OCT+ | octave ±2 (and cancel/confirm in dialogs) |
| **REC** | **sustain** (hold, or tap to latch) |
| SEQ | reserved (arp later) |

**Parameter system:** one `PARAMS[]` table of `{label, fmt, min, max, def, names, unit, cc, column/page}` in the
Felucca pattern (params.c). It drives drawing, value text, the settings record, presets and the MIDI CC map.

### Tempo (R08)

- **Units:** `bpm10` runs 40.0–240.0. `beat_samples` is published to the ISR, using 32-bit maths only.
- **Tap:** PLAY. The last 4 taps count; the tempo is set from the 3rd tap, and taps reset after 2 s.
- **MIDI clock in:** stamp each pulse in the main loop, average over one beat with a one-pole filter, reject out
  of range, and fall back to the internal clock after 500 ms without pulses.
- **MIDI clock out:** optional, off by default, scheduled at 24 PPQN with Bresenham remainders.
- **Sync table:** in 1/96-quarter units, `FREE, 1/32 … 1/4, 1/4D, 1/2, 1/1, 2BAR`. It feeds the delay TIME and
  the trem/phaser/chorus rates. Delay time glides; long delays fold down an octave to fit the buffer.

### Scales (R08)

- **Modes:** OFF (default) · SNAP · DEGREES (white keys walk the scale).
- **Scales:** MAJ MIN DOR MIX LYD PHRY HARM MEL PEN MPEN BLUES, with Felucca's masks (seq.c:16-33).
- **Note handling:** a per-note reference count, so two keys snapping to one note don't retrigger or cut each
  other. MIDI IN is not quantised.
- **LEDs:** dimmed for scale tones and full for the root.

### Presets (R07)

- **The panel is the sound:** about 40 B in the 256 B settings record, saved when silent.
- **Factory presets:** 8–16 const tables in flash.
- **User presets:** 16 × 48 B in an A/B torn-write-safe sector pair (port Felucca `storage.c`), saved only when
  silent.
- **MIDI:** Program Change selects presets. CCs: 64 sustain, 91 reverb, 92 trem depth, 93 chorus depth, 94 drive,
  and the rest from the table.

---

## 6. Budget

| Item | Est. ops/sample | 8 voices / chain | Memory |
|---|---|---|---|
| 3 resonators (V1 V2 O1) | ~15–20 | | per voice ~60 B RAM |
| + H1 + O2 (full) | +10–12 | | |
| hammer pulse (contact only, 1–3 ms) | ~3 | | 5 bands × 256 × Q15 = 2.5 KiB flash |
| pickup LUT (2 tables, lerp) + diff | ~12 | | 8 voicings × 1024 × Q15 = 16 KiB flash |
| RLC biquad + gain + mix | ~8 | | |
| **voice total** | **~40–55** | **~14–19 M ops/s** | per-key tables ~10 KiB flash |
| effects chain | | ~5 % of real time (R06 est.) | 110–145 KiB pool |
| UI drawing buffer | | | CV in pool (240×64×2 = 30 KiB) |

**The AC791N clock and multiply latency are not established by any source.** Phase 0 measures them. If 8 voices
of the full mode set don't fit, the order of what to drop is: O2 → H1 → polyphony 8→6 → the pickup's 2×
oversampling.

---

## 7. Phases

Each phase ends **working on the host and on the FM-1**, with its checks passing, before the next one starts.

**Phase 0: device baseline**
- Toolchain, SDK and Docker set up. `./build.sh` → install → run the current placeholder.
- Benchmark firmware: N Q30 resonators per block, timed with `fm1_ticks()`. Record cycles per resonator-sample,
  per LUT lerp, and per 64-bit multiply in `docs/model/BUDGET.md`.
- Check the printed button labels (SEL vs SCL) against `docs/API.md`.
- **Done when** the measured budget table replaces the estimates in §6.

**Phase 1: reference data and generators**
- Download the jRhodes FLACs (git-ignored). Run `fit_reference.py` → `reference.json`.
- Write `gen_rhodes.py` → `rhodes_tables.h`, and `analyze.py` with checks 1–6 from §3.
- **Done when** the reference curves are plotted and the generator output is deterministic.

**Phase 2: voice core** (replaces the placeholder inside `rhodes.c`; the interface is unchanged)
- V1+V2+O1 modes, the hammer pulse, the Φ pickup, the RLC load, the dB velocity law, the damper crossfade and
  the sustain interaction.
- **Done when** checks 1–6 pass on the host and 8 voices fit the measured budget on the device.

**Phase 3: realism layer**
- H1 polarisation and whirl, O2, register voicing (tip bands, pickup gap), the damper thump, key-down thump,
  felt noise, velocity→pitch in the bass, and the TYPE variants.
- Pickup 2× oversampling if aliasing is audible.
- **Done when** an A/B listening pass against the reference is signed off by the user.

**Phase 4: parameter system and panel UI**
- The `PARAMS[]` table, PANEL drawing (draw on change), encoder/button mapping, the VOICING pages, settings
  persistence and host UI tests (screen PNGs).

**Phase 5: effects chain**
- DRIVE → TREM/WAH → CHO/PHA → DELAY → REVERB → master, in that order. Each block is profiled on the device as it
  lands.

**Phase 6: tempo and scales**
- The tempo engine, tap, MIDI clock in/out, the sync table wired to the effects, the beat LED, and scale modes
  with the LEDs.

**Phase 7: presets and MIDI**
- Factory presets, the user preset bank, Program Change, the CC map.

**Phase 8 (optional)**
- Arp and strum on SEQ, a browser editor (needs a SysEx hook before `ota_service()`), and a first release to the
  hub (`FM-1_95N`; check the ID is free).

### Code layout (still one compilation unit: `frodhes.c` includes the rest)

```
firmware/src/frodhes.c     app: main loop, routing of keys, MIDI and buttons
firmware/src/rhodes.c      voice engine (interface unchanged: rh_note_on/off, rh_pedal, rh_render...)
firmware/src/fx.c          effects chain (fx_render, called from app_audio after rh_render)
firmware/src/params.c      PARAMS[] table, formatting, settings record
firmware/src/ui.c          panel and pages drawing, control mapping
firmware/src/tempo.c       BPM, tap, MIDI clock, sync table
firmware/src/scale.c       scale masks, SNAP and DEGREES mapping
firmware/src/presets.c     factory table, user bank (A/B sectors)
firmware/gen/rhodes_tables.h   generated by tools/gen_rhodes.py
```

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| The CPU budget is unknown (clock unverified) | Phase 0 measures it first, and §6 gives the drop order |
| Weak pickup parameters (sources disagree; R03) | Expose the voicing as TONE, fit by ear and by `analyze.py` against the reference |
| No published per-key decay data | Fit from the jRhodes recordings (Phase 1) |
| Pickup aliasing | Smooth Φ; 2× oversampling of the pickup stage only |
| Fixed-point limit cycles in lightly damped modes | Round toward zero, end voices below −96 dB |
| Flash erase stalls audio | Save only when silent (as now); A/B sectors |
| The Yamaha reface pages were unreachable (403) | The chain comes from the public product/feature pages; check against a reface manual |
| Licences | Port only GPL/MIT code (Felucca, STK). Never use the rdpiano ROMs or the EP-MK1/Sønderbo code (ideas only). Commit derived numbers, never reference audio |

## 9. Open questions for the user

1. **MIDI note range:** 73-key (E1–E7) or 88-key (A0–C8) behaviour for MIDI input? The FM-1 keys cover 27
   notes plus octave shift.
2. **Polyphony target** if the budget is tight: 8 voices with fewer modes, or 6 voices with the full mode set?
3. **Reference instrument:** are the jRhodes3d recordings (Mark I 1977) enough, or do you have recordings of a
   specific Rhodes to calibrate against?
4. **WAH:** keep it in the TREM/WAH column as on the reface, or replace it with a second tremolo shape?
