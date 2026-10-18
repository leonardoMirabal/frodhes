# SPDX-License-Identifier: GPL-3.0-only
"""tools/bartube.py (PLAN §11 P1 step 4): the V1 <-> tube pair must reduce to two resonators without coupling, agree
with a direct simulation, put V1 on f0 after the retune, shorten V1 (and move the tube peak earlier) as the coupling
grows, ring longer with the discs closed, follow the weak-regime formula, and its integer map must be stable and
match the design (§4.5). The motor law (§7) must be the single-leakage law with one AM period per phase cycle."""
import math
import os
import sys

import numpy as np
from scipy.signal import StateSpace, impulse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import bartube as bt  # noqa: E402

FS = 44100


def f3_pair(sigma=0.004, zr=0.014, cents=5.0, g=1.0, s=1.0):
    f = bt.midi_hz(53)
    ft = f * 2 ** (cents / 1200)
    return bt.BarTube(f, ft, sigma, bt.LN1000 / 25.0, zr * 2 * np.pi * ft, g=g, s=s)


def test_uncoupled_is_two_resonators():
    p = f3_pair(sigma=0.0)
    m = p.modes2()
    assert len(m) == 2
    for (f, s, rb, rt), (f_u, s_u) in zip(m, [(p.f_bar, p.sig_bar), (p.f_tube, p.sig_tube)]):
        fd = math.sqrt(f_u ** 2 - (s_u / (2 * np.pi)) ** 2)
        assert abs(f - fd) < 1e-6 and abs(s - s_u) < 1e-9
    # the bar output holds only the bar mode and the tube is never driven
    assert abs(m[1][2]) < 1e-15 and abs(m[0][3]) < 1e-15 and abs(m[1][3]) < 1e-15
    assert np.max(np.abs(p.impulse(FS, 4096, "tube"))) < 1e-15


def test_modal_sum_matches_ode_simulation():
    n = 8192
    for p in (f3_pair(), f3_pair(sigma=0.006, g=0.15), f3_pair(s=1.2, cents=40.0)):
        for part in ("bar", "tube"):
            A, B, C = p._ss(part)
            _, y = impulse(StateSpace(A, B[:, None], C[None, :], np.zeros((1, 1))), T=np.arange(n) / FS)
            h = p.impulse(FS, n, part)
            err = np.max(np.abs(y - h)) / np.max(np.abs(h))
            assert err < 1e-3, (part, err)                    # -60 dB


def test_retune_puts_v1_on_f0():
    for n, zr, c in ((53, 0.014, 5.0), (69, 0.012, 12.7), (89, 0.038, 100.0), (40, 0.01, 5.0)):
        f0 = bt.midi_hz(n)
        ft = f0 * 2 ** (c / 1200)
        p = bt.BarTube(f0 * 0.98, ft * 0.98, 0.005, bt.LN1000 / bt.t1a_free(n), zr * 2 * np.pi * ft).retune(f0)
        assert abs(1200 * math.log2(p.v1()[0] / f0)) < 0.1


def tube_peak(p):
    t = np.arange(0, 1.0, 1e-4)
    return t[int(np.argmax(p.env(t, "tube")))]


def test_more_coupling_shorter_v1_earlier_peak():
    t60, tf, tm = [], [], []
    for sg in (0.0015, 0.003, 0.0045, 0.006):
        p = f3_pair(sigma=sg).retune(bt.midi_hz(53))
        t60.append(p.t60_v1())
        tf.append(p.t_peak_formula())
        tm.append(tube_peak(p))
    assert all(a > b for a, b in zip(t60, t60[1:])), t60
    assert all(a > b for a, b in zip(tf, tf[1:])), tf
    assert all(a > b for a, b in zip(tm, tm[1:])), tm
    # and the peak measured like the recordings moves earlier too (wt chosen for the same tube/bar ratio)
    pk = [bt.model_peak_ms(f3_pair(sigma=sg).retune(bt.midi_hz(53)))[0] for sg in (0.0015, 0.006)]
    assert pk[0] > pk[1], pk


def test_discs_closed_ring_longer():
    p = f3_pair(sigma=0.005).retune(bt.midi_hz(53))
    t_open, t_closed = p.t60_v1(), p.with_motor(bt.G_C).t60_v1()
    t_free = bt.LN1000 / p.sig_bar
    assert t_open < t_closed < t_free
    # weak regime: the extra damping scales with g^2
    d_open = p.v1()[1] - p.sig_bar
    d_closed = p.with_motor(bt.G_C).v1()[1] - p.sig_bar
    assert abs(d_closed / d_open / bt.G_C ** 2 - 1) < 0.05


def test_weak_regime_formula():
    """Delta zeta = sigma^2 / (4 (zr - zn)) is the first-order root of (e - i zn)(e - i zr) = sigma^2 / 4 (E-VAL #62/#63).
    The pair reproduces the exact root within 0.5 % up to x = sigma / (zr - zn) = 0.5, and the first-order formula
    within 5 % up to x = 0.4. At x = 0.5 the formula itself is 7.2 % low ((1 - sqrt(1 - x^2)) / (x^2 / 2) = 1.072), so
    the plan's "within 5 % for sigma <= 0.5 (zr - zn)" holds only to x ~ 0.42."""
    f = 300.0
    for zn, zr in ((3e-4, 0.0073), (2e-4, 0.014), (5e-4, 0.03)):
        for x in (0.1, 0.25, 0.4, 0.5):
            sg = x * (zr - zn)
            w = 2 * np.pi * f
            p = bt.BarTube(f, f, sg, zn * w, zr * w)            # exactly tuned pair (mu = 1: equal slots)
            ws = 2 * np.pi * p.slot_freqs()[0]                   # the slots sit at w sqrt(1 + sigma): normalise there
            got = (p.v1()[1] - p.sig_bar) / ws
            d, xs = (zr - zn) * w / ws, sg / (1 + sg) / ((zr - zn) * w / ws)
            root = d / 2 * (1 - math.sqrt(1 - xs * xs))
            assert abs(got / root - 1) < 0.005, (zn, zr, x, got, root)
            if x <= 0.4:
                assert abs(got / p.dzeta_weak() - 1) < 0.05, (zn, zr, x, got, p.dzeta_weak())


def test_solve_sigma_hits_the_target_or_flags():
    p = f3_pair()
    s = p.solve_sigma(9.0).retune(bt.midi_hz(53))
    assert s.clamped == 0 and abs(s.t60_v1() / 9.0 - 1) < 0.01
    hi = p.solve_sigma(3.0)
    assert hi.clamped == +1 and hi.sigma == bt.SIG_HI and hi.sigma_wanted > bt.SIG_HI
    lo = p.solve_sigma(24.5)
    assert lo.clamped == -1 and lo.sigma == bt.SIG_LO


def design(n, zr, t1b):
    p, _ = bt.build_pair(n, zr, t1b)
    return p, bt.model_peak_ms(p)[1]


def test_integer_map_stable_and_matches_design():
    for n, zr, t1b in ((53, 0.014, 6.6), (69, 0.0116, 6.1), (89, 0.038, 2.4), (40, 0.012, 14.0)):
        p, wt = design(n, zr, t1b)
        rho, derr, cents, npts = bt.map_check(p, wt, bt.midi_hz(n), bt.felt_t60(n))
        assert npts == 270
        assert rho < 1.0, (n, rho)
        assert derr <= 0.02, (n, derr)
        assert abs(cents) <= 0.5, (n, cents)


def test_plan_sign_is_antidamping():
    """The §4.5 listing ("y0 -= ktb x1 ... y1 += kbt x0") is a skew coupling: it feeds energy into V1."""
    p, wt = design(53, 0.014, 6.6)
    w = bt.pair_words(p, wt, f0=bt.midi_hz(53))
    good, bad = bt.pair_map(w, +1), bt.pair_map(w, -1)
    sig_free = p.sig_bar
    assert bt.map_modes(good)[bt.map_v1(good)][1] > 2 * sig_free          # the tube shortens V1
    assert max(abs(np.linalg.eigvals(bad))) > 1.0                        # the skew form grows


def test_render_pair_decays_as_designed():
    p, wt = design(69, 0.0116, 6.1)
    w = bt.pair_words(p, wt, f0=bt.midi_hz(69))
    out, bar, tube = bt.render_pair(w, int(2.0 * FS))
    e = 20 * np.log10(np.array([np.max(np.abs(bar[i:i + 441])) for i in range(int(0.5 * FS), int(1.9 * FS), 441)]))
    slope = np.polyfit(np.arange(len(e)) * 441 / FS, e, 1)[0]
    assert abs(-60 / slope / p.t60_v1() - 1) < 0.02


def test_motor_law_single_leakage():
    phi = np.linspace(0, np.pi, 1001)
    g = bt.mot_law(phi, 127, 0)
    assert abs(g.min() - bt.G_C) < 1e-9 and abs(g.max() - 1.0) < 1e-6        # closed = g_c exactly (one leakage)
    assert np.all(bt.mot_law(phi, 0, 0) == 1.0)                               # depth 0: discs parked open
    o = 1 - np.abs(np.cos(phi))
    assert np.allclose(bt.mot_law(phi, 127, 127), bt.G_C + (1 - bt.G_C) * o * o)   # SHAPE 127 = o^2


def test_motor_block_bit_exact_law_and_rate():
    inc = bt.mot_inc(78)                                   # 3.5 Hz
    assert abs(inc * FS / 2 ** 32 - 3.52) < 0.01
    ph, gs, phis = 0, [], []
    for _ in range(int(FS / 3.52 / 256) + 2):              # a bit over one AM period
        g, ph2 = bt.mot_block(ph, inc, 127, 0)
        for i in range(9):
            phis.append(((ph + i * 32 * inc) & 0xFFFFFFFF) / 2 ** 32 * np.pi)
            gs.append(g[i] / 32768)
        ph = ph2
    ref = bt.mot_law(np.array(phis), 127, 0)
    assert np.max(np.abs(np.array(gs) - ref)) < 2e-3
    # one 2^32 phase cycle is ONE AM period: exactly one closed trough per cycle
    t = np.arange(0, 2 ** 32, 2 ** 20)
    g = [bt.mot_block(int(x), 0, 127, 0)[0][0] for x in t]
    lo = int((bt.G_C + 0.03) * 32768)                     # just over the closed state
    troughs = sum(1 for i in range(len(g)) if g[i] < lo and g[i - 1] >= lo)
    assert troughs == 1
    assert bt.mot_block(0, inc, 127, 0, on=False)[0] == [32768] * 9
    assert bt.mot_inc(127) == bt.mot_inc(120)              # rate capped at 10.08 Hz


def test_motor_ramp_is_the_c_ramp():
    g = [32768, 30000, 20000, 9000, 4915, 4915, 9000, 20000, 30000]
    r = bt.mot_ramp(1 << 20, g)
    assert len(r) == 256 and r[0] == (1 << 20) and r[32] == ((1 << 20) * 30000) >> 15


def test_f0_peak_procedure_like_the_recordings():
    t = np.arange(int(1.0 * FS)) / FS
    x = np.sin(2 * np.pi * 440 * t) * np.exp(-t)              # no tube: the envelope only falls
    assert bt.f0_peak_ms(x, 440.0) == 0.0
    y = np.sin(2 * np.pi * 440 * t) * (np.exp(-2 * t) - np.exp(-20 * t))   # a tube build-up peaking at 128 ms
    pk = bt.f0_peak_ms(y, 440.0)
    assert 60 < pk < 128                                      # read at the 93 ms window's start: ~46 ms early


def test_motor_makeup_keeps_the_tube_power():
    """bars.c bar_mot_mk (the motor gates the tube's radiation only, 2026-10-18): mk^2 mean(g^2) = 1 over a turn for
    every DEPTH / SHAPE (the loudness of the discs open), x 1 exactly when parked, and at most x 2.4"""
    phi = np.linspace(0, np.pi, 20001)[:-1]
    gc = int(round(bt.G_C * 32768))
    assert bt.mot_mk(32768, 0) == 32768 and bt.mot_mk(32768, 127) == 32768
    for depth in (16, 64, 110, 127):
        for shape in (0, 40, 127):
            gmin = 32768 - (depth * (32768 - gc)) // 127
            mk = bt.mot_mk(gmin, shape) / 32768
            g = bt.mot_law(phi, depth, shape)
            assert abs(mk * mk * np.mean(g * g) - 1) < 0.004, (depth, shape, mk)
            assert mk <= 2.4
