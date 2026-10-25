/* SPDX-License-Identifier: GPL-3.0-only */
/* hal/fm1_input.h's scan tick on the model of the FM-1's ports, 74HC595 chain and key matrix (fm1_hw_model.h; the HAL
 * copied by tests/py/test_tick_595.py with every register access a call of fake_reg(), as tests/py/test_keyadc.py does):
 * the one-bit shift (fm1__sr_at) must leave the chain exactly as the whole word did.
 *
 *   every latch: the chain holds column n's word w(n) (one column low, columns 0 / 1 also bits 11 / 12), and the LED
 *   lines are dark at that moment (no ghosting); after each tick the lines are fm1_led_tab[n][frame] (the lit layer);
 *   a held key is read closed and an open key open on every tick; the debounced notes / buttons follow the presses;
 *   the glow and the backlight pulses are lit for at least their time (TIMER4) and end before the latch.
 *   the dark part of every tick (the rows' read .. the latch) holds one shift-clock edge, so every column is dark as
 *   long as the others; the cost: one edge on 10 ticks of 11, 16 on the tick that latches column 1 (15 of them for
 *   column 2, after its lines are lit), and the register accesses per tick (printed: "stat NAME VALUE").
 * "trace" as argv[1] prints one line per tick (the latch, the lines, the rows, the debounced state) for a comparison
 * with another version of the HAL. Exit code: the number of failures. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static volatile uint32_t *fake_reg(uint32_t a);
#define FM1_MODEL_ON_ACCESS(a) on_access(a)
static void on_access(uint32_t a);
#include "fm1_input.h"

static int fails;
#define CHECK(c, ...) do { if (c) { printf("ok   "); printf(__VA_ARGS__); printf("\n"); } \
                           else { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

#include "fm1_hw_model.h"

static uint32_t accesses, srclk_edges, latches, latch_lit, pulse_t0, pulse_t1, latched, rows_read, dark_edges;
static uint32_t last_out_a;
static int counting;
static void on_access(uint32_t a)
{
    uint32_t o = REG(PA_(FM1_OUT));
    if (!counting) return;
    accesses++;
    if (a == PA_(FM1_IN)) rows_read = 1;
    if ((o & ~last_out_a) & (1u << 3)) {
        srclk_edges++;
        if (rows_read && !latched) dark_edges++;  /* shifted between the rows' read and the latch: the dark part */
    }
    if (!latched && led_lines()) {              /* the lines lit before the latch: the pulse (from the rows' read on) */
        if (!pulse_t0) pulse_t0 = now;
        pulse_t1 = now;
    }
    if ((o & ~last_out_a) & (1u << 1)) {        /* RCLK rising: the outputs change now */
        latches++;
        latched = 1;
        if (led_lines()) latch_lit++;
    }
    last_out_a = o;
}

static uint32_t w_of(uint32_t n) { return 0xFFFFu ^ (1u << n) ^ (n < 2u ? 1u << (11u + n) : 0u); }

static uint32_t bad_latch, bad_lines, raw_glitch, edges_hist[17], tick_acc_max, tick_acc_sum, ticks;
static uint32_t pulse_short, pulse_long, pulses, dark_uneven;
static int trace;

static void reset_all(void)
{
    memset(mem, 0, sizeof mem);
    memset(&bad, 0, sizeof bad);
    memset(pressed, 0, sizeof pressed); memset(rel_low_at, 0, sizeof rel_low_at);
    memset((void *)&fm1_in, 0, sizeof fm1_in); memset((void *)&fm1__kpass, 0, sizeof fm1__kpass);
    memset(fm1_led, 0, sizeof fm1_led); memset(fm1_led_dim, 0, sizeof fm1_led_dim);
    memset(fm1_led_bg, 0, sizeof fm1_led_bg);
    fm1_led_bg_ns = 0;
    fm1_led_duty[0] = fm1_led_duty[1] = 16u;
    sr = latch = prev_out_a = prev_dir_a = inited = 0;
    fm1__tick_col = 0; fm1__led_phase = 0;
    fm1_input_init();
    inited = 1;
    fm1_led_build();
}

/* one tick; the pulses' lit time is measured in fake TIMER4 ticks (one per register access) */
static void tick(uint32_t glow_t)
{
    uint32_t p = fm1__tick_col, n, e0 = srclk_edges, a0 = accesses, l0 = latches, lines;
    counting = 1;
    last_out_a = REG(PA_(FM1_OUT));
    pulse_t0 = pulse_t1 = latched = rows_read = dark_edges = 0;
    fm1_input_tick();
    counting = 0;
    n = fm1__tick_col;
    ticks++;
    if (latches - l0 != 1u || latch != w_of(n)) bad_latch++;
    if (ticks > 1u && dark_edges != 1u) dark_uneven++;   /* (the first tick after init: the whole word) */
    lines = led_lines();
    if (lines != fm1_led_tab[n][fm1__led_phase]) bad_lines++;
    if ((fm1_in.raw[p] & 0x3Eu) != (pressed[p] & 0x3Eu)) raw_glitch++;
    if (srclk_edges - e0 <= 16u) edges_hist[srclk_edges - e0]++;
    if (accesses - a0 > tick_acc_max) tick_acc_max = accesses - a0;
    tick_acc_sum += accesses - a0;
    if (glow_t && pulse_t0) {                   /* lit for at least the pulse's time, and not much longer */
        uint32_t len = pulse_t1 - pulse_t0 + 1u;
        pulses++;
        if (len < glow_t) pulse_short++;
        if (len > glow_t + 40u) pulse_long++;
    }
    if (trace)
        printf("T %u p%u n%u latch %04x lines %02x raw %02x notes %08x buttons %05x\n", ticks, p, n, latch, lines,
               fm1_in.raw[p], fm1_in.notes, fm1_in.buttons);
}

static void frames(uint32_t nf, uint32_t glow_t)
{
    uint32_t i;
    for (i = 0; i < nf * FM1_NCOL; i++) tick(glow_t);
}

static void clear_stats(void)
{
    bad_latch = bad_lines = raw_glitch = tick_acc_max = tick_acc_sum = ticks = latch_lit = latches = 0;
    pulse_short = pulse_long = pulses = dark_uneven = 0;
    accesses = srclk_edges = 0;
    memset(edges_hist, 0, sizeof edges_hist);
}

static void report(const char *what)
{
    CHECK(!bad_latch, "%s: every latch holds column n's word (%u bad of %u ticks)", what, bad_latch, ticks);
    CHECK(!latch_lit, "%s: the LED lines dark at every latch (%u lit)", what, latch_lit);
    CHECK(!bad_lines, "%s: the lines after each tick are the lit table's (%u bad)", what, bad_lines);
    CHECK(!dark_uneven, "%s: one bit between the rows' read and the latch on every tick (%u ticks more): every "
          "column dark as long, as bright", what, dark_uneven);
    CHECK(!raw_glitch, "%s: every key read as pressed / open (%u glitches)", what, raw_glitch);
    printf("stat %s_ticks %u\nstat %s_one_bit %u\nstat %s_whole %u\nstat %s_acc_mean %u\nstat %s_acc_max %u\n", what,
           ticks, what, edges_hist[1], what, edges_hist[16], what, ticks ? tick_acc_sum / ticks : 0u, what, tick_acc_max);
}

int main(int argc, char **argv)
{
    uint32_t c, i, seed = 12345u;
    trace = argc > 1 && !strcmp(argv[1], "trace");

    /* 1. idle, LEDs off */
    reset_all(); clear_stats();
    frames(40, 0);
    report("idle");
    CHECK(edges_hist[1] + edges_hist[16] + 1u == ticks && edges_hist[16] + 1u == 40u,
          "idle: one shift-clock edge per tick, 1 + 15 on the tick that latches column 1 (column 2's word but its last "
          "bit), the whole word once after init (%u one-bit, %u with 15 more)", edges_hist[1], edges_hist[16]);

    /* 2. every LED lit, two duties (the lit table's frames), keys held */
    reset_all(); clear_stats();
    for (c = 0; c < FM1_NCOL; c++) fm1_led[c] = 0x1Eu;
    fm1_led_group_ids(14u, 40u);
    fm1_led_duty[0] = 1u; fm1_led_duty[1] = 10u;
    fm1_led_build();
    pressed[3] = 0x10u; pressed[7] = 0x08u;     /* note keys */
    frames(64, 0);
    report("lit");
    CHECK(fm1_in.notes != 0u, "lit: the held note keys debounced (notes %08x)", fm1_in.notes);

    /* 3. the glow and the backlight on every LED but the lit ones (the pulses ride on the shift, then wait) */
    reset_all(); clear_stats();
    for (c = 0; c < FM1_NCOL; c++) { fm1_led[c] = c & 1u ? 0x02u : 0u; fm1_led_dim[c] = 0x0Cu; fm1_led_bg[c] = 0x1Eu; }
    fm1_led_bg_ns = 1500u;
    fm1_led_build();
    frames(40, FM1__NS_T(FM1_GLOW_NS));
    report("glow");
    CHECK(pulses >= 40u * FM1_NCOL && !pulse_short && !pulse_long,
          "glow: every tick's pulse lit %u..%u+40 TIMER4 ticks (%u pulses, %u short, %u long)",
          (unsigned)FM1__NS_T(FM1_GLOW_NS), (unsigned)FM1__NS_T(FM1_GLOW_NS), pulses, pulse_short, pulse_long);

    /* 4. presses and releases at random, the glow on half the columns */
    reset_all(); clear_stats();
    for (c = 0; c < FM1_NCOL; c++) { fm1_led[c] = (uint8_t)(c * 7u & 0x1Eu); fm1_led_dim[c] = c & 1u ? 0x10u : 0u; }
    fm1_led_build();
    for (i = 0; i < 400u; i++) {
        seed = seed * 1103515245u + 12345u;
        c = (seed >> 16) % FM1_NCOL;
        pressed[c] ^= (uint8_t)(2u << ((seed >> 8) & 3u));
        frames(3, 0);
    }
    report("keys");
    printf("%s\n", fails ? "FAILED" : "all ok");
    return fails;
}
