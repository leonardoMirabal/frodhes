/* SPDX-License-Identifier: GPL-3.0-only */
/* DRIVE TYPE 2, KHLON: the clean-blend overdrive. A gain stage (its feedback per AMOUNT) drives a pair of germanium
 * diodes to ground through a coupling capacitor; a clean path (two clean feeds, one working against the gain pot) is
 * summed with it in an inverting amp with an active treble control (TONE: cut .. boost). AMOUNT 1..127 is the GAIN
 * knob from its minimum (a clean boost) to its maximum; TONE is the TREBLE knob.
 *
 * Per sample, loops over the block (the arithmetic of the private reference kernel, bit for bit:
 * tests/py/test_drive_kernels_b.py), signals in volts Q20 (the bus x 32: 1.0 V for the engine's full scale):
 *   O1   the gain stage, a biquad (transposed form II, 64-bit states)                 in -> kh_tmp
 *   CL   the clean paths, first order                                                 in -> out
 *   then, per AMOUNT row ("post", AMOUNT <= 125): the clipper (the +-4.5 V rail, the coupling capacitor's state, the
 *        diodes' curve KH_GF) into kh_tmp, and the slot (its own path into the sum, first order) added into out;
 *        ("pre", 126 and 127): the slot as the gain stage's third order, then the clipper into kh_tmp, added into out
 *   POST the summing amp's pole x the treble control, a biquad; its b carry the level (per AMOUNT x per TONE: about
 *        the loudness of the drive switched off) and the volts -> bus scale
 * The clipper, from AMOUNT 77 (GAIN 0.6) up: first-order antiderivative anti-aliasing without a divide (the mean of
 * the curve between the last input and this one: (F(e) - F(e1)) / (e - e1), F the exact integral of the interpolated
 * curve; the quotient by count-leading-zeros, a reciprocal table and one 32 x 32 -> 64 multiply; |e - e1| < 2^12: the
 * curve at the midpoint), then a fixed droop compensation w = v + 0.148 (v - v1) where its output enters the sum (the
 * capacitor's state is fed the uncompensated v). Below AMOUNT 77 the plain curve on the same table (the reference's
 * values bit for bit; its folding stays under -40 dB there). |e| stays below 9 V (the capacitor's state is a running
 * mix of the clamped input): the table holds 0 .. 9.06 V, the curve is odd.
 *
 * Knobs: the rows glide one step a block. The post / pre change (between AMOUNT 125 and 126) is not a row step: the
 * two modes' states do not map onto each other, so it dips through the dry signal, a block each: the wet fades out
 * (5.8 ms), the new mode starts from rest and runs a block under the dry signal, then fades in (measured on the
 * reference: what is left of its start is 55 dB or more under the signal's peak; a switch with the states kept
 * clicked louder than the music). The fades ride on the last loop (POST, the blend fused in), so a dip block costs a
 * few instructions a sample more than the others.
 * Cost: tools/drive_insns.py counts the loops (pi32v2 -Os); DRV_US_KHLON is the longest block, a fade block of the dip,
 * at ~1.6 us per instruction per sample. */
#ifndef KH_AA                                   /* the anti-aliased clipper: 0 until it fits the budget (below) */
#define KH_AA 0
#endif
#if !KH_AA       /* plain (pi32v2 -Os, the firmware build, per sample): post 69, pre 70, a dip block 80.5 (was 79 / 83 /
                  * 96 counted the same way: O1+CL 25, clipper 21 (pre, adding into the sum: 24), slot 10, POST 13, the
                  * dip's blend 10.5) */
#define DRV_US_KHLON 129u
#define KH_AA_FROM 127u
#else                                           /* AA: post 96, pre 100, a dip block 109: over the budget (no build) */
#define DRV_US_KHLON 177u
#define KH_AA_FROM KH_AA_A
#endif
#define KH_M64(a, b) ((int64_t)(a) * (int64_t)(b))
#ifndef KH_FN
#define KH_FN static __attribute__((noinline))
#endif

static DRV_BSS int32_t kh_o1[5], kh_cl[3], kh_sl[3], kh_po[5];   /* the rows that run: b0 (b1 b2) a1 (a2), Q24, a negated */
static DRV_BSS int64_t kh_so1[2], kh_spo[2], kh_scl, kh_ssl;      /* transposed form II states */
static DRV_BSS int32_t kh_sc;                                     /* the coupling capacitor (Q20) */
static DRV_BSS int32_t kh_tmp[DRV_BLK], kh_u[DRV_BLK];             /* the driven path; the sum while it dips */
static DRV_BSS const int32_t *kh_gf;                              /* the plain clipper's node at 0 V (KH_GF) */
static DRV_BSS int32_t kh_e1, kh_F1, kh_v1;                       /* the AA clipper: the last e, F(e1), the last v */
static DRV_BSS uint8_t kh_aa;                                     /* the AA clipper runs (AMOUNT index >= KH_AA_A) */
static DRV_BSS int32_t kh_kc4, kh_rail;                           /* (in RAM: loaded into registers, as the reference) */
static DRV_DATA uint8_t kh_ra = 0xFFu, kh_rt, kh_pre, kh_dip, kh_held;      /* rows (AMOUNT 0..126, TONE 0..128); 0xFF: none yet */
/* each section's last two inputs and outputs (x1 x2 y1 y2), kept at the end of a block: when a row changes, the
 * transposed states are rebuilt from them with the new coefficients, so a row change acts as in direct form I (the
 * states are the signal's history, not partial sums of the old coefficients: no zipper) */
static DRV_BSS int32_t kh_ho1[4], kh_hcl[4], kh_hsl[4], kh_hpo[4];

static void kh_tdf2(int64_t *st, const int32_t *c, const int32_t *h)   /* biquad: b0 b1 b2 a1 a2 */
{
    st[0] = KH_M64(c[1], h[0]) + KH_M64(c[3], h[2]) + KH_M64(c[2], h[1]) + KH_M64(c[4], h[3]);
    st[1] = KH_M64(c[2], h[0]) + KH_M64(c[4], h[2]);
}
static int64_t kh_tdf1(const int32_t *c, const int32_t *h)              /* first order: b0 b1 a1 */
{
    return KH_M64(c[1], h[0]) + KH_M64(c[2], h[2]);
}

static int32_t kh_unf(const uint32_t *r0, uint32_t i, uint32_t h)   /* a stored value (or the mean of two rows) */
{
    int32_t v = (int32_t)(r0[i] ^ 0x80000000u);    /* (stored with the sign bit flipped: drive_gen/khlon.py) */
    return h ? (v + (int32_t)(r0[11u + i] ^ 0x80000000u)) >> 1 : v;
}
static uint32_t kh_mode_of(uint32_t ra) { return (uint32_t)(KH_AMAP[ra] >> 1) >= KH_PRE_ROW; }
/* F at the carried e1 (once, when the AA rows start) */
static int32_t kh_F(int32_t e)
{
    int32_t a = e < 0 ? -e : e, fr = a & ((1 << 14) - 1), g0, dg;
    const int32_t *p = KH_GF + ((a >> 14) << 1);
    g0 = p[0]; dg = p[2] - g0;
    return p[1] + (int32_t)(KH_M64(fr, g0 + ((dg * fr) >> 15)) >> 13);
}
static void kh_load(void)                        /* the rows -> the coefficients (only when one changes) */
{
    uint32_t ga = KH_AMAP[kh_ra], gr = ga >> 1, gh = ga & 1u, tr = kh_rt >> 1, th = kh_rt & 1u, i;
    const uint32_t *g = KH_G[gr], *t = KH_T[tr];
    int32_t gs = gh ? (KH_GS[gr] + KH_GS[gr + 1]) >> 1 : KH_GS[gr];
    int32_t lt = th ? (KH_LT[tr] + KH_LT[tr + 1]) >> 1 : KH_LT[tr];
    int32_t s = (int32_t)(((int64_t)gs * lt + (1 << 13)) >> 14);   /* Q24 */
    for (i = 0; i < 5; i++) kh_o1[i] = kh_unf(g, i, gh);
    for (i = 0; i < 3; i++) { kh_cl[i] = kh_unf(g, 5 + i, gh); kh_sl[i] = kh_unf(g, 8 + i, gh); }
    for (i = 0; i < 5; i++) {
        int32_t v = (int32_t)(t[i] ^ 0x80000000u);
        if (th) v = (v + (int32_t)(t[5 + i] ^ 0x80000000u)) >> 1;
        kh_po[i] = i < 3 ? (int32_t)((KH_M64(v, s) + (1 << 23)) >> 24) : v;
    }
    kh_pre = (uint8_t)(gr >= KH_PRE_ROW);
    if (kh_ra >= KH_AA_FROM && !kh_aa) kh_F1 = kh_F(kh_e1);   /* (the AA rows start: F1 from the carried e1) */
    kh_aa = kh_ra >= KH_AA_FROM;
    kh_gf = KH_GF; kh_kc4 = KH_KC4; kh_rail = KH_RAIL;
}
static void kh_rest(void)                        /* every state to rest */
{
    kh_so1[0] = kh_so1[1] = kh_spo[0] = kh_spo[1] = kh_scl = kh_ssl = 0;
    kh_sc = kh_e1 = kh_F1 = kh_v1 = 0;              /* (F(0) = 0) */
    memset(kh_ho1, 0, sizeof kh_ho1); memset(kh_hcl, 0, sizeof kh_hcl);
    memset(kh_hsl, 0, sizeof kh_hsl); memset(kh_hpo, 0, sizeof kh_hpo);
}
static void kh_hist(int32_t *h, int32_t x1, int32_t x2, int32_t y1, int32_t y2)
{
    h[0] = x1; h[1] = x2; h[2] = y1; h[3] = y2;
}
static void drv_khlon_reset(void)
{
    kh_rest();
    kh_ra = 0xFFu; kh_dip = kh_held = 0;                  /* the next run starts at the knobs' rows, no glide */
}

/* a biquad, transposed form II: in -> out (in may be out); IN_SH: the input's shift to Q20 */
#define KH_BQ(c, st, in, out, IN_SH) do {                                                  \
        int32_t b0 = (c)[0], b1 = (c)[1], b2 = (c)[2], a1 = (c)[3], a2 = (c)[4];         \
        int64_t s1 = (st)[0], s2 = (st)[1];                                                \
        for (i = 0; i < n; i++) {                                                          \
            int32_t x = (in)[i] << (IN_SH), y;                                             \
            y = (int32_t)((s1 + KH_M64(b0, x)) >> 24);                                     \
            s1 = s2 + KH_M64(b1, x) + KH_M64(a1, y);                                       \
            s2 = KH_M64(b2, x) + KH_M64(a2, y);                                            \
            (out)[i] = y;                                                                  \
        }                                                                                  \
        (st)[0] = s1; (st)[1] = s2; } while (0)
/* a first-order section, transposed form II; STORE(y) */
#define KH_OP(c, st, in, IN_SH, STORE) do {                                                \
        int32_t b0 = (c)[0], b1 = (c)[1], a1 = (c)[2];                                     \
        int64_t s = (st);                                                                  \
        for (i = 0; i < n; i++) {                                                          \
            int32_t x = (in)[i] << (IN_SH), y;                                             \
            y = (int32_t)((s + KH_M64(b0, x)) >> 24);                                      \
            s = KH_M64(b1, x) + KH_M64(a1, y);                                             \
            STORE;                                                                         \
        }                                                                                  \
        (st) = s; } while (0)
/* the clipper's input: e = rail(x) - sc; its node pointer, fraction, g0, dg for |e| (the curve is odd) */
#define KH_E(x)                                                                                 \
            e = (x); e = e < nL ? nL : e; e = e > L ? L : e; e -= sc;                           \
            a = e < 0 ? -e : e;                                                                 \
            p = gf + ((a >> 14) << 1);                                                          \
            fr = a & ((1 << 14) - 1);                                                           \
            g0 = p[0]; dg = p[2] - g0;
#define KH_AA_SF 12                                     /* F in Q28 = Q40 >> 12 */
#ifndef KH_BETA_Q8
#define KH_BETA_Q8 38                                   /* the droop compensation: 0.148 */
#endif
/* O1 and CL in one pass (the input read and scaled once): in -> kh_tmp (O1), u (CL); in may be u */
KH_FN void kh_o1cl_pass(const int32_t *in, int32_t *u, uint32_t n)
{
    int32_t b0 = kh_o1[0], b1 = kh_o1[1], b2 = kh_o1[2], a1 = kh_o1[3], a2 = kh_o1[4];
    int32_t c0 = kh_cl[0], c1 = kh_cl[1], ca = kh_cl[2], *t = kh_tmp;
    int64_t s1 = kh_so1[0], s2 = kh_so1[1], sc = kh_scl;
    const int32_t *e = in + n;
    while (in < e) {
        int32_t x = *in++ << 5, y, z;
        y = (int32_t)((s1 + KH_M64(b0, x)) >> 24);
        s1 = s2 + KH_M64(b1, x) + KH_M64(a1, y);
        s2 = KH_M64(b2, x) + KH_M64(a2, y);
        *t++ = y;
        z = (int32_t)((sc + KH_M64(c0, x)) >> 24);
        sc = KH_M64(c1, x) + KH_M64(ca, z);
        *u++ = z;
    }
    kh_so1[0] = s1; kh_so1[1] = s2; kh_scl = sc;
}
/* the plain clipper (rows below KH_AA_A), on the signed node table KH_GFS (the curve over -9.06 .. 9.06 V, its
 * negative half mirrored): e's cell and fraction come from e itself (two's complement: e = (e >> 14) 2^14 + (e &
 * 16383)), no |e| and no sign select. g0, D are even (Q21 = 2 x the reference's Q20). For e >= 0 (g0 + (D fr) >> 14)
 * >> 1 is the reference's G + floor(d fr / 2^14); for e < 0 the reference's rounding in its own (negative)
 * coordinates (|e|'s cell, the bias 0x7ffe) comes out as the same expression in e's cell (D fr is even), bit for bit:
 * checked for every e of +-9 V, and tests/py/test_drive_kernels_b.py.
 * Keeps e1 current for a switch to the AA rows. KH_PV: one sample, e and v. */
#define KH_PV(x)                                                                                   \
            e = (x); e = e < nL ? nL : e; e = e > L ? L : e; e -= sc;                              \
            p = gs + ((e >> 14) << 1); g0 = p[0];                                                  \
            v = (g0 + (((p[2] - g0) * (e & ((1 << 14) - 1))) >> 14)) >> 1;                         \
            sc += (int32_t)(KH_M64(e - v, kc4) >> 32);
/* post: the clipper in place on kh_tmp */
KH_FN void kh_clipv_pass(uint32_t n)
{
    const int32_t *gs = kh_gf;
    int32_t sc = kh_sc, kc4 = kh_kc4, L = kh_rail, nL = -kh_rail, e = kh_e1, *t = kh_tmp, *te = kh_tmp + n;
    while (t < te) {
        int32_t g0, v;
        const int32_t *p;
        KH_PV(*t)
        *t++ = v;
    }
    kh_sc = sc; kh_e1 = e;
}
/* pre: the clipper, its V_S added into u (kh_tmp keeps the input: the last two for the history) */
KH_FN void kh_clipvadd_pass(int32_t *u, uint32_t n)
{
    const int32_t *gs = kh_gf, *t = kh_tmp;
    int32_t sc = kh_sc, kc4 = kh_kc4, L = kh_rail, nL = -kh_rail, e = kh_e1, *ue = u + n;
    while (u < ue) {
        int32_t g0, v;
        const int32_t *p;
        KH_PV(*t++)
        *u++ += v;
    }
    kh_sc = sc; kh_e1 = e;
}
/* the anti-aliased clipper (rows from KH_AA_A), V_S into kh_tmp: F(e) = F[k] + (fr (g0 + (dg fr) >> 15)) >> 13;
 * |d| = |e - e1| >= 2^12: v = (F - F1) 2^12 / d by clz, KH_RCP and one 64-bit product; else g at the midpoint */
KH_FN void kh_clipaa_pass(uint32_t n)
{
    const int32_t *gf = KH_GF;
    const uint16_t *rc = KH_RCP - (1 << 10);
    int32_t sc = kh_sc, kc4 = kh_kc4, L = kh_rail, nL = -kh_rail, e1 = kh_e1, F1 = kh_F1;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t e, a, fr, g0, dg, pr, F, d, v;
        uint32_t ad;
        const int32_t *p;
        KH_E(kh_tmp[i])
        pr = dg * fr;
        F = p[1] + (int32_t)(KH_M64(fr, g0 + (pr >> 15)) >> 13);
        d = e - e1;
        ad = (uint32_t)(d < 0 ? -d : d);
        if (__builtin_expect(ad >= (1u << KH_AA_SF), 1)) {
            int nz = __builtin_clz(ad);
            uint32_t r = rc[(ad << nz) >> (32 - 11)];
            int32_t q = (int32_t)(KH_M64(F - F1, r) >> (47 - KH_AA_SF - nz));
            v = d < 0 ? -q : q;
        } else {
            int32_t gm = g0 + (pr >> 14);
            v = ((e < 0 ? -gm : gm) - ((dg * d) >> 15)) >> 1;
        }
        e1 = e; F1 = F;
        sc += (int32_t)(KH_M64(e - v, kc4) >> 32);
        kh_tmp[i] = v;
    }
    kh_sc = sc; kh_e1 = e1; kh_F1 = F1;
}
KH_FN void kh_sladd_pass(int32_t *u, uint32_t n) /* post: u += the slot (V_S); on pointers */
{
    int32_t b0 = kh_sl[0], b1 = kh_sl[1], a1 = kh_sl[2];
    int64_t s = kh_ssl;
    const int32_t *t = kh_tmp, *te = kh_tmp + n;
    while (t < te) {
        int32_t x = *t++, y;
        y = (int32_t)((s + KH_M64(b0, x)) >> 24);
        s = KH_M64(b1, x) + KH_M64(a1, y);
        *u++ += y;
    }
    kh_ssl = s;
}
KH_FN void kh_sl_pass(uint32_t n)                /* pre: the third order, in place */
{
    uint32_t i;
    KH_OP(kh_sl, kh_ssl, kh_tmp, 0, kh_tmp[i] = y);
}
/* the AA rows' droop compensation w = v + beta (v - v1), fused where V_S enters the sum: post mode u += slot(w) */
KH_FN void kh_slfadd_pass(int32_t *u, uint32_t n)
{
    int32_t b0 = kh_sl[0], b1 = kh_sl[1], a1 = kh_sl[2], v1 = kh_v1;
    int64_t s = kh_ssl;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t v = kh_tmp[i], x = v + (((v - v1) * KH_BETA_Q8) >> 8), y;
        v1 = v;
        y = (int32_t)((s + KH_M64(b0, x)) >> 24);
        s = KH_M64(b1, x) + KH_M64(a1, y);
        u[i] += y;
    }
    kh_ssl = s; kh_v1 = v1;
}
/* pre mode: u += w */
KH_FN void kh_firadd_pass(int32_t *u, uint32_t n)
{
    int32_t v1 = kh_v1;
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t v = kh_tmp[i];
        u[i] += v + (((v - v1) * KH_BETA_Q8) >> 8);
        v1 = v;
    }
    kh_v1 = v1;
}
KH_FN void kh_post_pass(int32_t *u, uint32_t n)
{
    uint32_t i;
    KH_BQ(kh_po, kh_spo, u, u, 0);
}
/* a dip's fade block: POST into u, then the blend over the dry signal, out = dry + (POST(u) - dry) w / 256, w = 1..n
 * (up: fading in) or n - 1..0 (fading out) a sample (a block of DRV_BLK = 256 ends at 0 or 1; a smooth ramp: one
 * stepped every 4 samples is heard as highs by the click test), the product 32-bit (|POST(u) - dry| < 2^23). The
 * same pass whatever the clipper (plain or anti-aliased): a fade block costs POST + the blend, two samples a pass. */
/* the dip's dry at the wet's level (0.9.6, #5: the wet and the dry differ by a few dB, so the 17 ms dip through the
 * plain dry was a level jump of 3.9 dB in the control sweep's windows): the dry x kh_dg (Q12, 1/4 .. 4), the RMS
 * ratio of the wet to the dry over the dip's first block (every 4th sample), taken there (a square root of each, then a
 * 32-bit divide) */
static DRV_BSS int32_t kh_dg;
static uint32_t kh_isqrt(uint64_t v)              /* floor(sqrt(v)), bit by bit (no divide) */
{
    uint64_t r = 0, b = (uint64_t)1 << 62;
    while (b > v) b >>= 2;
    while (b) { if (v >= r + b) { v -= r + b; r = (r >> 1) + b; } else r >>= 1; b >>= 2; }
    return (uint32_t)r;
}
static void kh_dip_gain(const int32_t *dry, const int32_t *y, uint32_t n)
{
    uint64_t ew = 0, ed = 0;
    uint32_t i, rw, rd;
    for (i = 0; i < n; i += 4u) { ew += (uint64_t)((int64_t)y[i] * y[i]); ed += (uint64_t)((int64_t)dry[i] * dry[i]); }   /* (every 4th sample: the CPU of a dip block) */
    rw = kh_isqrt(ew); rd = kh_isqrt(ed);
    while (rw >= (1u << 18) || rd >= (1u << 18)) { rw >>= 1; rd >>= 1; }
    kh_dg = !rd ? 4096 : (int32_t)((rw << 12) / rd);
    kh_dg = kh_dg < 1024 ? 1024 : kh_dg > 16384 ? 16384 : kh_dg;
}
static inline int32_t kh_dry(int32_t d) { return (int32_t)(((int64_t)d * kh_dg) >> 12); }
KH_FN void kh_fade_pass(const int32_t *dry, const int32_t *y, int32_t *out, uint32_t n, int32_t w, int32_t dw)
{
    const int32_t *e = dry + (n & ~1u);
    while (dry < e) {
        int32_t d0 = kh_dry(dry[0]), d1 = kh_dry(dry[1]);
        out[0] = d0 + (((y[0] - d0) * w) >> 8);
        out[1] = d1 + (((y[1] - d1) * (w + dw)) >> 8);
        dry += 2; y += 2; out += 2; w += 2 * dw;
    }
    if (n & 1u) out[0] = kh_dry(dry[0]) + (((y[0] - kh_dry(dry[0])) * w) >> 8);
}
static void kh_fade(const int32_t *in, int32_t *u, int32_t *out, uint32_t n, uint32_t up)
{
    kh_post_pass(u, n);
    if (up) kh_fade_pass(in, u, out, n, 1, 1);
    else { kh_dip_gain(in, u, n); kh_fade_pass(in, u, out, n, (int32_t)n - 1, -1); }
}

/* the wet signal of the block, in -> out (out may be in); n >= 3 */
static void drv_khlon_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    uint32_t ta = (uint32_t)(c->amount - 1), tt = (uint32_t)(c->tone + ((c->tone + 1) >> 7));   /* 0..126, 0..128 */
    uint32_t tpre = kh_mode_of(ta), ph = kh_dip;
    int32_t *u = out, cl1, cl2, x1, x2;
    if (kh_ra == 0xFFu) { kh_ra = (uint8_t)ta; kh_rt = (uint8_t)tt; kh_load(); ph = 0; }
    else {
        uint32_t ra = kh_ra, rt = kh_rt;
#ifdef KH_DIP_TEST_OFF                            /* (a test build: the mode change without the dip) */
        if (tpre != kh_pre) ph = 2;
#else
        if (!ph && tpre != kh_pre) ph = 1;       /* the other mode: the dip (1 fade out, 2 under the dry, 3 fade in) */
#endif
        if (ph == 2) {                           /* the dip's floor: the mode the knob is in, from rest */
            ra = ta; rt = tt; kh_rest();
            kh_ra = (uint8_t)ra; kh_rt = (uint8_t)rt; kh_load();
        } else if (!ph && !kh_held) {            /* one row a block, inside the mode that runs (held while it dips, and
                                                  * the block after: POST's history is whole again) */
            if (ra != ta) {
                uint32_t nr = ra < ta ? ra + 1u : ra - 1u;
                if (kh_mode_of(nr) == kh_pre) ra = nr;
            }
            rt = rt < tt ? rt + 1u : rt > tt ? rt - 1u : rt;
            if (ra != kh_ra || rt != kh_rt) {    /* new coefficients; the states from the history */
                kh_ra = (uint8_t)ra; kh_rt = (uint8_t)rt; kh_load();
#ifndef KH_REBUILD_TEST_OFF                       /* (a test build: the states kept as they are) */
                kh_tdf2(kh_so1, kh_o1, kh_ho1); kh_scl = kh_tdf1(kh_cl, kh_hcl);
                kh_ssl = kh_tdf1(kh_sl, kh_hsl); kh_tdf2(kh_spo, kh_po, kh_hpo);
#endif
            }
        }
    }
#ifdef KH_DIP_TEST_OFF
    ph = 0;
#endif
    if (ph) u = kh_u;                            /* (the dry signal stays in in) */
    x1 = in[n - 1] << 5; x2 = in[n - 2] << 5;      /* (in may be u: overwritten) */
    kh_o1cl_pass(in, u, n);
    kh_hist(kh_ho1, x1, x2, kh_tmp[n - 1], kh_tmp[n - 2]);
    kh_hist(kh_hcl, x1, x2, cl1 = u[n - 1], cl2 = u[n - 2]);
    if (!kh_pre) {
        int32_t w1, w2;
        if (!kh_aa) {
            kh_clipv_pass(n); kh_sladd_pass(u, n);
            kh_v1 = w1 = kh_tmp[n - 1]; w2 = kh_tmp[n - 2];
        } else {
            kh_clipaa_pass(n); kh_slfadd_pass(u, n);
            w1 = kh_tmp[n - 1] + (((kh_tmp[n - 1] - kh_tmp[n - 2]) * KH_BETA_Q8) >> 8);
            w2 = kh_tmp[n - 2] + (((kh_tmp[n - 2] - kh_tmp[n - 3]) * KH_BETA_Q8) >> 8);
        }
        kh_hist(kh_hsl, w1, w2, u[n - 1] - cl1, u[n - 2] - cl2);
    } else {
        kh_sl_pass(n);
        kh_hist(kh_hsl, kh_ho1[2], kh_ho1[3], kh_tmp[n - 1], kh_tmp[n - 2]);
        if (kh_aa) { kh_clipaa_pass(n); kh_firadd_pass(u, n); }
        else kh_clipvadd_pass(u, n);
    }
    kh_hpo[0] = u[n - 1]; kh_hpo[1] = u[n - 2];
    if (!ph) kh_post_pass(u, n);
    else {
        if (ph != 2) kh_fade(in, u, out, n, ph == 3);
        else {
            uint32_t i;
            kh_post_pass(u, n);
            for (i = 0; i < n; i++) out[i] = kh_dry(in[i]);   /* (the dry at the wet's level) */
        }
        ph = ph < 3u ? ph + 1u : 0u;
    }
    kh_hpo[2] = u[n - 1]; kh_hpo[3] = u[n - 2];  /* POST's output (in a dip block: not; the rows hold the block after) */
    kh_held = u != out;
    kh_dip = (uint8_t)ph;
}
