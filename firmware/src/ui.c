/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes UI: one page at a time, four big cards = the four knobs (the FM-1 has 4 knobs and a 240 x 240 screen,
 * so nothing else competes for them). A draw scheduler sends at most one screen element per main-loop pass
 * (a full screen is ~77 ms of SPI: drawn at once it would delay the keys).
 *
 *   SELECT     turns through the pages (the order of the chain):
 *              PERFORM ASSIGN FRODHES (the main page) VOICE DECAY COMP TREM/WAH PHASER CHORUS DELAY TEMPO SCALE
 *              CHORD SEQUENCER ARPEGGIO SETUP MIDI
 *   K1..K4     the four cards of the page, always
 *   ALGORITHM  TYPE (on SEQUENCER: a cursor, step by step)   PRESETS  sounds (presets.c; on SEQUENCER: bar by bar)
 *   shortcuts  HOME = PERFORM (again: the main page), EDIT = VOICE, LFO / ENV / SEL = TREM / PHASER / DELAY (on
 *              that page: the effect on / off), SEQ = CHORD (chord mode on: SEQUENCER; on it: REC),
 *              ARP = ARPEGGIO (on it: on / off), GLO = TEMPO (again: SETUP, MIDI).
 *   FX         all effects off / on (a bypass: no setting changes; not saved). PLAY: tap tempo. REC: sustain. */

#define PR_NF_UI 12u                                  /* presets.c: PR_NF factory presets, then the user ones */
#define PR_NALL_UI 28u
static uint8_t pr_save_mode, pr_save_slot;            /* presets.c */
static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel);
static void preset_name(uint32_t idx, char *s);
static void tempo_tap(void);                          /* tempo.c */
static void tempo_set10(uint32_t b);

enum { PG_PERF, PG_ASSIGN, PG_PIANO, PG_VOICE, PG_DECAY, PG_COMP, PG_TREM, PG_PHA, PG_CHO, PG_DELAY, PG_TEMPO, PG_SCALE,
       PG_CHORD, PG_SEQ, PG_ARP, PG_SETUP, PG_MIDI, PG_N };
enum { E_HEADER = 0, E_TITLE = 1, E_CARD0 = 2, E_FOOTER = 6, E_METER = 7, E_ROLL = 8, E_N = 9, E_CARDS = 10 };   /* E_CARDS: all four;
                                    * E_METER: COMP's meter / SEQUENCER's cells, E_ROLL: SEQUENCER's NOW / NEXT */
/* card slots: a parameter id (< P_COUNT) or one of these */
enum { S_KVEL = 64, S_TUNE, S_BPM, S_CLOCK, S_SCMODE, S_SCALE, S_ROOT, S_TRANS, S_TWKIND, S_PERF0, S_ASG0 = S_PERF0 + 4,
       S_ARPMODE = S_ASG0 + 4, S_ARPRATE, S_ARPOCT, S_ARPGATE, S_USBVOL, S_CHTYPE, S_CHINV, S_CHVOICE, S_CHSTRUM,
       S_SQBARS, S_SQQUANT, S_SQSWING, S_SQEDIT, S_MCH, S_TRS, S_NONE = 0xFF };

static int sq_browsing(void);                           /* the SEQUENCER page's cursor (below) */
static uint32_t sq_delete_step(void);
static void sq_browse(int32_t d);
static const char *const PAGE_TITLE[PG_N] = {"PERFORM", "ASSIGN", "FRODHES", "VOICE", "DECAY", "COMPRESSOR", "TREM/WAH", "PHASER",
                                             "CHORUS", "DELAY", "TEMPO", "SCALE", "CHORD", "SEQUENCER",
                                             "ARPEGGIO", "SETUP", "MIDI"};
static const uint8_t PAGE_S[PG_N][4] = {
    {S_PERF0, S_PERF0 + 1, S_PERF0 + 2, S_PERF0 + 3},
    {S_ASG0, S_ASG0 + 1, S_ASG0 + 2, S_ASG0 + 3},
    {P_TYPE, P_DRIVE, P_RV_DEPTH, S_KVEL},
    {P_TONE, P_BARK, P_BELL, P_HAMMER},
    {P_DECAY, P_RELEASE, P_NOISE, S_TUNE},
    {P_CMP_AMT, P_CMP_RATIO, P_CMP_ATK, P_CMP_GAIN},
    {S_TWKIND, P_TW_DEPTH, P_TW_RATE, P_TW_SHAPE},
    {P_PH_SW, P_PH_DEPTH, P_PH_SPEED, P_PH_FDBK},
    {P_CP_SW, P_CP_DEPTH, P_CP_SPEED, P_CP_SYNC},
    {P_DL_SW, P_DL_DEPTH, P_DL_TIME, P_DL_FDBK},
    {S_BPM, P_TW_SYNC, P_PH_SYNC, P_DL_SYNC},
    {S_SCMODE, S_SCALE, S_ROOT, S_TRANS},
    {S_CHTYPE, S_CHINV, S_CHVOICE, S_CHSTRUM},
    {S_SQBARS, S_SQQUANT, S_SQSWING, S_SQEDIT},
    {S_ARPMODE, S_ARPRATE, S_ARPOCT, S_ARPGATE},
    {P_DL_TONE, P_LEVEL, S_USBVOL, S_NONE},
    {S_MCH, S_TRS, S_CLOCK, S_NONE},
};
static const char *const PAGE_LAB[PG_N][4] = {
    {"", "", "", ""},                                   /* PERFORM: the assigned parameter's name (slot_label) */
    {"PLAYS", "PLAYS", "PLAYS", "PLAYS"},
    {"TYPE", "DRIVE", "REVERB", "KEY VEL"},
    {"TONE", "BARK", "BELL", "HAMMER"},
    {"DECAY", "RELEASE", "NOISE", "TUNE"},
    {"THRESH", "RATIO", "ATTACK", "MAKEUP"},
    {"EFFECT", "DEPTH", "RATE", "SHAPE"},
    {"EFFECT", "DEPTH", "SPEED", "FEEDBK"},
    {"EFFECT", "DEPTH", "SPEED", "SYNC"},
    {"EFFECT", "MIX", "TIME", "FEEDBK"},
    {"BPM", "TREM SYNC", "PHA SYNC", "DLY SYNC"},
    {"MODE", "SCALE", "ROOT", "TRANSP"},
    {"TYPE", "INVERSION", "VOICING", "STRUM"},
    {"BARS", "QUANT", "SWING", "EDIT"},
    {"MODE", "RATE", "OCTAVES", "GATE"},
    {"DLY TONE", "LEVEL", "USB VOL", ""},
    {"CHANNEL", "TRS IN", "CLOCK", ""},
};
static const char *const PAGE_HINT[PG_N] = {"NEXT PAGE: ASSIGN THE KNOBS", "WHAT EACH PERFORM KNOB PLAYS",
                                            "FX: ALL EFFECTS ON / OFF", "HOW THE TINE IS STRUCK", "HOW NOTES DIE AWAY",
                                            "K1 THRESH: UP = ON, 0 = OFF", "LFO BUTTON: ON / OFF", "ENV BUTTON: ON / OFF",
                                            "K1: ON / OFF", "SEL: ON / OFF  K1: DIG / ANA", "PLAY: TAP TEMPO", "DEGREE: WHITE KEYS PLAY IT",
                                            "SEQ: THE SEQUENCER", "", "ARP: ON / OFF   PLAY: HOLD", "",
                                            "USB AND TRS: THE SAME CHANNEL"};
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
/* the version on screen: a release build's ("0.8.1"), else DEV (dev builds are "0.0", host / web "dev" / "web") */
static const char *ui_version(void)
{
    static const char v[] = APP_VER;
    return v[0] < '0' || v[0] > '9' || (v[0] == '0' && v[1] == '.' && v[2] == '0' && !v[3]) ? "DEV" : v;
}
static void ui_changed(void) { set_dirty = 1; set_t_change = fm1_ms; }
static int32_t sgn(int32_t d) { return d > 0 ? 1 : -1; }

/* switching an effect on from the panel while its depth is 0 would be silent (the depths default to 0): give it a
 * starting depth then; a depth the player set is never touched */
static void fx_wake(uint32_t sw_id)
{
    static const uint8_t SW[5] = {P_TW_SW, P_PH_SW, P_CP_SW, P_DL_SW, P_CMP_SW};
    static const uint8_t DEPTH[5] = {P_TW_DEPTH, P_PH_DEPTH, P_CP_DEPTH, P_DL_DEPTH, P_CMP_AMT};
    static const uint8_t START[5] = {64, 80, 64, 50, 64};
    uint32_t i;
    for (i = 0; i < 5u; i++)
        if (SW[i] == sw_id && PV(sw_id) && !PV(DEPTH[i])) {
            param_set(DEPTH[i], START[i]);
            if (sw_id == P_CMP_SW && !PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, 15);   /* -20 dB, 4:1: +4.3 dB is
                                                                                * level-neutral (FM-1, LUFS) */
            ui_mark(E_CARDS);
        }
}

/* ---- PERFORM: four knobs that play whatever ASSIGN gives them; by default the effects' dry / wet. The list is
 * append-only (settings keep an index into it). */
typedef struct { uint8_t id; const char *name; } perf_t;
static const perf_t PERF_T[] = {
    {P_TW_DEPTH, "TREM/WAH"}, {P_PH_DEPTH, "PHASER"}, {P_CP_DEPTH, "CHORUS"}, {P_DL_DEPTH, "DELAY"},
    {P_RV_DEPTH, "REVERB"}, {P_DRIVE, "DRIVE"}, {P_CMP_AMT, "COMP THR"}, {P_CMP_GAIN, "COMP MKUP"},
    {P_TW_RATE, "TREM RATE"}, {P_TW_SHAPE, "TREM SHAPE"}, {P_PH_SPEED, "PHA SPEED"}, {P_PH_FDBK, "PHA FEEDBK"},
    {P_CP_SPEED, "CHO SPEED"}, {P_DL_TIME, "DLY TIME"}, {P_DL_FDBK, "DLY FEEDBK"}, {P_DL_TONE, "DLY TONE"},
    {P_TONE, "TONE"}, {P_BARK, "BARK"}, {P_BELL, "BELL"}, {P_HAMMER, "HAMMER"}, {P_DECAY, "DECAY"},
    {P_RELEASE, "RELEASE"}, {P_NOISE, "NOISE"}, {S_KVEL, "KEY VEL"}, {P_LEVEL, "LEVEL"},
    {S_ARPRATE, "ARP RATE"}, {S_ARPGATE, "ARP GATE"},
};
#define PERF_N (sizeof PERF_T / sizeof PERF_T[0])
static uint32_t perf_idx(uint32_t k)                     /* knob k's entry in PERF_T */
{
    uint32_t v = set.perf[k];
    return v && v <= PERF_N ? v - 1u : k;                /* default: TREM/WAH, PHASER, CHORUS, DELAY */
}
/* turning an effect's amount up from PERFORM switches the effect on (TREM/WAH: as TREM unless WAH was chosen) */
static void perf_wake(uint32_t id)
{
    if (!PV(id)) return;
    if (id == P_TW_DEPTH && !PV(P_TW_SW)) param_set(P_TW_SW, 1);
    else if (id == P_PH_DEPTH && !PV(P_PH_SW)) param_set(P_PH_SW, 1);
    else if (id == P_CP_DEPTH && !PV(P_CP_SW)) param_set(P_CP_SW, 1);
    else if (id == P_DL_DEPTH && !PV(P_DL_SW)) param_set(P_DL_SW, 1);
    else if (id == P_CMP_AMT && !PV(P_CMP_SW)) { param_set(P_CMP_SW, 1); if (!PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, 15); }
}
/* the slot a card shows: PERFORM's are resolved to the assigned parameter */
static uint32_t slot_at(uint32_t pg, uint32_t k)
{
    uint32_t s = PAGE_S[pg][k];
    return s >= S_PERF0 && s < S_PERF0 + 4u ? PERF_T[perf_idx(s - S_PERF0)].id : s;
}
static const char *slot_label(uint32_t pg, uint32_t k)
{
    return pg == PG_PERF ? PERF_T[perf_idx(k)].name : PAGE_LAB[pg][k];
}

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
static void slot_text(uint32_t s, char *v)
{
    v[0] = 0;
    switch (s) {
    case P_TYPE: p_strcpy(v, N_TYPE_LONG[PV(P_TYPE)], 12); return;
    case P_CMP_AMT: if (!PV(P_CMP_SW)) { p_strcpy(v, "OFF", 3); return; } break;
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
    case S_ASG0: case S_ASG0 + 1: case S_ASG0 + 2: case S_ASG0 + 3: p_strcpy(v, PERF_T[perf_idx(s - S_ASG0)].name, 12); return;
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
        if (id == P_DL_TIME && PV(P_DL_SYNC)) { id = P_DL_SYNC; step = sgn(d); }
        else if (id == P_CP_SPEED && PV(P_CP_SYNC)) { id = P_CP_SYNC; step = -sgn(d); }
        else if (id == P_PH_SPEED && PV(P_PH_SYNC)) { id = P_PH_SYNC; step = -sgn(d); }
        else if (id == P_TW_RATE && PV(P_TW_SYNC) && PV(P_TW_SW) != 2) { id = P_TW_SYNC; step = -sgn(d); }
        v = PV(id) + step;
        if (id != s && v < 1) v = 1;
        param_set(id, clampi(v, 0, PARAMS[id].hi));
        fx_wake(id);
        if (id == P_CMP_AMT) {                          /* THRESH is the compressor's switch: 0 = OFF, up = on */
            if (PV(P_CMP_AMT) && !PV(P_CMP_SW) && !PV(P_CMP_GAIN)) param_set(P_CMP_GAIN, 15);   /* level-neutral */
            param_set(P_CMP_SW, PV(P_CMP_AMT) != 0);
            ui_mark(E_CARDS); ui_mark(E_FOOTER);
        }
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
    case S_CHTYPE: CH_TYPE = (uint8_t)clampi(CH_TYPE + sgn(d), 0, CH_NTYPE - 1); break;
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
#define GR_X0 4
#define GR_W 12
#define GR_X1 (GR_X0 + (int32_t)GR_SEGS * GR_W + 2)
static const int16_t GR_DB10[GR_SEGS] = {10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 180, 200, 240, 270, 300};
static const uint8_t GR_LAB[GR_SEGS] = {0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1};    /* 3 6 10 20 30 */
static int32_t gr_db10;
static uint8_t gr_peak;
static uint32_t gr_shown = 0xFFFF;

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
        cv_text(116, 2, &FONT_S, o, C_GRAY);
    }
    if (rh_hold) cv_text(142, 2, &FONT_S, "SUS", UI_ACC);
    if (fx_bypass) cv_text(166, 2, &FONT_S, "BYP", C_HI);
    slot_text(S_BPM, b);
    cv_text(238 - text_w(&FONT_S, b), 2, &FONT_S, b, C_AMB);
    cv_rect(0, 19, 240, 1, C_LINE);
    cv_blit(0, 0);
}

static void seq_pos_text(char *t);
static uint32_t sq_view_tick(void);
static void sq_shown_beat_set(void);
static void draw_title(void)
{
    uint32_t i;
    int32_t x0 = 120 - (int32_t)PG_N * 5;
    if (ui_page == PG_SEQ) {                            /* a small title and where the loop is: BAR 2/4 . 3 */
        char t[24];
        cv_begin(240, 18, C_BLACK);
        cv_text(2, 1, &FONT_S, "< SEQUENCER >", UI_ACC);
        seq_pos_text(t);
        sq_shown_beat_set();
        cv_text(238 - text_w(&FONT_S, t), 1, &FONT_S, t, C_HI);
        cv_rect(0, 17, 240, 1, C_LINE);
        cv_blit(0, 21);
        return;
    }
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
    uint32_t s = slot_at(ui_page, k);
    int hot = ui_hot_card == k && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS;
    cv_begin(118, 68, C_BLACK);
    if (s != S_NONE) {
        char v[16], kk[3] = {'K', (char)('1' + k), 0};
        int32_t hi, val = slot_val(s, &hi);
        cv_rect(0, 0, 118, 68, hot ? UI_ACC : C_LINE);
        cv_rect(1, 1, 116, 66, C_BLACK);
        cv_text(5, 3, &FONT_S, kk, C_DIM);
        cv_text(28, 3, &FONT_S, slot_label(ui_page, k), C_GRAY);
        slot_text(s, v);
        if (text_w(&FONT_L, v) <= 112) text_c(0, 21, 118, &FONT_L, v, C_HI);
        else text_c(0, 29, 118, &FONT_S, v, C_HI);
        if (hi && slot_is_level(s)) gauge(5, 56, 108, val, hi);
    }
    cv_blit(k & 1u ? 121u : 1u, k & 2u ? 135u : 63u);
}

/* ---- the SEQUENCER page, made to be read on a small screen: what plays NOW in big letters, what comes NEXT, and the
 * bar as 16 big cells (a filled cell: notes start there; white: playing). Its four cards are a compact row. */
#define SQ_NOW_Y 40
#define SQ_STRIP_Y 104
#define SQ_CARD_Y 171
#define SQ_CARD_H 33
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
    cv_begin(240, 60, C_BLACK);
    if (!sq_n) {
        text_c(0, 8, 240, &FONT_L, "EMPTY", C_DIM);
        text_c(0, 42, 240, &FONT_S, "SEQ: REC, THEN PLAY", C_GRAY);
    } else {
        if (empty) p_strcpy(v, "-", 1); else sq_group_name(now, v);
        if (text_w(&FONT_L, v) <= 236) text_c(0, 4, 240, &FONT_L, v, sq_browsing() ? UI_ACC : seq_play ? C_WHITE : C_HI);
        else text_c(0, 12, 240, &FONT_S, v, C_HI);
        sq_group_name(next, v);
        p_strcpy(t, "NEXT ", 5); n = 5; p_strcpy(t + n, v, 12); while (t[n]) n++;
        p_strcpy(t + n, "  BAR ", 6); n += 6;
        p_fmt_int(t + n, (int32_t)(next / (4u * SQ_PPQ) + 1u)); while (t[n]) n++;
        t[n++] = '.'; p_fmt_int(t + n, (int32_t)(next / SQ_PPQ % 4u + 1u));
        text_c(0, 42, 240, &FONT_S, t, C_GRAY);
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
static void draw_card_small(uint32_t k)
{
    uint32_t s = PAGE_S[PG_SEQ][k];
    int hot = ui_hot_card == k && ui_hot_t && fm1_ms - ui_hot_t < HOT_MS;
    char v[16];
    cv_begin(58, SQ_CARD_H, C_BLACK);
    cv_rect(0, 0, 58, SQ_CARD_H, hot ? UI_ACC : C_LINE);
    cv_rect(1, 1, 56, SQ_CARD_H - 2, C_BLACK);
    text_c(0, 1, 58, &FONT_S, PAGE_LAB[PG_SEQ][k], C_GRAY);
    slot_text(s, v);
    text_c(0, 16, 58, &FONT_S, v, s == S_SQEDIT && sq_erase ? UI_ACC : C_HI);
    cv_blit(1u + k * 60u, SQ_CARD_Y);
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
        if (ui_page == PG_COMP) {                        /* the gain-reduction meter (draw_meter) and its scale */
            uint32_t i;
            if (PV(P_CMP_SW)) {
                for (i = 0; i < GR_SEGS; i++)
                    if (GR_LAB[i]) { char t[4]; p_fmt_int(t, GR_DB10[i] / 10); text_c(GR_X0 + (int32_t)i * GR_W - 4, 18, GR_W + 6, &FONT_S, t, C_DIM); }
                text_c(GR_X1, 18, 240 - GR_X1, &FONT_S, "GR DB", C_DIM);
            } else text_c(0, 18, 240, &FONT_S, "K1 THRESH: TURN UP = ON", C_DIM);
            gr_shown = 0xFFFF;                          /* the meter rows: draw them again */
            ui_mark(E_METER);
            cv_blit(0, 205);
            return;
        }
        if (ui_page == PG_SEQ) {                       /* the state, and what the buttons do */
            char t[32];
            uint32_t n = 0;
            if (seq_rec) p_strcpy(t, seq_play ? "REC: OVERDUB" : "STEP REC: PLAY NOTES", 20);
            else if (sq_erase) p_strcpy(t, "ERASE: HOLD KEYS", 20);
            else { p_strcpy(t, seq_play ? "PLAYING  " : "STOPPED  ", 10); n = 9; p_fmt_int(t + n, (int32_t)sq_n); while (t[n]) n++; p_strcpy(t + n, " NOTES", 6); }
            text_c(0, 2, 240, &FONT_S, t, seq_rec || sq_erase ? UI_ACC : C_GRAY);
            text_c(0, 18, 240, &FONT_S, "SEQ: REC   HOLD SEQ: CLEAR", C_DIM);
            cv_blit(0, 205);
            return;
        }
        if (ui_page == PG_ARP) text_c(0, 2, 240, &FONT_S, ARP_HOLD ? "HOLD ON: NOTES KEEP PLAYING" : "HOLD OFF",
                                      ARP_HOLD ? UI_ACC : C_GRAY);
        else if (ui_page == PG_SETUP) {                 /* what this is: the name and the version */
            char t[24];
            p_strcpy(t, "FRODHES ", 8); p_strcpy(t + 8, ui_version(), 12);
            text_c(0, 2, 240, &FONT_S, t, UI_ACC);
        } else text_c(0, 2, 240, &FONT_S, "SELECT: PAGE  PRESETS: SOUND", C_GRAY);
        if (ui_page == PG_SETUP) {
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

/* the compressor's gain-reduction meter, as on a hardware compressor: LED segments that light from 1 dB to 30 dB,
 * a peak that holds for a second, and the reading in dB; footer rows 1..16, redrawn only when they change (~30 Hz) */
static void draw_meter(void)
{
    char t[12];
    uint32_t i, n = 0;
    int32_t db10 = gr_db10;
    cv_begin(240, 16, C_BLACK);
    for (i = 0; i < GR_SEGS; i++) {
        uint16_t c = C_LINE;
        if (PV(P_CMP_SW) && db10 >= GR_DB10[i]) c = GR_DB10[i] > 100 ? C_HI : C_AMB;
        else if (PV(P_CMP_SW) && i + 1u == gr_peak) c = C_WHITE;
        cv_rect(GR_X0 + (int32_t)i * GR_W, 3, GR_W - 2, 10, c);
    }
    if (PV(P_CMP_SW)) {
        t[n++] = '-'; p_fmt_int(t + n, db10 / 10);
        while (t[n]) n++;
        t[n++] = '.'; t[n++] = (char)('0' + db10 % 10); t[n] = 0;
        text_c(GR_X1, 0, 240 - GR_X1, &FONT_S, db10 ? t : "0.0", db10 ? C_HI : C_DIM);
    } else text_c(GR_X1, 0, 240 - GR_X1, &FONT_S, "OFF", C_DIM);
    cv_blit(0, 206);
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
    text_c(0, 0, 240, &FONT_L, "FRODHES", UI_ACC);
    text_c(0, 40, 240, &FONT_S, "ELECTRIC PIANO", C_GRAY);
    cv_blit(0, 110);
    cv_begin(240, 20, C_BLACK);
    p_strcpy(v, "VERSION ", 8); p_strcpy(v + 8, ui_version(), 10);
    text_c(0, 2, 240, &FONT_S, v, C_DIM);
    cv_blit(0, 206);
    ui_splash_t = fm1_ms;
    ui_splash_on = 1;
}

static void ui_draw_page_change(void) { ui_dirty |= (1u << E_N) - 1u; }

/* one element per pass, in priority order: footer, header, title, cards */
static void ui_draw(void)
{
    uint32_t e, d;
    static uint8_t cleared;
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
    else draw_card(e - E_CARD0);
}

/* ---- input */
static void ui_goto(uint32_t pg)
{
    if (pg >= PG_N) pg = PG_PERF;
    if ((pg == PG_SEQ) != (ui_page == PG_SEQ)) lcd_fill(0, 21, 240, 183, C_BLACK);   /* its own layout: no leftovers */
    ui_page = (uint8_t)pg;
    ui_hot_card = 0xFF;
    ui_draw_page_change();
}

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
    if (b >> BTN_EDIT & 1u) ui_goto(PG_VOICE);
    if (b >> BTN_ENV & 1u) ui_fx_button(PG_PHA, 3);   /* PHASER; on it: on / off (0.9.2: was DECAY / COMP) */
    if (b >> BTN_GLO & 1u) ui_goto(ui_page == PG_TEMPO ? PG_SETUP : ui_page == PG_SETUP ? PG_MIDI : PG_TEMPO);   /* TEMPO -> SETUP -> MIDI */
    if (b >> BTN_LFO & 1u) ui_fx_button(PG_TREM, 0);
    if (b >> BTN_SEL & 1u) ui_fx_button(PG_DELAY, 2); /* DELAY; on it: on / off (0.9.2: was CHORUS) */
    if (b >> BTN_FX & 1u) {                             /* bypass all effects: nothing else changes */
        fx_bypass = !fx_bypass;
        ui_say(fx_bypass ? "EFFECTS OFF" : "EFFECTS ON");
        ui_mark(E_HEADER);
    }
    if (b >> BTN_SEQ & 1u) {                            /* CHORD (or straight to SEQUENCER when chord mode is on);
                                                         * CHORD -> SEQUENCER; on SEQUENCER: recording on / off */
        if (ui_page == PG_SEQ) { sq_btn_down = 1; sq_btn_t = fm1_ms; }   /* short: REC on / off; held 1 s: CLEAR */
        else if (ui_page == PG_CHORD || chord_on()) ui_goto(PG_SEQ);
        else ui_goto(PG_CHORD);
    }
    if (b >> BTN_ARP & 1u) {                            /* ARPEGGIO; on it: the arpeggiator on / off */
        if (ui_page != PG_ARP) ui_goto(PG_ARP);
        else { arp_toggle(); ui_say(arp_on() ? "ARP ON" : "ARP OFF"); ui_changed(); ui_mark(E_CARDS); }
    }
    if (b >> BTN_PLAY & 1u) {                           /* tap tempo; on ARPEGGIO: HOLD on / off */
        if (ui_page == PG_ARP) { arp_hold_toggle(); ui_say(ARP_HOLD ? "HOLD ON" : "HOLD OFF"); ui_changed(); }
        else if (ui_page == PG_SEQ) seq_toggle_play();
        else tempo_tap();
    }
    if (b >> BTN_OCT_DOWN & 1u && set.oct > -2) { set.oct--; ui_changed(); ui_mark(E_HEADER); }
    if (b >> BTN_OCT_UP & 1u && set.oct < 2) { set.oct++; ui_changed(); ui_mark(E_HEADER); }

    if (d[ENC_SELECT]) {                                /* the pages: no wrap, so the ends are easy to find */
        int32_t p = clampi((int32_t)ui_page + sgn(d[ENC_SELECT]), 0, PG_N - 1);
        if (p != ui_page) ui_goto((uint32_t)p);
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
        ui_say(N_TYPE_LONG[PV(P_TYPE)]);
        ui_mark(E_CARDS);
    }
    for (k = 0; k < 4u; k++) {
        uint32_t s = slot_at(ui_page, k);
        if (!d[ENC_KNOB1 + k] || s == S_NONE) continue;
        slot_turn(s, d[ENC_KNOB1 + k]);
        if (ui_page == PG_PERF && s < P_COUNT) perf_wake(s);
        if (ui_msg_t) { ui_msg_t = 0; ui_mark(E_FOOTER); }   /* an old message would contradict the card */
        if (ui_hot_card != k && ui_hot_card < 4u) ui_mark(E_CARD0 + ui_hot_card);
        ui_hot_card = (uint8_t)k; ui_hot_t = fm1_ms;
        ui_mark(E_CARD0 + k);
        if (s == P_DL_SW || s == P_CP_SW || s == S_TWKIND || s == P_TW_SYNC || s == P_CP_SYNC || s == P_DL_SYNC ||
            s == P_PH_SYNC || s == P_CMP_SW)
            ui_mark(E_CARDS);                           /* a switch or sync changes what the other cards show */
    }
}

/* ---- LEDs, once per pass: the page's shortcut button is lit; LFO / SEQ / SEL / ENV: that effect is on, ARP: the
 * arpeggiator */
static void ui_leds(uint32_t lit, uint32_t beat_on)
{
    uint32_t k;
    fm1_led_key(BTN_LFO, PV(P_TW_SW) != 0);
    fm1_led_key(BTN_FX, !fx_bypass);
    fm1_led_key(BTN_SEL, PV(P_DL_SW) != 0);
    fm1_led_key(BTN_ARP, arp_on());
    fm1_led_key(BTN_SEQ, seq_rec ? (fm1_ms & 256u) != 0 : chord_on());   /* chord mode; blinks: recording */
    fm1_led_key(BTN_HOME, ui_page == PG_PIANO || ui_page == PG_PERF);
    fm1_led_key(BTN_EDIT, ui_page == PG_VOICE);
    fm1_led_key(BTN_ENV, PV(P_PH_SW) != 0);
    fm1_led_key(BTN_GLO, ui_page == PG_TEMPO || ui_page == PG_SETUP || ui_page == PG_MIDI);
    fm1_led_key(BTN_SAVE, pr_save_mode && (fm1_ms & 256u));
    fm1_led_key(BTN_PLAY, ui_page == PG_ARP ? ARP_HOLD : ui_page == PG_SEQ ? seq_play && beat_on : beat_on);   /* ARPEGGIO: HOLD */
    fm1_led_key(BTN_OCT_DOWN, set.oct < 0);
    fm1_led_key(BTN_OCT_UP, set.oct > 0);
    fm1_led_key(BTN_REC, rh_hold);
    for (k = 0; k < 27u; k++) fm1_led_key(KEY_LED(k), lit >> k & 1u);
    for (k = 0; k < 27u; k++) fm1_led_key_glow(KEY_LED(k), scale_is_root(k));   /* the scale's root, dimly */
}
