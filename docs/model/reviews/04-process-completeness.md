# Review 04: delivery process, testing, risk, licensing and completeness

Reviewer lens: delivery process, verification, risk, licensing, completeness. The subject is
[`docs/model/PLAN.md`](../PLAN.md) (commit `cdac46a`). The evidence is the repo as it is at that commit, research files
R01–R08, and Felucca's test suite and docs (`docs/model/research/sources/06-felucca/Felucca/{BUILDING.md,CONTRIBUTING.md,tests/}`).
Abbreviations: **R0n** = `research/0n-*.md`, **FEL** = `sources/06-felucca/Felucca/`. File:line references point into this
repo unless marked otherwise. Numbers I estimated myself are marked **ESTIMATE**.

---

## Verdict

**The design is sound but the delivery plan is not ready. Do not start Phase 2 until the two BLOCKERs below are fixed.**

The engineering choices (modal tine + tonebar pair, physical Φ pickup, host-generated tables, the reface chain ported
from Felucca) follow the research well. As a delivery plan, PLAN.md is thin:

1. **Licensing (BLOCKER).** The repository already contains, and has pushed to `origin/main`, material that may not be
   redistributed. This includes disassemblies of Roland firmware, the UVI reference recordings (unknown licence; R03
   itself says "Do not redistribute them"), the Sønderbo thesis and its unlicensed code, and copyrighted paper PDFs. The
   plan's licence row only covers the ROM images.
2. **No verification safety net (BLOCKER).** The host harness does not assert anything. It always exits 0
   (`tests/host.c:186-189`). It cannot turn an encoder (`host.c:28`) or release or hold a button (`host.c:26`). There is
   no CI, no golden render, no unit test and no sanitizer run. Phase 2 replaces the whole engine, and nothing would
   detect a regression. Felucca, the most mature FM-1 firmware, has 78 test files: hash-golden renders, CPU baselines,
   a static target budget, fuzzers under ASan/UBSan and torn-write flash tests (FEL `tests/run_tests.sh`,
   `tests/regress.c:1-24`).
3. The acceptance criteria are only partly measurable. Phases 4–7 have no "done when" at all. Several of the realism
   checks (§3) would measure the wrong thing (Critique 4).
4. The plan does not mention three of the user's requirements: everything kept in the project folder, push to GitHub,
   and the on-board key velocity that the realism depends on.

None of this changes the architecture. Sections 5–6 below give a test, CI, release and DoD specification, plus a
PR-sized task list. Together they turn the plan into an executable one.

---

## Critique

Each item has a severity, the evidence and a concrete fix.

### 1. BLOCKER: non-redistributable third-party material is committed and pushed

**Evidence.** `git ls-files docs/model/research/sources | wc -l` gives 2997 files, about 233 MB on disk. `origin/main`
is at `cdac46a`, which contains commit `a490f9f` ("Add research sources"). The tracked files include:

| Tracked material | Problem | Source of the claim |
|---|---|---|
| `04-code-rdpiano-stk/rdpiano/re_stuff/disasm/{mk80_rom_b,mks20_rom_b,rd200_rom_a,rd200_rom_b}.asm` (≈1.3 MB) | Full f9dasm disassemblies of Roland's copyrighted firmware ROMs. The `.gitignore` excludes the ROM images (`.gitignore:4-5`) but not these derivatives. | R04 §Source: "ROMs: NO… must not be copied, converted or shipped" |
| `03-falaize-phs/code/extracted/code_for_webpage/REF/REF{,1..8}.wav`, `allsigs.wav` | UVI recordings with unknown licence | R03 §Gaps: "The licence of the UVI reference WAVs is unknown. Do not redistribute them." |
| `02-sonderbo-thesis/thesis.pdf`, `thesis.txt`, `repo/EPiano/**` | The thesis says "publication… only due to agreement with the author". The repo has no LICENSE, so all rights are reserved. | R02 §Source, §Gaps |
| `01-dafx17/p79.pdf`, `pg-*.png`; `03-falaize-phs/paper.pdf`; `hal*.html`, `ircam.html`, `blog.html`; `05-…/web/*.png` | Author or publisher copyright with no redistribution grant ("HAL Authorization" is a deposit licence) | R01 §Source, R03 §Source |
| `__MACOSX/**` (19 files), `.DS_Store` (4) | Junk | — |
| `rdpiano/re_stuff/ident_cells/**` (1531 JPEGs) | Die photos. They are probably covered by rdpiano's GPL, but they are 176 MB of history for no use. | — |

The research README states that "the papers and recordings belong to their authors and are here for research
reference". That is not a licence. Frodhes is GPL-3.0-only, and anything distributed in its source tree needs a
licence that allows redistribution. A release's "complete corresponding source" would carry these files too.

The plan also says the jRhodes FLACs will live in `docs/model/research/sources/05-…/audio/`, "which is git-ignored".
**It is not**: `.gitignore` has no such entry. Those samples are CC BY-NC 4.0
(`sources/05-…/jlearman.jRhodes3d/LICENSE`). Combined with a habit of running `git add -A`, NC audio would end up in a
GPL repo.

**Fix (task H1).**

- Check whether `origin` (`leonardoMirabal/fhodes`) is public.
- Remove the sources from the tree **and from history** with `git filter-repo --path docs/model/research/sources
  --invert-paths`, then force-push. This is cheap now (12 commits, no forks). It will not be cheap later.
- Keep sources as a **local, git-ignored cache**: `.local/sources/` (or keep the current path and ignore it).
- Commit `docs/model/research/SOURCES.lock`. It is a TSV of id, URL, commit or sha256, licence, "redistributable y/n"
  and local path. Commit `tools/fetch_sources.py` to rebuild the cache.
- Keep only redistributable items in git, if any are needed at all. The Felucca code is referenced by commit
  `a53ecf4…`. STK (MIT), EP-MK1 (GPL-3.0, with its License.txt) and PyPHS (CeCILL-B) are redistributable but not needed
  in-tree.
- Add `.local/` and `*.flac` to `.gitignore`. Add a CI guard that fails on any tracked `*.wav|*.flac|*.asm|*.pdf` under
  `docs/`.
- Update the research README and LICENSING.md to match.

### 2. BLOCKER: no automated verification before the engine is replaced

**Evidence.**

- `tests/run_host.sh` builds and renders but checks nothing.
- `host.c` exits 0 at `end_ms`.
- `fm1_enc_take()` always returns 0 (`host.c:28`), so knobs cannot be tested.
- `fm1_input_edges` never reports releases (`host.c:26`), so "hold REC" and every tap/hold gesture in §5 cannot be
  tested.
- Keys have no velocity (`host.c:117`; the app plays them at a fixed 100, `frodhes.c:114`).
- Screenshots are taken only at 1000 and 3000 ms (`host.c:94`).
- PLAN.md §7 says "host UI tests (screen PNGs)" for Phase 4, but nothing else in the plan builds the harness that
  requires.

Felucca's suite shows what is needed on this exact platform:

- **Golden render hashes.** FNV-1a 64 per scenario, rewritten only with `GOLDEN_UPDATE=1` (FEL `tests/golden.txt`,
  `regress.c:8-12`).
- **Health checks.** Clipping, DC, peak, tail silence and voices freed (`regress.c:13-14,37-48`).
- **Host CPU baselines** from kernel-counted instructions, ±25 % (`regress.c:15-18`, `cpu_baseline.txt`).
- **A static target budget** from the pi32v2 disassembly, +10 % (`tests/target_budget.py:4-13`).
- **ASan/UBSan fuzzers** with fixed seeds (`fuzz_proj.c:1-5`, `run_tests.sh` "DRUM STRESS").
- `-fsanitize=integer-divide-by-zero` (`run_tests.sh:294`), which is exactly the RULES.md hazard "a division by zero
  does not crash".
- **Torn-write NOR simulation** (`tests/storage_test.c:1-12`).
- **A fixed-point vs floating-point comparison** (`phys_fixed.c` vs `phys_ref.cpp`).

**Fix.** Add a "Phase 1b: verification infrastructure" ahead of Phase 2 (tasks P1-7 … P1-10, §5.1). Phase 2's DoD then
includes the green test suite.

### 3. MAJOR: the plan contradicts itself on host-only coefficients

D5 and D7 say per-key coefficients are host-computed into `rhodes_tables.h`. But the plan also asks for:

- **TUNE ±50 ct** (§5 ENV page);
- **DECAY and RELEASE** scaling knobs (§5; R05 suggests ×0.25..×2);
- **velocity→pitch** offset that decays (§2);
- **TYPE** variants that change pickup and load tables;
- MIDI notes outside the 27-key range (open question 1).

R02 and R04 point out that pitch bend is trivial in a modal model. All of these need the pole angle θ and radius r
computed **at runtime**, without float or 64-bit division. Exact pole placement means `2r·cosθ` and `r²`, which in turn
needs a cos table and an exp2 table. R04 §FM-1 4 gives the 4096-step exponential pitch LUT. Nothing in the plan
provides this path. Without it, either the knobs cannot exist or Phase 2's resonator gets rewritten in Phase 4.

**Fix (task P2-3).**

- Specify a note-on and knob-change coefficient path in the main loop. The host tables hold per-key `f0` as a Q32 phase
  increment and `T60` as a log2-domain rate.
- At runtime: `θ` = increment × 2^(cents/1200), using an exp2 mantissa LUT plus a shift. `cosθ` comes from a 1024-entry
  Q30 quarter-wave table with lerp. `r = exp2(−k/T60)` uses a reciprocal-T60 table indexed by the DECAY scale step.
- Publish the coefficients to the ISR through the existing write-then-bump-`strike` protocol.
- Unit-test the result against double precision: ≤ 0.2 cent and ≤ 1 % T60 error over keys 21–108 at every TUNE and
  DECAY corner.

### 4. MAJOR: `tools/analyze.py`'s realism checks are partly invalid as written

The targets in plan §3 need these corrections:

| Check | Problem | Correction |
|---|---|---|
| 2. T60 ±20 % | **(a)** The output is the pickup's *nonlinear* function of the tine. Broadband or RMS decay therefore mixes the decay of the fundamental with fading bark, so early decay looks faster at high velocity. **(b)** The jRhodes samples were **denoised and trimmed**, and their "length" is not T60 (R05 §Parameters, §Gaps). **(c)** Treble decay is non-monotonic in the reference (E5 5.2 s < A5 8.4 s, R05 table): a smooth "curve" will not fit both. | Fit **per partial**: heterodyne or STFT magnitude of k·f0 at k=1..6 and at the tine modes. Fit two slopes: EDT, 0 to −10 dB, and late, −10 to −35 dB *above the measured noise floor* (the last 0.3 s; stop 10 dB above it). Compare model and reference **per root, per matched velocity**, not against a smoothed curve. |
| 4. Release 0.5/0.4/0.3/0.2 s | These are **sfz `ampeg_release` settings chosen by the sample author** (R05 §Parameters). They are not measurements of damper behaviour, and the set contains no release samples. | Keep them as soft targets (±50 %). Also check that there are no clicks: the post-note-off high-pass residual must stay below −50 dBFS. |
| 6. "About 30 dB of level range" | The 30 dB is the **excitation** range (R05 Eq. 1, recommendation). The pickup nonlinearity compresses the output level, and jRhodes' own level law is 40·log10(v/127) + 3 dB/layer (R05 Eq. 6). The check mixes these up. | Check **monotonic** RMS versus velocity. Check output range v=1→127 between 20 and 40 dB (ESTIMATE band). Check bass layer-centre spacing of 2–5 dB per jRhodes layer (velocities 24/60/84/104/120 from the no-xfade map). Check **bark onset**: the H2–H5/H1 energy ratio rises by ≥ 6 dB between v=60 and v=120, with the steepest slope for v in [88, 104]. |
| 3. Overtones "much faster" | Not quantified | O1 T60 ≤ 0.25 × V1 T60 and O2 ≤ 0.1 × V1 T60 (ESTIMATE, from R03 code damping law: 0.19 s vs 7.5 s at A4). |
| Spectral targets in general | jRhodes was recorded **with EQ** ("treble boost and low-mid scoop", R05 §Summary, §Gaps). Absolute partial levels are biased. | Use jRhodes for time-domain metrics (T60, ratios, velocity *differences*). Do not use it for absolute spectral balance. If balance is compared, fit and remove a smooth spectral tilt first. Add the UVI A4 8-force set (R03) as a second, analysis-only reference for velocity→timbre. |
| 7. A/B "final judge" | No protocol, no loudness matching, no anchor | See §5.1 T-LISTEN: blind and randomised, RMS-matched within ±0.5 dB, with an STK-Rhodey low anchor (R04). Record a pass rule. |
| (missing) aliasing | Plan §2 only says "if host tests show audible aliasing" | Quantitative check in Phase 2 (§5.1 A7). |

There is also a matching problem. jRhodes has 15 roots, each stretched over 3–15 keys, and only 3 layers above F4
(R05 tables). Compare only at the **root keys** and only at layers that really exist. Interpolate everything else.

### 5. MAJOR: risky items are placed too late

| Item | Where the plan puts it | Why that is late | Move to |
|---|---|---|---|
| Effects CPU | Phase 5 ("profiled as it lands") | Phase 2's DoD "8 voices fit the measured budget" cannot be judged without knowing what the FX need. R06's 5 % is a Felucca conversion at 128-frame halves. | Phase 0 bench: port HALL, the phaser and the delay core unmodified into the bench firmware and time them. Allocate the 2.9 ms budget up front: **voices ≤ 1.9 ms, FX ≤ 0.5 ms, UI/USB margin ≥ 0.5 ms** (ESTIMATE split). |
| Aliasing | Phase 3, conditional | The 2× pickup oversampling decision changes voice cost and the §6 drop order | Phase 2 gate A7 |
| Flash layout for user presets | Phase 7 | It needs a platform change (`FL_RANGE_OK`, `platform.c:71`; R07 §Presets) and a sector-pair choice that must not collide with the OTA staging area at 0xE0000 (`docs/API.md` Memory and flash) | Decide and reserve in Phase 0 (P0-4). Implement in Phase 7. |
| Settings record layout | Phase 4 (implicit) | Phase 2–3 voicing params must be stored and must stay compatible | Phase 4 first task, with a version byte and the append-only rule (Critique 11) |
| Canvas size | Never stated. Plan §6 says "240×64×2 = 30 KiB" | The PANEL mockup is full-screen. R07 §Cost says either strips or `CV_MAX` 240×176 (84 KiB pool). `CV_MAX` is defined in `platform.c:57`. | Phase 0 memory budget; Phase 4 platform delta |
| Pool baseline | Never | Delay 94–128 KiB + HALL 12.5 + chorus 2 + canvas 30–84 KiB. The current pool use by USB audio and the platform is not recorded. | P0-4 |
| First release | Phase 8 (optional) | Early user feedback and the hub ID are needed earlier | GitHub pre-release 0.1 after Phase 2 |

### 6. MAJOR: changes to upstream (Dinghy) files are not declared, so merges will hurt

CLAUDE.md says not to edit `platform.c`, `hal/`, `loader/` and the other platform files, so upstream merges stay
clean. The plan needs at least these changes:

- `platform.c`: `FL_RANGE_OK` for presets, `CV_MAX` for the canvas, an app SysEx hook before `ota_service()`
  (`platform.c:261`; R07 §Web editor).
- `gfx.c`: `ramp(fg,bg)` for text on filled cells (R07 §Screen architecture).
- `tests/host.c`: encoders, holds, velocity, screenshots.
- `tools/build.py`: the identity fix (Critique 7), new lints and the static budget.

None of these is listed.

**Fix.**

- Keep a "platform delta" table in README §Updating the platform, listing each touched upstream file and why.
- Make each delta an **override hook** so the app sets it before `#include "platform.c"`: `#ifndef CV_MAX`,
  `#ifdef APP_FL_RANGE`, a weak or `#ifdef APP_SYSEX` call. Offer the hooks upstream as PRs to zvenson/dinghy. If
  Dinghy accepts them, Frodhes's delta goes to zero.
- Put each delta in its own commit.
- Add a weekly CI job that merges `upstream/main` into a scratch branch and runs the tests (§5.3, job `upstream`).

### 7. MAJOR: the release identity scheme collides

- `tools/build.py:281` sets `PRODUCT = "FM-1_95" + m[2]`, using only the **minor** digit. So 0.1 and 1.1 both become
  `FM-1_951`.
- 1.0 becomes `FM-1_950`, which is also the identity of every dev build (`build.py:50`).
- The help text says "FM-1_9XY" (`build.py:273`), which is wrong.

Felucca encodes major and minor (1.0 becomes `FM-1_910`, FEL `BUILDING.md` "Build"). It also ships a release folder
with `SHA256SUMS`, `LICENSE`, `LICENSING.md`, `LICENSES/` and a source tarball.

The plan only says "first release to the hub (`FM-1_95N`; check the ID is free)" in optional Phase 8.

**Fix (tasks H6, H7).**

- Refuse `--release X.0` and any major other than 0 until a second identity block is agreed.
- Reserve `950` for dev builds.
- Releases are 0.1–0.9 (identity 951–959). Map them to phase milestones (§5.4).
- Check the hub in Phase 0, not Phase 8.
- Add `tools/release.py` to build the bundle.

### 8. MAJOR: "port Felucca" depends on code that is not in the clone

The effects that §4 proposes to port call `softclip`, `mulq15`, `mulq16`, `osc_sine`, `clamp`, `SINE`, `SVF_G`,
`TANH_Q15`, `FS` and `CTL`. All of these come from Felucca's MIT `engines/` submodule, which is **empty** in the clone
(R06 §Source, §Gaps). The chorus delay range even depends on `osc_sine`'s unseen scale (R06 §CHORUS). The plan never
mentions this.

**Fix (task P2-1).**

- Write `firmware/src/dsp.c` with these helpers and their tables, using `gen_tables.py`-style generators in
  `tools/gen_rhodes.py`.
- Unit-test the semantics the ported code assumes: rounding of `mulq15`, the `osc_sine` range of ±32767 or ±32768,
  and `softclip` continuity.
- Port Felucca's `reverb_test` checks (RT60 ranges, no metallic ringing) to confirm the port behaves like the
  original.

### 9. MAJOR: the CPU measurement method and overload behaviour are not specified

- Timer 5 (10 kHz key scan, USB-audio service) **outranks and nests inside** the audio ISR
  (`platform.c:194-216`). The measured render time therefore includes the timer's work, and the work varies with USB
  audio streaming (`uac_service`).
- Felucca measures every half and **sheds a voice after two halves above 85 %** (R06 §Effects chain, `audio.c:46-62`).
- Frodhes has no overload protection. A late ISR crackles, and one that never finishes trips the watchdog
  (RULES.md "The audio interrupt").

**Fix.**

- Add a profile build flag (task P0-2). It times `app_audio` with `fm1_ticks()` and records max and mean per second,
  with USB audio streaming both on and off.
- Show the numbers on screen and send them as a SysEx or CC dump.
- Specify the worst-case scenario for every budget DoD: 8 voices at v=127 in the bass, with the pedal, every FX on, the
  A.DLY at maximum and USB audio streaming.
- Add load-based voice shedding (task P5-11), the same as Felucca's.

### 10. MAJOR: on-board keys always play at velocity 100

`frodhes.c:114` calls `rh_note_on(held[k], 100)`. The plan's realism rests on velocity: the bark comes through the
pickup (D6, R05 Eq. 1). On the FM-1's own keys, the realism lever is therefore fixed. The plan's VEL CURVE includes
FIXED but never says where on-board velocity comes from.

**Fix.**

- Add a `KEY VEL` param (default 100) on the VOICING page or under SELECT-hold. Felucca's precedent is MENU > SELECT
  VEL (FEL `run_tests.sh` "SELECT MODW / VEL").
- Phase 0 confirms whether the hardware can sense velocity at all (P0-5).

### 11. MAJOR: no versioning rule for settings and presets

R07 §Parameter system states Felucca's rule: param ids and enum values are **append-only**, and stored records map
values *by count* (`upreset.c:12-17`). PLAN §5 defines 40 B settings and 48 B presets with no version byte, no
migration rule and no test.

Every firmware update in Phases 4–7 changes `PARAMS[]`. Without the rule, saved user presets break silently.

**Fix (task P4-1).**

- Add a record header: `magic`, `ver`, `nparams`.
- Adopt the append-only rule in CLAUDE.md.
- Add a host test that loads every committed past-version fixture (`tests/fixtures/settings_v*.bin`).

### 12. MINOR: the boot guard and recovery are never exercised

The plan's Phase 0 installs the placeholder, but it never practises the documented recovery paths:

- the two-crashes-at-boot auto-rescue;
- OCT- held at power-on;
- FM-1-transporter (RULES.md "When it crashes").

Phase 7 adds flash writes, the riskiest operation, without a power-cut test on hardware.

**Fix.** P0-1 runs a recovery drill using a deliberately crashing build (`-DFRODHES_CRASHTEST`). Repeat it in every
release checklist (§5.4). P7-6 adds a power-cut test during a preset save.

### 13. MINOR: the hub ID check comes too late

README already says to check the hub before the first public release. With a GitHub pre-release after Phase 2, move
the check to P0-6 and repeat it at every release.

### 14. MINOR: the "everything in the project folder" requirement is not addressed

The toolchain defaults to `~/.jieli/toolchain`, the SDK to `~/fw-AC79_AIoT_SDK` (`build.sh:9-10`), Python packages to
the global `pip3`, and the reference audio to an unspecified place.

Also, `tools/get_toolchain.sh` downloads `https://pkgman.jieliapp.com/s/linux-toolchain` with **no version pin and no
checksum**. That hurts reproducibility and supply-chain safety, and CI would silently pick up a new compiler.

**Fix (task H3).**

- Add a `make setup` target. It installs into `.local/` (git-ignored): `.local/jieli/`, `.local/fw-AC79_AIoT_SDK/`
  (sparse: only the 3 files `build.py` needs), `.local/venv/`, `.local/reference/` and `.local/sources/`.
- Export `JIELI_TOOLCHAIN` and `AC79_SDK` from the Makefile. No upstream script changes: `get_toolchain.sh` already
  takes `DEST`.
- Pin the toolchain with a sha256 in `tools/toolchain.lock`.

### 15. MINOR: the reference-data plan is under-specified but realistic

- **Size.** The STREAMINFO headers already fetched say: 65 mono FLACs, **22.9 MB** compressed, 892 s of audio, about
  79 MB decoded (`sources/05-…/flac-streaminfo-mono.txt`, summed). The upstream README says 22 MB for the mono set.
  That is small enough to fetch in CI. The stereo variants are not needed.
- **Fetch.** Use `git clone --depth 1 --filter=blob:none --sparse` of `sfzinstruments/jlearman.jRhodes3d` with
  `sparse-checkout set jRhodes3d-mono`, or per-file raw URLs. Pin a commit. Verify the sha256 values that P1-1 commits.
  Store the files in `.local/reference/jrhodes3d/`.
- **Second reference.** Fetch the UVI A4 force set from the IRCAM zip on demand into `.local/`, for analysis only.
- **CI.** Never needs the audio. It compares renders against the committed `reference.json` numbers.

### 16. MINOR: the resonator topology is undecided

R01 recommends the magic circle or coupled form, and R02 the coupled form or a phasor at low f/fs. R03 and R05 use a
normalised direct form with exact poles. R06 proposes the SVF band-pass, which never quantises to less damping than the
exact filter. D5 fixes the pole placement but not the structure.

With 1 − r ≈ 3·10⁻⁶ at E1 (R05 §Discretization), the structure decides three things: noise floor, limit cycles and
pitch error. **Fix:** task P2-2, a measured comparison with an ADR.

### 17. MINOR: pitch bend, half-pedal and MIDI channel are missing

- Pitch bend: R02 MPE; R04 exp pitch table.
- Half-pedal: CC64 curve (R05 Eq. 8).
- MIDI channel / omni: R07 MENU.
- Pedal-up with notes held should release without a thump (R05 EP-MK1 notes).

These are cheap once P2-3 exists.

### 18. MINOR: Phase 3 sign-off is subjective and has no record

"Signed off by the user" needs a protocol (T-LISTEN in §5.1) and a stored result: `docs/model/reports/listening-*.md`,
containing the scores, not audio.

### 19. MINOR: no GitHub workflow

"Push to GitHub" is a user requirement, but the plan has nothing about branches, PRs, CI status checks, tags or
releases. The remote is named `fhodes` (`git remote -v`), which looks like a typo of Frodhes; see Open question 1.
**Fix:** §5.3 and §5.4.

### 20. MINOR: the jRhodes licence is misstated and attribution is missing

PLAN §3 says "CC0 vs CC-BY-SA vs NC". R05 establishes that the `LICENSE` file governs: **samples CC BY-NC 4.0,
everything else CC0**. The "CC-SA-BY" sfz header is a stale string.

Committing **derived measurements** (T60s, ratios, levels) is low risk. They are facts about a recording, not an
adaptation of the expressive work, and the licence explicitly allows musical use. This is not legal advice. Even so:

- **attribute** them in `reference.json` (`"derived_from": "jRhodes3d by Jeff Learman, samples CC BY-NC 4.0"`) and in
  LICENSING.md;
- never commit sample excerpts, including as a "≤ 2 kB 8-bit noise sample" (R05 §Noises) or as A/B listening files;
- keep listening packs in `build/`.

EP-MK1's repo is GPL-3.0 (R05 S4). The plan's "ideas only" is stricter than necessary, which is harmless.

---

## Completeness matrix

Status values:

- **COV**: covered.
- **PART**: partly covered.
- **MISS**: missing.
- **CONF**: the research files contradict each other and the plan resolved it silently.
- **VIOL**: the plan or repo contradicts the recommendation.
- **DISC**: missing, and I recommend discarding it (see the Discard list).

§ refers to PLAN.md sections.

### R01: DAFx-17 (Pfeifle)

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R01-1 | Do not port the FD beam (§FM-1 1) | D1 | COV |
| R01-2 | Pickup Φ as a host LUT, 256–1024 Q15, lerp (§2) | D2, §6 | COV |
| R01-3 | Φ difference vs dΦ/dx·velocity (§2) | D2 picks the difference | COV |
| R01-4 | Rest offset and field width as per-key or voicing params (§2) | §2 geometry; Phase 3 "pickup gap"; not in `gen_rhodes` outputs per key | PART |
| R01-5 | Fold the circular-arc trajectory into the table (Eq. 8) | — | MISS (low; fine to fold into the R03 family) |
| R01-6 | Magic-circle or coupled-form resonator (§3) | D5 places poles, structure unstated | CONF → P2-2 |
| R01-7 | H polarisation, weakly or amplitude-dependently coupled (§3) | §2 H1, Phase 3 | COV |
| R01-8 | H/V ratio ≈ 0.2–0.25, whirl envelope (Fig. 4/11) as a target | — | MISS (add to Phase 3 check) |
| R01-9 | Precomputed hammer pulse (§4a) | D6 | COV |
| R01-10 | Clamp displacement to the table window (§5) | §2 diagram | COV |
| R01-11 | Q1.31 decay coefficients, 64-bit products (§5) | D5 | COV |
| R01-12 | Per-key gain normalisation, since the derivative scales with f0 (§5) | §3 gen_rhodes | COV |
| R01-13 | Aliasing: 2× oversampling of the pickup or a smooth Φ (§7) | §2, Phase 3 conditional | PART (too late, Critique 5) |
| R01-14 | Validation: a sinusoid through Φ gives two asymmetric spikes per period | §3 check 5 | COV |
| R01-15 | Velocity → larger excursion → richer spectrum | §3 check 6 | COV (metric wrong, Critique 4) |

### R02: Sønderbo thesis

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R02-1 | FDTD and dynamic grid infeasible | D1 | COV |
| R02-2 | Modal tine with 3–4 modes, per-key measured ratios and decays | D3, D7, §2 | COV |
| R02-3 | Do not use uniform EB ratios (6.27/17.55) | D3 | COV |
| R02-4 | Manual tine lengths as input to a host modal analysis | — | DISC |
| R02-5 | Excitation weights φ_n(x_c); contact position is the main timbre control | §2 "strike weights per key" | COV |
| R02-6 | Damper: crossfade the decay coefficient; abrupt damping clicks (Fig. 3.4) | §2 note-off 5 ms | PART (no click metric) |
| R02-7 | Fold the EP-MK1 pickup chain into one LUT | — (physical Φ chosen) | DISC |
| R02-8 | Normalise state to full scale, never SI units in fixed point | — | PART (no scaling spec) |
| R02-9 | a1 ≈ 1.99965 at 41 Hz needs Q2.30 or the coupled form | D5 "Q30" | PART (state the Q2.30 range ±2; test it) |
| R02-10 | f0 is an explicit table value, not derived from geometry | §3 gen_rhodes | COV |
| R02-11 | Overtones must decay much faster | §3 check 3 | COV (unquantified) |
| R02-12 | Pitch bend (MPE ±1 st in the thesis) | — | MISS |
| R02-13 | Ideas only; thesis publication needs the author's agreement; repo has no licence | §8 "ideas only" | **VIOL** (PDF and repo committed, Critique 1) |

### R03: Falaize & Hélie PHS

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R03-1 | Full PHS / discrete gradient not worthwhile | D1 | COV |
| R03-2 | Pickup g(q)·q̇ or Φ antiderivative LUT; normalise dynamic range on the host, per-key scale | D2, §3 | COV |
| R03-3 | Use the code's strongly non-monotonic geometry family | §2 | COV |
| R03-4 | Impulse-invariant poles to avoid trapezoidal warping of 7–25 % | D5 | COV |
| R03-5 | Cap at 3–4 modes | §2 mode count | COV |
| R03-6 | Map velocity to hammer **speed**, which sets the pulse shape, not just amplitude | D6 (amplitude only; width per register) | PART (add velocity-dependent width, also R06 §Exciter) |
| R03-7 | RC/RLC load biquad (~5 kHz) | §2 | COV |
| R03-8 | Q plan: Q1.30 state normalised to the tip maximum, Q15 clamped pickup index | partly D5 | PART |
| R03-9 | Tonebar as a second coupled resonator | D4 | COV |
| R03-10 | Add release damping (not in the source) | §2 | COV |
| R03-11 | LUT aliasing mitigation | §2 | PART (late) |
| R03-12 | Limit cycles: round toward zero, end voices below a threshold | §8 | COV (needs a unit test) |
| R03-13 | UVI reference WAVs: do not redistribute | — | **VIOL** (committed) |
| R03-14 | UVI A4 at 8 forces as a velocity→timbre reference | — | MISS (analysis only) |
| R03-15 | Sweet spot l_p 1.5 mm, l_δ 0.5 mm | §2 | COV |
| R03-16 | Measured 1–2 kHz content missing from the model (tonebar or polarisation) | — | MISS (validation target) |

### R04: rdpiano and STK

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R04-1 | STK Rhodey as a cheap fallback voice | — | DISC (use as the A/B low anchor only) |
| R04-2 | Hybrid FM "bark/ping" layer | — | DISC (unless check A6 fails) |
| R04-3 | Log-domain envelopes, rate table, segment IRQs | — | DISC |
| R04-4 | Exponential pitch LUT (4096 steps per octave) for detune and bend without division | — | **MISS** (needed, Critique 3) |
| R04-5 | Multiply-free log interpolation | — | DISC |
| R04-6 | Stereo autopan tremolo `(1−d)+d(0.5±0.5 sin)` | §4 | COV |
| R04-7 | Never use Roland ROMs or firmware; SpaceD IP uncertain | §8 (ROMs only) | **VIOL** (ROM disassemblies committed) |
| R04-8 | AC791N clock and MUL latency unknown | Phase 0 | COV |
| R04-9 | 350 Hz peak EQ | — | DISC |

### R05: jRhodes3d, EP-MK1, hardware facts

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R05-1 | Per-key T60 table, not a constant Q | D7 | COV |
| R05-2 | Initial curve: 18 s up to MIDI 62, then down to 1.5 s at 96 | §3 check 2 | COV (method, Critique 4) |
| R05-3 | Release 0.5/0.4/0.3/0.2 s by register | §3 check 4 | PART (sfz settings, not measured) |
| R05-4 | Linear-in-dB excitation law, default 30 dB, user range R 12–36 dB as 128×Q15 tables | D6, VEL CURVE enum | PART (R not exposed as a range) |
| R05-5 | Bark onset at v ≈ 96 | check 6 | COV |
| R05-6 | Velocity drives excitation, not output gain | D6 | COV |
| R05-7 | Pickup per voice, never on the mix | §2 | COV |
| R05-8 | Skip silent voices; clear state on retrigger after a 1 ms fade | §2 voice management | COV |
| R05-9 | Q30/Q31 for the tonebar resonator | D5 | COV |
| R05-10 | Normalised 2-pole, hammer as the initial state | — | CONF → P2-2 |
| R05-11 | Key-down thump (hammer leak, −30 dB) | Phase 3 | COV |
| R05-12 | Damper thump: −36 dB pulse into the tonebar | §2 | COV |
| R05-13 | Felt noise from an LFSR | §2 optional | COV |
| R05-14 | Pedal up/down noise | — | MISS (low) |
| R05-15 | Half-pedal curve on CC64 | — | MISS |
| R05-16 | Pedal-up with notes held: release with no thump | "Sustain holds it all" | PART |
| R05-17 | Bass goes sharp on hard hits | §2 optional, Phase 3 | COV |
| R05-18 | Suitcase square-ish tremolo; AM before 1969, pan after | §4 TREM | COV |
| R05-19 | Tremolo delay and fade | — | MISS (low) |
| R05-20 | jRhodes is EQ'd and denoised, so not a neutral spectral target | — | **MISS** (Critique 4) |
| R05-21 | Sample lengths are not T60; measure from the FLACs | Phase 1 | COV |
| R05-22 | 73-key vs 88-key range | §9 Q1 | COV |
| R05-23 | 5 hammer-tip bands by register | §2 | COV |
| R05-24 | Gap 1/32" mid, 1/16" bass and top | §2 | COV |
| R05-25 | EP-MK1-style panel (Bark/Bell/Decay/Tone; thump levels) | Superseded by R07 (user requirement) | CONF (resolved correctly; damper-thump and key-down-thump levels not exposed) |
| R05-26 | About 3 dB level per layer in the bass | — | MISS (as a check) |
| R05-27 | EP-MK1 tremolo φ² bug: do not copy | n/a | COV |
| R05-28 | jRhodes LICENSE governs (CC BY-NC samples); EP-MK1 is GPL-3.0 | §3, §8 | PART (misstated; no attribution) |

### R06: Felucca effects

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R06-1 | Order DRIVE → TREM/WAH → CHO/PHA → DELAY → REVERB → master | D8, §4 | COV |
| R06-2 | Mono until the tremolo; coefficients per sub-block, gains ramped per sample | §4 | COV |
| R06-3 | DRIVE: port `track_dist`, own tanh LUT, per-TYPE table | §4 | COV |
| R06-4 | Equal-power autopan and AM tremolo, from scratch | §4 | COV |
| R06-5 | WAH: envelope follower plus `pf_svf` with a new k<1 table | §4 | COV |
| R06-6 | Phaser: port `ph_side`, free LFO, DEPTH optionally drives feedback | §4 | COV |
| R06-7 | Chorus: 2 taps, stereo, keep the dry signal | §4 | COV |
| R06-8 | Delay time glide; A.DLY wow, flutter, LP, saturation, slew; 22.05 kHz storage option | §4 | COV |
| R06-9 | Delay DEPTH = feedback **and** level (R06) vs separate DEPTH and FDBK (R07 hold layer) | — | CONF (pick one in the PARAMS table) |
| R06-10 | HALL: call per 32-frame sub-block (CTL even, CTL/2 ≤ 131) | §4 "16–32" | PART (state CTL=32 for HALL) |
| R06-11 | SPRING as an optional variant | §4 | COV |
| R06-12 | `master_out`; skip LOWCUT/BASS+ unless the FM-1 speaker needs it | §4 | PART (speaker never evaluated) |
| R06-13 | **Write the missing MIT helpers** (softclip, mulq15, osc_sine, SVF_G, TANH) | — | **MISS** (Critique 8) |
| R06-14 | Do not port `phys_dsp.c` (64-bit divisions) | D1 | COV |
| R06-15 | Error-feedback DC blocker, `px_sin`, `px_exp2` utilities (MIT notice) | — | MISS (useful for P2-3) |
| R06-16 | Felucca's 1.7 %/100-instruction factor is not Frodhes's; measure | §4, Phase 5 | COV |
| R06-17 | Overload: shed a voice after 2 halves above 85 % | — | **MISS** (Critique 9) |
| R06-18 | Rate-limit the HALL gain recompute while the knob turns | — | MISS (low) |
| R06-19 | reface details unverified (403) | §8 | COV |
| R06-20 | FX pool 110–145 KiB | §4, §6 | PART (no pool baseline) |

### R07: Felucca UI and reface-style panel

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R07-1 | PANEL with 5 reface columns | D9, §5 | COV |
| R07-2 | ALGORITHM=TYPE, SELECT=tempo, PRESETS=presets, K3=DRIVE, K4=REVERB | §5 | COV |
| R07-3 | Focus / flick / hold-layer gesture; 250 ms knob-quiet after a layer | §5 | PART (no quiet period) |
| R07-4 | One `PARAMS[]` table drives everything | §5 | COV |
| R07-5 | **Append-only ids and enums; migrate by count** | — | **MISS** (Critique 11) |
| R07-6 | Draw on change with per-element signatures; ≤ 60 fps | Phase 4 | COV |
| R07-7 | Port `ramp(fg,bg)`: text on filled cells (`gfx.c`, a platform file) | — | MISS (platform delta) |
| R07-8 | Canvas: strips or `CV_MAX` 240×176 (84 KiB) | §6 "30 KiB" | CONF (undecided; platform delta) |
| R07-9 | Hot-accent card and 2 s header messages for off-screen values | — | MISS (minor) |
| R07-10 | Help hints and first-run hints | — | MISS (minor) |
| R07-11 | MENU rows: LEDS, COLOR, ACCEL, HOLD, MIDI CH, VEL, HELP | "HOME hold: MENU" | PART |
| R07-12 | Screen off | — | MISS (minor) |
| R07-13 | Settings: memcmp before write; deferred writes | §5 "saved when silent" | PART |
| R07-14 | User presets 16×48 B in an A/B pair at new sectors; widen `FL_RANGE_OK` | §5 | PART (addresses and platform change missing) |
| R07-15 | Preset naming (`ui_name`) or automatic names | — | MISS |
| R07-16 | Load per detent, SAVE-hold undo, `*` when edited | §5 | COV |
| R07-17 | "QUIET TO SAVE" feedback | — | PART |
| R07-18 | Program Change and CC map | §5 | COV |
| R07-19 | Editor: own SysEx tag `F0 7D 46 52`, DESC subset, app SysEx hook | Phase 8 | PART (tag and hook unspecified) |
| R07-20 | Host UI tests (frame/press/hold/turn) and layout lint | Phase 4 | PART (harness cannot do it, Critique 2) |
| R07-21 | Check the SEL vs SCL label | Phase 0 | COV |
| R07-22 | Velocity-curve graph on VOICING | — | MISS (minor) |
| R07-23 | Encoder acceleration, off by default | — | MISS (minor) |
| R07-24 | Button LEDs (lit, breathing, beat) | §5 partially | PART |

### R08: Felucca tempo, scales and arp

| ID | Recommendation / FM-1 note | PLAN | Status |
|---|---|---|---|
| R08-1 | `bpm10`, a volatile `beat_samples`, 32-bit maths only | §5 | COV |
| R08-2 | Tap on PLAY (last 4 taps, from the 3rd, 2 s reset); ignored on an external clock | §5 | PART (external-clock case) |
| R08-3 | Clock in: main-loop µs stamps, average over 24 pulses plus a one-pole, reject, 500 ms fallback | §5 | COV |
| R08-4 | Clock out 24 PPQN with Bresenham, off by default; hold PLAY toggles it | §5 | PART (control unassigned) |
| R08-5 | Transport FA/FB/FC handling (re-phase LFOs and arp) | — | MISS |
| R08-6 | One 1/96-unit sync table; chorus needs 4BAR (1536) | §5 | PART (no 4BAR) |
| R08-7 | Delay glide; fold an octave to fit the buffer | §5 | COV |
| R08-8 | Optional re-trigger on the beat | — | MISS (low) |
| R08-9 | OFF/SNAP/WHITE; 11 scales; TRN outside the snap | §5 | COV |
| R08-10 | MIDI IN not quantised (optional MIDI IN QNT) | §5 | COV (option missing) |
| R08-11 | Per-note reference count | §5 | COV |
| R08-12 | LEDs: dim scale tones, full root; display "C DOR · SNAP" | §5 | COV |
| R08-13 | DIA3/DIA7 chord keys | — | DISC (defer) |
| R08-14 | Arp in the main loop on the pulse counter; strum | Phase 8 | COV |
| R08-15 | **Port `tests/scale_test.c` as a host test** | — | MISS |
| R08-16 | Arp swing | — | DISC (defer with arp) |
| R08-17 | BPM on SELECT; GLO+SELECT anywhere | §5 | COV |
| R08-18 | SCALE page knob order: R08 ROOT/SCALE/MODE/TRN vs R07 MODE/SCALE/ROOT/TRANS | R07 chosen | CONF (silent; fine) |
| R08-19 | ARP button: R08 arp vs R07 DELAY column | R07 chosen, arp moved to SEQ | CONF (silent; correct for the reface requirement) |
| R08-20 | Tempo param needs 16 bits, but R07 says "a byte per param" | — | CONF (the PARAMS table needs 16-bit entries) |

### Cross-cutting FM-1 notes (RULES.md, API.md, code)

| ID | Note | PLAN | Status |
|---|---|---|---|
| F-1 | No float, no 64-bit `/` or `%`. The device link catches it, but the host does not. | Constraints | PART (add a host lint, §5.2) |
| F-2 | Division by zero gives a wrong value on the device instead of trapping | — | MISS (UBSan `integer-divide-by-zero` on host, as Felucca) |
| F-3 | Timer 5 nests in the audio ISR; USB-audio work varies | — | MISS (Critique 9) |
| F-4 | Flash erase stalls audio; save only when silent | §5, §8 | COV |
| F-5 | Watchdog 8 s; `platform_poll` every few ms (heavy redraws, preset banks) | — | MISS (Phase 4 check: maximum main-loop gap) |
| F-6 | On-board keys are fixed at velocity 100 | — | **MISS** (Critique 10) |
| F-7 | Package identity `FM-1_95N`; check the hub | Phase 8 | PART (Critique 7, 13) |
| F-8 | Boot guard and recovery | — | MISS (Critique 12) |
| F-9 | Keep upstream files clean | — | MISS (Critique 6) |

### User requirements

| ID | Requirement | PLAN | Status |
|---|---|---|---|
| U1 | Realistic physically modelled Rhodes | §1–3, Phases 1–3 | PART (acceptance criteria need fixing, Critique 4) |
| U2 | reface CP columns | D9, §5 | COV |
| U3 | Delay, reverb, phaser | §4 | COV |
| U4 | Tempo | D10, §5 | COV |
| U5 | Scale modes | §5 | COV |
| U6 | Sustain button | D11 (REC), CC64 | COV (half-pedal and pedal-noise missing) |
| U7 | Everything kept in the project folder | — (toolchain and SDK in `~`, reference location wrong) | **MISS** (Critique 14) |
| U8 | Push to GitHub | — (no branch, PR, CI or release flow; remote named `fhodes`) | **MISS** (Critique 19) |

---

## Discard list

| Item | Source | Reason |
|---|---|---|
| Committed research sources (papers, WAVs, disassemblies, third-party repos) | `a490f9f` | Licensing (Critique 1). Replace with a manifest and a local cache. |
| STK Rhodey as a fallback voice | R04-1 | It is not physical modelling. It has linear 1.5 s decays and aliases above MIDI 54 (R04 §Measured). Keep it only as the **low anchor** in blind listening tests. |
| FM "ping" bark layer | R04-2 | Duplicates the pickup's job (D2) and aliases. Reconsider only if bark check A6 fails after Phase 3. |
| Log-domain envelopes and rdpiano rate table, multiply-free interpolation | R04-3, R04-5 | Modal decays are pole radii; the pi32v2 has a multiplier. |
| EP-MK1/Sønderbo waveshaper pickup chain | R02-7, R05 | Superseded by the physical Φ. The thesis found it harsh with overtone-rich input (R02 §5.2). |
| Manual tine lengths as model input | R02-4 | Partial ratios come from measurement (D3); lengths add nothing. |
| 2-D (H/V) pickup field table | R01 §2 | Costs memory and lookups. A 1-D Φ with H1 folded in at a small weight is enough until the A/B says otherwise. |
| `1.7 % per 100 instructions` as a planning number | R06, PLAN §4 | Felucca's ratio at 128-frame halves. Delete it from §4 once P0-3 has measured the real cost. |
| 8 pickup voicings × 1024 entries (16 KiB) up front | PLAN §6 | Start with 2 tables (TONE blend) and add more only if the TYPE variants need them. Size is not a problem; tuning effort is. |
| Browser editor in Phase 8 | PLAN §7 | Needs a platform SysEx hook and a second UI to maintain. Defer past 1.0 unless Dinghy accepts the hook upstream. |
| MIDI clock out | PLAN §5 | Low value for an EP and risks fighting the DAW. Keep it behind a flag, last in Phase 6. |
| Chord keys, arp swing | R08-13, R08-16 | Out of scope for a Rhodes. Revisit with the arp. |
| 350 Hz "MKS-20" EQ | R04-9 | Tuned by ear to a different instrument. |

---

## Detailed implementation spec

### 5.1 Test strategy per phase

**Test levels.** All but D, E and the CI firmware job run on the host. Everything except the listening tests runs in CI.

| Level | What | Where | Gate |
|---|---|---|---|
| U: unit | Integer maths, tables, pure logic, against a double-precision oracle | `tests/unit/*.c` (one file per module, a tiny `check()` header), built with `-fsanitize=address,undefined -fno-sanitize-recover=all` | Exact or stated tolerance; exit code |
| G: golden | Full-app renders and screens, bit-exact | `tests/scripts/*.txt` → `build/host/*.wav`, `*.ppm` → FNV-1a 64 in `tests/golden/audio.txt` and `screens.txt` | Hash equality. `GOLDEN_UPDATE=1` rewrites; the PR must show why (analysis diff). |
| H: health | Every golden render | in `tests/check_render.py` | Felucca limits (FEL `regress.c:37-48`): no `|x|` ≥ 32700 except in an "overload" scenario; DC < 400 LSB; peak > −42 dBFS; voices free ≤ 12 s after the last note-off with the pedal up; tail AC peak < 6 LSB in the last 0.25 s; click detector (high-pass at 8 kHz, residual spike < −50 dBFS at note-off and steal) |
| A: analysis | Physical-realism metrics vs `docs/model/data/reference.json` | `tools/analyze.py` with thresholds in `tests/analysis_thresholds.toml` | Thresholds below; report in `build/analysis/` |
| C: cost | Host instructions per sample (Linux `perf stat -e instructions:u`, macOS `proc_pid_rusage`) and static loop metric of `rh_render`, `fx_render` and `app_audio` from `build/frodhes.dis` (port FEL `target_budget.py`) | `tests/cpu_baseline.txt`, `tests/target_budget.txt` | +25 % host, +10 % static. `BUDGET_UPDATE=1` rewrites. |
| F: fuzz | MIDI byte streams, settings and preset records, key/knob storms | `tests/fuzz/*.c`, ASan/UBSan, fixed seed, 20k iterations in CI and 1M nightly | No sanitizer error, no hang (one host second must render ≤ 2 s wall), no stuck voice after all-notes-off + pedal up |
| D: device | Checklists (below), profile dumps and capture comparison | `tools/fm1_profile.py`, `tools/fm1_capture.py` | Manual, recorded in `docs/model/reports/device-<phase>.md` |
| L: listening | Blind A/B | `tools/ab.py` → `build/ab/` (never committed) | Protocol T-LISTEN |

**Why exact hashes for audio.** The firmware is integer-only, so the same source gives bit-identical output on macOS
clang and Linux gcc, *provided there is no undefined behaviour*. For that reason the golden job also runs under UBSan:
signed overflow in a Q30 resonator could otherwise make the hashes differ between compilers. Tolerances belong in A,
not G. Screens are also exact, since `gfx.c` is integer, and are stored as hashes. On a mismatch,
`tests/screen_diff.py` writes `build/host/diff-*.png`; no PNG binaries are committed.

**Host harness extensions (P1-7).** These are additive and stay under `DINGHY_HOST`. Script verbs:

- `T key K VEL` / `T keyup K`;
- `T press ID` / `T release ID` / `T tap ID` / `T hold ID MS`;
- `T turn ENC DETENTS`;
- `T shot NAME`;
- `T expect led ID 0|1`;
- `T expect midi ST D1 D2`;
- `T profile`, which prints instructions per block.

Also add an exit code from `T expect` failures, and a `RENDER_ONLY` mode for analysis (no screens).

**Unit tests (U).** Thresholds are ESTIMATES to be confirmed in P2-2.

| Module | Checks |
|---|---|
| `dsp.c` helpers | `mulq15`, `mulq30` and saturating add: exhaustive edges (±32767, −32768, 0, ±1) vs exact rational, rounding as documented. `tanh` and `softclip` LUT max error ≤ 2 LSB. Sine table ≤ 1 LSB. No ROM-range constants (build check). |
| Resonator | Keys 21–108, T60 from 0.05 to 40 s. Measured f vs target ≤ 0.2 cent (plan check 1 allows ±2 cents for the whole voice). Measured T60 vs target ≤ 1 %. Impulse at v=127 does not overflow (max `|state|` < 2^30). Decays to exact 0 or below −120 dBFS within 3×T60 (no limit cycle). DF vs coupled form vs SVF compared on all four (P2-2 ADR). |
| Coefficient path (P2-3) | TUNE ±50 ct, DECAY ×0.25..×2, RELEASE: pitch ≤ 0.2 cent and T60 ≤ 1 % vs double at every corner. No division by a value that can be 0 (UBSan). |
| Pickup | Φ table monotone where expected, clamped index at ±full scale. A pure sine of amplitude A at default offset gives 2 peaks per period with alternate-peak ratio ≥ 1.5 (R01 check 5). Output with zero input is exactly 0 (no DC offset). |
| Hammer and velocity | Velocity table monotone; v=1 vs v=127 = R dB ±0.1 for each R in 12..36. Pulse area normalised per band. |
| Voice allocator | Steal order (oldest released first), 1 ms fade, retrigger clears state, pedal holds, refcount with SNAP (two keys, one note). |
| Tempo | `beat_samples` for every `bpm10` in 400..2400 vs exact rational, error ≤ 1 sample. `sync_samples` has no 32-bit overflow at 40 BPM × 4BAR. Tap: 4 taps at 500 ms give 120.0. Clock follower: synthetic F8 at 120 BPM with ±3 ms uniform jitter gives a displayed BPM within ±0.5 after 2 beats; a 500 ms gap falls back to INT. |
| Scales | Port FEL `tests/scale_test.c` `EXPECTED[]` and the mapping test (R08-15). SNAP rounds down; WHITE black keys are silent; TRN outside the snap. |
| Params and settings | Every param formats in ≤ 5 characters plus unit. Every past-version fixture loads with defaults for new params (append-only rule). CRC detects a single-bit flip. |
| Storage (Phase 7) | Port FEL `storage_test.c`: simulated NOR, torn write after every programmed byte and during every erase. Load picks the newest valid copy; erases stay inside `FL_RANGE_OK`. |

**Analysis checks (A)** in `tools/analyze.py`. Thresholds are ESTIMATES; they are tuned once in P2-8 and then frozen,
and loosening one needs a recorded reason.

| # | Metric (method) | Threshold | From phase |
|---|---|---|---|
| A1 | f0 per key 28–100, v=64, window 0.3–1.0 s (YIN + parabolic, or heterodyne phase slope) | \|cents\| ≤ 2. With vel→pitch on, ≤ 1 cent after 0.5 s. | 2 |
| A2 | Fundamental-partial T60, two-stage (EDT and late) at the 15 jRhodes roots × real layers | Late-T60 ratio model/reference in [0.8, 1.25] at ≥ 13/15 roots, and never outside [0.67, 1.5] | 2 |
| A3 | O1 and O2 T60 relative to V1 | O1 ≤ 0.25×, O2 ≤ 0.1× | 2 (O1), 3 (O2) |
| A4 | Note-off T60 and click residual | Within ±50 % of 0.5/0.4/0.3/0.2 s; click < −50 dBFS | 2 |
| A5 | Pickup two-spike test (unit) plus H2/H1 vs voicing: on-axis raises H2 (W6 in R05) | 2 spikes; H2/H1 increases monotonically as L_ver → 0 | 2 |
| A6 | Velocity: monotonic RMS; range v1→v127 in [20, 40] dB; bass layer spacing 2–5 dB; H2–H5/H1 rises ≥ 6 dB from v60 to v120, with the steepest slope in v 88–104 | All | 2 |
| A7 | Aliasing: keys C5–C7 at v=127, 0–0.5 s. Energy outside ±1 % of the expected harmonic and modal lines, 0–16 kHz, relative to total. Cross-check against a host-only 4× oversampled build of the same code (`-DRH_HOST_OS=4`). | ≤ −50 dB, and the difference from the 4× build in 0–16 kHz ≤ −45 dB | 2 (measure), 3 (fix) |
| A8 | H1 beating and whirl: envelope modulation depth of the fundamental band 0.5–3 s | 0.5–3 dB modulation present (R01 Fig. 4); not a gate before Phase 3 | 3 |
| A9 | Log-spectral distance of harmonics 1–12 over 0–2 s vs reference, after removing a fitted tilt (jRhodes EQ) | Trend only. Fails if it gets > 1 dB worse than the last accepted report. | 2 |
| A10 | UVI A4 force sweep: centroid vs force rank, Spearman ρ vs model centroid vs velocity | ρ ≥ 0.9 | 3 |
| A11 | FX: delay time exact to ±1 sample at every sync division and BPM; no zipper on a TIME change (residual < −50 dBFS); HALL RT60 1.25–5 s across the knob (R06); phaser notches sweep 150 Hz–4.8 kHz; tremolo rate equals the sync division ±0.5 % | All | 5–6 |

**T-LISTEN protocol (Phase 3 sign-off).**

- 15 roots × 2 velocities (layer centres v≈60 and v≈120). 3 s excerpts; model and reference RMS-matched to ±0.5 dB over
  the first 1 s.
- Present triples in random order: reference, model, and STK Rhodey as the low anchor.
- The listener rates realism from 1 to 5 and picks "which is the real one" for each model/reference pair.
- Play back on headphones **and** on the FM-1 speaker. For the speaker, the reference goes through the same master
  path.
- **Pass:** model mean ≥ 3.5, anchor mean ≤ 2, and correct-identification rate ≤ 75 % (ESTIMATE rule).
- Store the scores and the git SHA in `docs/model/reports/listening-<date>.md`. Never store the audio.

**Device checklist** (`docs/model/reports/device-<phase>.md`, every phase).

1. `./build.sh` passes (ROM, MMIO and size checks). Record the image, RAM and pool numbers.
2. `fm1_install.py --info` shows the expected identity and version before and after the install.
3. Cold boot, warm reboot and a 10-minute soak: no crash screen and no audible dropout.
4. All 27 keys, OCT±2 and REC hold and latch. CC64 from a DAW; MIDI notes at velocity 1/64/127; USB-audio capture
   running.
5. Profile build: the worst-case scenario (Critique 9) gives max ISR time ≤ the phase budget and mean ≤ 80 % of it,
   with USB audio on.
6. Capture comparison: `fm1_capture.py` plays `tests/scripts/conformance.mid` over USB-MIDI with MASTER at maximum.
   Record UAC, align each note onset by cross-correlation, and compare with the host render of the same script.
   Residual ≤ −60 dB per note. The host and device do the same integer maths, so a bigger residual means a
   compiler or ABI difference.
7. Phase-specific items: the UI on screen against the golden PNGs; LEDs; save-when-silent with no click; a power cut
   during save (Phase 7).
8. Recovery drill, at Phase 0 and every release: install the `CRASHTEST` build. Two boots go to auto-rescue; install a
   good build. Hold OCT- at power-on to enter rescue and install again.

### 5.2 Repo and tooling layout

```
Makefile                        targets below; sets JIELI_TOOLCHAIN / AC79_SDK to .local/ (U7)
.local/                         git-ignored: jieli/, fw-AC79_AIoT_SDK/ (sparse), venv/, reference/, sources/
.github/workflows/{ci.yml,nightly.yml,release.yml}
firmware/src/{frodhes.c,rhodes.c,dsp.c,fx.c,params.c,ui.c,tempo.c,scale.c,presets.c,storage.c}
firmware/gen/rhodes_tables.h    generated, committed; CI regenerates and diffs
tools/
  gen_rhodes.py                 tables (deterministic: sorted dicts, fixed float formatting)
  fit_reference.py              .local/reference -> docs/model/data/reference.json (+ provenance, attribution)
  analyze.py, dsp_analysis.py   metrics library (+ its own unit tests on synthetic signals)
  rhodes_ref.py                 double-precision model of the same voice: oracle for U and A
  fetch_reference.py, fetch_sources.py   pinned commits + sha256
  fm1_profile.py, fm1_capture.py         device ISR timings; UAC capture vs host render
  ab.py                         blind listening pack
  release.py                    the release bundle (§5.4)
  lint.py                       no float/double tokens in firmware/src (comments stripped); SPDX headers;
                                no tracked *.wav|*.flac|*.pdf|*.asm under docs/; file size cap 1 MB outside firmware/gen
tests/
  host.c (+ the verbs in §5.1), run_host.sh
  unit/  fuzz/  scripts/  fixtures/  golden/{audio.txt,screens.txt}
  cpu_baseline.txt  target_budget.txt  target_budget.py  analysis_thresholds.toml  check_render.py
docs/model/data/reference.json  docs/model/research/SOURCES.lock  docs/model/BUDGET.md  docs/model/reports/
```

### 5.3 Make targets and CI

**Make targets.**

| Target | Does |
|---|---|
| `make setup` | venv with pinned `requirements.txt` (Pillow, numpy, scipy, soundfile, mido, python-rtmidi); toolchain (sha256-checked) and SDK into `.local/` |
| `make host` | `tests/run_host.sh` |
| `make unit` | build and run `tests/unit` under ASan/UBSan |
| `make golden` / `make golden-update` | render every script; hash compare / rewrite |
| `make health` | `check_render.py` on every render |
| `make tables` / `make tables-check` | regenerate `rhodes_tables.h`; fail if the git diff is not empty |
| `make reference` / `make fit` | fetch jRhodes into `.local/`; write `reference.json` (local only; the result is committed) |
| `make analyze` | render the analysis scripts and check A1–A11 against `reference.json` |
| `make cost` / `make budget` | host instruction counts; static loop metric on `build/frodhes.dis` |
| `make fuzz` (`FUZZ_ITERS=`) | fuzzers |
| `make lint` | `tools/lint.py` |
| `make test` | lint, unit, tables-check, golden, health, cost, fuzz (short), analyze |
| `make fw` | `./build.sh`, then `make budget` |
| `make install` / `make info` | `fm1_install.py` |
| `make release V=0.N` | §5.4 |
| `make sources` | rebuild the local research cache from `SOURCES.lock` |
| `make upstream-check` | `git fetch upstream`, merge into a temporary worktree, `make test` |

**GitHub Actions** (all on `ubuntu-24.04`).

| Workflow | Job | Steps | Notes |
|---|---|---|---|
| `ci.yml` (push, PR) | `host` | `make setup-py` (venv only), then `make test` | About 2–4 min ESTIMATE. Uploads `build/analysis/`, the diff PNGs and the WAVs of failing renders as artifacts. Counts instructions with `perf stat -e instructions:u`. If perf is unavailable on hosted runners, it falls back to the static budget only and prints a warning. |
| | `firmware` | cache `.local/jieli`, keyed on `toolchain.lock`; download with the sha256 check; sparse-fetch the 3 SDK files from gitee (cache keyed on `SDK_SHA256`); `./build.sh` natively (Linux x86-64, no Docker; `build.py:62-64`); `make budget` | **Feasible.** The toolchain is Linux x86-64 native. Risks: availability of `pkgman.jieliapp.com` and gitee from GH runners, mitigated by the cache. The 3 SDK files are Apache-2.0, so a private mirror in a separate repo or a release asset is allowed if gitee is flaky. Upload `frodhes.fwsc`, `.dis` and the size numbers. |
| | `licence` | `make lint`; check that `git ls-files` has no audio, PDF or `.asm` under docs; every new `.c`/`.py` has an SPDX header | — |
| `nightly.yml` (cron) | `fuzz-long` | `make fuzz FUZZ_ITERS=1000000` | — |
| | `upstream` | `make upstream-check` | Opens an issue on failure. Covers Critique 6. |
| `release.yml` (tag `v0.*`) | `release` | the firmware job plus `make release V=${tag}`; **draft** GitHub release with the bundle | Publishing stays manual, after the device checklist and the hub check. |

**Branch policy.**

- `main` is protected and requires the `host`, `firmware` and `licence` jobs.
- One PR per task from §6.
- Commits end with the attribution lines the session asks for.
- Force-push only once, for H1.

### 5.4 Release process, package ID and versioning

1. **Identity.** `FM-1_950` is dev and never released. Releases 0.1 … 0.9 map to `FM-1_951` … `959`.
   `build.py --release` refuses anything else (H6) until the next identity block has been checked with the hub
   (Open question 4). Confirm on <https://thefirmwarehub.com> that 95x is free (P0-6) and again before each
   publication.
2. **Milestones (proposal).**

   | Version | Content |
   |---|---|
   | 0.1 | Phase 2: the PM voice, no UI beyond the current one |
   | 0.2 | Phase 3: realism |
   | 0.3 | Phase 4: panel |
   | 0.4 | Phase 5: effects |
   | 0.5 | Phase 6: tempo and scales |
   | 0.6 | Phase 7: presets |
   | 0.9 | Release candidate for the hub |

3. **Version string.** `APP_VER` = `X.Y[.Z]`. `.Z` is a hotfix that keeps the identity; the installer resumes on the
   same identity.
4. **`tools/release.py V`** builds `build/release-V/`, after Felucca's release folder (FEL `BUILDING.md` "Build"). It
   contains:
   - `frodhes-V.fwsc` and `frodhes-V-app.bin`;
   - `SHA256SUMS`;
   - `LICENSE`, `LICENSING.md`, `LICENSES/` (GPL-3.0, Apache-2.0 for the SDK files inside the package, OFL for
     Terminus);
   - `frodhes-V-source.tar.gz` from `git archive` (GPL §6 corresponding source);
   - `CHANGELOG` excerpt;
   - the analysis report, the device report, and the listening report for 0.2 and later.
5. **Gate.** The checklist is:
   - CI green on the tag;
   - device checklist §5.1 items 1–8, including the recovery drill;
   - `--info` shows the new identity and version;
   - the hub ID is free;
   - CHANGELOG and README "Status" updated.

   Then `git tag -s vV`, push, publish the draft release, and only then the hub.

### 5.5 Risk register

**Owners.**

- **MAINT** is Leonardo Mirabal: decisions, device and listening.
- **DEV** is the implementer, human or agent.
- **CI** means automated detection.

| ID | Risk | L / I | Owner | Trigger (early warning) | Mitigation | Contingency |
|---|---|---|---|---|---|---|
| K1 | Licence violation in the tree or history (sources, NC audio) | H/H (present now) | MAINT | `licence` CI job; any `git add` under `docs/model/research/sources` | H1 history rewrite; manifest plus local cache; lint | Make the repo private until it is cleaned |
| K2 | CPU budget exceeded (voices plus FX) | M/H | DEV | Profile max > 2.3 ms; static budget +10 %; host cost +25 % | Phase 0 measured allocation; drop order §6; per-sub-block coefficients | Voice shedding (P5-11); 6 voices; mono FX |
| K3 | Fixed-point resonator: pitch, decay or limit-cycle error | M/H | DEV | U resonator tests; A1/A2 failures | P2-2 topology ADR; Q2.30; round toward zero | Coupled form or SVF; end voices below −96 dB |
| K4 | Realism target unreachable: the reference is EQ'd and denoised | M/H | MAINT | A2/A6 drift; T-LISTEN mean < 3.5 | Per-partial metrics; second reference (UVI); tilt removal | Ask the user for their own recordings (plan Q3) |
| K5 | Aliasing in the top 2 octaves | M/M | DEV | A7 > −50 dB | Smooth Φ; 2× pickup oversampling | Key-scaled Φ softening above C6 |
| K6 | Pool or RAM overflow (delay + canvas + HALL) | M/M | DEV | `build.py` pool note > 300 KiB (ESTIMATE alarm) | P0-4 allocation; strip-drawing canvas | Delay at 22.05 kHz (R06); 1 s delay |
| K7 | Flash corruption or brick during preset saves | L/H | DEV | Storage torn-write tests; device power-cut test | A/B sectors, header last, read-back; `FL_RANGE_OK` narrow; save only when silent | Recovery drill; FM-1-transporter |
| K8 | Upstream merge conflicts | M/M | DEV | `upstream` nightly job red | Override hooks; upstream PRs; delta table | Rebase deltas by hand; pin to the last good upstream |
| K9 | Toolchain or SDK URL changes, disappears, or silently updates | M/M | CI | Firmware job download failure or sha mismatch | `toolchain.lock`; cache; mirror the 3 Apache-2.0 SDK files | Build locally from `.local/` |
| K10 | Package identity collides with another firmware | L/H | MAINT | Hub listing check at P0-6 and every release | H7 identity rules | Pick a new block; never reuse a published ID |
| K11 | Watchdog reset from long main-loop work (full redraw, bank write) | L/H | DEV | Host "max main-loop gap" probe > 50 ms; device soak | Draw on change; strips; `platform_poll` in long loops | Split work across frames |
| K12 | Saved presets break across updates | M/M | DEV | Fixture-load unit test fails | Append-only rule; versioned records (P4-1) | Migration code; reset to factory with a message |
| K13 | reface semantics wrong (pages were 403) | M/L | MAINT | User review of the PANEL at Phase 4 | Check against the reface owner's manual PDF | Remap in the PARAMS table only |
| K14 | Host and device behave differently (compiler 4.0.1 vs host, division by zero) | L/M | DEV | `fm1_capture` residual > −60 dB | UBSan on host; capture comparison per phase | Bisect with the profile build; fix the UB |
| K15 | Scope creep (editor, arp, chords) delays the core | M/M | MAINT | Phase 8 work before 0.6 | Discard list; milestones | Cut to the 0.6 scope |

### 5.6 Definition of done per phase

Every phase also requires:

- `make test` green in CI on `main`;
- `make fw` green;
- the device checklist signed in `docs/model/reports/`;
- README status line updated;
- a tagged pre-release where §5.4 maps one.

| Phase | Done when (all of) |
|---|---|
| H (hygiene, new) | No non-redistributable file in tree or history (`licence` job). `SOURCES.lock` plus fetch script rebuild the cache. `make setup` installs everything under `.local/`. CI runs on PRs and `main` is protected. Release identity rules in `build.py`. |
| 0: device baseline | Recovery drill done and recorded. Profile build reports ISR max and mean with USB audio on and off. `docs/model/BUDGET.md` holds **measured** cycles for: Q30 multiply-add, DF, coupled and SVF resonator per sample, LUT lerp, the HALL, phaser and delay ports, and the empty ISR overhead. The voice / FX / margin allocation is written down. Pool, RAM and flash baseline recorded. Preset sector pair and canvas size chosen. Button labels, key velocity capability and LED dimming confirmed. Hub ID checked. |
| 1: reference and tools | `reference.json` committed with provenance and attribution, regenerated identically from `.local/reference` (sha-pinned). `gen_rhodes.py` deterministic (`tables-check`). `dsp_analysis.py` passes its synthetic tests (known T60 within 1 %, known f within 0.1 cent). Host harness verbs, golden, health, unit and fuzz frameworks merged, with the current placeholder's goldens committed. |
| 2: voice core | U tests green, including the P2-2 ADR and the P2-3 coefficient path. A1, A2, A3 (O1), A4, A5, A6 and A7 (measured, threshold known) pass. Golden and health green. Device: 8 voices worst case ≤ allocated voice budget (≤ 1.9 ms max, ESTIMATE). Capture residual ≤ −60 dB. Release 0.1. |
| 3: realism | A3 (O2), A7, A8 and A10 pass. TYPE variants exist as param sets. T-LISTEN passes and is recorded. The budget still holds with H1 and O2 (or the drop order is applied and recorded). Release 0.2. |
| 4: params and panel | `PARAMS[]` with versioned settings record and fixture tests. Every screen has a golden hash, including each column focus, each switch position, each page and the MENU. Host UI tests cover tap, hold, flick, layer and the 250 ms quiet period. Maximum main-loop gap < 50 ms on host and device. Platform deltas are hooks, listed in README. Release 0.3. |
| 5: effects | Each effect has U or A11 checks and goldens. All FX on plus 8 voices ≤ 2.9 ms max with USB audio on, mean ≤ 2.3 ms. Overload shedding tested (host forced-overload scenario, device stress). Release 0.4. |
| 6: tempo and scales | Tempo, tap and follower U tests. Scale test port green. A11 sync timing passes. Device: follows a DAW clock at 60/120/200 BPM within ±0.5 BPM; beat LED in sync by eye; MIDI IN not quantised. Release 0.5. |
| 7: presets and MIDI | Storage torn-write tests green. Fixtures of every earlier settings version load. Factory presets have goldens. PC and CC map fuzzed. Device power cut during save leaves the old or the new bank, never a broken one. `FL_RANGE_OK` covers exactly the new sectors. Release 0.6. |
| 8 (optional) | Hub release 0.9 or 1.x under the identity rules. Any arp or editor has its own tests. |

---

## Task breakdown

Sizes are ESTIMATES: **S** ≤ ½ day, **M** 1–3 days, **L** 3–6 days. "AC" means acceptance criteria. Each task is one PR.

### H: hygiene (before anything else)

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| H1 | Remove non-redistributable sources from tree and history; `SOURCES.lock` + `tools/fetch_sources.py`; update research README and LICENSING.md | — | `git log --all -- docs/model/research/sources` is empty after filter-repo. Fetch script rebuilds the cache with matching sha256. MAINT approved the force-push. | M |
| H2 | `.gitignore`: `.local/`, `*.flac`, `build/`; `tools/lint.py` (float tokens, SPDX, forbidden tracked types, size cap) | H1 | `make lint` fails on a planted `.wav` under docs or a `double` in `firmware/src` | S |
| H3 | Makefile + `.local/` setup; `requirements.txt`; `toolchain.lock` with sha256 | — | A fresh clone builds the host and firmware with only `make setup`; nothing is written outside the project folder except Docker images | M |
| H4 | CI `ci.yml`: `host` + `licence` jobs; branch protection | H2, H3 | PRs show the required checks | S |
| H5 | CI `firmware` job with caches | H3, H4 | `frodhes.fwsc` artifact on every PR; cached run < 5 min (ESTIMATE) | M |
| H6 | Release identity rules in `build.py` and fixed help text; README | — | `--release 1.0` and `--release 0.0` are refused; 0.1 gives `FM-1_951` | S |
| H7 | `tools/release.py` + `release.yml` (draft) | H5, H6 | A dry-run tag produces the bundle with `SHA256SUMS`, licences and the source tarball | M |

### P0: device baseline

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P0-1 | Device bring-up and recovery drill (`-DFRODHES_CRASHTEST`) | H3 | Report: install, `--info`, two-crash rescue, OCT- rescue all verified | S |
| P0-2 | Profile build: `app_audio` timing (max, mean per second), screen overlay and SysEx/CC dump; `tools/fm1_profile.py` | P0-1 | Numbers readable on the host with USB audio on and off | M |
| P0-3 | Bench firmware: resonator variants, LUT lerp, 64-bit multiply, HALL/phaser/delay ports (with P2-1 helpers stubbed) | P0-2 | `docs/model/BUDGET.md` table of measured µs per 256 frames per item | M |
| P0-4 | Memory and flash allocation: pool, RAM and flash baseline; canvas decision; preset sectors (not 0xE0000–, not 0xFC000) | P0-1 | Written allocation; platform-delta list started | S |
| P0-5 | Hardware facts: SEL/SCL label, key velocity capability, LED dim, speaker quick check; `tools/fm1_capture.py` UAC capture | P0-1 | Facts recorded; one capture equals the host render of the placeholder | M |
| P0-6 | Hub ID check | — | Screenshot or date recorded | S |

### P1: reference, tools and verification infrastructure

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P1-1 | `fetch_reference.py` (jRhodes mono, pinned, sha256 manifest; UVI A4 on demand) | H3 | 65 files, 22.9 MB, all hashes verified, inside `.local/reference/` | S |
| P1-2 | `dsp_analysis.py` (heterodyne partials, two-stage T60 with noise floor, YIN, centroid) + synthetic tests | H3 | Synthetic tests: T60 within 1 %, f within 0.1 cent, at SNR 40 dB | M |
| P1-3 | `fit_reference.py` → `reference.json` with attribution; plots in `build/` | P1-1, P1-2 | Deterministic. Per root and layer: f0, partial ratios, T60s (EDT and late), levels, noise floor. | M |
| P1-4 | `rhodes_ref.py`: double-precision voice model (same equations as the plan) | P1-2 | Renders a note; it is the oracle the U tests import as golden values | M |
| P1-5 | `gen_rhodes.py` skeleton + `tables-check` | P1-3 | `rhodes_tables.h` byte-identical across runs and OSes | M |
| P1-6 | `analyze.py` + `analysis_thresholds.toml` (A1–A7 wired; thresholds marked provisional) | P1-2, P1-3 | Runs on the placeholder, fails as expected, report written | M |
| P1-7 | Host harness verbs (velocity, press/release/hold, turn, shot, expect, profile) | H3 | Scripts for REC hold and latch pass with `expect`; exit code non-zero on failure | M |
| P1-8 | Golden and health infrastructure; placeholder goldens committed | P1-7 | `make golden` green; a deliberate 1-LSB change fails it | M |
| P1-9 | Unit framework + sanitizers + fuzz harness (MIDI stream) | P1-7 | `make unit fuzz` green under ASan/UBSan with `integer-divide-by-zero` | S |
| P1-10 | Cost checks: host instruction counter + port of `target_budget.py` | H5, P1-8 | Baselines committed; +25 % / +10 % gates work | M |

### P2: voice core

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P2-1 | `dsp.c` helpers and tables (`mulq15`/`mulq30`, sat, lerp, sine, tanh/softclip, `osc_sine`), replacing Felucca's missing submodule | P1-9 | Unit tests per §5.1 | S |
| P2-2 | Resonator topology ADR: DF vs coupled vs SVF in Q2.30 | P2-1, P0-3, P1-4 | ADR records the measured pitch, T60, noise, limit-cycle and cost per variant; one is chosen | M |
| P2-3 | Runtime coefficient path (exp2 pitch, cos, T60 → r, TUNE/DECAY/RELEASE, damper crossfade) | P2-2 | ≤ 0.2 cent and ≤ 1 % T60 at every corner; no division by possible zero (UBSan) | M |
| P2-4 | Hammer pulse bands + velocity law tables (R selectable) + strike weights | P1-5 | Unit tests; table sizes within §6 | S |
| P2-5 | Pickup Φ family, TONE blend, antiderivative difference, clamping | P1-5, P2-1 | A5 and the pickup unit tests | M |
| P2-6 | RLC load biquad + per-key gain normalisation | P2-5 | Level across keys within ±3 dB at v=96 (ESTIMATE) | S |
| P2-7 | Integrate V1+V2+O1 into `rh_render`; voice allocation, steal fade, damper, pedal | P2-3…P2-6 | Interface unchanged; golden and health green; new goldens reviewed | L |
| P2-8 | Calibrate against the reference; freeze A1–A7 thresholds; report | P2-7, P1-6 | A1–A6 pass; A7 measured; report committed | M |
| P2-9 | Developer CC map for voicing params (tune by ear on device before the panel) | P2-7 | CCs documented; host `expect` tests | S |
| P2-10 | Device validation + capture conformance; release 0.1 | P2-8, H7 | DoD for Phase 2 | M |

### P3: realism layer

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P3-1 | O2 and H1 (+ whirl coupling) behind cost flags | P2-10 | A3 (O2), A8; device cost recorded | M |
| P3-2 | Register voicing: tip bands and gap per key; velocity-dependent pulse width | P2-10 | A2, A6 and A10 do not regress; A10 ρ ≥ 0.9 | M |
| P3-3 | Noises: damper thump, key-down thump, LFSR felt noise; half-pedal and pedal-up behaviour | P2-10 | Click detector green; levels as params | S |
| P3-4 | Bass velocity→pitch | P2-3 | A1 variant passes | S |
| P3-5 | Aliasing fix (2× pickup or Φ smoothing) if A7 fails | P2-8 | A7 ≤ −50 dB; budget holds | M |
| P3-6 | TYPE variants as param sets | P3-1, P3-2 | 4 goldens; distinct centroid and H2 metrics | M |
| P3-7 | `ab.py` + T-LISTEN session + report; release 0.2 | P3-1…P3-6 | Protocol pass recorded | M |

### P4: parameters and panel

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P4-1 | `PARAMS[]` (16-bit capable), formatting, versioned settings record, append-only rule (CLAUDE.md), fixtures | P2-9 | Fixture tests; every param formats in ≤ 5 characters | M |
| P4-2 | Platform hooks: `CV_MAX` override, `ramp(fg,bg)`; upstream PRs opened | P0-4 | Hooks used by the app; README delta table; Dinghy PR links | M |
| P4-3 | PANEL drawing, draw on change, golden screens | P4-1, P4-2 | Screen hashes for all column, switch and focus states | L |
| P4-4 | Control mapping: focus, flick, hold layer, 250 ms quiet, HOME/SAVE tap-hold, header messages | P4-3, P1-7 | Host UI scripts with `expect` | L |
| P4-5 | VOICING pages (+ KEY VEL, VEL range R, thump levels) | P4-4 | Goldens; KEY VEL reaches `rh_note_on` | M |
| P4-6 | MENU (MIDI CH, LEDS, HELP) | P4-4 | Goldens; MIDI channel filter tests | M |
| P4-7 | Device UI checklist; main-loop gap probe; release 0.3 | P4-5, P4-6 | DoD Phase 4 | S |

### P5: effects

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P5-1 | `fx_render` skeleton, sub-block framework, stereo split at TREM | P4-1 | Golden with FX bypassed equals the Phase 4 golden | S |
| P5-2 | DRIVE (`track_dist` port, per-TYPE table) | P5-1 | Unit (bypass keeps state, no click); golden | S |
| P5-3 | TREM: AM and autopan, sine↔square | P5-1 | Rate and depth exact; equal-power check | S |
| P5-4 | WAH: envelope follower + SVF k<1 table | P5-1 | Sweep range 300 Hz–2.5 kHz; stability across the table | M |
| P5-5 | PHASER port, free LFO | P5-1 | Notch sweep check (A11) | M |
| P5-6 | CHORUS: 2 taps, stereo | P5-1 | Delay range measured; no zipper | M |
| P5-7 | D.DLY with glide; fold to buffer | P5-1, P6-1 | A11 timing ±1 sample; no click on a TIME change | M |
| P5-8 | A.DLY: wow, flutter, LP, saturation, slew | P5-7 | Pitch-glide on a TIME change; bounded feedback | M |
| P5-9 | HALL port (CTL 32) + reverb tests ported from Felucca | P5-1 | RT60 range; no ringing; cost | M |
| P5-10 | `master_out` port; speaker check (LOWCUT option if needed) | P5-1 | Limiter never above −0.3 dBFS; DC test | S |
| P5-11 | Overload monitor + voice shedding | P5-9, P0-2 | Forced-overload host scenario sheds with a fade; device stress shows no watchdog reset | M |
| P5-12 | Device budget, all FX; release 0.4 | P5-2…P5-11 | DoD Phase 5 | S |

### P6: tempo and scales

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P6-1 | `tempo.c` core + sync table (with 4BAR) + unit tests | P4-1 | §5.1 tempo tests | S |
| P6-2 | Tap on PLAY; beat LED | P6-1 | `expect led` script | S |
| P6-3 | MIDI clock follow (µs stamps, beat average, fallback), transport FA/FB/FC | P6-1 | Jitter simulation test; device DAW test | M |
| P6-4 | Sync wiring to TREM, PHA, CHO and DLY | P6-1, P5-* | A11 | M |
| P6-5 | Scales: port masks, SNAP/WHITE, refcount, LEDs, `scale_test` port | P4-4 | Scale tests; goldens of the SCALE page | M |
| P6-6 | Clock out (flag, off by default); release 0.5 | P6-3 | 24 PPQN measured on the host ±0.1 % | S |

### P7: presets and MIDI

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| P7-1 | `FL_RANGE_OK` hook for the chosen sector pair (upstream-friendly) | P0-4, P4-2 | Erases outside the range refused (unit with a NOR simulation) | S |
| P7-2 | `storage.c` port + torn-write tests | P7-1 | Port of Felucca `storage_test` green | M |
| P7-3 | User bank, SAVE UX, auto names, QUIET TO SAVE, undo | P7-2, P4-4 | Host UI scripts; fixtures | L |
| P7-4 | Factory presets | P4-1, P5-12 | Goldens per preset | S |
| P7-5 | Program Change, CC map, pitch bend, MIDI fuzz extended | P4-1 | Fuzz green; CC table test | M |
| P7-6 | Device power-cut test; release 0.6 | P7-3 | DoD Phase 7 | S |

### X: continuous

| ID | Title | Deps | AC | Size |
|---|---|---|---|---|
| X1 | Nightly `upstream` merge check | H4 | Issue on failure | S |
| X2 | Nightly long fuzz | P1-9 | Green or issue | S |
| X3 | Hub release (0.9 or 1.x) after the identity block decision | P7-6, OQ4 | §5.4 gate | S |

**Critical path (ESTIMATE):** H1 → H3 → P1-7 → P1-8 → P2-1 → P2-2 → P2-3 → P2-7 → P2-8 → P2-10. That is about
4–6 weeks of focused work to release 0.1, assuming device access and no toolchain surprises.

---

## Open questions

1. **Remote.** Is `origin` = `github.com/leonardoMirabal/fhodes` public, and is "fhodes" intended, or a typo for
   "frodhes"? This decides how urgent H1 is and the repo name in release links.
2. **History rewrite.** May we rewrite the published history (H1) to remove the third-party sources? The alternative
   is a fresh repository.
3. **Project-local tooling.** Is `.local/` inside the project acceptable for the toolchain, SDK, venv and reference
   audio (U7)? Docker images necessarily live in Docker's own store.
4. **Identity beyond 0.9.** `FM-1_95x` allows 9 releases. Should 1.0 use a second block (for example 96x, after a hub
   check), or should Frodhes stay in 0.x?
5. **On-board velocity.** Are the FM-1's keys velocity-sensitive in hardware? If not, where should KEY VEL live:
   VOICING page, SELECT-hold, or MENU?
6. **Upstream.** Should we offer the platform hooks (`CV_MAX`, `FL_RANGE_OK`, SysEx) to zvenson/dinghy, or keep them
   as local deltas?
7. **Listening tests.** Who listens, on what playback, and is the T-LISTEN pass rule acceptable?
8. **Commercial intent.** Is there any intent to sell builds? It does not affect derived numbers, but it rules out
   ever shipping jRhodes-derived audio, and it matters for the jRhodes author's licence offer.
9. **Delay knobs.** DEPTH as feedback plus level (R06), or separate DEPTH and FDBK (R07)? Plus the WAH question already
   in PLAN §9.
10. **Pre-releases.** Are GitHub pre-releases 0.1–0.6 wanted, or should the GitHub release wait for the hub-ready
    build?
