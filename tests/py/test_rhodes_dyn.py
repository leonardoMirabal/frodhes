# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes dynamics stage (rhodes.c rh_dyn, 0.9.10): a key x velocity gain at the strike.
- the soft end quieter: v20 -> v127 at least 5 dB wider than without the stage (0.9.9: 25.6 -> 31.4 dB), the curve
  without a step (v8..v64 every 8 rising);
- mf and f untouched below key 82 (the table is exactly 1 at v >= 60: the levels of the build without it, -DRH_NO_DYN);
- the top taper: key 96 at v64 4..8 dB under key 60;
- a re-strike takes the new strike's gain: a v127 re-strike on a ringing v20 note as loud as a fresh v127 strike
  (within 1 dB, or no further under it than without the stage), and a soft re-strike on a ringing loud note does not
  turn its tail down (no more than without it).
The engine alone (CC 119 RAW), RMS 50-500 ms after the note-on."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, HOST, build, run  # noqa: E402

NODYN = os.path.join(HOST, "frodhes-nodyn")


def lev(name, ev, t_on, end, binary=None):
    if binary:
        build(binary, ("-DRH_NO_DYN",))
    r = run(name, ["40 midi 0xB0 119 127"] + ev, end, **({"binary": binary} if binary else {}))
    x = (r.L.astype(float) + r.R.astype(float)) / 2.0
    s = x[int((t_on + 50) * FS / 1000):int((t_on + 500) * FS / 1000)]
    return 20 * np.log10(np.sqrt(np.mean(s * s)) / 32768.0 + 1e-12), x


def note(name, key, vel, binary=None):
    ev = [f"300 midi 0x90 {key} {vel}", f"1300 midi 0x80 {key} 0"]
    return lev(f"dyn-{name}-{key}-{vel}{'-n' if binary else ''}", ev, 300, 1500, binary)[0]


def test_soft_end_range_and_curve():
    for key in (40, 60):
        lv = [note("v", key, v) for v in (8, 16, 24, 32, 40, 48, 56, 64, 127)]
        wide = (lv[-1] - note("v", key, 20)) - (note("v", key, 127, NODYN) - note("v", key, 20, NODYN))
        assert wide >= 5.0, (key, wide)                              # the range ~6 dB wider than without the stage
        d = np.diff(lv[:-1])
        assert np.all(d > 0.8) and np.all(d < 5.0), (key, d)   # rising, no step


def test_mf_f_kept_soft_cut():
    for key in (40, 60):
        for v in (64, 96, 127):                                  # mf, f: exactly the levels without the stage
            assert abs(note("v", key, v) - note("v", key, v, NODYN)) < 0.05, (key, v)
        for v, lo, hi in ((20, -9.5, -8.0), (40, -5.2, -3.8)):   # the soft end's cut (0.9.10 FULL: -9 dB at v20, -4.5 at v40)
            d = note("v", key, v) - note("v", key, v, NODYN)
            assert lo <= d <= hi, (key, v, d)


def test_top_taper():
    d = note("t", 96, 64) - note("t", 60, 64)
    assert -8.0 <= d <= -4.0, d


PED = ["200 midi 0xB0 64 127"]                                  # the pedal down: the note rings after its key-up


def test_restrike_gain():
    fresh = note("r", 60, 127)
    soft_hard, _ = lev("dyn-rs-sh", PED + ["300 midi 0x90 60 20", "700 midi 0x80 60 0", "800 midi 0x90 60 127"], 800,
                       2000)
    sh_ref, _ = lev("dyn-rs-sh-n", PED + ["300 midi 0x90 60 20", "700 midi 0x80 60 0", "800 midi 0x90 60 127"], 800,
                    2000, NODYN)
    assert soft_hard >= min(fresh - 1.0, sh_ref - 0.5), (soft_hard, fresh, sh_ref)   # (or no worse than without it)
    hard_soft, _ = lev("dyn-rs-hs", PED + ["300 midi 0x90 60 127", "700 midi 0x80 60 0", "800 midi 0x90 60 20"], 800,
                       2000)
    ref, _ = lev("dyn-rs-hs-n", PED + ["300 midi 0x90 60 127", "700 midi 0x80 60 0", "800 midi 0x90 60 20"], 800,
                 2000, NODYN)
    assert hard_soft >= ref - 0.5, (hard_soft, ref)              # the tail not turned down more than without the stage
