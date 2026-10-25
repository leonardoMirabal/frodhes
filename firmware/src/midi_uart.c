/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* MIDI IN on the TRS jack: PH8 -> input channel 1 -> UART1 RX, 31250 baud,
 * RX DMA into a 256-byte ring, polled from the TIMER5 ISR (no UART IRQ). (Frodhes: 256, was 128: 82 ms at
 * 31250 baud, more than a ~50 ms flash erase with the IRQs off, so a full line loses nothing over one.)
 * Built only with FELUCCA_UART=1. Channel messages, clock and transport (F8 FA FB FC) go into midi_in_q next to
 * USB, as USB-MIDI packets with cable 1 (CIN = status >> 4), so midi_get tells the jack from USB (2, not 1);
 * other realtime, system common and SysEx are dropped.
 *
 * The ring is read by content, not by the DMA count: every slot the parser has
 * not yet been given holds UM_EMPTY, and the reader takes bytes until it meets
 * one. The count (HRXCNT, latched every poll) can miss a byte while the byte
 * itself is in the ring: the reader then stays one byte behind, so each message
 * ends only with the next byte (a note-off held until the next note). Following
 * the content cannot drift. (Fix from Felucca [Salt] 0.9-salt14 by ChanceTheMaker,
 * found by keremimo on an FM-1 with an Akai MPC Sample; GPL-3.0.) */
#include "../hal/fm1_uart.h"   /* registers; relative, so the host tests find it too */

#define UM_RING 256u           /* a power of two (the reader masks); fm1_uart1_midi_init takes any length */
#define UM_EMPTY 0xFDu         /* undefined MIDI realtime byte: never sent, marks unwritten slots */
static volatile uint8_t um_ring[UM_RING] __attribute__((aligned(16)));
static volatile uint8_t uart_midi_on = 1;          /* Frodhes: the app (the MIDI page's TRS IN): 0 = the jack is ignored */
static struct {
    uint32_t rd, bytes, drops, msgs;
    uint8_t st, need, got, d0, sysex;
} um;

static void uart_midi_init(void)                   /* before timer5_start(): PORTH RMW */
{
    uint32_t i;
    for (i = 0; i < UM_RING; i++)
        um_ring[i] = UM_EMPTY;
    fm1_uart1_midi_init(um_ring, UM_RING);
}

static uint32_t um_len(uint32_t s)                 /* data bytes of a channel status */
{
    return (s & 0xE0u) == 0xC0u ? 1u : 2u;         /* Cx Dx: 1, else 2 */
}

static void um_byte(uint32_t b)
{
    if (b >= 0xF8u) {                              /* realtime: the state is kept; clock and transport */
        static uint8_t clk_n;                      /* (Frodhes: the pulses counted here, a dropped one too) */
        if (b == 0xF8u) clk_n++;
        if ((b == 0xF8u || b == 0xFAu || b == 0xFBu || b == 0xFCu) && uart_midi_on && mi_w - mi_r < MQ) {
            midi_in_q[mi_w % MQ] = 0x1Fu | b << 8 | ((MIDI_RX_STAMP() & 0xFFFu) | (clk_n & 15u) << 12) << 16;
                                                   /* (cable 1: from the TRS jack; Frodhes: 12 bits of its arrival and
                                                    * 4 of the count, for tempo.c) */
            RING_PUBLISH();
            mi_w++;
        }
        return;
    }
    if (b == 0xF2u) {                              /* Frodhes: Song Position (2 data bytes), passed on (CIN 3) */
        um.sysex = 0;
        um.st = 0xF2u;
        um.need = 2;
        um.got = 0;
        return;
    }
    if (b & 0x80u) {
        um.sysex = b == 0xF0u;
        um.st = b < 0xF0u ? (uint8_t)b : 0;        /* system common/SysEx cancel running status */
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
        uint32_t pkt = 0x10u | (um.st == 0xF2u ? 3u : um.st >> 4u) | (uint32_t)um.st << 8 | d1 << 16 | d2 << 24;   /* (cable 1) */
        um.got = 0;
        if (um.st == 0xF2u) um.st = 0;             /* (system common: no running status) */
        if (!uart_midi_on) {
            /* (Frodhes: TRS IN off, parsed but not passed on) */
        } else if (mi_w - mi_r < MQ) {
            midi_in_q[mi_w % MQ] = pkt;
            RING_PUBLISH();
            mi_w++;
            um.msgs++;
        } else {
            um.drops++;
        }
    }
}

/* the bytes the DMA has written since the last call; a received UM_EMPTY (line
 * noise) is passed over once the byte after it has landed */
static void um_drain(void)
{
    uint32_t n;
    for (n = 0; n < UM_RING; n++) {
        uint32_t b = um_ring[um.rd];
        if (b == UM_EMPTY) {
            if (um_ring[(um.rd + 1u) & (UM_RING - 1u)] == UM_EMPTY)
                break;
            b = um_ring[um.rd];                    /* the DMA writes in order: this one has landed too */
        }
        um_ring[um.rd] = UM_EMPTY;
        um.rd = (um.rd + 1u) & (UM_RING - 1u);
        um.bytes++;
        um_byte(b);                                /* a UM_EMPTY is an unknown realtime byte: ignored */
    }
}

static __attribute__((noinline)) void uart_midi_poll(void)   /* TIMER5 ISR, same context as usb_poll */
{
    fm1_uart1_rx_take();                           /* acknowledges the pendings; the count is not used */
    um_drain();
}

/* Frodhes: the TIMER5 ISR's call, every 2 kHz poll: the RDC latch (fm1_uart1_rx_take) on every call, the drain only when
 * the ring holds a byte (the slot to read, or the one after it: um_drain passes a received UM_EMPTY once the byte after
 * it has landed). The latch is what puts the last bytes of a message into the ring: the UART's RX DMA holds the tail
 * of a burst until RDC or more bytes push it out, so a 3-byte note waited for the next latch. Latching only every
 * 64th quiet call (c9d8530) made a lone note over TRS 0-32 ms late (the FM-1, hil_midi_parity: TRS onsets spread
 * 34 ms against USB's 6); now it is <= 0.5 ms. */
static inline void uart_midi_tick(void)
{
    fm1_uart1_rx_take();                           /* acknowledges the pendings, flushes the DMA's held bytes */
    if (um_ring[um.rd] != UM_EMPTY || um_ring[(um.rd + 1u) & (UM_RING - 1u)] != UM_EMPTY)
        um_drain();
}
