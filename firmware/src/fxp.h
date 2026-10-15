/* SPDX-License-Identifier: GPL-3.0-only */
/* Fixed-point helpers for Frodhes (IMPLEMENTATION-v3 §4.1). The only place they are defined.
 * Every product is a widening int32 x int32 -> int64; shifts of signed values are arithmetic (floor).
 * No 64-bit division anywhere (docs/RULES.md). */
#ifndef FXP_H
#define FXP_H
#include <stdint.h>

static inline int32_t mul30(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 30); }
static inline int32_t mul28(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 28); }
static inline int32_t mul25(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 25); }
static inline int32_t mul16(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 16); }
static inline int32_t mul15(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 15); }
static inline int32_t mul15s(int32_t a, int32_t b) { return (a * b) >> 15; }   /* |a|, |b| <= 32768 */
static inline int32_t sat15(int32_t v) { return v > 32767 ? 32767 : v < -32767 ? -32767 : v; }
static inline int32_t sat28(int64_t v)
{
    return v > ((1 << 28) - 1) ? ((1 << 28) - 1) : v < -((1 << 28) - 1) ? -((1 << 28) - 1) : (int32_t)v;
}
static inline int32_t lerp15(int32_t a, int32_t b, int32_t f) { return a + (((b - a) * f) >> 15); }
static inline int32_t iabs(int32_t v) { return v < 0 ? -v : v; }
/* fm1_ticks() (24 MHz) to samples at 44.1 kHz, no division: 2^32 * 44100 / 24e6 = 7892002.4 */
static inline uint32_t ticks_to_samples(uint32_t t) { return (uint32_t)(((uint64_t)t * 7892002u) >> 32); }
#endif
