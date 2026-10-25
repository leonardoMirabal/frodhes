/* SPDX-License-Identifier: GPL-3.0-only */
/* DRIVE TYPE 5, BHLUE: the two-stage soft overdrive: a treble-boosting clean stage, then a stage whose feedback holds
 * two + two diodes in series with a resistor: a soft symmetric clip that never goes flat; a treble-cut tone. The
 * circuit and its tables: tools/drive_gen/bhlue.py (the stock values). 1x, no oversampling, no divide.
 *
 * Once a block (drive.c: n <= DRV_BLK, even), four passes, two samples per iteration where it pays:
 *   1  the two coupling high-passes (input, stage 2), x << 12 (Q27; x clamped to +-2^18 first: 8 x full scale)
 *   2  PRE: stage 1 x stage 2's linear gain, one biquad per AMOUNT step (b Q15, a Q28)            -> u, in m
 *   3  the clipper: vo = table(|u|) at 1/16 V (1025 nodes, linear interpolation, flat beyond 64 V), odd
 *   4  POST: the tone x the volume load x the level, one biquad per TONE step (c Q25, d Q28)      -> m (Q15)
 * Rows: 64 per knob plus a few of their own (tools/drive_gen/bhlue.py), a step between two interpolated when it moves
 * (BHLUE_PA / BHLUE_PT); the levels the same way.
 * Knobs: the steps that run glide toward the knobs, BHLUE_GSTEP steps a block (the whole range in 64 blocks, 0.37 s);
 * the coefficients are copied (and POST scaled by the level) only when a step moves. */
/* compiled (pi32v2 -Os, tools/drive_insns.py; two-sample loops halved), per sample:
 * BHLUE_AA 1 (the anti-aliased kernel, on): HPs + the P shelf 24.5 (49 / 2, the clamp included) + PRE 19.5 (39 / 2,
 * its hold included) + the ADAA-1 clipper 38 (bh_aa1, the longest path) + POST x Q 15.5 (31 / 2) = 97.5 = 156 us at
 * 1.6; ~100 instructions of set-up a block: ~1 us -> 157 us. The {g, F} table in RAM, built once at boot (bh_gf_build:
 * 1025 x 15, ~0.1 ms in the main loop).
 * BHLUE_AA 0 (the plain kernel): HPs 16 + PRE 15 + clip 18 + POST 15.5 = 64.5 = 103.2 us, +1 us: 105. */
#if BHLUE_AA
#define DRV_US_BHLUE 157u
#else
#define DRV_US_BHLUE 105u
#endif
_Static_assert((DRV_BLK & 1u) == 0, "BHLUE runs two samples a pass: the block must be even");
#ifndef BHLUE_GSTEP
#define BHLUE_GSTEP 2                                    /* AMOUNT / TONE steps the rows move at most a block */
#endif
#define BH_CLAMP (1 << 18)

typedef struct { int32_t pa, ph, b0, b1, b2, a1, a2, c0, c1, c2, d1, d2, lo, hi, s0, s1, sp; } bh_coef_t;   /* lo, hi: the
                                                          * input clamp; s0, s1, sp: the anti-aliased kernel's P shelf */
typedef struct { int32_t xa1, x1, h1, h2, u1, u2, w1, w2, z1, z2, xo, up, ax1, aF1; } bh_state_t;   /* xo, up: the shelf's;
                                                          * ax1, aF1: the anti-aliased clipper's previous x and F */
static DRV_BSS bh_coef_t bh_k;
static DRV_BSS bh_state_t bh_s;
static DRV_DATA bh_coef_t *volatile bh_kp = &bh_k;                 /* (read once a block: the compiler cannot see through it,
                                                          * so the loops read the coefficients as the counted design
                                                          * did, not as spilled 64-bit pairs) */
static DRV_DATA int32_t bh_ra = -1, bh_rt;                          /* the AMOUNT and TONE steps that run (-1: take the knobs) */

#define BHM(a, b) ((int64_t)(a) * (b))

#if BHLUE_AA
/* the anti-aliased clipper's table in RAM (8.2 KB; in flash it cost 8.2 KB of image): {g, F} per 1/16 V node, g (Q16 V,
 * low word) the running sum of BHLUE_D, F (high word) its exact antiderivative >> 9 (node width 4096: the trapezoid
 * 4096 (g0 + g1) / 2 = (g0 + g1) << 11, summed in 64 bits); tools/drive_gen/bhlue.py gf_table bit for bit (the last
 * node repeated: D[1024] = 0). Built once at boot by drv_boot (the main loop, never an audio block); until then the
 * TYPE passes its input through. A host harness without app_main builds it in the reset (DINGHY_HOST only). */
static DRV_BSS int64_t bh_gf[BHLUE_NV + 1];
static DRV_BSS volatile uint8_t bh_gf_ok;
static void bh_gf_build(void)
{
    int32_t g = 0, gp = 0;
    int64_t s = 0;
    uint32_t i;
    bh_gf[0] = 0;
    for (i = 1; i <= BHLUE_NV; i++) {
        g = gp + BHLUE_D[i - 1];
        s += (int64_t)(gp + g) << 11;
        bh_gf[i] = (int64_t)(((uint64_t)(uint32_t)(int32_t)(s >> 9) << 32) | (uint32_t)g);
        gp = g;
    }
    bh_gf_ok = 1;
}
#endif

static void drv_bhlue_reset(void)
{
#if BHLUE_AA && defined(DINGHY_HOST)
    if (!bh_gf_ok) bh_gf_build();
#endif
    memset(&bh_s, 0, sizeof bh_s);
    bh_ra = -1;
}

static inline __attribute__((always_inline)) void bh_hps2q(const bh_coef_t *k, bh_state_t *st, const int32_t *in,
                                                           int32_t *m, uint32_t n)
{
    int32_t xa1 = st->xa1, x1 = st->x1, h1 = st->h1;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t ma = in[0], mb = in[1], xa, xb, ga, gb, ha, hb, c;
        c = k->lo; ma = ma > c ? ma : c; mb = mb > c ? mb : c;   /* (the bounds from k: smax, smin with registers) */
        c = k->hi; ma = ma < c ? ma : c; mb = mb < c ? mb : c;
        xa = ma << 12; xb = mb << 12;
        c = k->pa;
        ga = (xa - xa1) + (int32_t)(BHM(c, x1) >> 30);
        gb = (xb - xa) + (int32_t)(BHM(c, ga) >> 30);
        c = k->ph;
        ha = (ga - x1) + (int32_t)(BHM(c, h1) >> 30);
        hb = (gb - ga) + (int32_t)(BHM(c, ha) >> 30);
        xa1 = xb; x1 = gb; h1 = hb;
        m[0] = ha; m[1] = hb;
        in += 2; m += 2;
    }
    st->xa1 = xa1; st->x1 = x1;                            /* h1 is PRE's x[n-1]: PRE keeps it */
}

static inline __attribute__((always_inline)) void bh_pre2q(const bh_coef_t *k, bh_state_t *st, int32_t *m, uint32_t n)
{
    int32_t h1 = st->h1, h2 = st->h2, u1 = st->u1, u2 = st->u2;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t ha = m[0], hb = m[1], ua, ub, c;
        int64_t pa, pb;
        c = k->b2; pa = BHM(c, h2); pb = BHM(c, h1);
        c = k->b1; pa += BHM(c, h1); pb += BHM(c, ha);
        c = k->b0; pa += BHM(c, ha); pb += BHM(c, hb);
        pa <<= 6; pb <<= 6;
        c = k->a2; pa -= BHM(c, u2); pb -= BHM(c, u1);
        c = k->a1; pa -= BHM(c, u1);
        ua = (int32_t)(pa >> 28);
        pb -= BHM(c, ua);
        ub = (int32_t)(pb >> 28);
        h2 = ha; h1 = hb; u2 = ua; u1 = ub;
        m[0] = ua >> 4; m[1] = ub >> 4;
        m += 2;
    }
    st->h1 = h1; st->h2 = h2; st->u1 = u1; st->u2 = u2;
}

#if !BHLUE_AA
static inline __attribute__((always_inline)) int32_t bh_clip(int32_t us)
{
    int32_t a = us < 0 ? -us : us, i = a >> 12, f = a & 4095, v;
    if (i > 1024) i = 1024;
    v = BHLUE_V[i] + ((BHLUE_D[i] * f) >> 12);
    return us < 0 ? -v : v;
}

static inline __attribute__((always_inline)) void bh_clip1(int32_t *m, uint32_t n)
{
    uint32_t j;
    for (j = 0; j < n; j++) m[j] = bh_clip(m[j]);
}
#endif

static __attribute__((noinline)) void bh_post2q(const bh_coef_t *k, bh_state_t *st, int32_t *m,
                                                            uint32_t n)
{
    int32_t w1 = st->w1, w2 = st->w2, z1 = st->z1, z2 = st->z2;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t va = m[0], vb = m[1], za, zb, c;
        int64_t pa, pb;
        c = k->c2; pa = BHM(c, w2); pb = BHM(c, w1);
        c = k->c1; pa += BHM(c, w1); pb += BHM(c, va);
        c = k->c0; pa += BHM(c, va); pb += BHM(c, vb);
        pa <<= 10; pb <<= 10;
        c = k->d2; pa -= BHM(c, z2); pb -= BHM(c, z1);
        c = k->d1; pa -= BHM(c, z1);
        za = (int32_t)(pa >> 28);
        pb -= BHM(c, za);
        zb = (int32_t)(pb >> 28);
        w2 = va; w1 = vb; z2 = za; z1 = zb;
        m[0] = za >> 8; m[1] = zb >> 8;
        m += 2;
    }
    st->w1 = w1; st->w2 = w2; st->z1 = z1; st->z2 = z2;
}

#if BHLUE_AA
/* ---- the anti-aliased kernel (first-order antiderivative anti-aliasing without a divide, drive.c's way for every
 * TYPE: tools/drive_gen/aa.py). Its two-sample average (cos where linear, ~sinc where it clips) is compensated: the
 * P shelf (sinc / cos, one first-order section) after the high-passes, the tone rows refitted x Q (1 / sinc). */
#define BH_XMAX ((64 << 16) - 1)                          /* |u| held to the table's 64 V (PRE's own clamp) */
/* the same hold before the >> 4: (u >> 4) in [-BH_XMAX, BH_XMAX] <=> u in [-16 BH_XMAX, 16 BH_XMAX + 15], bit for bit;
 * -BH_XMAX itself (0xFFC00001) would be a constant in the chip ROM's window (tools/build.py refuses it), these are not */
#define BH_UHI (16 * BH_XMAX + 15)
#define BH_ULO (-16 * BH_XMAX)
#define BH_AT (1 << 9)                                    /* |du| below this (2^-7 V): g at the midpoint */
/* pass 1: the two high-passes and the P shelf, 2 samples per iteration (x clamped first, as the plain kernel) */
static __attribute__((noinline)) void bh_hps2qs(const bh_coef_t *k, bh_state_t *st, const int32_t *in,
                                                            int32_t *m, uint32_t n)
{
    int32_t xa1 = st->xa1, x1 = st->x1, h1 = st->xo, y1 = st->up;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t ma = in[0], mb = in[1], xa, xb, ga, gb, ha, hb, ya, c;
        c = k->lo; ma = ma > c ? ma : c; mb = mb > c ? mb : c;
        c = k->hi; ma = ma < c ? ma : c; mb = mb < c ? mb : c;
        xa = ma << 12; xb = mb << 12;
        c = k->pa;
        ga = (xa - xa1) + (int32_t)(BHM(c, x1) >> 30);
        gb = (xb - xa) + (int32_t)(BHM(c, ga) >> 30);
        c = k->ph;
        ha = (ga - x1) + (int32_t)(BHM(c, h1) >> 30);
        hb = (gb - ga) + (int32_t)(BHM(c, ha) >> 30);
        ya = (int32_t)((BHM(k->s0, ha) + BHM(k->s1, h1) + BHM(k->sp, y1)) >> 28);
        y1 = (int32_t)((BHM(k->s0, hb) + BHM(k->s1, ha) + BHM(k->sp, ya)) >> 28);
        xa1 = xb; x1 = gb; h1 = hb;
        m[0] = ya; m[1] = y1;
        in += 2; m += 2;
    }
    st->xa1 = xa1; st->x1 = x1; st->xo = h1; st->up = y1;
}
/* pass 2: PRE, its output held to +-64 V */
static __attribute__((noinline)) void bh_pre2qc(const bh_coef_t *k, bh_state_t *st, int32_t *m, uint32_t n)
{
    int32_t h1 = st->h1, h2 = st->h2, u1 = st->u1, u2 = st->u2;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t ha = m[0], hb = m[1], ua, ub, c, sa, sb;
        int64_t pa, pb;
        c = k->b2; pa = BHM(c, h2); pb = BHM(c, h1);
        c = k->b1; pa += BHM(c, h1); pb += BHM(c, ha);
        c = k->b0; pa += BHM(c, ha); pb += BHM(c, hb);
        pa <<= 6; pb <<= 6;
        c = k->a2; pa -= BHM(c, u2); pb -= BHM(c, u1);
        c = k->a1; pa -= BHM(c, u1);
        ua = (int32_t)(pa >> 28);
        pb -= BHM(c, ua);
        ub = (int32_t)(pb >> 28);
        h2 = ha; h1 = hb; u2 = ua; u1 = ub;
        sa = ua > BH_UHI ? BH_UHI : ua; sa = sa < BH_ULO ? BH_ULO : sa; sa >>= 4;
        sb = ub > BH_UHI ? BH_UHI : ub; sb = sb < BH_ULO ? BH_ULO : sb; sb >>= 4;
        m[0] = sa; m[1] = sb;
        m += 2;
    }
    st->h1 = h1; st->h2 = h2; st->u1 = u1; st->u2 = u2;
}
/* pass 3: the clipper with its antiderivative: {g, F} per 1/16 V node in one 64-bit word (g low, F >> 9 high) */
static __attribute__((noinline)) void bh_aa1(bh_state_t *st, int32_t *m, uint32_t n)
{
    const int64_t *gf = bh_gf;
    const uint16_t *rcp = (const uint16_t *)((uintptr_t)DRV_AA_RCP - (2u << (DRV_AA_RB - 1)));   /* (from 2^(RB-1)) */
    int32_t x1 = st->ax1, F1 = st->aF1;
    uint32_t j;
    for (j = n; j; j--) {
        int32_t x = m[0], a, fr, g0, dg, p, F, d, v;
        uint32_t ad;
        int64_t w_;
        a = x < 0 ? -x : x;
        w_ = gf[a >> 12]; fr = a & 4095;
        g0 = (int32_t)w_; dg = (int32_t)gf[(a >> 12) + 1] - g0; p = dg * fr;
        F = (int32_t)(w_ >> 32) + (int32_t)(BHM(fr, (g0 << 13) + p) >> (13 + 9));
        d = x - x1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (ad < BH_AT) {
            int32_t v_ = g0 + (p >> 12);
            v_ = x < 0 ? -v_ : v_;
            v = v_ - ((dg * d) >> 13);
        } else {
            int nz = __builtin_clz(ad);
            int32_t r = (int32_t)rcp[(ad << nz) >> (32 - DRV_AA_RB)];
            int32_t df = F - F1;
            if (d < 0) df = -df;
            v = (int32_t)(BHM(df, r) >> (47 - 9 - nz));
        }
        x1 = x; F1 = F;
        *m++ = v;
    }
    st->ax1 = x1; st->aF1 = F1;
}
#endif

/* the four passes on n (even) samples, in to out (out may be in): the kernel (tests/c/drv_kern_test.c calls it with
 * its own rows) */
static __attribute__((noinline)) void bh_kernel(const bh_coef_t *k, bh_state_t *st, const int32_t *in, int32_t *out,
                                                uint32_t n)
{
#if BHLUE_AA
    bh_hps2qs(k, st, in, out, n);
    bh_pre2qc(k, st, out, n);
    bh_aa1(st, out, n);
#else
    bh_hps2q(k, st, in, out, n);
    bh_pre2q(k, st, out, n);
    bh_clip1(out, n);
#endif
    bh_post2q(k, st, out, n);
}

/* the rows of steps (a, t); POST's c x the level: c' = c L >> 24, L = LVA[a] LVT[t] >> 15 (exactly c at L = 2^24) */
/* a table word stored as two int16 (high, low: no word in the chip-ROM window in the image) */
static inline int32_t bh_w(const int16_t *p, int i) { return (int32_t)(((uint32_t)(uint16_t)p[2 * i] << 16) | (uint16_t)p[2 * i + 1]); }
/* r0 + ((r1 - r0) w >> 8), 64-bit (tools/drive_gen/bhlue.py lerp) */
static int32_t bh_lerp(int32_t r0, int32_t r1, int32_t w) { return r0 + (int32_t)((((int64_t)r1 - r0) * w) >> 8); }
/* word i of a split16 row table at position pos (row << 8 | w): the row, or between it and the next (k words a row) */
static int32_t bh_row(const int16_t *R, int k, uint32_t pos, int i)
{
    const int16_t *p = R + 2 * k * (int)(pos >> 8);
    return pos & 255u ? bh_lerp(bh_w(p, i), bh_w(p + 2 * k, i), (int32_t)(pos & 255u)) : bh_w(p, i);
}
static int32_t bh_lv(const int32_t *L, uint32_t pos)
{
    return pos & 255u ? bh_lerp(L[pos >> 8], L[(pos >> 8) + 1u], (int32_t)(pos & 255u)) : L[pos >> 8];
}
static void bh_set(int32_t a, int32_t t)
{
    uint32_t pa = BHLUE_PA[a], pt = BHLUE_PT[t];
    int32_t L = (int32_t)(((int64_t)bh_lv(BHLUE_LVA, pa) * bh_lv(BHLUE_LVT, pt)) >> 15), *k = &bh_k.pa, i;
    for (i = 0; i < 7; i++) k[i] = bh_row(&BHLUE_PRE[0][0], 7, pa, i);          /* pa ph b0 b1 b2 a1 a2 */
    for (i = 0; i < 3; i++) k[7 + i] = (int32_t)(((int64_t)bh_row(&BHLUE_POST[0][0], 5, pt, i) * L) >> 24);
    bh_k.d1 = bh_row(&BHLUE_POST[0][0], 5, pt, 3); bh_k.d2 = bh_row(&BHLUE_POST[0][0], 5, pt, 4);
    bh_k.lo = -BH_CLAMP; bh_k.hi = BH_CLAMP;
#if BHLUE_AA
    bh_k.s0 = BHLUE_SH[0]; bh_k.s1 = BHLUE_SH[1]; bh_k.sp = BHLUE_SH[2];
#endif
}

static int32_t bh_step(int32_t v, int32_t tgt)
{
    return v < tgt - BHLUE_GSTEP ? v + BHLUE_GSTEP : v > tgt + BHLUE_GSTEP ? v - BHLUE_GSTEP : tgt;
}

static void drv_bhlue_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    int32_t a = c->amount, t = c->tone;
#if BHLUE_AA
    if (!bh_gf_ok) { if (out != in) memcpy(out, in, n * sizeof *out); return; }    /* (before drv_boot: dry) */
#endif
    if (bh_ra < 0) { bh_ra = a; bh_rt = t; bh_set(a, t); }                         /* (after a reset: the knobs) */
    else if (bh_ra != a || bh_rt != t) { bh_ra = bh_step(bh_ra, a); bh_rt = bh_step(bh_rt, t); bh_set(bh_ra, bh_rt); }
    bh_kernel(bh_kp, &bh_s, in, out, n & ~1u);
}
