/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes parameters (IMPLEMENTATION-v3 §10.1, §10.5): one table drives the panel, the pages, the settings record,
 * presets and the MIDI CC map. Ids and enum values are APPEND-ONLY (stored records map by id).
 * Values are bytes 0..127 (bipolar ones are stored with +64: TONE 0..127 means -64..+63). */

enum { P_TYPE, P_DRIVE, P_TW_SW, P_TW_DEPTH, P_TW_RATE, P_TW_SYNC, P_TW_MODE, P_TW_SHAPE,
       P_CP_SW, P_CP_DEPTH, P_CP_SPEED, P_CP_SYNC, P_CP_FDBK,
       P_DL_SW, P_DL_DEPTH, P_DL_TIME, P_DL_SYNC, P_DL_FDBK, P_DL_TONE,
       P_RV_DEPTH, P_TONE, P_BARK, P_BELL, P_HAMMER, P_DECAY, P_RELEASE, P_NOISE, P_SPREAD,
       P_PH_SW, P_PH_DEPTH, P_PH_SPEED, P_PH_SYNC, P_PH_FDBK,                 /* 0.4: the phaser on its own */
       P_CMP_SW, P_CMP_AMT, P_CMP_ATK, P_CMP_REL,                             /* 0.4: the compressor */
       P_LEVEL,                                                               /* 0.8: the preset's volume trim */
       P_CMP_RATIO, P_CMP_GAIN,                                               /* 0.8: the compressor's 4 knobs */
       P_FL_TYPE, P_FL_FREQ, P_FL_RESO, P_FL_LFO,                             /* 0.9.3: the filter */
       P_LFO_SHAPE, P_LFO_RATE, P_LFO_SYNC, P_LFO_RETRIG,                     /*        its LFO */
       P_COUNT };
#define P_SLOTS 48u                /* room in the settings / preset records (P_COUNT <= P_SLOTS) */

enum { F_R10, F_ENUM, F_HZ, F_MS, F_DIV, F_BIP, F_DB, F_THR, F_GAIN, F_FHZ };
typedef struct {
    const char *lab;          /* <= 4 chars: panel / card label */
    uint8_t hi, def, fmt, cc;
    const char *const *names; /* F_ENUM */
} pdesc_t;

static const char *const N_TYPE[] = {"ST77", "SUIT", "FELT", "DYNO"};
static const char *const N_TYPE_LONG[] = {"STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO"};
static const char *const N_TW[] = {"OFF", "TRM", "WAH"};
static const char *const N_CP[] = {"OFF", "CHO", "PHA"};   /* "PHA": records before 0.4 */
static const char *const N_ONOFF[] = {"OFF", "ON"};
static const char *const N_RATIO[] = {"1.5:1", "2:1", "3:1", "4:1", "6:1", "8:1", "10:1", "20:1"};
static const char *const N_DL[] = {"OFF", "DIG", "ANA"};
static const char *const N_TWM[] = {"PAN", "AM"};
static const char *const N_FLT[] = {"OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH"};
static const char *const N_LFOS[] = {"SINE", "TRI", "SAW", "RAMP", "SQR", "S&H"};
static const char *const N_RETRIG[] = {"FREE", "KEY"};
static const char *const N_SYNC[] = {"FREE", "1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D", "1/2",
                                     "1/1", "2BAR", "4BAR"};

static const pdesc_t PARAMS[P_COUNT] = {
    [P_TYPE] = {"TYPE", 3, 0, F_ENUM, 80, N_TYPE},
    [P_DRIVE] = {"DRV", 127, 0, F_R10, 81, 0},
    [P_TW_SW] = {"TREM", 2, 0, F_ENUM, 17, N_TW},
    [P_TW_DEPTH] = {"DPTH", 127, 0, F_R10, 18, 0},
    [P_TW_RATE] = {"RATE", 127, 90, F_HZ, 19, 0},
    [P_TW_SYNC] = {"SYNC", 13, 0, F_DIV, 20, N_SYNC},
    [P_TW_MODE] = {"MODE", 1, 0, F_ENUM, 21, N_TWM},
    [P_TW_SHAPE] = {"SHPE", 127, 0, F_R10, 22, 0},
    [P_CP_SW] = {"CHO", 1, 0, F_ENUM, 85, N_ONOFF},
    [P_CP_DEPTH] = {"DPTH", 127, 0, F_R10, 86, 0},
    [P_CP_SPEED] = {"SPD", 127, 50, F_HZ, 87, 0},
    [P_CP_SYNC] = {"SYNC", 14, 0, F_DIV, 23, N_SYNC},
    [P_CP_FDBK] = {"FDBK", 127, 40, F_R10, 24, 0},
    [P_DL_SW] = {"DLY", 2, 0, F_ENUM, 88, N_DL},
    [P_DL_DEPTH] = {"DPTH", 127, 0, F_R10, 89, 0},
    [P_DL_TIME] = {"TIME", 127, 85, F_MS, 90, 0},
    [P_DL_SYNC] = {"SYNC", 13, 0, F_DIV, 25, N_SYNC},
    [P_DL_FDBK] = {"FDBK", 127, 90, F_R10, 26, 0},
    [P_DL_TONE] = {"TONE", 127, 70, F_R10, 27, 0},
    [P_RV_DEPTH] = {"REV", 127, 0, F_R10, 91, 0},
    [P_TONE] = {"TONE", 127, 64, F_BIP, 28, 0},
    [P_BARK] = {"BARK", 127, 64, F_R10, 29, 0},
    [P_BELL] = {"BELL", 127, 64, F_R10, 30, 0},
    [P_HAMMER] = {"HAMR", 127, 64, F_R10, 31, 0},
    [P_DECAY] = {"DCAY", 127, 64, F_R10, 102, 0},
    [P_RELEASE] = {"REL", 127, 64, F_R10, 103, 0},
    [P_NOISE] = {"NOIS", 127, 48, F_R10, 104, 0},
    [P_SPREAD] = {"SPRD", 127, 0, F_R10, 105, 0},
    [P_PH_SW] = {"PHA", 1, 0, F_ENUM, 12, N_ONOFF},
    [P_PH_DEPTH] = {"DPTH", 127, 0, F_R10, 13, 0},
    [P_PH_SPEED] = {"SPD", 127, 40, F_HZ, 14, 0},
    [P_PH_SYNC] = {"SYNC", 14, 0, F_DIV, 15, N_SYNC},
    [P_PH_FDBK] = {"FDBK", 127, 50, F_R10, 16, 0},
    [P_CMP_SW] = {"COMP", 1, 0, F_ENUM, 106, N_ONOFF},
    [P_CMP_AMT] = {"THR", 127, 0, F_THR, 107, 0},            /* threshold 0 .. -40 dBFS (0.8; was AMOUNT) */
    [P_CMP_ATK] = {"ATK", 127, 40, F_R10, 108, 0},
    [P_CMP_REL] = {"REL", 127, 50, F_R10, 109, 0},
    [P_LEVEL] = {"LVL", 127, 64, F_DB, 110, 0},              /* 64 = 0 dB, 0.1875 dB a step: -12 .. +11.8 dB */
    [P_CMP_RATIO] = {"RAT", 7, 3, F_ENUM, 112, N_RATIO},     /* 4:1 */
    [P_CMP_GAIN] = {"MKUP", 127, 0, F_GAIN, 113, 0},         /* make-up 0 .. +24 dB */
    [P_FL_TYPE] = {"FILT", 6, 0, F_ENUM, 70, N_FLT},         /* 0.9.3: CC 70 type, 74 cutoff, 71 resonance, 72 LFO */
    [P_FL_FREQ] = {"FREQ", 127, 127, F_FHZ, 74, 0},
    [P_FL_RESO] = {"RESO", 127, 0, F_R10, 71, 0},
    [P_FL_LFO] = {"LFO", 127, 0, F_R10, 72, 0},
    [P_LFO_SHAPE] = {"SHPE", 5, 0, F_ENUM, 75, N_LFOS},      /*        CC 75 shape, 76 rate, 77 sync, 78 retrig */
    [P_LFO_RATE] = {"RATE", 127, 40, F_HZ, 76, 0},
    [P_LFO_SYNC] = {"SYNC", 14, 0, F_DIV, 77, N_SYNC},
    [P_LFO_RETRIG] = {"TRIG", 1, 0, F_ENUM, 78, N_RETRIG},
};
_Static_assert(P_COUNT <= P_SLOTS, "records hold P_SLOTS parameters");

/* ---- the settings record: always 256 B, versioned, append-only (§10.5) */
#define SET_VER 2u                 /* 2 (0.4): panel / undo hold P_SLOTS; version 1 records are converted */
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
    uint8_t panel[32], undo[32], undo_valid;   /* version 1: ids 0..31 */
    uint8_t panel2[16], undo2[16];             /* version 2: ids 32..47 (byte 89 on: the old reserved area) */
    uint8_t perf[4];                           /* 0.9: the PERFORM knobs, ui.c PERF_T index + 1 (0 = the default) */
    uint8_t arp[6];                            /* 0.9: the arpeggiator: mode, rate, octaves, gate, last mode, hold */
    uint8_t usb_fixed;                         /* 0.9: 1 = the USB audio does not follow the MASTER knob */
    uint8_t chord[4];                          /* 0.9.1: chord mode: type, inversion, voicing, strum (notes.c) */
    uint8_t seq_bars, seq_quant, seq_swing;    /* 0.9.1: the sequencer (seq.c; the pattern itself is in flash) */
    uint8_t trs_off;                           /* 0.9.1: 1 = the TRS MIDI input is ignored (MIDI page) */
    uint8_t seq_rsv[94];
    uint8_t reserved[22];
} settings_t;
_Static_assert(sizeof(settings_t) == 256, "settings record is 256 B");

static settings_t set;
#define PV(id) (*((id) < 32u ? &set.panel[(id)] : &set.panel2[(id) - 32u]))   /* a parameter's value */
#define UV(id) (*((id) < 32u ? &set.undo[(id)] : &set.undo2[(id) - 32u]))

/* records before 0.4 had the phaser as CHO/PHA = 2, sharing the chorus' depth, speed, sync and feedback */
static void params_split_phaser(uint8_t *p, uint32_t np)
{
    if (np > P_CP_FDBK && p[P_CP_SW] == 2u) {
        p[P_CP_SW] = 0;
        if (np > P_PH_FDBK) {
            p[P_PH_SW] = 1; p[P_PH_DEPTH] = p[P_CP_DEPTH]; p[P_PH_SPEED] = p[P_CP_SPEED];
            p[P_PH_SYNC] = p[P_CP_SYNC]; p[P_PH_FDBK] = p[P_CP_FDBK];
        }
    }
}
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
    for (i = 0; i < P_COUNT; i++) PV(i) = PARAMS[i].def;
}

static void params_sanitize(void)
{
    uint32_t i;
    if (set.ver == 1u) {                                 /* 0.1 .. 0.3: new ids get their defaults */
        uint8_t p[P_SLOTS];
        for (i = 0; i < P_SLOTS; i++) p[i] = i < 32u ? set.panel[i] : i < P_COUNT ? PARAMS[i].def : 0;
        for (i = 0; i < 16u; i++) set.panel2[i] = set.undo2[i] = 0;
        for (i = 0; i < sizeof set.reserved; i++) set.reserved[i] = 0;
        for (i = 0; i < 4u; i++) set.perf[i] = 0;
        params_split_phaser(p, P_SLOTS);
        for (i = 0; i < P_SLOTS; i++) PV(i) = p[i];
        set.undo_valid = 0;
        set.ver = SET_VER;
    }
    if (set.ver != SET_VER) { params_defaults(); return; }
    if (!(set.flags & 1u)) { PV(P_LEVEL) = PARAMS[P_LEVEL].def; set.flags |= 1u; }   /* records before 0.8 */
    if (!(set.flags & 2u)) { PV(P_CMP_RATIO) = PARAMS[P_CMP_RATIO].def; PV(P_CMP_GAIN) = 0; set.flags |= 2u; }
    if (!(set.flags & 8u)) { for (i = P_FL_TYPE; i <= P_LFO_RETRIG; i++) PV(i) = PARAMS[i].def; set.flags |= 8u; }   /* 0.9.3 */
    for (i = 0; i < P_COUNT; i++) if (PV(i) > PARAMS[i].hi) PV(i) = PARAMS[i].def;
    if (set.oct < -2 || set.oct > 2) set.oct = 0;
    if (set.bpm10 < 400 || set.bpm10 > 2400) set.bpm10 = 1200;
    if (set.midi_ch > 16u) set.midi_ch = 0;                 /* 0 = all channels, 1..16 */
    if (set.trs_off > 1u) set.trs_off = 0;
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
    int32_t v = PV(id);
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
    case P_PH_SW: fx_set(FXP_PH_SW, v); break;
    case P_PH_DEPTH: fx_set(FXP_PH_DEPTH, v); break;
    case P_PH_SPEED: fx_set(FXP_PH_SPEED, v); break;
    case P_PH_FDBK: fx_set(FXP_PH_FDBK, v); break;
    case P_CMP_SW: fx_set(FXP_CMP_SW, v); break;
    case P_CMP_AMT: fx_set(FXP_CMP_AMT, v); break;
    case P_CMP_ATK: fx_set(FXP_CMP_ATK, v); break;
    case P_CMP_REL: fx_set(FXP_CMP_REL, v); break;
    case P_LEVEL: fx_set(FXP_LEVEL, v); break;
    case P_CMP_RATIO: fx_set(FXP_CMP_RATIO, v); break;
    case P_CMP_GAIN: fx_set(FXP_CMP_GAIN, v); break;
    case P_FL_TYPE: fx_set(FXP_FL_TYPE, v); break;
    case P_FL_FREQ: fx_set(FXP_FL_FREQ, v); break;
    case P_FL_RESO: fx_set(FXP_FL_RESO, v); break;
    case P_FL_LFO: fx_set(FXP_FL_LFO, v); break;
    case P_LFO_SHAPE: fx_set(FXP_LFO_SHAPE, v); break;
    case P_LFO_RATE: fx_set(FXP_LFO_RATE, v); break;
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
    if (PV(id) == (uint8_t)v) return;
    PV(id) = (uint8_t)v;
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
    int32_t v = PV(id);
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
    case F_FHZ: {                                  /* the filter's cutoff: "850" Hz / "4.2K" */
        int32_t hz = FX_FL_HZ[v];
        if (hz < 1000) p_fmt_int(s, hz);
        else if (hz < 10000) { s[0] = (char)('0' + hz / 1000); s[1] = '.'; s[2] = (char)('0' + (hz / 100) % 10); s[3] = 'K'; s[4] = 0; }
        else { p_fmt_int(s, hz / 1000); s[2] = 'K'; s[3] = 0; }
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
    case F_THR:                                    /* "-20" dB */
        if (v) { s[0] = '-'; p_fmt_int(s + 1, (v * 40 + 63) / 127); } else { s[0] = '0'; s[1] = 0; }
        break;
    case F_GAIN: {                                 /* "+6.0" dB */
        int32_t t = (v * 240 + 63) / 127, n = 0;
        s[n++] = '+';
        if (t >= 100) { p_fmt_int(s + n, t / 10); break; }
        s[n++] = (char)('0' + t / 10); s[n++] = '.'; s[n++] = (char)('0' + t % 10); s[n] = 0;
        break;
    }
    case F_DB: {                                   /* "+1.5" / "-12" dB */
        int32_t t = (v - 64) * 15 / 8, a = t < 0 ? -t : t, n = 0;  /* tenths of a dB */
        s[n++] = t < 0 ? '-' : '+';
        if (a >= 100) { p_fmt_int(s + n, a / 10); break; }
        s[n++] = (char)('0' + a / 10); s[n++] = '.'; s[n++] = (char)('0' + a % 10); s[n] = 0;
        break;
    }
    default: fmt_r10(s, v); break;
    }
}
