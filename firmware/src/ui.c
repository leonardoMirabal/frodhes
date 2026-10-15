/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes UI: one page at a time, four big cards = the four knobs (the FM-1 has 4 knobs and a 240 x 240 screen,
 * so nothing else competes for them). A draw scheduler sends at most one screen element per main-loop pass
 * (a full screen is ~77 ms of SPI: drawn at once it would delay the keys).
 *
 *   SELECT     turns through the pages (the order of the chain):
 *              PIANO VOICE DECAY COMP TREM/WAH PHASER CHORUS DELAY TEMPO SCALE SETUP
 *   K1..K4     the four cards of the page, always
 *   ALGORITHM  TYPE (on any page)          PRESETS  sounds (presets.c)
 *   shortcuts  HOME = PIANO, EDIT = VOICE, ENV = DECAY (again: COMP), LFO / SEQ / FX / ARP = TREM / PHASER /
 *              CHORUS / DELAY (on that page: the effect on / off), GLO = TEMPO (again: SETUP), SEL = SCALE.
 *              PLAY: tap tempo. REC: sustain. */

#define PR_NF_UI 12u                                  /* presets.c: PR_NF factory presets, then the user ones */
#define PR_NALL_UI 28u
static uint8_t pr_save_mode, pr_save_slot;            /* presets.c */
static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel);
static void preset_name(uint32_t idx, char *s);
static void tempo_tap(void);                          /* tempo.c */
static void tempo_set10(uint32_t b);

enum { PG_PIANO, PG_VOICE, PG_DECAY, PG_COMP, PG_TREM, PG_PHA, PG_CHO, PG_DELAY, PG_TEMPO, PG_SCALE, PG_SETUP, PG_N };
enum { E_HEADER = 0, E_TITLE = 1, E_CARD0 = 2, E_FOOTER = 6, E_N = 7, E_CARDS = 10 };   /* E_CARDS: all four */
/* card slots: a parameter id (< P_COUNT) or one of these */
enum { S_KVEL = 64, S_TUNE, S_BPM, S_CLOCK, S_SCMODE, S_SCALE, S_ROOT, S_TRANS, S_TWKIND, S_NONE = 0xFF };

static const char *const PAGE_TITLE[PG_N] = {"PIANO", "VOICE", "DECAY", "COMPRESSOR", "TREM/WAH", "PHASER",
                                             "CHORUS", "DELAY", "TEMPO", "SCALE", "SETUP"};
static const uint8_t PAGE_S[PG_N][4] = {
    {P_TYPE, P_DRIVE, P_RV_DEPTH, S_KVEL},
    {P_TONE, P_BARK, P_BELL, P_HAMMER},
    {P_DECAY, P_RELEASE, P_NOISE, S_TUNE},
    {P_CMP_SW, P_CMP_AMT, P_CMP_ATK, P_CMP_REL},
    {S_TWKIND, P_TW_DEPTH, P_TW_RATE, P_TW_SHAPE},
    {P_PH_SW, P_PH_DEPTH, P_PH_SPEED, P_PH_FDBK},
    {P_CP_SW, P_CP_DEPTH, P_CP_SPEED, P_CP_SYNC},
    {P_DL_SW, P_DL_DEPTH, P_DL_TIME, P_DL_FDBK},
    {S_BPM, P_TW_SYNC, P_PH_SYNC, P_DL_SYNC},
    {S_SCMODE, S_SCALE, S_ROOT, S_TRANS},
    {S_CLOCK, P_DL_TONE, S_NONE, S_NONE},
};
static const char *const PAGE_LAB[PG_N][4] = {
    {"TYPE", "DRIVE", "REVERB", "KEY VEL"},
    {"TONE", "BARK", "BELL", "HAMMER"},
    {"DECAY", "RELEASE", "NOISE", "TUNE"},
    {"EFFECT", "AMOUNT", "ATTACK", "RELEASE"},
    {"EFFECT", "DEPTH", "RATE", "SHAPE"},
    {"EFFECT", "DEPTH", "SPEED", "FEEDBK"},
    {"EFFECT", "DEPTH", "SPEED", "SYNC"},
    {"EFFECT", "MIX", "TIME", "FEEDBK"},
    {"BPM", "TREM SYNC", "PHA SYNC", "DLY SYNC"},
    {"MODE", "SCALE", "ROOT", "TRANSP"},
    {"CLOCK", "DLY TONE", "", ""},
};
static const char *const PAGE_HINT[PG_N] = {"REC: SUSTAIN  SAVE: STORE", "HOW THE TINE IS STRUCK", "HOW NOTES DIE AWAY",
                                            "", "LFO BUTTON: ON / OFF", "SEQ BUTTON: ON / OFF", "FX BUTTON: ON / OFF",
                                            "ARP BUTTON: ON / OFF", "PLAY: TAP TEMPO", "DEGREE: WHITE KEYS PLAY IT", ""};
static const char *const SCALE_NAME[11] = {"MAJOR", "MINOR", "DORIAN", "MIXO", "LYDIAN", "PHRYG", "HARMON", "MELODIC",
                                           "PENTA", "MPENTA", "BLUES"};
static const char *const NOTE_NAME[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
static const char *const SCMODE_NAME[3] = {"OFF", "SNAP", "DEGREE"};
static const char *const TWKIND_NAME[4] = {"OFF", "PAN", "TREM", "WAH"};
static const char *const CLOCK_NAME[3] = {"INT", "AUTO", "USB"};

static uint8_t ui_page;
static uint32_t ui_dirty = ~0u;
static uint32_t ui_msg_t, ui_hot_t, ui_last_draw;
static uint8_t ui_hot_card = 0xFF;
static char ui_msg[32];

#define UI_ACC pal[3]
#define MSG_MS 1500u
#define HOT_MS 1000u

static void ui_mark(uint32_t e)
{
    if (e == E_CARDS) ui_dirty |= 0xFu << E_CARD0;
    else ui_dirty |= 1u << e;
}
static void ui_mark_param(uint32_t id) { (void)id; ui_mark(E_CARDS); }   /* cheap: the marks coalesce */
static void ui_say(const char *s)
{
    uint32_t i = 0;
    while (s[i] && i < sizeof ui_msg - 1u) { ui_msg[i] = s[i]; i++; }
    ui_msg[i] = 0;
    ui_msg_t = fm1_ms;
    ui_mark(E_FOOTER);
}
static void p_strcpy(char *d, const char *s, uint32_t max) { uint32_t i = 0; while (s[i] && i < max) { d[i] = s[i]; i++; } d[i] = 0; }
static void ui_changed(void) { set_dirty = 1; set_t_change = fm1_ms; }
static int32_t sgn(int32_t d) { return d > 0 ? 1 : -1; }

/* ---- card slots */
static uint32_t tw_kind(void)
{
    uint32_t sw = PV(P_TW_SW);
    return sw == 2u ? 3u : sw == 1u ? 1u + PV(P_TW_MODE) : 0u;
}
static int32_t slot_val(uint32_t s, int32_t *hi)
{
    if (s < P_COUNT) { *hi = PARAMS[s].hi; return PV(s); }
    switch (s) {
    case S_KVEL: *hi = 127; return set.key_vel;
    case S_TUNE: *hi = 100; return set.tune + 50;
    case S_BPM: *hi = 2000; return set.bpm10 - 400;
    case S_CLOCK: *hi = 2; return set.clock;
    case S_SCMODE: *hi = 2; return set.sc_mode;
    case S_SCALE: *hi = 10; return set.sc_scale;
    case S_ROOT: *hi = 11; return set.sc_root;
    case S_TRANS: *hi = 48; return set.sc_trans + 24;
    case S_TWKIND: *hi = 3; return (int32_t)tw_kind();
    default: *hi = 0; return 0;
    }
}
static int slot_is_level(uint32_t s)                    /* a gauge only for amounts, not for choices */
{
    if (s < P_COUNT) return PARAMS[s].fmt != F_ENUM && PARAMS[s].fmt != F_DIV;
    return s == S_KVEL || s == S_TUNE || s == S_BPM || s == S_TRANS;
}
static void fmt_signed(char *s, int32_t v) { if (v > 0) { s[0] = '+'; p_fmt_int(s + 1, v); } else p_fmt_int(s, v); }
static void slot_text(uint32_t s, char *v)
{
    v[0] = 0;
    switch (s) {
    case P_TYPE: p_strcpy(v, N_TYPE_LONG[PV(P_TYPE)], 12); return;
    case P_DL_TIME: if (PV(P_DL_SYNC)) { p_strcpy(v, N_SYNC[PV(P_DL_SYNC)], 5); return; } break;
    case P_CP_SPEED: if (PV(P_CP_SYNC)) { p_strcpy(v, N_SYNC[PV(P_CP_SYNC)], 5); return; } break;
    case P_PH_SPEED: if (PV(P_PH_SYNC)) { p_strcpy(v, N_SYNC[PV(P_PH_SYNC)], 5); return; } break;
    case P_TW_RATE:
        if (PV(P_TW_SW) == 2) { fmt_r10(v, PV(s)); return; }          /* WAH: resonance */
        if (PV(P_TW_SYNC)) { p_strcpy(v, N_SYNC[PV(P_TW_SYNC)], 5); return; }
        break;
    case S_KVEL: p_fmt_int(v, set.key_vel); return;
    case S_TUNE: fmt_signed(v, set.tune); return;
    case S_BPM: {
        uint32_t n = 0;
        p_fmt_int(v, set.bpm10 / 10);
        while (v[n]) n++;
        v[n++] = '.'; v[n++] = (char)('0' + set.bpm10 % 10); v[n] = 0;
        return;
    }
    case S_CLOCK: p_strcpy(v, CLOCK_NAME[set.clock], 5); return;
    case S_SCMODE: p_strcpy(v, SCMODE_NAME[set.sc_mode], 7); return;
    case S_SCALE: p_strcpy(v, SCALE_NAME[set.sc_scale], 8); return;
    case S_ROOT: p_strcpy(v, NOTE_NAME[set.sc_root], 3); return;
    case S_TRANS: fmt_signed(v, set.sc_trans); return;
    case S_TWKIND: p_strcpy(v, TWKIND_NAME[tw_kind()], 5); return;
    case S_NONE: return;
    default: break;
    }
    if (s < P_COUNT) param_text(s, v);
}

static void slot_turn(uint32_t s, int32_t d)
{
    if (!d || s == S_NONE) return;
    if (s < P_COUNT) {
        uint32_t id = s;
        int32_t step = PARAMS[id].fmt == F_ENUM || PARAMS[id].fmt == F_DIV ? sgn(d) : d, v;
        /* a synced rate / time knob steps the note value (clockwise: longer time, faster rate); never unsyncs */
        if (id == P_DL_TIME && PV(P_DL_SYNC)) { id = P_DL_SYNC; step = sgn(d); }
        else if (id == P_CP_SPEED && PV(P_CP_SYNC)) { id = P_CP_SYNC; step = -sgn(d); }
        else if (id == P_PH_SPEED && PV(P_PH_SYNC)) { id = P_PH_SYNC; step = -sgn(d); }
        else if (id == P_TW_RATE && PV(P_TW_SYNC) && PV(P_TW_SW) != 2) { id = P_TW_SYNC; step = -sgn(d); }
        v = PV(id) + step;
        if (id != s && v < 1) v = 1;
        param_set(id, clampi(v, 0, PARAMS[id].hi));
        if (id == P_TW_SYNC || id == P_CP_SYNC || id == P_DL_SYNC || id == P_PH_SYNC) tempo_sync_publish();
        return;
    }
    switch (s) {
    case S_KVEL: set.key_vel = (uint8_t)clampi(set.key_vel + d * (d > 1 || d < -1 ? 4 : 1), 1, 127); break;
    case S_TUNE: set.tune = (int8_t)clampi(set.tune + d, -50, 50); rh_tune_t = TUNE_T[set.tune + 50]; break;
    case S_BPM: tempo_set10((uint32_t)clampi((int32_t)set.bpm10 + d * 10, 400, 2400)); break;
    case S_CLOCK: set.clock = (uint8_t)clampi(set.clock + sgn(d), 0, 2); break;
    case S_SCMODE: set.sc_mode = (uint8_t)clampi(set.sc_mode + sgn(d), 0, 2); break;
    case S_SCALE: set.sc_scale = (uint8_t)clampi(set.sc_scale + sgn(d), 0, 10); break;
    case S_ROOT: set.sc_root = (uint8_t)((set.sc_root + (d > 0 ? 1u : 11u)) % 12u); break;
    case S_TRANS: set.sc_trans = (int8_t)clampi(set.sc_trans + d, -24, 24); break;
    case S_TWKIND: {
        int32_t k = clampi((int32_t)tw_kind() + sgn(d), 0, 3);
        param_set(P_TW_SW, k == 0 ? 0 : k == 3 ? 2 : 1);
        if (k == 1 || k == 2) param_set(P_TW_MODE, k - 1);
        break;
    }
    default: break;
    }
    ui_changed();
}

/* ---- drawing */
static void text_c(int32_t x, int32_t y, int32_t w, const felucca_font_t *f, const char *s, uint16_t c)
{
    cv_text(x + (w - text_w(f, s)) / 2, y, f, s, c);
}
static void gauge(int32_t x, int32_t y, int32_t w, int32_t v, int32_t hi)
{
    cv_rect(x, y, w, 6, C_LINE);
    cv_rect(x + 1, y + 1, (w - 2) * v / (hi ? hi : 1), 4, C_AMB);
}

static void draw_header(void)
{
    char nm[20], b[8];
    cv_begin(240, 20, C_BLACK);
    if (set.preset < PR_NALL_UI) preset_name(set.preset, nm); else p_strcpy(nm, "FRODHES", 8);
    nm[14] = 0;
    cv_text(2, 2, &FONT_S, nm, C_HI);
    if (set.oct) {
        char o[4] = {'O', set.oct < 0 ? '-' : '+', (char)('0' + (set.oct < 0 ? -set.oct : set.oct)), 0};
        cv_text(122, 2, &FONT_S, o, C_GRAY);
    }
    if (rh_hold) cv_text(150, 2, &FONT_S, "SUS", UI_ACC);
    slot_text(S_BPM, b);
    cv_text(238 - text_w(&FONT_S, b), 2, &FONT_S, b, C_AMB);
    cv_rect(0, 19, 240, 1, C_LINE);
    cv_blit(0, 0);
}

static void draw_title(void)
{
    uint32_t i;
    int32_t x0 = 120 - (int32_t)PG_N * 5;
    cv_begin(240, 40, C_BLACK);
    if (ui_page > 0) cv_text(4, 0, &FONT_L, "<", C_DIM);
    if (ui_page + 1u < PG_N) cv_text(220, 0, &FONT_L, ">", C_DIM);
    text_c(0, 0, 240, &FONT_L, PAGE_TITLE[ui_page], UI_ACC);
    for (i = 0; i < PG_N; i++)                          /* where we are: one mark per page */
        cv_rect(x0 + 10 * (int32_t)i, 35, i == ui_page ? 8 : 4, 3, i == ui_page ? UI_ACC : C_LINE);
    cv_blit(0, 21);
}

static void draw_card(uint32_t k)
{
    uint32_t s = PAGE_S[ui_page][k];
    int hot = ui_hot_card == k && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS;
    cv_begin(118, 68, C_BLACK);
    if (s != S_NONE) {
        char v[16], kk[3] = {'K', (char)('1' + k), 0};
        int32_t hi, val = slot_val(s, &hi);
        cv_rect(0, 0, 118, 68, hot ? UI_ACC : C_LINE);
        cv_rect(1, 1, 116, 66, C_BLACK);
        cv_text(5, 3, &FONT_S, kk, C_DIM);
        cv_text(28, 3, &FONT_S, PAGE_LAB[ui_page][k], C_GRAY);
        slot_text(s, v);
        if (text_w(&FONT_L, v) <= 112) text_c(0, 21, 118, &FONT_L, v, C_HI);
        else text_c(0, 29, 118, &FONT_S, v, C_HI);
        if (hi && slot_is_level(s)) gauge(5, 56, 108, val, hi);
    }
    cv_blit(k & 1u ? 121u : 1u, k & 2u ? 135u : 63u);
}

static void draw_footer(void)
{
    cv_begin(240, 35, C_BLACK);
    cv_rect(0, 0, 240, 1, C_LINE);
    if (pr_save_mode) {                                 /* the save mode owns the footer until it ends */
        char s[24], t[32];
        preset_name(PR_NF_UI + pr_save_slot, s);
        p_strcpy(t, "SAVE TO ", 8); p_strcpy(t + 8, s, 20);
        text_c(0, 2, 240, &FONT_S, t, C_HI);
        text_c(0, 18, 240, &FONT_S, "SAVE: YES   HOME: NO", UI_ACC);
    } else if (ui_msg_t && fm1_ms - ui_msg_t < MSG_MS) {
        if (text_w(&FONT_L, ui_msg) <= 236) text_c(0, 2, 240, &FONT_L, ui_msg, C_HI);
        else text_c(0, 10, 240, &FONT_S, ui_msg, C_HI);
    } else {
        text_c(0, 2, 240, &FONT_S, "SELECT: PAGE  PRESETS: SOUND", C_GRAY);
        if (ui_page == PG_COMP) {
            static char gr[24];
            int32_t db10 = PV(P_CMP_SW) ? fx_cmp_gr * 602 / 4096 : 0;      /* log2 Q12 -> dB x 10 */
            p_strcpy(gr, "REDUCING ", 9); p_fmt_int(gr + 9, db10 / 10);
            { uint32_t n = 0; while (gr[n]) n++; gr[n++] = '.'; gr[n++] = (char)('0' + db10 % 10); p_strcpy(gr + n, " DB", 3); }
            text_c(0, 18, 240, &FONT_S, PV(P_CMP_SW) ? gr : "ENV BUTTON: DECAY / COMP", C_DIM);
        } else if (ui_page == PG_SETUP) {
            static char cpu[32];
            uint32_t n = 0;
            p_strcpy(cpu, "AUDIO ", 6); p_fmt_int(cpu + 6, (int32_t)prof_us);
            while (cpu[n]) n++;
            p_strcpy(cpu + n, " MAX ", 5); n += 5; p_fmt_int(cpu + n, (int32_t)prof_max_us);
            while (cpu[n]) n++;
            p_strcpy(cpu + n, " US", 3);
            text_c(0, 18, 240, &FONT_S, cpu, C_DIM);
        } else text_c(0, 18, 240, &FONT_S, PAGE_HINT[ui_page], C_DIM);
    }
    cv_blit(0, 205);
}

static void ui_draw_page_change(void) { ui_dirty |= (1u << E_N) - 1u; }

/* one element per pass, in priority order: footer, header, title, cards */
static void ui_draw(void)
{
    uint32_t e, d;
    static uint8_t cleared;
    if (!cleared) { lcd_fill(0, 0, 240, 240, C_BLACK); cleared = 1; }
    if (ui_msg_t && fm1_ms - ui_msg_t >= MSG_MS && fm1_ms - ui_msg_t < MSG_MS + 100u) ui_mark(E_FOOTER);
    if (ui_hot_t && ui_hot_card < 4u && fm1_ms - ui_hot_t >= HOT_MS && fm1_ms - ui_hot_t < HOT_MS + 100u)
        ui_mark(E_CARD0 + ui_hot_card);
    if ((ui_page == PG_SETUP || ui_page == PG_COMP) && (fm1_ms & 255u) == 0u) ui_mark(E_FOOTER);   /* live readouts */
    d = ui_dirty & ((1u << E_N) - 1u);
    if (!d || fm1_ms - ui_last_draw < 4u) return;
    e = d >> E_FOOTER & 1u ? E_FOOTER : d & 1u ? E_HEADER : d >> E_TITLE & 1u ? E_TITLE : 0xFF;
    if (e == 0xFF) for (e = E_CARD0; e < E_FOOTER && !(d >> e & 1u); e++) {}
    ui_dirty &= ~(1u << e);
    ui_last_draw = fm1_ms;
    if (e == E_HEADER) draw_header();
    else if (e == E_TITLE) draw_title();
    else if (e == E_FOOTER) draw_footer();
    else draw_card(e - E_CARD0);
}

/* ---- input */
static void ui_goto(uint32_t pg)
{
    if (pg >= PG_N) pg = PG_PIANO;
    ui_page = (uint8_t)pg;
    ui_hot_card = 0xFF;
    ui_draw_page_change();
}

/* LFO / FX / ARP: go to the effect's page; on it, the effect on <-> off (back to the last one used) */
static void ui_fx_button(uint32_t pg, uint32_t k)
{
    static const uint8_t SW[4] = {P_TW_SW, P_CP_SW, P_DL_SW, P_PH_SW};
    static const char *const NAME[4] = {"TREM/WAH", "CHORUS", "DELAY", "PHASER"};
    uint32_t id = SW[k], sw = PV(id), n = 0;
    char s[24];
    if (ui_page != pg) { ui_goto(pg); return; }
    if (sw) { if (k < 3u) set.last_on[k] = (uint8_t)sw; param_set(id, 0); }
    else param_set(id, k < 3u && set.last_on[k] <= PARAMS[id].hi ? set.last_on[k] : 1);
    p_strcpy(s, NAME[k], 12);
    while (s[n]) n++;
    p_strcpy(s + n, PV(id) ? " ON" : " OFF", 4);
    ui_say(s);
    ui_mark(E_CARDS);
}

static void ui_input(uint32_t b, uint32_t rel)
{
    int32_t d[7];
    uint32_t k, i;
    for (i = 0; i < 7u; i++) d[i] = fm1_enc_take(i);
    {
        uint8_t was = pr_save_mode;
        presets_poll(d[ENC_PRESETS], was ? d[ENC_KNOB1] : 0, b, rel);
        if (was || pr_save_mode) { ui_mark(E_FOOTER); return; }      /* the save mode owns the pass */
    }
    if (b >> BTN_HOME & 1u) ui_goto(PG_PIANO);
    if (b >> BTN_EDIT & 1u) ui_goto(PG_VOICE);
    if (b >> BTN_ENV & 1u) ui_goto(ui_page == PG_DECAY ? PG_COMP : PG_DECAY);
    if (b >> BTN_SEL & 1u) ui_goto(PG_SCALE);
    if (b >> BTN_GLO & 1u) ui_goto(ui_page == PG_TEMPO ? PG_SETUP : PG_TEMPO);
    if (b >> BTN_LFO & 1u) ui_fx_button(PG_TREM, 0);
    if (b >> BTN_FX & 1u) ui_fx_button(PG_CHO, 1);
    if (b >> BTN_SEQ & 1u) ui_fx_button(PG_PHA, 3);
    if (b >> BTN_ARP & 1u) ui_fx_button(PG_DELAY, 2);
    if (b >> BTN_PLAY & 1u) tempo_tap();
    if (b >> BTN_OCT_DOWN & 1u && set.oct > -2) { set.oct--; ui_changed(); ui_mark(E_HEADER); }
    if (b >> BTN_OCT_UP & 1u && set.oct < 2) { set.oct++; ui_changed(); ui_mark(E_HEADER); }

    if (d[ENC_SELECT]) {                                /* the pages: no wrap, so the ends are easy to find */
        int32_t p = clampi((int32_t)ui_page + sgn(d[ENC_SELECT]), 0, PG_N - 1);
        if (p != ui_page) ui_goto((uint32_t)p);
    }
    if (d[ENC_ALGORITHM]) {
        slot_turn(P_TYPE, sgn(d[ENC_ALGORITHM]));
        ui_say(N_TYPE_LONG[PV(P_TYPE)]);
        ui_mark(E_CARDS);
    }
    for (k = 0; k < 4u; k++) {
        uint32_t s = PAGE_S[ui_page][k];
        if (!d[ENC_KNOB1 + k] || s == S_NONE) continue;
        slot_turn(s, d[ENC_KNOB1 + k]);
        if (ui_msg_t) { ui_msg_t = 0; ui_mark(E_FOOTER); }   /* an old message would contradict the card */
        if (ui_hot_card != k && ui_hot_card < 4u) ui_mark(E_CARD0 + ui_hot_card);
        ui_hot_card = (uint8_t)k; ui_hot_t = fm1_ms;
        ui_mark(E_CARD0 + k);
        if (s == P_DL_SW || s == P_CP_SW || s == S_TWKIND || s == P_TW_SYNC || s == P_CP_SYNC || s == P_DL_SYNC ||
            s == P_PH_SYNC || s == P_CMP_SW)
            ui_mark(E_CARDS);                           /* a switch or sync changes what the other cards show */
    }
}

/* ---- LEDs, once per pass: the page's shortcut button is lit; LFO / FX / ARP: that effect is on */
static void ui_leds(uint32_t lit, uint32_t beat_on)
{
    uint32_t k;
    fm1_led_key(BTN_LFO, PV(P_TW_SW) != 0);
    fm1_led_key(BTN_FX, PV(P_CP_SW) != 0);
    fm1_led_key(BTN_ARP, PV(P_DL_SW) != 0);
    fm1_led_key(BTN_SEQ, PV(P_PH_SW) != 0);
    fm1_led_key(BTN_HOME, ui_page == PG_PIANO);
    fm1_led_key(BTN_EDIT, ui_page == PG_VOICE);
    fm1_led_key(BTN_ENV, ui_page == PG_DECAY || ui_page == PG_COMP);
    fm1_led_key(BTN_GLO, ui_page == PG_TEMPO || ui_page == PG_SETUP);
    fm1_led_key(BTN_SEL, ui_page == PG_SCALE);
    fm1_led_key(BTN_SAVE, pr_save_mode && (fm1_ms & 256u));
    fm1_led_key(BTN_PLAY, beat_on);
    fm1_led_key(BTN_OCT_DOWN, set.oct < 0);
    fm1_led_key(BTN_OCT_UP, set.oct > 0);
    fm1_led_key(BTN_REC, rh_hold);
    for (k = 0; k < 27u; k++) fm1_led_key(KEY_LED(k), lit >> k & 1u);
}
