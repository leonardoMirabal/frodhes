/* SPDX-License-Identifier: GPL-3.0-only */
/* The EQ before the drive (fx_eq.c) on the host, fx.c included whole as the firmware has it (tests/py/test_eq.py):
 *   eq_test MODE LO MID FREQ HI [N]   stdin: int32 samples, stdout: int32 samples, 256 a block, the knobs set first
 *   (64 = flat). MODE: "run" (the knobs from the start, settled first over SETTLE blocks of silence), "glide" (the
 *   knobs at 64, then set at the first block: their glides heard). EQ_SCHED="B:LO,MID,FREQ,HI;B:..." sets the knobs
 *   again at those blocks. Then the EQ's state is printed to stderr. */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef DINGHY_HOST
#define DINGHY_HOST 1
#endif
#include "fxp.h"
#define FS 44100u
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"

int main(int argc, char **argv)
{
    static int32_t b[256];
    int glide;
    uint32_t k, settle;
    if (argc < 6) return 2;
    glide = !strcmp(argv[1], "glide");
    settle = argc > 6 ? (uint32_t)atoi(argv[6]) : 64u;
    if (!glide) {
        fx_set(FXP_EQ_LO, atoi(argv[2])); fx_set(FXP_EQ_MID, atoi(argv[3]));
        fx_set(FXP_EQ_FREQ, atoi(argv[4])); fx_set(FXP_EQ_HI, atoi(argv[5]));
        memset(b, 0, sizeof b);
        for (k = 0; k < settle; k++) { fx_eq(b, 256u); memset(b, 0, sizeof b); }
    }
    const char *sc = getenv("EQ_SCHED");
    for (k = 0;; k++) {
        size_t n = fread(b, sizeof b[0], 256, stdin);
        if (!n) break;
        while (sc && *sc && (uint32_t)atoi(sc) == k) {
            int v[4] = {64, 64, 64, 64}, i;
            sc = strchr(sc, ':') + 1;
            for (i = 0; i < 4; i++) { v[i] = atoi(sc); while (*sc && *sc != ',' && *sc != ';') sc++; if (*sc == ',') sc++; }
            if (*sc == ';') sc++;
            fx_set(FXP_EQ_LO, v[0]); fx_set(FXP_EQ_MID, v[1]); fx_set(FXP_EQ_FREQ, v[2]); fx_set(FXP_EQ_HI, v[3]);
        }
        if (glide && k == 0) {
            fx_set(FXP_EQ_LO, atoi(argv[2])); fx_set(FXP_EQ_MID, atoi(argv[3]));
            fx_set(FXP_EQ_FREQ, atoi(argv[4])); fx_set(FXP_EQ_HI, atoi(argv[5]));
        }
        fx_eq(b, (uint32_t)n);
        fwrite(b, sizeof b[0], n, stdout);
    }
    fprintf(stderr, "run %u %u %u cost %u\n", eq_run[0], eq_run[1], eq_run[2], eq_cost_us());
    for (k = 0; k < 3u; k++)
        fprintf(stderr, "c%u %d %d %d %d %d\n", k, eq_c[k][0], eq_c[k][1], eq_c[k][2], eq_c[k][3], eq_c[k][4]);
    return 0;
}
