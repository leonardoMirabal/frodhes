/* SPDX-License-Identifier: GPL-3.0-only */
/* The USB audio IN path at 44.1 kHz, frame by frame, for hours of simulated packets: usb.c's own uac_render_start,
 * uac_tap, uac_packet AND uac_service (the EP4 queue: TxPktRdy, UnderRun, the missed / stall counters), compiled here
 * unchanged against a model of the SIE's EP4 registers (the fm1_usb_sie_* and fm1_usb_ep4_send calls are routed to
 * the model below; the rest of usb.c is not called). Every frame the render produces is a counter (30 bits: L the low
 * 15, R the high 15), so the host side sees exactly whether a frame came twice or went missing.
 *
 * What runs (times in ns of real time):
 *   - the audio ISR every 256 frames of the FM-1's clock (ppm fast or slow), late by up to `lat`; uac_render_start,
 *     a render of rend +- rj, then the block tapped in 8 pieces of 32 frames over `tail` (platform.c);
 *   - TIMER5 every 0.5 ms of the FM-1's clock: uac_service. It also preempts the audio ISR INSIDE usb.c's functions:
 *     RING_PUBLISH() (every ordering point of the ring: between the ring's slots and its index, in uac_tap, uac_packet,
 *     uac_render_start's prime) runs a nested poll with probability `pre`;
 *   - the host: one IN token per 1 ms USB frame of its own clock, at `phase` +- jitter `tj` (the SOF jitter); with
 *     probability `skip` a frame has no token (a host skip), and now and then a run of 2..60 frames without one (a
 *     busy host: 40 polls = 20 ms of a queued packet stops the stream, uac.stalls); an IN token also lands at random
 *     inside uac_service's own SIE accesses (the hardware does not wait for the CPU);
 *   - the DMA reads ep4tx when the IN token comes (the hardware's single buffer).
 * The checks, per received packet: a frame equal to the one before is allowed only in a packet whose build counted
 * an underrun (uac.underruns: the copies), a gap only after a counted event (overruns, stalls: the ring re-primed),
 * and silence only while the ring is not primed. Anything else is a frame sent twice or dropped with every counter
 * clean: `dup_uncounted` / `drop_uncounted`, both must be 0. `--sie-fault P` makes the INDEX write and the TXCSR1
 * read each time out with probability P (sie_rd then returns 0): before 0.9.7 uac_service took that 0 for "free" and
 * queued a new packet over the waiting one (its frames lost, no counter: `overwritten`); now a timed-out access counts
 * uac.sie_to and the packet waits, so the check is the same: no frame lost or repeated uncounted.
 *
 * -DFELUCCA_UAC_PHASE=1 (0.9.9): TIMER5 every 0.1 ms of the FM-1's clock calls uac_tick (the EP4 poll on its own
 * phase) instead of uac_service every 0.5 ms. `--cli US MS`: an interrupts-off stretch of US us every ~MS ms (at a
 * random place): the TIMER5 ticks it covers fold into one at its end (uac_tick's n; without the phase the poll runs
 * at its end, kinder than platform.c), the host's tokens do not wait. `true_missed` counts the IN tokens that found nothing once the stream was flowing (the
 * host got no data that frame); `--tj US` sets the token jitter (default 400: +-200 us).
 *
 *   cc -O1 -w -Ifirmware/src -o build/uac_test/uac_seq_test tests/uac_seq_test.c && build/uac_test/uac_seq_test
 *   build/uac_test/uac_seq_test --hours H [--sie-fault P] [--seed S] [--cli US MS [--cli-exact] [--align]] [--tj US]
 * (--cli-exact: MS is the exact gap after each stretch, e.g. a flash erase's slice train 282 / 0.75; --align: each
 * stretch waits until usb.c's uac_slice_ok says yes (2 ms at most), as fm1_flash.h's FL_SLICE_OK does)
 *   (-DUAC_OLD: a tree before 0.9.7)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void sim_preempt(void);
#define RING_PUBLISH() sim_preempt()
#define HALF_FRAMES 256u
#define FELUCCA_UAC 1
static double now;                                     /* the time (ns) */
#define UAC_STAMP() ((uint32_t)(now / 1e3 * 24.0))     /* (24 MHz ticks, as fm1_ticks: usb.c uac_slice_ok, --align,
                                                         * the locked stream's fill reading) */
#define UAC_SLICE_WIN (200u * 24u)
#define UAC_STAMP_MS 24000u
static void fm1_delay_ms(uint32_t ms) { (void)ms; }   /* (usb_start: never called here) */
#include "../hal/fm1_usb.h"                         /* the real helpers first; usb.c's calls go to the model: */
static uint32_t sim_sie_on(void);
static void sim_sie_wr_start(uint32_t r, uint32_t v);
static void sim_sie_rd_start(uint32_t r);
static uint32_t sim_sie_done(void);
static uint32_t sim_sie_data(void);
static void sim_ep4_send(void *p, uint32_t n);
#define fm1_usb_sie_on sim_sie_on
#define fm1_usb_sie_wr_start sim_sie_wr_start
#define fm1_usb_sie_rd_start sim_sie_rd_start
#define fm1_usb_sie_done sim_sie_done
#define fm1_usb_sie_data sim_sie_data
#define fm1_usb_ep4_send sim_ep4_send
static void sim_usb_reset(void);                       /* (usb_start, --ctl-reset: the controller reset; the rest no-ops) */
static void sim_usb_nop(void) {}
#define fm1_usb_reset sim_usb_reset
#define fm1_usb_attach(b) sim_usb_nop()
#define fm1_usb_ep_enable(m) sim_usb_nop()
#include "usb.c"

#ifdef UAC_OLD                                         /* (a tree before 0.9.7: no uac.sie_to) */
#define UAC_SIE_TO 0u
#else
#define UAC_SIE_TO uac.sie_to
#endif
#ifndef FELUCCA_UAC_PHASE
#define FELUCCA_UAC_PHASE 0
#endif
static uint64_t rng = 88172645463325252ull;
static double urand(void)                              /* 0..1 */
{
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return (double)(rng >> 11) / 9007199254740992.0;
}

/* ---- the SIE model: INDEX and EP4's TXCSR1 (bit 0 TxPktRdy, bit 2 UnderRun) */
static uint32_t sie_index, ep4_csr, sie_last, sie_fail;
static uint32_t ep4_cnt;                               /* bytes of the queued packet (fm1_usb_ep4_send) */
static double sie_fault, tok_in_sie;
static int in_audio_ctx, in_timer;
static double tok_at;                             /* the time; the pending IN token's (this USB frame's) */
static int tok_pending;
static void host_token(void);
static void sie_token(void)                            /* this frame's token, if it is due within 50 us: now */
{
    if (in_timer && tok_pending && tok_at - now < 50e3 && urand() < tok_in_sie) {
        tok_pending = 0;
        host_token();
    }
}
static uint32_t sim_sie_on(void) { return 1; }
static uint64_t ep_wrong;                              /* TXCSR1 read or written with INDEX not 4 while the stream runs
                                                        * (an EP4 poll on another endpoint: EP0's CSR0 after a reset) */
static void sim_usb_reset(void)
{
    sie_index = 0;                                     /* the controller reset: INDEX 0, the queued packet gone */
    ep4_csr = 0;
}
static uint64_t blk_id;                                /* the SIE accesses in each 256-frame audio block (5805 us): */
static uint32_t blk_acc, blk_max;                      /* the poll's cost inside the audio ISR, worst block (blk_max) */
static void sie_count(void)
{
    uint64_t b = (uint64_t)(now / 5805e3);
    if (b != blk_id) {
        blk_id = b;
        blk_acc = 0;
    }
    if (++blk_acc > blk_max)
        blk_max = blk_acc;
}
static void sim_sie_wr_start(uint32_t r, uint32_t v)
{
    sie_token();
    sie_count();                                       /* the host does not wait for our SIE accesses */
    sie_fail = sie_fault > 0 && r == S_INDEX && urand() < sie_fault;
    if (sie_fail)
        return;                                        /* (timed out: not written) */
    if (r == S_TXCSR1 && sie_index != 4u && in_timer)
        ep_wrong++;
    if (r == S_INDEX)
        sie_index = v;
    else if (r == S_TXCSR1 && sie_index == 4u) {
        if (v & 0x08u)                                 /* FlushFIFO */
            ep4_csr &= ~1u;
        ep4_csr = (ep4_csr & ~0x05u) | (v & 0x01u);    /* TxPktRdy set; UnderRun written 0 */
    }
}
static uint32_t rd_r_before, rd_under_before;          /* the ring and the counter as uac_packet starts */
static uint64_t n_polls;                               /* EP4 TXCSR1 reads (uac_service / uac_tick's polls and looks) */
static double poll_last, gap_max, take_t, lat_max;     /* the longest time between two EP4 polls (as the probe's svc gap)
                                                        * and from a host take to the next packet queued (ns) */
static void sim_sie_rd_start(uint32_t r)
{
    sie_token();
    sie_count();
    sie_fail = sie_fault > 0 && r == S_TXCSR1 && urand() < sie_fault;
    sie_last = r == S_TXCSR1 && sie_index == 4u ? ep4_csr : 0u;
    if (r == S_TXCSR1 && sie_index != 4u && in_timer)
        ep_wrong++;
    if (r == S_TXCSR1 && sie_index == 4u) {
        n_polls++;
        if (poll_last > 0 && uac.flowing && now - poll_last > gap_max)
            gap_max = now - poll_last;
        poll_last = now;
    }
    rd_r_before = ua_r;
    rd_under_before = uac.underruns;
}
static uint32_t sim_sie_done(void) { return !sie_fail; }
static uint32_t sim_sie_data(void) { return sie_last; }

/* ---- the queued packet's bookkeeping (what uac_packet did when it built ep4tx) */
static uint32_t q_n, q_under, q_overwritten;
static int cli_pending;                                /* (--align, below) */
static double ctl_ms, t_ctl;                           /* --ctl-reset MS: usb_start every MS ms in the main loop, the
                                                        * stream still configured (usb_retry after SIE timeouts) */
static double cli_due;
static void cli_arm(double t);
static void sim_ep4_send(void *p, uint32_t n)
{
    (void)p;
    if (ep4_csr & 1u)
        q_overwritten++;                               /* a packet still queued: its frames are lost */
    ep4_cnt = n;
    q_n = n / 4u;
    if (take_t > 0 && uac.flowing && now - take_t > lat_max)
        lat_max = now - take_t;
    take_t = 0;
    q_under = uac.underruns != rd_under_before;
}

/* ---- the host's side: the checker */
typedef struct {
    uint64_t pkts, frames, taken, dup_uncounted, drop_uncounted, dup_counted, gaps_counted, silence, n_bad, true_missed;
    uint32_t nmin, nmax;
    uint64_t last45;
} res_t;
static int uac_restarted = 1;
static int sim_r48;                                    /* --r48: the host set 48 kHz (uac.r48; the ring resampled) */
static res_t R;
static uint32_t h_prev, h_expect, h_seen;
/* the frames an overrun dropped (uac_tap counted it): [lo, hi) of the counter, in order */
#define DQ 4096u
static uint32_t dq_lo[DQ], dq_hi[DQ], dq_w, dq_r;
static int gap_counted(uint32_t from, uint32_t to)     /* the frames from..to-1 went in counted drops */
{
    while (dq_r != dq_w && dq_lo[dq_r % DQ] != from && dq_hi[dq_r % DQ] <= from)
        dq_r++;                                        /* (drops before this point: re-primed past them) */
    while (dq_r != dq_w && dq_lo[dq_r % DQ] == from) {
        from = dq_hi[dq_r % DQ];
        dq_r++;
        if (from == to)
            return 1;
    }
    return 0;
}
static void host_token(void)
{
    uint32_t i, n, *d = (uint32_t *)ep4tx;
    if (!(ep4_csr & 1u)) {
        ep4_csr |= 0x04u;                              /* nothing queued: UnderRun */
        if (uac.queued && uac.flowing)
            R.true_missed++;                           /* (the host gets no data this frame) */
        return;
    }
    ep4_csr &= ~1u;
    take_t = now;
    n = ep4_cnt / 4u;
    R.taken++;
    if (n < R.nmin) R.nmin = n;
    if (n > R.nmax) R.nmax = n;
#if FELUCCA_UAC_LOCK
    if (sim_r48 ? n != 48u : n < 44u || n > 45u)       /* (locked: only the nominal sizes) */
        R.n_bad++;
    if (n == 45u) {                                    /* (and 45 exactly once in every 10 packets) */
        if (R.last45 && R.taken - R.last45 != 10u && !uac_restarted) R.n_bad++;
        R.last45 = R.taken;
    }
    uac_restarted = 0;
    R.frames += n;
    return;                                            /* (the frames' values: a correction interpolates two counters) */
#else
    if (n < 43u || n > 46u)
        R.n_bad++;
#endif
    for (i = 0; i < n; i++) {
        uint32_t f = d[i], v = (f & 0x7FFFu) | (f >> 16 & 0x7FFFu) << 15;
        if (!f) {                                      /* silence: not primed (yet / again) */
            R.silence++;
            h_seen = 0;
            continue;
        }
        if (h_seen && f == h_prev) {
            if (q_under) R.dup_counted++;
            else R.dup_uncounted++;
        } else if (h_seen && v != h_expect) {
            if (gap_counted(h_expect, v)) R.gaps_counted++;
            else R.drop_uncounted++;
        }
        h_seen = 1;
        h_prev = f;
        h_expect = (v + 1u) & 0x3FFFFFFFu;
        if (!h_expect) h_expect = 1u;
    }
    R.frames += n;
}

static double p_pre, t_poll, poll_dt;
static void sim_preempt(void)                          /* RING_PUBLISH: TIMER5 may preempt the audio ISR here */
{
    if (in_audio_ctx && !in_timer && urand() < p_pre) {
        in_timer = 1;
#if FELUCCA_UAC_PHASE
        uac_tick(1);                                   /* (the next tick, taken here: TIMER5 every 0.1 ms) */
        t_poll += poll_dt;
#else
        uac_service();
#endif
        in_timer = 0;
    }
}
static double cli_us, cli_ms, cli_at, cli_end;         /* --cli: the next interrupts-off stretch [cli_at, cli_end) */
static int cli_exact, cli_align;          /* --cli-exact: MS is the gap after each stretch, no randomness;
                                                        * --align: a stretch waits for uac_slice_ok (at most 2 ms),
                                                        * checked at each event (cli_due: when due) */
static void cli_next(double from)                      /* the next stretch, from `from` */
{
    double gap = cli_exact ? cli_ms * 1e6 : cli_ms * (0.5 + urand()) * 1e6;
    if (cli_ms <= 0) { cli_at = cli_end = 1e30; return; }
    if (cli_align) {
        cli_pending = 1;
        cli_due = from + gap;
        cli_at = cli_end = 1e30;
        return;
    }
    cli_at = from + gap;
    cli_end = cli_at + cli_us * 1e3;
}
static void cli_arm(double t)                          /* --align: start the waiting stretch at t */
{
    cli_pending = 0;
    cli_at = t;
    cli_end = t + cli_us * 1e3;
}
static double t_fold;                                  /* the tick its covered ticks fold into, k_fold of them */
static uint32_t k_fold;
static uint64_t n_cli;

typedef struct {
    double ppm, rend_us, rj_us, tail_us, lat_us, phase_us, tj_us, skip, burst, hours;
} cfg_t;

static void run(const cfg_t *c)
{
    double fs = 44100.0 * (1.0 + c->ppm * 1e-6);
    double per = 256.0 / fs * 1e9, poll = (FELUCCA_UAC_PHASE ? 0.1e6 : 0.5e6) / (1.0 + c->ppm * 1e-6);
    double end = c->hours * 3600e9;
    double t_due = 0.37e6, t_isr = t_due, t_frame = c->phase_us * 1e3, t_tap = -1.0, tap_dt = 0;
    double tt = t_frame, t_end = 0;
    uint32_t taps = 0, ctr = 1, i, skip_run = 0;
    int32_t blk[2u * HALF_FRAMES];
    t_poll = 0.1e6;
    poll_dt = poll;
    cli_pending = 0;
    cli_next(0.0);
    n_cli = 0;
    n_polls = 0;
    blk_max = blk_acc = 0;
    blk_id = 0;
    ep_wrong = 0;
    t_ctl = ctl_ms > 0 ? ctl_ms * 1e6 : 1e30;
    poll_last = gap_max = take_t = lat_max = 0;
    t_fold = 1e30;
    memset(&R, 0, sizeof R);
    R.nmin = 0xFFFFFFFFu;
    memset(&uac, 0, sizeof uac);
    memset(ua_ring, 0, sizeof ua_ring);
    memset(ep4tx, 0, sizeof ep4tx);
    ua_w = ua_r = 0;
    ep4_csr = 0;
    q_overwritten = 0;
    h_seen = 0;
    dq_w = dq_r = 0;
    tok_pending = 1;
    usb.up = usb.config = 1;
    uac.alt = 1;
    uac.r48 = (uint8_t)sim_r48;
    uac.fill_lo = 0xFFFFFFFFu;
    for (;;) {
        double t = t_isr;
        int ev = 0;
        if (t_poll < t) { t = t_poll; ev = 1; }
        if (taps && t_tap < t) { t = t_tap; ev = 2; }
        if (tt < t) { t = tt; ev = 3; }
        if (t_fold < t) { t = t_fold; ev = 4; }
        if (t > end)
            break;
        if (ev == 1 && t >= cli_at && t < cli_end) {   /* TIMER5 in an interrupts-off stretch (the audio ISR's own
                                                         * lateness is `lat`; the render's timing is not the point) */
            k_fold = 0;                                /* the ticks it covers: one pending flag, taken at its end */
            while (t_poll < cli_end) { t_poll += poll; k_fold++; }
            t_fold = cli_end;
            continue;
        }
        if (ev == 4) {                                 /* the folded tick, at the stretch's end */
            now = t;
            tok_at = tt;
            t_fold = 1e30;
            in_timer = 1;
#if FELUCCA_UAC_PHASE
            uac_tick(k_fold);
#else
            uac_service();                             /* (2 kHz: the polls it covers become one, at once: kinder than
                                                         * platform.c, whose poll then waits for its tick in the 5) */
#endif
            in_timer = 0;
            continue;
        }
        if (t >= t_ctl) {                              /* --ctl-reset: usb_retry's usb_start (usb.up 0 after the
                                                         * SIE timeouts), between two events of the ISRs */
            now = t;
            usb.up = 0;
            usb_start();
            t_ctl += ctl_ms * 1e6;
        }
        if (cli_pending && t >= cli_due) {             /* --align: the main loop's flash slice asks usb.c */
            now = t;
#ifdef UAC_OLD
            if (t >= cli_due + 2e6)                    /* (a tree before 0.9.9: no uac_slice_ok) */
#else
            if (uac_slice_ok() || t >= cli_due + 2e6)  /* (fm1_flash.h: FL_ALIGN_US 2 ms at most) */
#endif
                cli_arm(t + 2e3);
        }
        if (t >= cli_end) {                            /* the next stretch, ~cli_ms on */
            n_cli++;
            cli_next(cli_end);
        }
        now = t;
        tok_at = tt;
        if (ev == 0) {                                  /* the audio ISR starts */
            double rend = c->rend_us + (urand() * 2.0 - 1.0) * c->rj_us;
            in_audio_ctx = 1;
            uac_render_start();
            in_audio_ctx = 0;
            for (i = 0; i < HALF_FRAMES; i++, ctr = (ctr + 1u) & 0x3FFFFFFFu ? (ctr + 1u) & 0x3FFFFFFFu : 1u) {
                blk[2u * i] = (int32_t)(ctr & 0x7FFFu);
                blk[2u * i + 1u] = (int32_t)(ctr >> 15 & 0x7FFFu);
            }
            tap_dt = c->tail_us * 1e3 / 8.0;
            t_tap = t + (rend < 50.0 ? 50.0 : rend) * 1e3 + tap_dt;
            taps = 8;
            t_end = t_tap + 7.0 * tap_dt;
            t_due += per;
            t_isr = t_due + urand() * c->lat_us * 1e3;
            if (t_isr < t_end + 1e3)
                t_isr = t_end + 1e3;
        } else if (ev == 1) {                           /* TIMER5 */
            in_timer = 1;
#if FELUCCA_UAC_PHASE
            uac_tick(1);
#else
            uac_service();
#endif
            in_timer = 0;
            t_poll += poll;
        } else if (ev == 2) {                           /* a 32-frame piece into the ring */
            uint32_t j = 8u - taps, o = uac.overruns, lo = (uint32_t)blk[64u * j] | (uint32_t)blk[64u * j + 1u] << 15;
            in_audio_ctx = 1;
            uac_tap(blk + 2u * 32u * j, 32u);
            in_audio_ctx = 0;
            if (uac.overruns != o) {                    /* dropped: the checker expects this gap */
                dq_lo[dq_w % DQ] = lo;
                dq_hi[dq_w % DQ] = (lo + 32u) & 0x3FFFFFFFu;
                dq_w++;
            }
            taps--;
            t_tap += tap_dt;
        } else {                                        /* the host's USB frame */
            if (skip_run)
                skip_run--;
            else if (urand() < c->burst)
                skip_run = 1u + (uint32_t)(urand() * 59.0);
            else if (urand() >= c->skip && tok_pending)
                host_token();
            t_frame += 1e6;
            tt = t_frame + (urand() - 0.5) * c->tj_us * 1e3;
            tok_pending = 1;
        }
    }
    R.pkts = uac.pkts;
}

int main(int argc, char **argv)
{
#if FELUCCA_UAC_LOCK
    static const double ppms[] = {451, 600, 176, 0, -400, -600};
#else
    static const double ppms[] = {540, 0, -540};
#endif
    static const double rends[] = {500, 2900, 5000, 5400};
    double hours = 1.0, fault = 0.0, tj = 400.0;
    int clean = 0;                                     /* --clean: no host skips / bursts, no nested polls (timing only) */
    int a, b, k, bad = 0;
    for (k = 1; k < argc; k++) {
        if (!strcmp(argv[k], "--hours") && k + 1 < argc) hours = atof(argv[++k]);
        else if (!strcmp(argv[k], "--sie-fault") && k + 1 < argc) fault = atof(argv[++k]);
        else if (!strcmp(argv[k], "--seed") && k + 1 < argc) rng ^= (uint64_t)atoll(argv[++k]) * 0x9E3779B97F4A7C15ull;
        else if (!strcmp(argv[k], "--cli") && k + 2 < argc) { cli_us = atof(argv[++k]); cli_ms = atof(argv[++k]); }
        else if (!strcmp(argv[k], "--cli-exact")) cli_exact = 1;
        else if (!strcmp(argv[k], "--align")) cli_align = 1;
        else if (!strcmp(argv[k], "--clean")) clean = 1;
        else if (!strcmp(argv[k], "--r48")) sim_r48 = 1;
        else if (!strcmp(argv[k], "--ctl-reset") && k + 1 < argc) ctl_ms = atof(argv[++k]);
        else if (!strcmp(argv[k], "--tj") && k + 1 < argc) tj = atof(argv[++k]);
    }
    sie_fault = fault;
    tok_in_sie = 0.05;
    p_pre = clean ? 0.0 : 0.02;
    printf("UA_N %u UA_LO %u UA_HI %u UA_PRIME %u UA_MAXF %u\n", UA_N, UA_LO, UA_HI, UA_PRIME, UA_MAXF);
    for (a = 0; a < (int)(sizeof ppms / sizeof ppms[0]); a++)
        for (b = 0; b < (int)(sizeof rends / sizeof rends[0]); b++) {
            cfg_t c;
            memset(&c, 0, sizeof c);
            c.ppm = ppms[a];
            c.rend_us = rends[b];
            c.rj_us = 300;
            c.tail_us = 60;
            c.lat_us = 150;
            c.phase_us = 100.0 + 200.0 * (a * 4 + b) / 3.0;   /* the token's place in the frame, spread */
            c.tj_us = tj;                                     /* +-200 us of SOF / token jitter (default) */
            c.skip = clean ? 0.0 : 0.002;
            c.burst = clean ? 0.0 : 0.00005;
            c.hours = hours;
            run(&c);
            printf("case ppm %.0f rend %.0f hours %.2f pkts %llu taken %llu frames %llu nmin %u nmax %u n_bad %llu "
                   "underruns %u dup_counted %llu missed %u stalls %u overruns %u overwritten %u gaps_counted %llu "
                   "adj_up %u adj_down %u silence %llu sie_to %u dup_uncounted %llu drop_uncounted %llu "
                   "true_missed %llu fill_lo %u fill_hi %u cli %llu phase %d polls_per_s %.0f gap_max_us %.0f lat_max_us %.0f sie_blk_max %u ep_wrong %llu\n",
                   c.ppm, c.rend_us, c.hours, (unsigned long long)R.pkts, (unsigned long long)R.taken,
                   (unsigned long long)R.frames, R.nmin, R.nmax, (unsigned long long)R.n_bad, uac.underruns,
                   (unsigned long long)R.dup_counted, uac.missed, uac.stalls, uac.overruns, q_overwritten,
                   (unsigned long long)R.gaps_counted, uac.adj_up, uac.adj_down, (unsigned long long)R.silence,
                   UAC_SIE_TO, (unsigned long long)R.dup_uncounted, (unsigned long long)R.drop_uncounted,
                   (unsigned long long)R.true_missed, uac.fill_lo, uac.fill_hi, (unsigned long long)n_cli, FELUCCA_UAC_PHASE,
                   (double)n_polls / (c.hours * 3600.0), gap_max / 1e3, lat_max / 1e3, blk_max,
                   (unsigned long long)ep_wrong);
            if (R.dup_uncounted || R.drop_uncounted || R.n_bad || q_overwritten)
                bad++;
        }
    printf("%s\n", bad ? "FAIL" : "ok");
    return bad ? 1 : 0;
}
