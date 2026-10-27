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



def reference_from(x, taps, ph0):
    """test_uac_fir.reference with the resampler's phase at ph0 when the stream starts (rs.ph: whatever ran before)"""
    x = np.clip(x, -32768, 32767).astype(np.int64)
    hist, out, ph = np.zeros((12, 2), np.int64), [], ph0
    for f in x:
        hist = np.vstack([hist[1:], f])
        while ph < 160:
            c = taps[ph] if ph < 80 else taps[159 - ph][::-1]
            out.append(np.clip(((c[:, None] * hist).sum(axis=0) + (1 << 14)) >> 15, -32768, 32767))
            ph += 147
        ph -= 160
    return np.array(out)


def test_every_start_of_the_triangle_against_the_resampler_passes():
    """how the 160 frames fall on the triangle depends on the resampler's phase when it started: phase 4 (and 151)
    puts two equal frames on every turn, 7405 7603 7815 8011 8011 7815 7603, exactly the FM-1's take of 2026-10-26
    (--midi none: 181139 of them, slip 0, mismatch 0): the wave's own, so not counted, and the level is checked"""
    taps = F.fir()
    w = app_wave_from_the_firmware(147 * 60)
    flat = 0
    for ph0 in (0, 4, 37, 80, 151, 159):
        y = reference_from(w, taps, ph0)
        flat += int(np.all(y[301:] == y[300:-1], axis=1).sum())
        r = B.analyse_app(np.concatenate([np.zeros((300, 2), np.int64), y]), 48000)
        assert r["ok"] and not r["repeat"] and r["level_ok"], (ph0, r["repeat"][:3], r.get("peak"))
    assert flat > 0                                            # (the flat turns are among them)
    y = reference_from(w, taps, 4)
    z = np.insert(np.concatenate([np.zeros((300, 2), np.int64), y]), 3000, y[2699], axis=0)
    assert [sh for _, sh in B.analyse_app(z, 48000)["slip"]] == [1]   # a copy in that wave is still found


def test_the_report_carries_the_host_load(capsys):
    """the computer's 1-minute load before / after the recording, in the JSON and the printed report"""
    v = B.host_load()
    assert v is None or v >= 0
    js = dict(rate=44100, midi="none", minutes=1, bench=dict(frames=10, start=0.1, repeat=[], jump=[], lr=0, ok=True),
              load=[1.25, 2.5])
    B.print_report(js)
    assert "host load (1 min) before / after: 1.25 / 2.5" in capsys.readouterr().out
