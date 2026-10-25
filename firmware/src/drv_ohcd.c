/* SPDX-License-Identifier: GPL-3.0-only */
/* DRIVE TYPE 4, OHCD: the MOSFET overdrive: a high-gain non-inverting stage whose output is clipped by two MOSFET body
 * diodes, a second gain stage, a passive treble-cut tone (the HP position of its HP / LP switch, the circuit's default).
 * AMOUNT 1..127 is the circuit's DRIVE knob over its whole range (0.9.5; it was capped at 10 o'clock): AMOUNT <= 32 as
 * the capped range had it (the same rows), above it the first stage's peak gain rises evenly in dB to the knob's end;
 * both op-amps' rails are modelled (+-2.8 V): stage 1's before the clipper, stage 2's (IC1B) after it, each with
 * anti-aliasing. A 0.3 V input scale; TONE is its TONE knob. The circuit and its tables: tools/drive_gen/ohcd.py. 1x,
 * no oversampling, no divide.
 *
 * Once a block (drive.c: n <= DRV_BLK, even), on pointers, two samples an iteration, the reference kernel's arithmetic
 * bit for bit (tests/c/drv_kern_ref.c; tests/py/test_drive_kernels.py). Four passes, each its own function:
 *   A  the input high-pass (a shift-only one-pole, ~14 Hz, on x << 8) and stage 1 x P (a biquad per AMOUNT step, direct
 *      form I: its states are the signal's own history, so a row change is clean; C4's pole and the droop split
 *      before the clipper in it)                                                                    -> u
 *   C  oh_aa1: stage 1's rail (a clamp at 2^17 u) and the clipper with ADAA-1 (no divide: clz and the shared
 *      reciprocal table DRV_AA_RCP; its exact antiderivative on a {2 g, F, step} node table; the midpoint below |du|
 *      4), output u x 2^12. (The rail sits after C4 here, before it in the circuit: past 2.8 V the
 *      clipper's curve is all but flat, so the order moves little.)
 *   B  oh_s2r: stage 2 x Q (one row per AMOUNT group) -> u x 2^8, then the IC1B rail with ADAA-1 (oh_rail: inside the
 *      rail the mean of two samples, past it on one side the rail, a crossing the exact quotient through the
 *      reciprocal table)
 *   D  oh_out: the tone x 1 / cos (the rail's ADAA droop, so a signal below the rails comes out as before) as one
 *      biquad, its b x the level (per AMOUNT x per TONE, about the loudness of the drive off); W = the bus x 2^4
 *      inside, the bus out, rounded
 * Units: u = 2.8 V / 2^17 (stage 1 out, the curve); the bus (Q15) in and out.
 * Rows: 65 stored per knob (tools/drive_gen/ohcd.py: every other step), a step between two stored rows their mean.
 * Knobs: the steps that run glide toward the knobs, OHCD_GSTEP steps a block (the whole range in 64 blocks, 0.37 s:
 * no click); the coefficients are copied (and the tone's b scaled) only when a step moves. */
/* compiled (pi32v2 -Os, tools/drive_insns.py; two-sample loops halved), per sample: A 19 (oh_s12, 38 / 2) + C 38
 * (oh_aa1, 76 / 2) + B 21 (oh_s2r, 42 / 2, inside the rail) + D 16 (oh_out, 32 / 2) = 94 = 150.4 us at 1.6; ~200
 * instructions of set-up a block (~1.3 us); a sample whose pair crosses the IC1B rail adds ~58 (oh_rail's quotient):
 * 5 % of them (a full-range chord crosses far less) ~4.6 us -> 157 us. (The capped kernel: 78.5, 127 us.) */
#define DRV_US_OHCD 157u
_Static_assert(OHCD_AA, "OHCD runs its anti-aliased kernel only (tools/drive_gen/ohcd.py AA)");
_Static_assert((DRV_BLK & 1u) == 0, "OHCD runs two samples a pass: the block must be even");
#ifndef OHCD_GSTEP
#define OHCD_GSTEP 2                                     /* AMOUNT / TONE steps the rows move at most a block */
#endif
#define OHCD_RAIL (1 << 17)                              /* 2.8 V in u: the curve is flat beyond it */
#define OH_PASS __attribute__((noinline))                /* each pass its own function: its loop gets every register */
typedef struct { int32_t b0, b1, b2, a1, a2, s0, s1, s2, r1, r2, o0, o1, o2, p1, p2; } oh_coef_t;  /* S1 x P, S2 x Q,
                                                          * the tone x 1 / cos (b x the level): every a negated */
typedef struct { int32_t lp, x1, x2, y1, y2, au1, aF1, u1, u2, v1, v2, rp, t1, t2, w1, w2; } oh_state_t;
static DRV_BSS oh_coef_t oh_k;
static DRV_BSS oh_state_t oh_s;
static DRV_DATA oh_coef_t *volatile oh_kp = &oh_k;                /* (read once a block: the loops read the coefficients as the
                                                          * counted design did, not as spilled 64-bit pairs) */
static DRV_DATA int32_t oh_ra = -1, oh_rt;                        /* the AMOUNT and TONE steps that run (-1: take the knobs) */

#define OHM(a, b) ((int64_t)(a) * (b))

static void drv_ohcd_reset(void)
{
    memset(&oh_s, 0, sizeof oh_s);
    oh_ra = -1;
}

/* the clipper with anti-aliasing (first-order antiderivative, ADAA-1, no divide), u in place: v = (F(u) - F(u1)) /
 * (u - u1), F the exact antiderivative of the interpolated curve ({2 g, F, g's step, 0} per node, OHCD_GF: one 64-bit load + one; F even,
 * so |u| indexes it), the quotient by count-leading-zeros, the shared reciprocal table (DRV_AA_RCP) and one 64-bit
 * product; |u - u1| < OHCD_AT: the curve at the midpoint (first order). Clamped to stage 1's rail first (past
 * 2.8 V the curve is flat: the rail's own corners fold back little; an antiderivative held past the rail measured the
 * same fold-back, 0.9.5). One function, so another clipper can replace it. */
#define OHCD_AT 4                                        /* 2^AA_SF, F in u^2 / 4 */
#define OHCD_VSH 12                                      /* the clipper's output: u x 2^12 (the quotient's bits kept) */
static DRV_DATA const int32_t *volatile oh_gfp = &OHCD_GF[0][0];                        /* (read once a block: held in */
static DRV_DATA const uint16_t *volatile oh_rcp = DRV_AA_RCP - (1u << (DRV_AA_RB - 1));  /* registers through the loop) */
static DRV_DATA volatile int32_t oh_railv = OHCD_RAIL;
/* one sample: u (clamped to the rail), the previous u1 and F1 -> the output (u x 2^OHCD_VSH); F of u in *F */
static inline __attribute__((always_inline)) int32_t oh_aa(const int32_t *gf, const uint16_t *rcp, int32_t u,
                                                           int32_t u1, int32_t F1, int32_t *Fo)
{
    int32_t a, t, g2, dg, p, F, d, v;
    const int32_t *q4;
    uint32_t ad;
    int64_t w_;
    a = u < 0 ? -u : u;
    q4 = gf + ((a >> OHCD_CSH) << 2); t = (int32_t)((uint32_t)a << (32 - OHCD_CSH) >> (32 - OHCD_CSH));
    w_ = *(const int64_t *)q4;                           /* {2 g, F} of the node, then g's step to the next */
    g2 = (int32_t)w_; dg = q4[2]; p = dg * t;
    F = (int32_t)(w_ >> 32) + ((t * (g2 + (p >> OHCD_CSH))) >> 3);
    d = u - u1;
    ad = (uint32_t)(d < 0 ? -d : d);
    if (__builtin_expect(ad >= OHCD_AT, 1)) {
        int nz = __builtin_clz(ad);
        int32_t r = (int32_t)rcp[(ad << nz) >> (32 - DRV_AA_RB)];
        int32_t q = (int32_t)(OHM(F - F1, r) >> (47 - 2 - OHCD_VSH - nz));
        v = d < 0 ? -q : q;
    } else {
        int32_t gm = (g2 >> 1) + (p >> OHCD_CSH);
        v = ((u < 0 ? -gm : gm) - ((dg * d) >> (OHCD_CSH + 1))) << OHCD_VSH;
    }
    *Fo = F;
    return v;
}
static OH_PASS void oh_aa1(oh_state_t *st, int32_t *m, uint32_t n)
{
    const int32_t *gf = oh_gfp;
    const uint16_t *rcp = oh_rcp;
    int32_t u1 = st->au1, F1 = st->aF1, *e = m + n, hi = oh_railv, lo = -hi;
    for (; m != e; m += 2) {
        int32_t ua = m[0], ub = m[1], Fa;
        ua = ua > lo ? ua : lo; ua = ua < hi ? ua : hi;
        ub = ub > lo ? ub : lo; ub = ub < hi ? ub : hi;
        m[0] = oh_aa(gf, rcp, ua, u1, F1, &Fa);
        m[1] = oh_aa(gf, rcp, ub, ua, Fa, &F1);
        u1 = ub;
    }
    st->au1 = u1; st->aF1 = F1;
}

/* A: in -> m (n even): the HP, stage 1 (x P) */
static OH_PASS void oh_s12(const oh_coef_t *k, oh_state_t *st, const int32_t *in,
                                                         int32_t *m, uint32_t n)
{
    int32_t lp = st->lp, x1 = st->x1, x2 = st->x2, y1 = st->y1, y2 = st->y2;
    uint32_t j;
    for (j = n >> 1; j; j--) {
        int32_t va = in[0] << 8, vb = in[1] << 8, xa, xb, ya, yb, c;
        int64_t pa, pb;
        lp += (va - lp) >> 9; xa = va - lp;
        lp += (vb - lp) >> 9; xb = vb - lp;
        c = k->b2; pa = OHM(c, x2); pb = OHM(c, x1);
        c = k->b1; pa += OHM(c, x1); pb += OHM(c, xa);
        c = k->b0; pa += OHM(c, xa); pb += OHM(c, xb);
        c = k->a2; pa += OHM(c, y2); pb += OHM(c, y1);
        c = k->a1; pa += OHM(c, y1);
        ya = (int32_t)(pa >> OHCD_QS1);
        pb += OHM(c, ya);
        yb = (int32_t)(pb >> OHCD_QS1);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        m[0] = ya; m[1] = yb;
        in += 2; m += 2;
    }
    st->lp = lp; st->x1 = x1; st->x2 = x2; st->y1 = y1; st->y2 = y2;
}

/* the IC1B rail (+-OHCD_RAIL2, u x 2^8) with ADAA-1 on one sample v, the one before p: (F(v) - F(p)) / (v - p) of the
 * clamp. Inside the rail that is the mean of the two; both past it on one side, the rail; else the mean less
 * (E(v) - E(p)) / (v - p), E(x) = (|x| - R)^2 / 2 past the rail: in u (>> 8), the quotient by count-leading-zeros
 * and the shared reciprocal table (relative error <= 2^-11 of a term <= half the excess), kept within the rail.
 * tools/drive_gen/ohcd.py
 * rail_ref is the same integers */
static inline __attribute__((always_inline)) int32_t oh_rail(int32_t v, int32_t p, const uint16_t *rcp)
{
    const int32_t R = OHCD_RAIL2;
    int32_t av = v < 0 ? -v : v, ap = p < 0 ? -p : p, ev, ep, d, q;
    uint32_t ad;
    int nz;
    if (__builtin_expect(av <= R && ap <= R, 1)) return (v + p) >> 1;
    if (v > R && p > R) return R;
    if (v < -R && p < -R) return -R;
    ev = av > R ? (av - R) >> 8 : 0;
    ep = ap > R ? (ap - R) >> 8 : 0;
    d = v - p;
    ad = (uint32_t)(d < 0 ? -d : d) >> 8;
    q = 0;
    if (ad) {
        nz = __builtin_clz(ad);
        q = (int32_t)(((OHM(ev, ev) - OHM(ep, ep)) * rcp[(ad << nz) >> (32 - DRV_AA_RB)]) >> (48 - nz)) << 8;
    }
    q = ((v + p) >> 1) - (d < 0 ? -q : q);
    return q > R ? R : q < -R ? -R : q;                  /* (the quotient's error may pass the rail by a few u) */
}

/* B: m in place (n even): stage 2 x Q (direct form I: u x 2^12 in, u x 2^8 out), then the IC1B rail */
static OH_PASS void oh_s2r(const oh_coef_t *k, oh_state_t *st, int32_t *m, uint32_t n)
{
    const uint16_t *rcp = oh_rcp;
    int32_t u1 = st->u1, u2 = st->u2, v1 = st->v1, v2 = st->v2, rp = st->rp, *e = m + n;
    for (; m != e; m += 2) {
        int32_t ua = m[0], ub = m[1], va, vb, c;
        int64_t pa, pb;
        c = k->s2; pa = OHM(c, u2); pb = OHM(c, u1);
        c = k->s1; pa += OHM(c, u1); pb += OHM(c, ua);
        c = k->s0; pa += OHM(c, ua); pb += OHM(c, ub);
        c = k->r2; pa += OHM(c, v2); pb += OHM(c, v1);
        c = k->r1; pa += OHM(c, v1);
        va = (int32_t)(pa >> OHCD_QS2);
        pb += OHM(c, va);
        vb = (int32_t)(pb >> OHCD_QS2);
        m[0] = oh_rail(va, rp, rcp); m[1] = oh_rail(vb, va, rcp);
        u2 = ua; u1 = ub; v2 = va; v1 = vb; rp = vb;
    }
    st->u1 = u1; st->u2 = u2; st->v1 = v1; st->v2 = v2; st->rp = rp;
}

/* D: m in place (n even): the tone x 1 / cos (direct form I, b x the level: u x 2^8 in, W = the bus x 2^4 inside), the
 * bus out, rounded */
static OH_PASS void oh_out(const oh_coef_t *k, oh_state_t *st, int32_t *m, uint32_t n)
{
    int32_t t1 = st->t1, t2 = st->t2, w1 = st->w1, w2 = st->w2, *e = m + n;
    for (; m != e; m += 2) {
        int32_t ta = m[0], tb = m[1], wa, wb, c;
        int64_t pa, pb;
        c = k->o2; pa = OHM(c, t2); pb = OHM(c, t1);
        c = k->o1; pa += OHM(c, t1); pb += OHM(c, ta);
        c = k->o0; pa += OHM(c, ta); pb += OHM(c, tb);
        c = k->p2; pa += OHM(c, w2); pb += OHM(c, w1);
        c = k->p1; pa += OHM(c, w1);
        wa = (int32_t)(pa >> 28);
        pb += OHM(c, wa);
        wb = (int32_t)(pb >> 28);
        m[0] = (wa + 8) >> 4; m[1] = (wb + 8) >> 4;
        t2 = ta; t1 = tb; w2 = wa; w1 = wb;
    }
    st->t1 = t1; st->t2 = t2; st->w1 = w1; st->w2 = w2;
}

/* the four passes on n (even) samples, in to out (out may be in): the kernel (tests/c/drv_kern_test.c calls it with
 * its own rows) */
static __attribute__((noinline)) void oh_kernel(const oh_coef_t *k, oh_state_t *st, const int32_t *in, int32_t *out,
                                                uint32_t n)
{
    oh_s12(k, st, in, out, n);
    oh_aa1(st, out, n);
    oh_s2r(k, st, out, n);
    oh_out(k, st, out, n);
}

/* the rows of steps (a 1..127, t 0..127) -> oh_k: S1 (stored with the sign bit flipped), S2 x Q of the AMOUNT's group
 * (OHCD_SG), the tone x the level (lv = LG LT, Q16). A step between two stored rows: their mean (OHCD_AMAP; TONE
 * j = t + (t + 1) / 128, j / 2 the stored row, j odd the mean) */
static void oh_set(int32_t a, int32_t t)
{
    uint32_t ga = OHCD_AMAP[a - 1], gr = ga >> 1, gh = ga & 1u, tj = (uint32_t)(t + ((t + 1) >> 7)), tr = tj >> 1,
             th = tj & 1u, i;
    const uint32_t *s = OHCD_S1[gr];
    const int32_t *q = OHCD_S2Q[OHCD_SG[a - 1]];
    int32_t lg = gh ? (OHCD_LG[gr] + OHCD_LG[gr + 1]) >> 1 : OHCD_LG[gr];
    int32_t lt = th ? (OHCD_LT[tr] + OHCD_LT[tr + 1]) >> 1 : OHCD_LT[tr];
    int32_t lv = (int32_t)((OHM(lg, lt) + (1 << 13)) >> 14), w[5], o[5];
    for (i = 0; i < 5; i++) {
        int32_t v = (int32_t)(s[i] ^ 0x80000000u);
        if (gh) v = (v + (int32_t)(s[5 + i] ^ 0x80000000u)) >> 1;
        w[i] = i < 3 ? v : -v;
        o[i] = th ? (OHCD_OUT[tr][i] + OHCD_OUT[tr + 1][i]) >> 1 : OHCD_OUT[tr][i];
    }
    oh_k.b0 = w[0]; oh_k.b1 = w[1]; oh_k.b2 = w[2]; oh_k.a1 = w[3]; oh_k.a2 = w[4];
    oh_k.s0 = q[0]; oh_k.s1 = q[1]; oh_k.s2 = q[2]; oh_k.r1 = -q[3]; oh_k.r2 = -q[4];
    oh_k.o0 = (int32_t)(OHM(o[0], lv) >> 16);
    oh_k.o1 = (int32_t)(OHM(o[1], lv) >> 16);
    oh_k.o2 = (int32_t)(OHM(o[2], lv) >> 16);
    oh_k.p1 = -o[3]; oh_k.p2 = -o[4];
}

static int32_t oh_step(int32_t v, int32_t tgt)
{
    return v < tgt - OHCD_GSTEP ? v + OHCD_GSTEP : v > tgt + OHCD_GSTEP ? v - OHCD_GSTEP : tgt;
}

/* the wet signal of the block, in -> out (out may be in) */
static void drv_ohcd_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    int32_t a = c->amount, t = c->tone;
    if (oh_ra < 0) { oh_ra = a; oh_rt = t; oh_set(a, t); }                          /* (after a reset: the knobs) */
    else if (oh_ra != a || oh_rt != t) { oh_ra = oh_step(oh_ra, a); oh_rt = oh_step(oh_rt, t); oh_set(oh_ra, oh_rt); }
    oh_kernel(oh_kp, &oh_s, in, out, n & ~1u);
}
