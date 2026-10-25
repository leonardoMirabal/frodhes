# SPDX-License-Identifier: GPL-3.0-only
"""rhodes.c's inaudible-mode gate (rh_mode_runs, the engine CPU study 2026-10-24): the tone-bar mode V2 of keys
29..48 (a 28-104 Hz partial, 3-4 LSB through the pickup at v127) never runs.

Since the O1 tables (params.json v2_drop: tools/gen_rhodes.py leaves V2 out of keys 21-39 and 85-99; 40-84 hold
the beat partner, and the gate ends at 39) the gated keys have no V2 at all, so the gate stops nothing any more: the gate's build and the -DRH_NO_GATE build must now play
byte-identically on every key, and the saving is the tables'. (The gate's own inaudibility evidence against the
0.9.5 tables: this file's history, tools/rh_gate_scan.py; the tables' null test: tools/rh_nulltest.py.)

The engine alone (tests/c/rh_gate.c: rhodes.c, its Q15 output before the effects) with the gate and with
-DRH_NO_GATE (every mode runs: the engine as before), compared with tools/engine_ab.py's metrics. Pinned: which modes
the gate stops, the saving (9 bass notes v127 + pedal: 27 modes, not 35), that nothing else moves, and the
inaudibility bounds of the residual. The whole grid (every key x 6 velocities x TYPE x held / released / pedal, the
knob ends on every gated key) is tools/rh_gate_scan.py; the full chain (effects, presets, Gate R's renders)
tools/engine_ab.py."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402
from engine_ab import compare  # noqa: E402

OUT = os.path.join(ROOT, "build", "rh_gate", os.environ.get("PYTEST_XDIST_WORKER", "main"))
GATED = range(29, 40)                    # keys whose V2 the gate stops (29..48 until the whirl on 40-48)
BASS9 = [28, 31, 33, 35, 36, 38, 40, 41, 43]   # tools/hil_cpu.py's worst case at 9 voices
V9 = ["-DRH_VOICES=9u"]


def binary(tag, src, defs=()):
    exe = os.path.join(OUT, f"{src}_{tag}")

    def make(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", *defs,
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", path, os.path.join(ROOT, "tests", "c", src + ".c")], check=True)
    return hostrun.once(exe, make)


@pytest.fixture(scope="module")
def bins():
    return binary("on", "rh_gate", V9), binary("off", "rh_gate", V9 + ["-DRH_NO_GATE"])


def render(exe, note, vel, typ=0, bark=64, bell=64, tone=64, ham=64, decay=64, rel=64, off=-1, pedal=0, blocks=520,
           chord=()):
    p = subprocess.run([exe, "render", *map(str, (note, vel, typ, bark, bell, tone, ham, decay, rel, off, pedal, blocks)),
                        *map(str, chord)], capture_output=True, check=True)
    st = p.stderr.split()
    return p.stdout, int(st[2]), int(st[4])


def test_masks(bins):
    """the gate and the no-gate build run the same modes on every key: keys 21..48 and 85..99 have no V2 (O1)"""
    on = subprocess.run([bins[0], "masks"], capture_output=True, text=True, check=True).stdout.split("\n")
    off = subprocess.run([bins[1], "masks"], capture_output=True, text=True, check=True).stdout.split("\n")
    assert len(on) == len(off) == 89
    assert on == off
    for a in on[:-1]:
        n, g = map(int, a.split())
        if 21 <= n <= 39 or 85 <= n <= 99:
            assert not g & 2, a


def test_saving(bins):
    """9 bass notes v127 with the pedal (hil_cpu's worst case at 9 voices): 30 live modes per block with or
    without the gate (key 28 has 3 modes, the others 4: V1, O1, O2 and no V2), the same output, the whole 3 s"""
    on, n_on, s_on = render(bins[0], BASS9[0], 127, pedal=1, off=200, chord=BASS9[1:])
    off, n_off, s_off = render(bins[1], BASS9[0], 127, pedal=1, off=200, chord=BASS9[1:])
    assert (n_off, n_on) == (30, 30) and s_off == s_on and on == off


def grid():
    for n in range(21, 109):
        for t in range(4):
            for scen in ("held", "release", "pedal"):
                yield dict(note=n, vel=127, typ=t, scen=scen)
    for n in GATED:
        for v in (1, 20, 64, 100):
            yield dict(note=n, vel=v, typ=n % 4, scen="pedal")


SCEN = {"held": dict(off=-1, pedal=0), "release": dict(off=86, pedal=0), "pedal": dict(off=86, pedal=1)}


def run(bins, c, **kn):
    a = dict(SCEN[c["scen"]], **kn)
    on = render(bins[0], c["note"], c["vel"], c["typ"], **a)[0]
    off = render(bins[1], c["note"], c["vel"], c["typ"], **a)[0]
    if on == off:
        return c, None
    return c, compare(np.frombuffer(off, dtype=np.int32).astype(float), np.frombuffer(on, dtype=np.int32).astype(float))


def test_identical_default_knobs(bins):
    """every key at v127 x TYPE x held / released / pedal, and keys 29..39 at other velocities: byte-identical"""
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda c: run(bins, c), grid()))
    for c, r in res:
        assert r is None, (c, r)


def test_identical_knob_extremes(bins):
    """the voicing knobs at their ends on keys 29..39: byte-identical"""
    kns = ({"bark": 0}, {"bark": 127}, {"bell": 127}, {"tone": 0}, {"tone": 127}, {"ham": 0}, {"ham": 127},
           {"decay": 127}, {"rel": 127}, {"bark": 127, "bell": 127, "tone": 127}, {"bark": 0, "tone": 0})
    cases = [(dict(note=n, vel=127, typ=t, scen=s), kn) for n in range(29, 40, 2) for t in range(4)
             for s in ("held", "pedal") for kn in kns]
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda ck: run(bins, ck[0], **ck[1]) + (ck[1],), cases))
    for c, r, kn in res:
        assert r is None, (c, kn, r)


def test_cost_model():
    """the saving in instructions per block (tests/c/rh_cost.c: the FM-1 build's loop counts, tools/build.py's hot
    loops; 4.4 ns an instruction): 9 bass notes v127 with the pedal at 9 voices, the attack block (hil_cpu's worst
    case). With the 0.9.5 tables: 449,056 -> 365,505 (-83,551, ~368 us; the gate 56,032, the split force loop
    27,519). With the O1 tables there is no V2 on these keys for the gate to stop: 27 live modes either way (until the whirl
    on 40-48, below), the old
    loops 393,888, 0.9.5 RC 8d913af's (-DRH_COST_RC) 366,307 (the split force loop: at v127 no layer mix, 20 per
    sample, not 39; +802 over the unjittered tables: the per-key contact-rate jitter lengthens some pulses); the held
    blocks 292,608 (the 0.9.5 tables without the gate: 337,664, 8 V2 loops x 256 x 22 more). This tree (O2 the layers
    pre-mixed at the strike, O6 the felt noise in a pass of its own, O9 no rh_x zero pass, the mode loops out of line;
    O3 / O5 / O14 the pickup on the interleaved table, 32-bit multipliers, rh_pickup / rh_mode_* noinline): attack
    366,307 -> 305,413 (-60,894), held 292,608 -> 242,568 (-50,040); with S4 (the bass strike's E per sub-block, 48
    a first mode with kap: every note here is below key 48) 308,869 and 246,024. Since the whirl on keys 40-48
    (RH_V2_GATE_HI 39, params.json whirl.keys [40, 84]) keys 40, 41 and 43 here run a V2 again: 30 live modes, the
    0.9.5-RC loops 386,787 / 309,504, this tree 328,141 / 262,224 (+19,272 at the attack, ~85 us)"""
    def cost(tag, defs):
        exe = binary(tag, "rh_cost", V9 + defs)
        out = subprocess.run([exe, "6", str(BASS9[0]), "127", *map(str, BASS9[1:])],
                             capture_output=True, text=True, check=True).stdout.split("\n")
        return [tuple(map(int, ln.split())) for ln in out if ln and not ln.startswith("max")]
    old, new = cost("old", ["-DRH_NO_GATE", "-DRH_COST_OLD"]), cost("new", ["-DRH_COST_RC"])
    gate, split = cost("gate", ["-DRH_COST_OLD"]), cost("split", ["-DRH_NO_GATE", "-DRH_COST_RC"])
    tree = cost("tree", [])
    assert [b[1] for b in old][:5] == [240] * 5 and [b[1] for b in new][:5] == [240] * 5   # 30 modes x 8
    assert [b[1] for b in tree] == [b[1] for b in new]                                   # the same modes run
    assert max(b[2] for b in old) == 414368 and max(b[2] for b in new) == 386787
    assert max(b[2] for b in gate) == 414368 and max(b[2] for b in split) == 386787
    assert old[1][2] == new[1][2] == 309504
    assert max(b[2] for b in tree) == 328141 and tree[1][2] == 262224
    assert (449056 - max(b[2] for b in tree)) * 4.4e-3 >= 300                         # us re 0.9.4's, the target
