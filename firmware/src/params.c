/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes parameters (IMPLEMENTATION-v3 §10.1, §10.5): one table drives the panel, the pages, the settings record,
 * presets and the MIDI CC map. Ids and enum values are APPEND-ONLY (stored records map by id).
 * Values are bytes 0..127 (bipolar ones are stored with +64: TONE 0..127 means -64..+63). */

enum { P_TYPE, P_DRIVE, P_TW_SW, P_TW_DEPTH, P_TW_RATE, P_TW_SYNC, P_TW_MODE, P_TW_SHAPE,
       P_CP_SW, P_CP_DEPTH, P_CP_SPEED, P_CP_SYNC, P_CP_FDBK,
       P_DL_SW, P_DL_DEPTH, P_DL_TIME, P_DL_SYNC, P_DL_FDBK, P_DL_TONE,
       P_RV_DEPTH, P_TONE, P_BARK, P_BELL, P_HAMMER, P_DECAY, P_RELEASE, P_NOISE, P_SPREAD, P_COUNT };

enum { F_R10, F_ENUM, F_HZ, F_MS, F_DIV, F_BIP };
typedef struct {
    const char *lab;          /* <= 4 chars: panel / card label */
    uint8_t hi, def, fmt, cc;
    const char *const *names; /* F_ENUM */
} pdesc_t;

static const char *const N_TYPE[] = {"ST77", "SUIT", "FELT", "DYNO"};
static const char *const N_TYPE_LONG[] = {"STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO"};
static const char *const N_TW[] = {"OFF", "TRM", "WAH"};
static const char *const N_CP[] = {"OFF", "CHO", "PHA"};
static const char *const N_DL[] = {"OFF", "DIG", "ANA"};
static const char *const N_TWM[] = {"PAN", "AM"};
static const char *const N_SYNC[] = {"FREE", "1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D", "1/2",
                                     "1/1", "2BAR", "4BAR"};

static const pdesc_t PARAMS[P_COUNT] = {
    [P_TYPE] = {"TYPE", 3, 0, F_ENUM, 80, N_TYPE},
    [P_DRIVE] = {"DRV", 127, 13, F_R10, 81, 0},
    [P_TW_SW] = {"TREM", 2, 0, F_ENUM, 17, N_TW},
    [P_TW_DEPTH] = {"DPTH", 127, 64, F_R10, 18, 0},
    [P_TW_RATE] = {"RATE", 127, 90, F_HZ, 19, 0},
    [P_TW_SYNC] = {"SYNC", 13, 0, F_DIV, 20, N_SYNC},
    [P_TW_MODE] = {"MODE", 1, 0, F_ENUM, 21, N_TWM},
    [P_TW_SHAPE] = {"SHPE", 127, 0, F_R10, 22, 0},
    [P_CP_SW] = {"CHO", 2, 0, F_ENUM, 85, N_CP},
    [P_CP_DEPTH] = {"DPTH", 127, 50, F_R10, 86, 0},
    [P_CP_SPEED] = {"SPD", 127, 50, F_HZ, 87, 0},
    [P_CP_SYNC] = {"SYNC", 14, 0, F_DIV, 23, N_SYNC},
    [P_CP_FDBK] = {"FDBK", 127, 40, F_R10, 24, 0},
    [P_DL_SW] = {"DLY", 2, 0, F_ENUM, 88, N_DL},
    [P_DL_DEPTH] = {"DPTH", 127, 40, F_R10, 89, 0},
    [P_DL_TIME] = {"TIME", 127, 85, F_MS, 90, 0},
    [P_DL_SYNC] = {"SYNC", 13, 0, F_DIV, 25, N_SYNC},
    [P_DL_FDBK] = {"FDBK", 127, 90, F_R10, 26, 0},
    [P_DL_TONE] = {"TONE", 127, 70, F_R10, 27, 0},
    [P_RV_DEPTH] = {"REV", 127, 25, F_R10, 91, 0},
    [P_TONE] = {"TONE", 127, 64, F_BIP, 28, 0},
    [P_BARK] = {"BARK", 127, 64, F_R10, 29, 0},
    [P_BELL] = {"BELL", 127, 64, F_R10, 30, 0},
    [P_HAMMER] = {"HAMR", 127, 64, F_R10, 31, 0},
    [P_DECAY] = {"DCAY", 127, 64, F_R10, 102, 0},
    [P_RELEASE] = {"REL", 127, 64, F_R10, 103, 0},
    [P_NOISE] = {"NOIS", 127, 48, F_R10, 104, 0},
    [P_SPREAD] = {"SPRD", 127, 0, F_R10, 105, 0},
};

/* ---- the settings record: always 256 B, versioned, append-only (§10.5) */
#define SET_VER 1u
typedef struct {
    uint8_t ver, flags;
    int8_t oct;
    uint8_t preset;
    uint16_t bpm10;
    uint8_t clock, midi_ch, key_vel, vel_curve;
    int8_t tune;
    uint8_t sel_role, focus, color, leds, humanize, sc_mode, sc_scale, sc_root;
    int8_t sc_trans;
    uint8_t last_on[3], hints_seen;
    uint8_t panel[32], undo[32], undo_valid;
    uint8_t reserved[167];
} settings_t;
_Static_assert(sizeof(settings_t) == 256, "settings record is 256 B");

static settings_t set;
static uint8_t set_dirty;
static uint32_t set_t_change;

static void params_defaults(void)
{
    uint32_t i;
    for (i = 0; i < sizeof set; i++) ((uint8_t *)&set)[i] = 0;
    set.ver = SET_VER;
    set.preset = 0;
    set.bpm10 = 1200;
    set.clock = 1;                 /* AUTO */
    set.key_vel = 96;
    set.focus = 0;                 /* TREM column */
    set.color = 1;                 /* AMBER */
    set.last_on[0] = set.last_on[1] = set.last_on[2] = 1;
    set.sc_scale = 0;
    for (i = 0; i < P_COUNT; i++) set.panel[i] = PARAMS[i].def;
}

static void params_sanitize(void)
{
    uint32_t i;
    if (set.ver != SET_VER) { params_defaults(); return; }
    for (i = 0; i < P_COUNT; i++) if (set.panel[i] > PARAMS[i].hi) set.panel[i] = PARAMS[i].def;
    if (set.oct < -2 || set.oct > 2) set.oct = 0;
    if (set.bpm10 < 400 || set.bpm10 > 2400) set.bpm10 = 1200;
    if (!set.key_vel || set.key_vel > 127) set.key_vel = 96;
    if (set.tune < -50 || set.tune > 50) set.tune = 0;
    if (set.focus > 2) set.focus = 0;
    if (set.sc_mode > 2) set.sc_mode = 0;
    if (set.sc_scale > 10) set.sc_scale = 0;
    if (set.sc_root > 11) set.sc_root = 0;
    if (set.sc_trans < -24 || set.sc_trans > 24) set.sc_trans = 0;
    for (i = 0; i < 3; i++) if (set.last_on[i] < 1 || set.last_on[i] > 2) set.last_on[i] = 1;
}

/* ---- apply a parameter to the engine (main loop; volatile one-writer handover) */
static void param_apply(uint32_t id)
{
    int32_t v = set.panel[id];
    switch (id) {
    case P_TYPE: rh_type = (uint8_t)v; fx_set(FXP_DRIVE_ROW, v); break;
    case P_DRIVE: fx_set(FXP_DRIVE, v); break;
    case P_TW_SW: fx_set(FXP_TW_SW, v); break;
    case P_TW_DEPTH: fx_set(FXP_TW_DEPTH, v); break;
    case P_TW_RATE: fx_set(FXP_TW_RATE, v); break;
    case P_TW_MODE: fx_set(FXP_TW_MODE, v); break;
    case P_TW_SHAPE: fx_set(FXP_TW_SHAPE, v); break;
    case P_CP_SW: fx_set(FXP_CP_SW, v); break;
    case P_CP_DEPTH: fx_set(FXP_CP_DEPTH, v); break;
    case P_CP_SPEED: fx_set(FXP_CP_SPEED, v); break;
    case P_CP_FDBK: fx_set(FXP_CP_FDBK, v); break;
    case P_DL_SW: fx_set(FXP_DL_SW, v); break;
    case P_DL_DEPTH: fx_set(FXP_DL_DEPTH, v); break;
    case P_DL_TIME: fx_set(FXP_DL_TIME, v); break;
    case P_DL_FDBK: fx_set(FXP_DL_FDBK, v); break;
    case P_RV_DEPTH: fx_set(FXP_RV_DEPTH, v); break;
    case P_TONE: rh_tone_off = (v - 64) * (1 << 18); break;          /* +-0.5 gap */
    case P_BARK: rh_bark_s = (int32_t)KN_P64[v]; rh_bark_g = (int32_t)KN_P64I[v]; break;
    case P_BELL: rh_bell = (int32_t)KN_P32[v]; break;
    case P_HAMMER: rh_ham = (int32_t)KN_HAM[v]; break;
    case P_DECAY: rh_k_decay = (int32_t)DECAY_K[v]; break;           /* k = 1/S, S = 2^((v-64)/32): higher = longer */
    case P_RELEASE: rh_k_rel = (int32_t)DECAY_K[v]; break;
    case P_NOISE: rh_thump = KN_THUMP[v]; rh_felt = KN_FELT[v]; break;
    case P_DL_TONE: fx_set(FXP_DL_TONE, v); break;
    default: break;                                                   /* SYNC: tempo.c; SPREAD: not yet in the engine */
    }
}
static void params_apply_all(void)
{
    uint32_t i;
    for (i = 0; i < P_COUNT; i++) param_apply(i);
    rh_tune_t = TUNE_T[set.tune + 50];
}

static void param_set(uint32_t id, int32_t v)
{
    if (id >= P_COUNT) return;
    if (v < 0) v = 0;
    if (v > PARAMS[id].hi) v = PARAMS[id].hi;
    if (set.panel[id] == (uint8_t)v) return;
    set.panel[id] = (uint8_t)v;
    param_apply(id);
    set_dirty = 1;
    set_t_change = fm1_ms;
}

/* ---- value text (<= 5 chars) */
static void fmt_r10(char *s, int32_t v)           /* reface 0.0 .. 10.0 */
{
    int32_t t = (v * 100 + 63) / 127;
    if (t >= 100) { s[0] = '1'; s[1] = '0'; s[2] = '.'; s[3] = '0'; s[4] = 0; return; }
    s[0] = (char)('0' + t / 10); s[1] = '.'; s[2] = (char)('0' + t % 10); s[3] = 0;
}
static void p_fmt_int(char *s, int32_t v)
{
    char t[8];
    int32_t i = 0, n = 0;
    if (v < 0) { s[n++] = '-'; v = -v; }
    do { t[i++] = (char)('0' + v % 10); v /= 10; } while (v && i < 7);
    while (i) s[n++] = t[--i];
    s[n] = 0;
}
static void param_text(uint32_t id, char *s)
{
    const pdesc_t *d = &PARAMS[id];
    int32_t v = set.panel[id];
    switch (d->fmt) {
    case F_ENUM:
    case F_DIV: {
        const char *p = d->names[v];
        uint32_t i = 0;
        while (p[i] && i < 5) { s[i] = p[i]; i++; }
        s[i] = 0;
        break;
    }
    case F_HZ: {                                   /* 0.5 * 24^(v/127) Hz in tenths, shown as "4.7" or "12" */
        int32_t t = (int32_t)(((uint64_t)FX_LFO_INC[v] * 441u) >> 32 >> 0);   /* inc * fs / 2^32 * 10 */
        t = (int32_t)(((uint64_t)FX_LFO_INC[v] * 441000u) >> 32);
        if (t >= 100) p_fmt_int(s, t / 10);
        else { s[0] = (char)('0' + t / 10); s[1] = '.'; s[2] = (char)('0' + t % 10); s[3] = 0; }
        break;
    }
    case F_MS: {
        int32_t ms = (int32_t)((uint32_t)FX_DLY_T[v] * 1000u / 44100u);
        if (ms < 1000) p_fmt_int(s, ms);
        else { s[0] = (char)('0' + ms / 1000); s[1] = '.'; s[2] = (char)('0' + (ms / 100) % 10); s[3] = 'S'; s[4] = 0; }
        break;
    }
    case F_BIP: {
        int32_t t = ((v - 64) * 50 + (v >= 64 ? 31 : -31)) / 63;  /* -5.0 .. +5.0 */
        char b[6];
        b[0] = t < 0 ? '-' : '+'; t = t < 0 ? -t : t;
        b[1] = (char)('0' + t / 10); b[2] = '.'; b[3] = (char)('0' + t % 10); b[4] = 0;
        for (t = 0; t < 5; t++) s[t] = b[t];
        break;
    }
    default: fmt_r10(s, v); break;
    }
}
