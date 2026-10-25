# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA's output pitch (0.9.5 fix): every key's f0 at v100 (mrb_ref_measure's: the FFT peak of the ring from 20 ms)
within +-1 cent of ET. Before the fix the strongly coupled bar / tube pair's lower normal mode pulled the low keys'
FFT peak flat (A0 -5.3 c, C1 -4.2, B1 -2.7, C2 -1.3, C#2 -1.05): gen_bars.v1_target centred the modes' energy-weighted
mean, not the peak the ear and the tuner read. The trims (docs/model/data/marimba_pitch_pins.json) stop under C4."""
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_bars as gb  # noqa: E402
import marimba_va as mva  # noqa: E402

TOL_C = 1.0
HOST_KEYS = (21, 28, 35, 36, 37, 48, 60, 84)


def cents(f, n):
    return 1200 * math.log2(f / gb.midi_hz(n))


def test_golden_output_pitch_every_key():
    spec = mva.load_spec()
    keys = list(range(gb.KEY_LO, gb.KEY_HI + 1))
    ys = mva.render(spec, keys, [100] * len(keys), 1.5, None)
    bad = [(n, round(cents(gb.output_f0(y, n), n), 2)) for n, y in zip(keys, ys)
           if abs(cents(gb.output_f0(y, n), n)) > TOL_C]
    assert not bad, bad


def test_host_output_pitch():
    """the C engine (host build, RAW, NOISE 0) on the low keys and a few above"""
    import bar_measure as bm
    bm.build()
    out = os.path.join(ROOT, "build", "test_marimba_pitch")
    bad = []
    for n in HOST_KEYS:
        ev = bm.setup_events(typ=5, raw=True, noise=0) + bm.note(100, n, 100)
        x, _ = bm.render("p%d" % n, ev, 100 + 1500, out)
        c = cents(gb.output_f0(bm.mono(x)[int(0.1 * gb.FS):], n), n)
        if abs(c) > TOL_C:
            bad.append((n, round(c, 2)))
    assert not bad, bad


def test_pitch_trims_low_keys_only():
    """the trims are data for keys under C4 only, a few cents at most (the higher keys' words never move)"""
    tr = gb.pitch_trims()
    assert tr and all(gb.KEY_LO <= n < gb.PITCH_TRIM_HI for n in tr), sorted(tr)
    assert all(abs(c) <= 8.0 for c in tr.values()), tr
    d = json.load(open(gb.MAR_PITCH_PINS))
    assert d["version"] == 1 and d["_doc"]


def test_vibes_target_untouched():
    """VIBES's V1 target stays f0 (its tables byte-identical)"""
    vb = gb.load_vibes()
    assert gb.v1_target(vb, dict(f0=gb.midi_hz(40), bt=None, wt=None)) == gb.midi_hz(40)
