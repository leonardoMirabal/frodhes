/* SPDX-License-Identifier: GPL-3.0-only */
/* The drive framework's shared values, printed (tests/py/test_drive_own.py builds this with -DFR_OWN=0 and -DFR_OWN=1
 * and compares): the MIX law (DR_WET / DR_DRY per MIX value), the wet share fx_spread aligns its side to (dr_share on
 * a grid of gains) and the gains at boot (dr_w, dr_d, dr_sal). The plugin's own-code build states them in drive.c;
 * every other build has them from fx.c. fx.c is included whole, as tests/c/drive_scan.c does. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DINGHY_HOST 1
#define FS 44100u
#include "fxp.h"
static uint32_t rh_lfsr, bar_lfsr, bar_mot_ph;
static void eng_rh_kill_all(void) {}
static void bar_kill_all(void) {}
#define RH_SP_SEC(s)
static int32_t rh_side[256];
static uint8_t rh_side_on;
#include "fx.c"
#include "rev_types.c"

int main(void)
{
    int v, w, d;
    printf("boot %d %d %d %d\n", (int)dr_w, (int)dr_d, (int)dr_sal, (int)drv_on);
    for (v = 0; v < 128; v++) printf("mix %d %d %d\n", v, (int)DR_WET(v), (int)DR_DRY(v));
    for (w = 0; w <= 32768; w += 256)
        for (d = 0; d <= 32768; d += 1280) printf("share %d %d %d\n", w, d, (int)dr_share(w, d));
    return 0;
}
