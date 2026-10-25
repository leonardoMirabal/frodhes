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
| `RH_KEYS[88]` (`rh_key_t`, 168 B) | 14 784 | §4.2 |
| `RH_PULSE[5][8][66]` int16 | 5 280 | |
| `RH_VAMP[5][128]`, `RH_VRATE[5][128]` uint16 | 2 560 | |
| `RH_VLAYER[128]` uint8 | 128 | |
| `RH_PHI[2][2049]`, `RH_SLOPE[2][2049]` int32 (source of the pool copies) | 32 784 | |
| `RH_RES[5][3][512]` int16 (Phase 3) | 15 360 | |
| `RH_RLC[4][5]` int32, `RH_TYPE[4]`, velocity law tables `RH_VLAW[5][128]` uint16 | ≈ 1 500 | |
| Coefficient-path tables: `TUNE_T[101]` (2^(c/1200)−1, c = −50..50, Q30), `DECAY_K[128]`, `REL_K[128]` (Q16), `PEDAL_W[128]` (Q15), `GSCALE_INV[4][128]` (Q16), `HAMMER_R[9]` | 3 240 | |
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

typedef struct {                 /* per key, const, flash; 168 B */
    rh_mode_t m[RH_NM];          /* 140 B */
    int32_t off;                 /* Q25 default voicing offset (L_ver / L_hor), signed */
    int32_t gain;                /* Q16 output gain: loudness normalisation at v = 100 */
    int32_t kill;                /* Q25 sum of live-mode sub-block peaks below which the voice ends */
    int32_t hc;                  /* Q30 V1 -> H1 coupling per sample (Phase 3; 0 before) */
    int32_t pk;                  /* Q30 amplitude -> pitch: dE/E = (pk * a2) >> 25, a2 = (x^2 + y^2) >> 25 in Q25 gap^2 (Phase 3) */
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
  pamp_scale = mul16(ty->gscale, bark_g)                                /* Q16: scales the force = scales the tip */
  gain  = mul16(k->gain, GSCALE_INV[type][bark_v])                        /* Q16; GSCALE_INV[4][128] = 65536^2 / (gscale * bark_g), host table: no division */
  xprev = hprev = 0; Hprev = H(s) with u = off, h = 0      /* the rest position's Phi~ (one Hermite), so the first difference is 0 */
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
mode sleep: for m != V1: if live && !ptab && peak < (k->kill >> 3): live = 0; x = y = ex = ey = 0   (a re-strike re-arms every mode)
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
    if rpos < 512: rh_x[j] += mul15s(RH_RES[band][layer3][rpos++], pamp) << 7     /* Q15 of 1/8 gap -> Q25: x 2^25 / (2^15 x 8); layer3 = RH_VLAYER[vel] * 3 / 8 */
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

## 6. Host generator (`tools/gen_rhodes.py`): exact steps

Inputs: `docs/model/data/params.json` (fitted per root plus global priors, §7.5), the 73 service-manual tine
lengths E1–E7 (R02 `TineLengths.h`, as numbers: 0.1564 m at E1 to 0.0226 m at E7; `research/tools/perkey.csv`
already lists them), the tip bands by key, the gap families by key. Output: `firmware/gen/rhodes_tables.h`,
byte-identical across runs and OSes (sorted dicts, fixed float formatting, numpy with fixed seeds, `np.round`
to integers only at the end). Every step has a unit test in `tests/unit/` (host, Python).

### 6.1 Key range
Keys 21–108. Keys 28–100 use the manual lengths; 21–27 and 101–108 extrapolate L by a log-log fit of L against
f0 over the nearest 12 keys (ESTIMATE). f0 = 440·2^((midi−69)/12). Everything else per key follows from L and f0.

### 6.2 FE tine with the tuning spring
40 Hermite Euler–Bernoulli beam elements, d = 0.075" (1.905 mm), E = 200 GPa, ρ = 7850 kg/m³, clamped base, free
tip, consistent mass matrix (the stencils of `scratch-01/fe.py`). A point mass m_s at position p·L (p from params,
global; default 0.65 ESTIMATE; the alternative near 0.95 is a fit candidate, §17). Bisect m_s so that f1 = f_t, the
tine's own target (§6.3 passes f_t). Outputs per key: ratios r2 = f2/f1, r3 = f3/f1, r4..r12/f1; mode shapes ψ_m at
the strike point x_c(key) and at the tip; modal masses m_m = ψᵀMψ. Strike line: 2¼" (bass, key 21) to ⅛" (treble,
key 108) from the clamp (R05 W1), interpolated linearly in key. Test: m_s = 0 gives 6.267 and 17.548 ± 0.1 %.

### 6.3 2-DOF tine–bar
Per key, from params (δ_b bar detune in cents, k_rel coupling relative to ω_t², μ mass ratio, σ_t, σ_b): M = diag(1,
μ), K = [[ω_t² + k, −k], [−k, μω_b² + k]], C = diag(2σ_t, 2μσ_b), force on coordinate 1, output coordinate 1. Build the
4×4 state matrix A = [[0, I], [−M⁻¹K, −M⁻¹C]], eigen-decompose, residues R_i = (CᵀV)_i (V⁻¹B)_i for the two
eigenvalues with positive imaginary part. V1 = the mode with the larger late weight |R|·e^(−σ·2 s); V2 the other.
Scale both frequencies by f0/f(V1) (so V1 lands exactly on f0; `scratch-05/tinebar_residue.py`: −0.4..−18 c
depending on k_rel). Weights b_m = 2|R_m|ω_m (the pure-2-pole equivalent; the residue phase of V1 is within 11° of
the ideal and is dropped, with the generator asserting |phase| < 15°; V2's larger phase error only shifts the
beat phase). Release: repeat with C = diag(2(σ_t + σ_d), 2μσ_b) and match each release mode to a sustain mode by
nearest eigenvalue (NOT by weight: with the felt on, the bar mode is the long one). Tests: k = 0 reproduces the bare
tine and bar; residues sum to the static compliance; retune exact.

### 6.4 Overtones
f(O1) = f0·r2, f(O2) = f0·r3 from §6.2; σ from params, defaulting to R03's code law σ_m = σ_1·(κ_m/κ_1)⁴
(T60 ≈ 0.19 s and 24 ms at A4). Any mode above 18 kHz: `th = 0` (absent). O1/O2 weights from ψ(x_c)ψ(tip)/m_m.

### 6.5 Coefficients per mode
r = exp(−6.9078/(T60·fs)), θ = 2πf/fs. Emit `sh = round(sin(θ/2)·2^30)`, `ch = round(cos(θ/2)·2^30)`, `th =
round(θ·2^29)`, `d = ceil((1 − r²)·2^30)` (never less damped), `drel` likewise from the release σ, `cr =
round(2^16/(8 sin²(θ/2)))`. Assert `cr < 2^31` (true for f ≥ 27.5 Hz) and `th ≥ 1`.

### 6.6 Hammer pulses (coupled contact simulation)
Per band, with the band's representative key (30 duro: E1; 50: C2; 70: A3; 90: C5; wood: C7 — ESTIMATE
mapping of R05 W1's 88-position bands onto the 73 keys, §17), at 16×fs: hammer mass m_h (prior 11 g, R02 Tab.
3.2), felt F = K·e^p + R·d(e^p)/dt with p = 2.5, K and R per band (fitted so T_c at v = 100 matches a 1–3 ms prior
and the reference attack spectra), the key's first 6 FE modes plus the 2-DOF bar, all at rest; the tine's modal
masses from §6.2. Hammer speed from MIDI velocity: v_h = v_max·2^(−R_dB/6.0206·(1 − (v−1)/126)), R_dB = 30, v_max = 4
m/s (ESTIMATE). Record F(t) until the final loss of contact (re-contacts included). Per (band, layer of 8 velocity
layers centred at v = 8, 24, 40, 56, 72, 88, 104, 120): normalise the time axis to [0, T_c] and the peak to 32767,
resample to 64 samples + 2 zeros → `RH_PULSE`. Assert adjacent layers differ by < 3 % RMS (else 16 layers). From
the 128 velocities: `RH_VRATE[band][v] = round(4096·T_c(100)/T_c(v))`, `RH_VAMP[band][v] = round(32767·F_pk(v)/F_pk(127))`.
`prate(key) = round(4096·64/(T_c(band, 100)·fs))`; assert T_c ∈ [0.2, 25] ms. `RH_VLAYER[v]` = nearest layer centre.
Tests: energy conserved with R = 0; contact against a rigid wall follows the Hertz law T_c ∝ v^(−(p−1)/(p+1)).

### 6.7 Input gains b (calibrated on the quantised recursion)
Run the MC3+EF1 recursion in float with the quantised `E, eps` of §6.5, inject a unit Q15 force for one sample,
measure the x response amplitude; scale b_m so that the response equals the continuous modal response (residue ×
ψ_m(x_c)·ψ_m(tip)/m_m, in gap units of the key's family) for a unit impulse. Assert |b| < 2^31 and ≥ 12
significant bits.

### 6.8 Pickup tables
Per family (L_hor = 1 gap by definition; R_p/L_hor from params, prior 1.0 as R03's code): g0(u) = (f1 − 2)/f1² −
(f2 − 2)/f2², f1 = (u − R_p)² + 1, f2 = (u + R_p)² + 1 (R03 eq. 25 with L_ver = 0). Φ(u) = ∫₀ᵘ g0, by 2^20-point
trapezoid quadrature over |u| ≤ 8. Φ̃(s) = Φ(√s), D(s) = g0(√s)/(2√s) with D(0) = g0'(0)/2. Grid s_i = i/64 gap², i =
0..2048. `RH_PHI[i] = round(Φ̃(s_i)·2^28/A)`, `RH_SLOPE[i] = round(D(s_i)·(1/64)·2^28/A)`, A = max(|Φ̃|, |D/64|) so both
fit Q28. Asserts: |D(32)| < 3·10⁻³·max|D| (measured 2.65·10⁻³); the golden model's maximum s at v = 127 over all
keys < 0.8·S_MAX; no word in the ROM window (nudge by 1 LSB, re-check). Two families: 1/16" (keys ≤ 32 and ≥ 96
ESTIMATE from R05 W3 "first 15 notes" and "wood-tip notes"), 1/32" otherwise.

### 6.9 off, gain, kill, life, GSCALE_INV
`off` = L_ver/L_hor per key from params (per root, interpolated; prior −0.2 gap, sign by the fit). `gain`: render
each key at v = 100 with the golden model, 1 s RMS → target −20 dBFS (ESTIMATE), smoothed over ±3 keys, as Q16;
assert 8 voices at v = 127 sum below 2^23 in rh_mono. `kill`: the Σ-peak at which the voice's output falls below 1
LSB Q15, solved with the golden model, ×2 margin. `life` = blocks(2·T60(V1) + 1 s) ≤ 6891. `GSCALE_INV[type][v] =
round(2^32/(gscale_type·bark(v)))` with bark(v) = 2^((v−64)/64) in Q16; `HAMMER_R[i] = round(65536·2^(0.5·(i−4)/4))`.

### 6.10 RLC, TYPE, jitter, residuals, emit
RLC: prewarped bilinear low-pass, f = 5 kHz, Q from the TYPE (1.0 STAGE, 1.2 SUITCASE, 0.8 FELT, 1.5 DYNO —
ESTIMATE), Q28. TYPE deltas (`rh_type_t`): STAGE '77 (0, 1.0, 0, 1.0); SUITCASE (0, 1.0, 0, 1.0, eq_lo +2 dB at 150
Hz, eq_hi +3 dB at 4 kHz, from the Peterson tone stack — ESTIMATE); EARLY FELT (−1, 0.9, 0, 0.8); BELL/DYNO (+1, 1.2,
−0.1 gap, 1.2). Jitter (Phase 3): xorshift32 with seed 0x46524F44, per key: V2 detune ±1.5 c, gain ±0.7 dB, d ±8 %,
off ±0.05 gap, within the fit residuals. *As built (0.9.5, gen_rhodes.jit / whirl, params.json `jitter`, `whirl`,
`v2_drop`):* a sha256 of (seed 958, key, quantity) instead of xorshift32, so a new quantity never moves the others:
V1 T60 ±8 %, gain ±0.15 dB, off ±0.05 gap, contact rate ±3 %; never V1's pitch. The V2 slot holds a beat partner
on keys 40-84 (a second vertical mode 0.3-1.5 Hz from V1, weight 0.12, T60 0.8-1.1 × V1's: the polarisations'
energy exchange as a slow 1.5-2.5 dB level wobble with the pitch within 1 c) and nothing on keys 21-39 and 85-99
(the tone-bar mode there was ≥ 60 dB under V1); keys 100-108 keep the bar mode. Residuals (Phase 3): per band and 3 layers, the tip displacement of modes
4..12 for the first 512 samples from the contact simulation, Q15 of 1/8 gap. Emit the header with `GEN_VERSION`
and sha256 of `params.json` and of every array; `make tables-check` fails on any diff.

---

## 7. Reference data and the fit

### 7.1 Sources (`docs/model/data/sources.json`, committed; audio in `.local/reference/<id>/`, git-ignored)

| id | Source | Instrument | Chain / processing | Layers etc. | Terms | Status |
|---|---|---|---|---|---|---|
| `pianobook` | Pianobook "Matt's Fender Rhodes" <https://www.pianobook.co.uk/packs/matts-fender-rhodes/> | Mark I 73 | DI: Countryman Type 10 → Chandler TG2; **no processing** (per the user) | layers, round robins, releases UNVERIFIED until downloaded | free login; terms unknown → analysis-only | not fetched |
| `jrhodes` | jRhodes3d (Jeff Learman) | 1977 Mark I Stage 73 | harp direct; **EQ'd** (treble boost, low-mid scoop) and **denoised** | 15 roots, 3–5 layers, no releases, 44.1 kHz mono | samples CC BY-NC 4.0; sfz CC0 | not fetched (sha256 manifest prepared) |
| `mpc_ek2` | MPC-Samples "Electric Keys 2" <https://www.mpc-samples.com/product.php/324/electric-keys-2-mpc-expansion/> (paid, $29.99) | Mark I 88 Stage **and** Mark II 73 **Suitcase** | chain and EULA unknown; possibly processed | **8 velocity layers**, 24-bit WAV | analysis-only until the EULA is read | not purchased (§17) |
| `user_<name>` | the user's own captures (a Nord playing EP8 Nefertiti MKI / EP7 Amped Tines, or a real Rhodes) by §7.4 | as captured | as documented in the manifest | per §7.4 | the user's | none yet |
| — | Nord Piano Library `.npno` | — | proprietary container: **never decoded** | — | — | excluded |

Manifest record: `{id, name, url, instrument: {model, year, keys, amp}, chain: {pickup: "DI"|"mic", preamp,
converter}, processing: {eq: bool|null, denoised: bool|null, compressed: bool|null, normalised: bool|null},
content: {roots: [...], layers: n, round_robins: n, releases: bool, rate, bits, channels}, licence, fetched: bool,
sha256_manifest: ".local/reference/<id>/SHA256SUMS", notes}`. `null` = unknown (treated as the worst case).

### 7.2 Trust rules (which source may feed which measure)

| Measure | Allowed sources | Rule |
|---|---|---|
| f0, partial ratios (C1, C3 frequency) | all | EQ-invariant. |
| Late decay slope (C2 late), ripple rate/depth | all, **denoised sources only down to noise floor + 20 dB** (not +10) | denoise steepens tails. |
| Early decay (C2 EDT), breakpoint | all | |
| Noise floor, tail end, release (C4 target) | **unprocessed only** (`processing.denoised == false`): pianobook, user captures | |
| Absolute spectral balance (A9 trend; RLC/EQ by ear) | **DI + unprocessed only**: pianobook, user captures | jRhodes is EQ'd; MPC unknown → excluded until its chain is known. |
| Velocity → timbre ΔB (C6), bark onset | sources with ≥ 3 real layers; **mpc_ek2 primary** (8 layers) when available, else jrhodes | relative to the softest layer of the same root. |
| Level spacing per layer | mpc_ek2, jrhodes (3 dB/layer stated) | relative only. |
| Attack pitch (Phase 3) | any with ≥ 2 layers | |
| SUITCASE TYPE deltas | mpc_ek2 Mark II Suitcase vs its Mark I Stage (same chain) | difference measures only. |
| Re-strike, pedal, mechanical noises | user captures (per §7.4) | |

`fit_model.py` minimises Σ_sources w_s · Σ_measures (model − ref)²/σ² with w_s = 1 for the source listed as primary
for that measure, 0.3 for others allowed, 0 for excluded; it reports per-source residuals, and Gate A passes on the
primary source of each measure. Where sources disagree beyond their stated tolerances the report flags it and the
primary wins.

### 7.3 Fetch and manifest tooling
`tools/fetch_reference.py <id>`: jRhodes by sparse git clone at a pinned commit (65 mono FLACs, 22.9 MB); pianobook
and mpc_ek2 require a manual download into `.local/reference/<id>/` (login / purchase), after which the script
verifies or writes `SHA256SUMS`, probes the files (rate, bits, lengths, layer naming) and fills the manifest's
`content` block. `fit_reference.py` runs per source → `docs/model/data/reference_<id>.json` (derived numbers only,
with `derived_from` attribution). CI never needs the audio.

### 7.4 Capture spec for user recordings (`docs/model/data/CAPTURE.md`)
- **Chain:** DI or line out, no effects, no EQ, no compression, no normalisation; note the gear in the manifest.
  48 kHz or 44.1 kHz, 24-bit, mono. Sustain pedal up unless the case says so. Room silent; record 10 s of silence
  first (noise floor), and a 1 kHz tone at −18 dBFS from the same chain if possible (level reference).
- **Level:** v = 127 on the loudest root must peak below −6 dBFS; do not change gain between files.
- **Notes:** the 15 jRhodes roots (MIDI 29 35 40 45 50 55 59 62 65 71 76 81 86 91 96) at least; the full
  chromatic E1–E7 if time allows.
- **Velocities:** 8, 24, 60, 84, 104, 120, 127 (layer centres plus extremes); on a Nord, send MIDI from the FM-1
  or a DAW so velocities are exact.
- **Cases per root:** (a) hold 8 s then release, record 2 s more; (b) hold 1.5 s then release (damper); (c)
  re-strike: v = 100 then v = 60 at 0.5 s; (d) pedal: strike, pedal down at 0.5 s, key up at 1 s, pedal up at 6 s.
- **Naming:** `<src>_<midi>_<vel>_<case>_<rr>.wav`, e.g. `nordep8_045_120_a_1.wav`.
- **Manifest:** one `user_<name>` record per instrument/chain.

### 7.5 `params.json` (fitted) and the golden model
`tools/rhodes_model.py` is float64 with exactly the C structure (§5): MC3 modes, pulse player, Hermite pickup in
s (float tables of the same grid), RLC, same event semantics; it reads `rhodes_tables.h`'s generator inputs.
`params.json` holds, per root: δ_b, k_rel, μ, σ_t, σ_b, σ_d (release), off, x-scale (tip amplitude per unit force),
κ, O1/O2 σ overrides; and global: spring position p, R_p/L_hor, felt K/R per band, m_h, v_max, the gain target.
**Gate A** = the golden model passes C1, C2, C3, C5, C6, C12 against the primary sources.

---

## 8. Checks (`tools/analyze.py`, thresholds in `tests/analysis_thresholds.toml`)

Shared primitives (`tools/dsp_analysis.py`, unit-tested on synthetic decaying sines: f within 0.1 c, T60 within 1 %
at 40 dB SNR): `band(sig, f, bw)` FFT-domain band-pass f·(1 ± bw) then the analytic signal; `env_db` = |analytic|
smoothed by a 10 ms moving RMS, in dB; `inst_f` = slope of the unwrapped phase by linear regression over a window;
`harm(sig, f0, k, win)` = power at k·f0 (±3 %) from a Hann FFT; noise floor = median of `env_db` over the last 0.3 s.

| # | Check | Computation | Pass |
|---|---|---|---|
| C1 tuning | every key, v = 40, 3 s held | f = `inst_f(band(f0, 1/6 oct))` over [0.5, 2.5] s ([0.2 s, t(−40 dB)] above C6) | \|1200 log2(f/f_target)\| ≤ 2 c; first-half vs second-half drift ≤ 2 c |
| C2 decay | 15 roots × real layers, model vs reference per §7.2 | `env_db(band(f0))`; two-segment least-squares fit over [peak − 3 dB, max(peak − 45 dB, floor + 10 dB (+20 dB if denoised))], breakpoint searched | late T60 ±20 % at ≥ 13/15 roots, never outside ±50 %; EDT T60 ±30 %; breakpoint level ±4 dB |
| C3 overtones | bands at f(O1) ± 1.5 %, f(O2), where the reference has a peak ≥ 10 dB above its neighbourhood | T60 by the same fit; frequency by `inst_f` | T60(O1) ≤ 0.25·T60(V1), T60(O2) ≤ 0.1·T60(V1); T60 ±30 % of the reference; frequency within ±1 % of the reference ratio |
| C4 release | each register's root: hold 1.5 s, key up | 5 ms RMS in dB, regress from key-up + 10 ms to −40 dB or 1 s; HP (8 kHz) residual at key-up | T60 within ±25 % of the (user-RELEASE-scaled) target (0.5/0.4/0.3/0.2 s unless an unprocessed source gives a measurement); click < −50 dBFS; `alive = 0` within 2·T60_rel + 50 ms |
| C5 pickup unit | `rh_pickup_probe()` feeds x = 1.5 gap·sin(2π·441·t) through the C pickup | peaks per period, peak ratio, antisymmetry; off = 0 variant | 2 positive peaks/period above 20 % of max; smaller/larger ∈ [0.3, 0.95]; \|max/min\| = 1 ± 0.01; at off = 0: equal peaks (0.99–1.01) and P(H1)/P(H2) < −40 dB; C vs float ≤ −70 dB |
| C6 velocity and bark | roots F1 E2 D3 D4 B4 at v = 8, 16, …, 127 | L(v) = peak 50 ms RMS; B(v) = 10 log10(Σ_{k=2..5} harm_k / harm_1) over [30, 150] ms; ΔB = B(v) − B(v_soft); layer centres per source | RMS monotonic; L(127) − L(8) ∈ [20, 40] dB (default R = 30 dB → 24–32); bass layer spacing 2–5 dB; \|ΔB_model − ΔB_ref\| ≤ 3 dB at each layer centre; ≥ 50 % of the ΔB rise above v = 84 |
| C7 golden (Gate B) | every scripted case (notes, chords, re-strikes, pedal, TONE moves): C render vs golden render, aligned | residual energy / signal energy | ≤ −60 dB per case |
| C8 alias | keys ≥ C5, v = 127, [0.05, 0.5] s | least-squares fit of all harmonics of f0 and f(O1) ± k·f0 below Nyquist; residual energy / fitted energy | ≤ −60 dB; report the worst key. If it fails: first reduce the treble x-scale within the fit's tolerance, then 2× oversample the pickup input with a half-band decimator (task P3-5) |
| C9 noise | soft E1 (v = 20): C vs golden; silence | residual; output after every voice ends | ≤ −60 dB; exactly 0 |
| C10 re-strike | E2 v = 100 then v = 60 at 0.5 s | C vs golden; 1 ms RMS | as C7; the 1 ms RMS never falls below −30 dB of its local level |
| C11 device timing | the P2 worst-case block on the device (profile build) | `prof.max` | voices ≤ 1 900 µs, app total ≤ 2 400 µs, no `late` |
| **C12 waveform** | full voice, F1 and A2, v = 120, [100, 200] ms | positive peaks per period above 20 % of the local max; ratio of the two | 2 ± 0 peaks; ratio ∈ [0.3, 0.95] (the reference's ratio ± 0.2 where an unprocessed source exists) |
| **C13 chord linearity** | C3-E3-G3 at v = 100, DRIVE off, vs the sum of the three single renders | residual energy / signal | ≤ −60 dB (the voice chain is linear after the per-voice pickup; catches any accidental shared nonlinearity) |
| Attack pitch (Phase 3) | `inst_f` at [50, 200] ms minus [2, 3] s, loudest vs softest layer, F1/B1/E2 | | within ±1.5 c of the reference |
| A8 whirl (Phase 3) | envelope modulation depth of the fundamental band, 0.5–3 s | | 0.5–3 dB present |
| A11 effects | delay time; TIME change; HALL RT60; phaser notches; tremolo rate | | ±1 sample at every division and BPM; zipper residual < −50 dBFS; RT60 1.25–5 s across the knob; notches sweep 150 Hz–4.8 kHz; rate = division ± 0.5 % |

**Health (every golden render, `tests/check_render.py`):** no \|x\| ≥ 32700 outside "overload" scenarios; DC < 400
LSB; peak > −42 dBFS; voices free ≤ 12 s after the last note-off with the pedal up; tail AC peak < 6 LSB in the
last 0.25 s; click detector (8 kHz HP, spike < −50 dBFS at note-off, steal and preset load).

**T-LISTEN (Phase 3 sign-off):** 15 roots × 2 velocities (layer centres ≈ 60 and ≈ 120), 3 s excerpts, model and
reference RMS-matched ±0.5 dB over the first 1 s; triples in random order (reference, model, STK Rhodey low
anchor); the listener rates realism 1–5 and picks "the real one"; headphones and the FM-1 speaker (reference through
the same master path). Pass: model mean ≥ 3.5, anchor ≤ 2, correct identification ≤ 75 %. Scores and the git SHA go
to `docs/model/reports/listening-<date>.md`; never the audio.

---

## 9. Effects (`fx.c`)

Conventions: mono `int32` Q15 (with headroom) until TREM, stereo after. Coefficients are computed per 32-frame
sub-block from the latched params; gains that change are ramped per sample (`step = (to − from) >> 5` over the
sub-block). Every effect has `bypass` behaviour that keeps its state so switching on never clicks, and a
switch-off that cuts the *input* and lets the tail ring (U8). Ported Felucca code keeps its GPL-3.0 header; the
missing MIT helpers are rewritten in `dsp.c`: `softclip(x)` = Q15 tanh table (257 entries, lerp, |x| ≤ 4·32767
in), `osc_sine(ph)` = 1024-entry Q15 table with lerp on a Q32 phase (returns ±32767), `clamp16`.

### 9.1 DRIVE (port of Felucca `track_dist`, fx.c:96-114, per sample; v = DRIVE 0..127)
```
sub-block: g = 4096 + ((v*v*2*S[row]) >> 4)  (Q12; S[row] Q4 per TYPE drive row: 16 STAGE, 20 SUITCASE, 14 FELT, 24 DYNO ESTIMATE)
           k = 32000 - 95 v ; mk = ((30000 - 120 v) * MU[row]) >> 12   (MU: make-up per row, level-matched by the host test)
           bias = BIAS[row] (2400 STAGE; 3200 DYNO; 1800 FELT; 2400 SUITCASE)
if v == 0: output = input (state kept)
per sample: hp += (x - hp + 64) >> 7 ; x = clamp(x - hp, ±230000)
            y = softclip((((x >> 2) * g) >> 10) + bias) - softclip(bias)
            lp1 += mul15(y - lp1, k) ; lp2 += mul15(lp1 - lp2, k) ; out = mul15(lp2, mk)
```

### 9.2 TREM / WAH (switch OFF / TRM / WAH; 10 ms crossfade on switch change)
```
LFO: ph += inc per sample (uint32); inc from RATE: LFO_INC[v] = 2^32 * 0.5 * 24^(v/127) / fs (0.5..12 Hz), or the sync inc (§11.2)
     lfo = osc_sine(ph) ; shape: lfo = sat15((lfo * (32768 + 7 * 32768 * v_shape / 127)) >> 15)   (sine -> rounded square)
PAN (TW_MODE 0): theta = pi/4 * (1 + depth * lfo / 32767), gL = cos(theta), gR = sin(theta)  → per sub-block from a quarter-wave table; per-sample linear ramp
                 L = mul15(x, gL) ; R = mul15(x, gR)
AM  (TW_MODE 1): g = 32768 - ((depth * (32767 - lfo)) >> 16)  ; L = R = mul15(x, g)
WAH: env += (|x| - env) >> 3 if |x| > env else env -= (env >> 11) + 1          (follower, Q15 of the input)
     sweep = (env * (depth + 16)) >> 7 → row = clamp(sweep >> 9, 0, 63) with a 64-row cutoff table 300 Hz..2.5 kHz (log)
     k_res = 0.60 - 0.45 * reso/127 (Q15) → resonance; SVF (ZDF, Felucca pf_svf form) with WAH_SVF[row] = {a1, a2, a3} Q14 for the row's g = tan(pi f/fs) and the current k, interpolated between rows per sub-block
     out = 0.7 bp + 0.3 lp  ; L = R = out
```
SYNC applies to TRM only; in WAH the K2 knob is RESO.

### 9.3 CHORUS / PHASER
```
PHASER (port of Felucca perform.c ph_side, stereo, R 90° behind): PH_A[33] Q14 as in R06 (150 Hz..4.8 kHz)
  sub-block: q = tri(ph) ; qd = 32768 + ((q - 32768) * depth) >> 7 ; aL = lerp(PH_A, qd), aR = lerp(PH_A, qd - 16384)
  per sample per side: xs = clamp((x + 2) >> 2, ±16383); u = xs + mul15(fb, FB) (FB = CP_FDBK * 180 Q15)
      4 stages: y = clamp(px + ((a * (u - py) + 8192) >> 14), ±65535); px = u; py = y; u = y
      fb = clamp(y, ±32767); out = (x + ((xs + y) * 7 >> 1)) >> 1   ((x + ap)/2 * 0.9, *4 undone)
CHORUS (2 taps from one line of 1024 int16 in the pool, written at half level):
  cho_buf[w & 1023] = clamp16(x >> 1)
  sub-block: dL = (7 ms ± 3.5 ms * depth/127 * sine(ph)) in Q8 samples; dR the same with ph + WIDTH (CP_FDBK as L/R phase offset 0..180°)
  per sample: tapL = lerp(buf[w - dL>>8], buf[w - dL>>8 - 1], dL & 255) << 1 ; tapR likewise
              m = 16384 * min(127, 2 depth) / 127 ; L = x + mul15(tapL - x, m) ; R = x + mul15(tapR - x, m)
```

### 9.4 DELAY (D.DLY / A.DLY; `dly_buf[65536]` int16 at half level)
```
time: target_q8 = (SYNC != FREE ? delay_sync(SYNC) : ms_to_samples(TIME)) << 8, folded >> 1 while >= 65536 (cell shows "!")
      D: dl glides toward target by <= 256 (1 sample) per sample
      A: dl += (target - dl) >> 12 per sample (one-pole ≈ 100 ms) + wow (0.7 Hz sine, ±0.3 % of dl) + flutter (7 Hz, ±0.05 %) + LFSR noise LP'd (±0.02 %)
per sample: i = (w - (dl >> 8)) & 0xFFFF ; r = lerp(buf[i], buf[i - 1], dl & 255) << 1   (D uses the integer tap when dl is settled)
            fbp = D ? lp += mul15(r - lp, col) : softclip(lp += mul15(r - lp, mul15(col, 19661)))   (A: darker LP, saturation)
            hp (55 Hz) on the feedback as track_dist's hp
            buf[w & 0xFFFF] = clamp16((in_gated >> 1) + mul15(fbp, fb)) ; w++
            wet = mul15(r, wetlvl) ; L += wet ; R += wet   (stereo-summed input: in = (L + R) >> 1)
knobs: wetlvl = DEPTH * 258 (Q15) ; fb = min(27853, (DEPTH * 180 * FDBK) >> 7)  (0..0.85, FDBK scales) ; col = 2000 + TONE * 240
switch OFF: in_gated = 0 (the line keeps ringing) ; D <-> A: keep the buffer, crossfade the read over 32 samples
```
A BPM change glides the synced time (D at ≤ 1 sample/sample, A through its slew: audible tape bend).

### 9.5 REVERB (HALL, port of Felucca fx.c:46-90, 318-489, 606-613 verbatim, called per 32-frame sub-block)
Structure: input decimated to 22.05 kHz (1-2-1), ×2580, low cut; 2 Schroeder diffusers {131, 307} with gains
{11469, 10240} Q14; 8 lines {467, 509, 547, 593, 631, 673, 709, 743} at 22.05 kHz, lines 1 2 4 7 read through ±8-sample
sines at 0.37/0.74 Hz, lines 0 3 5 6 one-pole LP `32767 − damp·180` with remainder feedback; gains g = 2^(−D·59.2/65536·
2^(−SIZE/30))/√7 capped 0.99; Paley conference matrix; L = y0+y3+y4−y7, R = y1+y2+y5+y6; mono wet (L+R)·4 and side
(L−R)·4 upsampled linearly; quiet lines round toward zero so the tail ends in exact silence. Frodhes mapping: v =
RV_DEPTH 0..127 → `SIZE = 40 + v·70/127`, `send = v·258`, DAMP fixed 60; gains recomputed only when SIZE changes,
rate-limited to once per 50 ms while the knob turns; v = 0 → send 0, tail rings; the REV lamp lit while v > 0.

### 9.6 Master (port of Felucca `master_out`, fx.c:139-214)
DC blocker (2 Hz leaky integrator, Q6, error feedback) → peak limiter (T = 18000; attack `env += (a − env) >> 2`;
release `−((env − T) >> 12) − 1`; gain `(T << 15)/env`, a 32-bit divide only while limiting) → softclip → `out[2j] +=
L`, `out[2j+1] += R`. Also: `fx_peak` = block max |out| (ISR-written) and `fx_quiet_blocks` (blocks with `fx_peak <
16`, i.e. −66 dBFS); `fx_quiet()` = `fx_quiet_blocks ≥ 86` (500 ms). SPREAD (pan by key, ±45° max) is applied
per voice in `rh_render` only when SPREAD > 0 (then the voice sum is stereo from the start: two gains per voice, +1
multiply per sample; the mono stages up to TREM run on L and R separately in that case — budget +15 %, flagged on the
CPU page).

### 9.7 Budget and placement
| Stage | Target µs | Pool |
|---|---|---|
| DRIVE | 20 | — |
| TREM / WAH (worst WAH) | 29 | — |
| CHORUS / PHASER (worst PHASER) | 87 | 2 KiB |
| D.DLY / A.DLY (worst A) | 41 | 128 KiB |
| HALL | 105 | ≈ 12.8 KiB |
| master + splits | 30 | — |
| **total** | **≈ 310** | |

---

## 10. UI

### 10.1 PARAMS table (sound params: in the panel record and in presets; ids append-only; `P_COUNT` = 28 of 32 slots)

Stored values are bytes (`int8_t` for bipolar). "R10" = reface 0.0–10.0 display: `tenths = (v·100 + 63)/127`.

| id | key | cell | control | min | max | def | fmt | CC | law → engine |
|---|---|---|---|---|---|---|---|---|---|
| 0 | TYPE | TYPE | ALGORITHM; TYPE column | 0 | 3 | 0 | ENUM `ST77 SUIT FELT DYNO` (long `STAGE 77`, `SUITCASE`, `EARLY FELT`, `BELL DYNO`) | 80 (bands of 32; TX 16 + 32t) | `RH_TYPE[v]` at note-on; drive row; RLC row |
| 1 | DRIVE | DRV | K3 on PANEL | 0 | 127 | 13 | R10 | 81 | §9.1 |
| 2 | TW_SW | TREM | LFO tap (focused) / layer K1 | 0 | 2 | 0 | ENUM `OFF TRM WAH` | 17 (0–42/43–85/86–127; TX 0/64/127) | 10 ms crossfade |
| 3 | TW_DEPTH | DPTH | K1 (TREM focused) | 0 | 127 | 64 | R10 | 18 | §9.2 |
| 4 | TW_RATE | RATE / RESO | K2 | 0 | 127 | 90 | HZ (TRM) / R10 (WAH) | 19 | 0.5·24^(v/127) Hz; WAH resonance |
| 5 | TW_SYNC | SYNC | layer K2 | 0 | 13 | 0 | DIV | 20 | one LFO cycle per division; K2 steps divisions (backwards: clockwise = faster) |
| 6 | TW_MODE | MODE | layer K3 | 0 | 1 | 0 | ENUM `PAN AM` | 21 | |
| 7 | TW_SHAPE | SHPE | layer K4 | 0 | 127 | 0 | R10 | 22 | sine → rounded square |
| 8 | CP_SW | CHO | FX tap / layer K1 | 0 | 2 | 0 | ENUM `OFF CHO PHA` | 85 | |
| 9 | CP_DEPTH | DPTH | K1 | 0 | 127 | 50 | R10 | 86 | §9.3 |
| 10 | CP_SPEED | SPD | K2 | 0 | 127 | 50 | HZ | 87 | 0.05·160^(v/127) Hz |
| 11 | CP_SYNC | SYNC | layer K2 | 0 | 14 | 0 | DIV (+4BAR) | 23 | |
| 12 | CP_FDBK | FDBK / WIDE | layer K3 | 0 | 127 | 40 | R10 | 24 | PHA: fb = v·180; CHO: L/R phase 0–180° |
| 13 | DL_SW | DLY | ARP tap / layer K1 | 0 | 2 | 0 | ENUM `OFF DIG ANA` | 88 | |
| 14 | DL_DEPTH | DPTH | K1 | 0 | 127 | 40 | R10 | 89 | wet and feedback (§9.4) |
| 15 | DL_TIME | TIME | K2 (FREE) | 0 | 127 | 85 | MS | 90 | 20·50^(v/127) ms (36 ms at v = 19, 274 ms at 85) |
| 16 | DL_SYNC | SYNC | layer K2; PANEL K2 steps divisions when ≠ FREE | 0 | 13 | 0 | DIV | 25 | folded, `!` shown |
| 17 | DL_FDBK | FDBK | layer K3 | 0 | 127 | 90 | R10 | 26 | scales the feedback |
| 18 | DL_TONE | TONE | layer K4 | 0 | 127 | 70 | R10 | 27 | col = 2000 + v·240 |
| 19 | RV_DEPTH | REV | K4 on PANEL | 0 | 127 | 25 | R10 | 91 | §9.5 |
| 20 | TONE | TONE | VOICING K1 | −64 | 63 | 0 | BIP | 28 | `tone_off = v · 2^25 / 128` (±0.5 gap) |
| 21 | BARK | BARK | VOICING K2 | 0 | 127 | 64 | R10 | 29 | tip scale 2^((v−64)/64) via `pamp_scale`, gain compensated by `GSCALE_INV` |
| 22 | BELL | BELL | VOICING K3 | 0 | 127 | 64 | R10 | 30 | O1/O2 b scale 2^((v−64)/32) |
| 23 | HAMMER | HAMR | VOICING K4 | 0 | 127 | 64 | R10 | 31 | `hammer_h = (v − 64)·64` Q12: band shift ±1 at the ends, contact time ×2^(∓0.5) |
| 24 | DECAY | DCAY | VOICING 2 K1 | 0 | 127 | 64 | R10 (hot `×1.00`) | 102 | `k_decay = DECAY_K[v]` (S = 2^((v−64)/32)) |
| 25 | RELEASE | REL | VOICING 2 K2 | 0 | 127 | 64 | R10 | 103 | `k_rel = REL_K[v]` |
| 26 | NOISE | NOIS | VOICING 2 K3 | 0 | 127 | 48 | R10 | 104 | 0 = off; thumps/felt at −60 + 36·v/127 dB |
| 27 | SPREAD | SPRD | VOICING 2 K4 | 0 | 127 | 0 | R10 | 105 | pan by key ±(v/127)·45° |

Global params (settings only): G0 BPM (`bpm10` 400–2400, def 1200; SELECT role BPM, GLO K1, PLAY tap), G1 CLOCK
(`INT AUTO USB`, def AUTO), G2 KEYVEL (1–127, def 96; GLO K3, SELECT role DYN), G3 TUNE (±50 c, GLO K4), G4 MIDICH
(0 = ALL, 1–16; MIDI K1), G5 CCTX (off), G6 VELCRV (`SOFT MED HARD FIX`, MIDI-in only), G7 CLKOUT (off), G8 SC_MODE
(`OFF SNAP DEG`), G9 SC_SCALE (11 scales), G10 SC_ROOT (C..B), G11 SC_TRANS (±24), G12 SELROLE (`DYN BPM LAST`, def
DYN), G13 ACCEL (on), G14 HELP (on), G15 LEDS (`FULL DIM OFF`), G16 SCLLED (on), G17 COLOR (palette index), G18 MIDIQNT
(off), G19 HALFPED (on), G20 HUMAN (0–16). Velocity curves: SOFT `v' = 127·(v/127)^0.7`, MED linear, HARD `^1.4`, FIX
100 (tables of 128).

Other MIDI: CC 64 sustain (continuous when HALFPED), CC 120/123 all sound/notes off, CC 1 optional → TW_DEPTH
(MENU), pitch bend ±2 semitones (`bend_t` via `exp2_q30`; applied to new notes and to sounding notes at the next
sub-block through §5.6, cost 10 evaluations per voice per bend event, rate-limited to once per block), PC 0–11 →
F01–F12, PC 16–31 → U01–U16. CC TX sends the stored v with the bands above, rate-limited to ≥ 10 ms per CC.

Descriptor: `typedef struct { const char *lab4, *name; int8_t lo; uint8_t hi; int8_t def; uint8_t fmt, cc, where;
const char *const *names; } pdesc_t;` A host lint checks `lab4` ≤ 4 chars, `name` ≤ 13, CCs unique, defaults in range.

### 10.2 Screen layouts (240×240; FONT_S 8×16, FONT_L 16×32 capitals; palette `pal[]` from gfx.c: C_LINE, C_DIM,
C_GRAY, C_AMB, C_HI; C_ACC = the palette accent for focus/hot fills with black text)

**PANEL**
```
y 0   header FONT_S y2: cols 0-14 "F03 SUITCASE*", cols 16-20 BPM "120.0" (inverted 1 s after a change), beat square 6x6 at (172,7),
      cols 23-25 "SNP"/"DEG", cols 27-29 "PED"
y 20  rule
y 22  7 column titles, 32x16 each at x = 2 + 34 i, centred; focused (cols 2-4) inverted
y 40  row A: TYPE row 0 / switch A labels (TRM CHO DIG) ; y 56 TYPE row 1 ; y 64 OFF lamp 6x6 at column centre (cols 2-5) ;
y 72  TYPE row 2 ; y 88 row B: TYPE row 3 / switch B (WAH PHA ANA)
y 105 rule
y 108 slot 1 label (C_GRAY) ; y 126 gauge 28x6 at x+3 (frame C_LINE, fill v*26/127 C_AMB) ; y 136 value (C_HI; hot = inverted 1.5 s)
y 154 slot 2 label ; y 172 gauge ; y 182 value
y 201 rule
y 204 footer row 1: knob map "K1 DPTH K2 TIME K3 DRV K4 REV" ; y 222 row 2: hints/messages "LFO:TRM FX:CHO ARP:DLY PLY:TAP"
      hot readout replaces both rows: FONT_L y 205 centred, e.g. "TIME 274MS", "RATE 4.75HZ", "1/1>1/2 BUF", for 1.5 s
```
Column i: TYPE(0) rows = the 4 types; DRIVE(1) slot 1 = AMT; TREM(2) DPTH / RATE|RESO; CHO(3) DPTH / SPD; DLY(4)
DPTH / TIME|DIV; REV(5) slot 1 = REV + lamp; VOL(6) slot 1 = LVL from the MASTER ADC (0..1023 → R10, redrawn when it
moves ≥ 8 counts). Value strings ≤ 4 chars: R10 `0.0`–`10.0`; MS `36`–`999` then `1.0S`; DIV names; BIP `-5.0`–`+5.0`.

**Hold layer** (LFO/FX/ARP held ≥ 400 ms or combo): replaces y 106–199 with 4 cards 59×94 at x = 60k: label y 110,
value FONT_L y 132 (≤ 3 chars, else FONT_S y 140), gauge (x+4, 170) 51×6, `K1`..`K4` y 180. Cards: TREM `SW SYNC MODE
SHPE`; CHO `SW SYNC FDBK —`; DLY `SW SYNC FDBK TONE`. Footer `[LFO] TREM LAYER` / `LET GO: PANEL`. Drawn as 2 strips.

**Pages with 4 cards** (VOICING, VOICING 2, SCALE, GLO, MIDI): header as PANEL (page name; TYPE short name at cols
23–25 on VOICING pages); cards 59×80 at (60k, 24): label y 28 (≤ 7 chars), value y 52, gauge (x+4, 78), `Kn` y 86;
graph 240×94 at y 106 in 2 strips: VOICING = the Φ̃ curve for the current TONE (240 points) + KEY VEL marker; VOICING
2 = decay sketch (T60·DECAY of the last note, release); SCALE = degree numbers y 112 + `draw_keys` at y 140 (root
C_ACC, scale C_KEY, out C_OFF, silent black keys C_LINE); GLO = BPM FONT_L y 112, 4 beat squares 16×16 at x = 72 + 28k
y 152, source text y 176; MIDI = last message text y 120 + clock state.

**SAVE page:** header `SAVE TO USER`; rows y = 28 + 20r, 8 per view, `U01 ST77 TRM PHA DD`, selected inverted,
empty `U05 ----`; footer `[PRESETS]SLOT [OCT+]SAVE` / `[OCT-]CANCEL` or `SAVING WHEN QUIET`.

**MENU** (HOME held 700 ms): rows FONT_S y = 24 + 18r: SELECT ROLE, KNOB ACCEL, HELP, LEDS, SCALE LEDS, COLOR, MIDI IN
QNT, HALF PEDAL, HUMANIZE, INFO, CPU, FACTORY RESET (scrolls; PRESETS moves, K1–K4/OCT± change, OCT+ opens INFO/CPU/RESET
(`SURE? OCT+`), HOME exits and saves). INFO: `v<ver>`, `BAT <raw>`, `ISR <max us>`, `POOL <KB>`, `EV DROP <n>`.
**CPU page:** one row per stage (name, cur µs, max µs, bar to 2 900), total, averaged load %, voices/modes, late and
shed counts, the 16-bin histogram as a sparkline; OCT− resets maxima; redrawn at 4 Hz on changed rows only.

**Hint strings** (≤ 30 chars, linted): PANEL `K1 DPTH K2 TIME K3 DRV K4 REV` / `LFO:TRM FX:CHO ARP:DLY PLY:TAP`; first
boot `HOLD LFO/FX/ARP: MORE`; VOICING `K1TONE K2BARK K3BELL K4HAMR` / `[ENV]DECAY  [HOME]PANEL`; VOICING 2 `K1 DCAY K2
REL K3 NOIS K4 SPRD` / `[EDIT]VOICING [HOME]PANEL`; SCALE `K1 MODE K2 SCAL K3 ROOT K4 TRN` / `HOLD SEL + KEY: ROOT`; GLO
`K1 BPM K2 CLK K3 VEL K4 TUNE` / `[PLAY]TAP [GLO]MIDI PAGE`; MIDI `K1 CH K2 CCTX K3 VCRV K4 CLKO` / `[GLO]PANEL`.
Messages (row 2, 1.5 s): `TYPE SUITCASE`, `EXT CLOCK`, `UNDO`, `REDO`, `SAVED U05`, `SAVING WHEN QUIET`, `CLOCK LOST`,
`PRESET F04 80S BALLAD`, `ROOT D  DOR`, `SEQ: LATER`.

**Draw scheduler:** each element has a dirty bit and an estimated cost (bytes/1500 ms); one element per pass, only
once the previous blit has had its estimated time (or `lcd_busy()` if exposed); priority hot cell → header → footer
→ columns → graph strips. Redraw costs at 12 MHz SPI (ESTIMATE): knob turn 2.8 + 18.2 KB (14 ms over 2 passes); switch
tap 12 KB (8 ms); page change 115 KB (≈ 12 passes). A host test (`HOST_BLITMAX`) asserts ≤ 240×64×2 B per pass.

### 10.3 Button state machine (TAP_MS 250 for REC; HOLD_MS 400 for layers and SEL; LONG_MS 700 for HOME, SAVE;
QUIET_MS 250 knob-ignore after a layer closes; any other input while held = combo: the layer opens at once and the
release is not a tap)

| Button | Context | Tap | Hold ≥ threshold | Release after hold/combo |
|---|---|---|---|---|
| LFO / FX / ARP | PANEL, column unfocused | focus TREM / CHO / DLY (sound unchanged) | open the column layer: K1–K4 edit SW SYNC x y; keys keep playing | close; ignore knobs QUIET_MS |
| LFO / FX / ARP | PANEL, column focused | toggle OFF ↔ `last_on[col]` (saved) | layer | close |
| LFO / FX / ARP | any page / MENU / SAVE | cancel if needed, go to PANEL, focus (no toggle) | layer over PANEL | PANEL |
| EDIT | any | VOICING; on VOICING → PANEL | — | — |
| ENV | any | VOICING 2; on it → PANEL | — | — |
| SEL | any | SCALE; on SCALE → PANEL | peek: key LEDs show the scale, header `C DOR SNAP`; **key k: ROOT = (k+5) % 12**, silent, no MIDI; K1–K4 edit | previous screen |
| GLO | any | GLO; on GLO → MIDI page; on MIDI → PANEL | SELECT = BPM ±0.1 | — |
| HOME | any but MENU | PANEL (cancels SAVE/dialogs) | ≥ LONG_MS: MENU | — |
| HOME | MENU | exit (save settings) | — | — |
| SAVE | PANEL / pages | SAVE page | ≥ LONG_MS: UNDO / REDO the last preset load | — |
| SAVE | SAVE page | cancel | — | — |
| PLAY | any | tap tempo (§11.1); `EXT CLOCK` message when locked externally | — | — |
| REC | any | sustain on at press; released < TAP_MS flips the latch; ≥ TAP_MS releases and clears the latch | | |
| OCT− / OCT+ | PANEL / pages | octave ±1, clamp ±2; both within 100 ms → 0 | — | — |
| OCT− / OCT+ | SAVE / dialog / MENU | cancel / confirm (MENU: OCT− back, OCT+ open) | — | — |
| SEQ | any | `SEQ: LATER` (Phase 8 looper) | — | — |

Encoders (any non-zero count is input and makes a held button a combo): ALGORITHM = TYPE (clamped, message);
PRESETS = browse F01..F12, U01..U16 (clamped; loads after 400 ms still, §12.1) / SAVE slot / MENU row; SELECT = role
(DYN: KEY VEL ±1 with acceleration; BPM: ±1.0; LAST: the last-turned param); K1, K2 = focused column slots (DIV steps
when synced) / layer cards 1–2 / page cards 1–2 / SAVE: K1 = slot / MENU value; K3, K4 = DRIVE, REVERB / cards 3–4.
Acceleration (ACCEL on): detents within 50 ms of the previous add ×4 for ranges > 32 steps. Edge cases: page changes
never change the focus (saved, default TREM); a layer release during QUIET_MS ignores K1–K4 detents.

### 10.4 LEDs (evaluated once per pass into a picture, then written; "dim" = `fm1_led_dim`)
LFO/FX/ARP: full = the column's switch ≠ OFF; dim = OFF and focused; off otherwise. EDIT/ENV/SEL/GLO: full on
their page (GLO on GLO or MIDI; SEL full while held). HOME: full on PANEL, dim in MENU. SAVE: full while edited (`*`);
blinks 4 Hz while a save waits for quiet. PLAY: beat flash (1/6 beat; 1/2 beat on beat 1) only while a synced effect
is on, the clock is external and locked, or clock out is on; tap feedback 80 ms. REC: full while sustain is on from
any source; CC64 half-pedal > 0 and < 64: dim. OCT±: full when octave < 0 / > 0. SEQ: off. Keys: scale OFF → key k
full while it holds a note or its note rings under sustain (per key); scale ON + SCALE LEDS → scale tones dim, root
full, held keys full, DEG black keys off, SNAP out-of-scale off; SEL held → as scale ON; LEDS = DIM/OFF → every full
becomes dim / all off except REC and the beat.

### 10.5 Settings record (always 256 B; saved 2 s after the last change if it differs (memcmp), when `rh_quiet() &&
fx_quiet()`, through §12.3)

| off | size | field |
|---|---|---|
| 0 | 1 | `ver` = 1 |
| 1 | 1 | `flags`: b0 edited, b1 CC TX, b2 CLK OUT, b3 HELP, b4 ACCEL, b5 SCALE LEDS, b6 MIDI QNT, b7 HALF PEDAL |
| 2 | 1 | `oct` int8 −2..2 |
| 3 | 1 | `preset` 0–11 F, 16–31 U, 255 none |
| 4 | 2 | `bpm10` LE |
| 6..19 | 14 | `clock`, `midi_ch`, `key_vel`, `vel_curve`, `tune` int8, `sel_role`, `focus`, `color`, `leds`, `humanize`, `sc_mode`, `sc_scale`, `sc_root`, `sc_trans` int8 |
| 20 | 3 | `last_on[3]` (1/2) for TREM, CHO, DLY |
| 23 | 1 | `hints_seen` |
| 24 | 32 | `panel[32]` sound params (28 used) |
| 56 | 32 | `undo[32]` |
| 88 | 1 | `undo_valid` |
| 89 | 167 | reserved 0 |

Load: every byte range-checked against PARAMS (out of range → default); `ver > 1` → defaults; new fields are
appended with defaults. Fixtures `tests/fixtures/settings_v*.bin` must load.

### 10.6 Preset record (48 B) and factory presets
`ver` (1), `np` (params stored, 28; ids ≥ np take defaults), `used`, reserved, `name[12]` (ASCII 32..95, space
padded, FONT_L-safe), `p[32]`. Auto-name on save: `TYPE4 + " " + TRM|WAH + CHO|PHA + DD|AD` (≤ 12 chars). Factory
(R10 bytes = round(x·12.7); switch 0/1/2; `—` = default): F01 STAGE 77 (0, 13, off, off, off, 25); F02 70S PHASER
(0, 25, off, 2/83/38/0/50, off, 32); F03 SUITCASE (1, 13, 1/64/64/0/0/40, off, off, 25); F04 80S BALLAD (2, 0, off,
1/64/50/0/40, 1/38/85/0/90/70, 64, BELL 80); F05 SLAPBACK (2, 13, off, off, 2/64/19/0/60/50, 13); F06 BRIGHT CHORUS (3,
32, off, 1/32/38/0/40, 1/19/19/0/60/90, 25, TONE +16); F07 FUNK WAH (0, 51, 2/89/64/0/0/0, off, off, 13, BARK 80); F08
BARK (0, 76, off, off, off, 13, BARK 100, HAMR 90); F09 TAPE ECHO (1, 19, off, off, 2/57/—/8/100/50, 38); F10 DREAM (2,
0, 1/38/—/6/0/0, 2/51/25/0/60, 1/51/—/9/100/70, 89, DCAY 80); F11 RADIO (3, 115, off, off, off, 0, TONE +30); F12 AM
TREMOLO (0, 13, 1/70/100/0/1/90, off, off, 25). (TYPE indices after S17: 0 STAGE 77, 1 SUITCASE, 2 EARLY FELT, 3 BELL
DYNO; values ESTIMATE, tuned by ear in P7-4.)

### 10.7 Host UI tests (`tests/scripts/ui_*.txt`, harness verbs of §14.1)
1 boot/shot (7 columns; FOCUS=2 TW_SW=0; layout lint); 2 focus/switch (no WAH between TRM and OFF; FX tap only
focuses); 3 layer (ARP held, K1 SW → DIG, K2 SYNC → 1/4; detents in QUIET_MS ignored); 4 TYPE keeps FX; 5 knob clamp
(±300 detents, no wrap); 6 tap tempo (4 taps at 500 ms → 120.0; restart after 2 s); 7 BPM on SELECT and GLO+SELECT;
8 delay sync fold (`1/2!`, echo at 1.000 s ± 1 ms); 9 clock follow (100 BPM ± 0.5 within 2 beats; `CLOCK LOST` at
500 ms; tap → `EXT CLOCK`); 10 SNAP (C#4 → 60; refcount); 11 DEGREES (Felucca `scale_test.c` table); 12 SEL+key root
(no MIDI); 13 scale LEDs per key; 14 SELECT DYN → velocity 56; 15 settings round trip (256 B, ver 1); 16 save waits
for quiet; 17 sustain regression; 18 lint; 19 redraw budget; 20 FX switch-off tail and D↔A swap (no step > 2000); 21
preset browse settle + undo; 22 save flow and auto-name; 23 MIDI CC/PC; 24 CC TX rate limit; 25 preset load
crossfade (click detector); 26 pitch bend (±2 st, C1 on a bent note ≤ 2 c); 27 half-pedal (CC64 = 54 → release T60
× ≈ 1/0.7, ESTIMATE check ±30 %).

### 10.8 Developer CC map (Phase 2, before the panel exists) and the profiler dump
CCs 20–31, 102–105 as above drive TONE/BARK/BELL/HAMMER/DECAY/RELEASE/NOISE/SPREAD from a DAW; CC 102 = result id, CC
108 = field, CC 103–107 = 32-bit value in 7-bit chunks stream the profiler (`tools/bench_read.py` decodes to CSV);
PLAY on the CPU page streams it on demand. No platform change needed (SysEx hook deferred).

---

## 11. Tempo, sync and scales (`tempo.c`, `scale.c`; main loop; ISR reads volatiles)

### 11.1 Tempo engine (ticks; `scratch-05/tempo_ticks.py`)
```c
typedef struct {
    uint16_t bpm10;                  /* 400..2400 */
    uint8_t  src;                    /* 0 INT, 1 USB locked (AUTO follows while pulses arrive) */
    uint32_t pulse_ticks, pulse_rem; /* 600000000 / bpm10, 600000000 % bpm10 (32-bit divides) */
    uint32_t next_pulse, acc_rem;    /* Bresenham over 24 PPQN, in ticks */
    uint8_t  pulse;                  /* 0..23 */
    uint32_t tap[4]; uint8_t ntap;   /* ticks */
    uint32_t in_last, in_acc; uint8_t in_n;   /* clock follow: ticks */
} tempo_t;
static volatile uint32_t beat_samples_v = 22050;      /* 26460000 / bpm10 */
tempo_set10(b): bpm10 = clamp(b, 400, 2400); pulse_ticks = 600000000u / bpm10; pulse_rem = 600000000u % bpm10;
                beat_samples_v = 26460000u / bpm10; recompute sync incs (§11.2)
tempo_tap(): now = fm1_ticks(); if (ntap && now - tap[ntap-1] > 48000000u) ntap = 0;        /* 2 s */
             if (ntap == 4) shift; tap[ntap++] = now;
             if (ntap >= 3) { span_t = (tap[ntap-1] - tap[0]) / 2400u;  /* 0.1 ms units, 32-bit */
                              tempo_set10(6000000u * (ntap - 1) / span_t); re-phase: pulse = 0, next_pulse = now; }
tempo_poll(): now = fm1_ticks(); while ((int32_t)(now - next_pulse) >= 0) {
                 next_pulse += pulse_ticks; acc_rem += pulse_rem; if (acc_rem >= bpm10) { acc_rem -= bpm10; next_pulse++; }
                 if (clk_out && src == 0) midi_send(0xF8, 0, 0); pulse = (pulse + 1) % 24; beat_led(pulse); }
tempo_f8(stamp): dt = stamp - in_last; in_last = stamp; if (dt < 192000u || dt > 1920000u) { in_n = in_acc = 0; return; }  /* 8..80 ms */
                 in_acc += dt; if (++in_n == 24) { beat_01ms = in_acc / 2400u;            /* ticks per beat -> 0.1 ms units */
                     bpm10_new = 6000000u / beat_01ms;                                      /* two 32-bit divides, no 64-bit */
                     bpm10 += (bpm10_new - bpm10) / 4, or snap when |diff| > 10 %; tempo_set10(bpm10); in_n = in_acc = 0; }
                 pulse = (pulse + 1) % 24; src = 1; last_pulse = stamp
AUTO: src falls back to 0 when fm1_ticks() - last_pulse > 12000000u (500 ms): message CLOCK LOST, keep the last BPM.
FA: pulse = 0, synced LFO phases = 0 ; FB: continue ; FC: stop following until the next F8
```
Stamps: the main loop stamps each F8 with `fm1_ticks()` as it drains `midi_get` (jitter = one pass, averaged
over 24 pulses). `beat_samples_v` ≤ 66 150; every sync product below stays < 2^31.

### 11.2 Sync table (1/96-quarter units) and increments
`SYNC_NAME[] = {FREE, 1/32, 16T, 1/16, 8T, 16D, 1/8, 4T, 8D, 1/4, 4D, 1/2, 1/1, 2BAR, 4BAR}`, `SYNC_UNITS[] = {0, 12,
16, 24, 32, 36, 48, 64, 72, 96, 144, 192, 384, 768, 1536}` (4BAR for chorus only). `sync_samples(i) = beat_samples_v ·
SYNC_UNITS[i] / 96`. Delay: `while (s >= 65536) s >>= 1` (fold, `!` shown). LFO: `inc = 0xFFFFFFFFu / s` (one 32-bit
divide, error ≤ 1.2·10⁻⁴ cycle). Recomputed only on BPM or knob change and published as volatiles; LFO phases are
reset to 0 on the 3rd tap and on FA. Rate knob with SYNC ≠ FREE walks the table backwards (clockwise = faster).

### 11.3 Scales (port of Felucca `seq.c` SCALE_MASK, scale_snap, kb_pitch WHITE branch; test table ported)
Masks (bit i = i semitones above ROOT): MAJ 0 2 4 5 7 9 11; MIN 0 2 3 5 7 8 10; DOR 0 2 3 5 7 9 10; MIX 0 2 4 5 7 9
10; LYD 0 2 4 6 7 9 11; PHRY 0 1 3 5 7 8 10; HARM 0 2 3 5 7 8 11; MEL 0 2 3 5 7 9 11; PEN 0 2 4 7 9; MPEN 0 3 5 7 10;
BLUES 0 3 5 6 7 10. Key k (0 = F3) → n = 53 + k. OFF: n + 12·oct + TRN. SNAP: `scale_snap(n + 12·oct + TRN)` rounds
*down* to the nearest scale tone. DEG: white keys walk degrees with `DEGREE[12] = {0,−1,1,−1,2,3,−1,4,−1,5,−1,6}`,
C4 = degree 0 = 60 + ROOT, black keys silent (255); TRN and octave added after. `held[k]` stores the resulting note;
`cnt[128]` reference counts (second key on the same note: no retrigger; first key-up: no cut). A change while keys
are held ends exactly the note each key started. MIDI in is not quantised unless MIDI QNT. Header shows `SNP`/`DEG`.

---

## 12. Presets and the flash protocol (`presets.c`, `storage.c`)

### 12.1 Flows
- **Browse/load:** PRESETS detents move the candidate; the header shows its name inverted; **400 ms after the last
  detent** the candidate loads: if `edited && !undo_valid` copy the panel to `undo` (set `undo_valid`); copy p[] to the
  panel; set `preset`; clear `edited`; message. Effects follow U8 (tails ring; switches crossfade; nothing clicks).
- **Edit:** any sound-param change sets `edited` when the panel differs from the loaded preset (memcmp).
- **Undo/redo:** SAVE held swaps panel ↔ `undo`; `UNDO` then `REDO`.
- **Save:** SAVE → SAVE page; PRESETS/K1 pick U01..U16; OCT+ on a used slot → `OVERWRITE? OCT+`; a second OCT+ queues
  the write with the auto-name; the write runs when quiet (SAVE LED blinks, `SAVING WHEN QUIET`), then `SAVED U05`,
  `preset` = that slot, `edited` cleared. `undo_valid` is cleared only on a save. Never refuse a save: queue it.

### 12.2 Bank storage (port of Felucca `storage.c`)
16 records = 768 B in one object with a 32 B header (magic `FRPB`, seq, length, CRC32), A/B sectors 0x97000/0x98000:
write the inactive sector (erase, data, header last), read back, then it is the newest valid copy; load picks the
valid copy with the higher seq. Mirrored in RAM (768 B in the pool) so browsing never reads flash. Factory presets
are `const`. Torn-write test: a simulated NOR with a cut after every programmed byte and during every erase.

### 12.3 Flash write protocol (`app_flash_begin/end`, main loop)
```
app_flash_begin(): require rh_quiet() && fx_quiet() (else return BUSY: the caller retries next pass)
                   post EV_MUTE(1); wait until rh_mute_ack ≥ 2 blocks (the DMA now replays two silent halves)
                   -- the erase/program (interrupts off ≤ 400 ms worst, ≈ 50 ms typical, measured K14) loops silence
app_flash_end():   clear the delay line (memset 128 KiB ≈ 0.3 ms), chorus line, HALL lines (they were < −66 dBFS: the
                   clear only removes the time discontinuity); post EV_MUTE(0)
```
Used by settings (platform `settings_save`) and the preset bank. Flash wear: never save in a loop; settings only
when changed (memcmp).

---

## 13. Platform deltas (upstream Dinghy files Frodhes touches)

| File | Change | Form | Upstream? |
|---|---|---|---|
| `firmware/src/gfx.c` | host-mode `.pool` guard | done | yes |
| `firmware/src/platform.c` | `#ifndef CV_MAX` (keep 240×64; the hook costs nothing); `APP_FL_RANGE(off, n)` OR'ed into `FL_RANGE_OK` for 0x97000–0x98FFF; `settings_save` unchanged but called only inside §12.3 | override macros defined before `#include "platform.c"` | yes (generic hooks) |
| `firmware/src/platform.c` (optional, Phase 0) | accumulate ticks spent in `fm1_timer5_irq` while `in_audio`, exposed as `volatile uint32_t nest_ticks` | 3 lines | yes |
| `tests/host.c` | harness verbs (§14.1), `fm1_in.buttons`, releases, encoders, velocity, LED log, `HOST_STORE`, `HOST_BLITMAX`, `expect`, exit code, `RENDER_ONLY`, profile | additive under `DINGHY_HOST` | yes |
| `tools/build.py` | identity rules (D3); forward `FRODHES_BENCH`/`FRODHES_CRASHTEST`; call `tools/frodhes_checks.py`; pool headroom gate | small | partly |
| `firmware/src/lcd.c` | `LCD_BAUD` is already `#ifndef` | none | — |

Each delta is one commit; README keeps the table; PRs offered to zvenson/dinghy; the nightly `upstream` job merges
`upstream/main` into a scratch worktree and runs `make test`.

---

## 14. Test, CI and release

### 14.1 Host harness verbs (`tests/host.c`; one line per event: `T verb args`)
`keys MASK` (as now) · `key K VEL` / `keyup K` · `down ID` / `up ID` / `btn ID` (press + release 50 ms) / `hold ID MS` ·
`enc ID N` · `adc N` · `midi ST D1 D2` · `clock BPM10 MS` (F8 at the exact period, Bresenham) · `shot NAME` · `dump`
(prints `P TYPE=0 … G BPM=1200 … FOCUS=2 PAGE=PANEL LED …`) · `expect KEY=VAL` / `expect led ID 0|1|dim` / `expect
midi ST D1 D2` (PASS/FAIL, exit code = failures) · `profile` (host instructions per block) · `end`. Environment:
`HOST_STORE=path` persists settings and the bank; `HOST_BLITMAX=1` prints the largest per-pass blit; `RENDER_ONLY=1`
skips screens. `fm1_led_key` logs changes.

### 14.2 Levels
| Level | What | Gate |
|---|---|---|
| U unit | `tests/unit/*.c` + Python generator tests; ASan/UBSan `-fsanitize=address,undefined,integer-divide-by-zero -fno-sanitize-recover=all` | exact or stated tolerance |
| G golden | every `tests/scripts/*.txt` → WAV/PPM → FNV-1a 64 in `tests/golden/{audio,screens}.txt`; `GOLDEN_UPDATE=1` rewrites with a PR justification | hash equality (integer code is bit-exact across compilers when UB-free; UBSan guards that) |
| H health | `tests/check_render.py` on every render | §8 health limits |
| A analysis | `tools/analyze.py` C1–C13, A8, A11 vs `reference_<id>.json` | §8 thresholds, per-source trust |
| C cost | host instructions per sample (macOS `proc_pid_rusage`, Linux `perf stat -e instructions:u`) vs `tests/cpu_baseline.txt` (+25 %); static loop metric of `rh_render`, `rh_mode_run`, `rh_pickup_run`, `rh_events`, `rh_load`, `fx_*`, `fm1_alnk0_irq` from `build/frodhes.dis` vs `tests/target_budget.txt` (+10 %); device µs = instructions × `tests/device_ratio.txt` (1.0 until Phase 0) ≤ 2 400 per block and ≤ each stage alarm | |
| F fuzz | MIDI byte streams, settings/preset records, key/knob storms; fixed seed; 20k iterations in CI, 1M nightly | no sanitizer error, no hang (1 host second renders ≤ 2 s wall), no stuck voice after all-notes-off + pedal up |
| D device | checklist below, `tools/fm1_profile.py`, `tools/fm1_capture.py` | recorded in `docs/model/reports/device-<phase>.md` |
| L listening | T-LISTEN (§8) | recorded |

**Unit test contents:** `fxp.h` helpers (edges, rounding); `exp2_q30` ≤ 2e-7; MC3 kernel per key and mode from a full hit
and from −60 dB: round-off ≤ −90 dB re peak, T60 ±2 %, voice off before 2·T60 + 1 s, no state > 2^28; coefficient path
(§5.6 thresholds); pickup (C5, zero input → exactly 0, ROM-window scan); hammer tables (monotone VAMP, T_c range,
layer continuity, R dB ±0.1 for R = 12..36); allocator (same-note superposition, steal order, fade, pedal, refcount);
tempo (`beat_samples` vs exact ±1 sample for every bpm10; sync overflow at 40 BPM × 4BAR; tap 4 × 500 ms → 120.0;
follower with ±3 ms jitter → ±0.5 BPM after 2 beats, 500 ms → INT); scales (Felucca table); params/settings
(formatting ≤ 5 chars, fixtures, CRC single-bit flip); storage (torn writes); fx (`softclip` continuity, `osc_sine`
range, HALL RT60 and no ringing via the ported `reverb_test`, delay time exact, phaser notch sweep, tremolo equal power).

### 14.3 Device checklist (every phase; `docs/model/reports/device-<phase>.md`)
1 `./build.sh` passes; record image/RAM/pool. 2 `fm1_install.py --info` identity and version before/after. 3 cold boot,
warm reboot, 10-minute soak: no crash screen, no dropout. 4 all 27 keys, OCT ±2, REC hold and latch, CC64, MIDI v =
1/64/127, USB audio capture running. 5 profile build: the P2 worst case gives `prof.max` within the phase budget, mean
≤ 80 %, USB audio on. 6 capture conformance: `fm1_capture.py` plays `tests/scripts/conformance.mid`, records UAC
with MASTER at maximum, aligns per note, residual vs the host render ≤ −60 dB. 7 phase items (UI vs goldens; LEDs;
save-when-silent with no click; power cut during a save in Phase 7). 8 recovery drill at Phase 0 and every release:
the `CRASHTEST` build → auto-rescue after two boots; OCT− rescue; reinstall.

### 14.4 Repo layout, make targets, CI
```
Makefile  .local/{jieli,fw-AC79_AIoT_SDK,venv,reference,sources}  .github/workflows/{ci,nightly,release}.yml
firmware/src/{frodhes.c,rhodes.c,fxp.h,dsp.c,fx.c,params.c,ui.c,tempo.c,scale.c,presets.c,storage.c,bench.c}
firmware/gen/rhodes_tables.h   tools/{gen_rhodes.py,tine_fe.py,tinebar.py,pickup.py,hammer.py,rhodes_model.py,
fit_model.py,fit_reference.py,fetch_reference.py,fetch_sources.py,analyze.py,dsp_analysis.py,frodhes_checks.py,
target_budget.py,bench_read.py,fm1_profile.py,fm1_capture.py,ab.py,release.py,lint.py}
tests/{host.c,run_host.sh,unit/,fuzz/,scripts/,fixtures/,golden/,cpu_baseline.txt,target_budget.txt,device_ratio.txt,
analysis_thresholds.toml,check_render.py}   docs/model/data/{sources.json,params.json,reference_<id>.json,CAPTURE.md}
docs/model/{BUDGET.md,reports/}
```
Make: `setup` (venv pinned; toolchain sha256 + SDK's 3 files into `.local/`), `host`, `unit`, `golden[-update]`,
`health`, `tables[-check]`, `reference` (fetch), `fit`, `analyze`, `cost`, `budget`, `fuzz [FUZZ_ITERS=]`, `lint` (no
float/double tokens in `firmware/src`; SPDX headers; no tracked audio/PDF/asm under docs; 1 MB size cap outside
`firmware/gen`), `test` (lint unit tables-check golden health cost fuzz analyze), `fw`, `install`, `info`, `release
V=0.N`, `sources`, `upstream-check`. CI (`ubuntu-24.04`): `host` (make test; artifacts), `firmware` (cached toolchain
keyed on `toolchain.lock`, native build, `make budget`, upload `.fwsc` and `.dis`), `licence` (lint + tracked-file
scan + SPDX); nightly `fuzz-long` and `upstream`; `release.yml` on `v0.*` tags builds the bundle (`.fwsc`, `-app.bin`,
`SHA256SUMS`, `LICENSE`, `LICENSING.md`, `LICENSES/`, source tarball, CHANGELOG excerpt, analysis/device/listening
reports) as a **draft**. `main` protected; one PR per task; commits end with the session's attribution lines.

### 14.5 Release gate and milestones
Identity `FM-1_950` dev; `951`–`959` = 0.1–0.9 (`build.py` refuses others until a second block is agreed). Milestones:
0.1 Phase 2 voice; 0.2 Phase 3 realism; 0.3 panel; 0.4 effects; 0.5 tempo/scales; 0.6 presets/MIDI; 0.9 RC for the
hub. Gate: CI green on the tag; device checklist incl. recovery drill; `--info` identity/version; hub ID free; CHANGELOG
and README status. Then `git tag -s`, push, publish the draft, then the hub. `.Z` = hotfix on the same identity.

---

## 15. Tasks

Sizes: S ≤ ½ day, M 1–3 days, L 3–6 days. One PR per task; AC = acceptance criteria (all of them).

### H hygiene
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| H1 | Sources manifest `SOURCES.lock` + `tools/fetch_sources.py` (history already purged) | — | cache rebuilt with matching sha256 | S |
| H2 | `tools/lint.py` (float tokens, SPDX, forbidden tracked types, size cap); rename toolchain paths to `.local/` | — | `make lint` fails on a planted `.wav` under docs and on `double` in `firmware/src` | S |
| H3 | Makefile, `.local/` setup, `requirements.txt`, `toolchain.lock` | — | a fresh clone builds host and firmware with `make setup` only; nothing written outside the project but Docker images | M |
| H4 | `ci.yml` host + licence jobs; branch protection | H2 H3 | PRs show required checks | S |
| H5 | CI firmware job with caches | H3 H4 | `.fwsc` artifact per PR; cached run < 5 min (ESTIMATE) | M |
| H6 | Identity rules in `build.py`, help text, README | — | `--release 1.0`, `0.0` refused; `0.1` → `FM-1_951` | S |
| H7 | `tools/release.py` + `release.yml` draft | H5 H6 | dry-run tag produces the bundle | M |
| H8 | CLAUDE.md: append-only rule for param ids/enums; `.local/` and reference rules | — | text merged | S |

### P0 device baseline
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P0-1 | Bring-up + recovery drill (`-DFRODHES_CRASHTEST`) | H3 | report: install, `--info`, two-crash rescue, OCT− rescue | S |
| P0-2 | Profile build: `app_audio` timing, overlay, CC dump, `tools/fm1_profile.py`; optional `nest_ticks` hook | P0-1 | numbers readable on the host, USB audio on/off | M |
| P0-3 | Bench firmware `bench.c` K00–K19: MC3±EF (K07), DF2 (K06, control), Hermite-22 pickup XIP warm/cold vs pool (K10), `RAMFN` variant (K11), delay/HALL/phaser ports (with `dsp.c` stubs), 8-voice prototype (K13), in-ISR run (K16), stolen-time meter (K17), `-O2` (K18), 64-bit divide link probe (K15), ROM-data probe (K19), `settings_save` stall (K14) | P0-2 | `docs/model/BUDGET.md`: device ns/sample, host instr/sample, ratio, −Os/−O2 delta per kernel; `tests/device_ratio.txt` written; §3.1 targets and alarms re-cut | M |
| P0-4 | Memory and flash allocation: pool/RAM/flash baseline; canvas confirmed 240×64; preset sectors 0x97000/0x98000 confirmed free of the image; `FL_RANGE_OK` hook | P0-1 | written allocation; delta list started | S |
| P0-5 | Hardware facts: SEL/SCL label; **does the keybed sense velocity** (scan timing between two contacts, if any); LED dim; speaker quick check; `tools/fm1_capture.py` capture equals the host render of the placeholder | P0-1 | facts recorded in README | M |
| P0-6 | Hub ID check (95x free) | — | date/screenshot recorded | S |

### P1 reference, tools, verification infrastructure
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P1-1 | `docs/model/data/sources.json` + `tools/fetch_reference.py` (jRhodes pinned clone; manual-download probe for pianobook/mpc_ek2; user captures) + `CAPTURE.md` | H3 | manifest validates; jRhodes 65 files verified; probe fills `content` for any present source | M |
| P1-2 | `tools/dsp_analysis.py` + synthetic tests | H3 | T60 within 1 %, f within 0.1 c at 40 dB SNR | M |
| P1-3 | `tools/fit_reference.py` per source → `reference_<id>.json` with attribution and trust flags; plots in `build/`; key-54 step check; 7×/20× partials separated from pickup harmonics | P1-1 P1-2 | deterministic; per root and layer: f0, ratios, EDT/late T60, levels, floor; runs on whichever sources are present | M |
| P1-3a | `tools/tine_fe.py` | P1-2 | bare cantilever 6.267/17.548 ± 0.1 %; F1 table of `scratch-01/fe.py` reproduced | M |
| P1-3b | `tools/tinebar.py` | P1-3a | k = 0 limits; residues; retune; release matching by continuity | S |
| P1-3c | `tools/pickup.py` | P1-2 | Φ̃, slope tables, asserts of §6.8, C5 in float | S |
| P1-3d | `tools/hammer.py` | P1-3a | energy with R = 0; Hertz law vs a rigid wall; T_c range assert | M |
| P1-4 | `tools/rhodes_model.py` golden float model (exact C structure incl. §5.6) | P1-3b P1-3c P1-3d | renders a note; unit oracle | M |
| P1-4a | `tools/fit_model.py` → `params.json`; **Gate A** on the sources present (report lists which measures had a primary source) | P1-3 P1-4 | C1 C2 C3 C5 C6 C12 pass on the primary source of each measure | L |
| P1-5 | `tools/gen_rhodes.py` (§6 in full) + `make tables-check` | P1-4a | byte-identical across runs/OSes; all asserts incl. ROM nudge | M |
| P1-6 | `tools/analyze.py` + thresholds (C1–C13 wired, provisional) | P1-2 P1-3 | runs on the placeholder, fails as expected, report written | M |
| P1-7 | Harness verbs (§14.1) | H3 | REC hold/latch scripts pass with `expect`; non-zero exit on failure | M |
| P1-8 | Golden + health infrastructure; placeholder goldens | P1-7 | `make golden` green; a 1-LSB change fails | M |
| P1-9 | Unit framework + sanitizers + MIDI fuzz harness | P1-7 | `make unit fuzz` green under ASan/UBSan with divide-by-zero | S |
| P1-10 | Cost checks (host counter, `target_budget.py` port) | H5 P1-8 | baselines committed; +25 % / +10 % gates work | M |

### P2 voice core
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P2-1 | `fxp.h` + `dsp.c` (helpers, tanh/sine tables, `exp2_q30`) + no-libcall check in `frodhes_checks.py` | P1-9 | unit tests; check fails on a planted `__divdi3` | S |
| P2-2 | MC3+EF1 kernel with the generated tables; resonator unit test (keys 21–108, both levels) | P2-1 P1-5 | round-off ≤ −90 dB re peak; T60 ±2 %; off before 2·T60 + 1 s; ADR note recording the bake-off numbers | S |
| P2-3 | Run-time coefficient path (§5.6) + TUNE/DECAY/RELEASE/bend/half-pedal tables | P2-2 | thresholds of §5.6; UBSan clean | M |
| P2-4 | Pulse player + velocity tables (8 layers) + R selectable | P1-5 | unit tests of §14.2 | S |
| P2-5 | Radial Hermite pickup, pool copies, offset TONE, `rh_pickup_probe()` | P1-5 P2-1 | C5; ROM scan; SNR ≥ 70 dB at −30 dB for every key (host, vs float) | M |
| P2-6 | Post-mix RLC + per-key gain + exact-silence rule | P2-5 | level across keys within ±3 dB at v = 96; C9 silence | S |
| P2-6a | Event ring with sub-block timing, ISR-owned voices, summary words, `rh_quiet/rh_lit` | P2-1 | latency test on host: note starts at its sub-block ± 0; fuzz clean | M |
| P2-7 | Integrate in order: V1 only (C1 + C7 on x) → + V2/O1 (C2 two-stage vs golden) → + hammer (C7 on x) → + pickup (C5, C7) → + velocity, damper, pedal, re-strike, steal (C4 C6 C10) → + RLC/gain (C9). **Gate B at every step.** | P2-2..P2-6a | goldens reviewed; health green | L |
| P2-7a | Profiler + CPU page + shedder (moved here so every later phase is measured) | P2-6a P0-2 | forced-overload host scenario sheds with a fade; device stress: no watchdog | M |
| P2-8 | Calibrate; freeze thresholds; report | P2-7 P1-6 | C1–C7, C9, C10, C12, C13 pass; C8 measured | M |
| P2-9 | Developer CC map | P2-7 | host `expect` tests | S |
| P2-10 | Device validation, capture conformance, **release 0.1** | P2-8 H7 | Phase 2 DoD: voices worst ≤ allocated budget, capture residual ≤ −60 dB | M |

### P3 realism
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P3-1 | O2 + H1 through the radial pickup (C5 bit-identical with x_h = 0) | P2-10 | C3 (O2), A8; cost recorded | M |
| P3-2 | Register voicing: TYPE deltas, gap families, residual table (S12) | P2-10 | C2 C6 do not regress; attack spectra closer to the reference (report) | M |
| P3-3 | Noises: damper thump on the tine force, key-down thump, LFSR felt; pedal-up behaviour | P2-10 | click detector green; levels as params | S |
| P3-4 | Amplitude→pitch (S11) from the MC3 quadrature | P2-3 | attack-pitch check ±1.5 c | S |
| P3-5 | Only if C8 fails: treble x-scale, then 2× pickup oversampling | P2-8 | C8 ≤ −60 dB; budget holds | M |
| P3-6 | TYPE set (S17) | P3-1 P3-2 | 4 goldens; distinct centroid/H2 metrics | M |
| P3-6a | Seeded jitter (S14) | P3-6 | goldens; per-key variation within residuals | S |
| P3-7 | `tools/ab.py`, T-LISTEN session, report; **release 0.2** | P3-1..P3-6a | protocol pass recorded | M |

### P4 params and panel
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P4-1 | `params.c`: PARAMS (16-bit capable), formatting, versioned 256 B settings, fixtures | P2-9 H8 | fixtures load; every param formats ≤ 5 chars; lint | M |
| P4-2 | Platform hooks (`CV_MAX`, `APP_FL_RANGE`); upstream PRs | P0-4 | hooks used; README delta table; PR links | S |
| P4-3 | PANEL drawing, scheduler, golden screens | P4-1 P4-2 | hashes for every column/switch/focus state; `HOST_BLITMAX` ≤ 30 720 | L |
| P4-4 | Control mapping (§10.3), layers, QUIET_MS, messages, hot readout | P4-3 P1-7 | scripts 1–7, 19 | L |
| P4-5 | VOICING pages, KEY VEL/DYN on SELECT, HUMANIZE, velocity curves | P4-4 | goldens; script 14 | M |
| P4-6 | MENU, INFO, CPU page, MIDI channel filter | P4-4 P2-7a | goldens; channel tests | M |
| P4-7 | Device UI checklist; main-loop gap probe; **release 0.3** | P4-5 P4-6 | Phase 4 DoD; gap < 50 ms | S |

### P5 effects
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P5-1 | `fx_render` skeleton, sub-block framework, stereo split, `fx_quiet()`, mute | P4-1 | FX-bypassed golden equals the Phase 4 golden | S |
| P5-2 | DRIVE port + TYPE rows level-matched | P5-1 | unit (bypass keeps state, no click); rows within ±0.5 dB RMS | S |
| P5-3 | TREM PAN/AM, shape, sync | P5-1 P6-1 | rate/depth exact; equal power ±0.1 dB | S |
| P5-4 | WAH (follower + SVF table) | P5-1 | sweep 300 Hz–2.5 kHz; stable over the whole table | M |
| P5-5 | PHASER port, free LFO | P5-1 | notch sweep A11 | M |
| P5-6 | CHORUS 2 taps | P5-1 | delay range measured; no zipper | M |
| P5-7 | D.DLY with glide, fold, `!` | P5-1 P6-1 | A11 ±1 sample; no click on TIME change; script 8 | M |
| P5-8 | A.DLY wow/flutter/LP/saturation/slew | P5-7 | pitch glide on TIME change; bounded feedback | M |
| P5-9 | HALL port (CTL 32) + ported `reverb_test` | P5-1 | RT60 range; no ringing; cost | M |
| P5-10 | Master port; speaker check (LOWCUT option) | P5-1 | limiter never above −0.3 dBFS; DC test | S |
| P5-11 | Spillover and crossfade rules (U8), preset-load crossfade; effects-aware shedding | P5-2..P5-10 | scripts 20, 25 | S |
| P5-12 | Device budget with all FX; **release 0.4** | P5-11 | all FX + 8 voices ≤ 2.9 ms max, mean ≤ 2.3 ms, USB on | S |

### P6 tempo and scales
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P6-1 | `tempo.c` core (ticks), sync table incl. 4BAR, unit tests | P4-1 | §14.2 tempo tests | S |
| P6-2 | PLAY tap, beat LED, re-phase | P6-1 | script 6; `expect led` | S |
| P6-3 | Clock follow with tick stamps, AUTO fallback, FA/FB/FC | P6-1 | script 9; jitter simulation; device DAW test | M |
| P6-4 | Sync wiring to TREM, PHA, CHO, DLY | P6-1 P5-* | A11 | M |
| P6-5 | Scales: masks, SNAP/DEG, refcount, per-key LEDs, `scale_test` port, MIDI QNT | P4-4 | scripts 10–13 | M |
| P6-6 | Clock out behind a flag; **release 0.5** | P6-3 | 24 PPQN on the host ±0.1 % | S |

### P7 presets and MIDI
| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P7-1 | `APP_FL_RANGE` sectors; refusal test with the NOR simulation | P0-4 P4-2 | erases outside the range refused | S |
| P7-2 | `storage.c` port + torn-write tests | P7-1 | ported `storage_test` green | M |
| P7-3 | Bank, browse-settle load, SAVE UX, auto names, queued save, undo/redo | P7-2 P4-4 | scripts 21, 22; fixtures | L |
| P7-4 | Factory presets tuned by ear | P4-1 P5-12 | goldens per preset | S |
| P7-5 | PC, CC map RX/TX, pitch bend, half-pedal, extended MIDI fuzz | P4-1 P2-3 | scripts 23, 24, 26, 27; fuzz green | M |
| P7-6 | Device power-cut test during a save; **release 0.6** | P7-3 | old or new bank, never broken; Phase 7 DoD | S |

### X continuous
X1 nightly upstream merge check (H4); X2 nightly long fuzz (P1-9); X3 hub release 0.9 (P7-6, identity decision).

**Critical path (ESTIMATE 5–7 weeks to 0.1):** H3 → P0-1/2/3 → P1-2 → P1-3a–d → P1-4 → P1-4a (Gate A, needs at least
one fetched source) → P1-5 → P2-1/2/3 → P2-6a → P2-7 (Gate B) → P2-8 → P2-10. P1-7..P1-10 run in parallel with
P1-3x; P0-5/P0-6 and H1/H2/H4–H8 are off the path.

**Definition of done per phase:** `make test` green on `main`, `make fw` green, the device checklist signed, README
status updated, the tagged pre-release where §14.5 maps one; plus: Phase 0 — BUDGET.md measured, allocation written,
sectors and canvas chosen, velocity capability and hub ID recorded; Phase 1 — sources manifest, reference files for
every fetched source, deterministic tables, harness/golden/unit/fuzz merged; Phase 2 — §15 P2-8/P2-10 AC; Phase 3 —
C3(O2), C8, A8, T-LISTEN recorded, budget holds with H1/O2 or the drop order applied; Phase 4 — fixtures, every
screen hashed, gap < 50 ms, deltas as hooks; Phase 5 — each effect has U/A11 + goldens, all-FX budget; Phase 6 —
tempo/scale tests, DAW follow at 60/120/200 BPM ± 0.5; Phase 7 — torn-write, fixtures of every settings version,
power-cut test.

---

## 16. Risks

| ID | Risk | L/I | Early warning | Mitigation | Contingency |
|---|---|---|---|---|---|
| K1 | No unprocessed reference is obtained (pianobook login/terms, MPC purchase) | M/H | P1-1 shows only jRhodes fetched | trust rules make jRhodes sufficient for tuning, ratios, decay (late slope above floor + 20 dB), ΔB; balance set by ear | ask the user for captures by §7.4 |
| K2 | CPU budget (voices + FX) exceeded; device ratio worse than Felucca's | M/H | K13/K16 > targets; static +10 %; host +25 % | mode sleep; sub-block grid; `-O2` split; RAMFN leaf kernels | drop order O2 → H1 → residual → 6 voices → mono FX |
| K3 | XIP cache misses after redraws inflate the ISR peak | M/M | K10 cold ≫ warm | hot tables in the pool; per-key rows read once at note-on | RAMFN kernels; smaller redraw strips |
| K4 | Fixed-point resonator error (pitch, T60, limit cycle) | L/H | U resonator tests | MC3+EF1 (measured 108 dB at −60 dB E1); kill + lifetime cap | second-order EF on V1/V2 |
| K5 | Soft-bass pickup SNR limited by the Q25 tip (80 dB) found audible | L/M | C9 at v = 10 | — | Q26 tip with a 4-gap per-mode clamp |
| K6 | Aliasing above C6 at hard hits | M/M | C8 > −60 dB | generator assert on the treble tip amplitude | P3-5 (x-scale, then 2× pickup oversampling) |
| K7 | Realism target unreachable (2-DOF/pickup family cannot fit) | M/H | Gate A residuals; T-LISTEN < 3.5 | fit on EQ-invariant measures; shape parameter on the pole family; second reference | user captures of the target instrument |
| K8 | The keybed has no velocity | H/M | P0-5 | KEY VEL on SELECT (DYN), HUMANIZE, MIDI in | — |
| K9 | Pool/RAM overflow | L/M | build gate ≥ 8 KiB spare | map closed at 62 % | delay at 22.05 kHz or 1 s |
| K10 | Flash corruption / brick during saves | L/H | torn-write tests; power-cut test | A/B sectors, header last, read-back, narrow `FL_RANGE_OK`, §12.3 | recovery drill; FM-1-transporter |
| K11 | Upstream merge conflicts | M/M | nightly `upstream` red | override hooks, PRs, delta table | rebase by hand; pin upstream |
| K12 | Toolchain/SDK URL changes | M/M | sha mismatch | `toolchain.lock`, cache, mirror of the 3 Apache-2.0 files | build from `.local/` |
| K13 | reface semantics or CC numbers wrong (sources 403 / midi.guide) | M/L | user review of the panel | remap in PARAMS only | — |
| K14 | Host/device divergence (compiler, UB) | L/M | capture residual > −60 dB | UBSan; capture per phase | bisect with the profile build |
| K15 | Watchdog from long main-loop work | L/H | gap probe > 50 ms | one element per pass; strips; poll in long loops | split work |
| K16 | Hammer sim constants unphysical (contact 20 ms) | M/M | T_c assert in `hammer.py`; attack spectra off | fit K/R per band to the reference attack; 1–3 ms prior | hand-set T_c per band |
| K17 | Saved presets break across updates | M/M | fixture test | append-only rule, versions | migration; factory reset message |
| K18 | Scope creep (editor, arp, looper) | M/M | Phase 8 work before 0.6 | discard list; milestones | cut to 0.6 |
| K19 | Pickup pole not axisymmetric (chisel tip): H1 through a radial field is an approximation | L/L | A8 vs reference whirl | H1 is a small (0.2–0.25) component | per-axis scale on x_h (one multiply) |

---

## 17. Open questions for the user

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | **Reference access.** Will you (a) log in and download Pianobook "Matt's Fender Rhodes" into `.local/reference/pianobook/`, and (b) buy MPC-Samples "Electric Keys 2" ($29.99) for the 8-layer and Suitcase data? Both are analysis-only; only derived numbers are committed. | fit on jRhodes alone; balance by ear; SUITCASE deltas ESTIMATE |
| Q2 | **Your own captures.** Can you record a Nord (EP8 Nefertiti MKI / EP7 Amped Tines) or a real Rhodes by `CAPTURE.md` (§7.4)? If yes, which instrument is *the* target sound for T-LISTEN? | jRhodes is the T-LISTEN reference |
| Q3 | **Commercial intent.** Any intent to sell builds? It does not change the derived-number policy but it rules out ever shipping reference-derived audio and matters for the jRhodes author's licence offer. | hobby/GPL only |
| Q4 | **Repo name** `fhodes` vs project name Frodhes: intended? | keep `fhodes` |
| Q5 | **Listening panel:** who listens (you only?), on what (headphones + FM-1 speaker), and is the T-LISTEN pass rule (mean ≥ 3.5, identification ≤ 75 %) acceptable? | you, headphones + speaker, rule as written |
| Q6 | **Tuning-spring position** (0.65 L vs near the tip) and **hammer-tip band mapping** onto the 73 keys: do you know the real instrument well enough to override the fit's choice? | the fit decides (both are fit candidates) |
| Q7 | **Identity beyond 0.9:** stay in 0.x, or reserve a second block (e.g. 96x) after a hub check? | stay in 0.x until the hub check |
