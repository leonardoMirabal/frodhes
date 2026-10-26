/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes presets (IMPLEMENTATION-v3 §10.6, §12): 19 factory presets (const; F13 / F14 VIBES, F15 / F16 MARIMBA, F17..F19
 * the Rhodes effect sounds TAPE ECHO, DREAM, RADIO) and 16 user presets in a bank of two
 * flash sectors written alternately (A 0x97000, B 0x98000): erase the older sector, write the records, the header
 * last; load picks the valid copy with the higher sequence number, so a cut-off save never loses the bank.
 *
 *   PRESETS encoder   browse F01..F19, U01..U16; loads 400 ms after the last detent (the edit goes to undo)
 *   SAVE              open the save mode: PRESETS / K1 pick the slot, SAVE again saves (when quiet), HOME cancels
 *   SAVE held 700 ms  undo / redo the last load
 *   MIDI PC           0..15 -> F01..F16, 16..31 -> U01..U16 (as before 0.9.5), 32..34 -> F17..F19 (preset_of_pc) */

#define PR_NF 19u                               /* 12 Rhodes (F01..F12, unchanged), the bar types' F13..F16, then
                                                 * F17..F19 (0.9.5: the starters U10..U12 before) */
#define PR_NU 16u
_Static_assert(PR_NF == PR_NF_UI && PR_NF + PR_NU == PR_NALL_UI && PR_NF == PR_NF_SET, "ui.c / params.c preset counts");
_Static_assert(PR_NF >= 16u && PR_NF <= 16u + 96u, "program changes 0..15: F01..F16, 16..31: U01..U16, 32..: F17..");
/* 0.9.6: the user presets are the store's record tail (set_store.c: with the settings, one CRC, two copies). The bank
 * of the versions before (two sectors, PR_BANK_A / B = frodhes_store.h OLD_PR_A / B) is only read, once: the first
 * boot's migration (frodhes.c app_main) */
#define PR_BANK_A OLD_PR_A
#define PR_BANK_B OLD_PR_B
#define PR_MAGIC 0x42505246u                    /* "FRPB" */

typedef struct { uint8_t ver, np, used, rsv; char name[12]; uint8_t p[P_SLOTS]; uint8_t rsv2[16]; } preset_t;   /* v3 */
_Static_assert(sizeof(preset_t) == 96, "preset record is 96 B (v3: 64 parameters, 16 bytes spare)");
typedef struct { uint8_t ver, np, used, rsv; char name[12]; uint8_t p[48]; } preset_v2_t;     /* 0.4 .. 0.9.2 */
_Static_assert(sizeof(preset_v2_t) == 64, "v2 preset record is 64 B");
typedef struct { uint8_t ver, np, used, rsv; char name[12]; uint8_t p[32]; } preset_v1_t;     /* 0.1 .. 0.3 */
_Static_assert(sizeof(preset_v1_t) == 48, "old preset record is 48 B");
typedef struct { uint32_t magic, seq, len, crc; } bank_hdr_t;

static preset_t pr_user[PR_NU];
_Static_assert(sizeof pr_user == ST_TAIL_N, "the user presets are the store record's tail (frodhes_store.h ST_TAIL_N)");

/* factory presets (0.9.5): (param id, value) pairs over the defaults, 0xFF ends. Two groups per engine (the user): DRY,
 * each instrument as the model plays it (F01..F04 the four Rhodes TYPEs, F13 VIBES, F15 MARIMBA: the compressor and a
 * trace of room only), and FX (F05..F12 and F17..F19 Rhodes, F14 VIBES, F16 MARIMBA: the compressor, a reverb TYPE with its LOW CUT,
 * echoes on the tempo). LEVEL: the Rhodes presets within 1 LU of F01 on the presets suite's chord
 * (tools/hil_scan.py --host --only presets, plus each preset's critical bands); VIBES ~2.5 LU above, MARIMBA as the
 * MANUAL says. F01 is the boot sound and the level reference.
 *
 * The drive TYPEs (P_DRV_SW, drive.c: 1 FDRV, 2 KHLON, 3 TSH8, 4 OHCD, 5 BHLUE) in six of them, their LEVELs
 * re-run with the TYPE in (the chord's level moves with it), each one's CPU predicted with its TYPE's own cost in
 * place of FDRV's (tools/cpu_budget.py + drive.c's DRV_US, the blend included: <= 4200 us):
 *   F06 DRIVEN 77   KHLON   a transparent boost: the piano stays itself, the bark comes with the hammer
 *   F09 70S PHASER  BHLUE   a light amp breakup under the phaser
 *   F10 FUNK WAH    TSH8    the mid hump and tight lows into the wah
 *   F11 RESO SWEEP  OHCD    more gain, open, for the resonance to ride on
 *   F12 AUTO WAH    TSH8    mids into the band-pass wah
 *   F19 RADIO       OHCD    the hard drive before the band-pass
 * F12's deep 1/4 sweep (the band-pass passes little: LEVEL 127, the compressor's make-up) is a documented exception to
 * the 1.5 LU (hil_scan.py's PRESET_TARGET: 0 +- 2.0) */
#define PR_FDN 64u                              /* bytes per factory record: up to 31 pairs, then 0xFF */
/* the compressor on every preset (0.9.5, the user: "always a bit of compression"): glue, not squash. Keys: THR -16 dB,
 * 2:1, ATTACK 80 (5 ms: the hammer and the bark pass before it acts), RELEASE 72 (~230 ms); the mallets (VIBES,
 * MARIMBA): 1.5:1, ATTACK 92 (9 ms), RELEASE 64. LOW CUT 70 (~150 Hz) / 80 (~200 Hz) keeps the bass out of the tails. */
static const char *const PR_FNAME[PR_NF] = {
    "STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO", "SUIT TREM", "DRIVEN 77", "BALLAD", "DYNO 80S", "70S PHASER",
    "FUNK WAH", "RESO SWEEP", "AUTO WAH", "VIBES", "VIBES ECHO", "MARIMBA", "WOOD ECHO", "TAPE ECHO", "DREAM", "RADIO"};   /* (the header shows 14 characters: names up to 10) */
static const uint8_t PR_FDATA[PR_NF][PR_FDN] = {
    /* ---- DRY (F01..F04, F13, F15): each instrument as the model plays it, the compressor and a trace of room only */
    /* F01 STAGE 77: the clean stage piano (the boot sound) */
    {P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_RV_DEPTH, 18, P_RV_TYPE, 1, P_RV_LOCUT, 70, P_LEVEL, 55, 0xFF},
    /* F02 SUITCASE: the suitcase's cabinet, no vibrato */
    {P_TYPE, 1, P_NOISE, 44, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_RV_DEPTH, 16, P_RV_TYPE, 1, P_RV_LOCUT, 70, P_LEVEL, 49, 0xFF},
    /* F03 EARLY FELT: the felt hammers, their soft onset, NOISE lower (soft playing) */
    {P_TYPE, 2, P_NOISE, 36, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_RV_DEPTH, 18, P_RV_TYPE, 1, P_RV_LOCUT, 70, P_LEVEL, 52, 0xFF},
    /* F04 BELL DYNO: the bell voicing, a short plate */
    {P_TYPE, 3, P_NOISE, 44, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_RV_DEPTH, 16, P_RV_TYPE, 2, P_RV_LOCUT, 80, P_LEVEL, 56, 0xFF},
    /* ---- FX */
    /* F05 SUIT TREM: the suitcase amp, its stereo PAN vibrato at 4.5 Hz, a little warmth, a vintage plate */
    {P_TYPE, 1, P_NOISE, 44, P_DRIVE, 20, P_TW_SW, 1, P_TW_MODE, 0, P_TW_DEPTH, 72, P_TW_RATE, 88, P_CMP_SW, 1,
     P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8, P_RV_DEPTH, 24, P_RV_TYPE, 2,
     P_RV_LOCUT, 70, P_LEVEL, 46, 0xFF},
    /* F06 DRIVEN 77: a gentle compressor that lets the hammer through, then the drive (soft playing clean, hard barks),
     * a tape slap on the dotted 1/8, a short plate */
    {P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 40, P_CMP_RATIO, 2, P_CMP_ATK, 70, P_CMP_GAIN, 16, P_DRIVE, 64,
     P_DRV_SW, 2, P_DRV_TONE, 72, P_BARK, 96, P_HAMMER, 80, P_DL_SW, 3, P_DL_DEPTH, 12, P_DL_SYNC, 8, P_DL_FDBK, 40,
     P_DL_TONE, 56, P_RV_DEPTH, 18, P_RV_TYPE, 2, P_RV_LOCUT, 80, P_LEVEL, 45, 0xFF},
    /* F07 BALLAD: rounder, a slow chorus, a long plate behind a pre-delay, a soft quarter-note digital echo */
    {P_TYPE, 0, P_TONE, 56, P_BELL, 76, P_NOISE, 40, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80,
     P_CMP_REL, 72, P_CMP_GAIN, 8, P_CP_SW, 1, P_CP_DEPTH, 56, P_CP_SPEED, 40, P_DL_SW, 1, P_DL_DEPTH, 12,
     P_DL_SYNC, 9, P_DL_FDBK, 30, P_DL_TONE, 50, P_RV_DEPTH, 48, P_RV_TYPE, 2, P_RV_DECAY, 84, P_RV_PREDLY, 40,
     P_RV_LOCUT, 70, P_LEVEL, 53, 0xFF},
    /* F08 DYNO 80S: the bell voicing, chorus, a dotted-1/8 digital echo on the tempo, a bright plate */
    {P_TYPE, 3, P_NOISE, 44, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_CP_SW, 1, P_CP_DEPTH, 70, P_CP_SPEED, 56, P_DL_SW, 1, P_DL_DEPTH, 18, P_DL_SYNC, 8, P_DL_FDBK, 24,
     P_DL_TONE, 76, P_RV_DEPTH, 30, P_RV_TYPE, 2, P_RV_LOCUT, 80, P_LEVEL, 68, 0xFF},
    /* F09 70S PHASER: a slow, deep phaser with feedback on a lightly driven stage piano, a dark analog echo, a plate */
    {P_TYPE, 0, P_NOISE, 48, P_DRIVE, 34, P_DRV_SW, 5, P_BARK, 76, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1,
     P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8, P_PH_SW, 1, P_PH_DEPTH, 85, P_PH_SPEED, 30, P_PH_FDBK, 64,
     P_DL_SW, 2, P_DL_DEPTH, 12, P_DL_SYNC, 9, P_DL_FDBK, 30, P_DL_TONE, 50, P_RV_DEPTH, 22, P_RV_TYPE, 2,
     P_RV_LOCUT, 70, P_LEVEL, 60, 0xFF},
    /* F10 FUNK WAH: a fast compressor, drive, the touch wah (harder playing opens it), a small dry room */
    {P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 56, P_CMP_RATIO, 3, P_CMP_ATK, 20, P_CMP_REL, 40, P_CMP_GAIN, 26,
     P_DRIVE, 44, P_DRV_SW, 3, P_TW_SW, 2, P_TW_DEPTH, 92, P_TW_RATE, 72, P_BARK, 84, P_RV_DEPTH, 14, P_RV_TYPE, 1,
     P_RV_LOCUT, 80, P_LEVEL, 42, 0xFF},
    /* F11 RESO SWEEP: a driven tone into the resonant LP12, the LFO's triangle sweeping it over 2 bars, a clean
     * quarter-note digital echo, a hall */
    {P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_DRIVE, 53, P_DRV_SW, 4, P_DRV_TONE, 60, P_FL_TYPE, 1, P_FL_FREQ, 76, P_FL_RESO, 88, P_FL_LFO, 64,
     P_LFO_SHAPE, 1, P_LFO_SYNC, 13, P_DL_SW, 1, P_DL_DEPTH, 14, P_DL_SYNC, 9, P_DL_FDBK, 40, P_DL_TONE, 60,
     P_RV_DEPTH, 22, P_RV_TYPE, 1, P_RV_LOCUT, 80, P_LEVEL, 10, 0xFF},
    /* F12 AUTO WAH: the wah: a resonant band-pass swept deep by the LFO, a sine at 1/4 on the tempo, restarted by every
     * note (the compressor's make-up carries the level LEVEL 127 cannot) */
    {P_TYPE, 0, P_BARK, 80, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_DRIVE, 52, P_DRV_SW, 3, P_FL_TYPE, 5, P_FL_FREQ, 70, P_FL_RESO, 84, P_FL_LFO, 50, P_LFO_SHAPE, 0, P_LFO_SYNC, 9,
     P_LFO_RETRIG, 1, P_RV_DEPTH, 14, P_RV_TYPE, 1, P_RV_LOCUT, 80, P_LEVEL, 121, 0xFF},
    /* F13 VIBES (dry): the vibraphone as the model plays it: STRIKE at L/3, a mallet between YARN and the middle, the
     * tube a little fuller, the motor at 2.6 Hz a little shallower, the damper and the ring as made, a quiet mallet
     * contact; the mallet compressor (1.5:1, 9 ms: the strike passes) and a trace of hall */
    {P_TYPE, 4, P_TONE, 64, P_BARK, 52, P_BELL, 70, P_HAMMER, 64, P_DECAY, 64, P_RELEASE, 64, P_NOISE, 40, P_MOT_ON, 1,
     P_MOT_DEPTH, 90, P_MOT_RATE, 66, P_MOT_SHAPE, 0, P_CMP_SW, 1, P_CMP_AMT, 46, P_CMP_RATIO, 0, P_CMP_ATK, 92,
     P_CMP_REL, 64, P_CMP_GAIN, 6, P_RV_DEPTH, 20, P_RV_TYPE, 1, P_RV_PREDLY, 20, P_RV_LOCUT, 70, P_LEVEL, 55, 0xFF},
    /* F14 VIBES ECHO: the motor off, a cord mallet, a longer ring and a softer damper, a light slow chorus, a dotted-1/8
     * tape echo on the tempo (darker repeats), a bright plate behind a short pre-delay */
    {P_TYPE, 4, P_TONE, 64, P_BARK, 82, P_BELL, 64, P_HAMMER, 60, P_DECAY, 72, P_RELEASE, 54, P_NOISE, 36, P_MOT_ON, 0,
     P_CMP_SW, 1, P_CMP_AMT, 46, P_CMP_RATIO, 0, P_CMP_ATK, 92, P_CMP_REL, 64, P_CMP_GAIN, 6, P_CP_SW, 1,
     P_CP_DEPTH, 40, P_CP_SPEED, 40, P_DL_SW, 3, P_DL_DEPTH, 20, P_DL_SYNC, 8, P_DL_FDBK, 50, P_DL_TONE, 56,
     P_RV_DEPTH, 34, P_RV_TYPE, 2, P_RV_DECAY, 70, P_RV_PREDLY, 16, P_RV_LOCUT, 80, P_LEVEL, 59, 0xFF},
    /* F15 MARIMBA (dry): the default mallet, tube and weight, MUTE 0 (rings out, as the instrument), a soft mallet
     * tick, the mallet compressor and a trace of hall */
    {P_TYPE, 5, P_TONE, 64, P_BARK, 64, P_BELL, 64, P_HAMMER, 64, P_DECAY, 64, P_RELEASE, 0, P_NOISE, 40, P_CMP_SW, 1,
     P_CMP_AMT, 46, P_CMP_RATIO, 0, P_CMP_ATK, 92, P_CMP_REL, 64, P_CMP_GAIN, 6, P_RV_DEPTH, 22, P_RV_TYPE, 1,
     P_RV_PREDLY, 12, P_RV_LOCUT, 70, P_LEVEL, 59, 0xFF},
    /* F16 WOOD ECHO: a hard yarn mallet, heavier, MUTE 0, a light chorus, a dotted-1/8 digital echo on the tempo with a
     * few soft repeats, a hall behind a pre-delay */
    {P_TYPE, 5, P_TONE, 60, P_BARK, 80, P_BELL, 64, P_HAMMER, 110, P_DECAY, 64, P_RELEASE, 0, P_NOISE, 40, P_CMP_SW, 1,
     P_CMP_AMT, 46, P_CMP_RATIO, 0, P_CMP_ATK, 92, P_CMP_REL, 64, P_CMP_GAIN, 6, P_CP_SW, 1, P_CP_DEPTH, 40,
     P_CP_SPEED, 44, P_DL_SW, 1, P_DL_DEPTH, 22, P_DL_SYNC, 8, P_DL_FDBK, 26, P_DL_TONE, 50, P_RV_DEPTH, 38,
     P_RV_TYPE, 1, P_RV_DECAY, 64, P_RV_PREDLY, 24, P_RV_LOCUT, 80, P_LEVEL, 67, 0xFF},
    /* ---- 0.9.5: TAPE ECHO, DREAM, RADIO (the starters U10..U12 until 0.9.5's RC, the same records: their sound and
     * their compressor as they were) */
    /* F17 TAPE ECHO: a warm piano into the tape echo, dotted 1/8 on the tempo, a plate */
    {P_TYPE, 0, P_TONE, 58, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72,
     P_CMP_GAIN, 8, P_DRIVE, 22, P_DL_SW, 3, P_DL_DEPTH, 29, P_DL_SYNC, 8, P_DL_FDBK, 72, P_DL_TONE, 62,
     P_RV_DEPTH, 22, P_RV_TYPE, 2, P_RV_LOCUT, 80, P_LEVEL, 47, 0xFF},
    /* F18 DREAM: felt hammers, a slow stereo pan, chorus, a quarter-note analog echo, a long dark hall, more motion */
    {P_TYPE, 2, P_DECAY, 84, P_NOISE, 30, P_CMP_SW, 1, P_CMP_AMT, 46, P_CMP_RATIO, 1, P_CMP_ATK, 90, P_CMP_REL, 80,
     P_CMP_GAIN, 8, P_TW_SW, 1, P_TW_MODE, 0, P_TW_DEPTH, 36, P_TW_RATE, 30, P_CP_SW, 1, P_CP_DEPTH, 60,
     P_CP_SPEED, 30, P_DL_SW, 2, P_DL_DEPTH, 21, P_DL_SYNC, 9, P_DL_FDBK, 30, P_RV_DEPTH, 76, P_RV_TYPE, 1,
     P_RV_DECAY, 104, P_RV_PREDLY, 60, P_RV_TONE, 56, P_RV_MOD, 84, P_RV_LOCUT, 80, P_LEVEL, 55, 0xFF},
    /* F19 RADIO: a hard drive through a band-pass (a small speaker's band), a short tape slap, a small room */
    {P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
     P_DRIVE, 55, P_DRV_SW, 4, P_DRV_TONE, 76, P_FL_TYPE, 5, P_FL_FREQ, 66, P_FL_RESO, 0, P_DL_SW, 3, P_DL_DEPTH, 15,
     P_DL_TIME, 54, P_DL_FDBK, 30, P_RV_DEPTH, 10, P_RV_TYPE, 1, P_RV_LOCUT, 80, P_LEVEL, 75, 0xFF},
};
_Static_assert(PR_FDN % 2u == 0u, "id / value pairs");

/* the user bank's starters (0.9.5): a bank that was never saved (no header in either sector: a new FM-1, or one that
 * never saved a preset) opens with these in U01..U09, in RAM only; the first save writes them with the bank. A bank
 * already saved keeps exactly what it holds, and a damaged one stays empty. Rhodes effect sounds the factory bank does
 * not have (U10..U12 held TAPE ECHO, DREAM and RADIO until 0.9.5's RC: now the factory F17..F19); LEVELs as
 * the factory ones (within 1 LU of F01 on the presets suite's chord, tools/hil_scan.py --host --only presets), each
 * one's CPU <= 4200 us (tests/py/test_presets_cpu.py). One string of (id, value) pairs, 0xFF after each starter. */
#define PR_NS 9u
_Static_assert(PR_NS <= PR_NU, "the starters fit the user bank");
static const char *const PR_SNAME[PR_NS] = {
    "DARK KEYS", "GLASS BELL", "FUNK COMP", "DREAM PAD", "LO-FI", "HARD TREM", "SLOW PAN", "REEDY", "VELVET"};
static const uint8_t PR_SDATA[] = {
    /* U01 DARK KEYS: a rounder tine, less bell, a softer hammer, a slow chorus, a dark analog echo, a long dark hall */
    P_TYPE, 0, P_TONE, 50, P_BELL, 48, P_HAMMER, 50, P_DECAY, 76, P_NOISE, 40, P_CMP_SW, 1, P_CMP_AMT, 50,
    P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8, P_CP_SW, 1, P_CP_DEPTH, 48, P_CP_SPEED, 36, P_DL_SW, 2,
    P_DL_DEPTH, 10, P_DL_SYNC, 9, P_DL_FDBK, 30, P_DL_TONE, 40, P_RV_DEPTH, 46, P_RV_TYPE, 1, P_RV_DECAY, 84,
    P_RV_PREDLY, 36, P_RV_TONE, 56, P_RV_LOCUT, 62, P_LEVEL, 46, 0xFF,
    /* U02 GLASS BELL: the bell voicing nearer the pickup's axis with less bark (glass, not growl), chorus, a dotted-1/8
     * echo, a bright plate */
    P_TYPE, 3, P_TONE, 76, P_BELL, 80, P_HAMMER, 84, P_BARK, 56, P_NOISE, 40, P_CMP_SW, 1, P_CMP_AMT, 50,
    P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8, P_CP_SW, 1, P_CP_DEPTH, 56, P_CP_SPEED, 50,
    P_DL_SW, 1, P_DL_DEPTH, 17, P_DL_SYNC, 8, P_DL_FDBK, 24, P_DL_TONE, 80, P_RV_DEPTH, 32, P_RV_TYPE, 2,
    P_RV_PREDLY, 20, P_RV_LOCUT, 80, P_LEVEL, 65, 0xFF,
    /* U03 FUNK COMP: the bright, barky comping piano: a firm compressor for evenness, a touch of mid drive (TSH8), a
     * small room */
    P_TYPE, 0, P_TONE, 72, P_BARK, 88, P_HAMMER, 84, P_BELL, 72, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50,
    P_CMP_RATIO, 3, P_CMP_ATK, 30, P_CMP_REL, 50, P_CMP_GAIN, 18, P_DRV_SW, 3, P_DRIVE, 28, P_DRV_TONE, 70,
    P_RV_DEPTH, 14, P_RV_TYPE, 1, P_RV_LOCUT, 80, P_LEVEL, 45, 0xFF,
    /* U04 DREAM PAD: felt hammers, a long decay, a slow pan at 0.9 Hz, chorus, a dotted-1/4 tape echo, the INFINITE
     * reverb's long bloom (DECAY 40: a long tail that still dies, so a save never waits) */
    P_TYPE, 2, P_DECAY, 100, P_RELEASE, 92, P_TONE, 52, P_BELL, 44, P_HAMMER, 44, P_NOISE, 30, P_CMP_SW, 1,
    P_CMP_AMT, 46, P_CMP_RATIO, 1, P_CMP_ATK, 90, P_CMP_REL, 80, P_CMP_GAIN, 8, P_TW_SW, 1, P_TW_MODE, 0,
    P_TW_DEPTH, 30, P_TW_RATE, 24, P_CP_SW, 1, P_CP_DEPTH, 56, P_CP_SPEED, 26, P_DL_SW, 3, P_DL_DEPTH, 24,
    P_DL_SYNC, 10, P_DL_FDBK, 70, P_DL_TONE, 46, P_RV_DEPTH, 70, P_RV_TYPE, 3, P_RV_DECAY, 40, P_RV_PREDLY, 60,
    P_RV_TONE, 50, P_RV_LOCUT, 80, P_LEVEL, 45, 0xFF,
    /* U05 LO-FI: worn tape, a small amp: a mellow MOSFET drive (OHCD), a closed low-pass, a dark analog slap, a little
     * AM wobble at 1.7 Hz */
    P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
    P_DRV_SW, 4, P_DRIVE, 46, P_DRV_TONE, 40, P_FL_TYPE, 1, P_FL_FREQ, 72, P_FL_RESO, 24, P_DL_SW, 2, P_DL_DEPTH, 16,
    P_DL_SYNC, 3, P_DL_FDBK, 9, P_DL_TONE, 36, P_TW_SW, 1, P_TW_MODE, 1, P_TW_DEPTH, 36, P_TW_RATE, 44,
    P_RV_DEPTH, 18, P_RV_TYPE, 1, P_RV_TONE, 44, P_RV_LOCUT, 80, P_LEVEL, 48, 0xFF,
    /* U06 HARD TREM: the amp tremolo, a near-square chop at 6 Hz over a soft two-stage breakup (BHLUE), a plate */
    P_TYPE, 0, P_NOISE, 48, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
    P_DRV_SW, 5, P_DRIVE, 30, P_TW_SW, 1, P_TW_MODE, 1, P_TW_DEPTH, 100, P_TW_RATE, 100, P_TW_SHAPE, 56, P_BARK, 72,
    P_RV_DEPTH, 20, P_RV_TYPE, 2, P_RV_LOCUT, 70, P_LEVEL, 53, 0xFF,
    /* U07 SLOW PAN: the suitcase, a wide stereo pan at 2.1 Hz with a whisper of chorus, a soft analog echo, a hall */
    P_TYPE, 1, P_NOISE, 44, P_CMP_SW, 1, P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8,
    P_TW_SW, 1, P_TW_MODE, 0, P_TW_DEPTH, 96, P_TW_RATE, 58, P_CP_SW, 1, P_CP_DEPTH, 30, P_CP_SPEED, 30, P_DL_SW, 2,
    P_DL_DEPTH, 10, P_DL_SYNC, 9, P_DL_FDBK, 26, P_DL_TONE, 54, P_RV_DEPTH, 30, P_RV_TYPE, 1, P_RV_PREDLY, 24,
    P_RV_LOCUT, 70, P_LEVEL, 53, 0xFF,
    /* U08 REEDY: a reedy electric piano: the tine near the pickup's axis (a strong second harmonic), a hard hammer, a
     * short decay, a mid drive (TSH8), an AM tremolo at 3.9 Hz, a tape slap, a short plate */
    P_TYPE, 0, P_TONE, 94, P_BARK, 72, P_BELL, 40, P_HAMMER, 100, P_DECAY, 40, P_RELEASE, 44, P_NOISE, 36, P_CMP_SW, 1,
    P_CMP_AMT, 50, P_CMP_RATIO, 1, P_CMP_ATK, 80, P_CMP_REL, 72, P_CMP_GAIN, 8, P_DRV_SW, 3, P_DRIVE, 26,
    P_DRV_TONE, 66, P_TW_SW, 1, P_TW_MODE, 1, P_TW_DEPTH, 64, P_TW_RATE, 82, P_DL_SW, 3, P_DL_DEPTH, 10, P_DL_SYNC, 6,
    P_DL_FDBK, 20, P_DL_TONE, 56, P_RV_DEPTH, 16, P_RV_TYPE, 2, P_RV_LOCUT, 80, P_LEVEL, 60, 0xFF,
    /* U09 VELVET: the softest piano, early felt hammers, the dampers softly audible, a mid hall */
    P_TYPE, 2, P_TONE, 46, P_BARK, 44, P_BELL, 36, P_HAMMER, 40, P_DECAY, 72, P_NOISE, 40, P_CMP_SW, 1, P_CMP_AMT, 46,
    P_CMP_RATIO, 1, P_CMP_ATK, 90, P_CMP_REL, 72, P_CMP_GAIN, 8, P_RV_DEPTH, 40, P_RV_TYPE, 1, P_RV_DECAY, 76,
    P_RV_PREDLY, 30, P_RV_LOCUT, 62, P_LEVEL, 39, 0xFF,
};

static void presets_starters(void)                  /* an empty bank: U01..U09 the starters (RAM; saved with the bank) */
{
    const uint8_t *d = PR_SDATA;
    uint32_t i, j;
    for (i = 0; i < PR_NS; i++) {
        preset_t *u = &pr_user[i];
        const char *nm = PR_SNAME[i];
        u->ver = 3; u->np = P_COUNT; u->used = 1; u->rsv = 0;
        for (j = 0; j < sizeof u->name; j++) u->name[j] = *nm ? *nm++ : ' ';
        for (j = 0; j < P_SLOTS; j++) u->p[j] = j < P_COUNT ? PARAMS[j].def : 0;
        for (j = 0; j < sizeof u->rsv2; j++) u->rsv2[j] = 0;
        for (; *d != 0xFF; d += 2) u->p[d[0]] = d[1];
        d++;
    }
}

/* ---- bank storage (host: two RAM sectors) */
#ifdef DINGHY_HOST
static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu, k;
    while (n--) { c ^= *p++; for (k = 0; k < 8u; k++) c = c & 1u ? (c >> 1) ^ 0xEDB88320u : c >> 1; }
    return ~c;
}
static int bank_read(uint32_t at, void *p, uint32_t n) { return st_read(at, p, n); }   /* (tests/host.c's flash) */
static uint8_t bank_buf[sizeof(bank_hdr_t) + sizeof pr_user];
#else
static int bank_read(uint32_t at, void *p, uint32_t n) { return flash_ok ? st_read(at, p, n) : -1; }
#define bank_buf st_rec                         /* (the migration's: the store's record buffer, free until its save) */
_Static_assert(sizeof st_rec >= sizeof(bank_hdr_t) + sizeof pr_user, "the old bank fits the record buffer");
#endif

_Static_assert(sizeof(bank_hdr_t) + sizeof pr_user <= 4096u, "a bank copy is one 4 KiB sector");
static int bank_valid(uint32_t at, bank_hdr_t *h)          /* the records: v3 (96 B), 0.4 (64 B) or 0.1 .. 0.3 (48 B) */
{
    if (bank_read(at, h, sizeof *h) || h->magic != PR_MAGIC) return 0;
    if (h->len != sizeof pr_user && h->len != PR_NU * sizeof(preset_v2_t) && h->len != PR_NU * sizeof(preset_v1_t)) return 0;
    if (bank_read(at, bank_buf, sizeof(bank_hdr_t) + h->len)) return 0;
    return crc32(bank_buf + sizeof(bank_hdr_t), h->len) == h->crc;
}

static void presets_load_bank_in(void);
static void presets_load_bank(void)
{
    presets_load_bank_in();
    if (set_pr_from_rc) {                            /* the settings named U10..U12 of 16 factory presets (params.c's
                                                      * conversion) and that slot is empty now: it was the starter that
                                                      * is F17..F19 today (TAPE ECHO, DREAM, RADIO) */
        uint32_t k = set.preset - PR_NF;
        if (set.preset >= PR_NF + 9u && set.preset < PR_NF + 12u && !pr_user[k].used) set.preset = (uint8_t)(16u + k - 9u);
        k = set.undo_preset - 1u - PR_NF;
        if (set.undo_preset >= PR_NF + 10u && set.undo_preset < PR_NF + 13u && !pr_user[k].used)
            set.undo_preset = (uint8_t)(17u + k - 9u);
        set_pr_from_rc = 0;
    }
}
static uint8_t pr_old;                          /* the migration found a bank before 0.9.6's (frodhes.c) */
static void presets_load_bank_in(void)
{
    bank_hdr_t ha, hb;
    int va, vb;
    uint32_t i;
    if (st_new) return;                                   /* (the record's: settings_load) */
    for (i = 0; i < PR_NU; i++) pr_user[i].used = 0;
    if (!st_old_ok) { presets_starters(); return; }     /* (SMK: the stock's data there, not ours) */
    va = bank_valid(PR_BANK_A, &ha); vb = bank_valid(PR_BANK_B, &hb);
    if (!va && !vb) {                                     /* never saved (no header in either sector): the starters;
                                                           * a damaged bank stays empty (its presets are lost, not
                                                           * replaced) */
        if (bank_read(PR_BANK_A, &ha, sizeof ha) || ha.magic != PR_MAGIC)
            if (bank_read(PR_BANK_B, &hb, sizeof hb) || hb.magic != PR_MAGIC) presets_starters();
        return;
    }
    pr_old = 1;
    {
        uint32_t at = va && (!vb || (int32_t)(ha.seq - hb.seq) > 0) ? PR_BANK_A : PR_BANK_B;
        bank_hdr_t h;
        if (!bank_valid(at, &h)) return;                      /* (re-reads it into bank_buf) */
        if (h.len == sizeof pr_user) memcpy(pr_user, bank_buf + sizeof(bank_hdr_t), sizeof pr_user);
        else {                                                /* an older bank: v3 records, the new ids at their
                                                               * defaults (it is written as v3 at the next save) */
            const uint8_t *b = bank_buf + sizeof(bank_hdr_t);
            uint32_t rl = h.len / PR_NU, nold = rl == sizeof(preset_v1_t) ? 32u : 48u;
            for (i = 0; i < PR_NU; i++) {
                const preset_v2_t *o = (const preset_v2_t *)(b + i * rl);   /* v1 has the same head, p[32] */
                preset_t *u = &pr_user[i];
                uint32_t j;
                u->ver = 3; u->used = o->used; u->rsv = 0;
                memcpy(u->name, o->name, sizeof u->name);
                for (j = 0; j < P_SLOTS; j++) u->p[j] = j < nold && j < o->np ? o->p[j] : j < P_COUNT ? PARAMS[j].def : 0;
                for (j = 0; j < sizeof u->rsv2; j++) u->rsv2[j] = 0;
                if (nold == 32u) params_split_phaser(u->p, P_SLOTS);
                params_v3_convert(u->p);
                u->np = P_COUNT;
            }
        }
    }
}

static settings_t set_saved;                     /* (frodhes.c: the settings last written) */
static int presets_save_bank(void)                 /* main loop, only when quiet (the erase stalls the audio): the
                                                    * record with the settings last written, then read back */
{
    int rc;
#ifdef DINGHY_HOST
    printf("%6u ms  preset bank saved\n", fm1_ms);   /* (tests: when it saves) */
#endif
    rc = settings_save(&set_saved, sizeof set_saved);
    return rc ? rc : settings_check();
}

/* ---- loading, browsing, saving */
static uint8_t pr_cand = 255, pr_save_mode, pr_save_slot, pr_save_pending;
static uint32_t pr_cand_t;

static void preset_name(uint32_t idx, char *s)          /* "F03 SUITCASE" / "U05 ST77 TRM" */
{
    uint32_t i = 0, n = idx < PR_NF ? idx + 1u : idx - PR_NF + 1u;
    const char *nm = idx < PR_NF ? PR_FNAME[idx] : pr_user[idx - PR_NF].used ? pr_user[idx - PR_NF].name : "----";
    s[i++] = idx < PR_NF ? 'F' : 'U';
    s[i++] = (char)('0' + n / 10u); s[i++] = (char)('0' + n % 10u); s[i++] = ' ';
    while (*nm && i < 16u) s[i++] = *nm++;
    while (i > 4u && s[i - 1u] == ' ') i--;
    s[i] = 0;
}

static uint32_t preset_of_pc(uint32_t pc)              /* a MIDI program change: 0..15 F01..F16, 16..31 U01..U16 (the
                                                        * map before 0.9.5, kept), 32.. F17..; else 255 (none) */
{
    return pc < 16u ? pc : pc < 32u ? PR_NF + pc - 16u : pc < 16u + PR_NF ? pc - 16u : 255u;
}

static int preset_values(uint32_t idx, uint8_t *p)      /* the parameters preset idx sets; -1: no preset there */
{
    uint32_t i;                                         /* ids from P_SLOTS on are not in presets yet: defaults */
    if (idx >= PR_NF + PR_NU) return -1;
    if (idx >= PR_NF && !pr_user[idx - PR_NF].used) return -1;
    for (i = 0; i < P_COUNT; i++) p[i] = PARAMS[i].def;
    if (idx < PR_NF) {
        const uint8_t *d = PR_FDATA[idx];
        for (i = 0; i < PR_FDN && d[i] != 0xFF; i += 2u) p[d[i]] = d[i + 1u];
    } else {
        const preset_t *u = &pr_user[idx - PR_NF];
        for (i = 0; i < P_SLOTS && i < P_COUNT && i < u->np; i++) p[i] = u->p[i] <= PARAMS[i].hi ? u->p[i] : PARAMS[i].def;
    }
    return 0;
}

/* the boot (frodhes.c app_main): the preset the record names, played as it is stored, without an undo step (the
 * record's undo copy stays); 0 = done, -1 = it is not there (the record's sound plays: snd_base, params.c) */
static int preset_boot(void)
{
    uint8_t p[P_COUNT];
    uint32_t i;
    if (preset_values(set.preset, p)) return -1;
    for (i = 0; i < P_COUNT; i++) param_set(i, p[i]);
    rev_relatch();                                      /* (a sound's own PLATE AUTO size, as before) */
    tempo_sync_publish();
    return 0;
}

static void preset_load(uint32_t idx)
{
    uint8_t p[P_COUNT];
    uint32_t i;
    if (preset_values(idx, p)) return;
    for (i = 0; i < P_COUNT; i++) UV(i) = PV(i);
    set.undo_valid = 1;
    set.undo_preset = (uint8_t)(set.preset < PR_NF + PR_NU ? set.preset + 1u : 0u);   /* what the undo copy was */
    for (i = 0; i < P_COUNT; i++) param_set(i, p[i]);
    rev_relatch();                                      /* (a sound's own PLATE AUTO size, as before) */
    set.preset = (uint8_t)idx;
    snd_base_take();                                    /* the sound a power cycle comes back to (params.c) */
    tempo_sync_publish();
    set_dirty = 1; set_t_change = fm1_ms;
    ui_draw_page_change();
    {
        char s[20];
        preset_name(idx, s);
        ui_say(s);
    }
}

static void preset_undo(void)
{
    uint8_t t[P_COUNT];
    uint32_t i;
    if (!set.undo_valid) return;
    for (i = 0; i < P_COUNT; i++) t[i] = PV(i);
    for (i = 0; i < P_COUNT; i++) param_set(i, UV(i));
    rev_relatch();                                      /* (a sound's own PLATE AUTO size, as before) */
    for (i = 0; i < P_COUNT; i++) UV(i) = t[i];
    if (set.undo_preset) {                              /* the header names the sound that plays (0.9.3: it kept
                                                         * the loaded preset's name over the sound before it) */
        uint8_t was = set.preset;
        set.preset = (uint8_t)(set.undo_preset - 1u);
        set.undo_preset = (uint8_t)(was < PR_NF + PR_NU ? was + 1u : 0u);
    }
    snd_base_take();                                    /* (an undo picks the sound and the preset it names) */
    set_dirty = 1; set_t_change = fm1_ms;
    tempo_sync_publish();
    ui_draw_page_change();
    ui_say("UNDO");
}

static void preset_autoname(char *n)                     /* TYPE + the effects on: "ST77 TRM PHA DD" */
{
    const char *parts[5];
    uint32_t i = 0, k, np = 0;
    parts[np++] = N_TYPE[PV(P_TYPE)];
    if (PV(P_MOT_ON) && TYPE_HAS_MOTOR(PV(P_TYPE))) parts[np++] = "MOT";   /* the motor (records v3; VIBES only) */
    if (PV(P_TW_SW) && np < 3u) parts[np++] = N_TW[PV(P_TW_SW)];
    if (PV(P_CP_SW)) parts[np++] = "CHO";
    if (PV(P_PH_SW) && np < 4u) parts[np++] = "PHA";
    if (PV(P_DL_SW) && np < 5u) parts[np++] = PV(P_DL_SW) == 1 ? "DD" : PV(P_DL_SW) == 3 ? "TD" : "AD";
    for (k = 0; k < np; k++) {
        const char *p = parts[k];
        if (k && i < 12u) n[i++] = ' ';
        while (*p && i < 12u) n[i++] = *p++;
    }
    while (i < 12u) n[i++] = ' ';
}

static void presets_poll(int32_t d_presets, int32_t d_k1, uint32_t b, uint32_t rel)
{
    static uint32_t save_t;
    char s[20];
    uint32_t i;
    if (b >> BTN_SAVE & 1u) save_t = fm1_ms;
    if (!pr_save_mode && fm1_in.buttons >> BTN_SAVE & 1u && save_t && fm1_ms - save_t >= 700u) {
        save_t = 0; preset_undo(); return;
    }
    if (rel >> BTN_SAVE & 1u && save_t) {               /* a tap: open the save mode, or (in it) save */
        save_t = 0;
        if (!pr_save_mode) {
            pr_save_mode = 1;
            pr_save_slot = set.preset >= PR_NF && set.preset < PR_NF + PR_NU ? (uint8_t)(set.preset - PR_NF) : 0;
            return;
        }
        {
            preset_t *u = &pr_user[pr_save_slot];
            u->ver = 3; u->np = P_COUNT; u->used = 1; u->rsv = 0;
            preset_autoname(u->name);
            for (i = 0; i < P_SLOTS; i++) u->p[i] = i < P_COUNT ? PV(i) : 0;
            for (i = 0; i < sizeof u->rsv2; i++) u->rsv2[i] = 0;
            set.preset = (uint8_t)(PR_NF + pr_save_slot);
            snd_base_take();
            set_dirty = 1; set_t_change = fm1_ms;
            pr_save_mode = 0;
            pr_save_pending = 1;
            ui_mark(E_HEADER);
            ui_say("SAVING...");
        }
        return;
    }
    if (pr_save_mode) {
        int32_t d = d_presets + d_k1;
        if (d) {
            int32_t v = pr_save_slot + (d > 0 ? 1 : -1);
            pr_save_slot = (uint8_t)(v < 0 ? 0 : v > (int32_t)PR_NU - 1 ? PR_NU - 1u : (uint32_t)v);
        }
        if ((b >> BTN_HOME | b >> BTN_OCT_DOWN) & 1u) { pr_save_mode = 0; ui_say("NOT SAVED"); }
        return;
    }
    if (d_presets) {
        int32_t v0 = pr_cand == 255 ? (set.preset < PR_NF + PR_NU ? set.preset : 0) : pr_cand, v = v0 + (d_presets > 0 ? 1 : -1);
        while (v >= (int32_t)PR_NF && v < (int32_t)(PR_NF + PR_NU) && !pr_user[v - PR_NF].used) v += d_presets > 0 ? 1 : -1;
        if (v < 0) v = 0;
        if (v >= (int32_t)(PR_NF + PR_NU)) v = v0;          /* past the last saved preset: stay (it went back to the last factory one) */
        pr_cand = (uint8_t)v; pr_cand_t = fm1_ms;
        preset_name((uint32_t)v, s);
        ui_say(s);
    }
    if (pr_cand != 255 && fm1_ms - pr_cand_t >= 400u) { preset_load(pr_cand); pr_cand = 255; }
}

static uint32_t pr_wait_t;
static void presets_save_poll(void)
{
    /* a save waits for silence (the flash rule); INFINITE's held tail (SUSTAIN, FREEZE) never falls silent: the footer says so every
     * MSG_MS until the freeze is left (INFINITE's SUSTAIN or FREEZE: DECAY under 120, another TYPE, OFF) and the tail
     * ends; then it saves */
    if (pr_save_pending && rev_frozen() && fm1_ms - pr_wait_t >= MSG_MS) {
        pr_wait_t = fm1_ms;
        ui_say(rev_frozen() == 2 ? "SAVE WAITS: FREEZE ON" : "SAVE WAITS: SUSTAIN ON");
    }
    if (pr_save_pending && eng_idle() && fx_quiet()) {
        pr_save_pending = 0;
        ui_say(presets_save_bank() ? "SAVE FAILED" : "SAVED");
    }
}
