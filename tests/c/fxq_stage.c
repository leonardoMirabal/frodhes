/* SPDX-License-Identifier: GPL-3.0-only */
/* One effect stage of fx.c alone on a synthetic signal, for the modulation / filter lane's measurements
 * (tools/fxq_metrics.py, tests/py/test_fxq_mod.py): spurs of the modulated delay reads, the filter's control-rate
 * sidebands, the wah's noise floor, zipper and clicks of moved knobs. Host only; fx.c is included whole, as
 * tests/c/drive_kernels.c does, so the stage runs the firmware's own integers.
 *
 *   fxq_stage STAGE SECONDS SIGNAL OUT.raw [NAME=V | @MS:NAME=V] ...
 *
 * STAGE: chorus, phaser, filter, trem (TREM / WAH: TW_SW 1 / 2), delay (DIG / ANA / TAPE: DL_SW 1 / 2 / 3).
 * SIGNAL: sine:F:A[:F2:A2] (A in Q15), noise:A (white, uniform), burst:F:A:MS (a sine for MS ms, then silence),
 * late:F:A:MS (a sine from MS ms on), impulse:A:MS (one sample every MS ms), zero. NAME: an FXP_* name without the prefix (CP_DEPTH ...); @MS: at that
 * time (block resolution). OUT.raw: int32 L, R per frame. */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef DINGHY_HOST
#define DINGHY_HOST 1
#endif
#include "fxp.h"
#define FS 44100u
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"

static const struct { const char *n; int id; } NAMES[] = {
    {"DRIVE", FXP_DRIVE}, {"TW_SW", FXP_TW_SW}, {"TW_DEPTH", FXP_TW_DEPTH}, {"TW_RATE", FXP_TW_RATE},
    {"TW_MODE", FXP_TW_MODE}, {"TW_SHAPE", FXP_TW_SHAPE}, {"CP_SW", FXP_CP_SW}, {"CP_DEPTH", FXP_CP_DEPTH},
    {"CP_SPEED", FXP_CP_SPEED}, {"DL_SW", FXP_DL_SW}, {"DL_DEPTH", FXP_DL_DEPTH}, {"DL_TIME", FXP_DL_TIME},
    {"DL_FDBK", FXP_DL_FDBK}, {"DL_TONE", FXP_DL_TONE}, {"PH_SW", FXP_PH_SW}, {"PH_DEPTH", FXP_PH_DEPTH},
    {"PH_SPEED", FXP_PH_SPEED}, {"PH_FDBK", FXP_PH_FDBK}, {"FL_TYPE", FXP_FL_TYPE}, {"FL_FREQ", FXP_FL_FREQ},
    {"FL_RESO", FXP_FL_RESO}, {"FL_LFO", FXP_FL_LFO}, {"LFO_SHAPE", FXP_LFO_SHAPE}, {"LFO_RATE", FXP_LFO_RATE},
    {"LEVEL", FXP_LEVEL},
};
static int param_id(const char *n)
{
    for (size_t i = 0; i < sizeof NAMES / sizeof NAMES[0]; i++) if (!strcmp(NAMES[i].n, n)) return NAMES[i].id;
    fprintf(stderr, "unknown parameter %s\n", n);
    exit(2);
}
typedef struct { uint32_t at; int id, v; } ev_t;

int main(int argc, char **argv)
{
    if (argc < 5) { fprintf(stderr, "usage: fxq_stage STAGE SECONDS SIGNAL OUT.raw [NAME=V | @MS:NAME=V] ...\n"); return 2; }
    const char *stage = argv[1], *sig = argv[3];
    uint32_t frames = (uint32_t)(atof(argv[2]) * FS), t, j;
    FILE *fo = fopen(argv[4], "wb");
    ev_t ev[64];
    int nev = 0;
    double f1 = 0, a1 = 0, f2 = 0, a2 = 0, ms = 0;
    char kind[16] = {0};
    uint32_t rnd = 12345u;
    if (!fo) return 2;
    sscanf(sig, "%15[a-z]", kind);
    if (!strcmp(kind, "sine")) sscanf(sig, "sine:%lf:%lf:%lf:%lf", &f1, &a1, &f2, &a2);
    else if (!strcmp(kind, "noise")) sscanf(sig, "noise:%lf", &a1);
    else if (!strcmp(kind, "burst")) sscanf(sig, "burst:%lf:%lf:%lf", &f1, &a1, &ms);
    else if (!strcmp(kind, "late")) sscanf(sig, "late:%lf:%lf:%lf", &f1, &a1, &ms);
    else if (!strcmp(kind, "impulse")) sscanf(sig, "impulse:%lf:%lf", &a1, &ms);
    for (int k = 5; k < argc; k++) {
        char name[32];
        int v;
        unsigned at = 0;
        if (argv[k][0] == '@') { if (sscanf(argv[k], "@%u:%31[A-Z_0-9]=%d", &at, name, &v) != 3) return 2; }
        else if (sscanf(argv[k], "%31[A-Z_0-9]=%d", name, &v) != 2) return 2;
        ev[nev].at = (uint32_t)((uint64_t)at * FS / 1000u); ev[nev].id = param_id(name); ev[nev].v = v; nev++;
    }
    for (t = 0; t < frames; t += 256u) {
        static int32_t m[256], L[256], R[256], o[512];
        uint32_t n = frames - t < 256u ? frames - t : 256u;
        for (int k = 0; k < nev; k++) if (ev[k].at <= t && ev[k].at + 256u > t) fx_set((uint32_t)ev[k].id, ev[k].v);
        for (j = 0; j < n; j++) {
            double s = 0, tt = (double)(t + j) / FS;
            if (!strcmp(kind, "sine")) s = a1 * sin(2 * M_PI * f1 * tt) + a2 * sin(2 * M_PI * f2 * tt);
            else if (!strcmp(kind, "noise")) { rnd = rnd * 1664525u + 1013904223u; s = a1 * ((double)(rnd >> 8) / 8388608.0 - 1.0); }
            else if (!strcmp(kind, "burst")) s = tt * 1000.0 < ms ? a1 * sin(2 * M_PI * f1 * tt) : 0;
            else if (!strcmp(kind, "late")) s = tt * 1000.0 >= ms ? a1 * sin(2 * M_PI * f1 * tt) : 0;
            else if (!strcmp(kind, "impulse")) s = ((t + j) % (uint32_t)(ms * FS / 1000.0)) == 0 ? a1 : 0;
            m[j] = (int32_t)lrint(s);
            L[j] = R[j] = m[j];
        }
        if (!strcmp(stage, "chorus")) fx_chorus(L, R, n);
        else if (!strcmp(stage, "phaser")) fx_phaser(L, R, n);
        else if (!strcmp(stage, "delay")) fx_delay(L, R, n);
        else if (!strcmp(stage, "filter")) { fx_filter(m, n); memcpy(L, m, n * 4); memcpy(R, m, n * 4); }
        else if (!strcmp(stage, "trem")) { memset(L, 0, sizeof L); memset(R, 0, sizeof R); fx_trem(m, L, R, n); }
        else { fprintf(stderr, "unknown stage %s\n", stage); return 2; }
        for (j = 0; j < n; j++) { o[2 * j] = L[j]; o[2 * j + 1] = R[j]; }
        fwrite(o, sizeof o[0], 2u * n, fo);
    }
    fclose(fo);
    return 0;
}
