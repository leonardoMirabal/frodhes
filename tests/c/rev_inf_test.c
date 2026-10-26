/* SPDX-License-Identifier: GPL-3.0-only */
/* The REVERB type INFINITE's kernel (firmware/src/rev_inf.c) on the host, as the framework drives it: one control
 * block per record. stdin: records of 6 int32 (decay, damp, size, mod, freeze, reset) + HL_NC int32 input samples
 * (the half-rate send, in rev_hall's domain); reset 1 = rev_inf_start before the block (a type switch).
 * stdout per block: HL_NC mid, HL_NC side, then rev_inf_active (int32). argv[1] "garbage": the state is filled with
 * random bytes before the first start (stale buffers must read as zero). tests/py/test_rev_inf.py drives it; build
 * with -DREV_INF_QUARTER=0/1 and the sanitizers. The helpers below are fx.c's, copied verbatim (the test checks it). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fx_tables.h"

#define POOL
#define CTL 32u
#define HL_NC (CTL / 2u)
/* fx.c (verbatim) */
static inline __attribute__((always_inline)) int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }
static inline __attribute__((always_inline)) int32_t osc_sine(uint32_t ph)   /* Q32 phase -> +-32767 */
{
    uint32_t i = ph >> 22;
    int32_t f = (int32_t)((ph >> 7) & 0x7FFFu);
    return FX_SINE[i] + (((FX_SINE[i + 1] - FX_SINE[i]) * f) >> 15);
}
static inline __attribute__((always_inline)) int32_t sh_tz(int32_t m, uint32_t sh) { return (m + (int32_t)((uint32_t)(m >> 31) >> (32u - sh))) >> sh; }
static uint32_t exp2_neg(uint32_t x)
{
    uint32_t i = x >> 16, u = 32768u - ((x & 0xFFFFu) >> 1), p = 2579u;
    p = ((p * u) >> 15) + 7409u;
    p = ((p * u) >> 15) + 22780u;
    p = ((p * u) >> 15) + 32768u;
    return i < 15u ? p >> (i + 1u) : 0u;
}
static inline __attribute__((always_inline)) int32_t hl_c16(int32_t v) { v = v > -32768 ? v : -32768; return v < 32767 ? v : 32767; }   /* smax + smin */
/* rev_types.c's (freeze: 0, 1 SUSTAIN, 2 FREEZE) */
typedef struct {
    int32_t decay, damp, size, mod, freeze;
} rev_ctl_t;
#include "rev_inf.c"

static rev_inf_t st;
int main(int argc, char **argv)
{
    int32_t rec[6 + HL_NC], out[2 * HL_NC + 1];
    rev_ctl_t c;
    if (argc > 1 && !strcmp(argv[1], "garbage")) {
        uint32_t r = 12345u, i;
        unsigned char *p = (unsigned char *)&st;
        for (i = 0; i < sizeof st; i++) { r = r * 1664525u + 1013904223u; p[i] = (unsigned char)(r >> 24); }
    } else
        memset(&st, 0, sizeof st);
    if (argc > 1 && !strcmp(argv[1], "size")) { printf("%u\n", (unsigned)sizeof st); return 0; }
    rev_inf_start(&st);
    while (fread(rec, sizeof rec, 1, stdin) == 1) {
        c.decay = rec[0], c.damp = rec[1], c.size = rec[2], c.mod = rec[3] << 4, c.freeze = rec[4];
        if (rec[5]) rev_inf_start(&st);
        rev_inf_run(&st, rec + 6, out, out + HL_NC, &c);
        out[2 * HL_NC] = rev_inf_active(&st);
        fwrite(out, sizeof out, 1, stdout);
    }
    return 0;
}
