/* SPDX-License-Identifier: GPL-3.0-only */
/* The locked USB audio stream's resampler (usb.c uac_tap_lock, FELUCCA_UAC_LOCK 1), compiled unchanged: "L R" frames
 * from stdin go in as the audio ISR taps them (pieces of 32 frames, blocks of 256), and every frame the ring receives
 * comes out on stdout as "L R". The step is set as the control loop would set it (uac_lock_set), with no loop:
 *
 *   uac_lock_test RATE PPM [PPM_END]     RATE 48 or 44 (the host's rate); the FM-1's clock PPM fast (the step then
 *                                        holds the stream at the host's rate); with PPM_END the correction moves from
 *                                        PPM to PPM_END in equal steps once a block (a slow ramp: a zipper would show)
 * tests/py/test_uac_lock.py feeds it sines and measures the error against an ideal resampler.
 *   uac_lock_test table                  the table usb.c rebuilt in RAM (uac_fl, from the packed uac_fir_lock.h), one
 *                                        phase a line
 *   cc -O1 -w -Ifirmware/src -o build/uac_test/uac_lock_test tests/uac_lock_test.c
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RING_PUBLISH() ((void)0)
#define HALF_FRAMES 256u
#define FELUCCA_UAC 1
#define FELUCCA_UAC_LOCK 1
static void fm1_delay_ms(uint32_t ms) { (void)ms; }   /* (usb_start: never called here) */
#include "usb.c"

static int64_t q52(double ppm) { return (int64_t)(ppm * 1e-6 * 4503599627370496.0); }

int main(int argc, char **argv)
{
    int r48 = argc > 1 && atoi(argv[1]) == 48;
    double ppm = argc > 2 ? atof(argv[2]) : 0.0, ppm_end = argc > 3 ? atof(argv[3]) : ppm;
    static int32_t frames[2u * 44100u * 60u];
    uint32_t nf = 0, b, p, nb, i;
    int l, r;
    if (argc > 1 && !strcmp(argv[1], "table")) {
        uac_lock_init();
        for (b = 0; b < UAC_FL_L / 2u; b++)
            for (i = 0; i < UAC_FL_N; i++) printf("%d%c", uac_fl[b][i], i + 1u < UAC_FL_N ? ' ' : '\n');
        return 0;
    }
    while (nf < 44100u * 60u && scanf("%d %d", &l, &r) == 2) {
        frames[2u * nf] = l; frames[2u * nf + 1u] = r;
        nf++;
    }
    memset(&uac, 0, sizeof uac);
    uac.feed = 1; uac.ring48 = (uint8_t)r48; uac.r48 = (uint8_t)r48;
    ua_w = ua_r = 0;
    uac_lock_init();
    uac_lock_set(q52(ppm));
    nb = nf / HALF_FRAMES;
    for (b = 0; b < nb; b++) {
        if (argc > 3)
            uac_lock_set(q52(ppm + (ppm_end - ppm) * b / (nb > 1 ? nb - 1 : 1)));
        for (p = 0; p < HALF_FRAMES; p += 32u)
            uac_tap(frames + 2u * (b * HALF_FRAMES + p), 32u);
        for (i = ua_r; i != ua_w; i++) {
            uint32_t f = ua_ring[i & (UA_N - 1u)];
            printf("%d %d\n", (int16_t)(f & 0xFFFFu), (int16_t)(f >> 16));
        }
        ua_r = ua_w;
    }
    printf("# S %u\n", uac.lk_S);
    return uac.overruns ? 1 : 0;
}
