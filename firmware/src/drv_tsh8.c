/* SPDX-License-Identifier: GPL-3.0-only */
/* DRIVE TYPE 3, TSH8: the mid-hump overdrive: two diodes in the clipping amplifier's feedback, so the output is the
 * clean input plus a high-passed, clipped branch (the lows stay at unity gain), then a low-pass and an active tone.
 * The circuit and its tables: tools/drive_gen/tsh8.py. 1x, no oversampling, no divide.
 *
 * Once a block (drive.c: n <= DRV_BLK, even), four passes, two samples per iteration where it pays:
 *   1  the input high-pass (one corner for both couplings; x TSH8_IN_K: full scale = 0.301 V)  -> vi (volts Q24)
 *   2  the branch biquad (per AMOUNT step: 720 Hz high-pass x the 51 pF low-pass x the gain)    -> u (volts Q14), in m
 *   3  V = g(u): the diode table of this AMOUNT step (RAM, built from the 8 keys), linear interpolation, two ranges
 *   4  vi + V -> the tone biquad (per TONE step, x the level x 2^TSH8_OSH) -> >> TSH8_OSH -> m
 * Compiled (pi32v2 -Os, tools/drive_insns.py): see DRV_US_TSH8.
 *
 * Knobs: the AMOUNT and TONE steps that run glide toward the knobs, TSH8_GSTEP steps a block (the whole range in
 * 64 blocks, 0.37 s: no click); the branch and tone rows (and the level) are copied to RAM only when a step moves.
 * Rows: 64 per knob (tools/drive_gen/tsh8.py: grids where the coefficients move smoothly), a step between two of them
 * interpolated when it moves (TSH8_PA / TSH8_PT); the levels the same way.
 * The diode table follows AMOUNT behind, at most TSH8_TSTEP steps a swap: the next one is built in the second RAM
 * buffer, 32 nodes a block (505 nodes: 16 blocks, 93 ms), then swapped in at the next block's start, crossfaded
 * from the old one over its first 16 samples (a block builds or swaps, never both: the worst block's cost). The very first table (the first time TSH8 runs after
 * power-on) is built whole in one call: ~4,000 instructions once (the kernel runs in that block too: a pass-through
 * block there clicked as the fade-in reached it); now at boot (ts_first, drv_boot), never in an audio block. */
/* compiled (pi32v2 -Os, tools/drive_insns.py; two-sample loops halved), the longest paths, per sample:
 * TSH8_AA 1 (the anti-aliased kernel, on): input HP x P 14.5 (29 / 2) + branch 18.5 (37 / 2) + ADAA-1 clipper 47
 * (ts_aa1) + tone x Q 18 (36 / 2) = 98 = 156.8 us at 1.6; per block on top, the worst: building 32 {g, F} nodes
 * (ts_build_gf, 31 a node: ~6.2 us; a swap's 16 crossfaded samples, ts_aa1x +43 each, ~4.3 us, never in the same
 * block), ~170 of set-up (~1.1 us): 164.1 -> 165 us. The first table whole (505 x 31, ~98 us) is built at boot (drv_boot),
 * never in an audio block.
 * TSH8_AA 0 (the plain kernel): HP 8.5 + branch 15 + table 23 + tone 17.5 = 64 = 102.4 us, +3.4 us a block: 106. */
#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#if TSH8_AA
#define DRV_US_TSH8 165u
#else
#define DRV_US_TSH8 106u
#endif
_Static_assert((DRV_BLK & 1u) == 0, "TSH8 runs two samples a pass: the block must be even");
#define TSH8_FINE_SH 7
#define TSH8_FINE_END (2 << 14)
#define TSH8_COARSE_SH 12
#define TSH8_NFINE (TSH8_FINE_END >> TSH8_FINE_SH)
#define TSH8_UEND (64 << 14)
#ifndef TSH8_SLICE
#define TSH8_SLICE 32u                                   /* table nodes built a block */
#endif
#ifndef TSH8_GSTEP
#define TSH8_GSTEP 2                                     /* AMOUNT / TONE steps the rows move at most a block */
#endif
#define TSH8_OSH 12                                      /* the tone stage's output bits below the sample's */
#define TSH8_XF 16u                                      /* samples a table swap is crossfaded over */
#ifndef TSH8_TSTEP
#define TSH8_TSTEP 16                                    /* AMOUNT steps a table moves at most per swap */
#endif

static DRV_BSS int32_t ts_hs[2], ts_bs[4], ts_ts[4];               /* the HP, branch and tone states */
#if TSH8_AA
static DRV_BSS int32_t ts_in[5], ts_is[4], ts_as[2];               /* the input biquad, its state; the clipper's {x1, F1} */
static DRV_DATA int32_t *volatile ts_inp = ts_in;
#endif
static DRV_BSS int32_t ts_br[5], ts_tone[5];                       /* the branch and tone rows that run (tone: b x the level) */
static DRV_DATA int32_t *volatile ts_brp = ts_br, *volatile ts_tonep = ts_tone;   /* (read once a block: the compiler cannot
                                                          * see through them, so the loops read the coefficients as
                                                          * the counted design did) */
static DRV_DATA int32_t ts_ra = -1, ts_rt;                          /* the AMOUNT and TONE steps that run (-1: take the knobs) */
#if TSH8_AA
#define TSH8_W 2                                         /* words a node: {g, F} (the anti-aliased clipper) */
#else
#define TSH8_W 1
#endif
static DRV_BSS int32_t ts_tab[2][TSH8_W * TSH8_NODES];             /* the diode table that runs, and the next one */
static DRV_DATA int32_t ts_trow = -1, ts_brow, ts_bpos = -1;        /* its step (-1: none yet), the one being built, where */
static DRV_BSS uint8_t ts_cur;
static DRV_BSS int32_t ts_vi[DRV_BLK];

#define TSM(a, b) ((int64_t)(a) * (b))

#ifdef DINGHY_HOST
static DRV_BSS uint32_t ts_nb;                         /* (host: table nodes built since drv_tsh8_run started: tests) */
#define TS_NB(i, e) (ts_nb += (e) - (i))
#else
#define TS_NB(i, e) ((void)0)
#endif
/* nodes [i, e) of AMOUNT step r's table into t: the keys below and above it, linearly (tools/drive_gen/tsh8.py) */
static DRV_BSS int32_t ts_v0, ts_v1;                              /* the two keys at node i (a build runs from node 0 up) */
static void ts_build(int32_t *t, int32_t r, uint32_t i, uint32_t e)
{
    uint32_t kp = TSH8_KPOS[r];
    const uint8_t *d0 = TSH8_KD[kp >> 9], *d1 = TSH8_KD[(kp >> 9) + (kp & 511u ? 1u : 0u)];
    TS_NB(i, e);
    int32_t w = (int32_t)(kp & 511u), v0 = i ? ts_v0 : 0, v1 = i ? ts_v1 : 0;   /* (every key starts at 0) */
    for (; i < e; i++) {
        t[i] = v0 + (((v1 - v0) * w) >> 8);
        v0 += d0[i]; v1 += d1[i];
    }
    ts_v0 = v0; ts_v1 = v1;
}

/* the anti-aliased variant's table: {g, F} per node, F = the exact antiderivative of the interpolated g
 * >> TSH8_AA_SF, built the same way (nodes [i, e) of AMOUNT step r, in order from node 0; tools/drive_gen/tsh8.py
 * built_gf). F's running sum needs 35 bits: int64. */
#define TSH8_AA_SF 5
static DRV_BSS int32_t ts_gp;                                      /* g at node i - 1 */
static DRV_BSS int64_t ts_s;                                       /* the running sum at node i */
static void ts_build_gf(int32_t *t, int32_t r, uint32_t i, uint32_t e)
{
    uint32_t kp = TSH8_KPOS[r];
    const uint8_t *d0 = TSH8_KD[kp >> 9], *d1 = TSH8_KD[(kp >> 9) + (kp & 511u ? 1u : 0u)];
    TS_NB(i, e);
    int32_t w = (int32_t)(kp & 511u), v0 = i ? ts_v0 : 0, v1 = i ? ts_v1 : 0, gp = ts_gp;
    int64_t s = i ? ts_s : 0;
    for (; i < e; i++) {
        int32_t g = v0 + (((v1 - v0) * w) >> 8);
        if (i) s += ((int64_t)(gp + g) << (i <= (uint32_t)TSH8_NFINE ? TSH8_FINE_SH : TSH8_COARSE_SH)) >> 1;   /* (no 64-bit divide) */
        t[2 * i] = g; t[2 * i + 1] = (int32_t)(s >> TSH8_AA_SF);
        gp = g; v0 += d0[i]; v1 += d1[i];
    }
    ts_v0 = v0; ts_v1 = v1; ts_gp = gp; ts_s = s;
}

#if TSH8_AA
#define TS_BUILD ts_build_gf
#else
#define TS_BUILD ts_build
#endif

/* the first table (whole, ~15,700 instructions with TSH8_AA): built once, at boot, by drive.c drv_boot (the main
 * loop, never an audio block); until then the TYPE passes its input through (ts_ok 0). A host harness without
 * app_main builds it in the reset instead (DINGHY_HOST only). */
static DRV_BSS volatile uint8_t ts_ok;
static void ts_first(int32_t a);

static void drv_tsh8_reset(void)
{
#ifdef DINGHY_HOST
    if (!ts_ok) ts_first(fx_p[FXP_DRIVE]);
#endif
    memset(ts_hs, 0, sizeof ts_hs); memset(ts_bs, 0, sizeof ts_bs); memset(ts_ts, 0, sizeof ts_ts);
#if TSH8_AA
    memset(ts_is, 0, sizeof ts_is); memset(ts_as, 0, sizeof ts_as);
#endif
    ts_ra = -1;                                            /* (the table is kept: it stays right for its step) */
}

/* pass 1: the input HP, 2 samples per iteration (x scaled to volts Q24 on the way in) */
static inline __attribute__((always_inline)) void ts_hp2q(int32_t *st, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t b0 = TSH8_HP[0], a1 = TSH8_HP[1], x1 = st[0], y1 = st[1];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0] * TSH8_IN_K, xb = in[1] * TSH8_IN_K, ya;
        ya = (int32_t)((TSM(b0, xa - x1) + TSM(a1, y1)) >> 28);
        y1 = (int32_t)((TSM(b0, xb - xa) + TSM(a1, ya)) >> 28);
        x1 = xb;
        out[0] = ya; out[1] = y1;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = y1;
}

/* pass 2: the branch biquad (b Q22, a Q28), 2 samples per iteration, the coefficients read in the loop */
static inline __attribute__((always_inline)) void ts_br2q(const int32_t *c, int32_t *st, const int32_t *in,
                                                          int32_t *out, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0], xb = in[1], ya, yb, k;
        int64_t pa, pb;
        k = c[2]; pa = TSM(k, x2); pb = TSM(k, x1);
        k = c[1]; pa += TSM(k, x1); pb += TSM(k, xa);
        k = c[0]; pa += TSM(k, xa); pb += TSM(k, xb);
        k = c[4]; pa += TSM(k, y2); pb += TSM(k, y1);
        k = c[3]; pa += TSM(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += TSM(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        out[0] = ya >> 4; out[1] = yb >> 4;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

static inline __attribute__((always_inline)) int32_t ts_g(const int32_t *g, int32_t x)
{
    int32_t a = x < 0 ? -x : x, k, fr, g0, v;
    a = a > TSH8_UEND - 1 ? TSH8_UEND - 1 : a;
    if (a < TSH8_FINE_END) {
        k = a >> TSH8_FINE_SH; fr = a & ((1 << TSH8_FINE_SH) - 1); g0 = g[k];
        v = g0 + (((g[k + 1] - g0) * fr) >> TSH8_FINE_SH);
    } else {
        k = TSH8_NFINE + ((a - TSH8_FINE_END) >> TSH8_COARSE_SH); fr = (a - TSH8_FINE_END) & ((1 << TSH8_COARSE_SH) - 1);
        g0 = g[k]; v = g0 + (((g[k + 1] - g0) * fr) >> TSH8_COARSE_SH);
    }
    return x < 0 ? -v : v;
}

/* pass 3: the diode table, in place */
static inline __attribute__((always_inline)) void ts_tab1(const int32_t *g, int32_t *u, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) u[i] = ts_g(g, u[i]);
}

/* pass 3 in the block after a table swap, its first TSH8_XF samples: the old table's V crossfaded into the new
 * one's (0.7 ms), so a swap never steps */
static inline __attribute__((always_inline)) void ts_tab1x(const int32_t *g0, const int32_t *g1, int32_t *u)
{
    uint32_t i;
    for (i = 0; i < TSH8_XF; i++) {
        int32_t a = ts_g(g0, u[i]), b = ts_g(g1, u[i]);
        u[i] = a + (((b - a) * (int32_t)(i + 1u)) >> 4);
    }
}

/* pass 4: s = vi + V, the tone biquad (Q28), 2 samples per iteration, the output over V */
static FR_RAM __attribute__((noinline)) void ts_tone2qi(const int32_t *c, int32_t *st, const int32_t *vi,
                                                             int32_t *v, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = vi[0] + (v[0] << 9), xb = vi[1] + (v[1] << 9), ya, yb, k;
        int64_t pa, pb;
        k = c[2]; pa = TSM(k, x2); pb = TSM(k, x1);
        k = c[1]; pa += TSM(k, x1); pb += TSM(k, xa);
        k = c[0]; pa += TSM(k, xa); pb += TSM(k, xb);
        k = c[4]; pa += TSM(k, y2); pb += TSM(k, y1);
        k = c[3]; pa += TSM(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += TSM(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        v[0] = ya >> TSH8_OSH; v[1] = yb >> TSH8_OSH;      /* (its state keeps them: the recursion's rounding */
        vi += 2; v += 2;                                   /* stays 2^-12 of a sample, not ~400 samples' worth) */
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

#if TSH8_AA
/* ---- the anti-aliased kernel (first-order antiderivative anti-aliasing without a divide: the reciprocal of the step
 * from DRV_AA_RCP by count-leading-zeros, one multiply, one shift; tools/drive_gen/aa.py). ADAA-1 averages two samples
 * (cos where linear, ~sinc where it clips, half a sample of delay): the input biquad is the coupling high-pass x P
 * (sinc / cos), the clean Vi gets the same two-sample average, the tone rows x Q (1 / sinc). The table runs to 128 V
 * (|u| <= 67 V at full scale); |u| is masked to 21 bits, so a far hotter input than the engines make (> 1.9 x full
 * scale) folds back into the table: never a read outside it. */
#define TSH8_AEND ((128 << 14) - 1)
#define TSH8_AT (1 << TSH8_AA_SF)                         /* |du| below this (2 mV): g at the midpoint, no quotient */

/* pass 1: the input biquad (HP x P, Q28), x TSH8_IN_K on the way in, 2 samples per iteration */
static FR_RAM __attribute__((noinline)) void ts_in2q(const int32_t *c, int32_t *st, const int32_t *in,
                                                          int32_t *out, uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0] * TSH8_IN_K, xb = in[1] * TSH8_IN_K, ya, yb, k;
        int64_t pa, pb;
        k = c[2]; pa = TSM(k, x2); pb = TSM(k, x1);
        k = c[1]; pa += TSM(k, x1); pb += TSM(k, xa);
        k = c[0]; pa += TSM(k, xa); pb += TSM(k, xb);
        k = c[4]; pa += TSM(k, y2); pb += TSM(k, y1);
        k = c[3]; pa += TSM(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += TSM(k, ya);
        yb = (int32_t)(pb >> 28);
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        out[0] = ya; out[1] = yb;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

/* pass 2: the branch biquad, writing the two-sample average of Vi back over vi (x1 is the previous Vi) */
static FR_RAM __attribute__((noinline)) void ts_br2qa(const int32_t *c, int32_t *st, int32_t *in, int32_t *out,
                                                           uint32_t n)
{
    int32_t x1 = st[0], x2 = st[1], y1 = st[2], y2 = st[3];
    uint32_t i;
    for (i = n >> 1; i; i--) {
        int32_t xa = in[0], xb = in[1], ya, yb, k;
        int64_t pa, pb;
        k = c[2]; pa = TSM(k, x2); pb = TSM(k, x1);
        k = c[1]; pa += TSM(k, x1); pb += TSM(k, xa);
        k = c[0]; pa += TSM(k, xa); pb += TSM(k, xb);
        k = c[4]; pa += TSM(k, y2); pb += TSM(k, y1);
        k = c[3]; pa += TSM(k, y1);
        ya = (int32_t)(pa >> 28);
        pb += TSM(k, ya);
        yb = (int32_t)(pb >> 28);
        in[0] = (xa + x1) >> 1; in[1] = (xb + xa) >> 1;
        x2 = xa; x1 = xb; y2 = ya; y1 = yb;
        out[0] = ya >> 4; out[1] = yb >> 4;
        in += 2; out += 2;
    }
    st[0] = x1; st[1] = x2; st[2] = y1; st[3] = y2;
}

/* one sample of the clipper: x (u Q14) -> V (Q15); X1, F1 the previous input and its F (updated); gf / gc the {g, F}
 * table's fine and coarse bases, rcp the reciprocal table's base */
#define TS_SEGA(BASE, SH)                                                                                   \
    do {                                                                                                    \
        e = (BASE) + 2 * (a >> (SH)); fr = a & ((1 << (SH)) - 1);                                           \
        g0 = e[0]; dg = e[2] - g0; p = dg * fr;                                                             \
        F = e[1] + ((g0 * fr + (int32_t)(((uint32_t)p * (uint32_t)fr) >> ((SH) + 1))) >> TSH8_AA_SF);      \
        hd = (SH) + 1;                                                                                      \
    } while (0)
#define TS_AA_BODY(gf, gc, xin, vout, X1, F1)                                                               \
    do {                                                                                                    \
        int32_t x = (xin), a, fr, g0, dg, p, F, d, hd;                                                      \
        uint32_t ad;                                                                                        \
        const int32_t *e;                                                                                   \
        a = x < 0 ? -x : x;                                                                                 \
        a &= TSH8_AEND;                         /* (|u| < 128 V always here: a guard, no clamp; one 'and') */ \
        if (a < TSH8_FINE_END) TS_SEGA(gf, TSH8_FINE_SH); else TS_SEGA(gc, TSH8_COARSE_SH);                 \
        d = x - (X1);                                                                                       \
        ad = (uint32_t)(d < 0 ? -d : d);                                                                    \
        if (ad < TSH8_AT) {                                                                                 \
            int32_t v_ = g0 + (p >> (hd - 1));                                                              \
            v_ = x < 0 ? -v_ : v_;                                                                          \
            (vout) = v_ - ((dg * d) >> hd);                                                                 \
        } else {                                                                                            \
            int nz = __builtin_clz(ad);                                                                     \
            int32_t r = rcp[(ad << nz) >> (32 - DRV_AA_RB)];                                                \
            int32_t df_ = F - (F1);                                                                         \
            if (d < 0) df_ = -df_;                                                                          \
            (vout) = (int32_t)(TSM(df_, r) >> (47 - TSH8_AA_SF - nz));                                      \
        }                                                                                                   \
        (X1) = x; (F1) = F;                                                                                 \
    } while (0)
#define TS_GC(gf) ((gf) + 2 * (TSH8_NFINE - (TSH8_FINE_END >> TSH8_COARSE_SH)))
#define TS_RCP ((const uint16_t *)((uintptr_t)DRV_AA_RCP - (2u << (DRV_AA_RB - 1))))   /* (indexed from 2^(RB-1)) */

/* pass 3: the clipper, in place, one sample per iteration */
static FR_RAM __attribute__((noinline)) void ts_aa1(const int32_t *gf, int32_t *st, int32_t *u, uint32_t n)
{
    const int32_t *gc = TS_GC(gf);
    const uint16_t *rcp = TS_RCP;
    int32_t x1 = st[0], F1 = st[1];
    uint32_t i;
    for (i = n; i; i--) {
        int32_t v;
        TS_AA_BODY(gf, gc, u[0], v, x1, F1);
        *u++ = v;
    }
    st[0] = x1; st[1] = F1;
}

/* F of the table g at x (the clipper's segment, no output): after a table swap the previous F must be the new
 * table's, or the first quotient jumps */
static int32_t ts_F(const int32_t *gf, int32_t x)
{
    const int32_t *gc = TS_GC(gf), *e;
    int32_t a = x < 0 ? -x : x, fr, g0, dg, p, F, hd;
    a &= TSH8_AEND;
    if (a < TSH8_FINE_END) TS_SEGA(gf, TSH8_FINE_SH); else TS_SEGA(gc, TSH8_COARSE_SH);
    (void)hd;
    return F;
}

/* pass 3 after a swap, its first TSH8_XF samples: both tables' outputs (each its own F chain), crossfaded */
static __attribute__((noinline)) void ts_aa1x(const int32_t *g0f, const int32_t *gf, int32_t *st, int32_t *u)
{
    const int32_t *gc0 = TS_GC(g0f), *gc = TS_GC(gf);
    const uint16_t *rcp = TS_RCP;
    int32_t x1 = st[0], F0 = st[1], F1 = ts_F(gf, st[0]), xo;
    uint32_t i;
    for (i = 0; i < TSH8_XF; i++) {
        int32_t va, vb;
        xo = x1;
        TS_AA_BODY(g0f, gc0, u[i], va, xo, F0);
        TS_AA_BODY(gf, gc, u[i], vb, x1, F1);
        u[i] = va + (int32_t)(((int64_t)(vb - va) * (int32_t)(i + 1u)) >> 4);
    }
    st[0] = x1; st[1] = F1;
}

/* the four passes, in to out (out may be in); g0f: the table before a swap (crossfaded into gf), or 0. In RAM with
 * the three steady passes (0.9.7); the swap's crossfade (ts_aa1x, a block now and then) stays in flash */
static FR_RAM __attribute__((noinline)) void ts_kernel_aa(const int32_t *br, const int32_t *tone, const int32_t *g0f,
                                                   const int32_t *gf, const int32_t *in, int32_t *out, uint32_t n)
{
    ts_in2q(ts_inp, ts_is, in, ts_vi, n);
    ts_br2qa(br, ts_bs, ts_vi, out, n);
    if (g0f) { FR_FAR(ts_aa1x)(g0f, gf, ts_as, out); ts_aa1(gf, ts_as, out + TSH8_XF, n - TSH8_XF); }
    else ts_aa1(gf, ts_as, out, n);
    FR_FAR(ts_tone2qi)(tone, ts_ts, ts_vi, out, n);
}
#endif

/* the four passes on n (even) samples, in to out (out may be in): the kernel (tests/c/drv_kern_test.c calls it with
 * its own rows); g0: the table before a swap, crossfaded into g over the first TSH8_XF samples (n >= TSH8_XF), or 0 */
static __attribute__((noinline)) void ts_kernel(const int32_t *br, const int32_t *tone, const int32_t *g0,
                                                const int32_t *g, const int32_t *in, int32_t *out, uint32_t n)
{                                                          /* (noinline: the loops as the counted design) */
    ts_hp2q(ts_hs, in, ts_vi, n);
    ts_br2q(br, ts_bs, ts_vi, out, n);
    if (g0) { ts_tab1x(g0, g, out); ts_tab1(g, out + TSH8_XF, n - TSH8_XF); }
    else ts_tab1(g, out, n);
    FR_FAR(ts_tone2qi)(tone, ts_ts, ts_vi, out, n);
}

/* the tone row of step t x the level of steps (a, t): b' = b L >> 24, L = LVA[a] LVT[t] >> 15 (Q24; exactly b at
 * L = 2^24) */
/* a table word stored as two int16 (high, low: no word in the chip-ROM window in the image) */
static inline int32_t ts_w(const int16_t *p) { return (int32_t)(((uint32_t)(uint16_t)p[0] << 16) | (uint16_t)p[1]); }
/* r0 + ((r1 - r0) w >> 8), 64-bit (tools/drive_gen/tsh8.py lerp) */
static int32_t ts_lerp(int32_t r0, int32_t r1, int32_t w) { return r0 + (int32_t)((((int64_t)r1 - r0) * w) >> 8); }
/* row word i at position pos (row << 8 | w) of a split16 table: the row, or between it and the next */
static int32_t ts_row(const int16_t (*R)[10], uint32_t pos, int i)
{
    const int16_t *p = R[pos >> 8];
    return pos & 255u ? ts_lerp(ts_w(p + 2 * i), ts_w(p + 10 + 2 * i), (int32_t)(pos & 255u)) : ts_w(p + 2 * i);
}
static int32_t ts_lv(const int32_t *L, uint32_t pos)
{
    return pos & 255u ? ts_lerp(L[pos >> 8], L[(pos >> 8) + 1u], (int32_t)(pos & 255u)) : L[pos >> 8];
}
static void ts_set(int32_t a, int32_t t)
{
    uint32_t pa = TSH8_PA[a], pt = TSH8_PT[t];
    int32_t L = (int32_t)(((int64_t)ts_lv(TSH8_LVA, pa) * ts_lv(TSH8_LVT, pt)) >> 15), i;
    for (i = 0; i < 5; i++) ts_br[i] = ts_row(TSH8_BR, pa, i);
    for (i = 0; i < 3; i++) ts_tone[i] = (int32_t)(((int64_t)ts_row(TSH8_TONE, pt, i) * L) >> (24 - TSH8_OSH));
    ts_tone[3] = ts_row(TSH8_TONE, pt, 3); ts_tone[4] = ts_row(TSH8_TONE, pt, 4);
}

static int32_t ts_step(int32_t v, int32_t tgt)
{
    return v < tgt - TSH8_GSTEP ? v + TSH8_GSTEP : v > tgt + TSH8_GSTEP ? v - TSH8_GSTEP : tgt;
}

static void ts_first(int32_t a)
{
    a = a < 1 ? 1 : a > 127 ? 127 : a;
    TS_BUILD(ts_tab[0], a, 0u, TSH8_NODES); ts_cur = 0; ts_trow = a; ts_bpos = -1;
#if TSH8_AA
    { int i; for (i = 0; i < 5; i++) ts_in[i] = ts_w(&TSH8_IN[0][2 * i]); }   /* (the input biquad, fixed) */
#endif
    ts_ok = 1;
}

static void drv_tsh8_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)
{
    int32_t a = c->amount, t = c->tone;
    const int32_t *g0 = 0;
#ifdef DINGHY_HOST
    ts_nb = 0;
#endif
    if (!ts_ok) { if (out != in) memcpy(out, in, n * sizeof *out); return; }       /* (before drv_boot: dry) */
    if (ts_ra < 0) { ts_ra = a; ts_rt = t; ts_set(a, t); }                         /* (after a reset: the knobs) */
    else if (ts_ra != a || ts_rt != t) { ts_ra = ts_step(ts_ra, a); ts_rt = ts_step(ts_rt, t); ts_set(ts_ra, ts_rt); }
    if (ts_bpos < 0) {                                     /* (a block builds, or swaps, or neither: never both) */
        if (ts_trow != ts_ra) {                                                     /* the next table: from here */
            int32_t d = ts_ra - ts_trow;
            ts_brow = ts_trow + (d > TSH8_TSTEP ? TSH8_TSTEP : d < -TSH8_TSTEP ? -TSH8_TSTEP : d);
            ts_bpos = 0;
        }
    } else if (ts_bpos < (int32_t)TSH8_NODES) {
        uint32_t e = (uint32_t)ts_bpos + TSH8_SLICE;
        if (e > TSH8_NODES) e = TSH8_NODES;
        TS_BUILD(ts_tab[ts_cur ^ 1u], ts_brow, (uint32_t)ts_bpos, e);
        ts_bpos = (int32_t)e;
    } else {                                                                        /* built: it runs from here, */
        ts_cur ^= 1u; ts_trow = ts_brow; ts_bpos = -1;                              /* crossfaded in */
        if (n >= TSH8_XF) g0 = ts_tab[ts_cur ^ 1u];
    }
#if TSH8_AA
    FR_FAR(ts_kernel_aa)(ts_brp, ts_tonep, g0, ts_tab[ts_cur], in, out, n & ~1u);
#else
    ts_kernel(ts_brp, ts_tonep, g0, ts_tab[ts_cur], in, out, n & ~1u);
#endif
}
