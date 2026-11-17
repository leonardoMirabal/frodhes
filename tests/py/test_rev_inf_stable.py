# SPDX-License-Identifier: GPL-3.0-only
"""INFINITE at the full rate (firmware/src/rev_hq_inf.c: the c1 FM-1 build, the HQ builds: plugin, Move, MPC) stays
bounded and ends (0.9.13). In 0.9.12 a dark TONE at a low DECAY grew a v127 chord's tail to full scale and held it
there for good, OFF too: the line ends' damping pole (the half rate's largest c, its square root plus the full rate's
least damping) came to 17111 / 16384, over 1. On the firmware's own fx_reverb (tests/c/rev_scan.c, -DFR_REV_HQ=1):

- the sweep: a loud chord into INFINITE (DECAY 127 while it plays), then DECAY set to each value under TONE 0..127
  (SIZE longest and shortest): nothing above the level the input drove (x1.5 for a held tank's beating), and where
  the TYPE decays (DECAY under 120) the level falls, and a short DECAY ends in exact zeros (fx_quiet: saves go
  through);
- DECAY (CC 92) jumped about during the tail: bounded, and it ends;
- OFF: the tail ends in exact zeros, and INFINITE picked again afterwards starts from rest (silence in, silence out;
  a chord then renders as from a fresh start), as a TYPE change does.
FR_TREE=<tree> runs it on another tree (0.9.12 fails the sweep, the jumps and OFF)."""
import os
import sys
from multiprocessing import Pool

import numpy as np
import pytest

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import rev_scan as rs  # noqa: E402

TREE = os.environ.get("FR_TREE") or ROOT
FS = rs.FS
BLK = rs.BLK
NPROC = int(os.environ.get("PYTEST_N", "2") or 2)
SEC_IN = 2                       # the chord plays (and DECAY 127 holds) for this long
SEC = 10                         # the whole render
HOLD = 1.5                       # a held tank's peak over the input-driven peak (its beating; 0.9.12: x15 .. x30)
TONES = sorted(set(range(0, 33, 2)) | set(range(0, 128, 8)) | {127})
DECAYS = sorted(set(range(0, 41, 2)) | set(range(0, 128, 8)) | {119, 120, 126, 127})


def _chord():
    return rs.chord(FS * SEC_IN, amp=20000.0)


def _job(a):
    exe, ids, ton, dec, size, sw = a
    p = dict(RV_TYPE=3, RV_DEPTH=127, RV_TONE=ton, RV_DECAY=127, RV_SIZE=size)
    r = rs.render(exe, _chord(), p, switches=[(FS * SEC_IN // BLK, "RV_DECAY", dec), *sw],
                  blocks=FS * SEC // BLK, ids=ids)
    w = np.abs(r["wet"])
    drive = int(w[:FS * (SEC_IN + 1)].max())
    return dict(tone=ton, decay=dec, size=size, drive=drive, after=int(w[FS * (SEC_IN + 1):].max()),
                mid=int(w[FS * 5:FS * 6].max()), end=int(w[-FS:].max()), last=r["last"])


@pytest.fixture(scope="module")
def exe():
    if not rs.has_types(TREE):
        pytest.skip("no reverb TYPEs in this tree")
    e = rs.build(cflags=["-DFR_REV_HQ=1"], tag="inf-stable", tree=TREE)
    yield e, rs.fxp_ids(TREE)
    os.remove(e)


def _run(jobs):
    with Pool(NPROC) as p:
        return p.map(_job, jobs)


def _check(res):
    bad = []
    for r in res:
        if r["after"] > HOLD * r["drive"] + 64:
            bad.append(("over the input-driven level", r))
        elif r["decay"] < 120 and r["end"] > r["mid"] and r["end"] > 64:
            bad.append(("not decaying", r))
        elif r["decay"] <= 16 and r["last"] >= SEC * FS // BLK - 1:
            bad.append(("no exact zero by the end", r))
    return bad


def test_tone_decay_sweep_bounded_and_ends(exe):
    e, ids = exe
    jobs = [(e, ids, t, d, 127, ()) for t in TONES for d in DECAYS]
    jobs += [(e, ids, t, d, 0, ()) for t in range(0, 128, 16) for d in range(0, 128, 16)]
    bad = _check(_run(jobs))
    assert not bad, f"{len(bad)} of {len(jobs)}: {bad[:6]}"


def test_decay_jumps_during_the_tail(exe):
    e, ids = exe
    b0 = FS * SEC_IN // BLK
    step = FS // 2 // BLK
    seq = [0, 127, 0, 64, 127, 8, 120, 0]
    sw = tuple((b0 + (i + 1) * step, "RV_DECAY", v) for i, v in enumerate(seq))
    jobs = [(e, ids, t, 0, s, sw) for t in (0, 8, 16, 32, 64, 127) for s in (0, 127)]
    res = _run(jobs)
    bad = [r for r in res if r["after"] > HOLD * r["drive"] + 64 or r["last"] >= SEC * FS // BLK - 1]
    assert not bad, bad


def test_off_ends_and_infinite_restarts_from_rest(exe):
    e, ids = exe
    nb = FS * 16 // BLK
    b_dec, b_off, b_on = FS * SEC_IN // BLK, FS * 3 // BLK, FS * 9 // BLK
    for ton in (0, 16):
        p = dict(RV_TYPE=3, RV_DEPTH=127, RV_TONE=ton, RV_DECAY=127, RV_SIZE=127)
        x = np.zeros((nb * BLK, 2), np.int64)
        x[:FS * SEC_IN, :] = _chord()[:, None]
        c0 = (b_on + FS // BLK) * BLK                   # (the second chord a second after INFINITE is picked again)
        x[c0:c0 + FS * SEC_IN, :] = _chord()[:, None]
        r = rs.render(e, x, p, switches=[(b_dec, "RV_DECAY", 0), (b_off, "RV_TYPE", 0), (b_on, "RV_TYPE", 3)],
                      blocks=nb, ids=ids)
        w = r["wet"]
        assert not np.any(w[b_on * BLK - FS:c0]), f"TONE {ton}: OFF did not end the tail"
        y = x.copy()                                    # (the same moves without the first chord: nothing to keep)
        y[:FS * SEC_IN] = 0
        fresh = rs.render(e, y, p, switches=[(b_dec, "RV_DECAY", 0), (b_off, "RV_TYPE", 0), (b_on, "RV_TYPE", 3)],
                          blocks=nb, ids=ids)
        assert np.array_equal(w[b_on * BLK:], fresh["wet"][b_on * BLK:]), \
            f"TONE {ton}: INFINITE again did not start from rest"
