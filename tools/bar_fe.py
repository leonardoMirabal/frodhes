# SPDX-License-Identifier: GPL-3.0-only
"""Finite-element model of a vibraphone bar: a free-free aluminium bar with a central undercut (VIBES PLAN T9b, T12,
T13a; [HA] eqs. 1-2).

Vertical bending is a Timoshenko beam (shear + rotary inertia: the bars are thick against their length), with a
height profile H(x) over the bar's length and a constant width. Two-node elements, deflection and rotation per node,
the shear term integrated at the element centre (no shear locking). Torsion is Saint-Venant on the same profile
(rectangular torsion constant, polar inertia of the section). The profile optimiser searches the [HA] cosine
family H(x) = A0 + sum_s A_s cos(2 pi s x / L) (symmetric about mid-bar, clipped to [h_min, t]) for the undercut
that puts V1 on f0 and V2 : V3 on 1 : 4 : 10, with a small curvature penalty.

    bar = Bar(L, w, t, profile)                    # profile: callable H(x) or an array of element heights
    f, phi = bar.bending(n=4)                      # Hz, mass-normalised shapes: phi(x, m) in 1/sqrt(kg)
    f, th = bar.torsion(n=2)                       # Hz, mass-normalised twist: th(x, m) in 1/sqrt(kg m^2)
    p = tune(f0, L, w, t)                          # the optimised profile: p["A"], p["f"], p["bar"], ...
    w = strike_weights(bar, x, y, nb=4, nt=2)      # |modal displacement| at (x, y): V1..V4, T1, T2

Units: SI. A mass-normalised bending shape phi_m(x) is the displacement of mode m at x; a torsion shape th_m(x) times
the off-axis distance y is the displacement there. Both have the same units, so V and T weights compare directly.
Material default: 6061 aluminium (E 69.39 GPa, rho 2693 kg/m^3, G 26.09 GPa).
"""
import math

import numpy as np
from scipy.linalg import eigh
from scipy.optimize import least_squares

E_AL, RHO_AL, G_AL, KAPPA = 69.39e9, 2693.0, 26.09e9, 5.0 / 6.0
N_ELEM = 120
N_TORS = 120


def rect_J(a, b):
    """Saint-Venant torsion constant of an a x b rectangle (any order): a b^3 (1/3 - 0.21 b/a (1 - b^4 / 12 a^4))."""
    a, b = np.maximum(a, b), np.minimum(a, b)
    return a * b ** 3 * (1.0 / 3.0 - 0.21 * b / a * (1 - b ** 4 / (12 * a ** 4)))


def _assemble(ke, me, nd, dof):
    """Global K, M from per-element matrices [n, 2 dof, 2 dof] of a chain of 2-node elements."""
    n, k = ke.shape[0], ke.shape[1]
    idx = (np.arange(n) * dof)[:, None] + np.arange(k)[None, :]
    rows = np.repeat(idx, k, axis=1).ravel()
    cols = np.tile(idx, (1, k)).ravel()
    K = np.zeros((nd, nd))
    M = np.zeros((nd, nd))
    np.add.at(K, (rows, cols), ke.reshape(n, -1).ravel())
    np.add.at(M, (rows, cols), me.reshape(n, -1).ravel())
    return K, M


class Bar:
    def __init__(self, L, w, t, profile=None, E=E_AL, rho=RHO_AL, G=G_AL, kappa=KAPPA, n_elem=N_ELEM):
        """L, w, t: length, width, stock thickness (m). profile: None (uniform t), a callable H(x) (m) or an array of
        n_elem element heights."""
        self.L, self.w, self.t = float(L), float(w), float(t)
        self.E, self.rho, self.G, self.kappa = E, rho, G, kappa
        self.n = n_elem
        self.le = self.L / n_elem
        xc = (np.arange(n_elem) + 0.5) * self.le
        if profile is None:
            H = np.full(n_elem, self.t)
        elif callable(profile):
            H = np.asarray(profile(xc), float)
        else:
            H = np.asarray(profile, float)
        assert H.shape == (n_elem,) and np.all(H > 0), "bad profile"
        self.H, self.xc = H, xc
        self._bend = {}
        self._tors = None

    def mass(self):
        return float(self.rho * self.w * np.sum(self.H) * self.le)

    # ---- vertical (or lateral) bending: Timoshenko, 2 dof per node (w, psi)
    def _bending_mats(self, lateral=False):
        n, le = self.n, self.le
        A = self.w * self.H
        I = self.H * self.w ** 3 / 12.0 if lateral else self.w * self.H ** 3 / 12.0
        EI, kGA = self.E * I, self.kappa * self.G * A
        B = np.array([-1.0 / le, -0.5, 1.0 / le, -0.5])                     # shear strain w' - psi at the centre
        kb = np.array([[0, 0, 0, 0], [0, 1, 0, -1], [0, 0, 0, 0], [0, -1, 0, 1]], float) / le
        ke = EI[:, None, None] * kb + (kGA * le)[:, None, None] * np.outer(B, B)
        mA, mI = self.rho * A * le / 6.0, self.rho * I * le / 6.0
        me = np.zeros((n, 4, 4))
        for (r, c), v in (((0, 0), 2), ((0, 2), 1), ((2, 0), 1), ((2, 2), 2)):
            me[:, r, c] = v * mA
        for (r, c), v in (((1, 1), 2), ((1, 3), 1), ((3, 1), 1), ((3, 3), 2)):
            me[:, r, c] = v * mI
        return _assemble(ke, me, 2 * (n + 1), 2)

    def bending(self, n=4, lateral=False):
        """(freqs Hz [n], shape(x, m)) for the n lowest elastic modes (the two rigid-body modes are dropped)."""
        key = (n, lateral)
        if key not in self._bend:
            K, M = self._bending_mats(lateral)
            w2, V = eigh(K, M, subset_by_index=[0, n + 1])
            f = np.sqrt(np.maximum(w2[2:], 0)) / (2 * np.pi)
            Wn = V[0::2, 2:]                                              # nodal deflections
            for m in range(n):                                            # sign: positive at x = 0
                if Wn[0, m] < 0:
                    Wn[:, m] = -Wn[:, m]
            self._bend[key] = (f, Wn)
        f, Wn = self._bend[key]
        xs = np.arange(self.n + 1) * self.le

        def shape(x, m):
            return float(np.interp(min(max(x, 0.0), self.L), xs, Wn[:, m]))
        return f, shape

    # ---- torsion: Saint-Venant, 1 dof per node (twist angle)
    def torsion(self, n=2):
        if self._tors is None:
            ne, le = self.n, self.le
            J = rect_J(self.w, self.H)
            Ip = self.rho * self.w * self.H * (self.w ** 2 + self.H ** 2) / 12.0
            ke = (self.G * J / le)[:, None, None] * np.array([[1.0, -1.0], [-1.0, 1.0]])
            me = (Ip * le / 6.0)[:, None, None] * np.array([[2.0, 1.0], [1.0, 2.0]])
            K, M = _assemble(ke, me, ne + 1, 1)
            w2, V = eigh(K, M, subset_by_index=[0, 6])
            f = np.sqrt(np.maximum(w2[1:], 0)) / (2 * np.pi)              # drop the rigid rotation
            Tn = V[:, 1:]
            for m in range(Tn.shape[1]):
                if Tn[0, m] < 0:
                    Tn[:, m] = -Tn[:, m]
            self._tors = (f, Tn)
        f, Tn = self._tors
        xs = np.arange(self.n + 1) * self.le

        def shape(x, m):
            return float(np.interp(min(max(x, 0.0), self.L), xs, Tn[:, m]))
        return f[:n], shape

    def v1_nodes(self):
        """x positions of V1's two nodes (m)."""
        f, sh = self.bending(1)
        xs = np.linspace(0, self.L, 2001)
        v = np.array([sh(x, 0) for x in xs])
        z = np.flatnonzero(np.diff(np.sign(v)) != 0)
        return [float(xs[i] - v[i] * (xs[i + 1] - xs[i]) / (v[i + 1] - v[i])) for i in z]


# ---------------------------------------------------------------- the profile optimiser
def cos_profile(A, L, t, h_min):
    """H(x) = clip(A0 + sum_s A_s cos(2 pi s x / L), h_min, t)."""
    def H(x):
        x = np.asarray(x, float)
        v = A[0] + sum(A[s] * np.cos(2 * np.pi * s * x / L) for s in range(1, len(A)))
        return np.clip(v, h_min, t)
    return H


def _curvature(A, L):
    """Mean square of H''(x) over the bar, scaled to mm (the [HA] smoothness term, eps_2)."""
    x = np.linspace(0, L, 201)
    d2 = sum(-A[s] * (2 * np.pi * s / L) ** 2 * np.cos(2 * np.pi * s * x / L) for s in range(1, len(A)))
    return float(np.sqrt(np.mean(d2 ** 2))) * 1e3 * L * L


def tune(f0, L, w, t, ratios=(4.0, 10.0), h_min=1.5e-3, n_shape=3, alpha=0.02, n_elem=N_ELEM, A_init=None, **mat):
    """The undercut profile (A0..A_n) that puts V1 on f0 and V2/V1, V3/V1 on ratios, with a small curvature penalty
    (alpha: weight in cents per unit of the curvature term). Several starting depths; the best converged one wins,
    then a polish without the penalty if it left more than 0.05 c. ratios may be shorter (the top octave tunes
    V2 only: 1:4:10 does not fit a 13 mm bar that short). A_init: start from this profile only (continuation from
    the neighbouring key, so the profile family stays the same along the keyboard); the multi-start is the fallback
    if it does not converge to 1 c. Returns dict(A, f (V1..V4), ratios, err_c, bar)."""
    targets = np.array([f0] + [f0 * r for r in ratios])

    def model(A):
        bar = Bar(L, w, t, cos_profile(A, L, t, h_min), n_elem=n_elem, **mat)
        f, _ = bar.bending(len(targets))
        return f, bar

    def resid(A, a):
        f, _ = model(A)
        r = 1200 * np.log2(f / targets)
        return np.concatenate([r, [a * _curvature(A, L)]]) if a else r

    best = None
    if A_init is not None:
        r = least_squares(resid, np.array(A_init, float), args=(alpha,), x_scale=1e-3, diff_step=1e-4, max_nfev=400)
        err = np.abs(resid(r.x, 0))
        if np.max(err) < 1.0:
            best = (float(np.max(err)) + alpha * _curvature(r.x, L), r.x, float(np.max(err)))
    for depth in (() if best else (0.3, 0.5, 0.7, 0.85)):
        hc = t - depth * (t - h_min)
        A0 = np.zeros(n_shape + 1)
        A0[0], A0[1] = 0.5 * (t + hc), 0.5 * (t - hc)
        # a cut confined to the middle: a little of the 2nd harmonic flattens the ends
        A0[2] = -0.1 * (t - hc)
        A0[0] += 0.1 * (t - hc)
        try:
            r = least_squares(resid, A0, args=(alpha,), x_scale=1e-3, diff_step=1e-4, max_nfev=400)
        except Exception:
            continue
        err = np.abs(resid(r.x, 0))
        score = float(np.max(err)) + alpha * _curvature(r.x, L)
        if best is None or score < best[0]:
            best = (score, r.x, float(np.max(err)))
    A = best[1]
    if best[2] > 0.05:
        r = least_squares(resid, A, args=(0,), x_scale=1e-3, diff_step=1e-5, max_nfev=400, xtol=1e-14, ftol=1e-14)
        A = r.x
    f, bar = model(A)
    f4, _ = bar.bending(4)
    return dict(A=[float(a) for a in A], f=[float(x) for x in f4], ratios=[float(x / f4[0]) for x in f4],
                err_c=[float(x) for x in 1200 * np.log2(f / targets)], bar=bar, curv=_curvature(A, L))


# ---------------------------------------------------------------- strike weights
def strike_weights(bar, x, y, nb=4, nt=2):
    """|modal displacement| per unit modal coordinate at the strike point (x along, y off the centreline, m):
    [|phi_V1(x)|, .., |phi_Vnb(x)|, |y th_T1(x)|, .., |y th_Tnt(x)|] (1/sqrt(kg)). Vertical bending does not depend
    on y; torsion is zero on the centreline."""
    fb, sb = bar.bending(nb)
    ft, st = bar.torsion(nt)
    return [abs(sb(x, m)) for m in range(nb)] + [abs(y * st(x, m)) for m in range(nt)]


def outline(n, lo_key=53, hi_key=89, L_mm=(381.0, 175.0), w_mm=(57.0, 38.0)):
    """(L, w) in m for MIDI key n: geometric interpolation in semitones between the F3 and F6 bars, clamped."""
    k = min(max(n, lo_key), hi_key)
    u = (k - lo_key) / (hi_key - lo_key)
    L = L_mm[0] * (L_mm[1] / L_mm[0]) ** u
    w = w_mm[0] * (w_mm[1] / w_mm[0]) ** u
    return L * 1e-3, w * 1e-3


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)
