/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* MIDI IN on the TRS jack: PH8 -> input channel 1 -> UART1 RX, 31250 baud, RX DMA into a 128-byte ring, polled from
 * the TIMER5 ISR (no UART IRQ). From Felucca 1.0 (firmware/src/midi_uart.c, GPL-3.0); Frodhes (0.9.1): the messages go
 * into usb.c's MIDI in ring next to USB-MIDI, as USB-MIDI packets (cable 0, CIN = status >> 4), so the app reads both
 * the same way (midi_get); uart_midi_on (the MIDI page's TRS IN) lets the app ignore the jack.
 * Clock / Start / Continue / Stop pass; other realtime, system common and SysEx are ignored (no update over DIN). */
#include "../hal/fm1_uart.h"

#define UM_RING 128u
static volatile uint8_t um_ring[UM_RING] __attribute__((aligned(16)));
static volatile uint8_t uart_midi_on = 1;          /* the app: 0 = the TRS input is ignored */
static struct {
    uint32_t rd, pend;
    volatile uint32_t bytes, drops, msgs;
    uint8_t st, need, got, d0, sysex;
} um;

static int um_enqueue(uint32_t pkt)                /* TIMER5 ISR, the same context as usb_poll: one writer at a time */
{
    if (!uart_midi_on) return 1;
    if (mi_w - mi_r >= MQ) return 0;
    midi_in_q[mi_w % MQ] = pkt;
    RING_PUBLISH();
    mi_w++;
    return 1;
}

static void uart_midi_init(void)                   /* before the TIMER5 start: PORTH RMW */
{
    fm1_uart1_midi_init(um_ring, UM_RING);
}

static uint32_t um_len(uint32_t s)                 /* data bytes of a channel status */
{
    return (s & 0xE0u) == 0xC0u ? 1u : 2u;         /* Cx Dx: 1, else 2 */
}

static void um_byte(uint32_t b)
{
    if (b >= 0xF8u) {
        if (b == 0xF8u || b == 0xFAu || b == 0xFBu || b == 0xFCu) {
            if (um_enqueue(0xFu | b << 8)) um.msgs++;
            else um.drops++;
        }
        return;                                    /* realtime keeps running status */
    }
    if (b & 0x80u) {
        um.sysex = b == 0xF0u;
        um.st = b < 0xF0u ? (uint8_t)b : 0;        /* system common / SysEx cancel running status */
        um.need = (uint8_t)um_len(b);
        um.got = 0;
        return;
    }
    if (um.sysex || !um.st)
        return;
    if (um.need == 2u && !um.got) {
        um.d0 = (uint8_t)b;
        um.got = 1;
        return;
    }
    {
        uint32_t d1 = um.need == 2u ? um.d0 : b, d2 = um.need == 2u ? b : 0u;
        uint32_t pkt = (um.st >> 4) | (uint32_t)um.st << 8 | d1 << 16 | d2 << 24;
        um.got = 0;
        if (um_enqueue(pkt)) um.msgs++;
        else um.drops++;
    }
}

static void uart_midi_take(uint32_t n)
{
    um.pend += n;
    if (um.pend > UM_RING) {
        um.drops += um.pend - UM_RING;
        um.rd = (um.rd + um.pend - UM_RING) & (UM_RING - 1u);
        um.pend = UM_RING;
        um.st = um.got = um.sysex = 0;              /* lost bytes: wait for a complete new status */
    }
    while (um.pend) {
        um_byte(um_ring[um.rd]);
        um.rd = (um.rd + 1u) & (UM_RING - 1u);
        um.pend--;
        um.bytes++;
    }
}
static void uart_midi_poll(void)                   /* TIMER5 ISR, 2 kHz: <= ~7 bytes per call at 31250 baud */
{
    uart_midi_take(fm1_uart1_rx_take());
}
