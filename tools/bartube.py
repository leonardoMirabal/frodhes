# SPDX-License-Identifier: GPL-3.0-only
"""The vibraphone bar <-> tube pair: V1 two-way coupled to the resonator tube, with the motor (PLAN §3.3, §4.5, §7).

The physics is tinebar.TineBar reused: the "tine" DOF is the bar's V1 (driven by the mallet, damped by the felt, read
as the bar's direct sound) and the "bar" DOF is the tube's first resonance. The coupling is a symmetric stiffness
k = sigma * w_V1^2 with mu = 1, so the tuned pair splits by sigma * w (SDA23's inertial coupling near resonance,
E §3.4: invert the 2x2 mass matrix, 1 - sigma^2 ~ 1). BarTube adds what the C engine does on top of TineBar:
the BELL size s and the motor gain g multiply the OFF-DIAGONAL coupling only (the slot frequencies stay put, as in
bars.c, which scales k_bt / k_tb and wt per sub-block), and the output can be read as bar, tube or
bar + wt * tube.

    bt = BarTube(f_bar, f_tube, sigma, sig_bar, sig_tube)   # Hz, Hz, dimensionless, 1/s, 1/s (amplitude e^-sig t)
    bt.modes(part)            # [(f_hz, sigma_per_s, residue)] sorted by frequency; part "bar" | "tube"
    bt.impulse(fs, n, part, wt)   # displacement for a unit force impulse on the bar (part "out": bar + wt tube)
    bt.retune(f0)             # scaled so the V1-like (late-sustain) normal mode is exactly f0
    bt.with_damper(sig)       # the felt on the bar (the tube is never felt-damped)
    bt.with_motor(g)          # coupling (and, for the output, the radiation wt) x g; g_c = 0.15 closed
    bt.solve_sigma(t60)       # sigma for a V1-with-tube T60, bounded 0.0015..0.007, .clamped / .sigma_wanted
    pair_words(bt, ...)       # the quantised slot words + Q30 couplings + Q28 wt (§4.2, §4.5)
    pair_map(words) / map_check(...)   # the exact per-sample 4x4 integer map, its spectral radius and modes (§4.5)

Motor (§7): mot_law (float), MOT_COS / mot_block (bit-exact to bar_motor_block), mot_ramp (the per-sample word ramp),
mot_inc (the rate law of FX_LFO_INC).

Per-key design: `python3 tools/bartube.py [--smooth-peaks]` solves tube zeta_r (from the measured f0-peak times) and sigma (from the
measured V1-with-tube T60s) for every key 21..108 and writes build/bartube/solve.csv and summary.txt.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rh_coefs as rc  # noqa: E402
import tinebar  # noqa: E402
import vib_ref_measure as vrm  # noqa: E402  (only its envelope functions: the model is measured like the recordings)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FS = 44100.0
LN1000 = 6.907755279
SIG_LO, SIG_HI = 0.0015, 0.007         # T5a: the exposed coupling range (E-VAL V14; hi 0.007 per vibes.json T5a)
S_MAX = 1.2                             # KN_TUBE[127] (§4.4): BELL's top
WEAK_MARGIN = 0.9                       # §4.5: s_max * sigma <= 0.9 (zeta_r - zeta_n)
ZR_HI = 0.04                            # T4a: tube zeta_r upper bound
G_C = 0.15                              # T6c: closed-disc residual on the tube path
G_PLAN_MAX = 8                          # §4.5: G in {1, 2, 4, 8}
COUPLE_SIGN = +1                        # y0 += SIGN * (k_tb * x1) >> 30.  +1 = a physical (symmetric) spring. The
#                                         plan's §4.5 listing has "y0 -= ...", i.e. -1: a skew coupling that pumps
#                                         energy INTO V1 (see map_check and test_plan_sign_is_antidamping).


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


class BarTube(tinebar.TineBar):
    """TineBar with the bar as the driven DOF and the tube as the passive one; s (BELL) and g (motor) scale the
    off-diagonal coupling only."""

    def __init__(self, f_bar, f_tube, sigma, sig_bar, sig_tube, g=1.0, s=1.0, mu=1.0):
        super().__init__(f_bar, f_tube, sigma, mu, sig_bar, sig_tube)
        self.g, self.s = float(g), float(s)
        self.clamped, self.sigma_wanted = 0, float(sigma)

    # readable names for the TineBar fields
    f_bar = property(lambda self: self.f_t)
    f_tube = property(lambda self: self.f_b)
    sigma = property(lambda self: self.k_rel)
    sig_bar = property(lambda self: self.sigma_t)
    sig_tube = property(lambda self: self.sigma_b)

    def _with(self, **kw):
        a = dict(f_bar=self.f_t, f_tube=self.f_b, sigma=self.k_rel, sig_bar=self.sigma_t, sig_tube=self.sigma_b,
                 g=self.g, s=self.s, mu=self.mu)
        a.update(kw)
        return BarTube(**a)

    def _ss(self, part="bar"):
        wb, wr = 2 * np.pi * self.f_t, 2 * np.pi * self.f_b
        k = self.k_rel * wb * wb
        c = self.s * self.g
        M = np.diag([1.0, self.mu])
        C = np.diag([2 * self.sigma_t, 2 * self.mu * self.sigma_b])
        K = np.array([[wb * wb + k, -c * k], [-c * k, self.mu * wr * wr + k]])
        Mi = np.linalg.inv(M)
        A = np.block([[np.zeros((2, 2)), np.eye(2)], [-Mi @ K, -Mi @ C]])
        B = np.concatenate([np.zeros(2), Mi @ np.array([1.0, 0.0])])
        Cout = np.array([1.0, 0.0, 0.0, 0.0]) if part == "bar" else np.array([0.0, 1.0, 0.0, 0.0])
        return A, B, Cout

    def modes2(self):
        """[(f_hz, sigma, r_bar, r_tube)] sorted by frequency."""
        A, B, _ = self._ss()
        lam, V = np.linalg.eig(A)
        Vi = np.linalg.inv(V)
        vb = Vi @ B
        out = [(abs(l.imag) / (2 * np.pi), -l.real, V[0, i] * vb[i], V[1, i] * vb[i])
               for i, l in enumerate(lam) if l.imag > 0]
        return sorted(out, key=lambda m: m[0])

    def modes(self, part="bar"):
        return [(f, s, rb if part == "bar" else rt) for f, s, rb, rt in self.modes2()]

    def impulse(self, fs, n, part="bar", wt=None):
        """Displacement for a unit force impulse on the bar: part "bar", "tube", or (wt given) bar + wt * tube."""
        t = np.arange(n) / fs
        h = np.zeros(n)
        for f, s, rb, rt in self.modes2():
            r = rb + wt * rt if wt is not None else (rb if part == "bar" else rt)
            h += 2 * np.real(r * np.exp((-s + 2j * np.pi * f) * t))
        return h

    def env(self, t, part="bar", wt=None):
        """Analytic amplitude envelope of the same signal (the modes share one frequency to within the coupling)."""
        t = np.asarray(t, float)
        z = 0
        for f, s, rb, rt in self.modes2():
            r = rb + wt * rt if wt is not None else (rb if part == "bar" else rt)
            z = z + r * np.exp((-s + 2j * np.pi * (f - self.f_t)) * t)
        return 2 * np.abs(z)

    def scaled(self, c):
        return self._with(f_bar=self.f_t * c, f_tube=self.f_b * c)

    def retune(self, f0, t_late=2.0, iters=6):
        return tinebar.retune(self, f0, t_late, iters)

    def with_damper(self, sig):
        return self._with(sig_bar=self.sigma_t + sig)

    def with_motor(self, g):
        return self._with(g=g)

    def with_bell(self, s):
        return self._with(s=s)

    def with_sigma(self, sigma):
        return self._with(sigma=sigma)

    def v1(self, t_late=2.0):
        """(f, sigma, r_bar) of the V1-like normal mode: the one carrying the bar's late sustain."""
        m = self.modes()
        return m[tinebar.sustain_mode(m, t_late)]

    def t60_v1(self):
        return LN1000 / self.v1()[1]

    def zetas(self):
        """(zeta_n, zeta_r): the uncoupled bar and tube damping ratios."""
        return self.sigma_t / (2 * np.pi * self.f_t), self.sigma_b / (2 * np.pi * self.f_b)

    def dzeta_weak(self):
        """The weak-regime extra damping of V1, exactly tuned (E §3.4, E-VAL #63): sigma_eff^2 / (4 (zr - zn))."""
        zn, zr = self.zetas()
        se = self.s * self.g * self.k_rel
        return se * se / (4 * (zr - zn))

    def weak_ok(self, s_max=S_MAX, margin=WEAK_MARGIN):
        zn, zr = self.zetas()
        return s_max * self.k_rel <= margin * (zr - zn)

    def sigma_weak_max(self, s_max=S_MAX, margin=WEAK_MARGIN):
        zn, zr = self.zetas()
        return margin * (zr - zn) / s_max

    def t_peak_formula(self):
        """ln(sig_t / sig_b) / (sig_t - sig_b) with sig_t = the tube's 2 pi f zeta_r and sig_b = the V1-with-tube decay
        6.908 / T60 (§3.3 T5c): the peak of a tube driven by a decaying bar, the bar's direct sound left out."""
        a, b = self.sigma_b, self.v1()[1]
        return math.log(a / b) / (a - b)

    def slot_freqs(self):
        """The C slots' own frequencies (K_ii / M_ii: the diagonal coupling spring folded in): these, not f_bar /
        f_tube, go to mode_coefs, because bars.c only adds the off-diagonal terms."""
        wb, wr = 2 * np.pi * self.f_t, 2 * np.pi * self.f_b
        k = self.k_rel * wb * wb
        return math.sqrt(wb * wb + k) / (2 * np.pi), math.sqrt(wr * wr + k / self.mu) / (2 * np.pi)

    def solve_sigma(self, target_t60, lo=SIG_LO, hi=SIG_HI):
        """sigma such that the V1-like mode's T60 (at s = 1, g = 1) is target_t60, by bisection; returned as a new
        BarTube with .clamped = -1 / +1 when the wanted sigma lies below lo / above hi (then sigma = the bound) and
        .sigma_wanted = the unclamped solution (searched in 1e-5 .. 0.05; NaN if even that cannot reach the target)."""
        base = self._with(s=1.0, g=1.0)

        def t60(sg):
            return base.with_sigma(sg).t60_v1()

        def bisect(a, b):
            for _ in range(60):
                m = math.sqrt(a * b)
                if t60(m) > target_t60:
                    a = m
                else:
                    b = m
            return math.sqrt(a * b)

        if t60(1e-5) <= target_t60:
            wanted = float("nan") if t60(1e-5) < target_t60 * 0.999 else 1e-5
        elif t60(0.05) >= target_t60:
            wanted = float("nan")
        else:
            wanted = bisect(1e-5, 0.05)
        if not wanted == wanted:                      # unreachable: report which side
            sg, cl = (lo, -1) if t60(1e-5) <= target_t60 else (hi, +1)
        elif wanted < lo:
            sg, cl = lo, -1
        elif wanted > hi:
            sg, cl = hi, +1
        else:
            sg, cl = wanted, 0
        out = self._with(sigma=sg)
        out.clamped, out.sigma_wanted = cl, wanted
        return out


# ------------------------------------------------------------------------- measurement, like vib_ref_measure
HOP = int(0.005 * FS)


def f0_peak_ms(x, f0, fs=FS):
    """The f0-band envelope peak (ms after the onset) with exactly the procedure of vib_ref_measure.measure():
    5 ms RMS frames for the onset (first frame within 40 dB of the peak), then a 4096-point Hann STFT magnitude at f0
    every 5 ms from the onset; the reported time is the window START (so a peak at t ~ the window's centre reads ~46 ms
    early, as it did on the recordings)."""
    env = vrm.envelope_db(x, fs, HOP)
    pk = int(np.argmax(env))
    onset = next((i for i in range(pk + 1) if env[i] > env[pk] - 40), 0)
    lv, _ = vrm.band_env(x[onset * HOP:], fs, f0, 0, HOP)
    return int(np.argmax(lv)) * HOP / fs * 1000


def wt_for_ratio(bt, ratio_db, t=0.2):
    """The radiation weight wt (physical tube units, G = 1) that puts the tube part ratio_db over the bar's direct
    f0 at time t (T5b: +10 dB at 0.2 s, BELL 64)."""
    return 10 ** (ratio_db / 20) * bt.env(t, "bar") / bt.env(t, "tube")


# ------------------------------------------------------------------------- integer words and the per-sample map
def pair_words(bt, wt, t60_rel_bar=None, t30=0, k16=65536, release=False, G=None, f0=None, kc=1.0, iters=4):
    """The quantised words of the pair as bars.c will hold them (s and g of bt applied to k_bt, k_tb and wt).

    Slot 0 (V1): mode_coefs(f_slot0, T60_bar, t60_rel_bar) run through rc.coef with (t30, k16, d or drel).
    Slot 1 (TUBE): mode_coefs(f_slot1, T60_tube, T60_tube) with k16 = 65536 and drel = d (§4.1).
    Couplings: the continuous stiffness kappa = kc sigma w^2 becomes E_i * k = kappa / fs^2 per slot, so
    k_tb = kappa / (fs^2 E0) / G, k_bt = kappa / (fs^2 E1) * G (Q30); wt -> wt / G (Q28). G: the smallest power of
    two with |wt / G| <= 4 (unless given). NOTE: the plan allows G in {1, 2, 4, 8}; the solved wt reaches ~130, so G
    here goes up to 64 (G_PLAN_MAX flags the keys that need more than 8).
    f0: if given, the discrete design step: both slot frequencies are scaled so the integer map's V1-like mode sits
    exactly on f0, and kc is set so its V1 decay equals the continuous design's (bt.v1()). The per-sample coupling of
    the MC3 pair is not exactly the continuous one (a phase offset of order theta between x and y): uncorrected, the
    V1 shortening comes out up to ~10 % weak at F6 (theta = 0.2 rad); kc absorbs it (1.00 at F3, ~1.05 at F6).
    Returns a dict (E0, eps0, E1, eps1, k_bt, k_tb, wt_q28, G, kc, w0, w1 words, the float slot frequencies)."""
    scale = 1.0
    sig_target = bt.v1()[1] if f0 else None
    for it in range(iters if f0 else 1):
        fs0, fs1 = bt.slot_freqs()
        fs0, fs1 = fs0 * scale, fs1 * scale
        t60b = LN1000 / bt.sig_bar
        t60t = LN1000 / bt.sig_tube
        w0 = rc.mode_coefs(fs0, t60b, t60_rel_bar if t60_rel_bar else t60b)
        w1 = rc.mode_coefs(fs1, t60t, t60t)
        E0, eps0 = rc.coef(w0, w0["drel"] if release else w0["d"], t30, k16)
        E1, eps1 = rc.coef(w1, w1["d"], t30, 65536)
        if G is None:
            G = next((c for c in (1, 2, 4, 8, 16, 32, 64) if abs(wt) * bt.s * bt.g / c <= 4.0), 64)

        def mk(kc_):
            kap = kc_ * bt.k_rel * (2 * np.pi * bt.f_t * scale) ** 2 / FS ** 2 * bt.s * bt.g
            return dict(E0=E0, eps0=eps0, E1=E1, eps1=eps1, k_tb=int(round(kap / (E0 / rc.Q30) / G * rc.Q30)),
                        k_bt=int(round(kap / (E1 / rc.Q30) * G * rc.Q30)),
                        wt_q28=int(round(wt * bt.s * bt.g / G * (1 << 28))), G=G, kc=kc_, w0=w0, w1=w1,
                        f_slot0=fs0, f_slot1=fs1)
        words = mk(kc)
        if not f0:
            break
        if bt.k_rel * bt.s * bt.g > 0:                 # kc: the map's V1 decay = the design's (bisection, log)
            def fast(c):
                M = pair_map(mk(c))
                return map_modes(M)[map_v1(M)][1] > sig_target
            lo_, hi_ = 0.25, 4.0
            for _ in range(40):
                m = math.sqrt(lo_ * hi_)
                lo_, hi_ = (lo_, m) if fast(m) else (m, hi_)
            kc = math.sqrt(lo_ * hi_)
            words = mk(kc)
        M = pair_map(words)
        scale *= f0 / map_modes(M)[map_v1(M)][0]
    return words


def pair_map(w, sign=None):
    """The exact per-sample linear map of the pair on (x0, y0, x1, y1), in the bars.c operation order (§4.5):
        y0 += sign * k_tb x1;  x0 += E0 y0;  y0 += -eps0 y0 - E0 x0;
        y1 += k_bt x0 (the updated x0);  x1 += E1 y1;  y1 += -eps1 y1 - E1 x1.
    The error feedback makes the long-run arithmetic exact, so the integer words / 2^30 define the map."""
    sign = COUPLE_SIGN if sign is None else sign
    q = float(rc.Q30)
    E0, e0, E1, e1 = w["E0"] / q, w["eps0"] / q, w["E1"] / q, w["eps1"] / q
    kt, kb = w["k_tb"] / q, w["k_bt"] / q

    def op(i, j, a):                     # state[i] += a * state[j]
        m = np.eye(4)
        m[i, j] += a
        return m
    seq = [op(1, 2, sign * kt), op(0, 1, E0), op(1, 1, -e0), op(1, 0, -E0),
           op(3, 0, kb), op(2, 3, E1), op(3, 3, -e1), op(3, 2, -E1)]
    M = np.eye(4)
    for s in seq:
        M = s @ M
    return M


def map_modes(M, fs=FS):
    """[(f_hz, sigma_per_s, bar_share)] of the map's two oscillatory eigenpairs (positive frequencies)."""
    lam, V = np.linalg.eig(M)
    out = []
    for i, l in enumerate(lam):
        if l.imag > 0:
            v = V[:, i]
            share = (abs(v[0]) ** 2 + abs(v[1]) ** 2) / (np.sum(np.abs(v) ** 2) + 1e-300)
            out.append((np.angle(l) * fs / (2 * np.pi), -math.log(abs(l)) * fs, share))
    return sorted(out, key=lambda m: m[0])


def map_v1(M):
    """Index of the V1-like eigenpair: the slower-decaying one, ties by the larger bar share."""
    m = map_modes(M)
    return int(min(range(len(m)), key=lambda i: (round(m[i][1], 9), -m[i][2])))


def words_equivalent(w, fs=FS, sign=None):
    """The continuous pair the integer words stand for (the "float design" of a grid point): each slot's exact
    MC3 pole (frequency, decay) and the stiffnesses kappa_01 = sign k_tb E0 fs^2, kappa_10 = k_bt E1 fs^2.
    Returns [(f, sigma)] of its two normal modes, sorted by frequency."""
    sign = COUPLE_SIGN if sign is None else sign
    q = float(rc.Q30)
    poles = []
    for E, e in ((w["E0"], w["eps0"]), (w["E1"], w["eps1"])):
        E, e = E / q, e / q
        lam = np.linalg.eigvals(np.array([[1, E], [-E, 1 - e - E * E]]))
        l = lam[np.argmax(lam.imag)]
        poles.append((np.angle(l) * fs, -math.log(abs(l)) * fs))
    (w0, s0), (w1, s1) = poles
    k01 = sign * w["k_tb"] / q * w["E0"] / q * fs * fs
    k10 = w["k_bt"] / q * w["E1"] / q * fs * fs
    W0, W1 = w0 * w0 + s0 * s0, w1 * w1 + s1 * s1        # undamped stiffness of each damped pole
    A = np.array([[0, 0, 1, 0], [0, 0, 0, 1], [-W0, k01, -2 * s0, 0], [k10, -W1, 0, -2 * s1]], float)
    lam = np.linalg.eigvals(A)
    return sorted([(l.imag / (2 * np.pi), -l.real) for l in lam if l.imag > 0])


def scaled_words(w_ref, w_coef, s=1.0, g=1.0):
    """The run-time words of a voice: E/eps from w_coef (rh_coef with TUNE, DECAY or RELEASE), the couplings and wt
    from the key's words w_ref times BELL s and motor g as bars.c does it (v->kbt = k_bt * s >> 15, then * g >> 15
    per sub-block edge): TUNE does not touch the coupling words."""
    sq, gq = int(round(s * 32768)), int(round(g * 32768))
    w = dict(w_coef)
    for k in ("k_bt", "k_tb", "wt_q28"):
        w[k] = ((w_ref[k] * sq) >> 15) * gq >> 15
    return w


def map_check(bt, wt, f0, t60_rel_bar, grid=None):
    """§4.5 stability check of one key over the grid: s in {0, .25, .5, 1, 1.2}, motor g in {0.15, 0.5, 1}, DECAY
    (held pair) / RELEASE (felt pair) k16 at knob 0, 64, 127, TUNE in {-50, 0, +50} c.
    The words of every point are the key's design words (G, kc and the discrete retune fixed) with s, g, k16, t30
    applied as the engine applies them: rh_coef for E/eps, Q15 products for k_bt, k_tb and wt (scaled_words). The float design of a point is the continuous
    BarTube with the same s, g, bar decay x k16 / 65536 (or the felt), and TUNE as the C does it: frequencies x (1 + t)
    with the coupling words fixed, i.e. sigma / (1 + t).
    Returns (rho_max, worst relative decay error of the two normal modes vs the design, V1 cents vs f0 at s = 1,
    g = 1, TUNE 0, number of points)."""
    dk = [int(round(rc.Q16 / 2 ** ((v - 64) / 32))) for v in (0, 64, 127)]
    tune = [(c, int(round((2 ** (c / 1200) - 1) * rc.Q30))) for c in (-50, 0, 50)]
    grid = grid or dict(s=(0.0, 0.25, 0.5, 1.0, 1.2), g=(0.15, 0.5, 1.0))
    rho, derr, n = 0.0, 0.0, 0
    w_ref = pair_words(bt, wt, t60_rel_bar, f0=f0)
    btn = bt.scaled(w_ref["f_slot0"] / bt.slot_freqs()[0])
    sig_rel = LN1000 / t60_rel_bar
    for s in grid["s"]:
        for g in grid["g"]:
            for k16 in dk:
                for c, t30 in tune:
                    for rel in (False, True):
                        w = scaled_words(w_ref, pair_words(btn, wt, t60_rel_bar, t30=t30, k16=k16, release=rel,
                                                           G=w_ref["G"], kc=w_ref["kc"]), s, g)
                        M = pair_map(w)
                        rho = max(rho, max(abs(np.linalg.eigvals(M))))
                        tt = 2 ** (c / 1200)
                        d = bt.with_bell(s).with_motor(g)._with(sig_bar=(sig_rel if rel else bt.sig_bar) * k16 / 65536.0)
                        d = d.scaled(tt).with_sigma(bt.sigma / tt)
                        for (fa, sa, _), (fb, sb, _) in zip(map_modes(M), d.modes()):
                            derr = max(derr, abs(sa / sb - 1))
                        n += 1
    M = pair_map(w_ref)
    cents = 1200 * math.log2(map_modes(M)[map_v1(M)][0] / f0)
    return rho, derr, cents, n


# ------------------------------------------------------------------------- the motor (§7, T6b-T6g)
MOT_COS = [int(round(32767 * abs(math.cos(math.pi * i / 512)))) for i in range(257)]
# Q15 |cos| over a quarter turn of the table index: t = ph >> 23 (0..511) folds to COS[t] / COS[512 - t], so ONE
# 2^32 phase cycle is ONE AM period (|cos phi|, phi = pi t / 512). The plan's "|cos(pi i / 256)|" would give two AM
# periods per cycle, i.e. twice the displayed rate.


def mot_inc(v):
    """Q32 phase step per sample: FX_LFO_INC[min(v, 120)] (0.5 * 24^(v/127) Hz; 3.5 Hz at v = 78, 10.08 at 120)."""
    v = min(int(v), 120)
    return int(round(0.5 * 24 ** (v / 127) / FS * 2 ** 32))


def mot_law(phi, depth=127, shape=0, g_c=G_C):
    """Float opening law with one leakage parameter (T6b, F-VAL F4), in the C form: o = 1 - |cos phi|,
    o' = o + (o^2 - o) * shape / 127 (= o^p only at shape 0 and 127, p = 1 + shape / 127), g_min = 1 - depth/127 (1 - g_c),
    g = g_min + (1 - g_min) o'.  phi = pi f_AM t (|cos| has the AM period)."""
    o = 1.0 - np.abs(np.cos(phi))
    op = o + (o * o - o) * shape / 127.0
    gmin = 1.0 - depth / 127.0 * (1.0 - g_c)
    return gmin + (1.0 - gmin) * op


def mot_block(ph, inc, depth=127, shape=0, gc_q15=None, on=True):
    """bar_motor_block(), bit-exact: the Q15 gains at the 9 sub-block edges of one 256-frame block and the next
    phase. ph, inc: uint32 Q32. Integer division truncates toward zero as in C."""
    gc = int(round(G_C * 32768)) if gc_q15 is None else gc_q15
    if not on:
        return [32768] * 9, (ph + 256 * inc) & 0xFFFFFFFF
    gmin = 32768 - rc._tdiv(depth * (32768 - gc), 127)
    g = []
    for i in range(9):
        p = (ph + i * 32 * inc) & 0xFFFFFFFF
        t, fr = p >> 23, (p >> 15) & 255
        c0 = MOT_COS[t] if t < 256 else MOT_COS[512 - t]
        t1 = (t + 1) & 511
        c1 = MOT_COS[t1] if t1 < 256 else MOT_COS[512 - t1]
        c = c0 + ((c1 - c0) * fr >> 8)
        o = 32768 - c
        op = o + rc._tdiv((((o * o) >> 15) - o) * shape, 127) if shape else o
        g.append(gmin + (((32768 - gmin) * op) >> 15))
    return g, (ph + 256 * inc) & 0xFFFFFFFF


def mot_ramp(word, g):
    """The per-sample values of a coupling word over one block as bars.c ramps it (§5.5 step 3): per sub-block b,
    k0 = word g[b] >> 15, k1 = word g[b+1] >> 15, dk = (k1 - k0) >> 5, sample j uses k0 + j dk. 256 ints."""
    out = []
    for b in range(8):
        k0 = (word * g[b]) >> 15
        k1 = (word * g[b + 1]) >> 15
        dk = (k1 - k0) >> 5
        out += [k0 + j * dk for j in range(32)]
    return out


def mot_gains(n, inc, depth=127, shape=0, gc_q15=None, on=True, ph0=0):
    """(Q15 edge gains per block [nblk][9], the per-sample float gain with the C's linear ramp) for n samples."""
    ph, edges, per = ph0 & 0xFFFFFFFF, [], []
    for _ in range((n + 255) // 256):
        g, ph = mot_block(ph, inc, depth, shape, gc_q15, on)
        edges.append(g)
        for b in range(8):
            per += list(g[b] + (g[b + 1] - g[b]) * np.arange(32) / 32.0)
    return edges, np.array(per[:n]) / 32768.0


def render_pair(w, n, g=None, x0=None, upper=()):
    """Float render of the pair in the bars.c order with the integer words of pair_words (s applied; g = 1),
    the motor gain per sample g[n] (or None) multiplying k_bt, k_tb and wt (T6e: both); the strike is a unit
    velocity kick in y0 (x0 = initial y0). upper: [(f, t60, amp)] free bar modes added unmodulated (V2, V3 ...).
    Returns (out, bar, tube) where out = bar + wt tube / G-normalised as the engine sums it, + the upper modes."""
    q = float(rc.Q30)
    E0, e0, E1, e1 = w["E0"] / q, w["eps0"] / q, w["E1"] / q, w["eps1"] / q
    kt, kb, wtq = w["k_tb"] / q, w["k_bt"] / q, w["wt_q28"] / float(1 << 28)
    gg = np.ones(n) if g is None else np.asarray(g, float)
    a0 = b0 = a1 = b1 = 0.0
    b0 = 1.0 if x0 is None else x0
    out = np.empty(n)
    bar = np.empty(n)
    tub = np.empty(n)
    for j in range(n):
        gj = gg[j]
        b0 += COUPLE_SIGN * kt * gj * a1
        a0 += E0 * b0
        b0 += -e0 * b0 - E0 * a0
        b1 += kb * gj * a0
        a1 += E1 * b1
        b1 += -e1 * b1 - E1 * a1
        bar[j] = a0
        tub[j] = a1
        out[j] = a0 + wtq * gj * a1
    t = np.arange(n) / FS
    for f, t60, amp in upper:
        out += amp * np.sin(2 * np.pi * f * t) * np.exp(-LN1000 / t60 * t)
    return out, bar, tub


# ------------------------------------------------------------------------- per-key design from the measured targets
# §3.0 / private/vibraphone/ref_measure.md (MEASURED-REF; files named one octave low, so file "F2" = F3 = MIDI 53).
# Lane A's vibes.json is the long-term home of these numbers; design_keys() takes them as arguments.
REF_NOTES = [53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84, 86, 88, 89]
REF_T1B = [6.6, 6.1, 5.0, 9.5, 5.2, 4.7, 4.3, 6.5, 4.2, 6.1, 10.2, 5.7, 6.7, 9.1, 5.2, 7.8, 7.4, 6.0, 6.4, 5.2, 4.6, 2.4]
REF_PEAK_MS = [145, 110, 105, 35, 80, 85, 40, 15, 40, 50, 45, 0, 30, 70, 50, 25, 30, 0, 0, 15, 0, 15]
E_ZETA = [(53, 0.0073), (57, 0.0073), (60, 0.0073), (65, 0.0093), (69, 0.0116), (72, 0.014), (77, 0.019),
          (81, 0.025), (84, 0.029), (89, 0.038)]          # T4a starting curve (E §3.1, §4 row 11), F3..F6
RATIO_DB = 10.0                                            # T5b: tube part over the bar's direct f0 at 0.2 s, BELL 64


def smooth_median(v, r=3):
    """±r-key median over the reference list, the window shrinking symmetrically at the ends (keeps F3, F6 raw)."""
    out = []
    for i in range(len(v)):
        k = min(r, i, len(v) - 1 - i)
        out.append(float(np.median(v[i - k:i + k + 1])))
    return out


def interp_semi(n, xs, ys, log=False):
    y = np.log(ys) if log else np.asarray(ys, float)
    v = float(np.interp(n, xs, y))
    return math.exp(v) if log else v


def t1a_free(n):
    """T1a: 25 (174.6 / f)^0.77 s, capped by 7330 / f (eta 3e-4) and at 40 s."""
    f = midi_hz(n)
    return min(25 * (174.6 / f) ** 0.77, 7330 / f, 40.0)


def t1b_target(n, smooth):
    """T1b: the smoothed reference table in F3..F6 (semitone interpolation), D's 10 (174.6 / f)^0.45 outside."""
    if REF_NOTES[0] <= n <= REF_NOTES[-1]:
        return interp_semi(n, REF_NOTES, smooth)
    return min(10 * (174.6 / midi_hz(n)) ** 0.45, 40.0)


def zeta_lo(n):
    """T4a lower bound: max(0.73 %, the E curve), E held flat below F3 and extrapolated (log) above F6."""
    xs, ys = [a for a, _ in E_ZETA], [b for _, b in E_ZETA]
    if n > xs[-1]:
        sl = (math.log(ys[-1]) - math.log(ys[-2])) / (xs[-1] - xs[-2])
        return min(ZR_HI, max(0.0073, ys[-1] * math.exp(sl * (n - xs[-1]))))
    return max(0.0073, interp_semi(n, xs, ys, log=True))


def tube_cents(n):
    """T4b: +5 c at every key (vibes.json T4b, 2026-10-17: the old +5 c (F3) -> +20 c (C6) -> +100 c (F6) curve is
    contradicted by the reference's F6 T60 and D5..A5 f0 peaks, and made the integer pair anti-damping above D7)."""
    return float(np.interp(n, [53, 89], [5.0, 5.0]))


def build_pair(n, zr, t1b, sigma=None, s=1.0):
    """The retuned pair for key n with tube zeta_r: sigma solved for t1b (or given), weak-regime limited."""
    f0 = midi_hz(n)
    ft = f0 * 2 ** (tube_cents(n) / 1200)
    sb = LN1000 / t1a_free(n)
    st = zr * 2 * np.pi * ft
    bt = BarTube(f0, ft, SIG_LO, sb, st)
    if sigma is None:
        bt = bt.solve_sigma(t1b)
    else:
        bt = bt.with_sigma(sigma)
    weak = 0
    smax = bt.sigma_weak_max()
    if bt.sigma > smax:                                # §4.5: lower sigma, flag bit2
        cl, sw = bt.clamped, bt.sigma_wanted
        bt = bt.with_sigma(max(smax, 1e-5))
        bt.clamped, bt.sigma_wanted = cl, sw
        weak = 1
    bt2 = bt.retune(f0)
    bt2.clamped, bt2.sigma_wanted = bt.clamped, bt.sigma_wanted
    return bt2.with_bell(s) if s != 1.0 else bt2, weak


def model_peak_ms(bt, ratio_db=RATIO_DB, dur=0.8):
    wt = wt_for_ratio(bt, ratio_db)
    x = bt.impulse(FS, int(dur * FS), wt=wt)
    return f0_peak_ms(x, bt.v1()[0]), wt


def solve_zeta(n, t1b, target_ms, ratio_db=RATIO_DB, lo=None):
    """zeta_r in [zeta_lo(n), 4 %] whose model f0-peak (measured like the recordings) is the target: the smallest
    zeta_r with peak <= target (bisection; the peak falls with zeta_r). Returns (zeta_r, flag) with flag -1 when even
    zeta_lo peaks too early (clamped low), +1 when even 4 % peaks too late (clamped high)."""
    lo, hi = (zeta_lo(n) if lo is None else lo), ZR_HI

    def pk(z):
        return model_peak_ms(build_pair(n, z, t1b)[0], ratio_db)[0]
    if pk(lo) <= target_ms:
        return lo, -1
    if pk(hi) > target_ms:
        return hi, +1
    for _ in range(24):
        m = math.sqrt(lo * hi)
        if pk(m) > target_ms:
            lo = m
        else:
            hi = m
    return hi, 0


def felt_t60(n):
    """T3 felt (damper) T60 of V1: 0.5 s mid, x1.7 at F3-A3 and D6-F6 (ESTIMATE; lane A's vibes.json owns it)."""
    return 0.5 * float(np.interp(n, [57, 60, 84, 86], [1.7, 1.0, 1.0, 1.7]))


def design_keys(keys=range(21, 109), ratio_db=RATIO_DB, check=True, smooth_peaks=False):
    """Per key: T1a, T1b, the tube zeta_r (solved at the 22 reference notes, log-interpolated between them, the E
    curve scaled by the edge ratio outside F3..F6), sigma (solved for T1b, clamped, weak-regime limited), the
    achieved V1 T60, t_peak (model, measured and formula), wt, G, and the §4.5 integer-map check."""
    smooth = smooth_median(REF_T1B)
    peaks = smooth_median(REF_PEAK_MS) if smooth_peaks else REF_PEAK_MS
    zref, zflag, zwant = {}, {}, {}
    for n, tp in zip(REF_NOTES, peaks):
        zref[n], zflag[n] = solve_zeta(n, t1b_target(n, smooth), tp, ratio_db)
        zwant[n] = solve_zeta(n, t1b_target(n, smooth), tp, ratio_db, lo=0.005)    # §3.0's 0.5 % floor instead
    xs = REF_NOTES
    rows = []
    for n in keys:
        if n in zref:
            zr, zf = zref[n], zflag[n]
        elif xs[0] < n < xs[-1]:
            zr, zf = interp_semi(n, xs, [zref[k] for k in xs], log=True), 0
        else:
            e = xs[0] if n < xs[0] else xs[-1]
            zr, zf = min(ZR_HI, max(0.005, zeta_lo(n) * zref[e] / zeta_lo(e))), 0
        t1b = t1b_target(n, smooth)
        bt, weak = build_pair(n, zr, t1b)
        pk, wt = model_peak_ms(bt, ratio_db)
        zn, _ = bt.zetas()
        r = dict(n=n, f0=midi_hz(n), t1a=t1a_free(n), t1b=t1b, zeta_n=zn, zeta_r=zr, zeta_flag=zf,
                 zeta_r_floor05=zwant[n][0] if n in zwant else float("nan"),
                 sigma=bt.sigma, sigma_wanted=bt.sigma_wanted, sigma_clamp=bt.clamped, weak_limited=weak,
                 sigma_weak_max=bt.sigma_weak_max(), t60_v1=bt.t60_v1(),
                 t60_v1_bell127=bt.with_bell(S_MAX).t60_v1(), t60_v1_closed=bt.with_motor(G_C).t60_v1(),
                 peak_ms=pk, peak_target=dict(zip(REF_NOTES, peaks)).get(n),
                 peak_formula_ms=1000 * bt.t_peak_formula(), wt=wt, ref=n in zref, tube_cents=tube_cents(n),
                 felt_t60=felt_t60(n))
        r["regime_felt"] = "weak" if S_MAX * bt.sigma <= (zr - bt.with_damper(LN1000 / felt_t60(n)).zetas()[0]) else "strong"
        if check:
            w = pair_words(bt, wt, felt_t60(n), f0=midi_hz(n))
            rho, derr, cents, npts = map_check(bt, wt, midi_hz(n), felt_t60(n))
            r.update(G=w["G"], kc=w["kc"], k_bt=w["k_bt"], k_tb=w["k_tb"], wt_q28=w["wt_q28"], rho=rho, decay_err=derr,
                     v1_cents=cents, grid_points=npts)
            # max |x1| / max |x0| after the G choice (plan: <= 2)
            h0 = bt.impulse(FS, int(0.5 * FS), "bar")
            h1 = bt.impulse(FS, int(0.5 * FS), "tube")
            r["x1_over_x0"] = w["G"] * np.max(np.abs(h1)) / np.max(np.abs(h0))
        rows.append(r)
    return rows


def main(argv):
    out = os.path.join(ROOT, "build", "bartube")
    os.makedirs(out, exist_ok=True)
    sp = "--smooth-peaks" in argv                    # variant: the f0-peak times smoothed like T1b (same window)
    sfx = "_smoothpk" if sp else ""
    rows = design_keys(smooth_peaks=sp)
    cols = ["n", "f0", "t1a", "t1b", "zeta_n", "zeta_r", "zeta_flag", "zeta_r_floor05", "sigma", "sigma_wanted", "sigma_clamp",
            "weak_limited", "sigma_weak_max", "t60_v1", "t60_v1_bell127", "t60_v1_closed", "peak_ms", "peak_target",
            "peak_formula_ms", "wt", "G", "kc", "x1_over_x0", "k_bt", "k_tb", "wt_q28", "rho", "decay_err", "v1_cents",
            "regime_felt", "tube_cents", "felt_t60", "ref"]
    with open(os.path.join(out, "solve%s.csv" % sfx), "w") as fh:
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join(("%.6g" % r[c]) if isinstance(r[c], float) else str(r[c]) for c in cols) + "\n")
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    L = ["# bartube per-key solve (tools/bartube.py)", "",
         "zeta_r from the measured f0-peak times (model output measured with vib_ref_measure's procedure, tube +%.0f dB "
         "over the bar at 0.2 s%s), sigma from the +-3-key median of the measured V1-with-tube T60. Bounds: zeta_r "
         "max(0.73 %%, E curve)..4 %%, sigma 0.0015..0.007, then sigma <= 0.9 (zeta_r - zeta_n) / 1.2." % (
             RATIO_DB, "; peak times +-3-key median smoothed" if sp else "; raw per-note peak times"), "",
         "note | T1a | T1b | T60 got | zeta_r % (flag) | sigma (wanted) clamp | weak-max | peak ms got/target/formula | wt | G | x1/x0 | rho | dErr | V1 c"]
    for r in rows:
        if not r["ref"] and r["n"] not in (40, 100, 108, 21):
            continue
        nm = names[r["n"] % 12] + str(r["n"] // 12 - 1)
        L.append("%-4s %5.1f %5.1f %5.2f | %.2f (%+d) | %.4f (%.4f) %+d%s | %.4f | %3.0f / %s / %3.0f | %5.1f | %d | %.2f | %.9f | %.2g | %+.3f" % (
            nm, r["t1a"], r["t1b"], r["t60_v1"], 100 * r["zeta_r"], r["zeta_flag"], r["sigma"], r["sigma_wanted"],
            r["sigma_clamp"], " W" if r["weak_limited"] else "", r["sigma_weak_max"], r["peak_ms"],
            "-" if r["peak_target"] is None else "%3d" % r["peak_target"], r["peak_formula_ms"], r["wt"], r["G"],
            r["x1_over_x0"], r["rho"], r["decay_err"], r["v1_cents"]))
    ncl = sum(1 for r in rows if r["sigma_clamp"])
    L += ["", "keys with sigma clamped: %d of %d (hi %d, lo %d); weak-regime limited: %d; zeta_r clamped at the 22 refs: "
          "lo %d, hi %d" % (ncl, len(rows), sum(r["sigma_clamp"] > 0 for r in rows), sum(r["sigma_clamp"] < 0 for r in rows),
                           sum(r["weak_limited"] for r in rows), sum(r["zeta_flag"] < 0 for r in rows if r["ref"]),
                           sum(r["zeta_flag"] > 0 for r in rows if r["ref"])),
          "max rho %.9f; worst decay error vs the words' float design %.2g; V1 cents range %+.3f..%+.3f" % (
              max(r["rho"] for r in rows), max(r["decay_err"] for r in rows), min(r["v1_cents"] for r in rows),
              max(r["v1_cents"] for r in rows))]
    with open(os.path.join(out, "summary%s.txt" % sfx), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1:])
