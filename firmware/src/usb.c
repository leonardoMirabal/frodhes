/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* USB full-speed device on USB0: a class-compliant USB-MIDI interface.
 * All SIE traffic happens in usb_poll(), called from the TIMER5 ISR at 2 kHz,
 * so INDEX is never shared and no USB IRQ is needed. MIDI in goes to a ring
 * the audio ISR drains; MIDI out comes from a ring the audio ISR fills.
 * SysEx F0 22 24 35 7D F7 (soft key) asks the main loop to enter UBOOT.
 * VID 0x1209 / PID 0x0001 is the pid.codes test id.
 * FELUCCA_CDC=1 adds a CDC-ACM serial function (IAD composite: EP2 notify,
 * EP3 bulk data) for the console in console.c.
 * FELUCCA_UAC=1 adds a USB audio input (UAC1, class compliant): the master
 * output as 16-bit stereo at 44.1 kHz on an isochronous asynchronous IN
 * endpoint (EP4), so a computer can record the FM-1 over the cable. audio.c
 * fills a ring (uac_render_start, uac_tap); uac_service sends one packet per
 * USB frame from the TIMER5 ISR, nested in the render too (main.c; with
 * FELUCCA_UAC_PHASE, uac_tick from every TIMER5 tick instead). The
 * update loader leaves both off: MIDI only.
 * SLOOP 2.5, after Felucca 1.1.5: FELUCCA_UAC_48K=1 (the default with FELUCCA_UAC) offers 48 kHz too, for
 * phones and apps that only take 48 kHz: the host picks the rate with SET_CUR (two discrete rates in the one alt
 * setting). The engines stay at 44.1 kHz; at 48 kHz uac_tap48 resamples (a polyphase FIR 160 / 147, uac_fir.h:
 * 12 taps a phase, Q15, Felucca's 24 x Q30 made smaller) into the ring. 44.1 kHz, the rate until a host asks
 * for another, sends the samples as before, bit for bit. */
#ifndef FR_RAM
#define FR_RAM                                  /* (frodhes.c: the audio hot loops in RAM) */
#define FR_FAR(fn) fn
#endif
#include "../hal/fm1_usb.h"   /* registers; relative, so the loader and the host tests find it too */
#ifndef FELUCCA_CDC
#define FELUCCA_CDC 0
#endif
#ifndef FELUCCA_UAC
#define FELUCCA_UAC 0
#endif
#ifndef FELUCCA_UAC_48K
#define FELUCCA_UAC_48K 1
#endif
#ifndef FELUCCA_UAC_PHASE
#define FELUCCA_UAC_PHASE 0                     /* 1: the EP4 poll on its own phase (uac_tick, every TIMER5 tick);
                                                 * Frodhes' platform.c turns it on */
#endif
#ifndef FELUCCA_UAC_LOCK
#define FELUCCA_UAC_LOCK 0                      /* Frodhes 0.9.9 (iOS): the stream locked to the USB frame clock: every
                                                 * packet nominal (44.1: 9 x 44 + 45 per 10 ms, 48: 48), the FM-1's clock
                                                 * drift taken out of the samples instead. 1: a fine-ratio resampler at
                                                 * both rates (uac_tap_lock), steered by the ring's fill; 2: the diagnostic (one frame
                                                 * dropped or repeated, stretched over 32 frames, when the ring's
                                                 * average fill leaves its band); 0: the packet sizes follow the fill */
#endif
#if !FELUCCA_UAC
#undef FELUCCA_UAC_48K
#define FELUCCA_UAC_48K 0
#undef FELUCCA_UAC_PHASE
#define FELUCCA_UAC_PHASE 0
#undef FELUCCA_UAC_LOCK
#define FELUCCA_UAC_LOCK 0
#endif
#if FELUCCA_UAC_LOCK == 1 && !FELUCCA_UAC_48K
#error "FELUCCA_UAC_LOCK 1 uses the 48 kHz resampler's history (FELUCCA_UAC_48K)"
#endif
enum { S_FADDR = 0, S_POWER = 1, S_INTRTX1 = 2, S_INTRTX2 = 3, S_INTRRX1 = 4, S_INTRRX2 = 5, S_INTRUSB = 6,
       S_INTRTX1E = 7, S_INTRTX2E = 8, S_INTRRX1E = 9, S_INTRRX2E = 10, S_INTRUSBE = 11, S_FRAME1 = 12,
       S_FRAME2 = 13, S_INDEX = 14,
       S_TXMAXP = 16, S_CSR0 = 17, S_TXCSR1 = 17, S_TXCSR2 = 18, S_RXMAXP = 19, S_RXCSR1 = 20, S_RXCSR2 = 21,
       S_COUNT0 = 22, S_RXCOUNT1 = 22, S_RXCOUNT2 = 23 };

/* the endpoint buffers are read / written by the USB DMA: 32-byte aligned (a guess on 2026-10-13 that did not
 * cure OberVave's MIDI IN babble, kept as harmless; the cure was building the app with -Os: keep -Os, see tools/build.py) */
static uint8_t ep0buf[64 + 4] __attribute__((aligned(32)));
static uint8_t ep1tx[64] __attribute__((aligned(32)));
static uint8_t ep1rx[64 + 4] __attribute__((aligned(32)));
#if FELUCCA_CDC
static uint8_t ep2tx[8] __attribute__((aligned(32)));
static uint8_t ep3tx[64] __attribute__((aligned(32)));
static uint8_t ep3rx[64 + 4] __attribute__((aligned(32)));
/* serial rings: in = host -> console (ISR writes), out = console -> host (ISR reads) */
#define CI_N 256u
#define CO_N 2048u
static uint8_t cdc_in[CI_N], cdc_out[CO_N];
static volatile uint32_t ci_w, ci_r, co_w, co_r;
static struct {
    uint8_t line[7];             /* line coding: just stored and echoed back */
    volatile uint8_t dtr;        /* host has the port open */
    uint8_t e0_rx;               /* SET_LINE_CODING data stage pending */
    uint8_t rx_pend;             /* EP3 OUT packet seen, not yet taken (the ring was full) */
    uint32_t rx_pkts, tx_pkts;
} cdc = {{0x00, 0xC2, 0x01, 0x00, 0, 0, 8}};   /* 115200 8N1 */
#endif

#if FELUCCA_UAC
/* USB audio input. The I2S clock is not locked to USB (about 44,117.6 Hz against the host's 44,100):
 * the endpoint is asynchronous, so each 1 ms packet carries what the ring holds, 44 or 45 frames
 * (the 44.1 pattern) and one more or one fewer while the ring's fill drifts out of its band.
 * The render writes HALF_FRAMES (128) frames every 2.9 ms; the fill measured when a render starts is
 * the low point of that saw tooth, and is kept in UA_LO..UA_HI. A frame waits in the ring for that fill
 * plus its place in the render: 80..256 frames, ~3.8 ms on average (256-frame halves and a 96..160
 * band: ~5.8 ms). Measured under load (65 % CPU): a 64..112 band started renders at
 * 48..119 with no glitch; one render was once seen 29 frames under the floor: a packet (46) must
 * still be in hand then. */
/* Frodhes: the block's frames enter the ring only when its render is over (uac_tap after app_audio), and the
 * packets keep going out meanwhile, nested in the render: a render of R ms takes ~44 R frames out of the ring before
 * its own arrive. The band above held a render of ~1.8-2.9 ms; a longer one ran the ring dry and a packet repeated
 * a frame (a click: tests/uac_test.c, the user's recordings). So the band and the prime are raised by one whole
 * block (UA_HOLD: a render may take the block's full time, 5.8 ms), and a packet and more is still in hand at the
 * low point. The cost: one block more of latency (5.8 ms). */
#define UA_N 1024u               /* ring, frames (16-bit L | R << 16); a power of two */
#define UA_HOLD HALF_FRAMES      /* (Frodhes) the frames one render may take out before its own go in */
#define UA_PRIME (104u + UA_HOLD)   /* silence ahead of the first rendered frame */
#define UA_LO (80u + UA_HOLD)    /* a render may start late: keep a packet and more in hand */
#define UA_HI (128u + UA_HOLD)
#define UAC_RATE 44100u
#if FELUCCA_UAC_48K
/* 48 kHz: the same times in 48 kHz frames (x 160 / 147); a render of HALF_FRAMES gives 139..140 frames */
#define UA_REND48 ((HALF_FRAMES * 160u + 146u) / 147u)
#define UA_PRIME48 (113u + UA_REND48)
#define UA_LO48 (87u + UA_REND48)
#define UA_HI48 (139u + UA_REND48)
#define UA_MAXF 49u              /* frames in the largest packet: 48 + 1 */
_Static_assert(UA_HI48 + UA_REND48 + UA_MAXF < UA_N, "the ring holds the band, a render and a packet (48 kHz)");
#else
#define UA_MAXF 46u              /* frames in the largest packet */
#endif
#define UAC_MAXP (UA_MAXF * 4u)
_Static_assert(UA_HI + HALF_FRAMES + UA_MAXF < UA_N, "the ring holds the band, a render and a packet");
static uint32_t ua_ring[UA_N];
static volatile uint32_t ua_w, ua_r;
static uint32_t ep4tx[UA_MAXF] __attribute__((aligned(32)));
static struct {
    volatile uint8_t alt;        /* streaming interface setting: 1 = the host records */
    volatile uint8_t r48;        /* the host's rate (SET_CUR): 1 = 48 kHz, 0 = 44.1 (bus reset) */
    uint8_t ring48;              /* the rate the ring was primed for (the render's; a change re-primes) */
    volatile uint8_t go;         /* the ring is primed: the consumer reads it (0: silence packets) */
    volatile uint8_t flowing;    /* the host takes our packets (it may set alt 1 well before it reads) */
    uint8_t queued;              /* a packet was queued since the stream (re)started */
    uint8_t feed;                /* this render feeds the ring (sampled when it starts) */
    uint8_t wait;                /* polls the queued packet has waited */
    uint8_t e0_rx;               /* SET_CUR sampling frequency: data stage pending */
    volatile uint32_t fill_min;  /* ring fill when the last render started */
    uint32_t acc;                /* 44.1 frames per ms: 0.1 frame steps */
    uint32_t last;               /* the last frame sent, repeated on an underrun */
    uint32_t starts, pkts, frames;               /* frames in packets sent from the ring */
    uint32_t underruns, overruns, missed;        /* glitches: ring empty (repeat), ring full (drop), no packet ready */
    uint32_t stalls;                             /* the host stopped reading for > 20 ms (alt still 1) */
    uint32_t sie_to;                             /* EP4 polls an SIE access timed out in (taken as "still queued") */
    uint32_t adj_up, adj_down;                   /* packets one frame longer / shorter than the pattern */
    uint32_t fill_lo, fill_hi;                   /* range of fill_min since the stream started */
    uint32_t rate_sets;                          /* SET_CUR sampling frequency requests */
#if FELUCCA_UAC_PHASE
    uint8_t cd;                  /* TIMER5 ticks to the next EP4 poll (uac_tick) */
    uint8_t probe;               /* polls since the last early look */
    uint8_t look;                /* the looks after a late poll (0: none) */
    uint8_t acq;                 /* lock on: the poll after the next wait looks on the following ticks */
    volatile uint8_t idx4;       /* the SIE's INDEX is 4 since the EP4 poll wrote it (sie_wr clears it: any INDEX write) */
    uint32_t ph_early, ph_late;  /* the poll moved one tick earlier / later (diagnostics) */
#endif
#if FELUCCA_UAC_LOCK == 1
    uint32_t lk_P;               /* the next output's place after the newest input frame (Q16 of 1/2560 frame) */
    uint32_t lk_S;               /* the step a frame out: 2560 / 44.1 frame x 2^16 x (147 / 160 at 48 kHz), drift in */
    uint32_t lk_inv2;            /* ~2^44 / (2 lk_S): the correction's scale */
    uint32_t lk_g;               /* the held output's correction gain (its fraction of a phase x lk_inv2) */
    int32_t lk_sp[2], lk_sc[2];  /* the sums (>> UA_LK_SQ) of the output before the held one and of the held one (L, R) */
    int32_t lk_e1, lk_ef;        /* the fill's error (Q12 frames) through two smoothing stages */
    int32_t lk_c;                /* the fill the loop holds (Q12 frames): the one found after the prime (UA_LK_CAP) */
    uint8_t lk_fast;             /* the fill > UA_LK_FAR off: the 0.08 Hz stage */
    uint32_t lk_blocks;          /* blocks since the ring was primed (the loop's gains by stage) */
    uint32_t lk_n;               /* frames in the last packet */
    int64_t lk_I;                /* the loop's integral: the drift (Q52 of 1.0); kept over restarts and rate changes */
    int32_t lk_u10;              /* the last correction in 0.1 ppm (CC 116 item 22) */
#endif
#if FELUCCA_UAC_LOCK == 2
    uint32_t favg16;             /* the ring's fill at a render's start, averaged (x 16: 1/16 of the way each block) */
    int8_t adj;                  /* the next piece tapped in: 1 = one frame fewer, -1 = one more (adj_up / adj_down) */
    uint8_t cool;                /* blocks to wait before the next correction */
#endif
#ifdef UAC_STAMP
    volatile uint32_t t_q;       /* UAC_STAMP() when the last packet after a host take was queued (uac_slice_ok;
                                  * Frodhes' platform.c defines UAC_STAMP and UAC_SLICE_WIN) */
#endif
} uac;
#if FELUCCA_UAC_PHASE
#define UA_PROBE 16u             /* (uac_slot) frames between two early looks */
#define UA_LOOKS 4u              /* (uac_slot) the ticks after a late poll that look again */
#endif
#if FELUCCA_UAC_LOCK == 1
#include "uac_fir_lock.h"
/* Frodhes 0.9.9 (iOS): the locked stream. The packets take exactly the host's rate; the samples are read from the
 * 44.1 kHz render at a step that holds the ring's fill, so the FM-1's clock drift (its I2S runs ~44,117.6 Hz, not
 * 44,100) never reaches the host as a packet size. uac_tap_lock: each output at its place, 2560 phases a frame
 * (uac_fir_lock.h, the nearest one below it) plus the rest of the way as a first-order term (the slope of the outputs
 * either side x the fraction of a phase left: one output of latency); the knee and clamp of the 48 kHz path.
 * uac_render_start: the error of the fill at the block's start (the partial packet since the last one went out taken
 * off: a low-noise reading) through two smoothing stages into a PI loop on the step, fast for 3 s after a start, then
 * slower (12 s), then 0.005 Hz: the step moves by well under 0.1 ppm from block to block (no zipper, no flutter). The
 * integral (the drift) is kept over restarts. tests/py/test_uac_lock.py. */
#define UA_LK_ONE (UAC_FL_L << 16)                       /* one 44.1 kHz frame in lk_P units */
#define UA_LK_S48 (147u * 16u * 65536u)                  /* 48 kHz: 147 / 160 of a frame */
#define UA_LK_MAX ((int64_t)1 << 42)                      /* the correction's limit: 2^-10 (977 ppm) in Q52 */
#ifndef UAC_STAMP_MS
#ifdef FM1_TICKS_PER_US
#define UAC_STAMP_MS (FM1_TICKS_PER_US * 1000u)           /* (UAC_STAMP's ticks a millisecond) */
#endif
#endif
#if defined(__ELF__) && !defined(DINGHY_HOST)
#define UA_LK_POOL __attribute__((section(".pool")))
#else
#define UA_LK_POOL
#endif
static int16_t uac_fl[UAC_FL_L / 2u][UAC_FL_N] UA_LK_POOL;   /* the table in RAM (the render reads a row an output:
                                                              * scattered over 30 KB, it would miss the flash cache) */
static uint8_t uac_fl_ok;
#define UA_LK_FAR (24 << 12)                    /* (the fill this far off its mark: the 0.08 Hz stage, Q12) */
#define UA_LK_PRIME 42u                         /* (frames: the fill found ~42 under the middle without) */
#define UA_LK_CAP 32u                           /* (the blocks averaged for the fill to hold: 186 ms) */
#define UA_LK_CAP_SH 5
#define UA_LK_CAP_BAND 64u                      /* (that fill within +-64 frames of the band's middle) */
#define UA_LK_SQ 4                              /* (uac_tap_lock: its sums >> 4) */
#define UA_LK_I0 2026619832317LL                        /* the loop's start: +450 ppm (Q52), the FM-1's I2S as measured
                                                         * against a computer (+450.8 at both rates): the first lock
                                                         * after boot starts near the answer */
static void uac_lock_init(void)                         /* boot (usb_start): the table into RAM */
{
    if (!uac_fl_ok) {
        memcpy(uac_fl, UAC_FL, sizeof uac_fl);
        uac.lk_I = UA_LK_I0;
        uac_fl_ok = 1;
    }
}
static void uac_lock_set(int64_t u)                     /* the step from the loop's output u (Q52 of 1.0: + = fewer
                                                         * outputs, the FM-1's clock fast) */
{
    uint32_t s0 = uac.ring48 ? UA_LK_S48 : UA_LK_ONE, st;
    int32_t u32;
    if (u > UA_LK_MAX) u = UA_LK_MAX;
    if (u < -UA_LK_MAX) u = -UA_LK_MAX;
    u32 = (int32_t)(u >> 20);                           /* (Q32 of 1.0, |u32| <= 2^22) */
    st = s0 + (uint32_t)(int32_t)(((int64_t)s0 * u32) >> 32);
    uac.lk_S = st;
    uac.lk_inv2 = 0xFFFFFFFFu / ((2u * st) >> 12);
    uac.lk_u10 = (int32_t)(((int64_t)u32 * 10000000) >> 32);
}
/* the loop's stages: until the block count, smoothing shift, KP and KI (Q52 of 1.0 a Q12 frame of error: 2 zeta wn /
 * 48000 x 2^40 and wn^2 / 48000 x 256 / 44100 x 2^40; zeta 0.7, fn 0.08 (far off) / 0.02 / 0.005 Hz; tests/py/test_uac_lock.py) */
static const struct { uint32_t until; uint8_t sh; int32_t kp, ki; } UA_LK_ST[3] = {
    {0u, 5u, 16119682, 33597},                          /* 0.08 Hz: only while the fill is > UA_LK_FAR off its mark,
                                                         * until back within a quarter of that (a drift far from the
                                                         * seed); a start at 0.3 Hz turned the fill's +-1..3 frames of
                                                         * noise into +-150 ppm */
    {2067u, 7u, 4029920, 2100},                         /* the first 12 s: 0.02 Hz (from the seed or the drift last
                                                         * found: +-3 ppm of the fill's noise) */
    {0xFFFFFFFFu, 8u, 1007480, 131},                    /* then 0.005 Hz */
};
#endif
#if FELUCCA_UAC_LOCK == 2
#define UA_LK_M 8u               /* (uac_render_start) the average fill's dead band around the band's middle, frames */
#define UA_LK_GAP 8u             /* blocks between two corrections (46 ms) */
#endif
#if FELUCCA_UAC_48K
#include "uac_fir.h"
/* the resampler's input history (Frodhes 0.9.5): the UAC_FIR_N - 1 frames before a pass, then the pass's RS_C frames,
 * in a line (no wrap: a window is 12 frames in a row); after a pass its last UAC_FIR_N - 1 frames move to the front */
#if FELUCCA_UAC_LOCK == 1
#define RS_C HALF_FRAMES         /* (locked: the tail writes a whole block into the history, one tap a block:
                                  * uac_tap_hist; the pieces of uac_tap still work, up to RS_C frames a pass. L and R
                                  * interleaved, h[2k], h[2k + 1]: a window's 24 samples at small offsets from one
                                  * pointer, as l / r did with 32-frame passes) */
#else
#define RS_C 32u
#endif
#define RS_B (UAC_FIR_N - 1u + RS_C)
static struct {
#if FELUCCA_UAC_LOCK == 1
    int16_t h[2u * RS_B];        /* frame k: L at h[2k], R at h[2k + 1] */
#else
    int16_t l[RS_B], r[RS_B];    /* r right after l (the FIR loop reads both from one pointer: x[RS_B + i]) */
#endif
    uint32_t ph;                 /* the next output's phase, 0..159 */
} rs;
#endif
#endif

static struct {
    uint8_t up, config, pend_addr, has_pend_addr, e0_tx, e0_zlp;
    const uint8_t *e0_src;
    uint16_t e0_left;
    uint32_t resets, setups, rx_pkts, tx_pkts, sof_seen, timeouts, no_sof;
    uint32_t suspends, max_gap, retries, frame_stalls;   /* diagnostics: suspend events, longest run of polls without SOF */
    uint16_t frame, frame_same;  /* last SOF frame number and how long it has not moved (x 10 ms) */
    uint8_t detached;            /* usb_detach() was called: no retry */
    volatile uint8_t suspended;  /* bus idle > 3 ms (unplugged or host asleep): the UI hides "USB" */
    uint8_t last_setup[8];
    uint8_t sysex[16];
    uint8_t sx_len, sx_on;
    volatile uint8_t uboot_req;
    volatile uint8_t ota_req;    /* F0 22 24 35 7F F7: M-UPGRADE upgrade command (FELUCCA_OTA) */
    uint8_t rx_pend;             /* EP1 OUT packet seen, not yet taken (the MIDI ring was too full) */
    uint32_t rx_held, rx_bad;    /* packets held back (NAK) for room, malformed events ignored */
} usb;

#if FELUCCA_OTA
/* M-UPGRADE SysEx (ota.c): one received frame at a time (7-bit bytes between
 * F0 and F7; frames arriving while one is pending are dropped, the host
 * retries), and a TX ring of SysEx event packets sent before any MIDI. */
static uint8_t sx_frame[640];
static uint32_t sx_pos;
static volatile uint32_t sx_frame_len;
static volatile uint8_t sx_ready, sx_collect, sx_busy;
#define SXQ 64u
static uint32_t sx_out_q[SXQ];
static volatile uint32_t so_w, so_r;
#endif

/* MIDI rings: 4-byte USB-MIDI event packets */
#ifndef MIDI_RX_STAMP
#define MIDI_RX_STAMP() 0u                     /* (Frodhes: platform.c stamps the clock's arrival, fm1_ms) */
#endif
#define MQ 64u
static uint32_t midi_in_q[MQ], midi_out_q[MQ];
static volatile uint32_t mi_w, mi_r, mo_w, mo_r;
static void midi_out_event(uint32_t pkt)            /* from the audio ISR */
{
    if (usb.config && mo_w - mo_r < MQ) {
        midi_out_q[mo_w % MQ] = pkt;
        RING_PUBLISH();
        mo_w++;
    }
}

/* ------------------------------------------------------- descriptors --- */
/* interfaces: 0 audio control, 1 MIDI streaming, 2 audio streaming (FELUCCA_UAC), then CDC's two.
 * bcdDevice: 3.00 MIDI, +0.01 CDC, +0.10 the audio input, +0.20 more with 48 kHz (2.5) (hosts cache descriptors
 * per version) */
#define CDC_IF (2 + FELUCCA_UAC)
#ifdef FELUCCA_LOADER
#define MS_IJACK 0               /* the update loader's jacks: no name, its descriptors as before */
#else
#define MS_IJACK 2               /* Frodhes: the jacks named by the product string (hosts that name a port by its jack) */
#endif
#define UAC_AS_IF 2u
#define CFG_LEN (101 + 74 * FELUCCA_CDC + 74 * FELUCCA_UAC + 3 * FELUCCA_UAC_48K)
#define USB_NIF (2 + FELUCCA_UAC + 2 * FELUCCA_CDC)
#if FELUCCA_CDC
static const uint8_t DEV_DESC[18] = {18, 1, 0x00, 0x02, 0xEF, 0x02, 0x01, 64, 0x09, 0x12, 0x01, 0x00,
                                     0x01 + 0x10 * (FELUCCA_UAC + FELUCCA_UAC_48K), 0x03, 1, 2, 0, 1};   /* misc/IAD */
static const uint8_t CFG_DESC[] = {
    9, 2, CFG_LEN & 0xFF, CFG_LEN >> 8, USB_NIF, 1, 0, 0x80, 50,
    8, 0x0B, 0, 2 + FELUCCA_UAC, 1, 1, 0, 0,            /* IAD: audio + MIDI (IF 0-1, 0-2) */
#else
#ifndef FELUCCA_USB_PID
#if defined(BOARD_SMK37) && BOARD_SMK37
#define FELUCCA_USB_PID 0x0007   /* the SMK-37 Pro (Elite 0x0008, MKE-P37 0x0009: tools/build.py MODEL_PID) */
#else
#define FELUCCA_USB_PID 0x0006   /* the FM-1. The update loader is 0x0002. Frodhes 0.9.5-0.9.6: 0x0005 "Frodhes", 0.9.7:
                                  * the model in the name ("Frodhes FM-1", GitHub #21: tools that find a unit by its
                                  * MIDI port's model name) and a new pid.codes test PID per model, the next free ones
                                  * (0x0004 was Dinghy 0.1's): macOS keeps a MIDI device per vendor / product id
                                  * (Apple's USB-MIDI driver matches an entry by VID:PID, then serial, location or the
                                  * first one offline) with the name it had first, so a Mac that saw "Felucca" (0x0001)
                                  * or "Frodhes" (0x0005) kept it; a new PID makes a new entry, named as now
                                  * (tests/usb_desc_test.c) */
#endif
#endif
static const uint8_t DEV_DESC[18] = {18, 1, 0x10, 0x01, 0, 0, 0, 64, 0x09, 0x12, FELUCCA_USB_PID & 0xFF,
                                     FELUCCA_USB_PID >> 8, 0x00 + 0x10 * (FELUCCA_UAC + FELUCCA_UAC_48K), 0x03,
                                     1, 2, 0, 1};
static const uint8_t CFG_DESC[] = {
    9, 2, CFG_LEN & 0xFF, CFG_LEN >> 8, USB_NIF, 1, 0, 0x80, 50,
#endif
    9, 4, 0, 0, 0, 1, 1, 0, 0,
#if FELUCCA_UAC
    10, 0x24, 1, 0x00, 0x01, 31, 0, 2, 1, UAC_AS_IF,    /* AC header 1.00: MIDI + audio streaming */
    12, 0x24, 2, 1, 0x03, 0x06, 0, 2, 0x03, 0x00, 0, 0, /* input terminal 1: line, 2 ch (L R) */
    9, 0x24, 3, 2, 0x01, 0x01, 0, 1, 0,                 /* output terminal 2: USB streaming, from 1 */
#else
    9, 0x24, 1, 0x00, 0x01, 9, 0, 1, 1,
#endif
    9, 4, 1, 0, 2, 1, 3, 0, 0,
    7, 0x24, 1, 0x00, 0x01, 0x41, 0x00,
    6, 0x24, 2, 1, 1, MS_IJACK,                         /* MIDI jacks: in embedded 1, external 2, */
    6, 0x24, 2, 2, 2, MS_IJACK,
    9, 0x24, 3, 1, 3, 1, 2, 1, MS_IJACK,                /* out embedded 3 (from 2), external 4 (from 1) */
    9, 0x24, 3, 2, 4, 1, 1, 1, MS_IJACK,
    9, 5, 0x01, 2, 64, 0, 0, 0, 0,
    5, 0x25, 1, 1, 1,
    9, 5, 0x81, 2, 64, 0, 0, 0, 0,
    5, 0x25, 1, 1, 3,
#if FELUCCA_UAC
    9, 4, UAC_AS_IF, 0, 0, 1, 2, 0, 0,                  /* IF2 audio streaming, alt 0: no bandwidth */
    9, 4, UAC_AS_IF, 1, 1, 1, 2, 0, 0,                  /* alt 1: the stream */
    7, 0x24, 1, 2, 1, 0x01, 0x00,                       /* AS general: terminal 2, delay 1, PCM */
#if FELUCCA_UAC_48K
    14, 0x24, 2, 1, 2, 2, 16, 2, 0x44, 0xAC, 0x00, 0x80, 0xBB, 0x00,   /* type I: 2 ch, 16 bit, 44100 / 48000 */
#else
    11, 0x24, 2, 1, 2, 2, 16, 1, UAC_RATE & 0xFF, (UAC_RATE >> 8) & 0xFF, UAC_RATE >> 16,
                                                        /* type I: 2 ch, 16 bit, one rate */
#endif
    9, 5, 0x84, 0x05, UAC_MAXP & 0xFF, UAC_MAXP >> 8, 1, 0, 0,   /* EP4 IN isochronous async, 1 ms */
    7, 0x25, 1, 0x01, 0, 0, 0,                          /* CS endpoint: sampling frequency control */
#endif
#if FELUCCA_CDC
    8, 0x0B, CDC_IF, 2, 2, 2, 1, 0,                     /* IAD: CDC ACM (IF 2-3, 3-4) */
    9, 4, CDC_IF, 0, 1, 2, 2, 1, 0,                     /* communication */
    5, 0x24, 0x00, 0x10, 0x01,                          /* header 1.10 */
    5, 0x24, 0x01, 0x00, CDC_IF + 1,                    /* call management: the data IF */
    4, 0x24, 0x02, 0x02,                                /* ACM: line coding + state */
    5, 0x24, 0x06, CDC_IF, CDC_IF + 1,                  /* union */
    7, 5, 0x82, 3, 8, 0, 16,                            /* EP2 IN interrupt (never sent) */
    9, 4, CDC_IF + 1, 0, 2, 0x0A, 0, 0, 0,              /* data */
    7, 5, 0x03, 2, 64, 0, 0,                            /* EP3 OUT bulk */
    7, 5, 0x83, 2, 64, 0, 0,                            /* EP3 IN bulk */
#endif
};
typedef char cfg_len_ok[sizeof CFG_DESC == CFG_LEN ? 1 : -1];
#if FELUCCA_CDC
/* SLOOP 2.4, after Felucca 1.0.3 (#67): menu USB SERIAL. ON presents the serial console as before (the
 * descriptors above, byte for byte); OFF, the default, presents the device as a FELUCCA_CDC=0 build does: class 0,
 * no IAD, audio + MIDI only (the same bytes: tests/uac_test.c compares them). With the console, macOS 13-15 let
 * Apple's CDC composite driver take the device and the USB audio input never appears. The console is a developer
 * tool; the editor, the installer and the soft key use USB-MIDI. Read once, before usb_start (main.c: the settings
 * are loaded first), so the host sees one device from the start */
static uint8_t usb_cdc_on;
static const uint8_t DEV_DESC_PLAIN[18] = {18, 1, 0x10, 0x01, 0, 0, 0, 64, 0x09, 0x12, 0x01, 0x00,
                                           0x00 + 0x10 * (FELUCCA_UAC + FELUCCA_UAC_48K), 0x03, 1, 2, 0, 1};
#define CFG_PLAIN_LEN (CFG_LEN - 74)                    /* without the audio IAD (8) and the CDC block (66) */
static uint8_t cfg_plain[CFG_PLAIN_LEN];
static const uint8_t *cfg_plain_get(void)
{
    if (!cfg_plain[0]) {
        memcpy(cfg_plain, CFG_DESC, 9);
        cfg_plain[2] = CFG_PLAIN_LEN & 0xFF;
        cfg_plain[3] = CFG_PLAIN_LEN >> 8;
        cfg_plain[4] = USB_NIF - 2;
        memcpy(cfg_plain + 9, CFG_DESC + 17, CFG_PLAIN_LEN - 9);
    }
    return cfg_plain;
}
static int usb_cdc_now(void) { return usb_cdc_on; }   /* (the menu: what this start presents) */
#else
static int usb_cdc_now(void) { return 0; }
#endif
static const uint8_t STR0[4] = {4, 3, 0x09, 0x04};
static const uint8_t STR1[] = {42, 3, 'H', 0, 0xFC, 0, 'g', 0, 'e', 0, 'l', 0, 't', 0, 'o', 0, 'n', 0, ' ', 0, 'I', 0,
                               'n', 0, 's', 0, 't', 0, 'r', 0, 'u', 0, 'm', 0, 'e', 0, 'n', 0, 't', 0, 's', 0};
#ifdef FELUCCA_LOADER
static const uint8_t STR2[] = {30, 3, 'F', 0, 'e', 0, 'l', 0, 'u', 0, 'c', 0, 'c', 0, 'a', 0, ' ', 0, 'U', 0, 'p', 0,
                               'd', 0, 'a', 0, 't', 0, 'e', 0};
#else
#if defined(BOARD_SMK37) && BOARD_SMK37
#ifndef SMK_MODEL
#define SMK_MODEL "SMK-37 Pro"                                  /* (fm1_cc.h) */
#endif
#define USB_PRODUCT "Frodhes " SMK_MODEL
#elif !defined(USB_PRODUCT)                     /* (tools/build.py --usb-name: a test unit's own name) */
#define USB_PRODUCT "Frodhes FM-1"
#endif
/* the product, so the MIDI port and the audio input: "Frodhes" and the model (0.9.7, GitHub #21), UTF-16LE, written
 * from USB_PRODUCT at the first request (a string literal cannot be spread into an initializer) */
static uint8_t STR2[2u + 2u * (sizeof USB_PRODUCT - 1u)];
static void str2_fill(void)
{
    uint32_t i;
    for (i = 0; i + 1u < sizeof USB_PRODUCT; i++) STR2[2u + 2u * i] = (uint8_t)USB_PRODUCT[i];
    STR2[1] = 3;
    STR2[0] = (uint8_t)sizeof STR2;
}
#endif

static int get_desc(uint32_t wvalue, const uint8_t **d, uint16_t *l)
{
    switch (wvalue >> 8) {
    case 1:
        *d = DEV_DESC;
        *l = sizeof DEV_DESC;
#if FELUCCA_CDC
        if (!usb_cdc_on)
            *d = DEV_DESC_PLAIN;
#endif
        return 1;
    case 2:
        *d = CFG_DESC;
        *l = sizeof CFG_DESC;
#if FELUCCA_CDC
        if (!usb_cdc_on) {
            *d = cfg_plain_get();
            *l = CFG_PLAIN_LEN;
        }
#endif
        return 1;
    case 3:
        switch (wvalue & 0xFFu) {
        case 0:
            *d = STR0;
            *l = sizeof STR0;
            return 1;
        case 1:
            *d = STR1;
            *l = sizeof STR1;
            return 1;
        case 2:
#ifndef FELUCCA_LOADER
            if (!STR2[0]) str2_fill();
#endif
            *d = STR2;
            *l = sizeof STR2;
            return 1;
        default:
            return 0;
        }
    default:
        return 0;
    }
}

/* ---------------------------------------------------------- the SIE --- */
static void sie_wr(uint32_t r, uint32_t v)
{
    uint32_t n = 20000;
#if FELUCCA_UAC && FELUCCA_UAC_PHASE
    if (r == S_INDEX)
        uac.idx4 = 0;                                   /* (uac_ep4_poll: INDEX is no longer known to be 4. Before the
                                                         * write and after it: an EP4 poll in TIMER5 may preempt the
                                                         * write anywhere) */
#endif
    if (!fm1_usb_sie_on())
        return;
    fm1_usb_sie_wr_start(r, v);
    while (!fm1_usb_sie_done() && --n)
        ;
#if FELUCCA_UAC && FELUCCA_UAC_PHASE
    if (r == S_INDEX)
        uac.idx4 = 0;
#endif
    if (n)
        usb.timeouts = 0;                               /* consecutive failures only */
    else if (++usb.timeouts > 50u)
        usb.up = 0;                                     /* SIE dead (no clock?): stop polling it */
}

static uint32_t sie_rd(uint32_t r)
{
    uint32_t n = 20000;
    if (!fm1_usb_sie_on())
        return 0;
    fm1_usb_sie_rd_start(r);
    while (!fm1_usb_sie_done())
        if (!--n) {
            if (++usb.timeouts > 50u)
                usb.up = 0;
            return 0;
        }
    usb.timeouts = 0;
    return fm1_usb_sie_data();
}

#if FELUCCA_UAC
static void uac_ep4_reset(void)                         /* drop a queued packet; isochronous mode */
{
    sie_wr(S_INDEX, 4);
    sie_wr(S_TXCSR1, 0x48);                             /* ClrDataTog + FlushFIFO (SDK usb_g_ep_config) */
    sie_wr(S_TXCSR2, 0);
    sie_wr(S_TXCSR1, 0);
    sie_wr(S_TXCSR2, 0x40);                             /* ISO */
}

#if FELUCCA_UAC_PHASE
static void uac_service(void);
#endif
static void uac_stream(uint32_t alt)                    /* SET_INTERFACE, configuration, bus reset */
{
    uac.go = 0;                                         /* the next render primes the ring again */
    uac.flowing = 0;                                    /* ... once the host has taken a packet */
    uac.queued = 0;
    uac.wait = 0;
    RING_PUBLISH();
    uac.alt = (uint8_t)alt;
    uac.acc = 0;
    uac.last = 0;
#if FELUCCA_UAC_PHASE
    uac.look = 0;
    uac.acq = 1;                                        /* (uac_slot: lock on to the host's first tokens) */
#endif
    if (alt) {                                          /* (the fill range of the last stream stays readable) */
        uac.starts++;
        uac.fill_lo = 0xFFFFFFFFu;
        uac.fill_hi = 0;
    }
}
#endif

#if FELUCCA_UAC
/* SET_CUR sampling frequency (any time: before or after SET_INTERFACE; a bus reset or a new configuration go
 * back to 44.1). 48 kHz when built with it and asked for (the nearer of the two), else 44.1. A change restarts
 * the stream: silence packets until the next render primes the ring at the new rate. */
static void uac_rate_set(uint32_t hz)
{
    uint8_t r48 = (uint8_t)(FELUCCA_UAC_48K && hz >= (UAC_RATE + 48000u) / 2u);
    if (r48 == uac.r48)
        return;
    uac.go = 0;
    RING_PUBLISH();
    uac.r48 = r48;
    uac.acc = 0;
    uac.pkts = uac.frames = 0;
}
#endif

static void ep1_config(void)
{
    fm1_usb_ep_txbuf(1, ep1tx);
    fm1_usb_ep_rxbuf(1, ep1rx);
    sie_wr(S_INDEX, 1);
    sie_wr(S_TXMAXP, 0xFF);
    sie_wr(S_TXCSR1, 0x48);
    sie_wr(S_TXCSR2, 0);
    sie_wr(S_RXMAXP, 0xFF);
    sie_wr(S_RXCSR1, 0x90);
    sie_wr(S_RXCSR2, 0);
    sie_wr(S_INTRRX1E, 0x02);
    fm1_usb_ep_enable(1u << 1);
#if FELUCCA_CDC
    fm1_usb_ep_txbuf(2, ep2tx);
    sie_wr(S_INDEX, 2);
    sie_wr(S_TXMAXP, 0xFF);
    sie_wr(S_TXCSR1, 0x48);
    sie_wr(S_TXCSR2, 0);
    fm1_usb_ep_txbuf(3, ep3tx);
    fm1_usb_ep_rxbuf(3, ep3rx);
    sie_wr(S_INDEX, 3);
    sie_wr(S_TXMAXP, 0xFF);
    sie_wr(S_TXCSR1, 0x48);
    sie_wr(S_TXCSR2, 0);
    sie_wr(S_RXMAXP, 0xFF);
    sie_wr(S_RXCSR1, 0x90);
    sie_wr(S_RXCSR2, 0);
    sie_wr(S_INTRRX1E, 0x0A);
    fm1_usb_ep_enable((1u << 2) | (1u << 3));
    cdc.rx_pend = 1;                                    /* look once: a packet may already wait */
#endif
#if FELUCCA_UAC
    fm1_usb_ep4_txbuf(ep4tx);
    sie_wr(S_INDEX, 4);
    sie_wr(S_TXMAXP, 0xFF);
    uac_ep4_reset();
    fm1_usb_ep_enable(1u << 4);
    uac_stream(0);
#endif
}

static void e0_chunk(void)
{
    uint32_t n = usb.e0_left > 64u ? 64u : usb.e0_left, i;
    int last;
    for (i = 0; i < n; i++)
        ep0buf[i] = usb.e0_src[i];
    fm1_usb_ep0_send(ep0buf, n);
    usb.e0_src += n;
    usb.e0_left = (uint16_t)(usb.e0_left - n);
    last = usb.e0_left == 0 && !(n == 64u && usb.e0_zlp);
    if (usb.e0_left == 0 && n == 64u)
        usb.e0_zlp = 0;
    sie_wr(S_INDEX, 0);
    sie_wr(S_CSR0, last ? 0x0A : 0x02);
    if (last)
        usb.e0_tx = 0;
}

static void e0_send(const uint8_t *p, uint16_t len, uint16_t wlen)
{
    if (len > wlen)
        len = wlen;
    usb.e0_src = p;
    usb.e0_left = len;
    usb.e0_zlp = (len < wlen) && (len % 64u == 0);
    usb.e0_tx = 1;
    sie_wr(S_INDEX, 0);
    sie_wr(S_CSR0, 0x40);
    e0_chunk();
}

static void ep0_service(void)
{
    static const uint8_t zero2[2];
    uint32_t csr, i;
    uint16_t wvalue, wlength;
    uint8_t *s = usb.last_setup;
    if (usb.has_pend_addr) {
        sie_wr(S_FADDR, usb.pend_addr);
        usb.has_pend_addr = 0;
    }
    sie_wr(S_INDEX, 0);
    csr = sie_rd(S_CSR0);
    if (csr & 0x04u) {                                  /* SentStall */
        sie_wr(S_CSR0, 0);
        usb.e0_tx = 0;
#if FELUCCA_CDC
        cdc.e0_rx = 0;
#endif
#if FELUCCA_UAC
        uac.e0_rx = 0;
#endif
        return;
    }
    if (csr & 0x10u) {                                  /* SetupEnd: the host abandoned the transfer */
        sie_wr(S_CSR0, 0x80);
        usb.e0_tx = 0;
#if FELUCCA_CDC
        cdc.e0_rx = 0;                                  /* else the next SETUP is taken as line coding */
#endif
#if FELUCCA_UAC
        uac.e0_rx = 0;
#endif
    }
    if (usb.e0_tx) {
        if (!(csr & 0x02u))
            e0_chunk();
        return;
    }
    if (!(csr & 0x01u))
        return;
    fm1_usb_rx_sync();
#if FELUCCA_CDC
    if (cdc.e0_rx) {                                    /* SET_LINE_CODING data stage */
        uint32_t n = sie_rd(S_COUNT0);
        for (i = 0; i < 7u && i < n; i++)
            cdc.line[i] = ep0buf[i];
        cdc.e0_rx = 0;
        goto ack;
    }
#endif
#if FELUCCA_UAC
    if (uac.e0_rx) {                                    /* SET_CUR sampling frequency: 3 bytes, Hz */
        uac.rate_sets++;
        if (sie_rd(S_COUNT0) >= 3u)
            uac_rate_set(ep0buf[0] | (uint32_t)ep0buf[1] << 8 | (uint32_t)ep0buf[2] << 16);
        uac.e0_rx = 0;
        goto ack;
    }
#endif
    for (i = 0; i < 8u; i++)
        s[i] = ep0buf[i];
    usb.setups++;
    wvalue = (uint16_t)(s[2] | s[3] << 8);
    wlength = (uint16_t)(s[6] | s[7] << 8);
    switch ((uint32_t)s[0] << 8 | s[1]) {
    case 0x0005:                                        /* SET_ADDRESS */
        usb.pend_addr = s[2] & 0x7Fu;
        usb.has_pend_addr = 1;
        goto ack;
    case 0x8006: {                                      /* GET_DESCRIPTOR */
        const uint8_t *d;
        uint16_t l;
        if (get_desc(wvalue, &d, &l)) {
            if (!wlength)
                goto ack;
            e0_send(d, l, wlength);
            return;
        }
        goto stall;
    }
    case 0x0009:                                        /* SET_CONFIGURATION: 0 or 1 only */
        if (s[2] > 1u)
            goto stall;
        usb.config = s[2];
        if (usb.config == 1u)
            ep1_config();
        else
            sie_wr(S_INTRRX1E, 0);
#if FELUCCA_UAC
        if (usb.config != 1u)
            uac_stream(0);
        uac_rate_set(UAC_RATE);                         /* a new configuration starts at 44.1 */
#endif
        goto ack;
    case 0x8008:
        e0_send(&usb.config, 1, wlength);
        return;
    case 0x8000:
    case 0x8100:
    case 0x8200:
        e0_send(zero2, 2, wlength);
        return;
    case 0x010B:                                        /* SET_INTERFACE: alt 0 (alt 1 for the audio stream) */
#if FELUCCA_UAC
        if (s[4] == UAC_AS_IF && wvalue <= 1u && usb.config) {
            uac_ep4_reset();
            uac_stream(wvalue);
#if FELUCCA_UAC_PHASE
            uac_service();                              /* the first (silence) packet now, as usb_poll's own call did:
                                                         * a token right after the request finds it */
#endif
            goto ack;
        }
#endif
        if (wvalue == 0)
            goto ack;
        goto stall;
    case 0x810A:
#if FELUCCA_UAC
        if (s[4] == UAC_AS_IF) {
            static uint8_t alt;
            alt = uac.alt;
            e0_send(&alt, 1, wlength);
            return;
        }
#endif
        e0_send(zero2, 1, wlength);
        return;
    case 0x0201: {                                      /* CLEAR_FEATURE(ENDPOINT_HALT): data toggle reset */
#if FELUCCA_CDC
        uint32_t ep = s[4] & 0x0Fu, last = usb_cdc_on ? 3u : 1u;   /* (USB SERIAL OFF: EP2 / EP3 are not described) */
#else
        uint32_t ep = s[4] & 0x0Fu, last = 1u;
#endif
#if FELUCCA_UAC
        if (wvalue == 0 && s[4] == 0x84u)
            goto ack;                                   /* isochronous: no halt, no toggle */
#endif
        if (wvalue != 0 || ep > last)
            goto stall;                                 /* not an endpoint we have */
        if (ep) {                                       /* (EP0 has no halt to clear) */
            sie_wr(S_INDEX, ep);
            if (s[4] & 0x80u)
                sie_wr(S_TXCSR1, 0x40);
            else
                sie_wr(S_RXCSR1, 0x80);
        }
        goto ack;
    }
#if FELUCCA_CDC
    case 0x2120:                                        /* SET_LINE_CODING: 7 bytes follow */
        if (wlength) {
            cdc.e0_rx = 1;
            sie_wr(S_INDEX, 0);
            sie_wr(S_CSR0, 0x40);                       /* ServicedRxPktRdy, no DataEnd yet */
            return;
        }
        goto ack;
    case 0xA121:                                        /* GET_LINE_CODING */
        e0_send(cdc.line, 7, wlength);
        return;
    case 0x2122:                                        /* SET_CONTROL_LINE_STATE */
        cdc.dtr = s[2] & 1u;
        goto ack;
    case 0x2123:                                        /* SEND_BREAK */
        goto ack;
#endif
#if FELUCCA_UAC
    case 0x2201:                                        /* SET_CUR, endpoint: sampling frequency */
        if (s[4] != 0x84u || s[3] != 1u)
            goto stall;
        if (wlength) {
            uac.e0_rx = 1;
            sie_wr(S_INDEX, 0);
            sie_wr(S_CSR0, 0x40);                       /* the 3 bytes follow */
            return;
        }
        goto ack;
    case 0xA281:                                        /* GET_CUR / MIN / MAX: the rate / the range */
    case 0xA282:
    case 0xA283: {
        static const uint8_t RATE[3] = {UAC_RATE & 0xFF, (UAC_RATE >> 8) & 0xFF, UAC_RATE >> 16};
#if FELUCCA_UAC_48K
        static const uint8_t RATE48[3] = {48000u & 0xFF, (48000u >> 8) & 0xFF, 48000u >> 16};
#endif
        if (s[4] != 0x84u || s[3] != 1u)
            goto stall;
#if FELUCCA_UAC_48K
        if (s[1] == 0x83u || (s[1] == 0x81u && uac.r48)) {
            e0_send(RATE48, 3, wlength);
            return;
        }
#endif
        e0_send(RATE, 3, wlength);
        return;
    }
#endif
    default:
        goto stall;
    }
ack:
    sie_wr(S_INDEX, 0);
    sie_wr(S_CSR0, 0x48);
    return;
stall:
    sie_wr(S_INDEX, 0);
    sie_wr(S_CSR0, 0x60);
}

/* SysEx assembly: only short commands matter here */
static void sysex_byte(uint8_t b)
{
    static const uint8_t UBOOT_KEY[6] = {0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7};
    if (b >= 0xF8u)
        return;                                        /* realtime may occur anywhere in SysEx */
    if ((b & 0x80u) && b != 0xF0u && b != 0xF7u) {
        usb.sx_on = 0;
#if FELUCCA_OTA
        sx_collect = 0;
#endif
        return;                                        /* another status aborts the unfinished frame */
    }
    if (b == 0xF0) {
        usb.sx_on = 1;
        usb.sx_len = 0;
#if FELUCCA_OTA
        sx_collect = !sx_ready;
        sx_pos = 0;
#endif
    }
    if (!usb.sx_on)
        return;
#if FELUCCA_OTA
    if (sx_collect && b != 0xF0 && b != 0xF7) {
        if (sx_pos < sizeof sx_frame)
            sx_frame[sx_pos++] = b;
        else
            sx_collect = 0;                             /* too long: not ours */
    }
#endif
    if (usb.sx_len < sizeof usb.sysex)
        usb.sysex[usb.sx_len++] = b;
    if (b == 0xF7) {
        uint32_t i, ok = usb.sx_len == 6u;
        for (i = 0; ok && i < 6u; i++)
            ok = usb.sysex[i] == UBOOT_KEY[i];
        if (ok)
            usb.uboot_req = 1;
#if FELUCCA_OTA
        else if (usb.sx_len == 6u && usb.sysex[1] == 0x22 && usb.sysex[2] == 0x24 && usb.sysex[3] == 0x35 &&
                 usb.sysex[4] == 0x7F)
            usb.ota_req = 1;
        else if (sx_collect && sx_pos) {
            sx_frame_len = sx_pos;
            RING_PUBLISH();
            sx_ready = 1;                               /* main loop: ota_take() / ed_service() */
        }
        sx_collect = 0;
#endif
        usb.sx_on = 0;
    }
}

/* one USB-MIDI event packet: SysEx bytes to sysex_byte, channel voice messages and the clock /
 * transport realtime ones to the MIDI ring; a malformed packet (status not matching its CIN, a data
 * byte with bit 7) is ignored (after Felucca 1.0) */
static void midi_in_event(uint32_t pkt)
{
    uint32_t cin = pkt & 15u, st = (pkt >> 8) & 0xFFu;
    if ((cin == 0xFu || cin == 0x5u) && (st == 0xF8u || st == 0xFAu || st == 0xFBu || st == 0xFCu)) {
        /* clock, transport (Frodhes: also as CIN 5, as some hosts send them; stamped for the app's tempo, tempo.c
         * tempo_clock: 12 bits of their arrival, MIDI_RX_STAMP (platform.c: fm1_ms), and 4 bits of the pulses
         * counted here, so a pulse the full ring drops is still counted) */
        static uint8_t clk_n;
        if (st == 0xF8u) clk_n++;
        if (mi_w - mi_r < MQ) {
            midi_in_q[mi_w % MQ] = 0xFu | st << 8 | ((MIDI_RX_STAMP() & 0xFFFu) | (clk_n & 15u) << 12) << 16;
            RING_PUBLISH();
            mi_w++;
        }
        return;
    }
    if (cin == 0x3u && st == 0xF2u && !((pkt >> 16) & 0x80u) && !(pkt & 0x80000000u)) {   /* Song Position (Frodhes) */
        if (mi_w - mi_r < MQ) {
            midi_in_q[mi_w % MQ] = pkt;
            RING_PUBLISH();
            mi_w++;
        }
        return;
    }
    if (cin >= 4u && cin <= 7u) {                      /* SysEx */
        uint32_t k, nb = cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u;
        for (k = 0; k < nb; k++)
            sysex_byte((uint8_t)(pkt >> (8u * (k + 1u))));
        return;
    }
    if (st >= 0x80u && st < 0xF8u) {                   /* channel / system common also ends a SysEx */
        usb.sx_on = 0;
#if FELUCCA_OTA
        sx_collect = 0;
#endif
    }
    if (cin >= 8u && cin <= 0xEu && (st >> 4) == cin && !((pkt >> 16) & 0x80u) &&
        (cin == 0xCu || cin == 0xDu || !(pkt & 0x80000000u))) {
        if (mi_w - mi_r < MQ) {
            midi_in_q[mi_w % MQ] = pkt;
            RING_PUBLISH();
            mi_w++;
        }
        return;
    }
    if (cin >= 8u)
        usb.rx_bad++;
}

/* one EP1 OUT packet (<= 16 events) into the MIDI ring, or 0: fewer than 16 + 8 slots free, so the
 * packet stays (the host is NAKed) and is taken on a later poll: a burst from a DAW never drops a
 * note-off (0.9 dropped what did not fit: hanging notes), and 8 slots stay for the TRS input, which
 * shares the ring and cannot wait. The update loader never drains the ring: no back-pressure there.
 * (After Felucca 1.0.) */
#define EP1_ROOM (16u + 8u)
static int ep1_take(const uint8_t *b, uint32_t n)
{
    uint32_t i;
#ifndef FELUCCA_LOADER
    if (MQ - (mi_w - mi_r) < EP1_ROOM)
        return 0;
#endif
    for (i = 0; i + 3u < n; i += 4u)
        midi_in_event((uint32_t)b[i] | (uint32_t)b[i + 1] << 8 | (uint32_t)b[i + 2] << 16 | (uint32_t)b[i + 3] << 24);
    return 1;
}

static void ep1_rx(void)                                /* leaves the packet (NAK) while the ring is too full */
{
    uint32_t csr, n;
    sie_wr(S_INDEX, 1);
    csr = sie_rd(S_RXCSR1) | (sie_rd(S_RXCSR2) << 8);
    if (!(csr & 1u)) {
        usb.rx_pend = 0;
        return;
    }
    n = sie_rd(S_RXCOUNT1) | (sie_rd(S_RXCOUNT2) << 8);
    if (n > 64u)
        n = 64u;
    fm1_usb_rx_sync();
    if (!ep1_take(ep1rx, n)) {
        usb.rx_held++;
        return;                                         /* rx_pend stays: taken on a later poll */
    }
    usb.rx_pend = 0;
    usb.rx_pkts++;
    csr = (csr & ~0x164u) | 0x10u;
    sie_wr(S_RXCSR1, csr & 0xFFu);
    sie_wr(S_RXCSR2, csr >> 8);
}

#if FELUCCA_OTA
/* ---- SysEx frames for the main loop (ota.c / editor.c hooks; felucca.c and the
 * update loader supply ota_now_ms / ota_idle) ---- */
static uint32_t ota_now_ms(void);
static void ota_idle(void);

/* a reply cut off half way (the host did not read for 500 ms) leaves the host's parser inside a SysEx, and it
 * would swallow everything after it: the next send ends that SysEx first (an F7 on its own) */
static uint8_t sx_open;
static int ota_wire_send(const uint8_t *p, uint32_t n)   /* F0..F7 -> USB-MIDI SysEx packets */
{
    uint32_t i = 0, t0 = ota_now_ms();
    sx_busy = 1;
    if (sx_open) {
        while (so_w - so_r >= SXQ) {
            if (!*(volatile uint8_t *)&usb.config || ota_now_ms() - t0 > 500u) {
                sx_busy = 0;
                return -1;
            }
            ota_idle();
        }
        sx_out_q[so_w % SXQ] = 5u | 0xF7u << 8;           /* CIN 5: a SysEx ending with one byte */
        RING_PUBLISH();
        so_w++;
        sx_open = 0;
    }
    while (i < n) {
        uint32_t k = n - i >= 3u ? 3u : n - i, pkt;
        uint32_t cin = k == 3u && i + 3u < n ? 4u : k == 3u ? 7u : 4u + k;   /* 4 continues; 5/6/7 end */
        pkt = cin | (uint32_t)p[i] << 8 | (k > 1u ? (uint32_t)p[i + 1] << 16 : 0u) |
              (k > 2u ? (uint32_t)p[i + 2] << 24 : 0u);
        while (so_w - so_r >= SXQ) {
            if (!*(volatile uint8_t *)&usb.config || ota_now_ms() - t0 > 500u) {
                sx_open = i > 0u;                           /* (cut inside the frame: end it next time) */
                sx_busy = 0;
                return -1;
            }
            ota_idle();
        }
        sx_out_q[so_w % SXQ] = pkt;
        RING_PUBLISH();
        so_w++;
        i += k;
    }
    sx_busy = 0;
    return 0;
}
static int ota_frame_get(const uint8_t **p, uint32_t *n)
{
    if (!sx_ready)
        return 0;
    RING_PUBLISH();                                     /* read the frame only after the flag */
    *p = sx_frame;
    *n = sx_frame_len;
    return 1;
}
static void ota_frame_done(void)
{
    RING_PUBLISH();                                     /* done with the frame before the ISR may refill it */
    sx_ready = 0;
}
#endif

static void ep1_tx(void)
{
    uint32_t csr, n = 0;
#if FELUCCA_OTA
    if (mo_w == mo_r && so_w == so_r)
        return;
#else
    if (mo_w == mo_r)
        return;
#endif
    sie_wr(S_INDEX, 1);
    csr = sie_rd(S_TXCSR1);
    if (csr & 0x01u)
        return;                                         /* previous packet still pending */
    if (csr & 0x80u)
        sie_wr(S_TXCSR1, csr & ~0x80u);
#if FELUCCA_OTA
    while (so_r != so_w && n < 64u) {                   /* SysEx first, never split by notes */
        uint32_t pkt = sx_out_q[so_r % SXQ];
        ep1tx[n] = (uint8_t)pkt;
        ep1tx[n + 1] = (uint8_t)(pkt >> 8);
        ep1tx[n + 2] = (uint8_t)(pkt >> 16);
        ep1tx[n + 3] = (uint8_t)(pkt >> 24);
        n += 4u;
        so_r++;
    }
    while (!sx_busy && so_r == so_w && mo_r != mo_w && n < 64u) {
#else
    while (mo_r != mo_w && n < 64u) {
#endif
        uint32_t pkt;
        RING_PUBLISH();                                 /* the audio ISR (producer) can preempt us: */
        pkt = midi_out_q[mo_r % MQ];                    /* slot read strictly between the index checks */
        ep1tx[n] = (uint8_t)pkt;
        ep1tx[n + 1] = (uint8_t)(pkt >> 8);
        ep1tx[n + 2] = (uint8_t)(pkt >> 16);
        ep1tx[n + 3] = (uint8_t)(pkt >> 24);
        n += 4u;
        RING_PUBLISH();
        mo_r++;
    }
    if (!n)
        return;
    fm1_usb_ep_send(1, ep1tx, n);
    sie_wr(S_TXCSR1, sie_rd(S_TXCSR1) | 0x01u);
    usb.tx_pkts++;
}

#if FELUCCA_CDC
static void ep3_rx(void)                                /* leaves the packet (NAK) while the ring is full */
{
    uint32_t csr, n, i;
    sie_wr(S_INDEX, 3);
    csr = sie_rd(S_RXCSR1) | (sie_rd(S_RXCSR2) << 8);
    if (!(csr & 1u)) {
        cdc.rx_pend = 0;
        return;
    }
    n = sie_rd(S_RXCOUNT1) | (sie_rd(S_RXCOUNT2) << 8);
    if (n > 64u)
        n = 64u;
    if (CI_N - (ci_w - ci_r) < n)
        return;                                         /* rx_pend stays: retried next poll */
    cdc.rx_pend = 0;
    fm1_usb_rx_sync();
    for (i = 0; i < n; i++)
        cdc_in[(ci_w + i) % CI_N] = ep3rx[i];
    RING_PUBLISH();
    ci_w += n;
    cdc.rx_pkts++;
    csr = (csr & ~0x164u) | 0x10u;
    sie_wr(S_RXCSR1, csr & 0xFFu);
    sie_wr(S_RXCSR2, csr >> 8);
}

static void ep3_tx(void)                                /* <= 63 bytes per packet: never needs a ZLP */
{
    uint32_t csr, n = 0;
    if (co_w == co_r)
        return;
    sie_wr(S_INDEX, 3);
    csr = sie_rd(S_TXCSR1);
    if (csr & 0x01u)
        return;
    if (csr & 0x80u)
        sie_wr(S_TXCSR1, csr & ~0x80u);
    while (co_r != co_w && n < 63u)
        ep3tx[n++] = cdc_out[co_r++ % CO_N];
    fm1_usb_ep_send(3, ep3tx, n);
    sie_wr(S_TXCSR1, sie_rd(S_TXCSR1) | 0x01u);
    cdc.tx_pkts++;
}
#endif

#if FELUCCA_UAC
/* ---- the audio input: audio.c produces (render start, then each block), TIMER5 consumes ---- */
static __attribute__((noinline)) void uac_render_start(void)   /* audio ISR, before a half buffer renders */
{
    uint32_t fill;
    uac.feed = uac.alt && usb.config && uac.flowing;
    if (!uac.feed) {
        uac.go = 0;                                     /* not read (yet / any more): prime when it is */
        return;
    }
    if (!uac.go || uac.ring48 != uac.r48) {             /* (re)start: silence ahead, then this render */
        uint32_t r = ua_r, i, prime = UA_PRIME;
        uac.go = 0;
#if FELUCCA_UAC_48K
        uac.ring48 = uac.r48;
        if (uac.ring48) {                               /* the resampler starts from silence */
            prime = UA_PRIME48;
#if FELUCCA_UAC_LOCK != 1                               /* (locked: below) */
            for (i = 0; i < RS_B; i++)
                rs.l[i] = rs.r[i] = 0;
#endif
            rs.ph = 0;
        }
#endif
#if FELUCCA_UAC_LOCK == 1
        prime += UA_LK_PRIME;                           /* (the loop holds the fill it finds after the prime: this
                                                         * puts it at the band's middle, as the packets' adjustment did) */
#endif
        for (i = 0; i < prime; i++)
            ua_ring[(r + i) & (UA_N - 1u)] = 0;
        RING_PUBLISH();
        ua_w = r + prime;
        uac.fill_min = prime;
#if FELUCCA_UAC_LOCK == 1
        for (i = 0; i < 2u * RS_B; i++)                 /* (both rates: the resampler starts from silence) */
            rs.h[i] = 0;
        uac.lk_P = 0;
        uac.lk_g = 0;
        uac.lk_sp[0] = uac.lk_sp[1] = uac.lk_sc[0] = uac.lk_sc[1] = 0;
        uac.lk_e1 = uac.lk_ef = 0;
        uac.lk_c = 0;
        uac.lk_fast = 0;
        uac.lk_blocks = 0;
        if (!uac.lk_n)
            uac.lk_n = uac.ring48 ? 48u : 44u;
        uac_lock_init();                                /* (done at boot: usb_start; the host tests start here) */
        uac_lock_set(uac.lk_I);                         /* (the drift as last found; after the init: its seed) */
#endif
#if FELUCCA_UAC_LOCK == 2
        uac.favg16 = prime << 4;
        uac.adj = 0;
        uac.cool = UA_LK_GAP;
#endif
        RING_PUBLISH();
        uac.go = 1;
        return;
    }
    fill = ua_w - ua_r;
    uac.fill_min = fill;
    if (fill < uac.fill_lo)
        uac.fill_lo = fill;
    if (fill > uac.fill_hi)
        uac.fill_hi = fill;
#if FELUCCA_UAC_LOCK == 1
    {
        uint32_t c = uac.ring48 ? (UA_LO48 + UA_HI48) / 2u : (UA_LO + UA_HI) / 2u, k = 0, part = 0;
        int32_t e;
        int64_t u;
#if defined(UAC_STAMP) && defined(UAC_STAMP_MS)
        {   /* the frames the host has taken since the last packet went in (the fill falls a packet at a time; this
             * reading falls smoothly) */
            uint32_t dt = UAC_STAMP() - uac.t_q;
            if (uac.flowing && dt < UAC_STAMP_MS)
                part = uac.lk_n * dt * 256u / (UAC_STAMP_MS / 16u);
        }
#endif
        e = (int32_t)(fill << 12) - (int32_t)part;
        if (uac.lk_blocks < UA_LK_CAP) {                /* after the prime: the fill as it is (averaged over the first
                                                         * blocks), not the band's middle, becomes the fill to hold: a
                                                         * start ~12 frames off the middle drove the 0.3 Hz stage to the
                                                         * clamp (-977 ppm, then +730: ~2.4 cents for 3 s, and the jack
                                                         * and USB off by a varying fraction of a sample); the step stays
                                                         * the drift last found meanwhile */
            uac.lk_c += e >> UA_LK_CAP_SH;
            if (++uac.lk_blocks == UA_LK_CAP) {
                int32_t lo = (int32_t)((c - UA_LK_CAP_BAND) << 12), hi = (int32_t)((c + UA_LK_CAP_BAND) << 12);
                uac.lk_c = uac.lk_c < lo ? lo : uac.lk_c > hi ? hi : uac.lk_c;   /* (inside the band) */
            }
        } else {
            e -= uac.lk_c;
            if (!uac.lk_fast && (uac.lk_ef > UA_LK_FAR || uac.lk_ef < -UA_LK_FAR))
                uac.lk_fast = 1;
            else if (uac.lk_fast && uac.lk_ef < UA_LK_FAR / 4 && uac.lk_ef > -UA_LK_FAR / 4)
                uac.lk_fast = 0;
            k = 1u;
            while (uac.lk_blocks >= UA_LK_ST[k].until)
                k++;
            if (uac.lk_fast)
                k = 0;
            uac.lk_blocks++;
            uac.lk_e1 += (e - uac.lk_e1) >> UA_LK_ST[k].sh;
            uac.lk_ef += (uac.lk_e1 - uac.lk_ef) >> UA_LK_ST[k].sh;
            uac.lk_I += (int64_t)uac.lk_ef * UA_LK_ST[k].ki;
            if (uac.lk_I > UA_LK_MAX) uac.lk_I = UA_LK_MAX;
            if (uac.lk_I < -UA_LK_MAX) uac.lk_I = -UA_LK_MAX;
            u = uac.lk_I + (int64_t)uac.lk_ef * UA_LK_ST[k].kp;
            uac_lock_set(u);
        }
    }
#endif
#if FELUCCA_UAC_LOCK == 2
    {   /* the packets take the host's rate exactly; the render gives the FM-1's: the drift moves the fill (the
         * fill at a render's start jumps by up to a packet with the 1 ms frames' place: averaged over ~16 blocks).
         * Out of the band's middle +- UA_LK_M: the next piece tapped in loses or gains one frame, at most one
         * correction every UA_LK_GAP blocks (46 ms: 21.5 frames a second, 490 ppm; far out, as after a host that skipped a frame, every block) */
        uint32_t c, a;
# if FELUCCA_UAC_48K
        c = uac.ring48 ? (UA_LO48 + UA_HI48) / 2u : (UA_LO + UA_HI) / 2u;
# else
        c = (UA_LO + UA_HI) / 2u;
# endif
        uac.favg16 += fill - (uac.favg16 >> 4);
        a = uac.favg16 >> 4;
        if (uac.cool)
            uac.cool--;
        else if (a > c + UA_LK_M) {
            uac.adj = 1;
            uac.cool = a > c + 3u * UA_LK_M ? 0u : UA_LK_GAP;
            uac.favg16 -= 16u;
        } else if (a + UA_LK_M < c) {
            uac.adj = -1;
            uac.cool = a + 3u * UA_LK_M < c ? 0u : UA_LK_GAP;
            uac.favg16 += 16u;
        }
    }
#endif
}

#if FELUCCA_UAC_LOCK == 2
/* the piece (n frames, 3 <= n <= 32) as n - 1 (adj 1) or n + 1 (adj -1) frames spread over the same span: output i at
 * position i * (n - 1) / (m - 1) of the piece, linear between its neighbours (a 3 % stretch for 0.7 ms, then the stream goes on one frame
 * ahead or behind) */
static int32_t ua_lk_buf[2u * 33u];
static const int32_t *uac_lock_piece(const int32_t *x, uint32_t *pn)
{
    uint32_t n = *pn, m = uac.adj > 0 ? n - 1u : n + 1u, i, step;
    step = ((n - 1u) << 16) / (m - 1u);                 /* (the first and the last frame stay where they are) */
    for (i = 0; i < m; i++) {
        uint32_t p = i * step, k = p >> 16, fr = p & 0xFFFFu, ch;
        for (ch = 0; ch < 2u; ch++) {
            int32_t a = x[2u * k + ch];
            if (fr && k + 1u < n)
                a += (int32_t)(((int64_t)(x[2u * k + 2u + ch] - a) * (int32_t)fr) >> 16);
            ua_lk_buf[2u * i + ch] = a;
        }
    }
    if (uac.adj > 0) uac.adj_up++;
    else uac.adj_down++;
    uac.adj = 0;
    *pn = m;
    return ua_lk_buf;
}
#endif

static inline __attribute__((always_inline)) int32_t uac_s16(int32_t v)
{
    return v > 32767 ? 32767 : v < -32768 ? -32768 : v;
}

#if FELUCCA_UAC_48K
/* 44.1 -> 48 kHz (x 160 / 147), one block: each input frame enters the history, then the 1 or 2 output
 * frames whose newest input it is: phase ph, the window of UAC_FIR_N frames ending at it, the taps of phase
 * ph (or of 159 - ph backwards: the prototype is symmetric), Q15 into 32-bit sums (the taps' absolute sum
 * stays under 2.0: no overflow). Frodhes (0.9.5 cpu-trim): in passes of up to RS_C frames, the pass's frames into
 * the history first, then its outputs (the same frames, the same order: tests/py/test_uac_fir.py, bit for bit) */
#define UAC_KNEE 29204                                  /* Frodhes: the 48 kHz knee, fx.c's LIM_T (-1.0 dBFS; 24576 with
                                                         * the -2.7 dBFS ceiling before 0.9.10) */
#define UAC_KNEE_SH 3u                                  /* the excess over it x 1/8: up to 1.97 x the knee fits under full
                                                         * scale (1/2 before 0.9.10, 1.67 x of 24576) */
#if FELUCCA_UAC_LOCK != 1                               /* (locked: uac_tap_lock serves both rates) */
static FR_RAM __attribute__((noinline)) void uac_tap48(const int32_t *out, uint32_t n)
{
    uint32_t w = ua_w, i, wk = w, ph = rs.ph;
    uint32_t *ring = ua_ring;
    if (UA_N - (w - ua_r) < n + n / 8u + 2u) {         /* nobody reads: drop the block (<= n * 160 / 147 + 1) */
        uac.overruns++;
        return;
    }
    while (n) {
        uint32_t c = n < RS_C ? n : RS_C;
        int16_t *hp = rs.l + (UAC_FIR_N - 1u);
        for (i = 0; i < c; i++) {
            hp[i] = (int16_t)uac_s16(out[2u * i]);
            hp[RS_B + i] = (int16_t)uac_s16(out[2u * i + 1u]);
        }
        for (i = 0; i < c; i++) {
            const int16_t *x = rs.l + i;                /* the window: frame i - 11 .. i (R: x[RS_B]) */
            do {
                /* Frodhes (0.9.5): the sums in 64 bits (the core's multiply-accumulate) and the 12 taps unrolled; the
                 * same values as the 32-bit sums: those never overflow (a phase's taps sum to at most 54638 in
                 * absolute value: |sum| < 1.8e9; tests/py/test_uac_fir.py checks the stream bit for bit) */
                int64_t al = 1 << (UAC_FIR_Q - 1), ar = 1 << (UAC_FIR_Q - 1);
#define RS_TAP(i, t) do { int32_t t_ = (t); al += (int64_t)t_ * x[i]; ar += (int64_t)t_ * x[RS_B + (i)]; } while (0)
#define RS_TAPS(c, d) do { RS_TAP(0, c[0]); RS_TAP(1, c[d]); RS_TAP(2, c[2 * d]); RS_TAP(3, c[3 * d]);           \
                           RS_TAP(4, c[4 * d]); RS_TAP(5, c[5 * d]); RS_TAP(6, c[6 * d]); RS_TAP(7, c[7 * d]);   \
                           RS_TAP(8, c[8 * d]); RS_TAP(9, c[9 * d]); RS_TAP(10, c[10 * d]); RS_TAP(11, c[11 * d]); } while (0)
                _Static_assert(UAC_FIR_N == 12u, "RS_TAPS: 12 taps");
                if (ph < UAC_FIR_L / 2u) {
                    const int16_t *c = UAC_FIR[ph];
                    RS_TAPS(c, 1);
                } else {
                    const int16_t *c = &UAC_FIR[UAC_FIR_L - 1u - ph][UAC_FIR_N - 1u];
                    RS_TAPS(c, -1);
                }
#undef RS_TAPS
#undef RS_TAP
                {   /* (the clamp as max, then min: the same value; the ring through a local pointer) */
                    int32_t vl = (int32_t)(al >> UAC_FIR_Q), vr = (int32_t)(ar >> UAC_FIR_Q);
                    /* Frodhes (0.9.7, BUGHUNT #38b): a knee before the clamp. The FIR's phases gain up to 1.67 on a
                     * loud high passage, so a signal the limiter kept at 24000 could overshoot full scale and be
                     * clamped (+2.7 dB measured on the FM-1). Above UAC_KNEE the excess is cut (UAC_KNEE_SH): 29204 ..
                     * 57708 maps to 29204 .. 32767 (symmetric: -x gives -y); below it nothing changes (one unsigned
                     * test a channel; tests/py/test_uac_fir.py) */
                    if ((uint32_t)(vl + UAC_KNEE) > 2u * UAC_KNEE)
                        vl = vl > 0 ? UAC_KNEE + ((vl - UAC_KNEE) >> UAC_KNEE_SH) : -UAC_KNEE - ((-vl - UAC_KNEE) >> UAC_KNEE_SH);
                    if ((uint32_t)(vr + UAC_KNEE) > 2u * UAC_KNEE)
                        vr = vr > 0 ? UAC_KNEE + ((vr - UAC_KNEE) >> UAC_KNEE_SH) : -UAC_KNEE - ((-vr - UAC_KNEE) >> UAC_KNEE_SH);
                    vl = vl < -32768 ? -32768 : vl; vl = vl > 32767 ? 32767 : vl;
                    vr = vr < -32768 ? -32768 : vr; vr = vr > 32767 ? 32767 : vr;
                    ring[wk++ & (UA_N - 1u)] = (uint32_t)(uint16_t)vl | (uint32_t)vr << 16;
                }
                ph += UAC_FIR_M;
            } while (ph < UAC_FIR_L);
            ph -= UAC_FIR_L;
        }
        for (i = 0; i < UAC_FIR_N - 1u; i++) {          /* the last frames before the next pass */
            rs.l[i] = rs.l[c + i];
            rs.r[i] = rs.r[c + i];
        }
        out += 2u * c;
        n -= c;
    }
    rs.ph = ph;
#if FELUCCA_UAC_TONE == 1
    {   /* bench (as uac_tap's at 44.1): the resampler's frames replaced by triangles of period 100 frames (L, R in
         * antiphase), as many as it made, so every recorded frame must follow the one before (BUGHUNT #24) */
        static uint32_t ph48;
        for (i = w; i != wk; i++, ph48 = ph48 == 99u ? 0u : ph48 + 1u) {
            uint32_t q = ph48 < 50u ? ph48 : 100u - ph48, q2 = ph48 < 50u ? 50u - ph48 : ph48 - 50u;
            ring[i & (UA_N - 1u)] = (uint32_t)(uint16_t)(int16_t)((int32_t)q * 1000 - 25000) |
                                    (uint32_t)(uint16_t)(int16_t)((int32_t)q2 * 1000 - 25000) << 16;
        }
    }
#endif
    RING_PUBLISH();
    ua_w = wk;
}
#endif
#endif

#if FELUCCA_UAC_LOCK == 1
/* one piece of the render (n frames) into the ring at the locked step: as uac_tap48 (the history, passes of RS_C
 * frames, the 12 taps unrolled on 64-bit sums, the knee, the clamp), each output at phase lk_P >> 16 of 2560 (phase
 * 2559 - j backwards for j >= 1280), then held one output: written as its sum + (the next sum - the one before) x its
 * fraction of a phase / 2 steps (the slope across it), so the fraction of a phase below the table's grid costs nothing */
static FR_RAM __attribute__((noinline)) void uac_tap_lock(const int32_t *out, uint32_t n)
{
    uint32_t w = ua_w, i, wk = w, P = uac.lk_P, S = uac.lk_S, inv2 = uac.lk_inv2, g = uac.lk_g;
    int32_t spl = uac.lk_sp[0], spr = uac.lk_sp[1], scl = uac.lk_sc[0], scr = uac.lk_sc[1];
    uint32_t *ring = ua_ring;
    if (UA_N - (w - ua_r) < n + n / 8u + 2u) {         /* nobody reads: drop the piece (<= n * 160 / 147 + 1) */
        uac.overruns++;
        return;
    }
    while (n) {
        uint32_t c = n < RS_C ? n : RS_C;
        int16_t *hp = rs.h + 2u * (UAC_FL_N - 1u);
        if (out)                                        /* (0: the tail wrote them there, uac_tap_hist) */
            for (i = 0; i < c; i++) {                   /* (no clip: the tail clipped them, +-32767: TAIL_PRE) */
                hp[2u * i] = (int16_t)out[2u * i];
                hp[2u * i + 1u] = (int16_t)out[2u * i + 1u];
            }
        for (i = 0; i < c; i++) {
            const int16_t *x = rs.h + 2u * i;           /* the window: frame i - 11 .. i (L x[2k], R x[2k + 1]) */
            while (P < UA_LK_ONE) {
                uint32_t j = P >> 16;
                int64_t al = 1 << (UAC_FL_Q - 1), ar = 1 << (UAC_FL_Q - 1);
                int32_t sl, sr, vl, vr;
#if defined(__PI32V2__) && !defined(DINGHY_HOST)
                HAL_KEEP_REG(x);                        /* (the window read in each output: hoisted, its last two
                                                         * samples were kept as 64-bit pairs on the stack) */
#endif
#define LK_TAP(i, t) do { int32_t t_ = (t); al += (int64_t)t_ * x[2 * (i)]; ar += (int64_t)t_ * x[2 * (i) + 1]; } while (0)
#define LK_TAPS(c, d) do { LK_TAP(0, c[0]); LK_TAP(1, c[d]); LK_TAP(2, c[2 * d]); LK_TAP(3, c[3 * d]);             \
                           LK_TAP(4, c[4 * d]); LK_TAP(5, c[5 * d]); LK_TAP(6, c[6 * d]); LK_TAP(7, c[7 * d]);     \
                           LK_TAP(8, c[8 * d]); LK_TAP(9, c[9 * d]); LK_TAP(10, c[10 * d]); LK_TAP(11, c[11 * d]); } while (0)
                _Static_assert(UAC_FL_N == 12u && UAC_FIR_N == 12u, "LK_TAPS: 12 taps, the history's 11 frames");
                if (j < UAC_FL_L / 2u) {
                    const int16_t *t = uac_fl[j];
                    LK_TAPS(t, 1);
                } else {
                    const int16_t *t = &uac_fl[UAC_FL_L - 1u - j][UAC_FL_N - 1u];
                    LK_TAPS(t, -1);
                }
#undef LK_TAPS
#undef LK_TAP
                sl = (int32_t)(al >> UA_LK_SQ);         /* (the sums kept with 11 bits below the output's LSB: |s| <
                                                         * 2^27, so a difference fits 32 bits and the correction is one
                                                         * 32 x 32 multiply's high word; the high word of each sum is
                                                         * used, so both halves keep the one-instruction 64-bit MACs) */
                sr = (int32_t)(ar >> UA_LK_SQ);
                vl = (scl + (int32_t)(((int64_t)(sl - spl) * (int32_t)g) >> 32)) >> (UAC_FL_Q - UA_LK_SQ);
                vr = (scr + (int32_t)(((int64_t)(sr - spr) * (int32_t)g) >> 32)) >> (UAC_FL_Q - UA_LK_SQ);
                if (((uint32_t)(vl + UAC_KNEE) | (uint32_t)(vr + UAC_KNEE)) > 2u * UAC_KNEE) {
                    /* (either above the knee, rare: the 48 kHz path's knee, then the clamp; under it both fit 16 bits
                     * as they are: the same samples, one test) */
                    if ((uint32_t)(vl + UAC_KNEE) > 2u * UAC_KNEE)
                        vl = vl > 0 ? UAC_KNEE + ((vl - UAC_KNEE) >> UAC_KNEE_SH) : -UAC_KNEE - ((-vl - UAC_KNEE) >> UAC_KNEE_SH);
                    if ((uint32_t)(vr + UAC_KNEE) > 2u * UAC_KNEE)
                        vr = vr > 0 ? UAC_KNEE + ((vr - UAC_KNEE) >> UAC_KNEE_SH) : -UAC_KNEE - ((-vr - UAC_KNEE) >> UAC_KNEE_SH);
                    vl = vl < -32768 ? -32768 : vl; vl = vl > 32767 ? 32767 : vl;
                    vr = vr < -32768 ? -32768 : vr; vr = vr > 32767 ? 32767 : vr;
                }
                ring[wk++ & (UA_N - 1u)] = (uint32_t)(uint16_t)vl | (uint32_t)vr << 16;
                spl = scl; scl = sl; spr = scr; scr = sr;
                g = ((P & 0xFFFFu) >> 1) * inv2 >> 11;  /* (this output's fraction x 2^32 / 2 steps, < 2^20) */
                P += S;
            }
            P -= UA_LK_ONE;
        }
        for (i = 0; i < 2u * (UAC_FL_N - 1u); i++)      /* the last frames before the next pass */
            rs.h[i] = rs.h[2u * c + i];
        if (out)
            out += 2u * c;
        n -= c;
    }
    uac.lk_P = P;
    uac.lk_g = g;
    uac.lk_sp[0] = spl; uac.lk_sp[1] = spr; uac.lk_sc[0] = scl; uac.lk_sc[1] = scr;
#if FELUCCA_UAC_TONE == 1
    {   /* bench (as uac_tap48's): the frames made replaced by triangles of period 100 frames (L, R in antiphase) */
        static uint32_t phl;
        for (i = w; i != wk; i++, phl = phl == 99u ? 0u : phl + 1u) {
            uint32_t q = phl < 50u ? phl : 100u - phl, q2 = phl < 50u ? 50u - phl : phl - 50u;
            ring[i & (UA_N - 1u)] = (uint32_t)(uint16_t)(int16_t)((int32_t)q * 1000 - 25000) |
                                    (uint32_t)(uint16_t)(int16_t)((int32_t)q2 * 1000 - 25000) << 16;
        }
    }
#endif
    RING_PUBLISH();
    ua_w = wk;
}
#endif

#if FELUCCA_UAC_LOCK == 1
/* Frodhes (0.9.9): the tail (platform.c) writes the block's frames straight into the resampler's history, as uac_tap_lock
 * would copy them (frame i: L at UAC_HIST[2i], R at UAC_HIST[2i + 1], int16, already clipped), then taps once: the
 * same outputs as eight pieces of 32 (tests/uac_rs_test.c, bit for bit), without the copy, the calls and the passes */
#define UAC_HIST (rs.h + 2u * (UAC_FL_N - 1u))
static __attribute__((noinline)) void uac_tap_hist(uint32_t n)   /* audio ISR: the block in the history, n <= RS_C */
{
    if (uac.feed)
        FR_FAR(uac_tap_lock)(0, n);
}
#endif
static __attribute__((noinline)) void uac_tap(const int32_t *out, uint32_t n)   /* audio ISR: one block (Q15) */
{
    uint32_t w = ua_w, i;
    if (!uac.feed)
        return;
#if FELUCCA_UAC_LOCK == 2
    if (uac.adj && n >= 3u && n <= 32u)
        out = uac_lock_piece(out, &n);
#endif
#if FELUCCA_UAC_LOCK == 1
    FR_FAR(uac_tap_lock)(out, n);                       /* (both rates) */
    (void)w; (void)i;
    return;
#endif
#if FELUCCA_UAC_48K && FELUCCA_UAC_LOCK != 1
    if (uac.ring48) {
        FR_FAR(uac_tap48)(out, n);
        return;
    }
#endif
    if (UA_N - (w - ua_r) < n) {                        /* nobody reads: drop the block */
        uac.overruns++;
        return;
    }
#if FELUCCA_UAC_TONE == 1
    {   /* bench: triangles of period 100 frames instead of the music (L, R in antiphase): every
         * recorded frame must follow the one before, so a lost or repeated frame shows exactly */
        static uint32_t ph;
        for (i = 0; i < n; i++, ph = ph == 99u ? 0u : ph + 1u) {
            uint32_t q = ph < 50u ? ph : 100u - ph, q2 = ph < 50u ? 50u - ph : ph - 50u;
            ua_ring[(w + i) & (UA_N - 1u)] = (uint32_t)(uint16_t)(int16_t)((int32_t)q * 1000 - 25000) |
                                             (uint32_t)(uint16_t)(int16_t)((int32_t)q2 * 1000 - 25000) << 16;
        }
        (void)out;
    }
#else
    for (i = 0; i < n; i++)
        ua_ring[(w + i) & (UA_N - 1u)] = (uint32_t)(uint16_t)uac_s16(out[2u * i]) |
                                         (uint32_t)(uint16_t)uac_s16(out[2u * i + 1u]) << 16;
#endif
    RING_PUBLISH();
    ua_w = w + n;
}

/* the next packet into d: 44 / 45 frames (+-1 to hold the ring's fill), from the ring or, until it is
 * primed, silence. An empty ring repeats the last frame. Returns the frame count. */
static uint32_t uac_packet(uint32_t *d)
{
    uint32_t n = 44u, r, fill, k, i, lo = UA_LO, hi = UA_HI;
#if FELUCCA_UAC_48K
    if (uac.r48) {                                      /* 48 frames a ms, no pattern */
        n = 48u;
        lo = UA_LO48;
        hi = UA_HI48;
    } else
#endif
    {
        uac.acc += UAC_RATE % 1000u;
        if (uac.acc >= 1000u) {
            uac.acc -= 1000u;
            n++;
        }
    }
    if (!uac.go || uac.ring48 != uac.r48) {             /* not primed (at this rate) yet */
        for (i = 0; i < n; i++)
            d[i] = 0;
        return n;
    }
#if FELUCCA_UAC_LOCK
    (void)lo; (void)hi;                                 /* (locked: the packet is always the nominal size) */
#else
    if (uac.fill_min > hi) {
        n++;
        uac.adj_up++;
    } else if (uac.fill_min < lo) {
        n--;
        uac.adj_down++;
    }
#endif
    r = ua_r;
    fill = ua_w - r;
    RING_PUBLISH();                                     /* the slots after the index */
    k = fill < n ? fill : n;
    for (i = 0; i < k; i++)
        d[i] = ua_ring[(r + i) & (UA_N - 1u)];
    if (k < n) {
        uint32_t last = k ? d[k - 1u] : uac.last;
        uac.underruns++;
        for (; i < n; i++)
            d[i] = last;
    }
    uac.last = d[n - 1u];
#if FELUCCA_UAC_LOCK == 1
    uac.lk_n = n;
#endif
    RING_PUBLISH();
    ua_r = r + k;
    uac.pkts++;
    uac.frames += n;
    return n;
}

/* TIMER5, 2 kHz (uac_service; FELUCCA_UAC_PHASE: uac_slot, below), also nested in the render: the host takes one
 * packet per 1 ms frame; queue the
 * next one as soon as the last has gone (TxPktRdy clear). Not paced on SOF-pending (see usb_poll).
 * The ring is fed only while the host reads: it often selects alt 1 long before its first IN token,
 * and a recorder may stop reading without going back to alt 0 (a packet waiting > 20 ms).
 * (FELUCCA_UAC_PHASE: probe 1 is uac_tick's early look, which does not count as a wait. Returns 0 while the packet
 * waits, else 1 + the polls it waited.) */
static uint32_t uac_ep4_poll(uint32_t probe)
{
    uint32_t csr, n, w;
    if (!usb.up || !usb.config || !uac.alt)
        return 0;
#if FELUCCA_UAC_PHASE
    if (!fm1_usb_sie_on()) {                            /* (the SIE held in reset: sie_rd's 0 is not "free", and INDEX */
        uac.idx4 = 0;                                   /*  is lost; not a wait either) */
        return 0;
    }
    if (!uac.idx4) {                                    /* (0.9.9: INDEX written only when another access changed it:
                                                         * an SIE access costs ~2 us of the audio ISR the poll nests in,
                                                         * and the phase-locked poll's looks read TXCSR1 alone) */
        sie_wr(S_INDEX, 4);
        uac.idx4 = !usb.timeouts;
    }
    csr = uac.idx4 ? sie_rd(S_TXCSR1) : 0x01u;
#else
    sie_wr(S_INDEX, 4);
    csr = usb.timeouts ? 0x01u : sie_rd(S_TXCSR1);
#endif
    if (usb.timeouts) {                                 /* (0.9.7) the INDEX write or the TXCSR1 read timed out: sie_rd's
                                                         * 0 is not "free" (a new packet over the queued one lost its
                                                         * frames); as still queued, counted */
        csr = 0x01u;
        uac.sie_to++;
#if FELUCCA_UAC_PHASE
        uac.idx4 = 0;                                   /* (write INDEX again next time) */
#endif
    }
    if (csr & 0x01u) {                                  /* still queued */
        if (!probe && uac.wait < 255u && ++uac.wait == 40u && uac.flowing) {
            uac.flowing = 0;
            uac.stalls++;
        }
        return 0;
    }
    if (uac.queued) {
        if (csr & 0x04u)
            uac.missed++;                               /* UnderRun: an IN token found nothing queued */
        uac.flowing = 1;
    }
    uac.queued = 1;
    w = uac.wait;
    uac.wait = 0;
    n = uac_packet(ep4tx);
    fm1_usb_ep4_send(ep4tx, n * 4u);
    sie_wr(S_TXCSR1, 0x01);                             /* TxPktRdy (and UnderRun cleared) */
#ifdef UAC_STAMP
    if (uac.flowing)
        uac.t_q = UAC_STAMP();
#endif
    return 1u + w;
}

#ifdef UAC_STAMP
/* Frodhes (0.9.9), the main loop (with the interrupts off, before a flash slice: fm1_flash.h FL_SLICE_OK): 1 when an
 * interrupts-off stretch of ~300 us costs the USB audio stream nothing now: the stream does not flow, or a packet
 * went in less than UAC_SLICE_WIN ago right after the host took the last. The next IN token is then ~1 ms after the
 * last, so the stretch ends with most of the frame left for the poll that queues the next packet (uac_tick runs it
 * as soon as the stretch ends); a slice at a random moment may instead cover that poll's whole margin. */
static int uac_slice_ok(void)
{
    return !*(volatile uint8_t *)&uac.flowing || UAC_STAMP() - uac.t_q < UAC_SLICE_WIN;   /* (read anew: a wait loop) */
}
#endif

static void uac_service(void)
{
    (void)uac_ep4_poll(0);
}

#if FELUCCA_UAC_PHASE
/* Frodhes (0.9.9): the EP4 poll on its own phase. The host takes the queued packet at about the same place in every
 * 1 ms frame, and the next packet must be queued before the next frame's IN token, else that token finds nothing
 * (uac.missed: no data that frame). This controller holds one IN packet (no double buffering), so the margin is the
 * time from the token to the poll that queues the next packet. With the 2 kHz poll at a fixed phase that is anywhere
 * in 0..500 us, the poll after an interrupts-off stretch waits for its tick in the 5 (the stretch folds the ticks),
 * and a stretch of ~520 us may already cost a frame (tests/py/uac_timing.py). Here:
 *   - the poll still runs every 5 ticks (2 kHz), but on its own count, and after a stretch (uac_tick's n: the ticks
 *     it folded) a poll whose time has passed runs at once;
 *   - its phase follows the token: once in UA_PROBE frames the poll expected to queue looks one tick (100 us) early,
 *     and if the packet has gone already it queues the next one there, keeps that phase and looks again the next
 *     frame; a poll that finds the packet still there one frame after the last (two waits: the token came late,
 *     drift or jitter) looks again on each of the next 4 ticks, and the tick that queues becomes the phase;
 *   - when the stream starts or resumes after a gap, the looks follow the frame's first wait once, so the poll
 *     locks on within a frame.
 * So the next packet is queued within ~100 us of the token and a stretch may last ~900 us (less the token's
 * movement) before a frame is missed. The same polls, plus two SIE accesses once in UA_PROBE frames and the looks
 * after a late token; the counters (missed, stalls, sie_to) count as before (looks are not waits). */
static __attribute__((noinline)) void uac_slot(uint32_t c)
{
    uint32_t q, look = uac.look;
    if (c == 1u) {                                      /* one tick before the poll */
        uac.cd = 1;
        if (uac.wait != 1u || ++uac.probe < UA_PROBE)
            return;                                     /* (look before the poll expected to queue, now and then) */
        uac.probe = 0;
        if (!uac_ep4_poll(1))
            return;                                     /* still queued: the poll stays where it is */
        uac.ph_early++;                                 /* queued a tick early: the poll keeps this phase, */
        uac.probe = UA_PROBE;                           /* and looks again next frame */
        uac.cd = 5;
        return;
    }
    q = uac_ep4_poll(look != 0u);                       /* (c 0, or past it: ticks folded, or the first tick) */
    uac.cd = 5;
    if (c + 4u < 4u)                                    /* (late by 1..4 ticks, a stretch folded them: the next poll */
        uac.cd = (uint8_t)(5u + c);                     /*  on the old grid, the phase not pushed late by the stretch) */
    uac.look = 0;
    if (q) {
        uac.ph_late += look != 0u;                      /* queued on a look: the poll keeps this phase */
        if (q > 3u)
            uac.acq = 1;                                /* (after a gap: the host starts, or resumes: lock again) */
        return;
    }
    if (look ? look < UA_LOOKS : uac.wait == 2u || (uac.wait == 1u && uac.acq)) {
        uac.look = (uint8_t)(look + 1u);                /* late, or locking: look again next tick */
        uac.acq = 0;
        uac.cd = 1;
    } else if (look) {
        uac.cd = 1;                                     /* (the 4 looks done: the poll in its place, next tick) */
    }
}

/* TIMER5, every tick (10 kHz), n: the ticks since the last one (1; more when an interrupts-off stretch folded some) */
static inline __attribute__((always_inline)) void uac_tick(uint32_t n)
{
    uint32_t c = uac.cd - n;
    if (c - 2u < 4u)                                    /* (2..5: not this tick, nor the one before the poll) */
        uac.cd = (uint8_t)c;
    else
        uac_slot(c);
}
#endif
#endif

static void usb_poll(void)                              /* TIMER5 ISR, 2 kHz */
{
    uint32_t iu, it, ir;
    if (!usb.up)
        return;
    if (fm1_usb_sof_take()) {                           /* SOF pending: not a reliable "host is there", */
                                                        /* it keeps firing with the cable out */
        usb.sof_seen++;
        usb.no_sof = 0;
    } else if (++usb.no_sof > usb.max_gap) {
        usb.max_gap = usb.no_sof;
    }
    if (usb.config) {                                   /* the frame number only advances with a real host */
        static uint32_t tick;
        if (++tick >= 20u) {                            /* every 20 polls = 10 ms */
            uint16_t f;
            tick = 0;
            f = (uint16_t)(sie_rd(S_FRAME1) | (sie_rd(S_FRAME2) & 7u) << 8);
            if (f != usb.frame) {
                usb.frame = f;
                usb.frame_same = 0;
                usb.suspended = 0;
            } else if (++usb.frame_same >= 10u && !usb.suspended) {   /* 100 ms frozen: unplugged / host asleep */
                usb.suspended = 1;
                usb.frame_stalls++;
            }
        }
    }
    iu = sie_rd(S_INTRUSB);
#ifdef FELUCCA_LOADER
    it = sie_rd(S_INTRTX1) | sie_rd(S_INTRTX2) << 8;    /* the update loader reads both */
    ir = sie_rd(S_INTRRX1) | sie_rd(S_INTRRX2) << 8;
#else
    it = sie_rd(S_INTRTX1);                             /* EP0..7 only: skipping INTR*2 saves */
    ir = sie_rd(S_INTRRX1);                             /* 2 SIE round trips per poll */
#endif
    if (iu & 0x01u) {                                   /* suspend: the bus went idle */
        usb.suspends++;
        usb.suspended = 1;
    }
    if (iu & 0x02u)                                     /* resume */
        usb.suspended = 0;
    if (iu & 0x04u) {                                   /* bus reset */
        usb.resets++;
        sie_wr(S_FADDR, 0);
        usb.config = 0;
        usb.suspended = 0;
        mo_r = mo_w;                                    /* nothing stale for the next host */
        usb.sx_on = 0;
#if FELUCCA_OTA
        so_r = so_w;
        sx_collect = 0;
#endif
        usb.e0_tx = 0;
        usb.has_pend_addr = 0;
        usb.rx_pend = 0;
#if FELUCCA_CDC
        cdc.dtr = 0;
        cdc.e0_rx = 0;
        cdc.rx_pend = 0;
#endif
#if FELUCCA_UAC
        uac.e0_rx = 0;
        uac_stream(0);
        uac_rate_set(UAC_RATE);
#endif
        fm1_usb_ep0_buf(ep0buf);
        sie_wr(S_INTRUSBE, 0x07);
        sie_wr(S_INTRTX1E, 0x01);
        sie_wr(S_INTRRX1E, 0);
    }
    if (it & 0x01u)
        ep0_service();
    if (ir & 0x02u)
        usb.rx_pend = 1;                                /* (the flag reads once: remember the packet) */
    if (usb.rx_pend)
        ep1_rx();
    if (usb.config)
        ep1_tx();
#if FELUCCA_CDC
    if (usb.config) {
        if (ir & 0x08u)
            cdc.rx_pend = 1;
        if (cdc.rx_pend)                                /* not every poll: 3 SIE round trips each */
            ep3_rx();
        if (cdc.dtr)
            ep3_tx();
    }
#endif
#if FELUCCA_UAC && !FELUCCA_UAC_PHASE
    uac_service();                                      /* (FELUCCA_UAC_PHASE: uac_tick, from TIMER5 itself) */
#endif
}

static void usb_start(void)                             /* boot, or main-loop retry while usb.up == 0 */
{
    usb.timeouts = 0;
#if FELUCCA_UAC_PHASE
    uac.idx4 = 0;                                       /* (the reset puts INDEX back to 0; usb_retry runs this with
                                                         * usb.config and uac.alt still set; usb.up is 0 meanwhile, so
                                                         * no EP4 poll runs until the end) */
#endif
#if FELUCCA_UAC_LOCK == 1
    uac_lock_init();                                    /* (the locked stream's table into RAM, once) */
#endif
    fm1_usb_reset();                                    /* reset whatever the ROM left */
    fm1_delay_ms(25);
    fm1_usb_attach(ep0buf);
    sie_wr(S_POWER, 0x60);
    sie_wr(S_INTRUSBE, 0x07);                           /* suspend, resume, reset (polled) */
    sie_wr(S_INTRTX1E, 0x01);
    sie_wr(S_INTRTX2E, 0);
    sie_wr(S_INTRRX1E, 0);
    sie_wr(S_INTRRX2E, 0);
    fm1_usb_ep_enable(0x1Fu);
    usb.up = usb.timeouts < 3u;                         /* a dead SIE leaves USB off */
}

/* Powered on without a cable, the SIE does not answer and usb_start leaves
 * USB off: retry once a second from the main loop so a cable plugged in
 * later still enumerates. */
static void usb_retry(uint32_t now_ms)
{
    static uint32_t t;
    if (usb.up || usb.detached || now_ms - t < 1000u)
        return;
    t = now_ms;
    usb.retries++;
    usb_start();
}

static void usb_detach(void)
{
    usb.detached = 1;
    usb.up = 0;
#if FELUCCA_UAC_PHASE
    uac.idx4 = 0;
#endif
    fm1_usb_off();
}
