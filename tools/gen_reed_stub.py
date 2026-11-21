#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""PLACEHOLDER tables for the WHURLI type (firmware/gen/reed_tables.h), in the interface format of
private/plans/whurli-interface.md, until the physics generator replaces this file.

The rows are the Rhodes engine's own (modes, offsets, gains, laws sampled at the REED's five velocity anchors, a
subsampled pickup curve of the keys' own family, the STAGE 77 body; keys 33..95: one pickup family): the REED path then plays about like STAGE 77, which proves the engine
reads these tables and not the Rhodes ones. Deterministic, integers; reads firmware/gen/rhodes_tables.h only.

  tools/gen_reed_stub.py          write firmware/gen/reed_tables.h
"""
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "firmware", "gen")
KEY_LO, NKEY, NANCH, NGRP, PU_N, PU_SH = 33, 63, 5, 4, 256, 47
RH_KEY_LO = 21
ANCH_V = [1, 32, 64, 96, 127]


def body(t, name):
    i = t.index(name)
    i = t.index("{", t.index("=", i))
    d, j = 0, i
    while True:
        d += (t[j] == "{") - (t[j] == "}")
        j += 1
        if d == 0:
            return t[i:j]


def ints(s):
    return [int(x) for x in re.findall(r"-?\d+", s)]


def main():
    t = open(os.path.join(GEN, "rhodes_tables.h")).read()
    keys = []
    for ln in body(t, "RH_KEYS[RH_NKEY]").split("\n"):
        m = re.match(r"\s*\{\{(.*)\}, (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (\d+), (\d+), (\d+), (\d+), 0xFF, 0\},?$", ln)
        if m:
            keys.append((m.group(1), [int(m.group(i)) for i in range(2, 11)]))
    assert len(keys) == 88
    anch = lambda name: [ints(r) for r in re.findall(r"\{([^{}]*)\}", body(t, name))]
    flat = lambda name: ints(body(t, name))
    pamp, pdrv, pgf = anch("RH_PAMP[RH_NKEY][RH_NANCH]"), anch("RH_PDRV[RH_NKEY][RH_NANCH]"), anch("RH_PGF[RH_NKEY][RH_NANCH]")
    prate, prate_b = anch("RH_PRATE[RH_NKEY][RH_NANCH]"), flat("RH_PRATE_B[RH_NKEY]")
    vi, vf = flat("RH_VPOS_I[128]"), flat("RH_VPOS_F[128]")
    phi, slope = flat("RH_PHI_1[RH_PU_N + 1]"), flat("RH_SLOPE_1[RH_PU_N + 1]")   # the 1/32" family (keys 33..95)
    rlc, rbody = ints(body(t, "RH_RLC[4][5]"))[:5], ints(body(t, "RH_BODY[4][2][5]"))[:10]
    off_max = int(re.search(r"#define RH_OFF_MAX \((-?\d+)\)", t).group(1))
    fam_sh = ints(body(t, "RH_FAM_SH[2]"))

    def law(row, v):
        i, f = vi[v], vf[v]
        return row[i] + (((row[i + 1] - row[i]) * f) >> 8)

    pulse_all = re.findall(r"\{([^{}]*)\}", body(t, "RH_PULSE[RH_NKEY][RH_NLAYER][RH_PLEN + 2]"))
    assert len(pulse_all) == 88 * 8
    layers = [0, 2, 4, 6, 7]                                     # the layers nearest the five anchors
    rows, prt, pmp, pgn, grp = [], [], [], [], []
    for n in range(KEY_LO, KEY_LO + NKEY):
        k = n - RH_KEY_LO
        modes, (off, gain, kill, hc, pk, pr, life, band, fam) = keys[k]
        assert fam == 1 and fam_sh[fam] == 0, n        # the stub range is the one pickup family's
        rows.append("    {{%s}, %d, %d, %d, 0, 0, 0, %d, 0, 0, 0xFF, 0}," % (modes, off, gain, kill, life))
        rb = prate_b[k]
        prt.append([min(65535, rb + law(prate[k], v)) for v in ANCH_V])
        pmp.append([max(1, min(32767, law(pdrv[k], v))) for v in ANCH_V])
        g = []
        for v in ANCH_V:
            q, d = law(pgf[k], v), max(1, law(pdrv[k], v))
            g.append(max(1, min(65535, (q << 15) // d)))
        pgn.append(g)
        grp.append((n - KEY_LO) * NGRP // NKEY)
    # RH prate is Q12 relative to ... (rh_load_pulse: (RH_PRATE_B + pr) << 4 is Q16): the same Q12 here
    for r in prt:
        assert all(0 < x < 65536 for x in r)
    pul = []
    for gi in range(NGRP):
        key = KEY_LO - RH_KEY_LO + gi * NKEY // NGRP + NKEY // NGRP // 2
        pul.append([pulse_all[key * 8 + layers[a]] for a in range(NANCH)])
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* PLACEHOLDER tables for the WHURLI type: tools/gen_reed_stub.py (the physics generator replaces this file). */",
         "#define RD_GEN_VERSION 1", "#define RD_KEY_LO %d" % KEY_LO, "#define RD_NKEY %d" % NKEY,
         "#define RD_NANCH %d" % NANCH, "#define RD_NGRP %d" % NGRP, "#define RD_PU_N %d" % PU_N, "#define RD_PU_SH %d" % PU_SH,
         "static const rh_key_t RD_KEYS[RD_NKEY] = {"] + rows + ["};"]
    for name, tab in (("RD_PRATE", prt), ("RD_PAMP", pmp), ("RD_PGAIN", pgn)):
        L.append("static const uint16_t %s[RD_NKEY][RD_NANCH] = {" % name)
        L += ["  {" + ",".join(map(str, r)) + "}," for r in tab]
        L.append("};")
    L.append("static const uint8_t RD_KGRP[RD_NKEY] = {" + ",".join(map(str, grp)) + "};")
    L.append("static const int16_t RD_PULSE[RD_NGRP][RD_NANCH][RH_PLEN + 2] = {")
    for g in pul:
        L.append("  {" + ",".join("{" + s + "}" for s in g) + "},")
    L.append("};")
    step = 2048 // PU_N
    pm = []
    for i in range(PU_N + 1):
        pm += [phi[i * step], slope[i * step] * step]
    L.append("static const int32_t RD_PHIM[2 * (RD_PU_N + 1)] = {" + ",".join(map(str, pm)) + "};")
    L.append("static const int32_t RD_RLC[5] = {" + ",".join(map(str, rlc)) + "};")
    L.append("static const int32_t RD_BODY[2][5] = {{" + ",".join(map(str, rbody[:5])) + "},{" + ",".join(map(str, rbody[5:])) + "}};")
    L.append("static const rh_type_t RD_TYPE = {0, 65536, 65536, 65536, 65536, 0, 0};")
    L.append("static const rd_lim_t RD_LIM = {131072, %d, 32767, 0};" % off_max)
    open(os.path.join(GEN, "reed_tables.h"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
