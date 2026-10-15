/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes effects: the Yamaha reface CP chain (IMPLEMENTATION-v3 §9):
 *   COMP -> DRIVE -> TREMOLO/WAH (mono -> stereo) -> PHASER -> CHORUS -> D.DELAY/A.DELAY -> REVERB (HALL) -> master
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
       FXP_PH_SW, FXP_PH_DEPTH, FXP_PH_SPEED, FXP_PH_FDBK, FXP_CMP_SW, FXP_CMP_AMT, FXP_CMP_ATK, FXP_CMP_REL, FXP_N };   /* FXP_RAW: developer bypass of the whole chain (tests) */
static volatile int32_t fx_p[FXP_N] = {[FXP_DL_FDBK] = 80, [FXP_DL_TONE] = 70};
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

/* ---- DRIVE (Felucca track_dist; per TYPE drive row: gain scale, bias, make-up) */
static const int16_t DR_S[4] = {16, 20, 14, 24}, DR_BIAS[4] = {2400, 2400, 1800, 3200}, DR_MU[4] = {4096, 4096, 4300, 3700};
static int32_t dr_hp, dr_lp1, dr_lp2;
static void fx_drive(int32_t *m, uint32_t n)
{
    int32_t d = fx_p[FXP_DRIVE], row = fx_p[FXP_DRIVE_ROW] & 3, g, k, mk, bias, b0;
    uint32_t i;
    if (!d) return;
    g = 4096 + ((d * d * 2 * DR_S[row]) >> 4);
    k = 32000 - d * 95;
    mk = ((30000 - d * 120) * DR_MU[row]) >> 12;
    mk = (mk * (int32_t)FX_DRV_LVL[d]) >> 15;          /* level-neutral: the drive adds tone, not volume (0.6) */
    bias = DR_BIAS[row];
    b0 = softclip(bias);
    for (i = 0; i < n; i++) {
        int32_t x = m[i], y;
        dr_hp += (x - dr_hp + 64) >> 7;
        x = clampi(x - dr_hp, -230000, 230000);
        y = softclip((((x >> 2) * g) >> 10) + bias) - b0;
        dr_lp1 += mulq15(y - dr_lp1, k);
        dr_lp2 += mulq15(dr_lp1 - dr_lp2, k);
        m[i] = mulq15(dr_lp2, mk);
    }
}

/* ---- TREMOLO (autopan / amplitude) and touch WAH; mono in, stereo out */
static uint32_t tw_ph;
static int32_t tw_gl = 32768, tw_gr = 32768, wah_env, wah_lp, wah_bp;
static void fx_trem(const int32_t *m, int32_t *L, int32_t *R, uint32_t n)
{
    int32_t sw = fx_p[FXP_TW_SW], depth = fx_p[FXP_TW_DEPTH], rate = fx_p[FXP_TW_RATE];
    uint32_t inc = fx_tw_sync_inc ? fx_tw_sync_inc : FX_LFO_INC[rate], sb, j;
    for (sb = 0; sb < n; sb += 32u) {
        int32_t tl = 32768, tr = 32768, dl, dr, gl = tw_gl, gr = tw_gr;
        if (sw == 1) {                                  /* tremolo: the LFO this sub-block */
            int32_t lfo = osc_sine(tw_ph), shp = fx_p[FXP_TW_SHAPE];
            lfo = clampi((lfo * (32768 + 7 * 258 * shp)) >> 15, -32767, 32767);   /* sine -> rounded square */
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
            }
            gl += dl; gr += dr;
            L[j] += (int32_t)(((int64_t)x * gl) >> 15);
            R[j] += (int32_t)(((int64_t)x * gr) >> 15);
        }
        tw_gl = tl; tw_gr = tr;
    }
}

/* ---- PHASER: 4 first-order allpasses per side, the right one 90 degrees behind */
static uint32_t ph_ph;
static int32_t ph_x[2][4], ph_y[2][4], ph_fb[2];
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
    uint32_t inc = fx_ph_sync_inc ? fx_ph_sync_inc : FX_LFO_INC[fx_p[FXP_PH_SPEED]] >> 1, sb, j, s, k;
    if (!fx_p[FXP_PH_SW]) return;
    for (sb = 0; sb < n; sb += 32u) {
        int32_t a[2];
        a[0] = ph_coef(ph_ph, depth);
        a[1] = ph_coef(ph_ph + 0x40000000u, depth);
        for (j = sb; j < sb + 32u && j < n; j++)
            for (s = 0; s < 2u; s++) {
                int32_t *io = s ? R : L, x = io[j], xs = clampi((x + 2) >> 2, -16383, 16383);
                int32_t u = xs + mulq15(ph_fb[s], fbk), y = 0;
                for (k = 0; k < 4u; k++) {
                    y = clampi(ph_x[s][k] + ((a[s] * (u - ph_y[s][k]) + 8192) >> 14), -65535, 65535);
                    ph_x[s][k] = u; ph_y[s][k] = y; u = y;
                }
                ph_fb[s] = clampi(y, -32767, 32767);
                io[j] = ((x + (y << 2)) * 23170) >> 15;   /* x 0.707: the notches cost ~3 dB (0.6) */
            }
        ph_ph += inc * 32u;
    }
}

/* ---- CHORUS: 7 ms +- 3.5 ms, two taps 90 degrees apart */
static uint32_t cp_ph;
static int16_t cho_buf[1024] POOL;
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
        for (j = sb; j < sb + 32u && j < n; j++) {
            int32_t x = (L[j] + R[j]) >> 1, tl, tr;
            uint32_t i0 = (cho_w - (uint32_t)(dlq >> 8)) & 1023u, i1 = (cho_w - (uint32_t)(drq >> 8)) & 1023u;
            cho_buf[cho_w & 1023u] = (int16_t)clampi(x >> 1, -32768, 32767);
            tl = (cho_buf[i0] + (((cho_buf[(i0 - 1u) & 1023u] - cho_buf[i0]) * (dlq & 255)) >> 8)) << 1;
            tr = (cho_buf[i1] + (((cho_buf[(i1 - 1u) & 1023u] - cho_buf[i1]) * (drq & 255)) >> 8)) << 1;
            L[j] += mulq15(tl - L[j], mix);
            R[j] += mulq15(tr - R[j], mix);
            L[j] = (L[j] * (32768 + (mix * 13540 >> 14))) >> 15;   /* the comb costs ~3 dB at full mix (0.6) */
            R[j] = (R[j] * (32768 + (mix * 13540 >> 14))) >> 15;
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
    int32_t amt = fx_p[FXP_CMP_AMT], ka = (int32_t)FX_CMP_ATK[fx_p[FXP_CMP_ATK]], kr = (int32_t)FX_CMP_REL[fx_p[FXP_CMP_REL]];
    int32_t thr = -6144 - amt * 20480 / 127;                       /* log2 re full scale: -1.5 .. -6.5 (-9 .. -39 dB) */
    int32_t slope = 4096 - 4096 * 2 * 127 / (3 * 127 + 9 * amt);    /* 1 - 1/R, R = 1.5 + 4.5 amt/127; Q12 */
    int32_t mk = -17736 > thr ? (slope * (-17736 - thr)) >> 12 : 0;   /* make-up: the reduction a typical piano
                                                                     * level (-26 dBFS) gets: level-neutral (0.7) */
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
static int32_t dly_d = 4410 << 8, dly_lp, dly_hp;
static void fx_delay(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t sw = fx_p[FXP_DL_SW], depth = fx_p[FXP_DL_DEPTH];
    uint32_t sync = fx_dly_sync_samples, j;
    int32_t target = (int32_t)(sync ? sync : FX_DLY_T[fx_p[FXP_DL_TIME]]);
    int32_t wet = depth * 258, fb = (depth * 180 * fx_p[FXP_DL_FDBK]) >> 7, col = 2000 + fx_p[FXP_DL_TONE] * 240;
    if (sw == 2) col = (col * 19661) >> 15;                 /* tape: darker */
    while (target >= (int32_t)DLY_LEN - 2) target >>= 1;
    target <<= 8;
    if (fb > 27853) fb = 27853;
    if (!sw && !dly_lp && !dly_hp) { dly_w += n; return; }    /* fully quiet and off: only keep time */
    for (j = 0; j < n; j++) {
        int32_t d, r, in = sw ? (L[j] + R[j]) >> 1 : 0, f;
        uint32_t i;
        if (sw == 2) {                                   /* tape: slewed time, wow and flutter */
            dly_d += (target - dly_d) >> 12;
            dly_wow += 69000u; dly_flt += 690000u;      /* ~0.7 Hz, ~7 Hz */
            d = dly_d + (int32_t)(((int64_t)dly_d * (osc_sine(dly_wow) * 3 + osc_sine(dly_flt) / 2)) >> 25);
        } else {
            if (dly_d < target) dly_d = dly_d + 256 > target ? target : dly_d + 256;
            else if (dly_d > target) dly_d = dly_d - 256 < target ? target : dly_d - 256;
            d = dly_d;
        }
        i = (dly_w - (uint32_t)(d >> 8)) & (DLY_LEN - 1u);
        r = (dly_buf[i] + (((dly_buf[(i - 1u) & (DLY_LEN - 1u)] - dly_buf[i]) * (d & 255)) >> 8)) << 1;
        dly_lp += mulq15(r - dly_lp, col);
        f = sw == 2 ? softclip(dly_lp) : dly_lp;
        dly_hp += (f - dly_hp + 64) >> 7;
        f -= dly_hp;
        dly_buf[dly_w & (DLY_LEN - 1u)] = (int16_t)clampi((in >> 1) + mulq15(f, fb), -32768, 32767);
        dly_w++;
        r = mulq15(r, wet);
        L[j] += r; R[j] += r;
    }
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
static int32_t rv_in[CTL], rv_mid[CTL], rv_side[CTL];
static uint8_t rv_running;
static void fx_reverb(int32_t *L, int32_t *R, uint32_t n)
{
    int32_t v = fx_p[FXP_RV_DEPTH], send = v * 258, size = 40 + v * 70 / 127;
    uint32_t sb, j;
    if (!v && !rv_running) return;
    rv_running = 1;
    for (sb = 0; sb < n; sb += CTL) {
        int32_t act = 0;
        for (j = 0; j < CTL; j++) rv_in[j] = mulq15((L[sb + j] + R[sb + j]) >> 1, send);
        rev_hall(rv_in, rv_mid, rv_side, size, 60);
        for (j = 0; j < CTL; j++) {                    /* the return eases off as the send rises: x1 .. x0.5 (0.6) */
            int32_t wm = (rv_mid[j] * (254 - v)) / 254, ws = (rv_side[j] * (254 - v)) / 254;
            L[sb + j] += wm + ws;
            R[sb + j] += wm - ws;
            act |= rv_mid[j] | rv_side[j];
        }
        if (!v && !act) rv_running = 0;               /* the tail has ended in exact silence */
    }
}

/* ---- master: DC blocker, peak limiter, soft clip (Felucca master_out) */
#define LIM_T 18000
static int32_t lim_env = LIM_T, dc_l, dc_r, dce_l, dce_r;
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
static void fx_process(const int32_t *mono, int32_t *out, uint32_t n)
{
    static int32_t m[256], dry[256];
    uint32_t j;
    int32_t pk = 0;
    if (fx_p[FXP_RAW]) {                               /* developer: the voice engine's output untouched */
        for (j = 0; j < n; j++) { out[2u * j] += mono[j]; out[2u * j + 1u] += mono[j]; }
        return;
    }
    for (j = 0; j < n; j++) { dry[j] = m[j] = mono[j]; fx_L[j] = fx_R[j] = 0; }
    fx_comp(m, n);
    fx_drive(m, n);
    fx_trem(m, fx_L, fx_R, n);
    fx_phaser(fx_L, fx_R, n);
    fx_chorus(fx_L, fx_R, n);
    fx_delay(fx_L, fx_R, n);
    fx_reverb(fx_L, fx_R, n);
    for (j = 0; j < n; j++) {
        int32_t l, r, al, ar, a;
        /* the bypass: the effects keep running (their tails stay current), the output crossfades in 5.8 ms */
        if (fx_bypass ? fx_wet > 0 : fx_wet < 32768) fx_wet += fx_bypass ? -128 : 128;
        if (fx_wet != 32768) {
            fx_L[j] = (int32_t)(((int64_t)fx_L[j] * fx_wet + (int64_t)dry[j] * (32768 - fx_wet)) >> 15);
            fx_R[j] = (int32_t)(((int64_t)fx_R[j] * fx_wet + (int64_t)dry[j] * (32768 - fx_wet)) >> 15);
        }
        l = dc_block(fx_L[j], &dc_l, &dce_l); r = dc_block(fx_R[j], &dc_r, &dce_r);
        al = l < 0 ? -l : l; ar = r < 0 ? -r : r; a = al > ar ? al : ar;
        if (a > lim_env) lim_env += (a - lim_env) >> 2;
        else if (lim_env > LIM_T) lim_env -= ((lim_env - LIM_T) >> 12) + 1;
        if (lim_env > LIM_T) {
            int32_t g = (int32_t)(((uint32_t)LIM_T << 15) / (uint32_t)lim_env);
            l = ((l >> 4) * g) >> 11;
            r = ((r >> 4) * g) >> 11;
        }
        fx_L[j] = l = softclip(l); fx_R[j] = r = softclip(r);
        if (iabs(l) > pk) pk = iabs(l);
        if (iabs(r) > pk) pk = iabs(r);
    }
    /* silence gate: the codec mutes itself on digital zero, but while anything at all comes out its analog hiss
     * is there; a held note's last -66 dBFS lie under that hiss anyway. So after 200 ms under GATE_LO the output
     * fades (46 ms) to exact zero, and it opens within 1.5 ms of the first block above GATE_HI (0.4) */
    if (pk >= GATE_HI) { gate_quiet = 0; gate_tgt = 32768; }
    else if (pk < GATE_LO && ++gate_quiet >= 35u) { gate_quiet = 35u; gate_tgt = 0; }
    for (j = 0; j < n; j++) {
        if (gate_g < gate_tgt) gate_g = gate_g + 512 > gate_tgt ? gate_tgt : gate_g + 512;
        else if (gate_g > gate_tgt) gate_g = gate_g - 16 < gate_tgt ? gate_tgt : gate_g - 16;
        out[2u * j] += gate_g == 32768 ? fx_L[j] : (fx_L[j] * gate_g) >> 15;
        out[2u * j + 1u] += gate_g == 32768 ? fx_R[j] : (fx_R[j] * gate_g) >> 15;
    }
    fx_peak = pk;
    if (pk < 16) { if (fx_quiet_blocks < 0xFFFFu) fx_quiet_blocks++; } else fx_quiet_blocks = 0;
}
