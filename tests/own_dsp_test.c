/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* Host test of firmware/src/fx_own.h against the helpers in fx.c / rev_hq.c (their bodies are copied here as the
 * reference): exp2_neg, sh_tz and hl_c16 over every 32-bit input, the two DC blockers over long random and edge
 * sequences on many seeds, bit-exact on every output and every state word. Build with -fwrapv (the old code
 * relies on signed wrap). Exit 0 = all equal. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "../firmware/src/fx_own.h"

static inline int32_t sh_tz(int32_t m, uint32_t sh) { return (m + (int32_t)((uint32_t)(m >> 31) >> (32u - sh))) >> sh; }
static uint32_t exp2_neg(uint32_t x)
{
    uint32_t i = x >> 16, u = 32768u - ((x & 0xFFFFu) >> 1), p = 2579u;
    p = ((p * u) >> 15) + 7409u;
    p = ((p * u) >> 15) + 22780u;
    p = ((p * u) >> 15) + 32768u;
    return i < 15u ? p >> (i + 1u) : 0u;
}
static inline int32_t hl_c16(int32_t v) { v = v > -32768 ? v : -32768; return v < 32767 ? v : 32767; }
static inline int32_t dc_block(int32_t x, int32_t *dc, int32_t *err)
{
    int32_t e = (x << 6) - *dc + *err, d = e >> 12;
    *err = e - (d << 12);
    *dc += d;
    return x - ((*dc + 32) >> 6);
}
static inline int32_t rq_dc(int32_t x, int32_t *hp, int32_t *he)
{
    int32_t v = x - *hp + *he;
    *he = v & 63;
    *hp += v >> 6;
    return x - *hp;
}

static uint64_t rs = 88172645463325252ull;
static uint32_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (uint32_t)(rs >> 16); }
static long bad, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { if (bad++ < 20) { printf("FAIL " __VA_ARGS__); printf("\n"); } } } while (0)

static int32_t sample(int kind, uint32_t n, int32_t amp)
{
    switch (kind) {
    case 0: return 0;
    case 1: return amp;
    case 2: return -amp;
    case 3: return (n & 1) ? amp : -amp;
    case 4: return ((n >> 5) & 1) ? amp : -amp;                 /* square, 64 samples */
    case 5: return ((n >> 10) & 1) ? amp : 0;                   /* slow gate */
    case 6: return (int32_t)(rnd() % (2u * (uint32_t)amp + 1u)) - amp;
    case 7: return 1234;                                        /* DC */
    default: return (n % 4000) < 3 ? amp : 0;                   /* clicks */
    }
}

int main(void)
{
    uint64_t x;
    uint32_t sh, seed, n;
    int kind;
    /* exp2_neg: all 2^32 */
    for (x = 0; x <= 0xFFFFFFFFull; x++) CHECK(own_exp2_neg((uint32_t)x) == exp2_neg((uint32_t)x), "exp2_neg %llu", (unsigned long long)x);
    printf("exp2_neg: 2^32 inputs\n");
    /* hl_c16: all 2^32 */
    for (x = 0; x <= 0xFFFFFFFFull; x++) CHECK(own_sat16((int32_t)x) == hl_c16((int32_t)x), "sat16 %d", (int)x);
    printf("hl_c16: 2^32 inputs\n");
    /* sh_tz: every m for every sh 1..31 */
    for (sh = 1; sh <= 31; sh++) for (x = 0; x <= 0xFFFFFFFFull; x++)
        CHECK(own_div_pow2_tz((int32_t)x, sh) == sh_tz((int32_t)x, sh), "sh_tz %d %u", (int)x, sh);
    printf("sh_tz: 31 x 2^32 inputs\n");
    /* HL_IN */
    CHECK(OWN_HL_IN == 10320, "HL_IN");
    /* DC blockers */
    {
        static const int32_t amps[] = {1, 100, 32767, 32768, 131071, 1 << 20, 1 << 24};
        long outs = 0;
        for (seed = 0; seed < 200; seed++) {
            rs = 0x9E3779B97F4A7C15ull * (seed + 1);
            for (kind = 0; kind < 9; kind++) {
                int32_t amp = amps[(seed + (uint32_t)kind) % 7];
                int32_t dc = 0, err = 0, odc = 0, oerr = 0, hp = 0, he = 0, ohp = 0, ohe = 0;
                if (seed & 1) { dc = (int32_t)(rnd() % 400000u) - 200000; err = (int32_t)(rnd() & 4095u); odc = dc; oerr = err;
                                hp = (int32_t)(rnd() % 400000u) - 200000; he = (int32_t)(rnd() & 63u); ohp = hp; ohe = he; }
                for (n = 0; n < 40000; n++) {
                    int32_t in = sample(kind, n, amp), a, b, c, d;
                    a = dc_block(in, &dc, &err);
                    b = own_dc_block(in, &odc, &oerr);
                    c = rq_dc(in, &hp, &he);
                    d = own_dc_hp(in, &ohp, &ohe);
                    CHECK(a == b && dc == odc && err == oerr, "dc_block seed %u kind %d n %u", seed, kind, n);
                    CHECK(c == d && hp == ohp && he == ohe, "rq_dc seed %u kind %d n %u", seed, kind, n);
                    outs += 2;
                }
            }
        }
        printf("dc_block + rq_dc: %ld sample outputs (200 seeds x 9 signals x 40000, with start states)\n", outs);
    }
    printf("%ld checks, %ld failures\n", checks, bad);
    return bad != 0;
}
