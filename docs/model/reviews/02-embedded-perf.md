# Review 02: embedded feasibility (fixed point, CPU, memory, real time, platform rules)

Reviewer lens: can PLAN.md run on the FM-1 (AC791N, pi32v2, no FPU, JieLi clang, `-Os`, one compilation unit)
inside the rules of `docs/RULES.md`? Nothing was built for the target (there is no toolchain yet). Host
experiments are in `docs/model/reviews/scratch-02/` and can be rerun with `cc -O2 -o X X.c -lm && ./X`:

| Scratch file | What it shows |
|---|---|
| `reson.c` | round-off, DC, limit cycles and T60 error of five int32/Q30 two-pole resonator forms |
| `pickup.c` | SNR of three integer forms of the pickup `y = Φ(x[n]) − Φ(x[n−1])` |
| `voicecost.c` | host instructions per voice-sample of a prototype voice (macOS `proc_pid_rusage`, as Felucca's tests/drum_test.c:834) |
| `romconst.c` | the optimiser creating a ROM-window constant (0xFFC00000) from innocent LUT code (clang i386 as a proxy) |
| `mul64.c` | which 64-bit forms become libcalls on 32-bit targets (proxies for pi32v2) |

Paths are relative to the repo root; `Felucca/` means `docs/model/research/sources/06-felucca/Felucca/`.
Numbers marked **ESTIMATE** are mine. "Host instructions" are Apple-clang arm64 instructions. They are
converted to device time with Felucca's measured ratio, 1.7 % of real time per 100 host instructions per
sample (Felucca/tests/drum_test.c:28). That works out to **about 1 µs per 256-frame block per host
instruction per sample**.

---

## Verdict

**The plan is feasible on the FM-1, with room to spare, once a few numerical and real-time mistakes are
fixed. None of them is expensive to fix.**

- **CPU (ESTIMATE).** A prototype voice measures 61 host instructions per sample with 3 modes and 87 with 5
  (`voicecost.c`). For 8 voices that is about 0.48 ms and 0.69 ms per block. The effects add about
  0.31 ms (R06), the platform about 0.05–0.1 ms, and nested TIMER5/USB time is unknown. Total: about
  **0.9–1.2 ms of the 2.9 ms target**. The plan's per-voice figure ("~40–55 ops") is about 1.6× low, but the
  conclusion holds.
- **Memory.** The pool holds the delay, reverb, canvas and RAM copies of the tables with 38–60 % to spare
  (see the memory map below). RAM is not a concern.
- **Two blockers are numerical, not CPU:**
  1. Feeding the pickup a **Q15 x into a Q15 Φ table** gives 17 dB SNR on a soft E1 (`pickup.c`).
  2. The plan's limit-cycle cure, **"round toward zero"**, shortens a soft E1's T60 from 18 s to 13 s. The
     alternatives fail in other ways: round-to-nearest leaves a −85 dB limit cycle, so the voice never ends,
     `rh_quiet()` never becomes true, and settings are never saved. Plain floor leaves a −79 dB limit cycle
     at pp (`reson.c`).

  First-order error feedback fixes all of these for one extra add per mode-sample.
- **Real-time and robustness issues:**
  - a race in the main-loop → ISR handover during voice stealing;
  - the flash erase looping stale audio (a buzz) while effect tails still sound;
  - a tempo scheduler built on `fm1_micros()`, which wraps at 178.9 s and not at 2³², so the clock stops;
  - hot tables left in XIP flash, where UI drawing can evict them from the cache.

---

## Critique

Each item gives the evidence, the severity, and a concrete fix.

### C1. BLOCKER: the pickup's input and table are Q15 (PLAN §2 "x … Q15", §6 "8 voicings × 1024 × Q15")

The difference form `Φ(x[n]) − Φ(x[n−1])` throws away about log₂(fs / 2πf₀) bits: 7.4 bits at 41 Hz. With
x and Φ both quantised to 16 bits, the step between two consecutive samples is a few table LSBs.
`pickup.c` (P1 = the plan as written) measures this SNR against a double-precision reference:

| Case | P1: plan (Q15 x, Q15 Φ) | P2: Q30 x, int32 Φ, 16-bit lerp | P3: g(x)·dx chain rule |
|---|---|---|---|
| E1 41 Hz, ff | 46 dB | 71 dB | 39 dB |
| E1 41 Hz, pp (−30 dB) | **17 dB** | 57 dB | 75 dB |
| C4, pp | 33 dB | 61 dB | 59 dB |
| C7 2 kHz, ff | 79 dB | 101 dB | 6 dB |

**Fix.**
- Keep x as the int32 sum of the mode states, never narrowed to Q15.
- Use **int32 Φ tables** (1025 entries with a guard entry). Index with `u >> 21` and interpolate on a 16-bit
  fraction (P2, specified below). The output keeps its 32-bit precision until the per-key gain.
- P3 (the derivative table) is rejected: its first-order error explodes at high pitch.
- Cost: P2 needs one 32×32→64 multiply for the lerp, versus a 32-bit one for P1. In the prototype the whole
  pickup is about 22 host instructions per sample.
- Flash: 8 × 1025 × 4 B = 32.8 KiB instead of 16 KiB.

### C2. BLOCKER: resonator rounding (PLAN §8 "Round toward zero"; D5 doesn't specify the rounding)

`reson.c`: int32 state with a peak of 2²⁸ for a full hit, Q30 coefficients, the same quantised coefficients
in a double-precision reference.

| Form | E1 ff: error re peak | E1 pp: error | E1 pp: T60 (ideal 18.0 s) | E1 pp: level left after 60 s | C4 ff: error |
|---|---|---|---|---|---|
| DF floor (`>> 30`) | −81 dB, DC −12k LSB | −50 dB | 17.2 s | **29 000 LSB (limit cycle)** | −105 dB |
| DF round half up | −82 dB | −55 dB | 17.1 s | **14 500 LSB (dead band, forever)** | −109 dB |
| DF toward zero (the plan) | **−60 dB** | −64 dB | **13.0 s (−28 %)** | 0 | **−61 dB** |
| DF + 1st-order error feedback | −135 dB | −97 dB | 18.0 s | 3 LSB | −135 dB |
| DF + 2nd-order error feedback | −177 dB | −147 dB | 18.0 s | 1 LSB | −164 dB |
| Coupled form, floor | −129 dB | −93 dB | 18.0 s | 226 LSB | −135 dB |

What goes wrong with each:
- **Toward zero** acts as amplitude-dependent extra damping. It fails validation target 2 (T60 within ±20 %)
  on soft bass notes and adds −60 dB of distortion on loud ones.
- **Round half up** leaves a permanent limit cycle at about −85 dB re a full hit, above the plan's −96 dB
  "voice off" threshold. The voice never ends. `rh_quiet()` (rhodes.c:136-142) never becomes true, and
  frodhes.c:146 never saves the settings.

**Fix.**
- Use **DF2 with floor plus first-order error feedback**: `acc += e; y = acc >> 30; e = acc & 0x3FFFFFFF`.
  The cost is one 64-bit add and one AND per mode-sample.
- Use second-order feedback (`acc += 2e1 − e2`) for the tonebar (V2) only if listening asks for it.
- Coefficient quantisation is not an issue: −0.0125 cents and −0.003 % T60 at E1 (`reson.c`).

### C3. MAJOR: main-loop/ISR handover breaks with voice stealing (PLAN §2 "1 ms fade before a retrigger"; rhodes.c:16-17, 95-115)

Today the main loop writes `inc/level/decay` and then bumps `strike`. The model needs about 100 B per voice:
5 modes × (a1, a2, release a1/a2, weight), the hammer band and rate, and the gain.

To steal a sounding voice, the ISR must keep rendering the *old* coefficients for the 44-sample fade. If the
main loop overwrites them meanwhile, the fade runs on half-new coefficients. Writing the parts "in a safe
order" (RULES.md:38-40) cannot work for a 100 B struct that the ISR reads across a whole block.

**Fix.**
- The ISR owns all voice state.
- The main loop posts 32-bit events (`NOTE_ON key vel`, `NOTE_OFF key`, `PEDAL`, `ALL_OFF`) into an SPSC
  ring of 32 entries. The main loop writes `w` and the ISR writes `r`, like platform.c's MIDI ring
  (platform.c:174-185).
- At the start of each block the ISR drains the ring, allocates or steals, and copies the key's coefficients
  from the const tables (no arithmetic beyond indexing).
- `rh_lit()` and `rh_quiet()` read ISR-written summary words (`active` bits, `quiet`), one writer each.

### C4. MAJOR: a flash erase loops stale audio, and the "quiet" test ignores effect tails (PLAN §5 presets, §8 "save only when silent")

- `settings_save` → `fl_erase4k` turns interrupts off for about 50 ms (platform.c:135-136, 166;
  hal/fm1_flash.h:231-237) without clearing `abuf`. The I2S DMA keeps replaying the last two 5.8 ms halves,
  which buzzes.
- Felucca clears the buffer before every erase for exactly this reason: "IRQs off: the DMA would loop stale
  audio (buzz)" (Felucca/firmware/src/storage_hw.c:22-35).
- `rh_quiet()` checks voices only. With a 1.5 s delay at high feedback and a 5 s HALL, "voices silent" does
  not mean "silent".

**Fix.** Wrap every erase or program from the app in `app_flash_begin()` / `app_flash_end()`:
- Check `rh_quiet() && fx_quiet()`, where `fx_quiet` is the block peak of the effects output below −80 dBFS
  for 500 ms, kept by the ISR.
- Then zero `abuf` (it is visible: one compilation unit), erase, and flush the delay and reverb lines.
- The A/B preset sectors also need `FL_RANGE_OK` widened (platform.c:71).
- Measure the real stall in Phase 0 (K14 below).

### C5. MAJOR: the tempo engine in R08 schedules with `fm1_micros()`, which wraps at 178.9 s, not 2³² (PLAN §5 "32-bit maths only")

- `fm1_micros()` is `FM1_T4_CNT / 24` (hal/fm1_time.h:33). It runs 0 … 178 956 970 and jumps back to 0.
- The R08 sketch's `(int32_t)(now − next_pulse_us) >= 0` (08-felucca-tempo-scales.md:461-468) therefore
  goes negative forever after the first wrap. MIDI clock out and the beat LED stop after about 3 minutes.
  `tempo_midi_f8`'s `dt` breaks the same way (it is then rejected).

**Fix.** Keep every time in **`fm1_ticks()`** (24 MHz, wraps cleanly at 2³²; hal/fm1_time.h:30):
- `pulse_ticks = 600000000u / bpm10` with `pulse_rem = 600000000u % bpm10` gives an exact 24-PPQN Bresenham
  step (6·10⁸ fits in 32 bits; the 40 BPM pulse is 1.5 M ticks).
- Ticks to samples is a multiply with no division: `samples = ((uint64_t)ticks * 7892002u) >> 32`.
  The constant is 2³²·44100/24·10⁶ = 7 892 002.4; the error is 5·10⁻⁸.
- The other R08 bounds are fine:
  - `beat_samples ≤ 66150` (40 BPM) × 768 units = 50.8 M < 2³¹;
  - `0xFFFFFFFF / cyc` is a 32-bit division (a hardware instruction, see Felucca/tests/target_budget.py:107);
  - `beat_us / 100 * 441 / 100` is fine.
- **Rule:** if beat_samples ever carries fractional bits, use Q4 at most (66150·16·768 = 8.1·10⁸ < 2³¹).

### C6. MAJOR: hot tables are left in XIP flash (PLAN §6 "16 KiB flash")

- Code and `.rodata` execute and read through the SFC cache, decrypted (app.ld:21; hal/fm1_xip.h:3-6).
- The pickup tables are read on every sample of every voice. The main loop's text drawing reads fonts from
  the same flash, and the screen is redrawn while notes play.
- Cache size, line and miss latency are unknown. **ESTIMATE** worst case: 8 voices × ~64 lines per 2 KiB of
  table slice × ~1 µs per miss ≈ 0.5 ms per block in the blocks that follow a redraw. That makes the peak
  ISR time nearly double the mean.

**Fix.**
- Copy the *active* Φ tables into the pool: 4 register tables × 1025 × int32 = 16.4 KiB, double-buffered =
  32.8 KiB.
- Rebuild them in the main loop when TYPE or TONE changes, already blended, which also removes the per-sample
  "blends 2 tables" of §2. Then publish the bank index with one 32-bit store.
- The per-key coefficient tables are read only at note-on and can stay in flash.
- Phase 0 measures XIP vs pool, warm vs thrashed (K8–K9).

### C7. MAJOR: ROM-window constants that Q30/LUT code can generate (RULES.md:21-23; tools/build.py:204-211)

The window is signed −4 194 304 … −3 145 729. By format that is:
- Q20 −4 … −3;
- Q22 −1.0 … −0.75;
- Q23 −0.5 … −0.375;
- Q24 −0.25 … −0.19;
- **Q30 −0.0039 … −0.0029**;
- Q31 −0.00195 … −0.00146.

Plausible sources:
1. **LUT cell base:** `(x >> 22) << 22` for a 1024-entry table. The optimiser turns it into
   `and 0xFFC00000` (`romconst.c`: `movl $-4194304` on clang i386).
2. Clamping a Q22 signal at −1.0, or a Q20 state at −4.
3. A Q30 literal such as a hammer weight or DC coefficient of about −0.0035, if it is written as a code
   constant rather than table data.
4. **Generated tables:** app.ld:21 puts `.rodata` *inside* `.text`, and build.py disassembles the whole of
   `.text` (`objdump -d`, build.py:169). Whether table words can decode to `rN = −3 5xx xxx <…>` is unknown.

**Fix.**
- Index tables with `x >> 21` / `x >> 22` and take the fraction with `x & 0x3FFFFF` (never the base mask).
- Clamp in Q15 *before* the shift (as platform.c:242-247 does).
- Never hard-code Q30 coefficients: put them in host-generated tables.
- `gen_rhodes.py` asserts that no 32-bit table word lies in 0xFFC00000–0xFFCFFFFF and nudges such a word by
  1 LSB if it does.
- Phase 0 plants a known word to see whether the check reads data (open question Q4).

### C8. MAJOR: no on-device CPU visibility after Phase 0 (PLAN §6, §7)

- The plan measures once, in Phase 0, and keeps nothing in the firmware.
- Felucca keeps `audio_max_us`, an averaged load, a "late" counter (the DMA moved on during the render) and a
  per-half overload shedder (Felucca/firmware/src/audio.c:46-139).
- Frodhes's ISR time is also inflated by TIMER5 nesting: 58 key scans and about 12 USB audio services per
  block (platform.c:191-218; the timer has priority 4 against the audio's 3, platform.c:335-337). That
  nesting is not counted anywhere.

**Fix.** Keep the per-stage profiler and the CPU page specified below, plus a "late" counter
(`fm1_audio_free_half()` before and after, hal/fm1_audio.h:87) and the overload shedder.

### C9. MINOR: the per-voice cost estimate is about 1.6× low (PLAN §6 "~40–55 ops/sample")

- The `voicecost.c` prototype (DF + EF1 modes in mode-outer loops, P2 pickup, gain, mix):

  | Modes | Host instructions per sample | Device ESTIMATE per voice | 8 voices, per block |
  |---|---|---|---|
  | 3 | 61 | 1.0 % | 0.48 ms |
  | 5 | 87 | 1.5 % | 0.69 ms |
  | contact | +2–5 % | | |

- The per-voice RLC biquad in the plan adds about 10 more.
- The 1.7 % ratio comes from Felucca's PHYS code, which is itself built on 64-bit multiplies (`px_m`,
  phys_dsp.c:71), so it is the right calibration class. Phase 0 must recalibrate it with Frodhes's own kernels.
- The total still fits.

### C10. MINOR: the RLC load biquad is per voice (PLAN §2)

- The load is a linear network on the whole harness, after the pickups are summed.
- One biquad on the mono mix is physically right and saves about 8 × 10 host instructions per sample
  (about 80 µs per block, ESTIMATE).
- Per-key gain stays before the sum.

### C11. MINOR: the ISR structure is underspecified (PLAN §4, §7 code layout)

- `rh_render` today adds stereo into `out` (rhodes.c:79-80), but the chain is mono until the tremolo.
- Define `rh_render(int32_t *mono, n)` writing a static `int32_t rh_mono[256]`. Keep the 1 KiB block buffers
  **static, off the ISR stack**: the system stack is 7.9 KiB (app.ld:30, crt0.S:24) and TIMER5 nests on it.
- The fx chain then writes L/R into `out`.
- Never call `irq_save()` / `irq_restore()` from ISR code: `irq_restore` always executes `sti`
  (hal/fm1_flash.h:229), which would re-enable nesting in the middle of a render.

### C12. MINOR: the loop order is not specified, and H1's coupling constrains it (PLAN §2)

- Felucca's modal bank loops sample-outer and mode-inner, with state loads and stores in the loop
  (phys_dsp.c:443-451).
- Run **mode-outer, sample-inner** over the block, keeping the state in registers. Each mode adds into a
  static `x[256]`, then the pickup loop runs once.
- H1 ("weak coupling ← V1") then takes V1's block output buffer as its input, which is fine because V1 runs
  first.

### C13. MINOR: damper crossfade and pitch glide coefficient updates (PLAN §2 note-off; phase 3 velocity → pitch)

- Interpolate `a1` and `a2` linearly per 32-frame sub-block between the host-supplied sustain and release
  pairs (8 steps ≈ 5.8 ms).
- This is provably stable: the stability triangle of (a1, a2) is convex, so any convex combination of two
  stable pairs is stable. No per-sub-block trigonometry and no division.

### C14. MINOR: the build checks don't check pool headroom; no static cost check (tools/build.py:218-222)

- build.py prints the pool but only fails on RAM. A pool overflow fails late, in the linker, and nothing
  keeps a margin.
- Felucca requires ≥ 4 KiB RAM and ≥ 8 KiB pool spare (Felucca/tools/build.py:314-325), and compares a
  static loop-cost metric from the disassembly against a budget (Felucca/tests/target_budget.py).
- See "Build-check additions" below.

### C15. MINOR: `-Os` for everything (tools/build.py:40)

- The single compilation unit is built at `-Os`, and clang has no per-function optimisation attribute.
- Phase 0 times the kernels at `-Os` and `-O2`. If `-O2` is clearly faster and the image fits (the app slot
  is 568 KB), adopt Felucca's split: emit IR, mark UI functions `minsize` (Felucca/tools/build.py:207-221).

### C16. MINOR: note-on timing jitter of 5.8 ms (256-frame blocks, events drained per block)

- Felucca halved its I2S block to 128 frames (Felucca/firmware/src/core.h:15). Frodhes cannot: platform.c:61
  says the USB audio band is set for 256.
- Optional: stamp each event with `fm1_ticks()` and start it at its matching 32-frame sub-block. That gives a
  constant latency of about one block, at the price of slightly more complex voice start code.

### C17. MINOR: the canvas size is inconsistent (PLAN §6: 30 KiB; R07: 84 KiB for 240×176)

- `CV_MAX` is defined in platform.c:57 *and* in tests/host.c:53. A bigger panel must change both.
- The pool still fits (memory map below).

### C18. MINOR (note): the "no 64-bit division" rule disagrees with Felucca's evidence

- Felucca divides `uint64_t` by `uint32_t` (phys_dsp.c:165, 168, 191, 268, 303) and links with plain `ld`
  and no libraries (Felucca/tools/build.py:225).
- So the JieLi clang either expands these inline or has an instruction for them, unlike `__aeabi_ldivmod` /
  `__divdi3` on ARM and x86 (`mul64.c`).
- The plan doesn't need 64-bit division; keep the rule. Phase 0 probes it once (K15) so RULES.md can state
  the truth.

---

## Ideas ledger

| # | Idea | Source | Status | Reason |
|---|---|---|---|---|
| 1 | Mode-outer / sample-inner loops, with state in registers and one x buffer per voice | Felucca does the opposite (phys_dsp.c:443-451); prototype `voicecost.c` | **MISSING** | ~13 host instructions per mode-sample; no spills |
| 2 | Skip silent voices | EP-MK1 (R05), rhodes.c:66-67 | IN PLAN | |
| 3 | **Per-mode skip** once a mode's block peak < −90 dB re a full hit (O1/O2 die in < 0.5 s) | R02/R05 decay data | **MISSING** | Sustained chords then cost about 2–3 modes per voice, not 5: big steady-state saving |
| 4 | Sub-block (32-frame) coefficient steps, with a1/a2 linear interpolation | RULES.md:33-34; C13 | IN PLAN (fx) / **MISSING** (voice) | Stable by convexity |
| 5 | Oversample only the pickup | R01, R03 | IN PLAN | Keep, but the Φ-difference is already first-order antiderivative anti-aliasing (ADAA); try a smoother Φ first |
| 6 | Power-of-two int32 tables with a guard entry, `u >> 21` index, 16-bit fraction | `pickup.c` | **MISSING** (plan has Q15) | 57–101 dB SNR |
| 7 | Pre-blend the TONE pair into a pool table in the main loop | C6 | **MISSING** | Halves the pickup lookups; no XIP reads in the ISR |
| 8 | Copy hot tables to the pool | C6 | **MISSING** | Removes cache-miss tails |
| 9 | Hot leaf kernels in `.ram_text` via `RAMFN` + `FL_FAR` (hal/fm1_flash.h:16-22; build.py:199-203 already forbids calls there) | platform | **MISSING (Phase 0 experiment)** | Immune to XIP misses; 24 KiB region (app.ld:14) shared with the flash driver |
| 10 | DF2 + first-order error feedback | `reson.c` | **MISSING** | 1 add + 1 AND; −97 to −145 dB; clean ending |
| 11 | Coupled-form resonator | R03, R06 | REJECT (keep as fallback) | 4 multiplies; still leaves a 226-LSB residue; EF-DF is better and cheaper |
| 12 | Felucca SVF modal bank | phys_dsp.c | REJECT | 4 multiplies per mode plus per-block 64-bit divides; ~13 % per voice (Felucca/tests/phys_test.c:16-18) |
| 13 | Round toward zero against limit cycles | PLAN §8 | **REJECT (bad)** | T60 −28 % at pp bass; −60 dB error |
| 14 | Per-block state clamp at ±2³⁰ instead of per-sample saturation | phys_dsp.c:434-435 | **MISSING** | Guard against blow-ups for ~0 cost; saturate audio only at master |
| 15 | Silence detection by OR-ing magnitudes (`big \|= (v + 256) >> 9`) | phys_dsp.c:432 | **MISSING** | 1–2 instructions per sample for voice and mode end detection |
| 16 | Hard voice lifetime cap (e.g. 2 × T60(key), at most 40 s) | robustness | **MISSING** | Guarantees `rh_quiet()` eventually |
| 17 | Persistent ISR profiler: max µs, averaged load, late counter | Felucca/firmware/src/audio.c:101-139 | **MISSING** | See the spec |
| 18 | Overload shedder: 2 halves over the limit → fade the quietest released voice | audio.c:46-95 | **MISSING** | Graceful instead of crackle |
| 19 | Static loop-cost budget from the `.dis` | Felucca/tests/target_budget.py | **MISSING** | Catches regressions per build, without hardware |
| 20 | Host instruction-count CPU regression (`proc_pid_rusage`) | Felucca/tests/drum_test.c:834, cpu_baseline.txt | **MISSING** | The primary CPU gate until a device is on the bench |
| 21 | RLC biquad on the mix | C10 | **MISSING** | −80 µs per block |
| 22 | SPSC event queue, ISR-owned voices | C3 | **MISSING** | Removes the stealing race |
| 23 | Timestamped events at sub-block resolution | C16 | MISSING (optional) | Constant latency |
| 24 | Zero `abuf` before an erase; `fx_quiet()` before a save | Felucca/firmware/src/storage_hw.c:22-35 | **MISSING** | No buzz, no cut tails |
| 25 | Ticks-based tempo; `>> 32` reciprocal for ticks → samples | C5 | **MISSING** | Wrap-safe; no division |
| 26 | Delay stored at 22.05 kHz | R06 (HALL does it, fx.c:47-50) | MISSING (optional) | Halves 128 KiB if the canvas grows |
| 27 | High-word multiplies: `((int64_t)a1 * (y1 << 2)) >> 32` instead of `>> 30` (state ≤ 2²⁹) | pi32v2 32-bit | **MISSING (Phase 0 K4)** | Can drop the 64-bit shift, keeping only the high register |
| 28 | Unroll 2 modes per pass (halves the x-buffer load/store) | `voicecost.c` | MISSING (micro-opt) | About −3 instructions per mode-sample, ESTIMATE |
| 29 | Host-computed coefficients; no runtime trig or division | D5, D7 | IN PLAN | |
| 30 | Second core (`FELUCCA_CPU2`) | Felucca/firmware/hal/fm1_cpu1.h | REJECT (for now) | Some units misread on core 1 (fm1_cpu1.h:16-21); not needed at about 1.2 ms per block |
| 31 | Finite differences / port-Hamiltonian / 2× oversampling by default | R01–R03 | REJECT (IN PLAN) | |
| 32 | Per-sample TONE blend of 2 tables in the ISR | PLAN §2 | **REJECT (bad)** | Replace with #7 |
| 33 | Voice coefficients written by the main loop | rhodes.c pattern | **REJECT (bad)** | Replace with #22 |
| 34 | `fm1_micros()` for scheduling | R08 sketch | **REJECT (bad)** | Wraps at 178.9 s |
| 35 | True `int64 × int64` multiplies where `int32 × int32 → int64` suffices | phys_dsp.c:105-114 (`px_exp2`) | REJECT | Several multiplies instead of one widening multiply |
| 36 | `-O2` for the DSP (whole unit, or Felucca's IR + `minsize` split) | Felucca/tools/build.py:207-221 | MISSING (Phase 0 decides) | |

---

## Detailed implementation spec

### A. Phase 0 benchmark firmware

**Build.**
- Add `firmware/src/bench.c`. `frodhes.c` includes it instead of the app body when `FRODHES_BENCH` is
  defined.
- tools/build.py forwards `FRODHES_BENCH=1` from the environment as `-DFRODHES_BENCH=1`. This is one line,
  in Felucca's pattern (Felucca/tools/build.py:195-200).
- The same file builds on the host (`tests/run_host.sh` with `-DFRODHES_BENCH`), where each kernel is
  counted in **host instructions** with `proc_pid_rusage(RUSAGE_INFO_V4).ri_instructions`.

**Method (device).**
- Each kernel processes one 256-frame block, from pool buffers, warmed once.
- `t0 = fm1_ticks()` → kernel → `t1`, with **interrupts off** around the single run
  (`irq_save()` / `irq_restore()`, main loop only; ≤ 2 ms each).
- 64 repetitions; keep the min, median (insertion into a 64-entry array) and max of `t1 − t0`, minus the
  K00 baseline.
- Report ticks, ns per sample (`ticks × 1000 / 24 / 256`), and the **device ns per host instruction**
  (device ns per sample ÷ host instructions per sample). That last ratio replaces Felucca's 1.7 %/100 for
  this code base.
- **Cold** variants first stream a 64 KiB const dummy table in flash (sum it into a volatile sink) to evict
  the cache.

| Id | Kernel (256 samples unless noted) | Answers |
|---|---|---|
| K00 | empty: `fm1_ticks()` pair, plus a loop of 256 `nop`-like adds | overhead of the timer read and the loop |
| K01 | dependent chain of 1024 32-bit multiplies; then 1024 independent ones | MUL latency vs throughput |
| K02 | 1024 `mul_q30` (`(int64)a*b >> 30`), dependent and independent | 64-bit widening-multiply cost (D5) |
| K03 | 1024 `mac2_q30` (two products summed in 64 bits) | is the "one MAC" (phys_dsp.c:42) real? |
| K04 | 1024 `mulhi32` (`(int64)a*b >> 32`) | is the high word free? (idea #27) |
| K05 | 1024 32-bit `/` and 1024 32-bit `%` | hardware divide cost (tempo, LFO incs) |
| K06 | resonator DF floor, mode-outer | baseline mode-sample |
| K07 | DF + EF1 (the chosen form), and DF + EF2 | cost of the C2 fix |
| K08 | coupled form; Felucca SVF mode (phys_dsp.c:443-451, MIT notice kept in the bench) | alternatives |
| K09 | 5 modes, sample-outer vs mode-outer | loop order (C12) |
| K10 | pickup P2: table in XIP, warm / cold; table in the pool | XIP miss cost (C6) |
| K11 | K07 + K10 compiled with `RAMFN` (in `.ram_text`, called via `FL_FAR`) | RAM execution (idea #9) |
| K12 | int16 delay line read/write with 64 Ki mask; HALL-sized random reads (pool vs RAM) | pool bandwidth |
| K13 | full prototype `rh_render`: N = 1, 4, 8, 12 voices × {3, 5} modes × {contact, free} | **the budget table** |
| K14 | `settings_save()` of 64 B: ticks with interrupts off (one `fm1_ticks` pair around it) | erase stall → blocks lost |
| K15 | (separate build, `FRODHES_BENCH_DIV64=1`) one `uint64_t / uint32_t` | does it link? its cost (C18) |
| K16 | **in the ISR**: `app_audio` runs K13 at 8 voices × 5 modes for 1000 blocks, interrupts on: min, mean, max, a 16-bin histogram (100 µs bins), the late count | real ISR time including TIMER5/USB nesting |
| K17 | **stolen-time meter** (main loop): iterations of a fixed busy loop per 100 ms with interrupts on, against a reference of 5 × 20 ms windows with interrupts off. Load = 1 − on/off; run with app_audio empty and with K16 | platform + nesting + USB overhead |
| K18 | the same kernels at `-O2` (a second bench build) | C15 |
| K19 | a planted `static const int32_t probe[1] = {(int32_t)0xFFC00080}` | does the ROM check read table data? (Q4) |

**Output.**
- *Screen* (FONT_S, 13 lines × 30 characters, one page per encoder detent of SELECT; drawn only between
  runs, never during one): `K07 DF+EF1   12.3ns/s  m 723 M 731 X 790`.
- *USB-MIDI*: press PLAY to stream all results on channel 16, paced at ≤ 32 messages per poll (`MQ` is 64,
  usb.c:159). Per result: CC 102 = result id, CC 108 = field (0 min, 1 median, 2 max, 3 host instructions ×
  10 from the compiled-in host table), then CC 103–107 = the 32-bit value in 7-bit chunks, LSB first.
  `tools/bench_read.py` (python-rtmidi/mido) decodes this into CSV and the `docs/model/BUDGET.md` table.
- A copy of the result array in `.noinit` survives a reset (as felucca_dbg, Felucca/firmware/src/audio.c:16-22).

**Done when** BUDGET.md lists, for each kernel: device ns per sample, host instructions per sample, the
ratio, and the -Os/-O2 delta; and §6 of the plan is rewritten from K13/K16/K17.

### B. Fixed-point helper header: `firmware/src/fxp.h` (included by rhodes.c and fx.c, and by the host)

**Conventions.**
- All helpers are `static inline __attribute__((always_inline))`, with int32 arguments only.
- Every 64-bit multiply is a **widening `int32 × int32`**: one multiply on a target with a high-multiply,
  which pi32v2 has, per Felucca's inline `(int64_t)a*b` linking with no libraries (phys_dsp.c:71;
  Felucca/tools/build.py:225).
- No `/` or `%` on 64-bit operands. No literal in the ROM window (C7).

| Name | Signature | Q formats | Rounding | Notes |
|---|---|---|---|---|
| `mul_q30` | `int32_t mul_q30(int32_t a, int32_t b)` | a Q30 coefficient × b any → b's format | floor (`>> 30`) | `(int32_t)(((int64_t)a * b) >> 30)` |
| `mul_q30r` | same | same | round half up (`+ (1 << 29)`) | gains, not resonators |
| `mulhi` | `int32_t mulhi(int32_t a, int32_t b)` | Q31 × Q31 → Q30, or "×4 prescaled" forms | floor | `>> 32`: the high word only (K04) |
| `mul_q15` | `int32_t mul_q15(int32_t a, int32_t b)` | Q15 × Q15 (both \|·\| ≤ 32768) → Q15 | floor | 32-bit only: the product fits |
| `mul_q15r` | same | same | round | `(a*b + 16384) >> 15` |
| `mac_q30` | `int64_t mac_q30(int64_t acc, int32_t a, int32_t b)` | accumulate in Q30 × format | n/a | `acc + (int64_t)a * b` |
| `q30_out` | `int32_t q30_out(int64_t acc, int32_t *e)` | Q30 acc → int32 with first-order error feedback | floor + EF | `acc += *e; y = acc >> 30; *e = (int32_t)(acc & 0x3FFFFFFF);` |
| `sat_q15` | `int32_t sat_q15(int32_t v)` | any → ±32767 | n/a | constants ±32767 only (0xFFFF8001 is safe) |
| `sat_s32` | `int32_t sat_s32(int64_t v)` | 64 → 32 | n/a | `v > INT32_MAX ? … : v < -2147483647 ? … : (int32_t)v` |
| `clamp_state` | `int32_t clamp_state(int32_t v)` | resonator state | n/a | ±(1 << 30) (0xC0000000: outside the window); once per block |
| `lut_i32` | `int32_t lut_i32(const int32_t *t, uint32_t u)` | u = 32-bit position over the table range (index `u >> 22`, 1024 + 1 entries) → table format | floor | `k = u >> 22; f = (u >> 6) & 0xFFFF; a = t[k]; return a + (int32_t)(((int64_t)(t[k+1] − a) * f) >> 16);` (never `(u >> 22) << 22`) |
| `lut_i16` | `int32_t lut_i16(const int16_t *t, uint32_t u)` | Q15 tables (FX: tanh, sine, wah) | floor | the fraction as 15 bits; a 32-bit product |
| `bias_u` | `uint32_t bias_u(int32_t x)` | signed Q31 → table position | n/a | `(uint32_t)x + 0x80000000u` |
| `ramp_step` | `int32_t ramp_step(int32_t from, int32_t to)` | per-sample gain ramp over 32 frames | floor | `(to − from) >> 5` |
| `exp2_q16` / `sin_q30` | port of `px_exp2` / `px_sin` (phys_dsp.c:83-118, MIT notice) | main loop / per sub-block only | | LFOs; never per sample per voice |
| `ticks_to_samples` | `uint32_t ticks_to_samples(uint32_t t)` | 24 MHz ticks → 44.1 kHz samples | floor | `(uint32_t)(((uint64_t)t * 7892002u) >> 32)` |

**Proving "no libcalls".**
1. Phase 0 inspects the `.dis` of `mul_q30`, `mac_q30`, `mulhi` and `lut_i32`. Expected: no `call` inside.
2. A new build check (D below) fails on any `call` from the hot functions to a symbol starting with `__`.
3. A libcall would fail the link anyway, because nothing is linked (build.py:161). The check exists to name
   the culprit and to catch libcalls that are *defined* (for example in libc.c) and so would link silently.

### C. Memory map (byte counts; ESTIMATEs marked)

**Flash (`.text` + `.rodata`, XIP)**

| Item | Bytes |
|---|---|
| Φ voicings: 8 × 1025 × int32 | 32 800 |
| Hammer pulses: 5 bands × 257 × int16 (read during contact only) | 2 570 |
| Per key × mode: 88 × 5 × {cosθ·r sustain a1, a2, release a1, a2: Q30 int32; weight, level: int16} = 20 B | 8 800 |
| Per key: gain, hammer band and rate, Φ register, flags (8 B) | 704 |
| FX: tanh 257 × 2, sine 257 × 2, wah 64 × 3 × 2, PH_A 33 × 2, LFO_INC 128 × 4 | ~2 000 |
| Factory presets: 16 × 48 | 768 |
| **Total new constants** | **~47 700** (of a 568 KB slot shared with code) |

**RAM `.bss` (hot ISR state; 98 304 available)**

| Item | Bytes |
|---|---|
| Voices: 8 × `rh_voice_t` (5 × `rh_mode_t` of 36 B + 40 B of voice fields) | 1 760 |
| `rh_mono[256]`, `rh_x[256]`, `rh_h[256]` (int32 block buffers; never on the ISR stack) | 3 072 |
| Event ring: 32 × uint32 + r/w | 136 |
| FX state (drive, trem/wah, cho/pha, delay pointers, HALL scalars, master) | ~600 |
| Profiler: 12 stages × (cur, max, avg) × 4 + histogram 16 × 4 + counters | ~250 |
| Params, UI state, tempo, scales (ESTIMATE) | ~3 000 |
| **App total** | **~8 800** (+ the platform's: abuf 4 096, USB; the build prints it) |

**Pool (`.pool`, zeroed at boot; 344 064 B)**

| Item | Bytes |
|---|---|
| `cv_px` canvas 240 × 64 × 2 (today; platform.c:57) | 30 720 |
| Delay: 65 536 × int16 (1.486 s) | 131 072 |
| HALL lines (R06, ESTIMATE) | 12 800 |
| Chorus: 1 024 × int16 | 2 048 |
| Φ working tables: 2 banks × 4 registers × 1 025 × int32 | 32 800 |
| Storage sector buffer (A/B presets) + user bank mirror 16 × 48 | 4 864 |
| Bench/profiler history (optional) | 512 |
| **Total** | **214 816 (62 %)**; with a 240 × 176 canvas (84 480): 268 576 (78 %); full screen (115 200): 299 296 (87 %) |

Policy:
- The audio state that runs every sample (voices, block buffers) goes in `.bss`. Big lines go in the pool.
  Felucca puts engine state in the pool too (Felucca/firmware/src/engines.c:19), so the pool is the same
  SRAM. Phase 0 K12 confirms there is no speed difference.
- If the canvas grows to full screen, cut the delay to 48 000 samples (96 000 B) to keep ≥ 8 KiB headroom
  comfortably.

### D. Audio ISR structure

```c
static void app_audio(int32_t *out, uint32_t n)        /* n = 256; out is zero */
{
    PROF_BEGIN();                     /* t = fm1_ticks(); half = fm1_audio_free_half() */
    rh_events();                      /* S1 drain the SPSC ring: alloc / steal (44-sample fade) / damper / pedal */
    PROF(S_EVT);
    fx_latch();                       /* S2 copy the volatile UI params published by the main loop; sync incs */
    PROF(S_LATCH);
    rh_render(rh_mono, n);            /* S3 voices: per voice, per mode (mode-outer, 8 sub-blocks of 32 for
                                         a1/a2 steps, hammer while in contact) -> rh_x; pickup -> rh_mono */
    PROF(S_VOICE);
    rh_load(rh_mono, n);              /* S4 the RLC biquad on the mix (and the TYPE preamp EQ) */
    PROF(S_LOAD);
    fx_drive(rh_mono, n);             /* S5 */
    fx_trem(rh_mono, out, n);         /* S6 mono -> L/R (autopan / tremolo / wah) */
    fx_mod(out, n);                   /* S7 chorus / phaser, stereo */
    fx_delay(out, n);                 /* S8 */
    fx_reverb(out, n);                /* S9 */
    fx_master(out, n);                /* S10 DC block, limiter, soft clip; tail peak for fx_quiet() */
    PROF_END();                       /* S11 stores, late check, overload shedder (out of line) */
}
```

**Budget per 256-frame block** (ESTIMATE until Phase 0. "Target" is what each stage is designed to; "alarm"
is what the profiler flags.)

| Stage | Target µs | Alarm µs | Basis |
|---|---|---|---|
| S1 events | 5 | 30 | 3–4 events, table copies |
| S2 latch | 2 | 10 | |
| S3 voices, 8 × 5 modes, worst (all in contact) | 730 | 1 300 | `voicecost.c` × Felucca ratio |
| S3 voices, typical (3 modes after the skip) | 480 | | |
| S4 load biquad | 12 | 25 | |
| S5–S10 FX, all on, worst variants | 320 | 500 | R06 |
| S11 profiler + shedder | 3 | 10 | |
| **App total** | **≈ 1 070** | **2 000** | |
| Platform (zero, master, clip, uac_tap, `<< 7`) | 50–100 | | platform.c:236-247, ESTIMATE |
| TIMER5 + USB nested in the render | unknown | | Phase 0 K16/K17 |
| **Hard limit (RULES.md:35)** | **2 900** | | |

**Overload shedder.** The policy follows Felucca/firmware/src/audio.c:46-95.
- If the app total exceeds **2 400 µs** in two blocks in a row, set `shed_req`.
- The next block then:
  1. fades the quietest released voice over 1 block;
  2. if none, drops the upper modes (O2, then H1) of the quietest held voice.
- Count each shed in `prof.shed`.

### E. CPU profiling feature (kept in the firmware)

- **Data**, written by the ISR only:
  ```c
  static volatile struct {
      uint16_t cur[12], max[12];
      uint32_t avg_q8;
      uint32_t blocks, late, shed;
      uint16_t hist[16];        /* 150 µs bins */
      uint8_t voices, modes;
  } prof;
  ```
- The main loop resets it by bumping `prof_reset_req` (its own single writer). The ISR clears `max` and
  `hist` when it sees the change.
- **Cost.** One `fm1_ticks()` read (a register load) plus a subtract per stage. Delta = `(t − t_prev)`
  ticks; store µs as `ticks / 24`, a 32-bit divide by a constant, which the compiler turns into a multiply.
  About 12 stages ≈ 0.3 µs per block.
- **Page.** MENU (hold HOME) > SYSTEM > CPU, redrawn at 4 Hz and only on changed rows:
  - one row per stage: name, current µs, max µs, and a bar scaled to 2 900 µs;
  - the total;
  - the averaged load in % of 5 805 µs;
  - voices and active modes;
  - the late and shed counts;
  - the 16-bin histogram as a sparkline;
  - OCT− resets the maxima.
- **USB.** The same numbers, on demand, through the CC stream of A (CC 102/108/103–107), so
  `tools/bench_read.py` also works on release firmware.

### F. Build-check additions (`tools/frodhes_checks.py`, run by build.sh after build.py; host checks in `tests/run_host.sh`)

**Target (from `build/frodhes.dis` and `objdump -t`):**
1. **Static loop cost.** Port Felucca/tests/target_budget.py with FUNCS = `rh_render`, `rh_mode_run`,
   `rh_pickup_run`, `rh_events`, `rh_load`, `fx_*`, `fm1_alnk0_irq`. Baseline in
   `tests/target_budget.txt`; fail at +10 %.
2. **No divides or calls in per-sample loops** of those functions: `= rN / rM` and `call` inside any
   backward-branch span (same span logic). Allowed callees are listed explicitly.
3. **No `__`-prefixed callees** (64-bit helpers) anywhere in the hot functions.
4. **Headroom.** RAM spare ≥ 4 096 B and pool spare ≥ 8 192 B (Felucca's thresholds, Felucca/tools/build.py:322-325).
5. **Table-size report.** Sum symbol sizes from `objdump -t` by prefix (`RH_`, `PHI_`, `FX_`, `cv_px`,
   `dly_`, …) into `build/memory.txt`, printed as one line per region. Fail if `.rodata` tables exceed
   64 KiB (a guard against an accidental 88-key × 16-mode table).
6. **ROM-window table guard.** `gen_rhodes.py` asserts that no generated int32 word lies in
   0xFFC00000–0xFFCFFFFF (and nudges it by 1 LSB if one does). frodhes_checks re-scans `build/text.bin` at
   the generated symbols' ranges.

**Host (`tests/cost_test.c`, macOS `proc_pid_rusage`; skipped elsewhere):**
1. **Worst-case render.** 8 voices, all in contact, 5 modes, all FX on in their heaviest variants: host
   instructions per sample against `tests/cpu_baseline.txt` (+25 %, as Felucca's regress.c).
2. **Max ISR-time assertion.** `device_µs = host_instructions_per_sample × RATIO`, where RATIO (µs per
   block per host instruction per sample) is stored in `tests/device_ratio.txt` from Phase 0. Initially it is
   1.0, Felucca's ratio. The assertion is **device_µs ≤ 2 000** (the alarm). The same test fails if any
   stage exceeds its alarm row in D.
3. **Numerical checks.**
   - `reson`-style: every key's modes rung from a full hit and from pp for 2 × T60. Pass if the round-off is
     ≤ −90 dB re peak, the measured T60 is within ±2 % of the table, the voice reaches "off" before
     2 × T60 + 1 s, and no state exceeds ±2³⁰.
   - `pickup`-style: SNR ≥ 50 dB at pp for every key.

---

## Open questions

1. **Clock and memory system.** What are the AC791N's core clock, cache size and line, and XIP miss latency?
   None of this is established (PLAN §6). K01–K04 and K10 answer it empirically.
2. **Is the pi32v2 widening multiply-accumulate one instruction** ("one MAC", phys_dsp.c:42)? Is the
   `>> 30` of a 64-bit value 1 or 3 instructions? This decides between `>> 30` and the high-word form
   (idea #27).
3. **64-bit division.** How does Felucca link its `uint64_t / uint32_t` (C18)? Inline expansion, an
   instruction, or something else? RULES.md should state what K15 finds.
4. **Does the ROM-address check see table data?** `.rodata` sits inside `.text` (app.ld:21), which build.py
   disassembles. K19 answers it. If yes, a data-aware check (skip known table symbols) is a platform change
   for upstream.
5. **TIMER5/USB nesting.** What do the 10 kHz scan and the USB audio service cost inside each render? A
   two-line platform patch, upstream-friendly, would expose it: accumulate ticks spent nested while
   `in_audio` (as Felucca/firmware/src/audio.c:24, :61).
6. **The ISR stack.** Do both interrupt levels nest on `ssp` (7.9 KiB, crt0.S:24)? What is the deepest
   frame? Does the JieLi clang support `-fstack-usage`, so a check could bound it?
7. **Polyphony.** With per-mode skipping, the steady-state cost of a sustained chord is about 2–3 modes per
   voice. Should the user choose 8 voices with headroom, or 10–12 (PLAN §9 Q2)? Only K16 can tell.
8. **Tooling.** May `tools/build.py` forward `FRODHES_BENCH` and call `tools/frodhes_checks.py`? build.py is
   arguably a platform file (CLAUDE.md: keep upstream merges clean).
