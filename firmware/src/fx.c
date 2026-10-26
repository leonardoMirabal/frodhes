/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes effects: a vintage-style stage keyboard's effect columns (IMPLEMENTATION-v3 §9):
 *   COMP -> DRIVE -> FILTER -> TREMOLO/WAH (mono -> stereo) -> PHASER -> CHORUS -> D.DELAY/A.DELAY -> REVERB (HALL)
 *   -> master   (0.9.3: the FILTER, 12 / 18 dB, with its LFO; 0.9.5: DRIVE moved before it, the user)
 * (0.4: the combined CHO/PHA switch became two effects that can run together, and a compressor came first)
 * Mono int32 Q15 (with headroom) until the tremolo, stereo after. Every effect keeps its state while bypassed
 * (switching on never clicks); switching an effect off cuts its input and lets the tail ring.
 *
 * Ported from Felucca (GPL-3.0-only, Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments):
 * track_dist (DRIVE), dc_block and the master limiter, the HALL reverb (rev_hall and helpers), the delay core.
 * Felucca's MIT helpers (softclip, osc_sine, ...) are not in its tree: rewritten here from generated tables.
 *
 * Main loop: fx_set(id, value 0..127). ISR: fx_process(mono, out, n) adds stereo Q15 into out. */
#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#include "fx_tables.h"

#ifdef DINGHY_HOST
#define POOL
#else
#define POOL __attribute__((section(".pool")))
#endif

enum { FXP_DRIVE, FXP_TW_SW, FXP_TW_DEPTH, FXP_TW_RATE, FXP_TW_MODE, FXP_TW_SHAPE,
       FXP_CP_SW, FXP_CP_DEPTH, FXP_CP_SPEED, FXP_CP_FDBK,
       FXP_DL_SW, FXP_DL_DEPTH, FXP_DL_TIME, FXP_DL_FDBK, FXP_DL_SYNC,
       FXP_RV_DEPTH, FXP_DRIVE_ROW, FXP_RAW, FXP_DL_TONE,
       FXP_PH_SW, FXP_PH_DEPTH, FXP_PH_SPEED, FXP_PH_FDBK, FXP_CMP_SW, FXP_CMP_AMT, FXP_CMP_ATK, FXP_CMP_REL,
       FXP_LEVEL, FXP_CMP_RATIO, FXP_CMP_GAIN,
       FXP_FL_TYPE, FXP_FL_FREQ, FXP_FL_RESO, FXP_FL_LFO, FXP_LFO_SHAPE, FXP_LFO_RATE,
       FXP_DRV_TONE, FXP_DRV_MIX, FXP_DRV_SW, FXP_RV_DECAY, FXP_RV_TONE, FXP_RV_PREDLY,   /* 0.9.3: DRIVE / REVERB pages */
       FXP_RV_TYPE, FXP_RV_SIZE, FXP_RV_MOD, FXP_RV_LOCUT,  /* 0.9.5: the reverb's TYPE and REVERB 2 (rev_types.c) */
       FXP_N };   /* FXP_RAW: developer bypass of the whole chain (tests) */
static volatile int32_t fx_p[FXP_N] = {[FXP_DL_FDBK] = 80, [FXP_DL_TONE] = 70, [FXP_LEVEL] = 64, [FXP_CMP_RATIO] = 3,
                                       [FXP_FL_FREQ] = 127, [FXP_LFO_RATE] = 40, [FXP_DRV_TONE] = 64,
                                       [FXP_DRV_MIX] = 127, [FXP_DRV_SW] = 1, [FXP_RV_DECAY] = 64, [FXP_RV_TONE] = 64,
                                       [FXP_RV_TYPE] = 1, [FXP_RV_MOD] = 64};
static volatile uint32_t fx_lfo_sync_inc;        /* main loop: a synced LFO rate (Q32 step), 0 = free */
static volatile uint8_t fx_lfo_retrig;           /* main loop: a note started and RETRIG is on: the LFO from 0 */
static volatile uint32_t fx_cp_sync_inc;         /* main loop: a synced chorus rate, 0 = free */
static volatile uint32_t fx_ph_sync_inc;         /* main loop: a synced phaser rate, 0 = free */
static volatile int32_t fx_cmp_gr;               /* ISR: the compressor's gain reduction now, log2 Q12 (>= 0) */
static volatile uint32_t fx_dly_sync_samples;    /* main loop (tempo.c): a synced delay time, 0 = free */
static volatile uint32_t fx_tw_sync_inc;         /* main loop: a synced tremolo rate (Q32 step), 0 = free */
static volatile uint8_t fx_bypass;              /* main loop (the FX button): all effects off, settings kept */
static volatile int32_t fx_peak;                 /* ISR: last block's max |out| */
static volatile uint32_t prof_us, prof_max_us;   /* ISR (frodhes.c app_audio): its own time, this block / maximum */
static uint32_t prof_over;
static volatile uint32_t fx_quiet_blocks;
static void fx_set(uint32_t id, int32_t v) { if (id < FXP_N) fx_p[id] = v < 0 ? 0 : v > 127 ? 127 : v; }
static int fx_quiet(void) { return fx_quiet_blocks >= 86u; }   /* 500 ms under -66 dBFS */

/* FX_K32: a local multiplier that is constant over a loop and used in (int64_t)a * v, read from the stack where it is
 * used (volatile: one load, the same value). Kept in a register instead, the compiler widens it to 64 bits once before
 * the loop and then, inside it, no longer sees a 32 x 32 product: it multiplies 64 x 64 (four multiplies and the
 * carries) instead of one instruction. */
#define FX_K32 volatile int32_t
static inline __attribute__((always_inline)) int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }
static inline __attribute__((always_inline)) int32_t mulq15(int32_t a, int32_t b) { return (a * b) >> 15; }        /* |a|,|b| small enough */
/* ---- the on / off crossfade (0.9.6, the bug hunt: switching an effect replaced the dry signal by the processed one,
 * or back, in one sample: up to 1700 LSB steps under a held chord). An effect switched on or off keeps running while
 * its wet share w (Q15) glides 0 <-> 32768 by XF_STEP a sample (46 ms, as the drive's ON / OFF), then rests: at rest
 * (w at 0 or 32768) nothing extra runs, and at 32768 the effect's output is what it always was, bit for bit. The dry
 * copy (xf_d) is taken before the effect runs, the blend after; the effects that use it run one after another */
#define XF_STEP 16
static int32_t xf_d[2][256] POOL;
static inline int32_t xf_next(int32_t w, int on, uint32_t n)   /* the share at this block's end */
{
    int32_t s = XF_STEP * (int32_t)n;
    return on ? (w + s > 32768 ? 32768 : w + s) : (w - s < 0 ? 0 : w - s);
}
/* x = d + (x - d) w, w ramping from w0 to w1 over the n samples (n = 256: steps of 4096, exact ends) */
__attribute__((noinline)) static void xf_blend(int32_t *x, const int32_t *d, uint32_t n, int32_t w0, int32_t w1)
{
    int32_t w = w0, dw = (w1 - w0) / (int32_t)n;
    uint32_t i;
    for (i = 0; i < n; i++) {
        w += dw;
        x[i] = d[i] + (int32_t)(((int64_t)(x[i] - d[i]) * w) >> 15);
    }
}
/* one helper pair for every effect's on / off (shared code: the flash is tight). xf_pre: the share at this block's
 * end and, while it moves, the dry copy of L (and of R; R = 0: mono) into xf_d; xf_post: the blend, L (and R) against
 * dl (dr) */
__attribute__((noinline)) static int32_t xf_pre(int32_t w, int on, const int32_t *L, const int32_t *R, uint32_t n)
{
    int32_t w1 = xf_next(w, on, n);
    if (w != 32768 || w1 != 32768) { memcpy(xf_d[0], L, n * sizeof *L); if (R) memcpy(xf_d[1], R, n * sizeof *R); }
    return w1;
}
__attribute__((noinline)) static void xf_post(int32_t *L, int32_t *R, const int32_t *dl, const int32_t *dr, uint32_t n,
                                              int32_t w0, int32_t w1)
{
    if (w0 == 32768 && w1 == 32768) return;
    xf_blend(L, dl, n, w0, w1);
    if (R) xf_blend(R, dr, n, w0, w1);
}
static inline int32_t softclip(int32_t x)                 /* Q15 tanh: x in Q15, flat beyond +-4 */
{
    uint32_t i;
    int32_t f;
    x = clampi(x, -131072, 131071);
    i = (uint32_t)(x + 131072) >> 10;
    f = (x + 131072) & 1023;
    return FX_TANH[i] + (((FX_TANH[i + 1] - FX_TANH[i]) * f) >> 10);
}
static inline int32_t osc_sine(uint32_t ph)               /* Q32 phase -> +-32767 */
{
    uint32_t i = ph >> 22;
    int32_t f = (int32_t)((ph >> 7) & 0x7FFFu);
    return FX_SINE[i] + (((FX_SINE[i + 1] - FX_SINE[i]) * f) >> 15);
}

/* ---- the LFO (its page) and the FILTER: a TPT state-variable stage (stable at any setting). TYPE: OFF, LP12 LP18,
 * HP12 HP18, BP, NOTCH. 12 dB / octave = the resonant stage; 18 = it and a 6 dB one-pole (no 24: its second stage cost
 * ~190 us of the audio block at the worst case). FREQ 30 Hz .. 16 kHz (log), RESO Q 0.707 .. 20, LFO moves FREQ by up to
 * +-4 octaves. Coefficients every 32 samples (smoothed), 32-bit division only (two partial quotients). */
static uint32_t lfo_ph, lfo_rnd = 0x9E3779B9u;
static int32_t lfo_sh, fl_ic[2], fl_op, fl_pos = 127 * 516;
static uint8_t fl_was_on, fl_type;                 /* fl_type: the TYPE the filter runs (it glides to dry before
                                                     * it takes another one, or stops for OFF: the bug hunt, 2026-10-25,
                                                     * LP12 -> HP12 was -11 dB in one sample) */
static int32_t fl_ow = 32768, fl_dry[32];         /* the filter's wet share (0 = fully open: dry), a sub-block of dry */
static int32_t fl_kq;                              /* RESO's damping k as it glides (Q24): at most 1/128 of itself a
                                                    * sub-block (k stepped once a block: on the band-pass the output is
                                                    * k v1, a gain step, 34x a held one's d2 on F12 -> F01, the bug hunt) */
#define FL_JMP 910                                 /* the cutoff's largest step a sub-block, 1/8 octave (fl_pos: 516 a
                                                    * FREQ step, ~7280 an octave): a jump of FREQ or of an S&H / square
                                                    * LFO swept 9 octaves in ~3 ms, a click and a resonant burst on BP
                                                    * (#13); the knob's detents and the LFO's sines move far less */
static int32_t lfo_value(void)                            /* +-32767 */
{
    uint32_t ph = lfo_ph;
    switch (fx_p[FXP_LFO_SHAPE]) {
    case 1: return ph < 0x80000000u ? (int32_t)(ph >> 15) - 32768 : 98303 - (int32_t)(ph >> 15);   /* triangle */
    case 2: return (int32_t)(ph >> 16) - 32768;                                                     /* saw up */
    case 3: return 32767 - (int32_t)(ph >> 16);                                                     /* saw down */
    case 4: return ph < 0x80000000u ? 32767 : -32767;                                               /* square */
    case 5: return lfo_sh;                                                                          /* sample & hold */
    default: return osc_sine(ph);
    }
}
static int32_t recip27(uint32_t den)                      /* 2^43 / den (den Q16) = 1 / den in Q27, 32-bit only */
{
    uint32_t q = 0x80000000u / den, r = 0x80000000u - q * den;
    return (int32_t)((q << 12) + (r << 12) / den);
}
typedef struct { int32_t a1, a2, a3, k; } svf_t;
static void svf_coef(svf_t *c, int32_t g, int32_t k)      /* g, k in Q24 */
{
    c->a1 = recip27(65536u + (uint32_t)(((int64_t)g * (g + k)) >> 32));
    c->a2 = (int32_t)(((int64_t)g * c->a1) >> 24);
    c->a3 = (int32_t)(((int64_t)g * c->a2) >> 24);
    c->k = k;
}
/* clampi(v, -2^24, 2^24) as smax + smin (the same value) */
static inline int32_t fl_cl(int32_t v) { v = v > -(1 << 24) ? v : -(1 << 24); return v < (1 << 24) ? v : 1 << 24; }
static inline int32_t svf_tick(const svf_t *c, int32_t *ic, int32_t x, uint32_t mode)   /* 0 LP 1 HP 2 BP 3 NOTCH */
{
    int32_t v3 = x - ic[1], v1, v2, kv1;
    v1 = (int32_t)(((int64_t)c->a1 * ic[0] + (int64_t)c->a2 * v3 + (1 << 26)) >> 27);   /* rounded: no DC creep */
    v2 = ic[1] + (int32_t)(((int64_t)c->a2 * ic[0] + (int64_t)c->a3 * v3 + (1 << 26)) >> 27);
    ic[0] = fl_cl(2 * v1 - ic[0]);
    ic[1] = fl_cl(2 * v2 - ic[1]);
    kv1 = (int32_t)(((int64_t)c->k * v1 + (1 << 23)) >> 24);
    return mode == 0u ? v2 : mode == 1u ? x - kv1 - v2 : mode == 2u ? kv1 : x - kv1;
}
/* one sub-block of the filter; mode and p18 are constants after inlining (a version per TYPE), the state in locals */
static inline __attribute__((always_inline)) void fl_run(int32_t *m, uint32_t n, const svf_t *c, const uint32_t mode,
                                                         const int p18, int32_t gp)
{
    int32_t ic[2] = {fl_ic[0], fl_ic[1]}, op = fl_op, *e = m + n;
    for (; m < e; m++) {
        int32_t x = *m * 8;                                              /* 8x finer inside: a lower noise floor */
        x = svf_tick(c, ic, x, mode);
        if (p18) {                                                       /* 18: + a 6 dB one-pole */
            int32_t v = (int32_t)(((int64_t)(x - op) * gp + (1 << 26)) >> 27), lp = v + op;
            op = fl_cl(lp + v);
            x = mode == 0u ? lp : x - lp;
        }
        *m = (x + 4) >> 3;
    }
    fl_ic[0] = ic[0]; fl_ic[1] = ic[1]; fl_op = op;
}
/* a TYPE -> another TYPE (0.9.6, the control sweep: through dry, HP12 -> HP18 at 1.5 kHz rose +21 dB for ~40 ms): one
 * state-variable stage gives LP, BP, HP and NOTCH at once, so the old TYPE's output crossfades into the new one's over
 * 46 ms (w: Q15, +16 a sample), each 18 dB TYPE with its own one-pole (op: the old one's, fl_op2: the new one's). Only
 * while it fades; at rest the loops above run as before */
static int32_t fl_op2, fl_xw;                              /* the new TYPE's one-pole, the crossfade (0: none) */
static uint8_t fl_xt;                                       /* the TYPE it fades to */
/* one sub-block of the filter at a TYPE (1..6): one loop per (mode, poles), no tests per sample (noinline: the loops
 * get its registers, as they had in fx_filter; a TYPE change runs it twice) */
__attribute__((noinline)) static void fl_pass(int32_t *m, uint32_t n, const svf_t *c, uint32_t type, int32_t gp)
{
    switch (type) {
    case 1: fl_run(m, n, c, 0, 0, gp); break;
    case 2: fl_run(m, n, c, 0, 1, gp); break;
    case 3: fl_run(m, n, c, 1, 0, gp); break;
    case 4: fl_run(m, n, c, 1, 1, gp); break;
    case 5: fl_run(m, n, c, 2, 0, gp); break;
    default: fl_run(m, n, c, 3, 0, gp); break;
    }
}
/* the crossfade: the sub-block through the old TYPE (into m) and, from the same state, the new one (into xf_d[1], its
 * own one-pole fl_op2); the state goes on from either (the stage's update does not depend on the mode); then
 * m = old + (new - old) w, w from w0 by 16 a sample */
__attribute__((noinline)) static void fl_xrun(int32_t *m, uint32_t n, const svf_t *c, uint32_t t0, uint32_t t1,
                                              int32_t gp, int32_t w0)
{
    int32_t ic0 = fl_ic[0], ic1 = fl_ic[1], op;
    memcpy(xf_d[1], m, n * sizeof *m);
    fl_pass(m, n, c, t0, gp);
    op = fl_op; fl_ic[0] = ic0; fl_ic[1] = ic1; fl_op = fl_op2;
    fl_pass(xf_d[1], n, c, t1, gp);
    fl_op2 = fl_op; fl_op = op;
    xf_blend(xf_d[1], m, n, w0, w0 + 16 * (int32_t)n);
    memcpy(m, xf_d[1], n * sizeof *m);
}
/* noinline (0.9.5): its six loops get their own registers (inlined into app_audio they reloaded the coefficients'
 * and the buffer's addresses per sample: 43-68 instructions a sample, 34-57 now) */
static __attribute__((noinline)) void fx_filter(int32_t *m, uint32_t n)
{
    static const uint8_t POLES[7] = {0, 2, 3, 2, 3, 2, 2};   /* TYPE -> poles; the mode (LP HP BP NOTCH) is in the switch below */
    uint32_t type = fx_p[FXP_FL_TYPE], inc = fx_lfo_sync_inc ? fx_lfo_sync_inc : FX_LFO_INC[fx_p[FXP_LFO_RATE]], sb, j;
    int32_t k = (int32_t)FX_FL_K[fx_p[FXP_FL_RESO]], base = fx_p[FXP_FL_FREQ] * 516, depth = fx_p[FXP_FL_LFO];
    uint32_t poles, want;
    int open;
    if (fx_lfo_retrig) { fx_lfo_retrig = 0; lfo_ph = 0; }
    if (type > 6u) type = 0;
    if (!fl_was_on) {                                    /* off: the LFO keeps time, the filter rests */
        if (!type) { lfo_ph += inc * n; return; }
        memset(fl_ic, 0, sizeof fl_ic); fl_op = 0; fl_pos = base; fl_was_on = 1; fl_ow = 0; fl_type = (uint8_t)type;
        fl_kq = k;
    }                                                    /* (switched on: from dry, the 46 ms glide in) */
    want = type; type = fl_type;
    poles = POLES[type];
    open = (type == 1u || type == 2u) && fx_p[FXP_FL_FREQ] >= 127 && !fx_p[FXP_FL_RESO] && !depth;   /* LP fully open:
                         * transparent (the default LP18 at 16 kHz trimmed the top: SNR -1 dB, TODO #5) */
    if (want != type && want && fl_ow && !fl_xw) { fl_xt = (uint8_t)want; fl_xw = 1; fl_op2 = 0; }   /* another TYPE:
                                                         * the crossfade (from dry, or open: it is taken at once below) */
    open |= want != type && !fl_xw;                      /* OFF: out to dry first, as when open */
    for (sb = 0; sb < n; sb += 32u) {
        uint32_t prev = lfo_ph, i;
        int32_t tgt, g, f, gp = 0;
        svf_t c;
        lfo_ph += inc * 32u;
        if (lfo_ph < prev) { lfo_rnd = lfo_rnd * 1664525u + 1013904223u; lfo_sh = (int32_t)(lfo_rnd >> 16) - 32768; }
        tgt = clampi(base + (int32_t)(((int64_t)lfo_value() * depth * 28932) >> 22), 0, 65535);   /* 4 octaves */
        fl_pos += clampi((tgt - fl_pos) >> 2, -FL_JMP, FL_JMP);
        i = (uint32_t)fl_pos >> 8; f = fl_pos & 255;
        g = (int32_t)FX_FL_G[i] + ((((int32_t)FX_FL_G[i + 1] - (int32_t)FX_FL_G[i]) * f) >> 8);         /* Q24 */
        if (fl_kq != k) fl_kq += clampi(k - fl_kq, -(fl_kq >> 7), fl_kq >> 7);
        svf_coef(&c, g, fl_kq);
        if (poles == 3u || (fl_xw && POLES[fl_xt] == 3u)) gp = (1 << 27) - recip27(65536u + ((uint32_t)g >> 8));   /* g/(1+g) */
        j = sb + 32u < n ? sb + 32u : n;
        if (fl_ow != 32768 || open) memcpy(fl_dry, m + sb, (j - sb) * sizeof *m);
        if (fl_xw) {                                     /* TYPE -> TYPE: the crossfade, 46 ms */
            fl_xrun(m + sb, j - sb, &c, type, fl_xt, gp, fl_xw - 1);
            fl_xw += 16 * (int32_t)(j - sb);
            if (fl_xw > 32768) { fl_type = fl_xt; type = fl_xt; poles = POLES[type]; fl_op = fl_op2; fl_xw = 0; }
        } else fl_pass(m + sb, j - sb, &c, type, gp);
        if (fl_ow != 32768 || open) {                    /* fully open: the sound passes untouched (the filter keeps
                                                         * running, its state current); in / out of it a 46 ms glide */
            int32_t w1 = open ? (fl_ow > 512 ? fl_ow - 512 : 0) : (fl_ow < 32256 ? fl_ow + 512 : 32768);
            uint32_t len = j - sb;
            if (!fl_ow && !w1) memcpy(m + sb, fl_dry, len * sizeof *m);   /* open: the dry block exactly */
            else xf_blend(m + sb, fl_dry, len, fl_ow, w1);  /* (+-16 per sample over a 32 sub-block) */
            fl_ow = w1;
        }
    }
    if (want != type && !fl_ow) {                       /* dry now: take the new TYPE (it glides in), or rest */
        if (want) fl_type = (uint8_t)want; else fl_was_on = 0;
    }
}

/* ---- DRIVE (Felucca track_dist; per TYPE drive row: gain scale, bias, make-up). Its page (0.9.3): ON / OFF, AMOUNT,
 * TONE (after the drive: a shelf at ~1.8 kHz, 0 = the highs gone (a one-pole low-pass), 64 = flat, 127 = +6 dB) and MIX
 * (dry / driven). At TONE 64 and MIX 127 the loop is the one before the page, sample for sample. TONE's gain and the
 * wet share glide (TONE 0 .. 127 in ~23 ms, MIX in ~46 ms, ON / OFF a 46 ms crossfade): no zipper, no click. */
#define DR_TA 7700                                      /* the shelf's one-pole: 1 - e^(-2 pi 1.8 kHz / fs), Q15 */
static const int16_t DR_S[4] = {16, 20, 14, 24}, DR_BIAS[4] = {2400, 2400, 1800, 3200}, DR_MU[4] = {4096, 4096, 4300, 3700};
static int32_t dr_hp, dr_lp1, dr_lp2;
static int32_t dr_gl = -1, dr_kl, dr_ml, dr_dl;        /* FDRV's gain, its low-pass and make-up the last block ran with
                                                        * (-1: none), and its AMOUNT: AMOUNT moved, it glides (at most
                                                        * DR_DSTEP a block) and they ramp over the block (0.9.6, #5:
                                                        * they stepped once a block, a zipper of 2.7x the held ones';
                                                        * the gain and the make-up ramped apart over a big step were
                                                        * +4.8 dB: the make-up matches the gain per AMOUNT only) */
#define DR_DSTEP 4
static int32_t dr_w = 32768, dr_tg = 4096, dr_tlp;      /* the wet share (Q15), the shelf's high gain (Q12), its low-pass */
static uint8_t dr_ton;                                  /* the shelf runs (its gain is not 1, or on its way) */
/* TONE and MIX settled (0.9.5): the gliding loop below with its glides at rest, so each sample does what that loop
 * does then (the shelf at its gain tg when it runs, the wet share w), in registers (that loop: 125 instructions a
 * sample, its state in memory); shelf and full are constants at each use */
/* clampi(v, -230000, 230000) as smax + smin (the same value) */
static inline int32_t dr_cl(int32_t v) { v = v > -230000 ? v : -230000; return v < 230000 ? v : 230000; }
static inline __attribute__((always_inline)) void dr_settled(int32_t *m, uint32_t n, int32_t g, int32_t k, int32_t mk,
                                                             int32_t bias, int32_t b0, int32_t tg, int32_t w,
                                                             const int shelf, const int full)
{
    int32_t hp = dr_hp, lp1 = dr_lp1, lp2 = dr_lp2, tlp = dr_tlp;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], x, y;
        hp += (x0 - hp + 64) >> 7;
        x = dr_cl(x0 - hp);
        y = softclip((int32_t)(((int64_t)(x >> 2) * g) >> 10) + bias) - b0;
        lp1 += mulq15(y - lp1, k);
        lp2 += mulq15(lp1 - lp2, k);
        y = mulq15(lp2, mk);
        if (shelf) {
            tlp += mulq15(y - tlp, DR_TA);
            y = tlp + (int32_t)(((int64_t)(y - tlp) * tg) >> 12);
        }
        m[i] = full ? y : x0 + (int32_t)(((int64_t)(y - x0) * w) >> 15);
    }
    dr_hp = hp; dr_lp1 = lp1; dr_lp2 = lp2; dr_tlp = tlp;
}
static void fx_drive_classic(int32_t *m, uint32_t n, int32_t sw)   /* FDRV; sw: FXP_DRV_SW
                                                        * (0 while it fades out for another TYPE: drive.c fx_drive) */
{
    int32_t d = fx_p[FXP_DRIVE], row = fx_p[FXP_DRIVE_ROW] & 3, g, k, mk, bias, b0, tv = fx_p[FXP_DRV_TONE], mv = fx_p[FXP_DRV_MIX];
    int32_t wt = !sw ? 0 : mv >= 127 ? 32768 : mv * 258;          /* the wet share it goes to */
    int32_t tt = tv >= 64 ? 4096 + (((tv - 64) * 4161) >> 6) : tv * 64;         /* the shelf's gain: 0 .. 1 .. 2 (Q12) */
    int32_t gq = -1, kq = 0, mq = 0, dgq = 0, dkq = 0, dmq = 0;   /* (gq >= 0: the ramp, Q8) */
    uint32_t i;
    if (!d) { wt = 0; d = 1; }                          /* AMOUNT 0: off, faded out at AMOUNT 1's settings (it cut
                                                        * to dry in one sample: AMOUNT 1 is not the dry signal, the
                                                        * high-pass, the bias and the make-up still act; the bug hunt) */
    if (!wt && !dr_w) { dr_ton = 0; dr_tg = 4096; dr_gl = -1; return; }   /* off, or MIX 0: the dry signal, untouched */
    if (dr_gl >= 0 && d != dr_dl) d = dr_dl + clampi(d - dr_dl, -DR_DSTEP, DR_DSTEP);
    dr_dl = d;
    g = 4096 + ((d * d * 2 * DR_S[row]) >> 4);
    if (d > 16) g = 4096 + (int32_t)((((int64_t)(g - 4096)) * (95 + d)) / 111);   /* above 16 the gain rises to ~18x
                                                       * at the top (was ~9x: a single note never reached the knee, TODO #4;
                                                       * ~36x was a distortion, the user wants an overdrive); <= 16 (the
                                                       * presets' low drive) exactly as before */
    k = 32000 - d * 95;
    mk = ((30000 - d * 120) * DR_MU[row]) >> 12;
    mk = (mk * (int32_t)FX_DRV_LVL[d]) >> 15;          /* level-neutral: the drive adds tone, not volume (0.6) */
    mk = (mk * 39000) >> 15;                           /* +1.5 dB: measured on the FM-1, LUFS (0.8) */
    bias = DR_BIAS[row];
    b0 = softclip(bias);
    /* the shaper's input (x >> 2) g >> 10 + bias needs a 64-bit product (|x >> 2| <= 57500, g <= 100870 at AMOUNT 127),
     * but its result fits 32 bits (<= 5.67e6 + 3200), so softclip's own 32-bit clamp to +-4 is the whole clamp (it was
     * a second, 64-bit one before it: the same value) */
    if (tt > 8192) tt = 8192;
    if (dr_gl >= 0 && (dr_gl != g || dr_kl != k || dr_ml != mk)) {   /* AMOUNT moved: the ramp, below */
        gq = dr_gl << 8; kq = dr_kl << 8; mq = dr_ml << 8;
        dgq = ((g - dr_gl) << 8) / (int32_t)n; dkq = ((k - dr_kl) << 8) / (int32_t)n; dmq = ((mk - dr_ml) << 8) / (int32_t)n;
    }
    dr_gl = g; dr_kl = k; dr_ml = mk;
    if (gq < 0 && wt == 32768 && dr_w == 32768 && tt == 4096 && !dr_ton) {   /* the defaults: the loop before the page */
        int32_t hp = dr_hp, lp1 = dr_lp1, lp2 = dr_lp2, *e = m + n;   /* (0.9.5: the state in locals) */
        for (; m < e; m++) {
            int32_t x = *m, y;
            hp += (x - hp + 64) >> 7;
            x = dr_cl(x - hp);
            y = softclip((int32_t)(((int64_t)(x >> 2) * g) >> 10) + bias) - b0;
            lp1 += mulq15(y - lp1, k);
            lp2 += mulq15(lp1 - lp2, k);
            *m = mulq15(lp2, mk);
        }
        dr_hp = hp; dr_lp1 = lp1; dr_lp2 = lp2;
        return;
    }
    if (gq < 0 && dr_tg == tt && dr_w == wt && (dr_ton ? tt != 4096 : tt == 4096)) {   /* settled: the glides at rest, the
                                                        * shelf running (and staying on) or resting (dr_tg 4096) */
        if (!dr_ton) dr_settled(m, n, g, k, mk, bias, b0, tt, wt, 0, 0);   /* (MIX alone: all wet is the defaults) */
        else if (wt == 32768) dr_settled(m, n, g, k, mk, bias, b0, tt, wt, 1, 1);
        else dr_settled(m, n, g, k, mk, bias, b0, tt, wt, 1, 0);
        return;
    }
    if (gq < 0) { gq = g << 8; kq = k << 8; mq = mk << 8; }   /* (no ramp: the steps are 0) */
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], x, y;
        gq += dgq; kq += dkq; mq += dmq;
        g = gq >> 8; k = kq >> 8; mk = mq >> 8;
        dr_hp += (x0 - dr_hp + 64) >> 7;
        x = dr_cl(x0 - dr_hp);
        y = softclip((int32_t)(((int64_t)(x >> 2) * g) >> 10) + bias) - b0;
        dr_lp1 += mulq15(y - dr_lp1, k);
        dr_lp2 += mulq15(dr_lp1 - dr_lp2, k);
        y = mulq15(dr_lp2, mk);
        if (dr_ton || tt != 4096) {                     /* TONE: lows + the highs x gain (lp + g (y - lp)) */
            if (!dr_ton) { dr_tlp = y; dr_ton = 1; }    /* (from the signal itself: no step when it starts) */
            dr_tg += dr_tg < tt ? (tt - dr_tg > 8 ? 8 : tt - dr_tg) : -(dr_tg - tt > 8 ? 8 : dr_tg - tt);
            dr_tlp += mulq15(y - dr_tlp, DR_TA);
            y = dr_tlp + (int32_t)(((int64_t)(y - dr_tlp) * dr_tg) >> 12);
            if (dr_tg == 4096 && tt == 4096) dr_ton = 0;   /* back to flat: the shelf rests (y is exact again) */
        }
        if (dr_w != wt) dr_w += dr_w < wt ? (wt - dr_w > 16 ? 16 : wt - dr_w) : -(dr_w - wt > 16 ? 16 : dr_w - wt);
        m[i] = dr_w == 32768 ? y : x0 + (int32_t)(((int64_t)(y - x0) * dr_w) >> 15);
    }
}
#include "drive.c"                                      /* the drive's TYPEs and fx_drive */

/* ---- TREMOLO (autopan / amplitude) and touch WAH; mono in, stereo out */
static uint32_t tw_ph;
static int32_t tw_gl = 32768, tw_gr = 32768, wah_env, wah_lp, wah_bp;
static int32_t wah_ein, wah_eout, wah_g = 4096, wah_pi, wah_po;   /* the wah's make-up: loudness envelopes of the
                                                 * input and the output (pre-emphasised), the gain (Q12), last samples */
static int32_t wah_eslow, wah_g1 = 4096;        /* the input's slow envelope (onsets), the gain's first smoothing pole */
/* v / 10, truncated as C's division, for every int32 v: a multiply and a shift instead of the core's divide in the wah's
 * sample loops (0.9.5 cpu-trim; checked exhaustively over all 2^32 values) */
static inline __attribute__((always_inline)) int32_t div10(int32_t v) { return (int32_t)(((int64_t)v * 1717986919) >> 34) + (int32_t)((uint32_t)v >> 31); }
/* the wah, the tremolo's gains settled at unity: fx_trem's loop, bit for bit, with the state in registers (no reload
 * past the L / R stores) and L / R = x ((x * 32768) >> 15), onto the cleared fx_L / fx_R. Its own function: its
 * registers are its own (inlined into app_audio it spilled more, 86 -> 91 instructions a sample) */
FR_RAM __attribute__((noinline)) static void fx_wah_unity(const int32_t *mp, const int32_t *me, int32_t *lo, int32_t *ro,
                                                   int32_t wgq, int32_t wgs, int32_t depth, int32_t rate)
{
    int32_t env = wah_env, lp = wah_lp, bp = wah_bp, ei = wah_ein, eo = wah_eout, es = wah_eslow;
    int32_t xi = wah_pi, xo = wah_po, q = 9830 - rate * 58, dk = depth + 16;
    int32_t et = (4194304 + dk - 1) / dk;       /* (env x dk) >> 16 reaches row 63 from env >= et: below
                                                 * it the product fits 32 bits */
    while (mp < me) {
        int32_t xin = *mp++, x, a = xin < 0 ? -xin : xin, f, hi, pi, po, vi, vo;
        if (a > env) env += (a - env) >> 3; else env -= (env >> 11) + 1;
        if (env < 0) env = 0;
        f = FX_WAH_F[env >= et ? 63 : (env * dk) >> 16];   /* env >= 0, dk > 0 */
        hi = xin - lp - ((q * bp) >> 14);
        bp += (f * (hi >> 1)) >> 13;
        lp += (f * (bp >> 1)) >> 13;
        x = div10(9 * ((bp >> 2) * q >> 11) + 4 * lp);
        pi = xin - ((3 * xi) >> 2); po = x - ((3 * xo) >> 2);
        vi = (pi < 0 ? -pi : pi) << 8; vo = (po < 0 ? -po : po) << 8;
        xi = xin; xo = x;
        ei += (vi - ei) >> (vi > ei ? 7 : 9);
        eo += (vo - eo) >> (vo > eo ? 7 : 9);
        es += (vi - es) >> 11;
        wgq += wgs;
        x = (int32_t)(((int64_t)x * (wgq >> 5)) >> 12);
        *ro++ = x; *lo++ = x;                         /* (= x: fx_process clears fx_L / fx_R and the wah is their
                                                        * only writer before this; R first: 90 instructions a sample,
                                                        * 94 with L / R += x, 102 with L first) */
    }
    wah_env = env; wah_lp = lp; wah_bp = bp; wah_ein = ei; wah_eout = eo; wah_eslow = es;
    wah_pi = xi; wah_po = xo;
}
/* the amp law (SHAPE 65..127, 0.9.5): an amp's tremolo rather than a sine on the volume. Its amount a (0 at SHAPE 64,
 * 1 at 127) brings in, together: (1) the LFO's fall from the top to the dip takes 50 -> 65 % of the cycle, the rise
 * back the rest (a piecewise-linear phase warp of the sine: the dip is slower than the rise); (2) the gain moves evenly
 * in dB between unity and the dip's floor (FX_TREM_LT: the linear law's own floor, held at -30 dB, so a deep setting
 * breathes instead of chopping to silence), blended with the linear law by a, with FX_TREM_MK's make-up (the same
 * mean power: no level step when SHAPE moves into the region); (3) a touch of level-dependent depth: up to 25 % less
 * depth at full-scale output (the power stage compressing the swing), from the last blocks' peak (tw_lv, ~190 ms).
 * The squaring of SHAPE 0..64 eases back to the plain sine over the region. Everything is per sub-block (the samples'
 * loop and its gain ramp are as before), so nothing in it can alias; SHAPE 0..64 is bit for bit as before. */
static int32_t tw_lv, tw_amp, tw_dpt, tw_shp;
static uint32_t tw_fq, tw_r1, tw_r2;
static int32_t tw_exp2n(int32_t y)                       /* 2^(-y / 4096) in Q15, y in 0 .. 5 x 4096 */
{
    int32_t k = y >> 12, e = 4096 - (y & 4095);          /* 2^(-k - fr) = 2^(1 - fr) / 2^(k + 1) */
    int32_t mq = e == 4096 ? 131072 : (int32_t)FX_EXP2F[e >> 4];   /* Q16 */
    return mq >> (k + 2);
}
static void tw_amp_block(int32_t shp, int32_t depth)   /* this block's warp and depth */
{
    int32_t pk = fx_peak > 32767 ? 32767 : fx_peak, a = (shp - 64) * 520;   /* a: Q15, 520 .. 32760 */
    tw_amp = a;
    tw_fq = 0x80000000u + (uint32_t)a * 19661u;      /* the fall's share of the cycle: 0.5 + 0.15 a (Q32) */
    tw_r1 = 0x80000000u / (tw_fq >> 15);             /* 0.5 / share, Q15 */
    tw_r2 = 0x80000000u / ((0u - tw_fq) >> 15);      /* 0.5 / (1 - share), Q15 */
    tw_lv += (pk - tw_lv) >> 5;
    tw_dpt = depth - ((((depth * tw_lv) >> 15) * a) >> 17);   /* up to depth / 4 less at full scale */
    tw_shp = ((127 - shp) * 65) >> 6;                /* the squaring eases back: 62 at 65 .. 0 at 127 */
}
static int32_t tw_amp_sub(uint32_t ph, int32_t *tr)   /* a sub-block's gains (AM, PAN) */
{
    uint32_t u = ph - (1u << 30);                    /* the warp: the top (phase 1/4) to the dip (3/4) over tw_fq */
    int32_t lfo, d = tw_dpt, g, ge;
    ph = u < tw_fq ? (1u << 30) + (u >> 15) * tw_r1 : 0xC0000000u + ((u - tw_fq) >> 15) * tw_r2;
    lfo = osc_sine(ph);
    lfo = clampi((int32_t)(((int64_t)lfo * (32768 + 7 * 258 * tw_shp)) >> 15), -32767, 32767);
    if (fx_p[FXP_TW_MODE] == 0) {                    /* PAN: as in fx_trem, with the warp and the depth */
        uint32_t th = (1u << 29) + (uint32_t)((d * lfo) * 129);
        *tr = (osc_sine(th) * 46341) >> 15;
        return (osc_sine(th + (1u << 30)) * 46341) >> 15;
    }
    g = 32768 - (((d * 258) * ((32767 - lfo) >> 1)) >> 15);
    g = (g * (254 + d)) / 254;                       /* the linear law, as in fx_trem */
    ge = tw_exp2n((FX_TREM_LT[d] * ((32767 - lfo) >> 1)) >> 15);   /* even in dB, blended in by a */
    ge = (ge * FX_TREM_MK[d]) >> 12;
    return *tr = g + (int32_t)(((int64_t)(ge - g) * tw_amp) >> 15);
}
/* the tremolo with the amp law: fx_trem's sw == 1 path (its gain ramp and L / R sums, bit for bit) with these gains.
 * Its own function, so fx_trem's own loops compile as before */
__attribute__((noinline)) static void fx_trem_amp(const int32_t *m, int32_t *L, int32_t *R, uint32_t n, uint32_t inc)
{
    uint32_t sb;
    tw_amp_block(fx_p[FXP_TW_SHAPE], fx_p[FXP_TW_DEPTH]);
    for (sb = 0; sb < n; sb += 32u) {
        int32_t tr, tl = tw_amp_sub(tw_ph, &tr), gl = tw_gl, gr = tw_gr, dl = (tl - gl) >> 5, dr = (tr - gr) >> 5;
        const int32_t *mp = m + sb, *me = m + (sb + 32u < n ? sb + 32u : n);
        int32_t *lo = L + sb, *ro = R + sb;
        tw_ph += inc * 32u;
        while (mp < me) {
            int32_t x = *mp++;
            gl += dl; gr += dr;
            *lo++ += (int32_t)(((int64_t)x * gl) >> 15);
            *ro++ += (int32_t)(((int64_t)x * gr) >> 15);
        }
        tw_gl = tl; tw_gr = tr;
    }
}
/* noinline (0.9.5): with the filter out of app_audio and the drive / limiter loops slimmer, the allocator moved the
 * phaser's loop 77 -> 84 instructions a sample; the tremolo in its own function puts it at 78 (and the rest back) */
static __attribute__((noinline)) void fx_trem_k(const int32_t *m, int32_t *L, int32_t *R, uint32_t n, int32_t sw)
{
    int32_t depth = fx_p[FXP_TW_DEPTH], rate = fx_p[FXP_TW_RATE];
    uint32_t inc = fx_tw_sync_inc ? fx_tw_sync_inc : FX_LFO_INC[rate], sb, j;
    if (sw == 1 && fx_p[FXP_TW_SHAPE] > 64) { fx_trem_amp(m, L, R, n, inc); return; }   /* the amp law */
    for (sb = 0; sb < n; sb += 32u) {
        int32_t tl = 32768, tr = 32768, dl, dr, gl = tw_gl, gr = tw_gr, wgq = wah_g << 5, wgs = 0;
        if (sw == 1) {                                  /* tremolo: the LFO this sub-block */
            int32_t lfo = osc_sine(tw_ph), shp = fx_p[FXP_TW_SHAPE];
            lfo = clampi((int32_t)(((int64_t)lfo * (32768 + 7 * 258 * shp)) >> 15), -32767, 32767);
            /* sine -> rounded square (x up to 8: 64-bit, 0.9.3: in 32 bits it overflowed above SHAPE 18) */
            if (fx_p[FXP_TW_MODE] == 0) {               /* autopan: equal power around the centre */
                uint32_t th = (1u << 29) + (uint32_t)((depth * lfo) * 129);
                tl = (osc_sine(th + (1u << 30)) * 46341) >> 15;   /* sqrt2 cos(theta): unity at the centre */
                tr = (osc_sine(th) * 46341) >> 15;
            } else {                                     /* amplitude: down from full by depth */
                int32_t g = 32768 - (((depth * 258) * ((32767 - lfo) >> 1)) >> 15);
                tl = tr = (g * (254 + depth)) / 254;     /* the average dip, given back (0.6) */
            }
        }
        tw_ph += inc * 32u;
        if (sw == 2) {                                  /* the wah's make-up gain (0.9.3): the band-pass kept its peak,
                                                         * but where the chord sat against the band moved the level
                                                         * -14.6 .. +5.7 LU (high chords lost most); the output's slow
                                                         * envelope now follows the input's, so the wah only colours */
            /* 2026-10-24: the envelopes follow in 2.9 ms (up) / 11.6 ms (down) (they were 46 ms: a decaying high
             * note's level fell behind them, Rhodes C6 v60 RATE 127 -2.9 LU), the gain is smoothed by two poles of
             * 5.8 ms (2.9 ms at an onset: the input 4x over its 46 ms envelope) and ramped sample by sample (no
             * zipper), at most x32 (x8 cut a closed resonant wah on a high note: C7 RATE 127 -10 LU). Faster still
             * holds MARIMBA's top octave too, but only by boosting the strike (its peaks +7 dB) and pumping on low
             * chords: not taken. */
            int32_t ei = wah_ein, eo = wah_eout, tg = 4096, gs = wah_ein > 4 * wah_eslow ? 2 : 3;
            while (ei > 0x7FFFF || eo > 0x7FFFF) { ei >>= 1; eo >>= 1; }
            if (eo > 8) tg = clampi((ei << 12) / eo, 512, 131072);   /* x0.125 .. x32 */
            wah_g1 += (tg - wah_g1) >> gs;
            wah_g += (wah_g1 - wah_g) >> gs;
            wgs = wah_g - (wgq >> 5);                    /* the gain ramps over the sub-block: no zipper */
        }
        dl = (tl - gl) >> 5; dr = (tr - gr) >> 5;
        if (sw == 2 && gl == 32768 && gr == 32768) {    /* the wah, the tremolo's gains at unity (fx_wah_unity) */
            FR_FAR(fx_wah_unity)(m + sb, m + (sb + 32u < n ? sb + 32u : n), L + sb, R + sb, wgq, wgs, depth, rate);
            continue;                                    /* (tw_gl / tw_gr: 32768 already) */
        }
        if (sw == 0 && gl == 32768 && gr == 32768) {    /* TREM/WAH off, the gains settled: the loop below is
                                                         * L / R += x, bit for bit */
            const int32_t *mp = m + sb, *me = m + (sb + 32u < n ? sb + 32u : n);
            int32_t *lo = L + sb, *ro = R + sb;
            while (mp < me) { int32_t x = *mp++; *lo++ += x; *ro++ += x; }
            continue;
        }
        for (j = sb; j < sb + 32u && j < n; j++) {
            int32_t x = m[j];
            if (sw == 2) {                               /* touch wah: envelope -> SVF cutoff, RATE knob = resonance */
                int32_t a = x < 0 ? -x : x, row, f, q, hi;
                if (a > wah_env) wah_env += (a - wah_env) >> 3; else wah_env -= (wah_env >> 11) + 1;
                if (wah_env < 0) wah_env = 0;
                row = clampi((int32_t)(((int64_t)wah_env * (depth + 16)) >> 16), 0, 63);
                f = FX_WAH_F[row];
                q = 9830 - rate * 58;                    /* Q14 damping: 0.6 .. 0.15 (resonant) */
                hi = x - wah_lp - ((q * wah_bp) >> 14);
                wah_bp += (f * (hi >> 1)) >> 13;
                wah_lp += (f * (wah_bp >> 1)) >> 13;
                x = div10(9 * ((wah_bp >> 2) * q >> 11) + 4 * wah_lp);   /* bp x 2q: the resonant peak's gain held,
                                                                         * louder playing no longer gets louder (0.7) */
                {   /* envelopes of the input and the filtered output, each pre-emphasised (x - 3/4 x[-1]: +12 dB
                     * from the bass to the treble, roughly the ear's weighting) so a closed wah's rumble does not
                     * count as loudness */
                    int32_t pi = m[j] - ((3 * wah_pi) >> 2), po = x - ((3 * wah_po) >> 2);
                    int32_t vi = (pi < 0 ? -pi : pi) << 8, vo = (po < 0 ? -po : po) << 8;   /* x 2^8: a Q15 level's */
                    wah_pi = m[j]; wah_po = x;                                              /* steps survive the >> */
                    wah_ein += (vi - wah_ein) >> (vi > wah_ein ? 7 : 9);      /* 2.9 ms up, 11.6 ms down */
                    wah_eout += (vo - wah_eout) >> (vo > wah_eout ? 7 : 9);
                    wah_eslow += (vi - wah_eslow) >> 11;
                }
                wgq += wgs;
                x = (int32_t)(((int64_t)x * (wgq >> 5)) >> 12);
            }
            gl += dl; gr += dr;
            L[j] += (int32_t)(((int64_t)x * gl) >> 15);
            R[j] += (int32_t)(((int64_t)x * gr) >> 15);
        }
        tw_gl = tl; tw_gr = tr;
    }
}
/* the wah switched on or off (0.9.6, the bug hunt: it switched from the plain signal to the filtered one in one sample,
 * with the filter's stale state): the wah runs while its share glides (xf_next), over the plain signal (the tremolo's
 * gains at unity, its output m); from rest it starts from a clean state and unity make-up. TREM <-> WAH (BUGHUNT #7's
 * other half: the tremolo's dip stepped out at once, 16.5x a held chord's d2): the other side of the blend is the
 * tremolo itself (tw_o, the last setting that was not the wah), into xf_d; the wah runs at unity gains (fx_wah_unity)
 * and its LFO phase is the tremolo's (the wah's pass does not move it) */
static int32_t wah_xw;
static uint8_t tw_o;
static void fx_trem_x(const int32_t *m, int32_t *L, int32_t *R, uint32_t n, int32_t sw)
{
    int32_t w1, gl = tw_gl, gr = tw_gr;
    uint32_t ph = tw_ph;
    if (!wah_xw) {
        wah_env = wah_lp = wah_bp = 0; wah_ein = wah_eout = wah_eslow = 0; wah_pi = wah_po = 0;
        wah_g = wah_g1 = 4096;
    }
    w1 = xf_next(wah_xw, sw == 2, n);   /* (never at rest here: the blend always runs) */
    if (tw_o) tw_gl = tw_gr = 32768;
    fx_trem_k(m, L, R, n, 2);                           /* (L / R = the wah: fx_process clears them, the wah writes) */
    if (!tw_o) xf_post(L, R, m, m, n, wah_xw, w1);
    else {                                              /* the tremolo, from its own gains and phase */
        memset(xf_d, 0, sizeof xf_d);
        tw_gl = gl; tw_gr = gr; tw_ph = ph;
        fx_trem_k(m, xf_d[0], xf_d[1], n, 1);
        xf_post(L, R, xf_d[0], xf_d[1], n, wah_xw, w1);
        if (w1 == 32768) tw_gl = tw_gr = 32768;         /* (the wah alone from here: its gains at unity) */
    }
    wah_xw = w1;
}
static void fx_trem(const int32_t *m, int32_t *L, int32_t *R, uint32_t n)
{
    int32_t sw = fx_p[FXP_TW_SW];
    if (sw != 2) tw_o = (uint8_t)(sw == 1);
    if (sw == 2 ? wah_xw == 32768 : !wah_xw) fx_trem_k(m, L, R, n, sw);   /* at rest: as before */
    else fx_trem_x(m, L, R, n, sw);                     /* (the blend: out of app_audio) */
}

/* ---- PHASER: 4 first-order allpasses per side, the right one 30 degrees behind */
static uint32_t ph_ph;
static struct { int32_t x[2][4], y[2][4], fb[2], hp[2], ls[2]; } ph_st;   /* (one struct: one memset clears it) */
#define ph_x ph_st.x
#define ph_y ph_st.y
#define ph_fb ph_st.fb
#define ph_hp ph_st.hp
#define ph_ls ph_st.ls
static int32_t ph_cq = -1, ph_gq;                     /* the sweep's centre and the level, gliding (-1: not yet) */
static int32_t ph_dq, ph_fq;                            /* DEPTH's sweep width (Q8) and FDBK's gain, gliding (they
                                                         * stepped once a block: FDBK 0 <-> 127 13-20x a held one's d2,
                                                         * the bug hunt #16; F09 -> F01 12x) */
static int32_t ph_w;                                    /* the on / off crossfade's wet share (xf_next) */
static int32_t ph_coef(uint32_t ph, int32_t depth, int32_t c)
{
    int32_t q = (int32_t)(ph < 0x80000000u ? ph >> 16 : (0xFFFFFFFFu - ph) >> 16);   /* triangle 0..32767 */
    /* the sweep's centre: from DEPTH 64 up the middle of the range, as always; below 64 it moves up as the sweep
     * narrows, to the top at DEPTH 0 (the notches near 2 kHz and 11.6 kHz, above most of the Rhodes' body). Parked
     * in the middle, a narrow sweep was a fixed notch near 350 Hz and 2 kHz: the chord -4.3 LU, A4 -7.7 dB at DEPTH 0
     * (the parameter's default), the audit 2026-10-25 */
    int32_t qd = c + (((q - 16384) * depth) >> 15), i, f;   /* (depth: Q8) */
    qd = clampi(qd, 0, 32767);
    i = qd >> 10; f = qd & 1023;
    return FX_PH_A[i] + (((FX_PH_A[i + 1] - FX_PH_A[i]) * f) >> 10);
}
/* (0.9.5, the CPU: the same bits, fewer instructions; tools: an old / new harness over random settings and inputs)
 * PH_CL: clampi as max, then min (lo <= hi: the same value), two instructions (smax, smin) instead of a compare, a
 * branch and the bounds rebuilt in it. PH_AP: an allpass's (a * d + 8192) >> 14 as ((a * d >> 13) + 1) >> 1, the
 * same floor (|a| < 2^14, |d| < 2^22: a * d >> 13 fits int32), without the rounding constant's register pair.
 * FX_KEEP(v): v is "changed" here (an empty asm), so the compiler keeps it in a 32-bit register and multiplies
 * 32 x 32 -> 64 in one instruction (left alone, it widens a loop constant to 64 bits once: see FX_K32). */
static inline __attribute__((always_inline)) int32_t ph_cl(int32_t v, int32_t lo, int32_t hi) { v = v < lo ? lo : v; return v > hi ? hi : v; }
#define PH_CL(v, lo, hi) ph_cl(v, lo, hi)
#define PH_AP(a, d) (((int32_t)(((int64_t)(a) * (d)) >> 13) + 1) >> 1)
#ifdef DINGHY_HOST
#define FX_KEEP(v) ((void)(v))                          /* the host / the browser: no hint (the same bits) */
#else
#include "fm1_keep.h"                                   /* hal/: the empty asm (tools/build.py: no asm in src/) */
#define FX_KEEP(v) HAL_KEEP_REG(v)
#endif
/* one sub-block's settings: the centre and the level glide (a DEPTH step moved the centre a quarter of a table step
 * at once: a step in the allpasses' coefficient, 5x the LFO's own per sub-block, a zipper of 3.5x a static DEPTH's
 * largest jump): at most 32 (centre) and 4 (level, 0.1 %) a sub-block, the whole range in ~0.4 s; DEPTH's width and
 * FDBK too. Returns the mix gain; the shelf's depth into ksh, the sides' coefficients into a (out of fx_phaser:
 * one copy, less flash) */
__attribute__((noinline)) static int32_t ph_glide(int32_t ct, int32_t gt, int32_t dt, int32_t fbk, int32_t *ksh,
                                                  int32_t *a)
{
    ph_cq += PH_CL(ct - ph_cq, -32, 32);
    ph_gq += PH_CL(gt - ph_gq, -4, 4);
    ph_dq += PH_CL(dt - ph_dq, -64, 64);                /* (the whole range in ~0.37 s) */
    ph_fq += PH_CL(fbk - ph_fq, -64, 64);               /* (~0.26 s) */
    *ksh = 32768 - (23268 * 4096) / ph_gq;              /* the shelf: x 0.71 on the bass at the level 1 (9500) */
    a[0] = ph_coef(ph_ph, ph_dq, ph_cq);
    a[1] = ph_coef(ph_ph + 0x15555555u, ph_dq, ph_cq);  /* the right side 30 degrees behind (was 90: a mono sound
                                                         * turned very wide, heard as a jump in level, the user
                                                         * 2026-10-17) */
    return (int32_t)(((int64_t)(22940 << 15) * ph_gq) >> 12);
}
static void fx_phaser(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t depth = fx_p[FXP_PH_DEPTH], fbk = fx_p[FXP_PH_FDBK] * 180;
    /* the mix: 0.70 x (dry + allpasses): +3 dB at most where they are in phase, the notches cut (0.56, a cut-only
     * 50/50 mix, came out 2-5 LU quieter on the FM-1 and dropped the phaser presets). It was 0.707 x a depth law fitted on the FM-1 by LUFS (0.8): 0.81 at DEPTH 80, so a low note (the
     * notches above it) came out +4 LU, the bass +6 dB, and the feedback resonated on the bass: heard as a jump in
     * level when switched on (the user, 2026-10-17). The feedback now passes a DC block and the sides are 30 degrees
     * apart (they were 90: a mono sound turned very wide). */
    /* the level per DEPTH (Q12, knots every 16, the last at 127): the chord's loudness (LUFS) matched to the phaser
     * off at every DEPTH, within 0.1 LU (fitted on host renders; tests/py/test_mix_levels.py); the low shelf's depth follows it (ksh), so
     * the bass keeps its level too (C2 within +1.5 dB). The mix was a fixed 0.70: the chord -2.1 LU at DEPTH 64 */
    static const int16_t PH_G[9] = {2618, 2935, 3614, 4797, 5481, 4990, 4633, 4376, 4182};
    int32_t gt = PH_G[depth >> 4] + (((PH_G[(depth >> 4) + 1] - PH_G[depth >> 4]) * (depth & 15)) >> 4);
    int32_t ct = 16384 + (depth < 64 ? (64 - depth) * 256 - (64 - depth) / 64 : 0);   /* (DEPTH 0: 32767) */
    uint32_t inc = fx_ph_sync_inc ? fx_ph_sync_inc : FX_LFO_INC[fx_p[FXP_PH_SPEED]] >> 1, sb, s;
    int on = fx_p[FXP_PH_SW] != 0;
    int32_t w1;
    if (!on && !ph_w) { ph_cq = -1; ph_ph += inc * n; return; }   /* off and faded out: the phaser rests, its LFO
                                                         * keeps time (as the filter's): switched on, it is where a phaser
                                                         * that ran all along is (it resumed where it stopped: chorus ->
                                                         * phaser +4.2 dB over both held renders, the control sweep) */
    if (!ph_w) {                                        /* switched on from rest: the allpasses start from 0 (their
                                                         * states were stale), under the fade in */
        memset(&ph_st, 0, sizeof ph_st);
    }
    if (ph_cq < 0) { ph_cq = ct; ph_gq = gt; ph_dq = depth << 8; ph_fq = fbk; }   /* (switched on: at once) */
    w1 = xf_pre(ph_w, on, L, R, n);
    for (sb = 0; sb < n; sb += 32u) {
        int32_t a[2], ksh, pg = ph_glide(ct, gt, depth << 8, fbk, &ksh, a);
        for (s = 0; s < 2u; s++) {                   /* the sides are independent: one at a time, the state in
                                                        * registers (the same arithmetic as per sample and side) */
            int32_t *io = s ? R : L, *pe, hp = ph_hp[s], g13 = pg >> 16, ls = ph_ls[s];
            int32_t kas = a[s], kfb = ph_fq;
            int32_t x0 = ph_x[s][0], y0 = ph_y[s][0], y1 = ph_y[s][1], y2 = ph_y[s][2], y3 = ph_y[s][3];
            /* fewer registers (they spilled), the same values: an allpass's last input is the one before's last output
             * (x1 = y0, x2 = y1, x3 = y2, always: they start at 0 and take the same clamped value each sample), so
             * only x0 is kept; the feedback is clamp(y3 - hp) of the last sample, computed where it is used */
            pe = io + (sb + 32u < n ? sb + 32u : n);
            for (io += sb; io < pe; io++) {             /* at 4x the bus (it ran at a quarter: x >> 2 in, the allpasses'
                                                         * rounding x 4 out: a broadband floor ~55 dB under the note,
                                                         * heard as hiss / air and "more volume", TODO #7); the products
                                                         * 32 x 32 -> 64, one instruction each */
                int32_t x = *io, xs = PH_CL(x, -65535, 65535) << 2;   /* x 4 inside: 2 more bits */
                FX_KEEP(kas); FX_KEEP(kfb);
                int32_t fb = PH_CL(y3 - hp, -524287, 524287);         /* the feedback without its lows */
                int32_t u = xs + (int32_t)(((int64_t)fb * kfb) >> 15), t0, t1, t2, y;
                t0 = PH_CL(x0 + PH_AP(kas, u - y0), -1048575, 1048575); x0 = u;
                t1 = PH_CL(y0 + PH_AP(kas, t0 - y1), -1048575, 1048575); y0 = t0;
                t2 = PH_CL(y1 + PH_AP(kas, t1 - y2), -1048575, 1048575); y1 = t1;
                y = PH_CL(y2 + PH_AP(kas, t2 - y3), -1048575, 1048575); y2 = t2; y3 = y;
                hp += (y - hp) >> 6;                    /* (~110 Hz: it resonated on the bass, C2 +4 LU with the
                                                         * phaser on) */
                {   /* 0.70 x (dry + allpasses), / 4; then a low shelf: below ~150 Hz the allpasses are in phase
                     * with the dry, the sum +3 dB on the low notes (C2 / C3 heard ~25 % louder with the phaser on,
                     * Zwicker loudness, TODO #7): x 0.71 down there, the notches and the sweep above untouched */
                    int32_t o = (int32_t)(((int64_t)(xs + y) * g13) >> 16);
                    ls += (int32_t)(((int64_t)(o - ls) * 700) >> 15);
                    *io = o - (int32_t)(((int64_t)ls * ksh) >> 15);
                }
            }
            ph_x[s][0] = x0; ph_x[s][1] = y0; ph_x[s][2] = y1; ph_x[s][3] = y2;
            ph_y[s][0] = y0; ph_y[s][1] = y1; ph_y[s][2] = y2; ph_y[s][3] = y3;
            ph_fb[s] = PH_CL(y3 - hp, -524287, 524287); ph_hp[s] = hp; ph_ls[s] = ls;
        }
        ph_ph += inc * 32u;
    }
    xf_post(L, R, xf_d[0], xf_d[1], n, ph_w, w1);
    ph_w = w1;
}

/* ---- CHORUS: 7 ms +- 3.5 ms, two taps 90 degrees apart */
static uint32_t cp_ph;
static int32_t cho_buf[1025] POOL;                     /* full precision (it was int16 of x / 2: a noise floor
                                                         * ~65 dB under the note, TODO #7); [1024]: a copy of [0] */
static uint32_t cho_w;
#ifndef CHO_HPS
#define CHO_HPS 5                                       /* the wet's high-pass: a one-pole, 2^-5 a sample (~220 Hz) */
#endif
static int32_t cho_xw;                                  /* the on / off crossfade's wet share (xf_next) */
static uint8_t cho_fill, cho_run;                       /* blocks still to fill the line before the fade in; it runs */
static int32_t cho_el = -1, cho_er, cho_km, cho_cg, cho_hl[2];   /* the taps' end positions (Q8; -1: none yet), the
                                                         * last sub-block's mix and make-up, the wet's high-pass */
/* one side's tap over x[0 .. k): v the tap's Q13 position in the (backwards) line, stepping by e a sample; the mix
 * (km) and the comb's make-up (cg) step by dk / dc a sample in Q5 (ramp = 1: they move from the last sub-block's to
 * this one's over its 32 samples; 0: they are constant, the cheaper loop). Only the tap's part above ~220 Hz is mixed
 * in: (t - l) is high-passed (hl the low part), so the dry's bass is never cancelled by the 7 ms tap's comb (its first
 * notch sits near 71 Hz: C2 -10 dB at DEPTH 64, the audit 2026-10-25) */
static inline __attribute__((always_inline)) int32_t cho_tap_k(int32_t *x, uint32_t k, uint32_t v, uint32_t e,
                                                              int32_t km, int32_t cg, int32_t dk, int32_t dc,
                                                              int32_t hl, const int ramp)
{
    int32_t *xe = x + k;
    if (ramp) { km <<= 5; cg <<= 5; }
    for (; x < xe; x++) {
        const int32_t *a = cho_buf + ((v >> 13) & 1023u);
        int32_t l = *x, f = (int32_t)((v >> 5) & 255u), t = a[0] + (((a[1] - a[0]) * f) >> 8), d;   /* (|a[1] - a[0]|
                                                         * < 2^21: x 255 fits 32 bits) */
        int32_t kq, cq;
        FX_KEEP(km);
        v += e;
        d = t - l;
        hl += (d - hl) >> CHO_HPS;                      /* (|d| < 2^22: the low part, its own state per side) */
        if (ramp) { km += dk; cg += dc; kq = km >> 5; cq = cg >> 5; } else { kq = km; cq = cg; }
        l += (int32_t)(((int64_t)(d - hl) * kq) >> 15); /* (t - l) high-passed, x mix: 64-bit (it overflowed from
                                                         * |t - l| 131072) */
        *x = l + (int32_t)(((int64_t)l * cq) >> 15);    /* the comb's loss, minus the stereo width's loudness (0.8); */
    }                                                   /* (= (l * (32768 + cg)) >> 15 exactly, without its overflow;
                                                         * 64-bit: l passed 2^31 / cg at a VIBES fuzz seed's level) */
    return hl;
}
FR_RAM __attribute__((noinline)) static int32_t cho_tap(int32_t *x, uint32_t k, uint32_t v, uint32_t e, int32_t km,
                                                       int32_t cg, int32_t hl)   /* (its own registers: inlined in
                                                        * fx_delay it spilled, 25 / 23 instructions a sample) */
{
    return cho_tap_k(x, k, v, e, km, cg, 0, 0, hl, 0);
}
__attribute__((noinline)) static int32_t cho_tap_r(int32_t *x, uint32_t k, uint32_t v, uint32_t e, int32_t km,
                                                  int32_t cg, int32_t dk, int32_t dc, int32_t hl)   /* (DEPTH moving) */
{
    return cho_tap_k(x, k, v, e, km, cg, dk, dc, hl, 1);
}
__attribute__((noinline)) static int32_t cho_pos(uint32_t ph, int32_t sw)   /* a tap's position (Q8) at the LFO's ph */
{
    return 308 * 256 + ((osc_sine(ph) * sw) >> 7);
}
static void fx_chorus(int32_t *L, int32_t *R, uint32_t n)   /* (in fx_delay's function: see there) */
{
    int32_t depth = fx_p[FXP_CP_DEPTH], sw = 154 * depth / 127;
    uint32_t inc = fx_cp_sync_inc ? fx_cp_sync_inc : FX_LFO_INC[fx_p[FXP_CP_SPEED]] >> 1, sb;
    int on = fx_p[FXP_CP_SW] != 0;
    int32_t w1;
    if (!on && !cho_xw) { cho_el = -1; cho_km = cho_cg = 0; cho_fill = 0; cho_run = 0; return; }   /* off and faded out: rests (switched on
                                                         * again: the taps start where the LFO is, the mix from dry) */
    if (!cho_run) { memset(cho_buf, 0, sizeof cho_buf); cho_hl[0] = cho_hl[1] = 0; cho_fill = 2; cho_run = 1; }
                                                        /* (from rest: the line held what it held when it stopped;
                                                         * cleared, then two blocks dry while it fills: the taps read
                                                         * 154 .. 463 samples back) */
    w1 = xf_pre(cho_xw, on && !cho_fill, L, R, n);     /* (filling: cho_xw is 0, it stays 0) */
    if (cho_fill) cho_fill--;
    if (cho_el < 0) { cho_el = cho_pos(cp_ph, sw); cho_er = cho_pos(cp_ph + 0x40000000u, sw); }   /* (the taps' start) */
    for (sb = 0; sb < n; sb += 32u) {
        /* the taps glide sample by sample to the next sub-block's (they jumped once per 32 samples: a step in the
         * delay time, a zipper on the chorus' output of ~10x the signal's third difference, 2026-10-24); each sub-block
         * starts where the last one ended (it started from this DEPTH's position: each DEPTH step made the tap jump,
         * up to 1.2 samples, the audit 2026-10-25) */
        int32_t el = cho_pos(cp_ph + inc * 32u, sw), er = cho_pos(cp_ph + inc * 32u + 0x40000000u, sw);
        int32_t dlq = cho_el, drq = cho_er;
        int32_t sl = el - dlq, sr = er - drq;           /* per sample, in Q13 (x 32: the 32 steps add up exactly) */
        int32_t mix = (16384 * (2 * depth > 127 ? 127 : 2 * depth)) / 127;
        int32_t cg = (mix * 1950 >> 14) + (((mix * (16384 - mix)) >> 14) * 7 >> 3);   /* + half-mix comb; the gain is
                                                         * 1 + cg / 32768 (cg < 7600: l * cg fits up to |l| 282000; l * the
                                                         * whole gain overflowed from |l| 61856: a hot chord, fuzz 2390) */
        dlq <<= 5; drq <<= 5;
        cho_el = el; cho_er = er;
        /* the line is written backwards (sample w at -w): the tap I + F (Q13) behind w sits at -w + I and the one
         * before it at -w + I + 1, so one Q13 position per tap, (-w << 13) + dlq, gives the index (its bits 13..22)
         * and the fraction (bits 5..12) and steps by sl - 8192 a sample; cho_buf[1024] is cho_buf[0] again (copied
         * each sub-block: a tap that reads it wants a sample 154 .. 463 old, written before this sub-block), so the
         * pair needs no second wrap. The same taps, fractions and sums as cho_buf[w - I] + (cho_buf[w - I - 1] -
         * cho_buf[w - I]) x F, in fewer instructions (0.9.5) */
        uint32_t p = 0u - cho_w, k = n - sb < 32u ? n - sb : 32u;
        uint32_t vl = (p << 13) + (uint32_t)dlq, vr = (p << 13) + (uint32_t)drq;
        const int32_t *l = L + sb, *r = R + sb, *le = l + k;
        cho_buf[1024] = cho_buf[0];
        for (; l < le; l++, r++) {                      /* the line first (the taps are >= 154 samples old: none reads
                                                         * what this sub-block writes), then one tap per side (three
                                                         * short loops keep their values in registers) */
            cho_buf[p & 1023u] = clampi((*l + *r) >> 1, -1048575, 1048575);
            p--;
        }
        cho_w = 0u - p;
        if (mix == cho_km && cg == cho_cg) {
            cho_hl[0] = FR_FAR(cho_tap)(L + sb, k, vl, (uint32_t)sl - 8192u, mix, cg, cho_hl[0]);
            cho_hl[1] = FR_FAR(cho_tap)(R + sb, k, vr, (uint32_t)sr - 8192u, mix, cg, cho_hl[1]);
        } else {                                        /* DEPTH moved: the mix and make-up ramp over the sub-block */
            cho_hl[0] = cho_tap_r(L + sb, k, vl, (uint32_t)sl - 8192u, cho_km, cho_cg, mix - cho_km, cg - cho_cg, cho_hl[0]);
            cho_hl[1] = cho_tap_r(R + sb, k, vr, (uint32_t)sr - 8192u, cho_km, cho_cg, mix - cho_km, cg - cho_cg, cho_hl[1]);
            cho_km = mix; cho_cg = cg;
        }
        cp_ph += inc * 32u;
    }
    xf_post(L, R, xf_d[0], xf_d[1], n, cho_xw, w1);
    cho_xw = w1;
}

/* ---- COMPRESSOR (mono, before the drive): peak detector (ATTACK / RELEASE), soft knee of 6 dB, gain computed
 * in log2 (Q12) every 16 samples and ramped. AMOUNT moves the threshold -9 .. -39 dBFS and the ratio 1.5 .. 6
 * together; the make-up equals the reduction at a typical piano level (-26 dBFS): level-neutral. */
static int32_t cmp_env, cmp_g = 65536;
static int32_t ilog2_q12(uint32_t x)                     /* log2(x), Q12; x > 0 (no clz: a binary search) */
{
    int32_t e = 0;
    if (x >= 1u << 16) { x >>= 16; e += 16; }
    if (x >= 1u << 8) { x >>= 8; e += 8; }
    if (x >= 1u << 4) { x >>= 4; e += 4; }
    if (x >= 1u << 2) { x >>= 2; e += 2; }
    if (x >= 1u << 1) { e += 1; }
    return e << 12;
}
static int32_t log2_q12(uint32_t x)
{
    int32_t e = ilog2_q12(x) >> 12;
    uint32_t m = e >= 8 ? x >> (e - 8) : x << (8 - e);      /* 256 .. 511 */
    return (e << 12) + FX_LOG2F[m & 255u];
}
static int32_t exp2_q16(int32_t g)                       /* 2^(g / 4096) in Q16; g in [-16, 4] octaves */
{
    int32_t ip = g >> 12, v = (int32_t)FX_EXP2F[(g & 4095) >> 4];
    return ip >= 0 ? v << ip : ip > -16 ? v >> -ip : 0;
}
static int32_t cmp_xw;                                   /* the on / off crossfade's wet share (xf_next) */
/* the gain reduction (log2 Q12) at the detector's level env: the soft knee of 6 dB around thr */
static int32_t cmp_gr_at(int32_t env, int32_t thr, int32_t slope)
{
    int32_t lvl = env > 256 ? log2_q12((uint32_t)env) - 23 * 4096 : -16 * 4096;   /* re 32768 << 8 */
    int32_t over = lvl - thr;
    if (over <= -2048) return 0;                                    /* the knee: +-3 dB around the threshold */
    if (over < 2048) return (int32_t)(((int64_t)slope * (over + 2048) * (over + 2048)) >> 25);
    return (slope * over) >> 12;
}
__attribute__((noinline)) static void cmp_from(const int32_t *m, uint32_t n, int32_t mk, int32_t thr, int32_t slope)
{                                                        /* the detector and the gain settled at this block's peak */
    int32_t pk = 0;
    uint32_t j;
    for (j = 0; j < n; j++) if (iabs(m[j]) > pk) pk = iabs(m[j]);
    cmp_env = pk << 8;
    cmp_g = exp2_q16(mk - cmp_gr_at(cmp_env, thr, slope));
}
static void fx_comp(int32_t *m, uint32_t n)
{
    /* the four knobs (0.8): THRESHOLD 0 .. -40 dBFS, RATIO 1.5:1 .. 20:1, ATTACK, MAKEUP 0 .. +24 dB; RELEASE CC 109 */
    static const int16_t SLOPE[8] = {1365, 2048, 2731, 3072, 3413, 3584, 3686, 3891};   /* 1 - 1/R, Q12 */
    int32_t ka = (int32_t)FX_CMP_ATK[fx_p[FXP_CMP_ATK]], kr = (int32_t)FX_CMP_REL[fx_p[FXP_CMP_REL]];
    int32_t thr = -((fx_p[FXP_CMP_AMT] * 27434) >> 7);              /* log2 Q12 re full scale: 0 .. -6.64 (-40 dB) */
    int32_t slope = SLOPE[fx_p[FXP_CMP_RATIO] & 7];
    int32_t mk = (fx_p[FXP_CMP_GAIN] * 16461) >> 7;                /* make-up, log2 Q12: 0 .. 3.99 (+24 dB) */
    int on = fx_p[FXP_CMP_SW] != 0;
    int32_t w1;
    uint32_t sb, j;
    if (!on && !cmp_xw) { fx_cmp_gr = 0; return; }
    if (!cmp_xw) {                                    /* switched on from rest: the detector from this block's peak,
                                                       * the gain settled there (they were whatever the last use left:
                                                       * the bug hunt, 2026-10-25), under the 46 ms crossfade */
        cmp_from(m, n, mk, thr, slope);
    }
    w1 = xf_pre(cmp_xw, on, m, 0, n);                 /* on / off: a crossfade, the compressor running (off was a
                                                       * gain step to unity in one sample, on one over 16 samples) */
    for (sb = 0; sb < n; sb += 16u) {
        int32_t g0 = cmp_g, g1, gr, step;
        for (j = sb; j < sb + 16u && j < n; j++) {                  /* detector: |x| in Q8 */
            int32_t a = iabs(m[j]) << 8, d = a - cmp_env;
            cmp_env += (int32_t)(((int64_t)d * (d > 0 ? ka : kr)) >> 16);
        }
        gr = cmp_gr_at(cmp_env, thr, slope);
        fx_cmp_gr = gr;
        g1 = exp2_q16(mk - gr);
        step = (g1 - g0) / 16;
        for (j = sb; j < sb + 16u && j < n; j++) {
            g0 += step;
            m[j] = (int32_t)(((int64_t)m[j] * g0) >> 16);
        }
        cmp_g = g1;
    }
    xf_post(m, 0, xf_d[0], 0, n, cmp_xw, w1);
    cmp_xw = w1;
    if (!w1) fx_cmp_gr = 0;
}

/* ---- D.DELAY / A.DELAY: one line of 1.486 s (int16 at half level) */
#define DLY_LEN 65536u
static int16_t dly_buf[DLY_LEN] POOL;
static uint32_t dly_w, dly_wow, dly_flt;
static int32_t dly_d = 4410 << 8, dly_lp, dly_hp, dly_dg = 32768, dly_sg;   /* dly_sg: the send's stereo make-up, log2 Q12 */
static int32_t dly_sgk = 65536;                 /* the make-up (Q16) the last block ended on: each block ramps from it */
static int32_t dly_wk;                          /* the echo's gain (MIX: FX_DLY_WET) the last block ended on: each block
                                                 * ramps from it to the new one, sample by sample (a step once per
                                                 * block put a small step into the echoes at each MIX step; the
                                                 * audit, 2026-10-25: 1.9-3.3x a static MIX's largest jump) */
/* the per-sample step (Q8) of a ramp from a to b over n samples: 0 when they are equal (the old gain, bit for bit) */
__attribute__((noinline)) static int32_t dly_wstep(int32_t a, int32_t b, uint32_t n)   /* (one copy: less flash) */
{
    return a == b || !n ? 0 : ((b - a) << 8) / (int32_t)n;
}
#define DLY_WSTEP(a, b, n) dly_wstep(a, b, n)
/* the send switched (0.9.5): a block with the send off ends with the make-up at 0, so the send's first block ramps it
 * up from 0, and the block after the EFFECT goes OFF runs the old EFFECT once more with the make-up ramped down to 0
 * (dly_son: the EFFECT the last block asked for). The send was cut at a block edge: its step came back one delay time
 * later as a kink in the echo (2.7x a steady echo's second difference on a TAPE -> OFF switch, under chords) */
static uint8_t dly_son;
/* the developer reset (CC 10) empties the line over ~16 blocks instead of one 128 KB memset in one block (that block took
 * ~5 ms of the 5.8: USB packets lost): while dly_rst, [dly_rb, dly_w) is clean (cleared or written since the reset), the
 * reads behind dly_rb return 0 (what the cleared line held), and each block clears DLY_CLR more below dly_rb */
#define DLY_CLR 4096u
/* (a section of their own, as engine.c's ring: kept out of the compiler's global merge) */
#ifdef DINGHY_HOST
#define FX_OWN_SECTION
#else
#define FX_OWN_SECTION __attribute__((section(".bss.fx_rst")))
#endif
static int32_t dly_dt[256] POOL;               /* the tape's delay time per sample of the block (Q8) */
static uint32_t dly_rb FX_OWN_SECTION;
static uint8_t dly_rst FX_OWN_SECTION;
/* the send per sample, (L + R) / 2 times the make-up: the make-up's energy pass keeps the sum, dly_ramp scales it (only
 * while the make-up moves or is not 1: with L == R it is exactly 1 and the pass is skipped), the sample loops read it
 * (0.9.5: the ramp and the sum were in every loop, every block). With the send off it is stale and masked to 0. */
static int32_t dly_m[256] POOL;
/* the make-up ramp over n sends, from the last block's dly_sgk to sg16 (Q24 steps, |sg16 - dly_sgk| <= 65536: << 8
 * fits): the arithmetic the sample loops had; x 65536 all along (dly_sgk = sg16 = 65536) is x 1, skipped */
FR_RAM __attribute__((noinline)) static void dly_ramp(int32_t *m, uint32_t n, int32_t sg16)
{
    int32_t kq = dly_sgk << 8, dk = n ? ((sg16 - dly_sgk) << 8) / (int32_t)n : 0;
    uint32_t j;
    if (kq == 65536 << 8 && !dk) return;
    for (j = 0; j < n; j++) {
        kq += dk;
        m[j] = (int32_t)(((int64_t)m[j] * (kq >> 8)) >> 16);
    }
}
/* the delay's sample loop, one version for TAPE (tape = 1) and one for DIGITAL / off (tape = 0: a constant after
 * inlining), the state in locals: the same arithmetic per sample as one loop with the tests inside. msk: the send on
 * (-1) or off (0, the tail rings); sg16: the send's make-up (Q16, exactly 65536 when dly_sg is 0: x 1), ramped sample
 * by sample from the last block's dly_sgk (a step once per block put a one-sample step into the echoes, one delay time
 * after each block boundary, whenever L != R: up to ~700 LSB on VIBES with the chorus; equal ends: the old send, bit
 * for bit); dgt: the dry's gain it eases to. */
static inline __attribute__((always_inline)) void dly_loop(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                           const int32_t msk, int32_t sg16, int32_t target, int32_t col,
                                                           int32_t fb, int32_t wet, int32_t dgt, const int rst)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, j, c = dly_rst ? w - dly_rb : 0x7FFFFFFFu;   /* c: the clean span behind w */
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    int32_t wq = dly_wk << 8, ws = DLY_WSTEP(dly_wk, wet, n);   /* the echo's gain: a ramp from the last block's */
    FX_K32 kcol = col, kfb = fb;                         /* the tone filter's and the feedback's (r - lp) * col, f * fb:
                                                          * 32 x 32 -> 64, one instruction each (they were split into
                                                          * two 32-bit products, mulq15w: (a * b) >> 15, the same bits
                                                          * for |a| < 2^18; a * b alone overflows int32 from |a| 65536,
                                                          * fuzz vibes 4300-san) */
    if (tape)                                            /* tape: slewed time, wow and flutter; they depend on nothing
                                                          * else, so first for the whole block (fewer registers below) */
        for (j = 0; j < n; j++) {
            int32_t sf;
            dd += (target - dd) >> 12;
            wow += 69000u; flt += 690000u;              /* ~0.7 Hz, ~7 Hz */
            sf = osc_sine(flt);
            sf = (sf + (int32_t)((uint32_t)sf >> 31)) >> 1;   /* sf / 2 (towards 0) without the divide */
            dly_dt[j] = dd + (int32_t)(((int64_t)dd * (osc_sine(wow) * 3 + sf)) >> 25);
        }
    for (j = 0; j < n; j++) {
        int32_t d, r, in = dly_m[j] & msk, f;          /* (the send, ramped: dly_ramp) */
        uint32_t i;
        if (tape)
            d = dly_dt[j];
        else {
            dd += PH_CL(target - dd, -256, 256);
            d = dd;
        }
        i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        if (rst && c <= DLY_LEN) {                       /* a tap behind the clean span reads 0 (lookback L clean: L <= c,
                                                          * 0 counts as DLY_LEN; from c > DLY_LEN every lookback is) */
            uint32_t lb = (w - i) & (DLY_LEN - 1u);
            int32_t a0 = ((lb - 1u) & (DLY_LEN - 1u)) < c ? dly_buf[i] : 0;
            int32_t a1 = (lb & (DLY_LEN - 1u)) < c ? dly_buf[(i - 1u) & (DLY_LEN - 1u)] : 0;
            r = (a0 + (((a1 - a0) * (d & 255)) >> 8)) << 1;
            c++;
        } else                                           /* (c stays > DLY_LEN: no need to count on) */
            r = (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
        lp += (int32_t)(((int64_t)(r - lp) * kcol) >> 15);   /* (r - lp reaches 2^17 on a full-scale swing) */
        f = tape ? softclip(lp) : lp;
        hp += (f - hp + 64) >> 7;
        f -= hp;
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi((in >> 1) + (int32_t)(((int64_t)f * kfb) >> 15), -32768, 32767);
        w++;
        wq += ws;
        r = mulq15(r, wq >> 8);
        dg += (dgt - dg) >> 9;
        L[j] = (int32_t)(((int64_t)L[j] * dg) >> 15) + r; R[j] = (int32_t)(((int64_t)R[j] * dg) >> 15) + r;
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg; dly_sgk = msk ? sg16 : 0;
}
static uint32_t dly_clrn = DLY_CLR;            /* the clearing's share a block: DLY_CLR (CC 10), DLY_CLR / 4 (a tail's end) */
static uint32_t dly_qz;                         /* the send off: samples in a row the line was written within +-1 of 0 */
static uint8_t dly_lc = 1;                      /* the line is clean (all 0, or being cleared: dly_rst) */
static uint8_t dly_xo;                          /* DIG <-> ANA with echoes ringing (0.9.6, BUGHUNT #17: the tone, the
                                                 * softclip and the wow switched in one sample, ANA -> DIG 14x a ringing
                                                 * echo's d2): dly_fade crossfades the two (tape 2 / 3: what ANA adds
                                                 * glides in or out over 46 ms); 1 while it runs */
static uint8_t dly_ana;                         /* the old loops last ran ANA: its tail (OFF) runs ANA's loop (0.9.5: the
                                                 * DIG loop took over at OFF, its tone and the softclip changed at once:
                                                 * a kink in the echo one delay time later, 24x a steady one's) */
__attribute__((noinline)) static void dly_clear_step(void)   /* one block's share of the reset's clearing */
{
    uint32_t c = dly_w - dly_rb, k, p;
    if (c >= DLY_LEN) { dly_rst = 0; return; }
    k = DLY_LEN - c < dly_clrn ? DLY_LEN - c : dly_clrn;
    p = (dly_rb - k) & (DLY_LEN - 1u);
    if (p + k <= DLY_LEN) memset(dly_buf + p, 0, k * sizeof dly_buf[0]);
    else { memset(dly_buf + p, 0, (DLY_LEN - p) * sizeof dly_buf[0]); memset(dly_buf, 0, (p + k - DLY_LEN) * sizeof dly_buf[0]); }
    dly_rb -= k;
    if (dly_w - dly_rb >= DLY_LEN) dly_rst = 0;
}
/* the tail (the send off) is over once the line was written ~0 over the whole delay and two blocks more: every read
 * from then on is ~0; the line's older part still holds what it held a ring (1.49 s) before, so the quiet path below
 * clears it (dly_lc, through dly_rst: the reads behind the cleared span return 0 at once). 0.9.5: the test was |lp| < 2
 * at a block's end, which a ringing tail crossing 0 there passed: the tail stopped there, its line was left as it was
 * and read again by the next switch on, a ring old: a step of up to ~10000 LSB (OFF -> DIG / ANA under chords) */
__attribute__((noinline)) static int dly_tail_over(uint32_t n, int32_t target)
{
    uint32_t j, w = dly_w;
    for (j = 1u; j <= n; j++) {
        int32_t v = dly_buf[(w - j) & (DLY_LEN - 1u)];
        if (v > 1 || v < -1) { dly_qz = 0; return 0; }
    }
    dly_qz += n;
    return dly_qz >= (uint32_t)(target >> 8) + 2u * n;
}
#include "dly_tape.c"                              /* EFFECT = TAPE (its own loop; DIG / ANA as they were) */
/* fx_delay's body. rst is always 1: the masked reads run in every block (outside a reset c is huge and every tap is
 * clean). A separate reset version (out of line, or inlined) moved the Rhodes loops' registers in app_audio, whatever
 * the arrangement tried (tools/build.py's hot-loop counts, 2026-10-18); this one leaves them as they were. */
static inline __attribute__((always_inline)) void fx_delay_t(int32_t *L, int32_t *R, uint32_t n, const int rst)
{
    int32_t sw = fx_p[FXP_DL_SW], depth = fx_p[FXP_DL_DEPTH], last = !sw && dly_son;   /* last: the send's last block */
    uint32_t sync = fx_dly_sync_samples, j;
    int32_t target = (int32_t)(sync ? sync : FX_DLY_T[fx_p[FXP_DL_TIME]]);
    /* MIX (0.9.5, the user): the first echo as loud as the dry at 64 (it was -4.8 dB), +5.9 dB at 127 as before: the
     * wet 0.875 at 64 (linear below), then up to 1.0; the dry's ease-down (dgt below) as it was */
    int32_t wet = FX_DLY_WET[depth], fb = FX_DLY_FB[fx_p[FXP_DL_FDBK] & 127], col = 2000 + fx_p[FXP_DL_TONE] * 240, sg16;
    if (last) sw = dly_son;                                 /* the old EFFECT once more, its send ramped to 0 (sg16) */
    dly_son = (uint8_t)fx_p[FXP_DL_SW];
    if (sw) dly_qz = 0;
    if ((sw == 1 || sw == 2) && dly_ana != (sw == 2) && (dly_lp || dly_hp) && dly_cur != 3 && dly_xg == 32768) {
        if (!dly_xo) dly_xm = dly_ana ? 32768 : 0;      /* (from the type that ran; a turn back mid-way: from where it is) */
        dly_xo = 1;
    }
    if (sw == 1 || sw == 2) dly_ana = sw == 2;
    if (sw == 2 || (!sw && dly_ana)) col = (col * 19661) >> 15;   /* tape: darker (ANA's tail too) */
    while (target >= (int32_t)DLY_LEN - 2) target >>= 1;
    target <<= 8;
    /* FDBK alone sets the regeneration (0.9.5; it was MIX x FDBK: at MIX 64 the repeats died fast): the loop gain is 2 fb
     * (the line holds half level), at most 0.85 up to FDBK 114, so the echoes always die (they grew forever over MIX x
     * FDBK ~ 11650: the audit, 2026-10-17); DIG and every tail (OFF) keep that cap at any FDBK. ANA from 115 goes above
     * 1 (1.03 .. 1.06, its tone and DC filters lose ~3 %): the repeats build up into oscillation, as an analog echo's
     * do, held by its softclip (where tanh's slope times the loop gain is 1); TAPE's own law: dly_tape.c tp_block */
    if (sw != 2 && fb > 13926) fb = 13926;
    if (!sw && !dly_lp && !dly_hp && dly_cur != 3) {        /* fully quiet and off: only keep time */
        dly_wk = wet;                                       /* (nothing heard: no ramp to keep) */
        if (!dly_lc) { dly_rb = dly_w; dly_rst = 1; dly_clrn = DLY_CLR / 4u; dly_lc = 1; }   /* (the tail's line: cleared) */
        if (dly_rst)                                        /* (the skipped span is clean too: what the reset left) */
            for (j = 0; j < n; j++) dly_buf[(dly_w + j) & (DLY_LEN - 1u)] = 0;
        dly_w += n;
        dly_sgk = 0;                                        /* (the send off: its next block ramps up from 0) */
        return;
    }
    dly_lc = 0;
    {   /* the send is the mono sum (L + R) / 2; when the phaser / chorus / pan make L and R differ, the sum loses up to
         * 3-6 dB (the echoes "lost" under the chorus or phaser: the user, 2026-10-18). The send keeps the stereo
         * power instead: x sqrt(mean(L^2 + R^2) / 2 / mean(sum^2)), per block, gliding, at most +6 dB; exactly 1 when
         * L == R (the old send, sample for sample) */
        int64_t e = 0, m = 0;
        int32_t tg = 0;
        if (sw) for (j = 0; j < n; j++) {
            int32_t a = L[j], b = R[j], c = (a + b) >> 1;
            dly_m[j] = c;                                   /* (the send: dly_ramp, the sample loops) */
            e += (int64_t)a * a + (int64_t)b * b;
            m += (int64_t)c * c;
        }
        if (m > 0 && (e >> 1) != m) {
            while (e >= ((int64_t)1 << 31)) { e >>= 1; m >>= 1; }
            if (m > 0) tg = clampi((ilog2_q12((uint32_t)e) - 4096 - ilog2_q12((uint32_t)m)) >> 1, 0, 4096);
        }
        dly_sg += (tg - dly_sg) >> 2;
        if (tg == 0 && dly_sg < 4) dly_sg = 0;
    }
    sg16 = last ? 0 : exp2_q16(dly_sg);
    if (dly_xo || sw == 3 || dly_cur == 3 || dly_xg != 32768) {   /* TAPE, a switch to / from it, DIG <-> ANA (dly_tape.c) */
        if (tp_route(L, R, n, sw, target, sg16, fb, wet, depth)) { dly_wk = wet; return; }
    } else dly_last = (uint8_t)sw;                          /* (what a switch to TAPE fades out) */
    if (sw) FR_FAR(dly_ramp)(dly_m, n, sg16);                       /* (dly_loop sets dly_sgk to sg16) */
    /* the dry eases down as the mix rises (-0.7 dB at 50, -5.9 dB at 127): a full-mix delay no longer jumps +5 LU (0.8) */
    if (sw == 2) dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, 32768 - depth * depth, rst);
    else if (sw) dly_loop(L, R, n, 0, -1, sg16, target, col, fb, wet, 32768 - depth * depth, rst);   /* (the send's
                                                             * mask a constant: one register fewer in either loop) */
    else dly_fade(L, R, n, dly_ana, 0, sg16, target, col, fb, wet, 32768, 0);   /* the tail (OFF): DIG's or ANA's,
                                                             * dly_fade's loop at g 1 (the same integers as dly_loop's) */
    dly_wk = wet;
    if (!sw && dly_tail_over(n, target)) dly_lp = dly_hp = 0;
}
/* the chorus, then the delay, in their own function (0.9.5: the delay since e9eb294, so its loops do not move the
 * Rhodes pickup loops in app_audio; the chorus' three loops with it: the same app_audio as a separate chorus call) */
__attribute__((noinline)) static void fx_delay(int32_t *L, int32_t *R, uint32_t n)
{
    fx_chorus(L, R, n);
    fx_delay_t(L, R, n, 1);
}

/* ---- REVERB: Felucca's HALL (fx.c, GPL-3.0-only), unchanged except for its parameters' source and its internal
 * level: the int16 lines run 4x hotter (HL_IN) and the output gain is 4x lower (HL_OUT), the same reverb level
 * with 12 dB less rounding noise (at a piano's -9 dBFS the original level left the lines a few hundred LSB wide:
 * an audible hiss under every note, 0.4) */
#define CTL 32u
#define HL_NL 8u
#define HL_NAP 2u
#define HL_NC (CTL / 2u)
#define RV_DZ 10u                   /* rev_down's history: rv_in's 10 samples before the block */
/* the 8 lines share one ring of HL_N rows (0.9.5: the same delays, sample for sample, without the per-line write-back):
 * row t holds the 8 lines' samples written at one time, line k reads its row HL_LEN[k] back (+ its modulation); HL_N
 * is a multiple of HL_NC (a block's 16 rows never wrap) and >= the longest line; row HL_N repeats row 0 (the
 * interpolation's next sample) */
#define HL_N 752u
static int16_t rev_comb[(HL_N + 1u) * HL_NL] POOL;
static int16_t rev_ap[556 + 441] POOL;
#define HL_MOD 8                    /* the modulation's depth at MOD 64 (rev_types.c: (MOD + 4) >> 3, 0 .. 16) */
#define HL_LPM 0x69u
#define HL_KD(d) (32767 - (d) * 180)
#define HL_LOUD 32
#define HL_OUT 1
#define HL_IN 10320                 /* Felucca: 2580 with HL_OUT 4 */
static const uint16_t HL_LEN[HL_NL] = {467, 509, 547, 593, 631, 673, 709, 743};
static const uint16_t HL_AP[HL_NAP] = {131, 307};
static const int16_t HL_APG[HL_NAP] = {11469, 10240};
static struct {
    uint16_t w, d[HL_NAP];          /* the ring's write row, the allpasses' indices */
    int32_t lp[HL_NL], le[HL_NL];
    int32_t hp, he;                 /* the DC blocker */
    int16_t g[HL_NL], sz;
    uint32_t ph;
    int32_t bx[HL_NC];
    int16_t y[HL_NC][HL_NL];        /* the line ends, one row per sample */
} hl POOL;
static inline __attribute__((always_inline)) int32_t sh_tz(int32_t m, uint32_t sh) { return (m + (int32_t)((uint32_t)(m >> 31) >> (32u - sh))) >> sh; }
static uint32_t exp2_neg(uint32_t x)
{
    uint32_t i = x >> 16, u = 32768u - ((x & 0xFFFFu) >> 1), p = 2579u;
    p = ((p * u) >> 15) + 7409u;
    p = ((p * u) >> 15) + 22780u;
    p = ((p * u) >> 15) + 32768u;
    return i < 15u ? p >> (i + 1u) : 0u;
}
static void hall_gains(int32_t s)
{
    uint32_t q = exp2_neg((uint32_t)s * 65536u / 30u), k, g;
    for (k = 0; k < HL_NL; k++) {
        g = exp2_neg((HL_LEN[k] * 59u * q) >> 15);
        hl.g[k] = (int16_t)(((g > 32440u ? 32440u : g) * 6193u) >> 15);
    }
    hl.sz = (int16_t)(s + 1);
}
static inline __attribute__((always_inline)) int32_t hl_c16(int32_t v) { v = v > -32768 ? v : -32768; return v < 32767 ? v : 32767; }   /* smax + smin */
static inline void hall_ap(int16_t *q, int32_t g, int32_t *x, uint32_t n)
{
    int16_t *e = q + n;
    for (; q < e; q++, x++) {
        int32_t y = *q, v = *x + sh_tz(y * g, 14);
        *q = (int16_t)hl_c16(v);
        *x = y - ((v * g + 8192) >> 14);
    }
}
/* the line ends (n >= 1): b steps HL_NL (one ring row), y steps HL_NL (one hl.y row) */
static inline void hall_end(const int16_t *b, int32_t f, int16_t *y, uint32_t n, int32_t g, int lo)
{
    int32_t p = *b, q;
    if (lo)
        do { b += HL_NL; q = *b; *y = (int16_t)sh_tz((p + (((q - p) * f + 128) >> 8)) * g, 14); y += HL_NL; p = q; } while (--n);
    else
        do { b += HL_NL; q = *b; *y = (int16_t)(((p + (((q - p) * f + 128) >> 8)) * g + 8192) >> 14); y += HL_NL; p = q; } while (--n);
}
static inline void hall_end_lp(const int16_t *b, int16_t *y, uint32_t n, int32_t g, int lo, int32_t *lp, int32_t *le,
                               int32_t kd)
{
    int32_t l = *lp, r = *le, e;
    if (lo)
        do { e = (*b - l) * kd + r, r = e & 32767, l += e >> 15; *y = (int16_t)sh_tz(l * g, 14); b += HL_NL; y += HL_NL; } while (--n);
    else
        do { e = (*b - l) * kd + r, r = e & 32767, l += e >> 15; *y = (int16_t)((l * g + 8192) >> 14); b += HL_NL; y += HL_NL; } while (--n);
    *lp = l;
    *le = r;
}
#define HL_RUNS(d, len, k, s, body)                                                         \
    for (s = 0; s < HL_NC; s += k) {                                                        \
        k = (len) - (d) < HL_NC - s ? (len) - (d) : HL_NC - s;                              \
        body;                                                                               \
        d = (d) + k >= (len) ? 0u : (d) + k;                                                \
    }
/* the 2:1 down into every reverb TYPE (CTL frames in, HL_NC out; HALL here, the kernels' rev_dec): an 11-tap half-band
 * (1024 at the centre, (624, -152, 40) at +-1, 3, 5, / 2048: a minimax design, the band that folds below 6.9 kHz down
 * 41 dB and more; the old (1, 2, 1) / 4 let a 15 kHz tone fold to 7 kHz only 10 dB down), then HL_IN and the DC
 * blocker. in[-RV_DZ .. CTL): the block with the RV_DZ samples before it (rv_inb), |x| <= 2^19 (fx_reverb clamps the
 * send to +-131071; LOW CUT may double it). RV_HB(c, d): the half-band's odd taps around c, d apart */
#define RV_HB(c, d) (624 * ((c)[-(d)] + (c)[0]) - 152 * ((c)[-2 * (d)] + (c)[d]) + 40 * ((c)[-3 * (d)] + (c)[2 * (d)]))
static FR_RAM __attribute__((noinline)) void rev_down(const int32_t *in, int32_t *php, int32_t *phe, int32_t *out)
{
    int32_t hp = *php, he = *phe;
    uint32_t i;
    for (i = 0; i < HL_NC; i++) {
        const int32_t *c = in + 2u * i - 4u;                /* (c[5]: this pair's second sample, in[2i + 1]) */
        int32_t x = (1024 * c[0] + RV_HB(c + 1, 2) + 1024) >> 11, v;
        x = mulq15(clampi(x, -131071, 131071), HL_IN);
        v = x - hp + he;
        he = v & 31;
        hp += v >> 5;
        out[i] = x - hp;
    }
    *php = hp, *phe = he;
}
#define HL_LFO ((uint32_t)(0.37 * CTL / FS * 4294967296.0))
/* one control block (CTL frames in, HL_NC at the half rate out): ol[] = the mid, or[] = the side (x HL_OUT), the
 * caller upsamples them (fx_reverb) */
static void rev_hall(const int32_t *rev_in, int32_t size, int32_t damp, int32_t md, int32_t *ol, int32_t *or)
{
    int16_t *a = rev_ap, *o, *y;
    uint32_t i, k, s, m, d, w = hl.w;
    int32_t kd = HL_KD(damp);
    if (hl.sz != size + 1) hall_gains(size);
    FR_FAR(rev_down)(rev_in, &hl.hp, &hl.he, hl.bx);
    for (k = 0; k < HL_NAP; k++) {
        d = hl.d[k];
        HL_RUNS(d, HL_AP[k], m, s, hall_ap(a + d, HL_APG[k], hl.bx + s, m));
        hl.d[k] = (uint16_t)d;
        a += HL_AP[k];
    }
    hl.ph += HL_LFO;
    for (k = 0; k < HL_NL; k++) {
        const int16_t *b = rev_comb + k;
        int32_t g = hl.g[k], f = 0, lo;
        uint32_t len = HL_LEN[k];
        d = w + HL_N - len;                             /* the row written len samples ago */
        if (!((HL_LPM >> k) & 1u)) {
            uint32_t r = (uint32_t)(((md + 2) << 8) + ((osc_sine(hl.ph * (1u + (k & 1u)) + k * 0x2C000000u) * md) >> 7));
            f = (int32_t)(r & 255u);
            d += r >> 8;
        }
        d = d >= HL_N ? d - HL_N : d;
        s = d + len / 2u;
        s = s >= HL_N ? s - HL_N : s;
        lo = (b[d * HL_NL] < 0 ? -b[d * HL_NL] : b[d * HL_NL]) + (b[s * HL_NL] < 0 ? -b[s * HL_NL] : b[s * HL_NL]) < HL_LOUD;
        if ((HL_LPM >> k) & 1u) {
            HL_RUNS(d, HL_N, m, s, hall_end_lp(b + d * HL_NL, hl.y[s] + k, m, g, lo, &hl.lp[k], &hl.le[k], kd));
        } else {
            HL_RUNS(d, HL_N, m, s, hall_end(b + d * HL_NL, f, hl.y[s] + k, m, g, lo));
        }
    }
    /* the feedback matrix (8x8, 0 on the diagonal, +-1 elsewhere: y_k' = 2 p_k + y_k - S + x, S = the sum of all 8,
     * p_k = 3 of the others), straight into the ring's next 16 rows, and the outputs */
    o = rev_comb + w * HL_NL;
    for (i = 0, y = hl.y[0]; i < HL_NC; i++, y += HL_NL, o += HL_NL) {
        int32_t y0 = y[0], y1 = y[1], y2 = y[2], y3 = y[3], y4 = y[4], y5 = y[5], y6 = y[6], y7 = y[7], x = hl.bx[i];
        int32_t u = y3 + y4, r = y1 + y2 + y5 + y6, l = y0 + u - y7, t = r + u + y7, c = x - (y0 + t);
        ol[i] = (l + r) * HL_OUT;
        or[i] = (l - r) * HL_OUT;
        o[0] = (int16_t)hl_c16(t + x);
        o[1] = (int16_t)hl_c16(2 * (y2 + y3 + y5) + y1 + c);
        o[2] = (int16_t)hl_c16(2 * (y3 + y4 + y6) + y2 + c);
        o[3] = (int16_t)hl_c16(2 * (y4 + y5 + y7) + y3 + c);
        o[4] = (int16_t)hl_c16(2 * (y5 + y6 + y1) + y4 + c);
        o[5] = (int16_t)hl_c16(2 * (y6 + y7 + y2) + y5 + c);
        o[6] = (int16_t)hl_c16(2 * (y7 + y1 + y3) + y6 + c);
        o[7] = (int16_t)hl_c16(2 * (y1 + y2 + y4) + y7 + c - 2 * x);
    }
    if (!w) memcpy(rev_comb + HL_N * HL_NL, rev_comb, HL_NL * sizeof rev_comb[0]);
    w += HL_NC;
    hl.w = (uint16_t)(w >= HL_N ? 0u : w);
}
/* the REVERB page (0.9.3): MIX (the send, the size and the return as before), DECAY (moves the hall's size: 64 = as
 * before, 0 = about half the time, 127 = about twice: each step of the size is 2^(1/30) in T60), TONE (the lines' high
 * damping: 64 = as before (60), 0 = 150 dark, 127 = 0 bright) and PRE-DELAY (0 .. 80 ms before the hall; at 0 there is
 * no line at all). DECAY and TONE glide one step per 32 frames; a new pre-delay crossfades from the old tap (5.8 ms). */
#define PD_LEN 4096u                                    /* 92.9 ms: the pre-delay's line (int32: the send has headroom) */
#define FX_PDLY_SAMPLES(v) ((uint32_t)(v) * 3528u / 127u)   /* 0 .. 3528 samples = 80 ms (params.c shows it in ms) */
static int32_t pd_buf[PD_LEN] POOL;
static uint32_t pd_w, pd_d, pd_d0, pd_x;                /* write index, the tap, the old tap and the crossfade (0 = none) */
static uint8_t pd_on;
static uint32_t pd_fill FX_OWN_SECTION;                              /* writes since the line came on: a tap further back reads 0 */
static int32_t rv_inb[RV_DZ + CTL];                   /* the send: RV_DZ samples of history (rev_down), the block */
#define rv_in (rv_inb + RV_DZ)
static int32_t rv_off, rv_damp = 60;                   /* DECAY's size offset and TONE's damping, as they glide */
static uint8_t rv_running;
static void fx_predelay(int32_t *x, uint32_t tgt)      /* one control block: x[] delayed by the tap */
{
    uint32_t j;
    /* no old sound in it: the line reads as cleared until it has been written all through (a memset of 16 KB in the
     * audio block cost ~0.4 ms on the FM-1; this is the same sound) */
    if (!pd_on) { pd_fill = 0; pd_d = pd_d0 = pd_x = 0; pd_on = 1; }
#ifdef FX_RESET_ONE_BLOCK                               /* (the host test's reference: the old memsets, tests/py/test_reset_cc.py) */
    if (pd_fill == 0) memset(pd_buf, 0, sizeof pd_buf);
#endif
    if (!pd_x && tgt != pd_d) { pd_d0 = pd_d; pd_d = tgt; pd_x = 1; }
    if (!pd_x && pd_fill >= PD_LEN) {                   /* the line written all through, one tap (pd_d < PD_LEN):
                                                         * the loop below as it then runs (0.9.5: 42 -> 9 a sample) */
        uint32_t w = pd_w, d = pd_d;
        for (j = 0; j < CTL; j++, w++) {
            pd_buf[w & (PD_LEN - 1u)] = x[j];
            x[j] = pd_buf[(w - d) & (PD_LEN - 1u)];
        }
        pd_w = w;
        if (!pd_d && !tgt) pd_on = 0;                   /* back at 0: no line */
        return;
    }
    for (j = 0; j < CTL; j++) {
        int32_t a;
        pd_buf[pd_w & (PD_LEN - 1u)] = x[j];
        if (pd_fill < PD_LEN) pd_fill++;
        a = pd_d < pd_fill ? pd_buf[(pd_w - pd_d) & (PD_LEN - 1u)] : 0;
        if (pd_x) {                                     /* 256 samples from the old tap to the new one */
            int32_t b = pd_d0 < pd_fill ? pd_buf[(pd_w - pd_d0) & (PD_LEN - 1u)] : 0;
            a = b + (int32_t)(((int64_t)(a - b) * (int32_t)(pd_x << 7)) >> 15);
            if (++pd_x > 256u) pd_x = 0;
        }
        x[j] = a;
        pd_w++;
    }
    if (!pd_x && !pd_d && !tgt) pd_on = 0;              /* back at 0: no line */
}
/* the reverb's framework (rev_types.c, included after this file): the TYPE (OFF / HALL / PLATE / INFINITE), the send,
 * LOW CUT, the pre-delay, the dispatch to rev_hall or a kernel, the type-change fade and the return; its cost */
static void fx_reverb(int32_t *L, int32_t *R, uint32_t n);
static uint32_t rev_cost_us(void);
static void rev_reset_state(void);

/* ---- master: DC blocker, peak limiter (Felucca master_out; its soft clip never acted: gone, 0.9.5) */
#define LIM_T 24000                                     /* -2.7 dBFS: the look-ahead limiter's ceiling (0.8) */
static int32_t dc_l, dc_r, dce_l, dce_r;
/* look-ahead limiter (0.8): the output runs one 32-sample sub-block (0.73 ms) late; the gain ramps linearly over
 * each sub-block towards the lowest gain this one and the next need, so no sample ever passes LIM_T, nothing is
 * clipped, and the gain moves smoothly (big chords of 7-8 notes stay clean); release ~46 ms */
#define LK 32u
static int32_t lk_l[LK], lk_r[LK], lk_pk, lk_g = 32768;
static int32_t lk_need(int32_t pk) { return pk > LIM_T ? (int32_t)(((uint32_t)LIM_T << 15) / (uint32_t)pk) : 32768; }
static inline int32_t dc_block(int32_t x, int32_t *dc, int32_t *err)
{
    int32_t e = (x << 6) - *dc + *err, d = e >> 12;
    *err = e - (d << 12);
    *dc += d;
    return x - ((*dc + 32) >> 6);
}
static int32_t fx_L[256], fx_R[256];
#define GATE_LO 16                                      /* -66 dBFS (Q15) */
#define GATE_HI 32
static int32_t gate_g = 32768, gate_tgt = 32768, fx_wet = 32768;
static uint32_t gate_quiet;
/* developer CC 10 (>= 64): every free-running part back to its boot state, for before / after recordings that must
 * compare sample for sample (the user, 2026-10-18): the effect LFOs, the tape's wow / flutter, the reverb's modulation,
 * the delay / chorus / pre-delay / reverb lines, the phaser's allpasses, the engines' noise generators and the motor.
 * At the next audio block: everything but the delay line at once (~16 KB of small lines), the delay line in chunks
 * over the next ~16 blocks while its reads behind the cleared span return 0 (dly_rst): the same sound, sample for
 * sample, as clearing it all at once (that one block took ~5 ms of the 5.8 on the FM-1); the pre-delay line is
 * cleared the same way at its next use (pd_on = 0). */
static volatile uint8_t fx_reset_req;
static void fx_reset_state(void)
{
    lfo_ph = 0; lfo_rnd = 0x9E3779B9u; tw_ph = 0; ph_ph = 0; cp_ph = 0; dly_wow = 0; dly_flt = 0;
    ph_cq = -1; memset(&ph_st, 0, sizeof ph_st);
    dly_rb = dly_w; dly_rst = 1; dly_lp = dly_hp = 0;   /* the delay line: dly_clear_step(), the next blocks */
    dly_clrn = DLY_CLR; dly_lc = 1;
    tp_reset();                                         /* TAPE's filters, noise and transport */
    memset(cho_buf, 0, sizeof cho_buf); cho_el = -1; cho_hl[0] = cho_hl[1] = 0;   /* (the taps restart with cp_ph) */
    memset(rev_comb, 0, sizeof rev_comb); memset(rev_ap, 0, sizeof rev_ap); memset(&hl, 0, sizeof hl);
    pd_on = 0; rv_running = 0; rev_reset_state();       /* (the pre-delay line: at its next use; the TYPE's
                                                         * state: rev_types.c) */
    rh_lfsr = 0xACE1u; bar_lfsr = 0x1D872u; bar_mot_ph = 0;
    /* every voice ends and the shared pickup RLC / body states go to 0 (as at an engine change), and this block's
     * voice output is dropped (fx_process): the tails a take left under the gate's -66 dBFS rang on and the next take
     * met them (its level moved up to 5 LU with what was played before, on the FM-1: the presets suite) */
    eng_rh_kill_all(); bar_kill_all();
    lfo_sh = 0; fl_ic[0] = fl_ic[1] = 0; fl_op = 0;                 /* the filters, the smoothers, the master */
    dr_hp = dr_lp1 = dr_lp2 = dr_tlp = 0; wah_env = wah_lp = wah_bp = 0; cmp_env = 0; cmp_g = 65536;
    dc_l = dc_r = dce_l = dce_r = 0; memset(lk_l, 0, sizeof lk_l); memset(lk_r, 0, sizeof lk_r); lk_pk = 0; lk_g = 32768;
    gate_g = gate_tgt = 32768; gate_quiet = 0;
#ifdef FX_RESET_ONE_BLOCK
    memset(dly_buf, 0, sizeof dly_buf); memset(pd_buf, 0, sizeof pd_buf); dly_rst = 0;
#endif
}

/* SPREAD (S13): the Rhodes' side signal (rhodes.c rh_side) joins after TREM / WAH, before the stereo effects, at the
 * level the mono stages (COMP, DRIVE, the FILTER, TREM / WAH) gave the mono sum: per 32-sample sub-block the gain
 * g = sum |L + R| / (2 sum |mono|), ramped over the sub-block (1 exactly while they are all transparent) */
static int32_t sp_g RH_SP_SEC(".data.fx_sp_g") = 32768;   /* Q15: the last sub-block's gain */
FR_RAM __attribute__((noinline)) static void fx_spread(const int32_t *mono, int32_t *L, int32_t *R, uint32_t n)
{
    uint32_t sb;
    for (sb = 0; sb < n; sb += 32u) {
        uint32_t e = sb + 32u < n ? sb + 32u : n;
        int32_t num = 0, den = 0, g = sp_g, dg;
        {   /* (pointers stepping: no index adds; the same sums) */
            const int32_t *l = L + sb, *r = R + sb, *mo = mono + sb, *le = L + e;
            while (l < le) { num += iabs(*l++ + *r++); den += iabs(*mo++); }
        }
        den *= 2;
        if (den > 0) {
            while (num >= (1 << 16) || den >= (1 << 16)) { num >>= 1; den >>= 1; }
            g = den ? (num << 15) / den : g;
            if (g > (4 << 15)) g = 4 << 15;
        }
        dg = (g - sp_g) / 32;
        {
            int32_t *l = L + sb, *r = R + sb, *le = L + e, k = sp_g;
            const int32_t *sd = rh_side + sb;
            while (l < le) {
                int32_t s;
                k += dg;
                s = (int32_t)(((int64_t)*sd++ * k) >> 15);
                *l++ += s; *r++ -= s;
            }
        }
        sp_g = g;
    }
}

/* the bypassed dry with SPREAD's side: L = M + S, R = M - S (out of line: fx_process's registers as without it) */
__attribute__((noinline)) static void fx_spread_dry(const int32_t *mono, int32_t *L, int32_t *R, uint32_t sb)
{
    uint32_t j;
    for (j = sb; j < sb + LK; j++) { L[j] = mono[j] + rh_side[j]; R[j] = mono[j] - rh_side[j]; }
}

/* LEVEL moved (0.9.6, the bug hunt: each detent was a 0.19 dB step at a block's edge): the gain glides from the
 * last block's toward the new one (at most 512 in Q12 a block), sample by sample in the DC blocker's pass below (after
 * the blocker, as the settled gain: a ramp before it put the blocker's state into the other gain's domain, a step of
 * up to 11 % at the next block, the control sweep) */
static int32_t fx_lvk = -1;                              /* the LEVEL gain (Q12) the last block ended on */
/* this block's LEVEL: 4096 = unity (the plain loop), else the gain's ramp from *lq (Q20) by *ls a sample (0: settled
 * off unity, the same integers as a constant gain; one loop for both, less flash). still: bypassed or crossfading,
 * the step as before */
__attribute__((noinline)) static int32_t fx_lv_ramp(int32_t lvl, int still, uint32_t n, int32_t *lq, int32_t *ls)
{
    *lq = lvl << 8; *ls = 0;
    if (fx_lvk != lvl) {
        if (fx_lvk >= 0 && !still) {                     /* (at most 512 a block: a full swing in ~50 ms) */
            int32_t nx = fx_lvk + clampi(lvl - fx_lvk, -512, 512);
            *lq = fx_lvk << 8; *ls = ((nx - fx_lvk) << 8) / (int32_t)n; fx_lvk = nx;
            return -1;                                   /* (-1: the ramp) */
        }
        fx_lvk = lvl;
    }
    return lvl;
}
static inline __attribute__((always_inline)) void fx_process(const int32_t *mono, int32_t *out, uint32_t n)   /* (into app_audio, as it always was: the 0.9.3 delay fix took it over the inliner's limit, and the mode loop moved) */
{
    static int32_t m[256];
    if (fx_reset_req) {                                /* (the voices' last block too: mono is frodhes.c's voice_mono) */
        fx_reset_state(); memset((void *)mono, 0, n * sizeof mono[0]); fx_reset_req = 0;
        rh_side_on = 0; sp_g = 32768;                  /* (SPREAD's side of that last block too) */
    }
    else if (dly_rst) dly_clear_step();
    int32_t lvl = (int32_t)FX_LVL[fx_p[FXP_LEVEL] & 127];   /* the preset's volume trim, Q12 */
    uint32_t j, sb;
    int32_t pk = 0, wet = fx_wet, byp = fx_bypass, dl = dc_l, el = dce_l, dr = dc_r, er = dce_r, lq = 0, ls = 0;
    if (fx_p[FXP_RAW]) {                               /* developer: the voice engine's output untouched */
        for (j = 0; j < n; j++) { out[2u * j] = mono[j]; out[2u * j + 1u] = mono[j]; }   /* (every sample: "=") */
        return;
    }
    for (j = 0; j < n; j++) { m[j] = mono[j]; fx_L[j] = fx_R[j] = 0; }   /* (mono is the dry signal for the bypass) */
    fx_comp(m, n);
    fx_drive(m, n);                                    /* (0.9.5, the user: DRIVE before the FILTER; both mono, on m
                                                        * in place) */
    fx_filter(m, n);
    fx_trem(m, fx_L, fx_R, n);
    if (rh_side_on) FR_FAR(fx_spread)(mono, fx_L, fx_R, n);   /* SPREAD: the side (S13) */
    fx_phaser(fx_L, fx_R, n);
    fx_delay(fx_L, fx_R, n);                           /* (the chorus, then the delay) */
    fx_reverb(fx_L, fx_R, n);
    lvl = fx_lv_ramp(lvl, byp || wet != 32768, n, &lq, &ls);
    for (sb = 0; sb < n; sb += LK) {                   /* the look-ahead limiter, one sub-block late */
        int32_t pn = 0, tgt, g0 = lk_g, i, *po = out + 2u * sb;   /* (po: this sub-block's output, one late) */
        /* first the bypass, then the DC blocker and LEVEL (the limiter's peak scan of this sub-block is the only
         * reader): the bypass crossfades in 5.8 ms (the effects keep running, their tails stay current); (0.8: a +6 dB
         * push here put single notes on the limiter: everything squashed; the jack gain is APP_OUT_SHIFT). Each sample
         * goes through them independently, so a pass per stage is the one fused loop sample for sample (0.9.5: that
         * loop spilled, 88 instructions a sample with LEVEL off unity; the bypass pass, then 30 / 36 a sample) */
        if (byp && wet == 0) {                         /* bypassed: the dry signal ((x 0 + dry x 32768) >> 15) */
            if (!rh_side_on) for (j = sb; j < sb + LK; j++) fx_L[j] = fx_R[j] = mono[j];
            else fx_spread_dry(mono, fx_L, fx_R, sb);
        } else if (byp || wet != 32768) {              /* the crossfade (either way) */
            for (j = sb; j < sb + LK; j++) {
                if (byp ? wet > 0 : wet < 32768) wet += byp ? -128 : 128;
                if (wet != 32768) {
                    fx_L[j] = (int32_t)(((int64_t)fx_L[j] * wet + (int64_t)mono[j] * (32768 - wet)) >> 15);
                    fx_R[j] = (int32_t)(((int64_t)fx_R[j] * wet + (int64_t)mono[j] * (32768 - wet)) >> 15);
                }
            }
        }
        if (lvl == 4096) {                             /* the usual case: LEVEL at unity */
            for (j = sb; j < sb + LK; j++) {
                int32_t l = dc_block(fx_L[j], &dl, &el), r = dc_block(fx_R[j], &dr, &er), al = iabs(l), ar = iabs(r);
                fx_L[j] = l; fx_R[j] = r;
                if (al > pn) pn = al;
                if (ar > pn) pn = ar;
            }
        } else {                                       /* LEVEL off unity, or moving: its ramp (lq, Q20) */
            for (j = sb; j < sb + LK; j++) {
                int32_t l = dc_block(fx_L[j], &dl, &el), r = dc_block(fx_R[j], &dr, &er), al, ar;
                lq += ls;
                l = (int32_t)(((int64_t)l * (lq >> 8)) >> 12); r = (int32_t)(((int64_t)r * (lq >> 8)) >> 12);
                fx_L[j] = l; fx_R[j] = r;
                al = iabs(l); ar = iabs(r);
                if (al > pn) pn = al;
                if (ar > pn) pn = ar;
            }
        }
        tgt = lk_g + ((32768 - lk_g) >> 6) + 1;        /* release */
        if (tgt > 32768) tgt = 32768;
        if (lk_need(lk_pk) < tgt) tgt = lk_need(lk_pk);
        if (lk_need(pn) < tgt) tgt = lk_need(pn);
        if (g0 == 32768 && tgt == 32768) {
            /* not limiting: the gain is 1 over the sub-block, and tgt = 32768 means the delayed sub-block's peak
             * lk_pk <= LIM_T: the output is lk_l / lk_r as they are, and its peak
             * is lk_pk (computed from those very values) */
            for (i = 0; i < (int32_t)LK; i++) {
                int32_t xl = lk_l[i], xr = lk_r[i];
                lk_l[i] = fx_L[sb + (uint32_t)i]; lk_r[i] = fx_R[sb + (uint32_t)i];
                po[2 * i] = xl; po[2 * i + 1] = xr;
            }
            if (lk_pk > pk) pk = lk_pk;
        } else for (i = 0; i < (int32_t)LK; i++) {
            /* limiting: g0 (the last sub-block's tgt, <= lk_need of its pn, which is lk_pk now) and tgt are both
             * <= lk_need(lk_pk) and the ramp stays between them, so |x g >> 15| <= lk_pk lk_need(lk_pk) >> 15 <=
             * LIM_T, under the master clip's knee (24576): the clip never acted here and is left out (0.9.5) */
            int32_t g = g0 + (int32_t)(((int64_t)(tgt - g0) * (i + 1)) >> 5), xl = lk_l[i], xr = lk_r[i];
            lk_l[i] = fx_L[sb + (uint32_t)i]; lk_r[i] = fx_R[sb + (uint32_t)i];
            if (g != 32768) { xl = (int32_t)(((int64_t)xl * g) >> 15); xr = (int32_t)(((int64_t)xr * g) >> 15); }
            po[2 * i] = xl; po[2 * i + 1] = xr;          /* (no clip: <= LIM_T) */
#if defined(DINGHY_HOST) && !defined(DINGHY_WEB)
            if (iabs(xl) > LIM_T || iabs(xr) > LIM_T) __builtin_trap();   /* the host proves it on every render */
#endif
            if (iabs(xl) > pk) pk = iabs(xl);
            if (iabs(xr) > pk) pk = iabs(xr);
        }
        lk_pk = pn;
        lk_g = tgt;
    }
    fx_wet = wet; dc_l = dl; dce_l = el; dc_r = dr; dce_r = er;
    /* silence gate: the codec mutes itself on digital zero, but while anything at all comes out its analog hiss
     * is there; a held note's last -66 dBFS lie under that hiss anyway. So after 200 ms under GATE_LO the output
     * fades (46 ms) to exact zero, and it opens within 1.5 ms of the first block above GATE_HI (0.4) */
    if (pk >= GATE_HI) { gate_quiet = 0; gate_tgt = 32768; }
    else if (pk < GATE_LO && ++gate_quiet >= 35u) { gate_quiet = 35u; gate_tgt = 0; }
    /* (every sample of out written with "=" by the limiter above: platform.c does not clear it, APP_OUT_WRITTEN in
     * frodhes.c); 0.9.5: open and staying open this block, there is nothing left to do; it was a copy from fx_L /
     * fx_R, 7 instructions a sample */
    if (gate_g != 32768 || gate_tgt != 32768)
        for (j = 0; j < n; j++) {
            if (gate_g < gate_tgt) gate_g = gate_g + 512 > gate_tgt ? gate_tgt : gate_g + 512;
            else if (gate_g > gate_tgt) gate_g = gate_g - 16 < gate_tgt ? gate_tgt : gate_g - 16;
            if (gate_g != 32768) {
                out[2u * j] = (out[2u * j] * gate_g) >> 15;
                out[2u * j + 1u] = (out[2u * j + 1u] * gate_g) >> 15;
            }
        }
    fx_peak = pk;
    if (pk < 16) { if (fx_quiet_blocks < 0xFFFFu) fx_quiet_blocks++; } else fx_quiet_blocks = 0;
}

/* ---- what the effects that run now cost on the FM-1, us per block: the bar engine's CPU model (bars.c
 * bar_cost_fixed) takes it from its budget. An effect costs while it runs: the delay and the reverb also while their
 * tails ring after they are switched off; the bypass (FX) keeps everything running. PLACEHOLDERS: split by hand from
 * the measured total (every effect + the filter LP18 / LFO: 1388 - 312 = 1076 us, tools/hil_cpu.py 2026-10-16), each
 * rounded up; replace them with tools/hil_cpu.py --fx-each (idle, one effect at a time, minus idle without effects). */
#define FX_US_COMP 90u
#define FX_US_FILTER 150u      /* LP18 + the LFO, the heaviest type */
#define FX_US_DRIVE 80u
#define FX_US_TREM 40u         /* tremolo / pan (sw 1) */
#define FX_US_WAH 90u          /* touch wah (sw 2) */
#define FX_US_PHASER 150u
#define FX_US_CHORUS 160u
#define FX_US_DELAY 150u
#define FX_US_REVERB 311u     /* 260 measured, + 51 estimated for 0.9.5's half-bands (rev_types.c) */
static uint32_t fx_cost_us(void)
{
    uint32_t us = 0, t = (uint32_t)fx_p[FXP_FL_TYPE];
    if (fx_p[FXP_RAW]) return 0;
    if (fx_p[FXP_CMP_SW]) us += FX_US_COMP;
    if (t && t <= 6u) us += FX_US_FILTER;
    if (fx_p[FXP_DRIVE] && (fx_p[FXP_DRV_SW] || dr_w)) us += drv_cost_us(FX_US_DRIVE);
    if (fx_p[FXP_TW_SW] == 1) us += FX_US_TREM;
    else if (fx_p[FXP_TW_SW] == 2) us += FX_US_WAH;
    if (fx_p[FXP_PH_SW]) us += FX_US_PHASER;
    if (fx_p[FXP_CP_SW]) us += FX_US_CHORUS;
    if (fx_p[FXP_DL_SW] || dly_lp || dly_hp || dly_cur == 3) us += FX_US_DELAY;
    us += rev_cost_us();                               /* (rev_types.c: HALL FX_US_REVERB, as before) */
    return us;
}
