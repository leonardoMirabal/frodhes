/* SPDX-License-Identifier: GPL-3.0-only */
/* rhodes.c rh_hd_flux against rh_hermite_i (q * q), for tests/py/test_rh_hd_flux.py: both functions (and RH_S_MAX_HI,
 * RH_HD_QMAX) copied out of rhodes.c into RH_HD_CORE by the test. The table: random words at the pickup table's scale;
 * the gaps: the clamp's edge (+-RH_HD_QMAX, +-1, +-2), 0, the int32 ends, a sweep across the edge and random ones over
 * the whole int32 range. Prints the count of differences (0 expected) and of clamped gaps */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include RH_HD_CORE

static uint32_t rng = 0x12345678u;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
static int32_t T[2u * (RH_S_MAX_HI + 1u)];
static unsigned long bad, clamped, n;
static void one(int32_t q)
{
    int32_t a = rh_hd_flux(T, q), b = rh_hermite_i(T, (int64_t)q * q);
    n++;
    clamped += (uint64_t)((int64_t)q * q) >= ((uint64_t)RH_S_MAX_HI << 44);
    if (a != b) { if (bad < 5) fprintf(stderr, "q %d: %d != %d\n", q, a, b); bad++; }
}
int main(void)
{
    uint32_t i;
    int32_t e = (int32_t)RH_HD_QMAX, d;
    for (i = 0; i < 2u * (RH_S_MAX_HI + 1u); i++) T[i] = (int32_t)(rnd() & 0x0FFFFFFFu) - (1 << 27);
    for (d = -3; d <= 3; d++) { one(e + d); one(-e - d); }
    one(0); one(1); one(-1); one(INT32_MAX); one(INT32_MIN); one(INT32_MIN + 1);
    for (d = -200000; d <= 200000; d++) one(e + d);
    for (i = 0; i < 4000000u; i++) one((int32_t)rnd());
    for (i = 0; i < 4000000u; i++) one((int32_t)(rnd() % (2u * RH_HD_QMAX + 1u)) - e);
    printf("checked %lu, clamped %lu, differences %lu\n", n, clamped, bad);
    return bad != 0;
}
