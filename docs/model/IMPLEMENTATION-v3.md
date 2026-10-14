# Frodhes: implementation plan (v3)

Frodhes is a physically modelled Fender Rhodes for the M-VAVE FM-1 (JieLi AC791N, pi32v2, no FPU, JieLi
clang). This document is **self-contained**: an engineer implements from it without reading `PLAN.md` (v1),
`IMPLEMENTATION.md` (v2) or the reviews in `reviews/`. Where it copies a spec from a review it has corrected
it; where it disagrees with a review, this document wins. Research files are cited only as provenance
(`R01`–`R08` = `research/0N-*.md`). Experiments behind the numbers are in `reviews/scratch-05/` (see its README).

Marks: **ESTIMATE** = a number I chose, to be replaced by a measurement or the fit; **UNVERIFIED** = a fact
about hardware or a third party that nobody in this project has checked yet.

**Priorities, in order:** (1) a Rhodes that sounds real (physical model); (2) it runs on the FM-1; (3) a reface
CP-style panel and effects; (4) tempo, scales, sustain (done) and presets.

**Hard limits** (`docs/RULES.md`): integers only (no `float`, no 64-bit `/` or `%`; 64-bit multiply is fine);
44.1 kHz in 256-frame ISR blocks; < 2.9 ms per block for everything; ≈ 96 KiB RAM; ≈ 336 KiB pool; a 256 B
settings sector; no flash/screen/poll from the ISR; register access only in `hal/`; no constant in
0xFFC00000–0xFFCFFFFF; GPL-3.0-only.

---

## 0. What changed from v2, and why

| # | v2 said | v3 says | Why |
|---|---|---|---|
| 1 | S7: resonator kernel decided later by a bake-off (MC3 vs DF2+EF1) | **Decided: MC3 with first-order error feedback on both states.** | `scratch-05/reson_bakeoff.c`: at a −60 dB E1, MC3+EF1 gives 108 dB SNR vs 63 dB for DF2+EF1; cleaner voice end (≤ 22 LSB vs 109 LSB residue); the x²+y² amplitude estimate the amplitude→pitch law needs is 0.15 % accurate vs 13 % for the DF2 energy form. Cost +1 multiply per mode-sample. |
| 2 | S6: "cubic Hermite in s, ≥ 92 dB in every V01 case" | Hermite in s, **with a 22-bit cell fraction taken from the full 64-bit product**, Φ̃ in Q28, slope table int32. The tip is **Q25** gap. | V01 only measured Hermite in x. Measured in s: a 16-bit fraction gives 54 dB on a soft E1; 22 bits give 74 dB; the remaining floor is the tip quantisation, so the tip moves from Q24 to Q25 (80 dB). |
| 3 | S17: pitch/decay path "exp2 LUT, reciprocal table" | **Exact algorithm given** (§5.6), no division, verified: ≤ 0.14 c and ≤ 0.012 % T60 over keys 21–108 for TUNE ±50 c and DECAY ×0.25–4; ≤ 0.38 c with ±200 c bend. | It was hand-waved; a naive cos-table lerp would give 7 cents at E1. |
| 4 | Velocity-dependent contact time "about 4× over 30 dB" | A property the **host simulation produces per band**, not an assumption; the pulse player accepts T_c from 0.2 to 25 ms. | `scratch-05/hammer_contact.py`: the Hertz exponent only holds for stiff tips; soft felt on a heavy bass tine barely changes T_c. |
| 5 | 16-frame envelope blocks (V01) vs 32-frame sub-blocks (V02): unresolved | **One 32-frame sub-block grid** for voices, damper ramps, LFOs and HALL. | One grid, fewer per-block overheads; HALL needs CTL = 32 anyway. |
| 6 | Release: "E can stay fixed through the release" | Both E and eps crossfade; the release pair comes from the 2-DOF re-solve **matched by eigenvalue continuity**. | `scratch-05/tinebar_residue.py`: with felt on the tine the mode that keeps ringing is the bar mode; matching by "late energy" would swap them. |
| 7 | Reference = jRhodes (EQ'd, denoised) | **Multi-source reference pipeline** (§7): Pianobook "Matt's Fender Rhodes" (DI, unprocessed) as the primary for decay, noise floor and spectral balance; jRhodes second; MPC-Samples "Electric Keys 2" (8 layers, Mark I Stage + Mark II Suitcase) for velocity→timbre and the SUITCASE type; user captures (e.g. of a Nord) by a capture spec. Nord `.npno` files are never decoded. | Coordinator/user update. None is downloaded yet: no fit result is claimed anywhere here. |
| 8 | Preset browsing "previews, confirms on a press" (no confirm control existed) | **Load on detent after a 400 ms settle**, undo on SAVE-hold. | No encoder push; OCT± and SAVE are taken. Settling gives browsing without loading every intermediate preset. |
| 9 | Q6 delay knobs, Q7 DEGREES root: open | **Decided**: DEPTH = wet level and feedback together (hold-layer FDBK scales it); DEGREES root on C4 (5th white key), as Felucca, with its ported test. | Taste questions with a clear default; not worth the user's time. |
| 10 | Pulse shapes: 4 velocity layers, hard switch | **8 layers**, hard switch, generator asserts adjacent shapes differ < 3 % RMS. | Removes any audible step for +2.7 KiB of flash. |
| 11 | Hot tables "copied to the pool" | Also **per-key TYPE deltas** instead of 4 full key tables; the Φ̃/slope tables for 2 gap families in the pool (32.8 KiB). | Memory map closed (§3.4). |
| 12 | Event ring: 32-bit events, drained per block | Events carry a **3-bit sub-block index** so note timing has constant ≈ 1-block latency instead of 0–5.8 ms jitter. | Cheap; V02's "optional" idea #23 made mandatory. |
| 13 | Checks C1–C11 | C1–C11 kept and made exact; **C12 waveform** (two-spike pattern on the full voice) and **C13 chord linearity** added; per-source trust rules; Gate A/B unchanged. | "Sounds like a Rhodes" also means the waveform shape and clean chords. |
| 14 | Task list "as V04 with changes" | Complete task table with dependencies, acceptance criteria and sizes, inline (§15). | Self-contained. |

---

## 1. Conventions and number formats

| Symbol | Meaning |
|---|---|
| `fs` | 44 100 Hz. ISR block `N = 256` frames = 5.805 ms. **Sub-block** `SUB = 32` frames (8 per block). |
| gap | The unit of tip displacement: the horizontal tine–pickup gap of the key's register, 1/16" (bass, wood-tip treble) or 1/32" (mid) (R05 W3). |
| Q25 gap | int32 tip/mode displacement: 1.0 gap = 2^25. Range ±64 gap; per-mode clamp ±2^28 = ±8 gap. |
| Q50 gap² | int64 squared radius `s` from Q25 × Q25. `S_MAX` = 32 gap² = 2^55. |
| Q30 | coefficients E, eps, b, hc; `mul30(a,b) = (int32_t)(((int64_t)a*b) >> 30)` (floor). |
| Q28 | Φ̃ table values and the pickup output before gain. RLC coefficients. |
| Q15 | audio, pulse shapes, levels (`±32767`). |
| Q16 | gains (`gain`, `gscale`), reciprocal scales `k`. |
| Q12 | knob gains (DRIVE), per-key pulse-rate factor. |
| ticks | `fm1_ticks()`, 24 MHz, wraps at 2^32. Never `fm1_micros()` (wraps at 178.9 s). |

All shifts of signed values are arithmetic (floor). Every 64-bit product is a widening `int32 × int32`.
Divisions appear only in the main loop on 32-bit values with a tested non-zero divisor. Hard-coded Q30
literals are forbidden in code: they live in generated tables (ROM-window rule, §3.6).

---

## 2. Decisions

### 2.1 Sound model

| # | Decision | Rationale |
|---|---|---|
| S1 | **Modal synthesis.** No finite differences or port-Hamiltonian solver at run time. | FD costs 0.5–5 Gops/s for 8 voices (R01, R02); the PHS needs a Newton solve per sample (R03). |
| S2 | **The physics runs on the host** (`tools/gen_rhodes.py`): an FE tine + tuning-spring model per key from the 73 service-manual lengths, a 2-DOF tine–bar solve, a coupled hammer–felt contact simulation, a radial pickup field. The ISR runs resonators, a pulse player and a table lookup. | The per-key ratios (7.1 at F1 falling to ≈ 6.4 at C7), strike weights and contact shapes are predictions of that physics, not hand-set numbers (`scratch-01/fe.py`). |
| S3 | **Modes per voice:** V1 = the tine–bar normal mode that dominates the late sustain, retuned exactly to f0; V2 = the other normal mode; O1, O2 = 2nd/3rd tine modes; H1 = horizontal polarisation (Phase 3). Weights are the 2-DOF residues. | Symmetric detune made the pitch drift (8.6 c at δ = 0.5 %); residues make beating, double decay and release consistent. |
| S4 | **Resonator kernel: MC3 + EF1** (§5.3): `x += E·y; y += −eps·y − E·x`, exact poles r·e^(±jθ), damping rounded toward *more* damping, first-order error feedback on both states. | §0 #1. |
| S5 | **Pickup = R03 flux family, radial:** one 1-D table in s = (x_v + off)² + x_h², per gap family (2). TONE and voicing are an **offset** on x_v, not a table blend. | Φ is even in the offset coordinate (R03 eq. 25), so an offset is a translation of one field and H1 enters quadratically (whirl, R01 Fig. 4). |
| S6 | **Pickup numerics:** tip Q25 int32, s in int64, cubic Hermite on Φ̃ (Q28) + slope table, 22-bit fraction, exact difference `H[n] − H[n−1]` (first-order ADAA). | §0 #2. The midpoint form has no ADAA and droops at high harmonics. |
| S7 | **Hammer:** run-time pulse player; shapes from a coupled contact simulation on the host (felt power law, 6 FE tine modes + bar, 16×fs), 5 tip bands × 8 velocity layers; MIDI velocity → hammer **speed** (dB-linear, R = 30 dB default, 12–36 user range). | The tine's effective mass at the strike point (21–43 g) is comparable to the hammer, so the contact is coupled (`scratch-01/meff.py`). |
| S8 | **Re-strike of a sounding note superposes** (state kept, pulse restarted). Fade-and-clear only when stealing for a different note. | A real tine is hit while moving. |
| S9 | **Damper:** per-mode (E_rel, eps_rel) from adding felt damping on the tine coordinate in the 2-DOF solve, matched by eigenvalue continuity; 7 sub-block (5.1 ms) linear crossfade of both E and eps; thump injected on the tine force. Half-pedal scales the crossfade target. | Stable: for MC3 any E ∈ (0, 2), D ∈ (0, 1) is stable, so convex combinations are. |
| S10 | **One RLC load biquad on the mono mix**, per TYPE. Default 5 kHz, Q ≈ 1 (ESTIMATE, by ear). Excluded from spectral checks. | Linear, time-invariant, identical for all keys (R03). |
| S11 | **Amplitude→pitch:** E(V1), E(V2) scale by (1 + κ·a²) per sub-block, a² = x² + y² of the mode (MC3 quadrature). Fitted from the loud vs soft layers of the bass roots. | R05: "low notes go a bit sharp when struck harder". |
| S12 | **Upper-mode attack residual** (modes ≥ 4, first 11.6 ms): a precomputed tip-displacement table per band and layer added to x_v (Phase 3). | Linear superposition; exact for a tine at rest. |
| S13 | **Tone-bar-clip step** at key 54 allowed in the per-key log-T60 interpolation. | jRhodes lengths are non-monotonic there; Vintage Vibe clips from key 54 (R05). |
| S14 | **Seeded per-key jitter** within the fit residuals (Phase 3). | 73 identical keys sound synthetic. |
| S15 | **No 2× oversampling by default.** Check C8 decides; the generator asserts the treble tip amplitude. | Measured aliasing of the ADAA form: −54 dB at C7 at 1.5 gap, −128 dB at 0.5 gap. |
| S16 | **Run-time coefficient path** (§5.6) for TUNE, bend, DECAY, RELEASE, half-pedal, amplitude→pitch: from per-key (sin θ/2, cos θ/2, θ, d, d_rel, cr) with series, no division. | §0 #3. |
| S17 | **TYPE** = deltas from the fitted '77 Stage: STAGE '77, SUITCASE, EARLY FELT, BELL/DYNO. New notes only. | MK I/MK II/BRIGHT rested on shaky history (R05 W2/W9). |
| S18 | **Float golden model** `tools/rhodes_model.py` with exactly the C structure. **Gate A:** golden vs references. **Gate B:** C vs golden ≤ −60 dB per case. | Separates model error from fixed-point error. |
| S19 | **Fit by analysis-by-synthesis** on EQ-invariant measures, per source with trust rules (§7). | References are processed differently; sample lengths are not T60s. |

Discarded (with the reason): uniform-beam ratios (tuning spring); symmetric detune (drift); H1 summed into x (wrong
physics); TONE as a table blend (translation suffices); Q15 tip/Φ (4–17 dB SNR); round-toward-zero (−28 % T60);
EP-MK1's 1/f0 hammer, 2 kHz HPF, hammer-into-pickup, waveshaper chain, constant Q (non-physical); STK Rhodey / FM
bark (aliases; low anchor only); rdpiano log envelopes (decays are poles); Felucca SVF modal bank (64-bit divisions,
13 %/voice); Pfeifle pole profile, arc trajectory (0.6 % gap change), pickup back-force (folded into the fit), K1/K3
damper spring (implicit step); DF2 (noise); midpoint pickup form (no ADAA).

### 2.2 Platform and real time

| # | Decision |
|---|---|
| P1 | **The ISR owns all voice state.** The main loop posts 32-bit events into a 32-entry SPSC ring; the ISR drains it at block start and fires each at its sub-block. `rh_lit`, `rh_quiet` read ISR-written summary words. Knob parameters are `volatile int32` (one writer), latched once per block. |
| P2 | **Budget per 256-frame block:** voices ≤ 1 900 µs, effects ≤ 500 µs, margin ≥ 500 µs (TIMER5/USB nesting, platform). Worst case = 8 bass voices struck in one block at v = 127, 5 modes, pedal down, every effect on in its heaviest variant (WAH, PHASER, A.DLY at maximum time, HALL), USB audio streaming. |
| P3 | **Hot tables in the pool** (Φ̃ and slope, 2 families, copied at boot). Per-key rows stay in flash, read at note-on only. |
| P4 | **`fxp.h`** is the only place for fixed-point helpers (§4.1); a build check proves no libcall from hot functions. |
| P5 | **ROM-window safety** (§3.6). |
| P6 | **Time base = `fm1_ticks()`**; ticks→samples = `((uint64_t)t * 7892002u) >> 32`. |
| P7 | **Flash writes** only through `app_flash_begin/end()` (§12.3): quiet voices and effects, ISR muted for 2 blocks, erase, flush lines, unmute. |
| P8 | **Profiler in the firmware** (§3.3): per-stage µs, max, mean, late and shed counters, histogram; CPU page; CC dump. **Shedder:** two blocks over 2 400 µs → fade the quietest released voice, else drop O2/H1 of the quietest held voice. |
| P9 | Block buffers are `static` (never on the 7.9 KiB ISR stack). Never `irq_save/restore` in ISR code. |
| P10 | **Draw scheduler:** at most one element (≤ 240×64 px) per main-loop pass; priority hot cell → header → footer → columns. Try `LCD_BAUD=2` in Phase 0. |
| P11 | The "no 64-bit division" rule stays. Phase 0 probes whether the toolchain inlines it (K15) so RULES.md can state the fact. |
| P12 | **Canvas stays 240×64** (30 KiB); full pages are drawn in strips across passes. |
| P13 | **User preset bank sectors: 0x97000 (A) and 0x98000 (B)** — above the app slot end (0x8E0DC), below the OTA staging area (0xE0000), not the settings sector (0xFC000). `FL_RANGE_OK` widened by an app hook. |

### 2.3 Product

| # | Decision |
|---|---|
| U1 | PANEL = 7 reface columns of 34 px: TYPE, DRIVE, TREM/WAH, CHO/PHA, DELAY, REVERB, VOLUME (read-only MASTER). |
| U2 | **KEY VEL** (1–127, default 96) is the velocity of the on-board keys; **SELECT role = DYN** by default; HUMANIZE ±0–16 optional; BPM lives on GLO and PLAY tap. Phase 0 records whether the keybed senses velocity (UNVERIFIED; `fm1_in.notes` is a bitmask today). |
| U3 | A tap on the **focused** column toggles OFF ↔ its last-used A/B; A/B is chosen in the hold layer (K1) or by CC. A tap on an unfocused column only focuses it. |
| U4 | **reface CP CC map** (UNVERIFIED against Yamaha's document; from midi.guide): 80 TYPE, 81 DRIVE, 17/18/19 TREM/WAH sw/depth/rate, 85/86/87 CHO/PHA, 88/89/90 DELAY, 91 REVERB; switch bands 0–42 / 43–85 / 86–127; Frodhes-only params on 20–31 and 102–105; CC TX off by default; 64 sustain with half-pedal. |
| U5 | TYPE never rewrites effects; it changes voice deltas and the drive voicing row. TREM PAN/AM is a preset parameter (default PAN). |
| U6 | WAH is a touch wah: DEPTH = sensitivity, K2 = RESO; SYNC does not apply. |
| U7 | LEDs per key (not per note); per-note reference counts; MIDI-in notes shown on the screen keyboard only. |
| U8 | Effect switch-off lets tails ring (input cut); preset loads crossfade; D↔A keeps the buffer and crossfades the read. |
| U9 | **Preset load on detent after 400 ms still**; SAVE-hold = undo/redo; `*` and SAVE LED while edited. |
| U10 | Settings and presets are versioned and **append-only** (rule goes into CLAUDE.md); past-version fixtures are tested. |
| U11 | MIDI clock out behind a flag (off), last in Phase 6. Browser editor after 1.0. Chord keys, arp swing out of scope. SEQ = phrase looper candidate for Phase 8. |
| U12 | REC = sustain (hold, or tap to latch). Done. |
| U13 | DEGREES root = C4 (5th white key), as Felucca; degree numbers drawn over the white keys. |
| U14 | Delay DEPTH = wet level and feedback together (reface-like); FDBK in the hold layer scales the feedback. |

### 2.4 Process

| # | Decision |
|---|---|
| D1 | Only licence-compatible code is committed (Felucca GPL-3.0, STK MIT with notice). Reference audio is analysis-only in `.local/reference/<source>/`; only derived numbers with attribution are committed. |
| D2 | Everything lives in the project folder: `.local/` holds toolchain, SDK, venv, reference audio and research sources (git-ignored), pinned by sha256 in `tools/toolchain.lock` and `docs/model/data/sources.json`. Docker images are the only exception. |
| D3 | Identities: dev = `FM-1_950`; releases 0.1–0.9 = `FM-1_951`–`959`; `--release X.0` and majors ≠ 0 refused. Hub ID checked in Phase 0 and before each release. Pre-release 0.1 after Phase 2. |
| D4 | Verification before engine work: harness verbs, golden hashes, health checks, unit tests under ASan/UBSan (`-fsanitize=integer-divide-by-zero`), MIDI fuzzer, CI (host, licence, firmware). |
| D5 | Platform deltas are override hooks (§13), listed in README, offered upstream to Dinghy. |

---

## 3. Architecture

### 3.1 Audio ISR pipeline (every 256 frames)

```c
static void app_audio(int32_t *out, uint32_t n)      /* n = 256; out is zero, L R L R, Q15 */
{
    PROF_BEGIN();                   /* t0 = fm1_ticks(); half = fm1_audio_free_half(); isr_t0 published */
    rh_events();                    /* S1 drain the ring: alloc / re-strike / steal / damper / pedal, coefficient path at note-on */
    fx_latch();                     /* S2 copy volatile params (TONE offset, TYPE, DECAY k, knobs, sync incs) into ISR copies */
    rh_render(rh_mono, n);          /* S3 voices, 8 sub-blocks of 32: modes (mode-outer) -> rh_x/rh_h -> pickup -> rh_mono (int32) */
    rh_load(rh_mono, n);            /* S4 RLC biquad per TYPE + master voice gain */
    fx_drive(rh_mono, n);           /* S5 DRIVE (mono) */
    fx_trem(rh_mono, out, n);       /* S6 TREM / WAH: mono -> stereo (autopan) ; wah is mono then split */
    fx_mod(out, n);                 /* S7 CHORUS / PHASER, stereo */
    fx_delay(out, n);               /* S8 D.DLY / A.DLY */
    fx_reverb(out, n);              /* S9 HALL, per 32-frame sub-block */
    fx_master(out, n);              /* S10 DC block, limiter, soft clip; tail peak for fx_quiet() ; mute */
    PROF_END();                     /* S11 stage times, late check, shedder request */
}
```

**Budget per block.** "Target" is the design figure (ESTIMATE until Phase 0, from V02's host-instruction counts
at Felucca's ratio of ≈ 1 µs per block per host instruction per sample); "alarm" is what the profiler flags.

| Stage | Target µs | Alarm µs | Basis |
|---|---|---|---|
| S1 events | 5 (≤ 30 with 8 note-ons: 10 coefficient evaluations each ≈ 0.3 µs) | 40 | §5.6 |
| S2 latch | 2 | 10 | |
| S3 voices, worst (8 × 5 modes, all in contact, H1, residual) | 950 | 1 900 | ≈ 13 instr/mode-sample × 5 + ≈ 55 pickup/contact = 120 instr/voice-sample |
| S3 voices, typical (3 live modes after sleep) | 550 | | |
| S4 load biquad + gain | 15 | 30 | |
| S5–S10 effects, worst variants | 330 | 500 | R06 estimates: drive 20, wah 29, phaser 87, A.DLY 41, HALL 105, master 23 + splits |
| S11 profiler | 3 | 10 | |
| **App total** | **≈ 1 300** | **2 400 (shed)** | |
| Platform (zero, master, clip, uac_tap, `<< 7`) | 50–100 | | platform.c |
| TIMER5 + USB nested | UNVERIFIED (K16/K17) | | |
| **Hard limit** | **2 900** | | RULES.md |

Phase 0 replaces every target with a measurement (`docs/model/BUDGET.md`); the alarms and the voice/FX split
are then re-cut, keeping ≥ 500 µs margin.

### 3.2 Main loop (one pass ≈ 1 ms; `platform_poll()` first)

```
poll → keys (KEY VEL / HUMANIZE, scale map, per-key refcount, held[k]) → buttons (state machine §10.3)
     → encoders (acceleration) → MIDI in (notes, CC map, PC, bend, F8 stamped with fm1_ticks())
     → tempo_poll (ticks) → params: publish volatiles / post events → LEDs (whole picture, then write)
     → draw scheduler (one element) → deferred flash write (§12.3) → loop
```

Maximum pass time: 12 ms (the largest single blit, 240×64×2 B at 1.5 MB/s = 20 ms worst; measured in Phase 0).
A host probe asserts the maximum gap between `platform_poll()` calls < 50 ms.

### 3.3 Profiler and shedder (ISR-written, main-loop-read)

```c
static volatile struct {
    uint16_t cur[12], max[12];       /* µs per stage: this block / since reset */
    uint32_t avg_q8;                 /* app total µs, one-pole over 256 blocks, Q8 */
    uint32_t blocks, late, shed;     /* late: fm1_audio_free_half() changed during the render */
    uint16_t hist[16];               /* 150 µs bins of the app total */
    uint8_t  voices, modes;          /* live counts at block end */
} prof;
static volatile uint32_t prof_reset_req;   /* main loop bumps; ISR clears max/hist on change */
```
Stage time = `(fm1_ticks() - t_prev) / 24` (a constant divisor: the compiler emits a multiply). The shedder
sets `shed_req` when the app total exceeds 2 400 µs in two consecutive blocks; the next `rh_events()` fades the
quietest **released** voice over 2 sub-blocks; if none is released, it drops O2 then H1 of the quietest held
voice (mode sleep, §5.4). Every shed increments `prof.shed`. The CPU page (§10.5) and the CC dump (§10.8)
expose it.

### 3.4 Memory map

**Flash (`.text` + `.rodata`, XIP; app slot 568 KB shared with code)**

| Item | Bytes | Note |
|---|---|---|
| `RH_KEYS[88]` (`rh_key_t`, 176 B) | 15 488 | §4.2 |
| `RH_PULSE[5][8][66]` int16 | 5 280 | |
| `RH_VAMP[5][128]`, `RH_VRATE[5][128]` uint16 | 2 560 | |
| `RH_VLAYER[128]` uint8 | 128 | |
| `RH_PHI[2][2049]`, `RH_SLOPE[2][2049]` int32 (source of the pool copies) | 32 784 | |
| `RH_RES[5][3][512]` int16 (Phase 3) | 15 360 | |
| `RH_RLC[4][5]` int32, `RH_TYPE[4]`, velocity law tables `RH_VLAW[5][128]` uint16 | ≈ 1 500 | |
| Coefficient-path tables: `TUNE_T[101]` (2^(c/1200)−1, c = −50..50, Q30), `DECAY_K[128]`, `REL_K[128]` (Q16), `PEDAL_W[128]` (Q15) | 1 160 | |
| FX tables: tanh 257 × int16, sine 1024 × int16, `WAH_SVF[64][3]` int16, `PH_A[33]` int16, `LFO_INC[128]` uint32, `CHO_D[128]` | ≈ 3 700 | |
| Factory presets 12 × 48 B; PARAMS descriptors; strings | ≈ 2 500 | |
| **Total new constants** | **≈ 80 KiB** | fits with ≈ 150 KiB of code (ESTIMATE) |

**RAM `.bss` (96 KiB; platform uses ≈ 6 KiB + USB)**

| Item | Bytes |
|---|---|
| `rv[8]` voices (`rh_voice_t` ≈ 232 B) | 1 856 |
| `rh_mono[256]`, `rh_x[256]`, `rh_h[256]` int32 (static block buffers) | 3 072 |
| Event ring 32 × uint32 + r/w | 136 |
| FX state (drive, trem/wah, cho/pha, delay pointers, HALL scalars, master, latched params) | ≈ 800 |
| Profiler | ≈ 200 |
| Params mirror, settings mirror (256), undo (32), UI state, tempo, scales, LED picture | ≈ 1 500 |
| **App total** | **≈ 7.6 KiB** |

**Pool (`.pool`, 344 064 B, zeroed at boot)**

| Item | Bytes |
|---|---|
| `cv_px` canvas 240×64×2 | 30 720 |
| `dly_buf[65536]` int16 (1.486 s) | 131 072 |
| HALL lines + diffusers + state (Felucca fx.c, CTL = 32) | ≈ 12 800 |
| `cho_buf[1024]` int16 | 2 048 |
| `pu_phi[2][2049]`, `pu_slope[2][2049]` int32 (boot copies) | 32 784 |
| Preset bank mirror 16 × 48 + sector buffer 4 096 | 4 864 |
| **Total** | **≈ 214 KiB (62 %)**; build check requires ≥ 8 KiB spare |

**Flash layout (1 MiB NOR):** app 0x000120–0x08E0DC; **user presets A/B 0x97000, 0x98000 (P13)**; OTA staging
0xE0000– (platform); settings 0xFC000 (platform). `FL_RANGE_OK` covers exactly {OTA area, settings sector,
0x97000–0x98FFF}.

### 3.5 Event ring (P1)

```c
#define EV_NOTE_ON  1u   /* note 0..127, val = velocity 1..127 */
#define EV_NOTE_OFF 2u   /* note */
#define EV_PEDAL    3u   /* val = CC64 level 0..127 (REC gives 127 / 0) */
#define EV_ALL_OFF  4u   /* val 0: all notes off (release) ; 1: all sound off (fade) */
#define EV_MUTE     5u   /* val 1: render silence (flash write) ; 0: resume */
/* word: type << 28 | sub << 25 | note << 16 | val ; sub = 0..7 sub-block within the NEXT block */
static volatile uint32_t ev_q[32];
static volatile uint32_t ev_w;      /* main loop writes */
static volatile uint32_t ev_r;      /* ISR writes */
static volatile uint32_t isr_t0;    /* fm1_ticks() at the start of the current block (ISR writes) */
```
Main loop: `sub = ((fm1_ticks() - isr_t0) * 8u) / 139320u` clamped to 7 (32-bit divide by a constant); if the ring
is full (`ev_w - ev_r == 32`) the event is dropped and `ev_drop++` (shown on the CPU page). ISR, at block start:
`while (ev_r != ev_w) fire(ev_q[ev_r++ & 31])` into per-voice pending slots; a NOTE_ON whose `sub` is already
past fires at sub-block 0. Latency = one block ± half a sub-block, constant.

### 3.6 ROM-window rule (0xFFC00000–0xFFCFFFFF = Q30 −0.0039..−0.0029, Q25 −128..−96, Q28 −0.0156..−0.0117)

- Table indexing: `i = s >> k`; fraction `s & ((1u << k) - 1)`; never `(s >> k) << k`.
- Clamp in Q15 before any left shift; clamp states at `±(1 << 28)` (0x10000000 / 0xF0000000, outside the window).
- No Q30/Q28 literal in C; coefficients come from generated tables.
- `gen_rhodes.py` asserts no 32-bit table word lies in the window and nudges offenders by 1 LSB (measured: 19 slope
  words of the current geometry would, `scratch-05/pickup_hermite_s.py`). `tools/frodhes_checks.py` rescans
  `build/text.bin` at the generated symbols.
- Phase 0 K19 plants `0xFFC00080` as data to learn whether build.py's check reads `.rodata` (it disassembles all of
  `.text`, which contains `.rodata`): if it does, the check becomes data-aware (skip symbol ranges) as a platform PR.

---

## 4. Data structures

### 4.1 `firmware/src/fxp.h`

All `static inline`, int32 arguments, widening products only.

| Helper | Definition |
|---|---|
| `mul30(a,b)` | `(int32_t)(((int64_t)a * b) >> 30)` |
| `mul28(a,b)`, `mul25(a,b)`, `mul16(a,b)`, `mul15(a,b)` | the same with 28 / 25 / 16 / 15 |
| `mul15s(a,b)` | 32-bit `(a * b) >> 15` for \|a\|,\|b\| ≤ 32768 |
| `ef30(acc, &e)` | `acc += *e; y = (int32_t)(acc >> 30); *e = (int32_t)(acc & 0x3FFFFFFF); return y;` (error feedback) |
| `sat15(v)` | clamp to ±32767 |
| `sat28(v)` | clamp int64 to ±(2^28 − 1) |
| `lerp15(a, b, f)` | `a + (((b - a) * f) >> 15)` (32-bit, \|b−a\| < 2^16) |
| `lut16(t, u)` | Q15 table of 1025 entries, `u` a Q32 phase: `k = u >> 22; f = (u >> 7) & 0x7FFF; return lerp15(t[k], t[k+1], f);` |
| `exp2_q30(frac16)` | 2^(f/65536) − 1 in Q30 for f ∈ [0, 65535]: degree-5 polynomial (coefficients from the generator; max error 1.2e-7 ESTIMATE) |
| `ticks_to_samples(t)` | `(uint32_t)(((uint64_t)t * 7892002u) >> 32)` |
| `q8_ratio(num, den)` | main loop only: `den ? (num << 8) / den : 0` (32-bit) |

A unit test compares each against exact rational arithmetic at ±32767, −32768, 0, ±1 and random values;
`tools/frodhes_checks.py` fails on any `call` from `rh_*`/`fx_*`/`app_audio` to a `__`-prefixed symbol.

### 4.2 Generated tables (`firmware/gen/rhodes_tables.h`, from `tools/gen_rhodes.py`)

```c
#define RH_KEY_LO  21            /* A0; 88 keys to C8. Keys outside E1..E7 are extrapolated (§6.1) */
#define RH_NKEY    88
#define RH_NM      5             /* V1 V2 O1 O2 H1 */
#define RH_V1 0
#define RH_V2 1
#define RH_O1 2
#define RH_O2 3
#define RH_H1 4
#define RH_NBAND   5             /* 30 / 50 / 70 / 90 duro / wood (R05 W1) */
#define RH_NLAYER  8             /* pulse-shape velocity layers */
#define RH_PLEN    64            /* pulse samples per shape, + 2 guard zeros */
#define RH_PU_N    2048          /* pickup cells over s = 0..32 gap^2 */
#define RH_PU_CELL 44            /* s (Q50) >> 44 = cell ; cell width 2^44 Q50 = 1/64 gap^2 */
#define RH_PU_FR   22            /* fraction bits: (s >> 22) & 0x3FFFFF */
#define RH_S_MAX   (1LL << 55)   /* 32 gap^2 in Q50 */

typedef struct {                 /* per mode, const, flash */
    int32_t sh, ch;              /* Q30 sin(theta/2), cos(theta/2) */
    int32_t th;                  /* Q29 theta (rad); 0 = mode absent (above 18 kHz or not enabled) */
    int32_t d, drel;             /* Q30 1 - r^2, key held / damper on, rounded UP */
    int32_t cr;                  /* Q16 1 / (8 sin^2(theta/2)) */
    int32_t b;                   /* Q30 force (Q15) -> y (Q25): y += (F * b) >> 20 */
} rh_mode_t;                     /* 28 B */

typedef struct {                 /* per key, const, flash; 176 B */
    rh_mode_t m[RH_NM];          /* 140 B */
    int32_t off;                 /* Q25 default voicing offset (L_ver / L_hor), signed */
    int32_t gain;                /* Q16 output gain: loudness normalisation at v = 100 */
    int32_t kill;                /* Q25 sum of live-mode sub-block peaks below which the voice ends */
    int32_t hc;                  /* Q30 V1 -> H1 coupling per sample (Phase 3; 0 before) */
    int32_t pk;                  /* Q30 amplitude -> pitch: dE/E = (pk * a2) >> 50, a2 in Q50 gap^2 (Phase 3) */
    uint16_t prate;              /* Q12 pulse-position step per sample at the reference velocity (100) */
    uint16_t life;               /* lifetime cap in blocks (2 x T60(V1) + 1 s, at most 40 s = 6891 blocks) */
    uint8_t band, fam, res, pad; /* tip band 0..4; pickup family 0 = 1/16", 1 = 1/32"; residual index or 0xFF */
} rh_key_t;

typedef struct {                 /* TYPE deltas, applied at note-on */
    int8_t  band_shift;          /* -1, 0, +1 */
    int32_t gscale;              /* Q16 tip scale (closer pickup = > 1); the gain is divided by it */
    int32_t off_delta;           /* Q25 added to off */
    int32_t bell;                /* Q16 scale on b[O1], b[O2] */
    uint8_t rlc;                 /* RH_RLC row */
    uint8_t drive_row;           /* DRIVE voicing row (§9.1) */
    int16_t eq_lo, eq_hi;        /* preamp shelves, Q12 gain (SUITCASE); 4096 = flat */
} rh_type_t;

static const rh_key_t  RH_KEYS[RH_NKEY];
static const rh_type_t RH_TYPE[4];                        /* STAGE77 SUITCASE FELT DYNO */
static const int16_t   RH_PULSE[RH_NBAND][RH_NLAYER][RH_PLEN + 2];   /* Q15, peak 32767, time 0..T_c */
static const uint16_t  RH_VAMP[RH_NBAND][128];            /* Q15 force peak per velocity, re v = 127 */
static const uint16_t  RH_VRATE[RH_NBAND][128];           /* Q12 pulse-rate factor per velocity, re v = 100 */
static const uint8_t   RH_VLAYER[128];                    /* velocity -> shape layer 0..7 */
static const int32_t   RH_PHI[2][RH_PU_N + 1];            /* Q28 Phi~(s) per family */
static const int32_t   RH_SLOPE[2][RH_PU_N + 1];          /* Q28 dPhi~/ds * cell width per family */
static const int32_t   RH_RLC[4][5];                      /* b0 b1 b2 a1 a2, Q28 */
static const int16_t   RH_RES[RH_NBAND][3][512];          /* Phase 3: Q15 of 1/8 gap */
static const int32_t   TUNE_T[101];                       /* Q30 2^(c/1200) - 1, c = -50..+50 */
static const uint16_t  DECAY_K[128], REL_K[128];          /* Q16 1/S, S = 2^((v-64)/32) */
static const uint16_t  PEDAL_W[128];                      /* Q15 damper weight vs CC64 (§5.7) */
/* header: GEN_VERSION, sha256 of params.json and of every array; identical bytes on every run and OS */
```

### 4.3 Run-time voice state (`rhodes.c`, RAM)

```c
#define RH_NV 8u
#define SUB 32u
#define RAMP_SUB 7u              /* damper crossfade: 7 sub-blocks = 5.1 ms */
#define FADE_SUB 2u              /* steal fade: 64 frames = 1.45 ms */

typedef struct {
    int32_t x, y;                /* Q25 gap, MC3 states (x = displacement contribution) */
    int32_t ex, ey;              /* error-feedback remainders, 30 bits */
    int32_t e, eps;              /* live Q30 */
    int32_t e0, eps0;            /* sustain pair (after TUNE/DECAY/bend) */
    int32_t e1, eps1;            /* release pair */
    int32_t peak;                /* max |x|,|y| over the last sub-block */
    uint8_t live;                /* 0 = sleeping (state zero) */
} rh_ms_t;                       /* 40 B */

typedef struct {
    rh_ms_t m[RH_NM];            /* 200 B */
    const rh_key_t *k;
    uint8_t note, vel, alive, held;      /* held: key or pedal keeps it undamped */
    uint8_t damped;              /* target: 1 = damper on */
    uint8_t ramp;                /* damper crossfade sub-blocks left */
    uint8_t fade;                /* steal fade sub-blocks left (then init pending) */
    uint8_t pend_note, pend_vel, pend_sub;   /* pending start after a fade, or a timed start */
    uint8_t band;                /* after TYPE shift and HAMMER */
    uint32_t age;                /* allocation counter */
    uint32_t life;               /* blocks left before the hard cap ends the voice */
    int32_t xprev, hprev;        /* previous tip V (without offset) and H, Q25 */
    int32_t Hprev;               /* previous Phi~ value (Q28), the pickup difference state */
    int32_t off;                 /* Q25 offset in use (key + TYPE + TONE) */
    int32_t gain;                /* Q16 (key gain / gscale, x TYPE level) */
    uint32_t ppos, prate;        /* pulse position and step, Q16 in table samples */
    int32_t pamp;                /* Q15 */
    const int16_t *ptab;         /* NULL = no contact */
    uint16_t rpos;               /* residual position (Phase 3), 512 = done */
    int32_t wped;                /* Q15 damper weight (half-pedal) in effect for this voice */
} rh_voice_t;                    /* ≈ 232 B */

static rh_voice_t rv[RH_NV];
static int32_t rh_mono[256], rh_x[256], rh_h[256];
static volatile uint32_t rh_active_bits;   /* ISR writes: bit v = voice alive */
static volatile uint8_t  rh_notes[RH_NV];  /* ISR writes: note per voice (for rh_lit) */
static volatile uint32_t rh_quiet_blocks;  /* ISR writes: blocks since every voice ended and rh_mono was 0 */
```

### 4.4 Latched parameters (main loop writes `volatile int32`; ISR copies once per block in `fx_latch`)

`tone_off` (Q25), `type` (0–3), `tune_t` (Q30, from `TUNE_T`), `k_decay`, `k_rel` (Q16), `bark_g` (Q16 tip
scale), `bell_g` (Q16), `hammer_h` (Q12, −4096..4096), `noise_lvl` (Q15), `spread` (Q15), `pedal_lvl` (via event),
`bend_t` (Q30), the effect parameters of §9 and the sync increments of §11.

---

## 5. The voice

### 5.1 Note-on (`rh_events`, ISR, at the event's sub-block)

```
alloc(note, vel):
  v = voice with alive && note == note          → RE-STRIKE: keep m[].x/y; restart pulse; held = 1; damped = 0; ramp = RAMP_SUB if was damped
  else v = any !alive                            → init
  else v = quietest among !held (min Σ peak)     → steal: fade = FADE_SUB, pend_* = (note, vel, 0)
  else v = oldest (min age)                      → steal likewise
init(v, note, vel):
  k = &RH_KEYS[clamp(note, 21, 108) - 21]; ty = &RH_TYPE[type]
  band = clamp(k->band + ty->band_shift + (hammer_h >> 11), 0, 4)       /* hammer_h: Q12 -> -1..+1 band */
  for each mode m with k->m[m].th != 0:
      (e0, eps0) = coef(k->m[m], d = k->m[m].d,    tune_t + bend_t, k_decay)      /* §5.6 */
      (e1, eps1) = coef(k->m[m], d = k->m[m].drel, tune_t + bend_t, k_rel)
      if m == O1 or O2: b_eff = mul16(k->m[m].b, mul16(ty->bell, bell_g)) else b_eff = k->m[m].b
      m: x = y = ex = ey = 0; e = e0; eps = eps0; live = 1; peak = 0
  off   = k->off + ty->off_delta + tone_off
  gain  = (k->gain * 65536 / mul16(ty->gscale, bark_g)) is a division: instead the generator emits
          k->gain_inv (Q16 of 1/gain) ... NO: do gain = mul16(k->gain, GSCALE_INV[type_bark_index]) with a
          per-(TYPE, BARK) 4×128 Q16 table GSCALE_INV generated on the host. pamp scale = mul16(ty->gscale, bark_g).
  xprev = hprev = 0; Hprev = Hermite(s = (off^2) >> ...)  /* the rest position's Phi~, so the first difference is 0 */
  ramp = 0; damped = 0; held = 1; alive = 1; age = ++ages; life = k->life; rpos = res ? 0 : 512; wped = pedal weight
  load_pulse(v, vel)
load_pulse(v, vel):
  ptab  = RH_PULSE[band][RH_VLAYER[vel]]
  pamp  = mul15s(RH_VAMP[band][vel], pamp_scale)                        /* Q15 */
  prate = ((uint32_t)k->prate * RH_VRATE[band][vel] * 2^(hammer_h/4096 * 0.5)) >> 8   /* Q16; the 2^ term via a 9-entry table HAMMER_R[hammer_h >> 9] */
  ppos  = 0
```
Rules: a re-strike while the previous pulse is still playing restarts the pulse (replaces, never sums). A
NOTE_OFF for a note with `held` clears `held`; if the pedal weight allows damping (`wped > 0`) it sets `damped = 1`
and `ramp = RAMP_SUB`. PEDAL events update `wped` for every voice and re-evaluate `damped` for the unheld ones.
ALL_OFF(0) releases every voice; ALL_OFF(1) fades every voice over FADE_SUB and ends it. MUTE sets `rh_mute`.

### 5.2 Per sub-block (every 32 frames, per alive voice)

```
if fade:  gain ramps 1 -> 0 over FADE_SUB*32 samples (per-sample step of 32768/(FADE_SUB*32) Q15); when it reaches 0: init(pend) ; continue
if ramp:  ramp--; f = (RAMP_SUB - ramp) * 4681 (Q15 of i/7)
          target_e = damped ? e1 : e0 ; target_eps likewise ; eps lerps toward eps0 + ((eps1 - eps0) * wped >> 15) when damped
          e   = lerp(e_start, target_e, f) ; eps = lerp(eps_start, target_eps, f)   (per mode; e_start/eps_start captured at ramp start)
amplitude -> pitch (Phase 3, keys with pk != 0, modes V1 V2):
          a2  = ((int64)x*x + (int64)y*y) >> 25                                  /* Q25 gap^2 */
          rel = (int32)(((int64)pk * a2) >> 25)                                  /* Q30 relative dE/E */
          e   = e_base + mul30(e_base, rel)        (e_base = the ramp/lerp result above)
mode sleep: for m != V1: if live && peak < (k->kill >> 3) && m-th contact share done: live = 0; x = y = ex = ey = 0
voice end:  if !ptab && rpos == 512 && Σ_live peak < k->kill → alive = 0; zero everything; rh_active_bits &= ~bit
lifetime:   if --life == 0 → same as voice end
peak = 0 for every mode (re-armed for the next sub-block)
```
`e_start/eps_start` capture: at ramp start, copy the live `e, eps` so the crossfade is linear from wherever
the previous ramp was (pedal flutter never jumps). Half-pedal: `wped` ∈ [0, 32768] scales how far `eps` moves
toward the release value; `e` moves with the same fraction (§5.7).

### 5.3 Per sample (inside a sub-block; mode-outer, sample-inner)

```
/* 1. hammer force into a 32-sample buffer F[], Q15 (0 when !ptab) */
for j in 0..31:
    if ptab: i = ppos >> 16; f = (ppos >> 1) & 0x7FFF
             F[j] = mul15s(ptab[i] + mul15s(ptab[i+1] - ptab[i], f), pamp)
             ppos += prate; if (ppos >> 16) >= RH_PLEN: ptab = NULL
    else F[j] = 0
/* 2. modes: V1 first (so H1 can read it), then H1, then V2 O1 O2, each adding into rh_x[] (H1 into rh_h[]) */
mode V1 (then V2, O1, O2), state x,y,ex,ey,e,eps in registers:
    for j: if F[j]: y += (int32)(((int64)F[j] * b) >> 20)
           t = (int64)e * y + ex;        x += (int32)(t >> 30); ex = (int32)(t & 0x3FFFFFFF)
           t = -(int64)eps * y - (int64)e * x + ey;  y += (int32)(t >> 30); ey = (int32)(t & 0x3FFFFFFF)
           rh_x[j] += x ; peak = max(peak, |x|, |y|)
    per sub-block end: if |x| > 2^28 or |y| > 2^28: clamp to ±2^28 (sat28) — a guard, never expected
mode H1 (Phase 3): y += mul30(hc, x_V1[j]) + ((F[j] * b_H1) >> 20) ; same recursion ; rh_h[j] = x
/* 3. attack residual (Phase 3) */
    if rpos < 512: rh_x[j] += mul15s(RH_RES[band][layer3][rpos++], pamp) << 10    /* Q15 of 1/8 gap -> Q25 */
/* 4. pickup, radial, Hermite, exact difference */
for j:
    u   = rh_x[j] + off                                   /* Q25 */
    s   = (int64)u * u + (int64)rh_h[j] * rh_h[j]         /* Q50 */
    if s >= RH_S_MAX: s = RH_S_MAX - 1
    i   = (uint32)(s >> RH_PU_CELL);  t = (uint32)(s >> RH_PU_FR) & 0x3FFFFF        /* Q22 fraction */
    p0 = P[i]; p1 = P[i+1]; m0 = M[i]; m1 = M[i+1]                                 /* pool tables of the family */
    c3 = 2*(p0 - p1) + m0 + m1;  c2 = 3*(p1 - p0) - 2*m0 - m1                      /* int32: |P|,|M| < 2^28 */
    H  = p0 + (int32)(((int64)t * (m0 + (int32)(((int64)t * (c2 + (int32)(((int64)t * c3) >> 22))) >> 22))) >> 22)
    y  = H - Hprev; Hprev = H                              /* Q28 */
    rh_mono[j] += (int32)(((int64)y * gain) >> 24)        /* Q28 * Q16 >> 24 -> Q20, i.e. Q15 with 5 bits of mix headroom */
    (times the fade gain if fading)
```
Cost per voice-sample: 5 modes × (3 widening multiplies + 2 EF) ≈ 65 instr; pickup 5 multiplies + ≈ 30 instr;
contact +3 per sample. H1 +1 multiply. ≈ 120 host instructions worst, ≈ 70 typical (3 live modes).

**Offset changes.** `off` can change between sub-blocks (TONE knob, TYPE applies to new notes only). At the
sub-block where it changes, recompute `Hprev` from `xprev`, `hprev` with the new offset before the sample loop
(one Hermite per voice), so TONE moves never click.

### 5.4 Overflow budget

| Quantity | Bound | Fits |
|---|---|---|
| mode x, y | ±2^28 (8 gap) per mode by the sub-block guard; physically ≤ 2^27 (generator asserts max s < 0.8·S_MAX, i.e. tip < 5.06 gap) | int32 |
| `e·y`, `eps·y`, `e·x` | 2^31 × 2^28 = 2^59 | int64 |
| rh_x[j] = Σ 4 modes + residual | 4·2^28 + 2^22 < 2^31 | int32 |
| u = rh_x + off | \|off\| ≤ 2^26 (2 gap) → < 2^31 | int32 |
| s = u² + h² | (2^31)² + (2^28)² < 2^63 | int64 |
| Horner: t·c3 | 2^22 × 2^30 = 2^52; c2 ≤ 5·2^28 < 2^31 | int64 / int32 |
| y·gain | 2^29 × 2^17 = 2^46 | int64 |
| rh_mono | 8 voices × 2^20 = 2^23 ≪ 2^31 | int32 |
| a² = x² + y² | 2^57 | int64 |
| F·b | 2^15 × 2^31 = 2^46 | int64 |

### 5.5 After the voices (`rh_load`)

```
for j: acc = (int64)b0*m[j] + b1*m1 + b2*m2 - a1*o1 - a2*o2 + e_rlc      /* Q28 coefficients, DF1 */
       o = (int32)(acc >> 28); e_rlc = (int32)(acc & 0xFFFFFFF)          /* error feedback: no DC limit cycle */
       shift histories; rh_mono[j] = mul16(o, master_voice_gain)
if rh_mute: rh_mono[] = 0
if no voice alive and the block's max |rh_mono| < 2: rh_mono[] = 0 exactly; zero the RLC state (C9: exact silence)
```
The SUITCASE preamp shelves (`eq_lo`, `eq_hi`) are two first-order shelving one-poles here, Q12 gains, bypassed at
4096.

### 5.6 Run-time coefficient path (S16; `scratch-05/coefpath_mc3.py`)

Inputs per mode: `sh = sin(θ/2)`, `ch = cos(θ/2)` (Q30), `th = θ` (Q29), `d = 1 − r²` (Q30, rounded up),
`cr = 1/(8 sin²(θ/2))` (Q16). Run-time: `t = 2^(c/1200) − 1` (Q30: `TUNE_T[c+50]` for TUNE; `exp2_q30` for bend,
`t_total = t_tune + t_bend + mul30(t_tune, t_bend)`), `k = 1/S` (Q16 from `DECAY_K` / `REL_K`).

```
d'   = (k·d >> 16) − ((k·(k−1) >> 16)·d² >> 17) + ((k·(k−1)·(k−2) >> 32)·d³ >> 16)/6     (Q30; d², d³ by mul30)  ; max(d', 1)
q    = 2^30 − d'/4 − 3·d'²/32                                 (sqrt(r'))
om   = d'/2 + d'²/8 + d'³/16                                  (1 − r')
dl   = (th · t) >> 30                                         (θ·t/2 in Q30, since th is Q29)
cdl  = 2^30 − dl²/2 + dl⁴/24 ;  sdl = dl − dl³/6 + dl⁵/120
s'   = mul30(sh, cdl) + mul30(ch, sdl)                        (sin(θ'/2))
cr'  = cr − 2·mul30(cr, t) + 3·mul30(cr, mul30(t, t))         (the correction weight at the shifted angle)
E    = mul30(mul30(2q, s'), 2^30 + ((om² >> 30) · cr' >> 16))
eps  = d'
```
Verified: TUNE ±50 c, DECAY ×0.25–4, keys 21–108, modes at ratios 1/7/20 with T60 18 s–50 ms: pitch ≤ 0.14 c,
T60 ≤ 0.012 %; release pairs (T60 0.125–0.5 s) ≤ 2.4 c; with ±200 c bend: ≤ 0.38 c held. Unit test thresholds:
held ≤ 0.2 c (TUNE), ≤ 0.5 c (bend), release ≤ 3 c; T60 ≤ 1 %. The same code runs on the host in the golden model
(float, same structure) and in C. Cost ≈ 30 multiplies per mode; 10 evaluations per note-on.

### 5.7 Damper, half-pedal, thumps, noises

- Release pair per mode from the 2-DOF re-solve with σ_d on the tine coordinate (§6.3), matched by eigenvalue
  continuity to the sustain pair; both `e` and `eps` crossfade over RAMP_SUB sub-blocks.
- **Half-pedal:** `PEDAL_W[cc]` (Q15) = damper weight: 32768 for cc ≤ 41, then linear through (42, 29491), (54,
  22938), (62, 19661), (64, 0), 0 above (the jRhodes curve 11 inverted). A voice whose key is up uses
  `eps_target = eps0 + ((eps1 − eps0)·wped >> 15)`, same for `e`. REC and CC64 ≥ 64 → 0 (sustain), release → 32768.
- **Damper thump** (NOISE > 0): at damper-on, inject a half-sine force pulse of 2 ms (the 70-duro shape at layer 2)
  with amplitude `thump = NOISE level (−60 + 36·v/127 dB) − 6 dB` into V1/V2 via `b` (tine coordinate).
- **Key-down thump:** the pulse `F` added to `rh_mono` at `noise_lvl − 10 dB` (default off for STAGE '77: jRhodes
  is a DI recording). **Felt noise:** a 16-bit LFSR burst, 8 ms, one-pole LP 2 kHz, at `noise_lvl − 20 dB` at
  key-up (Phase 3, cheap).

### 5.8 Steal fade and voice end

Fade gain: `g -= 32768 / (FADE_SUB·32)` per sample (512 Q15 per sample), applied to the voice's contribution;
when g reaches 0 the voice is initialised for `pend_note` and starts in that sub-block. The click detector (§8,
health H) must stay < −50 dBFS at every steal and note-off in the goldens. Voice end: the sub-block rule of §5.2;
`rh_active_bits` and `rh_notes[]` are updated at the sub-block boundary. `rh_quiet()` = `rh_active_bits == 0 &&
rh_quiet_blocks ≥ 2`. `rh_lit(base, n)` = OR of `1 << (rh_notes[v] − base)` for alive voices whose key is held.

---
