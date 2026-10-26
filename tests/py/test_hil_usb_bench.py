# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_usb_bench.py without the FM-1 (BUGHUNT #24): its self-test, and the app bench's wave (frodhes.c
app_audio under FELUCCA_UAC_TONE=2: triangles of period 147 frames) through usb.c's resampler (tests/py/test_uac_fir.py's
reference, bit for bit the firmware's): exactly periodic with 160 frames at 48 kHz, no two neighbouring frames equal,
so the analysis passes it clean and finds an underrun's copy and a lost frame in it."""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import hil_usb_bench as B  # noqa: E402
import test_uac_fir as F  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_self_test():
    assert B.self_test() == 0


def app_wave_from_the_firmware(n):
    """the bench's triangle as frodhes.c writes it (its constants read from the source)"""
    src = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()
    m = re.search(r"#if FELUCCA_UAC_TONE == 2.*?tph == (\d+)u.*?\(tph < (\d+)u \? tph : (\d+)u - tph\) \* (\d+) - (\d+);",
                  src, re.S)
    assert m, "the app bench's triangle in frodhes.c"
    top, half, per, k, off = (int(v) for v in m.groups())
    assert top + 1 == per == 147
    ph = np.arange(n) % per
    v = np.where(ph < half, ph, per - ph) * k - off
    return np.stack([v, -v], 1)


def test_the_app_bench_through_the_resampler():
    y = F.reference(app_wave_from_the_firmware(147 * 300), F.fir())
    x = np.concatenate([np.zeros((500, 2), np.int64), y])
    r = B.analyse_app(x, 48000)
    assert r["ok"] and r["mismatch"] == 0, r
    z = np.insert(x, 20000, x[19999], axis=0)                 # an underrun's copy
    z = np.delete(z, [35000], axis=0)                         # a lost frame
    r = B.analyse_app(z, 48000)
    assert [sh for _, sh in r["slip"]] == [1, -1] and [n for _, n in r["repeat"]] == [2], r
