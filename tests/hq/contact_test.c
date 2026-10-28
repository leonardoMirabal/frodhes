/* SPDX-License-Identifier: GPL-3.0-only */
/* The HQ live contact alone (firmware/src/hq/hq_contact.c), for tests/py/test_rhodes_hq.py: per stdin line
 *   key(0..87) vel(1..127) q0[6] qd0[6]
 * prints "steps prate fpk s0 .. s63" (the pulse, as hq_contact_run makes it). Build: cc -O2 -ffp-contract=off.
 * contact_test N: each contact N times, printing "key vel steps us_mean us_min" (a timing; on the Move: taskset -c 1;
 * the min over the repeats is the code's own cost, the mean has the preemptions in it) */
#define _GNU_SOURCE
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define RH_NKEY 88
#define RH_PLEN 64u
#define RH_NKEY_ 88
#if defined(__clang__)
#define HQ_FP_STRICT _Pragma("clang fp contract(off)")
#else
#define HQ_FP_STRICT
#endif
#include "hq/rh_hq_pulse.h"
#include "hq/hq_contact.c"

int main(int argc, char **argv)
{
    unsigned key, vel, rep = argc > 1 ? (unsigned)atoi(argv[1]) : 1u, r;
    float q[HQ_CK_NFE], qd[HQ_CK_NFE];
    while (scanf("%u %u", &key, &vel) == 2) {
        hq_live_t o;
        uint32_t m, n = 0;
        for (m = 0; m < HQ_CK_NFE; m++) if (scanf("%f", &q[m]) != 1) return 1;
        for (m = 0; m < HQ_CK_NFE; m++) if (scanf("%f", &qd[m]) != 1) return 1;
        memset(&o, 0, sizeof o);
        if (rep > 1u) {                        /* timing: us per contact, the mean and the min over the repeats */
            double sum = 0.0, mn = 1e30;
            for (r = 0; r < rep; r++) {
                struct timespec t0, t1;
                double us;
                clock_gettime(CLOCK_MONOTONIC, &t0);
                n = hq_contact_run(key, vel, RH_HQ_PRATE[key][vel - 1u], q, qd, &o);
                clock_gettime(CLOCK_MONOTONIC, &t1);
                us = (t1.tv_sec - t0.tv_sec) * 1e6 + (t1.tv_nsec - t0.tv_nsec) * 1e-3;
                sum += us; if (us < mn) mn = us;
            }
            printf("%u %u %u %.2f %.2f\n", key + 21u, vel, n, sum / rep, mn);
            continue;
        }
        n = hq_contact_run(key, vel, RH_HQ_PRATE[key][vel - 1u], q, qd, &o);
        printf("%u %u %.9g", n, o.prate, o.fpk);
        for (m = 0; m < RH_PLEN; m++) printf(" %d", o.shape[m]);
        printf("\n");
    }
    return 0;
}
