/* SPDX-License-Identifier: GPL-3.0-only */
/* The converter scheduler (firmware/hal/fm1_adc.h, key velocity) on the host, against a model of the FM-1's
 * SARADC: every access to CON steps the model (a conversion begins when the enable bit appears, ends when CON is
 * written without it, and sets "done" and RES once its time is up: divider 7 = 26 us + 17 us per delay step, the
 * SDK's divider 6 / delay 15 = 211 us). The harness plays the scan tick as hal/fm1_keyadc.h does (take first, then
 * the slot, then a key start when the slot is free) and the main loop's fm1_adc_read between ticks.
 * tests/py/test_adc_sched.py builds and runs it: "ok <name>" per check, "<n> failed" at the end.
 *
 *   cc -O2 -Wall -Wno-unused-function -Ifirmware/hal -o build/host/adc_sched_test tests/c/adc_sched_test.c */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t con_v, res_v, wla_v;
static uint32_t *con_acc(void);
static uint32_t *res_acc(void) { return &res_v; }
#define FM1_ADC_CON (*(volatile uint32_t *)con_acc())
#define FM1_ADC_RES (*(volatile uint32_t *)res_acc())
#define FM1_WLA_CON0 (*(volatile uint32_t *)&wla_v)
#define FM1_ADC_SCHED_SECTION
#include "fm1_adc.h"

/* ---- the converter model */
static struct {
    uint64_t now;                  /* ns */
    int active, done;
    uint32_t ch, cfg, begin_con;
    uint64_t t0, need;
    uint32_t val[16];              /* what each channel reads */
    uint32_t slow;                 /* conversion time x this */
    uint32_t starts, tick_starts, aborts, done_n;
    uint32_t last_ch, last_cfg, last_begin;
    uint32_t chstarts[16], chdone[16];
} M;
static uint64_t dur_ns(uint32_t cfg)
{
    static const uint32_t DIV[8] = {1, 6, 12, 24, 48, 72, 96, 128};
    uint32_t div = cfg & 7u, delay = (cfg >> 12) & 15u;
    return (uint64_t)(26000u + 17000u * delay) * DIV[div] / 128u;
}
static uint32_t *con_acc(void)
{
    M.now += 50;                   /* an access: 50 ns */
    if (!(con_v & 0x10u)) {
        if (M.active && !M.done)
            M.aborts++;
        M.active = 0;
    } else if (!M.active) {
        M.active = 1;
        M.done = 0;
        M.ch = (con_v >> 8) & 15u;
        M.cfg = con_v & 0xF00Fu;
        M.begin_con = con_v;
        M.t0 = M.now;
        M.need = dur_ns(M.cfg) * (M.slow ? M.slow : 1u);
        M.starts++;
        M.tick_starts++;
        M.chstarts[M.ch]++;
        M.last_ch = M.ch;
        M.last_cfg = M.cfg;
        M.last_begin = con_v;
    }
    if (M.active && !M.done && M.now - M.t0 >= M.need) {
        M.done = 1;
        M.done_n++;
        M.chdone[M.ch]++;
        con_v |= 0x80u;
        res_v = M.val[M.ch] & 0x3FFu;
    }
    return &con_v;
}

static void reset(void)
{
    memset(&M, 0, sizeof M);
    memset((void *)&fm1_adc_sched, 0, sizeof fm1_adc_sched);
    con_v = res_v = wla_v = 0;
    M.val[FM1_ADC_ROW_PA7] = 300;
    M.val[FM1_ADC_ROW_PA8] = 700;
    M.val[FM1_ADC_BATT] = 573;
    M.val[FM1_ADC_MASTER] = 512;
}

/* ---- the tick, as hal/fm1_keyadc.h plays it */
static uint32_t T;                 /* ticks */
static uint32_t col;               /* the column latched last tick (n of the last tick) */
static int key_row = -1;           /* the row a key conversion was asked for last tick */
static uint32_t got_key, got_lost, key_ok, key_bad, max_tick_starts, slot1[11];
static int want_keys;              /* STRESS: a key on every free tick */
static int force_key;              /* ask for a key even when the slot said no (the refusal test) */
static uint32_t one_tick(void)
{
    uint32_t n = col + 1u == 11u ? 0u : col + 1u, v = 0, g, s;
    if (M.now < (uint64_t)T * 100000u)
        M.now = (uint64_t)T * 100000u;
    M.tick_starts = 0;
    g = fm1_adc_sched_take(&v);
    if (g == FM1_ADC_GOT_KEY) {
        got_key++;
        if (key_row >= 0 && v == M.val[key_row])
            key_ok++;
        else
            key_bad++;
    } else if (g == FM1_ADC_GOT_LOST) {
        got_lost++;
    }
    key_row = -1;
    s = fm1_adc_sched_slot(n);
    if (s)
        slot1[n]++;
    if ((!s && want_keys) || force_key) {
        uint32_t before = M.starts;
        int r = (int)(fm1_adc_sched.frame & 1u);
        fm1_adc_key_start((uint32_t)r);
        if (M.starts != before)
            key_row = r;
    }
    if (M.tick_starts > max_tick_starts)
        max_tick_starts = M.tick_starts;
    col = n;
    T++;
    return s;
}
static void run_frames(uint32_t frames)
{
    uint32_t i;
    for (i = 0; i < frames * 11u; i++)
        one_tick();
}
static void start_fresh(void)
{
    reset();
    T = 0;
    col = 10;                      /* the first tick latches column 0 */
    key_row = -1;
    got_key = got_lost = key_ok = key_bad = max_tick_starts = 0;
    memset(slot1, 0, sizeof slot1);
    want_keys = force_key = 0;
}

static int fails, oks;
#define CHECK(name, cond)                                                            \
    do {                                                                             \
        if (cond) { oks++; printf("ok %s\n", name); }                                \
        else { fails++; printf("FAIL %s (line %d)\n", name, __LINE__); }             \
    } while (0)

/* a full run, the keys on every free tick: who gets which tick */
static void t_pattern(void)
{
    uint32_t i, ticks_m = 0, ticks_b = 0, bad_slot = 0, frames = 1024;
    start_fresh();
    want_keys = 1;
    for (i = 0; i < frames * 11u; i++) {
        uint32_t n = (col + 1u) % 11u, f, before = M.starts, ch, s;
        uint32_t keys_before = M.chstarts[0] + M.chstarts[1];
        s = one_tick();
        f = fm1_adc_sched.frame;
        ch = M.starts != before ? M.last_ch : 99u;
        if (n == 0u && !(f & 1u)) {            /* MASTER's: column 0, even frames */
            ticks_m++;
            if (!s || ch != FM1_ADC_MASTER || M.last_cfg != FM1_ADC_CFG_KEY) bad_slot++;
        } else if (n == 1u && !(f & 255u)) {   /* the battery's: column 1, every 256th frame */
            ticks_b++;
            if (!s || ch != FM1_ADC_BATT || M.last_cfg != FM1_ADC_CFG_SDK) bad_slot++;
        } else if ((n == 2u || n == 3u) && !(f & 255u)) {   /* held: ~211 us = the next 2 ticks */
            if (!s || ch != 99u) bad_slot++;
        } else {                               /* a key's */
            if (s || M.chstarts[0] + M.chstarts[1] != keys_before + 1u) bad_slot++;
        }
    }
    CHECK("pattern_master_every_even_frame", ticks_m == frames / 2u && M.chstarts[FM1_ADC_MASTER] == frames / 2u);
    CHECK("pattern_battery_every_256_frames", ticks_b == frames / 256u && M.chstarts[FM1_ADC_BATT] == frames / 256u);
    CHECK("pattern_every_tick_as_planned", bad_slot == 0u);
    CHECK("pattern_one_conversion_per_tick", max_tick_starts <= 1u);
    CHECK("pattern_never_two_at_once", M.aborts == 0u && fm1_adc_sched.timeouts == 0u && fm1_adc_sched.batt_to == 0u);
    CHECK("pattern_keys_read_their_row", key_bad == 0u && key_ok == got_key && got_key > 9000u && got_lost == 0u);
    CHECK("pattern_convs_counted", fm1_adc_sched.convs == M.done_n && M.done_n == M.starts - 1u);
    /* (the last one still running) */
    CHECK("pattern_key_cfg", FM1_ADC_CFG_KEY == 0x200Fu && FM1_ADC_CFG_SDK == 0xF00Eu);
    CHECK("pattern_scheduler_on", fm1_adc_sched.on == 1u);
}

/* MASTER and the battery stay fresh, with and without key traffic: the longest gap (ticks) between two readings
 * taken into the cache, MASTER's changing every tick so that each take is new */
static void t_staleness(int keys)
{
    uint32_t i, mseq = 0, last_m = 0, worst_m = 0, last_b = 0, worst_b = 0, nb = 0, first_b = 1;
    char nm[64];
    start_fresh();
    want_keys = keys;
    for (i = 0; i < 4096u * 11u; i++) {
        M.val[FM1_ADC_MASTER] = (i * 13u) & 1023u;
        one_tick();
        if (fm1_adc_sched.mseq != mseq) {
            mseq = fm1_adc_sched.mseq;
            if (i - last_m > worst_m && last_m) worst_m = i - last_m;
            last_m = i;
            if (fm1_adc_sched.master != (i * 13u & 1023u) && fm1_adc_sched.master != ((i - 1u) * 13u & 1023u))
                worst_m = 9999;                  /* not the pot as it was at the conversion */
        }
        if (fm1_adc_sched.have_b && M.chdone[FM1_ADC_BATT] != nb) {
            nb = M.chdone[FM1_ADC_BATT];
            if (!first_b && i - last_b > worst_b) worst_b = i - last_b;
            first_b = 0;
            last_b = i;
        }
    }
    printf("# keys %d: master gap %u, battery gap %u (%u)\n", keys, worst_m, worst_b, nb);
    snprintf(nm, sizeof nm, "stale_master_2_frames%s", keys ? "_keys" : "");
    CHECK(nm, worst_m == 22u);
    snprintf(nm, sizeof nm, "stale_battery_256_frames%s", keys ? "_keys" : "");
    CHECK(nm, worst_b == 256u * 11u && nb == 16u);
}

/* the main loop's reads once the scheduler runs */
static void t_main_reads(void)
{
    int32_t a, b, c;
    uint32_t i, ok_ends = 1, starts;
    start_fresh();
    one_tick();                                  /* column 0, frame 1 (odd): on, no slot */
    a = fm1_adc_read(FM1_ADC_MASTER);
    b = fm1_adc_read(FM1_ADC_BATT);
    CHECK("main_none_yet", a == -1 && b == -1 && fm1_adc_sched.on);
    starts = M.starts;
    for (i = 0; i < 100; i++) (void)fm1_adc_read(FM1_ADC_MASTER), (void)fm1_adc_read(FM1_ADC_BATT);
    CHECK("main_never_converts_when_on", M.starts == starts);
    CHECK("main_other_channel_-1", fm1_adc_read(2) == -1 && fm1_adc_read(FM1_ADC_ROW_PA7) == -1);
    run_frames(2);
    a = fm1_adc_read(FM1_ADC_MASTER);
    c = fm1_adc_read(FM1_ADC_MASTER);
    CHECK("main_master_fresh_once", a == 512 && c == -1);
    run_frames(2);
    CHECK("main_master_again", fm1_adc_read(FM1_ADC_MASTER) == 512);
    /* the ends pass through exactly (MASTER reaches 0 and >= 1020 if the converter does) */
    for (i = 0; i < 3; i++) {
        static const uint32_t E[3] = {0, 1023, 1020};
        M.val[FM1_ADC_MASTER] = E[i];
        run_frames(2);
        if (fm1_adc_read(FM1_ADC_MASTER) != (int32_t)E[i]) ok_ends = 0;
    }
    CHECK("main_master_ends_pass", ok_ends);
    run_frames(256);
    CHECK("main_battery_cached", fm1_adc_read(FM1_ADC_BATT) == 573 && fm1_adc_read(FM1_ADC_BATT) == 573);
    /* knob_avg as platform_poll runs it: a pass every 0.5 ms sees each reading once, so the filter moves once per
     * reading (the pass rate does not change its time constant) */
    {
        int32_t knob_avg = 512 * 16, x;
        uint32_t used = 0, k;
        M.val[FM1_ADC_MASTER] = 1023;
        for (k = 0; k < 64u * 22u; k++) {
            one_tick();
            if (k % 5u == 0u && (x = fm1_adc_read(FM1_ADC_MASTER)) >= 0) {
                knob_avg += (x * 16 - knob_avg) / 64;
                used++;
            }
        }
        CHECK("main_knob_once_per_reading", used == 64u || used == 65u);
    }
}

/* a key start is refused while a slot's conversion runs */
static void t_refusal(void)
{
    uint32_t i, s, starts, refused_ok = 1;
    start_fresh();
    /* frame 2's column 0: MASTER's */
    while (!(fm1_adc_sched.frame == 1u && col == 10u)) one_tick();
    M.val[FM1_ADC_MASTER] = 777;
    force_key = 1;
    s = one_tick();                              /* the MASTER tick, a key asked for anyway */
    CHECK("refuse_on_master_tick", s == 1u && M.chstarts[FM1_ADC_MASTER] == 1u && M.chstarts[0] + M.chstarts[1] == 0u
                                       && key_row == -1);
    force_key = 0;
    one_tick();
    CHECK("refuse_master_not_clobbered", fm1_adc_sched.have_m && fm1_adc_sched.master == 777u && got_key == 0u
                                             && M.aborts == 0u);
    /* the battery's slot and its hold */
    start_fresh();
    while (!(fm1_adc_sched.frame == 255u && col == 10u)) one_tick();
    one_tick();                                  /* frame 256, column 0: MASTER */
    force_key = 1;
    for (i = 0; i < 3u; i++) {                   /* column 1 (the battery's start), 2, 3 (held) */
        starts = M.chstarts[0] + M.chstarts[1];
        s = one_tick();
        if (!s || M.chstarts[0] + M.chstarts[1] != starts) refused_ok = 0;
    }
    CHECK("refuse_during_battery_hold", refused_ok && M.chstarts[FM1_ADC_BATT] == 1u);
    s = one_tick();                              /* column 4: battery taken, a key may start */
    CHECK("battery_then_free", !s && fm1_adc_sched.have_b && fm1_adc_sched.batt == 573u && key_row >= 0
                                   && M.aborts == 0u);
    force_key = 0;
    /* a key start while another key's conversion runs (called twice in a tick) */
    start_fresh();
    run_frames(1);
    while (col != 4u) one_tick();
    starts = M.starts;
    fm1_adc_key_start(0);
    fm1_adc_key_start(1);
    CHECK("refuse_second_key", M.starts == starts + 1u && M.last_ch == 0u);
}

/* conversions not done in time */
static void t_timeouts(void)
{
    uint32_t i, starts;
    start_fresh();
    run_frames(3);
    while (col != 4u) one_tick();
    M.slow = 3;                                  /* keys and MASTER take 180 us: never by the next tick */
    starts = M.starts;
    fm1_adc_key_start(1);                        /* (as V2 would after a 0 from the slot) */
    key_row = 1;
    one_tick();
    CHECK("timeout_key_lost", got_lost == 1u && fm1_adc_sched.timeouts == 1u && M.aborts == 1u && M.starts == starts + 1u);
    while (!(col == 10u && !(fm1_adc_sched.frame & 1u))) one_tick();   /* the next frame is even's... */
    while (!(col == 10u && (fm1_adc_sched.frame & 1u))) one_tick();
    one_tick();                                  /* column 0, even: MASTER (slow) */
    one_tick();
    CHECK("timeout_master_counted_not_lost", fm1_adc_sched.timeouts == 2u && got_lost == 1u && M.aborts == 2u);
    /* the battery: 211 us x 5 > 8 ticks: dropped after 8, the converter free again */
    start_fresh();
    while (!(fm1_adc_sched.frame == 255u && col == 10u)) one_tick();
    one_tick();
    one_tick();                                  /* MASTER taken at normal speed; then make the battery slow */
    M.need *= 5;
    for (i = 0; i < 7u; i++) one_tick();
    CHECK("battery_held_7", fm1_adc_sched.busy && fm1_adc_sched.batt_to == 0u);
    one_tick();
    CHECK("battery_dropped_at_8", !fm1_adc_sched.busy && fm1_adc_sched.batt_to == 1u && !fm1_adc_sched.have_b
                                      && M.aborts == 1u);
    want_keys = 1;
    starts = M.chstarts[0] + M.chstarts[1];
    run_frames(4);
    CHECK("after_drop_keys_run", M.chstarts[0] + M.chstarts[1] > starts + 30u);
    /* the developer clear */
    fm1_adc_sched_clear();
    run_frames(1);
    CHECK("clear_counters", fm1_adc_sched.batt_to == 0u && fm1_adc_sched.timeouts == 0u && fm1_adc_sched.convs < 12u
                                && !fm1_adc_sched.clr);
}

/* before the scheduler runs: fm1_adc_read converts as 0.9.4 did, and the handshake */
static void t_boot(void)
{
    int32_t a;
    uint32_t begin_block, begin_slot, starts;
    start_fresh();
    M.val[FM1_ADC_BATT] = 581;
    a = fm1_adc_read(FM1_ADC_BATT);              /* nothing has run the tick: the blocking read */
    begin_block = M.last_begin;
    CHECK("boot_blocking_read", a == 581 && M.starts == 1u && M.last_cfg == FM1_ADC_CFG_SDK && !fm1_adc_sched.mbusy);
    CHECK("boot_blocking_is_094", begin_block == (0xF04Eu | (3u << 8) | 0x10u | 0x40u));
    /* the battery slot starts the converter exactly so */
    while (!(fm1_adc_sched.frame == 255u && col == 10u)) one_tick();
    one_tick();
    one_tick();
    begin_slot = M.last_begin;
    CHECK("battery_slot_is_094_sequence", begin_slot == begin_block && M.last_ch == FM1_ADC_BATT);
    /* a blocking read in progress (mbusy) when the tick comes: the tick starts nothing, even on MASTER's tick */
    start_fresh();
    fm1_adc_sched.mbusy = 1;
    starts = M.starts;
    run_frames(4);
    CHECK("boot_tick_waits_for_main_read", M.starts == starts && fm1_adc_sched.on);
    fm1_adc_sched.mbusy = 0;
    run_frames(2);
    CHECK("boot_then_slots", M.chstarts[FM1_ADC_MASTER] == 1u);
    /* the tick started a key before on (a column > 1 first): the main read must not convert under it */
    start_fresh();
    col = 4;                                     /* the first tick latches column 5 */
    one_tick();
    fm1_adc_key_start(0);
    key_row = 0;
    starts = M.starts;
    a = fm1_adc_read(FM1_ADC_MASTER);
    CHECK("boot_main_skips_under_tick", a == -1 && M.starts == starts && M.aborts == 0u && !fm1_adc_sched.on);
    one_tick();
    CHECK("boot_key_survives", got_key == 1u && key_bad == 0u);
}

int main(void)
{
    t_pattern();
    t_staleness(0);
    t_staleness(1);
    t_main_reads();
    t_refusal();
    t_timeouts();
    t_boot();
    printf("%d ok, %d failed\n", oks, fails);
    return fails != 0;
}
