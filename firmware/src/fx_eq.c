/* SPDX-License-Identifier: GPL-3.0-only */
/* The EQ before the drive (included by fx.c; fx_process: COMP -> EQ -> DRIVE, mono, on m in place). Its page: LOW, MID,
 * MID FREQ, HIGH. After the compressor, so its detector and the presets' compression stay as they are; before the
 * drive, so it shapes what the drive bites on.
 *
 * Three biquads in cascade, LOW -> MID -> HIGH: LOW a shelf at 120 Hz, MID a peak (Q 0.9) at MID FREQ (200 Hz .. 5 kHz,
 * log), HIGH a shelf at 5 kHz (the shelves' slope S = 1). Gains -15 .. +15 dB, 15 / 63 dB a knob step, 64 = 0 dB.
 * The coefficients are the RBJ cookbook's, written with c1 = 1 - cos(w0) (FX_EQ_F, EQ_LO_C1, EQ_HI_C1: no 1 - cos in
 * Q30 at 120 Hz), computed in integers: A and sqrt(A) from tables (fx_tables.h), the normalisation by a0 through
 * recip27 and one Newton step (no 64-bit divide). Each band runs eq_df1 (rhodes.c rh_df1's form: direct form I, Q28
 * coefficients, 64-bit accumulate, first-order error feedback, int32 state): the noise of the stage stays far under the bus' LSB, and
 * a coefficient change never clicks (DF1's states are the signal's own history).
 *
 * Knobs glide: the bands run at a position in quarter knob steps (A and FREQ's c1 / alpha interpolated between the
 * table's steps), EQ_GSTEP quarter steps toward the knob every 32-sample sub-block (the whole range in ~0.18 s), the
 * coefficients made again for the sub-blocks where a position moved.
 *
 * Flat is today's sound, bit for bit, at no cost: a band at 0 dB with its glide at rest does not run; it keeps only the
 * block's last two samples as its history (so leaving 0 dB starts from the signal: at unity a DF1's outputs equal its
 * inputs). A band that glides back to 0 dB runs on with the unity coefficients (b = a, exactly: the formulas at A = 1)
 * until its outputs meet its inputs within 1 LSB, then rests. With every band resting fx_eq returns before any loop:
 * m untouched. Factory presets and every older record load the EQ at 64 / 64 / its FREQ default / 64: flat. */

#define EQ_NB 3u
#define EQ_P0 256                                       /* a gain position (quarter steps) at 0 dB: knob 64 */
#ifndef EQ_GSTEP
#define EQ_GSTEP 2                                      /* quarter steps a position moves a sub-block */
#endif
#define EQ_US_BAND 34u                                  /* fx_cost_us: a band running (tools/drive_insns.py eq_df1:
                                                         * 21 instructions a sample, x 1.6) */
#define EQ_US_GLIDE 22u                                 /* + a band gliding (eq_df1g 34 - 21, x 1.6, + its
                                                         * coefficients 8 times a block) */

static int32_t eq_c[EQ_NB][5];                          /* each band's coefficients b0 b1 b2 a1 a2 (Q28) */
static int32_t eq_st[EQ_NB][5];                         /* rh_df1's state: m1 m2 o1 o2 e */
static int16_t eq_g[EQ_NB] = {EQ_P0, EQ_P0, EQ_P0};     /* each band's gain position (quarter knob steps, 0..508) */
static int16_t eq_f = 64 * 4;                           /* MID FREQ's position */
static uint8_t eq_run[EQ_NB];                           /* the band runs (off 0 dB, gliding, or settling) */
static uint8_t eq_cok;                                  /* the coefficients are those of the positions (0: make) */

/* one band over n samples in place: rh_df1_t's arithmetic (rhodes.c), st = m1 m2 o1 o2 e */
__attribute__((noinline)) static void eq_df1(int32_t *p, uint32_t n, const int32_t *c, int32_t *st)
{
    int32_t c0 = c[0], c1 = c[1], c2 = c[2], c3 = -c[3], c4 = -c[4];
    int32_t m1 = st[0], m2 = st[1], o1 = st[2], o2 = st[3], e = st[4];
    int32_t *pe = p + n;
    while (p < pe) {
        int32_t m = *p, o;
        int64_t acc = (int64_t)c0 * m + (int64_t)c1 * m1 + (int64_t)c2 * m2 + (int64_t)c3 * o1 + (int64_t)c4 * o2 + e;
        o = (int32_t)(acc >> 28); e = (int32_t)(acc & 0xFFFFFFF);
        m2 = m1; m1 = m; o2 = o1; o1 = o;
        *p++ = o;
    }
    st[0] = m1; st[1] = m2; st[2] = o1; st[3] = o2; st[4] = e;
}
/* the same while a knob glides: the coefficients ramp from c to t over the n (32) samples, one step a sample (the
 * step-wise update of each sub-block left a zipper of -75 dB under a 1 kHz tone at a full-speed MID sweep; ramped,
 * the sweep's own sidebands only), then c = t exactly */
__attribute__((noinline)) static void eq_df1g(int32_t *p, uint32_t n, int32_t *c, const int32_t *t, int32_t *st)
{
    int32_t k[5], d[5], i;
    int32_t m1 = st[0], m2 = st[1], o1 = st[2], o2 = st[3], e = st[4];
    int32_t *pe = p + n;
    for (i = 0; i < 5; i++) { k[i] = c[i]; d[i] = (t[i] - c[i]) / (int32_t)n; }
    while (p < pe) {
        int32_t m = *p, o;
        int64_t acc;
        k[0] += d[0]; k[1] += d[1]; k[2] += d[2]; k[3] += d[3]; k[4] += d[4];
        acc = (int64_t)k[0] * m + (int64_t)k[1] * m1 + (int64_t)k[2] * m2 - (int64_t)k[3] * o1 - (int64_t)k[4] * o2 + e;
        o = (int32_t)(acc >> 28); e = (int32_t)(acc & 0xFFFFFFF);
        m2 = m1; m1 = m; o2 = o1; o1 = o;
        *p++ = o;
    }
    for (i = 0; i < 5; i++) c[i] = t[i];
    st[0] = m1; st[1] = m2; st[2] = o1; st[3] = o2; st[4] = e;
}
static inline int32_t eq_lerp(const int32_t *t, int32_t p) /* a table at a quarter-step position */
{
    int32_t i = p >> 2;                                 /* (on a step: no t[i + 1]; MID's mirror of knob 0 is 512, */
    if (!(p & 3)) return t[i];                          /* the guard t[128], the last entry: the monkey's ASan seed 0) */
    return t[i] + (int32_t)(((int64_t)(t[i + 1] - t[i]) * (p & 3)) >> 2);
}
static inline int32_t eq_m30(int64_t a, int32_t b) { return (int32_t)((a * b) >> 30); }
/* 1 / a0 in Q30 (a0 Q28, 0.4 .. 8): recip27's 16-bit estimate, then one Newton step (~2^-30) */
static int32_t eq_rcp(int32_t a0)
{
    int32_t r = recip27((uint32_t)a0 >> 12) << 3, t = (int32_t)(((int64_t)a0 * r) >> 28);
    return r + (int32_t)(((int64_t)r * ((1 << 30) - t)) >> 30);
}
/* the coefficients of band b at its position: numerators b0 b1 b2 a0 a1 a2 (Q28, 64 bits), then / a0 */
static void eq_coef(uint32_t b, int32_t *c)
{
    int32_t g = eq_g[b], A = eq_lerp(FX_EQ_A, g), r;
    int64_t n[6];
    uint32_t i;
    if (b == 1u) {                                      /* MID: the peak (alpha / A = alpha x A at the mirrored knob) */
        int32_t p = eq_f, j = p >> 2, fr = p & 3, c1, al;
        c1 = FX_EQ_F[j][0] + (int32_t)(((int64_t)(FX_EQ_F[j + 1][0] - FX_EQ_F[j][0]) * fr) >> 2);
        al = FX_EQ_F[j][1] + (int32_t)(((int64_t)(FX_EQ_F[j + 1][1] - FX_EQ_F[j][1]) * fr) >> 2);
        n[4] = -2 * ((1 << 28) - (int64_t)(c1 >> 2));  /* -2 cos(w0) */
        n[3] = (1 << 28) + eq_m30(al, eq_lerp(FX_EQ_A, 2 * EQ_P0 - g));
        n[5] = (1 << 28) - eq_m30(al, eq_lerp(FX_EQ_A, 2 * EQ_P0 - g));
        n[0] = (1 << 28) + eq_m30(al, A);
        n[1] = n[4];
        n[2] = (1 << 28) - eq_m30(al, A);
    } else {                                            /* the shelves (S = 1): 2 sqrt(A) alpha = sqrt(A) sin sqrt2 */
        int64_t c1 = b ? EQ_HI_C1 : EQ_LO_C1, T = eq_m30(b ? EQ_HI_S : EQ_LO_S, eq_lerp(FX_EQ_SA, g));
        int64_t u = ((int64_t)(A - (1 << 28)) * c1) >> 30, v = ((int64_t)(A + (1 << 28)) * c1) >> 30;
        const int64_t two = (int64_t)2 << 28, A2 = 2 * (int64_t)A;
        if (!b) {                                       /* LOW */
            n[0] = ((two + u + T) * A) >> 28; n[1] = (2 * (v - two) * A) >> 28; n[2] = ((two + u - T) * A) >> 28;
            n[3] = A2 - u + T; n[4] = -2 * (A2 - v); n[5] = A2 - u - T;
        } else {                                        /* HIGH */
            n[0] = ((A2 - u + T) * A) >> 28; n[1] = (-2 * (A2 - v) * A) >> 28; n[2] = ((A2 - u - T) * A) >> 28;
            n[3] = two + u + T; n[4] = 2 * (v - two); n[5] = two + u - T;
        }
    }
    r = eq_rcp((int32_t)n[3]);
    c[3] = (int32_t)((n[4] * r) >> 30);
    c[4] = (int32_t)((n[5] * r) >> 30);
    if (g == EQ_P0) { c[0] = 1 << 28; c[1] = c[3]; c[2] = c[4]; return; }   /* 0 dB: b = a, exactly (no rounding) */
    for (i = 0; i < 3u; i++) c[i] = (int32_t)((n[i] * r) >> 30);
}
static int16_t eq_tgt(uint32_t v) { return (int16_t)((v > 127u ? 127u : v) * 4u); }
static int eq_glide(int16_t *p, int16_t t)                 /* one sub-block's step; 1: it moved */
{
    if (*p == t) return 0;
    *p = (int16_t)(*p < t ? (t - *p > EQ_GSTEP ? *p + EQ_GSTEP : t) : (*p - t > EQ_GSTEP ? *p - EQ_GSTEP : t));
    return 1;
}
static void eq_hist(uint32_t b, const int32_t *m, uint32_t n)   /* a resting band: the signal's last two samples */
{
    int32_t *s = eq_st[b];
    s[0] = s[2] = m[n - 1u]; s[1] = s[3] = m[n - 2u]; s[4] = 0;
}
static void eq_reset(void)                              /* fx_reset_state: every band at rest, its history 0 */
{
    memset(eq_st, 0, sizeof eq_st);
    memset(eq_run, 0, sizeof eq_run);
    eq_cok = 0;
}
static uint32_t eq_cost_us(void)                        /* fx_cost_us: each band that runs or is off flat, more
                                                         * while it glides; 0 flat and at rest */
{
    static const uint8_t ix[EQ_NB] = {FXP_EQ_LO, FXP_EQ_MID, FXP_EQ_HI};
    uint32_t b, us = 0;
    for (b = 0; b < EQ_NB; b++) {
        int16_t tg = eq_tgt((uint32_t)fx_p[ix[b]]);
        if (eq_run[b] || tg != EQ_P0) us += EQ_US_BAND + (tg != eq_g[b] ? EQ_US_GLIDE : 0u);
    }
    return us;
}
#if FR_C1
/* fx_eq for a chunk (fx.c fxc_o / fxc_e): the targets, which bands run and glide at the first, the rest at the last */
__attribute__((noinline)) static void fx_eq(int32_t *m, uint32_t n)
{
    static int16_t tg[EQ_NB], tf;
    static uint32_t mv, rest;
    uint32_t b, sb;
    if (FXC_1) {
        tg[0] = eq_tgt((uint32_t)fx_p[FXP_EQ_LO]); tg[1] = eq_tgt((uint32_t)fx_p[FXP_EQ_MID]);
        tg[2] = eq_tgt((uint32_t)fx_p[FXP_EQ_HI]); tf = eq_tgt((uint32_t)fx_p[FXP_EQ_FREQ]);
        mv = 0;
        for (b = 0; b < EQ_NB; b++) if (tg[b] != eq_g[b] || tg[b] != EQ_P0) eq_run[b] = 1;
        rest = !(eq_run[0] | eq_run[1] | eq_run[2]);
        if (rest) eq_f = tf;
        else {
            for (b = 0; b < EQ_NB; b++) mv |= (uint32_t)(tg[b] != eq_g[b]);
            if (!eq_run[1]) eq_f = tf;
            mv |= (uint32_t)(eq_f != tf);
            if (!eq_cok) { for (b = 0; b < EQ_NB; b++) eq_coef(b, eq_c[b]); eq_cok = 1; }
        }
    }
    if (rest) {
        if (FXC_Z && n >= 2u) for (b = 0; b < EQ_NB; b++) eq_hist(b, m, n);
        return;
    }
    for (sb = fxc_o; sb < fxc_e; sb += 32u) {
        uint32_t e = mv ? 32u : fxc_e - sb, gl = 0;
        int32_t t[5];
        if (mv) gl = (uint32_t)eq_glide(&eq_g[0], tg[0]) | (uint32_t)(eq_glide(&eq_f, tf) | eq_glide(&eq_g[1], tg[1])) << 1 |
                     (uint32_t)eq_glide(&eq_g[2], tg[2]) << 2;
        for (b = 0; b < EQ_NB; b++) {
            if (gl >> b & 1u) { eq_coef(b, t); eq_df1g(m + sb, e, eq_c[b], t, eq_st[b]); }
            else if (eq_run[b]) eq_df1(m + sb, e, eq_c[b], eq_st[b]);
            else if (e >= 2u) eq_hist(b, m + sb, e);
        }
        if (!mv) break;
    }
    if (!FXC_Z) return;
    for (b = 0; b < EQ_NB; b++) {
        int32_t *s = eq_st[b];
        if (eq_run[b] && eq_g[b] == EQ_P0 && tg[b] == EQ_P0) {
            int32_t d1 = s[2] - s[0], d2 = s[3] - s[1];
            if (d1 >= -1 && d1 <= 1 && d2 >= -1 && d2 <= 1) { s[2] = s[0]; s[3] = s[1]; s[4] = 0; eq_run[b] = 0; }
        }
    }
}
#else
__attribute__((noinline)) static void fx_eq(int32_t *m, uint32_t n)
{
    int16_t tg[EQ_NB] = {eq_tgt((uint32_t)fx_p[FXP_EQ_LO]), eq_tgt((uint32_t)fx_p[FXP_EQ_MID]),
                         eq_tgt((uint32_t)fx_p[FXP_EQ_HI])}, tf = eq_tgt((uint32_t)fx_p[FXP_EQ_FREQ]);
    uint32_t b, sb, mv = 0;
    for (b = 0; b < EQ_NB; b++) if (tg[b] != eq_g[b] || tg[b] != EQ_P0) eq_run[b] = 1;
    if (!(eq_run[0] | eq_run[1] | eq_run[2])) {         /* flat and at rest: today's integers, the history kept */
        eq_f = tf;                                       /* (FREQ alone moves nothing: MID is at 0 dB) */
        if (n >= 2u) for (b = 0; b < EQ_NB; b++) eq_hist(b, m, n);
        return;
    }
    for (b = 0; b < EQ_NB; b++) mv |= (uint32_t)(tg[b] != eq_g[b]);
    if (!eq_run[1]) eq_f = tf;                          /* (MID resting: FREQ jumps, nothing hears it) */
    mv |= (uint32_t)(eq_f != tf);
    if (!eq_cok) { for (b = 0; b < EQ_NB; b++) eq_coef(b, eq_c[b]); eq_cok = 1; }
    for (sb = 0; sb < n; sb += 32u) {                   /* per sub-block while a knob glides, else the whole block */
        uint32_t e = mv ? (sb + 32u < n ? 32u : n - sb) : n - sb, gl = 0;
        int32_t t[5];
        if (mv) gl = (uint32_t)eq_glide(&eq_g[0], tg[0]) | (uint32_t)(eq_glide(&eq_f, tf) | eq_glide(&eq_g[1], tg[1])) << 1 |
                     (uint32_t)eq_glide(&eq_g[2], tg[2]) << 2;
        for (b = 0; b < EQ_NB; b++) {
            if (gl >> b & 1u) { eq_coef(b, t); eq_df1g(m + sb, e, eq_c[b], t, eq_st[b]); }
            else if (eq_run[b]) eq_df1(m + sb, e, eq_c[b], eq_st[b]);
            else if (e >= 2u) eq_hist(b, m + sb, e);
        }
        if (!mv) break;
    }
    for (b = 0; b < EQ_NB; b++) {                       /* back at 0 dB, at rest: settled? then rest */
        int32_t *s = eq_st[b];
        if (eq_run[b] && eq_g[b] == EQ_P0 && tg[b] == EQ_P0) {
            int32_t d1 = s[2] - s[0], d2 = s[3] - s[1];
            if (d1 >= -1 && d1 <= 1 && d2 >= -1 && d2 <= 1) { s[2] = s[0]; s[3] = s[1]; s[4] = 0; eq_run[b] = 0; }
        }
    }
}
#endif
