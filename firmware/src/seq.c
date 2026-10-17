/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes sequencer (0.9.1), main loop only: a looping pattern recorder. What is played (single notes, chords, chord
 * mode's chords) is recorded as notes with their own timing; QUANT snaps them as they play, so changing it reshapes
 * the pattern at once, and nothing is lost. Edits never stop the loop:
 *
 *   PLAY            start / stop (on the SEQUENCER page)
 *   SEQ             recording on / off: playing, every pass adds what you play (overdub); stopped, the notes go on
 *                   the cursor's step and it moves on (step recording); held 1 s: CLEAR
 *   BARS 1..16      the loop's length (notes past it stay, and come back when it grows)
 *   QUANT           OFF, 1/4, 1/8, 1/16, 1/8T, 1/16T        SWING 50..75 % (the off-beats of 1/8 and 1/16)
 *   EDIT            left: UNDO the last pass recorded; right: ERASE on / off (hold keys: their notes go as the loop
 *                   passes them)
 *
 * Recording armed while stopped is step recording: the notes go on the cursor's step (live_note_on). The page (ui.c) shows NOW / NEXT and the
 * bar's 16 cells. The notes play through notes.c (the
 * arpeggiator takes them when it runs) and are kept in two flash sectors written in turn (0x99000 / 0x9A000). */

#define SQ_PPQ 96u                                  /* ticks per beat */
#define SQ_MAXEV 448u
#define SQ_MAXACT 32u
#define SQ_A 0x99000u
#define SQ_B 0x9A000u
#define SQ_MAGIC 0x46535131u                        /* "FSQ1" */
typedef struct { uint16_t tick, len; uint8_t note, vel, take, rsv; } sq_ev_t;
typedef struct { uint32_t magic, seq, n, crc; } sq_hdr_t;
static sq_ev_t sq_ev[SQ_MAXEV];
static uint32_t sq_n, sq_flash_seq;
static uint8_t seq_play, seq_rec, sq_erase, sq_take;
static uint32_t sq_pos, sq_last_ms, sq_prev;        /* sq_pos: ticks in Q16 */
static struct { uint8_t note; uint16_t off; } sq_act[SQ_MAXACT];
static uint32_t sq_nact;
static uint16_t rec_start[128];
static uint8_t rec_vel[128], live_held[128];
static uint8_t sq_dirty, sq_roll_dirty, sq_btn_down;
static int32_t sq_cur = -1;                              /* the page's cursor, in 16ths (ui.c moves it) */
static uint32_t sq_cur_t;
static uint32_t st_keys, st_start, st_len, st_n;          /* step recording: the notes held, where, how long (16ths) */
static uint16_t st_idx[CH_MAX * 2u];
static uint32_t sq_change_t, sq_btn_t;
static const uint8_t SQ_GRID[6] = {0, 96, 48, 24, 32, 16};
static const char *const SQ_QUANT_NAME[6] = {"OFF", "1/4", "1/8", "1/16", "1/8T", "1/16T"};
#define SQ_BARS (set.seq_bars)
#define SQ_QUANT (set.seq_quant)
#define SQ_SWING (set.seq_swing)
static void ui_mark(uint32_t e);

static uint32_t sq_len(void) { return (uint32_t)SQ_BARS * 4u * SQ_PPQ; }
static uint32_t sq_tick(void) { return sq_pos >> 16; }
static void seq_sanitize(void)
{
    if (SQ_BARS < 1u || SQ_BARS > 16u) SQ_BARS = 4;
    if (SQ_QUANT > 5u) SQ_QUANT = 3;
    if (SQ_SWING < 50u || SQ_SWING > 75u) SQ_SWING = 50;
}
static void sq_changed(void) { sq_dirty = 1; sq_roll_dirty = 1; sq_change_t = fm1_ms; }

/* where a note plays: its tick on the QUANT grid (with SWING), inside the loop */
static uint32_t sq_at(const sq_ev_t *e)
{
    uint32_t g = SQ_GRID[SQ_QUANT], t = e->tick, i, L = sq_len();
    if (g) {
        i = (t + g / 2u) / g;
        t = i * g;
        if ((g == 48u || g == 24u) && (i & 1u)) t += 2u * g * SQ_SWING / 100u - g;
    }
    return t % L;
}
static int sq_in(uint32_t t, uint32_t p0, uint32_t p1)   /* t in (p0, p1], around the loop's end */
{
    return p0 <= p1 ? t > p0 && t <= p1 : t > p0 || t <= p1;
}

static void sq_all_off(void)
{
    uint32_t i;
    for (i = 0; i < sq_nact; i++) out_off(sq_act[i].note);
    sq_nact = 0;
}
static void sq_add(uint32_t tick, uint32_t len, uint32_t note, uint32_t vel)
{
    sq_ev_t *e;
    if (sq_n >= SQ_MAXEV) return;
    e = &sq_ev[sq_n++];
    e->tick = (uint16_t)tick; e->len = (uint16_t)len; e->note = (uint8_t)note; e->vel = (uint8_t)vel;
    e->take = sq_take; e->rsv = 0;
    sq_changed();
}
static void sq_rec_close(uint32_t n)                    /* a recorded note ends: it becomes an event */
{
    uint32_t L = sq_len(), t = sq_tick(), len;
    if (rec_start[n] == 0xFFFFu) return;
    len = (t + L - rec_start[n]) % L;
    if (len < 6u) len = 6u;
    sq_add(rec_start[n], len, n, rec_vel[n]);
    rec_start[n] = 0xFFFFu;
}
static void sq_rec_close_all(void) { uint32_t n; for (n = 0; n < 128u; n++) sq_rec_close(n); }

static void seq_start(void)
{
    seq_play = 1;
    sq_pos = 0; sq_prev = sq_len() - 1u; sq_last_ms = fm1_ms;
    beat_t0 = fm1_ms;                                   /* the tempo's beat (arpeggiator, LED) from bar 1 */
    ui_mark(6);
}
static void seq_stop(void)                              /* stop: back to the start (the cursor too: bar 1, step 1) */
{
    sq_rec_close_all();
    seq_play = 0;
    sq_all_off();
    sq_cur = 0; sq_cur_t = fm1_ms;
    ui_mark(6); ui_mark(7);
}
static void seq_toggle_play(void) { if (seq_play) seq_stop(); else seq_start(); }
static void seq_toggle_rec(void)
{
    if (seq_rec) { sq_rec_close_all(); seq_rec = 0; }
    else { seq_rec = 1; sq_erase = 0; sq_take++; }
    ui_mark(6); ui_mark(10);
}
static void seq_clear(void)
{
    uint32_t n;
    sq_all_off();
    for (n = 0; n < 128u; n++) rec_start[n] = 0xFFFFu;
    sq_n = 0;
    sq_changed();
}
static uint32_t seq_undo(void)                          /* the last pass recorded goes: how many notes */
{
    uint32_t i, j, got = 0, best = 256u;
    uint8_t hi = 0;
    if (!sq_n) return 0;
    for (i = 0; i < sq_n; i++) {                        /* the most recent pass: the take closest behind sq_take */
        uint32_t d = (uint8_t)(sq_take - sq_ev[i].take);
        if (d < best) { best = d; hi = sq_ev[i].take; }
    }
    for (i = j = 0; i < sq_n; i++) { if (sq_ev[i].take == hi) got++; else sq_ev[j++] = sq_ev[i]; }
    sq_n = j;
    sq_all_off();
    sq_changed();
    return got;
}

/* step recording (armed and stopped): the notes go on the cursor's step, as long as the QUANT grid (stretched by
 * ALGORITHM while they are held); let go, the cursor moves on by that much */
static uint32_t sq_grid16(void) { static const uint8_t G[6] = {1, 4, 2, 1, 1, 1}; return G[SQ_QUANT]; }
static uint32_t sq_steps(void) { return (uint32_t)SQ_BARS * 16u; }
static void st_note_on(uint32_t n, uint32_t vel)
{
    if (!st_keys) {                                     /* a new step: its own take (UNDO takes it back alone) */
        st_start = sq_cur >= 0 && (uint32_t)sq_cur < sq_steps() ? (uint32_t)sq_cur : 0u;
        st_len = sq_grid16(); st_n = 0; sq_take++;
    }
    st_keys++;
    if (sq_n < SQ_MAXEV && st_n < CH_MAX * 2u) { st_idx[st_n++] = (uint16_t)sq_n; sq_add(st_start * 24u, st_len * 24u - 2u, n, vel); }
    sq_cur = (int32_t)st_start; sq_cur_t = fm1_ms;
}
static void st_note_off(void)
{
    if (!st_keys || --st_keys) return;
    sq_cur = (int32_t)((st_start + st_len) % sq_steps()); sq_cur_t = fm1_ms;
    sq_changed();
}
static int seq_step_held(void) { return seq_rec && !seq_play && st_keys != 0u; }
static void seq_step_stretch(int32_t d)                 /* ALGORITHM while the step's keys are held */
{
    uint32_t i;
    int32_t l = (int32_t)st_len + d;
    st_len = (uint32_t)(l < 1 ? 1 : l > (int32_t)sq_steps() ? (int32_t)sq_steps() : l);
    for (i = 0; i < st_n; i++) if (st_idx[i] < sq_n) sq_ev[st_idx[i]].len = (uint16_t)(st_len * 24u - 2u);
    sq_changed();
}

/* live notes (keys, USB-MIDI, chord mode's notes; not the sequencer's own): notes.c calls these */
static void live_note_on(uint32_t n, uint32_t vel)
{
    if (n > 127u) return;
    live_held[n]++;
    if (!seq_rec || sq_erase) return;
    if (!seq_play) { st_note_on(n, vel); return; }      /* stopped: step recording */
    if (rec_start[n] == 0xFFFFu) { rec_start[n] = (uint16_t)sq_tick(); rec_vel[n] = (uint8_t)vel; }
}
static void live_note_off(uint32_t n)
{
    if (n > 127u) return;
    if (live_held[n]) live_held[n]--;
    if (seq_rec && !seq_play && !sq_erase) { st_note_off(); return; }
    if (!live_held[n]) sq_rec_close(n);
}

static void seq_poll(void)
{
    uint32_t L = sq_len(), dt, p0, p1, i;
    if (!seq_play) return;
    dt = fm1_ms - sq_last_ms;
    sq_last_ms = fm1_ms;
    if (dt > 1000u) dt = 1000u;                         /* (32-bit: no 64-bit division on this chip) */
    sq_pos += dt * ((SQ_PPQ << 16) / (beat_ms ? beat_ms : 500u));
    while (sq_pos >= L << 16) sq_pos -= L << 16;
    p0 = sq_prev % L; p1 = sq_tick();
    if (p0 == p1) return;
    for (i = 0; i < sq_nact;) {                         /* note offs first: a repeated note restarts cleanly */
        if (sq_in(sq_act[i].off, p0, p1)) { out_off(sq_act[i].note); sq_act[i] = sq_act[--sq_nact]; }
        else i++;
    }
    for (i = 0; i < sq_n;) {
        sq_ev_t *e = &sq_ev[i];
        uint32_t t;
        if (e->tick >= L) { i++; continue; }            /* past the loop (BARS made shorter): kept, silent */
        t = sq_at(e);
        if (sq_in(t, p0, p1)) {
            if (sq_erase && live_held[e->note]) { sq_ev[i] = sq_ev[--sq_n]; sq_changed(); continue; }
            if (sq_nact < SQ_MAXACT) {
                out_on(e->note, e->vel, 1);
                sq_act[sq_nact].note = e->note;
                sq_act[sq_nact].off = (uint16_t)((t + (e->len < L ? e->len : L - 1u)) % L);
                sq_nact++;
            }
        }
        i++;
    }
    if (p1 < p0 && seq_rec) sq_take++;                  /* a new pass: UNDO takes it away on its own */
    sq_prev = p1;
}

/* ---- the pattern in flash (only when stopped and quiet: an erase stalls the audio) */
#ifdef DINGHY_HOST
static uint32_t crc32(const uint8_t *p, uint32_t n);   /* presets.c (host) */
static uint8_t sq_host_fl[2][4096];
static int sq_fl_read(uint32_t at, void *p, uint32_t n)   /* at: a sector and an offset in it (sq_valid reads the
                                                              * events after the header) */
{
    uint32_t s = at >= SQ_B, off = at - (s ? SQ_B : SQ_A);
    if (off + n > 4096u) return -1;
    memcpy(p, &sq_host_fl[s][off], n);
    return 0;
}
static int sq_fl_erase(uint32_t at)
{
    printf("%6u ms  pattern %c erased\n", fm1_ms, at == SQ_B ? 'B' : 'A');   /* (tests: when it saves) */
    memset(sq_host_fl[at == SQ_B], 0xFF, 4096);
    return 0;
}
static int sq_fl_write(uint32_t at, uint32_t off, const void *p, uint32_t n) { memcpy(&sq_host_fl[at == SQ_B][off], p, n); return 0; }
#else
static int sq_fl_read(uint32_t at, void *p, uint32_t n) { return flash_ok ? st_read(at, p, n) : -1; }
static int sq_fl_erase(uint32_t at) { uint32_t took; return fl_erase4k(at, &took); }
static int sq_fl_write(uint32_t at, uint32_t off, const void *p, uint32_t n) { return fl_write(at + off, p, n); }
#endif
static int sq_valid(uint32_t at, sq_hdr_t *h)
{
    if (sq_fl_read(at, h, sizeof *h) || h->magic != SQ_MAGIC || h->n > SQ_MAXEV) return 0;
    if (sq_fl_read(at + sizeof *h, sq_ev, h->n * sizeof(sq_ev_t))) return 0;
    return crc32((const uint8_t *)sq_ev, h->n * sizeof(sq_ev_t)) == h->crc;
}
static void seq_load(void)
{
    sq_hdr_t a, b;
    int va = sq_valid(SQ_A, &a), vb = sq_valid(SQ_B, &b);
    uint32_t n;
    for (n = 0; n < 128u; n++) rec_start[n] = 0xFFFFu;
    sq_n = 0;
    if (!va && !vb) return;
    {
        uint32_t at = va && (!vb || (int32_t)(a.seq - b.seq) > 0) ? SQ_A : SQ_B;
        sq_hdr_t h;
        if (!sq_valid(at, &h)) { sq_n = 0; return; }   /* (re-reads it into sq_ev) */
        sq_n = h.n; sq_flash_seq = h.seq;
    }
    for (n = 0; n < sq_n; n++) if (sq_ev[n].take > sq_take) sq_take = sq_ev[n].take;
}
static void seq_save_poll(void)
{
    sq_hdr_t h, cur;
    uint32_t at = SQ_A;
    if (!sq_dirty || seq_play || fm1_ms - sq_change_t < 1500u || !eng_idle() || !fx_quiet()) return;
    {
        static sq_ev_t keep[SQ_MAXEV];                  /* sq_valid() reads into sq_ev: keep ours aside */
        uint32_t n = sq_n;
        memcpy(keep, sq_ev, n * sizeof(sq_ev_t));
        if (sq_valid(SQ_A, &cur) && (!sq_valid(SQ_B, &h) || (int32_t)(cur.seq - h.seq) > 0)) at = SQ_B;
        memcpy(sq_ev, keep, n * sizeof(sq_ev_t));
        sq_n = n;
    }
    h.magic = SQ_MAGIC; h.seq = ++sq_flash_seq; h.n = sq_n; h.crc = crc32((const uint8_t *)sq_ev, sq_n * sizeof(sq_ev_t));
    if (sq_fl_erase(at) == 0 && (!sq_n || sq_fl_write(at, sizeof h, sq_ev, sq_n * sizeof(sq_ev_t)) == 0))
        sq_fl_write(at, 0, &h, sizeof h);              /* the header last: a cut save stays invalid */
    sq_dirty = 0;
}
