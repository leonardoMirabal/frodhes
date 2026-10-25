/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes arpeggiator (0.9), main loop only. The notes held (the FM-1's keys after the scale, and USB-MIDI notes)
 * are played one at a time on the tempo's grid (tempo.c: beat_ms, beat_t0, so tap tempo and MIDI clock lead it).
 *
 *   MODE     OFF / UP / DOWN / UP-DN / ORDER (as played) / RANDOM
 *   RATE     1/4, 1/8, 1/8T, 1/16, 1/16T, 1/32 of the beat grid
 *   OCTAVES  1..4: the pattern repeated octaves up
 *   GATE     how long each note sounds, 10 .. 100 % of a step
 *
 * HOLD (PLAY on the ARPEGGIO page): off, it plays while keys are held; on, it latches: the notes keep playing after
 * the keys are let go until new notes come (a chord started after every key was let go replaces the pattern; keys
 * added while others are held join it). The sustain (REC / CC 64) stays the piano's damper pedal only.
 * Settings (set.arp[]) belong to the FM-1, not to a preset. Nothing here runs in the audio ISR. */

#define MIDI_OUT_CH (set.midi_ch ? set.midi_ch - 1u : 0u)   /* the MIDI page's CHANNEL (ALL: sent on 1) */
static void eng_q(uint32_t type, uint32_t n, uint32_t v);   /* notes.c: to the engine, in order */
#define ARP_MAX 16u
enum { ARP_OFF, ARP_UP, ARP_DOWN, ARP_UPDN, ARP_ORDER, ARP_RANDOM, ARP_NMODE };
static const char *const ARP_MODE_NAME[ARP_NMODE] = {"OFF", "UP", "DOWN", "UP-DN", "ORDER", "RANDOM"};
static const uint8_t ARP_UNITS[6] = {96, 48, 32, 24, 16, 12};               /* 1/96 beat */
static const char *const ARP_RATE_NAME[6] = {"1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"};
#define ARP_MODE (set.arp[0])
#define ARP_RATE (set.arp[1])
#define ARP_OCT (set.arp[2])                    /* 0..3 = 1..4 octaves */
#define ARP_GATE (set.arp[3])                   /* 10..100 % */
#define ARP_LAST (set.arp[4])                   /* the mode the ARP button turns back on */
#define ARP_HOLD (set.arp[5])                   /* the latch: 0 / 1 */

static uint8_t arp_note[ARP_MAX], arp_vel[ARP_MAX], arp_down[ARP_MAX], arp_n;   /* in the order played */
static uint32_t arp_step_no = 0xFFFFFFFFu, arp_off_t, arp_seed = 0x1234567u;
static uint8_t arp_playing = 255, arp_pos, arp_dir;

static void arp_defaults(void) { ARP_MODE = ARP_OFF; ARP_RATE = 3; ARP_OCT = 0; ARP_GATE = 60; ARP_LAST = ARP_UP; ARP_HOLD = 0; }
static void arp_sanitize(void)
{
    if (ARP_MODE >= ARP_NMODE || ARP_RATE > 5u || ARP_OCT > 3u || ARP_GATE < 10u || ARP_GATE > 100u ||
        ARP_LAST == ARP_OFF || ARP_LAST >= ARP_NMODE || ARP_HOLD > 1u)
        arp_defaults();
}
static int arp_on(void) { return ARP_MODE != ARP_OFF; }

static void arp_stop_note(void)
{
    if (arp_playing < 128u) { eng_q(EV_NOTE_OFF, arp_playing, 0); midi_send(0x80 | MIDI_OUT_CH, arp_playing, 0); }
    arp_playing = 255;
}

/* a note in / out: the keys (frodhes.c, after the scale) and USB-MIDI */
static void arp_in_on(uint32_t n, uint32_t vel)
{
    uint32_t i;
    int any = 0;
    for (i = 0; i < arp_n; i++) any |= arp_down[i];
    if (!any) { arp_n = 0; arp_step_no = 0xFFFFFFFFu; arp_pos = 0; arp_dir = 0; }   /* a new chord: replaces, from its start */
    for (i = 0; i < arp_n; i++)
        if (arp_note[i] == n) { arp_down[i] = 1; arp_vel[i] = (uint8_t)vel; return; }
    if (arp_n == ARP_MAX) return;
    arp_note[arp_n] = (uint8_t)n; arp_vel[arp_n] = (uint8_t)vel; arp_down[arp_n] = 1; arp_n++;
}
static void arp_remove(uint32_t i)
{
    for (; i + 1u < arp_n; i++) { arp_note[i] = arp_note[i + 1u]; arp_vel[i] = arp_vel[i + 1u]; arp_down[i] = arp_down[i + 1u]; }
    arp_n--;
}
static void arp_stop_note(void);
static void arp_in_off(uint32_t n)                 /* HOLD: the note stays in the pattern; else it leaves */
{
    uint32_t i;
    for (i = 0; i < arp_n; i++)
        if (arp_note[i] == n) { if (ARP_HOLD) arp_down[i] = 0; else arp_remove(i); break; }
    if (!arp_n) arp_stop_note();
}
static void arp_clear(void) { arp_n = 0; arp_stop_note(); }

/* the k-th note of the pattern (before octaves), the notes sorted low to high or as played */
static uint32_t arp_pick(uint32_t k, int sorted)
{
    uint8_t s[ARP_MAX];
    uint32_t i, j;
    for (i = 0; i < arp_n; i++) s[i] = arp_note[i];
    if (sorted)
        for (i = 1; i < arp_n; i++)
            for (j = i; j > 0u && s[j - 1u] > s[j]; j--) { uint8_t t = s[j]; s[j] = s[j - 1u]; s[j - 1u] = t; }
    return s[k];
}
/* the next note: a walk over len = notes x octaves positions */
static uint32_t arp_next(void)
{
    uint32_t len = arp_n * (ARP_OCT + 1u), k, base;
    if (arp_pos >= len) arp_pos = 0;
    switch (ARP_MODE) {
    case ARP_DOWN: k = len - 1u - arp_pos; arp_pos = (uint8_t)((arp_pos + 1u) % len); break;
    case ARP_UPDN:                                                    /* up then down, the ends once */
        k = arp_pos;
        if (len < 2u) { arp_pos = 0; break; }
        if (!arp_dir) { if (arp_pos + 1u >= len) { arp_dir = 1; arp_pos--; } else arp_pos++; }
        else { if (arp_pos == 0u) { arp_dir = 0; arp_pos++; } else arp_pos--; }
        break;
    case ARP_RANDOM:
        arp_seed = arp_seed * 1664525u + 1013904223u;
        k = (arp_seed >> 16) % len;
        break;
    default: k = arp_pos; arp_pos = (uint8_t)((arp_pos + 1u) % len); break;   /* UP, ORDER */
    }
    if (k >= len) k = 0;
    base = arp_pick(k % arp_n, ARP_MODE != ARP_ORDER);
    base += 12u * (k / arp_n);
    return base > 127u ? 127u : base;
}

/* every main-loop pass: a step starts on the grid; the gate ends the note */
static uint8_t arp_clk;                                                /* 1: the last step was on the clock */
static void arp_poll(void)
{
    uint32_t unit_ms10, step, now = fm1_ms;
    if (!arp_on() || !arp_n) { arp_clk = (uint8_t)clk_live(); if (arp_playing < 128u && !arp_on()) arp_stop_note(); return; }
    unit_ms10 = beat_ms * 10u * ARP_UNITS[ARP_RATE] / 96u;             /* a step in 0.1 ms */
    if (!unit_ms10) unit_ms10 = 10u;
    {
        int32_t dd = (int32_t)(now - beat_t0);                        /* (a clock's beat may be a hair ahead) */
        step = (dd > 0 ? (uint32_t)dd : 0u) * 10u / unit_ms10;
    }
    if (clk_live()) step = clk_k && !clk_wait ? (clk_k - 1u) / (ARP_UNITS[ARP_RATE] / 4u) : arp_step_no;  /* CLOCK EXT:
                                                                      * on the pulses (3..24 a step), from Start's beat 1 */
    if ((uint8_t)clk_live() != arp_clk) { arp_clk = (uint8_t)clk_live(); if (arp_step_no != 0xFFFFFFFFu) arp_step_no = step; }   /* the clock comes or
                                                                      * goes: counted anew, no extra note */
    if (arp_playing < 128u && (int32_t)(now - arp_off_t) >= 0) arp_stop_note();
    if (step != arp_step_no) {
        uint32_t n = arp_next(), v = arp_vel[0];                       /* one velocity: the first note's */
        arp_step_no = step;
        arp_stop_note();
        eng_q(EV_NOTE_ON, n, v); midi_send(0x90 | MIDI_OUT_CH, n, v);
        arp_playing = (uint8_t)n;
        arp_off_t = now + unit_ms10 * ARP_GATE / 1000u - (ARP_GATE >= 100u ? 2u : 0u);
    }
}

/* PLAY on the ARPEGGIO page: HOLD on / off (off: the notes no longer held leave the pattern) */
static void arp_hold_toggle(void)
{
    uint32_t i = 0;
    ARP_HOLD = (uint8_t)!ARP_HOLD;
    if (!ARP_HOLD) {
        while (i < arp_n) { if (!arp_down[i]) arp_remove(i); else i++; }
        if (!arp_n) arp_stop_note();
    }
}

/* the ARP button on its page: on with the last mode / off */
static void arp_toggle(void)
{
    if (arp_on()) { ARP_LAST = ARP_MODE; ARP_MODE = ARP_OFF; arp_clear(); }
    else ARP_MODE = ARP_LAST;
}
