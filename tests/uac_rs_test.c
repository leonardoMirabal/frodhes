/* SPDX-License-Identifier: GPL-3.0-only */
/* The locked USB audio stream's resampler (usb.c uac_tap_lock, FELUCCA_UAC_LOCK 1), compiled unchanged, as a raw
 * pipe for the host proof: raw int16 mono samples at 44100 Hz (the FM-1's side) on stdin, raw int16 mono at the USB
 * rate on stdout (every frame the ring receives, left channel; the right carries the same samples). The samples go
 * in as the audio ISR taps them (pieces of 32 frames, blocks of 256). The step is set as the control loop would set
 * it (uac_lock_set), once a block, from a ppm schedule (no loop):
 *
 *   uac_rs_test RATE PPM                       RATE 44100 or 48000 (the host's rate); the FM-1's clock PPM fast
 *   uac_rs_test RATE PPM_START PPM_END SECS    a linear ramp from PPM_START to PPM_END over SECS seconds of input,
 *                                              then held at PPM_END
 *
 * Output length: about n x RATE / 44100 / (1 + PPM 1e-6) samples, n rounded up to whole blocks of 256 (the last
 * block padded with silence) (one output of latency, the resampler's hold). The
 * exit code is 1 if the ring overran (it cannot here: the pipe drains it every block). On stderr: "S <step>" at the
 * end (the last step, Q16 of 1/2560 frame). With UAC_RS_BLOCK=1 in the environment the block goes in as the firmware's
 * tail feeds it (platform.c: the samples straight into the history, UAC_HIST, then one uac_tap_hist a block): the
 * same output, bit for bit; UAC_RS_BLOCK=2: two blocks so, then one in pieces (a MASTER ramp's block, master_ramp), and
 * so on; UAC_RS_BLOCK=3: two blocks in pieces written to the history's start, each tapped (platform.c APP_TAIL_EARLY,
 * tail_piece), then one as a whole block or in uac_tap's pieces in turn. Exit 3 if a frame's right channel differs from its left (the history's R half misplaced).
 *   cc -O1 -w -Ifirmware/src -o build/uac_test/uac_rs_test tests/uac_rs_test.c
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
    int rate = argc > 1 ? atoi(argv[1]) : 0;
    double p0 = argc > 2 ? atof(argv[2]) : 0.0, p1 = argc > 3 ? atof(argv[3]) : p0;
    double secs = argc > 4 ? atof(argv[4]) : 0.0;
    int16_t in[HALF_FRAMES];
    int32_t fr[2u * HALF_FRAMES];
    int16_t outb[UA_N];
    uint32_t b = 0, p, i, k;
    const char *be = getenv("UAC_RS_BLOCK");
    int mode = be ? be[0] - '0' : 0, lr = 0;
    if ((rate != 44100 && rate != 48000) || argc == 4 || argc > 5) {
        fprintf(stderr, "usage: uac_rs_test 44100|48000 PPM [PPM_END SECONDS] < in.s16 > out.s16\n");
        return 2;
    }
    memset(&uac, 0, sizeof uac);
    uac.feed = 1; uac.ring48 = uac.r48 = (uint8_t)(rate == 48000);
    ua_w = ua_r = 0;
    uac_lock_init();
    for (;;) {
        size_t n = fread(in, sizeof in[0], HALF_FRAMES, stdin);
        double t = (double)b * HALF_FRAMES / 44100.0, ppm;
        if (n == 0)
            break;
        if (n < HALF_FRAMES)
            memset(in + n, 0, (HALF_FRAMES - n) * sizeof in[0]);   /* (the last block padded with silence) */
        ppm = secs > 0.0 && t < secs ? p0 + (p1 - p0) * t / secs : p1;
        uac_lock_set(q52(ppm));
        for (i = 0; i < HALF_FRAMES; i++)
            fr[2u * i] = fr[2u * i + 1u] = (int32_t)in[i];         /* (Q15: the ISR's int32 samples are Q15 here) */
        if (mode == 3 && b % 3u != 2u) {      /* (platform.c APP_TAIL_EARLY: each piece of 32 at the history's start,
                                                 * a tap of 32: tail_piece) */
            for (p = 0; p < HALF_FRAMES; p += 32u) {
                for (i = 0; i < 32u; i++) {
                    UAC_HIST[2u * i] = (int16_t)fr[2u * (p + i)];
                    UAC_HIST[2u * i + 1u] = (int16_t)fr[2u * (p + i) + 1u];
                }
                uac_tap_hist(32u);
            }
        } else if (mode == 1 || ((mode == 2 || mode == 3) && b % 3u != 2u) || (mode == 3 && b % 6u == 5u)) {
            for (i = 0; i < HALF_FRAMES; i++) {
                UAC_HIST[2u * i] = (int16_t)fr[2u * i];
                UAC_HIST[2u * i + 1u] = (int16_t)fr[2u * i + 1u];
            }
            uac_tap_hist(HALF_FRAMES);
        } else
            for (p = 0; p < HALF_FRAMES; p += 32u)
                uac_tap(fr + 2u * p, 32u);
        for (k = 0, i = ua_r; i != ua_w; i++) {
            uint32_t v = ua_ring[i & (UA_N - 1u)];
            lr |= (v & 0xFFFFu) != v >> 16;
            outb[k++] = (int16_t)(v & 0xFFFFu);
        }
        fwrite(outb, sizeof outb[0], k, stdout);
        ua_r = ua_w;
        b++;
    }
    fprintf(stderr, "S %u\n", uac.lk_S);
    return uac.overruns ? 1 : lr ? 3 : 0;
}
