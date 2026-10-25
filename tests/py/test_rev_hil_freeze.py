# SPDX-License-Identifier: GPL-3.0-only
"""tools/final/rev_hil.py's freeze take (rhodes-infinite-freeze60) on the host, with the USB stream on (T usbrec 1):

  - the take freezes INFINITE (DECAY 127) while its chord rings, never in the set-up: FREEZE closes the tank's input,
    so a freeze set before the notes holds nothing (the 0.9.5 final run: 60 s of exact zeros, freeze_ok passed);
  - the hold keeps a real level (every 5 s window over -50 dBFS, within +-1 dB) and is never silent, so the settings'
    autosave (2 s of silence after the set-up's changes) does not write the flash mid-hold (the final run's missed
    3-4 / overruns 2 came from such a save, then a blocking erase; the split erase, fm1_flash.h, keeps it clean now);
  - the old take (DECAY 127 in the set-up) fails both: silent, and the settings saved while the stream ran."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "final"))
import hil_crackle as C  # noqa: E402
import hil_scan  # noqa: E402
import rev_hil  # noqa: E402

FS = C.FS_HOST


@pytest.fixture(scope="module")
def takes():
    got = {}
    orig, argv = C.main, sys.argv
    try:
        C.main = lambda args: got.update(T=C.takes("rhodes")) or 0
        sys.argv = ["rev_hil.py", "jack", "--dry-run", "--rate", "44100"]
        rev_hil.main()
    finally:
        C.main, sys.argv = orig, argv
    return {n: (c, e, d) for n, c, e, d in got["T"]}


def play(ccs, events, dur):
    """the take as hil_crackle.host_take plays it, with the USB stream on; (left channel LSB, dirty at the end)"""
    H = hil_scan.hr()
    lines = ["1 usbrec 1"] + [f"{5 + k // 16} midi 0xB0 {c} {v}" for k, (c, v) in enumerate(ccs)]
    R = 5 + len(ccs) // 16 + 300
    lines.append(f"{R - 60} midi 0xB0 {hil_scan.RESET_CC} 127")
    for t, kind, a, b in events:
        ms = int(round(R + 1000 * float(t)))
        lines.append(f"{ms} midi 0x90 {a} {b}" if kind == "on" else f"{ms} midi 0x80 {a} 0" if kind == "off"
                     else f"{ms} midi 0xB0 {a} {b}")
    end = R + int(np.ceil(1000 * dur))
    lines.append(f"{end} expect state dirty 1")
    r = H.run("rev_hil_freeze", lines, end + 20, check=False)
    assert "expect state dirty" in r.out, r.out[-400:]
    return np.asarray(r.L[int(R * FS / 1000):], float), "1" if "ok   expect state dirty" in r.out else "0"


def hold_db(u, music_end):
    h = u[int((music_end + 2.0) * FS):]
    return [rev_hil.db(h[i:i + 5 * FS]) for i in range(0, len(h) - 5 * FS + 1, 5 * FS)]


def test_freeze_goes_on_while_the_chord_rings(takes):
    ccs, ev, _ = takes["rhodes-infinite-freeze60"]
    dk = rev_hil.reverb()[2]["RV_DECAY"]
    assert (dk, 127) not in ccs, "DECAY 127 in the set-up: FREEZE closes the input before any note"
    tf = [t for t, k, a, b in ev if k == "cc" and a == dk and b == 127]
    assert len(tf) == 1
    assert any(t < tf[0] for t, k, *_ in ev if k == "on") and any(t > tf[0] for t, k, *_ in ev if k == "off")


def test_no_other_take_freezes_in_its_set_up(takes):
    dk = rev_hil.reverb()[2]["RV_DECAY"]
    for n, (ccs, ev, d) in takes.items():
        assert (dk, 127) not in ccs or not n.startswith("rhodes-inf"), n


def test_the_freeze_holds_and_nothing_saves(takes):
    ccs, ev, dur = takes["rhodes-infinite-freeze60"]
    u, dirty = play(ccs, ev, dur)
    w = hold_db(u, dur - 60.0)                        # (rev_hil's judge: from 2 s after the music's end)
    assert len(w) >= 10
    assert min(w) >= rev_hil.FREEZE_MIN_DB, f"the held freeze under -50 dBFS: {[round(x - 90.3, 1) for x in w]}"
    assert max(w) - min(w) <= 2.0, f"the held freeze not within +-1 dB: {[round(x - 90.3, 1) for x in w]}"
    assert dirty == "1", "the settings autosave ran while the USB stream was on"


def test_the_old_take_fails(takes):
    """the 0.9.5 final run's take: DECAY 127 in the set-up (and no freeze after the chord)"""
    ccs, ev, dur = takes["rhodes-infinite-freeze60"]
    dk = rev_hil.reverb()[2]["RV_DECAY"]
    ev = [e for e in ev if not (e[1] == "cc" and e[2] == dk)]
    u, dirty = play(ccs + [(dk, 127)], ev, dur)
    w = hold_db(u, dur - 60.0)
    assert max(w) < rev_hil.FREEZE_MIN_DB, "the old take held a level: the test has no teeth"
    assert dirty == "0", "the old take did not autosave in its silence: the counters' cause is not reproduced"
