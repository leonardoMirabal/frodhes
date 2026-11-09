# SPDX-License-Identifier: GPL-3.0-only
"""tools/final/rev_hil.py's freeze take (rhodes-infinite-freeze60) on the host, with the USB stream on (T usbrec 1):

  - the take freezes INFINITE (DECAY 127) while its chord rings, never in the set-up: FREEZE closes the tank's input,
    so a freeze set before the notes holds nothing (the 0.9.5 final run: 60 s of exact zeros, freeze_ok passed);
  - the hold keeps a real level (every 5 s window over -50 dBFS, its spread within the chord's own frozen by the bare
    reverb + 0.5 dB) and is never silent, so the settings'
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
import rev_scan as rs  # noqa: E402

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


# THRESHOLD (0.9.10, coordinator sign-off 2026-11-09): the hold's spread judged against the material's, not a fixed
# 2.0 dB. The 0.9.10 chord's held tank beats more slowly than 0.9.9's: 2.02 dB here against 1.573 on v0.9.9 for the
# same take. tests/c/rev_scan.c on v0.9.9's tree and on this one, fed the same input, gives identical windows (the
# INFINITE tank is unchanged); v0.9.9's dry chord frozen spreads 1.515 dB, this tree's 1.93, through either reverb.
# So the reference is the same chord frozen by the bare reverb (rev_scan, this tree's fx_reverb, the take's reverb
# settings, the dry take as its input): the take's spread <= the reference's + 0.5 dB over the same windows.
SPREAD_MARGIN = 0.5


def dry_take(ccs, events, dur):
    """the take with the reverb's TYPE OFF (the reverb's input; left channel LSB)"""
    H = hil_scan.hr()
    lines = [f"{5 + k // 16} midi 0xB0 {c} {v}" for k, (c, v) in enumerate(ccs + [(rev_hil.reverb()[0], 0)])]
    R = 5 + (len(ccs) + 1) // 16 + 300
    lines.append(f"{R - 60} midi 0xB0 {hil_scan.RESET_CC} 127")
    for t, kind, a, b in events:
        ms = int(round(R + 1000 * float(t)))
        if kind in ("on", "off"):
            lines.append(f"{ms} midi 0x90 {a} {b}" if kind == "on" else f"{ms} midi 0x80 {a} 0")
    end = R + int(np.ceil(1000 * dur))
    r = H.run("rev_hil_freeze_dry", lines, end + 20, check=False)
    return np.asarray(r.L[int(R * FS / 1000):], float)


def reference_db(ccs, events, dur):
    """the dry take frozen by the bare reverb at the take's freeze time: its hold windows (dB re 1 LSB)"""
    knob = rev_hil.reverb()[2]
    cc = dict(ccs)
    tf = [t for t, k, a, b in events if k == "cc" and a == knob["RV_DECAY"] and b == 127][0]
    par = {n: cc[c] for n, c in knob.items() if c in cc}
    par["RV_TYPE"] = rs.TYPES["INFINITE"]
    exe = rs.build(tag="freeze-ref")
    try:
        x = np.round(dry_take(ccs, events, dur)).astype(np.int64)
        w = rs.render(exe, x, par, [(int(tf * FS) // rs.BLK, "RV_DECAY", 127)])["wet"][:, 0].astype(float)
    finally:
        os.remove(exe)
    return hold_db(w, dur - 60.0)


def judge(w, ref):
    """(ok, why): every window over -50 dBFS, the spread within the material's (the reference's) + SPREAD_MARGIN"""
    if min(w) < rev_hil.FREEZE_MIN_DB:
        return False, f"the held freeze under -50 dBFS: {[round(x - 90.3, 1) for x in w]}"
    s, sr = max(w) - min(w), max(ref) - min(ref)
    if s > sr + SPREAD_MARGIN:
        return False, f"the held freeze spreads {s:.2f} dB, the chord's own freeze {sr:.2f} + {SPREAD_MARGIN}"
    return True, ""


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
    ref = reference_db(ccs, ev, dur)
    assert len(ref) == len(w)
    ok, why = judge(w, ref)
    assert ok, why
    assert dirty == "1", "the settings autosave ran while the USB stream was on"
    # the judge's teeth: the same hold with a tank that loses 3 dB over it (a freeze that decays) fails
    n = len(w)
    ok, _ = judge([x - 3.0 * i / (n - 1) for i, x in enumerate(w)], ref)
    assert not ok, "a freeze decaying 3 dB over the hold passed the judge"


def test_the_old_take_fails(takes):
    """the 0.9.5 final run's take: DECAY 127 in the set-up (and no freeze after the chord)"""
    ccs, ev, dur = takes["rhodes-infinite-freeze60"]
    dk = rev_hil.reverb()[2]["RV_DECAY"]
    ev = [e for e in ev if not (e[1] == "cc" and e[2] == dk)]
    u, dirty = play(ccs + [(dk, 127)], ev, dur)
    w = hold_db(u, dur - 60.0)
    assert max(w) < rev_hil.FREEZE_MIN_DB, "the old take held a level: the test has no teeth"
    assert dirty == "0", "the old take did not autosave in its silence: the counters' cause is not reproduced"
