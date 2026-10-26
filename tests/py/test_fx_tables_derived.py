# SPDX-License-Identifier: GPL-3.0-only
"""The tables the firmware derives instead of storing (0.9.5, the flash): KN_P64I(v) and KN_P32(v) (fx_tables.h
macros, mirrors of KN_P64 and rhodes_tables.h DECAY_K; KN_P64(v) and FX_EXP2F(i), 0.9.6) give the numbers the stored tables had; the compressor's
narrowed FX_CMP_ATK / FX_CMP_REL hold the same values as before."""
import math
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FS = 44100.0


def tab(name, f):
    s = open(os.path.join(ROOT, "firmware", "gen", f)).read()
    m = re.search(r"\b" + name + r"\[[^\]]*\] = \{(.*?)\};", s, re.S)
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def test_kn_mirrors():
    lo, e2, dk = tab("KN_P64_LO", "fx_tables.h"), tab("FX_EXP2F_O", "fx_tables.h"), tab("DECAY_K", "rhodes_tables.h")
    p64 = [lo[v] if v < 64 else 65536 + e2[(v - 64) << 2] for v in range(128)]   # KN_P64(v) (0.9.6: from FX_EXP2F)
    assert p64 == [int(round(65536 * 2 ** ((v - 64) / 64))) for v in range(128)]
    assert [65536 + x for x in e2] == [int(round(65536 * 2 ** (i / 256))) for i in range(256)]   # FX_EXP2F(i)
    for v in range(128):
        p64i = p64[128 - v] if v else 131072
        p32 = dk[128 - v] if v else 16384
        assert p64i == int(round(65536 / 2 ** ((v - 64) / 64))), v
        assert p32 == int(round(65536 * 2 ** ((v - 64) / 32))), v
    h = open(os.path.join(ROOT, "firmware", "gen", "fx_tables.h")).read()
    assert "#define KN_P64(v) ((uint32_t)(v) >= 64u ? FX_EXP2F(((uint32_t)(v) - 64u) << 2) : (uint32_t)KN_P64_LO[v])" in h
    assert "#define FX_EXP2F(i) ((uint32_t)FX_EXP2F_O[i] + 65536u)" in h
    assert "#define KN_P64I(v) ((v) ? KN_P64(128u - (uint32_t)(v)) : 131072u)" in h
    assert "#define KN_P32(v) ((v) ? DECAY_K[128u - (uint32_t)(v)] : 16384u)" in h


def test_cmp_narrowed():
    atk, rel = tab("FX_CMP_ATK", "fx_tables.h"), tab("FX_CMP_REL", "fx_tables.h")
    assert atk == [max(1, int(round(65536 * (1 - math.exp(-1 / (0.0001 * 500 ** (v / 127) * FS)))))) for v in range(128)]
    assert rel == [max(1, int(round(65536 * (1 - math.exp(-1 / (0.020 * 75 ** (v / 127) * FS)))))) for v in range(128)]
    assert max(atk) < 65536 and max(rel) < 256


def test_bar_mot_cos_is_the_sine():
    """bars.h: BAR_MOT_COS = FX_SINE + 256 (the generated BAR_MOT_COS_GEN is not linked)"""
    sine, cos = tab("FX_SINE", "fx_tables.h"), tab("BAR_MOT_COS", "bars_tables.h")
    assert len(cos) == 257 and sine[256:513] == cos
    assert "#define BAR_MOT_COS (FX_SINE + 256)" in open(os.path.join(ROOT, "firmware", "src", "bars.h")).read()
