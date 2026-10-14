# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic tests for tools/rhodes_dsp.py: the analysers must recover known frequencies and decays
(f to 0.1 cent, T60 to 1 %, also at 40 dB SNR) before they are trusted on real recordings.

Run: .local/venv/bin/python -m pytest tests/py   (or: make pytest)"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import rhodes_dsp as d  # noqa: E402

FS = 44100


def decaying(f, t60, dur, amp=0.5, phase=0.3, fs=FS):
    t = np.arange(int(dur * fs)) / fs
    return amp * np.exp(-6.907755 * t / t60) * np.sin(2 * np.pi * f * t + phase)


def test_inst_f_exact_to_0p1_cent():
    for f in (41.2, 110.0, 440.0, 2637.0):
        x = decaying(f, 10.0, 2.0)
        z = d.band(x, FS, f, 0.15)
        est = d.inst_f(z, FS, 0.3, 1.7)
        assert abs(d.cents(est, f)) < 0.1, (f, est)


def test_inst_f_with_noise_40db():
    rng = np.random.default_rng(1)
    f = 82.4
    x = decaying(f, 8.0, 3.0)
    x += rng.normal(0, 0.5 / np.sqrt(2) * 10 ** (-40 / 20), len(x))
    est = d.inst_f(d.band(x, FS, f, 0.15), FS, 0.3, 2.5)
    assert abs(d.cents(est, f)) < 0.1


def test_single_slope_t60_1pct():
    for f, t60 in ((55.0, 18.0), (440.0, 5.0), (2093.0, 1.5)):
        dur = min(0.75 * t60 + 0.5, 14.0)
        x = decaying(f, t60, dur)
        e = d.env_db(d.band(x, FS, f, 0.15), FS)
        r = d.decay_fit(e, FS, bottom_db=40)
        assert abs(r["t60_late"] / t60 - 1) < 0.01, (f, t60, r)


def test_two_stage_decay_recovered():
    f = 110.0
    t = np.arange(int(8 * FS)) / FS
    # fast mode (T60 1 s) 6 dB above a slow mode (T60 10 s), slightly detuned so they do not beat audibly in 10 ms
    x = 0.5 * np.exp(-6.9078 * t / 1.0) * np.sin(2 * np.pi * f * t) \
        + 0.25 * np.exp(-6.9078 * t / 10.0) * np.sin(2 * np.pi * f * 1.0002 * t)
    e = d.env_db(d.band(x, FS, f, 0.1), FS, win=0.05)
    r = d.decay_fit(e, FS, top_db=1, bottom_db=40)
    assert r["break_db"] is not None
    assert r["t60_early"] < r["t60_late"]
    assert abs(r["t60_late"] / 10.0 - 1) < 0.05, r


def test_noise_floor_stops_fit():
    rng = np.random.default_rng(2)
    f, t60 = 220.0, 3.0
    x = decaying(f, t60, 4.0)
    x += rng.normal(0, 10 ** (-50 / 20), len(x))      # floor ~-50 dB
    e = d.env_db(d.band(x, FS, f, 0.1), FS)
    r = d.decay_fit(e, FS, bottom_db=60)
    assert abs(r["t60_late"] / t60 - 1) < 0.03, r


def test_harm_ratio():
    t = np.arange(FS) / FS
    x = 0.5 * np.sin(2 * np.pi * 100 * t) + 0.05 * np.sin(2 * np.pi * 300 * t)
    p1 = d.harm(x, FS, 100, 1, 0.1, 0.9)
    p3 = d.harm(x, FS, 100, 3, 0.1, 0.9)
    assert abs(10 * np.log10(p3 / p1) - (-20.0)) < 0.2
