# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_whurli_jack.py score_sweep on synthetic takes (no device): the device's audio is the host's delayed by a different
latency per note (5, 11, 18 ms), a decaying tone (0.07 dB/ms, as key 93): a window at the event time would read the 18 ms note
1.3 dB loud; aligned per note the levels agree within 0.1 dB. A note within 20 dB of the jack's floor is listed near floor with its
SNR, not gated."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_whurli_jack as W  # noqa: E402

FS = W.FS


def take(delays_ms, amps_db, floor_db=-90.0, jack_re_usb=-12.7, seed=1):
    rng = np.random.default_rng(seed)
    n_notes = len(delays_ms)
    dur = W.T0 + 0.8 * n_notes + 1.0
    N = int(dur * FS)
    host, dev = np.zeros(N), np.zeros(N)
    notes = []
    for i, (dly, a) in enumerate(zip(delays_ms, amps_db)):
        t = W.T0 + 0.8 * i
        notes.append((t, 60 + i, 100))
        tt = np.arange(int(0.7 * FS)) / FS
        x = 10 ** (a / 20) * np.sqrt(2) * np.sin(2 * np.pi * 1760 * tt) * 10 ** (-0.07 * 1000 * tt / 20)
        s = int(t * FS)
        host[s:s + len(x)] += x
        d = int(dly * FS / 1000)
        dev[s + d:s + d + len(x)] += x
    jack = dev * 10 ** (jack_re_usb / 20) + rng.normal(0, 10 ** (floor_db / 20), N)
    return dev, jack, host, notes


def test_the_delay_is_found_per_note():
    dev, jack, host, notes = take([5, 11, 18, 11, 8], [-30] * 5)
    for (t, k, v), want in zip(notes, [5, 11, 18, 11, 8]):
        assert abs(W.align_lag(dev, host, t) * 1000 - want) <= 1.0


def test_aligned_levels_agree_where_a_fixed_window_does_not():
    dev, jack, host, notes = take([5, 11, 18, 11, 8], [-30] * 5)
    sc = W.score_sweep(dev, jack, host, notes, -90.0)
    assert sc["max_abs"] < 0.15 and not sc["near_floor"], sc
    fixed = [W.level(dev, t) - W.level(host, t) for t, k, v in notes]
    assert max(abs(x) for x in fixed) > 0.8                       # (what the event-anchored window read)


def test_a_note_near_the_floor_is_listed_with_its_snr_not_dropped():
    dev, jack, host, notes = take([11, 11, 11, 11], [-30, -30, -75, -30])   # jack floor -90 on USB's scale -77.3: -75 is 2 dB over
    sc = W.score_sweep(dev, jack, host, notes, -90.0)
    assert [(k, v) for k, v, d, snr in sc["near_floor"]] == [(62, 100)], sc["near_floor"]
    assert sc["near_floor"][0][3] < 5
    assert len([r for r in sc["rows"] if not r["near_floor"]]) == 3
