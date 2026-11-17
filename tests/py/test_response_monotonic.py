# SPDX-License-Identifier: GPL-3.0-only
"""RESPONSE (CC 40) keeps the level rising with the velocity (0.9.13). B is a light-touch level map: the soft end
louder, the span smaller, the tone the same, and a harder strike never softer. 0.9.10 to 0.9.12 lifted the bars'
soft end by the electric piano's old FULL soft end (+9 dB at v20, 0 from v60), which fell faster than the bars'
own level rises: under B, VIBES came out up to 1.3 dB softer from v33 to v41 (the device scan: -1.6 dB), and
MARIMBA 0.7 dB softer. bars.c bar_lt_g now lifts 0.07 dB per velocity step under v127 (7 and below as 7).

Every TYPE (the four electric pianos, VIBES, MARIMBA), A and B, keys across each range, v1..v127 in steps of 8 (and
v127), the engine alone (CC 119 RAW), the motor off (its tremolo is no part of the strike's level): the level of
each note (20 ms before its note-on to 600 ms after) never falls from one velocity to the next. B's span (v1 to
v127) is smaller than A's, by 6..12 dB. On the two-core host build (the FM-1), the one-core build (the SMK-37) and
the HQ build (the Move, the plugin, the MPC). FR_BIN=<host binary> runs it on another build (0.9.12's fails)."""
import os
import sys
from multiprocessing import Pool

import numpy as np
import pytest

import hostrun
from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import monkey as mk  # noqa: E402

NPROC = int(os.environ.get("PYTEST_N", "2") or 2)
VELS = tuple(range(1, 128, 8)) + (127,)
RH_KEYS = (33, 45, 57, 69, 81, 93)
TYPES = (("ST77", "80 0", RH_KEYS), ("SUIT", "80 32", RH_KEYS), ("FELT", "80 64", RH_KEYS),
         ("DYNO", "80 96", RH_KEYS), ("VIBES", "79 0", (53, 61, 69, 77, 89)),
         ("MARIMBA", "79 127", (45, 53, 65, 77, 89, 96)))
STEP, HOLD = 1900, 700                   # ms: a note every STEP, released after HOLD


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-9)


def _ladder(a):
    binary, tcc, key, resp = a
    ev = [(40, "midi 0xB0 119 127"), (50, f"midi 0xB0 40 {resp}"), (60, f"midi 0xB0 {tcc}"), (70, "midi 0xB0 47 0")]
    ts = [1000 + i * STEP for i in range(len(VELS))]
    for t, v in zip(ts, VELS):
        ev += [(t, f"midi 0x90 {key} {v}"), (t + HOLD, f"midi 0x80 {key} 0")]
    r = mk.run(mk.script_text(ev, [], ts[-1] + 2000), binary)
    return [db(r.x[mk.ms(t - 20):mk.ms(t + 600)]) for t in ts]


@pytest.fixture(scope="module", params=["c1", "fm1", "hq"])
def binary(request):
    if os.environ.get("FR_BIN"):
        return os.environ["FR_BIN"]
    if request.param == "fm1":
        return mk.build()
    if request.param == "hq":
        b = os.path.join(ROOT, "build", "host-hq", "frodhes")
        hostrun.subprocess.run(["make", "-C", ROOT, "build/host-hq/frodhes"], check=True, capture_output=True)
        return b
    return hostrun.build(os.path.join(ROOT, "build", "host-c1-silence", "frodhes"), ["-O2", "-DFR_C1=1",
                                                                                      "-DFR_C1_LAT=1"])


def test_response_level_rises_with_velocity(binary):
    jobs = [(binary, tcc, k, resp) for _, tcc, ks in TYPES for k in ks for resp in (0, 127)]
    with Pool(NPROC) as p:
        res = p.map(_ladder, jobs)
    lv = {(tcc, k, resp): L for (_, tcc, k, resp), L in zip(jobs, res)}
    bad, spans = [], []
    for name, tcc, ks in TYPES:
        sa, sb = [], []
        for k in ks:
            for resp in (0, 127):
                L = lv[(tcc, k, resp)]
                d = np.diff(L)
                if d.min() < 0:
                    i = int(np.argmin(d))
                    bad.append(f"{name} {'AB'[resp > 63]} key {k}: v{VELS[i]} -> v{VELS[i + 1]} {d[i]:+.2f} dB")
                (sa if resp < 64 else sb).append(L[-1] - L[0])
        cut = float(np.mean(sa) - np.mean(sb))
        spans.append(f"{name} A {np.mean(sa):.1f} B {np.mean(sb):.1f}")
        if not 6.0 <= cut <= 12.0:
            bad.append(f"{name}: B's span {np.mean(sb):.1f} dB against A's {np.mean(sa):.1f} (cut {cut:.1f}, 6..12)")
    assert not bad, f"{len(bad)} failures ({binary}): {bad[:8]}; spans {spans}"
