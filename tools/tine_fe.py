# SPDX-License-Identifier: GPL-3.0-only
"""Finite-element model of a Rhodes tine: a clamped steel rod of the service-manual length with the tuning spring
as a point mass (IMPLEMENTATION S2/S4; review 01 #6).

A uniform cantilever has partial ratios 1 : 6.267 : 17.55. The tuning spring's mass raises the 2nd partial towards
the measured 7.11 at F1 and changes the ratios per key, so the model gives per-key ratios, mode shapes at the strike
point and the tip, and modal masses.

    model = Tine(length_m, spring_pos=0.65, spring_mass=0.0)
    f, shapes = model.modes(n=6)                 # Hz, mass-normalised mode shapes (callable at any x)
    m = tune_spring(length_m, f_target, pos)     # spring mass (kg) that puts f1 on f_target

Material: 0.075" piano wire (R05 W11): d = 1.905 mm, E = 200 GPa, rho = 7850 kg/m^3.
"""
import numpy as np
from scipy.linalg import eigh

D_TINE = 0.075 * 0.0254          # m
E_STEEL = 200e9                  # Pa
RHO_STEEL = 7850.0               # kg/m^3
N_ELEM = 40


def _hermite(xi, le):
    """Cubic Hermite shape functions (w1, th1, w2, th2) at xi in [0, 1] of an element of length le."""
    return np.array([1 - 3 * xi**2 + 2 * xi**3,
                     le * (xi - 2 * xi**2 + xi**3),
                     3 * xi**2 - 2 * xi**3,
                     le * (-xi**2 + xi**3)])


class Tine:
    def __init__(self, length_m, spring_pos=0.65, spring_mass=0.0, d=D_TINE, E=E_STEEL, rho=RHO_STEEL,
                 n_elem=N_ELEM):
        self.L, self.pos, self.ms = float(length_m), float(spring_pos), float(spring_mass)
        self.n = n_elem
        A = np.pi * d * d / 4
        I = np.pi * d**4 / 64
        self.rhoA, self.EI = rho * A, E * I
        le = self.L / n_elem
        self.le = le
        k = self.EI / le**3 * np.array([[12, 6 * le, -12, 6 * le],
                                         [6 * le, 4 * le**2, -6 * le, 2 * le**2],
                                         [-12, -6 * le, 12, -6 * le],
                                         [6 * le, 2 * le**2, -6 * le, 4 * le**2]])
        m = self.rhoA * le / 420 * np.array([[156, 22 * le, 54, -13 * le],
                                              [22 * le, 4 * le**2, 13 * le, -3 * le**2],
                                              [54, 13 * le, 156, -22 * le],
                                              [-13 * le, -3 * le**2, -22 * le, 4 * le**2]])
        ndof = 2 * (n_elem + 1)
        K = np.zeros((ndof, ndof))
        M = np.zeros((ndof, ndof))
        for e in range(n_elem):
            i = 2 * e
            K[i:i + 4, i:i + 4] += k
            M[i:i + 4, i:i + 4] += m
        if self.ms > 0:                               # point mass via the shape functions (exact for any position)
            e, xi = self._locate(self.pos * self.L)
            N = _hermite(xi, le)
            i = 2 * e
            M[i:i + 4, i:i + 4] += self.ms * np.outer([N[0], N[1], N[2], N[3]], [N[0], N[1], N[2], N[3]]) \
                * np.array([[1, 0, 1, 0], [0, 0, 0, 0], [1, 0, 1, 0], [0, 0, 0, 0]])
        self.K, self.M = K[2:, 2:], M[2:, 2:]         # clamp: w = theta = 0 at x = 0

    def _locate(self, x):
        e = min(int(x / self.le), self.n - 1)
        return e, (x - e * self.le) / self.le

    def modes(self, n=6):
        """(freqs in Hz, shapes) for the n lowest modes. shapes(x, m) = displacement of mass-normalised mode m."""
        w2, V = eigh(self.K, self.M, subset_by_index=[0, n - 1])
        f = np.sqrt(np.maximum(w2, 0)) / (2 * np.pi)
        full = np.vstack([np.zeros((2, n)), V])       # re-insert the clamped dofs

        def shape(x, m):
            e, xi = self._locate(min(max(x, 0.0), self.L))
            N = _hermite(xi, self.le)
            i = 2 * e
            return float(N @ full[i:i + 4, m])
        return f, shape

    def effective_mass(self, x, m, shape):
        """Effective mass of mode m seen at position x (kg): 1 / phi_m(x)^2 for mass-normalised modes."""
        p = shape(x, m)
        return float("inf") if p == 0 else 1.0 / (p * p)


def tune_spring(length_m, f_target, spring_pos=0.65, **kw):
    """Spring mass (kg) that brings the fundamental to f_target. Raises ValueError if the bare tine is already
    below the target (it cannot be tuned up by adding mass)."""
    f_bare = Tine(length_m, spring_pos, 0.0, **kw).modes(1)[0][0]
    if f_bare < f_target:
        raise ValueError(f"bare tine {f_bare:.2f} Hz is below the target {f_target:.2f} Hz")
    lo, hi = 0.0, 0.05
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f1 = Tine(length_m, spring_pos, mid, **kw).modes(1)[0][0]
        if f1 > f_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def load_lengths(path):
    """{midi: length_m} from docs/model/data/tine_lengths.csv."""
    out = {}
    for line in open(path):
        if line.startswith("#") or line.startswith("midi"):
            continue
        k, v = line.strip().split(",")
        out[int(k)] = float(v)
    return out


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)
