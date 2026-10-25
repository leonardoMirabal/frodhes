/* SPDX-License-Identifier: GPL-3.0-only */
/* Key velocity end to end on the host: the REAL converter scheduler (hal/fm1_adc.h), key rows (hal/fm1_keyadc.h),
 * scan tick (hal/fm1_input.h) and velocity (src/keyvel.c) together, nothing faked but the hardware.
 * tests/py/test_keyvel_e2e.py copies the HAL headers and src/keyvel.h with every register access a call of fake_reg()
 * and the FM-1's sections dropped (as tests/py/test_keyadc.py does), then builds and runs this.
 *
 * The hardware: tests/c/fm1_hw_model.h (port A / B, the 595 chain, the key matrix: V2's model) plus a SARADC here
 * (V1's model in tests/c/adc_sched_test.c: a conversion starts when the enable bit appears, is done after its time,
 * 26 us + 17 us per delay step at divider 7, 211 us at the SDK's settings). Its value is the pin at the start: a row
 * driven low 0, a key closed on the latched column and driven high its pill's reading (force[][], + 40 per DRIVE
 * step), on the pull-up 190, an open contact 1023; MASTER and the battery read master_val / batt_val.
 * The time: a tick every 100 us (fake TIMER4 moved on to the tick's start), fm1_ms every 10 ticks, the main loop's
 * pass (app_main's key part, platform_poll's two reads, the button edges) every PASS ticks unless stalled.
 * Prints "ok ..." / "FAIL ..." lines and "stat NAME VALUE"; the exit code is the number of failures. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static volatile uint32_t *fake_reg(uint32_t a);
#include "keyvel.h"                             /* the FM-1 side (not DINGHY_HOST): the hooks map to fm1_ka_* */

/* ---- what keyvel.c needs from the rest of the firmware (platform.c, params.c) */
static volatile uint32_t fm1_ms;
static struct { uint8_t key_vel, vel_curve; } set;
static uint32_t kv_touch(void) { return (set.vel_curve + 1u) & 3u; }
static void kv_touch_store(uint32_t touch) { set.vel_curve = (uint8_t)((touch + 3u) & 3u); }
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2);

#include "fm1_input.h"
#include "fm1_adc.h"
#include "keyvel.c"
#include "fm1_keyadc.h"

static int fails;
#define CHECK(c, ...) do { if (c) { printf("ok   "); printf(__VA_ARGS__); printf("\n"); } \
                           else { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

static void adc_access(uint32_t a);
#define FM1_MODEL_ON_ACCESS(a) adc_access(a)
#include "fm1_hw_model.h"

/* ---- the SARADC */
#define ADC_CON_A 0x13100u
#define ADC_RES_A 0x13104u
static struct {
    int active, done;
    uint32_t ch, t0, need, val;
    uint32_t stuck_keys, slow_next;             /* key conversions never done / the next one 3x as long */
    uint32_t starts[16], dones[16], aborts[16];
    uint32_t tick_starts, max_tick_starts;
    uint32_t master_val, batt_val;
} A;
static uint32_t dur_ticks(uint32_t cfg)         /* TIMER4 ticks (24 per us) */
{
    static const uint32_t DIV[8] = {1, 6, 12, 24, 48, 72, 96, 128};
    uint32_t ns = (26000u + 17000u * ((cfg >> 12) & 15u)) * DIV[cfg & 7u] / 128u;
    return ns * 24u / 1000u;
}
static uint32_t adc_value(uint32_t ch)
{
    uint32_t pb, st, closed;
    int c;
    if (ch == FM1_ADC_MASTER) return A.master_val;
    if (ch == FM1_ADC_BATT) return A.batt_val;
    if (ch > 1u) return 0;
    pb = ch ? 8u : 7u;
    st = pin_state(pb);
    c = active_col();
    if ((st & 3u) == 1u) return 0;              /* driven low */
    closed = c >= 0 && (pressed[c] >> (3u + ch) & 1u);
    if (!closed) return 1023;
    if ((st & 3u) == 2u) {
        uint32_t v = force[c][ch] + 40u * (st >> 2);
        return v > 1023u ? 1023u : v;
    }
    return 190;                                 /* the pull-up through the pill */
}
static void adc_access(uint32_t a)
{
    uint32_t con;
    if (a != ADC_CON_A) return;
    con = REG(a);
    if (!(con & 0x10u)) {
        if (A.active && !A.done) A.aborts[A.ch]++;
        A.active = 0;
    } else if (!A.active) {
        A.active = 1;
        A.done = 0;
        A.ch = (con >> 8) & 15u;
        A.t0 = now;
        A.need = dur_ticks(con & 0xF00Fu);
        if (A.ch <= 1u) {
            if (A.stuck_keys) A.need = 0x7FFFFFFFu;
            else if (A.slow_next) { A.slow_next--; A.need *= 3u; }
        }
        A.val = adc_value(A.ch);
        A.starts[A.ch]++;
        A.tick_starts++;
    }
    if (A.active && !A.done && now - A.t0 >= A.need) {
        A.done = 1;
        A.dones[A.ch]++;
        REG(a) = con | 0x80u;
        REG(ADC_RES_A) = A.val & 0x3FFu;
    }
}

/* ---- MIDI out (kv_report, the CC 42 replies) */
static uint8_t mlog[4096][3];
static uint32_t mlog_n;
static void midi_send(uint32_t st, uint32_t d1, uint32_t d2)
{
    if (mlog_n < 4096u) { mlog[mlog_n][0] = (uint8_t)st; mlog[mlog_n][1] = (uint8_t)d1; mlog[mlog_n][2] = (uint8_t)d2; }
    mlog_n++;
}

/* ---- the notes the main loop plays */
#define NEV 4096u
static struct { uint32_t k, vel, src, raw, t_on, t_phys, t_down, pass; } ev[NEV];
static uint32_t nev;
static uint32_t prev_keys, on_now, pass_no, dbl_on, off_without_on, onoff_same_pass;
static uint32_t t_phys[KV_NK], t_down[KV_NK], n_up[KV_NK];
static uint32_t btn_edges, btn_rel;
static int32_t last_master = -1, last_batt = -1;
static uint32_t master_t, batt_t;
static uint32_t tick_no, stall, PASS = 5u;

static void main_pass(void)
{
    uint32_t keys = fm1_in.notes, down = keys & ~prev_keys, up = prev_keys & ~keys, go, k, m0, b, rel = 0;
    int32_t m;
    pass_no++;
    prev_keys = keys;
    for (k = 0; k < KV_NK; k++)
        if (down >> k & 1u) t_down[k] = tick_no;
    m0 = mlog_n;
    go = kv_keys(down, up);
    for (k = 0; k < KV_NK; k++) {
        if (go >> k & 1u) {
            if (on_now >> k & 1u) dbl_on++;
            on_now |= 1u << k;
            if (nev < NEV) {
                ev[nev].k = k; ev[nev].vel = kv_vel(k); ev[nev].t_on = tick_no; ev[nev].t_phys = t_phys[k];
                ev[nev].t_down = t_down[k]; ev[nev].pass = pass_no; ev[nev].src = 99; ev[nev].raw = 0;
                for (; m0 + 4u <= mlog_n && m0 + 4u <= 4096u; m0 += 4u)   /* its report: CC 33..36 */
                    if (mlog[m0][1] == 33 && mlog[m0][2] == k) {
                        ev[nev].raw = (uint32_t)mlog[m0 + 1u][2] << 3 | (mlog[m0 + 2u][2] & 7u);
                        ev[nev].src = mlog[m0 + 2u][2] >> 3;
                        m0 += 4u;
                        break;
                    }
            }
            nev++;
        }
        if (up >> k & 1u) {
            n_up[k]++;
            if (!(on_now >> k & 1u)) off_without_on++;
            else if (go >> k & 1u) onoff_same_pass++;
            on_now &= ~(1u << k);
        }
    }
    b = fm1_input_edges(&rel);
    kv_dev_edges(b);
    btn_edges |= b;
    btn_rel |= rel;
    if ((m = fm1_adc_read(FM1_ADC_MASTER)) >= 0) { last_master = m; master_t = tick_no; }
    if ((m = fm1_adc_read(FM1_ADC_BATT)) >= 0) { if (m != last_batt) batt_t = tick_no; last_batt = m; }
}

/* ---- the tick and its checks */
static uint32_t raw_glitch, led_conv;
static uint32_t last_lit_frame[FM1_NCOL], max_gap[FM1_NCOL];
static void tick(void)
{
    uint32_t p = fm1__tick_col, lines;
    int c;
    tick_no++;
    if (now < tick_no * 2400u) now = tick_no * 2400u;
    A.tick_starts = 0;
    fm1_input_tick();
    if (A.tick_starts > A.max_tick_starts) A.max_tick_starts = A.tick_starts;
    if ((fm1_in.raw[p] & 0x1Eu) != (pressed[p] & 0x1Eu)) raw_glitch++;
    c = active_col();
    lines = led_lines();
    if (c >= 0 && lines) {
        if (last_lit_frame[c] && fm1_in.frames - last_lit_frame[c] > max_gap[c])
            max_gap[c] = fm1_in.frames - last_lit_frame[c];
        last_lit_frame[c] = fm1_in.frames;
    }
    if (A.active && !A.done && A.ch <= 1u && lines) led_conv++;
    if (tick_no % 10u == 0u) fm1_ms++;
    if (!stall && tick_no % PASS == 0u) main_pass();
}
static void ticks(uint32_t n) { while (n--) tick(); }
static void ms(uint32_t n) { ticks(n * 10u); }

static void reset_all(void)
{
    memset(mem, 0, sizeof mem);
    memset(&bad, 0, sizeof bad); memset(&A, 0, sizeof A);
    memset(pressed, 0, sizeof pressed); memset(force, 0, sizeof force); memset(rel_low_at, 0, sizeof rel_low_at);
    memset((void *)&fm1_in, 0, sizeof fm1_in); memset((void *)&fm1__kpass, 0, sizeof fm1__kpass);
    memset((void *)&fm1_ka, 0, sizeof fm1_ka);
    memset((void *)&fm1_adc_sched, 0, sizeof fm1_adc_sched);
    memset((void *)&kv, 0, sizeof kv); memset(&kvm, 0, sizeof kvm);
    memset(fm1_led, 0, sizeof fm1_led); memset(fm1_led_dim, 0, sizeof fm1_led_dim);
    memset(fm1_led_bg, 0, sizeof fm1_led_bg);
    memset(last_lit_frame, 0, sizeof last_lit_frame); memset(max_gap, 0, sizeof max_gap);
    memset(ev, 0, sizeof ev); memset(t_phys, 0, sizeof t_phys); memset(t_down, 0, sizeof t_down); memset(n_up, 0, sizeof n_up);
    fm1_led_duty[0] = fm1_led_duty[1] = 16u;
    sr = latch = prev_out_a = prev_dir_a = inited = 0;
    raw_glitch = led_conv = 0;
    nev = prev_keys = on_now = pass_no = dbl_on = off_without_on = onoff_same_pass = 0;
    btn_edges = btn_rel = 0;
    mlog_n = 0;
    last_master = last_batt = -1;
    master_t = batt_t = 0;
    stall = 0;
    PASS = 5u;
    fm1__tick_col = 0; fm1__led_phase = 0;
    fm1_input_init();
    inited = 1;
    fm1_led_build();
    A.master_val = 512;
    A.batt_val = 573;
    set.key_vel = 64;
    set.vel_curve = 0;                          /* MEDIUM */
    kv_apply();
    kv_dev_cc(KV_CC_MODE, KV_DEV_REPORT_ON);
    ms(20);                                     /* the scheduler running, the main loop's MASTER read in step */
}

/* note k's place in the matrix */
static void key_cell(uint32_t note, uint32_t *col, uint32_t *row)
{
    uint32_t c, r;
    for (r = 1; r < 5u; r++)
        for (c = 0; c < FM1_NCOL; c++)
            if (FM1_KEYMAP[r][c] == (int)(note + 14u)) { *col = c; *row = r; return; }
    *col = *row = 0xFFu;
}
static void key(uint32_t note, int on, uint32_t f)
{
    uint32_t c, r;
    key_cell(note, &c, &r);
    if (c == 0xFFu) return;
    if (on) {
        if (r >= 3u) force[c][r - 3u] = (uint16_t)f;
        pressed[c] |= (uint8_t)(1u << r);
        t_phys[note] = tick_no;
    } else {
        pressed[c] &= (uint8_t)~(1u << r);
    }
}
static void button(uint32_t id, int on)        /* OCT- 0 / OCT+ 1: PA8, columns 0 / 1 */
{
    if (on) pressed[id] |= 1u << 4;
    else pressed[id] &= (uint8_t)~(1u << 4);
}
static int last_ev(uint32_t k)                  /* the last note-on of key k, -1 none */
{
    int i;
    for (i = (int)(nev < NEV ? nev : NEV) - 1; i >= 0; i--)
        if (ev[i].k == k) return i;
    return -1;
}
static int count_ev(uint32_t k)
{
    uint32_t i, n = 0;
    for (i = 0; i < nev && i < NEV; i++) n += ev[i].k == k;
    return (int)n;
}
/* press and hold key k at force f for hold ms, then let go and wait rest ms: its note-on, -1 none */
static int play(uint32_t k, uint32_t f, uint32_t hold, uint32_t rest)
{
    key(k, 1, f);
    ms(hold);
    key(k, 0, 0);
    ms(rest);
    return last_ev(k);
}
static int clean(const char *what)
{
    int ok = !bad.read_driven && !bad.die_off && !bad.pa6_driven && !raw_glitch && !led_conv && A.max_tick_starts <= 1u &&
             !btn_edges && !btn_rel && !dbl_on && !off_without_on && !A.aborts[FM1_ADC_MASTER] &&
             !A.aborts[FM1_ADC_BATT] && !fm1_adc_sched.batt_to;
    CHECK(ok, "%s: rules (rows read driven %u, input buffer off %u, PA6 driven %u, raw glitches %u, LEDs lit while a "
          "key converts %u, most conversions in a tick %u, button edges 0x%x / 0x%x, double note-ons %u, offs without "
          "an on %u, MASTER / battery aborted %u / %u, battery timeouts %u)", what, bad.read_driven, bad.die_off,
          bad.pa6_driven, raw_glitch, led_conv, A.max_tick_starts, btn_edges, btn_rel, dbl_on, off_without_on,
          A.aborts[FM1_ADC_MASTER], A.aborts[FM1_ADC_BATT], fm1_adc_sched.batt_to);
    return ok;
}
static uint32_t lat_ms(int i) { return i < 0 ? 999u : (ev[i].t_on - ev[i].t_phys + 9u) / 10u; }

/* the CC 42 replies since mlog index m0: item -> value (14 bits), the count of each item */
static uint32_t q_val[KV_IT_N], q_cnt[KV_IT_N];
static void query_all(void)
{
    uint32_t g, i, m0 = mlog_n;
    memset(q_val, 0, sizeof q_val); memset(q_cnt, 0, sizeof q_cnt);
    for (g = 0; g < KV_IT_N / 8u; g++) kv_dev_cc(KV_CC_QUERY, g);
    for (i = m0; i + 3u <= mlog_n && i + 3u <= 4096u; i += 3u) {
        uint32_t it = mlog[i][2];
        if (mlog[i][1] != KV_CC_ITEM || mlog[i + 1u][1] != KV_CC_HI || mlog[i + 2u][1] != KV_CC_LO || it >= KV_IT_N) {
            q_cnt[0] += 1000u;                  /* (a malformed reply) */
            break;
        }
        q_val[it] = (uint32_t)mlog[i + 1u][2] << 7 | mlog[i + 2u][2];
        q_cnt[it]++;
    }
}

int main(void)
{
    static const uint32_t F[6] = {250, 450, 600, 750, 900, 990};
    uint32_t i, k, t, n, worst, v[6];
    int e;

    /* 1. soft / hard: one key, six landings from firm (250) to the lightest (990): the velocity is the curve's, falls
     * with the reading, comes from the reading (src 0) within 2 frames' debounce + a few ms */
    worst = 0;
    for (i = 0; i < 6u; i++) {
        reset_all();
        e = play(5, F[i], 40, 30);
        v[i] = e >= 0 ? ev[e].vel : 0;
        CHECK(e >= 0 && ev[e].src == 0 && ev[e].raw == F[i] && v[i] == kv_curve(F[i], KV_FLOOR0, KV_MEDIUM, 64),
              "soft / hard: reading %u -> velocity %u (curve %u), src %u, raw %u, latency %u ms", F[i], v[i],
              kv_curve(F[i], KV_FLOOR0, KV_MEDIUM, 64), e >= 0 ? ev[e].src : 99, e >= 0 ? ev[e].raw : 0, lat_ms(e));
        if (lat_ms(e) > worst) worst = lat_ms(e);
        clean("soft / hard");
    }
    CHECK(v[0] == 127 && v[1] == 127 && v[5] <= 10 && v[2] > v[3] && v[3] > v[4] && v[4] > v[5],
          "soft / hard: firm 127 (%u %u), descending %u > %u > %u > %u, the lightest <= 10", v[0], v[1], v[2], v[3],
          v[4], v[5]);
    CHECK(worst <= 4u, "soft / hard: the note-on within 4 ms of the contact (worst %u ms; the debounce is 2 frames)",
          worst);
    printf("stat latency_ms %u\n", worst);

    /* every analog key, its column / row in the real matrix: a reading (KV_COLKEY agrees with FM1_KEYMAP); the top 7:
     * CONTEXT (src 3), the average of the analog keys just played */
    reset_all();
    for (n = 0, k = 0; k < KV_NA; k++) {
        e = play(k, 600 + 10u * k, 30, 20);
        n += e >= 0 && ev[e].src == 0 && ev[e].raw == 600 + 10u * k;
    }
    CHECK(n == KV_NA, "every analog key gets its own reading (%u of 20)", n);
    for (n = 0, k = KV_NA; k < KV_NK; k++) {
        e = play(k, 0, 30, 20);
        n += e >= 0 && ev[e].src == 3 && ev[e].vel == kv_ctx();
    }
    CHECK(n == 7u && kv_ctx() != 64u, "the top 7 play CONTEXT (%u of 7, at %u)", n, kv_ctx());
    CHECK(A.starts[0] + A.starts[1] == KV_NA && A.dones[0] + A.dones[1] == KV_NA,
          "variant B: one conversion per landing (%u started, %u done for 20 landings)", A.starts[0] + A.starts[1],
          A.dones[0] + A.dones[1]);
    clean("every key");

    /* TOUCH: LIGHT > MEDIUM > HEAVY at the same reading; FIXED plays KEY VEL at once and converts nothing */
    for (t = 0; t < 4u; t++) {
        reset_all();
        kv_touch_store(t);
        kv_apply();
        set.key_vel = 80;
        e = play(9, 700, 30, 20);
        v[t] = e >= 0 ? ev[e].vel : 0;
        if (t < 3u)
            CHECK(e >= 0 && ev[e].src == 0 && v[t] == kv_curve(700, KV_FLOOR0, t, 80), "TOUCH %s: reading 700 -> %u",
                  KV_TOUCH_NAME[t], v[t]);
        else
            CHECK(e >= 0 && ev[e].src == 4 && v[t] == 80 && A.starts[0] + A.starts[1] == 0 &&
                  ev[e].t_on - ev[e].t_down <= PASS, "TOUCH FIXED: KEY VEL (%u), no conversion (%u), at once", v[t],
                  A.starts[0] + A.starts[1]);
        clean(KV_TOUCH_NAME[t]);
    }
    CHECK(v[0] > v[1] && v[1] > v[2], "TOUCH: LIGHT %u > MEDIUM %u > HEAVY %u", v[0], v[1], v[2]);

    /* 2. held keys debounce: a chord of 8 (both rows of column 5, columns 0 and 1 beside MASTER's / the battery's
     * slots, a top key) held 300 ms: one note-on each with its reading, none off until let go, the rows clean */
    reset_all();
    {
        static const uint32_t CH[8] = {9, 10, 11, 1, 14, 2, 19, 22};
        for (i = 0; i < 8u; i++) key(CH[i], 1, 300 + 70u * i);
        ms(300);
        for (n = 0, i = 0; i < 8u; i++) {
            e = last_ev(CH[i]);
            n += count_ev(CH[i]) == 1 && e >= 0 && (CH[i] >= KV_NA ? ev[e].src == 3 : ev[e].src == 0 &&
                 ev[e].raw == 300 + 70u * i);
        }
        CHECK(n == 8u && on_now == ((1u << 9) | (1u << 10) | (1u << 11) | (1u << 1) | (1u << 14) | (1u << 2) |
              (1u << 19) | (1u << 22)), "chord held 300 ms: one note-on each with its reading (%u of 8), all held "
              "(0x%x)", n, on_now);
        worst = 0;
        for (i = 0; i < 8u; i++) if (lat_ms(last_ev(CH[i])) > worst) worst = lat_ms(last_ev(CH[i]));
        CHECK(worst <= 5u, "chord: every note within 5 ms (worst %u)", worst);
        for (i = 0; i < 8u; i++) key(CH[i], 0, 0);
        ms(30);
        CHECK(on_now == 0 && kvm.wait == 0, "chord let go: every note off (0x%x), nothing waiting", on_now);
        CHECK(kvm.n_fallback == 0 && fm1_adc_sched.timeouts == 0, "chord: no fallback (%u), no timeout (%u)",
              kvm.n_fallback, fm1_adc_sched.timeouts);
    }
    clean("held chord");

    /* 3. MASTER and the battery keep their staleness bounds (MASTER: a slot every 2 frames; the battery every 256),
     * idle and while chords are mashed; MASTER reaches 0 and 1023 */
    for (t = 0; t < 2u; t++) {
        uint32_t mw = 0, bw = 0, step;
        reset_all();
        for (step = 0; step < 120u; step++) {
            uint32_t t0 = tick_no, val = step % 3u == 0u ? 0u : step % 3u == 1u ? 1023u : 300u + step;
            A.master_val = val;
            if (t && step % 2u == 0u)
                for (i = 0; i < 10u; i++) key(i * 2u, (step & 2u) != 0, 300 + 50u * i);   /* chords on / off */
            while (last_master != (int32_t)val && tick_no - t0 < 200u) tick();
            if (tick_no - t0 > mw) mw = tick_no - t0;
            ticks(7u + step % 13u);
        }
        for (step = 0; step < 4u; step++) {
            uint32_t t0 = tick_no;
            A.batt_val = 560u + step;
            while (last_batt != (int32_t)(560u + step) && tick_no - t0 < 6000u) tick();
            if (tick_no - t0 > bw) bw = tick_no - t0;
            if (t) for (i = 0; i < 10u; i++) key(i * 2u, step & 1u, 400);
        }
        for (i = 0; i < 10u; i++) key(i * 2u, 0, 0);
        ms(30);
        CHECK(mw <= 2u * 11u + 1u + PASS, "%s: MASTER fresh within %u ticks (bound 2 frames + 1 tick + a pass: %u)",
              t ? "mashing" : "idle", mw, 2u * 11u + 1u + PASS);
        CHECK(bw <= 256u * 11u + 11u + 3u + PASS, "%s: the battery fresh within %u ticks (bound 256 frames + its "
              "conversion + a pass: %u)", t ? "mashing" : "idle", bw, 256u * 11u + 11u + 3u + PASS);
        CHECK(fm1_adc_sched.timeouts == 0 && A.aborts[FM1_ADC_MASTER] == 0 && A.aborts[FM1_ADC_BATT] == 0,
              "%s: no timeout (%u), MASTER / battery never aborted", t ? "mashing" : "idle", fm1_adc_sched.timeouts);
        printf("stat master_stale_ticks_%s %u\nstat batt_stale_ticks_%s %u\n", t ? "mash" : "idle", mw,
               t ? "mash" : "idle", bw);
        CHECK(on_now == 0 && kvm.wait == 0, "%s: nothing hangs", t ? "mashing" : "idle");
        clean(t ? "MASTER / battery, mashing" : "MASTER / battery, idle");
    }

    /* 4. OCT- / OCT+ never read pressed, in every mode, while keys land; and a held OCT- reads as one press in
     * STRESS and SELFTEST (its row driven and restored before every read) */
    for (t = 0; t < KV_DEV_NMODE; t++) {
        reset_all();
        kv_dev_cc(KV_CC_MODE, t);
        for (i = 0; i < 30u; i++) { key(i % 20u, 1, 350 + 20u * i); ms(4); key(i % 20u, 0, 0); ms(11); }
        ms(30);
        CHECK(fm1_in.buttons == 0 && fm1_in.pressed == 0 && kvm.n_buttons == 0 && on_now == 0 && kvm.wait == 0,
              "mode %u: OCT- / OCT+ never pressed (item 13: %u), no note hangs", t, kvm.n_buttons);
        clean("mode");
        button(FM1_BTN_OCT_DOWN, 1);
        for (i = 0; i < 10u; i++) { key(i, 1, 400); ms(4); key(i, 0, 0); ms(11); }
        n = btn_edges;
        button(FM1_BTN_OCT_DOWN, 0);
        ms(30);
        CHECK(n == 1u && btn_edges == 1u && btn_rel == 1u && raw_glitch == 0, "mode %u: OCT- held: one press, one "
              "release (0x%x 0x%x), the rows clean (%u)", t, btn_edges, btn_rel, raw_glitch);
    }

    /* 5. BRIGHTNESS 1: every key lit 1 frame in 16; landings at every phase never swallow a lit frame (a deferral,
     * once per landing) and still get their reading */
    reset_all();
    for (i = 0; i < FM1_NCOL; i++) fm1_led[i] = 0x1Eu;
    fm1_led_duty[0] = fm1_led_duty[1] = 1u;
    fm1_led_build();
    ms(40);
    memset(max_gap, 0, sizeof max_gap);
    for (n = 0, i = 0; i < 96u; i++) {
        ticks(i % 11u);
        e = play(i % 20u, 500, 5, 12u + i % 5u);
        n += e >= 0 && ev[e].src == 0;
    }
    for (worst = 0, i = 0; i < FM1_NCOL; i++) if (max_gap[i] > worst) worst = max_gap[i];
    CHECK(worst <= 16u && n == 96u, "BRIGHTNESS 1: the longest gap between lit frames %u (16: none swallowed), %u of "
          "96 landings with their reading", worst, n);
    clean("BRIGHTNESS 1");

    /* 6. the fallback: a converter that never finishes a key conversion: each press plays after KV_WAIT_MS at CONTEXT
     * (src 1), counted; MASTER untouched */
    reset_all();
    e = play(3, 250, 30, 20);                   /* a firm press first: CONTEXT 127 */
    CHECK(e >= 0 && ev[e].vel == 127 && ev[e].src == 0, "fallback: a firm press first (%u)", e >= 0 ? ev[e].vel : 0);
    A.stuck_keys = 1;
    e = play(7, 900, 30, 20);
    t = e >= 0 ? ev[e].t_on - ev[e].t_down : 0;
    CHECK(e >= 0 && ev[e].src == 1 && ev[e].vel == 127 && t >= (KV_WAIT_MS - 1u) * 10u && t <= KV_WAIT_MS * 10u + 10u + PASS,
          "fallback: after %u ticks (KV_WAIT_MS on fm1_ms: 5 .. 7.5 ms) at CONTEXT %u, src %u", t, e >= 0 ? ev[e].vel : 0,
          e >= 0 ? ev[e].src : 99);
    CHECK(kvm.n_fallback == 1 && fm1_adc_sched.timeouts > 0 && last_master == 512,
          "fallback: counted (%u), the lost conversions counted (%u), MASTER still read (%d)", kvm.n_fallback,
          fm1_adc_sched.timeouts, last_master);
    clean("fallback");

    /* 7. a lost conversion is picked again: the key still gets its reading */
    reset_all();
    A.slow_next = 1;
    e = play(12, 640, 30, 20);
    CHECK(e >= 0 && ev[e].src == 0 && ev[e].raw == 640 && fm1_adc_sched.timeouts == 1 && A.starts[0] + A.starts[1] == 2,
          "lost: retried (%u key conversions, %u timeout), reading %u, src %u", A.starts[0] + A.starts[1],
          fm1_adc_sched.timeouts, e >= 0 ? ev[e].raw : 0, e >= 0 ? ev[e].src : 99);
    clean("lost");

    /* 8. released before its velocity (the main loop stalled while the converter gives nothing): the press plays at
     * CONTEXT in the pass that sees its release, before the release (src 2), never dropped, never left hanging */
    reset_all();
    A.stuck_keys = 1;
    key(6, 1, 500);
    for (t = 0; t < 200u && !(prev_keys >> 6 & 1u); t++) tick(); /* the main loop has the press, waiting for its reading */
    stall = 1;
    ms(3);
    key(6, 0, 0);
    ms(25);                                     /* released and debounced (8 frames) while the main loop stalls */
    stall = 0;
    while (!(pass_no && tick_no % PASS == PASS - 1u)) tick();
    tick();                                     /* one pass */
    e = last_ev(6);
    CHECK(e >= 0 && ev[e].src == 2 && ev[e].vel == 64 && onoff_same_pass == 1 && on_now == 0 && kvm.wait == 0,
          "let go first: played at CONTEXT %u (src %u), its note-on before its note-off in one pass (%u), nothing "
          "hangs", e >= 0 ? ev[e].vel : 0, e >= 0 ? ev[e].src : 99, onoff_same_pass);
    clean("let go first");

    /* 9. SELFTEST drops the keys' samples: they fall back (src 1); a key held through the switch back to NORMAL gets
     * sampled late, and its next press reads its own landing (no stale reading) */
    reset_all();
    kv_dev_cc(KV_CC_MODE, KV_DEV_SELFTEST);
    e = play(8, 300, 30, 20);
    CHECK(e >= 0 && ev[e].src == 1, "SELFTEST: a press falls back (src %u)", e >= 0 ? ev[e].src : 99);
    key(15, 1, 280);
    ms(30);
    kv_dev_cc(KV_CC_QUERY, 1);
    query_all();
    CHECK(q_val[KV_IT_ST_PA7] == 0 && q_val[KV_IT_ST_PA8] == 0 && q_val[KV_IT_ST_N] > 300u,
          "SELFTEST: every driven-low row reads 0 (PA7 %u, PA8 %u, %u readings)", q_val[KV_IT_ST_PA7],
          q_val[KV_IT_ST_PA8], q_val[KV_IT_ST_N]);
    kv_dev_cc(KV_CC_MODE, KV_DEV_NORMAL);
    ms(30);
    key(15, 0, 0);
    ms(30);
    e = play(15, 880, 30, 20);
    CHECK(e >= 0 && ev[e].src == 0 && ev[e].raw == 880 && count_ev(15) == 2, "after SELFTEST: the held key's next "
          "press reads its own landing (%u, src %u)", e >= 0 ? ev[e].raw : 0, e >= 0 ? ev[e].src : 99);
    clean("SELFTEST");

    /* 10. STRESS and STORM: real presses still get their readings, the storm plays nothing */
    reset_all();
    kv_dev_cc(KV_CC_MODE, KV_DEV_STRESS);
    e = play(4, 520, 30, 20);
    CHECK(e >= 0 && ev[e].src == 0 && ev[e].raw == 520 && A.starts[0] + A.starts[1] > 400u,
          "STRESS: %u conversions in ~0.07 s, a press still reads its landing (%u)", A.starts[0] + A.starts[1],
          e >= 0 ? ev[e].raw : 0);
    clean("STRESS");
    reset_all();
    kv_dev_cc(KV_CC_MODE, KV_DEV_STORM);
    ms(200);
    CHECK(nev == 0 && on_now == 0 && kv.ready == 0, "STORM: fake landings play nothing (%u notes)", nev);
    e = play(4, 520, 30, 20);
    CHECK(e >= 0 && ev[e].src == 0 && ev[e].raw == 520, "STORM: a real press reads its landing (%u)",
          e >= 0 ? ev[e].raw : 0);
    clean("STORM");

    /* 11. the developer protocol: CC 41 0..4 and 16..20 reach the key rows, the rest does not; CC 42 answers every
     * item 0..63 exactly once, each from its owner; a mode command clears V1's counters at once */
    reset_all();
    for (t = 0; t < KV_DEV_NMODE; t++) {
        kv_dev_cc(KV_CC_MODE, t);
        query_all();
        CHECK(fm1_ka.mode == t && q_val[KV_IT_MODE] == t && kvm.mode == t, "CC 41 %u: the mode (rows %u, item %u)", t,
              fm1_ka.mode, q_val[KV_IT_MODE]);
    }
    kv_dev_cc(KV_CC_MODE, KV_DEV_NORMAL);
    for (t = 0; t < 4u; t++) {
        kv_dev_cc(KV_CC_MODE, KV_DEV_DRIVE1 + t);
        query_all();
        CHECK(q_val[KV_IT_DRIVE] == t + 1u && fm1_ka.mode == 0, "CC 41 %u: DRIVE %u", KV_DEV_DRIVE1 + t,
              q_val[KV_IT_DRIVE]);
    }
    kv_dev_cc(KV_CC_MODE, KV_DEV_PULL);
    query_all();
    CHECK(q_val[KV_IT_DRIVE] == 0 && fm1_ka.pull == 1, "CC 41 20: PULL (item 1 = 0)");
    kv_dev_cc(KV_CC_MODE, KV_DEV_DRIVE1);
    for (n = 0, t = 0; t < 128u; t++) {
        uint8_t m0 = fm1_ka.mode, s0 = fm1_ka.strength, p0 = fm1_ka.pull, md = kvm.mode, w0 = kv.wait, r0 = kvm.report;
        if (t < KV_DEV_NMODE || (t >= KV_DEV_DRIVE1 && t <= KV_DEV_PULL) || t == KV_DEV_REPORT_ON ||
            t == KV_DEV_REPORT_OFF || t == KV_DEV_WAIT1 || t == KV_DEV_WAIT1 + 1u || t == KV_DEV_FLOORS)
            continue;
        kv_dev_cc(KV_CC_MODE, t);
        n += fm1_ka.mode != m0 || fm1_ka.strength != s0 || fm1_ka.pull != p0 || kvm.mode != md || kv.wait != w0 ||
             kvm.report != r0;
    }
    CHECK(n == 0, "CC 41: the undefined values change nothing (%u did)", n);
    ms(300);                                    /* conversions to count, a battery slot */
    query_all();
    for (n = 0, i = 0; i < KV_IT_N; i++) n += q_cnt[i] != 1u;
    CHECK(n == 0, "CC 42: items 0..63 answered once each (%u not)", n);
    CHECK(q_val[KV_IT_MASTER] == 512 && q_val[KV_IT_BATT] == 573 && q_val[KV_IT_TOUCH] == KV_MEDIUM &&
          q_val[KV_IT_WAIT] == 1 && q_val[KV_IT_DRIVE] == 1 && q_val[KV_IT_CONVS] > 100u &&
          q_val[KV_IT_CONVS] == fm1_adc_sched.convs && q_val[KV_IT_FRAMES] == (fm1_in.frames & 0x3FFFu) &&
          q_val[KV_IT_FLOOR0] == KV_FLOOR0 && q_val[KV_IT_FLOOR0 + 19u] == KV_FLOOR0 && q_val[18] == 0 &&
          q_val[52] == 0 && q_val[63] == 0, "CC 42: each from its owner (MASTER %u, battery %u, TOUCH %u, WAIT %u, "
          "DRIVE %u, convs %u, frames %u, floors %u)", q_val[KV_IT_MASTER], q_val[KV_IT_BATT], q_val[KV_IT_TOUCH],
          q_val[KV_IT_WAIT], q_val[KV_IT_DRIVE], q_val[KV_IT_CONVS], q_val[KV_IT_FRAMES], q_val[KV_IT_FLOOR0]);
    A.stuck_keys = 1;
    play(2, 500, 20, 10);
    A.stuck_keys = 0;
    CHECK(fm1_adc_sched.timeouts > 0, "a timeout to clear (%u)", fm1_adc_sched.timeouts);
    kv_dev_cc(KV_CC_MODE, KV_DEV_NORMAL);
    CHECK(fm1_adc_sched.convs == 0 && fm1_adc_sched.timeouts == 0 && fm1_adc_sched.batt_to == 0 && kvm.n_notes == 0 &&
          kvm.n_fallback == 0, "CC 41 0: V1's and V3's counters cleared at once");

    /* 12. a slow main loop (a pass every 4 ms): readings still arrive, nothing falls back that had one */
    reset_all();
    PASS = 40u;
    for (n = 0, i = 0; i < 20u; i++) {
        e = play(i, 400 + 25u * i, 30, 20);
        n += e >= 0 && ev[e].src == 0;
    }
    CHECK(n == 20u, "a pass every 4 ms: %u of 20 presses with their reading", n);
    clean("slow main loop");

    /* 13. one press, one note (the double-note report on the scan tick's one-bit shift, 1bc473e): every key pressed
     * once at every tick phase of the 11-column scan (so its landing meets column 1's tick, its 15-bit pre-shift and
     * a running conversion in every order), alone and with a held chord on the analog rows that keeps the converter
     * busy: exactly one note-on and, after the release, exactly one note-off, none between */
    {
        uint32_t ph, ch, bad_on = 0, bad_off = 0, presses = 0, n0[KV_NK], u0[KV_NK];
        for (ch = 0; ch < 2u; ch++) {
            reset_all();
            if (ch) { key(9, 1, 400); key(10, 1, 500); key(2, 1, 450); ms(20); }
            for (k = 0; k < KV_NK; k++) {
                if (ch && (k == 9u || k == 10u || k == 2u)) continue;
                for (ph = 0; ph < 2u * FM1_NCOL; ph++) {
                    ticks(ph);
                    for (i = 0; i < KV_NK; i++) { n0[i] = (uint32_t)count_ev(i); u0[i] = n_up[i]; }
                    key(k, 1, 300 + 30u * ph);
                    ms(40);
                    if ((uint32_t)count_ev(k) - n0[k] != 1u || n_up[k] != u0[k]) bad_on++;
                    key(k, 0, 0);
                    ms(30);
                    if (n_up[k] - u0[k] != 1u || (uint32_t)count_ev(k) - n0[k] != 1u) bad_off++;
                    for (i = 0; i < KV_NK; i++)
                        if (i != k && ((uint32_t)count_ev(i) != n0[i] || n_up[i] != u0[i])) bad_on++;
                    presses++;
                }
            }
        }
        CHECK(bad_on == 0 && bad_off == 0 && nev < NEV, "one press, one note: %u presses at every scan phase, alone "
              "and over a held chord: %u with not exactly one note-on (or another key moved), %u not exactly one off",
              presses, bad_on, bad_off);
        clean("one press, one note");
    }

    printf("%s (%d failed)\n", fails ? "FAIL" : "ok", fails);
    return fails;
}
