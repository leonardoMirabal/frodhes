/* SPDX-License-Identifier: GPL-3.0-only */
/* REVERB TYPE HALL at the full rate (FR_REV_HQ; rev_hq.c): a feedback delay network of 8 lines (J.-M. Jot and A. Chaigne,
 * "Digital delay networks for designing artificial reverberators", AES 90th Convention, 1991: a lossless feedback
 * matrix, each line's gain from its length and the T60, a low-pass in each line for the T60 over frequency), the
 * matrix an 8-point Hadamard / sqrt 8 (orthogonal: every line feeds every line), behind 4 series allpass diffusers
 * (M. R. Schroeder, "Natural sounding artificial reverberation", JAES 10(3), 1962). Written here from the papers
 * (integer, 44.1 kHz).
 *
 *   in (CTL samples, rq_in's level) -> 4 allpasses (210 / 158 / 561 / 410, g 0.75 / 0.75 / 0.625 / 0.625) -> + into
 *   the 8 lines (+-x, a sign pattern) -> each line: its read (4 of 8 modulated: a slow sine each, linear
 *   interpolation) -> TONE's one-pole -> its gain (T60) -> the Hadamard mix -> back into the lines (int16, clamped).
 *   Mid = the mix's row 0 (the sum of the 8), side = row 1 (the alternate signs): free taps.
 *
 * The lengths: 8 between 32 and 91 ms, coprime (0.49 s in all: ~0.49 modes per Hz, against the half-rate HALL's 0.22
 * (8 lines of 21 .. 34 ms): its long DECAYs rang metallic). (A 16-line version of this file, Hadamard 16, cost ~2x:
 * git 30f22c2c.) SIZE (rev_hq.c: HALL's size, 40 .. 110 from MIX or SIZE, DECAY's offset) moves the T60 only, as the
 * half-rate HALL: 0.5 x 2^(size / 30) s (no length moves: no pitch bend while a tail rings). TONE (damp 0 .. 150, 60
 * the default): the lines' one-pole (bright .. dark; at 60 the half-rate HALL's T60 per octave, 500 Hz .. 8 kHz, within
 * 3 %). MOD (x 16, 64 = 1024): +-16 samples at MOD 64 (the half-rate HALL's +-8 at 22.05 kHz: the same time),
 * 0.31 .. 0.59 Hz.
 *
 * Memory: one ring of RQH_R rows, a row = the 8 lines' samples written at one time (the half-rate HALL's layout): the
 * mix writes a row with plain stores; line k reads its column RQH_LEN[k] rows back (+ its modulation). RQH_R is a
 * multiple of the block, so a block's 32 rows never wrap; a read across the end is copied (rarely).
 * Exact silence: while the tank is quiet (every line end under RQH_LOUD) the products truncate toward zero (sh_tz), the
 * one-pole decays toward its input, the mix's / 4 truncates (always): without input every line can only shrink; loud,
 * the line ends round (no bias: the T60 holds at any level). rev_hqh_active() is 0 once the input and every line end have been 0
 * for longer than the longest line. Start: O(1) (the ring is not cleared): until a line has been written through,
 * its reads older than the start read 0 (through the copy of the ring's end, the first 93 ms). */

#ifndef FR_RAM
#define FR_RAM
#define FR_FAR(fn) fn
#endif
/* the cost, us per 256-frame block (instruction counts of the compiled loops, rev_hq.c's REV_US_* comment) */
#define REV_US_HQH 445u

#define RQH_NL 8u
#define RQH_SW 66u                                      /* a modulated line's room: 2 x 31.75 (MOD 127) + 2 */
#define RQH_MODM 0xAAu                                  /* the modulated lines (the odd ones) */
#define RQH_AN 4u
#define RQH_ASUM (210u + 158u + 561u + 410u)
static const uint16_t RQH_LEN[RQH_NL] = {1427, 1789, 2161, 2539, 2903, 3271, 3637, 4019};
#define RQH_LMAX 4019u
#define RQH_R 4128u                                     /* the ring's rows: >= RQH_LMAX + RQH_SW + 1 + a block, x 32 */
_Static_assert(RQH_R >= RQH_LMAX + RQH_SW + 1u + 32u && RQH_R % 32u == 0u, "rev_hq_hall.c: the ring");
#define RQH_KP 340                                      /* TONE's pole per damp step (Q15) */
#define RQH_SGN 0x96u                                   /* the input's sign per line (bit k: - for line k) */
#define RQH_OUT 13800                                   /* the taps (Q13 of the row / 4: the half-rate HALL's Bark loudness) */
#define RQH_LOUD 1024                                   /* a line end this quiet (4x the line's scale, all 8, the last block)
                                                         * truncates: it then dies to exact zero; louder, they round */
static const uint16_t RQH_AL[RQH_AN] = {210, 158, 561, 410};
static const int16_t RQH_AG[RQH_AN] = {12288, 12288, 10240, 10240};   /* Q14: 0.75, 0.75, 0.625, 0.625 */
#define RQH_HZ(f) ((uint32_t)((f) * CTL / 44100.0 * 4294967296.0))
static const uint32_t RQH_INC[4] = {RQH_HZ(0.31), RQH_HZ(0.47), RQH_HZ(0.37), RQH_HZ(0.59)};
#define RQH_WARM (RQH_LMAX + RQH_SW + 1u)              /* samples since the start: no read older than it */

typedef struct {
    uint32_t amax;                  /* the last block's line ends + RQH_LOUD, ORed (< 2 RQH_LOUD: all quiet) */
    uint32_t w, e, quiet;           /* the write row, samples since the start (to RQH_WARM), quiet samples */
    uint32_t ph[4];                 /* the LFOs (the modulated lines) */
    int32_t lp[RQH_NL];             /* the one-poles */
    int32_t g[RQH_NL];              /* the gains, Q15, x sqrt 2 (the line ends 4 / sqrt 8 of the orthonormal's) */
    int32_t gsz, gdmp, p;           /* the size and damp the gains / pole were made for; the pole (Q15) */
    uint16_t ad[RQH_AN];            /* the allpasses' indices */
    uint8_t afull;                  /* an allpass written through (bit k) */
    int32_t x[RQ_NC];
    int16_t t[(RQ_NC + 1u) * RQH_NL];   /* a read across the ring's end, contiguous */
    int32_t y[RQ_NC][RQH_NL];       /* the line ends of this block */
    int16_t ap[RQH_ASUM];
    int16_t ring[RQH_R * RQH_NL];
} rev_hqh_t;
_Static_assert(RQ_NC == 32u, "rev_hq_hall.c: one control block = 32 samples");

static void rev_hqh_start(rev_hqh_t *s)
{
    uint32_t k;
    memset(s, 0, __builtin_offsetof(rev_hqh_t, y));
    for (k = 0; k < 4u; k++)
        s->ph[k] = k * 0x2C000000u;
    s->gsz = s->gdmp = -1;
}
static int rev_hqh_active(const rev_hqh_t *s) { return s->quiet < RQH_WARM + RQ_NC; }

/* the gains (Q15 x sqrt 2) for size sz: T60 0.502 x 2^(sz / 30) s (the half-rate HALL's law), 2^(-9.966 len / (fs
 * T60)) */
static void rqh_gains(rev_hqh_t *s, int32_t sz)
{
    uint32_t q = exp2_neg((uint32_t)sz * 65536u / 30u), k, g;
    for (k = 0; k < RQH_NL; k++) {
        g = exp2_neg((RQH_LEN[k] * 59u * q) >> 16);
        g = g > 32700u ? 32700u : g;
        s->g[k] = (int32_t)((g * 46341u) >> 15);
    }
    s->gsz = sz;
}
/* the 4 diffusers over the block (v = x + g b, y = b - g v, Q14, truncated: no input, no growth), in runs that do
 * not wrap */
static FR_RAM __attribute__((noinline)) void rqh_dif(rev_hqh_t *s, int32_t *x)
{
    int16_t *a = s->ap;
    uint32_t k, i;
    for (k = 0; k < RQH_AN; k++) {
        uint32_t d = s->ad[k], L = RQH_AL[k], m, j;
        int32_t g = RQH_AG[k];
        for (i = 0; i < RQ_NC; i += m) {
            int16_t *q = a + d;
            int32_t *u = x + i;
            m = L - d < RQ_NC - i ? L - d : RQ_NC - i;
            if ((s->afull >> k) & 1u)
                for (j = 0; j < m; j++) {
                    int32_t b = q[j], v = hl_c16(u[j] + sh_tz(b * g, 14));
                    q[j] = (int16_t)v;
                    u[j] = b - sh_tz(v * g, 14);
                }
            else                                        /* (not written through since the start: it reads 0) */
                for (j = 0; j < m; j++) {
                    int32_t v = hl_c16(u[j]);
                    q[j] = (int16_t)v;
                    u[j] = -sh_tz(v * g, 14);
                }
            d += m;
            if (d == L) d = 0, s->afull |= (uint8_t)(1u << k);
        }
        s->ad[k] = (uint16_t)d;
        a += L;
    }
}
/* one line's end over the block: q[i * 8] the read of sample i (q[i * 8 - 8] the next older: the interpolation's),
 * its one-pole (pole p, toward the read: it ends at 0) and gain g; f: the fraction (Q8) toward the older sample
 * (modulated lines), into y[i][0] (stride 8). lo (the tank quiet): truncated toward zero; loud: rounded. Returns the
 * line ends + RQH_LOUD ORed; *pao |= the line ends ORed (activity) */
#define RQH_END(RD, SH)                                                                                         \
    for (i = 0; i < RQ_NC; i++, q += RQH_NL, y += RQH_NL) {                                                     \
        int32_t u = RD, v;                                                                                      \
        l = u + SH((l - u) * p, 15);                                                                            \
        v = SH(l * g, 15);                                                                                      \
        *y = v;                                                                                                 \
        ao |= v;                                                                                                \
        am |= (uint32_t)(v + RQH_LOUD);                                                                         \
    }
#define RQH_RND(m, sh) (((m) + (1 << ((sh) - 1))) >> (sh))
#define RQH_TZ(m, sh) sh_tz(m, sh)
static FR_RAM __attribute__((noinline)) uint32_t rqh_end(const int16_t *q, int32_t f, int32_t *y, int32_t *lp, int32_t p,
                                                         int32_t g, int lo, int32_t *pao)
{
    int32_t l = *lp, ao = 0;
    uint32_t i, am = 0;
    if (f) {
        if (lo) RQH_END(q[0] + (((q[-(int32_t)RQH_NL] - q[0]) * f) >> 8), RQH_TZ)
        else RQH_END(q[0] + (((q[-(int32_t)RQH_NL] - q[0]) * f + 128) >> 8), RQH_RND)
    } else {
        if (lo) RQH_END(q[0], RQH_TZ)
        else RQH_END(q[0], RQH_RND)
    }
    *lp = l;
    *pao |= ao;
    return am;
}
/* the mix: the 8-point Hadamard of the line ends, its row / 4 (truncated toward zero: no bias, and the flash it
 * saves against a rounded twin; the T60s the same) +- the input, clamped,
 * into the ring's rows o (8 plain stores a row); row 0 the mid, row 1 the side */
#define RQH_BF(a, b) { int32_t t_ = a; a = t_ + b; b = t_ - b; }
#define RQH_W8(SH, k, h) o[k] = (int16_t)hl_c16(SH(h, 2) + (((RQH_SGN >> k) & 1u) ? -x : x));
#define RQH_MIX8(SH)                                                                                            \
    for (i = 0; i < RQ_NC; i++, y += RQH_NL, o += RQH_NL) {                                                     \
        int32_t h0 = y[0], h1 = y[1], h2 = y[2], h3 = y[3], h4 = y[4], h5 = y[5], h6 = y[6], h7 = y[7], x = xs[i];\
        RQH_BF(h0, h1) RQH_BF(h2, h3) RQH_BF(h4, h5) RQH_BF(h6, h7)                                             \
        RQH_BF(h0, h2) RQH_BF(h1, h3) RQH_BF(h4, h6) RQH_BF(h5, h7)                                             \
        RQH_BF(h0, h4) RQH_BF(h1, h5) RQH_BF(h2, h6) RQH_BF(h3, h7)                                             \
        mid[i] = ((h0 >> 2) * RQH_OUT) >> 13;           /* (|h| <= 8 x 46341: the product < 2^31) */            \
        side[i] = ((h1 >> 2) * RQH_OUT) >> 13;                                                                  \
        RQH_W8(SH, 0, h0) RQH_W8(SH, 1, h1) RQH_W8(SH, 2, h2) RQH_W8(SH, 3, h3)                                 \
        RQH_W8(SH, 4, h4) RQH_W8(SH, 5, h5) RQH_W8(SH, 6, h6) RQH_W8(SH, 7, h7)                                 \
    }
static FR_RAM __attribute__((noinline)) void rqh_mix8(int16_t *o, const int32_t *y, const int32_t *xs, int32_t *mid,
                                                      int32_t *side)
{
    uint32_t i;
    RQH_MIX8(RQH_TZ)
}

static __attribute__((noinline)) void rev_hqh_run(rev_hqh_t *s, const int32_t *in, int32_t *mid, int32_t *side,
                                                  const rev_ctl_t *c)
{
    uint32_t k, i, am = 0;
    int32_t act = 0, lo, sw = c->mod << 2;              /* the swing, Q8 samples (MOD 64: 4096 = 16) */
    if (c->size != s->gsz) rqh_gains(s, c->size);
    if (c->damp != s->gdmp) { s->p = clampi(c->damp * RQH_KP, 0, 31500); s->gdmp = c->damp; }
    for (i = 0; i < RQ_NC; i++) { s->x[i] = in[i]; act |= in[i]; }
    FR_FAR(rqh_dif)(s, s->x);
    lo = s->amax < 2u * RQH_LOUD;
    for (k = 0; k < RQH_NL; k++) {
        uint32_t D = RQH_LEN[k], f = 0, r;
        const int16_t *q;
        if ((RQH_MODM >> k) & 1u) {
            uint32_t j = k >> 1, d8;
            s->ph[j] += RQH_INC[j];
            d8 = (D << 8) + (uint32_t)(sw + ((osc_sine(s->ph[j]) * sw) >> 15));
            D = d8 >> 8, f = d8 & 255u;
        }
        r = s->w + RQH_R - D - 1u;                     /* (the row of sample 0's older sample) */
        r = r >= RQH_R ? r - RQH_R : r;
        if (r + RQ_NC + 1u <= RQH_R && s->e >= RQH_WARM) q = s->ring + (r + 1u) * RQH_NL + k;
        else {                                          /* (the ring's end inside, or the warm-up: a contiguous */
            uint32_t j;                                 /*  copy, the rows older than the start read as 0) */
            for (i = 0, j = r; i < RQ_NC + 1u; i++, j = j + 1u == RQH_R ? 0u : j + 1u)
                s->t[i * RQH_NL + k] = s->e + i >= D + 1u ? s->ring[j * RQH_NL + k] : 0;
            q = s->t + RQH_NL + k;
        }
        am |= FR_FAR(rqh_end)(q, (int32_t)f, &s->y[0][k], &s->lp[k], s->p, s->g[k], lo, &act);
    }
    s->amax = am;                                       /* (< 2 RQH_LOUD: the tank is quiet) */
    FR_FAR(rqh_mix8)(s->ring + s->w * RQH_NL, &s->y[0][0], s->x, mid, side);
    s->w = s->w + RQ_NC >= RQH_R ? 0u : s->w + RQ_NC;
    if (s->e < RQH_WARM) s->e += RQ_NC;
    s->quiet = act ? 0u : s->quiet < RQH_WARM + RQ_NC ? s->quiet + RQ_NC : s->quiet;
}
