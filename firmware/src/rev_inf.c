/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes REVERB type INFINITE (0.9.5): a huge, unrealistic tank. DECAY 0..119: T60 4..60 s; 120..126: SUSTAIN (the
 * tail never decays and everything played piles into it, up to a bounded level); 127: FREEZE (the input closes, the
 * tail at that moment is held for ever; new notes play on top without feeding it).
 * Included by rev_types.c (after fx.c: it uses sh_tz, hl_c16, exp2_neg, osc_sine, CTL, HL_NC, POOL from there).
 *
 *   in (HL_NC samples at 22.05 kHz, as rev_types.c's rev_dec gives them) -> [2:1 down, REV_INF_QUARTER] -> TONE's
 *   input one-pole -> the input's gain (FREEZE's fade x the level guard x a limiter on the block's peak) -> 4
 *   diffusers (allpass, g 0.6) -> + into 8 lines (100..200 ms x SIZE 0.5..1, primes), each: a modulated read (allpass
 *   interpolation: no high loss, so a held tank keeps its colour) -> the damping one-pole and the line's gain in one
 *   product (g (1 - c), c; c relative to the line's loss: TONE sets the top's T60 against the middle's) -> [an
 *   allpass, g 0.5, on 4 of the 8 lines] -> line 0's DC block -> the 8 x 8 Householder mix (y_k - S / 4: adds,
 *   subtracts and one shift; lossless), each line's into the next -> back into the lines (int16, clamped: a safety
 *   only). Mid / side taps from the line ends ([1:2 up, REV_INF_QUARTER]).
 *
 * Integer only, and the loop loses nothing to its rounding (a 60 s T60 holds at any level): the reads round, the line
 * ends and the mix carry their remainders, the allpasses are lifting steps. A quiet line truncates toward zero
 * instead, so a tail that is not held ends in exact zeros. SUSTAIN / FREEZE (c->freeze 1 / 2): every line's gain is
 * 1 + dq, a level controller's (b0 = 1 + dq, c = 0 in Q14: the line end is the read itself); FREEZE fades the input
 * out in 50 ms. Leaving them: the gains return to DECAY's (the framework's slew), the damping and the input glide
 * back. Start (a type switch): O(1), no buffer cleared: the ring reads as zero until its rows have been written since
 * the start, each allpass until its index has wrapped once.
 *
 * The tank (the FM-1, 2026-10-25: the bright one was 4250 us whole ISR on VIBES, over the 4200 rule):
 *   REV_INF_LITE (the default): the bright tank at 22.05 kHz, lighter: run every 4th control block for 64 samples
 *     (its fixed costs once: the output 2.9 ms later; line 0 2 % shorter, so the first echo comes as early), 3
 *     diffusers g 0.5 as lifting steps (no multiply), no allpass in the lines, 4 of the 8 lines read whole samples
 *     (unmodulated; the 4 others as before, their reads updated every 11.6 ms). The same top band, lengths, T60, SUSTAIN
 *     and FREEZE; a little less smear at the onset, the tail's density builds a little slower, half the lines' wander.
 *   REV_INF_LITE=0: the bright tank as designed (4 line allpasses, 8 modulated lines): over the CPU gate.
 *   REV_INF_QUARTER=1 (the fallback, the plan's name): the tank at 11.025 kHz, run every second control block for 16
 *     samples (half the memory, cheaper than HALL, darker: 5.5 kHz). */

#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#ifndef REV_INF_QUARTER
#define REV_INF_QUARTER 0
#endif
#ifndef REV_INF_LITE                /* the bright tank, lighter (the default since the FM-1's 4250 us, 2026-10-25) */
#define REV_INF_LITE (!REV_INF_QUARTER)
#endif
#if REV_INF_QUARTER
#define RI_DIV 2u
#else
#define RI_DIV 1u
#endif
/* the cost, us per 256-frame block, from the FM-1 (early-int, 2026-10-25: 10 voices, every effect, hil_cpu --worst
 * --usb 48000, whole ISR; HALL 3956 VIBES / 4036 Rhodes SPREAD 127 + bend): f71a25c's quarter tank 4136..4140 (HALL
 * + 180 on VIBES), its bright tank 4248..4280 (+294). The compiled kernel's instructions a control block
 * (tools/drive_insns.py's paths x their trips): that bright 9300, that quarter 5049: 0.027 (VIBES) .. 0.034 us each.
 *   LITE (the default) 5200: quarter's + 151 -> ~4145 VIBES, ~4141 Rhodes: REV_US_INF 210 (HALL) + 180 + 5, less
 *   the framework's REV_US_DEC 10 and REV_US_LOCUT 16 (LOW CUT was on in that case)
 *   quarter now 4876 (-173: the mix in its own function): 364        bright (LITE=0): 478, over REV_US_MAX
 * rev_types.c adds REV_US_DEC (23 since the half-bands; 10 then) and checks it against REV_US_MAX (HALL + 200) */
#if REV_INF_QUARTER
#define REV_US_INF 364u
#elif REV_INF_LITE
#define REV_US_INF 369u
#else
#define REV_US_INF 478u
#endif
#define RI_NL 8u                    /* lines */
#define RI_CPT (REV_INF_QUARTER ? 2u : REV_INF_LITE ? 4u : 1u)   /* control blocks a tank block: its fixed costs
                                     * once for several (the output that many control blocks late: quarter 1.5 ms,
                                     * LITE 2.9 ms) */
#define RI_LCPT (RI_CPT == 4u ? 2u : RI_CPT == 2u ? 1u : 0u)   /* log2 RI_CPT */
#define RI_NB (16u * RI_CPT / RI_DIV)   /* tank samples a tank block: 16 (bright, quarter), 64 (LITE) */
#define RI_N (REV_INF_LITE ? 4544u : 4512u / RI_DIV)   /* ring rows: a multiple of RI_NB, >= the longest line + 2 x
                                     * the widest swing + 2 */
#define RI_MODQ8 (24u * 256u / RI_DIV)  /* the swing at MOD 64, Q8 samples (+-24 at 22.05 kHz); MOD 127 about twice */
#define RI_KD (9 / (RI_DIV * RI_DIV))   /* TONE's damping ratio R = damp x RI_KD / 256 (bright: damp 60 -> 2.1, 150
                                     * -> 5.3; the quarter tank a quarter of it: its 1 kHz is 4x nearer its top): a
                                     * line's one-pole c = R (1 - g) / 2, so the top's T60 is the T60 / (1 + R) at any
                                     * DECAY (an absolute damping would cut a 60 s tail's middle to 20 s) */
#define RI_KA 100                   /* TONE darker than its middle (damp 60 .. 150): an absolute c on top, Q14 per step
                                     * (TONE 0: c 0.55; the middle and brighter keep the T60 in the middle band) */
#define RI_KI (REV_INF_QUARTER ? 1330u : 2400u)   /* TONE's input one-pole: k = 2^(-damp x RI_KI / 65536): open
                                     * at TONE 127, ~1 kHz at its middle (HALL's colour), ~250 Hz at 0 (the tank's
                                     * colour without touching its T60) */
#define RI_IN 26029                 /* the input's level, Q15 (-2 dB: HALL's wet level over a chord's first 2 s) */
#define RI_CMIN (1311 / (int32_t)RI_DIV)   /* every line's least damping, always (held too), c 0.08 (Q14): the top
                                     * near the tank's Nyquist loses 1.4 dB a pass (HALL's brightness there), the middle
                                     * 0.03 (RI_LOSS gives it back to the T60).
                                     * Without it the lossless loop kept the rounding's noise, which its remainders
                                     * shape upwards, at 10..11 kHz for ever: +30 dB there in a FREEZE */
#define RI_KMAX (RI_DIV == 1u ? 12288 : 16384)   /* the input one-pole's brightest (TONE 127): 0.75 at 22.05 kHz
                                     * (-4.5 dB at 10.5 kHz, ~HALL's top), open on the quarter tank */
#define RI_HOT 2u                   /* the tank runs 4x hotter than its input (12 dB less rounding in a quiet freeze) */
#define RI_XMAX 12000               /* the input limiter: a block's peak into the tank (x 2^RI_HOT) at most this */
#define RI_LV 16000                 /* the level guard: a block's mean |mid| above this turns the input down */
#define RI_DQMAX (64 << 8)          /* the held level's make-up limit, Q8 of Q14: +2^-8 a pass */
#define RI_DQCUT (256 << 8)         /* SUSTAIN's leak limit: -2^-6 a pass */
#define RI_SUS 10000                /* SUSTAIN's ceiling: the mean |mid| it piles up to */
#define RI_KP 16                    /* the held level's proportional make-up: dq per 1/1024 of the level missing */
#define RI_XQ 64                    /* SUSTAIN: input above this (|x|, the tank's scale) raises the held level */
#define RI_LNB (4u + RI_LCPT - (RI_DIV - 1u))   /* log2 RI_NB */
#define RI_FIN_STEP (475 * (int32_t)RI_CPT)   /* the freeze fade: 32768 in 69 control blocks = 50 ms */
#define RI_DCK (13u - RI_DIV)       /* the loop's DC block: 0.86 Hz */
#define RI_LOUD 24                  /* a line this quiet (two samples + its last output) truncates (ri_end): it then dies
                                     * to exact zero; louder, the remainder is carried (lossless) */
#define RI_REL (-31)                /* a release's DECAY: T60 2 s (the law 4 x 15^(d / 119) below 0) */
#define RI_LOSS 140u                /* (see ri_gains) */
#define RI_NDIF (REV_INF_LITE ? 3u : 4u)   /* the input diffusers (LITE: the three longest) */
#define RI_NLAP 4u
#if REV_INF_LITE
#define RI_LAPM 0x00u               /* the lines with an allpass: none (LITE) */
#define RI_MODM 0x55u               /* the modulated lines (0, 2, 4, 6; the others read whole samples) */
#define RI_RRS 3u                   /* the reads' round robin: 2 lines a tank block, each every 4 (11.6 ms) */
#else
#define RI_LAPM 0x55u               /* the lines with an allpass (0, 2, 4, 6) */
#define RI_MODM 0xFFu
#define RI_RRS 3u                   /* 2 lines a block, each every 4 */
#endif
#define RI_LEN0 (REV_INF_LITE ? 2161 : 2203)   /* (LITE: its 2.9 ms later output, the first echo kept as early) */
static const uint16_t RI_LEN[RI_NL] = {
#if REV_INF_QUARTER
    1103, 1217, 1327, 1481, 1637, 1811, 1993, 2203
#else
    RI_LEN0, 2437, 2687, 2963, 3271, 3617, 3989, 4409
#endif
};
static const uint16_t RI_AP[RI_NDIF + RI_NLAP] = {   /* the diffusers, then the 4 lines' allpasses */
#if REV_INF_QUARTER
    43, 61, 97, 137, 73, 103, 149, 199
#elif REV_INF_LITE
    127, 193, 277, 151, 211, 293, 409
#else
    89, 127, 193, 277, 151, 211, 293, 409
#endif
};
#define RI_APSUM (RI_DIV == 1u ? 1750u : 862u)
#define RI_HZ(f) ((uint32_t)((f) * CTL / 44100.0 * 4294967296.0))   /* the LFO step per control block */
static const uint32_t RI_INC[RI_NL] = {RI_HZ(0.07), RI_HZ(0.207), RI_HZ(0.104), RI_HZ(0.241),
                                       RI_HZ(0.139), RI_HZ(0.276), RI_HZ(0.173), RI_HZ(0.31)};

typedef struct {
    /* the small state first (short offsets: one instruction a load); all of it reset by rev_inf_start (~1.3 KB) */
    uint32_t w, age, quiet, apfull;
    int32_t il, ie, ik;                 /* TONE's input one-pole: its state, remainder, coefficient (Q14) */
    int32_t xp, ilim, fin, c, ca, mp, sp, gdec, gsz, gi;
    int32_t xi;                         /* the tank block's input level so far */
    int32_t dz[10], qm[5], qs[5];       /* the quarter tank's half-bands: the 2:1 down's history, the 1:2 up's */
    uint8_t half;                       /* the control block within the tank block (0 .. RI_CPT - 1) */
    int32_t te;                         /* the mix's S / 4 remainder */
    int32_t dh, de;                     /* the loop's DC block */
    uint32_t hw;                        /* FREEZE: half-rate samples since the input closed */
    int32_t rd;                         /* a release's DECAY (119 .. RI_REL) */
    int32_t lv, tgt, dq, dqi;           /* the mid's level (mean |mid|), the held level, the gains' 1 + dq (Q8 of Q14) */
    uint8_t mlo, rr, fm, full;          /* quiet lines (bit k), the read's round robin, the held mode (0, 1 SUSTAIN,
                                         * 2 FREEZE), the ring written all through */
    int32_t yp[RI_NL], op[RI_NL], oe[RI_NL];   /* the interpolators' and the damping's last outputs, its remainder */
    int32_t ma[RI_NL];                  /* the reads' interpolation (Q15) and delay, set every 4th block */
    uint32_t ph[RI_NL];
    uint16_t mI[RI_NL];
    uint16_t len[RI_NL], tlen[RI_NL];   /* the lines' length now, and SIZE's target */
    uint16_t lo[RI_NL];                 /* a SIZE fade (0.9.6, BUGHUNT #19): the lengths it fades from (to len) */
    int32_t xw;                         /* the fade's weight of the new reads at this tank block's start (Q14) */
    uint8_t xf;                         /* a fade runs */
    int16_t g[RI_NL];                   /* the gains, Q14 */
    uint16_t apd[RI_NDIF + RI_NLAP];
    int32_t x[RI_NB];                   /* the diffused input (written before it is read; cleared at a start only as
                                         * it lies here: ri_mix reaches it, m and s from one register, short offsets) */
    int32_t m[RI_NB], s[RI_NB];         /* the taps (a tank block of two control blocks: their second half waits) */
    /* not cleared at a start: written before it is read (y), or stale content reads as zero (see above) */
    int32_t y[RI_NB][RI_NL];            /* the line ends of this block */
    int16_t ap[RI_APSUM];
    int16_t ring[(RI_N + 1u) * RI_NL];  /* row t = the 8 lines' samples written at t; row RI_N repeats row 0 */
} rev_inf_t;

static void rev_inf_start(rev_inf_t *s)
{
    memset(s, 0, __builtin_offsetof(rev_inf_t, y));
    s->ilim = 32768;
    s->fin = 32768;
    s->gi = RI_IN;
    s->ik = 16384;
    s->gdec = s->gsz = -1;
    s->rd = 119;
}
static int rev_inf_active(const rev_inf_t *s) { return s->quiet < RI_N + 2u * 512u; }

/* the allpasses: v = x + g b, out = b - g v (Q14), b the line's oldest sample; z: b reads as 0 (never written) */
static inline void ri_dif(int16_t *q, int32_t g, int32_t *x, uint32_t n, int z)
{
    int16_t *e = q + n;
    if (z)
        for (; q < e; q++, x++) { int32_t v = hl_c16(*x); *q = (int16_t)v; *x = -sh_tz(v * g, 14); }
    else
        for (; q < e; q++, x++) {
            int32_t b = *q, v = hl_c16(*x + sh_tz(b * g, 14));
            *q = (int16_t)v;
            *x = b - sh_tz(v * g, 14);
        }
}
/* LITE's diffusers: g 0.5 as two lifting steps (as ri_lap, x steps 1) */
static inline __attribute__((always_inline)) void ri_dif2(int16_t *q, int32_t *x, uint32_t n, int z)
{
    int16_t *e = q + n;
    if (z)
        for (; q < e; q++, x++) { int32_t v = hl_c16(*x); *q = (int16_t)v; *x = -(v >> 1); }
    else
        for (; q < e; q++, x++) {
            int32_t b = *q, v = hl_c16(*x + (b >> 1));
            *q = (int16_t)v;
            *x = b - (v >> 1);
        }
}
/* the lines' allpass, g 0.5, y steps RI_NL (one row): two lifting steps (v = x + b / 2, y = b - v / 2, each exactly
 * invertible), so it loses nothing however it rounds (the floor's -1/4 is a DC: the loop's DC block takes it) */
static inline __attribute__((always_inline)) void ri_lap(int16_t *q, int32_t *y, uint32_t n, int z)
{
    int16_t *e = q + n;
    if (z)
        for (; q < e; q++, y += RI_NL) { int32_t v = hl_c16(*y); *q = (int16_t)v; *y = -(v >> 1); }
    else
        for (; q < e; q++, y += RI_NL) {
            int32_t b = *q, v = hl_c16(*y + (b >> 1));
            *q = (int16_t)v;
            *y = b - (v >> 1);
        }
}
/* one line's end over n samples: b = the oldest row read (the next row is one RI_NL on), the allpass interpolation
 * (a Q15, rounded: the read itself loses nothing), then o = g (1 - c) v + c o' (b0, a1 Q14: b0 + a1 <= 1 + 2^-8, so
 * |o| <= max |v|). lo (the line quiet): the product truncated toward zero, so it dies to exact zero; else its
 * remainder carried to the next sample (a long T60 at any level loses nothing to the rounding) */
static inline __attribute__((always_inline)) void ri_end(const int16_t *b, int32_t *y, uint32_t n, int32_t a, int32_t b0, int32_t a1, int32_t *yp,
                          int32_t *op, int32_t *oe, int lo)
{
    int32_t p = *b, q, v = *yp, o = *op, e = *oe;
    if (lo)
        do {
            b += RI_NL;
            q = *b;
            v = p + ((a * (q - v) + 16384) >> 15);
            o = sh_tz(b0 * v + a1 * o, 14);
            *y = o;
            y += RI_NL;
            p = q;
        } while (--n);
    else
        do {
            b += RI_NL;
            q = *b;
            v = p + ((a * (q - v) + 16384) >> 15);
            o = b0 * v + a1 * o + e;
            e = o & 16383;                              /* the remainder carried: no loss at any level */
            o >>= 14;
            *y = o;
            y += RI_NL;
            p = q;
        } while (--n);
    *yp = v;
    *op = o;
    *oe = lo ? 0 : e;
}
/* a line end that reads whole samples (LITE's unmodulated lines): b = the row read; the rest as ri_end */
static inline __attribute__((always_inline)) void ri_endp(const int16_t *b, int32_t *y, uint32_t n, int32_t b0, int32_t a1, int32_t *op, int32_t *oe,
                           int lo)
{
    int32_t o = *op, e = *oe;
    if (lo)
        do { o = sh_tz(b0 * *b + a1 * o, 14); *y = o; b += RI_NL; y += RI_NL; } while (--n);
    else
        do { o = b0 * *b + a1 * o + e; e = o & 16383; o >>= 14; *y = o; b += RI_NL; y += RI_NL; } while (--n);
    *op = o;
    *oe = lo ? 0 : e;
}
/* the runs of an index d over a buffer of len (wrapping to 0), RI_NB samples from sample s0 */
#define RI_RUNS(d, len, k, s, s0, body)                                                     \
    for (s = (s0); s < RI_NB; s += k) {                                                     \
        k = (len) - (d) < RI_NB - s ? (len) - (d) : RI_NB - s;                              \
        body;                                                                               \
        d = (d) + k >= (len) ? 0u : (d) + k;                                                \
    }

/* a SIZE fade's reads (0.9.6, BUGHUNT #19: the lengths glided one sample a block, a bend of the ringing tail and a
 * click at every step): a line's RI_NB + 1 rows from the new read (row dn) and the old (dol), the new weighted w + j
 * RI_XS (Q14) at row j, into t (one column, ri_end's stride); 4096 tank samples, 186 ms at 22.05 kHz. In runs that
 * do not wrap (9 instructions a row, not 19) */
#define RI_XS 4
static int16_t ri_xt[(RI_NB + 1u) * RI_NL];
FR_RAM __attribute__((noinline)) static void ri_xrows(int16_t *t, const int16_t *r, uint32_t dn, uint32_t dol, int32_t w)
{
    uint32_t n = RI_NB + 1u, m;
    while (n) {
        const int16_t *pn = r + dn * RI_NL, *po = r + dol * RI_NL;
        m = RI_N - dn < RI_N - dol ? RI_N - dn : RI_N - dol;
        if (m > n) m = n;
        n -= m, dn += m, dol += m;
        if (dn == RI_N) dn = 0;
        if (dol == RI_N) dol = 0;
        do {
            int32_t a = *po;
            *t = (int16_t)(a + (((*pn - a) * w) >> 14));
            po += RI_NL, pn += RI_NL, t += RI_NL, w += RI_XS;
        } while (--m);
    }
}

/* the gains (Q14) for DECAY d (0..119: T60 4 .. 60 s, 4 x 15^(d / 119)) at the lines' lengths now */
static inline __attribute__((always_inline)) void ri_gains(rev_inf_t *s, int32_t d)     /* (d down to RI_REL: 131072 + d x 2151 stays > 0) */
{
    /* 32768 / T60, less the loop's own losses measured in the 500 Hz .. 1 kHz octaves (the rounding, the lines'
     * allpasses: ~0.004 / s; tests/py/test_rev_types.py's T60) */
    uint32_t inv = FR_FAR(exp2_neg)((uint32_t)(131072 + d * 2151)) - RI_LOSS, k, l;
    for (k = 0; k < RI_NL; k++) {                                   /* 2^(-9.966 len / (fs T60)) */
        l = s->len[k];                                              /* (a SIZE fade: the length between, as it fades) */
        if (s->xf) l = (uint32_t)((int32_t)s->lo[k] + ((((int32_t)l - (int32_t)s->lo[k]) * s->xw) >> 14));
        s->g[k] = (int16_t)(FR_FAR(exp2_neg)(((l * inv) >> 8) * (59u * RI_DIV) >> 8) >> 1);
    }
}

/* the input into s->x[o .. o + n): (the quarter tank: 2:1 down), the gain (the freeze fade, the level guard, and a
 * limiter on this block's own peak: one gain a block, no waveshaping), xi |= its |x| */
FR_RAM __attribute__((noinline)) static int32_t ri_input(rev_inf_t *s, const int32_t *in, uint32_t o)
{
    uint32_t i;
    int32_t pk = 0, gi = s->gi, xi = 0, il = s->il, ie = s->ie, ik = s->ik;
#if REV_INF_QUARTER
    int32_t b[10u + HL_NC];                         /* the half-band's history, then this block's input */
    for (i = 0; i < 10u; i++) b[i] = s->dz[i];
    for (i = 0; i < HL_NC; i++) b[10u + i] = clampi(in[i], -65535, 65535);
    for (i = 0; i < 10u; i++) s->dz[i] = b[HL_NC + i];
#endif
    for (i = 0; i < HL_NC; i++) pk |= in[i] ^ (in[i] >> 31);
    pk = pk > 65535 ? 65535 : pk;
    if (((pk * (gi >> 4)) >> (11 - RI_HOT)) > RI_XMAX) gi = (RI_XMAX << (15 - RI_HOT)) / pk;
    for (i = 0; i < HL_NC / RI_DIV; i++) {
#if REV_INF_QUARTER
        /* 2:1 down through an 11-tap half-band (Lagrange, (3, 0, -25, 0, 150, 256, ...) / 512: -40 dB and more over
         * the band that would fold below 5.5 kHz; the old (1, 2, 1) / 4 let it through at -8 dB) */
        const int32_t *c = b + 2u * i + 5u;
        int32_t x = (256 * c[0] + 150 * (c[-1] + c[1]) - 25 * (c[-3] + c[3]) + 3 * (c[-5] + c[5])) >> 9;
#else
        int32_t x = in[i];
#endif
        x = clampi(x, -65535, 65535);
        x = (x - il) * ik + ie;                     /* TONE's input one-pole, Q14 (the remainder carried: it settles
                                                     * to 0) */
        ie = x & 16383;
        il += x >> 14;
        x = sh_tz(il * gi, 15 - RI_HOT);
        s->x[o + i] = x;
        xi |= x ^ (x >> 31);
    }
    s->il = il, s->ie = ie;
    return xi;
}

/* the mix (Householder: y_k - S / 4, lossless; line k's into line k - 1) back into the ring's rows o, the taps
 * (mid, side); its own function, pointers stepping (the registers it needs: a spill fewer each sample). Returns the
 * taps ORed (activity); *ab += the sum of |mid| */
FR_RAM __attribute__((noinline)) static int32_t ri_mix(const int32_t *y, const int32_t *xp, int16_t *o, int32_t *mo,
                                                int32_t *so, int32_t *tep, int32_t *abp)
{
    const int32_t *e = y + RI_NB * RI_NL;
    int32_t te = *tep, ab = 0, act = 0;
    for (; y < e; y += RI_NL, o += RI_NL) {
        int32_t y0 = y[0], y1 = y[1], y2 = y[2], y3 = y[3], y4 = y[4], y5 = y[5], y6 = y[6], y7 = y[7], x = *xp++;
        int32_t A = y0 - y3 + y4 + y6, B = y1 + y2 - y5 + y7, u = A + B, S, t, p, q;
        S = u + 2 * (y3 + y5) + te, t = S >> 2;         /* (the sum of the 8 and te: u has all but y3, y5 with +) */
        te = S & 3;                                     /* (S / 4 with its remainder carried: unbiased) */
        p = x - t, q = -x - t;                          /* (y - t +- x as y + p / y + q: one add each) */
        o[0] = (int16_t)hl_c16(y1 + p);
        o[1] = (int16_t)hl_c16(y2 + q);
        o[2] = (int16_t)hl_c16(y3 + p);
        o[3] = (int16_t)hl_c16(y4 + p);
        o[4] = (int16_t)hl_c16(y5 + q);
        o[5] = (int16_t)hl_c16(y6 + p);
        o[6] = (int16_t)hl_c16(y7 + q);
        o[7] = (int16_t)hl_c16(y0 + q);
        A -= B;
        *mo++ = u >> (RI_HOT + 1u);                     /* (the taps: HALL's wet level over a chord's first 2 s,
                                                         * +1.3 dB: the longer tail, -6 dB here) */
        *so++ = A >> (RI_HOT + 1u);
        ab += u ^ (u >> 31);
        act |= u | A;
    }
    *tep = te;
    *abp += ab;
    return act;
}

/* one tank block: RI_NB samples of s->x in, the taps into mo / so */
static inline __attribute__((always_inline)) void ri_tank(rev_inf_t *s, int32_t *mo, int32_t *so, const rev_ctl_t *c, int32_t xi)
{
    uint32_t i, k, d, m, w = s->w;
    int32_t act = 0, ab = 0, closed = c->freeze == 2, dec, ct, ch = 0;
    int16_t *ap = s->ap;
    /* ---- control: SIZE (the lengths glide), DECAY (the gains), freeze, TONE, the input's gain */
    /* a release: the hold let go (OFF, another TYPE: c->freeze 0 with DECAY still >= 120): the tail fades out in ~2 s
     * (DECAY glides on below 0 to RI_REL, T60 2 s, in ~0.2 s: no step), so silence and a waiting save come soon;
     * DECAY turned down by hand keeps its own law */
    if (!c->freeze && c->decay >= 120) {
        s->rd = s->rd > RI_REL + 2 * (int32_t)RI_CPT ? s->rd - 2 * (int32_t)RI_CPT : RI_REL;
        dec = s->rd;
    } else {
        dec = c->decay > 119 ? 119 : c->decay;
        s->rd = 119;
    }
    if (s->gsz != c->size) {
        for (k = 0; k < RI_NL; k++) s->tlen[k] = (uint16_t)((RI_LEN[k] * (uint32_t)(127 + c->size)) / 254u);
        s->gsz = c->size;
    }
    if (s->xf) ch = 1;                                  /* (a SIZE fade: the gains follow the lengths between) */
    else {                                              /* one fade at a time: a new SIZE waits for its end */
        for (k = 0; k < RI_NL; k++) ch |= s->len[k] != s->tlen[k];
        if (ch) {
            if (!s->len[0] || !s->full || !rev_inf_active(s))   /* (a start, a tank silent: at once) */
                for (k = 0; k < RI_NL; k++) s->len[k] = s->tlen[k];
            else {                                      /* the reads cross-fade from the old lengths to the new */
                for (k = 0; k < RI_NL; k++) {
                    s->lo[k] = s->len[k];
                    s->mI[k] = (uint16_t)(s->mI[k] + s->tlen[k] - s->len[k]);   /* (the latched read moves with it) */
                    s->len[k] = s->tlen[k];
                }
                s->xf = 1, s->xw = 0;
            }
        }
    }
    if (ch || s->gdec != dec) { ri_gains(s, dec); s->gdec = dec; }
    ct = c->freeze ? 0 : c->damp * RI_KD;
    s->c = s->c < ct ? (ct - s->c > 64 ? s->c + 64 : ct) : (s->c - ct > 64 ? s->c - 64 : ct);
    s->ik = (int32_t)(FR_FAR(exp2_neg)((uint32_t)c->damp * RI_KI) >> 1);
    s->ik = s->ik < RI_KMAX ? s->ik : RI_KMAX;   /* (TONE glides in the framework) */
    ct = c->freeze || c->damp <= 60 ? 0 : (c->damp - 60) * RI_KA;
    s->ca = s->ca < ct ? (ct - s->ca > 64 ? s->ca + 64 : ct) : (s->ca - ct > 64 ? s->ca - 64 : ct);
    ct = closed ? 0 : 32768;
    s->fin = s->fin < ct ? (ct - s->fin > RI_FIN_STEP ? s->fin + RI_FIN_STEP : ct)
                         : (s->fin - ct > RI_FIN_STEP ? s->fin - RI_FIN_STEP : ct);
    for (k = 0; k < RI_NDIF; k++) {
        uint32_t s0;
        d = s->apd[k];
#if REV_INF_LITE
        RI_RUNS(d, RI_AP[k], m, s0, 0, ri_dif2(ap + d, s->x + s0, m, !((s->apfull >> k) & 1u));
#else
        RI_RUNS(d, RI_AP[k], m, s0, 0, ri_dif(ap + d, 9830, s->x + s0, m, !((s->apfull >> k) & 1u));
#endif
                if (d + m >= RI_AP[k]) s->apfull |= 1u << k);
        s->apd[k] = (uint16_t)d;
        ap += RI_AP[k];
    }
    /* ---- the line ends */
    for (k = 0; k < RI_NL; k++) {
        uint32_t I, s0, f = 0;
        int32_t g = c->freeze ? 16384 + (s->dq >> 8) : s->g[k];
        int32_t ck = clampi((((16384 - g) * s->c) >> 9) + s->ca, 0, 14000) + RI_CMIN;   /* the damping (Q14) */
        if (!((k ^ s->rr) & RI_RRS) || !s->mI[k]) {       /* the read: every 4th block (LITE: 8th; 2.9 / 5.8 ms) */
            int32_t dep = (int32_t)((RI_MODQ8 * (uint32_t)c->mod) >> 10);   /* (mod: MOD x 16) */
            uint32_t t, r;
            if ((RI_MODM >> k) & 1u) {
                t = (uint32_t)s->len[k] * 256u                                       /* len .. len + 2 dep */
                    + (uint32_t)(dep + ((dep * (osc_sine(s->ph[k] += (RI_RRS + 1u) * RI_CPT * RI_INC[k]) >> 1)) >> 14));
                I = (t - 128u) >> 8;
                r = t - I * 256u;                           /* 128 .. 383: the fraction 0.5 .. 1.5 */
                s->ma[k] = ((256 - (int32_t)r) * 32768) / (256 + (int32_t)r);
            } else
                I = s->len[k] - 1u;                         /* (a whole sample: the oldest row read is len back) */
            s->mI[k] = (uint16_t)I;
            {   /* quiet? (HALL's way: two samples of the line, its last output) */
                int32_t u0, u1;
                d = w + RI_N - I - 1u;
                d = d >= RI_N ? d - RI_N : d;
                r = d + (I >> 1);
                u0 = s->full ? s->ring[d * RI_NL + k] : 0;  /* (before the ring is full: stale rows) */
                u1 = s->full ? s->ring[(r >= RI_N ? r - RI_N : r) * RI_NL + k] : 0;
                if ((u0 < 0 ? -u0 : u0) + (u1 < 0 ? -u1 : u1) + (s->op[k] < 0 ? -s->op[k] : s->op[k]) < RI_LOUD)
                    s->mlo |= (uint8_t)(1u << k);
                else
                    s->mlo &= (uint8_t)~(1u << k);
            }
        }
        I = s->mI[k];
        if (!s->full) {                                     /* rows older than the start read as 0 */
            int32_t v = (int32_t)(I + 1u) - (int32_t)s->age;
            f = v <= 0 ? 0u : v >= (int32_t)RI_NB ? RI_NB : (uint32_t)v;
            for (i = 0; i < f; i++) s->y[i][k] = 0;
        }
        if (f < RI_NB) {
            const int16_t *rb = s->ring + k;
            uint32_t rn = RI_N;
            d = w + f + RI_N - I - 1u;
            d = d >= RI_N ? d - RI_N : d;
            int32_t b0 = (g * (16384 - ck)) >> 14, lo = (s->mlo >> k) & 1u;
            if (s->xf) {                                /* a SIZE fade (a full ring: f 0): the blended rows */
                uint32_t e = d + RI_N + (uint32_t)s->len[k] - s->lo[k];
                FR_FAR(ri_xrows)(ri_xt, rb, d, e >= 2u * RI_N ? e - 2u * RI_N : e >= RI_N ? e - RI_N : e, s->xw);
                rb = ri_xt, rn = RI_NB + 1u, d = 0;
            }
            if ((RI_MODM >> k) & 1u) {
                RI_RUNS(d, rn, m, s0, f, ri_end(rb + d * RI_NL, s->y[s0] + k, m, s->ma[k], b0, ck,
                                                &s->yp[k], &s->op[k], &s->oe[k], lo));
            } else {
                RI_RUNS(d, rn, m, s0, f, ri_endp(rb + d * RI_NL, s->y[s0] + k, m, b0, ck, &s->op[k],
                                                 &s->oe[k], lo));
            }
        }
        if ((RI_LAPM >> k) & 1u) {
            uint32_t n = RI_NDIF + (k >> 1);
            d = s->apd[n];
            RI_RUNS(d, RI_AP[n], m, s0, 0, ri_lap(ap + d, s->y[s0] + k, m, !((s->apfull >> n) & 1u));
                    if (d + m >= RI_AP[n]) s->apfull |= 1u << n);
            s->apd[n] = (uint16_t)d;
            ap += RI_AP[n];
        }
    }
    /* ---- the loop's DC block, on line 0 (0.9 Hz, exact: settles to 0): the mix below moves every line's content on
     * to the next line, so each pass of the 8 goes through it (the Householder alone keeps a DC that sums to 0 across
     * the lines for ever, and a freeze would hold it) */
    {
        int32_t hp = s->dh, he = s->de, *y = &s->y[0][0];
        for (i = 0; i < RI_NB; i++, y += RI_NL) {
            int32_t v = *y - hp + he;
            he = v & ((1 << RI_DCK) - 1);
            hp += v >> RI_DCK;
            *y -= hp;
        }
        s->dh = hp, s->de = he;
    }
    /* ---- the mix (Householder: y_k - S / 4, lossless; line k's into line k - 1) back into the ring, the taps */
    act = FR_FAR(ri_mix)(&s->y[0][0], s->x, s->ring + w * RI_NL, mo, so, &s->te, &ab);
    if (!w) memcpy(s->ring + RI_N * RI_NL, s->ring, RI_NL * sizeof s->ring[0]);
    w += RI_NB;
    s->rr++;
    if (s->xf && (s->xw += RI_NB * RI_XS) >= 16384) s->xf = 0, s->gdec = -1000;   /* (the fade's end: the new
                                                     * lengths' own gains next block) */
    s->w = w >= RI_N ? 0u : w;
    if (!s->full && (s->age += RI_NB) > RI_N + RI_NB) s->full = 1;
    s->quiet = act ? 0u : s->quiet < 0x40000000u ? s->quiet + RI_NB : s->quiet;
    /* the level guard: a held tank (DECAY high, playing on) must not reach the clamps: the input down fast, back
     * slowly */
    act |= xi;                                      /* the taps, the input: all zero for a whole ring = silence */
    ab >>= RI_LNB;
    s->lv += (ab - s->lv) >> (9u - RI_LCPT);        /* 0.37 s */
    if (ab > RI_LV) s->ilim -= s->ilim >> 4;
    else if (s->ilim < 32768) s->ilim += ((32768 - s->ilim) >> 8) + 1;
    /* the held level (DECAY 120..127, the line gains 1 + dq): every product truncates toward zero, so a held tank
     * loses a little each pass (more the quieter it is: -2 dB in 10 min at a normal level, all of it at a quiet one);
     * the make-up (dq, -2^-8 .. +2^-8 a pass) holds it at the held level tgt (the slow beating of a frozen texture too).
     * FREEZE (127, input closed): tgt = the level 2 s after the input has closed (settled); it never changes.
     * SUSTAIN (120..126, input open): tgt rises with what is played (while there is input), up to RI_SUS; there the
     * tank leaks just enough (dq < 0, down to -2^-6 a pass) to make room for the new notes: a gain that moves once a
     * block (no waveshaping: nothing to alias), the old sound fades under the new, the level stays bounded */
    if (!c->freeze) s->tgt = s->dq = s->dqi = 0, s->fm = 0;
    else {
        if (s->fm != 1 + closed) { s->fm = (uint8_t)(1 + closed); s->tgt = closed ? 0 : s->lv > 0 ? s->lv : 1; }
        if (closed && !s->tgt) {                       /* the held level: 3 s after the input has closed (the top, RI_CMIN, gone) */
            if (s->fin) s->hw = 0;
            else if ((s->hw += HL_NC * RI_CPT) >= 66150u) s->tgt = s->lv > 0 ? s->lv : 1;
        }
        else {
            int32_t t = s->tgt, e, d;
            if (!closed && xi > RI_XQ && s->lv > t) t = s->lv < RI_SUS ? s->lv : RI_SUS;
            t = t > 0 ? t : 1;
            e = clampi((s->lv - t) * 1024 / t, -1024, 1024);    /* the error, Q10 of the held level */
            /* PI: the integral holds the level exactly (the loss is steady), the proportional part answers a new
             * note at once; the integral (dqi) stays in the gain's range (no wind-up), the leak acts 8x as strong */
            s->dqi = clampi(s->dqi - e * (int32_t)RI_CPT, closed ? -RI_DQMAX : -RI_DQCUT, RI_DQMAX);
            d = s->dqi - e * (e < 0 ? RI_KP : 8 * RI_KP);
            s->dq = clampi(d, closed ? -RI_DQMAX : -RI_DQCUT, RI_DQMAX);
            s->tgt = t;
        }
    }
    s->gi = (((s->ilim * s->fin) >> 15) * RI_IN) >> 15;
}

FR_RAM __attribute__((noinline)) static void rev_inf_run(rev_inf_t *s, const int32_t *in, int32_t *mid, int32_t *side,
                                                 const rev_ctl_t *c)
{
#if RI_CPT > 1u
    /* RI_CPT control blocks a tank block (its fixed costs once for all of them): each call's input waits in s->x, each
     * call's output is its part of the last tank block (RI_CPT control blocks late); the last call runs the tank after
     * taking its part */
    uint32_t i, h = s->half, o = h * (RI_NB / RI_CPT);
    const int32_t *m = s->m + o, *q = s->s + o;
    s->xi |= FR_FAR(ri_input)(s, in, o);
#if REV_INF_QUARTER
    {
        /* 1:2 up through the same half-band (its odd phase: (3, -25, 150, 150, -25, 3) / 256; the even samples are
         * the tank's own): the images of the tank's band, which a (last + this) / 2 left at 11 kHz - f only ~-25 dB
         * down (a +8 dB band at 10..11.5 kHz over HALL's), now -40 dB and more */
        uint32_t j;
        int32_t bm[5u + RI_NB / RI_CPT], bs[5u + RI_NB / RI_CPT];
        for (i = 0; i < 5u; i++) bm[i] = s->qm[i], bs[i] = s->qs[i];
        for (i = 0; i < RI_NB / RI_CPT; i++) bm[5u + i] = m[i], bs[5u + i] = q[i];
        for (i = 0, j = 0; i < RI_NB / RI_CPT; i++, j += 2u) {
            const int32_t *u = bm + i + 2u, *v = bs + i + 2u;
            mid[j] = u[0], side[j] = v[0];
            mid[j + 1u] = (150 * (u[0] + u[1]) - 25 * (u[-1] + u[2]) + 3 * (u[-2] + u[3]) + 128) >> 8;
            side[j + 1u] = (150 * (v[0] + v[1]) - 25 * (v[-1] + v[2]) + 3 * (v[-2] + v[3]) + 128) >> 8;
        }
        for (i = 0; i < 5u; i++) s->qm[i] = bm[RI_NB / RI_CPT + i], s->qs[i] = bs[RI_NB / RI_CPT + i];
    }
#else
    for (i = 0; i < HL_NC; i++) mid[i] = m[i], side[i] = q[i];
#endif
    if (++h < RI_CPT) s->half = (uint8_t)h;
    else s->half = 0, ri_tank(s, s->m, s->s, c, s->xi), s->xi = 0;
#else
    ri_tank(s, mid, side, c, FR_FAR(ri_input)(s, in, 0));
#endif
}
