/* SPDX-License-Identifier: GPL-3.0-only */
/* firmware/src/keyvel.c on its own (tests/py/test_keyvel.py builds and runs it):
 *
 *   cc -O2 -Wall -Wno-unused-function -Ifirmware/src tests/c/keyvel_test.c -lm -o build/host/keyvel_test
 *
 * 1. the integer curve against the same curve in floating point, every reading x floor x TOUCH x KEY VEL: within 1;
 *    monotonic in the reading; FIXED = KEY VEL; a medium press = KEY VEL; guards (floor at or past the ceiling, 0).
 * 2. the ISR's state machine in a model of hal/fm1_input.h's tick (the order of fm1_input_tick: take the conversion
 *    started last tick, read column p, kv_isr_col(p), latch column n and start its sample unless it is MASTER's or the
 *    battery's slot, debounce p's keys: a press after 2 closed frames, a release after 8 open), synthetic press curves,
 *    and the main loop's kv_keys every millisecond: the reading is there when the note is (WAIT 1), hard louder than
 *    soft, a graze, a bounce of a played key, a fast repeat, a key let go before its reading (the main loop stalled),
 *    the 6 ms fallback, a chord on one column, all 20 analog keys at once, WAIT 2, the floor learned downward only,
 *    the LED deferral once, CONTEXT for the top keys, FIXED and the developer OFF mode (nothing armed), a developer
 *    storm (no reading or note left behind), a storm during real playing.
 * Prints "ok ..." and "FAIL ..." lines; the exit code is the number of failures. */
#define DINGHY_HOST
#define KV_HW 1                                   /* the main loop as on the FM-1: an analog key waits for its reading */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "keyvel.h"

static struct { uint8_t key_vel, vel_curve; } set;
static uint32_t kv_touch(void) { return (set.vel_curve + 1u) & 3u; }          /* = params.c */
static void kv_touch_store(uint32_t t) { set.vel_curve = (uint8_t)((t + 3u) & 3u); }
static uint32_t fm1_ms;
static uint32_t n_midi, last_cc[128];
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2) { n_midi++; if ((st & 0xF0u) == 0xB0u) last_cc[d1 & 127u] = d2; }
#include "keyvel.c"

static int fails;
#define CHECK(c, ...) do { if (c) { printf("ok   "); printf(__VA_ARGS__); printf("\n"); } \
                           else { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

/* ---- 1. the curve */
static double ref_curve(double d, double fl, uint32_t touch, double kvel)
{
    static const double G[3] = {115 / 256.0, 179 / 256.0, 282 / 256.0};
    double s, u, v, um = KV_UMID / 32768.0;
    if (kvel < 1) kvel = 1;
    if (touch >= KV_FIXED) return kvel;
    if (fl < KV_FLOOR_MIN) fl = KV_FLOOR_MIN;
    if (fl >= KV_CEIL) fl = KV_CEIL - 1;
    if (d <= fl) s = 1;
    else if (d >= KV_CEIL) s = 0;
    else s = 1 - log2((d - KV_DBASE) / (fl - KV_DBASE)) / log2((KV_CEIL - KV_DBASE) / (fl - KV_DBASE));
    u = s <= 0 ? 0 : pow(s, G[touch]);
    v = u <= um ? 1 + (kvel - 1) * u / um : kvel + (127 - kvel) * (u - um) / (1 - um);
    return v < 1 ? 1 : v > 127 ? 127 : v;
}
static void test_curve(void)
{
    static const uint32_t FL[] = {217, 300, 389, 450, 600, 800, 990};
    static const uint32_t KVS[] = {1, 40, 64, 96, 110, 127};
    uint32_t t, f, q, d, mono = 1, n = 0;
    double worst = 0;
    for (t = 0; t < KV_FIXED; t++)
        for (f = 0; f < sizeof FL / sizeof FL[0]; f++)
            for (q = 0; q < sizeof KVS / sizeof KVS[0]; q++) {
                uint32_t prev = 1000;
                for (d = 0; d <= 1023u; d++) {
                    uint32_t v = kv_curve(d, FL[f], t, KVS[q]);
                    double e = fabs((double)v - ref_curve(d, FL[f], t, KVS[q]));
                    if (e > worst) worst = e;
                    if (e > 1.0 && n++ < 5) printf("     d %u fl %u touch %u kv %u: %u\n", d, FL[f], t, KVS[q], v);
                    if (v > prev) mono = 0;   /* a higher reading (lighter) never plays louder */
                    prev = v;
                }
            }
    CHECK(n == 0, "the integer curve within 1 of the float one (worst %.2f, %u off)", worst, n);
    CHECK(mono, "the curve never rises with the reading");
    CHECK(kv_curve(300, 450, KV_FIXED, 77) == 77 && kv_curve(0, 0, KV_FIXED, 0) == 1, "FIXED = KEY VEL");
    CHECK(kv_curve(400, 450, KV_MEDIUM, 96) == 127 && kv_curve(1023, 450, KV_MEDIUM, 96) == 1, "the floor 127, the ceiling 1");
    CHECK(kv_curve(600, 1000, KV_MEDIUM, 96) >= 1 && kv_curve(600, 5000, KV_HEAVY, 96) >= 1 && kv_curve(600, 0, KV_LIGHT, 0) >= 1,
          "no divide by zero: floor at / past the ceiling, 0, KEY VEL 0");
    {   /* a medium press: u = KV_UMID plays KEY VEL; LIGHT plays it louder, HEAVY softer */
        uint32_t best = 0, bd = 0;
        for (d = 451; d < 1000u; d++)
            if (kv_curve(d, 450, KV_MEDIUM, 96) == 96u) { best = 96; bd = d; break; }
        CHECK(best == 96u, "a medium press (reading %u) plays KEY VEL 96", bd);
        CHECK(kv_curve(bd, 450, KV_LIGHT, 96) > 96u && kv_curve(bd, 450, KV_HEAVY, 96) < 96u,
              "the same press: LIGHT %u louder, HEAVY %u softer", kv_curve(bd, 450, KV_LIGHT, 96), kv_curve(bd, 450, KV_HEAVY, 96));
        d = kv_curve(bd, 450, KV_MEDIUM, 40);
        CHECK(d >= 39u && d <= 41u, "KEY VEL 40: the same medium press plays %u", d);
    }
    {   /* fm1-velocity's free playing (MEDIUM = its 0.7, 1 + 126 u): u = KV_UMID is its median 59 */
        double u = KV_UMID / 32768.0;
        CHECK(fabs(1 + 126 * u - 59) < 0.5, "KV_UMID = the median 59 of 1 + 126 u (%.2f)", 1 + 126 * u);
    }
}

/* ---- 2. the scan model */
#define NCOL 11u
static const int8_t KEYMAP[6][NCOL] = {           /* hal/fm1_input.h FM1_KEYMAP (test_keyvel.py checks it is) */
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
    { 5, 11,  4, 10,  3,  9,  2,  8, -1, -1, -1},
    {34, 35, 36, 37, 38, 40, 39, 13,  7,  6, 12},
    {23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33},
    { 0,  1, 15, 14, 17, 16, 19, 18, 20, 21, 22},
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
};
static struct {                                   /* a synthetic press per key (two at most) */
    int on;
    uint32_t t_down, t_up, t_down2, t_up2;        /* the contact closes / opens (ticks of 100 us); a second press */
    uint32_t b0, b1;                              /* a bounce: open in [b0, b1) */
    uint32_t land, land2;                         /* its driven reading once landed (each press) */
    uint32_t fall;                                /* the driven samples within this many ticks read 150 higher */
} key[KV_NK];
static uint32_t tick, notes, cnt[KV_NK], col, conv_on, conv_col, conv_row, frame, no_conv, no_main;
static uint32_t lit_led[NCOL], next_dark;         /* the LEDs: lit now, and dark the next frame (sparse) */
static uint32_t n_sampled[KV_NK], n_conv, n_conv_open;
static int closed_now(uint32_t k)
{
    if (!key[k].on) return 0;
    if (tick >= key[k].b0 && tick < key[k].b1) return 0;
    return (tick >= key[k].t_down && tick < key[k].t_up) || (tick >= key[k].t_down2 && tick < key[k].t_up2);
}
static uint32_t reading(uint32_t k)
{
    uint32_t second = tick >= key[k].t_down2 && key[k].t_down2, t0 = second ? key[k].t_down2 : key[k].t_down;
    uint32_t land = second ? key[k].land2 : key[k].land;
    if (!closed_now(k)) return 1023;
    return tick - t0 < key[k].fall ? land + 150u : land;
}
static void model_tick(void)
{
    uint32_t p = col, n = p + 1u == NCOL ? 0u : p + 1u, raw = 0, r;
    int32_t pick;
    if (conv_on) {                                /* take: the sample of the column latched last tick */
        uint32_t id = (uint32_t)KEYMAP[3 + conv_row][conv_col];
        conv_on = 0;
        if (id >= 14u) {
            uint32_t v = reading(id - 14u);
            n_sampled[id - 14u]++; n_conv++;
            if (v == 1023u) n_conv_open++;
            kv_isr_sample(conv_col, conv_row, v);
        }
    }
    for (r = 1; r < 5u; r++)                      /* read column p */
        if (KEYMAP[r][p] >= 14 && closed_now((uint32_t)KEYMAP[r][p] - 14u)) raw |= 1u << r;
    if (!kv_isr_busy(p, n, raw) && (kv_isr_want(p, raw) || kv_isr_pend(n))) {   /* the tick's idle test (fm1_ka_start) */
        printf("FAIL kv_isr_busy 0 with work: column %u / %u\n", p, n); fails++;
    }
    for (r = 0; r < NCOL; r++)                    /* its premise: a pending key is never IDLE */
        if (kv.pend[r] & ~kv.act[r]) { printf("FAIL column %u pending 0x%x not busy 0x%x\n", r, kv.pend[r], kv.act[r]); fails++; }
    if (kv_isr_want(p, raw)) kv_isr_col(p, raw, notes);
    if (n == 0u) frame++;
    if (!(n == 0u && !(frame & 1u)) && !(n == 1u && (frame & 255u) == 1u)) {   /* MASTER's and the battery's slots */
        pick = kv_isr_pick(n, frame & 1u);
        if (pick >= 0 && !(kv_isr_pend(n) >> pick & 1u)) { printf("FAIL pick %d not pending\n", pick); fails++; }
        if (pick >= 0 && !no_conv && !(lit_led[n] && next_dark && kv_isr_defer(n, (uint32_t)pick))) {
            conv_on = 1; conv_col = n; conv_row = (uint32_t)pick;
        }
    }
    col = n;
    for (r = 1; r < 5u; r++) {                    /* debounce column p's note keys (fm1__key) */
        int id = KEYMAP[r][p];
        uint32_t k, closed, on;
        if (id < 14) continue;
        k = (uint32_t)id - 14u; closed = raw >> r & 1u; on = notes >> k & 1u;
        if (closed == on) { cnt[k] = 0; continue; }
        if (++cnt[k] < (on ? 8u : 2u)) continue;
        cnt[k] = 0; notes ^= 1u << k;
    }
    tick++;
}
static uint32_t prev_notes, played[KV_NK], played_vel[KV_NK], played_ms[KV_NK], played_src[KV_NK], nplayed, hanging;
static uint32_t sounding;                         /* the keys whose note plays (started, not yet stopped) */
static void main_pass(void)
{
    uint32_t down = notes & ~prev_notes, up = prev_notes & ~notes, go, k;
    prev_notes = notes;
    go = kv_keys(down, up);
    for (k = 0; k < KV_NK; k++) {
        if (go >> k & 1u) {
            played[k]++; played_vel[k] = kv_vel(k); played_ms[k] = fm1_ms; nplayed++;
            played_src[k] = last_cc[35] >> 3;
            sounding |= 1u << k;
        }
        if (up >> k & 1u) sounding &= ~(1u << k);   /* src_off: after its src_on in the same pass */
    }
}
static void run_ms(uint32_t ms)
{
    uint32_t i, j;
    for (i = 0; i < ms; i++) {
        for (j = 0; j < 10u; j++) model_tick();
        fm1_ms++;
        if (no_main) no_main--;
        else main_pass();
    }
}
static void reset_model(void)
{
    memset(key, 0, sizeof key); memset(cnt, 0, sizeof cnt); memset(played, 0, sizeof played);
    memset(played_src, 0, sizeof played_src);
    memset(n_sampled, 0, sizeof n_sampled); memset((void *)&kv, 0, sizeof kv); memset(&kvm, 0, sizeof kvm);
    memset(lit_led, 0, sizeof lit_led); next_dark = 0;
    notes = prev_notes = 0; nplayed = 0; conv_on = 0; no_conv = 0; no_main = 0; sounding = 0; hanging = 0;
    n_conv = n_conv_open = 0;
    set.key_vel = 96; set.vel_curve = 0;          /* MEDIUM */
    kv_apply();
    kvm.report = 1;                               /* (the per-press report: its src in last_cc[35]) */
}
static void press(uint32_t k, uint32_t land, uint32_t fall, uint32_t after_ms, uint32_t hold_ms)
{
    key[k].on = 1; key[k].land = land; key[k].fall = fall;
    key[k].t_down = tick + after_ms * 10u; key[k].t_up = key[k].t_down + hold_ms * 10u;
    key[k].t_down2 = key[k].t_up2 = 0; key[k].b0 = key[k].b1 = 0;
}
static int all_idle(void)
{
    uint32_t i, c;
    for (i = 0; i < KV_NA; i++) if (kv.st[i] != KS_IDLE) return 0;
    for (c = 0; c < NCOL; c++) if (kv.pend[c] || kv.act[c]) return 0;
    return 1;
}
static uint32_t vel_of(uint32_t d) { return kv_curve(d, KV_FLOOR0, KV_MEDIUM, 96); }

static void test_scan(void)
{
    uint32_t vh, vs, k, t0;
    reset_model();
    press(5, 395, 0, 1, 200);                     /* a hard press (key 5: column 6, PA8) */
    run_ms(260);
    CHECK(played[5] == 1 && played_src[5] == 0, "a press plays once, with its reading (key 5: %u, src %u)", played[5], played_src[5]);
    vh = played_vel[5];
    reset_model();
    press(5, 900, 0, 1, 200);                     /* a soft one */
    t0 = fm1_ms;
    run_ms(260);
    vs = played_vel[5];
    CHECK(played[5] == 1 && vh > vs, "hard %u louder than soft %u", vh, vs);
    CHECK(kvm.n_analog == 1 && kvm.n_fallback == 0, "the soft one had its reading (analog %u fallback %u)", kvm.n_analog, kvm.n_fallback);
    CHECK(played_ms[5] - t0 - 1u <= 4u, "  ... %u ms after the contact (the debounce: ~2.2)", played_ms[5] - t0 - 1u);
    CHECK(all_idle() && n_conv_open == 0, "released: idle again; no sample of an open contact (%u)", n_conv_open);

    reset_model();                                /* WAIT 2: the lowest of two samples (the first still falling) */
    kv.wait = 2;
    press(12, 420, 15, 1, 100);
    run_ms(150);
    CHECK(played[12] == 1 && n_sampled[12] >= 2u && played_vel[12] == vel_of(420),
          "WAIT 2: the lowest of two samples (%u samples, vel %u)", n_sampled[12], played_vel[12]);

    reset_model();                                /* a graze: shorter than the debounce, never a note */
    key[7].on = 1; key[7].land = 500; key[7].t_down = tick + 10; key[7].t_up = key[7].t_down + 8;
    run_ms(50);
    CHECK(played[7] == 0 && !(kv.ready >> 7 & 1u) && all_idle(), "a graze: no note, no reading, idle");
    key[7].t_down = tick + 10; key[7].t_up = key[7].t_down + 15;   /* sampled, then gone before its note */
    run_ms(50);
    CHECK(played[7] == 0 && all_idle(), "a sampled graze: no note, idle (sampled %u)", n_sampled[7]);
    press(7, 700, 0, 1, 100);                     /* the next real press gets its own reading */
    run_ms(150);
    CHECK(played[7] == 1 && played_vel[7] == vel_of(700), "and the next press its own (%u)", played_vel[7]);

    reset_model();                                /* a bounce of a played key before its sample: it waits for the
                                                   * contact again, no open sample, its own reading */
    no_conv = 1;
    press(10, 380, 0, 1, 100);
    run_ms(4);                                    /* the note is on (2 frames); no sample yet */
    key[10].b0 = tick; key[10].b1 = tick + 25;    /* open 2.5 ms (not a release: 8 frames) */
    no_conv = 0;
    run_ms(150);
    CHECK(played[10] == 1 && played_src[10] == 0 && played_vel[10] == vel_of(380),
          "a bounce while waiting: its own reading still (src %u, vel %u; open samples not counted: %u)", played_src[10], played_vel[10], n_conv_open);

    reset_model();                                /* a fast repeat: pressed again just as the note goes off */
    {
        uint32_t gap, bad = 0, two = 0;
        for (gap = 80; gap <= 140u; gap++) {
            reset_model();
            press(3, 400, 0, 1, 30);
            key[3].t_down2 = key[3].t_up + gap; key[3].t_up2 = key[3].t_down2 + 300; key[3].land2 = 900;
            run_ms(120);
            if (played[3] == 2) two++;
            if (played[3] == 2 && (played_src[3] != 0 || played_vel[3] != kv_curve(900, kvm.floor_[3], KV_MEDIUM, 96) || kvm.n_fallback)) bad++;
            if (gap >= 100u && played[3] != 2) bad++;
            if (sounding || !all_idle()) bad++;
            if (getenv("KV_DEBUG")) printf("     gap %u: played %u src %u vel %u fb %u sounding %x idle %d\n", gap, played[3],
                                           played_src[3], played_vel[3], kvm.n_fallback, sounding, all_idle());
        }
        CHECK(bad == 0 && two > 30u, "a fast repeat (gaps 8..14 ms): the second press its own reading (%u twice, %u bad)", two, bad);
    }

    reset_model();                                /* a chord on one column (column 2: notes 11 on PA7, 1 on PA8)
                                                   * plus two more: each its own reading, a column's rows in turn */
    press(11, 400, 0, 1, 100); press(1, 800, 0, 1, 100); press(13, 600, 0, 1, 100); press(3, 450, 0, 1, 100);
    run_ms(160);
    for (k = 0, vh = 0; k < KV_NK; k++) vh += played[k];
    CHECK(vh == 4u && played_vel[11] > played_vel[13] && played_vel[13] > played_vel[1],
          "a chord: 4 notes, each its own velocity (%u %u %u %u)", played_vel[11], played_vel[13], played_vel[1], played_vel[3]);
    CHECK(kvm.n_fallback == 0, "no fallback in the chord (%u)", kvm.n_fallback);
    {
        uint32_t lat = played_ms[1] > played_ms[11] ? played_ms[1] - played_ms[11] : played_ms[11] - played_ms[1];
        CHECK(lat <= 3u, "the column's second key at most 3 ms after its first (%u)", lat);
    }

    reset_model();                                /* every analog key at once (a forearm), and WAIT 2 */
    {
        uint32_t w;
        for (w = 1; w <= 2u; w++) {
            uint32_t late = 0, n = 0;
            reset_model();
            kv.wait = (uint8_t)w;
            t0 = fm1_ms;
            for (k = 0; k < KV_NA; k++) press(k, 300 + k * 30u, 0, 1, 60);
            run_ms(120);
            for (k = 0; k < KV_NA; k++) { n += played[k]; if (played_ms[k] - t0 - 1u > late) late = played_ms[k] - t0 - 1u; }
            CHECK(n == KV_NA && kvm.n_fallback == 0 && kvm.n_analog == KV_NA && all_idle(),
                  "WAIT %u: 20 keys at once, 20 readings, no fallback, the last %u ms after the contact", w, late);
        }
    }

    reset_model();                                /* the top 7 keys: CONTEXT */
    press(2, 400, 0, 1, 50);
    run_ms(100);
    press(22, 0, 0, 1, 50);                       /* C#5..G5: no converter */
    run_ms(100);
    CHECK(played[22] == 1 && played_vel[22] == played_vel[2] && played_src[22] == 3,
          "a top key plays CONTEXT = the last analog velocity (%u)", played_vel[22]);
    press(4, 900, 0, 1, 50);
    run_ms(100);
    press(22, 0, 0, 1, 50);
    run_ms(100);
    {
        uint32_t want = ((played_vel[2] << 8) + (((int32_t)(played_vel[4] << 8) - (int32_t)(played_vel[2] << 8)) / 4) + 128u) >> 8;
        CHECK(played_vel[22] == want, "  ... the recent average (%u after %u, %u)", played_vel[22], played_vel[2], played_vel[4]);
    }
    fm1_ms += KV_CTX_MS + 1u;
    press(22, 0, 0, 1, 50);
    run_ms(100);
    CHECK(played[22] == 3 && played_vel[22] == 96u, "after a pause: KEY VEL (%u)", played_vel[22]);

    reset_model();                                /* the converter never answers (TOUCH on, no sample): KV_WAIT_MS */
    no_conv = 1;
    t0 = fm1_ms;
    press(4, 500, 0, 1, 100);
    run_ms(60);
    CHECK(played[4] == 1 && played_vel[4] == 96u && kvm.n_fallback == 1u && played_src[4] == 1,
          "no reading: it plays at CONTEXT (%u) after the wait (src %u)", played_vel[4], played_src[4]);
    CHECK(played_ms[4] >= t0 + 1u + 2u + KV_WAIT_MS && played_ms[4] <= t0 + 1u + 4u + KV_WAIT_MS,
          "  ... %u ms after the contact (the debounce ~2.2, the wait %u)", played_ms[4] - t0 - 1u, KV_WAIT_MS);
    no_conv = 0;
    run_ms(100);
    CHECK(all_idle(), "  ... and once released: idle (its late reading: dropped at the next landing)");

    reset_model();                                /* let go before its reading reached the main loop (the main loop
                                                   * stalled 20 ms after the press): it plays at once, then stops */
    no_conv = 1;
    press(6, 500, 0, 1, 5);
    run_ms(4);                                    /* the press is seen (down) */
    no_main = 20;
    run_ms(25);
    CHECK(played[6] == 1 && played_src[6] == 2 && played_vel[6] == 96u && !sounding && kvm.wait == 0,
          "let go before its reading: played at once at CONTEXT, then stopped (src %u)", played_src[6]);
    no_conv = 0;
    {
        uint32_t go1, go2;                        /* the same, kv_keys alone */
        reset_model();
        fm1_ms = 1000;
        go1 = kv_keys(1u << 6, 0);
        fm1_ms++;
        go2 = kv_keys(0, 1u << 6);
        CHECK(go1 == 0 && go2 == 1u << 6 && kv_vel(6) == 96u && kvm.wait == 0, "  ... kv_keys alone: down waits, up plays it");
    }
    reset_model();                                /* the reading comes after its fallback: dropped, the next press own */
    no_conv = 1;
    press(14, 300, 0, 1, 100);
    run_ms(20);
    no_conv = 0;
    run_ms(150);
    press(14, 800, 0, 1, 50);
    run_ms(100);
    CHECK(played[14] == 2 && played_vel[14] == vel_of(800) && played_src[14] == 0,
          "a late reading is dropped: the next press plays its own (%u)", played_vel[14]);

    reset_model();                                /* the floor: learned downward only, a step at most */
    {
        uint32_t f1, f2, f3;
        press(8, 300, 0, 1, 50); run_ms(80);
        f1 = kvm.floor_[8];
        press(8, 300, 0, 1, 50); run_ms(80);
        f2 = kvm.floor_[8];
        press(8, 700, 0, 1, 50); run_ms(80);
        f3 = kvm.floor_[8];
        CHECK(f1 == KV_FLOOR0 - KV_FLOOR_STEP && f2 == f1 - KV_FLOOR_STEP && f3 == f2,
              "the floor %u -> %u -> %u (down a step a press, never up)", f1, f2, f3);
        press(8, 190, 0, 1, 50); run_ms(80); press(8, 190, 0, 1, 50); run_ms(80);
        press(8, 190, 0, 1, 50); run_ms(80); press(8, 190, 0, 1, 50); run_ms(80);
        CHECK(kvm.floor_[8] >= KV_FLOOR_MIN && kvm.floor_[9] == 0, "never under KV_FLOOR_MIN (%u), the other keys' own",
              kvm.floor_[8]);
        kv_dev_cc(KV_CC_MODE, KV_DEV_FLOORS);
        CHECK(kvm.floor_[8] == 0 && kv_dev_item(KV_IT_FLOOR0 + 8u) == KV_FLOOR0, "CC 41 32: the floors back to KV_FLOOR0");
    }

    reset_model();                                /* a sparse LED frame: the sample waits one frame, once */
    lit_led[6] = 1; next_dark = 1;
    press(5, 500, 0, 1, 100);
    run_ms(150);
    CHECK(played[5] == 1 && (kv.defer >> 5 & 1u) && played_vel[5] == vel_of(500) && kvm.n_fallback == 0,
          "the LED deferral: once, the reading still comes (%u)", played_vel[5]);

    reset_model();                                /* FIXED: KEY VEL, nothing armed */
    kv_touch_store(KV_FIXED); kv_apply(); set.key_vel = 70;
    t0 = fm1_ms;
    press(9, 300, 0, 1, 50);
    run_ms(80);
    CHECK(played[9] == 1 && played_vel[9] == 70u && kv.act[0] == 0 && n_sampled[9] == 0 && played_ms[9] - t0 - 1u <= 3u,
          "FIXED: KEY VEL at once, no sample");
    reset_model();                                /* the developer OFF mode: nothing armed, every key at once */
    kv_dev_cc(KV_CC_MODE, KV_DEV_OFF);
    t0 = fm1_ms;
    press(9, 300, 0, 1, 50);
    run_ms(80);
    CHECK(!kv.on && played[9] == 1 && played_vel[9] == 96u && n_sampled[9] == 0 && played_ms[9] - t0 - 1u <= 3u
          && kvm.n_fallback == 0, "CC 41 1 (OFF): CONTEXT at once, no sample");
    kv_dev_cc(KV_CC_MODE, KV_DEV_NORMAL);
    CHECK(kv.on, "CC 41 0: armed again");

    reset_model();                                /* TOUCH: the same press, LIGHT louder than MEDIUM than HEAVY */
    {
        uint32_t t, v[3];
        for (t = 0; t < 3u; t++) {
            reset_model();
            kv_touch_store(t); kv_apply();
            press(15, 700, 0, 1, 50);
            run_ms(80);
            v[t] = played_vel[15];
        }
        CHECK(v[0] > v[1] && v[1] > v[2] && v[1] == vel_of(700), "TOUCH LIGHT %u > MEDIUM %u > HEAVY %u", v[0], v[1], v[2]);
    }

    reset_model();                                /* a developer storm: 8 fake landings, no reading left, no note */
    {
        uint32_t i;
        for (i = 0; i < 4u; i++) { kv_isr_storm(); run_ms(9); }
        CHECK(kv.ready == 0 && all_idle() && nplayed == 0 && kv.fake == 0, "a storm: sampled, dropped, idle again (%u samples)",
              n_conv);
    }
    reset_model();                                /* storms while playing: every note plays, none hangs */
    {
        uint32_t i, n = 0;
        for (i = 0; i < 40u; i++) {
            if (i % 3u == 0) kv_isr_storm();
            press(i % KV_NA, 300 + i * 13u, 0, 1, 7);
            run_ms(20);
        }
        run_ms(50);
        for (i = 0; i < KV_NK; i++) n += played[i];
        CHECK(n == 40u && !sounding && all_idle() && kvm.wait == 0, "storms while playing: 40 of 40 played (%u fell back), none hangs",
              kvm.n_fallback);
    }

    reset_model();                                /* random playing: every press plays once, nothing hangs */
    {
        uint32_t seed = 12345u, i, n = 0, want = 0, bad = 0;
        for (i = 0; i < 2000u; i++) {
            uint32_t kk;
            seed = seed * 1103515245u + 12345u;
            kk = (seed >> 16) % KV_NK;
            if (!key[kk].on || tick > key[kk].t_up + 120u) {   /* free again (its release debounced) */
                press(kk, 250 + (seed >> 8) % 700u, (seed >> 4) & 7u, (seed >> 20) & 1u, 3 + (seed >> 24) % 40u);
                want++;
            }
            run_ms(1 + (seed >> 12) % 4u);
        }
        run_ms(100);
        for (i = 0; i < KV_NK; i++) n += played[i];
        if (sounding || !all_idle() || kvm.wait) bad++;
        CHECK(n == want && !bad, "random playing: %u of %u presses played, none hangs (%u fell back, %u open samples)",
              n, want, kvm.n_fallback, n_conv_open);
    }

    set.vel_curve = 0; CHECK(kv_touch() == KV_MEDIUM, "vel_curve 0 (every older record) is MEDIUM");
    kv_touch_store(KV_LIGHT); CHECK(kv_touch() == KV_LIGHT && set.vel_curve == 3u, "LIGHT stored as 3");
    kv_touch_store(KV_FIXED); CHECK(kv_touch() == KV_FIXED && set.vel_curve == 2u, "FIXED stored as 2");
}

int main(void)
{
    uint32_t k, c, r;
    for (c = 0; c < NCOL; c++)                    /* KV_COLKEY = the keymap's analog rows */
        for (r = 0; r < 2u; r++) {
            int id = KEYMAP[3 + r][c];
            k = id >= 14 ? (uint32_t)id - 14u : 0xFFu;
            if (KV_COLKEY[c][r] != k) { printf("FAIL KV_COLKEY[%u][%u] %u, the keymap says %u\n", c, r, KV_COLKEY[c][r], k); fails++; }
        }
    for (k = 0, c = 0; k < 11u * 2u; k++) if (KV_COLKEY[k / 2u][k & 1u] < KV_NK) c |= 1u << KV_COLKEY[k / 2u][k & 1u];
    CHECK(c == KV_ANALOG && KV_ANALOG == (1u << KV_NA) - 1u, "KV_ANALOG is the analog rows' keys");
    test_curve();
    test_scan();
    printf("%d failed\n", fails);
    return fails;
}
