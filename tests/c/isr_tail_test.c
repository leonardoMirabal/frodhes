/* SPDX-License-Identifier: GPL-3.0-only */
/* platform.c's audio ISR (fm1_alnk0_irq: MASTER, the clip, the USB tap, the codec's shift) on the host, against the
 * three-pass tail it replaced (Dinghy 0.4 + Frodhes as of 0.9.5's RC, reference_tail below). tests/py/test_isr_tail.py
 * cuts the ISR out of firmware/src/platform.c (from "static int32_t abuf" to "extern void isr_alnk0") into
 * build/host/isr_tail/tail.inc and builds this with it. app_audio here writes every sample with random values (around
 * the clip, far over it, silence); for MASTER 0 .. 4088 and USB VOL on / off, the ISR's output and the samples it hands
 * the USB tap must be the reference's, bit for bit. Exit code: the number of failures. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define HALF_FRAMES 256u
#define HALF_WORDS (HALF_FRAMES * 2u)
#define APP_OUT_SHIFT 8u
#define APP_OUT_WRITTEN 1
#define FM1_TICKS_PER_US 24u
#define FM1_AUDIO_HALF 0x80u
#define UAC_ONLY(x) x
#define HAL_KEEP_REG(v) __asm__("" : "+r"(v))
static uint32_t fake_now;
static uint32_t fm1_ticks(void) { return fake_now += 24u; }
static volatile uint8_t in_audio;
static uint8_t fm1_audio_pending(void) { return FM1_AUDIO_HALF; }
static void fm1_audio_ack_aux(uint8_t p) { (void)p; }
static uint32_t fm1_audio_free_half(void) { return 1u; }
static void fm1_audio_ack_half(void) {}
static struct { uint8_t feed, go; } uac = {1, 1};
static uint32_t ua_w, ua_r;
static void uac_render_start(void) {}
static int32_t tapped[HALF_WORDS];
static uint32_t tapped_n;
static void uac_tap(const int32_t *out, uint32_t n)
{
    memcpy(tapped + tapped_n, out, 2u * n * sizeof out[0]);
    tapped_n += 2u * n;
}
static int32_t app_in[HALF_WORDS];
static void app_audio(int32_t *out, uint32_t n) { memcpy(out, app_in, 2u * n * sizeof out[0]); }
static void app_main(void) {}
#include "tail.inc"

static int32_t ref_out[HALF_WORDS], ref_tap[HALF_WORDS];
/* the tail as it was: three passes over the block; a MASTER new since the last block (prev, -1: none) ramps from
 * prev frame by frame (0.9.6, BUGHUNT #20: platform.c master_ramp, Q8: the frame f's gain (prev 256 + (f + 1)(mq -
 * prev)) >> 8, mq at the last frame) */
static int32_t ref_gain(int32_t mq, int32_t prev, uint32_t f)
{
    return prev < 0 || prev == mq ? mq : ((prev << 8) + (int32_t)(f + 1u) * (mq - prev)) >> 8;
}
static void reference_tail(int32_t mq, int32_t prev, int pre)
{   /* 0.9.7 (#8): the jack keeps MASTER's product at 24 bits (x * g >> (12 - APP_OUT_SHIFT), clamped at 32767 <<
     * APP_OUT_SHIFT when USB follows MASTER); the USB tap is the 16-bit sample as before */
    int32_t o[HALF_WORDS], jack[HALF_WORDS];
    uint32_t i, b, k = 0;
    const int32_t top = 32767 << APP_OUT_SHIFT, sh = 12 - (int32_t)APP_OUT_SHIFT;
    memset(o, 0, sizeof o);
    memcpy(o, app_in, sizeof o);
    if (pre) {
        for (i = 0; i < HALF_WORDS; i++)
            o[i] = o[i] > 32767 ? 32767 : o[i] < -32767 ? -32767 : o[i];
        for (b = 0; b < HALF_FRAMES; b += 32u, k += 64u)
            memcpy(ref_tap + k, o + 2u * b, 64u * sizeof o[0]);
        for (i = 0; i < HALF_WORDS; i++)
            jack[i] = (o[i] * ref_gain(mq, prev, i / 2u)) >> sh;
    } else {
        for (i = 0; i < HALF_WORDS; i++) {
            int32_t p = o[i] * ref_gain(mq, prev, i / 2u), v = p >> 12, j = p >> sh;
            o[i] = v > 32767 ? 32767 : v < -32767 ? -32767 : v;
            jack[i] = j > top ? top : j < -top ? -top : j;
        }
        for (b = 0; b < HALF_FRAMES; b += 32u, k += 64u)
            memcpy(ref_tap + k, o + 2u * b, 64u * sizeof o[0]);
    }
    memcpy(ref_out, jack, sizeof jack);
}

int main(void)
{
    static const int32_t MQ[] = {0, 1, 2, 255, 2048, 4087, 4088};
    uint32_t seed = 1u, t, i, bad_out = 0, bad_tap = 0, runs = 0;
    int32_t prev = -1;                                    /* (the MASTER of the block before: platform.c keeps it) */
    int pre;
    for (t = 0; t < 400u; t++)
        for (pre = 0; pre < 2; pre++) {
            int32_t mq = MQ[t % (sizeof MQ / sizeof MQ[0])];
            for (i = 0; i < HALF_WORDS; i++) {            /* silence, around the clip, far over it (|v| * 4088 fits) */
                seed = seed * 1103515245u + 12345u;
                switch ((t / 7u) % 4u) {
                case 0: app_in[i] = 0; break;
                case 1: app_in[i] = (int32_t)(seed >> 16) % 65536 - 32768; break;
                case 2: app_in[i] = (int32_t)(seed >> 8) % 1048576 - 524288; break;
                default: app_in[i] = 32767 - (int32_t)((seed >> 20) & 3u) * (i & 1u ? -1 : 1) * (int32_t)(i & 7u); break;
                }
            }
            master_q12 = mq;
            usb_pre_master = (uint8_t)pre;
            for (i = 0; i < 2u * HALF_WORDS; i++) abuf[i] = (int32_t)(0xA5A5A5A5u ^ i);   /* (APP_OUT_WRITTEN) */
            tapped_n = 0;
            fm1_alnk0_irq();
            reference_tail(mq, prev, pre);
            prev = mq;
            if (memcmp(abuf + HALF_WORDS, ref_out, sizeof ref_out)) bad_out++;
            if (tapped_n != HALF_WORDS || memcmp(tapped, ref_tap, sizeof ref_tap)) bad_tap++;
            runs++;
        }
    printf("%s output %u / %u blocks differ, USB tap %u\n", bad_out || bad_tap ? "FAIL" : "ok  ", bad_out, runs, bad_tap);
    return (int)(bad_out + bad_tap);
}
