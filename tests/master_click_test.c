/* SPDX-License-Identifier: GPL-3.0-only */
/* The MASTER path after the app (GitHub #12: K1 MASTER on the SMK-37 clicks on the jack, USB stays smooth). The host
 * build (tests/host.c) replaces platform.c, so its MASTER pass never runs there: this runs platform.c's own code
 * (copied verbatim between the markers; tests/py/test_master_click.py checks they match) on a recorded app output.
 *   - the knob path (platform_poll): the SMK-37's K1 as smk_input.h makes it (24 a detent, 0..1023), the glide
 *     (1/64 a main-loop pass), the dead band of 6, master_q12 = k^2 >> 8;
 *   - the audio ISR's MASTER pass, a block of 256 frames: mode 1 = 0.9.6 (master_ramp: the gain ramps over the
 *     block), mode 0 = 0.9.5 (one gain a block, the step);
 *   - usb_pre_master 0 / 1 (the jack is post-MASTER either way).
 * Usage: master_click_test IN.raw OUT.raw MODE PRE PASS_US K0 [T_MS:DETENTS ...]
 *   IN: int16 stereo frames (the app's output, Q15); OUT: int32 stereo, the jack's samples back at Q15 (>> the shift;
 *   MODE | 2: the jack's 24-bit samples as they go to the codec);
 *   PASS_US: the main loop's pass (platform_poll) interval; K0: K1's value at the start (the knob settled there);
 *   T_MS:D: D detents (+ up, - down) at T ms. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HALF_FRAMES 256u
#define HALF_WORDS (HALF_FRAMES * 2u)
#define APP_OUT_SHIFT 8u
#define HAL_KEEP_REG(v) ((void)(v))
#define UAC_ONLY(x) ((void)0)
static int32_t tap_q[64];
static uint8_t usb_pre_master;
static volatile int32_t master_q12 = 2048;
static int32_t knob_avg = 512 * 16;
static uint32_t k_used = 512;

/* platform.c begin */
/* Frodhes: the block's MASTER pass with the gain ramping from master_prev to m (the loops below, a gain a frame) */
static int32_t master_prev = -1;
/* Frodhes (0.9.7, #8): the jack after MASTER keeps the product's low bits (x * g >> (12 - APP_OUT_SHIFT)) instead of
 * cutting it to 16 bits first ((x * g >> 12) << APP_OUT_SHIFT: a -95 dBFS error with a DC part under the codec's
 * floor); the clamp at the same ceiling, 32767 << APP_OUT_SHIFT. No overflow: |x| <= 32767 (or the app's sample, as
 * before), g <= 4096. The USB tap's samples are the 16-bit ones, unchanged */
#define MASTER_JACK_SH (12 - (int32_t)APP_OUT_SHIFT)
#define TAIL_JACK(x) ((x) > (32767 << APP_OUT_SHIFT) ? (32767 << APP_OUT_SHIFT) : \
                      (x) < -(32767 << APP_OUT_SHIFT) ? -(32767 << APP_OUT_SHIFT) : (x))
_Static_assert(APP_OUT_SHIFT <= 12u, "the jack: MASTER's Q12 product shifted right by 12 - APP_OUT_SHIFT");
#define TAIL_PRE(x) ((x) > 32767 ? 32767 : (x) < -32767 ? -32767 : (x))
/* platform.c end */
static void master_ramp(int32_t *o, int32_t m)
/* platform.c begin */
{
    int32_t mq = (master_prev < 0 ? m : master_prev) << 8, dm = master_prev < 0 ? 0 : m - master_prev, b, i;
    for (b = 0; b < (int32_t)HALF_WORDS; b += 64) {
        int32_t *c = o + b, *q = tap_q;
        HAL_KEEP_REG(q);
        for (i = 0; i < 32; i++, c += 2, q += 2) {
            int32_t g, k, x;
            mq += dm; g = mq >> 8;
            for (k = 0; k < 2; k++) {           /* (both channels, either order of the gain and the clip below) */
                int32_t p;
                x = c[k];
                if (usb_pre_master) {
                    x = TAIL_PRE(x);
                    q[k] = x;
                    c[k] = (x * g) >> MASTER_JACK_SH;
                } else {
                    p = x * g;
                    x = p >> 12;
                    q[k] = TAIL_PRE(x);
                    c[k] = TAIL_JACK(p >> MASTER_JACK_SH);
                }
            }
        }
        UAC_ONLY(uac_tap(q - 64, 32u));
    }
    master_prev = m;
}
/* platform.c end */

static void block_const(int32_t *o, int32_t m)   /* the ISR's pass at one gain (platform.c's loops, the same math) */
{
    uint32_t b, i;
    for (b = 0; b < HALF_WORDS; b += 64u) {
        int32_t *c = o + b;
        for (i = 0; i < 32u; i++, c += 2) {
            int32_t k;
            for (k = 0; k < 2; k++) {
                int32_t x = c[k];
                if (usb_pre_master) x = (TAIL_PRE(x) * m) >> MASTER_JACK_SH;
                else { x = (x * m) >> MASTER_JACK_SH; x = TAIL_JACK(x); }
                c[k] = x;                       /* (0.9.7, #8: the jack's 24 bits) */
            }
        }
    }
}
#undef TAIL_PRE

static void knob_pass(int32_t a)                 /* platform_poll's MASTER part, a = fm1_adc_read(FM1_ADC_MASTER) */
{
        uint32_t k10;
/* platform.c begin */
        knob_avg += (a * 16 - knob_avg) / 64;
        k10 = (uint32_t)(knob_avg / 16);
        if (k10 + 6u < k_used || k10 > k_used + 6u || (k10 == 0u) != (k_used == 0u) || (k10 >= 1020u) != (k_used >= 1020u)) {
            k_used = k10;
            master_q12 = (int32_t)((k10 * k10) >> 8);
        }
/* platform.c end */
}

int main(int argc, char **argv)
{
    FILE *fi, *fo;
    int mode, ne = 0, i;
    uint32_t pass_us, t_us = 0, next_pass = 0, nb = 0;
    int32_t smk_master, ev_t[64], ev_d[64];
    static int16_t in[HALF_WORDS];
    static int32_t o[HALF_WORDS];
    if (argc < 7) { fprintf(stderr, "usage: see the head of tests/master_click_test.c\n"); return 2; }
    fi = fopen(argv[1], "rb"); fo = fopen(argv[2], "wb");
    if (!fi || !fo) { perror("open"); return 2; }
    mode = atoi(argv[3]); usb_pre_master = (uint8_t)atoi(argv[4]);
    pass_us = (uint32_t)atoi(argv[5]); smk_master = atoi(argv[6]);
    for (i = 7; i < argc && ne < 64; i++, ne++) sscanf(argv[i], "%d:%d", &ev_t[ne], &ev_d[ne]);
    knob_avg = smk_master * 16; k_used = (uint32_t)smk_master;           /* (settled there) */
    master_q12 = (int32_t)(((uint32_t)smk_master * (uint32_t)smk_master) >> 8);
    while (fread(in, sizeof in[0], HALF_WORDS, fi) == HALF_WORDS) {
        uint32_t t_block = (uint32_t)((uint64_t)nb * HALF_FRAMES * 1000000u / 44100u);
        int32_t m;
        for (; next_pass < t_block; next_pass += pass_us) {        /* the main loop's passes before this block */
            int e;
            for (e = 0; e < ne; e++)
                if (ev_d[e] && (uint32_t)ev_t[e] * 1000u <= next_pass) { smk_master += ev_d[e] * 24; ev_d[e] = 0; }
            smk_master = smk_master < 0 ? 0 : smk_master > 1023 ? 1023 : smk_master;   /* (smk_input.h) */
            knob_pass(smk_master);
        }
        for (i = 0; i < (int)HALF_WORDS; i++) o[i] = in[i];
        m = master_q12;
        if ((mode & 1) && m != master_prev) master_ramp(o, m);
        else { block_const(o, m); master_prev = m; }
        if (!(mode & 2))                                           /* (mode | 2: the jack's 24-bit samples) */
            for (i = 0; i < (int)HALF_WORDS; i++) o[i] >>= APP_OUT_SHIFT;
        fwrite(o, sizeof o[0], HALF_WORDS, fo);
        nb++;
    }
    (void)t_us;
    fclose(fi); fclose(fo);
    return 0;
}
