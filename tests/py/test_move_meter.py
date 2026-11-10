# SPDX-License-Identifier: GPL-3.0-only
"""The compressor's gain-reduction meter through the plugin (dsp.so, move/tests/test_bridge.c script): r_cmp_gr is
"OFF" while COMP is off, else "<dB x 10> <the held peak, dB x 10>" with the FM-1's meter ballistics (ui.c
meter_poll): it rises at once, falls 0.5 dB per 33 ms tick (15 dB/s), clamps at 30 dB, and the peak (on ui.c's
segment scale GR_DB10) holds for a second. The FX button (bypass) reads 0. Frodhes' screen draws it on the
COMPRESSOR page (move/ui/test_screen.mjs)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_move_params as tmp  # noqa: E402

EXT = 2
BLK_MS = 128 * 1000 / 44100
GR_DB10 = (10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 180, 200, 240, 270, 300)
CHORD = (36, 43, 48, 52, 55, 60, 64, 67, 72, 76)


def run(lines):
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join(lines) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    out = [ln.split("\t") for ln in r.stdout.splitlines() if "\t" in ln]
    assert out, r.stdout[-2000:] + r.stderr[-2000:]
    return [v for k, v in out if k == "r_cmp_gr"]


def gr(v):
    a, b = v.split()
    return int(a), int(b)


def test_meter_reads_the_fm1_ballistics():
    on = [f"midi {EXT} {0x90} {n} 127" for n in CHORD]
    off = [f"midi {EXT} {0x80} {n} 0" for n in CHORD]
    tick = max(1, round(33 / BLK_MS))                      # about one meter tick between two reads
    lines = (["set p_cmp_sw 0", "render 20", "get r_cmp_gr", "set p_cmp_sw 1", "set p_cmp_amt 127", "set p_cmp_ratio 7",
              "render 40", "get r_cmp_gr"]
             + on + ["render 30", "get r_cmp_gr"] + off   # THRESHOLD (stage 2): read at 30 blocks, not 120 (below)
             + sum((["render %d" % tick, "get r_cmp_gr"] for _ in range(60)), [])
             + ["set p_cmp_amt 96"]       # 2026-11-08: under the 30 dB clamp (0.9.10's louder chord read 300 both sides)
             + on + ["render 120", "get r_cmp_gr", "set s_fx 0", "render 200", "get r_cmp_gr",
                     "set s_fx 1", "set p_cmp_sw 0", "render 20", "get r_cmp_gr"])
    v = run(lines)
    assert v[0] == "OFF" and v[-1] == "OFF", (v[0], v[-1])
    assert gr(v[1]) == (0, 0), v[1]                         # on, silent: no reduction
    db, pk = gr(v[2])
    assert 30 <= db <= 300, v[2]                            # a loud chord into THRSH 127, 20:1: several dB
    assert pk in GR_DB10 and pk <= db, v[2]                 # the peak: the segment the reading reached
    # THRESHOLD (stage 2, 2026-11-10): the first read 120 -> 30 blocks after the chord. The compressor's adaptive release
    # lets the reduction follow the chord's strike down: the reading clamps at 30 dB for ~40 blocks, then falls at
    # the meter's 0.5 dB a tick (26.5 dB at 120 blocks under its held 30 dB peak), as the meter should
    fall = [gr(x) for x in v[3:63]]
    for (d0, _), (d1, _) in zip([(db, pk)] + fall, fall):
        assert d1 <= d0 and d0 - d1 <= 10, (d0, d1)     # at most 0.5 dB a tick (a read 0-2 ticks on; less when the
        #                                                 reduction itself is falling slower, as the HQ chord's at 30 dB)
    assert fall[-1][0] < db, fall[-1]
    held = [p for (_, p), i in zip(fall, range(len(fall))) if i * tick * BLK_MS < 900]
    assert all(p == pk for p in held), held                 # the peak holds about a second
    assert fall[-1][1] < pk or fall[-1][1] == 0, fall[-1]   # ... then follows the reading down
    assert gr(v[63])[0] > 0, v[63]
    assert gr(v[64]) == (0, 0) or gr(v[64])[0] < gr(v[63])[0], v[64]   # the FX button: the reduction reads 0


def run_all(lines):
    r = tmp._bridge("script", "build/move/frodhes/dsp.so", stdin="\n".join(lines) + "\n")
    if r is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    out = [ln.split("\t") for ln in r.stdout.splitlines() if "\t" in ln]
    assert out, r.stdout[-2000:] + r.stderr[-2000:]
    return out


def test_grid_cells_read_the_meter():
    """Schwung's knob grid (COMPRESSOR knobs 7-8, move/module/canvas.js): r_cmp_gr is "live", so the grid reads
    r_cmp_gr:effective every tick; it is r_cmp_gr itself. r_cmp_pk, the cell beside it: the held peak alone, or OFF"""
    on = [f"midi {EXT} {0x90} {n} 127" for n in CHORD]
    off = [f"midi {EXT} {0x80} {n} 0" for n in CHORD]
    probe = ["get r_cmp_gr", "get r_cmp_gr:effective", "get r_cmp_pk"]
    lines = (["set p_cmp_sw 0", "render 20"] + probe + ["set p_cmp_sw 1", "set p_cmp_amt 127", "set p_cmp_ratio 7",
             "render 40"] + probe + on + ["render 120"] + probe + off
             + sum((["render 12"] + probe for _ in range(10)), []) + ["set p_cmp_sw 0", "render 20"] + probe)
    out = run_all(lines)
    trip = [out[i:i + 3] for i in range(0, len(out), 3)]
    assert len(trip) == 14, out
    for (k0, g), (k1, e), (k2, p) in trip:
        assert (k0, k1, k2) == ("r_cmp_gr", "r_cmp_gr:effective", "r_cmp_pk"), (k0, k1, k2)
        assert e == g, (g, e)
        assert p == ("OFF" if g == "OFF" else g.split()[1]), (g, p)
    assert trip[0][0][1] == "OFF" and trip[-1][0][1] == "OFF"
    assert gr(trip[2][0][1])[0] >= 30, trip[2]
