/* SPDX-License-Identifier: GPL-3.0-only */
/* The DRIVE measured on the firmware's own code: fx.c is included whole and fx_drive runs on a test signal, 256 frames
 * per call as fx_process calls it. Integer-exact and deterministic: the same input gives the same output, bit for bit,
 * as on the FM-1. tools/drive_scan.py writes the signals (sines, sweeps, two-tones, a bass under a chord, level steps),
 * runs this and analyses the output (aliasing, the low band, levels).
 *
 *   drive_scan IN OUT [NAME=V ...] [@BLOCK:NAME=V ...]
 *
 * IN, OUT: raw little-endian int32 samples (Q15 with headroom, as fx_process's mono). NAME = an fx parameter
 * (DRIVE DRV_TONE DRV_MIX DRV_SW DRIVE_ROW, or #id for any FXP id), set with fx_set before the first block, or before
 * block BLOCK with the @ form (a switch while the signal plays). Prints "cost_us N peak N on N next N" at the end
 * (fx_cost_us(), the largest |out|, drive.c's TYPE that runs and the one chosen that waits for it to fade out);
 * with DRIVE_SCAN_TRACE set in the environment, the same per block on stderr ("blk N cost_us N on N next N"). */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DINGHY_HOST 1
#define FS 44100u                                     /* (platform.c's) */
#include "fxp.h"
/* fx.c's reset touches the voice engines: stubs (the harness never resets) */
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
/* fx.c's SPREAD (S13) mixes rhodes.c's side signal: none here (rh_side_on 0) */
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"    /* (fx.c's reverb framework: fx_reverb, rev_cost_us) */

static unsigned drv_next(void)                  /* the TYPE chosen that waits for the one that runs to fade out */
{
    unsigned t = (unsigned)fx_p[FXP_DRV_SW];
    return t && t != drv_on ? t : 0u;
}

static const struct { const char *name; int id; } NAMES[] = {
    {"DRIVE", FXP_DRIVE}, {"DRV_TONE", FXP_DRV_TONE}, {"DRV_MIX", FXP_DRV_MIX}, {"DRV_SW", FXP_DRV_SW},
    {"DRIVE_ROW", FXP_DRIVE_ROW}, {"RAW", FXP_RAW},
};

static int set_one(const char *s)                     /* "NAME=V" or "#ID=V" */
{
    char name[32];
    int v, id = -1;
    unsigned i;
    const char *eq = strchr(s, '=');
    if (!eq || (size_t)(eq - s) >= sizeof name) return -1;
    memcpy(name, s, (size_t)(eq - s)); name[eq - s] = 0;
    v = atoi(eq + 1);
    if (name[0] == '#') id = atoi(name + 1);
    else for (i = 0; i < sizeof NAMES / sizeof NAMES[0]; i++) if (!strcmp(name, NAMES[i].name)) id = NAMES[i].id;
    if (id < 0 || id >= FXP_N) return -1;
    fx_set((uint32_t)id, v);
    return 0;
}

int main(int argc, char **argv)
{
    FILE *fi, *fo;
    static int32_t buf[256];
    uint32_t blk = 0, n;
    int32_t pk = 0;
    int a, trace = getenv("DRIVE_SCAN_TRACE") != 0;
    if (argc < 3) { fprintf(stderr, "usage: drive_scan IN OUT [NAME=V ...] [@BLOCK:NAME=V ...]\n"); return 2; }
    for (a = 3; a < argc; a++)
        if (argv[a][0] != '@' && set_one(argv[a])) { fprintf(stderr, "bad parameter %s\n", argv[a]); return 2; }
    fi = fopen(argv[1], "rb"); fo = fopen(argv[2], "wb");
    if (!fi || !fo) { perror("drive_scan"); return 2; }
    while ((n = (uint32_t)fread(buf, sizeof buf[0], 256, fi)) > 0) {
        uint32_t j;
        for (a = 3; a < argc; a++)                    /* the switches due before this block */
            if (argv[a][0] == '@' && (uint32_t)atoi(argv[a] + 1) == blk) {
                const char *c = strchr(argv[a], ':');
                if (!c || set_one(c + 1)) { fprintf(stderr, "bad switch %s\n", argv[a]); return 2; }
            }
        if (n < 256) memset(buf + n, 0, (256 - n) * sizeof buf[0]);
        fx_drive(buf, 256);
        for (j = 0; j < n; j++) if (iabs(buf[j]) > pk) pk = iabs(buf[j]);
        fwrite(buf, sizeof buf[0], n, fo);
        if (trace) fprintf(stderr, "blk %u cost_us %u on %u next %u\n", blk, fx_cost_us(), drv_on, drv_next());
        blk++;
    }
    fclose(fi); fclose(fo);
    printf("cost_us %u peak %d on %u next %u\n", fx_cost_us(), pk, drv_on, drv_next());
    return 0;
}
