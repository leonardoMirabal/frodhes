/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes UI: one page at a time, four big cards = the four knobs (the FM-1 has 4 knobs and a 240 x 240 screen,
 * so nothing else competes for them). A draw scheduler sends at most one screen element per main-loop pass
 * (a full screen is ~77 ms of SPI: drawn at once it would delay the keys).
 *
 *   SELECT     turns through the pages (the order of the chain):
 *              PERFORM FRODHES (the main page) VOICE DECAY COMP FILTER DRIVE TREM/WAH PHASER CHORUS DELAY REVERB
 *              TEMPO SCALE CHORD ARPEGGIO LFO SEQUENCER ASSIGN SETUP MIDI (PG_ORDER)
 *   K1..K4     the four cards of the page, always
 *   ALGORITHM  TYPE (on SEQUENCER: a cursor, step by step)   PRESETS  sounds (presets.c; on SEQUENCER: bar by bar)
 *   shortcuts  HOME = PERFORM (again: the main page), EDIT = CHORD (on it: chord mode on / off), LFO / ENV / SEL =
 *              TREM / PHASER / DELAY (on that page: the effect on / off; on ARPEGGIO SEL is HOLD), SEQ = SEQUENCER (on
 *              it: REC), ARP = ARPEGGIO (on it: on / off), GLO = TEMPO (again: SETUP, MIDI).
 *   FX         all effects off / on (a bypass: no setting changes; not saved). PLAY: tap tempo (on SEQUENCER: start /
 *              stop). REC: sustain. */

#include "aatext.c"                                   /* anti-aliased Inter Tight text (Felucca), the FOCUS look */

#define PR_NF_UI PR_NF_SET                            /* presets.c: PR_NF factory presets, then the user ones */
#define PR_NALL_UI (PR_NF_SET + 16u)
static uint8_t pr_save_mode, pr_save_slot;            /* presets.c */
static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel);
static void preset_name(uint32_t idx, char *s);
static void tempo_tap(void);                          /* tempo.c */
static void tempo_set10(uint32_t b);

/* the pages. The ids are the host API's (tests: card.<page>.<k>), so they stay: 4 was DECAY and 21 MOTOR until the
 * engines' pages came from their tables (eng_pages.h): VOICE 1/n .. n/n, one PG_VOICE page with its number ui_vp */
enum { PG_PERF, PG_ASSIGN, PG_PIANO, PG_VOICE, PG_RETIRED4, PG_COMP, PG_FILTER, PG_TREM, PG_PHA, PG_CHO, PG_DELAY,
       PG_TEMPO, PG_SCALE, PG_CHORD, PG_SEQ, PG_ARP, PG_LFO, PG_SETUP, PG_MIDI, PG_DRIVE, PG_REVERB, PG_N };
enum { E_HEADER = 0, E_TITLE = 1, E_CARD0 = 2, E_FOOTER = 6, E_METER = 7, E_ROLL = 8, E_N = 9, E_CARDS = 10 };   /* E_CARDS: all four;
                                    * E_METER: COMP's meter / SEQUENCER's cells, E_ROLL: SEQUENCER's NOW / NEXT */
/* card slots: a parameter id (< P_COUNT) or one of these */
enum { S_KVEL = 64, S_TUNE, S_BPM, S_CLOCK, S_SCMODE, S_SCALE, S_ROOT, S_TRANS, S_TWKIND, S_PERF0, S_ASG0 = S_PERF0 + 4,
       S_ARPMODE = S_ASG0 + 4, S_ARPRATE, S_ARPOCT, S_ARPGATE, S_USBVOL, S_CHTYPE, S_CHINV, S_CHVOICE, S_CHSTRUM,
       S_SQBARS, S_SQQUANT, S_SQSWING, S_SQEDIT, S_MCH, S_TRS, S_NONE = 0xFF };
#include "eng_pages.h"                                /* the engines' controls: VOICE pages, FRODHES K2 / K3, names */
static const engui_t *eng_ui(void) { return &ENG_UI[PV(P_TYPE) < N_TYPE_N ? TYPE_ENGINE[PV(P_TYPE)] : 0u]; }
static const ectl_t *eng_ctl(uint32_t id)              /* the TYPE's engine's control for a slot (0: not one of its) */
{
    const engui_t *e = eng_ui();
    uint32_t i;
    for (i = 0; i < e->n; i++) if (e->c[i].id == id) return &e->c[i];
    return 0;
}
static const ectl_t *eng_head(uint32_t h)              /* the engine's headline control h (1: FRODHES K2, 2: K3) */
{
    const engui_t *e = eng_ui();
    uint32_t i;
    for (i = 0; i < e->n; i++) if (e->c[i].head == h) return &e->c[i];
    return 0;
}
static uint32_t vp_count(void) { return (eng_ui()->n + 3u) / 4u; }   /* the engine's VOICE pages */
static const ectl_t *voice_ctl(uint32_t vp, uint32_t k)   /* VOICE page vp's K(k+1) (0: an empty row) */
{
    uint32_t i = vp * 4u + k;
    return i < eng_ui()->n ? &eng_ui()->c[i] : 0;
}

static int sq_browsing(void);                           /* the SEQUENCER page's cursor (below) */
static uint32_t sq_delete_step(void);
static void sq_browse(int32_t d);
/* the SELECT order (0.9.3, user: the instrument pages next to each other right after PERFORM; ASSIGN, rarely used,
 * next to SETUP); VOICE stands for all the engine's VOICE pages */
#define PG_NORD 20u
static const uint8_t PG_ORDER[PG_NORD] = {PG_PERF, PG_PIANO, PG_VOICE, PG_COMP, PG_FILTER, PG_DRIVE, PG_TREM, PG_PHA,
                                          PG_CHO, PG_DELAY, PG_REVERB, PG_TEMPO, PG_SCALE, PG_CHORD, PG_ARP, PG_LFO,
                                          PG_SEQ, PG_ASSIGN, PG_SETUP, PG_MIDI};   /* DRIVE after FILTER, REVERB the
                                    * last effect, as in the chain; SEQUENCER after LFO (user, TODO 3) */
static uint8_t ui_vp;                                   /* the VOICE page shown: 0 .. vp_count() - 1 */
static int ui_bars(void) { return TYPE_IS_BARS(PV(P_TYPE)); }    /* the main loop's TYPE (not the ISR's latch) */
static void ui_goto(uint32_t pg);
static void ui_goto_v(uint32_t pg, uint32_t vp);
static uint32_t pg_count(void) { return PG_NORD - 1u + vp_count(); }    /* the pages SELECT walks */
static uint32_t pg_pos(uint32_t pg, uint32_t vp)        /* a page's place in the SELECT order */
{
    uint32_t i, k = 0;
    for (i = 0; i < PG_NORD; i++) {
        if (PG_ORDER[i] == pg) return k + (pg == PG_VOICE ? vp : 0u);
        k += PG_ORDER[i] == PG_VOICE ? vp_count() : 1u;
    }
    return 0;
}
static uint32_t pg_at(uint32_t k, uint32_t *vp)         /* the k-th page (and its VOICE number) */
{
    uint32_t i, n;
    for (i = 0; i < PG_NORD; i++) {
        n = PG_ORDER[i] == PG_VOICE ? vp_count() : 1u;
        if (k < n) { *vp = PG_ORDER[i] == PG_VOICE ? k : 0u; return PG_ORDER[i]; }
        k -= n;
    }
    *vp = 0;
    return PG_PERF;
}
static const char *const PAGE_TITLE[PG_N] = {"PERFORM", "ASSIGN", "FRODHES", "VOICE", "", "COMPRESSOR", "FILTER", "TREM/WAH",
                                             "PHASER", "CHORUS", "DELAY", "TEMPO", "SCALE", "CHORD", "SEQUENCER",
                                             "ARPEGGIO", "LFO", "SETUP", "MIDI", "DRIVE", "REVERB"};
static const uint8_t PAGE_S[PG_N][4] = {                /* VOICE and FRODHES K2 / K3: the engine's (slot_at) */
    {S_PERF0, S_PERF0 + 1, S_PERF0 + 2, S_PERF0 + 3},
    {S_ASG0, S_ASG0 + 1, S_ASG0 + 2, S_ASG0 + 3},
    {P_TYPE, S_NONE, S_NONE, S_KVEL},
    {S_NONE, S_NONE, S_NONE, S_NONE},
    {S_NONE, S_NONE, S_NONE, S_NONE},
    {P_CMP_AMT, P_CMP_RATIO, P_CMP_ATK, P_CMP_GAIN},
    {P_FL_TYPE, P_FL_FREQ, P_FL_RESO, P_FL_LFO},
    {S_TWKIND, P_TW_DEPTH, P_TW_RATE, P_TW_SHAPE},
    {P_PH_SW, P_PH_DEPTH, P_PH_SPEED, P_PH_FDBK},
    {P_CP_SW, P_CP_DEPTH, P_CP_SPEED, P_CP_SYNC},
    {P_DL_SW, P_DL_DEPTH, P_DL_TIME, P_DL_FDBK},
    {S_BPM, P_TW_SYNC, P_PH_SYNC, P_DL_SYNC},
    {S_SCMODE, S_SCALE, S_ROOT, S_TRANS},
    {S_CHTYPE, S_CHINV, S_CHVOICE, S_CHSTRUM},
    {S_SQBARS, S_SQQUANT, S_SQSWING, S_SQEDIT},
    {S_ARPMODE, S_ARPRATE, S_ARPOCT, S_ARPGATE},
    {P_LFO_SHAPE, P_LFO_RATE, P_LFO_SYNC, P_LFO_RETRIG},
    {P_DL_TONE, P_LEVEL, S_USBVOL, S_NONE},
    {S_MCH, S_TRS, S_CLOCK, S_NONE},
    {P_DRV_SW, P_DRIVE, P_DRV_TONE, P_DRV_MIX},
    {P_RV_DEPTH, P_RV_DECAY, P_RV_TONE, P_RV_PREDLY},
};
static const char *const PAGE_LAB[PG_N][4] = {
    {"", "", "", ""},                                   /* PERFORM: the assigned parameter's name (slot_label) */
    {"PLAYS", "PLAYS", "PLAYS", "PLAYS"},
    {"TYPE", "", "", "KEY VEL"},
    {"", "", "", ""},
    {"", "", "", ""},
    {"THRESH", "RATIO", "ATTACK", "MAKEUP"},
    {"TYPE", "FREQ", "RESO", "LFO"},
    {"EFFECT", "DEPTH", "RATE", "SHAPE"},
    {"EFFECT", "DEPTH", "SPEED", "FEEDBK"},
    {"EFFECT", "DEPTH", "SPEED", "SYNC"},
    {"EFFECT", "MIX", "TIME", "FEEDBK"},
    {"BPM", "TREM SYNC", "PHA SYNC", "DLY SYNC"},
    {"MODE", "SCALE", "ROOT", "TRANSP"},
    {"TYPE", "INVERSION", "VOICING", "STRUM"},
    {"BARS", "QUANT", "SWING", "EDIT"},
    {"MODE", "RATE", "OCTAVES", "GATE"},
    {"SHAPE", "RATE", "SYNC", "RETRIG"},
    {"DLY TONE", "LEVEL", "USB VOL", ""},
    {"CHANNEL", "TRS IN", "CLOCK", ""},
    {"EFFECT", "AMOUNT", "TONE", "MIX"},
    {"MIX", "DECAY", "TONE", "PRE-DELAY"},
};
static const char *const PAGE_HINT[PG_N] = {"THE KNOBS: SET ON ASSIGN", "WHAT EACH PERFORM KNOB PLAYS",
                                            "FX: ALL EFFECTS ON / OFF", "", "",
                                            "K1 THRESH: UP = ON, 0 = OFF", "THE LFO: ITS OWN PAGE", "LFO BUTTON: ON / OFF", "ENV BUTTON: ON / OFF",
                                            "K1: ON / OFF", "SEL: ON / OFF  K1: DIG / ANA", "PLAY: TAP TEMPO", "DEGREE: WHITE KEYS PLAY IT",
                                            "EDIT: CHORDS ON / OFF", "", "ARP: ON / OFF   SEL: HOLD", "MOVES THE FILTER (ITS LFO)", "",
                                            "USB AND TRS: THE SAME CHANNEL", "K1: ON / OFF", "MIX ALSO SETS THE SIZE"};
static uint32_t ui_vp_ok(void) { return ui_vp < vp_count() ? ui_vp : vp_count() - 1u; }
static const char *page_hint(uint32_t pg) { return pg == PG_VOICE ? eng_ui()->hint[ui_vp_ok()] : PAGE_HINT[pg]; }
static const char *const SCALE_NAME[11] = {"MAJOR", "MINOR", "DORIAN", "MIXO", "LYDIAN", "PHRYG", "HARMON", "MELODIC",
                                           "PENTA", "MPENTA", "BLUES"};
static const char *const NOTE_NAME[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
static const char *const SCMODE_NAME[3] = {"OFF", "SNAP", "DEGREE"};
static const char *const TWKIND_NAME[4] = {"OFF", "PAN", "TREM", "WAH"};
static const char *const CLOCK_NAME[2] = {"INT", "EXT"};   /* EXT: MIDI clock from USB or TRS */

static uint8_t ui_page;
static uint32_t ui_dirty = ~0u;
static uint32_t ui_msg_t, ui_hot_t, ui_last_draw;
static uint8_t ui_hot_card = 0xFF;
static char ui_msg[32];

/* ---- the look (METERS, the user's choice 2026-10-18; private/UI-SIMPLIFY-PLAN.md): a header, the page's title, four
 * horizontal rows = K1..K4 (an amount fills its row from the left: dim amber + a 2 px amber edge, the number large at
 * the right with its unit small; a choice shows its name and one segment per option, or a position bar over 8), a
 * hint. Two faces: AF_S for all small text, AF_L for values. Colours: the palette's accent, neutral greys and a near
 * black row surface (the FM-1's panel lifts dark tones: the fill starts at half the palette's darkest step). */
#define UI_ACC pal[3]
#define FC_BG C_BLACK
#define FC_HAIR RGB(30, 30, 34)                        /* the SEQUENCER's hairlines */
#define FC_LAB RGB(122, 122, 128)                      /* names, units */
#define FC_VAL RGB(236, 236, 230)                      /* values */
#define FC_DIM RGB(90, 90, 96)                         /* hints */
#define MT_SURF RGB(16, 16, 18)                        /* a row's surface */
#define MT_TRACK RGB(40, 40, 44)                       /* the segments not chosen, the position bar's track */
#define MT_TAG RGB(100, 100, 106)                      /* K1 .. K4 */
static uint16_t mt_fill(void)                          /* the fill: the palette's darkest step at half */
{
    uint32_t c = C_LINE, rr = (c >> 11) & 31u, gg = (c >> 5) & 63u, bb = c & 31u;
    return (uint16_t)(((rr >> 1) << 11) | ((gg >> 1) << 5) | (bb >> 1));
}
#ifndef AF_S_CAP_Y
#define AF_S_CAP_Y 3
#define AF_S_CAP_H 9
#endif
#ifndef AF_L_CAP_Y
#define AF_L_CAP_Y 7
#define AF_L_CAP_H 21
#endif
#define S_TOP(yc) ((yc) - AF_S_CAP_Y - AF_S_CAP_H / 2)  /* the line top that centres AF_S's capitals on row yc */
#define L_TOP(yc) ((yc) - AF_L_CAP_Y - AF_L_CAP_H / 2)
/* the screen: header 0..19, title 20..39, rows 46 + 42 k (38 high, x 6..233), footer 210..239 */
#define HD_H 20u
#define TI_Y 20u
#define TI_H 20u
#define RW_X 6u
#define RW_Y0 46u
#define RW_DY 42u
#define RW_W 228u
#define RW_H 38u
#define FT_Y 210u
#define FT_H 30u
#define MSG_MS 1500u
#define HOT_MS 1000u

static void ui_mark(uint32_t e)
{
    if (e == E_CARDS) ui_dirty |= 0xFu << E_CARD0;
    else ui_dirty |= 1u << e;
}
static void ui_mark_param(uint32_t id) { (void)id; ui_mark(E_CARDS); }   /* cheap: the marks coalesce */
static uint8_t ui_msg_big;                            /* the message in the large face (the TYPE flash) */
static void ui_say(const char *s)                     /* a message in the footer for MSG_MS, small */
{
    uint32_t i = 0;
    while (s[i] && i < sizeof ui_msg - 1u) { ui_msg[i] = s[i]; i++; }
    ui_msg[i] = 0;
    ui_msg_t = fm1_ms;
    ui_msg_big = 0;
    ui_mark(E_FOOTER);
}
static void ui_say_big(const char *s) { ui_say(s); ui_msg_big = 1; }
static void p_strcpy(char *d, const char *s, uint32_t max) { uint32_t i = 0; while (s[i] && i < max) { d[i] = s[i]; i++; } d[i] = 0; }
/* the version on screen: a release build's ("0.8.1"), else DEV (dev builds are "0.0", host / web "dev" / "web") */
static const char *ui_version(void)
{
    static const char v[] = APP_VER;
    return v[0] < '0' || v[0] > '9' || (v[0] == '0' && v[1] == '.' && v[2] == '0' && !v[3]) ? "DEV" : v;
}
static void ui_changed(void) { set_dirty = 1; set_t_change = fm1_ms; }
static int32_t sgn(int32_t d) { return d > 0 ? 1 : -1; }

/* switching an effect on from the panel while its depth is 0 would be silent (the depths default to 0): give it a
 * starting depth then; a depth the player set is never touched. The compressor's make-up: the engine's (cmp_mkup) */
static void fx_wake(uint32_t sw_id)
{
    static const uint8_t SW[6] = {P_TW_SW, P_PH_SW, P_CP_SW, P_DL_SW, P_CMP_SW, P_DRV_SW};
    static const uint8_t DEPTH[6] = {P_TW_DEPTH, P_PH_DEPTH, P_CP_DEPTH, P_DL_DEPTH, P_CMP_AMT, P_DRIVE};
    static const uint8_t START[6] = {64, 80, 64, 50, 64, 40};
    uint32_t i;
    for (i = 0; i < 6u; i++)
        if (SW[i] == sw_id && PV(sw_id) && !PV(DEPTH[i])) {
            param_set(DEPTH[i], START[i]);
            if (sw_id == P_CMP_SW && !PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, eng_ui()->cmp_mkup);
            ui_mark(E_CARDS);
        }
}

/* ---- PERFORM: four knobs that play whatever ASSIGN gives them; by default the effects' dry / wet. The list is
 * append-only (settings keep an index into it). An engine's parameter shows the engine's name for it (eng_pages.h) */
typedef struct { uint8_t id; const char *name; } perf_t;
static const perf_t PERF_T[] = {
    {P_TW_DEPTH, "TREM/WAH"}, {P_PH_DEPTH, "PHASER"}, {P_CP_DEPTH, "CHORUS"}, {P_DL_DEPTH, "DELAY"},
    {P_RV_DEPTH, "REVERB"}, {P_DRIVE, "DRIVE"}, {P_CMP_AMT, "COMP THR"}, {P_CMP_GAIN, "COMP MKUP"},
    {P_TW_RATE, "TREM RATE"}, {P_TW_SHAPE, "TREM SHAPE"}, {P_PH_SPEED, "PHA SPEED"}, {P_PH_FDBK, "PHA FEEDBK"},
    {P_CP_SPEED, "CHO SPEED"}, {P_DL_TIME, "DLY TIME"}, {P_DL_FDBK, "DLY FEEDBK"}, {P_DL_TONE, "DLY TONE"},
    {P_TONE, "TONE"}, {P_BARK, "BARK"}, {P_BELL, "BELL"}, {P_HAMMER, "HAMMER"},
    {P_DECAY, "DECAY"}, {P_RELEASE, "RELEASE"}, {P_NOISE, "NOISE"}, {S_KVEL, "KEY VEL"}, {P_LEVEL, "LEVEL"},
    {S_ARPRATE, "ARP RATE"}, {S_ARPGATE, "ARP GATE"},
    {P_FL_FREQ, "FILTER"}, {P_FL_RESO, "FILT RESO"}, {P_FL_LFO, "FILT LFO"}, {P_LFO_RATE, "LFO RATE"},   /* 0.9.3 */
};
#define PERF_N (sizeof PERF_T / sizeof PERF_T[0])
static uint32_t perf_idx(uint32_t k)                     /* knob k's entry in PERF_T */
{
    uint32_t v = set.perf[k];
    static const uint8_t DEF[4] = {0, 1, 27, 3};         /* default: TREM/WAH, PHASER, FILTER (0.9.3), DELAY */
    return v && v <= PERF_N ? v - 1u : DEF[k & 3u];
}
/* turning an effect's amount up from PERFORM switches the effect on (TREM/WAH: as TREM unless WAH was chosen) */
static void perf_wake(uint32_t id)
{
    if (id == P_FL_FREQ) { if (!PV(P_FL_TYPE) && PV(P_FL_FREQ) < 127) param_set(P_FL_TYPE, 2); return; }   /* LP18 */
    if (!PV(id)) return;
    if (id == P_TW_DEPTH && !PV(P_TW_SW)) param_set(P_TW_SW, 1);
    else if (id == P_PH_DEPTH && !PV(P_PH_SW)) param_set(P_PH_SW, 1);
    else if (id == P_CP_DEPTH && !PV(P_CP_SW)) param_set(P_CP_SW, 1);
    else if (id == P_DL_DEPTH && !PV(P_DL_SW)) param_set(P_DL_SW, 1);
    else if (id == P_DRIVE && !PV(P_DRV_SW)) param_set(P_DRV_SW, 1);
    else if (id == P_CMP_AMT && !PV(P_CMP_SW)) { param_set(P_CMP_SW, 1); if (!PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, eng_ui()->cmp_mkup); }
}
/* the control a card shows from the engine's table: VOICE page vp, or FRODHES K2 / K3 (the headlines); 0: none */
static const ectl_t *page_ctl(uint32_t pg, uint32_t vp, uint32_t k)
{
    if (pg == PG_VOICE) return voice_ctl(vp, k);
    if (pg == PG_PIANO && (k == 1u || k == 2u)) return eng_head(k);
    return 0;
}
/* the slot a card shows: the engine's, else the page's; PERFORM's are resolved to the assigned parameter */
static uint32_t slot_at_v(uint32_t pg, uint32_t vp, uint32_t k)
{
    const ectl_t *c = page_ctl(pg, vp, k);
    uint32_t s = c ? c->id : pg == PG_VOICE ? S_NONE : PAGE_S[pg][k];
    return s >= S_PERF0 && s < S_PERF0 + 4u ? PERF_T[perf_idx(s - S_PERF0)].id : s;
}
static uint32_t slot_at(uint32_t pg, uint32_t k) { return slot_at_v(pg, ui_vp_ok(), k); }
static const char *perf_name(uint32_t i) { const ectl_t *c = eng_ctl(PERF_T[i].id); return c ? c->name : PERF_T[i].name; }
static const char *slot_label_v(uint32_t pg, uint32_t vp, uint32_t k)
{
    const ectl_t *c = page_ctl(pg, vp, k);
    if (pg == PG_PERF) return perf_name(perf_idx(k));
    if (c) return c->name;
    return pg == PG_VOICE ? "" : PAGE_LAB[pg][k];
}
static const char *slot_label(uint32_t pg, uint32_t k) { return slot_label_v(pg, ui_vp_ok(), k); }
/* ---- card slots */
static uint32_t tw_kind(void)                          /* OFF PAN TREM WAH */
{
    uint32_t sw = PV(P_TW_SW), m = PV(P_TW_MODE);
    return sw == 2u ? 3u : sw != 1u ? 0u : m ? 2u : 1u;
}
static int32_t slot_val(uint32_t s, int32_t *hi)
{
    if (s < P_COUNT) { *hi = PARAMS[s].hi; return PV(s); }
    switch (s) {
    case S_KVEL: *hi = 127; return set.key_vel;
    case S_TUNE: *hi = 100; return set.tune + 50;
    case S_BPM: *hi = 2000; return set.bpm10 - 400;
    case S_CLOCK: *hi = 1; return set.clock;
    case S_SCMODE: *hi = 2; return set.sc_mode;
    case S_SCALE: *hi = 10; return set.sc_scale;
    case S_ROOT: *hi = 11; return set.sc_root;
    case S_TRANS: *hi = 48; return set.sc_trans + 24;
    case S_TWKIND: *hi = 3; return (int32_t)tw_kind();
    case S_ASG0: case S_ASG0 + 1: case S_ASG0 + 2: case S_ASG0 + 3: *hi = PERF_N - 1; return (int32_t)perf_idx(s - S_ASG0);
    case S_ARPMODE: *hi = ARP_NMODE - 1; return ARP_MODE;
    case S_ARPRATE: *hi = 5; return ARP_RATE;
    case S_ARPOCT: *hi = 3; return ARP_OCT;
    case S_ARPGATE: *hi = 90; return ARP_GATE - 10;
    case S_USBVOL: *hi = 1; return set.usb_fixed;
    case S_MCH: *hi = 16; return set.midi_ch;
    case S_TRS: *hi = 1; return !set.trs_off;
    case S_CHTYPE: *hi = CH_NTYPE - 1; return CH_TYPE;
    case S_CHINV: *hi = 3; return CH_INV;
    case S_CHVOICE: *hi = 2; return CH_VOICE;
    case S_CHSTRUM: *hi = 12; return CH_STRUM;
    case S_SQBARS: *hi = 16; return SQ_BARS;
    case S_SQQUANT: *hi = 5; return SQ_QUANT;
    case S_SQSWING: *hi = 25; return SQ_SWING - 50;
    case S_SQEDIT: *hi = 1; return sq_erase;
    default: *hi = 0; return 0;
    }
}
static int slot_is_level(uint32_t s)                    /* a gauge only for amounts, not for choices */
{
    if (s < P_COUNT) return PARAMS[s].fmt != F_ENUM && PARAMS[s].fmt != F_DIV;
    return s == S_KVEL || s == S_TUNE || s == S_BPM || s == S_TRANS || s == S_ARPGATE || s == S_CHSTRUM;
}
static void fmt_signed(char *s, int32_t v) { if (v > 0) { s[0] = '+'; p_fmt_int(s + 1, v); } else p_fmt_int(s, v); }
/* a rate / time knob that is synced to the tempo: its SYNC parameter (the knob shows and steps the note value), else 0 */
static uint32_t slot_sync_id(uint32_t s)
{
    uint32_t y = s == P_DL_TIME ? P_DL_SYNC : s == P_CP_SPEED ? P_CP_SYNC : s == P_PH_SPEED ? P_PH_SYNC :
                 s == P_LFO_RATE ? P_LFO_SYNC : s == P_MOT_RATE ? P_MOT_SYNC :
                 s == P_TW_RATE && PV(P_TW_SW) != 2u ? P_TW_SYNC : 0u;   /* (WAH: RATE is a resonance) */
    return y && PV(y) ? y : 0u;
}
static int slot_synced(uint32_t s) { return slot_sync_id(s) != 0u; }
static void slot_text(uint32_t s, char *v)
{
    v[0] = 0;
    if (slot_synced(s)) { p_strcpy(v, N_SYNC[PV(slot_sync_id(s))], 5); return; }
    if (s < P_COUNT) {                                  /* an engine's named detents (STRIKE, MALLET): the name near one */
        const ectl_t *c = eng_ctl(s);
        uint32_t f;
        if (c && c->kind == EK_DETENT)
            for (f = 0; f < c->nn; f++)
                if (PV(s) + c->tol >= c->at[f] && PV(s) <= c->at[f] + c->tol) { p_strcpy(v, c->names[f], 8); return; }
    }
    switch (s) {
    case P_TYPE: p_strcpy(v, N_TYPE_LONG[PV(P_TYPE)], 12); return;
    case P_CMP_AMT: if (!PV(P_CMP_SW)) { p_strcpy(v, "OFF", 3); return; } break;
    case P_DRIVE: if (!PV(P_DRV_SW) && ui_page != PG_DRIVE) { p_strcpy(v, "OFF", 3); return; } break;   /* (its page: K1) */
    case P_TW_RATE: if (PV(P_TW_SW) == 2) { fmt_r10(v, PV(s)); return; } break;   /* WAH: resonance */
    case P_MOT_RATE:                                                 /* the motor: 0.5 .. 10 Hz ("3.5HZ") */
        {
            uint32_t n = 0;
            fmt_hz(v, FX_LFO_INC[MOT_RATE_V(PV(s))]);   /* (params.c: rounded) */
            while (v[n]) n++;
            v[n++] = 'H'; v[n++] = 'Z'; v[n] = 0;
            return;
        }
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
    case S_ASG0: case S_ASG0 + 1: case S_ASG0 + 2: case S_ASG0 + 3: p_strcpy(v, perf_name(perf_idx(s - S_ASG0)), 12); return;
    case S_ARPMODE: p_strcpy(v, ARP_MODE_NAME[ARP_MODE], 7); return;
    case S_ARPRATE: p_strcpy(v, ARP_RATE_NAME[ARP_RATE], 6); return;
    case S_USBVOL: p_strcpy(v, set.usb_fixed ? "FIXED" : "KNOB", 5); return;
    case S_MCH: if (set.midi_ch) p_fmt_int(v, set.midi_ch); else p_strcpy(v, "ALL", 3); return;
    case S_TRS: p_strcpy(v, set.trs_off ? "OFF" : "ON", 3); return;
    case S_CHTYPE: p_strcpy(v, CH_TYPE_NAME[CH_TYPE], 9); return;
    case S_CHINV: p_strcpy(v, CH_INV_NAME[CH_INV], 4); return;
    case S_CHVOICE: p_strcpy(v, CH_VOICE_NAME[CH_VOICE], 5); return;
    case S_CHSTRUM: { uint32_t n = 0; p_fmt_int(v, CH_STRUM * 5); while (v[n]) n++; p_strcpy(v + n, " MS", 3); return; }
    case S_SQBARS: p_fmt_int(v, SQ_BARS); return;
    case S_SQQUANT: p_strcpy(v, SQ_QUANT_NAME[SQ_QUANT], 5); return;
    case S_SQSWING: { uint32_t n = 0; p_fmt_int(v, SQ_SWING); while (v[n]) n++; v[n++] = '%'; v[n] = 0; return; }
    case S_SQEDIT: p_strcpy(v, sq_erase ? "ERASE" : sq_browsing() ? "DELETE" : "UNDO", 6); return;
    case S_ARPOCT: p_fmt_int(v, ARP_OCT + 1); return;
    case S_ARPGATE: { uint32_t n = 0; p_fmt_int(v, ARP_GATE); while (v[n]) n++; v[n++] = '%'; v[n] = 0; return; }
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
        if (slot_synced(id)) { step = id == P_DL_TIME ? sgn(d) : -sgn(d); id = slot_sync_id(id); }
        v = PV(id) + step;
        if (id != s && v < 1) v = 1;
        if (id == P_MOT_RATE && v > 120) v = 120;       /* the motor's top speed (10 Hz) */
        if (id == P_TYPE) type_pick(clampi(v, 0, PARAMS[id].hi));   /* a TYPE change (bar types: the motor on) */
        else param_set(id, clampi(v, 0, PARAMS[id].hi));
        if (id == P_MOT_ON) set.mot_user_off = !PV(P_MOT_ON);        /* off by hand: no auto-on at TYPE VIBES */
        fx_wake(id);
        if (id == P_CMP_AMT) {                          /* THRESH is the compressor's switch: 0 = OFF, up = on */
            if (PV(P_CMP_AMT) && !PV(P_CMP_SW) && !PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, eng_ui()->cmp_mkup);   /* level-neutral */
            param_set(P_CMP_SW, PV(P_CMP_AMT) != 0);
            ui_mark(E_CARDS); ui_mark(E_FOOTER);
        }
        if (id == P_TW_SYNC || id == P_CP_SYNC || id == P_DL_SYNC || id == P_PH_SYNC || id == P_LFO_SYNC || id == P_MOT_SYNC)
            tempo_sync_publish();
        return;
    }
    switch (s) {
    case S_KVEL: set.key_vel = (uint8_t)clampi(set.key_vel + d * (d > 1 || d < -1 ? 4 : 1), 1, 127); break;
    case S_TUNE: set.tune = (int8_t)clampi(set.tune + d, -50, 50); rh_tune_t = TUNE_T[set.tune + 50]; break;
    case S_BPM: tempo_set10((uint32_t)clampi((int32_t)set.bpm10 + d * 10, 400, 2400)); break;
    case S_CLOCK: set.clock = (uint8_t)clampi(set.clock + sgn(d), 0, 1); break;
    case S_SCMODE: set.sc_mode = (uint8_t)clampi(set.sc_mode + sgn(d), 0, 2); break;
    case S_SCALE: set.sc_scale = (uint8_t)clampi(set.sc_scale + sgn(d), 0, 10); break;
    case S_ROOT: set.sc_root = (uint8_t)((set.sc_root + (d > 0 ? 1u : 11u)) % 12u); break;
    case S_TRANS: set.sc_trans = (int8_t)clampi(set.sc_trans + d, -24, 24); break;
    case S_TWKIND: {                                    /* OFF PAN TREM WAH */
        int32_t k = clampi((int32_t)tw_kind() + sgn(d), 0, 3);
        param_set(P_TW_SW, k == 0 ? 0 : k == 3 ? 2 : 1);
        if (k == 1 || k == 2) param_set(P_TW_MODE, k - 1);
        fx_wake(P_TW_SW);
        break;
    }
    case S_ASG0: case S_ASG0 + 1: case S_ASG0 + 2: case S_ASG0 + 3:
        set.perf[s - S_ASG0] = (uint8_t)(clampi((int32_t)perf_idx(s - S_ASG0) + sgn(d), 0, PERF_N - 1) + 1);
        break;
    case S_ARPMODE:
        ARP_MODE = (uint8_t)clampi(ARP_MODE + sgn(d), 0, ARP_NMODE - 1);
        if (ARP_MODE) ARP_LAST = ARP_MODE; else arp_clear();
        break;
    case S_ARPRATE: ARP_RATE = (uint8_t)clampi(ARP_RATE + sgn(d), 0, 5); break;
    case S_ARPOCT: ARP_OCT = (uint8_t)clampi(ARP_OCT + sgn(d), 0, 3); break;
    case S_ARPGATE: ARP_GATE = (uint8_t)clampi(ARP_GATE + 5 * sgn(d), 10, 100); break;
    case S_USBVOL: set.usb_fixed = d > 0; usb_pre_master = set.usb_fixed; break;
    case S_MCH: set.midi_ch = (uint8_t)clampi(set.midi_ch + sgn(d), 0, 16); break;
    case S_TRS: set.trs_off = d < 0; uart_midi_on = !set.trs_off; break;
    case S_CHTYPE: CH_TYPE = (uint8_t)clampi(CH_TYPE + sgn(d), 0, CH_NTYPE - 1); if (CH_TYPE) CH_LAST = CH_TYPE; break;
    case S_CHINV: CH_INV = (uint8_t)clampi(CH_INV + sgn(d), 0, 3); break;
    case S_CHVOICE: CH_VOICE = (uint8_t)clampi(CH_VOICE + sgn(d), 0, 2); break;
    case S_CHSTRUM: CH_STRUM = (uint8_t)clampi(CH_STRUM + sgn(d), 0, 12); break;
    case S_SQBARS: SQ_BARS = (uint8_t)clampi(SQ_BARS + sgn(d), 1, 16); sq_roll_dirty = 1; break;
    case S_SQQUANT: SQ_QUANT = (uint8_t)clampi(SQ_QUANT + sgn(d), 0, 5); sq_roll_dirty = 1; break;
    case S_SQSWING: SQ_SWING = (uint8_t)clampi(SQ_SWING + sgn(d), 50, 75); sq_roll_dirty = 1; break;
    case S_SQEDIT:                                      /* left: UNDO the last pass; right: ERASE on / off */
        if (d < 0) {
            char m[24];
            int del = sq_browsing();
            uint32_t n = del ? sq_delete_step() : seq_undo(), k = 0;
            if (del) sq_cur_t = fm1_ms;
            p_strcpy(m, del ? "DELETED: " : "UNDO: ", 9);
            while (m[k]) k++;
            p_fmt_int(m + k, (int32_t)n);
            while (m[k]) k++;
            p_strcpy(m + k, " NOTES", 6);
            ui_say(m);
        } else { sq_erase = (uint8_t)!sq_erase; if (sq_erase && seq_rec) seq_toggle_rec(); ui_say(sq_erase ? "ERASE: HOLD KEYS" : "ERASE OFF"); }
        break;
    default: break;
    }
    ui_changed();
}

/* ---- drawing */
#define GR_SEGS 15u
#define GR_X 6                                          /* the gain-reduction bar: x 6 .. 233 */
#define GR_W 228
static const int16_t GR_DB10[GR_SEGS] = {10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 180, 200, 240, 270, 300};
static int32_t gr_db10;
static uint8_t gr_peak;
static uint32_t gr_shown = 0xFFFF;
/* ---- the battery (0.9.3, TODO 8): the FM-1's PB1 divider on ADC ch3 (hal/fm1_adc.h), read from the main loop every
 * BATT_POLL_MS (never in the audio ISR), smoothed over ~10 s, shown as 3 segments in the header with a hysteresis so
 * it never flickers between two. USB power: a bolt (the reading is the charger's then).
 * PROVISIONAL thresholds: the stock firmware's (Felucca uses the same 531 / 561 / 591); calibrate them on the FM-1
 * from the SETUP footer's BAT reading or the developer query CC 4 (frodhes.c). */
#define BATT_T1 531                                   /* >= : 1 segment  (PROVISIONAL) */
#define BATT_T2 561                                   /* >= : 2 segments (PROVISIONAL) */
#define BATT_T3 591                                   /* >= : 3 segments (PROVISIONAL) */
#define BATT_HYST 4                                   /* a step needs the reading this far past its threshold */
#define BATT_POLL_MS 500u
#define BATT_HOST_RAW 600                             /* the host / web build: no ADC, a full battery */
static const int16_t BATT_T[3] = {BATT_T1, BATT_T2, BATT_T3};
static int32_t batt_avg;                            /* the smoothed reading x 256 (0: none yet) */
static uint8_t batt_lvl, batt_sim;                    /* the segments shown; batt_sim: developer CC 8 (x 8, 0 = real) */
#ifdef DINGHY_HOST
static int32_t batt_adc(void) { return BATT_HOST_RAW; }
static int batt_usb(void) { return 0; }
#else
static int32_t batt_adc(void) { return fm1_adc_read(FM1_ADC_BATT); }
static int batt_usb(void) { return usb.config && !usb.suspended; }
#endif
static int32_t batt_raw(void) { return (batt_avg + 128) / 256; }
static uint32_t batt_shown(void) { return batt_usb() ? 4u : batt_lvl; }
static void batt_poll(void)
{
    static uint32_t t;
    static uint8_t shown = 0xFF;
    int32_t a, r;
    uint32_t l;
    if (batt_avg && fm1_ms - t < BATT_POLL_MS) return;
    t = fm1_ms;
    a = batt_sim ? (int32_t)batt_sim * 8 : batt_adc();
    if (a <= 0) return;                               /* (a timeout, or nothing on the pin) */
    if (!batt_avg) batt_avg = a * 256;
    else { int32_t d = a * 256 - batt_avg; batt_avg += (d + (d > 0 ? 10 : -10)) / 20; }   /* 500 ms x 20: ~10 s;
                                                    * rounded, so it settles on the reading itself */
    r = batt_raw();
    for (l = 0; l < 3u && r >= BATT_T[l]; l++) {}
    if (shown == 0xFF) batt_lvl = (uint8_t)l;        /* the first reading: at once */
    else if (l > batt_lvl && r >= BATT_T[l - 1u] + BATT_HYST) batt_lvl = (uint8_t)l;
    else if (l < batt_lvl && r < BATT_T[batt_lvl - 1u] - BATT_HYST) batt_lvl = (uint8_t)l;
    if (batt_shown() != shown) { shown = (uint8_t)batt_shown(); ui_mark(E_HEADER); }
}
/* the icon, 18 x 9 at (x, y): a body with a tip, three segments, or a bolt on USB power. ink: the outline; the
 * segments in hi, the last one (or the empty outline) in the accent */
static void batt_icon(int32_t x, int32_t y, uint16_t ink, uint16_t hi, uint16_t bg)
{
    static const uint8_t BOLT[7] = {0x08, 0x10, 0x30, 0x7E, 0x0C, 0x08, 0x10};   /* 7 x 7, bit 6 = left */
    uint32_t lv = batt_shown(), i, j;
    uint16_t o = lv == 0u ? UI_ACC : ink;
    cv_rect(x, y, 15, 9, o); cv_rect(x + 1, y + 1, 13, 7, bg);
    cv_rect(x + 15, y + 2, 2, 5, o);
    if (lv == 4u) {
        for (j = 0; j < 7u; j++)
            for (i = 0; i < 7u; i++)
                if (BOLT[j] >> (6u - i) & 1u) cv_pset(x + 4 + (int32_t)i, y + 1 + (int32_t)j, hi);
        return;
    }
    for (i = 0; i < lv; i++) cv_rect(x + 2 + 4 * (int32_t)i, y + 2, 3, 5, lv == 1u ? UI_ACC : hi);
}

/* the header, right to left: the battery (last, the user), the BPM, BYP, SUS, the octave; the preset name gets what is
 * left (cut only when the flags reach it) */
static void draw_header(void)
{
    char nm[20], b[8];
    int32_t x;
    uint32_t n = 0;
    cv_begin(240, HD_H, FC_BG);
    if (set.preset < PR_NALL_UI) preset_name(set.preset, nm); else p_strcpy(nm, "FRODHES", 8);
    nm[16] = 0;
    slot_text(S_BPM, b);
    batt_icon(230 - 17, 6, FC_LAB, FC_VAL, FC_BG);
    x = 230 - 17 - 6 - aa_text_w(&AF_S, b);
    aa_text(x, S_TOP(10), &AF_S, b, FC_LAB, FC_BG);
    x -= 6;
    if (fx_bypass) { x -= aa_text_w(&AF_S, "BYP"); aa_text(x, S_TOP(10), &AF_S, "BYP", FC_VAL, FC_BG); x -= 6; }
    if (rh_hold) { x -= aa_text_w(&AF_S, "SUS"); aa_text(x, S_TOP(10), &AF_S, "SUS", UI_ACC, FC_BG); x -= 6; }
    if (set.oct) {
        char o[4] = {'O', set.oct < 0 ? '-' : '+', (char)('0' + (set.oct < 0 ? -set.oct : set.oct)), 0};
        x -= aa_text_w(&AF_S, o); aa_text(x, S_TOP(10), &AF_S, o, FC_LAB, FC_BG); x -= 6;
    }
    while (nm[n]) n++;
    while (n && 10 + aa_text_w(&AF_S, nm) > x - 4) nm[--n] = 0;
    aa_text(10, S_TOP(10), &AF_S, nm, FC_LAB, FC_BG);
    cv_blit(0, 0);
}

static void seq_pos_text(char *t);
static uint32_t sq_view_tick(void);
static void sq_shown_beat_set(void);
static void fmt_frac(char *t, uint32_t a, uint32_t b)   /* "a/b" */
{
    uint32_t n = 0;
    p_fmt_int(t, (int32_t)a); while (t[n]) n++;
    t[n++] = '/'; p_fmt_int(t + n, (int32_t)b);
}
/* the title: the page's name in the accent, where we are at the right (the SEQUENCER: where the loop is) */
static void draw_title(void)
{
    char t[24];
    cv_begin(240, TI_H, FC_BG);
    int32_t x = aa_text(10, S_TOP(10), &AF_S, PAGE_TITLE[ui_page], UI_ACC, FC_BG);
    if (ui_page == PG_VOICE) {                          /* VOICE 2/3: which of the engine's pages */
        fmt_frac(t, ui_vp_ok() + 1u, vp_count());
        aa_text(x + 5, S_TOP(10), &AF_S, t, FC_VAL, FC_BG);
    }
    if (ui_page == PG_SEQ) {                            /* BAR 2/4 . 3 */
        seq_pos_text(t);
        sq_shown_beat_set();
    } else fmt_frac(t, pg_pos(ui_page, ui_vp_ok()) + 1u, pg_count());
    aa_text_r(230, S_TOP(10), &AF_S, t, FC_LAB, FC_BG);
    cv_blit(0, TI_Y);
}

/* a row's text and its unit: the number apart from its unit ("16K" -> "16" + "kHz", "3.5HZ" -> "3.5" + "Hz", "1.5S" ->
 * "1.5" + "s", "60 MS" -> "60" + "ms"); 0: no unit (a name, a note value, a plain number) */
static int slot_synced(uint32_t s);
static const char *slot_unit(uint32_t s, char *v)
{
    uint32_t n = 0, f = s < P_COUNT ? PARAMS[s].fmt : 0xFFu;
    while (v[n]) n++;
    if (!n || !((v[0] >= '0' && v[0] <= '9') || v[0] == '+' || v[0] == '-') || slot_synced(s)) return 0;
    if (s == P_TW_RATE && PV(P_TW_SW) == 2u) return 0;                 /* WAH: RATE is a resonance */
    if (f == F_HZ || f == F_HZH || f == F_FHZ || s == P_MOT_RATE) {
        if (n > 2u && v[n - 2u] == 'H' && v[n - 1u] == 'Z') v[n -= 2u] = 0;
        if (v[n - 1u] == 'K') { v[n - 1u] = 0; return "kHz"; }
        return "Hz";
    }
    if (f == F_MS || f == F_PDLY || s == S_CHSTRUM) {
        if (n > 3u && v[n - 3u] == ' ') { v[n - 3u] = 0; return "ms"; }
        if (v[n - 1u] == 'S') { v[n - 1u] = 0; return "s"; }
        return "ms";
    }
    if (f == F_THR || f == F_GAIN || f == F_DB) return "dB";
    if (s == S_TUNE) return "c";
    return 0;
}
/* the name a choice shows on its row: the short TYPE names (ST77 SUIT ...), else its text */
static void slot_short(uint32_t s, char *v)
{
    if (s == P_TYPE) p_strcpy(v, N_TYPE[PV(P_TYPE)], 5);
    else slot_text(s, v);
}

/* a row: K tag, label (accent while its knob turns, with a 1 px frame for HOT_MS), then an amount (the fill, the
 * number large, its unit small) or a choice (its name, one segment per option up to 8, else a position bar) */
static void draw_row(uint32_t k)
{
    uint32_t s = slot_at(ui_page, k);
    int hot = ui_hot_card == k && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS;
    char v[16], kk[3] = {'K', (char)('1' + k), 0};
    cv_begin(RW_W, RW_H, MT_SURF);
    if (s != S_NONE) {
        const char *lab = slot_label(ui_page, k), *u;
        int32_t hi, val = slot_val(s, &hi), le, xr, w;
        if (hi && slot_is_level(s)) {                   /* the gauge: the row's fill to the value, a 2 px edge */
            int32_t fw = (int32_t)RW_W * clampi(val, 0, hi) / hi, ex = clampi(fw - 2, 0, (int32_t)RW_W - 2);
            cv_rect(0, 0, fw, RW_H, mt_fill());
            cv_rect(ex, 0, 2, RW_H, UI_ACC);
        }
        aa_text_ov(6, S_TOP(9), &AF_S, kk, MT_TAG);
        le = aa_text_ov(6, S_TOP(26), &AF_S, lab, hot ? UI_ACC : FC_LAB);   /* where the label ends */
        if (le < 6 + aa_text_w(&AF_S, kk)) le = 6 + aa_text_w(&AF_S, kk);
        if (hi && !slot_is_level(s)) {                  /* a choice */
            int32_t n = hi + 1, x0 = le + 6 > 86 ? le + 6 : 86, x1;
            slot_short(s, v);
            w = aa_text_ovr(220, S_TOP(19), &AF_S, v, FC_VAL);
            x1 = w - 8 < 162 ? w - 8 : 162;
            if (n <= 8 && x1 - x0 >= n * 4 - 1) {       /* one segment per option, the chosen one lit */
                int32_t j, sw = (x1 - x0 - (n - 1) * 3) / n;
                for (j = 0; j < n; j++) cv_rect(x0 + j * (sw + 3), 13, sw, 12, j == val ? UI_ACC : MT_TRACK);
            } else if (x1 - x0 >= 16) {                  /* a position bar: index / (n - 1) */
                int32_t p = x0 + (x1 - x0 - 3) * clampi(val, 0, hi) / hi;
                cv_rect(x0, 18, x1 - x0, 3, MT_TRACK);
                cv_rect(x0, 18, p - x0, 3, mt_fill());
                cv_rect(p, 13, 3, 13, UI_ACC);
            }
        } else {                                        /* an amount: the number large, its unit small at the right */
            slot_text(s, v);
            u = slot_unit(s, v);
            xr = 220 - (u ? aa_text_w(&AF_S, u) + 4 : 0);
            if (aa_text_w(&AF_L, v) <= xr - le - 8) aa_text_ovr(xr, L_TOP(19), &AF_L, v, FC_VAL);
            else aa_text_ovr(xr, S_TOP(19), &AF_S, v, FC_VAL);   /* too wide for the large face (a test reports it) */
            if (u) aa_text_ovr(220, L_TOP(19) + AF_L.asc - AF_S.asc, &AF_S, u, FC_LAB);   /* on the number's baseline */
        }
        if (hot) {                                      /* the knob being turned: a 1 px frame */
            cv_rect(0, 0, RW_W, 1, UI_ACC); cv_rect(0, RW_H - 1, RW_W, 1, UI_ACC);
            cv_rect(0, 0, 1, RW_H, UI_ACC); cv_rect(RW_W - 1, 0, 1, RW_H, UI_ACC);
        }
    } else aa_text_ov(6, S_TOP(9), &AF_S, kk, MT_TRACK);   /* an empty row: its K tag, dim */
    cv_blit(RW_X, RW_Y0 + RW_DY * k);
}
/* ---- the SEQUENCER page, made to be read on a small screen: what plays NOW in big letters, what comes NEXT, and the
 * bar as 16 big cells (a filled cell: notes start there; white: playing). Its four cards are a compact row. */
#define SQ_NOW_Y 40
#define SQ_STRIP_Y 104
#define SQ_CARD_Y 171
#define SQ_CARD_H 38                                    /* 171 .. 208: the footer starts at 210 */
static int sq_browsing(void) { return (sq_cur >= 0 || (seq_rec && !seq_play)) && (!seq_play || fm1_ms - sq_cur_t < 4000u); }
static uint32_t sq_view_tick(void)                      /* the place looked at: the cursor's step, else the loop's */
{
    if (sq_browsing()) return (uint32_t)(sq_cur >= 0 && sq_cur < (int32_t)(SQ_BARS * 16u) ? sq_cur : 0) * 24u;
    return seq_play ? sq_tick() : 0u;
}
static void sq_browse(int32_t d)                        /* d in 16ths (a bar = 16), around the loop */
{
    int32_t n = (int32_t)SQ_BARS * 16, c = sq_browsing() ? sq_cur : (int32_t)(sq_view_tick() / 24u);
    sq_cur = ((c + d) % n + n) % n;
    sq_cur_t = fm1_ms;
}
static uint32_t sq_delete_step(void)                    /* the notes starting in the cursor's step go */
{
    uint32_t i, j, t0 = (uint32_t)(sq_cur < 0 ? 0 : sq_cur) * 24u, got = 0, L = sq_len();
    for (i = j = 0; i < sq_n; i++) {
        uint32_t t = sq_ev[i].tick < L ? sq_at(&sq_ev[i]) : 0xFFFFu;
        if (t >= t0 && t < t0 + 24u) got++; else sq_ev[j++] = sq_ev[i];
    }
    sq_n = j;
    if (got) { sq_all_off(); sq_changed(); }
    return got;
}
static void seq_pos_text(char *t)                       /* BAR 2/4 . 3 (bar / bars . beat); on a step: . 3.2 (16th) */
{
    uint32_t p = sq_view_tick(), n = 0;
    p_strcpy(t, "BAR ", 4); n = 4;
    p_fmt_int(t + n, (int32_t)(p / (4u * SQ_PPQ) + 1u)); while (t[n]) n++;
    t[n++] = '/'; p_fmt_int(t + n, SQ_BARS); while (t[n]) n++;
    t[n++] = ' '; t[n++] = '.'; t[n++] = ' ';
    p_fmt_int(t + n, (int32_t)(p / SQ_PPQ % 4u + 1u)); while (t[n]) n++;
    if (sq_browsing()) { t[n++] = '.'; p_fmt_int(t + n, (int32_t)(p / 24u % 4u + 1u)); }
}
/* the notes struck together at tick g (their quantized start) and a name for them: a chord name, or C4 for one note */
static uint32_t sq_group(uint32_t g, uint8_t *notes)
{
    uint32_t i, j, c = 0, L = sq_len();
    for (i = 0; i < sq_n && c < CH_MAX; i++) {
        if (sq_ev[i].tick >= L || sq_at(&sq_ev[i]) != g) continue;
        for (j = 0; j < c && notes[j] != sq_ev[i].note; j++) {}
        if (j == c) notes[c++] = sq_ev[i].note;
    }
    return c;
}
static void sq_group_name(uint32_t g, char *v)
{
    uint8_t notes[CH_MAX];
    uint32_t c = sq_group(g, notes);
    if (c == 1u) { uint32_t n = 0; p_strcpy(v, NOTE_NAME_S[notes[0] % 12u], 2); while (v[n]) n++; p_fmt_int(v + n, (int32_t)notes[0] / 12 - 1); }
    else chord_name(notes, c, v);
}
/* the last start at or before tick p (around the loop), and the first after it; 0xFFFF: none */
static void sq_now_next(uint32_t p, uint32_t *now, uint32_t *next)
{
    uint32_t i, L = sq_len(), a = 0xFFFFu, b = 0xFFFFu, hi = 0xFFFFu, lo = 0xFFFFu;
    for (i = 0; i < sq_n; i++) {
        uint32_t t;
        if (sq_ev[i].tick >= L) continue;
        t = sq_at(&sq_ev[i]);
        if (t <= p && (a == 0xFFFFu || t > a)) a = t;
        if (t > p && (b == 0xFFFFu || t < b)) b = t;
        if (hi == 0xFFFFu || t > hi) hi = t;
        if (lo == 0xFFFFu || t < lo) lo = t;
    }
    *now = a != 0xFFFFu ? a : hi;                       /* nothing before: the last one of the loop goes on */
    *next = b != 0xFFFFu ? b : lo;                      /* nothing after: the loop's first */
}
static uint32_t sq_shown_now = 0xFFFFFFFFu, sq_shown_cell = 0xFFFFFFFFu, sq_shown_beat = 0xFFFFFFFFu, sq_shown_ph;
static void sq_shown_beat_set(void) { sq_shown_beat = sq_view_tick() / SQ_PPQ; }
/* centred text in the two faces (the SEQUENCER, the footer, the splash): small = AF_S, big = AF_L; yc: the row its
 * capitals are centred on */
static void ft_c(int32_t yc, const char *s, uint16_t c) { aa_text_c(0, S_TOP(yc), 240, &AF_S, s, c, FC_BG); }
static void ft_big(int32_t yc, const char *s, uint16_t c) { aa_text_c(0, L_TOP(yc), 240, &AF_L, s, c, FC_BG); }

static void draw_now(void)                              /* NOW (big) and NEXT */
{
    char v[20], t[40];
    uint32_t now, next, n = 0;
    int empty = 0;
    sq_now_next(sq_view_tick(), &now, &next);
    sq_shown_now = now;
    if (sq_browsing()) {                                /* the cursor's step: what starts there, or nothing */
        uint32_t t0 = sq_view_tick();
        empty = !(now != 0xFFFFu && now >= t0 && now < t0 + 24u);
        sq_now_next(empty ? t0 + 23u : now, &now, &next);
    }
    cv_begin(240, 60, FC_BG);
    if (!sq_n) {
        ft_big(22, "EMPTY", FC_DIM);
        ft_c(50, "SEQ: REC, THEN PLAY", FC_LAB);
    } else {
        if (empty) p_strcpy(v, "-", 1); else sq_group_name(now, v);
        if (aa_text_w(&AF_L, v) <= 236) ft_big(22, v, sq_browsing() ? UI_ACC : seq_play ? C_WHITE : FC_VAL);
        else ft_c(22, v, FC_VAL);
        sq_group_name(next, v);
        p_strcpy(t, "NEXT ", 5); n = 5; p_strcpy(t + n, v, 12); while (t[n]) n++;
        p_strcpy(t + n, "  BAR ", 6); n += 6;
        p_fmt_int(t + n, (int32_t)(next / (4u * SQ_PPQ) + 1u)); while (t[n]) n++;
        t[n++] = '.'; p_fmt_int(t + n, (int32_t)(next / SQ_PPQ % 4u + 1u));
        ft_c(50, t, FC_LAB);
    }
    cv_blit(0, SQ_NOW_Y);
}
static void draw_strip(void)                            /* the bar playing: 16 cells (16ths) in two rows of 8 */
{
    uint32_t p = sq_view_tick(), b0 = p / (4u * SQ_PPQ) * 4u * SQ_PPQ, i, L = sq_len();
    uint32_t cur = seq_play && sq_tick() >= b0 && sq_tick() < b0 + 4u * SQ_PPQ ? (sq_tick() - b0) / 24u : 99u;   /* the playhead */
    uint8_t hit[16] = {0};
    sq_shown_cell = p / 24u;
    sq_shown_ph = seq_play ? sq_tick() / 24u : 0u;
    for (i = 0; i < sq_n; i++) {
        uint32_t t;
        if (sq_ev[i].tick >= L) continue;
        t = sq_at(&sq_ev[i]);
        if (t >= b0 && t < b0 + 4u * SQ_PPQ) hit[(t - b0) / 24u] = 1;
        {                                               /* the steps it is held through: dim (2) */
            uint32_t e = t + (sq_ev[i].len < L ? sq_ev[i].len : L - 1u), k;
            for (k = 0; k < 16u; k++) {
                uint32_t c = b0 + k * 24u, c2 = c + L;      /* (and around the loop's end) */
                if (!hit[k] && ((c > t && c < e) || (c2 > t && c2 < e))) hit[k] = 2;
            }
        }
    }
    cv_begin(240, 62, C_BLACK);
    for (i = 0; i < 16u; i++) {
        int32_t x = 4 + (int32_t)(i % 8u) * 29, y = i < 8u ? 2 : 33;
        uint16_t c = i == cur ? C_WHITE : hit[i] == 1u ? C_AMB : hit[i] ? C_LINE : C_LINE;
        if (hit[i] == 2u && i != cur) { cv_rect(x, y, 26, 26, C_LINE); cv_rect(x, y + 9, 26, 8, C_DIM); }   /* held */
        else if (hit[i] || i == cur) cv_rect(x, y, 26, 26, c);
        else { cv_rect(x, y, 26, 26, i % 4u == 0u ? C_DIM : C_LINE); cv_rect(x + 2, y + 2, 22, 22, C_BLACK); }
        if (sq_browsing() && i == (p - b0) / 24u) {    /* the cursor: a white frame around its cell (inside: */
            cv_rect(x - 2, y - 2, 30, 3, C_WHITE); cv_rect(x - 2, y + 25, 30, 3, C_WHITE);   /* black on a filled */
            cv_rect(x - 2, y - 2, 3, 30, C_WHITE); cv_rect(x + 25, y - 2, 3, 30, C_WHITE);   /* one, so it shows) */
            if (hit[i] == 1u && i != cur) { cv_rect(x + 1, y + 1, 24, 2, C_BLACK); cv_rect(x + 1, y + 23, 24, 2, C_BLACK);
                                      cv_rect(x + 1, y + 1, 2, 24, C_BLACK); cv_rect(x + 23, y + 1, 2, 24, C_BLACK); }
        }
    }
    cv_blit(0, SQ_STRIP_Y);
}
static void draw_card_small(uint32_t k)                 /* the SEQUENCER's four: the name, the value, a hairline */
{
    uint32_t s = PAGE_S[PG_SEQ][k];
    int hot = ui_hot_card == k && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS;
    uint16_t c = s == S_SQEDIT && sq_erase ? UI_ACC : FC_VAL;
    char v[16];
    cv_begin(58, SQ_CARD_H, FC_BG);
    slot_text(s, v);
    aa_text_c(0, S_TOP(7), 58, &AF_S, PAGE_LAB[PG_SEQ][k], hot ? UI_ACC : FC_LAB, FC_BG);
    if (aa_text_w(&AF_L, v) <= 56) aa_text_c(0, L_TOP(24), 58, &AF_L, v, c, FC_BG);
    else aa_text_c(0, S_TOP(24), 58, &AF_S, v, c, FC_BG);
    cv_rect(4, SQ_CARD_H - 1, 50, 1, hot ? UI_ACC : FC_HAIR);
    cv_blit(1u + k * 60u, SQ_CARD_Y);
}

/* the footer (210..239): the save prompt, a message for MSG_MS (the TYPE flash large), the COMPRESSOR's meter, the
 * SEQUENCER's state, the ARPEGGIO's HOLD and the SETUP's readouts on two lines, else the page's hint */
static void draw_meter(void);
static void draw_footer(void)
{
    cv_begin(240, FT_H, FC_BG);
    if (pr_save_mode) {                                 /* the save mode owns the footer until it ends */
        char s[24], t[32];
        preset_name(PR_NF_UI + pr_save_slot, s);
        p_strcpy(t, "SAVE TO ", 8); p_strcpy(t + 8, s, 20);
        ft_c(7, t, FC_VAL);
        ft_c(22, "SAVE: YES   HOME: NO", UI_ACC);
    } else if (ui_msg_t && fm1_ms - ui_msg_t < MSG_MS) {
        if (ui_msg_big && aa_text_w(&AF_L, ui_msg) <= 236) ft_big(15, ui_msg, FC_VAL);
        else ft_c(15, ui_msg, FC_VAL);
    } else if (ui_page == PG_COMP) {                    /* the gain-reduction meter (draw_meter), or how to start it */
        if (PV(P_CMP_SW)) { gr_shown = 0xFFFF; draw_meter(); return; }
        ft_c(19, "K1 THRESH: TURN UP = ON", FC_DIM);
    } else if (ui_page == PG_SEQ) {                     /* the state, and what the buttons do */
        char t[32];
        uint32_t n = 0;
        if (seq_rec) p_strcpy(t, seq_play ? "REC: OVERDUB" : "STEP REC: PLAY NOTES", 20);
        else if (sq_erase) p_strcpy(t, "ERASE: HOLD KEYS", 20);
        else { p_strcpy(t, seq_play ? "PLAYING  " : "STOPPED  ", 10); n = 9; p_fmt_int(t + n, (int32_t)sq_n); while (t[n]) n++; p_strcpy(t + n, " NOTES", 6); }
        ft_c(7, t, seq_rec || sq_erase ? UI_ACC : FC_LAB);
        ft_c(22, "SEQ: REC   HOLD SEQ: CLEAR", FC_DIM);
    } else if (ui_page == PG_ARP) {
        ft_c(7, ARP_HOLD ? "HOLD ON: NOTES KEEP PLAYING" : "HOLD OFF", ARP_HOLD ? UI_ACC : FC_LAB);
        ft_c(22, page_hint(ui_page), FC_DIM);
    } else if (ui_page == PG_SETUP) {                   /* what this is: the name, the version, the battery's reading
                                                         * (calibration), the audio's time */
        char t[32];
        uint32_t n;
        p_strcpy(t, "FRODHES ", 8); p_strcpy(t + 8, ui_version(), 12);
        n = 0; while (t[n]) n++;
        p_strcpy(t + n, "  BAT ", 6); p_fmt_int(t + n + 6, batt_raw());
        ft_c(7, t, UI_ACC);
        p_strcpy(t, "AUDIO ", 6); p_fmt_int(t + 6, (int32_t)prof_us);
        n = 0; while (t[n]) n++;
        p_strcpy(t + n, " MAX ", 5); n += 5; p_fmt_int(t + n, (int32_t)prof_max_us);
        while (t[n]) n++;
        p_strcpy(t + n, " US", 3);
        ft_c(22, t, FC_DIM);
    } else ft_c(19, page_hint(ui_page), FC_DIM);
    cv_blit(0, FT_Y);
}

/* the compressor's gain reduction, as a hardware meter reads: a full-width bar filled from the right (1 .. 30 dB on the
 * scale GR_DB10, its 15 steps evenly apart), a peak that holds for a second, the reading in dB; the footer's canvas,
 * drawn again only when it changes (~30 Hz) */
static int32_t gr_px(int32_t db10)                      /* a reduction's place on the bar, from its right end */
{
    uint32_t i;
    int32_t lo = 0;
    for (i = 0; i < GR_SEGS; i++) {
        if (db10 <= GR_DB10[i]) return ((int32_t)i * GR_W + GR_W * (db10 - lo) / (GR_DB10[i] - lo)) / (int32_t)GR_SEGS;
        lo = GR_DB10[i];
    }
    return GR_W;
}
static void draw_meter(void)
{
    char t[12];
    uint32_t n = 0;
    int32_t db10 = gr_db10, fw = gr_px(db10), pk = gr_peak ? gr_px(GR_DB10[gr_peak - 1u]) : 0;
    cv_begin(240, FT_H, FC_BG);
    cv_rect(GR_X, 3, GR_W, 9, MT_SURF);
    cv_rect(GR_X + GR_W - fw, 3, fw, 9, mt_fill());
    cv_rect(GR_X + GR_W - (fw > 2 ? fw : 2), 3, 2, 9, UI_ACC);   /* the fill's edge (at 0: the bar's right end) */
    if (pk > fw) cv_rect(GR_X + GR_W - pk, 3, 2, 9, FC_VAL);    /* the peak */
    t[n++] = '-'; p_fmt_int(t + n, db10 / 10);
    while (t[n]) n++;
    t[n++] = '.'; t[n++] = (char)('0' + db10 % 10); t[n++] = ' '; t[n++] = 'd'; t[n++] = 'B'; t[n] = 0;
    aa_text(10, S_TOP(21), &AF_S, "GAIN REDUCTION", FC_LAB, FC_BG);
    aa_text_r(230, S_TOP(21), &AF_S, db10 ? t : "0.0 dB", db10 ? FC_VAL : FC_DIM, FC_BG);
    cv_blit(0, FT_Y);
}
/* ~30 Hz on the COMP page: follow the ISR's reduction (it rises at once, falls at 15 dB/s, like a meter's needle) */
static void meter_poll(void)
{
    static uint32_t t, peak_t;
    int32_t g;
    uint32_t segs = 0, key;
    if (ui_page != PG_COMP || pr_save_mode || (ui_msg_t && fm1_ms - ui_msg_t < MSG_MS) || fm1_ms - t < 33u) return;
    t = fm1_ms;
    g = PV(P_CMP_SW) && !fx_bypass ? fx_cmp_gr * 301 / 20480 : 0;      /* log2 Q12 -> dB x 10 */
    if (g > 300) g = 300;
    gr_db10 = g > gr_db10 ? g : gr_db10 > g + 5 ? gr_db10 - 5 : g;
    while (segs < GR_SEGS && gr_db10 >= GR_DB10[segs]) segs++;
    if (segs >= gr_peak || fm1_ms - peak_t > 1000u) { gr_peak = (uint8_t)segs; peak_t = fm1_ms; }
    key = (uint32_t)gr_db10 | (uint32_t)gr_peak << 9;
    if (key != gr_shown) { gr_shown = key; ui_mark(E_METER); }
}

/* the start screen: the name, what it is, the version; the pages follow after UI_SPLASH_MS or at the first touch */
#define UI_SPLASH_MS 1600u
static uint32_t ui_splash_t;
static uint8_t ui_splash_on;
static void ui_splash(void)
{
    static char v[20];
    uint32_t i;
    lcd_fill(0, 0, 240, 240, C_BLACK);
    cv_begin(240, 64, C_BLACK);
    for (i = 0; i < 9u; i++)                            /* nine tines, long to short, over the name */
        cv_rect(48 + 18 * (int32_t)i, 4 + 2 * (int32_t)i, 4, 22 - 2 * (int32_t)i, i == 4u ? C_HI : C_DIM);
    cv_rect(40, 30, 160, 2, C_LINE);
    cv_blit(0, 46);
    cv_begin(240, 64, C_BLACK);
    ft_big(16, "FRODHES", UI_ACC);
    ft_c(48, "ELECTRIC PIANO + VIBES", FC_LAB);
    cv_blit(0, 110);
    cv_begin(240, 20, C_BLACK);
    p_strcpy(v, "VERSION ", 8); p_strcpy(v + 8, ui_version(), 10);
    ft_c(10, v, FC_DIM);
    cv_blit(0, 210);
    ui_splash_t = fm1_ms;
    ui_splash_on = 1;
}
static void ui_draw_page_change(void) { ui_dirty |= (1u << E_N) - 1u; }

/* one element per pass, in priority order: footer, header, title, cards */
/* developer (MIDI CC 3 >= 64, 0.9.3): a colour test pattern for calibrating the screenshots and the palettes against
 * the FM-1's panel (a photo of it). Rows of 30 px: the 6 palettes' 5 steps + white (40 px each), then a grey ramp
 * 0 32 64 128 192 255 and under it (15 px each) a dark ramp 0 4 8 12 16 20, then pure red, green, blue, yellow,
 * black, white. CC 3 < 64 draws the page again. */
static uint8_t ui_testpat, ui_testpat_shown;
static void ui_draw_testpat(void)
{
    static const uint8_t G[6] = {0, 32, 64, 128, 192, 255};
    static const uint16_t P[6] = {RGB(255, 0, 0), RGB(0, 255, 0), RGB(0, 0, 255), RGB(255, 255, 0), C_BLACK, C_WHITE};
    uint32_t r, i;
    for (r = 0; r < NPALETTES && r < 6u; r++) {
        for (i = 0; i < 5u; i++) lcd_fill(40 * i, 30 * r, 40, 30, PALETTES[r].c[i]);
        lcd_fill(200, 30 * r, 40, 30, C_WHITE);
    }
    for (i = 0; i < 6u; i++) lcd_fill(40 * i, 180, 40, 15, RGB(G[i], G[i], G[i]));
    for (i = 0; i < 6u; i++) lcd_fill(40 * i, 195, 40, 15, RGB(4 * i, 4 * i, 4 * i));   /* the dark end: 0 4 8 12 16 20 */
    for (i = 0; i < 6u; i++) lcd_fill(40 * i, 210, 40, 30, P[i]);
}

static void ui_draw(void)
{
    uint32_t e, d;
    static uint8_t cleared, eng_shown;
    batt_poll();                                        /* (from the start: the splash too) */
    if (eng_ui() != &ENG_UI[eng_shown]) {               /* the TYPE crossed engines (ALGORITHM, CC 80 / 79, a preset):
                                                         * other VOICE pages, names and hints; marked once per change */
        eng_shown = (uint8_t)(eng_ui() - ENG_UI);
        if (ui_page == PG_VOICE && ui_vp >= vp_count()) ui_goto_v(PG_VOICE, vp_count() - 1u);   /* (VIBES 3/3 ->
                                                         * the Rhodes' last) */
        ui_mark(E_TITLE); ui_mark(E_CARDS); ui_mark(E_FOOTER);
    }
    if (ui_testpat) {
        if (!ui_testpat_shown) { ui_draw_testpat(); ui_testpat_shown = 1; }
        return;
    }
    if (ui_testpat_shown) { ui_testpat_shown = 0; cleared = 0; ui_draw_page_change(); }
    if (ui_splash_on) {
        if (fm1_ms - ui_splash_t < UI_SPLASH_MS) return;
        ui_splash_on = 0;
        cleared = 0;
        ui_draw_page_change();
    }
    if (!cleared) { lcd_fill(0, 0, 240, 240, C_BLACK); cleared = 1; }
    if (ui_msg_t && fm1_ms - ui_msg_t >= MSG_MS && fm1_ms - ui_msg_t < MSG_MS + 100u) ui_mark(E_FOOTER);
    if (ui_hot_t && ui_hot_card < 4u && fm1_ms - ui_hot_t >= HOT_MS && fm1_ms - ui_hot_t < HOT_MS + 100u)
        ui_mark(E_CARD0 + ui_hot_card);
    if (ui_page == PG_SETUP && (fm1_ms & 255u) == 0u) ui_mark(E_FOOTER);   /* live readouts */
    meter_poll();
    if (ui_page == PG_SEQ) {                            /* the playhead ~25 Hz; the notes when they change (4 Hz) */
        static uint32_t ph_t, roll_t;
        uint32_t now, next, p = sq_view_tick();
        (void)ph_t;
        if (p / 24u != sq_shown_cell || (seq_play && sq_tick() / 24u != sq_shown_ph)) ui_mark(E_METER);
        if (p / SQ_PPQ != sq_shown_beat) ui_mark(E_TITLE);
        sq_now_next(p, &now, &next);
        if (now != sq_shown_now && !sq_browsing()) ui_mark(E_ROLL);
        {                                               /* the cursor timing out (playing): back to the loop */
            static int was;
            if (was != sq_browsing()) { was = sq_browsing(); ui_mark(E_ROLL); ui_mark(E_METER); ui_mark(E_TITLE); ui_mark(E_CARD0 + 3); }
        }
        if (sq_roll_dirty && fm1_ms - roll_t >= 250u) { roll_t = fm1_ms; sq_roll_dirty = 0; ui_mark(E_ROLL); ui_mark(E_METER); ui_mark(E_FOOTER); }
    }
    d = ui_dirty & ((1u << E_N) - 1u);
    if (!d || fm1_ms - ui_last_draw < 4u) return;
    e = d >> E_FOOTER & 1u ? E_FOOTER : d & 1u ? E_HEADER : d >> E_TITLE & 1u ? E_TITLE : 0xFF;
    if (e == 0xFF) for (e = E_CARD0; e < E_N && !(d >> e & 1u); e++) {}
    ui_dirty &= ~(1u << e);
    ui_last_draw = fm1_ms;
    if (e == E_HEADER) draw_header();
    else if (e == E_TITLE) draw_title();
    else if (e == E_FOOTER) draw_footer();
    else if (e == E_METER) {
        if (ui_page == PG_COMP && !pr_save_mode && !(ui_msg_t && fm1_ms - ui_msg_t < MSG_MS)) draw_meter();
        else if (ui_page == PG_SEQ) draw_strip();
    } else if (e == E_ROLL) { if (ui_page == PG_SEQ) draw_now(); }
    else if (ui_page == PG_SEQ) { draw_card_small(e - E_CARD0); if (e == E_CARD0) ui_mark(E_ROLL); }
    else draw_row(e - E_CARD0);
}

/* ---- input */
static void ui_goto_v(uint32_t pg, uint32_t vp)         /* a page (VOICE: its number vp) */
{
    if (pg >= PG_N || pg == PG_RETIRED4) pg = PG_PERF;
    if ((pg == PG_SEQ) != (ui_page == PG_SEQ)) lcd_fill(0, TI_Y, 240, FT_Y - TI_Y, C_BLACK);   /* its own layout: no leftovers */
    ui_page = (uint8_t)pg;
    ui_vp = (uint8_t)(pg == PG_VOICE && vp < vp_count() ? vp : 0u);
    ui_hot_card = 0xFF;
    ui_draw_page_change();
}
static void ui_goto(uint32_t pg) { ui_goto_v(pg, 0); }

/* LFO / SEQ / SEL / ENV: go to the effect's page; on it, the effect on <-> off (back to the last one used) */
static void ui_fx_button(uint32_t pg, uint32_t k)
{
    static const uint8_t SW[5] = {P_TW_SW, P_CP_SW, P_DL_SW, P_PH_SW, P_CMP_SW};
    static const char *const NAME[5] = {"TREM/WAH", "CHORUS", "DELAY", "PHASER", "COMP"};
    uint32_t id = SW[k], sw = PV(id), n = 0;
    char s[24];
    if (ui_page != pg) { ui_goto(pg); return; }
    if (sw) { if (k < 3u) set.last_on[k] = (uint8_t)sw; param_set(id, 0); }
    else param_set(id, k < 3u && set.last_on[k] <= PARAMS[id].hi ? set.last_on[k] : 1);
    fx_wake(id);
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
    if (ui_splash_on) for (i = 0; i < 7u; i++) if (d[i] || b) ui_splash_t = fm1_ms - UI_SPLASH_MS;
    if (sq_btn_down) {                                  /* SEQ on SEQUENCER: a press toggles REC, a hold clears */
        if (!(fm1_in.buttons >> BTN_SEQ & 1u) || rel >> BTN_SEQ & 1u) {
            sq_btn_down = 0;
            seq_toggle_rec(); ui_say(seq_rec ? "REC ON" : "REC OFF");
        } else if (fm1_ms - sq_btn_t >= 1000u) { sq_btn_down = 0; seq_clear(); if (seq_rec) seq_toggle_rec(); ui_say("PATTERN CLEARED"); }
    }
    {
        uint8_t was = pr_save_mode;
        if (ui_page == PG_SEQ && !was && d[ENC_PRESETS]) {   /* on SEQUENCER: PRESETS moves the cursor bar by bar */
            sq_browse(sgn(d[ENC_PRESETS]) * 16);
            ui_mark(E_TITLE); ui_mark(E_ROLL); ui_mark(E_METER); ui_mark(E_CARD0 + 3);
            d[ENC_PRESETS] = 0;
        }
        presets_poll(d[ENC_PRESETS], was ? d[ENC_KNOB1] : 0, b, rel);
        if (was || pr_save_mode) { ui_mark(E_FOOTER); return; }      /* the save mode owns the pass */
    }
    if (b >> BTN_HOME & 1u) ui_goto(ui_page == PG_PERF ? PG_PIANO : PG_PERF);    /* PERFORM <-> the main page */
    if (b >> BTN_EDIT & 1u) {                           /* CHORD; on it: chord mode on / off (0.9.3: was VOICE) */
        if (ui_page != PG_CHORD) ui_goto(PG_CHORD);
        else { chord_toggle(); ui_say(chord_on() ? "CHORDS ON" : "CHORDS OFF"); ui_changed(); ui_mark(E_CARDS); }
    }
    if (b >> BTN_ENV & 1u) ui_fx_button(PG_PHA, 3);   /* PHASER; on it: on / off (0.9.2: was DECAY / COMP) */
    if (b >> BTN_GLO & 1u) ui_goto(ui_page == PG_TEMPO ? PG_SETUP : ui_page == PG_SETUP ? PG_MIDI : PG_TEMPO);   /* TEMPO -> SETUP -> MIDI */
    if (b >> BTN_LFO & 1u) ui_fx_button(PG_TREM, 0);
    if (b >> BTN_SEL & 1u) {                            /* DELAY; on it: on / off (0.9.2: was CHORUS); on ARPEGGIO:
                                                         * HOLD on / off (0.9.3: was PLAY, so PLAY taps the tempo there) */
        if (ui_page == PG_ARP) { arp_hold_toggle(); ui_say(ARP_HOLD ? "HOLD ON" : "HOLD OFF"); ui_changed(); ui_mark(E_FOOTER); }
        else ui_fx_button(PG_DELAY, 2);
    }
    if (b >> BTN_FX & 1u) {                             /* bypass all effects: nothing else changes */
        fx_bypass = !fx_bypass;
        ui_say(fx_bypass ? "EFFECTS OFF" : "EFFECTS ON");
        ui_mark(E_HEADER);
    }
    if (b >> BTN_SEQ & 1u) {                            /* SEQUENCER (0.9.3: no CHORD page on the way, EDIT has it);
                                                         * on SEQUENCER: recording on / off */
        if (ui_page == PG_SEQ) { sq_btn_down = 1; sq_btn_t = fm1_ms; }   /* short: REC on / off; held 1 s: CLEAR */
        else ui_goto(PG_SEQ);
    }
    if (b >> BTN_ARP & 1u) {                            /* ARPEGGIO; on it: the arpeggiator on / off */
        if (ui_page != PG_ARP) ui_goto(PG_ARP);
        else { arp_toggle(); ui_say(arp_on() ? "ARP ON" : "ARP OFF"); ui_changed(); ui_mark(E_CARDS); }
    }
    if (b >> BTN_PLAY & 1u) {                           /* tap tempo (ARPEGGIO too: HOLD is SEL there, 0.9.3) */
        if (ui_page == PG_SEQ) seq_toggle_play();
        else tempo_tap();
    }
    if (b >> BTN_OCT_DOWN & 1u && set.oct > -2) { set.oct--; ui_changed(); ui_mark(E_HEADER); }
    if (b >> BTN_OCT_UP & 1u && set.oct < 2) { set.oct++; ui_changed(); ui_mark(E_HEADER); }

    if (d[ENC_SELECT]) {                                /* the pages: no wrap, so the ends are easy to find */
        int32_t p = clampi((int32_t)pg_pos(ui_page, ui_vp_ok()) + sgn(d[ENC_SELECT]), 0, (int32_t)pg_count() - 1);
        uint32_t vp, pg = pg_at((uint32_t)p, &vp);
        if (pg != ui_page || vp != ui_vp) ui_goto_v(pg, vp);   /* (VOICE n -> n + 1: a page of its own) */
    }
    if (d[ENC_ALGORITHM] && ui_page == PG_SEQ) {        /* on SEQUENCER: the cursor, step by step */
        if (seq_step_held()) {                          /* step recording, keys held: the step gets longer */
            char m[24];
            uint32_t k = 0;
            seq_step_stretch(d[ENC_ALGORITHM]);
            p_strcpy(m, "LENGTH ", 7); k = 7; p_fmt_int(m + k, (int32_t)st_len); while (m[k]) k++;
            p_strcpy(m + k, st_len == 1u ? " STEP" : " STEPS", 6);
            ui_say(m);
        } else sq_browse(d[ENC_ALGORITHM]);
        ui_mark(E_TITLE); ui_mark(E_ROLL); ui_mark(E_METER); ui_mark(E_CARD0 + 3);
    } else if (d[ENC_ALGORITHM]) {
        slot_turn(P_TYPE, sgn(d[ENC_ALGORITHM]));
        ui_say_big(N_TYPE_LONG[PV(P_TYPE)]);           /* the TYPE flash: large */
        ui_mark(E_CARDS);
    }
    for (k = 0; k < 4u; k++) {
        uint32_t s = slot_at(ui_page, k);
        if (!d[ENC_KNOB1 + k] || s == S_NONE) continue;
        slot_turn(s, d[ENC_KNOB1 + k]);
        if ((ui_page == PG_PERF || (ui_page == PG_PIANO && s == P_DRIVE)) && s < P_COUNT) perf_wake(s);
        if (ui_msg_t) { ui_msg_t = 0; ui_mark(E_FOOTER); }   /* an old message would contradict the card */
        if (ui_hot_card != k && ui_hot_card < 4u) ui_mark(E_CARD0 + ui_hot_card);
        ui_hot_card = (uint8_t)k; ui_hot_t = fm1_ms;
        ui_mark(E_CARD0 + k);
        if (s == P_DL_SW || s == P_CP_SW || s == S_TWKIND || s == P_TW_SYNC || s == P_CP_SYNC || s == P_DL_SYNC ||
            s == P_PH_SYNC || s == P_CMP_SW || s == P_LFO_SYNC || s == P_FL_TYPE || s == P_DRV_SW)
            ui_mark(E_CARDS);                           /* a switch or sync changes what the other cards show */
    }
}

/* ---- LEDs, once per pass: the page's shortcut button is lit; LFO / SEL / ENV: that effect is on (SEL on ARPEGGIO:
 * HOLD), ARP: the arpeggiator, EDIT: chord mode, SEQ: the sequencer playing (blinking: recording) */
static void ui_leds(uint32_t lit, uint32_t beat_on)
{
    uint32_t k;
    fm1_led_key(BTN_LFO, PV(P_TW_SW) != 0);
    fm1_led_key(BTN_FX, !fx_bypass);
    fm1_led_key(BTN_SEL, ui_page == PG_ARP ? ARP_HOLD : PV(P_DL_SW) != 0);   /* ARPEGGIO: HOLD */
    fm1_led_key(BTN_ARP, arp_on());
    fm1_led_key(BTN_SEQ, seq_rec ? (fm1_ms & 256u) != 0 : seq_play);     /* playing; blinks: recording */
    fm1_led_key(BTN_HOME, ui_page == PG_PIANO || ui_page == PG_PERF);
    fm1_led_key(BTN_EDIT, chord_on());                                  /* chord mode (0.9.3) */
    fm1_led_key(BTN_ENV, PV(P_PH_SW) != 0);
    fm1_led_key(BTN_GLO, ui_page == PG_TEMPO || ui_page == PG_SETUP || ui_page == PG_MIDI);
    fm1_led_key(BTN_SAVE, pr_save_mode && (fm1_ms & 256u));
    fm1_led_key(BTN_PLAY, ui_page == PG_SEQ ? seq_play && beat_on : beat_on);
    fm1_led_key(BTN_OCT_DOWN, set.oct < 0);
    fm1_led_key(BTN_OCT_UP, set.oct > 0);
    fm1_led_key(BTN_REC, rh_hold);
    for (k = 0; k < 27u; k++) fm1_led_key(KEY_LED(k), lit >> k & 1u);
    for (k = 0; k < 27u; k++) fm1_led_key_glow(KEY_LED(k), scale_is_root(k));   /* the scale's root, dimly */
}
