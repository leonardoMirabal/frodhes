/* SPDX-License-Identifier: GPL-3.0-only */
/* hal/fm1_keyadc.h (the key rows' part of the scan tick: drive, LED blanking, self-test, developer modes) inside the
 * real hal/fm1_input.h tick, on a model of the FM-1's port A / B, its 74HC595 chain and its key matrix.
 * tests/py/test_keyadc.py copies the HAL headers to build/keyadc/hal with every register access turned into a call of
 * fake_reg() (and the .bss sections dropped: the host is not the FM-1's linker), then builds and runs this:
 *
 *   cc -O1 -Wall -Wno-unused-function -Ibuild/keyadc/hal -Ifirmware/src tests/c/keyadc_test.c -o build/keyadc/t
 *
 * fake_reg() sees every access in order: it clocks the 595 model on SRCLK / RCLK edges, gives PA / PB IN from the
 * latched column, the pressed keys and each row's pin state (a row driven low and released reads low until the
 * pull-up has lifted it, FAKE_RC_TICKS), advances a fake TIMER4, and records every rule broken: a row read while it
 * is driven, a digital input buffer switched off, PA6 driven, LED lines lit while a key row converts. The converter
 * scheduler (hal/fm1_adc.h, V1) and the key velocity's ISR side (src/keyvel.c, V3) are fakes here, with their
 * declared contracts (MASTER on column 0 of even frames, the battery a few ticks every 256 frames; a key pending
 * from its contact's close until its sample).
 * Prints "ok ..." / "FAIL ..." lines and "stat NAME VALUE" lines; the exit code is the number of failures. Built
 * with -DFM1_KA_SETTLE_US=0u it shows the self-test trap the settle time is there for (the python test expects it). */
#define DINGHY_HOST                             /* keyvel.h: KV_BSS empty, no FM-1 mapping (done here) */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static volatile uint32_t *fake_reg(uint32_t a);
#include "keyvel.h"
static inline void fm1_ka_take(uint32_t p);
static inline uint32_t fm1_ka_start(uint32_t p, uint32_t n, uint32_t lit);
#define FM1_INPUT_ADC_TAKE(p) fm1_ka_take(p)
#define FM1_INPUT_ADC_START(p, n, lit) fm1_ka_start(p, n, lit)
static inline uint32_t fm1_ka_busy(void);
#define FM1_INPUT_ADC_BUSY() fm1_ka_busy()
#include "fm1_input.h"
#include "fm1_adc.h"
/* V1's tick side, faked (fm1_keyadc.h calls these names) */
#define fm1_adc_sched_busy fake_sched_busy
#define fm1_adc_sched_take fake_sched_take
#define fm1_adc_sched_slot fake_sched_slot
#define fm1_adc_key_start fake_key_start
static uint32_t fake_sched_busy(void);
static uint32_t fake_sched_take(uint32_t *v);
static uint32_t fake_sched_slot(uint32_t n);
static void fake_key_start(uint32_t row);
#include "fm1_keyadc.h"

static int fails;
#define CHECK(c, ...) do { if (c) { printf("ok   "); printf(__VA_ARGS__); printf("\n"); } \
                           else { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

#include "fm1_hw_model.h"                      /* the hardware model (pin_state included) */

/* ---- V1 fake: the slots, one conversion at a time */
static struct {
    uint32_t running, row, val, col, drive_state, lose_next, tick_free, frame, batt_end;
    uint32_t key_starts, slots, takes, double_start, start_unfree;
} adc;
static uint32_t tick_no;
static uint32_t fake_sched_slot(uint32_t n)
{
    adc.slots++;
    if (n == 0u) adc.frame++;
    if (tick_no <= adc.batt_end) return 1;               /* (by the tick: the idle tick calls no slot on 2..10) */
    if (n == 0u && !(adc.frame & 1u)) return 1;            /* MASTER */
    if (n == 1u && (adc.frame & 255u) == 1u) { adc.batt_end = tick_no + 3u; return 1; }   /* the battery, 4 ticks */
    adc.tick_free = tick_no;
    return 0;
}
static void fake_key_start(uint32_t row)
{
    uint32_t pb = row ? 8u : 7u, st = pin_state(pb);
    int c = active_col();
    if (adc.running) adc.double_start++;
    if (adc.tick_free != tick_no) adc.start_unfree++;
    adc.key_starts++;
    adc.running = 1;
    adc.row = row;
    adc.col = (uint32_t)c;
    adc.drive_state = st;
    if ((st & 3u) == 1u) adc.val = 0;                      /* driven low */
    else if (c >= 0 && (pressed[c] >> (3u + row) & 1u))
        adc.val = (st & 3u) == 2u ? force[c][row] + 40u * (st >> 2) : 190u;   /* driven / the pull-up's diode */
    else adc.val = (st & 3u) == 2u ? 1015u : 1023u;
}
static uint32_t fake_sched_busy(void) { return adc.running; }
static uint32_t fake_sched_take(uint32_t *v)
{
    if (!adc.running) return FM1_ADC_GOT_NONE;
    adc.running = 0;
    adc.takes++;
    if (adc.lose_next) { adc.lose_next--; return FM1_ADC_GOT_LOST; }
    *v = adc.val;
    return FM1_ADC_GOT_KEY;
}

/* ---- V3 fake: a key pending from its contact's close (raw edge) until its sample; a deferral once per landing */
static struct {
    uint8_t pend[FM1_NCOL], prev[FM1_NCOL], deferred[FM1_NCOL];
    uint32_t want_calls, samples, defers, defer_calls, storms, no_defer;
    uint32_t last_col, last_row, last_v, picks_odd[2];
} kvf;
static inline int kv_isr_want(uint32_t p, uint32_t raw) { (void)p; (void)raw; kvf.want_calls++; return 1; }
static void kv_isr_col(uint32_t p, uint32_t raw, uint32_t notes)
{
    uint32_t a = (raw >> 3) & 3u;
    (void)notes;
    kvf.pend[p] |= (uint8_t)(a & ~kvf.prev[p]);
    kvf.prev[p] = (uint8_t)a;
}
static inline uint32_t kv_isr_pend(uint32_t n) { return kvf.pend[n]; }
/* the idle test's contract: 0 only if kv_isr_col(p, raw) would change nothing and column n has nothing pending */
static inline uint32_t kv_isr_busy(uint32_t p, uint32_t n, uint32_t raw)
{
    return ((raw >> 3) & 3u) | kvf.prev[p] | kvf.pend[n];
}
static int32_t kv_isr_pick(uint32_t n, uint32_t odd)
{
    uint32_t b = kvf.pend[n];
    if (!b) return -1;
    kvf.picks_odd[odd & 1u]++;
    return b == 3u ? (int32_t)(odd & 1u) : b == 2u ? 1 : 0;
}
static void kv_isr_sample(uint32_t n, uint32_t row, uint32_t v)
{
    kvf.samples++;
    kvf.last_col = n; kvf.last_row = row; kvf.last_v = v;
    kvf.pend[n] &= (uint8_t)~(1u << row);
    kvf.deferred[n] &= (uint8_t)~(1u << row);
}
static int kv_isr_defer(uint32_t n, uint32_t row)
{
    kvf.defer_calls++;
    if (kvf.no_defer || (kvf.deferred[n] >> row & 1u)) return 0;
    kvf.deferred[n] |= (uint8_t)(1u << row);
    kvf.defers++;
    return 1;
}
static void kv_isr_storm(void) { kvf.storms++; }

/* ---- the harness */
static uint32_t led_seen[FM1_NCOL];             /* lit ticks per column */
static uint32_t raw_glitch;                     /* a held key read open, or an unpressed key read closed */
static uint32_t dark_conv_ticks, conv_ticks;
static uint32_t last_lit_frame[FM1_NCOL], max_gap[FM1_NCOL];

static void reset_all(void)
{
    memset(mem, 0, sizeof mem);
    memset(&bad, 0, sizeof bad); memset(&adc, 0, sizeof adc); memset(&kvf, 0, sizeof kvf);
    memset(pressed, 0, sizeof pressed); memset(rel_low_at, 0, sizeof rel_low_at);
    memset((void *)&fm1_in, 0, sizeof fm1_in); memset((void *)&fm1__kpass, 0, sizeof fm1__kpass);
    memset((void *)&fm1_ka, 0, sizeof fm1_ka);
    memset(fm1_led, 0, sizeof fm1_led); memset(fm1_led_dim, 0, sizeof fm1_led_dim);
    memset(fm1_led_bg, 0, sizeof fm1_led_bg); memset(led_seen, 0, sizeof led_seen);
    memset(last_lit_frame, 0, sizeof last_lit_frame); memset(max_gap, 0, sizeof max_gap);
    fm1_led_duty[0] = fm1_led_duty[1] = 16u;
    sr = latch = prev_out_a = prev_dir_a = inited = 0;
    raw_glitch = dark_conv_ticks = conv_ticks = tick_no = 0;
    fm1__tick_col = 0; fm1__led_phase = 0;
    fm1_input_init();
    inited = 1;
    fm1_led_build();
}
/* one scan tick, then the model's checks on its result */
static void tick(void)
{
    uint32_t p = fm1__tick_col, lines;
    int c;
    tick_no++;
    fm1_input_tick();
    bad.ticks++;
    /* column p was read this tick: every key's raw bit must be what is pressed */
    if ((fm1_in.raw[p] & 0x1Eu) != (pressed[p] & 0x1Eu))
        raw_glitch++;
    c = active_col();
    lines = led_lines();
    if (c >= 0 && lines) {
        led_seen[c]++;
        if (last_lit_frame[c] && fm1_in.frames - last_lit_frame[c] > max_gap[c])
            max_gap[c] = fm1_in.frames - last_lit_frame[c];
        last_lit_frame[c] = fm1_in.frames;
    }
    if (adc.running) {                          /* the window: the row as it was at the start, the column dark */
        conv_ticks++;
        if (lines) bad.led_conv++;
        else dark_conv_ticks++;
        if (pin_state(adc.row ? 8u : 7u) != adc.drive_state) bad.led_conv += 1000u;
    }
}
static void frames(uint32_t n) { uint32_t i; for (i = 0; i < n * FM1_NCOL; i++) tick(); }
static int rules_ok(const char *what)
{
    int ok = !bad.read_driven && !bad.die_off && !bad.pa6_driven && !bad.led_conv && !adc.double_start &&
             !adc.start_unfree;
    CHECK(ok, "%s: rules (rows read driven %u, input buffer off %u, PA6 driven %u, LEDs lit while converting %u, "
          "two conversions %u, a conversion in a slot %u)", what, bad.read_driven, bad.die_off, bad.pa6_driven,
          bad.led_conv, adc.double_start, adc.start_unfree);
    return ok;
}
static void press(uint32_t col, uint32_t rowbit, int on)
{
    if (on) pressed[col] |= (uint8_t)(1u << rowbit);
    else pressed[col] &= (uint8_t)~(1u << rowbit);
}
static uint32_t pa_drive_bits(void) { return ~REG(PA_(FM1_DIR)) & (FM1_KA_PA7 | FM1_KA_PA8); }

int main(void)
{
    uint32_t i, k;

    /* 1. idle, NORMAL: no conversion, nothing driven, the LED lines exactly the table's */
    reset_all();
    for (i = 0; i < FM1_NCOL; i++) fm1_led[i] = 0x1Eu;
    fm1_led_build();
    frames(300);
    rules_ok("idle");
    CHECK(adc.key_starts == 0 && !pa_drive_bits() && raw_glitch == 0, "idle: no key conversion (%u), rows inputs, "
          "raw clean (%u)", adc.key_starts, raw_glitch);
    CHECK(adc.slots == 2u * 300u && kvf.want_calls == 0u && adc.takes == 0u,
          "idle: only the slots' columns 0 / 1 call out (slots %u of 600, kv_isr_want %u, takes %u in %u ticks)",
          adc.slots, kvf.want_calls, adc.takes, bad.ticks);
    for (k = 0, i = 0; i < FM1_NCOL; i++) k += led_seen[i] == 300u;
    CHECK(k == FM1_NCOL, "idle: every column lit every frame (%u of 11 columns)", k);

    /* 2. a landing on PA7 column 4 (key 27 = id 27 -> note 13) and on PA8 column 7 (note 4), DRIVE 1: one driven
     * sample each, the column dark that one tick, the held keys debounce and never read open */
    reset_all();
    for (i = 0; i < FM1_NCOL; i++) fm1_led[i] = 0x1Eu;
    fm1_led_build();
    force[4][0] = 300; force[7][1] = 420;
    frames(5);
    press(4, 3, 1); press(7, 4, 1);
    frames(20);
    rules_ok("landing");
    CHECK(kvf.samples == 2 && adc.key_starts == 2, "landing: two samples (%u), two conversions (%u)", kvf.samples,
          adc.key_starts);
    CHECK(kvf.last_col == 7 && kvf.last_row == 1 && kvf.last_v == 420, "landing: the sample of column 7 PA8 is the "
          "driven reading (%u %u %u)", kvf.last_col, kvf.last_row, kvf.last_v);
    CHECK(conv_ticks == 2 && dark_conv_ticks == 2, "landing: LEDs dark for the conversion's tick only (%u of %u)",
          dark_conv_ticks, conv_ticks);
    CHECK(led_seen[4] == 24 && led_seen[7] == 24, "landing: one dark frame per landing (col 4 lit %u, col 7 %u, of 25)",
          led_seen[4], led_seen[7]);
    CHECK(raw_glitch == 0 && (fm1_in.notes >> (27 - 14) & 1u) && (fm1_in.notes >> (18 - 14) & 1u),
          "landing: held keys read closed every frame (%u glitches) and debounce (notes 0x%x)", raw_glitch,
          fm1_in.notes);
    CHECK(!pa_drive_bits() && !(REG(PA_(FM1_HD0)) & 0x180u) && !(REG(PA_(FM1_HD)) & 0x180u) &&
          !(REG(PA_(FM1_OUT)) & 0x180u), "landing: rows restored (inputs, latch low, HD0 / HD clear)");
    press(4, 3, 0); press(7, 4, 0);
    frames(20);
    CHECK(!(fm1_in.notes & ((1u << 13) | (1u << 4))) && raw_glitch == 0, "landing: released keys debounce off");

    /* 3. DRIVE 1..4 and PULL: the driver strength in HD0 / HD during the conversion; PULL leaves the pull-up */
    for (k = 0; k < 5u; k++) {
        uint32_t st;
        reset_all();
        fm1_ka_dev_mode(k < 4u ? KV_DEV_DRIVE1 + k : KV_DEV_PULL);
        fm1_ka_dev_mode(KV_DEV_NORMAL);         /* (a mode command keeps DRIVE) */
        force[2][0] = 250;
        frames(3);
        press(2, 3, 1);
        frames(6);
        st = adc.drive_state;
        if (k < 4u)
            CHECK(st == (2u | k << 2) && kvf.last_v == 250u + 40u * k && fm1_ka_dev_item(KV_IT_DRIVE) == k + 1u,
                  "DRIVE %u: driven high, HD0 %u HD %u, reading %u, item %u", k + 1u, st >> 2 & 1u, st >> 3 & 1u,
                  kvf.last_v, fm1_ka_dev_item(KV_IT_DRIVE));
        else
            CHECK(st == 0u && kvf.last_v == 190u && fm1_ka_dev_item(KV_IT_DRIVE) == 0u && dark_conv_ticks == 1,
                  "PULL: not driven, the pull-up's reading %u, item 0, still dark (%u)", kvf.last_v, dark_conv_ticks);
        CHECK(!(REG(PA_(FM1_HD0)) & 0x180u) && !(REG(PA_(FM1_HD)) & 0x180u) && !pa_drive_bits() && raw_glitch == 0,
              "%s: restored, HD0 / HD clear, raw clean", k < 4u ? "DRIVE" : "PULL");
        rules_ok("drive");
    }

    /* 4. both rows of one column at once: one conversion a tick, one row a frame, the other the next */
    reset_all();
    force[5][0] = 300; force[5][1] = 310;
    frames(3);
    press(5, 3, 1); press(5, 4, 1);
    frames(6);
    CHECK(kvf.samples == 2 && adc.key_starts == 2 && kvf.picks_odd[0] == 1 && kvf.picks_odd[1] == 1,
          "chord on one column: two samples on two frames of each parity (%u %u %u)", kvf.samples,
          kvf.picks_odd[0], kvf.picks_odd[1]);
    rules_ok("chord");

    /* 5. MASTER's slot (column 0, even frames): a key on column 0 is sampled on an odd frame, never in the slot */
    reset_all();
    force[0][0] = 333;
    for (i = 0; i < 40u; i++) {
        press(0, 3, 1); frames(3);
        press(0, 3, 0); frames(10);
    }
    CHECK(kvf.samples == 40 && adc.start_unfree == 0, "column 0 vs MASTER: 40 landings, 40 samples (%u), none in a "
          "slot", kvf.samples);
    rules_ok("slots");

    /* 6. a lost conversion: no sample, the key still pending, sampled on its next frame */
    reset_all();
    force[3][1] = 444;
    adc.lose_next = 1;
    frames(2);
    press(3, 4, 1);
    frames(6);
    CHECK(kvf.samples == 1 && adc.key_starts == 2 && kvf.last_v == 444, "lost: retried, one sample (%u of %u starts)",
          kvf.samples, adc.key_starts);
    rules_ok("lost");

    /* 7. BRIGHTNESS 1 (duty 1 of 16): a landing on the lit frame would make a 32-frame blink; deferred once it never
     * does. Every phase of the 16 tried, on a key of each row */
    for (k = 0; k < 2u; k++) {
        uint32_t g = 0;
        reset_all();
        kvf.no_defer = k;
        for (i = 0; i < FM1_NCOL; i++) fm1_led[i] = 0x1Eu;
        fm1_led_duty[0] = fm1_led_duty[1] = 1u;
        fm1_led_build();
        force[6][0] = force[6][1] = 300;
        for (i = 0; i < 64u; i++) {
            uint32_t t;
            for (t = 0; t < i % 11u; t++) tick();
            press(6, 3 + (i & 1u), 1); frames(5);
            press(6, 3 + (i & 1u), 0); frames(12 + i % 5u);
        }
        for (i = 0; i < FM1_NCOL; i++) if (max_gap[i] > g) g = max_gap[i];
        if (!k) {
            CHECK(g <= 16u && kvf.samples == 64 && kvf.defers > 0 && kvf.defers <= 64,
                  "BRIGHTNESS 1: longest gap between lit frames %u (16 = none swallowed), %u samples, %u deferrals",
                  g, kvf.samples, kvf.defers);
            rules_ok("brightness 1");
        } else {
            CHECK(g > 16u, "BRIGHTNESS 1 without the deferral: a gap of %u frames (the test sees the blink)", g);
        }
    }
    /* LEDS ALL (every frame lit): never deferred; LEDS OFF: never asked, lines always dark */
    for (k = 0; k < 2u; k++) {
        reset_all();
        for (i = 0; i < FM1_NCOL; i++) fm1_led[i] = k ? 0u : 0x1Eu;
        fm1_led_build();
        force[8][0] = 300;
        for (i = 0; i < 20u; i++) { press(8, 3, 1); frames(4); press(8, 3, 0); frames(10); }
        if (!k)
            CHECK(kvf.defer_calls == 0 && kvf.samples == 20 && led_seen[8] == 280u - 20u,
                  "LEDS ALL: no deferral asked (%u), 20 samples, one dark frame each (lit %u of 280)",
                  kvf.defer_calls, led_seen[8]);
        else
            CHECK(kvf.defer_calls == 0 && kvf.samples == 20 && led_seen[8] == 0, "LEDS OFF: no deferral asked (%u), "
                  "20 samples, never lit (%u)", kvf.defer_calls, led_seen[8]);
    }

    /* 8. SELFTEST: every free tick the frame's row of the latched column driven low; every reading 0; restored and
     * settled before the read: OCT- / OCT+ (PA8 columns 0 / 1) and every other key never read pressed, a held key
     * stays held */
    reset_all();
    fm1_ka_dev_mode(KV_DEV_SELFTEST);
    press(9, 4, 1);                             /* key id 21 (note 7) held through it */
    frames(400);
    k = fm1_ka_dev_item(KV_IT_ST_N);
    printf("stat selftest_raw_glitch %u\n", raw_glitch);
    printf("stat selftest_buttons %u\n", fm1_in.buttons | fm1_in.pressed);
    CHECK(k > 3000u && fm1_ka_dev_item(KV_IT_ST_PA7) == 0 && fm1_ka_dev_item(KV_IT_ST_PA8) == 0,
          "SELFTEST: %u readings, highest PA7 %u PA8 %u (must be 0)", k, fm1_ka_dev_item(KV_IT_ST_PA7),
          fm1_ka_dev_item(KV_IT_ST_PA8));
    CHECK(raw_glitch == 0 && fm1_in.buttons == 0 && fm1_in.pressed == 0 && fm1_in.notes == (1u << 7),
          "SELFTEST: no key read pressed or open by the drive (%u glitches, buttons 0x%x, notes 0x%x)", raw_glitch,
          fm1_in.buttons, fm1_in.notes);
    CHECK(kvf.samples == 0, "SELFTEST: no reading goes to key velocity (%u)", kvf.samples);
    CHECK(fm1_ka_dev_item(KV_IT_MODE) == KV_DEV_SELFTEST, "SELFTEST: item 0 is the mode");
    rules_ok("selftest");
    fm1_ka_dev_mode(KV_DEV_NORMAL);
    CHECK(fm1_ka_dev_item(KV_IT_ST_N) == 0 && fm1_ka_dev_item(KV_IT_ST_PA7) == 0, "a mode command clears the counters");
    frames(5);
    CHECK(!pa_drive_bits() && adc.key_starts > 3000u && !adc.running, "SELFTEST off: rows inputs again");

    /* the self-test's sensitivity: a converter channel that is not the driven row would read the pull-up */
    reset_all();
    fm1_ka_dev_mode(KV_DEV_SELFTEST);
    force[1][0] = 0;
    frames(4);
    CHECK(adc.drive_state == 1u, "SELFTEST: the converted row is the driven-low one (state %u)", adc.drive_state);

    /* 9. STRESS: a conversion on every free tick (the frame's row when no key wants one), its reading dropped, the
     * LEDs dark on those ticks; held keys still debounce. OFF: none, even with a key pending */
    reset_all();
    fm1_ka_dev_mode(KV_DEV_STRESS);
    press(5, 3, 1);
    frames(100);
    k = adc.key_starts;
    CHECK(k > 1000u && kvf.samples == 1 && raw_glitch == 0 && (fm1_in.notes >> (28 - 14) & 1u),
          "STRESS: %u conversions in 1100 ticks, 1 key sample, held key clean (%u glitches)", k, raw_glitch);
    rules_ok("stress");
    reset_all();
    fm1_ka_dev_mode(KV_DEV_OFF);
    press(5, 3, 1);
    frames(50);
    CHECK(adc.key_starts == 0 && kvf.pend[5] == 1u && adc.slots == bad.ticks, "OFF: no key conversion, the slots run");
    reset_all();
    fm1_ka_dev_mode(KV_DEV_STORM);
    frames(320);
    CHECK(kvf.storms == 10u, "STORM: a storm every 32 frames (%u in 320)", kvf.storms);

    /* 10. the developer items */
    reset_all();
    frames(7);
    CHECK(fm1_ka_dev_item(KV_IT_FRAMES) == (fm1_in.frames & 0x3FFFu) && fm1_in.frames == 7u, "item 17: frames (%u)",
          fm1_ka_dev_item(KV_IT_FRAMES));
    CHECK(fm1_ka_dev_item(KV_IT_TICK_MAX) == 0 && fm1_ka_dev_item(KV_IT_DRIVE) == 1u && fm1_ka_dev_item(99) == 0,
          "items: tick max 0 (no FM1_KA_PROF), DRIVE 1 by default, unknown 0");

    printf("%s (%d failed)\n", fails ? "FAIL" : "ok", fails);
    return fails;
}
