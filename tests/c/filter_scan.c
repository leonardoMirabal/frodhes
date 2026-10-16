/* SPDX-License-Identifier: GPL-3.0-only */
/* The FILTER's frequency response, measured on the firmware's own code (fx.c fx_filter): a sine at each frequency
 * through it, the output level in dB. Prints "type freq reso hz db" lines for tools/filter_scan.py. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>
#define DINGHY_HOST 1
#include "fxp.h"
#include "fx_tables.h"
static inline int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }
static inline int32_t osc_sine(uint32_t ph)
{
    uint32_t i = ph >> 22;
    int32_t f = (int32_t)((ph >> 7) & 0x7FFFu);
    return FX_SINE[i] + (((FX_SINE[i + 1] - FX_SINE[i]) * f) >> 15);
}
enum { FXP_FL_TYPE, FXP_FL_FREQ, FXP_FL_RESO, FXP_FL_LFO, FXP_LFO_SHAPE, FXP_LFO_RATE, FXP_N };
static volatile int32_t fx_p[FXP_N];
static volatile uint32_t fx_lfo_sync_inc;
static volatile uint8_t fx_lfo_retrig;
/* the filter, copied out of fx.c by tools/filter_scan.py (between its markers) */
#include "../../build/filter_scan/filter_core.c"

int main(int argc, char **argv)
{
    static const int types[] = {1, 2, 3, 4, 5, 6, 7, 8};    /* LP12 LP18 LP24 HP12 HP18 HP24 BP NOTCH */
    static const int freqs[] = {20, 40, 60, 80, 100, 120};
    static const int resos[] = {0, 64, 127};
    (void)argc; (void)argv;
    for (unsigned ti = 0; ti < 8; ti++)
        for (unsigned fi = 0; fi < 6; fi++)
            for (unsigned ri = 0; ri < 3; ri++) {
                for (double hz = 25; hz < 20000; hz *= 1.12) {
                    static int32_t buf[256];
                    double in2 = 0, out2 = 0, dc = 0, nb = 0, dcs;
                    uint32_t n = 0, b, j;
                    for (j = 0; j < FXP_N; j++) fx_p[j] = 0;
                    fx_p[FXP_FL_TYPE] = types[ti]; fx_p[FXP_FL_FREQ] = freqs[fi]; fx_p[FXP_FL_RESO] = resos[ri];
                    fx_p[FXP_LFO_RATE] = 40;
                    fl_was_on = 0;
                    for (b = 0; b < 120; b++) {               /* 0.7 s: settle, then measure the last half */
                        for (j = 0; j < 256; j++, n++) buf[j] = (int32_t)(8000.0 * sin(2 * M_PI * hz * n / 44100.0));
                        double ii = 0;
                        for (j = 0; j < 256; j++) ii += (double)buf[j] * buf[j];
                        fx_filter(buf, 256);
                        if (b >= 60) { in2 += ii; for (j = 0; j < 256; j++) { out2 += (double)buf[j] * buf[j]; dc += buf[j]; } nb += 256; }
                    }
                    out2 -= dc * dc / nb;                 /* the AC level: an offset is reported apart */
                    dcs = dc / nb;
                    printf("%d %d %d %.1f %.2f %.1f\n", types[ti], freqs[fi], resos[ri], hz, 10 * log10(out2 / in2 + 1e-20), dcs);
                }
            }
    return 0;
}
