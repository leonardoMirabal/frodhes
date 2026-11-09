/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the value texts (frui_text.h). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "frui_text.h"
#include "../../firmware/gen/fx_tables.h"

/* ---- params.c's formats and defaults (PARAMS[].fmt / .def) */
enum { F_R10, F_ENUM, F_HZ, F_MS, F_DIV, F_BIP, F_DB, F_THR, F_GAIN, F_FHZ, F_HZH, F_PDLY, F_RSIZE, F_LCUT, F_EQDB, F_EQHZ };
static const uint8_t FMT[FRP_COUNT] = {
    [FRP_TYPE] = F_ENUM, [FRP_TW_SW] = F_ENUM, [FRP_TW_RATE] = F_HZ, [FRP_TW_SYNC] = F_DIV, [FRP_TW_MODE] = F_ENUM,
    [FRP_CP_SW] = F_ENUM, [FRP_CP_SPEED] = F_HZH, [FRP_CP_SYNC] = F_DIV, [FRP_DL_SW] = F_ENUM, [FRP_DL_TIME] = F_MS,
    [FRP_DL_SYNC] = F_DIV, [FRP_TONE] = F_BIP, [FRP_PH_SW] = F_ENUM, [FRP_PH_SPEED] = F_HZH, [FRP_PH_SYNC] = F_DIV,
    [FRP_CMP_SW] = F_ENUM, [FRP_CMP_AMT] = F_THR, [FRP_LEVEL] = F_DB, [FRP_CMP_RATIO] = F_ENUM, [FRP_CMP_GAIN] = F_GAIN,
    [FRP_FL_TYPE] = F_ENUM, [FRP_FL_FREQ] = F_FHZ, [FRP_LFO_SHAPE] = F_ENUM, [FRP_LFO_RATE] = F_HZ,
    [FRP_LFO_SYNC] = F_DIV, [FRP_LFO_RETRIG] = F_ENUM, [FRP_DRV_TONE] = F_BIP, [FRP_DRV_SW] = F_ENUM,
    [FRP_RV_TONE] = F_BIP, [FRP_RV_PREDLY] = F_PDLY, [FRP_MOT_ON] = F_ENUM, [FRP_MOT_RATE] = F_HZ,
    [FRP_MOT_SYNC] = F_DIV, [FRP_RV_TYPE] = F_ENUM, [FRP_RV_SIZE] = F_RSIZE, [FRP_RV_LOCUT] = F_LCUT,
    [FRP_EQ_LO] = F_EQDB, [FRP_EQ_MID] = F_EQDB, [FRP_EQ_FREQ] = F_EQHZ, [FRP_EQ_HI] = F_EQDB,
};
const uint8_t frui_param_def[FRP_COUNT] = {
    [FRP_TW_RATE] = 90, [FRP_CP_SPEED] = 50, [FRP_CP_FDBK] = 40, [FRP_DL_TIME] = 85, [FRP_DL_FDBK] = 90,
    [FRP_DL_TONE] = 70, [FRP_TONE] = 64, [FRP_BARK] = 64, [FRP_BELL] = 64, [FRP_HAMMER] = 64, [FRP_DECAY] = 64,
    [FRP_RELEASE] = 64, [FRP_NOISE] = 48, [FRP_PH_SPEED] = 40, [FRP_PH_FDBK] = 50, [FRP_CMP_ATK] = 40,
    [FRP_CMP_REL] = 50, [FRP_LEVEL] = 64, [FRP_CMP_RATIO] = 3, [FRP_FL_TYPE] = 2, [FRP_FL_FREQ] = 127,
    [FRP_LFO_RATE] = 40, [FRP_DRV_TONE] = 64, [FRP_DRV_MIX] = 127, [FRP_DRV_SW] = 1, [FRP_RV_DECAY] = 64,
    [FRP_RV_TONE] = 64, [FRP_MOT_DEPTH] = 110, [FRP_MOT_RATE] = 78, [FRP_RV_TYPE] = 1, [FRP_RV_MOD] = 64,
    [FRP_EQ_LO] = 64, [FRP_EQ_MID] = 64, [FRP_EQ_FREQ] = 64, [FRP_EQ_HI] = 64,
};
static const char *const N_RATIO[] = {"1.5:1", "2:1", "3:1", "4:1", "6:1", "8:1", "10:1", "20:1"};

/* ---- params.c's value text (fmt_r10, fmt_hz, fmt_db10, param_text; ui.c slot_text's special cases) */
static void fmt_r10(char *s, int32_t v)
{
    int32_t t = (v * 100 + 63) / 127;
    if (t >= 100) strcpy(s, "10.0"); else sprintf(s, "%d.%d", t / 10, t % 10);
}
static void fmt_hz(char *s, uint32_t inc)
{
    int32_t t = (int32_t)(((uint64_t)inc * 441000u + 0x80000000u) >> 32);
    if (t >= 100) sprintf(s, "%d", (int32_t)(((uint64_t)inc * 44100u + 0x80000000u) >> 32));
    else sprintf(s, "%d.%d", t / 10, t % 10);
}
static int32_t q12_db10(int32_t q) { return (q * 60206 + 2048000) / 4096000; }
static int32_t q12_db1(int32_t q) { return (q * 60206 + 20480000) / 40960000; }
static void fmt_db10(char *s, int32_t t10, int32_t t1)
{
    int32_t a = t10 < 0 ? -t10 : t10;
    if (a >= 100) sprintf(s, "%c%d", t10 < 0 ? '-' : '+', t1 < 0 ? -t1 : t1);
    else sprintf(s, "%c%d.%d", t10 < 0 ? '-' : '+', a / 10, a % 10);
}
static int32_t exp2_neg_hz(int32_t top, uint32_t x)    /* top * 2^(-x / 65536), rounded (fx.c exp2_neg's use) */
{
    return (int32_t)lround((double)top * pow(2.0, -(double)x / 65536.0));
}
static const char *const N_STRIKE[3] = {"CENTRE", "L/3", "EDGE"};
static const uint8_t V_STRIKE[3] = {0, 64, 127};
static const char *const N_VFAM[4] = {"SOFT", "YARN", "CORD", "RUBBER"}, *const N_MFAM[4] = {"SOFT", "MED", "HARD", "RUBBER"};
static const uint8_t V_FAM[4] = {0, 42, 85, 127};

void frui_text_default(void *ctx, const frui_model *m, int id, char *val, char *unit)
{
    int32_t v;
    int sy = 0;
    (void)ctx;
    unit[0] = 0;
    if (id == FRP_TUNE) { if (m->tune) sprintf(val, "%+d", m->tune); else strcpy(val, "0"); strcpy(unit, "c"); return; }
    if (id < 0 || id >= FRP_COUNT) { strcpy(val, "-"); return; }
    v = m->p[id];

    switch (id) {                                    /* ui.c slot_sync_id: a synced rate shows its note value */
    case FRP_DL_TIME: sy = FRP_DL_SYNC; break;
    case FRP_CP_SPEED: sy = FRP_CP_SYNC; break;
    case FRP_PH_SPEED: sy = FRP_PH_SYNC; break;
    case FRP_LFO_RATE: sy = FRP_LFO_SYNC; break;
    case FRP_MOT_RATE: sy = FRP_MOT_SYNC; break;
    case FRP_TW_RATE: sy = m->p[FRP_TW_SW] != 2 ? FRP_TW_SYNC : 0; break;
    }
    if (sy && m->p[sy]) { strcpy(val, FRN_SYNC[m->p[sy]]); return; }
    if (m->p[FRP_TYPE] >= 4) {                       /* the bar engine's named detents (eng_pages.h) */
        int f;
        if (id == FRP_TONE) {
            for (f = 0; f < 3; f++) if (v == V_STRIKE[f]) { strcpy(val, N_STRIKE[f]); return; }
            fmt_r10(val, v); return;
        }
        if (id == FRP_BARK)
            for (f = 0; f < 4; f++)
                if (v + 4 >= V_FAM[f] && v <= V_FAM[f] + 4) { strcpy(val, (m->p[FRP_TYPE] == 5 ? N_MFAM : N_VFAM)[f]); return; }
    }
    if (id == FRP_RV_DECAY && m->p[FRP_RV_TYPE] == 3 && v >= 120) { strcpy(val, v >= 127 ? "FREEZE" : "SUSTAIN"); return; }
    if (id == FRP_TW_RATE && m->p[FRP_TW_SW] == 2) { fmt_r10(val, v); return; }
    if (id == FRP_TW_SHAPE && m->p[FRP_TW_SW] == 2) { strcpy(val, "-"); return; }
    if (id == FRP_MOT_RATE) { fmt_hz(val, FX_LFO_INC[v > 120 ? 120 : v]); strcpy(unit, "Hz"); return; }
    switch (FMT[id]) {
    case F_ENUM:
        strcpy(val, id == FRP_CMP_RATIO ? N_RATIO[v] : "");
        break;
    case F_HZ: case F_HZH:
        fmt_hz(val, FMT[id] == F_HZH ? FX_LFO_INC[v] >> 1 : FX_LFO_INC[v]);
        strcpy(unit, "Hz");
        break;
    case F_EQHZ: case F_FHZ: {
        int32_t hz = FMT[id] == F_EQHZ ? exp2_neg_hz(5000, (uint32_t)(127 - v) * 2396u) : FX_FL_HZ[v];
        int32_t c = (hz + 50) / 100;
        if (hz < 1000) { sprintf(val, "%d", hz); strcpy(unit, "Hz"); }
        else if (c < 100) { sprintf(val, "%d.%d", c / 10, c % 10); strcpy(unit, "kHz"); }
        else { sprintf(val, "%d", (hz + 500) / 1000); strcpy(unit, "kHz"); }
        break;
    }
    case F_MS: {
        int32_t ms = (int32_t)(((uint32_t)FX_DLY_T[v] * 1000u + 22050u) / 44100u);
        int32_t c = (int32_t)(((uint32_t)FX_DLY_T[v] * 10u + 22050u) / 44100u);
        if (ms < 1000) { sprintf(val, "%d", ms); strcpy(unit, "ms"); }
        else { sprintf(val, "%d.%d", c / 10, c % 10); strcpy(unit, "s"); }
        break;
    }
    case F_BIP: {
        int32_t t = ((v - 64) * 50 + (v >= 64 ? 31 : -31)) / 63;
        sprintf(val, "%c%d.%d", t < 0 ? '-' : '+', (t < 0 ? -t : t) / 10, (t < 0 ? -t : t) % 10);
        break;
    }
    case F_PDLY: sprintf(val, "%d", (int32_t)(((uint32_t)v * 3528u / 127u * 1000u + 22050u) / 44100u)); strcpy(unit, "ms"); break;
    case F_THR:
        if (v && q12_db1((v * 27434) >> 7)) sprintf(val, "-%d", q12_db1((v * 27434) >> 7)); else strcpy(val, "0");
        strcpy(unit, "dB");
        break;
    case F_GAIN: fmt_db10(val, q12_db10((v * 16461) >> 7), q12_db1((v * 16461) >> 7)); strcpy(unit, "dB"); break;
    case F_DB:
        fmt_db10(val, ((v - 64) * 15 + (v >= 64 ? 4 : -4)) / 8, ((v - 64) * 3 + (v >= 64 ? 8 : -8)) / 16);
        strcpy(unit, "dB");
        break;
    case F_EQDB: {
        int32_t e = v ? v - 64 : -63;
        fmt_db10(val, (e * 150 + (e >= 0 ? 31 : -31)) / 63, (e * 15 + (e >= 0 ? 31 : -31)) / 63);
        strcpy(unit, "dB");
        break;
    }
    case F_RSIZE: if (!v) strcpy(val, "AUTO"); else fmt_r10(val, v); break;
    case F_LCUT:
        if (!v) strcpy(val, "OFF");
        else { sprintf(val, "%d", exp2_neg_hz(800, (uint32_t)(127 - v) * 2767u)); strcpy(unit, "Hz"); }
        break;
    default: fmt_r10(val, v); break;
    }
}

