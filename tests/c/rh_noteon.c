/* SPDX-License-Identifier: GPL-3.0-only */
/* rhodes.c's note-on coefficient cache (rh_cc_poll, rh_init) on the host, for tests/py/test_rh_cc.py:
 *   rh_noteon check     every key x 4 velocities x knob settings (TUNE, DECAY, RELEASE, a bend): the voice rh_init
 *                       builds from the cache is byte for byte the one rh_coef builds (the cache off); a stale
 *                       cache (a knob moved, rh_cc_poll not yet run) and a bend fall back to rh_coef. Prints
 *                       "cases N cached C" and exits 1 on a difference
 *   rh_noteon time R    a 9-note strike burst (rh_init x 9, every key in turn) R times with the cache, then without;
 *                       prints "ns_cached A ns_live B" per note-on */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "rhodes.c"

static rh_voice_t va, vb;

static void init_live(rh_voice_t *v, uint32_t note, uint32_t vel)
{
    uint32_t p = rh_cc_pub;
    rh_cc_pub = 0;                              /* no cache: rh_coef */
    rh_init(v, note, vel);
    rh_cc_pub = p;
}

static int cached_now(uint32_t note)            /* rh_init's test, as it is there */
{
    uint32_t cp = rh_cc_pub;
    (void)note;
    return cp && rh_cc_st[cp - 1u][0] == rh_tune_now() && rh_cc_st[cp - 1u][1] == rh_k_decay
           && rh_cc_st[cp - 1u][2] == rh_k_rel;
}

static double now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

int main(int argc, char **argv)
{
    static const int32_t TUNE[] = {0, 1073741, -6000000, 31000000, -31000000};     /* Q30: 0, ~+0.17, ~-10, +50, -50 c */
    static const int32_t KD[] = {65536, 16384, 262144, 40000};
    static const int32_t BEND[] = {0, 0, 120000000, -120000000};
    static const uint32_t VEL[] = {1, 40, 100, 127};
    if (argc > 1 && !strcmp(argv[1], "check")) {
        unsigned cases = 0, cached = 0, a, b, c, d, n;
        for (a = 0; a < 5; a++)
            for (b = 0; b < 4; b++)
                for (c = 0; c < 4; c++)
                    for (d = 0; d < 4; d++) {
                        rh_tune_t = TUNE[a]; rh_k_decay = KD[b]; rh_k_rel = KD[(b + 1u) & 3u];
                        rh_bend_t = 0;
                        if (c != 1) rh_cc_poll();       /* c == 1: the knobs moved since the last poll (stale) */
                        if (c == 1) { rh_k_rel = rh_k_rel + 1; }
                        rh_bend_t = BEND[c];
                        for (n = 21; n <= 108; n++) {
                            memset(&va, 0x55, sizeof va); memset(&vb, 0x55, sizeof vb);
                            rh_ages = 0; rh_init(&va, n, VEL[d]);
                            rh_ages = 0; init_live(&vb, n, VEL[d]);
                            cases++; cached += (unsigned)cached_now(n);
                            if ((va.ptab == va.pbuf) != (vb.ptab == vb.pbuf)) return 1;
                            if (va.ptab == va.pbuf) va.ptab = vb.ptab = 0;   /* each points at its own pbuf (O2) */
                            if (memcmp(&va, &vb, sizeof va)) {
                                printf("DIFF note %u vel %u tune %d kd %d bend %d\n", n, VEL[d], rh_tune_t, rh_k_decay,
                                       rh_bend_t);
                                return 1;
                            }
                        }
                    }
        printf("cases %u cached %u\n", cases, cached);
        return 0;
    }
    if (argc > 2 && !strcmp(argv[1], "time")) {
        long r, reps = atol(argv[2]);
        uint32_t n = 21, i;
        double t0, t1, t2;
        unsigned long chk = 0;
        rh_tune_t = 0; rh_k_decay = 65536; rh_k_rel = 65536; rh_bend_t = 0;
        rh_cc_poll();
        t0 = now_ns();
        for (r = 0; r < reps; r++)
            for (i = 0; i < 9u; i++) { rh_init(&rv[i], n, 100); chk += (uint32_t)rv[i].m[0].e0; n = n == 108u ? 21u : n + 1u; }
        t1 = now_ns();
        for (r = 0; r < reps; r++)
            for (i = 0; i < 9u; i++) { init_live(&rv[i], n, 100); chk += (uint32_t)rv[i].m[0].e0; n = n == 108u ? 21u : n + 1u; }
        t2 = now_ns();
        printf("ns_cached %.1f ns_live %.1f chk %lu\n", (t1 - t0) / (reps * 9.0), (t2 - t1) / (reps * 9.0), chk & 1u);
        return 0;
    }
    fprintf(stderr, "usage: rh_noteon check | time REPS\n");
    return 2;
}
