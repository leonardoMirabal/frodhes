/* SPDX-License-Identifier: GPL-3.0-only */
/* FM-1 key velocity, the key rows' side of the scan tick (Frodhes): hal/fm1_input.h's two hooks, the row drive, the
 * LED blanking, the self-test and the developer modes. The contract is src/keyvel.h (the order of a tick, who owns
 * what); the converter and its MASTER / battery slots are hal/fm1_adc.h's (fm1_adc_sched_*).
 * After charlesvestal/fm1-velocity (https://github.com/charlesvestal/fm1-velocity, commit 8f62ce0, GPL-3.0-only,
 * by Charles Vestal): the drive (the pin's own driver instead of the 10k pull-up for the sample) and the self-test
 * (a row driven low reads 0) are his; this scheduler (variant B: only keys whose contact just closed) is Frodhes'.
 *
 * The two analog rows: PA7 = converter channel 0 (row 0), PA8 = channel 1 (row 1); keys 0..19 (F3..C5) and, on PA8
 * columns 0 / 1, OCT- / OCT+. PA6's keys (the top 7) have no converter and are never driven here.
 *
 *   fm1_ka_take(p)            FM1_INPUT_ADC_TAKE, first in the tick, before column p's rows are read; nothing unless
 *                             fm1_adc_sched_busy() (one load and a branch), the rest out of line. RESTORE BEFORE
 *                             READ: the row driven last tick goes back to an input with its pull-up (direction first,
 *                             so a row driven high never glitches low; after the self-test's low drive the pull-up
 *                             gets FM1_KA_SETTLE_US to lift it), else its held keys read open and never debounce, or a
 *                             row left low reads every key on it pressed, OCT- / OCT+ too. Then
 *                             fm1_adc_sched_take(): a key's reading goes to kv_isr_sample(), the self-test's to its
 *                             counters, STRESS's filler conversions nowhere.
 *   fm1_ka_start(p, n, lit)   FM1_INPUT_ADC_START, column n just latched (the LED lines dark). Idle (kv_isr_busy(p,
 *                             n, raw) 0, NORMAL: three byte loads, the mode's, a branch) it only calls
 *                             fm1_adc_sched_slot on columns 0 / 1; else, out of line: kv_isr_want / kv_isr_col
 *                             for column p, then fm1_adc_sched_slot(n) (MASTER's / the battery's tick: lit, no key);
 *                             else, only if kv_isr_pend(n) (one load), kv_isr_pick(n, frame & 1): one row, O(1), no
 *                             loop over the keys. A conversion that would swallow a sparse LED frame (column n lit
 *                             this frame and dark the next: BRIGHTNESS 1 lights a key 1 frame in 16, a dark tick there
 *                             is a 32-frame blink) asks kv_isr_defer(): one frame later, once per landing. Else the row
 *                             is driven high at DRIVE (the weakest driver by default; PULL: left on its pull-up),
 *                             fm1_adc_key_start(row), and 0 is returned: column n's LED lines stay dark for this one
 *                             tick only (the LED current flows through the column the pill is read against; the next
 *                             tick lights them again, its dim pulses included). LEDS OFF: lit is 0 anyway; ALL (every
 *                             frame lit): one dark frame in a landing, never deferred.
 *   fm1_ka_dev_item / _mode   the developer protocol (src/keyvel.h KV_IT_* / KV_DEV_*), main loop.
 * Developer modes (CC 41, not saved): OFF no key conversions (the slots only); STRESS a conversion with the drive and
 * the dark LEDs on every free tick (a key's when one wants it, else the frame's row, its reading dropped: variant A's
 * ceiling, hil_cpu.py --keyadc stress); STORM 8 fake landings every 32 frames (kv_isr_storm); SELFTEST every free tick
 * the frame's row of the latched column driven low for its conversion: each must read 0 (items 9 / 10, count 11).
 * FM1_KA_PROF (APP_DEFS=-DFM1_KA_PROF): the tick's time from the take to the end of the start, its maximum (item 16).
 * Rules: never switch the rows' digital input buffers off (DIE untouched); one conversion a tick; no loop over the
 * keys; state in fm1_ka (.bss.fm1_ka); the idle tick's added path is a few loads and branches (take: 3 instructions,
 * start: 14 on the FM-1 build), the rest is out of line (if the tick's cost ever moves the Rhodes loops, the hooks go
 * to .ram_text: no calls there). The main loop writes fm1_ka's and fm1_adc_sched's fields with single stores only (the tick, which preempts it, never sees half a
 * write). */
#pragma once
#include <stdint.h>
#include "fm1_adc.h"
#include "fm1_input.h"

#define FM1_KA_PA7 (1u << 7)                    /* row 0 (converter channel 0) */
#define FM1_KA_PA8 (1u << 8)                    /* row 1 (channel 1) */
#define FM1_KA_PIN(row) ((row) ? FM1_KA_PA8 : FM1_KA_PA7)
#ifndef FM1_KA_SETTLE_US
#define FM1_KA_SETTLE_US 5u                     /* a row released from 0 V: the 10k pull-up lifts it before the read */
#endif
enum { FM1_KA_TO_NONE, FM1_KA_TO_KEY, FM1_KA_TO_SELFTEST, FM1_KA_TO_DROP };   /* where a key conversion's reading goes */

static volatile struct {
    uint16_t drv;                               /* the pin driven for the running conversion (0 none): restored at the
                                                 * next take */
    uint8_t low;                                /* it is driven low (the self-test): settle after the release */
    uint8_t to, col, row;                       /* the running key conversion: its reading's destination, column, row */
    uint8_t mode;                               /* KV_DEV_NORMAL .. KV_DEV_SELFTEST */
    uint8_t strength, pull;                     /* DRIVE - 1 (0 = DRIVE 1, the weakest: HD0 / HD clear); pull: no drive */
    uint16_t st_max[2], st_n;                   /* the self-test: the highest reading per row, the readings taken */
    uint32_t t0, tmax;                          /* FM1_KA_PROF: the tick's start, its longest (TIMER4 ticks) */
} fm1_ka KV_BSS(fm1_ka);

/* the driven row back to an input with its pull-up: direction first (no low glitch), then the output latch and the
 * driver strength back to their defaults (from fm1__ka_take, out of line: the idle tick never gets here) */
static void fm1__ka_restore(void)
{
    uint32_t m = fm1_ka.drv;
    FM1_PR(FM1_PA, FM1_DIR) |= m;
    FM1_PR(FM1_PA, FM1_OUT) &= ~m;
    FM1_PR(FM1_PA, FM1_HD0) &= ~m;
    FM1_PR(FM1_PA, FM1_HD) &= ~m;
    fm1_ka.drv = 0;
    if (fm1_ka.low) {
        fm1_ka.low = 0;
        fm1_delay_us(FM1_KA_SETTLE_US);
    }
}

/* the reading of the key conversion started last tick (got: FM1_ADC_GOT_KEY or _LOST) */
static __attribute__((noinline)) void fm1__ka_got(uint32_t got, uint32_t v)
{
    uint32_t to = fm1_ka.to, row = fm1_ka.row & 1u;
    fm1_ka.to = FM1_KA_TO_NONE;
    if (got != FM1_ADC_GOT_KEY)
        return;                                 /* (lost: fm1_adc.h counted it; the key is still pending) */
    if (to == FM1_KA_TO_KEY) {
        kv_isr_sample(fm1_ka.col, row, v);
    } else if (to == FM1_KA_TO_SELFTEST) {
        if (v > fm1_ka.st_max[row])
            fm1_ka.st_max[row] = (uint16_t)v;
        fm1_ka.st_n++;
    }
}

/* a conversion runs: restore its row (if driven), then take it. Out of line: the idle tick is one load and a branch.
 * A driven row always has a conversion running (fm1__ka_go drives only where fm1_adc_key_start then starts one, and
 * restores at once if it ever refused), so the restore never waits for a tick without one */
static __attribute__((noinline)) void fm1__ka_take(void)
{
    uint32_t v = 0, got;
    if (fm1_ka.drv)                             /* restore before read (see top) */
        fm1__ka_restore();
    got = fm1_adc_sched_take(&v);
    if (got != FM1_ADC_GOT_NONE)
        fm1__ka_got(got, v);
}
static inline __attribute__((always_inline)) void fm1_ka_take(uint32_t p)
{
    (void)p;
#ifdef FM1_KA_PROF
    fm1_ka.t0 = fm1_ticks();
#endif
    if (fm1_adc_sched_busy())
        fm1__ka_take();
}
/* FM1_INPUT_ADC_BUSY (Frodhes): a conversion runs (started this tick, taken next): the tick leaves the 595's pins still */
static inline __attribute__((always_inline)) uint32_t fm1_ka_busy(void) { return fm1_adc_sched_busy(); }

/* column n's row r: drive it (to: TO_SELFTEST low, else high at DRIVE unless PULL), convert it until the next tick,
 * and the column's LED lines dark meanwhile (returns 0) */
static __attribute__((noinline)) uint32_t fm1__ka_go(uint32_t n, uint32_t r, uint32_t to)
{
    uint32_t m = FM1_KA_PIN(r);
    if (to == FM1_KA_TO_SELFTEST) {
        FM1_PR(FM1_PA, FM1_OUT) &= ~m;          /* the latch low first, then an output: 0 V */
        FM1_PR(FM1_PA, FM1_DIR) &= ~m;
        fm1_ka.low = 1;
        fm1_ka.drv = (uint16_t)m;
    } else if (!fm1_ka.pull) {
        uint32_t s = fm1_ka.strength;
        if (s & 1u)
            FM1_PR(FM1_PA, FM1_HD0) |= m;
        else
            FM1_PR(FM1_PA, FM1_HD0) &= ~m;
        if (s & 2u)
            FM1_PR(FM1_PA, FM1_HD) |= m;
        else
            FM1_PR(FM1_PA, FM1_HD) &= ~m;
        FM1_PR(FM1_PA, FM1_OUT) |= m;           /* the latch high first, then an output: no low glitch */
        FM1_PR(FM1_PA, FM1_DIR) &= ~m;
        fm1_ka.drv = (uint16_t)m;
    }
    fm1_ka.to = (uint8_t)to;
    fm1_ka.col = (uint8_t)n;
    fm1_ka.row = (uint8_t)r;
    fm1_adc_key_start(r);
    if (!fm1_adc_sched_busy() && fm1_ka.drv)    /* refused (never: the callers' _slot said free): no row left driven */
        fm1__ka_restore();
    return 0;
}

/* lit now, dark next frame: a dark tick here is a gap of two frames or more (BRIGHTNESS 1: of 32) */
static inline __attribute__((always_inline)) uint32_t fm1__ka_sparse(uint32_t n, uint32_t lit)
{
    return lit & ~(uint32_t)fm1_led_tab[n][(fm1__led_phase + 1u) & 15u];
}

/* NORMAL: a key of column n wants a sample (kv_isr_pend), the converter is free */
static __attribute__((noinline)) uint32_t fm1__ka_key(uint32_t n, uint32_t lit)
{
    int32_t r = kv_isr_pick(n, fm1_in.frames & 1u);
    if (r < 0)
        return lit;
    if (fm1__ka_sparse(n, lit) && kv_isr_defer(n, (uint32_t)r))
        return lit;
    return fm1__ka_go(n, (uint32_t)r, FM1_KA_TO_KEY);
}

/* the developer modes (fm1_ka.mode != NORMAL), the slot included */
static __attribute__((noinline)) uint32_t fm1__ka_dev(uint32_t n, uint32_t lit)
{
    uint32_t mode = fm1_ka.mode, odd = fm1_in.frames & 1u;
    int32_t r;
    if (n == 0u && mode == KV_DEV_STORM && !(fm1_in.frames & 31u))
        kv_isr_storm();
    if (fm1_adc_sched_slot(n) || mode == KV_DEV_OFF)
        return lit;
    if (mode == KV_DEV_SELFTEST)                /* every free tick, the keys' own samples too: they fall back */
        return fm1__ka_go(n, odd, FM1_KA_TO_SELFTEST);
    r = kv_isr_pend(n) ? kv_isr_pick(n, odd) : -1;
    if (r >= 0) {
        if (fm1__ka_sparse(n, lit) && kv_isr_defer(n, (uint32_t)r))
            return lit;
        return fm1__ka_go(n, (uint32_t)r, FM1_KA_TO_KEY);
    }
    if (mode == KV_DEV_STRESS)
        return fm1__ka_go(n, odd, FM1_KA_TO_DROP);
    return lit;
}

/* column p read, column n latched, a key of either busy or a developer mode on: the whole start, out of line */
static __attribute__((noinline)) uint32_t fm1__ka_start(uint32_t p, uint32_t n, uint32_t lit, uint32_t raw)
{
    if (kv_isr_want(p, raw))
        kv_isr_col(p, raw, fm1_in.notes);
    if (fm1_ka.mode)
        return fm1__ka_dev(n, lit);
    if (!fm1_adc_sched_slot(n) && kv_isr_pend(n))
        return fm1__ka_key(n, lit);
    return lit;
}
/* idle (no analog row closed with TOUCH on, no key of column p or n busy, NORMAL): only the slots' columns 0 / 1 call
 * out (fm1_adc_sched_slot has no effect on the others) */
static inline __attribute__((always_inline)) uint32_t fm1_ka_start(uint32_t p, uint32_t n, uint32_t lit)
{
    uint32_t raw = fm1_in.raw[p];
    if (kv_isr_busy(p, n, raw) | fm1_ka.mode)
        lit = fm1__ka_start(p, n, lit, raw);
    else if (n < 2u)
        (void)fm1_adc_sched_slot(n);
#ifdef FM1_KA_PROF
    {
        uint32_t d = fm1_ticks() - fm1_ka.t0;
        if (d > fm1_ka.tmax)
            fm1_ka.tmax = d;
    }
#endif
    return lit;
}

/* ---- developer (src/keyvel.c kv_dev_cc: CC 42 items, CC 41 commands), main loop */
static uint32_t fm1_ka_dev_item(uint32_t item)
{
    switch (item) {
    case KV_IT_MODE: return fm1_ka.mode;
    case KV_IT_DRIVE: return fm1_ka.pull ? 0u : fm1_ka.strength + 1u;
    case KV_IT_CONVS: return fm1_adc_sched.convs;
    case KV_IT_TIMEOUTS: return fm1_adc_sched.timeouts;
    case KV_IT_BATT_TO: return fm1_adc_sched.batt_to;
    case KV_IT_MASTER: return fm1_adc_sched.master;
    case KV_IT_BATT: return fm1_adc_sched.batt;
    case KV_IT_ST_PA7: return fm1_ka.st_max[0];
    case KV_IT_ST_PA8: return fm1_ka.st_max[1];
    case KV_IT_ST_N: return fm1_ka.st_n;
    case KV_IT_TICK_MAX: {                      /* 0.1 us; restarts on the read (0 without FM1_KA_PROF) */
        uint32_t t = fm1_ka.tmax * 10u / FM1_TICKS_PER_US;
        fm1_ka.tmax = 0;
        return t;
    }
    case KV_IT_FRAMES: return fm1_in.frames & 0x3FFFu;   /* scan frames modulo 16384 (the reply's 14 bits) */
    default: return 0;
    }
}
/* CC 41 v: 0..4 the mode (the counters cleared), 16..19 DRIVE 1..4, 20 PULL; the rest is keyvel.c's */
static void fm1_ka_dev_mode(uint32_t v)
{
    if (v < KV_DEV_NMODE) {
        fm1_ka.mode = (uint8_t)v;
        fm1_ka.st_max[0] = 0;
        fm1_ka.st_max[1] = 0;
        fm1_ka.st_n = 0;
        fm1_ka.tmax = 0;
        fm1_adc_sched.convs = 0;
        fm1_adc_sched.timeouts = 0;
        fm1_adc_sched.batt_to = 0;
    } else if (v >= KV_DEV_DRIVE1 && v < KV_DEV_DRIVE1 + 4u) {
        fm1_ka.strength = (uint8_t)(v - KV_DEV_DRIVE1);
        fm1_ka.pull = 0;
    } else if (v == KV_DEV_PULL) {
        fm1_ka.pull = 1;
    }
}
