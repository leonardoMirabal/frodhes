/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes UI (IMPLEMENTATION-v3 §10): the reface CP-style PANEL of 7 columns, 4-card pages, the button state
 * machine, the encoders, the LEDs and a draw scheduler that sends at most one screen element per main-loop pass
 * (a full screen is ~77 ms of SPI: drawn at once it would delay the keys).
 *
 *   PANEL   TYPE DRV TREM CHO DLY REV VOL. LFO / FX / ARP focus TREM / CHO / DLY (tap again: OFF <-> last on);
 *           hold one: K1..K4 edit that column's switch, sync and extra params. K1/K2 = the focused column's two
 *           knobs, K3 = DRIVE, K4 = REVERB, ALGORITHM = TYPE, SELECT = key velocity, PRESETS = presets.
 *   EDIT    VOICING   TONE BARK BELL HAMR        ENV  VOICING 2  DCAY REL NOIS SPRD
 *   GLO     TEMPO     BPM KVEL TUNE CLK          SEL  SCALE      MODE SCAL ROOT TRN
 *   HOME    back to the PANEL. PLAY: tap tempo. REC: sustain (frodhes.c). OCT-/OCT+: octave. */

#ifdef DINGHY_HOST
#define UI_MASTER_Q12 4096
#else
#define UI_MASTER_Q12 master_q12
#endif

static uint8_t pr_save_mode;                           /* presets.c */
static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel);
static void preset_name(uint32_t idx, char *s);
enum { PG_PANEL, PG_VOICE, PG_VOICE2, PG_GLO, PG_SCALE };
enum { E_HEADER, E_TITLES, E_COL0, E_COL6 = E_COL0 + 6, E_FOOTER, E_CARDS, E_KEYS, E_N };

static uint8_t ui_page, ui_layer = 0xFF;      /* layer: the column (0..2) whose hold layer is open, 0xFF none */
static uint32_t ui_dirty = ~0u;
static uint32_t ui_hot_t, ui_msg_t, ui_quiet_until;
static char ui_hot[24], ui_msg[32];
static uint32_t ui_btn_t[14];                 /* press times */
static uint8_t ui_btn_combo[14];
static int32_t ui_vol_drawn = -1;
static uint32_t ui_last_draw;

#define UI_ACC pal[3]
#define HOLD_MS 400u

/* the three switchable columns: TREM, CHO, DLY */
static const uint8_t COL_SW[3] = {P_TW_SW, P_CP_SW, P_DL_SW};
static const uint8_t COL_K[3][2] = {{P_TW_DEPTH, P_TW_RATE}, {P_CP_DEPTH, P_CP_SPEED}, {P_DL_DEPTH, P_DL_TIME}};
static const uint8_t COL_LAYER[3][4] = {{P_TW_SW, P_TW_SYNC, P_TW_MODE, P_TW_SHAPE},
                                        {P_CP_SW, P_CP_SYNC, P_CP_FDBK, 0xFF},
                                        {P_DL_SW, P_DL_SYNC, P_DL_FDBK, P_DL_TONE}};
static const uint8_t PAGE_P[5][4] = {{0}, {P_TONE, P_BARK, P_BELL, P_HAMMER}, {P_DECAY, P_RELEASE, P_NOISE, 0xFF},
                                     {0}, {0}};
static const char *const PAGE_NAME[5] = {"", "VOICING", "VOICING 2", "TEMPO", "SCALE"};
static const char *const SCALE_NAME[11] = {"MAJ", "MIN", "DOR", "MIX", "LYD", "PHRY", "HARM", "MEL", "PEN", "MPEN", "BLUE"};
static const char *const NOTE_NAME[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
static const char *const SCMODE_NAME[3] = {"OFF", "SNAP", "DEG"};

static void ui_mark(uint32_t e) { ui_dirty |= 1u << e; }
static void ui_mark_param(uint32_t id)
{
    switch (id) {
    case P_TYPE: ui_mark(E_COL0); break;
    case P_DRIVE: ui_mark(E_COL0 + 1); break;
    case P_TW_SW: case P_TW_DEPTH: case P_TW_RATE: case P_TW_SYNC: ui_mark(E_COL0 + 2); break;
    case P_CP_SW: case P_CP_DEPTH: case P_CP_SPEED: case P_CP_SYNC: ui_mark(E_COL0 + 3); break;
    case P_DL_SW: case P_DL_DEPTH: case P_DL_TIME: case P_DL_SYNC: ui_mark(E_COL0 + 4); break;
    case P_RV_DEPTH: ui_mark(E_COL0 + 5); break;
    default: break;
    }
    ui_mark(E_CARDS);
}
static void ui_say(const char *s)
{
    uint32_t i = 0;
    while (s[i] && i < sizeof ui_msg - 1u) { ui_msg[i] = s[i]; i++; }
    ui_msg[i] = 0;
    ui_msg_t = fm1_ms;
    ui_mark(E_FOOTER);
}
static void ui_hot_param(uint32_t id)
{
    char v[8];
    uint32_t i = 0, j = 0;
    const char *l = PARAMS[id].lab;
    param_text(id, v);
    while (l[i]) ui_hot[j++] = l[i++];
    ui_hot[j++] = ' ';
    for (i = 0; v[i]; i++) ui_hot[j++] = v[i];
    ui_hot[j] = 0;
    ui_hot_t = fm1_ms;
    ui_mark(E_FOOTER);
}
static void ui_param_turn(uint32_t id, int32_t d)
{
    int32_t v;
    if (id >= P_COUNT || !d) return;
    v = set.panel[id] + d;
    if (PARAMS[id].fmt == F_ENUM || PARAMS[id].fmt == F_DIV) { if (v < 0) v = 0; }
    param_set(id, v);
    ui_mark_param(id);
    ui_hot_param(id);
}

/* ---- drawing helpers */
static void p_strcpy(char *d, const char *s, uint32_t max) { uint32_t i = 0; while (s[i] && i < max) { d[i] = s[i]; i++; } d[i] = 0; }
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
    char s[8];
    cv_begin(240, 20, C_BLACK);
    {
        char nm[20];
        if (ui_page == PG_PANEL && set.preset < 28u) preset_name(set.preset, nm);
        else p_strcpy(nm, ui_page == PG_PANEL ? "FRODHES" : PAGE_NAME[ui_page], 14);
        nm[13] = 0;
        cv_text(2, 2, &FONT_S, nm, C_HI);
    }
    p_fmt_int(s, set.bpm10 / 10);
    {
        uint32_t n = 0; while (s[n]) n++;
        s[n++] = '.'; s[n++] = (char)('0' + set.bpm10 % 10); s[n] = 0;
    }
    cv_text(118, 2, &FONT_S, s, C_AMB);
    if (set.oct) { char o[4] = {'O', set.oct < 0 ? '-' : '+', (char)('0' + (set.oct < 0 ? -set.oct : set.oct)), 0}; cv_text(166, 2, &FONT_S, o, C_GRAY); }
    if (set.sc_mode) cv_text(194, 2, &FONT_S, set.sc_mode == 1 ? "SNP" : "DEG", C_GRAY);
    if (rh_hold) cv_text(214, 2, &FONT_S, "PD", C_HI);
    cv_rect(0, 19, 240, 1, C_LINE);
    cv_blit(0, 0);
}

static const char *const COL_TITLE[7] = {"TYPE", "DRV", "TREM", "CHO", "DLY", "REV", "VOL"};
static void draw_titles(void)
{
    uint32_t i;
    cv_begin(240, 18, C_BLACK);
    for (i = 0; i < 7u; i++) {
        int32_t x = 2 + 34 * (int32_t)i;
        int focus = ui_page == PG_PANEL && i >= 2u && i - 2u == set.focus;
        if (focus) cv_rect(x, 0, 32, 17, UI_ACC);
        text_c(x, 1, 32, &FONT_S, COL_TITLE[i], focus ? C_BLACK : C_GRAY);
    }
    cv_blit(0, 21);
}

static void draw_slot(int32_t y, uint32_t id, const char *lab)
{
    char v[8];
    text_c(0, y, 32, &FONT_S, lab ? lab : PARAMS[id].lab, C_GRAY);
    gauge(2, y + 18, 28, set.panel[id], PARAMS[id].hi);
    param_text(id, v);
    if (id == P_DL_TIME && set.panel[P_DL_SYNC]) p_strcpy(v, N_SYNC[set.panel[P_DL_SYNC]], 4);
    if (id == P_TW_RATE && set.panel[P_TW_SW] == 2) fmt_r10(v, set.panel[id]);
    text_c(0, y + 28, 32, &FONT_S, v, C_HI);
}

static void draw_col(uint32_t c)
{
    int32_t x = 2 + 34 * (int32_t)c;
    cv_begin(32, 160, C_BLACK);
    if (c == 0) {                                          /* TYPE: the four types, the current one lit */
        uint32_t t;
        for (t = 0; t < 4u; t++) {
            int on = set.panel[P_TYPE] == t;
            if (on) cv_rect(0, (int32_t)t * 16, 32, 16, UI_ACC);
            text_c(0, (int32_t)t * 16, 32, &FONT_S, N_TYPE[t], on ? C_BLACK : C_DIM);
        }
    } else if (c >= 2u && c <= 4u) {                       /* a 3-way switch: A / OFF / B */
        uint32_t k = c - 2u, sw = set.panel[COL_SW[k]], r;
        const char *const *nm = PARAMS[COL_SW[k]].names;
        static const uint8_t ROWS[3] = {1, 0, 2};          /* drawn top to bottom: A, OFF, B */
        for (r = 0; r < 3u; r++) {
            uint32_t pos = ROWS[r];
            int on = sw == pos;
            if (on) cv_rect(0, (int32_t)r * 22, 32, 18, pos ? UI_ACC : C_LINE);
            text_c(0, (int32_t)r * 22 + 1, 32, &FONT_S, nm[pos], on ? (pos ? C_BLACK : C_HI) : C_DIM);
        }
    }
    cv_rect(0, 65, 32, 1, C_LINE);
    switch (c) {
    case 1: draw_slot(68, P_DRIVE, "AMT"); break;
    case 2: draw_slot(68, P_TW_DEPTH, 0); draw_slot(114, P_TW_RATE, set.panel[P_TW_SW] == 2 ? "RESO" : "RATE"); break;
    case 3: draw_slot(68, P_CP_DEPTH, 0); draw_slot(114, P_CP_SPEED, 0); break;
    case 4: draw_slot(68, P_DL_DEPTH, 0); draw_slot(114, P_DL_TIME, 0); break;
    case 5: draw_slot(68, P_RV_DEPTH, "AMT"); break;
    case 6: {
        char v[8];
        int32_t lv = (int32_t)UI_MASTER_Q12 * 127 / 4096;
        text_c(0, 68, 32, &FONT_S, "LVL", C_GRAY);
        gauge(2, 86, 28, lv, 127);
        fmt_r10(v, lv);
        text_c(0, 96, 32, &FONT_S, v, C_HI);
        ui_vol_drawn = (int32_t)UI_MASTER_Q12;
        break;
    }
    default: break;
    }
    cv_blit((uint32_t)x, 40);
}

static void draw_footer(void)
{
    cv_begin(240, 36, C_BLACK);
    cv_rect(0, 0, 240, 1, C_LINE);
    if (ui_hot_t && fm1_ms - ui_hot_t < 1500u) {
        text_c(0, 3, 240, &FONT_L, ui_hot, C_HI);
    } else {
        const char *a = "K1 DPTH K2 RATE K3 DRV K4 REV", *b = "LFO:TRM FX:CHO ARP:DLY PLY:TAP";
        if (ui_page == PG_PANEL && set.focus == 2) a = "K1 DPTH K2 TIME K3 DRV K4 REV";
        if (ui_page == PG_PANEL && set.focus == 1) a = "K1 DPTH K2 SPD K3 DRV K4 REV";
        if (ui_layer != 0xFF) { a = ui_layer == 0 ? "K1 SW K2 SYNC K3 MODE K4 SHPE" : ui_layer == 1 ? "K1 SW K2 SYNC K3 FDBK" : "K1 SW K2 SYNC K3 FDBK K4 TONE"; b = "LET GO: PANEL"; }
        if (ui_page == PG_VOICE) { a = "K1TONE K2BARK K3BELL K4HAMR"; b = "[ENV]DECAY  [HOME]PANEL"; }
        if (ui_page == PG_VOICE2) { a = "K1 DCAY K2 REL K3 NOIS K4 TUNE"; b = "[EDIT]VOICING [HOME]PANEL"; }
        if (ui_page == PG_GLO) {
            static char cpu[32];
            p_strcpy(cpu, "AUDIO ", 6); p_fmt_int(cpu + 6, (int32_t)prof_us);
            { uint32_t n = 0; while (cpu[n]) n++; p_strcpy(cpu + n, " MAX ", 5); n += 5; p_fmt_int(cpu + n, (int32_t)prof_max_us);
              while (cpu[n]) n++; p_strcpy(cpu + n, " US", 3); }
            a = "K1 BPM K2 KVEL K3 TUNE"; b = cpu;
        }
        if (ui_page == PG_SCALE) { a = "K1 MODE K2 SCAL K3 ROOT K4 TRN"; b = "[HOME]PANEL"; }
        if (ui_msg_t && fm1_ms - ui_msg_t < 1500u) b = ui_msg;
        text_c(0, 2, 240, &FONT_S, a, C_GRAY);
        text_c(0, 19, 240, &FONT_S, b, C_DIM);
    }
    cv_blit(0, 204);
}

static void card(int32_t k, const char *lab, const char *val, int32_t v, int32_t hi)
{
    int32_t x = 60 * k;
    cv_rect(x + 1, 0, 57, 80, C_LINE);
    cv_rect(x + 2, 1, 55, 78, C_BLACK);
    text_c(x, 4, 59, &FONT_S, lab, C_GRAY);
    text_c(x, 26, 59, &FONT_S, val, C_HI);
    if (hi) gauge(x + 4, 52, 51, v, hi);
    {
        char kk[3] = {'K', (char)('1' + k), 0};
        text_c(x, 62, 59, &FONT_S, kk, C_DIM);
    }
}

static void draw_cards(void)
{
    char v[4][8];
    uint32_t k;
    cv_begin(240, 80, C_BLACK);
    if (ui_page == PG_VOICE || ui_page == PG_VOICE2) {
        for (k = 0; k < 4u; k++) {
            if (PAGE_P[ui_page][k] == 0xFF) { p_fmt_int(v[k], set.tune); card((int32_t)k, "TUNE", v[k], set.tune + 50, 100); continue; }
            param_text(PAGE_P[ui_page][k], v[k]); card((int32_t)k, PARAMS[PAGE_P[ui_page][k]].lab, v[k], set.panel[PAGE_P[ui_page][k]], 127);
        }
    } else if (ui_page == PG_GLO) {
        p_fmt_int(v[0], set.bpm10 / 10);
        p_fmt_int(v[1], set.key_vel);
        p_fmt_int(v[2], set.tune);
        p_strcpy(v[3], set.clock == 0 ? "INT" : set.clock == 1 ? "AUTO" : "USB", 4);
        card(0, "BPM", v[0], set.bpm10 - 400, 2000);
        card(1, "KVEL", v[1], set.key_vel, 127);
        card(2, "TUNE", v[2], set.tune + 50, 100);
        card(3, "CLK", v[3], 0, 0);
    } else if (ui_page == PG_SCALE) {
        p_fmt_int(v[3], set.sc_trans);
        card(0, "MODE", SCMODE_NAME[set.sc_mode], set.sc_mode, 2);
        card(1, "SCAL", SCALE_NAME[set.sc_scale], set.sc_scale, 10);
        card(2, "ROOT", NOTE_NAME[set.sc_root], set.sc_root, 11);
        card(3, "TRN", v[3], set.sc_trans + 24, 48);
    }
    cv_blit(0, 40);
}

static void ui_draw_page_change(void)
{
    if (ui_page == PG_PANEL) ui_dirty |= (1u << E_TITLES) | (0x7Fu << E_COL0) | (1u << E_HEADER) | (1u << E_FOOTER);
    else ui_dirty |= (1u << E_HEADER) | (1u << E_CARDS) | (1u << E_FOOTER) | (1u << E_KEYS) | (1u << E_TITLES);
}

/* one element per pass, in priority order */
static void ui_draw(void)
{
    uint32_t e, d;
    if ((int32_t)UI_MASTER_Q12 != ui_vol_drawn && ui_page == PG_PANEL) {
        int32_t dv = (int32_t)UI_MASTER_Q12 - ui_vol_drawn;
        if (dv > 40 || dv < -40) ui_mark(E_COL0 + 6);
    }
    if (ui_hot_t && fm1_ms - ui_hot_t >= 1500u && fm1_ms - ui_hot_t < 1600u) ui_mark(E_FOOTER);
    if (ui_page == PG_GLO && (fm1_ms & 511u) == 0u) ui_mark(E_FOOTER);          /* the audio time, twice a second */
    if (ui_msg_t && fm1_ms - ui_msg_t >= 1500u && fm1_ms - ui_msg_t < 1600u) ui_mark(E_FOOTER);
    if (!ui_dirty || fm1_ms - ui_last_draw < 4u) return;
    if (ui_page == PG_PANEL) ui_dirty &= ~((1u << E_CARDS) | (1u << E_KEYS));
    else ui_dirty &= ~(0x7Fu << E_COL0);
    d = ui_dirty;
    e = d >> E_FOOTER & 1u ? E_FOOTER : d & 1u ? E_HEADER : d >> E_TITLES & 1u ? E_TITLES : 0xFF;
    if (e == 0xFF) for (e = 0; e < E_N && !(d >> e & 1u); e++) {}
    ui_dirty &= ~(1u << e);
    ui_last_draw = fm1_ms;
    if (e == E_HEADER) draw_header();
    else if (e == E_TITLES) {
        if (ui_page == PG_PANEL) draw_titles();
        else { cv_begin(240, 18, C_BLACK); cv_blit(0, 21); }
    } else if (e >= E_COL0 && e <= E_COL6) draw_col(e - E_COL0);
    else if (e == E_FOOTER) draw_footer();
    else if (e == E_CARDS) draw_cards();
    else if (e == E_KEYS) { cv_begin(240, 64, C_BLACK); cv_blit(0, 124); }
}

/* ---- input */
static void ui_goto(uint32_t pg)
{
    if (pg != ui_page) lcd_fill(0, 20, 240, 184, C_BLACK);     /* clear the body: nothing of the last page stays */
    ui_page = (uint8_t)pg;
    ui_layer = 0xFF;
    ui_draw_page_change();
}

static void ui_column_button(uint32_t k, uint32_t pressed, uint32_t released, uint32_t held, uint32_t btn)
{
    if (pressed) { ui_btn_t[btn] = fm1_ms; ui_btn_combo[btn] = 0; }      /* (a fast tap: press and release in one pass) */
    if (held && !ui_btn_combo[btn] && fm1_ms - ui_btn_t[btn] >= HOLD_MS && ui_layer != k) {
        if (ui_page != PG_PANEL) ui_goto(PG_PANEL);
        ui_layer = (uint8_t)k; set.focus = (uint8_t)k; ui_btn_combo[btn] = 1;
        ui_mark(E_TITLES); ui_mark(E_FOOTER);
    }
    if (released) {
        if (ui_layer == k) { ui_layer = 0xFF; ui_quiet_until = fm1_ms + 250u; ui_mark(E_FOOTER); return; }
        if (ui_btn_combo[btn]) return;
        if (ui_page != PG_PANEL) { ui_goto(PG_PANEL); set.focus = (uint8_t)k; ui_mark(E_TITLES); return; }
        if (set.focus != k) { set.focus = (uint8_t)k; ui_mark(E_TITLES); ui_mark(E_FOOTER); set_dirty = 1; set_t_change = fm1_ms; return; }
        {   /* focused: OFF <-> the last one used */
            uint32_t id = COL_SW[k], sw = set.panel[id];
            if (sw) { set.last_on[k] = (uint8_t)sw; param_set(id, 0); }
            else param_set(id, set.last_on[k]);
            ui_mark_param(id);
            ui_hot_param(id);
        }
    }
}

static void tempo_tap(void);                           /* tempo.c */
static void tempo_set10(uint32_t b);

static void ui_input(uint32_t b, uint32_t rel)
{
    static const uint8_t COLBTN[3] = {BTN_LFO, BTN_FX, BTN_ARP};
    int32_t d[7];
    uint32_t k, i;
    for (i = 0; i < 7u; i++) d[i] = fm1_enc_take(i);
    {
        uint8_t was = pr_save_mode;
        presets_poll(d[ENC_PRESETS], was ? d[ENC_KNOB1] : 0, b, rel);
        if (was || pr_save_mode) return;               /* the save mode owns this pass: PRESETS, K1, OCT+/OCT- */
    }
    for (k = 0; k < 3u; k++) {
        uint32_t bt = COLBTN[k];
        if (fm1_in.buttons >> bt & 1u && (d[ENC_KNOB1] | d[ENC_KNOB2] | d[ENC_KNOB3] | d[ENC_KNOB4]) && ui_layer != k) {
            if (ui_page != PG_PANEL) ui_goto(PG_PANEL);
            ui_layer = (uint8_t)k; set.focus = (uint8_t)k; ui_btn_combo[bt] = 1; ui_mark(E_TITLES); ui_mark(E_FOOTER);
        }
        ui_column_button(k, b >> bt & 1u, rel >> bt & 1u, fm1_in.buttons >> bt & 1u, bt);
    }
    if (b >> BTN_EDIT & 1u) ui_goto(ui_page == PG_VOICE ? PG_PANEL : PG_VOICE);
    if (b >> BTN_ENV & 1u) ui_goto(ui_page == PG_VOICE2 ? PG_PANEL : PG_VOICE2);
    if (b >> BTN_GLO & 1u) ui_goto(ui_page == PG_GLO ? PG_PANEL : PG_GLO);
    if (b >> BTN_SEL & 1u) ui_goto(ui_page == PG_SCALE ? PG_PANEL : PG_SCALE);
    if (b >> BTN_HOME & 1u) ui_goto(PG_PANEL);
    if (b >> BTN_PLAY & 1u) tempo_tap();
    if (b >> BTN_SEQ & 1u) ui_say("SEQ: LATER");
    if (b >> BTN_OCT_DOWN & 1u && set.oct > -2) { set.oct--; set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_HEADER); }
    if (b >> BTN_OCT_UP & 1u && set.oct < 2) { set.oct++; set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_HEADER); }

    if (d[ENC_ALGORITHM]) { ui_param_turn(P_TYPE, d[ENC_ALGORITHM] > 0 ? 1 : -1); ui_say(N_TYPE_LONG[set.panel[P_TYPE]]); }
    if (d[ENC_SELECT]) {                                /* SELECT = DYN: the on-board keys' velocity */
        int32_t v = set.key_vel + d[ENC_SELECT] * (d[ENC_SELECT] > 1 || d[ENC_SELECT] < -1 ? 4 : 1);
        set.key_vel = (uint8_t)(v < 1 ? 1 : v > 127 ? 127 : v);
        p_strcpy(ui_hot, "KVEL ", 5); p_fmt_int(ui_hot + 5, set.key_vel); ui_hot_t = fm1_ms; ui_mark(E_FOOTER);
        set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_CARDS);
    }
    if (fm1_ms < ui_quiet_until) return;                /* just after a layer closed: ignore the knobs */
    if (ui_layer != 0xFF) {
        for (k = 0; k < 4u; k++) {
            uint32_t id = COL_LAYER[ui_layer][k];
            if (d[ENC_KNOB1 + k] && id != 0xFF) {
                ui_param_turn(id, d[ENC_KNOB1 + k] > 0 ? 1 : -1);
                if (id == P_TW_SYNC || id == P_CP_SYNC || id == P_DL_SYNC) tempo_sync_publish();
            }
        }
        return;
    }
    switch (ui_page) {
    case PG_PANEL:
        ui_param_turn(COL_K[set.focus][0], d[ENC_KNOB1]);
        if (d[ENC_KNOB2]) {
            if (set.focus == 2 && set.panel[P_DL_SYNC]) ui_param_turn(P_DL_SYNC, d[ENC_KNOB2] > 0 ? -1 : 1);
            else if (set.focus == 0 && set.panel[P_TW_SYNC]) ui_param_turn(P_TW_SYNC, d[ENC_KNOB2] > 0 ? -1 : 1);
            else ui_param_turn(COL_K[set.focus][1], d[ENC_KNOB2]);
        }
        ui_param_turn(P_DRIVE, d[ENC_KNOB3]);
        ui_param_turn(P_RV_DEPTH, d[ENC_KNOB4]);
        break;
    case PG_VOICE:
    case PG_VOICE2:
        for (k = 0; k < 4u; k++) {
            if (PAGE_P[ui_page][k] == 0xFF) {
                if (d[ENC_KNOB1 + k]) {
                    int32_t v = set.tune + d[ENC_KNOB1 + k];
                    set.tune = (int8_t)(v < -50 ? -50 : v > 50 ? 50 : v); rh_tune_t = TUNE_T[set.tune + 50];
                    set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_CARDS);
                }
            } else ui_param_turn(PAGE_P[ui_page][k], d[ENC_KNOB1 + k]);
        }
        break;
    case PG_GLO:
        if (d[ENC_KNOB1]) tempo_set10((uint32_t)((int32_t)set.bpm10 + d[ENC_KNOB1] * 10));
        if (d[ENC_KNOB2]) { int32_t v = set.key_vel + d[ENC_KNOB2]; set.key_vel = (uint8_t)(v < 1 ? 1 : v > 127 ? 127 : v); }
        if (d[ENC_KNOB3]) { int32_t v = set.tune + d[ENC_KNOB3]; set.tune = (int8_t)(v < -50 ? -50 : v > 50 ? 50 : v); rh_tune_t = TUNE_T[set.tune + 50]; }
        if (d[ENC_KNOB1] | d[ENC_KNOB2] | d[ENC_KNOB3]) { set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_CARDS); ui_mark(E_HEADER); }
        break;
    case PG_SCALE:
        if (d[ENC_KNOB1]) { int32_t v = set.sc_mode + (d[ENC_KNOB1] > 0 ? 1 : -1); set.sc_mode = (uint8_t)(v < 0 ? 0 : v > 2 ? 2 : v); }
        if (d[ENC_KNOB2]) { int32_t v = set.sc_scale + (d[ENC_KNOB2] > 0 ? 1 : -1); set.sc_scale = (uint8_t)(v < 0 ? 0 : v > 10 ? 10 : v); }
        if (d[ENC_KNOB3]) set.sc_root = (uint8_t)((set.sc_root + (d[ENC_KNOB3] > 0 ? 1 : 11)) % 12u);
        if (d[ENC_KNOB4]) { int32_t v = set.sc_trans + d[ENC_KNOB4]; set.sc_trans = (int8_t)(v < -24 ? -24 : v > 24 ? 24 : v); }
        if (d[ENC_KNOB1] | d[ENC_KNOB2] | d[ENC_KNOB3] | d[ENC_KNOB4]) { set_dirty = 1; set_t_change = fm1_ms; ui_mark(E_CARDS); ui_mark(E_HEADER); }
        break;
    default: break;
    }
}

/* ---- LEDs (§10.4), once per pass */
static void ui_leds(uint32_t lit, uint32_t beat_on)
{
    static const uint8_t COLBTN[3] = {BTN_LFO, BTN_FX, BTN_ARP};
    uint32_t k;
    for (k = 0; k < 3u; k++) fm1_led_key(COLBTN[k], set.panel[COL_SW[k]] != 0 || (ui_page == PG_PANEL && set.focus == k && (fm1_ms & 512u)));
    fm1_led_key(BTN_EDIT, ui_page == PG_VOICE);
    fm1_led_key(BTN_ENV, ui_page == PG_VOICE2);
    fm1_led_key(BTN_GLO, ui_page == PG_GLO);
    fm1_led_key(BTN_SEL, ui_page == PG_SCALE);
    fm1_led_key(BTN_HOME, ui_page == PG_PANEL);
    fm1_led_key(BTN_PLAY, beat_on);
    fm1_led_key(BTN_OCT_DOWN, set.oct < 0);
    fm1_led_key(BTN_OCT_UP, set.oct > 0);
    fm1_led_key(BTN_REC, rh_hold);
    for (k = 0; k < 27u; k++) fm1_led_key(KEY_LED(k), lit >> k & 1u);
}
