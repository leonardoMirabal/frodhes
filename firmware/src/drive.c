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
 *   DRV_US_MAX    the budget per TYPE, everything included: 180 us (private drive plan; the user, 2026-10-24:
 *                 120, then 150, then 160, then anti-aliasing on in TSH8, BHLUE and OHCD: 180, just what TSH8's
 *                 anti-aliased kernel needs, 165 us; KHLON plain, 135, sets the measured worst case): CORE + <T> +
 *                 BLEND <= MAX, or it does not build; so DRV_US_KERNEL_MAX (165 us, ~102 instructions per sample) for a
 *                 TYPE's own loop. */
#define DRV_US_CORE 2u
#define DRV_US_BLEND 13u
#define DRV_US_MAX 180u
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
static __attribute__((noinline)) void drv_reset(uint32_t t)
{
    switch (t) {
    case 2: drv_khlon_reset(); break;
    case 3: drv_tsh8_reset(); break;
    case 4: drv_ohcd_reset(); break;
    case 5: drv_bhlue_reset(); break;
    default: break;                                       /* (FDRV keeps its state, as it always did) */
    }
}
static void drv_run(uint32_t t, const int32_t *in, int32_t *out, uint32_t n, const drv_ctl_t *c)   /* t >= 2 */
{
    switch (t) {
    case 2: drv_khlon_run(in, out, n, c); break;
    case 3: drv_tsh8_run(in, out, n, c); break;
    case 4: drv_ohcd_run(in, out, n, c); break;
    case 5: drv_bhlue_run(in, out, n, c); break;
    default: break;
    }
}

/* ---- the dispatch ---------------------------------------------------------------------------------------------- */
static DRV_DATA uint8_t drv_on = 1; /* ISR: the TYPE that runs (1..DRV_N-1); it changes only while the drive is silent */
/* CC 10 (frodhes.c, with fx_reset_req): every TYPE's state to rest, done here at the next TYPE block, not in
 * fx_reset_state: nothing of the TYPEs may sit in app_audio (its register allocation, the Rhodes mode loops: a call
 * there moved them 22 / 29 -> 25 / 32 instructions with stack loads) */
static DRV_BSS volatile uint8_t drv_rst_req;

static DRV_BSS int32_t drv_wet[DRV_BLK];   /* the TYPE's wet signal while it blends */

/* TYPE drv_on (2..), switch sw (0: OFF, fading out): the TYPE once over the block; all wet (dr_w = wt = 32768): in
 * place, nothing else; else into drv_wet, then the blend with dr_w ramping toward wt by 64 every 4 samples (46 ms,
 * as the classic drive; 4096 a block). The TYPE's wet shares are multiples of 256 (MIX v: (258 v + 128) & ~255), so
 * the ramp's step is exact and every block ends on its value. */
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
    if (drv_on == 1u) fx_drive_classic(m, n, sw);
    else fx_drive_types(m, n, sw);
}
/* the drive's cost (fx_cost_us, only while it runs): FDRV fdrv (FX_US_DRIVE: 80, as always); a TYPE whole: DRV_US[]
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
