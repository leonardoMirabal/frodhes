/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes tempo and scales (IMPLEMENTATION-v3 §11), main loop only.
 *
 * Tempo: bpm10 (40.0 .. 240.0 BPM) in the settings; PLAY taps it (the last 4 taps; from the 3rd; 2 s resets);
 * one sync table in 1/96-quarter units drives the delay time and the tremolo rate (published as volatiles the
 * ISR reads). Time base: fm1_ms (32-bit, wraps after 49 days; compared by difference).
 * Scales: OFF / SNAP (the nearest note of the scale) / DEG (white keys walk the scale degrees from C4 = degree 0,
 * black keys silent), Felucca's masks (seq.c SCALE_MASK, GPL-3.0). */

static const uint16_t SYNC_UNITS[15] = {0, 12, 16, 24, 32, 36, 48, 64, 72, 96, 144, 192, 384, 768, 1536};
static uint32_t tap_t[4], tap_n, beat_t0, beat_ms = 500;
static uint32_t beat_k0;                     /* the pulse count (24 a beat) at beat_t0: the bars for the LFOs' lock */
static uint32_t beat_smp = 22050;                                            /* samples a beat (the clock's, exact) */
static void ui_mark(uint32_t e);                                              /* ui.c */
static int eng_idle(void);                                                    /* notes.c */

static uint32_t beat_samples(void) { return beat_smp; }   /* 26460000 / bpm10 (fs * 60 * 10 / bpm10), or the clock's */

/* the synced LFOs (tremolo, chorus, phaser, the filter's LFO, the vibraphone's motor): their nominal increments, which
 * the clock's phase lock (tempo_lock_poll) corrects; 0 = not synced */
enum { LK_TW, LK_CP, LK_PH, LK_LFO, LK_MOT, LK_N };
static uint32_t lk_nom[LK_N];

static void tempo_sync_publish(void)
{
    uint32_t d = PV(P_DL_SYNC), t = PV(P_TW_SYNC);
    fx_dly_sync_samples = d ? beat_samples() * SYNC_UNITS[d] / 96u : 0u;
    if (t) {
        uint32_t s = beat_samples() * SYNC_UNITS[t] / 96u;
        fx_tw_sync_inc = 0xFFFFFFFFu / (s ? s : 1u);                         /* one LFO cycle per division */
    } else
        fx_tw_sync_inc = 0;
    lk_nom[LK_TW] = fx_tw_sync_inc;
    t = PV(P_MOT_SYNC);                                                      /* the vibraphone's motor: its own SYNC */
    if (t) {
        uint32_t s = beat_samples() * SYNC_UNITS[t] / 96u;
        mot_sync_inc = 0xFFFFFFFFu / (s ? s : 1u);
    } else
        mot_sync_inc = 0;
    lk_nom[LK_MOT] = mot_sync_inc;
    mot_rate_apply();
    {
        uint32_t c = PV(P_CP_SYNC), s = c ? beat_samples() * SYNC_UNITS[c] / 96u : 0u;
        fx_cp_sync_inc = c ? 0xFFFFFFFFu / (s ? s : 1u) : 0u;
        c = PV(P_PH_SYNC); s = c ? beat_samples() * SYNC_UNITS[c] / 96u : 0u;
        fx_ph_sync_inc = c ? 0xFFFFFFFFu / (s ? s : 1u) : 0u;
        c = PV(P_LFO_SYNC); s = c ? beat_samples() * SYNC_UNITS[c] / 96u : 0u;        /* the filter's LFO (0.9.3) */
        fx_lfo_sync_inc = c ? 0xFFFFFFFFu / (s ? s : 1u) : 0u;
        lk_nom[LK_CP] = fx_cp_sync_inc; lk_nom[LK_PH] = fx_ph_sync_inc; lk_nom[LK_LFO] = fx_lfo_sync_inc;
    }
}

static uint8_t clk_owns;                         /* the tempo is the clock's (tempo_bpm10 shows clk_bpm10, not the setting) */
static uint16_t clk_bpm10;
static uint32_t tempo_bpm10(void) { return clk_owns ? clk_bpm10 : set.bpm10; }   /* the BPM shown (ui.c) */
static int clk_follows(void);
static void tempo_set10(uint32_t b)
{
    if (clk_follows()) { ui_mark(0); ui_mark(10); return; }   /* a clock leads: its tempo stays (no jump, no save) */
    if ((int32_t)b < 400) b = 400;
    if (b > 2400) b = 2400;
    if (clk_owns) { clk_owns = 0; beat_ms = 0; ui_mark(0); ui_mark(10); }   /* the FM-1's own tempo again */
    if (b == set.bpm10 && beat_ms == 600000u / b) return;
    set.bpm10 = (uint16_t)b;
    beat_ms = 600000u / set.bpm10;
    beat_smp = 26460000u / set.bpm10;
    tempo_sync_publish();
    set_dirty = 1;
    set_t_change = fm1_ms;
    ui_mark(0);                                                              /* E_HEADER: the BPM */
    ui_mark(10);                                                             /* E_CARDS: the TEMPO page */
}

static void tempo_tap(void)
{
    uint32_t now = fm1_ms;
    if (clk_follows()) return;                          /* (a clock leads: the taps change nothing) */
    if (tap_n && now - tap_t[tap_n - 1u] > 2000u) tap_n = 0;
    if (tap_n == 4u) { tap_t[0] = tap_t[1]; tap_t[1] = tap_t[2]; tap_t[2] = tap_t[3]; tap_n = 3; }
    tap_t[tap_n++] = now;
    if (tap_n >= 3u && tap_t[tap_n - 1u] != tap_t[0]) {
        tempo_set10(600000u * (tap_n - 1u) / (tap_t[tap_n - 1u] - tap_t[0]));
        beat_t0 = now; beat_k0 = 0;                                          /* re-phase on the tap (bar 1 here) */
    }
}

/* MIDI clock in (CLOCK EXT; USB-MIDI and the TRS jack alike), 0.9.5. Each clock / transport message carries its
 * arrival time and a count of the clock pulses received (usb.c / midi_uart.c, in the timer ISR: 12 bits of fm1_ms and
 * 4 bits of count in the packet's free bytes), so a busy main loop, a USB packet of several pulses or a full ring
 * (a pulse dropped) do not matter. 0.9.4 and the 0.9.5 candidates timed the pulses when the main loop read them and
 * reset the count on any interval under 8 ms: pulses read together (every screen draw) kept the tempo from ever
 * updating, and nothing locked the LFOs, the arpeggiator or the sequencer to the clock.
 * - The tempo: the time over the last 24..96 pulses (1..4 beats), smoothed over beats (1/8 a pulse) and published
 *   (the delay time, the LFOs' increments, the BPM shown) on a beat when it moved by over 0.1 %: the stamps' jitter
 *   (the timer's polls wait for the audio ISR, up to ~5 ms) does not move the delay. A tempo change: three pulses in a
 *   row whose last beat differs from the estimate by over 8 % (one late pulse is not one); the history restarts
 *   from its last 8 pulses and the tempo is published at once. 40..240 BPM (the BPM card's); outside, not followed.
 *   A gap over 200 ms restarts the history; 250 ms without pulses ends the lock and the tempo goes on as it was.
 * - The beat grid: pulse clk_gk at clk_gt (Q8 ms), each pulse moving it 1/8 of the way to its stamp. Start (FA)
 *   makes the next pulse pulse 0 (beat 1 of bar 1), Continue (FB) goes on from the song position, Stop (FC) stops
 *   its count, Song Position (F2) sets it.
 * - The phase lock (tempo_lock_poll, main loop): each synced LFO's phase at the end of each audio block (app_audio's
 *   snapshot: blk_*) against the grid's (the clock's, or without one the FM-1's own from beat_t0); the error is taken
 *   out over about a beat by its increment (at most +-1/8: no click, no pitch jump), or set at once while everything
 *   is silent. Each LFO's cycle starts (phase 0, where a reset starts it) on the beat, counted from Start (a 1-bar
 *   LFO starts with bar 1). The ISR reads the increments and writes the snapshot. */
#define CLK_N 96u
static uint32_t clk_hist[CLK_N + 1u], clk_h, clk_hw;  /* the last CLK_N + 1 stamps (Q8 ms): a ring, clk_h of them valid */
static uint32_t clk_last, clk_last_ms, clk_b8, clk_pub, clk_k, clk_gk, clk_gt;   /* clk_b8: the beat (Q8 ms),
                                                        * smoothed; clk_pub: as published */
static uint8_t clk_lock, clk_fa, clk_chg, clk_dev, clk_stop = 1, clk_wait;   /* (clk_stop: no Start yet) */
static uint32_t clk_sp, clk_wait_t;   /* the song position (pulses from Start, counted while not stopped); Start /
                                       * Continue's time while they wait for the first pulse (clk_wait) */
static uint8_t clk_src, clk_cnt[3], clk_cnt_ok[3];    /* the source followed (1 USB, 2 TRS), its pulse counts */
static uint32_t clk_src_t;                             /* its last clock or transport message (ms) */
static int32_t clk_trend;                              /* pulses in a row the tempo moved one way (a ramp) */
static uint8_t clk_oor;                                /* beats in a row out of 40..240 BPM */
static uint32_t clk_jumps;                             /* Start, Continue, Song Position, missing pulses: the
                                                        * count jumped (seq.c: not a lag to catch up) */

static uint32_t clk_ago(uint32_t j) { return clk_hist[(clk_hw + 2u * (CLK_N + 1u) - 1u - j) % (CLK_N + 1u)]; }
static void clk_push(uint32_t t)
{
    clk_hist[clk_hw] = t;
    clk_hw = (clk_hw + 1u) % (CLK_N + 1u);
    if (clk_h < CLK_N + 1u) clk_h++;
}

static uint32_t tempo_pubs, tempo_marks;                             /* (the host tests: how often the clock's tempo was published) */
static void tempo_clock_set(uint32_t b8)                /* publish a beat (Q8 ms): the delay, the LFOs, the BPM shown */
{
    uint32_t bpm100 = 1536000000u / b8, b = (bpm100 + 5u) / 10u;   /* 60000 * 256 * 100 / b8 */
    b = b < 400u ? 400u : b > 2400u ? 2400u : b;
    clk_pub = b8;
    beat_ms = (b8 + 128u) >> 8;
    beat_smp = b8 * 441u / 2560u;                       /* 44.1 samples a ms */
    if (!clk_owns || (b != clk_bpm10 && (bpm100 + 7u < clk_bpm10 * 10u || bpm100 > clk_bpm10 * 10u + 7u))) {
        clk_bpm10 = (uint16_t)b;                        /* (0.1 BPM, with hysteresis: no redraw for nothing) */
        tempo_marks++;
        ui_mark(0);                                     /* (never set_dirty: the clock's tempo is not a setting) */
        ui_mark(10);
    }
    clk_owns = 1;
    tempo_pubs++;
    tempo_sync_publish();
}

static void tempo_spp(uint32_t pos, int src)            /* Song Position (F2): 16ths from the start */
{
    if (!set.clock || !clk_stop) return;                /* (only while stopped, as the MIDI spec has it) */
    if (clk_src && src != clk_src && fm1_ms - clk_src_t < 500u) return;   /* (the source followed only) */
    clk_sp = pos * 6u;                                  /* (the song only: the beat grid, the LFOs and the arpeggiator go
                                                         * on with the clock until Continue) */
    clk_jumps++;
}

static int tempo_clock(uint8_t st, uint32_t stamp, int src)   /* 1: taken (frodhes.c: Start / Stop for the sequencer) */
{
    uint32_t now = fm1_ms, t, dt, n = 1, cnt = stamp >> 12 & 15u, j, ne;
    if (st != 0xF8u && st != 0xFAu && st != 0xFBu && st != 0xFCu) return 0;
    if (src < 1 || src > 2) src = 1;
    if (st == 0xF8u && clk_cnt_ok[src]) n = (cnt - clk_cnt[src]) & 15u;   /* the pulses since the last one (> 1: the
                                                         * ring was full and dropped some) */
    clk_cnt[src] = (uint8_t)cnt; clk_cnt_ok[src] = 1;   /* (every source's count kept, followed or not, INT too) */
    if (set.clock == 0) return 0;
    if (clk_src && src != clk_src && now - clk_src_t < 500u) return 0;   /* two clocks: the first one, until it is
                                                                          * quiet for 500 ms */
    if (clk_src != src) { clk_h = 0; clk_lock = 0; }   /* (another source: its own history) */
    clk_src = (uint8_t)src;
    clk_src_t = now;
    if (st == 0xFAu) { clk_k = 0; clk_sp = 0; clk_stop = 0; clk_fa = 1; clk_wait = 1; clk_wait_t = now; beat_t0 = now; beat_k0 = 0; clk_jumps++; return 1; }
    if (st == 0xFBu) { clk_k = clk_sp; clk_stop = 0; clk_fa = 1; clk_wait = 1; clk_wait_t = now; clk_jumps++; return 1; }   /* Continue:
                                                         * from the song position (Stop's, or a Song Position's) */
    if (st == 0xFCu) { clk_stop = 1; return 1; }
    if (!n) n = 1;
    t = (now - ((now - stamp) & 0xFFFu)) << 8 | 128u;   /* its arrival (12 bits of fm1_ms), mid-millisecond, Q8 */
    dt = t - clk_last;
    clk_last = t;
    clk_last_ms = now;
    if (clk_lock && clk_b8 && clk_h && !clk_fa && dt < (2000u << 8)) {   /* the tempo known (not right after Start /
                                                         * Continue): the pulses the time says */
        ne = (dt * 24u + clk_b8 / 2u) / clk_b8;
        if (n > 1u) {                                   /* (the count wraps at 16: the time picks the right lap) */
            int32_t lap = ((int32_t)ne - (int32_t)n + 8) >> 4;
            if (lap > 0) n += 16u * (uint32_t)lap;
        } else if (!clk_stop && dt > (100u << 8) && ne >= 5u) n = ne;   /* (not while
                                                         * stopped: the song does not move) */   /* a gap with nothing received (the line, the computer):
                                                         * its pulses were sent all the same */
    }
    if (n > 1u) clk_jumps++;                            /* (the sequencer: not a lag to catch up) */
    if (!clk_h || dt > (200u << 8) * n) { clk_h = 0; clk_lock = 0; clk_chg = 0; clk_dev = 0; clk_oor = 0; n = 1; }
    for (j = 1; j < n && j < 2u * CLK_N; j++) clk_push(t - dt + dt / n * j);   /* (the missing pulses: in between) */
    clk_push(t);
    if (clk_h >= 25u) clk_chg = 0;
    if (clk_h >= 25u || (clk_chg && clk_h >= 4u)) {
        uint32_t m = clk_h - 1u, bl = (t - clk_ago(m)) * 24u / m, was = clk_lock, chg = 0;
        {   /* out of range: the last beat, or the last 3 pulses (+-30 % for their jitter): not followed, held */
            uint32_t b24 = clk_h >= 25u ? t - clk_ago(24) : 0u, b3 = (t - clk_ago(3)) * 8u;
            if ((b24 && (b24 < (249u << 8) || b24 > (1506u << 8))) || b3 < (174u << 8) || b3 > (1958u << 8)) {
                if (++clk_oor >= 3u) { clk_h = 0; clk_lock = 0; clk_oor = 0; }
            } else clk_oor = 0;
        }
        if (clk_h >= 25u && clk_b8) {
            uint32_t b24 = t - clk_ago(24);
            if (dt / n >= clk_b8 / 72u) {               /* (a pulse that came with others in a burst: not judged) */
                int32_t tr = b24 > clk_b8 + clk_b8 / 50u ? 1 : b24 + clk_b8 / 50u < clk_b8 ? -1 : 0;
                if (b24 > clk_b8 + clk_b8 / 12u || b24 + clk_b8 / 12u < clk_b8) clk_dev++;
                else clk_dev = 0;
                clk_trend = tr && (tr > 0) == (clk_trend > 0) ? clk_trend + tr : tr;   /* a ramp: the last beat 2 %
                                                         * off the same way for 12 pulses: the history is that beat */
                if (clk_trend >= 12 || clk_trend <= -12) { clk_h = 25u; m = 24u; bl = b24; clk_trend = 0; chg = 1; }
            }
            if (clk_dev >= 3u) {                        /* a tempo change: from its last 3 pulses on (after it) */
                clk_h = 4u; clk_chg = 1; clk_dev = 0; chg = 1;
                bl = (t - clk_ago(3)) * 8u;
            }
        }
        if (!clk_h) {
            /* (just found out of range: nothing published) */
        } else if (clk_oor) {
            /* (an out-of-range beat: wait and see) */
        } else if (bl >= (249u << 8) && bl <= (1506u << 8)) {        /* 240 .. 40 BPM (the BPM card's; +-0.4 %) */
            if (!was || chg || !clk_b8 || clk_chg) clk_b8 = bl;  /* (new, or changed: at once) */
            else clk_b8 += (uint32_t)(((int32_t)(bl - clk_b8)) / 8);   /* (else over beats) */
            clk_lock = 1;
            if (!was) clk_gk = clk_k - 1u, clk_gt = t - clk_b8 / 24u;    /* (the grid from here: snapped below) */
            if (!was || chg || !clk_owns ||
                (clk_chg && (clk_k % 6u == 0u || clk_b8 > clk_pub + clk_pub / 100u || clk_b8 + clk_pub / 100u < clk_pub)))
                tempo_clock_set(clk_b8);                /* (after a change: every 6 pulses, or on a 1 % move) */
        } else clk_lock = 0;
    }
    clk_k += n - 1u;
    if (!clk_stop) clk_sp += n - 1u;
    if (clk_b8) {                                       /* the grid */
        uint32_t dk = clk_k - clk_gk, pred = clk_gt + dk * clk_b8 / 24u;
        int32_t err = (int32_t)(t - pred);
        if (clk_fa || dk > 96u || err > (int32_t)(clk_b8 / 6u) || -err > (int32_t)(clk_b8 / 6u)) clk_gt = t;
        else clk_gt = pred + (uint32_t)(err / 8);
        clk_gk = clk_k;
        if (clk_k % 24u == 0u && clk_lock) {            /* a beat: the LED, the arpeggiator, the internal grid after */
            beat_t0 = now - (uint32_t)((int32_t)((now << 8) - clk_gt) >> 8);   /* (Q8 differences: no wrap) */
            beat_k0 = clk_k;
            if (!clk_oor && (clk_b8 > clk_pub + clk_pub / 1024u || clk_b8 + clk_pub / 1024u < clk_pub)) tempo_clock_set(clk_b8);
        }
    }
    clk_fa = 0;
    clk_wait = 0;
    clk_k++;
    if (!clk_stop) clk_sp++;
    return 1;
}

/* the arpeggiator and the sequencer under CLOCK EXT (arp.c, seq.c): while pulses come (one in the last 250 ms), they
 * follow the pulses themselves, not the tempo: the last pulse is clk_k - 1 (the beat grid's count, clk_gk) / clk_sp -
 * 1 (the song position), at clk_gt; clk_frac16 is how far the next one is (Q16, from the tempo; never a whole pulse:
 * never ahead of the clock) */
static int clk_live(void)                               /* pulses coming, or Start / Continue waiting for the first */
{
    if (!set.clock) return 0;
    if (clk_wait && fm1_ms - clk_wait_t <= 100u) return 1;   /* (Start / Continue wait for the next pulse; without
                                                             * a clock they go on their own after 100 ms) */
    return clk_last_ms && fm1_ms - clk_last_ms <= 250u && !clk_wait;
}
static int clk_follows(void) { return clk_lock && clk_live(); }   /* a clock's tempo leads now */
static void tempo_clock_int(void)                       /* CLOCK INT (ui.c): the setting's tempo again */
{
    if (!clk_owns) return;
    clk_owns = 0; clk_lock = 0;
    beat_ms = 600000u / set.bpm10;
    beat_smp = 26460000u / set.bpm10;
    tempo_sync_publish();
    ui_mark(0); ui_mark(10);
}
static uint32_t clk_frac16(void)
{
    int32_t d = (int32_t)((fm1_ms << 8 | 128u) - clk_gt);
    uint32_t f;
    if (!clk_b8 || !clk_lock || d <= 0) return 0;
    f = (uint32_t)(((uint64_t)(uint32_t)d * 24u * (0xFFFFFFFFu / clk_b8)) >> 16);
    return f > 0xFFFFu ? 0xFFFFu : f;
}
static int tempo_flash_ok(void) { return !clk_live(); }   /* (the automatic saves: not while a clock runs, whose
                                                           * pulses an erase would hold back) */

/* the phase lock: called every main-loop pass; works on each new audio block's snapshot (app_audio: blk_n, blk_ms
 * and the five LFO phases at the block's end), at the time that block's end is heard (TEMPO_OUT8: the device's DMA
 * half-buffer, ~2 blocks less the render; 0 on the host, whose WAV is the render itself). */
#ifdef DINGHY_HOST
#define TEMPO_OUT8 0u                                   /* (the host renders a block in the ms its samples end) */
#else
#define TEMPO_OUT8 (12u << 8)                           /* (the block's end is heard ~2 blocks after the render starts:
                                                         * the DMA's half-buffer; a first estimate, hil_clock tunes it) */
#endif
static volatile uint32_t blk_n, blk_ms, blk_ph[5];      /* app_audio (the audio ISR): its last block's end */
static uint32_t lk_prev[LK_N], lk_blk, ig_t0, ig_k0, ig_t, ig_k;   /* ig_*: the internal grid (tempo_lock_poll) */
static uint32_t ig_b8;                                  /* the internal grid's beat (Q8 ms) */
static uint8_t lk_reset;                                /* CC 10 (frodhes.c): the FM-1's own beat from the reset's block */
static void lk_st(volatile uint32_t *p, uint32_t v) { *p = v; }
static uint32_t *lk_phase(uint32_t i)
{
    return i == LK_TW ? &tw_ph : i == LK_CP ? &cp_ph : i == LK_PH ? &ph_ph : i == LK_LFO ? &lfo_ph : &bar_mot_ph;
}
static void lk_inc(uint32_t i, uint32_t v)
{
    if (i == LK_TW) fx_tw_sync_inc = v;
    else if (i == LK_CP) fx_cp_sync_inc = v;
    else if (i == LK_PH) fx_ph_sync_inc = v;
    else if (i == LK_LFO) fx_lfo_sync_inc = v;
    else eng_bar_knob(BK_MOT_INC, v);
}
static uint32_t lk_div(uint32_t i)                     /* the LFO's SYNC (an index into SYNC_UNITS), 0 = free */
{
    return i == LK_TW ? PV(P_TW_SYNC) : i == LK_CP ? PV(P_CP_SYNC) : i == LK_PH ? PV(P_PH_SYNC)
         : i == LK_LFO ? (PV(P_LFO_RETRIG) ? 0u : PV(P_LFO_SYNC)) : PV(P_MOT_SYNC);   /* (RETRIG: the keys lead it) */
}

static void tempo_lock_poll(void)
{
    uint32_t now = fm1_ms, i, t, inv, q, gk, gt, b8, n = blk_n, ms, ph[5];
    int32_t d;
    int quiet, ext;

    if (n == lk_blk) return;                            /* no block since the last pass */
    ms = blk_ms;
    for (i = 0; i < 5u; i++) ph[i] = blk_ph[i];
    if (blk_n != n) return;                             /* (a block ended meanwhile: its own snapshot next pass) */
    lk_blk = n;
    ext = clk_lock && set.clock && now - clk_last_ms <= 250u;   /* a clock leads: its beats are the only anchor */
    if (lk_reset && !fx_reset_req) {                    /* the block that reset every LFO to 0 (fx.c, at its start): */
        lk_reset = 0;
        if (!ext) {                                     /* the FM-1's own beat began with it, so nothing moves them (a
                                                         * clock's beats are never moved by a reset) */
            beat_t0 = ms - 6u; beat_k0 = 0;
            ig_t0 = beat_t0; ig_k0 = 0; ig_t = ms << 8; ig_k = 0;   /* (blk_ms: the block's start) */
        }
    }
    t = (ms << 8) + TEMPO_OUT8;
    if (ext) { gk = clk_gk; gt = clk_gt; b8 = clk_b8; }   /* the clock's
                                                         * beats (250 ms without one: the FM-1's own, from its last) */
    else {                                              /* the FM-1's own: from beat_t0 (a tap, Start, the clock's last
                                                         * beat: no jump when the clock stops) at the tempo */
        b8 = beat_smp * 2560u / 441u;                   /* (a beat in Q8 ms) */
        if (beat_t0 != ig_t0 || beat_k0 != ig_k0) { ig_t0 = beat_t0; ig_k0 = beat_k0; ig_t = beat_t0 << 8; ig_k = beat_k0; ig_b8 = b8; }
        if (b8 != ig_b8 && ig_b8) {                     /* a new tempo: the beat goes on from where it is */
            int32_t e = (int32_t)(t - ig_t);
            if (e > 0) { uint32_t p = (uint32_t)e * 24u / ig_b8; ig_k += p; ig_t += p * ig_b8 / 24u; }
        }
        ig_b8 = b8;
        while ((int32_t)(t - ig_t) > (int32_t)(8u * b8)) { ig_t += 4u * b8; ig_k += 96u; }   /* (kept near: no overflow) */
        gk = ig_k; gt = ig_t;
    }
    d = (int32_t)(t - gt);                              /* the pulses since the grid's (Q16) */
    inv = 0xFFFFFFFFu / b8;
    q = (uint32_t)(((uint64_t)(uint32_t)(d >= 0 ? d : -d) * 24u * inv) >> 16);   /* (d < 0: just before the grid's) */
    quiet = -1;
    for (i = 0; i < LK_N; i++) {
        uint32_t u = lk_div(i), c, cyc, pos, des, cur = ph[i];
        int32_t err, corr, lim;
        if (cur == lk_prev[i]) continue;                /* the LFO is not running */
        lk_prev[i] = cur;
        if (!u) { if (lk_nom[i]) lk_inc(i, lk_nom[i]); continue; }   /* (RETRIG: the keys lead it; its own rate) */
        if (!lk_nom[i]) continue;
        c = SYNC_UNITS[u] / 4u;                         /* the cycle in pulses (3 .. 384) */
        cyc = c << 16;
        pos = ((gk % c) << 16) + (d >= 0 ? q % cyc : cyc - q % cyc);
        if (pos >= cyc) pos -= cyc;
        if (pos >= cyc) pos -= cyc;
        des = (uint32_t)(((uint64_t)pos * (0xFFFFFFFFu / c)) >> 16);   /* each cycle starts on the beat (phase 0, as
                                                         * from a reset: the presets' levels were set so) */
        err = (int32_t)(des - cur);
        if (!ext && (uint32_t)(err < 0 ? -err : err) <= 88u * lk_nom[i]) { lk_inc(i, lk_nom[i]); continue; }   /* the
                                                         * FM-1's own beat, within 2 ms: left as it is (no correction
                                                         * for the ms grid's rounding); a clock's: always followed */
        if (quiet < 0) quiet = eng_idle() && fx_quiet();
        if (quiet) {                                    /* nothing sounds: straight to the beat's phase */
            lk_st(lk_phase(i), des);
            lk_inc(i, lk_nom[i]);
            continue;
        }
        corr = err / (int32_t)(beat_smp ? beat_smp : 1u);   /* out over a beat */
        lim = (int32_t)(lk_nom[i] >> 3);
        if (corr > lim) corr = lim;
        if (corr < -lim) corr = -lim;
        lk_inc(i, lk_nom[i] + (uint32_t)corr);
    }
}

/* the beat LED: on for 1/6 of a beat, only while something is synced */
static uint32_t tempo_beat_led(void)
{
    uint32_t ph = (fm1_ms - beat_t0) % (beat_ms ? beat_ms : 1u);
    if (fm1_ms - tap_t[tap_n ? tap_n - 1u : 0] < 80u) return 1;
    if (!PV(P_DL_SYNC) && !PV(P_TW_SYNC) && !PV(P_CP_SYNC) && !PV(P_PH_SYNC) && !PV(P_LFO_SYNC)) return 0;
    return ph < beat_ms / 6u;
}

/* ---- scales */
static const uint16_t SCALE_MASK[11] = {
    0xAB5, /* MAJ  0 2 4 5 7 9 11 */  0x5AD, /* MIN 0 2 3 5 7 8 10 */  0x6AD, /* DOR 0 2 3 5 7 9 10 */
    0x6B5, /* MIX  0 2 4 5 7 9 10 */  0xAD5, /* LYD 0 2 4 6 7 9 11 */  0x5AB, /* PHRY 0 1 3 5 7 8 10 */
    0x9AD, /* HARM 0 2 3 5 7 8 11 */  0xAAD, /* MEL 0 2 3 5 7 9 11 */  0x295, /* PEN 0 2 4 7 9 */
    0x4A9, /* MPEN 0 3 5 7 10 */      0x4E9, /* BLUES 0 3 5 6 7 10 */
};
static const int8_t DEGREE[12] = {0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6};   /* white keys C..B -> degree */

/* the nearest note of the scale (a tie, as F between E and G in a pentatonic, goes down) */
static int32_t scale_snap(int32_t n)
{
    uint32_t m = SCALE_MASK[set.sc_scale], i;
    for (i = 0; i < 12u; i++) {
        int32_t dn = ((n - (int32_t)set.sc_root - (int32_t)i) % 12 + 12) % 12, up = (dn + 2 * (int32_t)i) % 12;
        if (m >> dn & 1u) return n - (int32_t)i;
        if (m >> up & 1u) return n + (int32_t)i;
    }
    return n;
}

/* key k plays the scale's root (the keys that glow): SNAP, the key's own note is the root; DEGREE, the C keys */
static int scale_is_root(uint32_t k)
{
    int32_t n = 53 + (int32_t)k;
    if (set.sc_mode == 1) return ((n + set.sc_trans - (int32_t)set.sc_root) % 12 + 12) % 12 == 0;
    return set.sc_mode == 2 && n % 12 == 0;
}

/* key k's own pitch is in the scale (DISPLAY LEDS SCALE lights these, white and black keys alike): no scale, every key;
 * SNAP and DEGREE, the keys whose own pitch (TRANS applied; the octave shift moves no pitch class) is in the scale
 * (the user, 2026-10-20: SCALE highlights the scale on the keyboard, whatever the keys play) */
static int scale_key_in(uint32_t k)
{
    int32_t n = 53 + (int32_t)k;
    if (set.sc_mode == 1 || set.sc_mode == 2)
        return SCALE_MASK[set.sc_scale] >> (((n + set.sc_trans - (int32_t)set.sc_root) % 12 + 12) % 12) & 1u;
    return 1;
}

/* the note on-board key k plays (255 = silent), octave and transpose applied */
static uint32_t scale_key_note(uint32_t k)
{
    int32_t n = 53 + (int32_t)k + 12 * set.oct, out;
    if (set.sc_mode == 0) out = n + set.sc_trans;
    else if (set.sc_mode == 1) out = scale_snap(n + set.sc_trans);
    else {                                                                   /* DEG: white keys walk the degrees */
        int32_t key = 53 + (int32_t)k, deg = DEGREE[key % 12], oct, d, cnt = 0, i, step;
        uint32_t m = SCALE_MASK[set.sc_scale];
        int32_t offs[12];
        if (deg < 0) return 255u;
        for (i = 0; i < 12; i++) if (m >> i & 1u) offs[cnt++] = i;          /* the scale's degrees */
        d = (key / 12 - 5) * 7 + deg;                                        /* white-key index from C4 */
        oct = d >= 0 ? d / cnt : -((-d + cnt - 1) / cnt);
        step = d - oct * cnt;
        out = 60 + (int32_t)set.sc_root + 12 * oct + offs[step] + 12 * set.oct + set.sc_trans;
    }
    return out < 0 || out > 127 ? 255u : (uint32_t)out;
}
