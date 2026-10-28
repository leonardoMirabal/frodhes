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
             + on + ["render 120", "get r_cmp_gr"] + off
             + sum((["render %d" % tick, "get r_cmp_gr"] for _ in range(60)), [])
             + on + ["render 120", "get r_cmp_gr", "set s_fx 1", "render 200", "get r_cmp_gr",
                     "set s_fx 0", "set p_cmp_sw 0", "render 20", "get r_cmp_gr"])
    v = run(lines)
    assert v[0] == "OFF" and v[-1] == "OFF", (v[0], v[-1])
    assert gr(v[1]) == (0, 0), v[1]                         # on, silent: no reduction
    db, pk = gr(v[2])
    assert 30 <= db <= 300, v[2]                            # a loud chord into THRSH 127, 20:1: several dB
    assert pk in GR_DB10 and pk <= db, v[2]                 # the peak: the segment the reading reached
    fall = [gr(x) for x in v[3:63]]
    for (d0, _), (d1, _) in zip([(db, pk)] + fall, fall):
        assert d1 <= d0 and (d0 - d1) % 5 == 0 and d0 - d1 <= 10, (d0, d1)   # 0.5 dB a tick (a read 0-2 ticks on)
    assert fall[-1][0] < db, fall[-1]
    held = [p for (_, p), i in zip(fall, range(len(fall))) if i * tick * BLK_MS < 900]
    assert all(p == pk for p in held), held                 # the peak holds about a second
    assert fall[-1][1] < pk or fall[-1][1] == 0, fall[-1]   # ... then follows the reading down
    assert gr(v[63])[0] > 0, v[63]
    assert gr(v[64]) == (0, 0) or gr(v[64])[0] < gr(v[63])[0], v[64]   # the FX button: the reduction reads 0
