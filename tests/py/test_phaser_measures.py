# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py vibes_fx's PHASER measures, proved on synthetic signals of known rate / timbre and on the host
build's phaser at several tempos (the FM-1 VIBES suite, 2026-10-18, flagged PHASER SYNC and PHASER FDBK on the jack;
both were the measure, not fx.c):

- SYNC: the band's level repeats once an LFO cycle with harmonics that can outweigh the fundamental; the old measure
  (rate_of at twice the rate, halved) read the jack's 1/4 at the FM-1's 69 BPM as 1.72 Hz (the 3rd harmonic / 2).
  fx.c's synced rate is exact: 1/4 and 1/8 at 69, 103 and 120 BPM land within 0.5 % of the note lengths.
- FDBK: the feedback raises a resonance between the notches and fills the notches (Felucca's topology, x + y), so
  the spectrum over the whole 5 s window averages the moving resonance away; frame by frame (the sweeps in step after
  CC 10) FDBK 0 and 127 are clearly apart, two takes of one setting are not."""
import os
import sys

import numpy as np
import pytest
from scipy.signal import resample_poly

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402
import hostrun as H  # noqa: E402

FPS = h.FS / 240                                  # rel_trace's frame rate (5 ms hops)


def old_phrate(d, fps, ex, lo, hi):
    """vibes_fx's phrate before 2026-10-18: two dips a cycle assumed (rate_of at 2x, halved)"""
    return h.rate_of(h.detrend2(d), fps, 2 * ex, lo, hi) / 2


def band_like(f0, dur=5.1, amps=(0.4, 0.75, 1.0, 1.0, 0.27), seed=0):
    """a band level (dB) that repeats at f0 with the FM-1 jack's harmonic mix (SYNC 1/4, 69 BPM: harmonics 3 and 4
    2.5x the fundamental), on a decay trend, with a little noise"""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * FPS)) / FPS
    x = sum(a * np.cos(2 * np.pi * (k + 1) * f0 * t + 0.7 * k) for k, a in enumerate(amps))
    return 3 * x - 4 * t + 0.3 * t * t + 0.2 * rng.standard_normal(len(t))


@pytest.mark.parametrize("f0", [0.68, 1.15, 1.72, 2.30, 4.0, 6.0])
def test_lfo_rate_finds_the_period_not_a_harmonic(f0):
    got = h.lfo_rate(band_like(f0), FPS, 0.25, 10)
    assert abs(got / f0 - 1) <= 0.01, (f0, got)


def test_the_old_phaser_rate_picked_a_harmonic():
    """the failure as the FM-1 jack showed it: 1/4 at 69 BPM (1.15 Hz) read 1.72"""
    got = old_phrate(band_like(1.15), FPS, 2, 0.2, 5)
    assert got > 1.3 * 1.15, got
    assert abs(h.lfo_rate(band_like(1.15), FPS, 0.25, 10) / 1.15 - 1) <= 0.01


def test_lfo_rate_of_a_plain_wave():
    t = np.arange(int(5.1 * FPS)) / FPS
    for f0 in (0.5, 2.0, 7.5):
        assert abs(h.lfo_rate(np.sin(2 * np.pi * f0 * t), FPS, 0.25, 10) / f0 - 1) <= 0.01


# ---- the host build's phaser, played as vibes_fx plays it on the FM-1 (VRig.reset's CCs, CC 10, the chord)
P = h.vp()
PB = [("PH_SW", 1), ("PH_DEPTH", 80), ("PH_SPEED", P["PH_SPEED"]["de"]), ("PH_FDBK", P["PH_FDBK"]["de"]), ("PH_SYNC", 0)]


def take(tag, settings, bpm=120, stim=h.ST_LONG):
    """a host take as hil_scan's VRig: the defaults, DRY, VIBES, the motor off, the settings; the TEMPO page's knob
    to bpm; CC 10 before the chord; 44.1 -> 48 kHz; the Take's 'jack' is the right side (the other listening point)"""
    dur, ev = stim
    pre = ([(c, v) for c, v in h.param_defaults() if c not in (79, 80)] + [(h.CC[n], v) for n, v in h.DRY.items()]
           + [(79, h.VIBES_CC79), (P["MOT_ON"]["cc"], 0)] + h.ccs(settings))
    lines = [H.cc(5 + k // 16, c, v) for k, (c, v) in enumerate(pre)]
    t0 = 300
    if bpm != 120:
        lines += [f"{t0} btn 7", f"{t0 + 20} turn 2 {bpm - 120}", f"{t0 + 60} btn 7"]
        t0 += 400
    R = t0 + 200
    for t, kind, a, b in h.with_reset(ev):
        ms = int(round(R + 1000 * t))
        lines.append(f"{ms} midi 0x90 {a} {b}" if kind == "on" else f"{ms} midi 0x80 {a} 0" if kind == "off"
                     else H.cc(ms, a, b))
    lines.append(f"{R - 50} dump")
    r = H.run(f"phm-{tag}", lines, R + int(1000 * dur) + 20)
    assert r.state(R - 50)["bpm"] == f"{bpm:.1f}"
    i0 = int(R * H.FS / 1000)
    x = resample_poly(np.stack([r.L[i0:], r.R[i0:]], 1), 160, 147, axis=0)[:int(dur * h.FS)] / 32768
    return h.Take(tag, dur, ev, x, x[:, 1].copy())


@pytest.mark.parametrize("bpm", [69, 103, 120])
def test_phaser_sync_runs_at_the_note_values(bpm):
    """fx.c's PH_SYNC: one LFO cycle per note value at any tempo; the measure (vibes_fx phrate: lfo_rate over the
    300-3000 Hz band re DEPTH 0) reads it on both sides. At 69 BPM the old measure read 1/4 as 1.73 Hz"""
    pb = h.with_(PB, ("PH_DEPTH", 127))
    ref = take(f"ref-{bpm}", h.with_(pb, ("PH_DEPTH", 0)), bpm)
    T = {k: take(f"{k.replace('/', '')}-{bpm}", h.with_(pb, ("PH_SYNC", h.sync_idx(k))), bpm) for k in ("1/4", "1/8")}
    for q in ("usb", "jack"):
        for k, beats in (("1/4", 1), ("1/8", 0.5)):
            d, _, fps = h.rel_trace(T[k], ref, q, 0.2, 5.3, 300, 3000)
            want = bpm / 60 / beats
            got = h.lfo_rate(d, fps, 0.25, 10)
            assert abs(got / want - 1) <= 0.005, (bpm, q, k, want, got)
            if bpm == 69 and k == "1/4":
                assert old_phrate(d, fps, 2, 0.2, 5) > 1.3 * want      # the flag, reproduced


def test_frame_dist_sees_a_moving_resonance():
    """two takes with the same resonance swept half a cycle apart: the same spectrum over the window (sdist ~ 0),
    clearly apart frame by frame; one take against itself: 0"""
    fs, dur = h.FS, 5.0
    t = np.arange(int(fs * dur)) / fs
    parts = 220 * 2 ** (np.arange(12) / 2.4)

    def swept(phase):
        pos = 0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t + phase)        # the resonance's place, 0..1 over the partials
        return sum((1 + 3 * np.exp(-((i / 11 - pos) / 0.08) ** 2)) * np.sin(2 * np.pi * f * t)
                   for i, f in enumerate(parts)) * 0.02
    a, b = swept(0.0), swept(np.pi)
    assert h.sdist(h.third(a), h.third(b)) < 1.0
    assert h.frame_dist(a, b) >= 4.0
    assert h.frame_dist(a, a) < 0.01


def test_phaser_fdbk_is_clearly_heard_frame_by_frame():
    """FDBK 0 vs 127 at DEPTH 80 (vibes_fx's PHASER FDBK): frame by frame >= 3 dB apart ("a clear timbre change"),
    two takes of the default 50 within 0.5 dB; over the whole window less (the moving peak averages away)"""
    T = {x: take(f"fb{x}", h.with_(PB, ("PH_FDBK", x))) for x in (0, 50, 127)}
    T["50b"] = take("fb50b", h.with_(PB, ("PH_FDBK", 50)))
    for q in ("usb", "jack"):
        def seg(t):
            return h.cut(t.m(q), t.on(q)[0], 0.2, 5.3)
        fd = h.frame_dist(seg(T[0]), seg(T[127]))
        spread = h.frame_dist(seg(T[50]), seg(T["50b"]))
        whole = h.sdist(h.third(seg(T[0])), h.third(seg(T[127])))
        assert fd >= 3.0 and fd >= 2 * spread and spread < 0.5, (q, fd, spread)
        assert whole < fd, (q, whole, fd)
        assert h.frame_dist(seg(T[0]), seg(T[50])) >= 1.5, q       # the default already moves it
