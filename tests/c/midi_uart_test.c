/* SPDX-License-Identifier: GPL-3.0-only */
/* firmware/src/midi_uart.c (the TRS MIDI IN parser, Dinghy 0.4 + Frodhes' uart_midi_on) on the host: a fake DMA
 * writes bytes into its ring, the poll turns them into the shared queue's packets. Built by tests/py/test_midi_uart.py
 * with a stub fm1_uart.h (no registers): every message once, cable 1, running status, realtime inside a message,
 * SysEx and system common dropped, TRS IN off drops the jack but keeps the parser's state, a full queue drops. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MQ 64u
static uint32_t midi_in_q[MQ];
static volatile uint32_t mi_w, mi_r;
#define T_MS 0x12345u                                  /* the arrival stamp in realtime packets (fm1_ms, 16 bits) */
#define MIDI_RX_STAMP() T_MS
#define RING_PUBLISH() ((void)0)
#include "src/midi_uart.c"

static uint32_t dma_w;                             /* the fake DMA's write position in um_ring */
static void dma(const uint8_t *b, uint32_t n)
{
    while (n--) um_ring[dma_w++ & (UM_RING - 1u)] = *b++;
}
static int fails;
static uint8_t held[4], held_n;                    /* the DMA's held tail, written by the latch (rx_latch_hook) */
static void latch_held(void) { dma(held, held_n); held_n = 0; }
static void expect(const char *what, const uint32_t *want, uint32_t n)
{
    uint32_t i, got = mi_w - mi_r;
    int bad = got != n;
    for (i = 0; !bad && i < n; i++)
        bad = (midi_in_q[(mi_r + i) % MQ] & ((midi_in_q[(mi_r + i) % MQ] & 15u) == 15u ? 0x0FFFFFFFu : ~0u)) != want[i];
                                                   /* (realtime: the pulse count's 4 bits apart, checked below) */
    printf("%s  %s (%u packets)\n", bad ? "FAIL" : "ok  ", what, got);
    if (bad) {
        for (i = 0; i < got; i++) printf("      got  %08X\n", midi_in_q[(mi_r + i) % MQ]);
        for (i = 0; i < n; i++) printf("      want %08X\n", want[i]);
        fails++;
    }
    mi_r = mi_w;                                   /* the app read them */
}
#define PK(st, d1, d2) (0x10u | ((uint32_t)(st) >> 4) | (uint32_t)(st) << 8 | (uint32_t)(d1) << 16 | (uint32_t)(d2) << 24)
#define RT(b) (0x1Fu | (uint32_t)(b) << 8 | (T_MS & 0xFFFu) << 16)

int main(void)
{
    uart_midi_init();
    {   /* a note on and off, each once, cable 1 (midi_get: 2) */
        static const uint8_t b[] = {0x90, 60, 100, 0x80, 60, 0};
        static const uint32_t w[] = {PK(0x90, 60, 100), PK(0x80, 60, 0)};
        dma(b, sizeof b); uart_midi_poll(); uart_midi_poll();
        expect("note on / off", w, 2);
    }
    {   /* running status, a clock inside a message, a program change (1 data byte) */
        static const uint8_t b[] = {0x91, 64, 90, 67, 0xF8, 91, 0xC2, 5};
        static const uint32_t w[] = {PK(0x91, 64, 90), RT(0xF8), PK(0x91, 67, 91), PK(0xC2, 5, 0)};
        dma(b, sizeof b); uart_midi_poll();
        expect("running status, a clock inside a message, program change", w, 4);
    }
    {   /* a message split over two polls (the DMA had only part of it) */
        static const uint8_t b1[] = {0xB0, 7}, b2[] = {100};
        static const uint32_t w[] = {PK(0xB0, 7, 100)};
        dma(b1, sizeof b1); uart_midi_poll(); dma(b2, sizeof b2); uart_midi_poll();
        expect("a message over two polls", w, 1);
    }
    {   /* SysEx (no update over DIN), system common, active sensing: dropped; start / continue / stop pass */
        static const uint8_t b[] = {0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7, 0xF2, 1, 2, 0xFE, 0xFA, 0xFB, 0xFC};
        static const uint32_t w[] = {0x13u | 0xF2u << 8 | 1u << 16 | 2u << 24, RT(0xFA), RT(0xFB), RT(0xFC)};
        dma(b, sizeof b); uart_midi_poll();
        expect("SysEx, active sensing dropped; song position (Frodhes, CIN 3) and transport pass", w, 4);
    }
    {   /* TRS IN off: nothing reaches the queue, but the parser follows (running status after it is right) */
        static const uint8_t b1[] = {0x90, 48, 80, 0xF8}, b2[] = {50, 81};
        static const uint32_t w[] = {PK(0x90, 50, 81)};
        uart_midi_on = 0; dma(b1, sizeof b1); uart_midi_poll();
        expect("TRS IN off: dropped", w, 0);
        uart_midi_on = 1; dma(b2, sizeof b2); uart_midi_poll();
        expect("TRS IN on again: running status kept", w, 1);
    }
    {   /* many polls with nothing new: nothing (the ring is read by content, not by a count) */
        int k;
        for (k = 0; k < 1000; k++) uart_midi_poll();
        expect("idle polls", NULL, 0);
    }
    {   /* across the ring's wrap, 300 notes (the ring is 256 bytes: 900 bytes wrap it): every one once, in order */
        static uint32_t w[MQ];
        uint32_t k, n = 0, total = 0;
        int bad = 0;
        for (k = 0; k < 300u; k++) {
            uint8_t b[3] = {0x90, (uint8_t)(k & 0x7Fu), (uint8_t)(1u + k % 100u)};
            dma(b, 3); uart_midi_poll();
            w[0] = PK(0x90, b[1], b[2]);
            n = mi_w - mi_r;
            bad |= n != 1u || midi_in_q[mi_r % MQ] != w[0];
            total += n;
            mi_r = mi_w;
        }
        printf("%s  300 notes over the ring's wrap, each once (%u packets)\n", bad ? "FAIL" : "ok  ", total);
        fails += bad;
    }
    {   /* a full queue: the overflow is dropped and counted, nothing written twice */
        uint32_t k, d0 = um.drops;
        for (k = 0; k < MQ + 5u; k++) {
            uint8_t b[3] = {0x90, 10, 20};
            dma(b, 3); uart_midi_poll();
        }
        printf("%s  a full queue: %u in, %u dropped\n", mi_w - mi_r == MQ && um.drops - d0 == 5u ? "ok  " : "FAIL",
               mi_w - mi_r, um.drops - d0);
        fails += !(mi_w - mi_r == MQ && um.drops - d0 == 5u);
        mi_r = mi_w;
    }
    {   /* the ISR's call (uart_midi_tick): the latch on every call, the drain only with a byte in; a byte at once */
        uint32_t k, t0 = takes, n0, d0 = um.bytes;
        int bad;
        for (k = 0; k < 640u; k++) uart_midi_tick();
        bad = takes - t0 != 640u || mi_w != mi_r || um.bytes != d0;
        printf("%s  the tick, idle: %u latches in 640 calls, nothing drained\n", bad ? "FAIL" : "ok  ", takes - t0);
        fails += bad;
        for (k = 0, bad = 0; k < 300u; k++) {      /* 300 notes, one tick each, over the ring's wrap */
            uint8_t b[3] = {0x90, (uint8_t)(k & 0x7Fu), (uint8_t)(1u + k % 100u)};
            n0 = takes;
            dma(b, 3); uart_midi_tick();
            bad |= takes != n0 + 1u || mi_w - mi_r != 1u || midi_in_q[mi_r % MQ] != PK(0x90, b[1], b[2]);
            mi_r = mi_w;
        }
        printf("%s  the tick: 300 notes, each polled at once\n", bad ? "FAIL" : "ok  ");
        fails += bad;
    }
    {   /* the DMA holds a burst's bytes until the latch (RDC): a lone note on, none of it in the ring yet, quiet after
         * it. The very next tick latches it and passes the note on (c9d8530 latched every 64th quiet call: <= 32 ms) */
        static const uint32_t w[] = {PK(0x90, 62, 77)};
        uint32_t k;
        uart_midi_tick();                          /* (the idle phase: a fresh count, so the old 64th call is far) */
        held[0] = 0x90; held[1] = 62; held[2] = 77; held_n = 3;
        rx_latch_hook = latch_held;
        for (k = 0; k < 3u && mi_w == mi_r; k++) uart_midi_tick();
        rx_latch_hook = NULL;
        printf("%s  the tick: a held note latched at once (%u ticks)\n", mi_w - mi_r == 1u && k <= 2u ? "ok  " : "FAIL", k);
        fails += !(mi_w - mi_r == 1u && k <= 2u);
        expect("the tick: the held note", w, 1);
    }
    {   /* a received UM_EMPTY (line noise) before a note: the slot after it is seen, the note passes */
        static const uint8_t b[] = {UM_EMPTY, 0x90, 61, 99};
        static const uint32_t w[] = {PK(0x90, 61, 99)};
        dma(b, sizeof b); uart_midi_tick();
        expect("the tick: a noise byte, then a note", w, 1);
    }
    {   /* Frodhes: the clock's 4-bit pulse count goes on when the queue is full (the app sees the dropped ones) */
        static const uint8_t f8[] = {0xF8};
        uint32_t c0, c1, k, bad;
        mi_r = mi_w;
        dma(f8, 1); uart_midi_poll();
        c0 = midi_in_q[mi_r % MQ] >> 28;
        mi_r = mi_w;
        mi_w = mi_r + MQ;                              /* (the queue full: 3 pulses dropped) */
        for (k = 0; k < 3u; k++) { dma(f8, 1); uart_midi_poll(); }
        mi_r = mi_w;
        dma(f8, 1); uart_midi_poll();
        c1 = midi_in_q[mi_r % MQ] >> 28;
        bad = mi_w - mi_r != 1u || ((c1 - c0) & 15u) != 4u;
        printf("%s  the pulse count counts the dropped pulses (%u -> %u)\n", bad ? "FAIL" : "ok  ", c0, c1);
        fails += bad;
        mi_r = mi_w;
    }
    printf("%d failed\n", fails);
    return fails != 0;
}
