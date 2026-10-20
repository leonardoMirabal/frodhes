# SPDX-License-Identifier: GPL-3.0-only
"""MARIMBA's short top tails (TEST-PLAN A5, the FM-1 jack's "C7 v8 MUTE 0" take, 2026-10-19): a top note ends by its
own decay (C7's V1 T60 is 0.11 s: a v8 note is at exact zero ~0.1 s after the strike, before the silence gate's
203 ms), never by its voice being freed while it still sounds. On the host build, every MARIMBA key 84..108 at v8..127,
MUTE 0 and 127, RAW and through the effects (the gate), and C7 / C8 at the cards' extremes: the last 1 ms before
the output's exact zero peaks at a few LSB (C8 at RING 0 falls 60 dB in ~11 ms). And hil_jack.gate_tail tells a cut
from a decay on a tail that never met the gate (end_peak_lsb; before it, a decayed tail passed whatever level it
stopped at, and a cut under 246 ms after the strike read as a gate: its window lay before the note)."""
import itertools
import os
import sys

import numpy as np
import pytest

import hostrun

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_jack as hj  # noqa: E402

FS = hostrun.FS
END_LSB = 4          # measured: <= 3 LSB (the voice ends under 1 LSB of V1 + tube; the effects add their own)


def tail(key, vel, mute, raw, knobs=(), end_ms=3000):
    ls = ["50 midi 0xB0 79 127", f"51 midi 0xB0 103 {mute}"] + [f"52 midi 0xB0 {c} {v}" for c, v in knobs]
    ls += [f"100 midi 0x90 {key} {vel}", f"300 midi 0x80 {key} 0"]
    if raw:
        ls.insert(0, "40 midi 0xB0 119 127")
    tag = f"mtail-{key}-{vel}-{mute}-{int(raw)}" + "".join(f"-{c}.{v}" for c, v in knobs)
    x = hostrun.run(tag, ls, end_ms=end_ms).L[int(0.1 * FS):]
    nz = np.flatnonzero(x != 0)
    assert len(nz) and nz[-1] < len(x) - FS // 2, (tag, "no exact zero inside the take")
    z = int(nz[-1]) + 1
    return z / FS, float(np.abs(x[max(0, z - FS // 1000):z]).max()), float(np.abs(x).max())


@pytest.mark.parametrize("raw", [1, 0])
@pytest.mark.parametrize("mute", [0, 127])
def test_top_keys_decay_to_zero(raw, mute):
    bad, rows = [], []
    for key in range(84, 109):
        for vel in (8, 30, 64, 100, 127):
            z, end, pk = tail(key, vel, mute, raw)
            rows.append((key, vel, round(z, 3), end, pk))
            if end > END_LSB:
                bad.append(rows[-1])
    print("worst (key, vel, zero_s, last 1 ms peak, peak):", sorted(rows, key=lambda r: -r[3])[:3])
    assert not bad, bad


def test_c7_c8_card_extremes_decay_to_zero():
    """TONE, BARK, BELL, HAMMER, RING (CC 28 29 30 31 102) at 0 / 127, RAW, MUTE 0: C7 and C8 at v8 and v127"""
    bad = []
    for vals in itertools.product((0, 127), repeat=5):
        knobs = tuple(zip((28, 29, 30, 31, 102), vals))
        for key in (96, 108):
            for vel in (8, 127):
                z, end, pk = tail(key, vel, 0, 1, knobs, end_ms=2000)
                if end > END_LSB:
                    bad.append((key, vel, knobs, z, end, pk))
    assert not bad, bad


def _take(env, cut_at=None, dur=3.0, t_note=0.1, f=2093.0, a=350.0, t60=0.111):
    """a USB take at hil_jack's FS (full scale 1.0) rounded to LSB like the FM-1's, and a jack (-13 dB + a -92 dBFS
    floor while USB is live, -92.4 when muted)"""
    fs = hj.FS
    t = np.arange(int(dur * fs)) / fs - t_note
    x = np.where(t >= 0, a * np.exp(-6.91 * np.maximum(t, 0) / t60) * np.sin(2 * np.pi * f * t), 0.0) if env else 0 * t
    if cut_at is not None:
        x[t >= cut_at] = 0
    u = np.round(x) / 32768
    rng = np.random.default_rng(1)
    j = u * 10 ** (-13 / 20) + np.where(u != 0, 10 ** (-92 / 20), 10 ** (-92.4 / 20)) * rng.standard_normal(len(u))
    return u, j


def test_gate_tail_tells_a_cut_from_a_decay():
    """the device's C7 v8 tail as gate_tail sees it: decayed (no gate), its pre-fade window over the strike, its hiss
    window over the note; ok on end_peak_lsb. The same note freed at ~210 LSB (8 ms) fails"""
    u, j = _take(True)
    r = hj.gate_tail(u, j, 0.1)
    assert r["kind"] == "decayed" and r["zero_s"] < 0.15, r
    assert r["pre_fade_peak_lsb"] > 100 and r["hiss_window_has_note"], r
    assert r["end_peak_lsb"] <= 16.5 and r["ok"], r
    u, j = _take(True, cut_at=0.008)
    r = hj.gate_tail(u, j, 0.1)
    assert r["kind"] == "decayed" and r["end_peak_lsb"] > 100 and not r["ok"], r
