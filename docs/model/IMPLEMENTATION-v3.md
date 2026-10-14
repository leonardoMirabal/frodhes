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
