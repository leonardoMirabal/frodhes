/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the parameter ids and the option names the UI draws (B-M2a).
 * The ids are firmware/src/params.c's enum, in its order (append-only: they are the stored records' and the plugin's
 * parameter ids); tests/py/test_plugin_ui.py checks this copy against params.c. B-M1's generated
 * plugin/gen/params_map.h replaces it. The value text always comes from the app (param_text), never from here. */
#ifndef FRUI_PARAMS_H
#define FRUI_PARAMS_H

enum { FRP_TYPE, FRP_DRIVE, FRP_TW_SW, FRP_TW_DEPTH, FRP_TW_RATE, FRP_TW_SYNC, FRP_TW_MODE, FRP_TW_SHAPE,
       FRP_CP_SW, FRP_CP_DEPTH, FRP_CP_SPEED, FRP_CP_SYNC, FRP_CP_FDBK,
       FRP_DL_SW, FRP_DL_DEPTH, FRP_DL_TIME, FRP_DL_SYNC, FRP_DL_FDBK, FRP_DL_TONE,
       FRP_RV_DEPTH, FRP_TONE, FRP_BARK, FRP_BELL, FRP_HAMMER, FRP_DECAY, FRP_RELEASE, FRP_NOISE, FRP_SPREAD,
       FRP_PH_SW, FRP_PH_DEPTH, FRP_PH_SPEED, FRP_PH_SYNC, FRP_PH_FDBK,
       FRP_CMP_SW, FRP_CMP_AMT, FRP_CMP_ATK, FRP_CMP_REL,
       FRP_LEVEL,
       FRP_CMP_RATIO, FRP_CMP_GAIN,
       FRP_FL_TYPE, FRP_FL_FREQ, FRP_FL_RESO, FRP_FL_LFO,
       FRP_LFO_SHAPE, FRP_LFO_RATE, FRP_LFO_SYNC, FRP_LFO_RETRIG,
       FRP_DRV_TONE, FRP_DRV_MIX, FRP_DRV_SW, FRP_RV_DECAY, FRP_RV_TONE, FRP_RV_PREDLY,
       FRP_MOT_ON, FRP_MOT_DEPTH, FRP_MOT_RATE, FRP_MOT_SHAPE, FRP_MOT_SYNC,
       FRP_RV_TYPE, FRP_RV_SIZE, FRP_RV_MOD, FRP_RV_LOCUT,
       FRP_EQ_LO, FRP_EQ_MID, FRP_EQ_FREQ, FRP_EQ_HI,
       FRP_COUNT };
#define FRP_TUNE 100               /* the app's TUNE setting (cents -50..+50 as 0..100): VOICE's eighth card */
#define FRP_TW_KIND 101            /* TREM/WAH's EFFECT as the device shows it: OFF PAN TREM WAH (TW_SW + TW_MODE) */

/* params.c's ranges for the ids the UI draws as knobs (0..hi) and which are centred (64 = 0) */
static const unsigned char FRP_HI[FRP_COUNT] = {
    [FRP_TYPE] = 5, [FRP_DRIVE] = 127, [FRP_TW_SW] = 2, [FRP_TW_DEPTH] = 127, [FRP_TW_RATE] = 127, [FRP_TW_SYNC] = 13,
    [FRP_TW_MODE] = 1, [FRP_TW_SHAPE] = 127, [FRP_CP_SW] = 1, [FRP_CP_DEPTH] = 127, [FRP_CP_SPEED] = 127,
    [FRP_CP_SYNC] = 14, [FRP_CP_FDBK] = 127, [FRP_DL_SW] = 3, [FRP_DL_DEPTH] = 127, [FRP_DL_TIME] = 127,
    [FRP_DL_SYNC] = 13, [FRP_DL_FDBK] = 127, [FRP_DL_TONE] = 127, [FRP_RV_DEPTH] = 127, [FRP_TONE] = 127,
    [FRP_BARK] = 127, [FRP_BELL] = 127, [FRP_HAMMER] = 127, [FRP_DECAY] = 127, [FRP_RELEASE] = 127, [FRP_NOISE] = 127,
    [FRP_SPREAD] = 127, [FRP_PH_SW] = 1, [FRP_PH_DEPTH] = 127, [FRP_PH_SPEED] = 127, [FRP_PH_SYNC] = 14,
    [FRP_PH_FDBK] = 127, [FRP_CMP_SW] = 1, [FRP_CMP_AMT] = 127, [FRP_CMP_ATK] = 127, [FRP_CMP_REL] = 127,
    [FRP_LEVEL] = 127, [FRP_CMP_RATIO] = 7, [FRP_CMP_GAIN] = 127, [FRP_FL_TYPE] = 6, [FRP_FL_FREQ] = 127,
    [FRP_FL_RESO] = 127, [FRP_FL_LFO] = 127, [FRP_LFO_SHAPE] = 5, [FRP_LFO_RATE] = 127, [FRP_LFO_SYNC] = 14,
    [FRP_LFO_RETRIG] = 1, [FRP_DRV_TONE] = 127, [FRP_DRV_MIX] = 127, [FRP_DRV_SW] = 5, [FRP_RV_DECAY] = 127,
    [FRP_RV_TONE] = 127, [FRP_RV_PREDLY] = 127, [FRP_MOT_ON] = 1, [FRP_MOT_DEPTH] = 127, [FRP_MOT_RATE] = 127,
    [FRP_MOT_SHAPE] = 127, [FRP_MOT_SYNC] = 13, [FRP_RV_TYPE] = 3, [FRP_RV_SIZE] = 127, [FRP_RV_MOD] = 127,
    [FRP_RV_LOCUT] = 127, [FRP_EQ_LO] = 127, [FRP_EQ_MID] = 127, [FRP_EQ_FREQ] = 127, [FRP_EQ_HI] = 127,
};
#define FRP_CENTRED(id) ((id) == FRP_TONE || (id) == FRP_DRV_TONE || (id) == FRP_RV_TONE || (id) == FRP_LEVEL || \
                         (id) == FRP_EQ_LO || (id) == FRP_EQ_MID || (id) == FRP_EQ_HI || (id) == FRP_TUNE)

/* option names, as the device's cards show them (params.c N_*, the long forms where the device has them) */
static const char *const FRN_TYPE[] = {"STAGE 77", "SUITCASE", "EARLY FELT", "BELL DYNO", "VIBES", "MARIMBA"};
static const char *const FRN_ONOFF[] = {"OFF", "ON"};
static const char *const FRN_TWK[] = {"OFF", "PAN", "TREM", "WAH"};
static const char *const FRN_DRV[] = {"OFF", "FDRV", "KHLON", "TSH8", "OHCD", "BHLUE"};
static const char *const FRN_DL[] = {"OFF", "DIG", "ANA", "TAPE"};
static const char *const FRN_RV[] = {"OFF", "HALL", "PLATE", "INFINITE"};
static const char *const FRN_FLT[] = {"OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH"};
static const char *const FRN_LFOS[] = {"SINE", "TRI", "SAW", "RAMP", "SQR", "S&H"};
static const char *const FRN_RETRIG[] = {"FREE", "KEY"};
static const char *const FRN_SYNC[] = {"FREE", "1/32", "16T", "1/16", "8T", "16D", "1/8", "4T", "8D", "1/4", "4D",
                                       "1/2", "1/1", "2BAR", "4BAR"};
#endif
