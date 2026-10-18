# SPDX-License-Identifier: GPL-3.0-only
"""Machine-independent eigen-analysis of the small (4 x 4) matrices of the VIBES pair (bartube): the same bits on
every machine, which np.linalg.eig is not.

Why: LAPACK's eig (Apple Accelerate here, OpenBLAS on Linux, each version its own) differs in the last bits between
machines, and the pair's quantities are ill-conditioned functions of the eigenvalues: a decay is -ln|lambda| fs of a
lambda within ~1e-5 of the unit circle (the per-sample map), or -Re(lambda) next to an imaginary part 1e4 x larger
(the continuous pair), so 1e-16 in lambda is ~1e-11..1e-9 in the decay. gen_bars bisects on those decays (bartube
solve_sigma, the kc correction of pair_words) and rounds the results to integer words, so the machines' bits picked
different words (tools/gen_bars.py was not reproducible across machines).

How: every step is decimal arithmetic at PREC digits (Python's decimal module: software arithmetic, the same result
everywhere): the matrix entries are taken exactly (Decimal(float) is exact), the characteristic polynomial and the
adjugate come from the Faddeev-LeVerrier recursion, each eigenvalue is Newton-polished on the polynomial from
np.linalg.eigvals' estimate (only a starting point: Newton converges to the same root to ~PREC digits from any
machine's estimate), eigenvector directions are a column of adj(lambda I - A) and residues adj(lambda I - A) B /
p'(lambda). ln and sqrt are decimal's (correctly rounded); atan2 is a decimal series here. The floats returned are
the correctly rounded conversions of results good to far more digits than a float holds, so they are the same on
every machine (and more accurate than LAPACK's).
"""
import decimal
from decimal import Decimal

import numpy as np

PREC = 60
CTX = decimal.Context(prec=PREC, rounding=decimal.ROUND_HALF_EVEN, Emin=-999999, Emax=999999)
D0, D1, D2 = Decimal(0), Decimal(1), Decimal(2)
PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459230781640628620899862803")
TOL = Decimal(10) ** -(PREC - 8)
CLUSTERS = []                       # matrices where two estimates polished onto one root (diagnostic)


def dec(x):
    """An exact Decimal of a float / int (or a Decimal unchanged)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, (int, np.integer)):
        return Decimal(int(x))
    return Decimal(float(x))


def dsum(xs):
    """A sum in CTX (the builtin sum would round in the default 28-digit context)."""
    s = D0
    for x in xs:
        s = CTX.add(s, x)
    return s


def dmat(A):
    """A square matrix (numpy or nested lists of float / int / Decimal) as nested lists of exact Decimals."""
    return [[dec(x) for x in row] for row in (A.tolist() if isinstance(A, np.ndarray) else A)]


# ---------------------------------------------------------------- complex decimal (pairs)
def cmul(a, b):
    return (CTX.subtract(CTX.multiply(a[0], b[0]), CTX.multiply(a[1], b[1])),
            CTX.add(CTX.multiply(a[0], b[1]), CTX.multiply(a[1], b[0])))


def cadd(a, b):
    return (CTX.add(a[0], b[0]), CTX.add(a[1], b[1]))


def cabs2(a):
    return CTX.add(CTX.multiply(a[0], a[0]), CTX.multiply(a[1], a[1]))


def cdiv(a, b):
    d = cabs2(b)
    return (CTX.divide(CTX.add(CTX.multiply(a[0], b[0]), CTX.multiply(a[1], b[1])), d),
            CTX.divide(CTX.subtract(CTX.multiply(a[1], b[0]), CTX.multiply(a[0], b[1])), d))


def to_complex(a):
    return complex(float(a[0]), float(a[1]))


# ---------------------------------------------------------------- real decimal helpers
def datan(x):
    """atan(x), |x| <= 1, in CTX: three half-angle reductions, then the Taylor series."""
    k = 0
    while abs(x) > Decimal("0.05"):
        x = CTX.divide(x, CTX.add(D1, CTX.sqrt(CTX.add(D1, CTX.multiply(x, x)))))
        k += 1
    x2, term, s, n = CTX.multiply(x, x), x, x, 1
    while True:
        term = CTX.minus(CTX.multiply(term, x2))
        n += 2
        t = CTX.divide(term, Decimal(n))
        if abs(t) < TOL * Decimal("1e-8"):
            break
        s = CTX.add(s, t)
    return CTX.multiply(s, Decimal(1 << k))


def datan2(y, x):
    """atan2(y, x) in (-pi, pi] (decimal)."""
    if x == 0 and y == 0:
        return D0
    if abs(y) <= abs(x):
        a = datan(CTX.divide(y, x))
        if x > 0:
            return a
        return CTX.add(a, PI) if y >= 0 else CTX.subtract(a, PI)
    a = CTX.subtract(CTX.divide(PI, D2), datan(CTX.divide(x, y)))
    return a if y > 0 else CTX.subtract(a, PI)


# ---------------------------------------------------------------- the analysis
def _matmul(A, B):
    n = len(A)
    return [[CTX.add(CTX.add(CTX.multiply(A[i][0], B[0][j]), CTX.multiply(A[i][1], B[1][j])),
                     CTX.add(CTX.multiply(A[i][2], B[2][j]), CTX.multiply(A[i][3], B[3][j])))
             if n == 4 else dsum(CTX.multiply(A[i][k], B[k][j]) for k in range(n)) for j in range(n)]
            for i in range(n)]


def faddeev(A):
    """(c, Ms): det(s I - A) = sum c[i] s^i (c[n] = 1) and adj(s I - A) = sum_{k=1..n} Ms[k-1] s^(n-k)."""
    n = len(A)
    c = [D0] * (n + 1)
    c[n] = D1
    Mk = [[D0] * n for _ in range(n)]
    Ms = []
    for k in range(1, n + 1):
        Mk = _matmul(A, Mk) if k > 1 else [[D0] * n for _ in range(n)]
        for i in range(n):
            Mk[i][i] = CTX.add(Mk[i][i], c[n - k + 1])
        Ms.append(Mk)
        AM = _matmul(A, Mk)
        c[n - k] = CTX.minus(CTX.divide(dsum(AM[i][i] for i in range(n)), Decimal(k)))
    return c, Ms


def _poly(c, z):
    """(p(z), p'(z)) by Horner, complex decimal z."""
    p, dp = (c[-1], D0), (D0, D0)
    for a in reversed(c[:-1]):
        dp = cadd(cmul(dp, z), p)
        p = cadd(cmul(p, z), (a, D0))
    return p, dp


def _newton(c, z0):
    """Newton on p from z0: to TOL (a simple root: quadratic convergence), or, near a multiple root (linear
    convergence, attainable accuracy ~ the square root of the working precision, ~1e-30 here), until a step stops
    shrinking. Either way far below a float's resolution, so the rounded float is the same from any start."""
    z = (dec(z0.real), dec(z0.imag))
    prev = None
    for it in range(400):
        p, dp = _poly(c, z)
        if dp == (D0, D0):
            break
        dz = cdiv(p, dp)
        z = (CTX.subtract(z[0], dz[0]), CTX.subtract(z[1], dz[1]))
        a = cabs2(dz)
        if a <= CTX.multiply(cabs2(z), TOL * TOL):
            break
        if prev is not None and it > 8 and a >= prev and a <= CTX.multiply(cabs2(z), Decimal("1e-50")):
            break
        prev = a
    else:
        raise ArithmeticError("eig4: Newton did not converge from %r" % (z0,))
    return z, _poly(c, z)[1]


class Eig:
    """The eigen-analysis of one real square matrix A (any float / int / Decimal entries, taken exactly):
    .lam = [(re, im) decimal] for every eigenvalue, ordered as np.linalg.eigvals' estimates were (the order does not
    matter to the callers, which sort or select by value)."""

    def __init__(self, A):
        self.A = dmat(A)
        self.n = len(self.A)
        self.c, self.Ms = faddeev(self.A)
        est = np.linalg.eigvals(np.array([[float(x) for x in r] for r in self.A]))
        self.lam, self.dp = [], []
        for z0 in est:
            z, dp = _newton(self.c, complex(z0))
            self.lam.append(z)
            self.dp.append(dp)
        for i in range(self.n):                         # a cluster: two estimates on one root (counted, see CLUSTERS)
            for j in range(i):
                d = (CTX.subtract(self.lam[i][0], self.lam[j][0]), CTX.subtract(self.lam[i][1], self.lam[j][1]))
                if cabs2(d) <= CTX.multiply(cabs2(self.lam[i]), Decimal("1e-40")):
                    CLUSTERS.append([to_complex(z) for z in self.lam])

    def adj(self, i):
        """adj(lambda_i I - A) (complex decimal entries)."""
        z = self.lam[i]
        n = self.n
        P = [[(self.Ms[0][r][s], D0) for s in range(n)] for r in range(n)]
        for k in range(1, n):
            P = [[cadd(cmul(P[r][s], z), (self.Ms[k][r][s], D0)) for s in range(n)] for r in range(n)]
        return P

    def vector(self, i):
        """The right eigenvector of lambda_i (unnormalised): adj's column of the largest norm."""
        P = self.adj(i)
        n = self.n
        norms = [dsum(cabs2(P[r][s]) for r in range(n)) for s in range(n)]
        j = max(range(n), key=lambda s: norms[s])
        return [P[r][j] for r in range(n)]

    def residue(self, i, B, rows):
        """Residues at lambda_i of e_r^T (s I - A)^-1 B for r in rows: (adj(lambda_i I - A) B)_r / p'(lambda_i)."""
        P = self.adj(i)
        Bd = [dec(b) for b in B]
        out = []
        for r in rows:
            num = (D0, D0)
            for s in range(self.n):
                if Bd[s] != 0:
                    num = cadd(num, cmul(P[r][s], (Bd[s], D0)))
            out.append(to_complex(cdiv(num, self.dp[i])))
        return out


def map_modes(M, fs):
    """[(f_hz, sigma_per_s, bar_share)] of a per-sample map's oscillatory eigenpairs (positive frequencies), sorted
    by frequency: f = arg(lambda) fs / 2 pi, sigma = -ln|lambda| fs, share = |v0|^2 + |v1|^2 over |v|^2."""
    e = Eig(M)
    fsd = dec(fs)
    out = []
    for i, z in enumerate(e.lam):
        if z[1] > 0:
            v = e.vector(i)
            tot = dsum(cabs2(x) for x in v)
            share = CTX.divide(CTX.add(cabs2(v[0]), cabs2(v[1])), tot) if tot else D0
            f = CTX.divide(CTX.multiply(datan2(z[1], z[0]), fsd), CTX.multiply(D2, PI))
            sig = CTX.minus(CTX.multiply(CTX.divide(CTX.ln(cabs2(z)), D2), fsd))
            out.append((float(f), float(sig), float(share)))
    return sorted(out, key=lambda m: m[0])


def spectral_radius(M):
    """max |lambda| of a real matrix."""
    e = Eig(M)
    return float(max(CTX.sqrt(cabs2(z)) for z in e.lam))


def ss_modes(A, B, rows=(0, 1)):
    """[(f_hz, sigma, residue_row0, residue_row1, ...)] of a continuous state-space pair dx/dt = A x + B u, for the
    eigenvalues with a positive imaginary part, sorted by frequency: f = Im(lambda) / 2 pi, sigma = -Re(lambda)."""
    e = Eig(A)
    out = []
    for i, z in enumerate(e.lam):
        if z[1] > 0:
            f = float(CTX.divide(z[1], CTX.multiply(D2, PI)))
            out.append((f, float(CTX.minus(z[0])), *e.residue(i, B, rows)))
    return sorted(out, key=lambda m: m[0])
