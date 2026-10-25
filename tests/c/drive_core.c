/* SPDX-License-Identifier: GPL-3.0-only */
/* The DRIVE framework's shared helpers (firmware/src/drive.c), run on the firmware's own code for
 * tests/py/test_drive_core.py: fx.c is included whole (drive.c with it), the helpers are called on test signals.
 *
 *   drive_core CURVE HALF SH MODE [ARGS] < in.raw > out.raw
 *
 * CURVE: "tanh" (fx_tables.h FX_TANH with drive_tables.h DRV_TANH_H) or a file: int16 f[2 HALF + 1] then int32
 * h[2 HALF + 1], little-endian. in / out: int32 samples. MODE:
 *   curve            drv_curve(x)
 *   softclip         fx.c softclip(x) (the classic drive's own)
 *   p                drv_curve_p(x), int64 out
 *   adaa EPS         drv_adaa1 over the sequence (drv_adaa_reset first)
 *   bq B0 B1 B2 A1 A2   drv_bq over the sequence (Q28, a negated)
 *   tpt G            drv_tpt1 over the sequence (G Q30): the low-pass */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DINGHY_HOST 1
#define FS 44100u                                     /* (platform.c's) */
#include "fxp.h"
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
/* fx.c's SPREAD (S13) mixes rhodes.c's side signal: none here (rh_side_on 0) */
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"    /* (fx.c's reverb framework: fx_reverb, rev_cost_us) */

int main(int argc, char **argv)
{
    static int16_t fb[1 << 16];
    static int32_t hb[1 << 16];
    const int16_t *f = FX_TANH;
    const int32_t *h = DRV_TANH_H;
    uint32_t half, sh;
    const char *mode;
    int32_t x;
    drv_adaa_t s;
    drv_bq_t c;
    drv_bqs_t bs = {0, 0, 0, 0};
    int32_t ts = 0, eps = 0, G = 0;
    if (argc < 5) { fprintf(stderr, "usage: drive_core CURVE HALF SH MODE [ARGS]\n"); return 2; }
    half = (uint32_t)atoi(argv[2]); sh = (uint32_t)atoi(argv[3]); mode = argv[4];
    if (strcmp(argv[1], "tanh")) {
        FILE *cf = fopen(argv[1], "rb");
        size_t n = 2u * half + 1u;
        if (!cf || n > sizeof fb / sizeof fb[0] || fread(fb, 2, n, cf) != n || fread(hb, 4, n, cf) != n) {
            fprintf(stderr, "bad curve %s\n", argv[1]); return 2;
        }
        fclose(cf);
        f = fb; h = hb;
    }
    if (!strcmp(mode, "adaa")) { eps = argc > 5 ? atoi(argv[5]) : 0; drv_adaa_reset(&s, f, h, half, sh); }
    if (!strcmp(mode, "bq")) {
        if (argc < 10) return 2;
        c.b0 = atoi(argv[5]); c.b1 = atoi(argv[6]); c.b2 = atoi(argv[7]); c.a1 = atoi(argv[8]); c.a2 = atoi(argv[9]);
    }
    if (!strcmp(mode, "tpt")) G = argc > 5 ? atoi(argv[5]) : 0;
    while (fread(&x, 4, 1, stdin) == 1) {
        int32_t y = 0;
        if (!strcmp(mode, "p")) { int64_t p = drv_curve_p(f, h, half, sh, x); fwrite(&p, 8, 1, stdout); continue; }
        if (!strcmp(mode, "curve")) y = drv_curve(f, half, sh, x);
        else if (!strcmp(mode, "softclip")) y = softclip(x);
        else if (!strcmp(mode, "adaa")) y = drv_adaa1(&s, f, h, half, sh, eps, x);
        else if (!strcmp(mode, "bq")) y = drv_bq(&c, &bs, x);
        else if (!strcmp(mode, "tpt")) y = drv_tpt1(&ts, G, x);
        else { fprintf(stderr, "bad mode %s\n", mode); return 2; }
        fwrite(&y, 4, 1, stdout);
    }
    return 0;
}
