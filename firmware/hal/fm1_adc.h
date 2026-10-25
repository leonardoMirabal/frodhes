/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* FM-1 SARADC: PB6 = MASTER pot (ch4),
 * PB1 = battery divider (ch3). Polled, 10 bit, from the main loop.
 *
 *   fm1_adc_init()        both pins analog inputs, no pulls
 *   fm1_adc_read(ch)      0..1023, -1 on timeout
 *
 * Frodhes (key velocity, src/keyvel.h): the key matrix rows PA7 (ch 0) and PA8 (ch 1) are converter pins too, and
 * the 10 kHz scan tick (hal/fm1_keyadc.h) converts them. Then the tick owns the converter and schedules MASTER and
 * the battery in fixed slots of its own, so nothing ever converts in a blocking loop under its feet.
 * The non-blocking conversion (start, then the result at a later tick) and its settings (divider 7, start delay 2)
 * are after charlesvestal/fm1-velocity (https://github.com/charlesvestal/fm1-velocity, commit 8f62ce0,
 * GPL-3.0-only, by Charles Vestal); the slot scheduler is Frodhes' own.
 *   tick (ISR) side, at most one conversion per tick, started in one tick and taken in a later one:
 *     fm1_adc_sched_busy()     a conversion runs: fm1_adc_sched_take has something to do (else it returns NONE)
 *     fm1_adc_sched_take(&v)   first in every tick: last tick's conversion. FM1_ADC_GOT_NONE (nothing, a slot's
 *                              reading kept in the cache, or the battery still converting), FM1_ADC_GOT_KEY (v =
 *                              0..1023, the key row fm1_adc_key_start converted), FM1_ADC_GOT_LOST (a key
 *                              conversion not done in time: aborted, counted). It must run in every tick.
 *     fm1_adc_sched_slot(n)    column n just latched: 1 if this tick belongs to MASTER (column 0, even frames:
 *                              every 2.2 ms, at FM1_ADC_CFG_MASTER) or the battery (column 1, every 256th frame
 *                              (~0.28 s), right after MASTER's conversion as in 0.9.4's main loop, at the SDK's
 *                              settings, held over up to FM1_ADC_BATT_TICKS ticks with nothing else converting:
 *                              ~210 us, so 3 ticks), or the converter is otherwise busy; then no key conversion
 *                              this tick. 0: the converter is free for a key
 *     fm1_adc_key_start(row)   start row 0 (PA7) / 1 (PA8) at FM1_ADC_CFG_KEY; only after a 0 from _slot, and it
 *                              refuses (starts nothing) while any conversion runs: one at a time, always
 *   main loop side: fm1_adc_read(FM1_ADC_MASTER / FM1_ADC_BATT) keeps its signature. Once the scheduler runs
 *   (fm1_adc_sched.on, set by the tick's first column-0/1 slot) it converts nothing: MASTER returns each slot
 *   reading once (-1 when no conversion is new since the last call: platform_poll's knob_avg then moves once per
 *   reading, never faster than 0.9.4's (a main-loop pass of a few ms saw a fresh reading each time)), the battery
 *   its last slot reading (-1 before the first); other channels -1. So platform.c and ui.c stay unchanged, and
 *   MASTER and the battery keep running whatever TOUCH is (FIXED and KV_DEV_OFF included). Before that (the
 *   scheduler not wired: the stubbed hooks, or the boot) fm1_adc_read converts as it always did; a handshake
 *   (mbusy) keeps the tick from starting a conversion under such a read and the read from starting one under the
 *   tick's.
 * fm1_adc_sched (.bss.fm1_adc_sched: out of the compiler's global merge) is written by the tick only, except mbusy
 * and clr (one byte each, main loop) and the developer counters convs / timeouts / batt_to, which a mode command
 * (hal/fm1_keyadc.h fm1_ka_dev_mode) zeroes at once with single halfword stores; the main loop reads single fields.
 * Settings (CON without channel / enable / start): bits 0-2 the clock divider, bit 3 set, bits 12-15 the start
 * delay (x 8 ADC clocks). Bit 4 enable, bit 6 start / clear pending, bit 7 pending (done), bits 8-11 the channel.
 * Timing (fm1-velocity's lab notes): divider 7: 26 us + 17 us per delay step (delay 2: ~60 us, inside a tick);
 * every divider below 7 returns rails at short delays. Divider 6 at delay 15 (the SDK's) ~210 us. */
#pragma once
#include <stdint.h>
#include "fm1_gpio.h"

#ifndef FM1_ADC_CON                            /* (tests/c/adc_sched_test.c models the converter) */
#define FM1_ADC_CON (*(volatile uint32_t *)0x13100u)
#define FM1_ADC_RES (*(volatile uint32_t *)0x13104u)
#define FM1_WLA_CON0 (*(volatile uint32_t *)0x11900u)
#define FM1_ADC_SCHED_SECTION __attribute__((section(".bss.fm1_adc_sched")))
#endif
enum { FM1_ADC_ROW_PA7 = 0, FM1_ADC_ROW_PA8 = 1, FM1_ADC_BATT = 3, FM1_ADC_MASTER = 4 };
#define FM1_ADC_CFG(div, delay) ((uint32_t)((div) & 7u) | 8u | ((uint32_t)((delay) & 15u) << 12))
#define FM1_ADC_CFG_SDK FM1_ADC_CFG(6, 15)     /* fm1_adc_read's (0xF04E less its start bit): the battery's slot */
#define FM1_ADC_CFG_KEY FM1_ADC_CFG(7, 2)      /* the keys' and MASTER's: ~60 us, done by the next tick */
#ifndef FM1_ADC_CFG_MASTER                     /* (a measurement build may try the SDK's: then MASTER_TICKS 3) */
#define FM1_ADC_CFG_MASTER FM1_ADC_CFG_KEY
#endif
#ifndef FM1_ADC_MASTER_TICKS
#define FM1_ADC_MASTER_TICKS 1u                /* MASTER's conversion: taken within this many ticks, or lost */
#endif
#define FM1_ADC_BATT_TICKS 8u                  /* the battery's: done within this many ticks (0.8 ms), or dropped */
#define FM1_ADC_BATT_EVERY 256u                /* frames between battery slots (a power of 2) */
enum { FM1_ADC_GOT_NONE, FM1_ADC_GOT_KEY, FM1_ADC_GOT_LOST };
enum { FM1__ADC_W_KEY, FM1__ADC_W_MASTER, FM1__ADC_W_BATT };
static volatile struct {
    uint8_t on;                                /* the scheduler runs: fm1_adc_read(MASTER / BATT) reads the cache */
    uint8_t have_m, have_b;                    /* a slot reading exists */
    uint8_t busy, what, waits;                 /* a conversion runs (FM1__ADC_W_*), the ticks it has had */
    uint8_t mseq;                              /* MASTER readings taken (mod 256): fm1_adc_read's "new" test */
    uint8_t mbusy, clr;                        /* main loop: a blocking read runs (before on); clear the counters */
    uint16_t frame;                            /* column-0 slots seen (scan frames) */
    uint16_t master, batt;                     /* the slots' last readings, 0..1023 */
    uint16_t convs, timeouts, batt_to;         /* developer (CC 42 items 4..6): conversions taken (keys, MASTER and
                                                * the battery); key / MASTER conversions not done in time;
                                                * battery ones dropped */
} fm1_adc_sched FM1_ADC_SCHED_SECTION;

/* Both are only ever analog inputs: PB1 held low for 8 s would reset the
 * chip (isd_config). */
static void fm1_adc_init(void)
{
    uint32_t m = (1u << 6) | (1u << 1);
    FM1_PR(FM1_PB, FM1_DIE) &= ~m;
    FM1_PR(FM1_PB, FM1_PU) &= ~m;
    FM1_PR(FM1_PB, FM1_PD) &= ~m;
    FM1_PR(FM1_PB, FM1_DIR) |= m;
}

/* the converter itself (internal): the same register sequence as the blocking read below, with cfg in place of
 * 0xF04E's settings (FM1_ADC_CFG_SDK gives exactly 0.9.4's) */
static inline __attribute__((always_inline)) void fm1__adc_start(uint32_t ch, uint32_t cfg)
{
    FM1_ADC_CON = 0;
    if (FM1_WLA_CON0 & (1u << 14))
        FM1_WLA_CON0 &= ~(1u << 14);
    FM1_ADC_CON = (cfg & 0xF00Fu) | 0x40u | ((ch & 0xFu) << 8);
    FM1_ADC_CON |= 0x10u;
    FM1_ADC_CON |= 0x40u;
}
static inline __attribute__((always_inline)) uint32_t fm1__adc_done(void) { return FM1_ADC_CON & 0x80u; }
static inline __attribute__((always_inline)) uint32_t fm1__adc_take(void)
{
    uint32_t v = FM1_ADC_RES & 0x3FFu;
    FM1_ADC_CON = 0x40u;
    FM1_ADC_CON = 0;
    return v;
}
static inline __attribute__((always_inline)) void fm1__adc_abort(void)
{
    FM1_ADC_CON = 0x40u;
    FM1_ADC_CON = 0;
}

/* ---- the tick side (hal/fm1_keyadc.h; ISR only) */
/* a conversion runs (started in an earlier tick): only then has fm1_adc_sched_take anything to do (the tick's idle
 * test, one load) */
static inline __attribute__((always_inline)) uint32_t fm1_adc_sched_busy(void) { return fm1_adc_sched.busy; }
static inline __attribute__((always_inline)) uint32_t fm1_adc_sched_take(uint32_t *v)
{
    uint32_t w, x;
    if (!fm1_adc_sched.busy)
        return FM1_ADC_GOT_NONE;
    w = fm1_adc_sched.what;
    if (!fm1__adc_done()) {
        x = (uint32_t)fm1_adc_sched.waits + 1u;
        if (x < (w == FM1__ADC_W_BATT ? FM1_ADC_BATT_TICKS : w == FM1__ADC_W_MASTER ? FM1_ADC_MASTER_TICKS : 1u)) {
            fm1_adc_sched.waits = (uint8_t)x;
            return FM1_ADC_GOT_NONE;           /* (held: _slot keeps the converter for it) */
        }
        fm1__adc_abort();
        fm1_adc_sched.busy = 0;
        if (w == FM1__ADC_W_BATT)
            fm1_adc_sched.batt_to++;
        else
            fm1_adc_sched.timeouts++;
        return w == FM1__ADC_W_KEY ? FM1_ADC_GOT_LOST : FM1_ADC_GOT_NONE;
    }
    x = fm1__adc_take();
    fm1_adc_sched.busy = 0;
    fm1_adc_sched.convs++;
    if (w == FM1__ADC_W_KEY) {
        *v = x;
        return FM1_ADC_GOT_KEY;
    }
    if (w == FM1__ADC_W_MASTER) {
        fm1_adc_sched.master = (uint16_t)x;
        fm1_adc_sched.have_m = 1;
        fm1_adc_sched.mseq++;                  /* (after the reading: the main loop reads mseq, then master) */
    } else {
        fm1_adc_sched.batt = (uint16_t)x;
        fm1_adc_sched.have_b = 1;
    }
    return FM1_ADC_GOT_NONE;
}
static inline __attribute__((always_inline)) void fm1__adc_slot_start(uint32_t what, uint32_t ch, uint32_t cfg)
{
    fm1__adc_start(ch, cfg);
    fm1_adc_sched.what = (uint8_t)what;
    fm1_adc_sched.waits = 0;
    fm1_adc_sched.busy = 1;
}
/* columns 0 and 1 (the slots' columns), out of line: the other nine ticks stay a load and two branches */
static __attribute__((noinline)) uint32_t fm1__adc_slot01(uint32_t n)
{
    uint32_t f = fm1_adc_sched.frame;
    if (n == 0u) {
        fm1_adc_sched.frame = (uint16_t)++f;
        if (fm1_adc_sched.clr) {               /* the main loop's request (a developer mode change) */
            fm1_adc_sched.convs = fm1_adc_sched.timeouts = fm1_adc_sched.batt_to = 0;
            fm1_adc_sched.clr = 0;
        }
    }
    fm1_adc_sched.on = 1;                      /* from now on MASTER and the battery come from the slots */
    if (fm1_adc_sched.busy || fm1_adc_sched.mbusy)
        return 1u;                             /* the battery still converting / a blocking read (boot) */
    if (f & 1u)
        return 0u;                             /* odd frames: neither slot */
    if (n == 0u)
        fm1__adc_slot_start(FM1__ADC_W_MASTER, FM1_ADC_MASTER, FM1_ADC_CFG_MASTER);
    else if (!(f & (FM1_ADC_BATT_EVERY - 1u)))
        fm1__adc_slot_start(FM1__ADC_W_BATT, FM1_ADC_BATT, FM1_ADC_CFG_SDK);   /* right after MASTER's */
    else
        return 0u;
    return 1u;
}
static inline __attribute__((always_inline)) uint32_t fm1_adc_sched_slot(uint32_t n)
{
    if (n < 2u)
        return fm1__adc_slot01(n);
    return (uint32_t)(fm1_adc_sched.busy | fm1_adc_sched.mbusy) != 0u;
}
static inline __attribute__((always_inline)) void fm1_adc_key_start(uint32_t row)
{
    if (fm1_adc_sched.busy | fm1_adc_sched.mbusy)
        return;                                /* refused: a conversion runs (call only after a 0 from _slot) */
    fm1__adc_start(row ? FM1_ADC_ROW_PA8 : FM1_ADC_ROW_PA7, FM1_ADC_CFG_KEY);
    fm1_adc_sched.what = FM1__ADC_W_KEY;
    fm1_adc_sched.waits = 0;
    fm1_adc_sched.busy = 1;
}

/* ---- the main loop side */
/* the developer counters (convs, timeouts, batt_to) to 0 at the next frame (the tick clears them) */
static void fm1_adc_sched_clear(void) { fm1_adc_sched.clr = 1; }

/* 0.9.4's blocking conversion at the SDK's settings */
static int32_t fm1__adc_block(uint32_t ch)
{
    uint32_t t, v;
    FM1_ADC_CON = 0;
    if (FM1_WLA_CON0 & (1u << 14))
        FM1_WLA_CON0 &= ~(1u << 14);
    FM1_ADC_CON = 0xF04Eu | ((ch & 0xFu) << 8);
    FM1_ADC_CON |= 0x10u;
    FM1_ADC_CON |= 0x40u;
    for (t = 0; t < 20000u; t++)
        if (FM1_ADC_CON & 0x80u)
            break;
    v = FM1_ADC_RES & 0x3FFu;
    FM1_ADC_CON = 0x40u;
    FM1_ADC_CON = 0;
    return t == 20000u ? -1 : (int32_t)v;
}

static int32_t fm1_adc_read(uint32_t ch)
{
    static uint8_t m_seen;                     /* the MASTER reading (mseq) last returned */
    uint32_t s;
    if (!fm1_adc_sched.on) {                   /* the scheduler not running (yet): convert, unless the tick has */
        int32_t r = -1;                        /* started (busy) or starts meanwhile (on) */
        int conv = 0;
        fm1_adc_sched.mbusy = 1;               /* first: from here on the tick starts nothing */
        if (!fm1_adc_sched.on && !fm1_adc_sched.busy) {
            r = fm1__adc_block(ch);
            conv = 1;
        }
        fm1_adc_sched.mbusy = 0;
        if (conv)
            return r;
    }
    if (ch == FM1_ADC_MASTER) {
        if (!fm1_adc_sched.have_m)
            return -1;
        s = fm1_adc_sched.mseq;
        if (s == m_seen)
            return -1;                         /* nothing new since the last call */
        m_seen = (uint8_t)s;
        return (int32_t)fm1_adc_sched.master;
    }
    if (ch == FM1_ADC_BATT)
        return fm1_adc_sched.have_b ? (int32_t)fm1_adc_sched.batt : -1;
    return -1;                                 /* (no other channel while the tick owns the converter) */
}
