/* SPDX-License-Identifier: GPL-3.0-only */
/* fx_own.h: the small fixed-point helpers (2^-x, truncating shifts, int16 saturation, two DC blockers) written from
 * private/plans/own-dsp-spec.md, bit for bit equal to the ones in fx.c / rev_hq.c. Included by fx.c under FR_OWN (the plugin's own-code build);
 * tests/own_dsp_test.c proves the equality. Integers only; the sums wrap as unsigned 32-bit, so no signed overflow. */
#ifndef FX_OWN_H
#define FX_OWN_H
#include <stdint.h>

#define OWN_HL_IN 10320                     /* the reverb input gain, Q15 */

/* 2^-(x / 65536) in Q15 (1.0 = 32768): a cubic on the fraction, then a shift by the integer part + 1; 0 from 15 on */
static inline uint32_t own_exp2_neg(uint32_t x)
{
    static const uint16_t add[3] = {7409u, 22780u, 32768u};
    const uint32_t whole = x >> 16;
    const uint32_t u = 32768u - ((x & 0xFFFFu) >> 1);
    uint32_t p = 2579u, k;
    for (k = 0; k < 3; k++) p = ((p * u) >> 15) + add[k];
    return whole < 15u ? p >> (whole + 1u) : 0u;
}

/* m / 2^sh rounded toward zero, sh 1..31 */
static inline int32_t own_div_pow2_tz(int32_t m, uint32_t sh)
{
    if (m >= 0) return (int32_t)((uint32_t)m >> sh);
    return -(int32_t)(((uint32_t)0 - (uint32_t)m) >> sh);   /* the magnitude shifted, then negated */
}

/* saturate to int16 */
static inline int32_t own_sat16(int32_t v)
{
    if (v < -32768) return -32768;
    if (v > 32767) return 32767;
    return v;
}

/* DC blocker with error feedback: the estimate runs in Q6 plus a 12-bit remainder */
static inline int32_t own_dc_block(int32_t x, int32_t *dc, int32_t *err)
{
    const uint32_t e = ((uint32_t)x << 6) - (uint32_t)*dc + (uint32_t)*err;
    const int32_t d = (int32_t)e >> 12;
    *err = (int32_t)(e & 4095u);
    *dc = (int32_t)((uint32_t)*dc + (uint32_t)d);
    return (int32_t)((uint32_t)x - (uint32_t)((int32_t)((uint32_t)*dc + 32u) >> 6));
}

/* the reverb input's DC blocker: a 6-bit remainder */
static inline int32_t own_dc_hp(int32_t x, int32_t *hp, int32_t *he)
{
    const uint32_t v = (uint32_t)x - (uint32_t)*hp + (uint32_t)*he;
    *he = (int32_t)(v & 63u);
    *hp = (int32_t)((uint32_t)*hp + (uint32_t)((int32_t)v >> 6));
    return (int32_t)((uint32_t)x - (uint32_t)*hp);
}
#endif
