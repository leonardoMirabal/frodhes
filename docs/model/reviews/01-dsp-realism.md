# Review 01: DSP and physics realism of the voice (PLAN §1 D1–D7, §2, §3)

Reviewer lens: will the voice model sound like a real Rhodes, and is it numerically sound on an integer-only
AC791N? References like (R03 §Parameters) point to `docs/model/research/03-…md`, section *Parameters*.

**About the numbers marked COMPUTED.** I checked several claims with small numpy scripts. They emulate the
fixed-point arithmetic exactly (int64 products, `>>` as floor), but their physical inputs are ESTIMATEs: tip
amplitudes of 0.05–1.5 gap, and the R03 code geometry L_hor = 1, R_p = 1, L_ver = −0.2 in gap units. The
scripts live in the session scratchpad, not the repo. The conclusions are about orders of magnitude, not
exact values. Everything else that is my judgement is marked ESTIMATE.

---

## Verdict

1. The architecture is right and can sound like a Rhodes: a modal tine, a per-voice nonlinear flux pickup, per-key tables from the host, and decays fitted to a reference. No source offers a better fit for this CPU.
2. As specified, it will not sound right yet. The Q15 pickup path is a blocker (COMPUTED: a soft E1 comes out at 4 dB SNR). The resonator structure is unspecified. H1 is routed through the pickup as if it moved vertically. The tonebar detuning makes the pitch drift.
3. The biggest realism gaps are in the physical inputs, not the DSP. The hammer width ignores velocity. The 7.1/20 ratios are used for every key. A re-strike clears the ringing tine. Mode decays are read straight off the output partials of a reference that was EQ'd and denoised.
4. Every fix is cheap at run time, about 23 multiplies per voice-sample (ESTIMATE), because the physics moves into the host generator: an FE model of tine plus spring, a 2-DOF tine–bar model, a coupled hammer-contact simulation and a radial pickup field.
5. Build a float golden model and fit it by analysis-by-synthesis before writing any C. Then check the C engine against the golden model, and the golden model against jRhodes, as separate gates.

---

## Critique

### 1. BLOCKER: the pickup path quantises the tip to Q15 and differences a Q15 Φ table

**Where:** §2 says "x (tip displacement, Q15, clamped to the table window)" and "Φ_voicing(x) LUT → y = Φ(x[n]) − Φ(x[n−1])". §6 says "1024 × Q15".

**Why it fails:** the output is a *difference* of two nearly equal numbers. In the bass, x moves by only about
2π·41/44100 ≈ 0.6 % of its amplitude per sample. Two problems follow:
- Quantising x to Q15, with only 5 fractional bits left for the 1024-entry lerp, turns into broadband noise
  after the difference.
- A linearly interpolated Φ has a piecewise-constant slope. The pickup gain G = Φ′ then becomes a staircase,
  and every cell crossing makes a small step.

COMPUTED SNR against an exact Φ difference, 1 s decaying sine:

| Scheme | E1 hard | E1 soft (−30 dB) | A4 hard | A4 soft | C7 hard | C7 soft |
|---|---|---|---|---|---|---|
| Plan: Q15 x, Q15 Φ, lerp | 36 dB | **4 dB** | 57 dB | 24 dB | 70 dB | 37 dB |
| int32 x, int32 Φ, lerp | 56 dB | 39 dB | 78 dB | 41 dB | 89 dB | 52 dB |
| int32 x, **G table at the midpoint × exact Δx** | 91 dB | 84 dB | 65 dB* | 84 dB | 38 dB* | 84 dB |
| int32 x, int32 Φ + Q15 G, **cubic Hermite** | 106 dB | 93 dB | 126 dB | 93 dB | 139 dB | 99 dB |

\* These are not noise. The midpoint form approximates the true derivative, while the Φ difference is the
derivative averaged over one sample (a box filter). The two differ by the box's sinc droop at high harmonics.
COMPUTED aliasing is the same for both forms (see #12).

**Fix:**
- Keep the tip displacement in int32. Spec: Q24 in units of the pickup gap, never Q15.
- Compute the output in the derivative form with an exact difference: y = D(s̄)·Δs. D is a Q15 table, s̄ is
  the midpoint, and Δs is computed exactly as Δu·Σu, a product of two int32 values. See #3 for the radial
  form and the spec for formats.
- If a later A/B prefers the exact box-averaged form, use the Hermite variant instead: an int32 Φ table plus a
  Q15 G table, 3–4 multiplies.
- Never clamp x hard inside the field. Make the table wide enough that D(end) < 3·10⁻³·max (COMPUTED: 32 gap²
  gives 2.6·10⁻³), so leaving the window is physically "far from the magnet", which is flat.

### 2. MAJOR: the resonator structure is unspecified, and direct form is noisy at 41 Hz

**Where:** D1 "2-pole resonators" and D5 "Q30 coefficients and int32 state".

**The problem:** the sources disagree. R01 §FM-1 notes 3 recommends a coupled form, R05 §Discretization a
direct form, R06 §Reusability a 2-multiply resonator. The structure matters at low θ: direct-form round-off is
amplified by roughly 1/((1−r)·sin²θ).

COMPUTED for f = 41.2 Hz, T60 = 20 s, int32 state with a full-scale amplitude of 2²⁸, Q30 coefficients:

| Structure | mul/sample | Pitch error | T60 error | SNR at 0 dB | SNR at −60 dB |
|---|---|---|---|---|---|
| Direct form `y = a1·y1 + a2·y2` | 2 | −0.013 c / −0.11 c | < 0.1 % | 59 dB | **18 dB** |
| Damped magic circle "MC3" (below) | 3 | +0.0004 c | < 0.1 % | 110 dB | 55 dB |
| Undamped magic circle + decay every 16 frames | 2 + 2/16 | +0.0004 c | < 0.1 % | 89 dB | 64 dB |

At 2637 Hz all three are fine (≥ 50 dB). The direct form makes a soft bass tail grainy, because the
amplitude and phase wobble near resonance. That is exactly the "synthy" giveaway on held bass notes.

**Fix:** use **MC3**, an exact-pole coupled form with damping on one state:
```
x += E·y            (E = sqrt(1 + r² − 2r·cosθ), Q30)
y  = D·y − E·x      (D = r², stored as eps = 1 − r², Q30)
```
- Its poles are exactly r·e^(±jθ): det = D, trace = 1 + D − E².
- x is the displacement. The force is injected into y.
- Round eps **up** when quantising, so the quantised mode is never less damped than the exact one (R06
  §Physical-modelling: Felucca's "damping-safe" rounding).
- Changing D alone for the damper moves the pitch by less than 1 cent (ESTIMATE from the trace formula), so E
  can stay fixed through the release crossfade.
- To answer the question directly: yes, the coupled form is better here. It is more accurate in frequency and
  has 30–50 dB less noise in the bass, for one extra multiply.

### 3. MAJOR: H1 (the 2nd polarisation) is summed into the vertical tip displacement

**Where:** §2 lists H1 inside the modal box with "x = Σ w_m · y_m".

**Why it is wrong:** the horizontal polarisation is orthogonal to the vertical one. Adding it to x makes H1 just
a third detuned vertical mode, which gives more beating but no whirl. Physically:
- Both transverse motions move the tip parallel to the pole face.
- R03's flux is even in u = q + L_ver (I checked eq. 25: g(−u) = −g(u)), so Φ depends only on the distance from
  the coil axis.
- The 2-D generalisation is therefore Φ̃(ρ²) with ρ² = (x_v + L_ver)² + x_h².
- This is the cheap version of R01's precomputed 2-D field (R01 Eq. 7, §FM-1 notes 2). H then modulates the
  waveform quadratically and slowly as the orbit precesses, which is the measured "parallelogram/whirl" (R01
  §Measured, Fig. 4).

**Fix:** index the pickup table by s = (x_v + off)² + x_h², Q24 gap². This is smooth because Φ is even, so it
is analytic in s. COMPUTED: with a 2048-entry table over 32 gap², the s-form SNR is 74–93 dB.
With x_h = 0 it reduces exactly to the 1-D model. Cost: 2 more multiplies.

### 4. MAJOR: the tonebar is two symmetrically detuned modes f0·(1 ± δ_tb) with independent decays

**Problems:**
- **Pitch drift.** The perceived pitch slides from the tine-dominant mode to the bar-dominant one as the faster
  mode dies. With δ = 0.5 %, that is 8.6 cents, which breaks check 1 (±2 c) in some analysis windows.
- **Arbitrary weights.** The hammer strikes only the tine, and the pickup reads only the tine. Each normal mode's
  weight is its tine participation squared, and the two are not independent knobs. Arbitrary weights give
  either chorus-like beating on every note (synthy) or no double decay at all.
- **Damper physics lost.** The felt touches the tine, not the bar. A physical release damps the tine-dominant
  mode hard and lets the bar-dominant mode ring out briefly. The plan's per-mode crossfade to one release T60
  cannot produce that.

**Fix:** on the host, solve a 2-DOF damped tine–bar system:
- M = diag(1, μ), K = [[ω_t² + k, −k], [−k, μω_b² + k]], C = diag(2σ_t, 2μσ_b), with the force on the tine
  only.
- Take the two complex eigenvalues and their tine-to-tine residues. These give each normal mode's f, σ and
  input weight b_m, all mutually consistent.
- Retune so that the mode that dominates the late sustain is exactly f0.
- For the release, add σ_damper to the tine coordinate only and re-solve for eps_rel per mode.
- Fit the parameters (δ_b, k, μ, σ_t, σ_b) per jRhodes root to the measured two-stage decay and the envelope
  ripple (see #8).
- Default the ripple depth below 1 dB unless the reference shows more (ESTIMATE).

### 5. MAJOR: the hammer pulse ignores velocity-dependent contact time and the hammer–tine interaction

**Where:** D6 makes the width depend on the tip band only and "A(vel)" scale the amplitude.

**Velocity:** felt and neoprene are stiffening contacts (power law, exponent 2.5–2.8; R03 §Parameters, R02
§Parameters). For a Hertzian contact, T_c ∝ v^(−(α−1)/(α+1)), which is v^−0.43 for α = 2.5. Over the plan's
30 dB velocity range, contact time changes about 4× (ESTIMATE from that law). This is the second "brighter when
harder" mechanism, after the pickup. R01 §FM-1 notes 4a ("width and peak depending on v0") and R06
§Reusability ("width shrinks with velocity") both say so, and the plan dropped it.

**Interaction:** R03 §FM-1 notes 2b argues the re-interaction is small because "the tine is light compared with
the 30 g hammer". COMPUTED with an FE model of a 0.075" tine plus spring mass (#6): the fundamental's
**effective mass at the strike point is about 38–43 g at E1 and about 21 g at A4**. That is comparable to, or
heavier than, the hammer (11 g in R02 Tab. 3.2, 30 g in R03 T2). The upper modes are light, about 0.4–5 g.
So the contact is strongly coupled for the fundamental: the hammer recoils and the force shape is not a
half-sine.

**Fix:** keep the run-time "pulse into the resonators", but generate the pulses on the host with a coupled
simulation. R03's felt law F = K·e^p + R·d(e^p)/dt drives the first 6 FE tine modes and the bar, at 16× fs,
with the tine at rest. That is **exact** for a tine at rest and captures any hammer re-contact automatically.
Store:
- normalised shapes per (tip band × 4 velocity layers);
- a velocity → (amplitude, rate) table per band.

Map MIDI velocity to hammer **speed** with the dB-linear law, not to force amplitude. The only remaining
approximation is a re-strike on a moving tine (see #7).

### 6. MAJOR: the measured ratios 7.1/20 are applied to every key

**Where:** D3 cites 7.11/20.25, measured by SLDV on **F1 only** (R02 §Measured, citing Gabrielli). EP-MK1 uses a
global 7/20 (R05).

**What the physics says** (COMPUTED): a uniform 0.075" steel tine (R05 W11) cut to the service-manual length
(R02 `TineLengths.h`), plus a point mass for the tuning spring that is solved to hit f0, reproduces F1:

| Key | L (mm) | Bare cantilever f1 | Spring at 0.65 L: mass, f2/f1, f3/f1 | Spring at 0.95 L: mass, f2/f1, f3/f1 |
|---|---|---|---|---|
| E1 | 156.4 | 55.0 Hz | 2.45 g, 7.15, 19.5 | 0.78 g, 7.35, 21.9 |
| F1 | 152.4 | 57.9 Hz | 2.32 g, **7.12, 19.4** | 0.74 g, 7.32, 21.8 |
| E3 | 79.8 | 211 Hz | 1.03 g, 6.97, 19.0 | 0.33 g, 7.15, 21.2 |
| A4 | 50.8 | 521 Hz | 0.41 g, 6.67, 18.1 | 0.13 g, 6.80, 19.8 |
| C7 | 24.6 | 2223 Hz | 0.06 g, 6.37, 17.4 | 0.02 g, 6.42, 18.2 |
| E7 | 22.6 | 2634 Hz | (no spring needed: 6.27, 17.5) | |

This supports D3's premise that the spring is what raises 6.27 to 7.1. It also shows the ratio **falls to about
6.4–6.8 in the treble**, about a semitone lower than 7.1, so the bell partial is pitched differently per
register.

**Fix:**
- `gen_rhodes.py` builds this FE tine-plus-spring model per key (about 40 Hermite beam elements: a 2·40 × 2·40
  generalised eigenproblem on the host).
- Fit the spring position on the host to the F1 ratios and to the 7× partials measured on the 15 jRhodes roots.
- Take from the model the per-key ratios, the mode shapes at the strike line (R05 W1: 2¼" in the bass to ⅛" in
  the treble) and at the tip. Those shapes give the strike weights.
- `fit_reference.py` must separate the inharmonic 7.1× tine partial from the pickup's 7th harmonic. They differ
  by only 0.1·f0, about 4 Hz at F1.

### 7. MAJOR: a re-strike clears the ringing tine

**Where:** §2 Voice management, copied from EP-MK1: "1 ms fade before a retrigger, clearing the state only at
retrigger".

**Why it is wrong:** on a real Rhodes, re-striking a ringing note hits a moving tine. The new sound is the
superposition of the old motion and the new strike, and its result depends on the phase. Fading and clearing
make a 1–2 ms gap or click, and every repeated note sounds like a sample retrigger.

**Fix:** if the voice is still sounding the **same note**, keep the state and add the new pulse. Fade and clear
only when stealing a voice for a *different* note. Optional Phase 3 refinement (ESTIMATE): scale the pulse by
(1 + k·sign) depending on whether the tine's V1 velocity state moves toward the hammer at strike time.

### 8. MAJOR: fitting mode parameters directly from the output partials of the reference is wrong

**Where:** §3 `fit_reference.py` and check 2.

**What goes wrong:**
- **The pickup distorts decays.** With a nonlinear pickup, harmonic k grows roughly as A^k, so it decays about
  k times faster in dB than the mode. The fundamental is compressed while the tine is in the strong-field
  region. So an output envelope is not a mode envelope, and part of the "double decay" is the pickup itself.
- **The reference is EQ'd.** jRhodes was recorded with a treble boost and a low-mid scoop (R05 §Summary), so
  absolute spectral balance is not a target.
- **The reference is denoised.** CoolEdit-era noise reduction steepens the last 20–30 dB of each tail.
- **The quoted numbers are lengths.** Check 2 says "about 18 s at ≤ D4, then falling to about 1.5 s at C7".
  Those are sample *lengths* (R05 §Parameters says so), not T60s.

**Fix:**
- Fit by analysis-by-synthesis. A float golden model (#9) renders each root and layer, the same analyser
  measures both renders and reference, and a least-squares search adjusts the physical parameters.
- Use only measures that EQ does not change: decay slopes; ripple rate and depth; frequency ratios; and
  harmonic ratios *relative to the softest layer of the same root*.
- Fit decay slopes on the segment from −5 to −35 dB under the peak, above the denoise region.

### 9. MAJOR (process): there is no float golden model, so model errors and fixed-point errors are mixed up

**Where:** Phase 2's "done when checks 1–6 pass" tests the reference fit and the fixed-point implementation in
one step. When a check fails, you cannot tell which one is wrong.

**Fix:** write `tools/rhodes_model.py`, a float64 model with exactly the same structure as the C engine.
- Gate A (Phase 1): golden model vs jRhodes.
- Gate B (Phase 2): C render vs golden render with the same tables. Residual energy must be ≤ −60 dB per case,
  and ≤ −70 dB for the pickup unit test.

### 10. MINOR: the TONE knob blends two Φ tables, and "8 voicings × 1024" is not needed

**Why:** in R03 eq. 25, L_ver enters only as q + L_ver, so the vertical offset is a pure translation of one
radial field.
- Blending two tables is a crossfade between two pickups, not a change of geometry.
- Table shape depends only on R_p/L_hor. With R05 W3's gaps of 1/16" (bass and wood-tip treble) and 1/32"
  (mid), that is **2 tables**.

**Fix:**
- TONE and the per-key voicing become an offset `off` added to x_v before squaring.
- Apply the current offset to both x[n] and x[n−1] (store x_prev without the offset). Moving TONE while a note
  sounds then never clicks.
- This saves about 12 KiB of flash and the blend's multiplies.

### 11. MINOR: the RLC load runs per voice

**Problems:**
- The coil, capacitance and load form a linear, time-invariant filter that is identical for all keys. Running it
  per voice costs 8 biquads where 1 does the job.
- Its parameters are unsettled. R03's paper has an RC load with a 482 Hz corner; its code has an RLC tuned to
  5 kHz (R03 §Parameters). These give very different brightness.
- jRhodes's recording EQ is unknown, so the RLC cannot be fitted from it.

**Fix:** use one post-mix biquad per TYPE. Default to f = 5 kHz with Q ≈ 1 (ESTIMATE), then adjust by ear.
Keep it out of the automated spectral checks.

### 12. MINOR: plan 2× pickup oversampling only if a measurement shows it is needed

COMPUTED aliasing (energy away from the harmonics, relative to the signal) for the Φ-difference form with
the strongly nonlinear R03 code geometry:

| Key | 0.5 gap | 1.0 gap | 1.5 gap |
|---|---|---|---|
| C5 | −188 dB | −200 dB | −204 dB |
| C6 | −194 dB | −170 dB | −113 dB |
| C7 | −132 dB | −70 dB | −54 dB |
| E7 | −95 dB | −62 dB | −39 dB |

The midpoint form aliases the same as the sampled true derivative: −49 dB at C7 at 1.5 gap. Treble tines
have small amplitudes, and jRhodes's treble layers have little bark, so 2× oversampling is very likely
unnecessary.

**Fix:**
- Replace "oversample if audible" with an automated alias check (check C8 in the spec).
- Do **not** "keep Φ smooth" by hand (§2, §8). That trades away the physics for a problem that probably does
  not exist.

### 13. MINOR: the attack lacks the upper tine modes

**Where:** the plan keeps V1, V2, O1 and O2 only.

**Why it matters:** in the bass, cantilever modes 4 to about 12 sit below Nyquist (R03 §Per-key: 12 modes at
A1). COMPUTED: mode 4 at E1 is about 1.7 kHz. They die in milliseconds (R03 code damping law: T60 of 24 ms and
6 ms for modes 3 and 4 at A4), but they give the bass attack its "clank". The EP-MK1 "hammer into pickup"
hack (R05) exists to fake exactly this.

**Fix (Phase 3):** precompute, per tip band and velocity layer, the **residual tip displacement** of modes ≥ 4
(and optionally O2) for the first 20–50 ms. Add it to x_v before the pickup. It is linear superposition, so it
is exact for a tine at rest and correct under re-strike. It costs one table read per sample during the attack.

### 14. MINOR: velocity → pitch is modelled as a separate decaying offset

R05 says "low notes go a bit sharp when struck harder". The mechanism is large-deflection stiffening, R01's
Kirchhoff term (Eq. 2–3).

**Fix:** make E of V1 (and V2) depend on amplitude, once per block:
- E = E0·(1 + κ·a²), with a² ≈ x² + y² of the mode. In MC3, y is close to the quadrature at the same amplitude.
- The pitch then glides with the decay by itself, and re-strikes behave correctly.
- Fit κ from jRhodes: compare the instantaneous f0 at 50–200 ms with that at 2–3 s, for the loudest and softest
  layers of F1, B1 and E2.

### 15. MINOR: the damper details

**Problems:**
- The thump goes "into V2". It belongs on the tine coordinate, which drives both normal modes through their
  residues (#4).
- The release targets of 0.5/0.4/0.3/0.2 s are jRhodes `ampeg_release` sfz settings chosen by the sample
  author, not measurements (R05 §Parameters). The jRhodes set has no release samples.

**Fix:**
- Keep check 4, but as a soft target (±25 %) with a user RELEASE scale.
- Model the felt as added tine damping in the 2-DOF solve.

### 16. MINOR: the TYPE variants rest on shaky history and one contradiction

- **MK I vs MK II.** Felt gave way to neoprene in 1970–71, and the 1979 Mk II was "simply a set of cosmetic
  changes" (R05 W2, W9). "MK I felt-ish tips vs MK II neoprene" does not describe two real instruments.
- **BRIGHT.** "On-axis voicing … more bark" contradicts the sources. On-axis gives octave and even-harmonic
  "pure overtone" with less fundamental (R03 S2, R05 W6). Bark comes from an asymmetric offset combined with a
  close gap.

**Fix:** define each TYPE as physically named deltas from the jRhodes-fitted '77 Stage:
- tip band shift (harder or softer);
- gap scale (the x-scale per key);
- offset;
- RLC;
- preamp EQ.

Suggested set: **STAGE '77** (reference), **SUITCASE** (same harp, Peterson EQ, stereo vibrato), **EARLY
FELT** (softer tips, longer contact, less bell), **BELL/DYNO** (smaller offset, closer gap, bright EQ).

### 17. MINOR: check 5 is ill-defined

For a pure sinusoid, the pickup output G(x)·ẋ is **antisymmetric about each extremum of x**: max = −min
exactly (COMPUTED: ratio 0.9998). So "asymmetric spikes" must mean something else: the **two positive spikes in
one period have different heights**. COMPUTED with the code geometry: 0.78 at L_ver = −0.2 gap, 0.5 at
−0.5 gap, and 1.00 at 0. At offset 0 the output also has no fundamental (H1 is −330 dB below H2), which is
R03's "pure overtone". See the spec for the exact test.

### 18. MINOR: a smooth T60 curve would erase the tone-bar clip region

The jRhodes lengths are non-monotonic: E5 5.2 s, then A5 8.4 s and D6 7.2 s (R05 §Parameters). A5 is key 54 of
73, exactly where Vintage Vibe says tone-bar clips start on "about notes 54–73" (R05 W10).

**Fix:** fit per root and interpolate log-T60 per key, allowing a step between keys 53 and 54. Do not fit one
smooth curve.

### 19. MINOR: the worst-case block is a full chord struck at once

During contact (1–3 ms), each voice adds about 6 multiplies per sample for the pulse lerp and the injection
into the modes. Eight voices struck in the same block is the worst case for the ISR. Phase 0/2 timing must
measure that block, not the average.

### 20. MINOR: 73 perfectly regular keys will sound "too clean"

Real instruments vary key to key in voicing, bar detune and decay. The plan's tables are interpolated smoothly
between 15 roots.

**Fix (ESTIMATE):** add a seeded per-key jitter in `gen_rhodes.py`, with sizes taken from the fit residuals
between neighbouring roots. Starting values: bar detune ±1.5 c, level ±0.7 dB, T60 ±8 %, offset ±0.05 gap.

### 21. MINOR: check 6 does not say where "30 dB" is measured

The excitation range is 30 dB, but the pickup compresses loud notes, so the output range will be smaller.
jRhodes's level-versus-velocity is an sfz design choice (`amp_veltrack`, R05 Eq. 6), not a measurement.

**Fix:** state the target on output RMS, 24–32 dB (ESTIMATE), adjustable through the user's velocity range R.

---

## Ideas ledger

| Idea | Source | Status | Reason |
|---|---|---|---|
| Modal tine instead of FD/PHS | R01 §FM-1 notes 1–3; R02 §FM-1 1–2; R03 §FM-1 1–2 | IN PLAN | Every source agrees; FD costs 0.5–5 Gops/s. |
| Coupled-form / magic-circle resonator | R01 §FM-1 notes 3 | MISSING | Choose MC3 (#2): 30–50 dB less noise at 41 Hz. |
| Exact pole placement, not trapezoidal | R03 §Discretization (7–25 % warping) | IN PLAN | Keep. MC3 places poles exactly. |
| Q30 coefficients, int32 state, 64-bit multiply | R01 §FM-1 5; R03 §FM-1 4; R05 §Discretization | IN PLAN | Keep, but the tip must not drop to Q15 (#1). |
| Damping-safe coefficient rounding | R06 §Physical-modelling (phys_dsp.c:39-41) | MISSING | Round eps up, so the mode is never less damped and never unstable. |
| Nonlinear flux pickup Φ(x), output dΦ/dt | R01 Eq. 4–8; R03 eq. 24–27 | IN PLAN | The core of the sound. |
| Antiderivative / difference form | R03 §FM-1 2; R01 §FM-1 2 | IN PLAN (fix precision) | Use the midpoint D·Δs or the Hermite Φ form with an int32 tip (#1). |
| R03 code geometry family (non-monotonic g) | R03 §Parameters | IN PLAN | Right family. Use the R05 W3 gaps, not the code's per-key `lp = 1/F0 mm` law, which is ad hoc. |
| Radial 2-D pickup field (V and H) | R01 Eq. 7, §FM-1 2 ("2-D for H/V") | MISSING | Φ̃(ρ²) with s = u_v² + x_h² (#3). |
| Pfeifle 3-segment tapered pole profile | R01 Eq. 7 | REJECT | It has no numbers and a probable typo. Use R03's sphere/coil family and add a shape parameter only if the fit fails. |
| Circular-arc tip trajectory | R01 Eq. 8 | REJECT | COMPUTED: the gap changes by x²/2L, which is 0.6–0.8 % of the gap at 2 mm (bass) or 0.5 mm (treble). Negligible. |
| Pickup back-force (Maxwell) | R03 §Model (neglected); R01 §5.4 | REJECT | Its static part goes into the fitted tuning and offset. Its dynamic softening is opposed by the measured "goes sharp" stiffening, and one fitted amplitude–pitch law (#14) covers both. |
| Coil and RC/RLC load | R03 §Parameters | IN PLAN (move) | Use one post-mix biquad (#11). |
| Measured partial ratios 7.11/20.25 | R02 §Measured | IN PLAN (fix) | They hold for F1 only. Make the ratios per key (#6). |
| Tuning spring as added mass (non-uniform beam) | R01 §5.3; R02 §3.2.1 | MISSING | Host FE tine plus spring reproduces 7.1/19.4–21.8 at F1 and predicts per-key ratios (#6). |
| Manual tine cutting lengths (73 keys) | R02 §Per-key (`TineLengths.h`) | MISSING | Real per-key geometry, the input to the FE model. Use it as data; the repo has no licence, so do not copy code. |
| Tonebar as a coupled resonator / two normal modes | R03 §Model (A2 "most critical"); R05 EP-MK1 | IN PLAN (fix) | Derive the modes from a 2-DOF solve; put the long mode at f0 (#4). |
| Double decay | R03 §Measured; PLAN D4 | IN PLAN | It comes from the 2-DOF model and the pickup. Fit by analysis-by-synthesis (#8). |
| Tine whirl / H polarisation, energy exchange | R01 §Measured (Fig. 4, 11), §FM-1 3 | IN PLAN (fix) | Use one-way V1→H1 resonant forcing plus a small hammer share into H, read through the radial pickup (#3). |
| Kirchhoff amplitude coupling ∝ (u_H² + u_V²) | R01 Eq. 3, §FM-1 3 | MISSING (cheap form) | Per block: make the V1→H1 coupling and E(V1) depend on mode energy (#14). |
| Velocity → pitch in the bass | R05 §Summary, §Measured 4 | IN PLAN (change) | Use an amplitude-dependent E, fitted from jRhodes layers (#14). |
| Hunt–Crossley contact at run time | R01 Eq. 1; R02 Eq. 3.20; R03 eq. 9–15 | REJECT at run time / MISSING on host | Too stiff for fixed point at run time (R02 §FM-1 3), but exact offline. Run it as a host simulation that generates the pulses (#5). |
| Precomputed hammer force pulse | R01 §FM-1 4a; R02 §FM-1 3A; R03 §FM-1 2b | IN PLAN | Keep the run-time form; generate the shapes by coupled simulation. |
| Contact width shrinks with velocity | R01 §FM-1 4a; R06 §Reusability | MISSING | About 4× over 30 dB (ESTIMATE, Hertz law) (#5). |
| Hammer–tine interaction / re-contact | R03 §FM-1 2b (claimed small) | MISSING (host) | COMPUTED effective mass at the strike point is 21–43 g, which is not small. The host simulation captures it, including re-contact. |
| Strike position per key (mode weights φ_n(x_c)) | R02 §Measured Fig. 5.3, §FM-1 3; R05 W1 strike line | PARTLY (weights, no source) | Use the strike line from W1 and mode shapes from the FE model. R02 calls contact location the dominant timbre control. |
| Hammer tip hardness bands | R05 W1/W2 | IN PLAN | The MIDI mapping is ESTIMATE; see open questions. |
| Mode decays rising steeply with mode index | R03 §Parameters (code κ⁴ law) | MISSING (as default prior) | Use as the default for O2 and the residual modes where the reference cannot resolve them. |
| Upper modes / attack transient | R03 §Per-key (12 modes at A1) | MISSING | Residual-displacement table (#13). |
| Tone-bar clips in the treble | R05 W10 | MISSING | Visible in the jRhodes lengths at key 54 (#18). |
| Per-key T60 table, not constant Q | R05 §Measured 1 | IN PLAN | Fit per root; allow the key-54 step. |
| Release T60 per register | R05 §Measured 2 | IN PLAN (soft) | These are sfz settings, not measurements (#15). |
| Damper as a crossfade of decay coefficients | R02 §FM-1 4 (Fig. 3.4 clicks) | IN PLAN | Keep, but derive per-mode eps_rel from tine-only damping (#4). |
| Damper as nonlinear spring K1/K3/R | R02 Eq. 3.22–3.23 | REJECT | Needs a per-sample implicit step; the crossfade gives the audible result. |
| Damper thump (−36 dB pulse) | R05 EP-MK1 | IN PLAN (re-route) | Inject it on the tine coordinate, not V2. |
| Key-down thump (hammer leak to output) | R05 EP-MK1 | IN PLAN (default low) | jRhodes is a DI harp recording with little mechanical noise. Default it about −40 dB or off for STAGE. |
| Felt / LFSR key-release noise | R05 §FM-1 Noises | IN PLAN (optional) | Cheap. Low priority. |
| dB-linear velocity law (30 dB) | R05 Eq. 1, §FM-1 velocity | IN PLAN (change target) | Apply it to hammer **speed** into the contact model, not to force amplitude. |
| Velocity drives excitation, not output gain | R05 §Summary; R01 §FM-1 4 | IN PLAN | Correct: it is how bark appears. |
| Bark onset at v ≈ 96 | R05 §Measured 3 | IN PLAN | Make it a quantitative relative bark-index check (spec C6). |
| EP-MK1 half-sine one period long (1/f0) | R05 Eq. 2 | REJECT | 22 ms at F1 is far longer than any felt contact (1–3 ms, R03) and would kill the bell. |
| EP-MK1 2 kHz HPF before the tine resonators | R05 §Model | REJECT | Not physical. The contact spectrum and strike weights do this. |
| EP-MK1 hammer into the pickup (+3 dB) | R05 §Model | REJECT | A hack. Use the residual table (#13). |
| EP-MK1 tanh → exp-asymmetry → y⁴ buzz → HPF at f0 | R05 Eq. 4; R02 §Pickup | REJECT | Superseded by the physical Φ. R02 found this chain harsh with a non-sinusoidal input, and an HPF at f0 would cut the bass fundamental. |
| EP-MK1 decay = constant Q | R05 §Measured 1 | REJECT | 2.5× too long in the bass, 2× too short in the mid. |
| EP-MK1 clear state on retrigger after 1 ms fade | R05 §Discretization | REJECT for the same note | Superpose (#7); keep it only for steals. |
| EP-MK1 skip silent voices | R05 §FM-1 | IN PLAN | Use a per-key kill threshold. |
| EP-MK1 normalised 2-pole oscillator with the hammer as initial state | R05 §Discretization | PARTLY | Use the force-into-y injection of MC3, which handles superposition. |
| STK Rhodey hybrid "bark" FM layer | R04 §FM-1 2 | REJECT (as default) | Not physical. It aliases above MIDI 54/78 (R04 §Measured) and would fight the pickup's own bark. It could be a non-default "'80s DX" TYPE. |
| STK as a fallback voice | R04 §FM-1 1 | REJECT | 1.5 s linear decays, no key scaling (R04). |
| rdpiano log-domain envelopes | R04 §FM-1 3 | REJECT for the voice | Our decays come from the resonator poles; there is no envelope. It may suit the noise or thump layers. |
| rdpiano exponential pitch LUT (4096/oct) | R04 §FM-1 4 | REJECT for now | Per-key E comes from the host. Revisit for pitch bend (E scales by 2^(c/1200) through one LUT). |
| rdpiano rate table doubling every 8 steps | R04 §FM-1 3 | IN PLAN (UI) | A useful law for the DECAY and RELEASE knobs (scale eps). |
| Felucca SVF modal bank | R06 §Blocks 1 | REJECT | It uses 64-bit divisions and costs about 13 %/voice (R06). |
| Felucca error-feedback DC blocker | R06 §Blocks 3 | REJECT for the voice | The Δ form has no DC. Use it in the master stage only. |
| Per-key random variation | (this review) | MISSING | Seeded jitter from the fit residuals (#20). |
| Sympathetic resonance with the pedal down | (this review) | REJECT (for now) | Weak on a Rhodes (ESTIMATE) and costly (it couples voices). |
| Float golden model + analysis-by-synthesis fit | (this review) | MISSING | Separates model errors from fixed-point errors (#8, #9). |
| 2× pickup oversampling | R01 §FM-1 7; R03 §FM-1 5 | IN PLAN (downgrade) | COMPUTED aliasing is ≤ −54 dB up to C7 at 1.5 gap. Make it a check (#12). |
| "Keep Φ smooth" against aliasing | PLAN §2, §8 | REJECT (bad idea) | It discards the physics to solve a problem that is not there. |
| TONE = blend of 2 Φ tables | PLAN §2 | REJECT (bad idea) | L_ver is a translation; use an offset (#10). |
| H1 summed into x | PLAN §2 | REJECT (bad idea) | Wrong physics (#3). |
| Symmetric tonebar detune f0(1 ± δ) | PLAN §2 | REJECT (bad idea) | Pitch drift and inconsistent weights (#4). |

---

## Detailed implementation spec (Phases 1–3)

### S0. Conventions

- **fs** = 44 100 Hz. **Block** B = 16 frames (`RH_BLOCK`, unchanged), 16 blocks per ISR call of 256 frames.
- `mul30(a, b) = (int32_t)(((int64_t)a * b) >> 30)` and `mul24(a, b) = (int32_t)(((int64_t)a * b) >> 24)`.
  Shifts are arithmetic (floor). There is no 64-bit `/` or `%` anywhere; every division happens on the host.
- **Displacement unit:** 1.0 = the gap L_hor of the key's register (1/16" or 1/32", R05 W3). It is stored as
  int32 **Q24** (range ±128 gaps; in practice ≤ ±6).
- **Squared radius s:** Q24 gap². s_max = 32 gap² = 2²⁹ in Q24 (`S_MAX`).
- **Coefficients:** E and eps are Q30 (E < 2; eps in [0, 1)).
- **Audio out:** Q15 into `out[]` (the platform clips above ±32767).
- **Modes:** `RH_V1 = 0` (sustain normal mode, exactly f0), `RH_V2 = 1` (the other tine–bar normal mode),
  `RH_O1 = 2` (2nd tine mode), `RH_O2 = 3` (3rd tine mode), `RH_H1 = 4` (horizontal polarisation).
  `RH_NM = 5`. A mode with `E == 0` is absent: above 18 kHz, or not yet enabled in Phase 2.

### S1. Generated tables (`firmware/gen/rhodes_tables.h`, from `tools/gen_rhodes.py`)

```c
#define RH_KEY_LO 21                 /* A0; 88 entries to C8 (73-key data E1..E7, extrapolated outside) */
#define RH_NKEY   88
#define RH_NBAND  5                  /* tip bands: 30, 50, 70, 90 duro, wood (R05 W1) */
#define RH_NLAYER 4                  /* pulse-shape velocity layers */
#define RH_PLEN   64                 /* pulse samples per shape (+2 guard zeros) */
#define RH_PU_N   2048               /* pickup table cells over s = 0..32 gap^2 */
#define RH_PU_SH  18                 /* index = s(Q24) >> 18: 32 gap^2 = 2^29 in Q24, 2048 cells of 2^18 */

typedef struct {                     /* const, flash; ~96 B */
    int32_t e[RH_NM];                /* Q30  E = sqrt(1 + r^2 - 2 r cos(theta)), held */
    int32_t eps[RH_NM];              /* Q30  1 - r^2 per sample, key held (rounded UP) */
    int32_t eps_rel[RH_NM];          /* Q30  1 - r^2 per sample, damper on (rounded UP) */
    int32_t b[RH_NM];                /* Q30  force(Q15) -> y(Q24): dy = (F * b) >> 21 */
    int32_t off;                     /* Q24  default voicing offset L_ver / L_hor (signed) */
    int32_t gain;                    /* Q16  per-key output gain (loudness normalisation) */
    int32_t kill;                    /* Q24  sum(|x|+|y|) over modes below which the voice ends */
    int32_t hc;                      /* Q30  V1 -> H1 coupling per sample (Phase 3; 0 in Phase 2) */
    int16_t kappa;                   /* Q15  amplitude -> pitch, dE/E per gap^2 (Phase 3) */
    uint16_t prate;                  /* Q12  pulse samples per output sample at the reference velocity */
    uint8_t band;                    /* 0..4 */
    uint8_t fam;                     /* pickup table family 0 = 1/16", 1 = 1/32" */
    uint8_t res;                     /* residual table index (Phase 3), 0xFF = none */
    uint8_t pad;
} rh_key_t;

static const rh_key_t RH_KEYS[RH_NKEY];                     /* 88 x 96 B = 8.25 KiB */
static const int16_t  RH_PULSE[RH_NBAND][RH_NLAYER][RH_PLEN + 2];   /* Q15 shapes, 2.6 KiB */
static const uint16_t RH_VAMP[RH_NBAND][128];               /* Q15 force amplitude per MIDI velocity, 1.25 KiB */
static const uint16_t RH_VRATE[RH_NBAND][128];              /* Q12 pulse-rate multiplier per velocity, 1.25 KiB */
static const uint8_t  RH_VLAYER[128];                       /* velocity -> shape layer 0..3 */
static const int16_t  RH_PU_D[2][RH_PU_N + 2];              /* Q15 D(s) = dPhi~/ds, normalised; 8.0 KiB */
static const int32_t  RH_RLC[4][5];                         /* per TYPE: b0 b1 b2 a1 a2, Q28 (post-mix) */
/* Phase 3 */
static const int16_t  RH_RES[RH_NBAND][3][512];             /* residual tip displacement, Q15 of 1/8 gap; 15 KiB */
/* header: generator version, sha256 of params.json and of the emitted arrays (determinism check) */
```

The generator emits `RH_PU_SH` from s_max and `RH_PU_N`, and the C code uses only the macro.

Total flash: about 21 KiB (Phase 2) plus about 15 KiB (Phase 3 residuals).

### S2. Run-time voice state (`rhodes.c`, RAM about 160 B × 8)

```c
static struct {
    /* main loop writes these, then bumps strike (unchanged handshake) */
    volatile uint32_t strike; volatile uint8_t damped; volatile uint8_t vel;
    uint8_t note, key; uint32_t age;
    /* ISR-owned; the main loop reads seen and alive */
    volatile uint32_t seen; volatile uint8_t alive;
    uint8_t cur;                     /* the note the state belongs to */
    const rh_key_t *k;
    int32_t x[RH_NM], y[RH_NM];      /* Q24 gap units */
    int32_t e[RH_NM], eps[RH_NM];    /* live Q30 */
    int32_t xprev, hprev;            /* previous tip V (without offset) and H, Q24 */
    uint32_t ppos, prate;            /* pulse position Q16 (in table samples), step Q16 */
    int32_t pamp;                    /* Q15 */
    const int16_t *ptab;             /* NULL = no contact */
    uint16_t rpos;                   /* residual table position (Phase 3) */
    uint8_t ramp;                    /* damper crossfade: blocks left (0..RH_RAMP) */
    uint8_t fade;                    /* steal fade: blocks left */
    uint8_t was_damped;
} rv[RH_NV];

static volatile int32_t rh_off_user;     /* Q24 TONE offset delta (Phase 4 knob; 0 now) */
static volatile int32_t rh_type;         /* RLC set */
static int32_t rlc_z[4];                 /* post-mix biquad state, DF1 */
static int32_t mono[256];                /* mix buffer */
#define RH_RAMP 14                       /* 14 blocks = 5.1 ms damper crossfade */
```

`rh_quiet()` becomes `!alive && strike == seen` for every voice.

### S3. Per-block work (ISR, once per 16 frames per voice)

```
start of rh_render: clear mono[0..n)
for each voice v:
  if strike != seen:                              /* a new strike */
      seen = strike
      nk = &RH_KEYS[note - RH_KEY_LO]  (note clamped to 21..108)
      if alive and cur == note:  /* RE-STRIKE: keep x, y (superposition) */
          (nothing)
      else if alive:             /* steal: fade 3 blocks, then init (see "fade" below) */
          fade = 3; pending = 1; continue to the sample loop with fade
      else: init(v, nk)
      load_pulse(v, nk, vel)     /* ptab, pamp, prate, ppos = 0 */
      ramp = 0; was_damped = 0; eps[m] = nk->eps[m]   /* damper lifted */
  if !alive: continue
  /* damper */
  if damped != was_damped: ramp = RH_RAMP; was_damped = damped
  if ramp: ramp--; for m: eps[m] = lerp(target_other, target, (RH_RAMP-ramp)/RH_RAMP)
      where target = damped ? k->eps_rel[m] : k->eps[m]   (integer lerp: (a*(R-i) + b*i) * RECIP_R >> 16)
  /* amplitude -> pitch (Phase 3) */
  a2  = sq(x[V1] >> 12) + sq(y[V1] >> 12)                 /* Q24 gap^2 */
  rel = (int32)(((int64)k->kappa * a2) >> 9)              /* Q15 * Q24 >> 9 = Q30 relative dE/E */
  e[V1] = k->e[V1] + mul30(k->e[V1], rel)                 /* same for V2 */
  /* end of voice */
  if !ptab and !rpos_active and sum_m(|x[m]| + |y[m]|) < k->kill: alive = 0; continue
  run the sample loop for this block
```

`init(v, nk)` sets: x = y = 0; xprev = hprev = 0; e[m] = nk->e[m]; eps[m] = nk->eps[m]; k = nk; cur = note;
alive = 1.

`load_pulse(v, nk, vel)` does:
```
ptab  = RH_PULSE[band][RH_VLAYER[vel]]
pamp  = RH_VAMP[band][vel]
prate = (nk->prate * RH_VRATE[band][vel]) >> 8     /* Q12*Q12 >> 8 = Q16 */
ppos  = 0
```

Steal fade: an output gain ramp 1 → 0 over 48 frames, applied per sample. Then `init`, then `load_pulse`. The
new note starts about 1.1 ms late, which is acceptable (EP-MK1 uses 2 ms).

### S4. Per-sample loop (one voice, inside the block)

```
for j in block:
  /* 1. hammer contact */
  F = 0
  if ptab:
      i = ppos >> 16; f = (ppos >> 1) & 0x7FFF
      F = ptab[i] + (((ptab[i+1] - ptab[i]) * f) >> 15)     /* Q15 */
      F = (F * pamp) >> 15
      ppos += prate; if (ppos >> 16) >= RH_PLEN: ptab = NULL
  /* 2. modes (MC3); x is the tip displacement contribution, Q24 gaps */
  xv = 0
  for m in V1, V2, O1, O2 (skip if e[m] == 0):
      if F: y[m] += (int32)(((int64)F * k->b[m]) >> 21)
      x[m] += mul30(e[m], y[m])
      y[m]  = y[m] - mul30(eps[m], y[m]) - mul30(e[m], x[m])   /* D·y = y - eps·y */
      xv   += x[m]
  /* Phase 3: H1 */
  y[H1] += mul30(k->hc, x[V1]) + (F ? ((F * k->b[H1]) >> 21) : 0)
  x[H1] += mul30(e[H1], y[H1]);  y[H1] = y[H1] - mul30(eps[H1], y[H1]) - mul30(e[H1], x[H1])
  xh = x[H1]
  /* Phase 3: residual upper modes */
  if rpos < 512: xv += RH_RES[...][rpos++] << 9          /* Q15 of 1/8 gap -> Q24 */
  /* 3. pickup: radial field, exact difference, midpoint gain */
  off = k->off + rh_off_user
  u  = xv + off;   up = xprev + off                     /* the SAME offset on both: no click on TONE moves */
  s  = mul24(u, u) + mul24(xh, xh)                      /* Q24 gap^2 */
  sp = mul24(up, up) + mul24(hprev, hprev)
  ds = (int64)(u - up) * (u + up) + (int64)(xh - hprev) * (xh + hprev)   /* Q48, exact */
  ds >>= 12                                             /* Q36 */
  sm = (s + sp) >> 1
  if sm >= S_MAX: D = 0
  else: i = sm >> RH_PU_SH; f = sm & ((1 << RH_PU_SH) - 1)
        D = tab[i] + (int32)(((int64)(tab[i+1] - tab[i]) * f) >> RH_PU_SH)  /* Q15 */
  yv = (int32)(((int64)D * ds) >> RH_Y_SH)              /* generator picks RH_Y_SH, e.g. 36 + 15 - 15 - margin */
  mono[j] += (int32)(((int64)yv * k->gain) >> 16)       /* times the fade ramp if fading */
  xprev = xv; hprev = xh
```

Overflow budget: |u| ≤ 8 gaps = 2²⁷; Δu·Σu ≤ 2⁵⁵; D·ds ≤ 2¹⁵·2⁴³ = 2⁵⁸. All of these fit an int64.

Cost (ESTIMATE):
- Modes: 4 × 3 = 12 multiplies, plus 4 for H1.
- Pickup: 4 multiplies for the squares, 2 for ds, 1 for the lerp, 1 for D·ds, 1 for the gain.
- Total about 25 64-bit multiplies plus about 40 ALU operations per voice-sample, or about 6 more during
  contact.
- 8 voices: about 23–28 M instructions/s. Phase 0 converts this to cycles.

### S5. After the voices (once per ISR call)

```
for j in 0..n-1:
  acc = b0*m[j] + b1*m1 + b2*m2 - a1*o1 - a2*o2   (int64, Q28 coefficients) ; o = acc >> 28
  shift histories
  out[2j] += o; out[2j+1] += o
```

### S6. Main-loop changes (`rh_note_on`)

- Voice choice is unchanged in priority: same note, then silent, then oldest damped, then oldest.
- Write `vel` before `strike++`. Writing `damped = 0` before `strike++` is already the order.
- Nothing else changes in the interface.

### S7. Host generator (`tools/gen_rhodes.py`): the exact steps

1. **Inputs:**
   - `docs/model/data/params.json`: fitted per root (step 9 of S9), plus global priors.
   - The 73 tine lengths from R02 `TineLengths.h`, as numbers. Extrapolate MIDI 21–27 and 101–108 with a
     log-log fit of L against f0 (ESTIMATE).
2. **FE tine per key:**
   - 40 Hermite Euler–Bernoulli elements; d = 0.075"; E = 200 GPa; ρ = 7850; clamped base.
   - A point mass at s·L, with s from params (global fit).
   - Bisect m_s so that f1 = f_t, the tine target from step 3.
   - Output: f2/f1, f3/f1 (and f4..f12/f1 for the residual table); mode shapes at the strike point x_c(key)
     (R05 W1 strike line, interpolated linearly in key) and at the tip; modal masses.
   - Unit test: with m_s = 0, the ratios must come out 6.267 and 17.55 to within 0.1 %.
3. **2-DOF tine–bar:**
   - From params (δ_b, k, μ, σ_t, σ_b), build the 4×4 state matrix and take its eigenvalues λ1,2 and the
     tine-to-tine residues R1,2.
   - Identify the mode with the larger late energy |R|·e^(−σ·2 s); call it V1. Scale both frequencies so
     f(V1) = f0 exactly.
   - Repeat with σ_t + σ_damp to get the release decays (match modes by nearest eigenvalue).
4. **Overtones:** f(O1) = f0·r2 and f(O2) = f0·r3, with σ from params, defaulting to R03's κ⁴ law. Drop any
   mode above 18 kHz (E = 0).
5. **Coefficients:**
   - r = exp(−σ/fs), θ = 2πf/fs.
   - E = round(sqrt(1 + r² − 2r·cosθ)·2³⁰).
   - eps = ceil((1 − r²)·2³⁰). The same applies to eps_rel.
6. **Hammer pulses:**
   - For each band, simulate at 16× fs: hammer mass m_h (prior 11–30 g, R02/R03); felt law F = K·e^p +
     R·d(e^p)/dt with p = 2.5 and K, R per band (fitted so T_c matches a 1–3 ms prior; ESTIMATE); the tine's
     first 6 FE modes plus the bar, all at rest; hammer speeds v_h from MIDI velocity through
     v_h = v_max·2^(−R_dB/6.02·(1 − (v−1)/126)) with R_dB = 30 and v_max = 4 m/s (R02 Tab. 3.2).
   - Record F(t) until the contact ends.
   - Per (band, layer), normalise time to T_c and peak to 1, and resample to 64 samples → `RH_PULSE`.
   - Fit T_c(v) and the impulse ∫F per band over the 128 velocities → `RH_VRATE` (Q12, relative to the
     reference velocity 100) and `RH_VAMP` (Q15, the peak relative to the v = 127 peak).
   - `prate` per key = 64/(T_c(key, v_ref)·fs) in Q12.
7. **Input gains b[m]:**
   - Build the MC3 recursion **in float with the quantised E and eps**, inject a unit Q15 force for one sample,
     and measure the x amplitude.
   - Scale b[m] so that x reproduces the continuous modal response: residue × ψ_m(x_c)·ψ_m(tip)/m_m, in gap
     units.
   - Calibrating against the exact discrete structure, rather than a formula, removes any mismatch between
     the two.
   - Assert that |b| < 2³¹ and that at least 12 significant bits survive.
8. **Pickup tables:**
   - Per family, in gap units (L_hor = 1): R_p = prior ratio (R03 code: 1 gap). g0(u) is R03 eq. 25 with
     L_ver = 0.
   - Φ̃(s) = ∫g0, and D(s) = g0(√s)/(2√s). At s = 0, take the limit g0′(0)/2.
   - Normalise to Q15 with the scale folded into `RH_Y_SH` and `gain`.
   - Assert D(32 gap²) < 3·10⁻³ of max. Assert that the golden model's maximum s at v = 127 for every key is
     below 0.8·s_max.
9. **off, gain, kill:**
   - off = L_ver/L_hor from params.
   - gain sets the v = 100 RMS of a single voice at the per-register target, about −18 dBFS (ESTIMATE),
     smoothed over keys.
   - kill = the state sum that gives an output below 1 Q15 LSB, solved with the golden model.
10. **RLC:** prewarped bilinear lowpass, f = 5 kHz, Q from the TYPE, in Q28.
11. **Jitter (Phase 3):** a seeded PRNG (fixed seed) perturbs per-key bar detune, gain, eps and off within the
    fit-residual bounds.
12. **Emit** the header with sha256 values. Running the generator twice must produce identical bytes.

### S8. Checks in `tools/analyze.py`

The same analysis functions run on reference FLACs, golden-model renders and C renders.

**Shared analysis primitives:**
- `band(sig, f, bw)`: FFT-domain bandpass f·(1 ± bw), then the analytic signal.
- `env_db`: the magnitude of the analytic signal, smoothed with a 10 ms moving RMS, in dB.
- `inst_f`: the slope of the unwrapped phase, from a linear regression over a window.
- `harm(sig, f0, k, win)`: the power at k·f0 (±3 %) from a Hann FFT over `win`.

**The checks:**

**C1 Tuning.**
- Every key, v = 40, 3 s held.
- f = `inst_f` of `band(f0, 1/6 octave)` over [0.5, 2.5] s, or [0.2 s, the −40 dB time] for keys above C6.
- Pass: |1200·log2(f/f_target)| ≤ 2 cents.
- Also pass: the drift between the first and second halves of the window is ≤ 2 c (this catches #4).

**C2 Decay.**
- 15 roots × all layers, model and reference.
- Take env_db of the fundamental band. Fit a two-segment piecewise-linear model by least squares over
  [peak − 3 dB, max(peak − 45 dB, noise + 10 dB)], searching the breakpoint.
- Pass: T60_late = −60/slope_late within ±20 % of the reference; T60_early within ±30 %; breakpoint level
  within ±4 dB.
- The reference noise floor is the median of the last 0.2 s.

**C3 Overtones.**
- env_db of the bands at f(O1) ±1.5 % and f(O2), where the reference shows a peak 10 dB above its
  neighbourhood.
- Pass: T60_O1 ≤ T60_fund/5; T60 within ±30 % of the reference; frequency within ±1 % of the reference ratio.

**C4 Release.**
- Each register's key: hold 1.5 s, then key-up.
- Take the 5 ms RMS in dB. Regress from key-up + 10 ms until −40 dB or 1 s.
- Pass: T60 within ±25 % of the release table (a soft target). The voice must also end, `alive = 0`, within
  2 × T60_rel + 50 ms.

**C5 Pickup unit test.**
- An `rh_pickup_probe()` host entry feeds x = 1.5 gap·sin(2π·441 Hz·t) through the C pickup only.
- Pass at the default offset:
  - 2 positive peaks per period above 20 % of the maximum;
  - the ratio of the smaller to the larger positive peak in [0.3, 0.95];
  - |max/min| = 1 ± 0.01 (antisymmetry).
- Pass at off = 0: equal peaks (ratio 0.99–1.01) and P(H1)/P(H2) < −40 dB.
- C against the float pickup: residual ≤ −70 dB.

**C6 Velocity and bark.**
- Roots F1, E2, D3, D4, B4 at v = 8, 16, …, 127.
- Level = the peak 50 ms RMS. Pass: L(127) − L(8) within 24–32 dB (adjustable).
- Bark index B(v) = 10·log10(Σ_{k=2..5} harm_k / harm_1) over [30, 150] ms.
- ΔB(v) = B(v) − B(v_soft). For the reference, the layer centre velocities are 24, 60, 84, 104 and 120 (R05,
  no-xfade map).
- Pass: |ΔB_model − ΔB_ref| ≤ 3 dB at each layer centre, and ≥ 50 % of the total rise in ΔB happens above
  v = 84.

**C7 Golden.**
- The C render and the golden float render of the same scripted case (notes, chords, re-strikes, pedal),
  aligned and differenced.
- Pass: residual energy ≤ −60 dB of the signal, per case.

**C8 Alias.**
- Keys ≥ C5 at v = 127.
- alias_ratio = the energy left after a least-squares fit of all harmonics of f0 and of f(O1) ± k·f0 below
  Nyquist, divided by the fitted energy, measured in [0.05, 0.5] s.
- Pass: ≤ −60 dB. Report the worst key.

**C9 Noise.**
- A soft E1 (v = 20) note: the C render against the golden render, residual ≤ −60 dB.
- After every voice ends, the output is exactly 0.

**C10 Re-strike.**
- E2 struck at v = 100, then again at t = 0.5 s at v = 60.
- Pass: the C render equals the golden superposition (same as C7), and there is no output gap: the 1 ms RMS
  never falls below −30 dB of its local level.

**C11 Device timing.**
- Worst-case block: 8 keys struck in the same block while 8 voices are alive.
- Pass: the ISR time stays below the share Phase 0 assigns to the voices.

**Attack pitch (Phase 3).**
- `inst_f` at [50, 200] ms minus `inst_f` at [2, 3] s, loudest layer vs softest, at F1/B1/E2.
- Pass: the model is within ±1.5 c of the reference.

### S9. Order of work: small, verifiable steps

**Phase 1 (host only)**

1. `tools/rhodes_dsp.py`: the shared analysis primitives in S8, with unit tests on synthetic decaying sines of
   known f and T60 (they must recover f to 0.1 c and T60 to 1 %).
2. `fit_reference.py`, part A: download the jRhodes FLACs (git-ignored) and measure per root and layer: C1–C3
   values, ripple rate and depth, the 7× and 20× partials and their ratios, bark indices, attack pitch.
   → `reference.json`. Plot it. Check the key-54 step (#18).
3. `tine_fe.py`: the FE tine plus spring. Test that the bare cantilever gives 6.267/17.55. Reproduce the F1
   table in #6.
4. `tinebar.py`: the 2-DOF solve. Tests: with k = 0 the modes equal the bare tine and bar; the residues sum
   correctly; the long mode retunes to f0.
5. `pickup.py`: Φ̃, D and the tables. Tests: D(s_max) is small; the C5 spike pattern holds in float.
6. `hammer.py`: the contact simulation. Tests: energy is conserved with the felt damping off; T_c falls with
   v_h as a power law; contact with a rigid wall matches the Hertz-law T_c.
7. `rhodes_model.py`: the golden float model, with exactly the S3/S4 structure and the same table semantics,
   at float precision.
8. `fit_model.py`: the per-root least-squares fit of the golden model against `reference.json`, on C1–C3, the
   ripple, the overtone levels and ΔB. → `params.json`. **Gate A:** the golden model passes C1, C2, C3, C5 and
   C6 against the reference.
9. `gen_rhodes.py` → `rhodes_tables.h`. Test that two runs give identical bytes.

**Phase 2 (C, host first, then device)**

10. `tests/voice_render.c` includes `rhodes.c` with stubs, runs a case list (note, velocity, times, pedal) and
    writes `build/host/cases/*.wav`.
11. V1 only, no pickup (output = x scaled). Pass C1 and the C7 analogue against the golden model's x output.
12. Add V2 and O1. Pass the two-stage C2 against the golden model.
13. Add the hammer pulse. C7 on x.
14. Add the pickup. C5 and C7 on audio.
15. Add velocity, the damper ramp, sustain, re-strike superposition and steal fade. C4, C6, C10.
16. Add the post-mix RLC. Calibrate the gain. C9.
17. Device: timing C11, then a listening pass.

**Phase 3**

18. H1 with the radial pickup (C5 with x_h = 0 must still be bit-identical).
19. O2 and the residual table.
20. Amplitude → pitch (the attack-pitch check).
21. Jitter, thumps, felt noise.
22. TYPE parameter sets.
23. C8 over all keys. Only if it fails, add 2× oversampling of the x input to the pickup, with a halfband
    decimator.

---

## Open questions

1. **Tuning-spring position.** The FE fit admits a spring near 0.65 L and one near the tip (#6). Their 20×
   ratios differ: 19.4 vs 21.8 at F1. Can the jRhodes bass roots resolve it, or does the user know where
   Rhodes springs sit?
2. **Hammer mass and felt constants.** The sources disagree: 11 g in R02, 30 g in R03, and K differs by 3×
   between R03's paper and PyPHS. Only T_c matters for the generated pulse. Is a prior of 1–3 ms acceptable,
   to be adjusted against the reference attack spectra?
3. **Mapping hammer-tip numbers to MIDI.** R05 W1 uses 1–88 and W2 uses 00–81 (R05 Gaps). This review assumes
   W1's positions are 88-key, so A0 is position 1 (ESTIMATE). Is that right?
4. **Damper on the top keys.** Do the highest Rhodes keys have effective dampers? R05 says felt is "less
   effective" in the treble (ESTIMATE). This sets eps_rel for keys ≥ C6.
5. **Absolute tip amplitude.** The sources range from about 20 µm (R02, model) to cm (R01, likely uncalibrated).
   The design avoids needing it: the x-scale is fitted so that bark starts near v ≈ 96. Is that acceptable as
   the definition of "realistic" here?
6. **jRhodes EQ.** A neutral spectral target needs either the recording-chain EQ or a second reference. Should
   the plan accept that the absolute brightness (RLC, preamp) is set by ear?
7. **CPU.** The H1 and residual-table decisions depend on Phase 0. If the budget is tight, which comes first:
   H1 (whirl, cost 4 multiplies) or 8 voices?
