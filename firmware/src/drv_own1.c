/* SPDX-License-Identifier: GPL-3.0-only */
/* DRIVE TYPE 1 in the plugin's own-code build (FR_OWN; every other build runs the classic drive there): WARM, a
 * drive of Frodhes' own, designed from the black-box profile of the classic one's levels
 * (tests/baselines/drive_own_target.json) and tools/drive_gen/own1.py's design (which says why each part is there):
 *
 *   in -> bass pre-emphasis (a low shelf cut at 150 Hz, deeper with AMOUNT)
 *      -> gain G (per drive row and AMOUNT, ramped per sample) -> 2x (drive.c drv_up2) -> the curve with ADAA-1
 *      -> 1x (drv_dn2) -> bass de-emphasis (the exact inverse shelf) -> TONE: a state-variable low-pass, its corner
 *         from TONE and opening toward 20 kHz as AMOUNT falls -> DC high-pass (25 Hz) -> make-up M (per row and
 *         AMOUNT, ramped per sample)
 *
 * The curve F(u) = s + b s^2, s = u / sqrt(1 + u^2): smooth everywhere, slope 1 at 0, an even part b per drive row and
 * AMOUNT (own1.py BETA_LO / BETA_HI). s and s^2 are tabulated (Q23 out: 4097 points over u in +-32, and 4097 more over
 * +-1/2, where s^2's chords must be fine) with their exact integrals at the nodes (one function across the fine
 * table's edges), so ADAA-1 takes the mean of the interpolated curve over each 2x step:
 * (P(x) - P(xp)) / (x - xp) for each curve, mixed s + b s^2: two 64-bit divides (this file is never in the
 * firmware); |x - xp| <= OW_EPS: the curve at the midpoint.
 * Units: the bus (Q15) x 2^8 inside (Q23) before and after the curve; the curve's input Q24 (u = 1 at 2^23: a quiet
 * note at a low AMOUNT still has thousands of steps, no staircase).
 * Knobs: AMOUNT and TONE glide (OW_GSTEP steps a block, the whole range in 32 blocks, 0.19 s), G and M ramp sample by
 * sample from the last block's values, the filters' coefficients follow once a block (the TPT forms stay smooth while
 * they move). A zero input rests at exact 0 (every stage maps 0 to 0); the integer filters' last LSBs are cleared
 * after OW_ZRUN samples of zero input once the output is under OW_REST (drive.c drv_rest_ramp takes the step out).
 * Integers only: arm64 and x86_64 give the same bytes. */
#include "drive_own_tables.h"

#define DRV_US_OWN1 60u                                 /* (desktop only; drive.c's budget table wants a number) */
#define OW_GSTEP (4 << 8)                                /* AMOUNT / TONE (x 256) a block, at most */
#define OW_EPS 256                                       /* ADAA: |dx| up to this (Q24), the curve at the midpoint */
#define OW_ZRUN 1024u                                    /* zero input this long (samples) ... */
#define OW_REST 48                                       /* ... and the output under this (LSB): the states to 0 */
#define OW_CLAMP (1 << 29)                               /* the 1x input to the 2x stage (Q24): +-2 x the curve's span */

typedef struct {
    int32_t pe, de, s1, s2, dc;                     /* the one-poles' and the SVF's states (Q23) */
    drv_os_t os;                                        /* the half-bands */
    int32_t ax;                                         /* ADAA: the last 2x input (Q24) */
    int64_t as, aq;                                     /* ... and the two curves' integrals there (ow_prim) */
    int32_t b;                                          /* the even part b (Q15) the last block ran */
    int32_t g, m;                                       /* G and M where the last block ended (Q16) */
    int32_t pa, pt;                                     /* AMOUNT and TONE as they run (x 256) */
    uint32_t zrun;                                      /* zero input samples in a row */
    uint8_t live;                                       /* 0 after a reset: take the knobs as they are */
} ow_state_t;
static DRV_BSS ow_state_t ow;

static void drv_own1_reset(void)
{
    memset(&ow, 0, sizeof ow);
}

/* a curve at x (Q24, |x| < OW_X), linearly interpolated, Q23: the fine table inside +-OW_XF, else the coarse one */
typedef struct { const int32_t *ff; const int64_t *pf; const int32_t *fc; const int64_t *pc; } ow_tab_t;
static const ow_tab_t OW_TS = {OW_SF, OW_PSF, OW_SC, OW_PSC}, OW_TQ = {OW_QF, OW_PQF, OW_QC, OW_PQC};
static inline __attribute__((always_inline)) int32_t ow_curve(const ow_tab_t *t, int32_t x)
{
    uint32_t u, i, r;
    if (x > -OW_XF && x < OW_XF) {
        u = (uint32_t)(x + OW_XF); i = u >> OW_SHF; r = u & ((1u << OW_SHF) - 1u);
        return t->ff[i] + (int32_t)(((int64_t)(t->ff[i + 1] - t->ff[i]) * (int32_t)r) >> OW_SHF);
    }
    u = (uint32_t)(x + OW_X); i = u >> OW_SH; r = u & ((1u << OW_SH) - 1u);
    return t->fc[i] + (int32_t)(((int64_t)(t->fc[i + 1] - t->fc[i]) * (int32_t)r) >> OW_SH);
}
/* 2 x the integral of the interpolated curve from 0 to x: P[i] + 2 f[i] r + (f[i+1] - f[i]) r^2 / 2^sh (r: x's
 * offset in its segment; the last term floored, a fraction of a unit: the means below are differences of these over
 * at least OW_EPS) */
static inline __attribute__((always_inline)) int64_t ow_prim(const ow_tab_t *t, int32_t x)
{
    uint32_t u, i;
    int64_t r;
    if (x > -OW_XF && x < OW_XF) {
        u = (uint32_t)(x + OW_XF); i = u >> OW_SHF; r = (int64_t)(u & ((1u << OW_SHF) - 1u));
        return t->pf[i] + 2 * (int64_t)t->ff[i] * r + (((int64_t)(t->ff[i + 1] - t->ff[i]) * r * r) >> OW_SHF);
    }
    u = (uint32_t)(x + OW_X); i = u >> OW_SH; r = (int64_t)(u & ((1u << OW_SH) - 1u));
    return t->pc[i] + 2 * (int64_t)t->fc[i] * r + (((int64_t)(t->fc[i + 1] - t->fc[i]) * r * r) >> OW_SH);
}
/* the 2x samples in place: x (Q24) -> the curve's mean over the step from the last one (Q23); the even part b ramps
 * from b0 to b1 (Q15) over the block (in Q24 inside, so it lands exactly; the integrals are kept per curve, so a
 * moving b never makes a step) */
static __attribute__((noinline)) void ow_curve2(int32_t *x, uint32_t n2, int32_t b0, int32_t b1)
{
    int32_t bf = b0 * 512, dbf = (b1 - b0) * 512 / (int32_t)n2, b;
    int32_t xp = ow.ax, *e = x + n2;
    int64_t ps = ow.as, pq = ow.aq;
    for (; x < e; x++) {
        int32_t v = *x, d;
        int64_t s, q;
        v = v > -(OW_X - 1) ? v : -(OW_X - 1);
        v = v < OW_X - 1 ? v : OW_X - 1;
        s = ow_prim(&OW_TS, v); q = ow_prim(&OW_TQ, v);
        d = v - xp;
        bf += dbf;
        b = x + 1 == e ? b1 : bf >> 9;
        if (d > OW_EPS || d < -OW_EPS) {                 /* (two means, then mixed: a 64-bit difference times b
                                                         * would not fit) */
            int64_t dd = 2 * (int64_t)d;
            *x = (int32_t)((s - ps) / dd) + (int32_t)((((q - pq) / dd) * b) >> 15);
        }
        else {
            int32_t m = v - (d >> 1);
            *x = ow_curve(&OW_TS, m) + (int32_t)(((int64_t)ow_curve(&OW_TQ, m) * b) >> 15);
        }
        xp = v; ps = s; pq = q;
    }
    ow.ax = xp; ow.as = ps; ow.aq = pq;
}

/* a TPT one-pole's low-pass (G Q30) on Q23 */
static inline __attribute__((always_inline)) int32_t ow_lp1(int32_t *s, int32_t G, int32_t x)
{
    int32_t v = (int32_t)(((int64_t)(x - *s) * G + (1 << 29)) >> 30), y = v + *s;
    *s = y + v;
    return y;
}
/* a row of a per-AMOUNT table at p (x 256), linearly between the steps */
static inline int32_t ow_at(const int32_t *t, int32_t p)
{
    int32_t i = p >> 8, f = p & 255;
    return i >= 127 ? t[127] : t[i] + (int32_t)(((int64_t)(t[i + 1] - t[i]) * f) >> 8);
}

static void drv_own1_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    int32_t row = c->row & 3, ta = c->amount << 8, tt = c->tone << 8;
    int32_t g0, g1, dg, m0, m1, dm, b1, ke, kd, gd, a1, a2, a3, tp, sp, w;
    int32_t pe = ow.pe, de = ow.de, s1 = ow.s1, s2 = ow.s2, dc = ow.dc, any = 0, pk = 0;
    int32_t *x2 = drv_scr.x2;
    uint32_t i;
    if (n > DRV_BLK) n = DRV_BLK;
    if (!n) return;
    if (!ow.live) {                                      /* (after a reset: the knobs as they are, the 2x at rest) */
        ow.pa = ta; ow.pt = tt;
        ow.g = ow_at(OW_G[row], ta); ow.m = ow_at(OW_M[row], ta); ow.b = ow_at(OW_B[row], ta);
        ow.ax = 0; ow.as = ow_prim(&OW_TS, 0); ow.aq = ow_prim(&OW_TQ, 0);
        ow.live = 1;
    } else {
        ow.pa = drv_glide(ow.pa, ta, OW_GSTEP);
        ow.pt = drv_glide(ow.pt, tt, OW_GSTEP);
    }
    g0 = ow.g; g1 = ow_at(OW_G[row], ow.pa); dg = (g1 - g0) / (int32_t)n;   /* (a new drive row: G, M and b ramp
                                                         * to its rows over the block) */
    m0 = ow.m; m1 = ow_at(OW_M[row], ow.pa); dm = (m1 - m0) / (int32_t)n;
    b1 = ow_at(OW_B[row], ow.pa);
    ke = ow_at(OW_KE, ow.pa); kd = ow_at(OW_KD, ow.pa); gd = ow_at(OW_GD, ow.pa);
    w = ow_at(OW_TW, ow.pa);                             /* the SVF's corner on its grid (x 256) */
    tp = OW_TP0 + (int32_t)(((int64_t)(OW_TP1 - OW_TP0) * ow.pt) / (127 << 8));
    tp = OW_TPT + (int32_t)(((int64_t)(tp - OW_TPT) * w) >> 15);
    sp = tp >> 8;
    if (sp >= (int32_t)OW_NT - 1) { a1 = OW_SVF[OW_NT - 1][0]; a2 = OW_SVF[OW_NT - 1][1]; a3 = OW_SVF[OW_NT - 1][2]; }
    else {
        int32_t fr = tp & 255;
        a1 = OW_SVF[sp][0] + (int32_t)(((int64_t)(OW_SVF[sp + 1][0] - OW_SVF[sp][0]) * fr) >> 8);
        a2 = OW_SVF[sp][1] + (int32_t)(((int64_t)(OW_SVF[sp + 1][1] - OW_SVF[sp][1]) * fr) >> 8);
        a3 = OW_SVF[sp][2] + (int32_t)(((int64_t)(OW_SVF[sp + 1][2] - OW_SVF[sp][2]) * fr) >> 8);
    }
    /* before the curve, 1x: the pre-emphasis, the gain -> out (Q24) */
    for (i = 0; i < n; i++) {
        int32_t v = in[i], x;
        int64_t u;
        any |= v;
        x = v * 256;
        x -= (int32_t)(((int64_t)ow_lp1(&pe, OW_GPE, x) * ke + 16384) >> 15);
        g0 += dg;
        u = ((int64_t)x * (i + 1u == n ? g1 : g0) + 16384) >> 15;
        u = u > -OW_CLAMP ? u : -OW_CLAMP;
        out[i] = (int32_t)(u < OW_CLAMP ? u : OW_CLAMP);
    }
    drv_up2(&ow.os, out, x2, n);
    ow_curve2(x2, 2u * n, ow.b, b1);
    drv_dn2(&ow.os, x2, out, n, 0u);
    /* after it, 1x (Q23): the de-emphasis, the TONE's low-pass, the DC high-pass, the make-up -> the bus */
    for (i = 0; i < n; i++) {
        int32_t y = out[i], v1, v2, v3, o;
        y += (int32_t)(((int64_t)ow_lp1(&de, gd, y) * kd + 16384) >> 15);
        v3 = y - s2;
        v1 = (int32_t)(((int64_t)a1 * s1 + (int64_t)a2 * v3 + (1 << 29)) >> 30);
        v2 = s2 + (int32_t)(((int64_t)a2 * s1 + (int64_t)a3 * v3 + (1 << 29)) >> 30);
        s1 = 2 * v1 - s1; s2 = 2 * v2 - s2;
        y = v2 - ow_lp1(&dc, OW_GDC, v2);
        m0 += dm;
        o = (int32_t)(((int64_t)y * (i + 1u == n ? m1 : m0) + (1 << 23)) >> 24);
        out[i] = o;
        o = o < 0 ? -o : o;
        pk = o > pk ? o : pk;
    }
    ow.pe = pe; ow.de = de; ow.s1 = s1; ow.s2 = s2; ow.dc = dc;
    ow.g = g1; ow.m = m1; ow.b = b1;
    ow.zrun = any ? 0u : ow.zrun + n;
    if (ow.zrun >= OW_ZRUN && pk < OW_REST) {           /* a long zero input, the output nearly 0: every state to rest
                                                         * (the next block is exact 0; drv_rest_ramp ramps this one's
                                                         * last sample down over it) */
        ow.pe = ow.de = ow.s1 = ow.s2 = ow.dc = 0;
        memset(&ow.os, 0, sizeof ow.os);
        ow.ax = 0; ow.as = ow_prim(&OW_TS, 0); ow.aq = ow_prim(&OW_TQ, 0);
    }
}
