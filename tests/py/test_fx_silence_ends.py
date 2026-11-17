# SPDX-License-Identifier: GPL-3.0-only
"""A tail ends in exact silence and fx_quiet comes (flash saves wait for it), on the one-core and the two-core host
builds (0.9.13). In 0.9.12 the FILTER at a low cutoff held its state in the rounding's dead band after the notes
ended (a3 v3 rounded to 0: ic[1] never moved), up to +-19 LSB of DC that TREM then moved at its rate, for ever: no
silence, no fx_quiet, no save. A v127 chord through every filter TYPE at FREQ 0 / 3, RESO 0 / 127, with TREM, then
all notes off: 30 s later the output is exact zeros and the dump says quiet. FR_BIN=<host binary> runs it on another
build (0.9.12's fails)."""
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
NOTES = (24, 36, 48, 55, 60, 64, 67, 72, 84, 96)
Q0, Q1 = 31700, 33700                    # the window checked (ms): 30 s after all notes off


def cc(t, n, v):
    return (t, f"midi 0xB0 {n} {v}")


def configs():
    out = []
    for typ in (20, 40, 60, 80, 100, 120):              # (CC 70: LP12 LP18 HP12 HP18 BP NOTCH)
        for fq in (0, 3):
            for rs in (0, 127):
                for tw in (64, 110):
                    out.append((f"filter type {typ} freq {fq} reso {rs} trem {tw}",
                                [cc(100, 70, typ), cc(101, 74, fq), cc(102, 71, rs), cc(103, 17, tw), cc(104, 18, 127),
                                 cc(105, 91, 0)]))
    return out


def _job(a):
    binary, (name, setup) = a
    ev = [cc(50, 111, 0)] + setup
    ev += [(600 + i, f"midi 0x90 {n} 127") for i, n in enumerate(NOTES)]
    ev += [(1600, f"midi 0x80 {n} 0") for n in NOTES] + [cc(1700, 123, 0)]
    r = mk.run(mk.script_text(ev, [(Q1, "dump")], Q1 + 10), binary)
    seg = r.x[mk.ms(Q0):mk.ms(Q1)]
    return name, int(np.abs(seg).max()) if len(seg) else -1, r.dumps.get(Q1, {}).get("quiet")


@pytest.fixture(scope="module", params=["fm1", "c1"])
def binary(request):
    if os.environ.get("FR_BIN"):
        return os.environ["FR_BIN"]
    if request.param == "fm1":
        return mk.build()
    return hostrun.build(os.path.join(ROOT, "build", "host-c1-silence", "frodhes"), ["-O2", "-DFR_C1=1",
                                                                                      "-DFR_C1_LAT=1"])


def test_filter_tails_end_in_silence(binary):
    with Pool(NPROC) as p:
        res = p.map(_job, [(binary, c) for c in configs()])
    bad = [r for r in res if r[1] != 0 or r[2] != "1"]
    assert not bad, f"{len(bad)} of {len(res)} not silent / not quiet: {bad[:6]}"
