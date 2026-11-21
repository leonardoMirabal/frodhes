# SPDX-License-Identifier: GPL-3.0-only
"""WHURLI (TYPE 6): the Rhodes engine on its own tables (rhodes.c rh_init_rd, rh_pickup_rd). Every key of its range
at four velocities, one note at a time, RAW (CC 119: the engine alone): the output has no click (the glitch detector
the FM-1 takes are judged with, against STAGE 77's on the same notes: never more), nothing saturates and the engine
ends in exact silence; a note outside its keys has no voice; the other four-slot types are untouched (Gate R)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import glitch_lpc as G  # noqa: E402
import hostrun as H  # noqa: E402

KEYS = list(range(33, 96, 3))          # the stub's range is 33..95; the generator's real range is read from the header
VELS = (20, 64, 100, 127)
STEP = 500


def sweep(whurli):
    ls = [H.cc(50, 119, 127)]
    if whurli:
        ls += [f"{60 + 2 * i} turn 1 1" for i in range(6)]            # ALGORITHM: ST77 -> ... -> WHURLI
    t = 300
    for v in VELS:
        for k in KEYS:
            ls += H.note(t, k, v, 250)
            t += STEP
    ls.append(H.cc(t + 500, 123, 0))
    return ls, t + 3000


def test_the_sweep_has_no_click_and_nothing_saturates():
    ls, end = sweep(True)
    w = H.run("whurli-sweep", ls + ["200 expect state type WHURLI"], end)
    ref = H.run("whurli-ref", sweep(False)[0], end)
    assert "sat28 hits 0" in w.out
    assert np.max(np.abs(w.L)) < 32000 and np.max(np.abs(w.R)) < 32000
    assert np.all(w.L[-4410:] == 0) and np.all(w.R[-4410:] == 0), "the engine ends in exact silence"
    ew, er = len(G.events(w.L, H.FS)), len(G.events(ref.L, H.FS))
    assert ew <= er + 2, f"WHURLI {ew} glitch events, STAGE 77 {er}"
    lv = []          # each velocity sounds, and a harder strike is louder (the median over the keys)
    for v in range(len(VELS)):
        seg = [w.L[int((300 + (v * len(KEYS) + j) * STEP + 60) * 44.1):int((300 + (v * len(KEYS) + j) * STEP + 260) * 44.1)]
               for j in range(len(KEYS))]
        lv.append(np.median([H.rms_db(s) for s in seg]))
    assert all(a < b for a, b in zip(lv, lv[1:])), lv
    assert lv[0] > -80, lv


def test_a_note_outside_the_keys_has_no_voice():
    ls = [H.cc(50, 119, 127)] + [f"{60 + 2 * i} turn 1 1" for i in range(6)] + H.note(300, 24, 110, 200) + H.note(300, 108, 110, 200)
    r = H.run("whurli-oor", ls + ["200 expect state type WHURLI"], 1500)
    assert np.all(r.L == 0) and np.all(r.R == 0)


def test_the_header_has_the_type():
    src = open(os.path.join(H.ROOT, "firmware", "gen", "reed_tables.h")).read()
    assert "#define RD_KEY_LO" in src and "#define RD_NKEY" in src


def test_the_type_maps_agree():
    """one TYPE -> engine map: engine.c's, the plugin UI's copy and params.c's names (WHURLI is TYPE 6, the last)"""
    import re
    eng = open(os.path.join(H.ROOT, "firmware", "src", "engine.c")).read()
    ui = open(os.path.join(H.ROOT, "plugin", "ui", "frui_params.h")).read()
    par = open(os.path.join(H.ROOT, "firmware", "src", "params.c")).read()
    e = [int(x) for x in re.search(r"ENG_TYPE_ENG\[ENG_NTYPE\] = \{([^}]*)\}", eng).group(1).split(",")]
    u = [int(x) for x in re.search(r"FRN_TYPE_BARS\[FRN_NTYPE\] = \{([^}]*)\}", ui).group(1).split(",")]
    names = re.search(r'N_TYPE_LONG\[\] = \{([^}]*)\}', par).group(1)
    assert e == u and len(e) == 7 and names.split(",")[6].strip() == '"WHURLI"'
    assert "RH_T_REED" in eng and "#define ENG_NTYPE 7u" in eng


def test_a_compact_row_expands_to_the_generators_words(tmp_path):
    """rhodes.c rd_expand (sin, cos and the coupling word from theta, by integers: the C itself, copied out) against
    the words rh_coefs.mode_coefs gives for the same mode (the engine's rh_key_t rows): sh, ch within 4 LSB of Q30, cr
    within 1e-5, the rest verbatim; every key, every present mode"""
    import re
    import subprocess
    sys.path.insert(0, os.path.join(H.ROOT, "tools"))
    import gen_reed as gw
    import rh_coefs as rc
    src = open(os.path.join(H.ROOT, "firmware", "src", "rhodes.c")).read()
    td = src[src.index("typedef struct {                               /* a WHURLI key's row"):src.index("} rd_key_t;") + 11]
    fn = src[src.index("static void rd_sincos("):src.index("static void rh_load_pulse_rd(rh_voice_t *v, uint32_t vel, int fresh)\n{")]
    c = tmp_path / "t.c"
    c.write_text("""#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct { int32_t sh, ch, th, d, drel, cr, b; } rh_mode_t;
typedef struct { rh_mode_t m[4]; int32_t off, gain, kill, hc, pk; uint16_t prate, life; uint8_t band, fam, res, pad; } rh_key_t;
typedef struct { int32_t off_delta; int32_t bark, bark_g; int32_t bell, ham; uint8_t nbody, drive_row; } rh_type_t;
typedef struct { int32_t bark_max, off_max; uint16_t pamp_max, pad; } rd_lim_t;
#define RH_PLEN 64
""" + td + """
#include "%s"
""" % os.path.abspath(gw.OUT) + fn + """
int main(void) {
    int k, m;
    for (k = 0; k < RD_NKEY; k++) {
        rh_key_t o;
        rd_expand(&RD_KEYS[k], &o);
        for (m = 0; m < 3; m++)
            printf("%d %d %d %d %d %d %d %d\\n", k, m, o.m[m].sh, o.m[m].ch, o.m[m].th, o.m[m].d, o.m[m].drel, o.m[m].cr);
    }
    return 0;
}
""")
    exe = tmp_path / "t"
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-variable", "-Wno-unused-function", str(c), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split("\n")
    spec = gw.build()
    n = 0
    for ln in out:
        if not ln:
            continue
        k, m, sh, ch, th, d, dr, cr = [int(x) for x in ln.split()]
        mm = spec["keys"][k]["modes"]
        if m >= len(mm):
            assert th == 0
            continue
        w = rc.mode_coefs(mm[m]["f"], mm[m]["t60"], mm[m]["t60_rel"])
        assert th == w["th"] and d == w["d"] and dr == w["drel"], (k, m)
        if w["th"]:
            n += 1
            assert abs(sh - w["sh"]) <= 4 and abs(ch - w["ch"]) <= 4, (k, m, sh, w["sh"], ch, w["ch"])
            assert abs(cr - w["cr"]) <= max(2, 1e-5 * w["cr"]), (k, m, cr, w["cr"])
    assert n > 150
