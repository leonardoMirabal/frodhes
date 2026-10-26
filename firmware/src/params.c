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
       P_DRV_TONE, P_DRV_MIX, P_DRV_SW, P_RV_DECAY, P_RV_TONE, P_RV_PREDLY,   /* records v3: the drive's and the
                                                                               * reverb's new controls (fx.c) */
       P_MOT_ON, P_MOT_DEPTH, P_MOT_RATE, P_MOT_SHAPE, P_MOT_SYNC,            /* records v3: the vibraphone's motor
                                                                               * (the bar engine; the MOTOR page) */
       P_RV_TYPE, P_RV_SIZE, P_RV_MOD, P_RV_LOCUT,                           /* 0.9.5: the reverb's TYPE and REVERB 2
                                                                               * (rev_types.c); in the records' spare
                                                                               * slots (np: older records load them at
                                                                               * their defaults: HALL, today's sound) */
       P_COUNT };
#define P_SLOTS 64u                /* room in the settings / preset records (P_COUNT <= P_SLOTS); 48 before the v3
                                    * records (presets.c preset_t 96 B, the settings' panel3 / undo3) */

enum { F_R10, F_ENUM, F_HZ, F_MS, F_DIV, F_BIP, F_DB, F_THR, F_GAIN, F_FHZ, F_HZH, F_PDLY, F_RSIZE, F_LCUT };   /* F_RSIZE:
                                                                       * 0 AUTO, F_LCUT: OFF / Hz (the reverb's) */   /* F_HZH: half of F_HZ,
                                                                                         * F_PDLY: the pre-delay, ms */
typedef struct {
    const char *lab;          /* <= 4 chars: panel / card label */
    uint8_t hi, def, fmt, cc;
    const char *const *names; /* F_ENUM */
} pdesc_t;

static const char *const N_TYPE[] = {"ST77", "SUIT", "FELT", "DYNO", "VIBE", "MRMB"};
static const char *const N_TYPE_LONG[] = {"STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO", "VIBES", "MARIMBA"};
/* TYPE -> engine (0 Rhodes, 1 bars), the engine's own type index, and the drive row (fx.c, 0..3). Append-only, in
 * step with N_TYPE. The Rhodes types 0..3 are unchanged; VIBES (4) is the first bar type, MARIMBA (5) the second
 * (bars.c BAR_TYPE[1]). TYPE_FLAGS: what the instrument has, as the bar type record's flags (bars.h bar_type_t):
 * bit0 a motor (the MOTOR controls and their auto-on), bit1 a damper (key-up damps; without one DECAY's second card
 * is MUTE, the player's hand: 0 = rings out). */
#define N_TYPE_N 6u
static const uint8_t TYPE_ENGINE[N_TYPE_N] = {0, 0, 0, 0, 1, 1};
static const uint8_t TYPE_SUB[N_TYPE_N] = {0, 1, 2, 3, 0, 1};
static const uint8_t TYPE_DRIVE_ROW[N_TYPE_N] = {0, 1, 2, 3, 0, 0};
static const uint8_t TYPE_FLAGS[N_TYPE_N] = {0, 0, 0, 0, 3, 0};
static const uint8_t TYPE_CC79[N_TYPE_N] = {0, 0, 0, 0, 0, 96};   /* the bar types' CC 79 band starts, rising in TYPE
                                    * order (frodhes.c midi_cc): VIBES 0..95, MARIMBA 96..127 (the Rhodes: CC 80) */
_Static_assert(sizeof N_TYPE / sizeof N_TYPE[0] == N_TYPE_N && sizeof N_TYPE_LONG / sizeof N_TYPE_LONG[0] == N_TYPE_N,
               "one name per TYPE");
#define TYPE_IS_BARS(t) ((uint32_t)(t) < N_TYPE_N && TYPE_ENGINE[(t)] != 0u)   /* the main loop's TYPE (the UI) */
#define TYPE_HAS_MOTOR(t) ((uint32_t)(t) < N_TYPE_N && (TYPE_FLAGS[(t)] & 1u) != 0u)   /* VIBES */
#define TYPE_NO_DAMPER(t) (TYPE_IS_BARS(t) && !(TYPE_FLAGS[(t)] & 2u))   /* MARIMBA: DECAY's second card is MUTE */
/* the engine queries (engine.c, lane C, after the merge: it defines ENG_HAS_BARS, the BK_* knob ids and these
 * functions). Until then a stub: a bar TYPE plays as STAGE 77 and MOTOR as the tremolo's AM, so the tree builds and
 * plays; the UI already shows the bar cards (it asks TYPE_IS_BARS of the main loop's TYPE, not the engine) */
#ifndef ENG_HAS_BARS
enum { BK_STRIKE, BK_MALLET, BK_TUBE, BK_WEIGHT, BK_RING, BK_DAMPER, BK_NOISE,      /* the VOICE / DECAY cards, 0..127 */
       BK_MOT_ON, BK_MOT_DEPTH, BK_MOT_SHAPE, BK_MOT_INC };                          /* the motor (INC: a Q32 step) */
static void eng_set_type(uint32_t t) { rh_type = (uint8_t)(t < N_TYPE_N && TYPE_ENGINE[t] ? 0u : t); }
static int eng_is_bars(void) { return 0; }       /* the engine plays bars for the TYPE last set (not the ISR latch) */
static void eng_bar_knob(uint32_t k, uint32_t v) { (void)k; (void)v; }
#endif
#define PR_NF_SET 19u              /* presets.c PR_NF: the factory presets (12 before VIBES, 14 before MARIMBA, 16
                                    * before F17..F19, 0.9.5) */
static const char *const N_TW[] = {"OFF", "TRM", "WAH"};
static const char *const N_CP[] = {"OFF", "CHO", "PHA"};   /* "PHA": records before 0.4 */
static const char *const N_ONOFF[] = {"OFF", "ON"};
static const char *const N_DRV[] = {"OFF", "FDRV", "KHLON", "TSH8", "OHCD", "BHLUE"};   /* the drive's TYPE (fx.c
                                    * drive.c): 1 FDRV, the drive before ("ON" in older records), 2.. the circuits */
static const uint8_t DRV_CC84[6] = {0, 112, 64, 76, 88, 100};   /* CC 84's band start per drive TYPE (as TYPE_CC79):
                                    * OFF 0..63 and FDRV 112..127 (0 and 127 keep their sound), the others in between */
static uint8_t drv_last = 1;       /* the drive TYPE last on: turning DRIVE up on PERFORM / FRODHES wakes it (not saved) */
static int32_t drv_cc84(uint32_t v)
{
    int32_t t, x = 0;
    for (t = 1; t < 6; t++) if (v >= DRV_CC84[t] && DRV_CC84[t] >= DRV_CC84[x]) x = t;
    return x;
}
static const char *const N_RV[] = {"OFF", "HALL", "PLATE", "INF"};   /* the reverb's TYPE (rev_types.c RV_*) */
static const char *const N_RV_LONG[] = {"OFF", "HALL", "PLATE", "INFINITE"};
static uint8_t rv_last = 1;        /* the reverb TYPE last on: what a quick-assign or a button turns back on (not saved) */
static const char *const N_RATIO[] = {"1.5:1", "2:1", "3:1", "4:1", "6:1", "8:1", "10:1", "20:1"};
static const char *const N_DL[] = {"OFF", "DIG", "ANA", "TAPE"};   /* TAPE: 0.9.5 */
static const char *const N_TWM[] = {"PAN", "AM"};   /* (MOTOR was a third mode until records v3: P_MOT_*) */
static const char *const N_FLT[] = {"OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH"};
static const char *const N_LFOS[] = {"SINE", "TRI", "SAW", "RAMP", "SQR", "S&H"};
static const char *const N_RETRIG[] = {"FREE", "KEY"};
static const char *const N_SYNC[] = {"FREE", "1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D", "1/2",
                                     "1/1", "2BAR", "4BAR"};

static const pdesc_t PARAMS[P_COUNT] = {
    [P_TYPE] = {"TYPE", 5, 0, F_ENUM, 80, N_TYPE},
    [P_DRIVE] = {"DRV", 127, 0, F_R10, 81, 0},
    [P_TW_SW] = {"TREM", 2, 0, F_ENUM, 17, N_TW},
    [P_TW_DEPTH] = {"DPTH", 127, 0, F_R10, 18, 0},
    [P_TW_RATE] = {"RATE", 127, 90, F_HZ, 19, 0},
    [P_TW_SYNC] = {"SYNC", 13, 0, F_DIV, 20, N_SYNC},
    [P_TW_MODE] = {"MODE", 1, 0, F_ENUM, 21, N_TWM},
    [P_TW_SHAPE] = {"SHPE", 127, 0, F_R10, 22, 0},
    [P_CP_SW] = {"CHO", 1, 0, F_ENUM, 85, N_ONOFF},
    [P_CP_DEPTH] = {"DPTH", 127, 0, F_R10, 86, 0},
    [P_CP_SPEED] = {"SPD", 127, 50, F_HZH, 87, 0},
    [P_CP_SYNC] = {"SYNC", 14, 0, F_DIV, 23, N_SYNC},
    [P_CP_FDBK] = {"FDBK", 127, 40, F_R10, 24, 0},
    [P_DL_SW] = {"DLY", 3, 0, F_ENUM, 88, N_DL},
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
    [P_PH_SPEED] = {"SPD", 127, 40, F_HZH, 14, 0},
    [P_PH_SYNC] = {"SYNC", 14, 0, F_DIV, 15, N_SYNC},
    [P_PH_FDBK] = {"FDBK", 127, 50, F_R10, 16, 0},
    [P_CMP_SW] = {"COMP", 1, 0, F_ENUM, 106, N_ONOFF},
    [P_CMP_AMT] = {"THR", 127, 0, F_THR, 107, 0},            /* threshold 0 .. -40 dBFS (0.8; was AMOUNT) */
    [P_CMP_ATK] = {"ATK", 127, 40, F_R10, 108, 0},
    [P_CMP_REL] = {"REL", 127, 50, F_R10, 109, 0},
    [P_LEVEL] = {"LVL", 127, 64, F_DB, 110, 0},              /* 64 = 0 dB, 0.1875 dB a step: -12 .. +11.8 dB */
    [P_CMP_RATIO] = {"RAT", 7, 3, F_ENUM, 112, N_RATIO},     /* 4:1 */
    [P_CMP_GAIN] = {"MKUP", 127, 0, F_GAIN, 113, 0},         /* make-up 0 .. +24 dB */
    [P_FL_TYPE] = {"FILT", 6, 2, F_ENUM, 70, N_FLT},         /* 0.9.3: CC 70 type, 74 cutoff, 71 resonance, 72 LFO */
    [P_FL_FREQ] = {"FREQ", 127, 127, F_FHZ, 74, 0},
    [P_FL_RESO] = {"RESO", 127, 0, F_R10, 71, 0},
    [P_FL_LFO] = {"LFO", 127, 0, F_R10, 72, 0},
    [P_LFO_SHAPE] = {"SHPE", 5, 0, F_ENUM, 75, N_LFOS},      /*        CC 75 shape, 76 rate, 77 sync, 78 retrig */
    [P_LFO_RATE] = {"RATE", 127, 40, F_HZ, 76, 0},
    [P_LFO_SYNC] = {"SYNC", 14, 0, F_DIV, 77, N_SYNC},
    [P_LFO_RETRIG] = {"TRIG", 1, 0, F_ENUM, 78, N_RETRIG},
    [P_DRV_TONE] = {"DTON", 127, 64, F_BIP, 82, 0},          /* 0.9.3: the DRIVE page: tone dark .. bright (64 = flat) */
    [P_DRV_MIX] = {"DMIX", 127, 127, F_R10, 83, 0},          /*        dry / wet (127 = all driven, as before) */
    [P_DRV_SW] = {"DRV", 5, 1, F_ENUM, 84, N_DRV},           /*        OFF / the TYPE (on: DRIVE 0 is still off) */
    [P_RV_DECAY] = {"RDCY", 127, 64, F_R10, 92, 0},          /*        the REVERB page: the hall's length (64 = as before) */
    [P_RV_TONE] = {"RTON", 127, 64, F_BIP, 93, 0},           /*        its high damping, dark .. bright (64 = as before) */
    [P_RV_PREDLY] = {"PDLY", 127, 0, F_PDLY, 94, 0},         /*        pre-delay 0 .. 80 ms */
    [P_MOT_ON] = {"MOT", 1, 0, F_ENUM, 47, N_ONOFF},         /* on by itself when the TYPE turns to a bar type */
    [P_MOT_DEPTH] = {"DPTH", 127, 110, F_R10, 48, 0},        /* 110: a clear ~8 dB pulse over F3..F6 (host) */
    [P_MOT_RATE] = {"RATE", 127, 78, F_HZ, 49, 0},           /* 0.5 .. 10.1 Hz (120 and up: 10.1); 78 = 3.5 Hz */
    [P_MOT_SHAPE] = {"SHPE", 127, 0, F_R10, 50, 0},
    [P_MOT_SYNC] = {"SYNC", 13, 0, F_DIV, 51, N_SYNC},
    [P_RV_TYPE] = {"RTYP", 3, 1, F_ENUM, 95, N_RV},          /* 0.9.5: OFF / HALL / PLATE / INFINITE (CC 95 in 4 bands) */
    [P_RV_SIZE] = {"SIZE", 127, 0, F_RSIZE, 73, 0},          /*        0 = AUTO (from MIX, as before) */
    [P_RV_MOD] = {"MOD", 127, 64, F_R10, 68, 0},             /*        64 = HALL's modulation as before */
    [P_RV_LOCUT] = {"LCUT", 127, 0, F_LCUT, 69, 0},          /*        0 = off (as before), 20 .. 800 Hz */
};
_Static_assert(P_COUNT <= P_SLOTS, "records hold P_SLOTS parameters");
_Static_assert(P_SLOTS <= 64u, "ui.c card slots: a parameter id is under 64 (S_KVEL)");

/* ---- the settings record: always 256 B, versioned, append-only (§10.5) */
#define SET_VER 2u                 /* 2 (0.4): panel / undo hold P_SLOTS; version 1 records are converted */
typedef struct {
    uint8_t ver, flags;            /* flags: 1 LEVEL, 2 comp, 4 + 32 the preset index layout (VIBES, MARIMBA), 8 filter, 16 v3,
                                    * 64 the PERFORM defaults DRIVE FILTER DELAY REVERB (perf: below), 128 the reverb's
                                    * TYPE and REVERB 2 (P_RV_TYPE .. P_RV_LOCUT, 0.9.5) */
    int8_t oct;
    uint8_t preset;
    uint16_t bpm10;
    uint8_t clock, midi_ch, key_vel, vel_curve;   /* vel_curve: TOUCH (keyvel.h; 0 = MEDIUM) */
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
    uint8_t undo_preset;                       /* 0.9.3: the preset the undo copy is (index + 1; 0 = not known) */
    uint8_t panel3[16], undo3[16];             /* records v3: ids 48..63 (flags bit 16: present) */
    uint8_t mot_user_off;                      /* records v3: 1 = switched off on the MOTOR page (no auto-on) */
    uint8_t chord_last;                        /* 0.9.3: the chord type EDIT turns back on (0 = none yet: MAJ) */
    uint8_t palette;                           /* 0.9.4: DISPLAY COLOR, the step (ui.c COLOR_ACC; 0 = AMBER) */
    uint8_t dim;                               /* 0.9.4: DISPLAY BRIGHTNESS, steps down from full: 0 (as made) .. 9; the
                                                * screen and every light. LEDS is in Dinghy's `leds` above (0 in every
                                                * record so far = ROOTS; 1 OFF 2 PLAYED 3 ALL 4 SCALE) */
    uint8_t seq_rsv[57];
    uint8_t flags2;                            /* 0.9.5: 1 = the preset index in the 19-factory layout (U01 = 19) */
#if BOARD_SMK37
    uint8_t smk_mst[2];                        /* 0.9.6, SMK-37: K1's MASTER + 1, little endian (0, every record
                                                * before: smk_input.h's 724; frodhes_smk.h smk_master_stored) */
    uint8_t reserved[19];
#else
    uint8_t reserved[21];
#endif
} settings_t;
_Static_assert(sizeof(settings_t) == 256, "settings record is 256 B");

static settings_t set;
static uint8_t set_pr_from_rc;    /* params_sanitize converted an index of 16 factory presets that named U10..U12 */
/* TOUCH (keyvel.h): stored so that 0, every record before key velocity, is MEDIUM */
static uint32_t kv_touch(void) { return (set.vel_curve + 1u) & 3u; }
static void kv_touch_store(uint32_t touch) { set.vel_curve = (uint8_t)((touch + 3u) & 3u); }
/* a parameter's value (PV) and its undo copy (UV); the masks keep the branches not taken in range (constant ids) */
#define PV(id) (*((id) < 32u ? &set.panel[(id) & 31u] : (id) < 48u ? &set.panel2[((id) - 32u) & 15u] : &set.panel3[((id) - 48u) & 15u]))
#define UV(id) (*((id) < 32u ? &set.undo[(id) & 31u] : (id) < 48u ? &set.undo2[((id) - 32u) & 15u] : &set.undo3[((id) - 48u) & 15u]))

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
/* a record from before v3 (ids 0..47; 48.. already at their defaults): what the v3 ids take over (presets.c converts
 * old user presets with it too) */
static void params_v3_convert(uint8_t *p)
{
    if (p[P_TW_SW] == 1u && p[P_TW_MODE] == 2u) {    /* TREM/WAH = MOTOR (VIBES, 0.9.3 drafts) */
        if (TYPE_IS_BARS(p[P_TYPE])) {               /* a bar TYPE: the motor's own parameters, the effect off */
            p[P_MOT_ON] = 1;
            p[P_MOT_DEPTH] = p[P_TW_DEPTH];
            p[P_MOT_RATE] = p[P_TW_RATE] > 120u ? 120u : p[P_TW_RATE];
            p[P_MOT_SHAPE] = p[P_TW_SHAPE];
            p[P_MOT_SYNC] = p[P_TW_SYNC];
            p[P_TW_SW] = 0;
        }
        p[P_TW_MODE] = 1;                            /* a Rhodes TYPE played MOTOR as the AM tremolo: it still does */
    }
}
/* the PERFORM knobs' defaults, as ui.c PERF_T indexes (perf[k] = 0 plays these): DRIVE, FILTER, DELAY, REVERB, the
 * chain's order (after 0.9.4); up to 0.9.4 TREM/WAH, PHASER, FILTER, DELAY. A record without flags bit 64 (0.9.4 and
 * before) is moved once: an assignment that plays the old defaults on all four knobs (never changed, or set back to
 * them) gets the new ones; any other is written out as it plays, so a changed one stays exactly as it was */
#define PERF_DEF 5, 27, 3, 4
#define PERF_DEF_OLD 0, 1, 27, 3
static uint32_t perf_count(void);                    /* ui.c: PERF_N */
static void params_perf_update(void)
{
    static const uint8_t OLD[4] = {PERF_DEF_OLD};
    uint8_t r[4];
    uint32_t i, same = 1;
    for (i = 0; i < 4u; i++) {
        r[i] = set.perf[i] && set.perf[i] <= perf_count() ? (uint8_t)(set.perf[i] - 1u) : OLD[i];
        if (r[i] != OLD[i]) same = 0;
    }
    for (i = 0; i < 4u; i++) set.perf[i] = same ? 0u : (uint8_t)(r[i] + 1u);
    set.flags |= 64u;
}
static uint8_t set_dirty;          /* something in `set` may have changed: frodhes.c settings_poll looks (and decides) */
static uint32_t set_t_change;

/* ---- auto save keeps the system setup only (0.9.5; the user, 2026-10-25: "if I'm performing I always want to get
 * back to the original state"). The record written is settings_record()'s: `set` as it is (the MIDI page, CLOCK,
 * KEY VEL, TOUCH, TUNE, USB VOL, the DISPLAY page, ASSIGN, the octave / scale / chord / arpeggiator / sequencer
 * settings, the preset number), but the sound in it is the one the last preset LOADED or SAVED left (snd_base, not
 * the knobs turned since) and the tempo the one last set by hand (TAP, the TEMPO page's BPM), never the MIDI
 * clock's. So a knob, a CC or the clock changes nothing in the record and writes nothing; the boot plays the preset
 * again as it is stored (frodhes.c app_main), the record's sound only when that preset is not there any more */
static uint8_t snd_base[P_SLOTS];
static uint16_t bpm_hand = 1200;
#if BOARD_SMK37
/* the SMK's mod wheel (frodhes_smk.h smk_wheels; 0.9.6, #29: it wrote DEPTH, so its rest was DEPTH 0 and it lost the
 * preset's): 0..127 on top of TREM/WAH's DEPTH and the motor's DEPTH, up to 127 at the top; at rest the DEPTH as set.
 * What it switched on to get there (smk_mw_woke: P_TW_SW or P_MOT_ON, to smk_mw_wv) it switches off at rest; a sound
 * loaded or saved meanwhile (snd_base_take) keeps its own */
static int32_t smk_mw, smk_mw_wv;
static uint32_t smk_mw_woke;
static uint8_t smk_mw_uoff;                     /* set.mot_user_off before the wheel switched the motor on */
#define MW_ADD(v) ((v) + (smk_mw * (127 - (v)) + 63) / 127)
#define MW_FORGET() (smk_mw_woke = 0u)
#else
#define MW_ADD(v) (v)
#define MW_FORGET() ((void)0)
#endif
static void snd_base_take(void) { uint32_t i; for (i = 0; i < P_SLOTS; i++) snd_base[i] = PV(i); MW_FORGET(); }
static void settings_record(settings_t *r)
{
    uint32_t i;
    *r = set;
    for (i = 0; i < 32u; i++) r->panel[i] = snd_base[i];
    for (i = 0; i < 16u; i++) { r->panel2[i] = snd_base[32u + i]; r->panel3[i] = snd_base[48u + i]; }
    r->bpm10 = bpm_hand;
}

static void params_defaults(void)
{
    uint32_t i;
    for (i = 0; i < sizeof set; i++) ((uint8_t *)&set)[i] = 0;
    set.ver = SET_VER;
    set.flags = 4u | 32u | 64u | 128u;   /* a fresh record: its preset index is already in today's layout (19 factory:
                                    * flags2 1), its PERFORM knobs today's defaults */
    set.flags2 = 1u;
    set.preset = 0;
    set.bpm10 = 1200;
    set.clock = 1;                 /* EXT: follows MIDI clock when it comes (USB or TRS) */
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
#if BOARD_SMK37
        set.smk_mst[0] = set.smk_mst[1] = 0;
#endif
        set.flags2 = 0;
        for (i = 0; i < 4u; i++) set.perf[i] = 0;
        params_split_phaser(p, P_SLOTS);
        for (i = 0; i < P_SLOTS; i++) PV(i) = p[i];
        set.undo_valid = 0; set.undo_preset = 0;
        set.flags &= (uint8_t)~16u;                  /* ids 48.. come from the defaults below */
        set.ver = SET_VER;
    }
    if (set.ver != SET_VER) { params_defaults(); return; }
    if (!(set.flags & 1u)) { PV(P_LEVEL) = PARAMS[P_LEVEL].def; set.flags |= 1u; }   /* records before 0.8 */
    if (!(set.flags & 2u)) { PV(P_CMP_RATIO) = PARAMS[P_CMP_RATIO].def; PV(P_CMP_GAIN) = 0; set.flags |= 2u; }
    if (!(set.flags & 8u)) { for (i = P_FL_TYPE; i <= P_LFO_RETRIG; i++) PV(i) = PARAMS[i].def; set.flags |= 8u; }   /* 0.9.3 */
    if (!(set.flags & 16u)) {                        /* records before v3: ids 48.. get their defaults */
        uint8_t p[P_SLOTS];
        for (i = 48u; i < P_SLOTS; i++) PV(i) = UV(i) = i < P_COUNT ? PARAMS[i].def : 0u;
        for (i = 0; i < P_SLOTS; i++) p[i] = PV(i);
        params_v3_convert(p);
        for (i = 0; i < P_SLOTS; i++) PV(i) = p[i];
        for (i = 0; i < P_SLOTS; i++) p[i] = UV(i);
        params_v3_convert(p);
        for (i = 0; i < P_SLOTS; i++) UV(i) = p[i];
        set.mot_user_off = 0;
        set.flags |= 16u;
    }
    if (!(set.flags & 4u)) {                         /* records before VIBES had 12 factory presets: U01 was 12 */
        if (set.preset >= 12u && set.preset < 12u + 16u) set.preset = (uint8_t)(set.preset + 16u - 12u);
        set.flags |= 4u | 32u;                       /* (straight into 0.9.4's layout: 16 factory; then flags2) */
    }
    if (!(set.flags & 32u)) {                        /* records before MARIMBA (0.9.3) had 14 factory presets: U01
                                                      * was 14; undo_preset is index + 1 */
        if (set.preset >= 14u && set.preset < 14u + 16u) set.preset = (uint8_t)(set.preset + 16u - 14u);
        if (set.undo_preset >= 15u && set.undo_preset < 15u + 16u) set.undo_preset = (uint8_t)(set.undo_preset + 16u - 14u);
        set.flags |= 32u;
    }
    if (!(set.flags2 & 1u)) {                        /* records up to 0.9.5's RC had 16 factory presets: U01 was 16
                                                      * (F17..F19 come before the user bank now); U10..U12 that were
                                                      * the starters: presets_load_bank (set_pr_from_rc) */
        set_pr_from_rc = (uint8_t)((set.preset >= 16u + 9u && set.preset < 16u + 12u) ||
                                   (set.undo_preset >= 17u + 9u && set.undo_preset < 17u + 12u));
        if (set.preset >= 16u && set.preset < 16u + 16u) set.preset = (uint8_t)(set.preset + PR_NF_SET - 16u);
        if (set.undo_preset >= 17u && set.undo_preset < 17u + 16u) set.undo_preset = (uint8_t)(set.undo_preset + PR_NF_SET - 16u);
        set.flags2 |= 1u;
    }
    if (!(set.flags & 64u)) params_perf_update();     /* records up to 0.9.4: the PERFORM defaults (above) */
    if (!(set.flags & 128u)) {                        /* records before the reverb's TYPE: its ids at their defaults
                                                      * (HALL, AUTO, MOD 64, LOW CUT off: the sound as before) */
        for (i = P_RV_TYPE; i <= P_RV_LOCUT; i++) PV(i) = UV(i) = PARAMS[i].def;
        set.flags |= 128u;
    }
    for (i = 0; i < P_COUNT; i++) if (PV(i) > PARAMS[i].hi) PV(i) = PARAMS[i].def;
    if (set.oct < -2 || set.oct > 2) set.oct = 0;
    if (set.bpm10 < 400 || set.bpm10 > 2400) set.bpm10 = 1200;
    if (set.clock > 1u) set.clock = 1;                       /* 0.9.3: INT / EXT (was INT / AUTO / USB) */
    if (set.midi_ch > 16u) set.midi_ch = 0;                 /* 0 = all channels, 1..16 */
    if (set.trs_off > 1u) set.trs_off = 0;
    if (set.mot_user_off > 1u) set.mot_user_off = 0;
    if (set.undo_preset > PR_NF_SET + 16u) set.undo_preset = 0;
    if (!set.key_vel || set.key_vel > 127) set.key_vel = 96;
    if (set.vel_curve > 3u) set.vel_curve = 0;              /* TOUCH (keyvel.h): 0 = MEDIUM */
    if (set.tune < -50 || set.tune > 50) set.tune = 0;
    if (set.focus > 2) set.focus = 0;
    if (set.sc_mode > 2) set.sc_mode = 0;
    if (set.sc_scale > 10) set.sc_scale = 0;
    if (set.sc_root > 11) set.sc_root = 0;
    if (set.sc_trans < -24 || set.sc_trans > 24) set.sc_trans = 0;
    for (i = 0; i < 3; i++)                                 /* SEL / LFO's last type: TREM/WAH, CHORUS, DELAY (ui.c) */
        if (set.last_on[i] < 1 || set.last_on[i] > (i == 2 ? PARAMS[P_DL_SW].hi : 2u)) set.last_on[i] = 1;   /* (DELAY: TAPE 3) */
}

/* the TREM/WAH switch and mode as fx.c gets them (records v3: a pure effect on every TYPE; the vibraphone's motor
 * has its own parameters, P_MOT_*, and runs inside the bar engine) */
static void tw_apply(void)
{
    fx_set(FXP_TW_SW, PV(P_TW_SW));
    fx_set(FXP_TW_MODE, PV(P_TW_MODE));
}
/* the motor's rate: 0.5 .. 10 Hz (the tremolo's law up to 120 = 10.08 Hz), or MOT SYNC's (tempo.c) */
#define MOT_RATE_V(v) ((v) > 120 ? 120 : (v))
static uint32_t mot_sync_inc;                    /* main loop: a synced motor (Q32 step a sample), 0 = free */
static void mot_rate_apply(void)
{
    eng_bar_knob(BK_MOT_INC, mot_sync_inc ? mot_sync_inc : FX_LFO_INC[MOT_RATE_V(PV(P_MOT_RATE))]);
}

/* ---- apply a parameter to the engine (main loop; volatile one-writer handover). The Rhodes volatiles are written
 * exactly as before; the bar engine gets the same knobs through eng_bar_knob() (each engine reads only its own) */
static void param_apply(uint32_t id)
{
    int32_t v = PV(id);
    switch (id) {
    case P_TYPE: eng_set_type((uint32_t)v); fx_set(FXP_DRIVE_ROW, TYPE_DRIVE_ROW[v]); tw_apply();
        if (TYPE_IS_BARS(v)) {                       /* the bar type's own MALLET / TUBE / WEIGHT / NOISE maps (C4) */
            eng_bar_knob(BK_MALLET, PV(P_BARK)); eng_bar_knob(BK_TUBE, PV(P_BELL)); eng_bar_knob(BK_WEIGHT, PV(P_HAMMER));
            eng_bar_knob(BK_NOISE, PV(P_NOISE));
        }
        break;
    case P_DRIVE: fx_set(FXP_DRIVE, v); break;
    case P_TW_SW: tw_apply(); break;
    case P_TW_DEPTH: fx_set(FXP_TW_DEPTH, MW_ADD(v)); break;
    case P_TW_RATE: fx_set(FXP_TW_RATE, v); break;
    case P_TW_MODE: tw_apply(); break;
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
    case P_TONE:     /* -0.5 gap .. 0 below the middle; above it RH_TONE_UP per step (params.json "tone": short of the axis) */
        rh_tone_off = v <= 64 ? (v - 64) * (1 << 18) : (v - 64) * RH_TONE_UP; eng_bar_knob(BK_STRIKE, (uint32_t)v); break;
    case P_BARK: rh_bark_s = (int32_t)KN_P64(v); rh_bark_g = (int32_t)KN_P64I(v); eng_bar_knob(BK_MALLET, (uint32_t)v); break;
    case P_BELL: rh_bell = (int32_t)KN_P32(v); eng_bar_knob(BK_TUBE, (uint32_t)v); break;
    case P_HAMMER: rh_ham = (int32_t)KN_HAM(v); eng_bar_knob(BK_WEIGHT, (uint32_t)v); break;
    case P_DECAY: rh_k_decay = (int32_t)DECAY_K[v]; eng_bar_knob(BK_RING, (uint32_t)v); break;   /* k = 1/S, S = 2^((v-64)/32): higher = longer */
    case P_RELEASE: rh_k_rel = (int32_t)DECAY_K[v]; eng_bar_knob(BK_DAMPER, (uint32_t)v); break;
    case P_NOISE: rh_thump = KN_THUMP[v]; rh_felt = KN_FELT[v]; rh_knock = KN_KNOCK[v]; rh_pnz = KN_PEDAL[v]; eng_bar_knob(BK_NOISE, (uint32_t)v); break;
    case P_SPREAD: rh_spread = v; break;                              /* (S13; the Rhodes, at each strike) */
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
    case P_DRV_TONE: fx_set(FXP_DRV_TONE, v); break;                  /* records v3: the drive's and the reverb's */
    case P_DRV_MIX: fx_set(FXP_DRV_MIX, v); break;
    case P_DRV_SW: fx_set(FXP_DRV_SW, v); if (v) drv_last = (uint8_t)v; break;
    case P_RV_DECAY: fx_set(FXP_RV_DECAY, v); break;
    case P_RV_TONE: fx_set(FXP_RV_TONE, v); break;
    case P_RV_PREDLY: fx_set(FXP_RV_PREDLY, v); break;
    case P_RV_TYPE: fx_set(FXP_RV_TYPE, v); if (v) rv_last = (uint8_t)v; break;   /* 0.9.5: rev_types.c */
    case P_RV_SIZE: fx_set(FXP_RV_SIZE, v); break;
    case P_RV_MOD: fx_set(FXP_RV_MOD, v); break;
    case P_RV_LOCUT: fx_set(FXP_RV_LOCUT, v); break;
    case P_MOT_ON: eng_bar_knob(BK_MOT_ON, (uint32_t)v); break;        /* the bar engine reads these, the Rhodes not */
    case P_MOT_DEPTH: eng_bar_knob(BK_MOT_DEPTH, (uint32_t)MW_ADD(v)); break;
    case P_MOT_RATE: mot_rate_apply(); break;
    case P_MOT_SHAPE: eng_bar_knob(BK_MOT_SHAPE, (uint32_t)v); break;
    default: break;                                                   /* SYNC: tempo.c */
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

/* a TYPE picked on the panel (the TYPE card, ALGORITHM) or by CC 79, not by a preset load: turning to a TYPE with a
 * motor (VIBES) from one without (a Rhodes type, MARIMBA) starts the motor (VIBES is heard with its motor), unless it
 * was switched off on the MOTOR page (set.mot_user_off, kept until its ON card turns it on again). MARIMBA leaves
 * P_MOT_ON as it is: stored, inaudible (no motor), there for the next VIBES */
static void type_pick(int32_t t)
{
    int was = TYPE_HAS_MOTOR(PV(P_TYPE));
    param_set(P_TYPE, t);
    if (!was && TYPE_HAS_MOTOR(PV(P_TYPE)) && !PV(P_MOT_ON) && !set.mot_user_off) param_set(P_MOT_ON, 1);
}

/* ---- value text (<= 5 chars) */
static void fmt_r10(char *s, int32_t v)           /* panel 0.0 .. 10.0 */
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
/* every number on a card is rounded to its last digit (0.9.3: they were cut: 1.486 s showed "1.4S", +11.8 dB "+11") */
static void fmt_hz(char *s, uint32_t inc)        /* a Q32 phase step per sample as Hz: "0.6", "4.7", "12" */
{
    int32_t t = (int32_t)(((uint64_t)inc * 441000u + 0x80000000u) >> 32);   /* inc * fs / 2^32, tenths of a Hz */
    if (t >= 100) p_fmt_int(s, (int32_t)(((uint64_t)inc * 44100u + 0x80000000u) >> 32));
    else { s[0] = (char)('0' + t / 10); s[1] = '.'; s[2] = (char)('0' + t % 10); s[3] = 0; }
}
/* log2 in Q12 (>= 0) as dB (20 log10(2) = 6.0206 dB an octave), rounded to tenths / to whole dB */
static int32_t q12_db10(int32_t q) { return (q * 60206 + 2048000) / 4096000; }
static int32_t q12_db1(int32_t q) { return (q * 60206 + 20480000) / 40960000; }
static void fmt_db10(char *s, int32_t t10, int32_t t1)   /* "+1.5", "-6.4"; from 10 dB on whole dB: "+12" */
{
    int32_t a = t10 < 0 ? -t10 : t10, n = 0;
    s[n++] = t10 < 0 ? '-' : '+';
    if (a >= 100) { p_fmt_int(s + n, t1 < 0 ? -t1 : t1); return; }
    s[n++] = (char)('0' + a / 10); s[n++] = '.'; s[n++] = (char)('0' + a % 10); s[n] = 0;
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
    case F_HZ:                                     /* 0.5 * 24^(v/127) Hz, shown as "4.7" or "12" */
    case F_HZH:                                    /* the chorus and phaser run their LFO at half the table's rate
                                                    * (fx.c: FX_LFO_INC >> 1), 0.25 .. 6 Hz: shown as it plays */
        fmt_hz(s, d->fmt == F_HZH ? FX_LFO_INC[v] >> 1 : FX_LFO_INC[v]);
        break;
    case F_FHZ: {                                  /* the filter's cutoff: "850" Hz / "4.2K" / "12K", rounded */
        int32_t hz = FX_FL_HZ[v], c = (hz + 50) / 100;
        if (hz < 1000) p_fmt_int(s, hz);
        else if (c < 100) { s[0] = (char)('0' + c / 10); s[1] = '.'; s[2] = (char)('0' + c % 10); s[3] = 'K'; s[4] = 0; }
        else { p_fmt_int(s, (hz + 500) / 1000); s[2] = 'K'; s[3] = 0; }
        break;
    }
    case F_MS: {                                   /* "356" ms / "1.5S", rounded */
        int32_t ms = (int32_t)(((uint32_t)FX_DLY_T[v] * 1000u + 22050u) / 44100u);
        int32_t c = (int32_t)(((uint32_t)FX_DLY_T[v] * 10u + 22050u) / 44100u);   /* tenths of a second */
        if (ms < 1000) p_fmt_int(s, ms);
        else { s[0] = (char)('0' + c / 10); s[1] = '.'; s[2] = (char)('0' + c % 10); s[3] = 'S'; s[4] = 0; }
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
    case F_PDLY:                                   /* "0" .. "80" ms (fx.c FX_PDLY_SAMPLES), rounded */
        p_fmt_int(s, (int32_t)((FX_PDLY_SAMPLES(v) * 1000u + 22050u) / 44100u));
        break;
    case F_THR:                                    /* "-20" dB: fx.c's threshold (log2 Q12), rounded */
        if (v && q12_db1((v * 27434) >> 7)) { s[0] = '-'; p_fmt_int(s + 1, q12_db1((v * 27434) >> 7)); }
        else { s[0] = '0'; s[1] = 0; }               /* (no "-0" at 1: -0.3 dB rounds to 0) */
        break;
    case F_GAIN:                                   /* "+6.0" / "+23" dB: fx.c's make-up (log2 Q12) */
        fmt_db10(s, q12_db10((v * 16461) >> 7), q12_db1((v * 16461) >> 7));
        break;
    case F_DB:                                     /* "+1.5" / "-12" dB: 0.1875 dB a step (FX_LVL) */
        fmt_db10(s, ((v - 64) * 15 + (v >= 64 ? 4 : -4)) / 8, ((v - 64) * 3 + (v >= 64 ? 8 : -8)) / 16);
        break;
    case F_RSIZE:                                  /* the reverb's SIZE: "AUTO" (MIX; INFINITE 64) or 0.1 .. 10.0 */
        if (!v) { s[0] = 'A'; s[1] = 'U'; s[2] = 'T'; s[3] = 'O'; s[4] = 0; } else fmt_r10(s, v);
        break;
    case F_LCUT:                                   /* LOW CUT: "OFF" or "20" .. "800" Hz (rev_types.c rev_locut_a) */
        if (!v) { s[0] = 'O'; s[1] = 'F'; s[2] = 'F'; s[3] = 0; }
        else p_fmt_int(s, (800 * (int32_t)exp2_neg((uint32_t)(127 - v) * 2767u) + 16384) >> 15);
        break;
    default: fmt_r10(s, v); break;
    }
}
