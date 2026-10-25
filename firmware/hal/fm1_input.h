/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* FM-1 input HAL: key/button/encoder matrix and LEDs.
 *
 * One 11-column x 6-row diode matrix behind a 2x74HC595 chain (PA4 SER, PA3
 * SRCLK, PA1 RCLK), bit-banged and polled; rows PA0, PA5..PA8, PB7 with
 * pull-ups (low = closed). LED lines PH6/PH9/PA9/PA10 light the LED on the
 * same column, row PA7/PA8/PA5/PA6 respectively.
 *
 *   fm1_input_init();
 *   polled:  for (;;) { fm1_input_scan(); ... }
 *   IRQ:     call fm1_input_tick() from a ~10 kHz timer ISR; it advances one
 *            column per call (rows are sampled one tick after the column was
 *            latched, LEDs stay lit in between) and processes a frame every
 *            FM1_NCOL ticks. The main loop reads fm1_in.notes / buttons and
 *            takes edges/steps with fm1_input_edges() / fm1_enc_take().
 *
 * fm1_input_scan() runs one full frame (11 columns, ~0.6 ms) and calls
 * FM1_INPUT_IDLE() while it waits.
 * Keys/buttons (as Felucca 1.0): debounced as soon as their column is read. A press counts after
 * FM1_DEB_PRESS frames closed in a row (1.1-2.2 ms: the matrix has diodes and no ghosting, so a closed
 * sample is a closed key; two in a row keep one stray sample from playing a note), a release after
 * FM1_DEB_RELEASE frames open in a row (~9 ms): a contact bouncing open on the way down, or chattering
 * on the way up, never ends a note early or plays it twice.
 * Encoders: quadrature decoder (2-sample filter, + = clockwise) with detent counting, as
 * Felucca 1.0 reworked it (its #23, "knobs skipping or jumping"): an FM-1 detent is one full
 * quadrature cycle (4 transitions) and the knob rests in one state, the one seen at power-on
 * (relearned only after FM1_REST_FRAMES, ~1 s, parked elsewhere). Steps are emitted on arriving
 * back at it, the net transitions rounded to whole cycles (>= 2 counts one: a lost transition or
 * two is forgiven; a two-state jump counts on in the direction of travel). One click = one step
 * at any speed; bounce and back-and-forth cancel out. Never a second rest state: a knob held
 * mid-click taught the complement as a rest and every click counted twice, and short mid-click
 * pauses of a slow turn taught the mid states and the knob went dead (the 0.9 learner).
 * fm1_enc_take() returns the steps.
 * LEDs: set fm1_led[col] (packed row bits, bit1 PA5..bit4 PA8); they are lit while that column is
 * selected (one tick, ~95 us a frame). Two dim layers (fm1_input_tick only), after Felucca 1.0.1 (its
 * #35: the eye is logarithmic, an LED lit 1/4 or 1/6 of the time reads as nearly lit): a short pulse on
 * every frame (~910 Hz, no flicker) at the start of the column's next tick, riding on the 595 shift of
 * the next column (its outputs change only at the latch, so the pulse stays on column p and the key
 * read before it is unchanged). fm1_led_dim[col]: the glow (landmarks, notes under tiles), FM1_GLOW_NS
 * (~1/24 of a lit LED); fm1_led_bg[col]: the backlight (menu LIGHTS), fm1_led_bg_ns, shorter. TIMER4
 * times the pulses bit by bit; only a pulse longer than the whole shift waits for the rest. An LED in
 * several layers takes the brightest. fm1_led_key/btn helpers address them by id.
 * Brightness (Frodhes DISPLAY): fm1_led_duty[2] thins the lit layer per group of ids (fm1_led_group_ids) to a
 * duty of 1..16 of 16 scan frames; fm1_led_build (main loop, after the LEDs are set) works the 16 frames out into
 * fm1_led_tab and the tick only reads it.
 */
#pragma once
#include <stdint.h>
#include "fm1_time.h"
#include "fm1_gpio.h"
#include "fm1_cc.h"

#ifndef FM1_INPUT_IDLE
#define FM1_INPUT_IDLE() ((void)0)
#endif
/* Two hooks in fm1_input_tick for a scan that also converts the analog rows (Frodhes: hal/fm1_keyadc.h, key
 * velocity, src/keyvel.h). FM1_INPUT_ADC_TAKE(p): first in the tick, before column p's rows are read: take the
 * conversion started last tick and restore its row (a driven row read as it is shows its keys open, or pressed).
 * FM1_INPUT_ADC_START(p, n, lit): column n latched, column p read: may start a conversion on column n; returns the
 * LED lines to light for column n (lit, or 0: dark while it samples). Not defined: the tick as before. */
#ifndef FM1_INPUT_ADC_TAKE
#define FM1_INPUT_ADC_TAKE(p) ((void)0)
#endif
#ifndef FM1_INPUT_ADC_START
#define FM1_INPUT_ADC_START(p, n, lit) (lit)
#endif
/* Frodhes: FM1_INPUT_ADC_BUSY(): a conversion runs now (it started in FM1_INPUT_ADC_START and is taken next tick); the
 * tick then leaves the shift pins alone until the next tick. Not defined: never. */
#ifndef FM1_INPUT_ADC_BUSY
#define FM1_INPUT_ADC_BUSY() 0u
#endif
#ifndef FM1_LED_US
#define FM1_LED_US 40u           /* LED on-time per column (brightness vs scan rate) */
#endif
#define FM1_DEB_PRESS 2u          /* frames closed in a row: a press (a frame = 11 ticks, ~1.1 ms) */
#define FM1_DEB_RELEASE 8u        /* frames open in a row: a release (~9 ms) */
#ifndef FM1_SETTLE_US
#define FM1_SETTLE_US 10u
#endif
#define FM1_REST_FRAMES 900u      /* ~1 s still off the detent state: that is the detent (power-on) */
#define FM1_NCOL 11u
#define FM1_NKEY 41u              /* ids: 0..13 buttons, 14..40 note keys */
#define FM1_NENC 7u
#define FM1_NBTN 14u              /* buttons: ids 0..13 */


/* key id at (physical column, packed row bit), -1 = none */
static const int8_t FM1_KEYMAP[6][FM1_NCOL] = {
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},          /* PA0: encoders */
    { 5, 11,  4, 10,  3,  9,  2,  8, -1, -1, -1},          /* PA5 */
    {34, 35, 36, 37, 38, 40, 39, 13,  7,  6, 12},          /* PA6 */
    {23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33},          /* PA7 */
    { 0,  1, 15, 14, 17, 16, 19, 18, 20, 21, 22},          /* PA8 */
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},          /* PB7: encoder 6 */
};
/* encoder i: A at (col, row bit), B at (col, row bit) */
static const uint8_t FM1_ENC[FM1_NENC][4] = {
    {0, 0, 1, 0}, {2, 0, 3, 0}, {8, 1, 9, 1}, {8, 0, 9, 0}, {6, 0, 7, 0}, {4, 0, 5, 0}, {0, 5, 1, 5},
};
enum { FM1_BTN_OCT_DOWN = 0, FM1_BTN_OCT_UP = 1 };
#define FM1_OCT_BOTH (1u << FM1_BTN_OCT_DOWN | 1u << FM1_BTN_OCT_UP)

static const int8_t FM1_LED_PORT[6] = {-1, FM1_PA, FM1_PA, FM1_PH, FM1_PH, -1};
static const uint8_t FM1_LED_BIT[6] = {0, 9, 10, 6, 9, 0};

static volatile struct {
    uint32_t notes;              /* debounced: bit n = note key n (0 = F3 .. 26 = G5) */
    uint32_t buttons;            /* debounced: bit i = button i (0..13) */
    uint32_t pressed, released;  /* button edges since the last fm1_input_edges() */
    uint32_t notes_pressed;      /* note-key press edges since the last fm1_input_note_edges() */
    uint8_t raw[FM1_NCOL];       /* last frame, packed rows, 1 = closed */
    uint8_t cnt[FM1_NKEY];
    uint8_t enc_prev[FM1_NENC], enc_last[FM1_NENC];
    uint8_t enc_rest[FM1_NENC];  /* the detent state (0..3) */
    uint16_t enc_still[FM1_NENC]; /* frames since the last state change */
    int8_t enc_sub[FM1_NENC];    /* net transitions since the last rest state */
    int16_t enc_steps[FM1_NENC]; /* + = clockwise */
    uint32_t frames;
} fm1_in;
/* Frodhes: per column, the key rows (bits 1..4) at its last debounce pass and a count left running there (fm1__keys'
 * shortcut; a test that clears fm1_in clears it too). Own section, out of the global merge (as fm1_led_tab) */
static volatile struct { uint8_t raw[FM1_NCOL], busy[FM1_NCOL]; } fm1__kpass
    __attribute__((section(".bss.fm1_kpass")));
static uint8_t fm1_led[FM1_NCOL];
#ifndef FM1_GLOW_NS
#define FM1_GLOW_NS 4000u        /* the glow pulse a frame (ns); a lit LED ~95 us: ~1/24 the brightness */
#endif
#define FM1__NS_T(ns) (((uint32_t)(ns) * FM1_TICKS_PER_US + 500u) / 1000u)   /* ns -> TIMER4 ticks */
static uint8_t fm1_led_dim[FM1_NCOL];   /* same layout as fm1_led: the glow */
static uint8_t fm1_led_bg[FM1_NCOL];    /* same layout: the backlight (labels readable in the dark) */
static volatile uint16_t fm1_led_bg_ns; /* the backlight pulse a frame (ns), 0 = off (menu LIGHTS) */
static uint8_t fm1_led_duty[2] __attribute__((section(".data.fm1_led_duty"))) = {16u, 16u};   /* Frodhes DISPLAY BRIGHTNESS: the lit layer's duty per group: lit on
                                         * that many of 16 scan frames (spread evenly: no flicker from 2 up; 1 pulses
                                         * at 57 Hz, which the eye fuses at that dimness), 0 dark; group 0 the LEDs of
                                         * fm1_led_group_ids, 1 the rest */
static uint8_t fm1_led_grpmask[FM1_NCOL] __attribute__((section(".bss.fm1_led_grpmask")));   /* row bits per column of group 0 */

/* Frodhes: the four lines as two port words (FM1_LED_PORT / FM1_LED_BIT: rows 1, 2 on PA9, PA10, rows 3, 4 on PH6,
 * PH9), one read and one write per port instead of a read-modify-write per line; the same levels */
static void fm1__led_lines(uint32_t rowmask)
{
    uint32_t a = FM1_PR(FM1_PA, FM1_OUT) & ~(3u << 9);
    FM1_PR(FM1_PA, FM1_OUT) = a | (rowmask & 6u) << 8;
    a = FM1_PR(FM1_PH, FM1_OUT) & ~(1u << 6 | 1u << 9);
    FM1_PR(FM1_PH, FM1_OUT) = a | (rowmask & 8u) << 3 | (rowmask & 16u) << 5;
}

FM1_INLINE void fm1__sr_bit(uint32_t w, uint32_t i)   /* bit i of w (msb first) into the 595 */
{
    if (w & (0x8000u >> i))
        FM1_PR(FM1_PA, FM1_OUT) |= 1u << 4;
    else
        FM1_PR(FM1_PA, FM1_OUT) &= ~(1u << 4);
    FM1_PR(FM1_PA, FM1_OUT) |= 1u << 3;         /* read-modify-write per edge: each SFR write is */
    FM1_PR(FM1_PA, FM1_OUT) &= ~(1u << 3);      /* far slower than the 595 needs */
}
static void fm1__sr_latch(void)                 /* the outputs change here only */
{
    FM1_PR(FM1_PA, FM1_OUT) |= 1u << 1;
    FM1_PR(FM1_PA, FM1_OUT) &= ~(1u << 1);
}
static void fm1__sr_word(uint32_t w)            /* (Frodhes: unrolled) */
{
#define FM1__SR4(i) fm1__sr_bit(w, i); fm1__sr_bit(w, i + 1u); fm1__sr_bit(w, i + 2u); fm1__sr_bit(w, i + 3u)
    FM1__SR4(0u); FM1__SR4(4u); FM1__SR4(8u); FM1__SR4(12u);
#undef FM1__SR4
    fm1__sr_latch();
}
/* Frodhes: the chain is one bit (w(m)'s bit 0) away from column m's word: m + 1 (0: not known, the tick shifts the
 * whole word). Column m's word w(m) = 0xFFFF ^ 1 << m ^ (m < 2 ? 1 << (11 + m) : 0) is column m - 1's shifted by one
 * bit for every m but 2 (w(1) << 1 also clears bit 13): for column 2, fm1_input_tick shifts in w(2)'s first 15 bits
 * at the end of the tick that latched column 1, once its LED lines are lit (the outputs change only at a latch). So
 * every tick's dark part (rows read, one bit, the latch) is the same length: the columns stay equally bright */
static uint8_t fm1__sr_at __attribute__((section(".bss.fm1_sr_at")));

static uint32_t fm1__rows(void)
{
    uint32_t a = FM1_PR(FM1_PA, FM1_IN), b = FM1_PR(FM1_PB, FM1_IN);
    return (~((a & 1u) | ((a >> 4) & 0x1Eu) | ((b >> 2) & 0x20u))) & 0x3Fu;
}

static void fm1__wait(uint32_t us)
{
    uint32_t t0 = fm1_ticks(), span = us * FM1_TICKS_PER_US;
    while ((uint32_t)(fm1_ticks() - t0) < span)
        FM1_INPUT_IDLE();
}

static void fm1_input_init(void)
{
    static const uint8_t LEDP[4][2] = {{FM1_PH, 6}, {FM1_PH, 9}, {FM1_PA, 9}, {FM1_PA, 10}};
    const uint32_t rows_a = (1u << 0) | (1u << 5) | (1u << 6) | (1u << 7) | (1u << 8);
    const uint32_t sr = (1u << 1) | (1u << 3) | (1u << 4), row_b = 1u << 7;
    uint32_t i;
    for (i = 0; i < 4u; i++) {
        uint32_t p = LEDP[i][0], m = 1u << LEDP[i][1];
        FM1_PR(p, FM1_DIE) |= m;
        FM1_PR(p, FM1_OUT) &= ~m;
        FM1_PR(p, FM1_DIR) &= ~m;
        FM1_PR(p, FM1_HD0) |= m;
        FM1_PR(p, FM1_HD) |= m;
    }
    FM1_PR(FM1_PA, FM1_DIE) |= rows_a;
    FM1_PR(FM1_PA, FM1_DIR) |= rows_a;
    FM1_PR(FM1_PA, FM1_PD) &= ~rows_a;
    FM1_PR(FM1_PA, FM1_PU) |= rows_a;
    FM1_PR(FM1_PB, FM1_DIE) |= row_b;
    FM1_PR(FM1_PB, FM1_DIR) |= row_b;
    FM1_PR(FM1_PB, FM1_PD) &= ~row_b;
    FM1_PR(FM1_PB, FM1_PU) |= row_b;
    FM1_PR(FM1_PA, FM1_DIE) |= sr;
    FM1_PR(FM1_PA, FM1_PU) &= ~sr;
    FM1_PR(FM1_PA, FM1_PD) &= ~sr;
    FM1_PR(FM1_PA, FM1_OUT) &= ~sr;
    FM1_PR(FM1_PA, FM1_DIR) &= ~sr;
    fm1__sr_word(0xFFFFu);
    fm1__sr_at = 0;
    for (i = 0; i < FM1_NENC; i++)
        fm1_in.enc_prev[i] = fm1_in.enc_last[i] = 0xFF;   /* seeded by the first frame */
}

static void fm1__key(uint32_t id, uint32_t closed)
{
    volatile uint8_t *c = &fm1_in.cnt[id];
    uint32_t note = id >= 14u, bit = note ? 1u << (id - 14u) : 1u << id;
    uint32_t on = ((note ? fm1_in.notes : fm1_in.buttons) & bit) != 0u;
    if (closed == on) {                            /* agrees with the state: start over */
        *c = 0;
        return;
    }
    if (++*c < (on ? FM1_DEB_RELEASE : FM1_DEB_PRESS))
        return;
    *c = 0;
    if (note) {
        if (!on)
            fm1_in.notes_pressed |= bit;
        fm1_in.notes ^= bit;
    } else {
        fm1_in.buttons ^= bit;
        if (!on)
            fm1_in.pressed |= bit;
        else
            fm1_in.released |= bit;
    }
}

static __attribute__((noinline)) void fm1__keys_pass(uint32_t p, uint32_t raw)
{
    uint32_t r, busy = 0;
    for (r = 1; r < 5u; r++)
        if (FM1_KEYMAP[r][p] >= 0) {
            fm1__key((uint32_t)FM1_KEYMAP[r][p], (raw >> r) & 1u);
            busy |= fm1_in.cnt[FM1_KEYMAP[r][p]];
        }
    fm1__kpass.raw[p] = (uint8_t)raw;
    fm1__kpass.busy[p] = busy != 0u;
}
/* the keys of column p, just read. Frodhes: with the rows as at the last pass and no count left running there,
 * every key of the column agrees with its state (only a pass changes the state or the counts, and a pass that
 * leaves no count running ends that way): the pass would only clear counts already 0, so it is skipped */
FM1_INLINE void fm1__keys(uint32_t p)
{
    uint32_t raw = fm1_in.raw[p] & 0x1Eu;
    if (raw != fm1__kpass.raw[p] || fm1__kpass.busy[p])
        fm1__keys_pass(p, raw);
}

static void fm1__frame(void);

static void fm1_input_scan(void)
{
    uint32_t p;
    for (p = 0; p < FM1_NCOL; p++) {
        fm1__led_lines(0);
        fm1__sr_word(0xFFFFu ^ (1u << p) ^ (p < 2u ? 1u << (11u + p) : 0u));
        fm1__sr_at = 0;
        fm1__wait(FM1_SETTLE_US);
        fm1_in.raw[p] = (uint8_t)fm1__rows();
        fm1__keys(p);
        fm1__led_lines(fm1_led[p]);
        fm1__wait(FM1_LED_US);
    }
    fm1__led_lines(0);
    fm1__frame();
}

static void fm1__frame(void)
{
    uint32_t e;                                    /* (the keys: fm1__keys, as each column is read) */
    for (e = 0; e < FM1_NENC; e++) {               /* quadrature decoder + detents */
        const uint8_t *m = FM1_ENC[e];
        uint32_t cur = ((fm1_in.raw[m[0]] >> m[1]) & 1u) << 1 | ((fm1_in.raw[m[2]] >> m[3]) & 1u);
        uint32_t idx;
        volatile int8_t *sub = &fm1_in.enc_sub[e];
        if (cur != fm1_in.enc_last[e]) {
            fm1_in.enc_last[e] = (uint8_t)cur;
            fm1_in.enc_still[e] = 0;
            continue;
        }
        if (fm1_in.enc_prev[e] == 0xFF) {          /* first frame: the knob rests here */
            fm1_in.enc_prev[e] = (uint8_t)cur;
            fm1_in.enc_rest[e] = (uint8_t)cur;
        }
        if (fm1_in.enc_still[e] < 0xFFFFu && ++fm1_in.enc_still[e] == FM1_REST_FRAMES &&
            cur != fm1_in.enc_rest[e]) {
            fm1_in.enc_rest[e] = (uint8_t)cur;     /* parked ~1 s off the detent state (held at power-on): */
            *sub = 0;                              /* that is the detent. Never a second state (see top) */
        }
        if (cur == fm1_in.enc_prev[e])
            continue;
        idx = (uint32_t)fm1_in.enc_prev[e] << 2 | cur;
        if ((0x4182u >> idx) & 1u)
            (*sub)++;
        else if ((0x2814u >> idx) & 1u)
            (*sub)--;
        else if (*sub > 0)                         /* two states in one sample: a fast turn, */
            *sub = (int8_t)(*sub + 2);             /* the way it was going */
        else if (*sub < 0)
            *sub = (int8_t)(*sub - 2);
        fm1_in.enc_prev[e] = (uint8_t)cur;
        if (*sub > 100 || *sub < -100)
            *sub = 0;                              /* (never off the detent that long) */
        if (cur == fm1_in.enc_rest[e]) {           /* back on the detent: whole cycles, a lost transition */
            int32_t n = *sub < 0 ? -*sub : *sub;   /* or two forgiven (one click = 4 transitions) */
            n = n >= 2 ? (n + 2) / 4 : 0;
            fm1_in.enc_steps[e] = (int16_t)(fm1_in.enc_steps[e] + (*sub < 0 ? -n : n));
            *sub = 0;
        }
    }
    fm1_in.frames++;
}

/* one column per call, from a timer ISR (see top). The dim pulses of column p ride on the shift of
 * column n: the lines go lit | glow | backlight of p, each layer ends when its time is up (TIMER4,
 * checked after every bit), and the latch comes with the lines dark. Frodhes: the shift is one bit on 10 ticks of 11
 * (fm1__sr_at), the whole word on the tick to column 2 and whenever the chain's column is not known */
static uint8_t fm1__tick_col;
static uint8_t fm1__led_phase __attribute__((section(".bss.fm1_led_phase")));                 /* the frame 0..15 (fm1_led_duty) */
/* the lit lines per column and frame (fm1_led_build); [col][16] so a tick indexes it with a shift. Own section, out
 * of the compiler's global merge (merged in, it splits the Rhodes' rh_F / rh_x: src/engine.c eq) */
static uint8_t fm1_led_tab[FM1_NCOL][16] __attribute__((section(".bss.fm1_led_tab")));
FM1_INLINE uint32_t fm1__pulse(uint32_t t0, uint32_t cur, uint32_t lit, uint32_t a, uint32_t b, uint32_t ta,
                               uint32_t tb, uint32_t tmax)   /* the dim layers' lines at this moment */
{
    uint32_t d = fm1_ticks() - t0, on = d < tmax ? lit | (d < ta ? a : 0u) | (d < tb ? b : 0u) : 0u;
    if (on != cur)
        fm1__led_lines(on);
    return on;
}
static void fm1_input_tick(void)
{
    uint32_t p = fm1__tick_col, n = p + 1u == FM1_NCOL ? 0u : p + 1u, i;
    uint32_t w = 0xFFFFu ^ (1u << n) ^ (n < 2u ? 1u << (11u + n) : 0u);
    uint32_t f = fm1__led_phase, lit = fm1_led_tab[p][f], a = fm1_led_dim[p] & ~lit, b = fm1_led_bg[p] & ~lit & ~a;
    uint32_t ta = a ? FM1__NS_T(FM1_GLOW_NS) : 0u, tb = b ? FM1__NS_T(fm1_led_bg_ns) : 0u;
    uint32_t tmax = ta > tb ? ta : tb, i0 = fm1__sr_at == n + 1u ? 15u : 0u;   /* the first bit to shift */
    FM1_INPUT_ADC_TAKE(p);
    fm1__led_lines(0);
    fm1_in.raw[p] = (uint8_t)fm1__rows();          /* column p has been latched one tick (the lines dark) */
    if (tmax) {
        uint32_t t0 = fm1_ticks(), cur = lit | a | (tb ? b : 0u);
        fm1__led_lines(cur);                       /* (the 595 still drives column p) */
        for (i = i0; i < 16u; i++) {               /* the shift; then wait if the pulse is longer */
            fm1__sr_bit(w, i);
            cur = fm1__pulse(t0, cur, lit, a, b, ta, tb, tmax);
        }
        while (cur)                                /* (Frodhes: the wait its own loop; the same accesses) */
            cur = fm1__pulse(t0, cur, lit, a, b, ta, tb, tmax);
        fm1__sr_latch();                           /* column n, the lines dark */
    } else if (i0) {
        fm1__sr_bit(w, 15u);                       /* (w's bit 0) */
        fm1__sr_latch();
    } else {
        fm1__sr_word(w);
    }

    if (n == 0u)
        fm1__led_phase = (uint8_t)((f + 1u) & 15u);
    fm1__led_lines(FM1_INPUT_ADC_START(p, n, fm1_led_tab[n][fm1__led_phase]));
    if (n != 1u) {
        fm1__sr_at = (uint8_t)(n + 2u == 12u ? 1u : n + 2u);   /* the next column: one bit away */
    } else if (FM1_INPUT_ADC_BUSY()) {
        fm1__sr_at = 0;                            /* (a conversion runs: column 2 gets the whole word next tick) */
    } else {
        for (i = 0; i < 15u; i++)                  /* column 2's word but its last bit (column 1 stays latched) */
            fm1__sr_bit(0xFFFFu ^ 1u << 2, i);
        fm1__sr_at = 3u;
    }
    fm1__tick_col = (uint8_t)n;
    fm1__keys(p);                                  /* its keys now: no wait for the frame's end */
    if (n == 0u)
        fm1__frame();
}

/* main-loop critical section against fm1_input_tick (main loop only: it
 * re-enables interrupts unconditionally) */
static inline uint32_t fm1__lock(void)
{
    __asm__ volatile("cli" ::: "memory");
    return 0;
}
static inline void fm1__unlock(uint32_t v)
{
    (void)v;
    __asm__ volatile("csync\n\tsti" ::: "memory");
}

/* detent steps turned since the last call, + = clockwise */
static int32_t fm1_enc_take(uint32_t e)
{
    uint32_t k = fm1__lock();
    int32_t s = fm1_in.enc_steps[e];
    fm1_in.enc_steps[e] = 0;
    fm1__unlock(k);
    return s;
}

static uint32_t fm1_input_edges(uint32_t *released)
{
    uint32_t k = fm1__lock();
    uint32_t p = fm1_in.pressed;
    if (released)
        *released = fm1_in.released;
    fm1_in.pressed = fm1_in.released = 0;
    fm1__unlock(k);
    return p;
}

static uint32_t fm1_input_note_edges(void)
{
    uint32_t k = fm1__lock();
    uint32_t p = fm1_in.notes_pressed;
    fm1_in.notes_pressed = 0;
    fm1__unlock(k);
    return p;
}

/* the LEDs with ids id0 .. id1 are group 0 of fm1_led_duty (Frodhes: the note keys; the buttons are group 1) */
static void fm1_led_group_ids(uint32_t id0, uint32_t id1)
{
    uint32_t p, r;
    for (p = 0; p < FM1_NCOL; p++) {
        fm1_led_grpmask[p] = 0;
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][p] >= 0 && (uint32_t)FM1_KEYMAP[r][p] >= id0 && (uint32_t)FM1_KEYMAP[r][p] <= id1)
                fm1_led_grpmask[p] |= (uint8_t)(1u << r);
    }
}
/* the lit table of fm1_input_tick from fm1_led, fm1_led_duty and the groups, rebuilt for the columns whose inputs
 * changed (main loop, after the LEDs are set). A duty spreads its frames evenly over the 16. A tick reading a column
 * mid-build shows it stale one frame (~1 ms): no lock. */
static uint32_t fm1__led_on(uint32_t duty, uint32_t f) { return duty >= 16u || ((f * duty) & 15u) < duty; }
static void fm1_led_build(void)
{
    static struct { uint8_t in[FM1_NCOL][2], duty[2], built; } l __attribute__((section(".bss.fm1_led_in")));   /* (own
                                                            * section: the table above) the inputs of the last build */
    uint32_t p, f, d0 = fm1_led_duty[0], d1 = fm1_led_duty[1], all = !l.built || l.duty[0] != d0 || l.duty[1] != d1;
    l.built = 1; l.duty[0] = (uint8_t)d0; l.duty[1] = (uint8_t)d1;
    for (p = 0; p < FM1_NCOL; p++) {
        if (!all && l.in[p][0] == fm1_led[p] && l.in[p][1] == fm1_led_grpmask[p])
            continue;
        l.in[p][0] = fm1_led[p]; l.in[p][1] = fm1_led_grpmask[p];
        for (f = 0; f < 16u; f++) {
            uint32_t keys = l.in[p][1], v = l.in[p][0];
            fm1_led_tab[p][f] = (uint8_t)((fm1__led_on(d0, f) ? v & keys : 0u) | (fm1__led_on(d1, f) ? v & ~keys : 0u));
        }
    }
}
/* (the glow of key id, fm1_led_key_glow: platform.c, Dinghy 0.4) */

/* LED of key id (button 0..13 or note key 14..40) */
static void fm1_led_key(uint32_t id, int on)
{
    uint32_t p, r;
    for (p = 0; p < FM1_NCOL; p++)
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][p] == (int8_t)id) {
                if (on)
                    fm1_led[p] |= (uint8_t)(1u << r);
                else
                    fm1_led[p] &= (uint8_t)~(1u << r);
            }
}
