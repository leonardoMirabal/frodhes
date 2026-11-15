# SPDX-License-Identifier: GPL-3.0-only
"""vibes_decay's "NOISE contact 48 -> 127" row, USB against the jack (tools/hil_scan.py), on host renders of the row's
strikes (F3 A4 F6, NOISE 0 / 48 / 127) with a jack made from USB as the FM-1's: inverted, 12.6 dB down, a fractional
lag, the M6's -92.5 dBFS hiss. The rc-0.9.12 T2 flagged it MISMATCH (USB-jack -1.4 .. -3.0 dB on every key, 1.0 the
rule). Two causes: jack_by_usb took F6's jack onset a half or a whole period off (its 0..50 ms strike is near a sine:
r +0.998 there, -0.997 at the true lag), and the spectral excess read NOISE 48 with the jack's hiss in it. Now: one
polarity per take and a short-window refinement of each onset; the contact as the aligned difference re NOISE 0 with
the takes' hiss taken off (m_contact_db coarse=0.02, px / pr)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402

FS = h.FS
DE = h.vp()["NOISE"]["de"]
LAG0 = int(0.0032 * FS) + 200                             # (the jack's lag re USB, whole samples)


@pytest.fixture(scope="module")
def takes():
    h.ENGINE = "vibes"
    ev, dur = h.strikes(h.VKEYS, (100,), gap=3.0, hold=1.5)
    V = h.VRig("usb", host=1)
    T = {}
    for x in (0, DE, 127):
        V.reset([("NOISE", x)], label=f"NOISE {x}")
        T[x] = V.take(f"t-noise-{x}", dur, ev)
    return T


def jackify(T, rs, hiss_db=-92.5):
    out = {}
    for x, t in T.items():
        u = t.usb
        frac = rs.uniform(0, 1)
        j = h.frac_delay(np.concatenate([np.zeros(200), -u[:, 0]]), 0.0032 * FS + frac)[:len(u)] * 10 ** (-12.6 / 20)
        out[x] = h.Take(t.tag, t.dur, t.ev, u, j + 10 ** (hiss_db / 20) * rs.standard_normal(len(j)))
    return out


def contact(J, q, k):
    o = {x: J[x].on(q)[k] for x in J}
    m = {x: J[x].m(q) for x in J}
    c = {x: h.m_contact_db(m[x], o[x], m[0], o[0], coarse=0.02, px=J[x].pre(q), pr=J[0].pre(q))[0] for x in (DE, 127)}
    return c[127] - c[DE]


def old_contact(J, q, k):
    o = {x: J[x].on(q)[k] for x in J}
    m = {x: J[x].m(q) for x in J}
    e = {x: h.excess_db(h.cut(m[x], o[x], 0, 0.06), h.cut(m[0], o[0], 0, 0.06)) for x in (DE, 127)}
    return e[127] - e[DE]


def test_jack_onsets_not_a_period_off(takes):
    """seed 1's fractional lags 101 / 102 put F6's jack onset 17 and 34 samples off (|r| per strike); now every strike
    of every take within 2 samples of the true lag"""
    for rep in range(6):
        J = jackify(takes, np.random.default_rng(100 + rep))
        for x, t in J.items():
            d = [b - a for a, b in zip(t.on("usb"), t.on("jack"))]
            assert all(abs(v - LAG0) <= 2 for v in d), (rep, x, d)


def test_contact_usb_and_jack_agree(takes):
    """48 -> 127 on USB and the jack within 0.5 dB on every key (the MISMATCH rule 1.0 unchanged); the spectral excess
    (the row before) read the jack under USB by 0.3 .. 2 dB on the same takes (the hiss), and USB keeps >= +6 dB"""
    old = []
    for rep in range(4):
        J = jackify(takes, np.random.default_rng(100 + rep))
        for k in range(len(h.VKEYS)):
            u, j = contact(J, "usb", k), contact(J, "jack", k)
            assert u >= 6 and abs(j - u) <= 0.5, (rep, h.VKEYS[k], u, j)
            old.append(old_contact(J, "jack", k) - old_contact(J, "usb", k))
    assert min(old) <= -1.0, old                              # (the bias the hiss put in the old measure)
