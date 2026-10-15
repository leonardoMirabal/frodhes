/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes chord sequencer (0.9.1), main loop only: 2..16 bars of 4/4, one chord per bar, on the tempo (tempo.c).
 *
 *   SEQUENCER page   BARS 2..16, BAR (the one being edited), RHYTHM (HOLD: struck once per chord; 1/2, 1/4, 1/8:
 *                    struck on each, 3/4 of it long), CHORD (the edited bar's: turn left to empty it, right to copy
 *                    the chord before it)
 *   PLAY             start / stop (on that page)          SEQ   recording on / off (on that page)
 *   recording        stopped: what you play (a chord, or one key in chord mode) goes into the edited bar, which then
 *                    moves on; playing: into the bar that plays (a chord started in the last 1/8 of a bar counts
 *                    for the next one)
 *   an empty bar     the chord before it goes on (none yet: silence)
 *
 * The chords play as the sequencer source of notes.c, so they go through the arpeggiator when it runs (then each
 * chord is simply held for the arpeggio). Kept in the settings (set.seq_*): one sequence on the FM-1. */

#define SEQ_STEPS 16u
static const uint8_t SEQ_DIV[4] = {1, 2, 4, 8};
static const char *const SEQ_RHY_NAME[4] = {"HOLD", "1/2", "1/4", "1/8"};
static uint8_t seq_play, seq_rec, seq_cursor, seq_last_step = 0xFF;
static uint32_t seq_t0, seq_last_sub = 0xFFFFFFFFu, seq_gate_t;
static int seq_gate_on;
static uint8_t cap_notes[CH_MAX], cap_n, cap_keys, cap_step, cap_slot[SRC_N];
static void ui_mark(uint32_t e);

static void seq_sanitize(void)
{
    uint32_t i, j;
    if (set.seq_bars < 2u || set.seq_bars > SEQ_STEPS) set.seq_bars = 4;
    if (set.seq_rhythm > 3u) set.seq_rhythm = 0;
    for (i = 0; i < SEQ_STEPS; i++) for (j = 0; j < CH_MAX; j++) if (set.seq_step[i][j] > 127u) set.seq_step[i][j] = 0;
}
static uint32_t seq_count(uint32_t step)                       /* the notes stored in a bar (0: empty) */
{
    uint32_t c = 0;
    while (c < CH_MAX && set.seq_step[step][c]) c++;
    return c;
}
/* the chord a bar plays: its own, else the last one before it (around the loop); -1: none at all */
static int32_t seq_effective(uint32_t step)
{
    uint32_t i, s;
    for (i = 0; i < set.seq_bars; i++) {
        s = (step + set.seq_bars - i) % set.seq_bars;
        if (seq_count(s)) return (int32_t)s;
    }
    return -1;
}
static void seq_strike(int32_t s)
{
    src_off(SRC_SEQ);
    if (s >= 0) src_start(SRC_SEQ, set.seq_step[s], seq_count((uint32_t)s), set.key_vel, 1);
}

static void seq_stop(void)
{
    seq_play = 0;
    src_off(SRC_SEQ);
    seq_last_step = 0xFF; seq_last_sub = 0xFFFFFFFFu; seq_gate_on = 0;
    ui_mark(6);                                                 /* E_FOOTER */
}
static void seq_start(void)
{
    seq_play = 1;
    seq_t0 = beat_t0 = fm1_ms;                                  /* bar 1 now, the tempo's beat with it */
    seq_last_step = 0xFF; seq_last_sub = 0xFFFFFFFFu;
    ui_mark(6);
}
static void seq_toggle_play(void) { if (seq_play) seq_stop(); else seq_start(); }
static void seq_toggle_rec(void) { seq_rec = (uint8_t)!seq_rec; cap_keys = 0; ui_mark(6); }

/* the bar playing now and how far into it (ms) */
static uint32_t seq_now_step(uint32_t *into)
{
    uint32_t bar = beat_ms * 4u, el = fm1_ms - seq_t0;
    if (into) *into = el % bar;
    return (el / bar) % set.seq_bars;
}

static void seq_poll(void)
{
    uint32_t bar, el, step, div, sub, sub_ms;
    if (!seq_play) return;
    bar = beat_ms * 4u;
    el = fm1_ms - seq_t0;
    step = (el / bar) % set.seq_bars;
    div = arp_on() ? 1u : SEQ_DIV[set.seq_rhythm];
    sub_ms = bar / div;
    sub = el / sub_ms;
    if (seq_gate_on && (int32_t)(fm1_ms - seq_gate_t) >= 0) { seq_gate_on = 0; src_off(SRC_SEQ); }
    if (sub != seq_last_sub) {
        int32_t s = seq_effective(step);
        seq_last_sub = sub;
        if (div == 1u) {                                        /* HOLD (or the arpeggiator): a chord change only */
            if (step != seq_last_step && (seq_count(step) || !src_active[SRC_SEQ] || s < 0)) seq_strike(s);
        } else {
            seq_strike(s);
            if (s >= 0) { seq_gate_on = 1; seq_gate_t = fm1_ms + sub_ms * 3u / 4u; }
        }
    }
    if (step != seq_last_step) { seq_last_step = (uint8_t)step; ui_mark(6); }
}

/* recording: the notes played while the keys are down make one chord, stored when the last key comes up */
static void seq_capture_on(uint32_t slot, const uint8_t *notes, uint32_t c)
{
    uint32_t i, j;
    if (!seq_rec) return;
    if (!cap_keys) {
        uint32_t into;
        cap_n = 0;
        if (seq_play) {
            cap_step = (uint8_t)seq_now_step(&into);
            if (into > beat_ms * 4u * 7u / 8u) cap_step = (uint8_t)((cap_step + 1u) % set.seq_bars);
        } else cap_step = seq_cursor;
    }
    cap_slot[slot] = 1;
    cap_keys++;
    for (i = 0; i < c; i++) {
        for (j = 0; j < cap_n && cap_notes[j] != notes[i]; j++) {}
        if (j == cap_n && cap_n < CH_MAX) cap_notes[cap_n++] = notes[i];
    }
}
static void seq_capture_off(uint32_t slot)
{
    uint32_t i, j;
    if (!cap_slot[slot]) return;
    cap_slot[slot] = 0;
    if (!cap_keys || --cap_keys) return;
    if (!seq_rec || !cap_n) return;
    for (i = 1; i < cap_n; i++)                                 /* low to high */
        for (j = i; j > 0u && cap_notes[j - 1u] > cap_notes[j]; j--) { uint8_t t = cap_notes[j]; cap_notes[j] = cap_notes[j - 1u]; cap_notes[j - 1u] = t; }
    for (i = 0; i < CH_MAX; i++) set.seq_step[cap_step][i] = i < cap_n ? cap_notes[i] : 0u;
    if (!seq_play) seq_cursor = (uint8_t)((cap_step + 1u) % set.seq_bars);
    set_dirty = 1; set_t_change = fm1_ms;
    ui_mark(10); ui_mark(6);                                    /* E_CARDS, E_FOOTER */
}

/* the CHORD card: left empties the edited bar, right copies the chord that plays before it */
static void seq_edit_chord(int32_t d)
{
    uint32_t i;
    if (d < 0) { for (i = 0; i < CH_MAX; i++) set.seq_step[seq_cursor][i] = 0; return; }
    {
        int32_t s = seq_effective((seq_cursor + set.seq_bars - 1u) % set.seq_bars);
        if (s >= 0 && (uint32_t)s != seq_cursor) for (i = 0; i < CH_MAX; i++) set.seq_step[seq_cursor][i] = set.seq_step[s][i];
    }
}
