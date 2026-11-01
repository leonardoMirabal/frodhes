# SPDX-License-Identifier: GPL-3.0-only
"""The packed pulse banks (tools/gen_pulse_pz.py -> firmware/gen/pulse_pz.h; rhodes.c rh_pz / rh_pz_mix): the header
is current, and the firmware's own decoder, compiled on the host, gives every pulse of RH_PULSE and BAR_PULSE back
exactly (the pads 0), and its crossfade the integers the old code computed from the raw table."""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_pulse_pz as gz  # noqa: E402


def test_header_current():
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_pulse_pz.py"), "--check"]).returncode == 0


def test_decoder_bit_exact(tmp_path):
    src = open(os.path.join(ROOT, "firmware", "src", "rhodes.c")).read()
    a = src.index("static const int16_t *rh_pz(")
    b = src.index("\n}\n", src.index("static void rh_pz_mix(")) + 3
    rp, bp = gz.bank(gz.RH, "RH_PULSE"), gz.bank(gz.BA, "BAR_PULSE")
    ws = (1, 7, 4096, 16384, 20000, 32767)
    c = ["#include <stdint.h>", "#include <stdio.h>",
         '#include "%s"' % os.path.abspath(str(gz.OUT)), src[a:b],
         "static const int16_t RP[%d][66] = {%s};" % (len(rp), ",".join("{" + ",".join(map(str, p)) + "}" for p in rp)),
         "static const int16_t BP[%d][66] = {%s};" % (len(bp), ",".join("{" + ",".join(map(str, p)) + "}" for p in bp)),
         "static const int32_t W[] = {%s};" % ",".join(map(str, ws)),
         """
static int check(const pz_bank_t *z, const int16_t (*P)[66], int n)
{
    int bad = 0, p, j, k;
    int16_t o[66];
    for (p = 0; p < n; p++) {
        rh_pz(o, z, p);
        for (j = 0; j < 66; j++) bad += o[j] != P[p][j];
        if (p + 1 < n)
            for (k = 0; k < (int)(sizeof W / sizeof W[0]); k++) {   /* the crossfade to the next pulse */
                rh_pz(o, z, p);
                rh_pz_mix(o, z, p + 1, W[k]);
                for (j = 0; j < 66; j++) {
                    int32_t x = P[p][j];
                    bad += o[j] != (int16_t)(x + (((P[p + 1][j] - x) * W[k]) >> 15));
                }
            }
    }
    return bad;
}
int main(void)
{
    int bad = check(&RH_PZB, RP, %d) + check(&BAR_PZB, BP, %d);
    int j, p;
    for (p = 0; p < 22; p++) for (j = 0; j < 66; j++) bad += BAR_THUMP[p][j] != BP[p * 40][j];
    printf("bad %%d\\n", bad);
    return bad != 0;
}
""" % (len(rp), len(bp))]
    f = tmp_path / "pz.c"
    f.write_text("\n".join(c))
    exe = tmp_path / "pz"
    subprocess.run(["cc", "-O1", "-Wall", "-o", str(exe), str(f)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "bad 0", r.stdout
