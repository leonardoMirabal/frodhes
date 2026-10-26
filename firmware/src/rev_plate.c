/* SPDX-License-Identifier: GPL-3.0-only */
/* REVERB TYPE PLATE: the figure-8 plate tank of J. Dattorro, "Effect Design, Part 1: Reverberator and Other Filters"
 * (J. Audio Eng. Soc. 45(9), 1997), written here from the paper's figure and tables (integer, half rate).
 *
 * Included by rev_types.c (the framework), after fx.c: it uses fx.c's sh_tz, hl_c16, clampi, exp2_neg, osc_sine,
 * CTL, FS and HL_NC. Runs at the half rate (22.05 kHz), one control block = HL_NC = 16 samples a call:
 *   in -> bandwidth one-pole -> 4 input allpasses (142 / 107 / 379 / 277, g 0.75 / 0.625) -> the tank, two halves
 *   fed crosswise, each: + the other half's end x decay -> modulated allpass (672 / 908, g -0.7) -> delay (4453 /
 *   4217) -> damping one-pole -> x decay -> allpass (1800 / 2656, g = decay + 0.15 in 0.25 .. 0.5) -> delay (3720 /
 *   3163); 7 taps a side (the paper's table 2) -> mid / side.
 * The paper's lengths are at 29.76 kHz: x 0.7409 here (PL_R), and x 0.5 .. x 1 by SIZE.
 *
 * Memory: the tank's 8 lines share one ring of PL_N int16 (each line its own segment, all moving with one write
 * index: a line writes at w + its top and reads w + its top - its delay; PL_GAP samples between segments keep a
 * block's 16 writes out of a neighbour's reads); the 4 input allpasses share a small one the same way (PL_IN).
 * Every read is at least one block (16 samples) back, so a block runs stage by stage, each stage over its 16 samples,
 * with plain pointers: the rings carry PL_G guard samples after their end, a copy of their first PL_G (made at the
 * start of each block: a read sees only older blocks' writes), and a write that runs past the end lands in the guard
 * and is copied back to the front at the end of the block (pl_ptr, pl_wptr, pl_fix).
 * Start is O(1) (the ring is not cleared): while all of a read site's 16 reads are older than the start they come from
 * a block of zeros; in the block where they cross the start they reach back at most PL_G writes before it, and the
 * start zeroed those (each line's PL_G positions below its top: 8 x 17), so stale content (the other type's) reads
 * as 0, exactly as from a cleared ring (tests/py/test_rev_plate.py: started over garbage = started over zeros).
 * Every product in the loop is truncated toward zero (sh_tz) and the one-poles decay toward their input, so with no
 * input the tail ends in exact zeros; rev_plate_active() is 0 once the input and every line write have been 0 for
 * longer than the whole ring.
 *
 * Knobs (rev_ctl_t, per control block): DECAY 0..127 = T60 0.8 .. 6 s (exponential; per round of the tank the gain
 * is decay^4); TONE (damp 0..150, 60 = the default) = the in-loop damping and the input bandwidth; SIZE 0..127 = the
 * lengths x 0.5 .. x 1 (glides one step a block); MOD 0..127 = the modulated allpasses' excursion, 0 .. +-11.9
 * samples (64 = +-6, the paper's +-8 at 29.76 kHz), a 1 Hz sine, the two halves in quadrature.
 *
 * Cost (REV_US_PLATE): see the table at its #define (compiled pi32v2 -Os, loop counts from build/frodhes.dis). */

/* The cost, instructions per control block (16 half-rate samples; compiled pi32v2 -Os, build/frodhes.dis, every
 * instruction outside the loops counted as run once: an upper bound):
 *   pl_in        62 x 16 (+ 25)           1017   the bandwidth one-pole + the 4 input allpasses
 *   pl_half x 2  (38 + 32) x 16 (+ 43)    2326   the tank's two halves
 *   pl_taps      16 x 16 (+ 10)            266   the 7 left taps
 *   pl_taps_ms   34 x 16 (+ 17)            561   the 7 right taps, mid / side (31 before rev-upsample's mid / side
 *                                                 history ahead of the block: +48, ~2 us, inside REV_US_PLATE)
 *   rev_plate_run: the pointers 25 x 4 + 10 x 22 + 11 x 8, the guard copies 2 x 27, the rest 317     779
 *   = 4901 at rest; at worst + a SIZE step's 3 delays 87 + the decay gain 40 + the warm-up's checked reads (the
 *   first 0.15 s after a start) 132 + a wrapped write's copy 81 = 5241.
 * HALL (fx.c rev_hall, its own decimator and DC blocker included), counted the same way: 5644 at rest, 5864 with its
 * gains made anew (DECAY moving); PLATE + the framework's decimator (rev_dec, ~372): 5273 at rest, 5613 at worst,
 * 4-7 % under HALL. In us: HALL's kernel share is REV_US_HALL (210, from the FM-1's measured 260 for the whole
 * reverb); PLATE + rev_dec at 5613 / 5864 of it = 201 -> REV_US_PLATE 191 -> 200 (+ REV_US_DEC 10). (The rule of
 * thumb, 0.8 us per instruction per half-rate sample, reads both ~1.3x high against the FM-1: 262 us for 5241.) */
#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#define REV_US_PLATE 200u

#define PL_R(n) (((n) * 7409u + 5000u) / 10000u)       /* the paper's 29.76 kHz lengths at 22.05 kHz */
#define PL_N 16384u                                    /* the tank's ring (int16) */
#define PL_M (PL_N - 1u)
#define PL_IN 1024u                                    /* the input allpasses' ring */
#define PL_G 17u                                       /* the guard: a block's 16 samples + the interpolation's next */
#define PL_GAP 16u                                     /* between segments: >= one block */
#define PL_MSL 14u                                     /* the modulated lines' room: excursion (11.9) + interpolation */
/* the tank's 8 segments: the longest delay each holds (SIZE 127: x 255 / 256), their tops */
#define PL_LM0 (PL_R(672u) + PL_MSL)                    /* left: modulated allpass */
#define PL_LM1 PL_R(4453u)                              /* left: delay 1 */
#define PL_LM2 PL_R(1800u)                              /* left: allpass 2 */
#define PL_LM3 PL_R(3720u)                              /* left: delay 2 */
#define PL_LM4 (PL_R(908u) + PL_MSL)                    /* right: modulated allpass */
#define PL_LM5 PL_R(4217u)
#define PL_LM6 PL_R(2656u)
#define PL_LM7 PL_R(3163u)
#define PL_O0 PL_LM0
#define PL_O1 (PL_O0 + PL_GAP + PL_LM1)
#define PL_O2 (PL_O1 + PL_GAP + PL_LM2)
#define PL_O3 (PL_O2 + PL_GAP + PL_LM3)
#define PL_O4 (PL_O3 + PL_GAP + PL_LM4)
#define PL_O5 (PL_O4 + PL_GAP + PL_LM5)
#define PL_O6 (PL_O5 + PL_GAP + PL_LM6)
#define PL_O7 (PL_O6 + PL_GAP + PL_LM7)
_Static_assert(PL_O7 + PL_GAP <= PL_N, "the plate's 8 segments must fit its ring");
/* the input allpasses' segments (fixed lengths) */
#define PL_IL0 PL_R(142u)
#define PL_IL1 PL_R(107u)
#define PL_IL2 PL_R(379u)
#define PL_IL3 PL_R(277u)
#define PL_IO0 PL_IL0
#define PL_IO1 (PL_IO0 + PL_GAP + PL_IL1)
#define PL_IO2 (PL_IO1 + PL_GAP + PL_IL2)
#define PL_IO3 (PL_IO2 + PL_GAP + PL_IL3)
_Static_assert(PL_IO3 + PL_GAP <= PL_IN && PL_IL1 >= HL_NC, "the input allpasses must fit their ring");
#define PL_WARM (PL_LM1 + HL_NC)                        /* from here on no read is older than the start */
#define PL_LFO ((uint32_t)(1.0 * CTL / FS * 4294967296.0))   /* 1 Hz, per control block */
/* the output: the taps' 0.6 / 2 x 1.9 (Q12; / 1.5 for the lines' 1.5x level, pl_in) at SIZE <= 40, rising 0.067 dB a
 * SIZE step to +5 dB at 115 (pl_ctl): HALL's level at the same MIX (HALL grows with MIX's size, a plate thins out as its
 * tank grows) */
#define PL_OUT 2340
#define PL_LIM 262143                                  /* |mid|, |side| <= 2^18 (the framework's exact return) */
_Static_assert(14 * 32768 * 2 * PL_OUT <= 2147483647, "the output product");
/* the read sites: 0..7 the segments' own delays (0 / 4: the modulated ones, without the excursion), 8..21 the taps */
enum { PL_S_N = 22 };

typedef struct {
    int16_t ring[PL_N + PL_G + 1u];                  /* (+ 1: whole words for pl_cpy) */
    int16_t id[PL_IN + PL_G + 1u];
    int16_t zero[2u * HL_NC];       /* what a read of content older than the start sees */
    uint32_t w, e;                  /* the rings' write index (one a sample), samples since the start (to PL_WARM) */
    uint32_t ph;                    /* the LFO */
    uint32_t quiet;                 /* samples with no input and no line write (to PL_N) */
    int32_t bw, dl, dr;             /* the bandwidth and the two damping one-poles */
    int32_t g, g2, pdm, pbw;        /* decay, allpass 2's gain, the damping / bandwidth poles (Q15): g g2 pdm in
                                     * this order (pl_half's k[]) */
    int32_t sz, szn, rr;            /* the size that runs, the step being made (szn, rr: its block), -1 at the start */
    int32_t cdec, cdamp;            /* the settings the gains were made for */
    int32_t ko;                     /* the output gain (Q12) */
    uint32_t fix;                   /* this block: a write ran into the guard (ring 1, input 2) */
    uint16_t d[PL_S_N];             /* the delays at this size */
    uint16_t tk[PL_S_N];            /* the reads: their segment's top - their delay */
    int32_t x[HL_NC];
} rev_plate_t;

/* the paper's delays at 29.76 kHz: the segments, then the taps (left: 48_54 266, 48_54 2974, 55_59 1913, 59_63 1996,
 * 24_30 1990, 31_33 187, 33_39 1066; right: 24_30 353, 24_30 3627, 31_33 1228, 33_39 2673, 48_54 2111, 55_59 335,
 * 59_63 121) and the segment each one reads */
static const uint16_t PL_D0[PL_S_N] = {672, 4453, 1800, 3720, 908, 4217, 2656, 3163,
                                       266, 2974, 1913, 1996, 1990, 187, 1066,
                                       353, 3627, 1228, 2673, 2111, 335, 121};
static const uint8_t PL_SEG[PL_S_N] = {0, 1, 2, 3, 4, 5, 6, 7, 5, 5, 6, 7, 1, 2, 3, 1, 1, 2, 3, 5, 6, 7};
static const uint16_t PL_TOP[8] = {PL_O0, PL_O1, PL_O2, PL_O3, PL_O4, PL_O5, PL_O6, PL_O7};
static const uint16_t PL_ITOP[4] = {PL_IO0, PL_IO1, PL_IO2, PL_IO3};
static const uint16_t PL_IL[4] = {PL_IL0, PL_IL1, PL_IL2, PL_IL3};

static void rev_plate_start(rev_plate_t *s)
{
    uint32_t k, i;
    for (i = 0; i < 2u * HL_NC; i++)
        s->zero[i] = 0;
    s->w = s->e = s->ph = s->quiet = 0;
    for (k = 0; k < 8u; k++)                           /* each line's last PL_G writes before the start */
        for (i = PL_TOP[k] - PL_G; i < PL_TOP[k]; i++)
            s->ring[i] = 0;
    for (k = 0; k < 4u; k++)
        for (i = PL_ITOP[k] - PL_G; i < PL_ITOP[k]; i++)
            s->id[i] = 0;
    s->bw = s->dl = s->dr = 0;
    s->cdec = s->cdamp = -1;
    s->sz = -1;
}
static int rev_plate_active(const rev_plate_t *s) { return s->quiet < PL_N; }

/* the gains and lengths for these settings. SIZE glides one step at a time and a step's 22 delays are made 3 a block
 * (PL_RR blocks a step, 0.74 s over the whole range: the worst block's cost; a delay moves at most one step, 13 samples, between two blocks);
 * the decay gain when a step is complete or DECAY moves */
#define PL_RR 8
static void pl_dset(rev_plate_t *s, uint32_t k, int32_t sz)
{
    uint32_t d = (PL_R(PL_D0[k]) * (uint32_t)(128 + sz)) >> 8;
    s->d[k] = (uint16_t)d;
    s->tk[k] = (uint16_t)((PL_TOP[PL_SEG[k]] + PL_N - d) & PL_M);
}
static void pl_ctl(rev_plate_t *s, const rev_ctl_t *c)
{
    uint32_t k, k1, ls = 0, q, e;
    int new = c->decay != s->cdec;
    if (s->sz < 0) {                                    /* the start: all at once */
        s->sz = s->szn = c->size;
        for (k = 0; k < PL_S_N; k++)
            pl_dset(s, k, s->sz);
        s->rr = 0;
        new = 1;
    } else if (s->rr || s->sz != c->size) {
        if (!s->rr) s->szn = s->sz + (s->sz < c->size ? 1 : -1);
        k = s->rr * 3u, k1 = k + 3u < PL_S_N ? k + 3u : PL_S_N;
        for (; k < k1; k++)
            pl_dset(s, k, s->szn);
        if (++s->rr == PL_RR) { s->rr = 0; s->sz = s->szn; new = 1; }
    }
    if (new) {
        for (k = 0; k < 8u; k++)
            ls += s->d[k];
        /* g^4 per round of ls samples, 60 dB in T60 = 0.8 s x 7.5^(decay / 127): g = 2^-e, e = 9.2566 ls / 2^(2.907
         * decay / 127) (Q16), 2.907 / 127 = 1500 / 65536; + 104 a step (x 1.15 at 127) makes up the damping's,
         * the modulation's and the truncation's losses at the defaults (measured: tests/py/test_rev_plate.py) */
        q = exp2_neg((uint32_t)c->decay * 1841u);
        e = (uint32_t)(((uint64_t)(ls * q) * 9592u) >> 25);
        e = e * (1270u + (uint32_t)s->sz) / 1270u;      /* (a bigger tank damps less a second: + 10 % at 127) */
        s->g = (int32_t)exp2_neg(e);
        s->g2 = clampi(s->g + 4915, 8192, 16384);
        s->ko = (int32_t)((4u * PL_OUT / 3u * exp2_neg(65536u - (uint32_t)clampi((s->sz - 40) * 729, 0, 54427))) >> 15);
        s->cdec = c->decay;
    }
    if (c->damp != s->cdamp) {
        s->pdm = clampi(c->damp * 350, 0, 31000);      /* the poles (Q15): bright (0) .. dark; 60 (TONE 64): */
        s->pbw = clampi(c->damp * 240, 0, 26000);      /* about HALL's colour; the input bandwidth (|b - u| < 82560: the product < 2^31) */
        s->cdamp = c->damp;
    }
}
_Static_assert(3 * PL_RR >= PL_S_N, "a SIZE step's delays in PL_RR blocks");

/* a read site in the tank: P[j] (j = 0 .. 16) is the sample of delay hi - j at the block's sample 0 (the block's
 * sample i reads P[i] for delay hi, P[i + 1] for hi - 1). While every read of the block is older than the start: the
 * zeros. The block where the reads cross the start reads write-times from -17 on (a delay grows at most 13 a block,
 * e 16): those were zeroed by the start */
static inline const int16_t *pl_ptr(const rev_plate_t *s, uint32_t top, uint32_t hi, uint32_t lo)
{
    if (s->e + HL_NC <= lo) return s->zero;
    return s->ring + ((s->w + top - hi) & PL_M);
}
/* a write site: 16 samples from the top; one that runs past the ring's end writes the guard (pl_fix copies it) */
static inline int16_t *pl_wptr(rev_plate_t *s, int16_t *b, uint32_t n, uint32_t at, uint32_t bit)
{
    at &= n - 1u;
    if (at > n - HL_NC) s->fix |= bit;
    return b + at;
}
static void pl_cpy(int16_t *d, const int16_t *q)     /* PL_G + 1 int16 (both 4-byte aligned) */
{
    uint32_t *a = (uint32_t *)(void *)d, i;
    const uint32_t *c = (const uint32_t *)(const void *)q;
    for (i = 0; i < (PL_G + 1u) / 2u; i++)
        a[i] = c[i];
}

/* the input, one block: the bandwidth one-pole (toward its input: it ends at 0), then the 4 allpasses in a row, each
 * v = x - g d into its write, y = d + g v (plain shifts: with g > 0 and no input the stored value changes sign every
 * pass and never grows, so it dies out). Returns the OR of the input */
#define PL_AP(k, g) d = r##k[i], v = hl_c16(y - ((d * (g)) >> 15)), o##k[i] = (int16_t)v, y = d + ((v * (g)) >> 15)
static __attribute__((noinline)) int32_t pl_in(const int16_t *const *r, int16_t *const *o, const int32_t *in,
                                               int32_t *x, int32_t *bw, int32_t pb)
{
    const int16_t *r0 = r[0], *r1 = r[1], *r2 = r[2], *r3 = r[3];
    int16_t *o0 = o[0], *o1 = o[1], *o2 = o[2], *o3 = o[3];
    uint32_t i;
    int32_t b = *bw, act = 0;
    for (i = 0; i < HL_NC; i++) {
        int32_t y, d, v, u = in[i] + (in[i] >> 1);  /* the lines 1.5x hot: 3.5 dB less rounding noise */
        b = u + sh_tz((b - u) * pb, 15);
        act |= u;
        y = b;
        PL_AP(0, 24576);
        PL_AP(1, 24576);
        PL_AP(2, 20480);
        PL_AP(3, 20480);
        x[i] = y;
    }
    *bw = b;
    return act;
}

/* one half of the tank, one block, its segments' reads r[] and writes o[]. Truncated toward zero: the two decay
 * products, the damping one-pole (a plain shift there held a dark one-pole at up to -18 for ever: a DC loop) and the
 * allpasses' stored values, so without input the loop's content can only shrink; the allpasses' outputs use plain
 * shifts. a = x + g f (f = the other half's end); the modulated allpass (read q, the interpolation fm, gain -0.7,
 * write wm) into delay 1 (w1); delay 1's end (r1), damped (lp), x g, through allpass 2 (read r2, gain g2, write w2)
 * into delay 2 (w3). Returns the OR of the writes */
static FR_RAM __attribute__((noinline)) int32_t pl_half(const int32_t *x, const int16_t *f, const int16_t *const *r,
                                                 int32_t fm, int16_t *const *o, int32_t *lp, const int32_t *k)
{
    const int16_t *q = r[0], *r1 = r[1], *r2 = r[2];
    int16_t *wm = o[0], *w1 = o[1], *w2 = o[2], *w3 = o[3];
    uint32_t i;
    int32_t g = k[0], g2 = k[1], p = k[2], l = *lp, act = 0;
    for (i = 0; i < HL_NC; i++) {
        int32_t a = x[i] + sh_tz(f[i] * g, 15), q0 = q[i];
        int32_t d = q0 + (((q[i + 1] - q0) * fm) >> 8), v = hl_c16(a + sh_tz(d * 22938, 15));
        wm[i] = (int16_t)v;
        v = hl_c16(d - ((v * 22938) >> 15));
        w1[i] = (int16_t)v;
        act |= v;
    }
    for (i = 0; i < HL_NC; i++) {
        int32_t u = r1[i], d = r2[i], v;
        l = u + sh_tz((l - u) * p, 15);
        u = sh_tz(l * g, 15);
        v = hl_c16(u - sh_tz(d * g2, 15));
        w2[i] = (int16_t)v;
        v = hl_c16(d + ((v * g2) >> 15));
        w3[i] = (int16_t)v;
        act |= v;
    }
    *lp = l;
    return act;
}

/* the 7 taps of a side (+ + - + - - -), one block: the left into mid[]; then the right, and mid / side */
static __attribute__((noinline)) void pl_taps(const int16_t *const *t, int32_t *o)
{
    const int16_t *a0 = t[0], *a1 = t[1], *a2 = t[2], *a3 = t[3], *a4 = t[4], *a5 = t[5], *a6 = t[6];
    uint32_t i;
    for (i = 0; i < HL_NC; i++)
        o[i] = a0[i] + a1[i] - a2[i] + a3[i] - a4[i] - a5[i] - a6[i];
}
static __attribute__((noinline)) void pl_taps_ms(const int16_t *const *t, int32_t *mid, int32_t *side, int32_t k)
{
    const int16_t *a0 = t[0], *a1 = t[1], *a2 = t[2], *a3 = t[3], *a4 = t[4], *a5 = t[5], *a6 = t[6];
    uint32_t i;
    for (i = 0; i < HL_NC; i++) {
        int32_t r = a0[i] + a1[i] - a2[i] + a3[i] - a4[i] - a5[i] - a6[i], l = mid[i];
        int32_t m = ((l + r) * k) >> 12, d = ((l - r) * k) >> 12;
        m = m > -PL_LIM ? m : -PL_LIM, d = d > -PL_LIM ? d : -PL_LIM;     /* (smax / smin) */
        mid[i] = m < PL_LIM ? m : PL_LIM;
        side[i] = d < PL_LIM ? d : PL_LIM;
    }
}

static __attribute__((noinline)) void rev_plate_run(rev_plate_t *s, const int32_t *in, int32_t *mid, int32_t *side,
                                                    const rev_ctl_t *c)
{
    int16_t *b = s->ring, *ib = s->id, *o[8];
    const int16_t *t[PL_S_N];
    uint32_t k, w, ex, dm0, dm1;
    int32_t act;
    pl_ctl(s, c);
    ex = ((uint32_t)c->mod * 3u) >> 1;                 /* MOD 127 (x 16): 3048 = 11.9 samples (Q8) */
    s->ph += PL_LFO;
    dm0 = (s->d[0] << 8) + (uint32_t)((osc_sine(s->ph) * (int32_t)ex) >> 15);
    dm1 = (s->d[4] << 8) + (uint32_t)((osc_sine(s->ph + 0x40000000u) * (int32_t)ex) >> 15);
    w = s->w;
    s->fix = 0;
    /* the bandwidth one-pole and the input allpasses (their ring: the guard is the front's copy) */
    pl_cpy(ib + PL_IN, ib);
    for (k = 0; k < 4u; k++) {
        t[k] = s->e + HL_NC <= PL_IL[k] ? s->zero : ib + ((w + PL_ITOP[k] - PL_IL[k]) & (PL_IN - 1u));
        o[k] = pl_wptr(s, ib, PL_IN, w + PL_ITOP[k], 2u);
    }
    act = pl_in(t, o, in, s->x, &s->bw, s->pbw);
    /* the tank: every read site's pointer (the zeros while it reads before the start), the guard copy, the halves */
    if (s->e >= PL_WARM)
        for (k = 0; k < PL_S_N; k++)
            t[k] = b + ((w + s->tk[k]) & PL_M);
    else
        for (k = 0; k < PL_S_N; k++)
            t[k] = pl_ptr(s, PL_TOP[PL_SEG[k]], s->d[k], s->d[k]);
    t[0] = pl_ptr(s, PL_O0, dm0 >> 8, (dm0 >> 8) - 1u);
    t[4] = pl_ptr(s, PL_O4, dm1 >> 8, (dm1 >> 8) - 1u);
    pl_cpy(b + PL_N, b);
    for (k = 0; k < 8u; k++)
        o[k] = pl_wptr(s, b, PL_N, w + PL_TOP[k], 1u);
    {
        /* (volatile: read once; as a constant the compiler rebuilt the address in the loop, 4 instructions a sample) */
        const int32_t *volatile xv = s->x;
        act |= FR_FAR(pl_half)(xv, t[7], t, (int32_t)(dm0 & 255u), o, &s->dl, &s->g);
        act |= FR_FAR(pl_half)(xv, t[3], t + 4, (int32_t)(dm1 & 255u), o + 4, &s->dr, &s->g);
    }
    pl_taps(t + 8, mid);
    pl_taps_ms(t + 15, mid, side, s->ko);
    if (s->fix & 1u) pl_cpy(b, b + PL_N);            /* (the guard past the wrapped write: the front's own copy) */
    if (s->fix & 2u) pl_cpy(ib, ib + PL_IN);
    s->w = w + HL_NC;
    if (s->e < PL_WARM) s->e += HL_NC;
    s->quiet = act ? 0u : s->quiet < PL_N ? s->quiet + HL_NC : PL_N;
}
