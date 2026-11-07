/* SPDX-License-Identifier: GPL-3.0-only */
/* The other USB audio paths, as the same raw pipe as tests/uac_rs_test.c (the locked resampler), for the independent
 * host proof tests/py/test_uac_resample.py: usb.c compiled unchanged with
 *
 *   -DFELUCCA_UAC_LOCK=0   the path before the lock: uac_tap48 at its exact 160 / 147 (48000) or the samples as they
 *                          are (44100); PPM 0 only (the packet sizes that followed the fill are not simulated)
 *   -DFELUCCA_UAC_LOCK=2   the diagnostic: the real uac_render_start reads the ring's fill and decides; uac_tap drops
 *                          or repeats one frame, stretched over a 32-frame piece (uac_lock_piece); then uac_tap48
 *
 * Raw int16 mono at 44100 Hz (the FM-1's side) on stdin, raw int16 mono at the USB rate on stdout: every frame the
 * host takes. The host is simulated as reading exactly RATE frames a second of real time while the FM-1's clock runs
 * PPM fast (a block of 256 frames lasts 256 / (44100 (1 + PPM 1e-6)) s); the ring's fill drifts and LOCK 2 corrects.
 *
 *   uac_rs_alt_test 44100|48000 PPM < in.s16 > out.s16
 *   cc -O1 -w -DFELUCCA_UAC_LOCK=2 -Ifirmware/src -o build/uac_test/uac_rs_alt2 tests/uac_rs_alt_test.c
 * On stderr: "adj <dropped> <repeated> under <frames short> over <overruns>".
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

int main(int argc, char **argv)
{
    int rate = argc > 1 ? atoi(argv[1]) : 0;
    double ppm = argc > 2 ? atof(argv[2]) : 0.0, owed = 0.0;
    int16_t in[HALF_FRAMES];
    int32_t fr[2u * HALF_FRAMES];
    int16_t outb[UA_N];
    uint32_t p, i, k, under = 0;
    if ((rate != 44100 && rate != 48000) || argc != 3) {
        fprintf(stderr, "usage: uac_rs_alt_test 44100|48000 PPM < in.s16 > out.s16\n");
        return 2;
    }
    memset(&uac, 0, sizeof uac);
    uac.alt = 1; uac.flowing = 1; usb.config = 1;
    uac.r48 = (uint8_t)(rate == 48000);
    ua_w = ua_r = 0;
    for (;;) {
        size_t n = fread(in, sizeof in[0], HALF_FRAMES, stdin);
        if (n == 0)
            break;
        if (n < HALF_FRAMES)
            memset(in + n, 0, (HALF_FRAMES - n) * sizeof in[0]);
        uac_render_start();                             /* (the first one primes the ring with silence) */
        for (i = 0; i < HALF_FRAMES; i++)
            fr[2u * i] = fr[2u * i + 1u] = (int32_t)in[i];
        for (p = 0; p < HALF_FRAMES; p += 32u)
            uac_tap(fr + 2u * p, 32u);
        owed += (double)HALF_FRAMES * rate / (44100.0 * (1.0 + ppm * 1e-6));   /* the host's frames this block */
        k = (uint32_t)owed;
        owed -= k;
        for (i = 0; i < k; i++) {
            if (ua_r == ua_w) { outb[i] = 0; under++; continue; }
            outb[i] = (int16_t)(ua_ring[ua_r & (UA_N - 1u)] & 0xFFFFu);
            ua_r++;
        }
        fwrite(outb, sizeof outb[0], k, stdout);
    }
    fprintf(stderr, "adj %u %u under %u over %u\n", uac.adj_up, uac.adj_down, under, uac.overruns);
    return uac.overruns || under ? 1 : 0;
}
