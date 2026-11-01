/* SPDX-License-Identifier: GPL-3.0-only */
/* REVERB TYPE HALL at the full rate (FR_REV_HQ; rev_hq.c): a feedback delay network of 16 lines (J.-M. Jot and A. Chaigne,
 * "Digital delay networks for designing artificial reverberators", AES 90th Convention, 1991: a lossless feedback
 * matrix, each line's gain from its length and the T60, a low-pass in each line for the T60 over frequency), the
 * matrix a 16-point Hadamard / 4 (orthogonal: every line feeds every line, so the echo density grows fast), behind 4
 * series allpass diffusers (M. R. Schroeder, "Natural sounding artificial reverberation", JAES 10(3), 1962).
 * Written here from the papers (integer, 44.1 kHz).
 *
 *   in (CTL samples, rq_in's level) -> 4 allpasses (210 / 158 / 561 / 410, g 0.75 / 0.75 / 0.625 / 0.625) -> + into
 *   the 16 lines (+-x / 4 each, a sign pattern) -> each line: its read (8 of 16 modulated: a slow sine each, linear
 *   interpolation) -> TONE's one-pole -> its gain (T60) -> the Hadamard mix -> back into the lines (int16, clamped).
 *   Mid = the mix's row 0 (the sum of the 16), side = row 1 (the alternate signs): free taps.
 *
 * The lengths: 16 between 23 and 61 ms, coprime (0.66 s in all: ~0.66 modes per Hz, against the half-rate HALL's
 * 0.22: its long DECAYs rang metallic). SIZE (rev_hq.c: HALL's size, 40 .. 110 from MIX or SIZE, DECAY's offset) moves
 * the T60 only, as the half-rate HALL: 0.5 x 2^(size / 30) s (no length moves: no pitch bend while a tail rings).
 * TONE (damp 0 .. 150, 60 the default): the lines' one-pole (bright .. dark). MOD (x 16, 64 = 1024): +-16 samples at
 * MOD 64 (the half-rate HALL's +-8 at 22.05 kHz: the same time), 0.31 .. 0.83 Hz.
 *
 * Exact silence: the products truncate toward zero (sh_tz), the one-pole decays toward its input, the mix's / 4
 * truncates: without input every line can only shrink; rev_hqh_active() is 0 once the input and every write have been
 * 0 for longer than the longest line. Start: O(1) (the ring is not cleared): until a line has been written through,
 * its reads older than the start read 0 (the warm-up path, the first 62 ms). */

#ifndef FR_RAM
#define FR_RAM
#define FR_FAR(fn) fn
#endif
/* the cost, us per 256-frame block (instruction counts of the compiled loops, rev_hq.c's REV_US_* comment) */
#define REV_US_HQH 470u

#define RQH_NL 16u
#define RQH_N 32768u                                    /* the ring (int16): the 16 lines' segments */
#define RQH_M (RQH_N - 1u)
#define RQH_SW 66u                                      /* a modulated line's room: 2 x 31.75 (MOD 127) + 2 */
#define RQH_MODM 0xAAAAu                                /* the modulated lines (the odd ones) */
#define RQH_AN 4u
#define RQH_ASUM (210u + 158u + 561u + 410u)
static const uint16_t RQH_LEN[RQH_NL] = {1013, 1103, 1187, 1277, 1361, 1453, 1543, 1627,
                                         1721, 1823, 1913, 2017, 2129, 2237, 2357, 2683};
static const uint16_t RQH_AL[RQH_AN] = {210, 158, 561, 410};
static const int16_t RQH_AG[RQH_AN] = {12288, 12288, 10240, 10240};   /* Q14: 0.75, 0.75, 0.625, 0.625 */
#define RQH_HZ(f) ((uint32_t)((f) * CTL / 44100.0 * 4294967296.0))
static const uint32_t RQH_INC[8] = {RQH_HZ(0.31), RQH_HZ(0.47), RQH_HZ(0.37), RQH_HZ(0.59),
                                    RQH_HZ(0.43), RQH_HZ(0.71), RQH_HZ(0.53), RQH_HZ(0.83)};
/* the segments' tops: line k reads (w + top_k - d) and writes (w + top_k); top_k - top_(k-1) >= its longest delay +
 * a block, so another line's writes never land where k still reads (rev_plate.c's scheme) */
#define RQH_SEG(k) (RQH_LEN[k] + RQH_SW + RQ_NC)
static const uint16_t RQH_TOP[RQH_NL] = {1111, 2312, 3597, 4972, 6431, 7982, 9623, 11348, 13167, 15088, 17099, 19214, 21441, 23776, 26231, 29012};   /* the sums of RQH_SEG */
_Static_assert(29012 <= RQH_N - RQ_NC && RQH_SW == 66u && RQ_NC == 32u, "rev_hq_hall.c: the segments in the ring");
#define RQH_WARM (2683u + RQH_SW + 1u)                 /* samples since the start: no read older than it */
/* the input into the lines (x, sign k), the output (Q12: the half-rate HALL's level at its defaults) */
#define RQH_SGN 0x6996u                                 /* (bit k: - for line k) */
#define RQH_OUT 2033                                     /* (Q13: the half-rate HALL's Bark loudness at MIX 100) */
#define RQH_LOUD 256                                    /* a tank this quiet (the last block's largest write) truncates: it
                                                         * then dies to exact zero; louder, the products round (no bias) */

typedef struct {
    uint32_t w, e, quiet;           /* the write index, samples since the start (to RQH_WARM), quiet samples */
    uint32_t ph[8];                 /* the LFOs (the modulated lines) */
    int32_t lp[RQH_NL];             /* the one-poles */
    int16_t g[RQH_NL];              /* the gains, Q15 */
    int32_t gsz, gdmp, p;           /* the size and damp the gains / pole were made for; the pole (Q15) */
    int32_t amax;                   /* the last block's writes, |v| ORed (RQH_LOUD) */
    uint16_t ad[RQH_AN];            /* the allpasses' indices */
    uint8_t afull;                  /* an allpass written through (bit k) */
    int32_t x[RQ_NC];
    int32_t y[RQ_NC][RQH_NL];       /* the line ends of this block */
    int16_t ap[RQH_ASUM];
    int16_t ring[RQH_N];
} rev_hqh_t;
_Static_assert(RQ_NC == 32u, "rev_hq_hall.c: one control block = 32 samples");

static void rev_hqh_start(rev_hqh_t *s)
{
    uint32_t k;
    memset(s, 0, __builtin_offsetof(rev_hqh_t, y));
    for (k = 0; k < 8u; k++)
        s->ph[k] = k * 0x2C000000u;
    s->gsz = s->gdmp = -1;
}
static int rev_hqh_active(const rev_hqh_t *s) { return s->quiet < RQH_WARM + RQ_NC; }

/* the gains (Q15) for size sz: T60 0.502 x 2^(sz / 30) s (the half-rate HALL's law), 2^(-9.966 len / (fs T60)) */
static void rqh_gains(rev_hqh_t *s, int32_t sz)
{
    uint32_t q = exp2_neg((uint32_t)sz * 65536u / 30u), k, g;
    for (k = 0; k < RQH_NL; k++) {
        g = exp2_neg((RQH_LEN[k] * 59u * q) >> 16);
        s->g[k] = (int16_t)(g > 32700u ? 32700u : g);
    }
    s->gsz = sz;
}
/* the 4 diffusers over the block (v = x + g b, y = b - g v, Q14, truncated: no input, no growth) */
static FR_RAM __attribute__((noinline)) void rqh_dif(rev_hqh_t *s, int32_t *x)
{
    int16_t *a = s->ap;
    uint32_t k, i;
    for (k = 0; k < RQH_AN; k++) {
        uint32_t d = s->ad[k], L = RQH_AL[k];
        int32_t g = RQH_AG[k], full = (s->afull >> k) & 1u;
        for (i = 0; i < RQ_NC; i++) {
            int32_t b = full ? a[d] : 0, v = hl_c16(x[i] + sh_tz(b * g, 14));
            a[d] = (int16_t)v;
            x[i] = b - sh_tz(v * g, 14);
            if (++d == L) d = 0, s->afull |= (uint8_t)(1u << k), full = 1;
        }
        s->ad[k] = (uint16_t)d;
        a += L;
    }
}
/* one line's end over the block: r the ring index of sample 0's read, its one-pole (pole p, toward the read: it ends
 * at 0) and gain g; f: the fraction (Q8) toward the next older sample (modulated lines), into y[i][0] (stride 16) */
static FR_RAM __attribute__((noinline)) void rqh_end(const int16_t *b, uint32_t r, int32_t f, int32_t *y, int32_t *lp,
                                                     int32_t p, int32_t g, int lo)
{
    int32_t l = *lp;
    uint32_t i;
    if (!lo)                                            /* (loud: rounded) */
        for (i = 0; i < RQ_NC; i++, r++, y += RQH_NL) {
            int32_t u0 = b[r & RQH_M], u1 = b[(r - 1u) & RQH_M], u = u0 + (((u1 - u0) * f + 128) >> 8);
            l = u + (((l - u) * p + 16384) >> 15);
            *y = (l * g + 16384) >> 15;
        }
    else if (f)
        for (i = 0; i < RQ_NC; i++, r++, y += RQH_NL) {
            int32_t u0 = b[r & RQH_M], u1 = b[(r - 1u) & RQH_M], u = u0 + (((u1 - u0) * f) >> 8);
            l = u + sh_tz((l - u) * p, 15);
            *y = sh_tz(l * g, 15);
        }
    else
        for (i = 0; i < RQ_NC; i++, r++, y += RQH_NL) {
            int32_t u = b[r & RQH_M];
            l = u + sh_tz((l - u) * p, 15);
            *y = sh_tz(l * g, 15);
        }
    *lp = l;
}
/* the warm-up's line end: reads older than the start (age a + i < the delay) read 0 */
static __attribute__((noinline)) void rqh_end_w(const int16_t *b, uint32_t r, int32_t f, int32_t *y, int32_t *lp,
                                                int32_t p, int32_t g, uint32_t a, uint32_t D)
{
    int32_t l = *lp;
    uint32_t i;
    for (i = 0; i < RQ_NC; i++, r++, y += RQH_NL) {
        int32_t u0 = a + i >= D ? b[r & RQH_M] : 0, u1 = a + i >= D + 1u ? b[(r - 1u) & RQH_M] : 0;
        int32_t u = u0 + (((u1 - u0) * f) >> 8);
        l = u + sh_tz((l - u) * p, 15);
        *y = sh_tz(l * g, 15);
    }
    *lp = l;
}
/* the mix: 16-point Hadamard / 4 of the line ends, + the input, into the lines; row 0 the mid, row 1 the side.
 * Returns the OR of the writes */
#define RQH_BF(a, b) { int32_t t_ = a; a = t_ + b; b = t_ - b; }
static FR_RAM __attribute__((noinline)) int32_t rqh_mix(rev_hqh_t *s, int32_t *mid, int32_t *side)
{
    int16_t *b = s->ring;
    const int32_t *y = &s->y[0][0];
    uint32_t i, w = s->w;
    int32_t act = 0, am = 0, lo = s->amax < RQH_LOUD;
    for (i = 0; i < RQ_NC; i++, y += RQH_NL, w++) {
        int32_t h0 = y[0], h1 = y[1], h2 = y[2], h3 = y[3], h4 = y[4], h5 = y[5], h6 = y[6], h7 = y[7];
        int32_t h8 = y[8], h9 = y[9], h10 = y[10], h11 = y[11], h12 = y[12], h13 = y[13], h14 = y[14], h15 = y[15];
        int32_t x = s->x[i];
        RQH_BF(h0, h1) RQH_BF(h2, h3) RQH_BF(h4, h5) RQH_BF(h6, h7)
        RQH_BF(h8, h9) RQH_BF(h10, h11) RQH_BF(h12, h13) RQH_BF(h14, h15)
        RQH_BF(h0, h2) RQH_BF(h1, h3) RQH_BF(h4, h6) RQH_BF(h5, h7)
        RQH_BF(h8, h10) RQH_BF(h9, h11) RQH_BF(h12, h14) RQH_BF(h13, h15)
        RQH_BF(h0, h4) RQH_BF(h1, h5) RQH_BF(h2, h6) RQH_BF(h3, h7)
        RQH_BF(h8, h12) RQH_BF(h9, h13) RQH_BF(h10, h14) RQH_BF(h11, h15)
        RQH_BF(h0, h8) RQH_BF(h1, h9) RQH_BF(h2, h10) RQH_BF(h3, h11)
        RQH_BF(h4, h12) RQH_BF(h5, h13) RQH_BF(h6, h14) RQH_BF(h7, h15)
        mid[i] = (h0 * RQH_OUT) >> 13;                 /* (|h| <= 16 x 32767: the product < 2^31) */
        side[i] = (h1 * RQH_OUT) >> 13;
        act |= mid[i] | side[i];
#define RQH_W(k) { int32_t v_ = hl_c16((lo ? sh_tz(h##k, 2) : (h##k + 2) >> 2) + (((RQH_SGN >> k) & 1u) ? -x : x)); \
                   b[(w + RQH_TOP[k]) & RQH_M] = (int16_t)v_; am |= v_ ^ (v_ >> 31); }
        RQH_W(0) RQH_W(1) RQH_W(2) RQH_W(3) RQH_W(4) RQH_W(5) RQH_W(6) RQH_W(7)
        RQH_W(8) RQH_W(9) RQH_W(10) RQH_W(11) RQH_W(12) RQH_W(13) RQH_W(14) RQH_W(15)
#undef RQH_W
    }
    s->amax = am;
    return act | am;
}

static __attribute__((noinline)) void rev_hqh_run(rev_hqh_t *s, const int32_t *in, int32_t *mid, int32_t *side,
                                                  const rev_ctl_t *c)
{
    uint32_t k, i;
    int32_t act = 0, sw = c->mod << 2;                  /* the swing, Q8 samples (MOD 64: 4096 = 16) */
    if (c->size != s->gsz) rqh_gains(s, c->size);
    if (c->damp != s->gdmp) { s->p = clampi(c->damp * 246, 0, 31500); s->gdmp = c->damp; }
    for (i = 0; i < RQ_NC; i++) { s->x[i] = in[i]; act |= in[i]; }
    FR_FAR(rqh_dif)(s, s->x);
    for (k = 0; k < RQH_NL; k++) {
        uint32_t D = RQH_LEN[k], f = 0, r;
        if ((RQH_MODM >> k) & 1u) {
            uint32_t j = k >> 1, d8;
            s->ph[j] += RQH_INC[j];
            d8 = (D << 8) + (uint32_t)(sw + ((osc_sine(s->ph[j]) * sw) >> 15));
            D = d8 >> 8, f = d8 & 255u;
        }
        r = s->w + RQH_TOP[k] - D;
        if (s->e >= RQH_WARM)
            FR_FAR(rqh_end)(s->ring, r, (int32_t)f, &s->y[0][k], &s->lp[k], s->p, s->g[k], s->amax < RQH_LOUD);
        else rqh_end_w(s->ring, r, (int32_t)f, &s->y[0][k], &s->lp[k], s->p, s->g[k], s->e, D);
    }
    act |= FR_FAR(rqh_mix)(s, mid, side);
    s->w = (s->w + RQ_NC) & RQH_M;
    if (s->e < RQH_WARM) s->e += RQ_NC;
    s->quiet = act ? 0u : s->quiet < RQH_WARM + RQ_NC ? s->quiet + RQ_NC : s->quiet;
}
