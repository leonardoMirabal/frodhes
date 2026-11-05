/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ bars' live contact alone (firmware/src/hq/bars_contact.c), for tools/gen_bars_hq_contact.py and
 * tests/py/test_bars_hq.py. Build: cc -O2 -ffp-contract=off -I<dir of hq/bars_hq_contact.h> -Ifirmware/src.
 *   bars_contact_gen            the at-rest force scale: per type, key, law, velocity 1..127 one number (fpk, N;
 *                               0 where the law does not exist or nothing touched), the order [ty][key][law][vel]
 *   bars_contact_gen -s [N]     per stdin line "ty key law vel q0[8] qd0[8]": "steps prate fpk s0 .. s63 q[8] qd[8]" (the
 *                               modes at the contact's end); with N each
 *                               contact N times, printing "steps us_mean us_min" instead (a timing) */
#define _GNU_SOURCE
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define BAR_NTYPE 2
#define BAR_NKEY 88
#define BAR_NFAM 5
#define BAR_PLEN 64u
#if defined(__clang__)
#define HQ_FP_STRICT _Pragma("clang fp contract(off)")
#else
#define HQ_FP_STRICT
#endif
#include "hq/bars_contact.c"

int main(int argc, char **argv)
{
    static const float z[HQB_CK_NV];
    hqb_live_t o;
    if (argc < 2 || strcmp(argv[1], "-s")) {
        uint32_t t, k, l, v;
        for (t = 0; t < BAR_NTYPE; t++)
            for (k = 0; k < BAR_NKEY; k++)
                for (l = 0; l < HQB_CK_NLAW; l++)
                    for (v = 1; v <= 127u; v++) {
                        memset(&o, 0, sizeof o);
                        printf("%.9g\n", HQB_CK_LK[t][l] > 0.0f && hqb_contact_run(t, k, l, v, z, z, &o) ? (double)o.fpk : 0.0);
                    }
        return 0;
    }
    {
        unsigned ty, key, law, vel, rep = argc > 2 ? (unsigned)atoi(argv[2]) : 1u, r, m;
        float q[HQB_CK_NV], qd[HQB_CK_NV];
        while (scanf("%u %u %u %u", &ty, &key, &law, &vel) == 4) {
            uint32_t n = 0;
            for (m = 0; m < HQB_CK_NV; m++) if (scanf("%f", &q[m]) != 1) return 1;
            for (m = 0; m < HQB_CK_NV; m++) if (scanf("%f", &qd[m]) != 1) return 1;
            memset(&o, 0, sizeof o);
            if (rep > 1u) {
                double sum = 0.0, mn = 1e30;
                for (r = 0; r < rep; r++) {
                    struct timespec t0, t1;
                    double us;
                    clock_gettime(CLOCK_MONOTONIC, &t0);
                    n = hqb_contact_run(ty, key, law, vel, q, qd, &o);
                    clock_gettime(CLOCK_MONOTONIC, &t1);
                    us = (double)(t1.tv_sec - t0.tv_sec) * 1e6 + (double)(t1.tv_nsec - t0.tv_nsec) * 1e-3;
                    sum += us;
                    if (us < mn) mn = us;
                }
                printf("%u %.3f %.3f\n", n, sum / rep, mn);
                continue;
            }
            n = hqb_contact_run(ty, key, law, vel, q, qd, &o);
            printf("%u %u %.9g", n, o.prate, (double)o.fpk);
            for (m = 0; m < BAR_PLEN; m++) printf(" %d", o.shape[m]);
            for (m = 0; m < 2u * HQB_CK_NV; m++) printf(" %.9g", (double)hqb_ck_qe[m / HQB_CK_NV][m % HQB_CK_NV]);
            printf("\n");
        }
    }
    return 0;
}
