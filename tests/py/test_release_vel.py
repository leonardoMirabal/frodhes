# SPDX-License-Identifier: GPL-3.0-only
"""Release velocity (S5, rhodes.c rh_rel_len / rh_rel_thump) and the damper settle per register (S6, rh_settle).

A MIDI note-off's velocity reaches the Rhodes (notes.c src_off_v -> engine.c eng_note_off -> rhodes.c rh_events):
- none given (0, a note-on at 0) and 64 are the neutral release: byte-identical to each other (Gate R's renders all
  send 0);
- a quick lift (127) damps faster and harder than a slow one (1): 100-150 ms after the key-up the tone (re the same
  note held) is about 7 dB under the neutral release's on a quick lift, 7 dB over it on a slow one (C3, C4, C5, pedal
  up; RELEASE at its default);
- the knock (NOISE's thump) is louder on a quick lift, softer on a slow one;
- no click: hil_scan's detector finds none, and what the release adds above 8 kHz (the release render minus the same
  note held, high-passed) stays under -50 dBFS on every key and release speed (NOISE 0: the damper alone);
- VIBES and MARIMBA ignore it (byte-identical at 1 and 127).
The engine alone (CC 119 RAW)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, run  # noqa: E402

T_ON, T_OFF, END = 100, 700, 1100
NOTES = {"C3": 48, "C4": 60, "C5": 72}
RAW = "40 midi 0xB0 119 127"


def take(tag, n, rv, noise=None, engine=None, off=True, vel=100):
    ev = [RAW, f"{T_ON} midi 0x90 {n} {vel}"]
    if engine is not None:
        ev.insert(0, f"30 midi 0xB0 79 {engine}")
    if noise is not None:
        ev.insert(0, f"35 midi 0xB0 104 {noise}")
    if off:
        ev.append(f"{T_OFF} midi 0x80 {n} {rv}")
    return run(f"relvel-{tag}", ev, END)


def keyup(a, b):
    """the first sample where two takes differ (the key-up reaches the engine at a block boundary)"""
    d = np.nonzero(a.L != b.L)[0]
    return int(d[0]) if len(d) else None


def hp(x, fc=8000.0):
    """an FFT brick-wall high-pass (no scipy needed)"""
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(x), 1 / FS) < fc] = 0
    return np.fft.irfft(X, len(x))


def clicks(x):
    """tools/hil_scan.py clicks(): second-difference spikes far above the local level"""
    d2 = np.abs(np.diff(x, 2))
    lvl = np.convolve(np.abs(x), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def ms(i):
    return int(i * FS / 1000)


_cache = {}


def cached(*a, **k):
    key = (a, tuple(sorted(k.items())))
    if key not in _cache:
        tag = "-".join(str(x) for x in a[1:]) + "".join(f"-{kk}{vv}" for kk, vv in sorted(k.items()))
        _cache[key] = take(f"{a[0]}-{tag}", *a[1:], **k)
    return _cache[key]


@pytest.mark.parametrize("name", list(NOTES))
def test_neutral_is_none(name):
    n = NOTES[name]
    a, b = cached(name, n, 0), cached(name, n, 64)
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R), f"{name}: release velocity 64 != none"


@pytest.mark.parametrize("name", list(NOTES))
def test_quick_lift_damps_faster(name):
    """the level 100-150 ms after the key-up: 127 under 64 under 1 by >= 4 dB (NOISE 0: the damper alone)"""
    n = NOTES[name]
    held = cached(name, n, 0, noise=0, off=False)
    r = {rv: cached(name, n, rv, noise=0) for rv in (1, 64, 127)}
    k = keyup(r[64], held)
    assert k is not None
    e = {rv: float(np.sum(t.L[k + ms(100):k + ms(150)] ** 2)) for rv, t in r.items()}
    db = {rv: 10 * np.log10(v / e[64]) for rv, v in e.items()}
    print(f"{name}: 100-150 ms after the key-up, re 64: slow {db[1]:+.2f} dB, quick {db[127]:+.2f} dB")
    assert db[127] < -4.0 and db[1] > 4.0, db


@pytest.mark.parametrize("name", list(NOTES))
def test_quick_lift_knocks_louder(name):
    """the knock (default NOISE minus NOISE 0, the same release): 127 louder than 64, 1 softer"""
    n = NOTES[name]
    knock = {}
    for rv in (1, 64, 127):
        a, b = cached(name, n, rv), cached(name, n, rv, noise=0)
        d = a.L - b.L
        knock[rv] = 10 * np.log10(np.sum(d ** 2) + 1e-9)
    print(f"{name}: knock re 64: slow {knock[1] - knock[64]:+.2f} dB, quick {knock[127] - knock[64]:+.2f} dB")
    assert knock[127] > knock[64] + 0.5 and knock[1] < knock[64] - 0.5, knock


@pytest.mark.parametrize("name", list(NOTES))
@pytest.mark.parametrize("rv", [1, 64, 127])
def test_no_click(name, rv):
    n = NOTES[name]
    held = cached(name, n, 0, noise=0, off=False)
    t = cached(name, n, rv, noise=0)
    k = keyup(t, held)
    assert k is not None
    add = hp(t.L - held.L)[k - ms(5):k + ms(40)]
    peak = 20 * np.log10(np.abs(add).max() / 32767 + 1e-12)
    full = cached(name, n, rv)
    c = clicks(t.L) + clicks(full.L)
    print(f"{name} rv {rv}: the release's content above 8 kHz peaks at {peak:.1f} dBFS; clicks {c}")
    assert peak < -50.0 and c == 0


@pytest.mark.parametrize("engine", [0, 127])
def test_bars_ignore_it(engine):
    a, b = take(f"bar{engine}-1", 60, 1, engine=engine), take(f"bar{engine}-127", 60, 127, engine=engine)
    assert np.array_equal(a.L, b.L) and np.array_equal(a.R, b.R)
