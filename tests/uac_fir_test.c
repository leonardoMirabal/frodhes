/* SPDX-License-Identifier: GPL-3.0-only */
/* The USB audio's 44.1 -> 48 kHz resampler (usb.c uac_tap48), compiled unchanged: SECS seconds of a seeded random
 * signal (full scale, over full scale (the clamp) and quiet stretches) go in as the audio ISR taps them (blocks of 256
 * frames in 8 pieces of 32); every 48 kHz frame the ring receives comes out on stdout as "L R". tests/py/test_uac_fir.py
 * computes the same frames from uac_fir.h with a plain reference and compares them bit for bit.
 *
 *   cc -O1 -w -Ifirmware/src -o build/uac_test/uac_fir_test tests/uac_fir_test.c && build/uac_test/uac_fir_test SECS SEED
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RING_PUBLISH() ((void)0)
#define HALF_FRAMES 256u
#define FELUCCA_UAC 1
static void fm1_delay_ms(uint32_t ms) { (void)ms; }   /* (usb_start: never called here) */
#include "usb.c"

static uint32_t rng;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }

int main(int argc, char **argv)
{
    double secs = argc > 1 ? atof(argv[1]) : 2.0;
    uint32_t blocks = (uint32_t)(secs * 44100.0 / HALF_FRAMES), b, i, p;
    int32_t blk[2u * HALF_FRAMES];
    rng = argc > 2 ? (uint32_t)strtoul(argv[2], 0, 0) : 1u;
    if (!rng) rng = 1u;
    memset(&uac, 0, sizeof uac);
    uac.feed = 1; uac.ring48 = 1;
    ua_w = ua_r = 0;
    for (b = 0; b < blocks; b++) {
        uint32_t kind = (b / 8u) % 4u;                  /* 0 full scale, 1 over it (+-4x), 2 quiet, 3 a square */
        for (i = 0; i < 2u * HALF_FRAMES; i++) {
            int32_t v = (int32_t)(rnd() & 0xFFFFu) - 32768;
            if (kind == 1u) v *= 4;
            else if (kind == 2u) v >>= 9;
            else if (kind == 3u) v = (i >> 5 & 1u) ? 32767 : -32767;
            blk[i] = v;
        }
        if (argc > 3) {                                 /* pieces of other lengths (the passes split differently:
                                                         * the same stream) */
            static const uint32_t PC[] = {7u, 100u, 1u, 33u, 64u, 51u};
            for (p = 0, i = b; p < HALF_FRAMES; i++) {
                uint32_t m = PC[i % 6u] < HALF_FRAMES - p ? PC[i % 6u] : HALF_FRAMES - p;
                uac_tap(blk + 2u * p, m);
                p += m;
            }
        } else
            for (p = 0; p < HALF_FRAMES; p += 32u)
                uac_tap(blk + 2u * p, 32u);
        while (ua_r != ua_w) {                          /* the reader keeps the ring empty */
            uint32_t f = ua_ring[ua_r & (UA_N - 1u)];
            printf("%d %d\n", (int16_t)(f & 0xFFFFu), (int16_t)(f >> 16));
            ua_r++;
        }
    }
    if (uac.overruns) { fprintf(stderr, "overruns %u\n", uac.overruns); return 1; }
    return 0;
}
