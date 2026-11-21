# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes' packed level rows (tools/gen_rh_pack.py -> firmware/gen/rh_pack.h; rhodes.c RH_MK_U / RH_VMK_U / RH_TW_U):
the header is current, rhodes.c reads the packed tables, and through the row indexes they give every integer of
RH_DRV_MK, RH_VM_MK and RH_TONE_W (the generated headers the calibrators write)."""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GEN = os.path.join(ROOT, "firmware", "gen")


def test_header_current():
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_rh_pack.py"), "--check"]).returncode == 0


def test_rhodes_reads_the_packed_rows():
    src = open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()
    assert '#include "rh_pack.h"' in src
    assert "RH_DRV_MK[" not in src and "RH_VM_MK[" not in src and "RH_TONE_W[" not in src   # (the contract, not linked)
    for use in ("RH_MK_U[RH_DRV_MK_I[", "RH_VMK_U[RH_VM_MK_I[", "RH_TW_U[RH_TONE_W_I["):
        assert use in src, use


def test_pack_equals_tables(tmp_path):
    rt = open(os.path.join(GEN, "rhodes_tables.h")).read()
    nkey = int(re.search(r"#define RH_NKEY (\d+)", rt).group(1))
    nanch = int(re.search(r"#define RH_NANCH (\d+)", rt).group(1))
    mkn = int(re.search(r"#define RH_MK_N (\d+)", open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()).group(1))
    c = r'''
#include <stdint.h>
#include <stdio.h>
#define RH_NKEY %d
#define RH_NANCH %d
#define RH_MK_N %d
#include "rh_drive_mk.h"
#include "rh_velmap_mk.h"
#include "rh_tone_g.h"
#include "rh_pack.h"
int main(void)
{
    int bad = 0, t, k, i;
    for (t = 0; t < 4; t++)
        for (k = 0; k < RH_NKEY; k++) {
            for (i = 0; i < RH_MK_N; i++) bad += RH_DRV_MK[t][k][i] != RH_MK_U[RH_DRV_MK_I[t][k]][i];
            for (i = 0; i < RH_NANCH; i++) bad += RH_TONE_W[t][k][i] != RH_TW_U[RH_TONE_W_I[t][k]][i];
        }
    for (k = 0; k < RH_NKEY; k++)
        for (i = 0; i < RH_MK_N; i++) bad += RH_VM_MK[k][i] != RH_VMK_U[RH_VM_MK_I[k]][i];
    printf("bad %%d\n", bad);
    return bad != 0;
}
''' % (nkey, nanch, mkn)
    f = tmp_path / "rp.c"
    f.write_text(c)
    exe = tmp_path / "rp"
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-const-variable", "-I" + GEN, "-o", str(exe), str(f)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "bad 0", r.stdout
