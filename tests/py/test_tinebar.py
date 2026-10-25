# SPDX-License-Identifier: GPL-3.0-only
"""tools/tinebar.py: the 2-DOF normal modes must be consistent with a direct simulation, reduce to the uncoupled
tine with k = 0, put the sustain mode exactly on f0 after the retune, and damp the tine-dominant mode hardest."""
import os
import sys

import numpy as np
from scipy.signal import impulse, StateSpace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import tinebar as tb  # noqa: E402

FS = 44100


def test_uncoupled_is_the_tine():
    m = tb.TineBar(110.0, 112.0, 0.0, 3.0, 1.0, 0.1).modes()
    tine = [x for x in m if abs(x[2]) > 1e-12]
    fd = np.sqrt(110.0**2 - (1.0 / (2 * np.pi))**2)    # damped natural frequency
    assert len(tine) == 1 and abs(tine[0][0] - fd) < 1e-6 and abs(tine[0][1] - 1.0) < 1e-9


def test_modal_sum_matches_simulation():
    sys_ = tb.TineBar(110.0, 111.5, 0.02, 2.0, 0.8, 0.15)
    A, B, C = sys_._ss()
    n = 4096
    _, y = impulse(StateSpace(A, B[:, None], C[None, :], np.zeros((1, 1))), T=np.arange(n) / FS)
    h = sys_.impulse(FS, n)
    err = np.max(np.abs(y - h)) / np.max(np.abs(h))
    assert err < 1e-6, err


def test_retune_puts_sustain_mode_on_f0():
    f0 = 98.0
    s = tb.retune(tb.TineBar(100.0, 101.0, 0.03, 2.5, 1.2, 0.12), f0)
    m = s.modes()
    i = tb.sustain_mode(m)
    assert abs(1200 * np.log2(m[i][0] / f0)) < 0.01


def test_damper_hits_the_tine_mode_harder():
    base = tb.retune(tb.TineBar(100.0, 101.0, 0.03, 2.5, 1.0, 0.1), 98.0)
    dam = base.with_damper(30.0)
    b, d = base.modes(), dam.modes()
    # with the felt on the tine every mode decays faster, and the most tine-like mode gains the most damping
    assert all(md[1] > mb[1] for mb, md in zip(b, d))
    tine_like = int(np.argmax([abs(r) for _, _, r in b]))
    gains = [md[1] - mb[1] for mb, md in zip(b, d)]
    assert int(np.argmax(gains)) == tine_like


def test_tine_mode_with_the_bar_far_below():
    # the top keys' case (S3): bar 3779 cents below, tine T60 0.8 s, bar T60 1.6 s (key 108); sustain_mode picks the bar (its
    # late amplitude wins), tine_mode the tine, and retune(pick="tine") puts the tine-weighted mode on f0
    f0 = 3322.0
    s = tb.TineBar(f0, f0 * 2 ** (-3779 / 1200), 0.02, 3.0, 8.63, 4.32)
    m = s.modes()
    assert abs(m[tb.sustain_mode(m)][2]) < 1e-2 * abs(m[tb.tine_mode(m, s.f_t)][2])
    r = tb.retune(s, f0, pick="tine")
    mr = r.modes()
    i = tb.tine_mode(mr, r.f_t)
    assert abs(1200 * np.log2(mr[i][0] / f0)) < 0.01 and abs(mr[i][2]) == max(abs(x[2]) for x in mr)
