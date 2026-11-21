# SPDX-License-Identifier: GPL-3.0-only
"""The packed bar tables (tools/gen_bars_pack.py -> firmware/gen/bars_pack.h; bars.c BAR_KM / bar_card_gain): the header
is current, bars.h's key type is the 7-slot one, and the tables the firmware links give every integer the generated
ones (bars_tables.h) hold: every field of every key of every type (the 8th mode slot reads as zero), every
BAR_POS column the code reads, and every BAR_POSG / BAR_TUBEG point of every band and family and every BAR_PRATE anchor through the row indexes."""
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GEN = os.path.join(ROOT, "firmware", "gen")
sys.path.insert(0, os.path.join(ROOT, "tools"))


def test_header_current():
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_bars_pack.py"), "--check"]).returncode == 0


def test_pack_equals_tables(tmp_path):
    bars_h = open(os.path.join(ROOT, "firmware", "src", "bars.h")).read()
    assert "rh_mode_t m[7];" in bars_h and "BAR_KM(k, i)" in bars_h and '#include "bars_pack.h"' in bars_h
    c = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct { int32_t sh, ch, th, d, drel, cr, b; } rh_mode_t;
#define RH_NANCH 10
typedef struct { rh_mode_t m[8]; int32_t k_bt, k_tb, wt, gain, kill; uint16_t life; uint8_t band, flags; } bar_key8_t;
typedef struct { rh_mode_t m[7]; int32_t k_bt, k_tb, wt, gain, kill; uint16_t life; uint8_t band, flags; } bar_key_t;
typedef struct { int16_t tube_cents, mot_gc, tors_db; uint8_t nbody, drive_row, sleep_rel, budget, flags, kn_tube_top; } bar_type_t;
#define bar_key_t bar_key8_t
#define BAR_MOT_COS BAR_MOT_COS_GEN
#include "bars_tables.h"
#undef bar_key_t
#undef BAR_MOT_COS
#include "bars_pack.h"
static const rh_mode_t bar_zm;
#define BAR_KM(k, i) ((i) < BAR_NMK ? &(k)->m[i] : &bar_zm)
int main(void)
{
    int bad = 0, t, k, m, f, p, b;
    for (t = 0; t < BAR_NTYPE; t++)
        for (k = 0; k < BAR_NKEY; k++) {
            const bar_key8_t *a = &BAR_KEYS[t][k];
            const bar_key_t *q = &BAR_KP[t][k];
            for (m = 0; m < BAR_NM; m++) bad += memcmp(&a->m[m], BAR_KM(q, m), sizeof(rh_mode_t)) != 0;
            bad += a->k_bt != q->k_bt || a->k_tb != q->k_tb || a->wt != q->wt || a->gain != q->gain || a->kill != q->kill
                || a->life != q->life || a->band != q->band || a->flags != q->flags;
        }
    for (b = 0; b < BAR_NBAND; b++)
        for (f = 0; f < BAR_NFAM; f++)
            for (p = 0; p < BAR_NPOS; p++)
                bad += BAR_POSG[b][f][p] != BAR_GROW[BAR_POSG_I[b][f]][p] || BAR_TUBEG[b][f][p] != BAR_GROW[BAR_TUBEG_I[b][f]][p];
    for (b = 0; b < BAR_NBAND; b++)
        for (p = 0; p < BAR_NPOS; p++)
            for (m = 0; m < BAR_NM; m++)       /* the columns the code reads; TUBE and X (w = 0) hold 0 and b 0 */
                bad += (m == 1 || m == 7) ? BAR_POS[b][p][m] != 0 : BAR_POS[b][p][m] != BAR_PS[b][p][m - (m > 1)];
    for (t = 0; t < BAR_NTYPE; t++)
        for (k = 0; k < BAR_NKEY; k++)
            for (f = 0; f < BAR_NFAM; f++)
                for (p = 0; p < RH_NANCH; p++) bad += BAR_PRATE[t][k][f][p] != BAR_PRU[BAR_PRATE_I[t][k][f]][p];
    printf("bad %d\n", bad);
    return bad != 0;
}
'''
    f = tmp_path / "bp.c"
    f.write_text(c)
    exe = tmp_path / "bp"
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-const-variable", "-I" + GEN, "-o", str(exe), str(f)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "bad 0", r.stdout
