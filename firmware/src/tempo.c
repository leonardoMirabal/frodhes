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
static void ui_mark(uint32_t e);                                              /* ui.c */

static uint32_t beat_samples(void) { return 26460000u / set.bpm10; }       /* fs * 60 * 10 / bpm10 */

static void tempo_sync_publish(void)
{
    uint32_t d = PV(P_DL_SYNC), t = PV(P_TW_SYNC);
    fx_dly_sync_samples = d ? beat_samples() * SYNC_UNITS[d] / 96u : 0u;
    if (t) {
        uint32_t s = beat_samples() * SYNC_UNITS[t] / 96u;
        fx_tw_sync_inc = 0xFFFFFFFFu / (s ? s : 1u);                         /* one LFO cycle per division */
    } else
        fx_tw_sync_inc = 0;
    {
        uint32_t c = PV(P_CP_SYNC), s = c ? beat_samples() * SYNC_UNITS[c] / 96u : 0u;
        fx_cp_sync_inc = c ? 0xFFFFFFFFu / (s ? s : 1u) : 0u;
        c = PV(P_PH_SYNC); s = c ? beat_samples() * SYNC_UNITS[c] / 96u : 0u;
        fx_ph_sync_inc = c ? 0xFFFFFFFFu / (s ? s : 1u) : 0u;
    }
}

static void tempo_set10(uint32_t b)
{
    if ((int32_t)b < 400) b = 400;
    if (b > 2400) b = 2400;
    if (b == set.bpm10 && beat_ms == 600000u / b) return;
    set.bpm10 = (uint16_t)b;
    beat_ms = 600000u / set.bpm10;
    tempo_sync_publish();
    set_dirty = 1;
    set_t_change = fm1_ms;
    ui_mark(0);                                                              /* E_HEADER: the BPM */
    ui_mark(10);                                                             /* E_CARDS: the TEMPO page */
}

static void tempo_tap(void)
{
    uint32_t now = fm1_ms;
    if (tap_n && now - tap_t[tap_n - 1u] > 2000u) tap_n = 0;
    if (tap_n == 4u) { tap_t[0] = tap_t[1]; tap_t[1] = tap_t[2]; tap_t[2] = tap_t[3]; tap_n = 3; }
    tap_t[tap_n++] = now;
    if (tap_n >= 3u && tap_t[tap_n - 1u] != tap_t[0]) {
        tempo_set10(600000u * (tap_n - 1u) / (tap_t[tap_n - 1u] - tap_t[0]));
        beat_t0 = now;                                                       /* re-phase on the tap */
    }
}

/* MIDI clock in (CLOCK AUTO / USB): the tempo from 24 pulses, smoothed; FA restarts the beat. Pulses 8..80 ms
 * (31..312 BPM); without pulses the internal tempo simply goes on at the last value. */
static uint32_t clk_last, clk_acc, clk_n;
static void tempo_clock(uint8_t st)
{
    uint32_t now = fm1_ms, dt;
    if (set.clock == 0) return;
    if (st == 0xFAu) { beat_t0 = now; clk_n = clk_acc = 0; return; }
    if (st != 0xF8u) return;
    dt = now - clk_last;
    clk_last = now;
    if (dt < 8u || dt > 80u) { clk_n = clk_acc = 0; return; }
    clk_acc += dt;
    if (++clk_n == 24u) {
        uint32_t b = 600000u / clk_acc, cur = set.bpm10;
        int32_t diff = (int32_t)b - (int32_t)cur;
        if (diff > (int32_t)cur / 10 || -diff > (int32_t)cur / 10) tempo_set10(b);   /* a jump: follow at once */
        else tempo_set10((uint32_t)((int32_t)cur + diff / 4));                       /* else smooth */
        beat_t0 = now;
        clk_n = clk_acc = 0;
    }
}

/* the beat LED: on for 1/6 of a beat, only while something is synced */
static uint32_t tempo_beat_led(void)
{
    uint32_t ph = (fm1_ms - beat_t0) % (beat_ms ? beat_ms : 1u);
    if (fm1_ms - tap_t[tap_n ? tap_n - 1u : 0] < 80u) return 1;
    if (!PV(P_DL_SYNC) && !PV(P_TW_SYNC) && !PV(P_CP_SYNC) && !PV(P_PH_SYNC)) return 0;
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
