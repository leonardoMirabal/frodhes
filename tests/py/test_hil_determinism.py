# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py's classic suites give the same verdicts twice (no FM-1): run on the host build through HostRig,
each take a fresh host run that replays the panel, idles a random 0.3-1.3 s (the LFOs run on, as on the device
between takes) and plays the take 3-25 ms late with +-1 ms of jitter per event (the note lands in another audio
block). Two seeds = two device runs: with CC 10 before every take the verdicts match and the numbers agree within
the spreads the file header states; without it they do not (the test is not vacuous)."""
import contextlib
import io
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

NAMES = ["types", "presets", "effects", "bypass", "fx_all", "fx_toggle", "delay_time", "tremolo_rate", "sustain",
         "held_tail", "stress", "silence"]
# measure group -> the largest difference allowed between two runs (dB / LU; host: <= 0.09 seen)
TOL = {"types": 0.15, "preset": 0.15, "effects": 0.25, "fx_all": 0.25, "toggle": 0.25, "bypass": 0.25}


def metrics(rep):
    m = {f"types {k}": v for k, v in enumerate(rep["types_distance_db"])}
    m.update({f"types level {t}": d["level"] for t, d in rep["types"].items()})
    m.update({f"preset {q['preset']}": q["level"] for q in rep["presets"]})
    m.update({f"effects {q['name']}": q["delta_lu"] for q in rep["effects"]["rows"]})
    m.update({f"bypass {k}": v for k, v in rep["bypass"].items()})
    m.update({f"fx_all {q['name']}": q["usb_lu"] for q in rep["fx_all"]["rows"]})
    m["fx_all bypass"] = rep["fx_all"]["bypass_lu"]
    for q in rep["fx_toggle"]:
        m[f"toggle {q['name']} on"], m[f"toggle {q['name']} off"] = q["on_step"], q["off_step"]
    return m


_cache = {}


def scan(seed, reset):
    if (seed, reset) not in _cache:
        r, rep, ver = h.HostRig(seed, reset=reset, tag="det"), {}, {}
        with contextlib.redirect_stdout(io.StringIO()):
            for n in NAMES:
                ver[n] = h.TESTS[n](r, rep)[0]
        _cache[(seed, reset)] = ver, metrics(rep)
    return _cache[(seed, reset)]


def test_two_runs_same_verdicts_and_numbers():
    va, ma = scan(1, True)
    vb, mb = scan(2, True)
    assert va == vb, {n: (va[n], vb[n]) for n in va if va[n] != vb[n]}
    bad = {k: round(abs(ma[k] - mb[k]), 3) for k in ma if abs(ma[k] - mb[k]) > TOL[k.split()[0]]}
    assert not bad, bad


def test_without_the_reset_the_runs_differ():
    _, ma = scan(1, False)
    _, mb = scan(2, False)
    assert max(abs(ma[k] - mb[k]) for k in ma if k.startswith("preset")) > 1.0


@pytest.mark.parametrize("notes, lim", [((57, 64, 69), None), (h.CH_NOTES, 0.25), (h.FX_NOTES, 0.25)],
                         ids=["octave", "CHORD", "fx_all"])
def test_a_chord_note_one_block_late(notes, lim):
    """the classic chords hold no octave: a note one block late moves an octave chord's loudness by up to 2 LU
    (its coinciding partials' phase), the chosen chords by <= 0.25 LU"""
    H = h.hr()
    worst = 0.0
    for pc in (0, 5, 10):                                 # F01, F06 (2.0 LU with A3 E4 A4), F11
        lv = []
        for late in [None] + list(notes):
            ev = [f"5 midi 0xC0 {pc}", "796 midi 0xB0 10 127"] + \
                 [f"{847 + (6 if n == late else 0)} midi 0x90 {n} 100" for n in notes] + \
                 [f"2047 midi 0x80 {n} 0" for n in notes]
            r = H.run(f"det-late-{pc}-{late}-{len(notes)}", ev, 2400)
            x = np.stack([r.L, r.R], 1)[int(0.8 * H.FS):]
            i = int(np.argmax(np.abs(x).max(1) > 200))
            lv.append(h.lufs(x[i:i + int(1.1 * H.FS)] / 32768.0))
        worst = max(worst, max(abs(v - lv[0]) for v in lv))
    if lim is None:
        assert worst > 1.0, worst
    else:
        assert worst <= lim, worst
