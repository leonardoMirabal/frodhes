/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes effects: the Yamaha reface CP chain (IMPLEMENTATION-v3 §9):
 *   COMP -> FILTER -> DRIVE -> TREMOLO/WAH (mono -> stereo) -> PHASER -> CHORUS -> D.DELAY/A.DELAY -> REVERB (HALL)
 *   -> master   (0.9.3: the FILTER, 12 / 18 dB, with its LFO)
 * (0.4: the reface's CHO/PHA switch became two effects that can run together, and a compressor came first)
 * Mono int32 Q15 (with headroom) until the tremolo, stereo after. Every effect keeps its state while bypassed
 * (switching on never clicks); switching an effect off cuts its input and lets the tail ring.
 *
 * Ported from Felucca (GPL-3.0-only, Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments):
 * track_dist (DRIVE), dc_block and the master limiter, the HALL reverb (rev_hall and helpers), the delay core.
 * Felucca's MIT helpers (softclip, osc_sine, ...) are not in its tree: rewritten here from generated tables.
 *
 * Main loop: fx_set(id, value 0..127). ISR: fx_process(mono, out, n) adds stereo Q15 into out. */
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
       FXP_N };   /* FXP_RAW: developer bypass of the whole chain (tests) */
static volatile int32_t fx_p[FXP_N] = {[FXP_DL_FDBK] = 80, [FXP_DL_TONE] = 70, [FXP_LEVEL] = 64, [FXP_CMP_RATIO] = 3,
                                       [FXP_FL_FREQ] = 127, [FXP_LFO_RATE] = 40, [FXP_DRV_TONE] = 64,
                                       [FXP_DRV_MIX] = 127, [FXP_DRV_SW] = 1, [FXP_RV_DECAY] = 64, [FXP_RV_TONE] = 64};
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
static inline int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }
static inline int32_t mulq15(int32_t a, int32_t b) { return (a * b) >> 15; }        /* |a|,|b| small enough */
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
static uint8_t fl_was_on;
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
static inline int32_t svf_tick(const svf_t *c, int32_t *ic, int32_t x, uint32_t mode)   /* 0 LP 1 HP 2 BP 3 NOTCH */
{
    int32_t v3 = x - ic[1], v1, v2, kv1;
    v1 = (int32_t)(((int64_t)c->a1 * ic[0] + (int64_t)c->a2 * v3 + (1 << 26)) >> 27);   /* rounded: no DC creep */
    v2 = ic[1] + (int32_t)(((int64_t)c->a2 * ic[0] + (int64_t)c->a3 * v3 + (1 << 26)) >> 27);
    ic[0] = clampi(2 * v1 - ic[0], -(1 << 24), 1 << 24);
    ic[1] = clampi(2 * v2 - ic[1], -(1 << 24), 1 << 24);
    kv1 = (int32_t)(((int64_t)c->k * v1 + (1 << 23)) >> 24);
    return mode == 0u ? v2 : mode == 1u ? x - kv1 - v2 : mode == 2u ? kv1 : x - kv1;
}
static void fx_filter(int32_t *m, uint32_t n)
{
    static const uint8_t MODE[7] = {0, 0, 0, 1, 1, 2, 3}, POLES[7] = {0, 2, 3, 2, 3, 2, 2};
    uint32_t type = fx_p[FXP_FL_TYPE], inc = fx_lfo_sync_inc ? fx_lfo_sync_inc : FX_LFO_INC[fx_p[FXP_LFO_RATE]], sb, j;
    int32_t k = (int32_t)FX_FL_K[fx_p[FXP_FL_RESO]], base = fx_p[FXP_FL_FREQ] * 516, depth = fx_p[FXP_FL_LFO];
    uint32_t mode, poles;
    if (fx_lfo_retrig) { fx_lfo_retrig = 0; lfo_ph = 0; }
    if (!type || type > 6u) { fl_was_on = 0; lfo_ph += inc * n; return; }   /* off: the LFO keeps time, the filter rests */
    mode = MODE[type]; poles = POLES[type];
    if (!fl_was_on) { memset(fl_ic, 0, sizeof fl_ic); fl_op = 0; fl_pos = base; fl_was_on = 1; }
    for (sb = 0; sb < n; sb += 32u) {
        uint32_t prev = lfo_ph, i;
        int32_t tgt, g, f, gp = 0;
        svf_t c;
        lfo_ph += inc * 32u;
        if (lfo_ph < prev) { lfo_rnd = lfo_rnd * 1664525u + 1013904223u; lfo_sh = (int32_t)(lfo_rnd >> 16) - 32768; }
        tgt = clampi(base + (int32_t)(((int64_t)lfo_value() * depth * 28932) >> 22), 0, 65535);   /* 4 octaves */
        fl_pos += (tgt - fl_pos) >> 2;
        i = (uint32_t)fl_pos >> 8; f = fl_pos & 255;
        g = (int32_t)FX_FL_G[i] + ((((int32_t)FX_FL_G[i + 1] - (int32_t)FX_FL_G[i]) * f) >> 8);         /* Q24 */
        svf_coef(&c, g, k);
        if (poles == 3u) gp = (1 << 27) - recip27(65536u + ((uint32_t)g >> 8));                           /* g/(1+g) */
        for (j = sb; j < sb + 32u && j < n; j++) {
            int32_t x = m[j] * 8;                                            /* 8x finer inside: a lower noise floor */
            x = svf_tick(&c, fl_ic, x, mode);
            if (poles == 3u) {                                               /* 18: + a 6 dB one-pole */
                int32_t v = (int32_t)(((int64_t)(x - fl_op) * gp + (1 << 26)) >> 27), lp = v + fl_op;
                fl_op = clampi(lp + v, -(1 << 24), 1 << 24);
                x = mode == 0u ? lp : x - lp;
            }
            m[j] = (x + 4) >> 3;
        }
    }
}

/* ---- DRIVE (Felucca track_dist; per TYPE drive row: gain scale, bias, make-up). Its page (0.9.3): ON / OFF, AMOUNT,
 * TONE (after the drive: a shelf at ~1.8 kHz, 0 = the highs gone (a one-pole low-pass), 64 = flat, 127 = +6 dB) and MIX
 * (dry / driven). At TONE 64 and MIX 127 the loop is the one before the page, sample for sample. TONE's gain and the
 * wet share glide (TONE 0 .. 127 in ~23 ms, MIX in ~46 ms, ON / OFF a 46 ms crossfade): no zipper, no click. */
#define DR_TA 7700                                      /* the shelf's one-pole: 1 - e^(-2 pi 1.8 kHz / fs), Q15 */
static const int16_t DR_S[4] = {16, 20, 14, 24}, DR_BIAS[4] = {2400, 2400, 1800, 3200}, DR_MU[4] = {4096, 4096, 4300, 3700};
static int32_t dr_hp, dr_lp1, dr_lp2;
static int32_t dr_w = 32768, dr_tg = 4096, dr_tlp;      /* the wet share (Q15), the shelf's high gain (Q12), its low-pass */
static uint8_t dr_ton;                                  /* the shelf runs (its gain is not 1, or on its way) */
static void fx_drive(int32_t *m, uint32_t n)
{
    int32_t d = fx_p[FXP_DRIVE], row = fx_p[FXP_DRIVE_ROW] & 3, g, k, mk, bias, b0, tv = fx_p[FXP_DRV_TONE], mv = fx_p[FXP_DRV_MIX];
    int32_t wt = !fx_p[FXP_DRV_SW] ? 0 : mv >= 127 ? 32768 : mv * 258;          /* the wet share it goes to */
    int32_t tt = tv >= 64 ? 4096 + (((tv - 64) * 4161) >> 6) : tv * 64;         /* the shelf's gain: 0 .. 1 .. 2 (Q12) */
    uint32_t i;
    if (!d) { dr_w = wt; return; }                      /* AMOUNT 0: off, as before (no glide: there is no drive) */
    if (!wt && !dr_w) { dr_ton = 0; dr_tg = 4096; return; }   /* off, or MIX 0: the dry signal, untouched */
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
    if (wt == 32768 && dr_w == 32768 && tt == 4096 && !dr_ton) {   /* the defaults: the loop before the page */
        for (i = 0; i < n; i++) {
            int32_t x = m[i], y;
            dr_hp += (x - dr_hp + 64) >> 7;
            x = clampi(x - dr_hp, -230000, 230000);
            y = softclip((int32_t)(((int64_t)(x >> 2) * g) >> 10) + bias) - b0;
            dr_lp1 += mulq15(y - dr_lp1, k);
            dr_lp2 += mulq15(dr_lp1 - dr_lp2, k);
            m[i] = mulq15(dr_lp2, mk);
        }
        return;
    }
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], x, y;
        dr_hp += (x0 - dr_hp + 64) >> 7;
        x = clampi(x0 - dr_hp, -230000, 230000);
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

/* ---- TREMOLO (autopan / amplitude) and touch WAH; mono in, stereo out */
static uint32_t tw_ph;
static int32_t tw_gl = 32768, tw_gr = 32768, wah_env, wah_lp, wah_bp;
static int32_t wah_ein, wah_eout, wah_g = 4096, wah_pi, wah_po;   /* the wah's make-up: loudness envelopes of the
                                                 * input and the output (pre-emphasised), the gain (Q12), last samples */
static void fx_trem(const int32_t *m, int32_t *L, int32_t *R, uint32_t n)
{
    int32_t sw = fx_p[FXP_TW_SW], depth = fx_p[FXP_TW_DEPTH], rate = fx_p[FXP_TW_RATE];
    int32_t wg = 26214 + ((depth < 16 ? 16 : depth) - 32) * 159;   /* wah: x0.80 at depth 32 .. x1.26 at 127 */
    uint32_t inc = fx_tw_sync_inc ? fx_tw_sync_inc : FX_LFO_INC[rate], sb, j;
    for (sb = 0; sb < n; sb += 32u) {
        int32_t tl = 32768, tr = 32768, dl, dr, gl = tw_gl, gr = tw_gr;
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
            int32_t ei = wah_ein, eo = wah_eout, tg = 4096;
            while (ei > 0x7FFFF || eo > 0x7FFFF) { ei >>= 1; eo >>= 1; }
            if (eo > 8) tg = clampi((ei << 12) / eo, 512, 32768);    /* x0.125 .. x8 */
            wah_g += (tg - wah_g) >> 3;                  /* ~10 ms per step of a sub-block: no zipper, no pumping */
        }
        dl = (tl - gl) >> 5; dr = (tr - gr) >> 5;
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
                x = (9 * ((wah_bp >> 2) * q >> 11) + 4 * wah_lp) / 10;   /* bp x 2q: the resonant peak's gain held,
                                                                         * louder playing no longer gets louder (0.7) */
                {   /* ~46 ms envelopes of the input and the filtered output, each pre-emphasised (x - 3/4 x[-1]:
                     * +12 dB from the bass to the treble, roughly the ear's weighting) so a closed wah's rumble does
                     * not count as loudness */
                    int32_t pi = m[j] - ((3 * wah_pi) >> 2), po = x - ((3 * wah_po) >> 2);
                    wah_pi = m[j]; wah_po = x;
                    wah_ein += (((pi < 0 ? -pi : pi) << 8) - wah_ein) >> 11;    /* x 2^8: a Q15 level's steps */
                    wah_eout += (((po < 0 ? -po : po) << 8) - wah_eout) >> 11;  /* survive the >> 11 */
                }
                x = (int32_t)(((int64_t)x * wah_g) >> 12);
                x = (int32_t)(((int64_t)x * wg) >> 15);  /* the depth's level law (FM-1, LUFS: +1.9 at 32, -2.0 at 127) */
            }
            gl += dl; gr += dr;
            L[j] += (int32_t)(((int64_t)x * gl) >> 15);
            R[j] += (int32_t)(((int64_t)x * gr) >> 15);
        }
        tw_gl = tl; tw_gr = tr;
    }
}

/* ---- PHASER: 4 first-order allpasses per side, the right one 30 degrees behind */
static uint32_t ph_ph;
static int32_t ph_x[2][4], ph_y[2][4], ph_fb[2], ph_hp[2];
static int32_t ph_coef(uint32_t ph, int32_t depth)
{
    int32_t q = (int32_t)(ph < 0x80000000u ? ph >> 16 : (0xFFFFFFFFu - ph) >> 16);   /* triangle 0..32767 */
    int32_t qd = 16384 + (((q - 16384) * depth) >> 7), i, f;
    qd = clampi(qd, 0, 32767);
    i = qd >> 10; f = qd & 1023;
    return FX_PH_A[i] + (((FX_PH_A[i + 1] - FX_PH_A[i]) * f) >> 10);
}
static void fx_phaser(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t depth = fx_p[FXP_PH_DEPTH], fbk = fx_p[FXP_PH_FDBK] * 180;
    /* the mix: 0.70 x (dry + allpasses): +3 dB at most where they are in phase, the notches cut (0.56, a cut-only
     * 50/50 mix, came out 2-5 LU quieter on the FM-1 and dropped the phaser presets). It was 0.707 x a depth law fitted on the FM-1 by LUFS (0.8): 0.81 at DEPTH 80, so a low note (the
     * notches above it) came out +4 LU, the bass +6 dB, and the feedback resonated on the bass: heard as a jump in
     * level when switched on (the user, 2026-10-17). The feedback now passes a DC block and the sides are 30 degrees
     * apart (they were 90: a mono sound turned very wide). */
    int32_t pg = 22940 << 15;
    uint32_t inc = fx_ph_sync_inc ? fx_ph_sync_inc : FX_LFO_INC[fx_p[FXP_PH_SPEED]] >> 1, sb, j, s;
    if (!fx_p[FXP_PH_SW]) return;
    for (sb = 0; sb < n; sb += 32u) {
        int32_t a[2];
        a[0] = ph_coef(ph_ph, depth);
        a[1] = ph_coef(ph_ph + 0x15555555u, depth);   /* the right side 30 degrees behind (was 90: a mono sound
                                                       * turned very wide, heard as a jump in level, the user 2026-10-17) */
        for (s = 0; s < 2u; s++) {                   /* the sides are independent: one at a time, the state in
                                                        * registers (the same arithmetic as per sample and side) */
            int32_t *io = s ? R : L, as = a[s], fb = ph_fb[s], hp = ph_hp[s], g13 = pg >> 16;
            int32_t x0 = ph_x[s][0], x1 = ph_x[s][1], x2 = ph_x[s][2], x3 = ph_x[s][3];
            int32_t y0 = ph_y[s][0], y1 = ph_y[s][1], y2 = ph_y[s][2], y3 = ph_y[s][3];
            for (j = sb; j < sb + 32u && j < n; j++) {   /* at 4x the bus (it ran at a quarter: x >> 2 in, the allpasses'
                                                         * rounding x 4 out: a broadband floor ~55 dB under the note,
                                                         * heard as hiss / air and "more volume", TODO #7); the products
                                                         * 32 x 32 -> 64, one instruction each */
                int32_t x = io[j], xs = clampi(x, -65535, 65535) << 2;   /* x 4 inside: 2 more bits */
                int32_t u = xs + (int32_t)(((int64_t)fb * fbk) >> 15), y;
                y = clampi(x0 + (int32_t)(((int64_t)as * (u - y0) + 8192) >> 14), -1048575, 1048575); x0 = u; y0 = y; u = y;
                y = clampi(x1 + (int32_t)(((int64_t)as * (u - y1) + 8192) >> 14), -1048575, 1048575); x1 = u; y1 = y; u = y;
                y = clampi(x2 + (int32_t)(((int64_t)as * (u - y2) + 8192) >> 14), -1048575, 1048575); x2 = u; y2 = y; u = y;
                y = clampi(x3 + (int32_t)(((int64_t)as * (u - y3) + 8192) >> 14), -1048575, 1048575); x3 = u; y3 = y;
                hp += (y - hp) >> 6;                    /* the feedback without its lows (~110 Hz): it resonated on
                                                         * the bass (C2 +4 LU with the phaser on) */
                fb = clampi(y - hp, -524287, 524287);
                io[j] = (int32_t)(((int64_t)(xs + y) * g13) >> 16);   /* 0.70 x (dry + allpasses), / 4 */
            }
            ph_x[s][0] = x0; ph_x[s][1] = x1; ph_x[s][2] = x2; ph_x[s][3] = x3;
            ph_y[s][0] = y0; ph_y[s][1] = y1; ph_y[s][2] = y2; ph_y[s][3] = y3;
            ph_fb[s] = fb; ph_hp[s] = hp;
        }
        ph_ph += inc * 32u;
    }
}

/* ---- CHORUS: 7 ms +- 3.5 ms, two taps 90 degrees apart */
static uint32_t cp_ph;
static int32_t cho_buf[1024] POOL;                     /* full precision (it was int16 of x / 2: a noise floor
                                                         * ~65 dB under the note, TODO #7) */
static uint32_t cho_w;
static void fx_chorus(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t depth = fx_p[FXP_CP_DEPTH];
    uint32_t inc = fx_cp_sync_inc ? fx_cp_sync_inc : FX_LFO_INC[fx_p[FXP_CP_SPEED]] >> 1, sb, j;
    if (!fx_p[FXP_CP_SW]) return;
    for (sb = 0; sb < n; sb += 32u) {
        int32_t dlq = 308 * 256 + ((osc_sine(cp_ph) * (154 * depth / 127)) >> 7);
        int32_t drq = 308 * 256 + ((osc_sine(cp_ph + 0x40000000u) * (154 * depth / 127)) >> 7);
        int32_t mix = (16384 * (2 * depth > 127 ? 127 : 2 * depth)) / 127;
        int32_t cg = 32768 + (mix * 1950 >> 14) + (((mix * (16384 - mix)) >> 14) * 11 >> 3);   /* + half-mix comb */
        for (j = sb; j < sb + 32u && j < n; j++) {
            int32_t x = (L[j] + R[j]) >> 1, tl, tr;
            uint32_t i0 = (cho_w - (uint32_t)(dlq >> 8)) & 1023u, i1 = (cho_w - (uint32_t)(drq >> 8)) & 1023u;
            cho_buf[cho_w & 1023u] = clampi(x, -1048575, 1048575);
            tl = cho_buf[i0] + (int32_t)(((int64_t)(cho_buf[(i0 - 1u) & 1023u] - cho_buf[i0]) * (dlq & 255)) >> 8);
            tr = cho_buf[i1] + (int32_t)(((int64_t)(cho_buf[(i1 - 1u) & 1023u] - cho_buf[i1]) * (drq & 255)) >> 8);
            L[j] += mulq15(tl - L[j], mix);
            R[j] += mulq15(tr - R[j], mix);
            L[j] = (L[j] * cg) >> 15;                   /* the comb's loss, minus the stereo width's loudness (0.8) */
            R[j] = (R[j] * cg) >> 15;
            cho_w++;
        }
        cp_ph += inc * 32u;
    }
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
static void fx_comp(int32_t *m, uint32_t n)
{
    /* the four knobs (0.8): THRESHOLD 0 .. -40 dBFS, RATIO 1.5:1 .. 20:1, ATTACK, MAKEUP 0 .. +24 dB; RELEASE CC 109 */
    static const int16_t SLOPE[8] = {1365, 2048, 2731, 3072, 3413, 3584, 3686, 3891};   /* 1 - 1/R, Q12 */
    int32_t ka = (int32_t)FX_CMP_ATK[fx_p[FXP_CMP_ATK]], kr = (int32_t)FX_CMP_REL[fx_p[FXP_CMP_REL]];
    int32_t thr = -((fx_p[FXP_CMP_AMT] * 27434) >> 7);              /* log2 Q12 re full scale: 0 .. -6.64 (-40 dB) */
    int32_t slope = SLOPE[fx_p[FXP_CMP_RATIO] & 7];
    int32_t mk = (fx_p[FXP_CMP_GAIN] * 16461) >> 7;                /* make-up, log2 Q12: 0 .. 3.99 (+24 dB) */
    uint32_t sb, j;
    if (!fx_p[FXP_CMP_SW]) { fx_cmp_gr = 0; return; }
    for (sb = 0; sb < n; sb += 16u) {
        int32_t g0 = cmp_g, g1, over, gr, lvl, step;
        for (j = sb; j < sb + 16u && j < n; j++) {                  /* detector: |x| in Q8 */
            int32_t a = iabs(m[j]) << 8, d = a - cmp_env;
            cmp_env += (int32_t)(((int64_t)d * (d > 0 ? ka : kr)) >> 16);
        }
        lvl = cmp_env > 256 ? log2_q12((uint32_t)cmp_env) - 23 * 4096 : -16 * 4096;   /* re 32768 << 8 */
        over = lvl - thr;
        if (over <= -2048) gr = 0;                                  /* the knee: +-3 dB around the threshold */
        else if (over < 2048) gr = (int32_t)(((int64_t)slope * (over + 2048) * (over + 2048)) >> 25);
        else gr = (slope * over) >> 12;
        fx_cmp_gr = gr;
        g1 = exp2_q16(mk - gr);
        step = (g1 - g0) / 16;
        for (j = sb; j < sb + 16u && j < n; j++) {
            g0 += step;
            m[j] = (int32_t)(((int64_t)m[j] * g0) >> 16);
        }
        cmp_g = g1;
    }
}

/* ---- D.DELAY / A.DELAY: one line of 1.486 s (int16 at half level) */
#define DLY_LEN 65536u
static int16_t dly_buf[DLY_LEN] POOL;
static uint32_t dly_w, dly_wow, dly_flt;
static int32_t dly_d = 4410 << 8, dly_lp, dly_hp, dly_dg = 32768, dly_sg;   /* dly_sg: the send's stereo make-up, log2 Q12 */
/* the delay's sample loop, one version for TAPE (tape = 1) and one for DIGITAL / off (tape = 0: a constant after
 * inlining), the state in locals: the same arithmetic per sample as one loop with the tests inside. msk: the send on
 * (-1) or off (0, the tail rings); sg16: the send's make-up (Q16, exactly 65536 when dly_sg is 0: x 1); dgt: the dry's
 * gain it eases to. */
static inline __attribute__((always_inline)) void dly_loop(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                           int32_t msk, int32_t sg16, int32_t target, int32_t col,
                                                           int32_t fb, int32_t wet, int32_t dgt)
{
    uint32_t w = dly_w, wow = dly_wow, flt = dly_flt, j;
    int32_t dd = dly_d, lp = dly_lp, hp = dly_hp, dg = dly_dg;
    FX_K32 ksg = sg16;
    for (j = 0; j < n; j++) {
        int32_t d, r, in = ((L[j] + R[j]) >> 1) & msk, f;
        uint32_t i;
        in = (int32_t)(((int64_t)in * ksg) >> 16);
        if (tape) {                                      /* tape: slewed time, wow and flutter */
            int32_t sf;
            dd += (target - dd) >> 12;
            wow += 69000u; flt += 690000u;              /* ~0.7 Hz, ~7 Hz */
            sf = osc_sine(flt);
            sf = (sf + (int32_t)((uint32_t)sf >> 31)) >> 1;   /* sf / 2 (towards 0) without the divide */
            d = dd + (int32_t)(((int64_t)dd * (osc_sine(wow) * 3 + sf)) >> 25);
        } else {
            if (dd < target) dd = dd + 256 > target ? target : dd + 256;
            else if (dd > target) dd = dd - 256 < target ? target : dd - 256;
            d = dd;
        }
        i = (w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        r = (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
        lp += mulq15(r - lp, col);
        f = tape ? softclip(lp) : lp;
        hp += (f - hp + 64) >> 7;
        f -= hp;
        dly_buf[w & (DLY_LEN - 1u)] = (int16_t)clampi((in >> 1) + mulq15(f, fb), -32768, 32767);
        w++;
        r = mulq15(r, wet);
        dg += (dgt - dg) >> 9;
        L[j] = (int32_t)(((int64_t)L[j] * dg) >> 15) + r; R[j] = (int32_t)(((int64_t)R[j] * dg) >> 15) + r;
    }
    dly_w = w; dly_wow = wow; dly_flt = flt; dly_d = dd; dly_lp = lp; dly_hp = hp; dly_dg = dg;
}
static void fx_delay(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t sw = fx_p[FXP_DL_SW], depth = fx_p[FXP_DL_DEPTH];
    uint32_t sync = fx_dly_sync_samples, j;
    int32_t target = (int32_t)(sync ? sync : FX_DLY_T[fx_p[FXP_DL_TIME]]);
    int32_t wet = depth * 258, fb = (depth * 180 * fx_p[FXP_DL_FDBK]) >> 7, col = 2000 + fx_p[FXP_DL_TONE] * 240, sg16;
    if (sw == 2) col = (col * 19661) >> 15;                 /* tape: darker */
    while (target >= (int32_t)DLY_LEN - 2) target >>= 1;
    target <<= 8;
    if (fb > 13926) fb = 13926;                             /* the loop gain is 2 fb (the line holds half level): at most
                                                             * 0.85, the echoes always die (they grew forever over MIX x
                                                             * FDBK ~ 11650: the audit, 2026-10-17) */
    if (!sw && !dly_lp && !dly_hp) { dly_w += n; return; }    /* fully quiet and off: only keep time */
    {   /* the send is the mono sum (L + R) / 2; when the phaser / chorus / pan make L and R differ, the sum loses up to
         * 3-6 dB (the echoes "lost" under the chorus or phaser: the user, 2026-10-18). The send keeps the stereo
         * power instead: x sqrt(mean(L^2 + R^2) / 2 / mean(sum^2)), per block, gliding, at most +6 dB; exactly 1 when
         * L == R (the old send, sample for sample) */
        int64_t e = 0, m = 0;
        int32_t tg = 0;
        if (sw) for (j = 0; j < n; j++) {
            int32_t a = L[j], b = R[j], c = (a + b) >> 1;
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
    sg16 = exp2_q16(dly_sg);
    /* the dry eases down as the mix rises (-0.7 dB at 50, -5.9 dB at 127): a full-mix delay no longer jumps +5 LU (0.8) */
    if (sw == 2) dly_loop(L, R, n, 1, -1, sg16, target, col, fb, wet, 32768 - depth * depth);
    else dly_loop(L, R, n, 0, sw ? -1 : 0, sg16, target, col, fb, wet, sw ? 32768 - depth * depth : 32768);
    if (!sw && iabs(dly_lp) < 2) dly_lp = dly_hp = 0;
}

/* ---- REVERB: Felucca's HALL (fx.c, GPL-3.0-only), unchanged except for its parameters' source and its internal
 * level: the int16 lines run 4x hotter (HL_IN) and the output gain is 4x lower (HL_OUT), the same reverb level
 * with 12 dB less rounding noise (at a piano's -9 dBFS the original level left the lines a few hundred LSB wide:
 * an audible hiss under every note, 0.4) */
#define CTL 32u
static int16_t rev_comb[1116 + 1188 + 1277 + 1356] POOL;
static int16_t rev_ap[556 + 441] POOL;
#define HL_NL 8u
#define HL_NAP 2u
#define HL_NC (CTL / 2u)
#define HL_MOD 8
#define HL_LPM 0x69u
#define HL_KD(d) (32767 - (d) * 180)
#define HL_LOUD 32
#define HL_OUT 1
#define HL_IN 10320                 /* Felucca: 2580 with HL_OUT 4 */
static const uint16_t HL_LEN[HL_NL] = {467, 509, 547, 593, 631, 673, 709, 743};
static const uint16_t HL_AP[HL_NAP] = {131, 307};
static const int16_t HL_APG[HL_NAP] = {11469, 10240};
static struct {
    uint16_t i[HL_NL], d[HL_NAP];
    int32_t lp[HL_NL], le[HL_NL];
    int32_t x, m, s, hp, he;
    int16_t g[HL_NL], sz;
    uint32_t ph;
    int32_t bx[HL_NC], l[HL_NC], r[HL_NC];
    int16_t y[HL_NL][HL_NC];
} hl POOL;
static inline int32_t sh_tz(int32_t m, uint32_t sh) { return (m + ((m >> 31) & ((1 << sh) - 1))) >> sh; }
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
static inline void hall_ap(int16_t *q, int32_t g, int32_t *x, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t y = q[i], v = x[i] + sh_tz(y * g, 14);
        q[i] = (int16_t)clampi(v, -32768, 32767);
        x[i] = y - ((v * g + 8192) >> 14);
    }
}
static inline void hall_end(const int16_t *b, int32_t f, int16_t *y, uint32_t n, int32_t g, int lo)
{
    uint32_t i;
    if (lo)
        for (i = 0; i < n; i++) y[i] = (int16_t)sh_tz((b[i] + (((b[i + 1u] - b[i]) * f + 128) >> 8)) * g, 14);
    else
        for (i = 0; i < n; i++) y[i] = (int16_t)(((b[i] + (((b[i + 1u] - b[i]) * f + 128) >> 8)) * g + 8192) >> 14);
}
static inline void hall_end_lp(const int16_t *b, int16_t *y, uint32_t n, int32_t g, int lo, int32_t *lp, int32_t *le,
                               int32_t kd)
{
    uint32_t i;
    int32_t l = *lp, r = *le, e;
    if (lo)
        for (i = 0; i < n; i++) { e = (b[i] - l) * kd + r, r = e & 32767, l += e >> 15; y[i] = (int16_t)sh_tz(l * g, 14); }
    else
        for (i = 0; i < n; i++) { e = (b[i] - l) * kd + r, r = e & 32767, l += e >> 15; y[i] = (int16_t)((l * g + 8192) >> 14); }
    *lp = l;
    *le = r;
}
#define HL_RUNS(d, len, k, s, body)                                                         \
    for (s = 0; s < HL_NC; s += k) {                                                        \
        k = (len) - (d) < HL_NC - s ? (len) - (d) : HL_NC - s;                              \
        body;                                                                               \
        d = (d) + k >= (len) ? 0u : (d) + k;                                                \
    }
#define HL_LFO ((uint32_t)(0.37 * CTL / FS * 4294967296.0))
static void rev_hall(const int32_t *rev_in, int32_t *mid, int32_t *side, int32_t size, int32_t damp)
{
    int16_t *b = rev_comb, *a = rev_ap;
    uint32_t i, k, s, m, d;
    int32_t kd = HL_KD(damp);
    int32_t xp = hl.x, hp = hl.hp, he = hl.he, mp = hl.m, sp = hl.s;
    if (hl.sz != size + 1) hall_gains(size);
    for (i = 0; i < HL_NC; i++) {
        int32_t x = (xp + 2 * rev_in[2u * i] + rev_in[2u * i + 1u]) >> 2, v;
        xp = rev_in[2u * i + 1u];
        x = mulq15(clampi(x, -131071, 131071), HL_IN);
        v = x - hp + he;
        he = v & 31;
        hp += v >> 5;
        hl.bx[i] = x - hp;
    }
    hl.x = xp, hl.hp = hp, hl.he = he;
    for (k = 0; k < HL_NAP; k++) {
        d = hl.d[k];
        HL_RUNS(d, HL_AP[k], m, s, hall_ap(a + d, HL_APG[k], hl.bx + s, m));
        hl.d[k] = (uint16_t)d;
        a += HL_AP[k];
    }
    hl.ph += HL_LFO;
    for (k = 0; k < HL_NL; k++) {
        int32_t g = hl.g[k], f = 0, lo;
        if ((HL_LPM >> k) & 1u)
            d = hl.i[k];
        else {
            uint32_t r = (uint32_t)(((HL_MOD + 2) << 8) + ((osc_sine(hl.ph * (1u + (k & 1u)) + k * 0x2C000000u) * HL_MOD) >> 7));
            f = (int32_t)(r & 255u);
            d = hl.i[k] + (r >> 8);
            d = d >= HL_LEN[k] ? d - HL_LEN[k] : d;
        }
        s = d + HL_LEN[k] / 2u;
        s = s >= HL_LEN[k] ? s - HL_LEN[k] : s;
        lo = (b[d] < 0 ? -b[d] : b[d]) + (b[s] < 0 ? -b[s] : b[s]) < HL_LOUD;
        if ((HL_LPM >> k) & 1u) {
            HL_RUNS(d, HL_LEN[k], m, s, hall_end_lp(b + d, hl.y[k] + s, m, g, lo, &hl.lp[k], &hl.le[k], kd));
        } else {
            HL_RUNS(d, HL_LEN[k], m, s, hall_end(b + d, f, hl.y[k] + s, m, g, lo));
        }
        b += HL_LEN[k] + 1u;
    }
    for (i = 0; i < HL_NC; i++) {
        int32_t y0 = hl.y[0][i], y1 = hl.y[1][i], y2 = hl.y[2][i], y3 = hl.y[3][i];
        int32_t y4 = hl.y[4][i], y5 = hl.y[5][i], y6 = hl.y[6][i], y7 = hl.y[7][i], x = hl.bx[i];
        int32_t t, p0, p1, p2, p3, p4, p5, p6;
        hl.l[i] = y0 + y3 + y4 - y7;
        hl.r[i] = y1 + y2 + y5 + y6;
        t = y1 + y2 + y3 + y4 + y5 + y6 + y7;
        p0 = y2 + y3 + y5, p1 = y3 + y4 + y6, p2 = y4 + y5 + y7, p3 = y5 + y6 + y1;
        p4 = y6 + y7 + y2, p5 = y7 + y1 + y3, p6 = y1 + y2 + y4;
        y0 += t;
        hl.y[0][i] = (int16_t)clampi(t + x, -32768, 32767);
        hl.y[1][i] = (int16_t)clampi(2 * p0 + y1 - y0 + x, -32768, 32767);
        hl.y[2][i] = (int16_t)clampi(2 * p1 + y2 - y0 + x, -32768, 32767);
        hl.y[3][i] = (int16_t)clampi(2 * p2 + y3 - y0 + x, -32768, 32767);
        hl.y[4][i] = (int16_t)clampi(2 * p3 + y4 - y0 + x, -32768, 32767);
        hl.y[5][i] = (int16_t)clampi(2 * p4 + y5 - y0 + x, -32768, 32767);
        hl.y[6][i] = (int16_t)clampi(2 * p5 + y6 - y0 + x, -32768, 32767);
        hl.y[7][i] = (int16_t)clampi(2 * p6 + y7 - y0 - x, -32768, 32767);
    }
    b = rev_comb;
    for (k = 0; k < HL_NL; k++) {
        d = hl.i[k];
        HL_RUNS(d, HL_LEN[k], m, s, for (i = 0; i < m; i++) b[d + i] = hl.y[k][s + i]; if (!d) b[HL_LEN[k]] = b[0]);
        hl.i[k] = (uint16_t)d;
        b += HL_LEN[k] + 1u;
    }
    for (i = 0; i < HL_NC; i++) {
        int32_t mm = (hl.l[i] + hl.r[i]) * HL_OUT, ss = (hl.l[i] - hl.r[i]) * HL_OUT;
        mid[2u * i] = (mp + mm) >> 1;
        mid[2u * i + 1u] = mm;
        side[2u * i] = (sp + ss) >> 1;
        side[2u * i + 1u] = ss;
        mp = mm;
        sp = ss;
    }
    hl.m = mp, hl.s = sp;
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
static int32_t rv_in[CTL], rv_mid[CTL], rv_side[CTL];
static int32_t rv_off, rv_damp = 60;                   /* DECAY's size offset and TONE's damping, as they glide */
static uint8_t rv_running;
static void fx_predelay(int32_t *x, uint32_t tgt)      /* one control block: x[] delayed by the tap */
{
    uint32_t j;
    if (!pd_on) { memset(pd_buf, 0, sizeof pd_buf); pd_d = pd_d0 = pd_x = 0; pd_on = 1; }   /* no old sound in it */
    if (!pd_x && tgt != pd_d) { pd_d0 = pd_d; pd_d = tgt; pd_x = 1; }
    for (j = 0; j < CTL; j++) {
        int32_t a;
        pd_buf[pd_w & (PD_LEN - 1u)] = x[j];
        a = pd_buf[(pd_w - pd_d) & (PD_LEN - 1u)];
        if (pd_x) {                                     /* 256 samples from the old tap to the new one */
            int32_t b = pd_buf[(pd_w - pd_d0) & (PD_LEN - 1u)];
            a = b + (int32_t)(((int64_t)(a - b) * (int32_t)(pd_x << 7)) >> 15);
            if (++pd_x > 256u) pd_x = 0;
        }
        x[j] = a;
        pd_w++;
    }
    if (!pd_x && !pd_d && !tgt) pd_on = 0;              /* back at 0: no line */
}
static void fx_reverb(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t v = fx_p[FXP_RV_DEPTH], send = v * 258, size = 40 + v * 70 / 127;
    int32_t dv = fx_p[FXP_RV_DECAY], tv = fx_p[FXP_RV_TONE];
    int32_t off = ((dv - 64) * 15) / 32;                /* -30 .. +29 size steps: x0.5 .. x1.96 in T60 */
    int32_t damp = tv >= 64 ? 60 - ((tv - 64) * 60) / 63 : 60 + ((64 - tv) * 90) / 64;   /* 150 .. 60 .. 0 */
    uint32_t sb, j, pd = FX_PDLY_SAMPLES(fx_p[FXP_RV_PREDLY]);
    if (!v && !rv_running) return;
    rv_running = 1;
    for (sb = 0; sb < n; sb += CTL) {
        int32_t act = 0;
        for (j = 0; j < CTL; j++)                      /* 64-bit: the bus can be several times full scale here
                                                        * (feedback effects, many v127 notes: fuzz seed 95) */
            rv_in[j] = (int32_t)(((int64_t)((L[sb + j] + R[sb + j]) >> 1) * send) >> 15);
        if (pd || pd_on) fx_predelay(rv_in, pd);
        if (rv_off != off) rv_off += rv_off < off ? 1 : -1;
        if (rv_damp != damp) rv_damp += rv_damp < damp ? (damp - rv_damp > 2 ? 2 : damp - rv_damp)
                                                      : -(rv_damp - damp > 2 ? 2 : rv_damp - damp);
        rev_hall(rv_in, rv_mid, rv_side, size + rv_off, rv_damp);
        for (j = 0; j < CTL; j++) {                    /* the return eases off as the send rises: x1 .. x0.5 (0.6) */
            int32_t wm = (rv_mid[j] * (254 - v)) / 254, ws = (rv_side[j] * (254 - v)) / 254;
            L[sb + j] += wm + ws;
            R[sb + j] += wm - ws;
            act |= rv_mid[j] | rv_side[j];
        }
        if (!v && !act) { rv_running = 0; pd_on = 0; }  /* the tail has ended in exact silence (the line: cleared at
                                                         * its next use) */
    }
}

/* ---- master: DC blocker, peak limiter, soft clip (Felucca master_out) */
#define LIM_T 24000                                     /* -2.7 dBFS: the look-ahead limiter's ceiling (0.8) */
#define CLIP_K 24576                                    /* the soft clip is linear up to -2.5 dBFS (0.8) */
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
/* the master clip: exactly linear up to CLIP_K, a tanh knee above it to full scale. The old tanh over the whole
 * range bent everything from about -12 dBFS up: ~2 % distortion on chords (FM-1, headphones, 0.8) */
static inline int32_t clip_knee(int32_t x)
{
    int32_t a = x < 0 ? -x : x, y;
    if (a <= CLIP_K) return x;
    y = CLIP_K + ((8191 * softclip((a - CLIP_K) << 2)) >> 15);   /* (32767 - K) = 8191; tanh of the excess */
    return x < 0 ? -y : y;
}
#define GATE_LO 16                                      /* -66 dBFS (Q15) */
#define GATE_HI 32
static int32_t gate_g = 32768, gate_tgt = 32768, fx_wet = 32768;
static uint32_t gate_quiet;
static void fx_process(const int32_t *mono, int32_t *out, uint32_t n)
{
    static int32_t m[256];
    int32_t lvl = (int32_t)FX_LVL[fx_p[FXP_LEVEL] & 127];   /* the preset's volume trim, Q12 */
    uint32_t j, sb;
    int32_t pk = 0, wet = fx_wet, byp = fx_bypass, dl = dc_l, el = dce_l, dr = dc_r, er = dce_r;
    if (fx_p[FXP_RAW]) {                               /* developer: the voice engine's output untouched */
        for (j = 0; j < n; j++) { out[2u * j] += mono[j]; out[2u * j + 1u] += mono[j]; }
        return;
    }
    for (j = 0; j < n; j++) { m[j] = mono[j]; fx_L[j] = fx_R[j] = 0; }   /* (mono is the dry signal for the bypass) */
    fx_comp(m, n);
    fx_filter(m, n);
    fx_drive(m, n);
    fx_trem(m, fx_L, fx_R, n);
    fx_phaser(fx_L, fx_R, n);
    fx_chorus(fx_L, fx_R, n);
    fx_delay(fx_L, fx_R, n);
    fx_reverb(fx_L, fx_R, n);
    for (sb = 0; sb < n; sb += LK) {                   /* the look-ahead limiter, one sub-block late */
        int32_t pn = 0, tgt, g0 = lk_g, i;
        /* first the bypass, the DC blocker and LEVEL, sample by sample in the same order as a pass of their own (the
         * limiter's peak scan of this sub-block is the only reader): the bypass crossfades in 5.8 ms (the effects
         * keep running, their tails stay current); (0.8: a +6 dB push here put single notes on the limiter: everything
         * squashed; the jack gain is APP_OUT_SHIFT) */
        if (!byp && wet == 32768 && lvl == 4096) {     /* the usual case: effects on, LEVEL at unity */
            for (j = sb; j < sb + LK; j++) {
                int32_t l = dc_block(fx_L[j], &dl, &el), r = dc_block(fx_R[j], &dr, &er), al = iabs(l), ar = iabs(r);
                fx_L[j] = l; fx_R[j] = r;
                if (al > pn) pn = al;
                if (ar > pn) pn = ar;
            }
        } else {
            for (j = sb; j < sb + LK; j++) {
                int32_t l, r, al, ar;
                if (byp ? wet > 0 : wet < 32768) wet += byp ? -128 : 128;
                if (wet != 32768) {
                    fx_L[j] = (int32_t)(((int64_t)fx_L[j] * wet + (int64_t)mono[j] * (32768 - wet)) >> 15);
                    fx_R[j] = (int32_t)(((int64_t)fx_R[j] * wet + (int64_t)mono[j] * (32768 - wet)) >> 15);
                }
                l = dc_block(fx_L[j], &dl, &el); r = dc_block(fx_R[j], &dr, &er);
                if (lvl != 4096) { l = (int32_t)(((int64_t)l * lvl) >> 12); r = (int32_t)(((int64_t)r * lvl) >> 12); }
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
             * lk_pk <= LIM_T < CLIP_K, so the clip passes it too: the output is lk_l / lk_r as they are, and its peak
             * is lk_pk (computed from those very values) */
            for (i = 0; i < (int32_t)LK; i++) {
                int32_t xl = lk_l[i], xr = lk_r[i];
                lk_l[i] = fx_L[sb + (uint32_t)i]; lk_r[i] = fx_R[sb + (uint32_t)i];
                fx_L[sb + (uint32_t)i] = xl; fx_R[sb + (uint32_t)i] = xr;
            }
            if (lk_pk > pk) pk = lk_pk;
        } else for (i = 0; i < (int32_t)LK; i++) {
            int32_t g = g0 + (int32_t)(((int64_t)(tgt - g0) * (i + 1)) >> 5), xl = lk_l[i], xr = lk_r[i];
            lk_l[i] = fx_L[sb + (uint32_t)i]; lk_r[i] = fx_R[sb + (uint32_t)i];
            if (g != 32768) { xl = (int32_t)(((int64_t)xl * g) >> 15); xr = (int32_t)(((int64_t)xr * g) >> 15); }
            fx_L[sb + (uint32_t)i] = xl = clip_knee(xl); fx_R[sb + (uint32_t)i] = xr = clip_knee(xr);   /* (a no-op) */
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
    /* (out is 0 here: platform.c clears it before app_audio, so "=" is "+=") */
    if (gate_g == 32768 && gate_tgt == 32768)          /* open, and staying open this block */
        for (j = 0; j < n; j++) { out[2u * j] = fx_L[j]; out[2u * j + 1u] = fx_R[j]; }
    else
        for (j = 0; j < n; j++) {
            if (gate_g < gate_tgt) gate_g = gate_g + 512 > gate_tgt ? gate_tgt : gate_g + 512;
            else if (gate_g > gate_tgt) gate_g = gate_g - 16 < gate_tgt ? gate_tgt : gate_g - 16;
            out[2u * j] = gate_g == 32768 ? fx_L[j] : (fx_L[j] * gate_g) >> 15;
            out[2u * j + 1u] = gate_g == 32768 ? fx_R[j] : (fx_R[j] * gate_g) >> 15;
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
#define FX_US_REVERB 260u
static uint32_t fx_cost_us(void)
{
    uint32_t us = 0, t = (uint32_t)fx_p[FXP_FL_TYPE];
    if (fx_p[FXP_RAW]) return 0;
    if (fx_p[FXP_CMP_SW]) us += FX_US_COMP;
    if (t && t <= 6u) us += FX_US_FILTER;
    if (fx_p[FXP_DRIVE] && (fx_p[FXP_DRV_SW] || dr_w)) us += FX_US_DRIVE;
    if (fx_p[FXP_TW_SW] == 1) us += FX_US_TREM;
    else if (fx_p[FXP_TW_SW] == 2) us += FX_US_WAH;
    if (fx_p[FXP_PH_SW]) us += FX_US_PHASER;
    if (fx_p[FXP_CP_SW]) us += FX_US_CHORUS;
    if (fx_p[FXP_DL_SW] || dly_lp || dly_hp) us += FX_US_DELAY;
    if (fx_p[FXP_RV_DEPTH] || rv_running) us += FX_US_REVERB;
    return us;
}
