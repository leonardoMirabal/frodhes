/* SPDX-License-Identifier: GPL-3.0-only */
/* The DRIVE's TYPEs (included by fx.c, right after the classic drive, fx_drive_classic). P_DRV_SW / FXP_DRV_SW:
 *   0 OFF, 1 FDRV (the classic drive, fx_drive_classic: its loops as they always were), 2 KHLON, 3 TSH8, 4 OHCD,
 *   5 BHLUE (one file each: drv_<name>.c; append-only, the records store the number).
 *
 * fx_drive: OFF and FDRV run fx_drive_classic as before (two tests per block, nothing per sample); a TYPE runs in
 * fx_drive_types, once per block; the framework does the rest the classic drive does for itself: AMOUNT 0 = dry, MIX
 * (dry / wet, a 46 ms glide on dr_w), OFF (the same fade out). One TYPE runs at a time, never two (CPU: private drive
 * plan, the cost gate): a switch while the drive sounds fades the TYPE that runs out (46 ms, as OFF), then the new one
 * in (46 ms, as ON), through the dry signal; from silence (OFF faded, MIX 0, AMOUNT 0) the new TYPE simply starts.
 *
 * A TYPE's file defines (KHLON as the example):
 *   #define DRV_US_KHLON n          its loop's cost, us per 256-sample block, compiled (tools/drive_insns.py, the
 *                                   longest path, x 1.6 us per instruction per sample); CORE + it + DRV_US_BLEND must
 *                                   stay <= DRV_US_MAX, or it does not build
 *   static void drv_khlon_reset(void)                                   its state to 0 (CC 10, and when it starts)
 *   static void drv_khlon_run(const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)   the wet signal of
 *                                   n <= 256 samples (the whole block) into out; out may be in (all wet: in place)
 * and may use the shared helpers below: drv_curve / drv_adaa1 (a generated curve, with first-order antiderivative
 * anti-aliasing), drv_bq (a biquad), drv_tpt1 (a one-pole), drv_glide; its tables come from tools/gen_drive_tables.py
 * (firmware/gen/drive_tables.h). Integers only; the one divide is 32-bit (docs/RULES.md). */
#include "drive_tables.h"

/* Every TYPE's (and this file's) mutable static goes in a section of its own (firmware only), out of the compiler's
 * global merge: merged in, the TYPEs' state split rhodes.c's rh_F / rh_x into two merged blocks and the Rhodes mode
 * loops spilled (22 / 29 -> 24..25 / 32..33 instructions with stack loads; tools/build.py checks them; engine.c eq).
 * DRV_BSS for zero-initialised, DRV_DATA for initialised ones; a new TYPE's statics use them too. */
#ifdef DINGHY_HOST
#define DRV_BSS
#define DRV_DATA
#else
#define DRV_BSS __attribute__((section(".bss.drv")))
#define DRV_DATA __attribute__((section(".data.drv")))
#endif

#define DRV_N 6u                   /* P_DRV_SW values: OFF, FDRV and the four TYPEs */
#define DRV_BLK 256u               /* the most samples a call (fx_process: a block) */
/* The cost of a TYPE, us per 256-sample block, counted whole (tools/drive_insns.py, pi32v2 -Os, ~1.6 us per
 * instruction per sample; FDRV pays none of it):
 *   DRV_US_CORE   this file around the TYPE's loop, once a block: fx_drive's tests, fx_drive_types (the glide, the
 *                 dispatch, one call): compiled, at most ~85 instructions a block (every path summed), ~0.5 us;
 *   DRV_US_<T>    the TYPE's loop (its file), all wet: in place, MIX 127 at rest (the usual case);
 *   DRV_US_BLEND  the dry / wet blend, outside the TYPE, only while it blends (MIX < 127, a glide, a fade): the TYPE
 *                 writes its wet signal to drv_wet, then drv_blend: compiled 32 instructions per 4 samples (8 a
 *                 sample: 2 loads, subtract, 64-bit multiply, 64-bit shift, add, store, 1/4 of the pointers, ramp
 *                 and loop), 12.8 us;
 *   DRV_US_MAX    the budget per TYPE, everything included: 192 us (private drive plan; the user, 2026-10-24:
 *                 120, then 150, then 160, then anti-aliasing on in TSH8, BHLUE and OHCD: 180, just what TSH8's
 *                 anti-aliased kernel needs, 165 us; 0.9.9, KHLON's anti-aliasing on too: 192, what its kernel
 *                 needs, 177 us; FDRV, at 2x now, costs more than any TYPE: FX_US_DRIVE): CORE + <T> + BLEND <= MAX,
 *                 or it does not build; so DRV_US_KERNEL_MAX (177 us, ~110 instructions per sample) for a TYPE's own
 *                 loop. */
#define DRV_US_CORE 2u
#define DRV_US_BLEND 13u
#define DRV_US_MAX 192u
#define DRV_US_KERNEL_MAX (DRV_US_MAX - DRV_US_CORE - DRV_US_BLEND)

typedef struct {
    int32_t amount;                /* AMOUNT 1..127 (0 never reaches a TYPE: dry) */
    int32_t tone;                  /* TONE 0..127 (64: the middle) */
    int32_t row;                   /* the instrument's drive row 0..3 (params.c TYPE_DRIVE_ROW); a TYPE may ignore it */
} drv_ctl_t;

/* The dry / wet blend, after the TYPE, the classic drive's own formula, x + ((y - x) w >> 15) (y at w = 32768, x at
 * w = 0, bit for bit), w ramping by d4 every 4 samples (64 steps a block: 4096 = the classic drive's 46 ms; exact,
 * every w here a multiple of 256). Four samples a pass, on pointers. noinline: one copy, its own count
 * (tools/drive_insns.py drv_blend). */
static __attribute__((noinline)) void drv_blend(int32_t *m, const int32_t *y, uint32_t n, int32_t w, int32_t d4)
{
    int32_t *e = m + (n & ~3u);
    while (m < e) {
        int32_t a = m[0], b = m[1], c = m[2], d = m[3];
        m[0] = a + (int32_t)(((int64_t)(y[0] - a) * w) >> 15);
        m[1] = b + (int32_t)(((int64_t)(y[1] - b) * w) >> 15);
        m[2] = c + (int32_t)(((int64_t)(y[2] - c) * w) >> 15);
        m[3] = d + (int32_t)(((int64_t)(y[3] - d) * w) >> 15);
        m += 4; y += 4; w += d4;
    }
    for (e += n & 3u; m < e; m++, y++) *m += (int32_t)(((int64_t)(*y - *m) * w) >> 15);
}

/* ---- shared helpers ------------------------------------------------------------------------------------------- */

/* A curve: 2 half + 1 points f[] (Q15) at a step of 2^sh over [-X, X], X = half << sh, linearly interpolated; flat
 * beyond its ends. h[] (drive_tables.h, from the same f) is its running integral: h[i] - h[0] = sum (f[j] + f[j + 1]),
 * j < i (h[0] any constant: only differences count; the generator keeps every h >= 0, clear of the ROM window).
 * half, sh are constants after inlining; sh <= 14, |x| < 2^27. */
static inline __attribute__((always_inline)) int32_t drv_curve(const int16_t *f, const uint32_t half, const uint32_t sh,
                                                               int32_t x)
{
    uint32_t u = (uint32_t)x + (half << sh), i;
    if (u >= (2u * half) << sh) return x < 0 ? f[0] : f[2u * half];
    i = u >> sh;
    return f[i] + (((f[i + 1] - f[i]) * (int32_t)(u & ((1u << sh) - 1u))) >> sh);
}
/* 2^(sh + 1) x the integral of the interpolated curve from -X to x, exact (the integral of a line is a parabola:
 * h[i] 2^2sh + 2^(sh+1) f[i] r + (f[i+1] - f[i]) r^2, r = x's offset in its segment), in 64 bits */
static inline __attribute__((always_inline)) int64_t drv_curve_p(const int16_t *f, const int32_t *h, const uint32_t half,
                                                                 const uint32_t sh, int32_t x)
{
    const int64_t s1 = (int64_t)1 << (sh + 1u);           /* (products, not left shifts: the values may be negative) */
    uint32_t u = (uint32_t)x + (half << sh), i;
    int32_t r, a;
    if (u >= (2u * half) << sh) {                         /* beyond the ends: the curve flat, its integral a line */
        if (x < 0) return ((int64_t)h[0] << (2u * sh)) + (int64_t)f[0] * (int32_t)u * s1;
        return ((int64_t)h[2u * half] << (2u * sh)) + (int64_t)f[2u * half] * (int32_t)(u - ((2u * half) << sh)) * s1;
    }
    i = u >> sh;
    r = (int32_t)(u & ((1u << sh) - 1u));
    a = f[i];
    return ((int64_t)h[i] << (2u * sh)) + (int64_t)(a * r) * s1 + (int64_t)((f[i + 1] - a) * r) * r;
}
/* First-order antiderivative anti-aliasing (Parker, Zavalishin, Le Bihan, DAFx 2016): the curve's mean over the
 * segment from the last input to this one, (P(x) - P(xp)) / (x - xp). The difference of the exact integrals is a
 * 64-bit number at most |dx| 2^15 (2^(sh+1) units): for |dx| < 2^16 it is shifted into 32 bits, else both are
 * shifted right until dx fits 16 bits, so the quotient is one 32-bit divide (within 1 LSB of the exact mean; 2 for a
 * step of 2^16 or more). |dx| <= eps (ill-conditioned, or not worth a divide): the curve at the midpoint. Half a
 * sample of delay and a gentle top roll-off (-2.2 dB at 10 kHz) come with it. Measured (tests/py/test_drive_core.py):
 * the same as ADAA-1 in float; on tanh at x3, 7 / 9 / 11 dB less folded energy than the plain curve at 2.1 / 4.2 /
 * 6.3 kHz. pi32v2 -Os (tools/drive_insns.py): 46 instructions per sample on the common path, the plain curve 10. */
typedef struct { int32_t x; int64_t p; } drv_adaa_t;     /* the last input and its integral (drv_adaa_reset) */
static inline __attribute__((always_inline)) int32_t drv_adaa1(drv_adaa_t *s, const int16_t *f, const int32_t *h,
                                                               const uint32_t half, const uint32_t sh, const int32_t eps,
                                                               int32_t x)
{
    int64_t p = drv_curve_p(f, h, half, sh, x);
    int32_t dx = x - s->x, y;
    if (dx > eps || dx < -eps) {
        int64_t dp = p - s->p;
        if (dx < 65536 && dx > -65536) y = (int32_t)(dp >> (sh + 1u)) / dx;
        else {
            uint32_t sft = 16u - (uint32_t)__builtin_clz((uint32_t)(dx < 0 ? -dx : dx));
            y = (int32_t)(dp >> (sft + sh + 1u)) / (dx >> sft);
        }
    } else
        y = drv_curve(f, half, sh, x - (dx >> 1));
    s->x = x; s->p = p;
    return y;
}
static inline void drv_adaa_reset(drv_adaa_t *s, const int16_t *f, const int32_t *h, const uint32_t half,
                                  const uint32_t sh)
{
    s->x = 0; s->p = drv_curve_p(f, h, half, sh, 0);
}

/* A biquad, direct form 1: coefficients Q28 (|b| < 8; a1, a2 negated: y = b0 x + b1 x1 + b2 x2 + a1 y1 + a2 y2), the
 * signal in any format up to 2^28, 64-bit accumulate, rounded (no DC creep). tools/gen_drive_tables.py bq_q28 makes the
 * rows from an analog prototype (bilinear, prewarped). The FILTER's TPT state-variable stage (fx.c svf_coef / svf_tick)
 * is there too, for a filter that moves. */
typedef struct { int32_t b0, b1, b2, a1, a2; } drv_bq_t;
typedef struct { int32_t x1, x2, y1, y2; } drv_bqs_t;
static inline __attribute__((always_inline)) int32_t drv_bq(const drv_bq_t *c, drv_bqs_t *s, int32_t x)
{
    int32_t y = (int32_t)(((int64_t)c->b0 * x + (int64_t)c->b1 * s->x1 + (int64_t)c->b2 * s->x2 + (int64_t)c->a1 * s->y1 +
                           (int64_t)c->a2 * s->y2 + (1 << 27)) >> 28);
    s->x2 = s->x1; s->x1 = x; s->y2 = s->y1; s->y1 = y;
    return y;
}
/* A one-pole, topology-preserving (trapezoidal): G = g / (1 + g) in Q30, g = tan(pi fc / fs) (gen_drive_tables.py
 * tpt_g30); returns the low-pass, the high-pass is x - lp. Exact gain at any fc, stable while G moves. */
static inline __attribute__((always_inline)) int32_t drv_tpt1(int32_t *s, int32_t G, int32_t x)
{
    int32_t v = (int32_t)(((int64_t)(x - *s) * G + (1 << 29)) >> 30), y = v + *s;
    *s = y + v;
    return y;
}
/* a value toward its target by at most step (coefficients that move with a knob: once per sub-block) */
static inline int32_t drv_glide(int32_t v, int32_t tgt, int32_t step)
{
    return v < tgt ? (tgt - v > step ? v + step : tgt) : (v - tgt > step ? v - step : tgt);
}

/* ---- 2x oversampling around a clipper (FDRV's curve runs at 88.2 kHz; the TYPEs stay at 1x with their ADAA-1: at 2x
 * their kernels would cost ~2x + ~100 us, over the core-0 budget, docs/changes/fxq-drive.md). A polyphase IIR
 * half-band (tools/drive_gen/halfband.py): two branches of first-order allpass sections in z^-2,
 * H = (A0(z^2) + z^-1 A1(z^2)) / 2. Up: both branches on the input, interleaved (even = A0, odd = A1); down: the even
 * samples through A0, the odd ones (one 2x sample late) through A1, the mean. Each section y = x1 + a (x - y1) >> 30,
 * a in Q30 (0 < a < 1): one multiply; a zero input rests at exact 0 (the floor of a (0 - y1) brings |y| down every
 * sample). The phase is not linear near the top, which a clipper's output does not mind. DRV_HB_NC coefficients:
 *   4 (the default): two sections a branch, flat (< 0.0001 dB) to 18.96 kHz, -47.7 dB from 25.14 kHz; group delay 1.8
 *     samples at 88.2 kHz each way (~42 us in all at 1 kHz); up 25, down 34 instructions a sample (pi32v2 -Os);
 *   6: three a branch, -71.5 dB from 25.14 kHz, ~60 us; up 45, down 49: FDRV ~56 us a block more. Measured on FDRV
 *     (tests/py/test_drive_os.py, the folded energy's worst note, AMOUNT 127): the same within 0.3 dB from -6 dBFS up
 *     (the 2x fold itself dominates), 5.6 dB more at -12 dBFS with 6 (-64.1, not -58.5): both far under 1x's -30.6.
 *     6 does not fit core 0's budget with the EQ (VIBES' worst estimated 4520 us, the gate 4500).
 * The 2x scratch is shared (one TYPE runs at a time: KHLON keeps its block scratch there). */
#ifndef DRV_HB_NC
#define DRV_HB_NC 4
#endif
#define DRV_HB4_0 155715225                             /* (halfband.py q30(4): branch 0 = even indices) */
#define DRV_HB4_1 479616812
#define DRV_HB4_2 770309751
#define DRV_HB4_3 981081071
#define DRV_HB6_0 78564935                              /* (q30(6)) */
#define DRV_HB6_1 273987290
#define DRV_HB6_2 504766067
#define DRV_HB6_3 711052173
#define DRV_HB6_4 875293881
#define DRV_HB6_5 1009571710
#if DRV_HB_NC == 4
#define DRV_HB00 DRV_HB4_0
#define DRV_HB01 DRV_HB4_2
#define DRV_HB02 0
#define DRV_HB10 DRV_HB4_1
#define DRV_HB11 DRV_HB4_3
#define DRV_HB12 0
#elif DRV_HB_NC == 6
#define DRV_HB00 DRV_HB6_0
#define DRV_HB01 DRV_HB6_2
#define DRV_HB02 DRV_HB6_4
#define DRV_HB10 DRV_HB6_1
#define DRV_HB11 DRV_HB6_3
#define DRV_HB12 DRV_HB6_5
#else
#error "DRV_HB_NC: 4 or 6"
#endif
#define DRV_HBM(a, v) ((int32_t)(((int64_t)(a) * (v)) >> 30))
typedef struct { int32_t x, y0, y1, y2; } drv_ap_t;     /* a branch: its last input, each section's last output */
typedef struct { drv_ap_t u0, u1, d0, d1; int32_t od; } drv_os_t;   /* od: the last odd 2x sample (the down branch 1's) */
static DRV_BSS union {
    int32_t x2[2u * DRV_BLK];                          /* the 2x signal (FDRV, TSH8, OHCD, BHLUE) */
    struct { int32_t tmp[DRV_BLK], u[DRV_BLK]; } kh;   /* KHLON's block scratch (drv_khlon.c kh_tmp, kh_u) */
} drv_scr;
/* a branch's sections on x (macro: the states in the caller's registers) */
#if DRV_HB_NC == 4
#define DRV_AP3(a0, a1, a2, xp, y0, y1, y2, x, out) do {                                                     \
        int32_t p_ = (xp) + DRV_HBM(a0, (x) - (y0));                                                         \
        (out) = (y0) + DRV_HBM(a1, p_ - (y1)); (y0) = p_; (y1) = (out); (void)(y2);                          \
    } while (0)
#else
#define DRV_AP3(a0, a1, a2, xp, y0, y1, y2, x, out) do {                                                     \
        int32_t p_ = (xp) + DRV_HBM(a0, (x) - (y0)), q_ = (y0) + DRV_HBM(a1, p_ - (y1));                     \
        (out) = (y1) + DRV_HBM(a2, q_ - (y2)); (y0) = p_; (y1) = q_; (y2) = (out);                          \
    } while (0)
#endif
/* n samples in -> 2n out (out may not be in) */
static __attribute__((noinline)) void drv_up2(drv_os_t *s, const int32_t *in, int32_t *out, uint32_t n)
{
    int32_t xp = s->u0.x, a0 = s->u0.y0, a1 = s->u0.y1, a2 = s->u0.y2, b0 = s->u1.y0, b1 = s->u1.y1, b2 = s->u1.y2;
    const int32_t *e = in + n;
    while (in < e) {
        int32_t x = *in++, r0, r1;
        DRV_AP3(DRV_HB00, DRV_HB01, DRV_HB02, xp, a0, a1, a2, x, r0);
        DRV_AP3(DRV_HB10, DRV_HB11, DRV_HB12, xp, b0, b1, b2, x, r1);
        xp = x;
        out[0] = r0; out[1] = r1; out += 2;
    }
    s->u0.x = s->u1.x = xp; s->u0.y0 = a0; s->u0.y1 = a1; s->u0.y2 = a2; s->u1.y0 = b0; s->u1.y1 = b1; s->u1.y2 = b2;
}
/* 2n samples in -> n out, (A0 + A1) >> (sh + 1), rounded (sh: the extra bits the 2x signal carries) */
static __attribute__((noinline)) void drv_dn2(drv_os_t *s, const int32_t *in, int32_t *out, uint32_t n, uint32_t sh)
{
    int32_t ep = s->d0.x, a0 = s->d0.y0, a1 = s->d0.y1, a2 = s->d0.y2;
    int32_t op = s->d1.x, b0 = s->d1.y0, b1 = s->d1.y1, b2 = s->d1.y2, od = s->od, rnd = 1 << sh;
    int32_t *e = out + n;
    while (out < e) {
        int32_t x = in[0], r0, r1;
        DRV_AP3(DRV_HB00, DRV_HB01, DRV_HB02, ep, a0, a1, a2, x, r0);
        DRV_AP3(DRV_HB10, DRV_HB11, DRV_HB12, op, b0, b1, b2, od, r1);
        ep = x; op = od; od = in[1]; in += 2;
        *out++ = (r0 + r1 + rnd) >> (sh + 1u);
    }
    s->d0.x = ep; s->d0.y0 = a0; s->d0.y1 = a1; s->d0.y2 = a2;
    s->d1.x = op; s->d1.y0 = b0; s->d1.y1 = b1; s->d1.y2 = b2; s->od = od;
}

/* FDRV's shaper at 2x (fx.c dr_classic; 0.9.9: it ran at 1x with no anti-aliasing: a hard-driven C7 folded partials
 * 36 dB under the note, A7 24 dB; build/fx-audit, tests/py/test_drive_os.py). Per sample the high-pass and the gain as
 * before (gq Q8, ramping by dgq), clamped to +-2^20 (32 x the curve's flat ends: the clamp is never heard) and carried
 * with 6 more bits; up to 2x; the curve (softclip) less b0, with 8 more bits; down; -> drv_wet, the
 * shaper's output at 1x (Q15), for dr_classic's low-passes, make-up, TONE and MIX. A zero input ends in exact 0 (the
 * high-pass's fixed point aside: fx_drive_classic). */
#define DR_OS_IN 6u                                     /* the 2x input's extra bits */
#define DR_OS_OUT 8u                                    /* the 2x output's */
/* DR_YS_X (fx.c): the 1x shaper output's (ys), kept through dr_classic's low-passes to the make-up's one rounding */
#ifndef DR_OS_ADAA
#define DR_OS_ADAA 0                                    /* 1: the curve with ADAA-1 at 2x (dr_aa_mean): -59.5 dB, not
                                                        * -38.5, hard-driven at -6 dBFS (half-band 6), but +33
                                                        * instructions a 2x sample (~105 us a block): over core 0's
                                                        * budget (0.9.9) */
#endif
#define DR_EPS 16                                       /* ADAA: |dx| up to this, the curve at the midpoint */
static DRV_BSS drv_os_t dr_os;
static DRV_BSS int32_t drv_wet[DRV_BLK];   /* the TYPE's wet signal while it blends; FDRV's 1x shaper output */
#if DR_OS_ADAA
static DRV_BSS int32_t dr_aa_x, dr_aa_p;               /* ADAA's last input and its P / 2^13 (x bias at rest) */
/* ADAA-1 on FX_TANH without a divide or a 64-bit integral. The curve's input is held to its table (+-4, 2^17 - 1: the
 * curve is flat beyond, so the curve itself is unchanged; a step from beyond one end averages the end's flat part
 * as if it began at the end). Its integral P / 2^13 (drv_curve_p's units) in 32 bits, modulo 2^32: a difference of
 * two is exact while |dx| < 2^18 (|dP| / 2^13 <= |dx| 2^13), here always. The quotient as a product with 1 / |dx|:
 * the first guess from DRV_AA_RCP (11 bits, by count-leading-zeros), one Newton step (22 bits). The integral's floors
 * (3 a value) make an error of 8 / |dx| LSB at most; |dx| <= DR_EPS: the curve at the midpoint. Within 1 LSB of
 * drv_adaa1's divide (build/fxq aa_chk: 2e7 random pairs). (drv_adaa1's 32-bit divide, of unknown latency on the
 * pi32v2, twice a sample at 2x: not taken.) */
#define DR_XMAX ((1 << 17) - 1)
/* the tables through pointers read once a block (volatile: held in registers through the loop, not rebuilt each
 * sample, as drv_ohcd.c's) */
static DRV_DATA const int16_t *volatile dr_ftp = FX_TANH;
static DRV_DATA const int32_t *volatile dr_htp = DRV_TANH_H;
static DRV_DATA const uint16_t *volatile dr_rcp = DRV_AA_RCP - (1u << (DRV_AA_RB - 1));   /* (from 2^(RB-1)) */
static DRV_DATA volatile int32_t dr_xmax = DR_XMAX;
static inline __attribute__((always_inline)) int32_t dr_p13(const int16_t *f, const int32_t *h, int32_t x)
{                                                       /* P(x) / 2^13 mod 2^32, |x| <= DR_XMAX */
    uint32_t u = (uint32_t)x + (128u << 10), i = u >> 10, r = u & 1023u;
    int32_t f0 = f[i];
    return (int32_t)(((uint32_t)h[i] << 7) + (uint32_t)((f0 * (int32_t)r) >> 2) +
                     (uint32_t)((((f[i + 1] - f0) * (int32_t)r) * (int32_t)r) >> 13));
}
static inline __attribute__((always_inline)) int32_t dr_aa_mean(const int16_t *f, const int32_t *h, const uint16_t *rcp,
                                                                 int32_t xm, int32_t x, int32_t *xp, int32_t *pp)
{
    int32_t p, dx, y;
    uint32_t ad;
    x = x > -xm ? x : -xm;
    x = x < xm ? x : xm;
    p = dr_p13(f, h, x);
    dx = x - *xp;
    ad = (uint32_t)(dx < 0 ? -dx : dx);
    if (__builtin_expect(ad > DR_EPS, 1)) {
        uint32_t nz = (uint32_t)__builtin_clz(ad), m = (ad << nz) >> 16;   /* 2^15 <= m < 2^16 */
        int32_t r0 = rcp[m >> (16 - DRV_AA_RB)];           /* ~2^31 / m (the bucket's middle, 11 bits) */
        int32_t e = (int32_t)(0x80000000u - m * (uint32_t)r0);   /* 2^31 (1 - m r0 / 2^31): |e| < 2^21 */
        int32_t r1 = (r0 << 14) + ((r0 * (e >> 6)) >> 11);  /* one Newton step: ~2^45 / m, to 2^-16 (m's own) */
        int32_t d = (int32_t)((uint32_t)p - (uint32_t)*pp);
        y = (int32_t)(((int64_t)(dx < 0 ? -d : d) * r1) >> 32) >> (27u - nz);
    } else
        y = drv_curve(f, 128u, 10u, x - (dx >> 1));
    *xp = x; *pp = p;
    return y;
}
#endif
static void dr_os_rest(int32_t bias)                    /* FDRV's 2x state to rest (CC 10: fx_reset_state, the
                                                        * silence reset): the curve's input at rest is the bias, so
                                                        * its first mean is curve(bias) = b0, and the output 0 (from
                                                        * x = 0 it made a ~250 LSB click each time: the 2x bring-up) */
{
    memset(&dr_os, 0, sizeof dr_os);
#if DR_OS_ADAA
    dr_aa_x = bias; dr_aa_p = dr_p13(FX_TANH, DRV_TANH_H, bias);
#else
    (void)bias;
#endif
    dr_os_ok = 1;
}
/* softclip on the input's DR_OS_IN more bits, out with DR_OS_OUT more: its table read between entries at 16 bits, not
 * 10 (the same curve; c1-int: the 6 bits were cut before it, its interpolation cut again, then shifted up) */
static inline __attribute__((always_inline)) int32_t dr_curve_f(int32_t v)
{
    uint32_t u, i;
    int32_t f;
    v = v > -(131072 << DR_OS_IN) ? v : -(131072 << DR_OS_IN);
    v = v < (131071 << DR_OS_IN) ? v : (131071 << DR_OS_IN);
    u = (uint32_t)(v + (131072 << DR_OS_IN)); i = u >> (10u + DR_OS_IN); f = (int32_t)(u & ((1u << (10u + DR_OS_IN)) - 1u));
    return FX_TANH[i] * (1 << DR_OS_OUT) + (((FX_TANH[i + 1] - FX_TANH[i]) * f) >> (10u + DR_OS_IN - DR_OS_OUT));
}
__attribute__((noinline)) static void dr_os_curve(int32_t *x, uint32_t n2, int32_t bias, int32_t b0)   /* in place */
{
    int32_t *e = x + n2;
#if DR_OS_ADAA
    const int16_t *f = dr_ftp;
    const int32_t *h = dr_htp;
    const uint16_t *rcp = dr_rcp;
    int32_t xp = dr_aa_x, pp = dr_aa_p, xm = dr_xmax;
    for (; x < e; x++) *x = (dr_aa_mean(f, h, rcp, xm, (*x >> DR_OS_IN) + bias, &xp, &pp) - b0) * (1 << DR_OS_OUT);
    dr_aa_x = xp; dr_aa_p = pp;
#else
    int32_t z0 = dr_curve_f(bias * (1 << DR_OS_IN));     /* (b0 at this precision: 0 in, exact 0 out) */
    (void)b0;
    for (; x < e; x++) *x = dr_curve_f(*x + bias * (1 << DR_OS_IN)) - z0;
#endif
}
static const int32_t *dr_os2(const int32_t *m, uint32_t n, int32_t gq, int32_t dgq, int32_t bias, int32_t b0)
{
    int32_t *y = drv_wet, *x2 = drv_scr.x2, hp = dr_hp;
    uint32_t i;
    if (!dr_os_ok) dr_os_rest(bias);
    if (n > DRV_BLK) n = DRV_BLK;
    for (i = 0; i < n; i++) {
        int32_t x0 = m[i], x, d;
        gq += dgq;
        d = x0 - ((hp + 64) >> 7);                      /* (hp in Q7, 0.9.10: in whole LSB, hp += (x0 - hp + 64) >> 7
                                                        * moved only once x0 was 64 LSB away: a quiet bass (k28 v20,
                                                        * ~-55 dBFS) came out of a staircase at H3 -15 dB) */
        hp += d;
        x = (int32_t)(((int64_t)dr_cl(d) * (gq >> 8)) >> (12u - DR_OS_IN));   /* (the 6 bits from the product, not
                                                        * zeros: c1-int, the tail's staircase) */
        x = x > -(1 << (20 + DR_OS_IN)) ? x : -(1 << (20 + DR_OS_IN));
        y[i] = x < (1 << (20 + DR_OS_IN)) ? x : 1 << (20 + DR_OS_IN);
    }
    dr_hp = hp;
    drv_up2(&dr_os, y, x2, n);
    dr_os_curve(x2, 2u * n, bias, b0);
    drv_dn2(&dr_os, x2, y, n, DR_OS_OUT - DR_YS_X);   /* (ys keeps DR_YS_X of them: dr_classic's low-passes) */
    return y;
}

#if FR_C1
/* dr_os2 for a chunk (fx.c fxc_o / fxc_e): samples fxc_o .. fxc_e of the block into drv_wet (indexed from the block's
 * start, as the loops read it), the gain's ramp from the block's start */
static const int32_t *dr_os2_c(const int32_t *m, int32_t gq, int32_t dgq, int32_t bias, int32_t b0)
{
    int32_t *y = drv_wet, *x2 = drv_scr.x2, hp = dr_hp;
    uint32_t i, o = fxc_o, n = fxc_e - o;
    if (!dr_os_ok) dr_os_rest(bias);
    gq += dgq * (int32_t)o;
    for (i = o; i < fxc_e; i++) {
        int32_t x0 = m[i], x, d;
        gq += dgq;
        d = x0 - ((hp + 64) >> 7);                      /* (dr_os2's hp in Q7: the two cores' chunks as one core) */
        hp += d;
        x = (int32_t)(((int64_t)dr_cl(d) * (gq >> 8)) >> (12u - DR_OS_IN));
        x = x > -(1 << (20 + DR_OS_IN)) ? x : -(1 << (20 + DR_OS_IN));
        y[i] = x < (1 << (20 + DR_OS_IN)) ? x : 1 << (20 + DR_OS_IN);
    }
    dr_hp = hp;
    drv_up2(&dr_os, y + o, x2, n);
    dr_os_curve(x2, 2u * n, bias, b0);
    drv_dn2(&dr_os, x2, y + o, n, DR_OS_OUT - DR_YS_X);
    return y;
}
#endif

/* ---- the TYPEs (one file each) --------------------------------------------------------------------------------- */
#ifndef DRV_FILE_KHLON                 /* (a test build may put another file in a TYPE's place: tests/c/drv_demo.c) */
#define DRV_FILE_KHLON "drv_khlon.c"
#endif
#ifndef DRV_FILE_TSH8
#define DRV_FILE_TSH8 "drv_tsh8.c"
#endif
#ifndef DRV_FILE_OHCD
#define DRV_FILE_OHCD "drv_ohcd.c"
#endif
#ifndef DRV_FILE_BHLUE
#define DRV_FILE_BHLUE "drv_bhlue.c"
#endif
#include DRV_FILE_KHLON
#include DRV_FILE_TSH8
#include DRV_FILE_OHCD
#include DRV_FILE_BHLUE

#define DRV_US_T(k) (DRV_US_CORE + (k))                 /* a TYPE's cost all wet (fx_cost_us) */
static const uint16_t DRV_US[DRV_N] = {0, 0, DRV_US_T(DRV_US_KHLON), DRV_US_T(DRV_US_TSH8), DRV_US_T(DRV_US_OHCD),
                                       DRV_US_T(DRV_US_BHLUE)};
_Static_assert(DRV_US_KHLON <= DRV_US_KERNEL_MAX && DRV_US_TSH8 <= DRV_US_KERNEL_MAX &&
               DRV_US_OHCD <= DRV_US_KERNEL_MAX && DRV_US_BHLUE <= DRV_US_KERNEL_MAX,
               "a DRIVE TYPE over its CPU budget (DRV_US_MAX, the framework and the blend included)");
/* once at boot, from the main loop (frodhes.c app_main, the settings applied): the tables a TYPE builds in RAM, whole,
 * so no audio block ever builds one whole (a TYPE that runs before this passes its input through) */
static void drv_boot(void)
{
#if BHLUE_AA
    if (!bh_gf_ok) bh_gf_build();
#endif
    if (!ts_ok) ts_first(fx_p[FXP_DRIVE]);              /* (TSH8: its first diode table, at the AMOUNT set) */
}
static DRV_BSS int32_t drv_rlast;                 /* the TYPE's last output sample (drv_rest_ramp) */
static __attribute__((noinline)) void drv_reset(uint32_t t)
{
    switch (t) {
    case 2: drv_khlon_reset(); break;
    case 3: drv_tsh8_reset(); break;
    case 4: drv_ohcd_reset(); break;
    case 5: drv_bhlue_reset(); break;
    default: break;                                       /* (FDRV keeps its state, as it always did) */
    }
    drv_rlast = 0;
}
/* the rest step (0.9.10): on zero input a TYPE's rounding holds a fixed point, a DC of a few tens of LSB, until its
 * states stop moving and it rests (KHLON kh_rest, ...): the DC dropped to 0 in one sample, a tick after every last note
 * (F06 DRIVEN 77: C1 +21 dB re 0.9.9's key-up; 0.9.10's shorter damper brought it inside 300 ms of the key-up). A
 * block of zero input whose output is 0 at both ends after a last sample that was not: the DC ramps from that sample
 * to 0 over the block instead (only there: while anything sounds, every sample as it was). drv_rlast: the last output */
static void drv_rest_ramp(int32_t *out, uint32_t n, int32_t z, int32_t *last)
{
    int32_t L = *last;
    if (L && z && !out[0] && !out[n - 1u]) {
        uint32_t i;
        for (i = 0; i < n; i++) out[i] += L * (int32_t)(n - 1u - i) / (int32_t)n;   /* (|L| < 2^16: no overflow) */
    }
    *last = out[n - 1u];
}
#if FR_C1
/* drv_rest_ramp for a chunk (fx.c fxc_o / fxc_e): its tests read a chunk's own samples only, when it runs (in[n / 2],
 * in[n - 1] and out[n - 1] are later chunks': not rendered yet, and with core 1's chunks taken late or merged the
 * whole-block test read other samples, the output timing-dependent; it also added the ramp to later chunks' input).
 * The ramp starts at the first chunk (the last block ended on L != 0, in[0] and out[0] zero), goes on while in[n / 2]
 * and in[n - 1] are zero as their chunks come, and stops at out[n - 1] != 0 (its chunk unramped): wherever all five
 * tests pass, as one core's, sample for sample; one core would not have ramped a block whose later test fails (a
 * note starting in it), two cores ramp its chunks before that one (L < 2^16: a DC of a few LSB fading under it) */
static int32_t drv_rr_l;
static uint8_t drv_rr_on;
static void drv_rest_ramp_c(int32_t *out, uint32_t n, int32_t z)
{
    uint32_t i, o = fxc_o, e = fxc_e;
    if (FXC_1) { drv_rr_l = drv_rlast; drv_rr_on = drv_rr_l && z && !out[0]; }
    else if (!z) drv_rr_on = 0;
    if (FXC_Z && out[n - 1u]) drv_rr_on = 0;
    if (drv_rr_on)
        for (i = o; i < e; i++) out[i] += drv_rr_l * (int32_t)(n - 1u - i) / (int32_t)n;   /* (as drv_rest_ramp) */
    if (FXC_Z) drv_rlast = out[n - 1u];
}
#endif
static void drv_run(uint32_t t, const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)   /* t >= 2 */
{
#if FR_C1
    int32_t z = 1;                                      /* (this chunk's samples of the three: before the run) */
    if (FXC_1) z &= !in[0];
    if (fxc_o <= (n >> 1) && (n >> 1) < fxc_e) z &= !in[n >> 1];
    if (FXC_Z) z &= !in[n - 1u];
#else
    int32_t z = !(in[0] | in[n >> 1] | in[n - 1u]);     /* (before an in-place run overwrites in) */
#endif
    switch (t) {
    case 2: drv_khlon_run(in, out, n, c); break;
    case 3: drv_tsh8_run(in, out, n, c); break;
    case 4: drv_ohcd_run(in, out, n, c); break;
    case 5: drv_bhlue_run(in, out, n, c); break;
    default: break;
    }
#if FR_C1
    drv_rest_ramp_c(out, n, z);
#else
    drv_rest_ramp(out, n, z, &drv_rlast);
#endif
}

/* ---- the dispatch ---------------------------------------------------------------------------------------------- */
static DRV_DATA uint8_t drv_on = 1; /* ISR: the TYPE that runs (1..DRV_N-1); it changes only while the drive is silent */
/* CC 10 (frodhes.c, with fx_reset_req): every TYPE's state to rest, done here at the next TYPE block, not in
 * fx_reset_state: nothing of the TYPEs may sit in app_audio (its register allocation, the Rhodes mode loops: a call
 * there moved them 22 / 29 -> 25 / 32 instructions with stack loads) */
static DRV_BSS volatile uint8_t drv_rst_req;


/* TYPE drv_on (2..), switch sw (0: OFF, fading out): the TYPE once over the block; all wet (dr_w = wt = 32768): in
 * place, nothing else; else into drv_wet, then the blend with dr_w ramping toward wt by 64 every 4 samples (46 ms,
 * as the classic drive; 4096 a block). The TYPE's wet shares are multiples of 256 (MIX v: (258 v + 128) & ~255), so
 * the ramp's step is exact and every block ends on its value. */
#if FR_C1
/* fx_drive_types, drv_switch's use and fx_drive for a chunk: the switch, the TYPE's settings and the blend's ramp at the
 * first chunk (dr_sal set there: dr_w's value at the block's end, which fx_spread's first chunk reads); the TYPE's run
 * gets the chunk (drv_<name>_run with fxc_o / fxc_e) */
static struct { int32_t sw, w0, w1, d4; uint8_t rest, all; drv_ctl_t c; } drv_c;
static __attribute__((noinline)) void fx_drive_types(int32_t *m, uint32_t n, int32_t sw)
{
    uint32_t o = fxc_o, k = fxc_e - o;
    if (FXC_1) {
        int32_t d = fx_p[FXP_DRIVE], mv = fx_p[FXP_DRV_MIX], wt = !sw ? 0 : mv >= 127 ? 32768 : (mv * 258 + 128) & ~255;
        if (drv_rst_req) { uint32_t t; drv_rst_req = 0; for (t = 2u; t < DRV_N; t++) drv_reset(t); }
        if (!d) { wt = 0; d = 1; }
        dr_w &= ~255;
        drv_c.rest = !wt && !dr_w;
        if (drv_c.rest) return;
        drv_c.c.amount = d; drv_c.c.tone = fx_p[FXP_DRV_TONE]; drv_c.c.row = fx_p[FXP_DRIVE_ROW] & 3;
        drv_c.all = dr_w == 32768 && wt == 32768;
        drv_c.w0 = dr_w; drv_c.w1 = drv_glide(dr_w, wt, (int32_t)(16u * n));
        drv_c.d4 = (drv_c.w1 - drv_c.w0) >> 6;          /* (n: DRV_BLK) */
    }
    if (drv_c.rest) return;
    if (drv_c.all) { drv_run(drv_on, m, m, n, &drv_c.c); return; }
    drv_run(drv_on, m, drv_wet, n, &drv_c.c);
    drv_blend(m + o, drv_wet + o, k, drv_c.w0 + drv_c.d4 * (int32_t)(o >> 2), drv_c.d4);
    if (FXC_Z) dr_w = drv_c.w1;
}
static __attribute__((noinline)) int32_t drv_switch(uint32_t t)
{
    if (t >= DRV_N) t = 1u;
    if (t == drv_on) return (int32_t)t;
    if (dr_w) return 0;
    drv_on = (uint8_t)t;
    drv_reset(t);
    dr_ton = 0; dr_tg = 4096;
    return (int32_t)t;
}
static inline void fx_drive(int32_t *m, uint32_t n)
{
    static int32_t sw;
    if (FXC_1) {
        sw = fx_p[FXP_DRV_SW];
        if (sw && sw != (int32_t)drv_on) sw = drv_switch((uint32_t)sw);
    }
    if (drv_on == 1u) { fx_drive_classic(m, n, sw); if (FXC_1) dr_sal = dr_c.w_end; }
    else { fx_drive_types(m, n, sw); dr_sal = 0; }
}
#else
static __attribute__((noinline)) void fx_drive_types(int32_t *m, uint32_t n, int32_t sw)
{
    int32_t d = fx_p[FXP_DRIVE], mv = fx_p[FXP_DRV_MIX], wt = !sw ? 0 : mv >= 127 ? 32768 : (mv * 258 + 128) & ~255;
    int32_t w0, w1;
    drv_ctl_t c;
    if (drv_rst_req) { uint32_t t; drv_rst_req = 0; for (t = 2u; t < DRV_N; t++) drv_reset(t); }   /* (CC 10) */
    if (!d) { wt = 0; d = 1; }                           /* AMOUNT 0: dry, faded out (as the classic drive) */
    dr_w &= ~255;                                        /* (from FDRV's glide: onto the TYPE's grid) */
    if (!wt && !dr_w) return;                            /* off, or MIX 0: the dry signal, untouched */
    if (n > DRV_BLK) n = DRV_BLK;
    c.amount = d; c.tone = fx_p[FXP_DRV_TONE]; c.row = fx_p[FXP_DRIVE_ROW] & 3;
    if (dr_w == 32768 && wt == 32768) { drv_run(drv_on, m, m, n, &c); return; }   /* all wet */
    w0 = dr_w; w1 = drv_glide(w0, wt, (int32_t)(16u * n));
    drv_run(drv_on, m, drv_wet, n, &c);
    drv_blend(m, drv_wet, n, w0, n == DRV_BLK ? (w1 - w0) >> 6 : n >= 4u ? (w1 - w0) / (int32_t)(n >> 2) : 0);
    dr_w = w1;
}
/* the TYPE chosen, t (1..), is not the one that runs: it starts at once while the drive is silent; else the switch
 * the running TYPE sees is OFF (0) until it has faded out, and t starts (from its reset state) in the block after */
static __attribute__((noinline)) int32_t drv_switch(uint32_t t)
{
    if (t >= DRV_N) t = 1u;
    if (t == drv_on) return (int32_t)t;
    if (dr_w) return 0;                                  /* fading out first (AMOUNT 0 fades out too) */
    drv_on = (uint8_t)t;
    drv_reset(t);
    dr_ton = 0; dr_tg = 4096;                            /* (FDRV's shelf at rest, as its own OFF leaves it) */
    return (int32_t)t;
}
/* OFF / FDRV: the classic drive, exactly as before; a TYPE: fx_drive_types. Inlined in fx_process (one caller): on
 * FDRV the framework adds a load and two tests a block. fx_drive_classic is left to the inliner, as the release base
 * had fx_drive: forced noinline it moved app_audio's registers and the Rhodes mode loops went 22 / 29 -> 25 / 31
 * instructions with 3 stack loads each (build.py's check, e9eb294 merged) */
static inline void fx_drive(int32_t *m, uint32_t n)
{
    int32_t sw = fx_p[FXP_DRV_SW];
    if (sw && sw != (int32_t)drv_on) sw = drv_switch((uint32_t)sw);
    if (drv_on == 1u) { fx_drive_classic(m, n, sw); dr_sal = dr_w; }   /* (FDRV's wet share: fx.c fx_spread) */
    else { fx_drive_types(m, n, sw); dr_sal = 0; }
}
#endif
/* the drive's cost (fx_cost_us, only while it runs): FDRV fdrv (FX_US_DRIVE: 248, whatever its settings); a TYPE whole: DRV_US[]
 * all wet (MIX 127, at rest), + DRV_US_BLEND while it blends (MIX < 127, gliding, a fade); while a switch waits, the
 * larger of the two (the one fading out, the one to fade in, blending: one runs at a time) */
static uint32_t drv_cost_us(uint32_t fdrv)
{
    uint32_t t = (uint32_t)fx_p[FXP_DRV_SW], a = drv_on, us, ut;
    if (t >= DRV_N) t = 1u;
    us = a == 1u ? fdrv : DRV_US[a] + (fx_p[FXP_DRV_MIX] < 127 || dr_w != 32768 ? DRV_US_BLEND : 0u);
    if (t && t != a) {
        ut = t == 1u ? fdrv : DRV_US[t] + DRV_US_BLEND;
        if (ut > us) us = ut;
    }
    return us;
}
