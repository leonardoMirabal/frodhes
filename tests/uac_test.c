/* SPDX-License-Identifier: GPL-3.0-only */
/* The USB audio IN ring under the FM-1's timing, with the firmware's own functions: usb.c's uac_render_start,
 * uac_tap / uac_tap48 and uac_packet, compiled here unchanged (tests/py/test_uac_ring.py builds this file against the
 * working tree and against a released tree (v0.9.4), to show the old band fail where the new one holds).
 *
 * The model (times in ns of real time):
 *   - the I2S runs ppm fast (the FM-1's crystal, ~450-540 ppm against a computer), so an audio ISR is due every
 *     256 / (44100 (1 + ppm)) s; it starts `lat` late (TIMER5 outranks it), calls uac_render_start, renders for
 *     `rend` (the app: the engine + the effects), then taps the block in 8 pieces of 32 frames spread over `tail`
 *     (the MASTER pass, and at 48 kHz the resampler: ~370 us);
 *   - TIMER5 polls USB every 0.5 ms of the FM-1's clock (nested in the render too, as platform.c does): it queues a
 *     packet (uac_packet) when the last one has gone;
 *   - the host takes the queued packet at its IN token, once per 1 ms USB frame, at a fixed place in the frame
 *     (`phase`) plus a jitter;
 *   - the render time varies per block by +-`rj`; a render longer than the block delays the next ISR (the I2S
 *     half is not free before).
 * One line per case: the counters (underrun packets, repeated frames, missed, overruns, adj up / down), the lowest
 * fill just before a block is tapped in (the true low point), the mean fill at the IN tokens (frames) and, at 44.1
 * kHz, the stream checked frame by frame (each frame is a counter: a repeat or a gap shows exactly), the largest
 * shortfall in one packet and how many underruns came a whole number of beats (5 blocks = 29 packets) apart.
 * The mean fill over the throughput is the mean time a frame waits in the ring (Little's law): the USB latency.
 *
 * What it shows (2026-10-24): 0.9.4's band runs dry once a render (+ the tail) passes ~1.6-2.0 ms, and the
 * underruns grow with the render. The FM-1's own counters (CC 116, 0.9.4's band) agree: the true low point was 0
 * in every hil_cpu scenario (9 voices, with and without effects) while the fill at the render's start read 72-231,
 * 10-14 underrun packets per scenario without effects and 414-543 with every effect (whole ISR 4084-4476 us), the
 * controller pushing adj_up thousands of times (it cannot see the dip). The raised band holds every case here with
 * a packet and more in hand at the low point.
 *
 *   cc -O1 -w -Ifirmware/src -o build/uac/uac_test tests/uac_test.c && build/uac/uac_test [--quick]
 *   build/uac/uac_test --one R48 PPM REND_US RJ_US TAIL_US LAT_US PHASE_US TJ_US SECS    (one case)
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

static uint64_t rng = 88172645463325252ull;
static double urand(void)                              /* 0..1 */
{
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return (double)(rng >> 11) / 9007199254740992.0;
}

typedef struct {
    int r48;
    double ppm, rend_us, rj_us, tail_us, lat_us, phase_us, tj_us, secs;
} cfg_t;
typedef struct {
    uint32_t underruns, repeats, missed, overruns, adj_up, adj_down, low, seq_bad, maxshort, single;
    double fill_mean, beat_frac;               /* underruns spaced at a multiple of the beat (+-1.5 ms) */
} res_t;

static res_t run(const cfg_t *c)
{
    res_t r;
    double fs = 44100.0 * (1.0 + c->ppm * 1e-6);
    double per = 256.0 / fs * 1e9, poll = 0.5e6 / (1.0 + c->ppm * 1e-6), end = c->secs * 1e9;
    double t_due = 0.37e6, t_isr = t_due, t_poll = 0.1e6, t_tok = c->phase_us * 1e3, t_tap = -1.0, tap_dt = 0;
    double tt = t_tok, t_end = 0;                   /* this IN token (its jitter drawn once); the last tap */
    double fill_sum = 0, t_last_u = -1, beat = 5.0 * per;
    uint32_t nint = 0, nbeat = 0;
    uint32_t taps = 0, ntok = 0, ready = 0, started = 0, ctr = 0, expect = 0, seen = 0, i, n;
    int32_t blk[2u * HALF_FRAMES];
    uint32_t pk[UA_MAXF];
    memset(&r, 0, sizeof r);
    r.low = 0xFFFFFFFFu;
    memset(&uac, 0, sizeof uac);
    memset(ua_ring, 0, sizeof ua_ring);
    ua_w = ua_r = 0;
    usb.up = usb.config = 1;
    uac.alt = 1;
    uac.flowing = 1;
    uac.r48 = (uint8_t)c->r48;
    uac.fill_lo = 0xFFFFFFFFu;
    for (;;) {
        double t = t_isr;
        int ev = 0;
        if (t_poll < t) { t = t_poll; ev = 1; }
        if (taps && t_tap < t) { t = t_tap; ev = 2; }
        if (tt < t) { t = tt; ev = 3; }
        if (t > end)
            break;
        if (ev == 0) {                                  /* the audio ISR starts (late by up to lat) */
            double rend = c->rend_us + (urand() * 2.0 - 1.0) * c->rj_us;
            uac_render_start();
            for (i = 0; i < HALF_FRAMES; i++) {         /* a counter in the frames: the stream is checked by it */
                int32_t v = (int32_t)(ctr++ & 0x7FFFu);
                blk[2u * i] = v;
                blk[2u * i + 1u] = -v;
            }
            tap_dt = c->tail_us * 1e3 / 8.0;
            t_tap = t + (rend < 50.0 ? 50.0 : rend) * 1e3 + tap_dt;
            taps = 8;
            t_end = t_tap + 7.0 * tap_dt;
            t_due += per;
            t_isr = t_due + urand() * c->lat_us * 1e3;
            if (t_isr < t_end + 1e3)                    /* the block ran over: the next ISR waits for it */
                t_isr = t_end + 1e3;
        } else if (ev == 1) {                           /* TIMER5's USB poll: queue the next packet */
            if (!ready && usb.config) {
                uint32_t r0 = ua_r;
                n = uac_packet(pk);
                if (uac.go) {
                    uint32_t k = ua_r - r0;
                    r.repeats += n - k;
                    if (n - k > r.maxshort) r.maxshort = n - k;
                    if (n - k == 1u) r.single++;
                    if (n > k) {                        /* the spacing of the underruns: whole beats? */
                        if (t_last_u >= 0) {
                            double q = (t - t_last_u) / beat, f = q - (double)(long)(q + 0.5);
                            nint++;
                            if ((f < 0 ? -f : f) * beat < 1.5e6) nbeat++;
                        }
                        t_last_u = t;
                    }
                    if (!c->r48 && started) {           /* 44.1: every frame is the one after the last */
                        for (i = 0; i < n; i++) {
                            uint32_t v = pk[i] & 0x7FFFu;
                            if (!seen) { if (v) { seen = 1; expect = v; } else continue; }
                            if (v != (expect & 0x7FFFu)) r.seq_bad++;
                            expect = v + 1u;
                        }
                    }
                }
                ready = 1;
            }
            t_poll += poll;
        } else if (ev == 2) {                           /* a 32-frame piece of the block goes into the ring */
            uint32_t j = 8u - taps;
            if (j == 0u) {
                uint32_t f = ua_w - ua_r;
                if (uac.feed && uac.go && started && f < r.low) r.low = f;
            }
            uac_tap(blk + 2u * 32u * j, 32u);
            taps--;
            t_tap += tap_dt;
        } else {                                        /* the host's IN token */
            if (ready) { ready = 0; started = 1; }
            else if (started) r.missed++;
            if (started && uac.go) { fill_sum += (double)(ua_w - ua_r); ntok++; }
            t_tok += 1e6;
            tt = t_tok + (urand() - 0.5) * c->tj_us * 1e3;
        }
    }
    r.underruns = uac.underruns;
    r.overruns = uac.overruns;
    r.adj_up = uac.adj_up;
    r.adj_down = uac.adj_down;
    r.fill_mean = ntok ? fill_sum / ntok : 0;
    r.beat_frac = nint ? (double)nbeat / nint : -1;
    return r;
}

static void line(const char *tag, const cfg_t *c, const res_t *r)
{
    printf("%s rate %d ppm %.0f rend %.0f rj %.0f tail %.0f lat %.0f phase %.0f tj %.0f secs %.0f "
           "underruns %u repeats %u single %u maxshort %u missed %u overruns %u adj_up %u adj_down %u low %d fill_mean %.1f "
           "seq_bad %u beat_frac %.2f\n",
           tag, c->r48 ? 48000 : 44100, c->ppm, c->rend_us, c->rj_us, c->tail_us, c->lat_us, c->phase_us, c->tj_us,
           c->secs, r->underruns, r->repeats, r->single, r->maxshort, r->missed, r->overruns, r->adj_up, r->adj_down,
           r->low == 0xFFFFFFFFu ? -1 : (int)r->low, r->fill_mean, r->seq_bad, r->beat_frac);
}

int main(int argc, char **argv)
{
    /* the sweep: both rates; the FM-1 fast (450, 540 ppm), exact, and slow (-540: another unit, another computer);
     * the render 0.5..5.4 ms with +-0.2 ms per block (5.0 ms: the shedder's line); the tail (60 us at 44.1, 370 us
     * at 48 kHz: the resampler); the ISR up to 100 us late; the IN token at 5 places in the frame with +-60 us of
     * jitter; 20 s each (--quick: 5 s) */
    static const double rends[] = {500, 1000, 1500, 2000, 2500, 2900, 3300, 3700, 4100, 4500, 5000, 5400};
    static const double ppms[] = {540, 450, 0, -540};
    static const double phases[] = {50, 250, 450, 650, 850};
    cfg_t c;
    int a, b, d, e;
    double secs = argc > 1 && !strcmp(argv[1], "--quick") ? 5 : 20;
    if (argc == 11 && !strcmp(argv[1], "--one")) {     /* --one r48 ppm rend rj tail lat phase tj secs */
        res_t r;
        c.r48 = atoi(argv[2]); c.ppm = atof(argv[3]); c.rend_us = atof(argv[4]); c.rj_us = atof(argv[5]);
        c.tail_us = atof(argv[6]); c.lat_us = atof(argv[7]); c.phase_us = atof(argv[8]); c.tj_us = atof(argv[9]);
        c.secs = atof(argv[10]);
        r = run(&c);
        line("case", &c, &r);
        return 0;
    }
    printf("UA_N %u UA_LO %u UA_HI %u UA_PRIME %u", UA_N, UA_LO, UA_HI, UA_PRIME);
#if FELUCCA_UAC_48K
    printf(" UA_LO48 %u UA_HI48 %u UA_PRIME48 %u", UA_LO48, UA_HI48, UA_PRIME48);
#endif
    printf("\n");
    for (a = 0; a < 2; a++)
        for (b = 0; b < (int)(sizeof ppms / sizeof ppms[0]); b++)
            for (d = 0; d < (int)(sizeof rends / sizeof rends[0]); d++)
                for (e = 0; e < (int)(sizeof phases / sizeof phases[0]); e++) {
                    res_t r;
                    memset(&c, 0, sizeof c);
                    c.r48 = a;
                    c.ppm = ppms[b];
                    c.rend_us = rends[d];
                    c.rj_us = 200;
                    c.tail_us = a ? 370 : 60;
                    c.lat_us = 100;
                    c.phase_us = phases[e];
                    c.tj_us = 120;
                    c.secs = secs;
                    r = run(&c);
                    line("case", &c, &r);
                }
    return 0;
}
