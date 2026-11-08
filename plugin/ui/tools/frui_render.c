/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: renders the window to PNG files, headless (B-M2a). The pictures are the plugin's own drawing
 * code (frui.c) on the app's real presets (presets.c's factory records) and value texts (params.c param_text, the
 * same arithmetic over the same tables, firmware/gen/fx_tables.h); in the plugin the text comes from the engine itself.
 *
 *   frui_render <font.ttf> <out dir>      writes every state listed in SCENES at its sizes */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <zlib.h>
#include "../frui.h"
#include "../../../firmware/gen/fx_tables.h"

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
static const uint8_t DEF[FRP_COUNT] = {
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

static void text_cb(void *ctx, const frui_model *m, int id, char *val, char *unit)
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

/* ---- presets.c's factory records used in the pictures (id / value pairs, 0xFF ends), and their names */
static const uint8_t PR_F05[] = {FRP_TYPE, 1, FRP_NOISE, 44, FRP_DRIVE, 20, FRP_TW_SW, 1, FRP_TW_MODE, 0, FRP_TW_DEPTH, 72,
    FRP_TW_RATE, 88, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8,
    FRP_RV_DEPTH, 24, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 70, FRP_LEVEL, 46, 0xFF};
static const uint8_t PR_F06[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 40, FRP_CMP_RATIO, 2, FRP_CMP_ATK, 70,
    FRP_CMP_GAIN, 16, FRP_DRIVE, 64, FRP_DRV_SW, 2, FRP_DRV_TONE, 72, FRP_BARK, 96, FRP_HAMMER, 80, FRP_DL_SW, 3,
    FRP_DL_DEPTH, 12, FRP_DL_SYNC, 8, FRP_DL_FDBK, 40, FRP_DL_TONE, 56, FRP_RV_DEPTH, 18, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 80,
    FRP_LEVEL, 45, 0xFF};
static const uint8_t PR_F07[] = {FRP_TYPE, 0, FRP_TONE, 56, FRP_BELL, 76, FRP_NOISE, 40, FRP_CMP_SW, 1, FRP_CMP_AMT, 50,
    FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_CP_SW, 1, FRP_CP_DEPTH, 56, FRP_CP_SPEED, 40,
    FRP_DL_SW, 1, FRP_DL_DEPTH, 12, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30, FRP_DL_TONE, 50, FRP_RV_DEPTH, 48, FRP_RV_TYPE, 2,
    FRP_RV_DECAY, 84, FRP_RV_PREDLY, 40, FRP_RV_LOCUT, 70, FRP_LEVEL, 53, 0xFF};
static const uint8_t PR_F08[] = {FRP_TYPE, 3, FRP_NOISE, 44, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80,
    FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_CP_SW, 1, FRP_CP_DEPTH, 70, FRP_CP_SPEED, 56, FRP_DL_SW, 1, FRP_DL_DEPTH, 18,
    FRP_DL_SYNC, 8, FRP_DL_FDBK, 24, FRP_DL_TONE, 76, FRP_RV_DEPTH, 30, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 80, FRP_LEVEL, 68, 0xFF};
static const uint8_t PR_F09[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_DRIVE, 34, FRP_DRV_SW, 5, FRP_BARK, 76, FRP_CMP_SW, 1,
    FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80, FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_PH_SW, 1, FRP_PH_DEPTH, 85,
    FRP_PH_SPEED, 30, FRP_PH_FDBK, 64, FRP_DL_SW, 2, FRP_DL_DEPTH, 12, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30, FRP_DL_TONE, 50,
    FRP_RV_DEPTH, 22, FRP_RV_TYPE, 2, FRP_RV_LOCUT, 70, FRP_LEVEL, 60, 0xFF};
static const uint8_t PR_F10[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 56, FRP_CMP_RATIO, 3, FRP_CMP_ATK, 20,
    FRP_CMP_REL, 40, FRP_CMP_GAIN, 26, FRP_DRIVE, 44, FRP_DRV_SW, 3, FRP_TW_SW, 2, FRP_TW_DEPTH, 92, FRP_TW_RATE, 72,
    FRP_BARK, 84, FRP_RV_DEPTH, 14, FRP_RV_TYPE, 1, FRP_RV_LOCUT, 80, FRP_LEVEL, 42, 0xFF};
static const uint8_t PR_F11[] = {FRP_TYPE, 0, FRP_NOISE, 48, FRP_CMP_SW, 1, FRP_CMP_AMT, 50, FRP_CMP_RATIO, 1, FRP_CMP_ATK, 80,
    FRP_CMP_REL, 72, FRP_CMP_GAIN, 8, FRP_DRIVE, 53, FRP_DRV_SW, 4, FRP_DRV_TONE, 60, FRP_FL_TYPE, 1, FRP_FL_FREQ, 76,
    FRP_FL_RESO, 88, FRP_FL_LFO, 64, FRP_LFO_SHAPE, 1, FRP_LFO_SYNC, 13, FRP_DL_SW, 1, FRP_DL_DEPTH, 14, FRP_DL_SYNC, 9,
    FRP_DL_FDBK, 40, FRP_DL_TONE, 60, FRP_RV_DEPTH, 22, FRP_RV_TYPE, 1, FRP_RV_LOCUT, 80, FRP_LEVEL, 18, 0xFF};
static const uint8_t PR_F13[] = {FRP_TYPE, 4, FRP_TONE, 64, FRP_BARK, 52, FRP_BELL, 70, FRP_HAMMER, 64, FRP_DECAY, 64,
    FRP_RELEASE, 64, FRP_NOISE, 40, FRP_MOT_ON, 1, FRP_MOT_DEPTH, 90, FRP_MOT_RATE, 66, FRP_MOT_SHAPE, 0, FRP_CMP_SW, 1,
    FRP_CMP_AMT, 46, FRP_CMP_RATIO, 0, FRP_CMP_ATK, 92, FRP_CMP_REL, 64, FRP_CMP_GAIN, 6, FRP_RV_DEPTH, 20, FRP_RV_TYPE, 1,
    FRP_RV_PREDLY, 20, FRP_RV_LOCUT, 70, FRP_LEVEL, 55, 0xFF};
static const uint8_t PR_F16[] = {FRP_TYPE, 5, FRP_TONE, 60, FRP_BARK, 80, FRP_BELL, 64, FRP_HAMMER, 110, FRP_DECAY, 64,
    FRP_RELEASE, 0, FRP_NOISE, 40, FRP_CMP_SW, 1, FRP_CMP_AMT, 46, FRP_CMP_RATIO, 0, FRP_CMP_ATK, 92, FRP_CMP_REL, 64,
    FRP_CMP_GAIN, 6, FRP_CP_SW, 1, FRP_CP_DEPTH, 40, FRP_CP_SPEED, 44, FRP_DL_SW, 1, FRP_DL_DEPTH, 22, FRP_DL_SYNC, 8,
    FRP_DL_FDBK, 26, FRP_DL_TONE, 50, FRP_RV_DEPTH, 38, FRP_RV_TYPE, 1, FRP_RV_DECAY, 64, FRP_RV_PREDLY, 24,
    FRP_RV_LOCUT, 80, FRP_LEVEL, 67, 0xFF};
static const uint8_t PR_F18[] = {FRP_TYPE, 2, FRP_DECAY, 84, FRP_NOISE, 30, FRP_CMP_SW, 1, FRP_CMP_AMT, 46, FRP_CMP_RATIO, 1,
    FRP_CMP_ATK, 90, FRP_CMP_REL, 80, FRP_CMP_GAIN, 8, FRP_TW_SW, 1, FRP_TW_MODE, 0, FRP_TW_DEPTH, 36, FRP_TW_RATE, 30,
    FRP_CP_SW, 1, FRP_CP_DEPTH, 60, FRP_CP_SPEED, 30, FRP_DL_SW, 2, FRP_DL_DEPTH, 21, FRP_DL_SYNC, 9, FRP_DL_FDBK, 30,
    FRP_RV_DEPTH, 76, FRP_RV_TYPE, 1, FRP_RV_DECAY, 104, FRP_RV_PREDLY, 60, FRP_RV_TONE, 56, FRP_RV_MOD, 84,
    FRP_RV_LOCUT, 80, FRP_LEVEL, 55, 0xFF};

static void load(frui_model *m, const uint8_t *rec, const char *tag, const char *name)
{
    int i;
    memset(m, 0, sizeof *m);
    for (i = 0; i < FRP_COUNT; i++) m->p[i] = DEF[i];
    for (; *rec != 0xFF; rec += 2) m->p[rec[0]] = rec[1];
    snprintf(m->preset_tag, sizeof m->preset_tag, "%s", tag);
    snprintf(m->preset_name, sizeof m->preset_name, "%s", name);
    m->focus = m->hover = m->menu = -1;
    m->out_db[0] = -15.5f; m->out_db[1] = -16.8f; m->out_peak_db[0] = -7.2f; m->out_peak_db[1] = -8.1f;
    m->gr_db = m->p[FRP_CMP_SW] ? 2.4f : 0.f;
}
static void chord(frui_model *m, const int *n, int k) { while (k--) m->notes[n[k]] = 96; }

/* ---- PNG (8-bit RGB, zlib) */
static uint32_t crc_tab[256];
static uint32_t crc(uint32_t c, const uint8_t *p, size_t n)
{
    size_t i;
    if (!crc_tab[1]) {
        uint32_t k, j, v;
        for (k = 0; k < 256; k++) { v = k; for (j = 0; j < 8; j++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1; crc_tab[k] = v; }
    }
    for (i = 0; i < n; i++) c = crc_tab[(c ^ p[i]) & 255u] ^ (c >> 8);
    return c;
}
static void chunk(FILE *f, const char *t, const uint8_t *d, uint32_t n)
{
    uint8_t b[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n};
    uint32_t c;
    fwrite(b, 1, 4, f); fwrite(t, 1, 4, f); if (n) fwrite(d, 1, n, f);
    c = crc(crc(0xFFFFFFFFu, (const uint8_t *)t, 4), d, n) ^ 0xFFFFFFFFu;
    b[0] = (uint8_t)(c >> 24); b[1] = (uint8_t)(c >> 16); b[2] = (uint8_t)(c >> 8); b[3] = (uint8_t)c;
    fwrite(b, 1, 4, f);
}
static int write_png(const char *path, const uint32_t *px, int w, int h)
{
    size_t rl = (size_t)w * 3 + 1, raw_n = rl * (size_t)h;
    uint8_t *raw = malloc(raw_n), hdr[13];
    uLongf zn = compressBound((uLong)raw_n);
    uint8_t *z = malloc(zn);
    FILE *f;
    int x, y;
    for (y = 0; y < h; y++) {
        uint8_t *r = raw + rl * (size_t)y;
        r[0] = 0;
        for (x = 0; x < w; x++) {
            uint32_t c = px[(size_t)y * (size_t)w + (size_t)x];
            r[1 + 3 * x] = (uint8_t)(c >> 16); r[2 + 3 * x] = (uint8_t)(c >> 8); r[3 + 3 * x] = (uint8_t)c;
        }
    }
    compress2(z, &zn, raw, (uLong)raw_n, 9);
    f = fopen(path, "wb");
    if (!f) { free(raw); free(z); return -1; }
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    hdr[0] = (uint8_t)(w >> 24); hdr[1] = (uint8_t)(w >> 16); hdr[2] = (uint8_t)(w >> 8); hdr[3] = (uint8_t)w;
    hdr[4] = (uint8_t)(h >> 24); hdr[5] = (uint8_t)(h >> 16); hdr[6] = (uint8_t)(h >> 8); hdr[7] = (uint8_t)h;
    hdr[8] = 8; hdr[9] = 2; hdr[10] = hdr[11] = hdr[12] = 0;
    chunk(f, "IHDR", hdr, 13);
    chunk(f, "IDAT", z, (uint32_t)zn);
    chunk(f, "IEND", 0, 0);
    fclose(f);
    free(raw); free(z);
    return 0;
}

static frui_font *font, *dfont;
static const char *outdir;
static int look;

/* the layout check: every widget's edges on the 4 px grid, every knob on a column centre (or the half-column of a
 * row of three) and on its tier's row, every control row one height; prints what is off by more than 1 px at 1x */
static int check_layout(const frui *u, const char *name)
{
    int i, n = frui_widget_count(u), bad = 0;
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        float e[4] = {g->x, g->y, g->x + g->w, g->y + g->h};
        static const char *const en[4] = {"left", "top", "right", "bottom"};
        int k;
        if (g->kind == FRW_TAB_L || g->kind == FRW_TAB_R) continue;      /* text-width tabs: checked by their baseline */
        if (g->kind == FRW_KNOB && g->id != FRP_LEVEL) goto knob;          /* a knob: its centre, below */
        for (k = 0; k < 4; k++) {
            float d = e[k] - 4.f * roundf(e[k] / 4.f);
            if (fabsf(d) > 1.f) { printf("  %s: widget kind %d id %d %s edge %.2f off the 4 px grid by %.2f\n", name, g->kind, g->id, en[k], e[k], d); bad++; }
        }
    knob:
        if (g->kind == FRW_KNOB && g->id != FRP_LEVEL) {
            float cx = g->x + g->w * 0.5f, cy = g->y + 52.f, best = 1e9f;
            int j;
            for (j = 0; j < 2 * 8 - 1; j++) {                 /* column centres and the half-columns between them */
                float b2 = fabsf(cx - ((j < 7 ? 28.f : 500.f) + 54.f * (float)(j % 7 + 1)));
                if (b2 < best) best = b2;
            }
            if (best > 1.f) { printf("  %s: knob %d centre x %.2f off its column by %.2f\n", name, g->id, cx, best); bad++; }
            if (fabsf(cy - 196.f) > 1.f && fabsf(cy - 424.f) > 1.f) { printf("  %s: knob %d centre y %.2f off its row\n", name, g->id, cy); bad++; }
        }
        if ((g->kind == FRW_SEG || g->kind == FRW_MENU || g->kind == FRW_PAGER) && g->id != FRP_TYPE &&
            (fabsf(g->y - 336.f) > 1.f || fabsf(g->h - 28.f) > 1.f)) {
            printf("  %s: control %d row y %.2f h %.2f (want 336, 28)\n", name, g->id, g->y, g->h); bad++;
        }
    }
    /* the text: each knob's label and value centred on the knob, one baseline per row; the tabs on one baseline per
     * panel; the titles on the padding's left edge; the halves' captions on their centres */
    {
        int na = frui_anchor_count(u), j;
        float tab_y[2] = {-1.f, -1.f};
        for (i = 0; i < na; i++) {
            const frui_anchor *a = frui_anchor_get(u, i);
            if (a->what == FRA_LABEL || a->what == FRA_VALUE) {
                for (j = 0; j < n; j++) {
                    const frui_widget *g = frui_widget_get(u, j);
                    if (g->kind == FRW_KNOB && g->id == a->id) {
                        float cx = g->x + g->w * 0.5f, cy = g->y + 52.f;
                        float want = a->what == FRA_LABEL ? cy - 39.f : cy + 48.f;
                        if (fabsf(a->x - cx) > 1.f) { printf("  %s: knob %d %s centred at %.2f, knob at %.2f\n", name, a->id, a->what ? "value" : "label", a->x, cx); bad++; }
                        if (fabsf(a->y - want) > 1.f) { printf("  %s: knob %d %s baseline %.2f, want %.2f\n", name, a->id, a->what ? "value" : "label", a->y, want); bad++; }
                    }
                }
            } else if (a->what == FRA_TAB) {
                int side = a->x > 480.f;
                if (tab_y[side] < 0.f) tab_y[side] = a->y;
                else if (fabsf(a->y - tab_y[side]) > 0.01f) { printf("  %s: tab %d baseline %.2f, its row %.2f\n", name, a->id, a->y, tab_y[side]); bad++; }
            } else if (a->what == FRA_TITLE) {
                if (fabsf(a->x - 28.f) > 1.f) { printf("  %s: title %d at x %.2f, want 28\n", name, a->id, a->x); bad++; }
            } else if (a->what == FRA_CAPTION) {
                if (fabsf(a->x - (a->id ? 716.f : 244.f)) > 1.f) { printf("  %s: caption %d at %.2f\n", name, a->id, a->x); bad++; }
            }
        }
        if (tab_y[0] >= 0.f && tab_y[1] >= 0.f && fabsf(tab_y[0] - tab_y[1]) > 0.01f) { printf("  %s: the panels' tabs on different baselines\n", name); bad++; }
    }
    /* the rail: every control centred on the rail's centre line, one height */
    for (i = 0; i < n; i++) {
        const frui_widget *g = frui_widget_get(u, i);
        if (g->y < 56.f && (fabsf(g->y + g->h * 0.5f - 28.f) > 0.5f || fabsf(g->h - 32.f) > 0.5f)) {
            printf("  %s: rail widget %d/%d centre %.2f h %.2f (want 28, 32)\n", name, g->kind, g->id, g->y + g->h * 0.5f, g->h); bad++;
        }
    }
    return bad;
}

static uint32_t *render_px(const frui_model *m, int pw, int ph, int chk, const char *name)
{
    uint32_t *px = malloc(sizeof(uint32_t) * (size_t)pw * (size_t)ph);
    frui_canvas c;
    frui *u = frui_new(font, text_cb, 0);
    frui_set_look(u, look, dfont);
    frui_canvas_init(&c, px, pw, ph);
    frui_draw(u, m, &c);
    if (chk) {
        int bad = check_layout(u, name);
        printf("layout check %s: %s\n", name, bad ? "OFF-GRID ITEMS ABOVE" : "all on the grid");
    }
    frui_free(u);
    return px;
}
static void render(const frui_model *m, const char *name, int pw, int ph)
{
    char path[1024];
    uint32_t *px = render_px(m, pw, ph, pw == 960 && ph == 600, name);
    snprintf(path, sizeof path, "%s/%s.png", outdir, name);
    if (write_png(path, px, pw, ph)) { fprintf(stderr, "cannot write %s\n", path); exit(1); }
    printf("%s  %dx%d\n", path, pw, ph);
    free(px);
}

/* the look's variants side by side: each variant's main view, an effect tab and the instrument menu at 2x, and a
 * contact sheet of all of them at 1x (outdir/directions) */
static void blit(uint32_t *dst, int dw, const uint32_t *src, int sw, int sh, int ox, int oy)
{
    int y;
    for (y = 0; y < sh; y++) memcpy(dst + (size_t)(oy + y) * (size_t)dw + (size_t)ox, src + (size_t)y * (size_t)sw, sizeof(uint32_t) * (size_t)sw);
}
static int directions(const int *maj7, const int *min9, const int *vib)
{
    static const char *const vn[2] = {"A1-rail", "A2-plate"};
    char path[1024], dir[1024];
    int v, sw = 960 * 2 + 48, sh = 600 * 3 + 64, bad = 0;
    uint32_t *sheet = malloc(sizeof(uint32_t) * (size_t)sw * (size_t)sh);
    frui_model m;
    for (v = 0; v < sw * sh; v++) sheet[v] = 0xFF2A2A2Cu;
    snprintf(dir, sizeof dir, "%s/directions", outdir);
    for (v = 0; v < 2; v++) {
        int s;
        look = v;
        for (s = 0; s < 3; s++) {
            uint32_t *px;
            const char *sn = s == 0 ? "main" : s == 1 ? "tab-comp-delay" : "menu-engine";
            if (s == 0) { load(&m, PR_F09, "F09", "70S PHASER"); m.tab_l = FRT_DRIVE; m.tab_r = FRT_PHASER; chord(&m, maj7, 5); }
            else if (s == 1) { load(&m, PR_F06, "F06", "DRIVEN 77"); m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f; chord(&m, min9, 5); }
            else { load(&m, PR_F13, "F13", "VIBES"); m.tab_l = FRT_MOTOR; m.tab_r = FRT_REVERB; chord(&m, vib, 5);
                   m.menu = FRUI_WID(FRW_MENU, FRP_TYPE); }
            snprintf(path, sizeof path, "%s-%s", vn[v], sn);
            px = render_px(&m, 960, 600, 1, path);
            blit(sheet, sw, px, 960, 600, 16 + v * (960 + 16), 16 + s * (600 + 16));
            free(px);
            px = render_px(&m, 1920, 1200, 0, path);
            snprintf(path, sizeof path, "%s/%s-%s.png", dir, vn[v], sn);
            if (write_png(path, px, 1920, 1200)) { fprintf(stderr, "cannot write %s\n", path); return 1; }
            printf("%s\n", path);
            free(px);
        }
    }
    snprintf(path, sizeof path, "%s/compare.png", dir);
    if (write_png(path, sheet, sw, sh)) return 1;
    printf("%s\n", path);
    free(sheet);
    return bad;
}

int main(int argc, char **argv)
{
    FILE *f;
    long n;
    uint8_t *d;
    frui_model m;
    static const int maj7[] = {36, 48, 52, 55, 59}, min9[] = {41, 56, 60, 63, 67}, vib[] = {60, 64, 67, 71, 74};
    if (argc < 3) { fprintf(stderr, "usage: frui_render <font.ttf> <out dir> [display.ttf]\n"); return 2; }
    f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot read %s\n", argv[1]); return 1; }
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    d = malloc((size_t)n);
    if (fread(d, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);
    font = frui_font_load(d, (size_t)n);
    if (!font) { fprintf(stderr, "not a TrueType font: %s\n", argv[1]); return 1; }
    outdir = argv[2];
    dfont = 0;
    if (argc > 3) {
        FILE *g = fopen(argv[3], "rb");
        long n2;
        uint8_t *d2;
        if (!g) { fprintf(stderr, "cannot read %s\n", argv[3]); return 1; }
        fseek(g, 0, SEEK_END); n2 = ftell(g); fseek(g, 0, SEEK_SET);
        d2 = malloc((size_t)n2);
        if (fread(d2, 1, (size_t)n2, g) != (size_t)n2) return 1;
        fclose(g);
        dfont = frui_font_load(d2, (size_t)n2);
        if (!dfont) { fprintf(stderr, "not a TrueType font: %s\n", argv[3]); return 1; }
    }
    if (getenv("FRUI_DIRECTIONS")) return directions(maj7, min9, vib);

    /* the main view: F09 70S PHASER, DRIVE and PHASER open, a chord held; the default size at 1x and 2x, the smallest
     * and the largest window */
    load(&m, PR_F09, "F09", "70S PHASER");
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_PHASER;
    chord(&m, maj7, 5);
    render(&m, "main-960x600@1x", 960, 600);
    render(&m, "main-960x600@2x", 1920, 1200);
    render(&m, "size-min-720x450@1x", 720, 450);
    render(&m, "size-min-720x450@2x", 1440, 900);
    render(&m, "size-max-1920x1200@1x", 1920, 1200);
    render(&m, "size-max-1920x1200@2x", 3840, 2400);

    /* every tab, on the factory presets that use it */
    load(&m, PR_F06, "F06", "DRIVEN 77");                   /* COMP + DELAY (TAPE, synced 8D) */
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f;
    chord(&m, min9, 5);
    render(&m, "tab-comp-delay", 960, 600);
    load(&m, PR_F11, "U01", "SWEEP WARM");                   /* a user preset from F11: the EQ set, FILTER swept */
    m.p[FRP_EQ_LO] = 74; m.p[FRP_EQ_MID] = 54; m.p[FRP_EQ_FREQ] = 50; m.p[FRP_EQ_HI] = 60; m.edited = 1;
    m.tab_l = FRT_EQ; m.tab_r = FRT_FILTER;
    render(&m, "tab-eq-filter", 960, 600);
    load(&m, PR_F05, "F05", "SUIT TREM");                    /* DRIVE (FDRV) + TREM/WAH on PAN */
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_TREM;
    chord(&m, maj7, 5);
    render(&m, "tab-drive-tremwah", 960, 600);
    load(&m, PR_F08, "F08", "DYNO 80S");                     /* COMP + CHORUS */
    m.tab_l = FRT_COMP; m.tab_r = FRT_CHORUS; m.gr_db = 1.8f;
    render(&m, "tab-comp-chorus", 960, 600);
    load(&m, PR_F07, "F07", "BALLAD");                       /* EQ (flat) + REVERB page 1 */
    m.tab_l = FRT_EQ; m.tab_r = FRT_REVERB; m.page_rv = 0;
    chord(&m, min9, 5);
    render(&m, "tab-eq-reverb", 960, 600);
    load(&m, PR_F18, "F18", "DREAM");                        /* REVERB 2 (page 2) */
    m.tab_l = FRT_COMP; m.tab_r = FRT_REVERB; m.page_rv = 1; m.gr_db = 1.2f;
    render(&m, "tab-comp-reverb2", 960, 600);
    load(&m, PR_F13, "F13", "VIBES");                        /* the vibraphone: its cards, the MOTOR tab */
    m.tab_l = FRT_MOTOR; m.tab_r = FRT_REVERB;
    chord(&m, vib, 5);
    render(&m, "engine-vibes-motor", 960, 600);
    load(&m, PR_F16, "F16", "WOOD ECHO");                    /* the marimba: MUTE in place of DAMPER */
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 2.0f;
    chord(&m, vib, 3);
    render(&m, "engine-marimba", 960, 600);

    /* interaction states: an open menu (DELAY's SYNC), the keyboard focus ring and a hovered control */
    load(&m, PR_F06, "F06", "DRIVEN 77");
    m.tab_l = FRT_COMP; m.tab_r = FRT_DELAY; m.gr_db = 3.6f;
    m.menu = FRUI_WID(FRW_MENU, FRP_DL_SYNC);
    render(&m, "state-menu-open", 960, 600);
    render(&m, "state-menu-open@2x", 1920, 1200);
    load(&m, PR_F10, "F10", "FUNK WAH");
    m.tab_l = FRT_DRIVE; m.tab_r = FRT_TREM;
    m.focus = FRUI_WID(FRW_KNOB, FRP_TW_DEPTH); m.hover = FRUI_WID(FRW_KNOB, FRP_DRIVE);
    chord(&m, min9, 3);
    render(&m, "state-focus-hover", 960, 600);
    frui_font_free(font);
    free(d);
    return 0;
}
