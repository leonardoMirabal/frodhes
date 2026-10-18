# SPDX-License-Identifier: GPL-3.0-only
"""Portable Rhodes tables: the pinned tine finite-element results (docs/model/data/rhodes_pins.json).

The tine FE eigenproblem (tine_fe: 78 dofs, K and M each conditioned ~1e9) is ill-conditioned: scipy eigh gives the
partials to only ~1e-7 relative, and the last bits depend on the BLAS / LAPACK build (Accelerate on macOS, OpenBLAS
on Linux). tune_spring's 60-step bisection, the hammer contacts and the integer rounding downstream turn those bits
into different header words (533 values on arm64 Linux). So gen_rhodes.py reads each key's FE results (spring mass,
the 6 partials, the mode shapes at the strike point and the tip) from this file, written on the reference Mac, and
every machine builds the same rhodes_tables.h. Everything after the FE (the 2-DOF tine-bar, the hammer, the golden
model's calibration) then gives the same integer words, measured on macOS arm64, Linux arm64 and Linux x86_64 (only
rhodes_spec.json's floats may differ in the last ulps).

Honesty: exact() recomputes the same quantities with 60-digit decimal arithmetic (banded LDL^T, Rayleigh quotient
iteration, Sylvester inertia for the mode index, Newton on the spring mass) and tests/py/test_gen_rhodes_portable.py
checks that the pins agree with it to the FE solver's float accuracy: the pins are the model's values, not tuning.

    python3 tools/gen_rhodes.py --repin      # recompute the FE on this machine and rewrite the pins (model changes)
"""
import hashlib
import json
import os
from decimal import Decimal as D, localcontext

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PINS_PATH = os.path.join(ROOT, "docs", "model", "data", "rhodes_pins.json")
LEN_PATH = os.path.join(ROOT, "docs", "model", "data", "tine_lengths.csv")
NMODE = 6
PREC = 60


def fingerprint(p):
    """sha256 of every input the FE results depend on (a change means: --repin)."""
    import tine_fe as fe
    src = dict(lengths=open(LEN_PATH, "rb").read().decode(), spring_pos=p["spring_pos"], strike=p["strike"],
               fe=[fe.D_TINE, fe.E_STEEL, fe.RHO_STEEL, fe.N_ELEM], nmode=NMODE)
    return hashlib.sha256(json.dumps(src, sort_keys=True).encode()).hexdigest()


def load(p):
    """{midi: dict(L, ms, f, psi_c, psi_t)} or None when the pins are missing or stale."""
    if not os.path.exists(PINS_PATH):
        return None
    d = json.load(open(PINS_PATH))
    if d.get("fingerprint") != fingerprint(p):
        return None
    return {int(k): v for k, v in d["keys"].items()}


def save(p, keys):
    """One key per line; json writes floats as repr, which reads back bit for bit."""
    head = dict(_doc=__doc__.split("\n\n")[1].replace("\n", " "), fingerprint=fingerprint(p))
    lines = ['  "%d": %s' % (n, json.dumps({k: keys[n][k] for k in ("L", "ms", "f", "psi_c", "psi_t")}))
             for n in sorted(keys)]
    txt = json.dumps(head, indent=1)[:-2] + ',\n "keys": {\n' + ",\n".join(lines) + "\n }\n}\n"
    assert {int(k): v for k, v in json.loads(txt)["keys"].items()} == {n: keys[n] for n in keys}
    open(PINS_PATH, "w").write(txt)


# ---------------------------------------------------------------- exact (60-digit decimal) recomputation
def _pi():
    """pi by the decimal-module recipe (Python docs)."""
    with localcontext() as c:
        c.prec += 2
        three = D(3)
        lasts, t, s, n, na, d, da = 0, three, 3, 1, 0, 0, 24
        while s != lasts:
            lasts = s
            n, na = n + na, na + 8
            d, da = d + da, da + 32
            t = (t * n) / d
            s += t
    return +s


def _hermite(xi, le):
    return [1 - 3 * xi ** 2 + 2 * xi ** 3, le * (xi - 2 * xi ** 2 + xi ** 3), 3 * xi ** 2 - 2 * xi ** 3,
            le * (-xi ** 2 + xi ** 3)]


class ExactTine:
    """tine_fe.Tine in decimal: K, M as symmetric bands (half-bandwidth 3) of the clamped 78-dof system."""
    BW = 3

    def __init__(self, L, pos, ms, n_elem=40):
        pi = _pi()
        d = D("0.075") * D("0.0254")
        self.L, self.n = L, n_elem
        self.rhoA = D("7850") * pi * d * d / 4
        self.EI = D("200e9") * pi * d ** 4 / 64
        le = L / n_elem
        self.le = le
        k = [[12, 6 * le, -12, 6 * le], [6 * le, 4 * le ** 2, -6 * le, 2 * le ** 2],
             [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le ** 2, -6 * le, 4 * le ** 2]]
        m = [[156, 22 * le, 54, -13 * le], [22 * le, 4 * le ** 2, 13 * le, -3 * le ** 2],
             [54, 13 * le, 156, -22 * le], [-13 * le, -3 * le ** 2, -22 * le, 4 * le ** 2]]
        ck, cm = self.EI / le ** 3, self.rhoA * le / 420
        ndof = 2 * (n_elem + 1)
        K = [[D(0)] * ndof for _ in range(ndof)]
        M = [[D(0)] * ndof for _ in range(ndof)]
        for e in range(n_elem):
            i = 2 * e
            for a in range(4):
                for b in range(4):
                    K[i + a][i + b] += ck * k[a][b]
                    M[i + a][i + b] += cm * m[a][b]
        # the spring's point mass (w dofs only) and its derivative dM/dms
        e, xi = self._locate(pos * L)
        N = _hermite(xi, le)
        self.P = {}
        for a in (0, 2):
            for b in (0, 2):
                self.P[(2 * e + a - 2, 2 * e + b - 2)] = N[a] * N[b]
                M[2 * e + a][2 * e + b] += ms * N[a] * N[b]
        self.K = [row[2:] for row in K[2:]]
        self.M = [row[2:] for row in M[2:]]
        self.N = ndof - 2

    def _locate(self, x):
        e = min(int(x / self.le), self.n - 1)
        return e, (x - e * self.le) / self.le

    def _ldl(self, s):
        """Banded LDL^T of K - s M: (L rows as {j: l}, D) without pivoting."""
        n, bw = self.N, self.BW
        K, M = self.K, self.M
        Ld = [dict() for _ in range(n)]
        dd = [D(0)] * n
        for j in range(n):
            dj = K[j][j] - s * M[j][j] - sum(Ld[j][k] ** 2 * dd[k] for k in Ld[j])
            dd[j] = dj
            for i in range(j + 1, min(n, j + bw + 1)):
                v = K[i][j] - s * M[i][j] - sum(Ld[i][k] * Ld[j][k] * dd[k] for k in Ld[i] if k in Ld[j])
                Ld[i][j] = v / dj
        return Ld, dd

    def count_below(self, s):
        """Number of eigenvalues below s (Sylvester's law of inertia)."""
        return sum(1 for x in self._ldl(s)[1] if x < 0)

    def _solve(self, s, b):
        Ld, dd = self._ldl(s)
        n = self.N
        y = list(b)
        for i in range(n):
            y[i] -= sum(Ld[i][k] * y[k] for k in Ld[i])
        y = [y[i] / dd[i] for i in range(n)]
        for i in reversed(range(n)):
            for k, l in Ld[i].items():
                y[k] -= l * y[i]
        return y

    def _mv(self, A, x):
        n, bw = self.N, self.BW
        return [sum(A[i][j] * x[j] for j in range(max(0, i - bw), min(n, i + bw + 1))) for i in range(n)]

    def _dot(self, a, b):
        return sum(x * y for x, y in zip(a, b))

    def mode(self, m, s0):
        """(lambda, mass-normalised vector) of the m-th lowest mode (0-based) from a shift s0 near it."""
        x = [D(1)] * self.N
        s = s0
        for it in range(40):
            y = self._solve(s, self._mv(self.M, x))
            nrm = self._dot(y, self._mv(self.M, y)).sqrt()
            x = [v / nrm for v in y]
            s_new = self._dot(x, self._mv(self.K, x))
            if it >= 2 and abs(s_new - s) <= abs(s) * D(10) ** (-PREC + 8):
                s = s_new
                break
            if it >= 1:                          # two inverse iterations at s0, then Rayleigh quotient iteration
                s = s_new
        eps = abs(s) * D(10) ** -30
        assert self.count_below(s - eps) == m and self.count_below(s + eps) == m + 1, "not mode %d" % m
        return s, x

    def shape(self, x_vec, x):
        e, xi = self._locate(min(max(x, D(0)), self.L))
        N = _hermite(xi, self.le)
        full = [D(0), D(0)] + list(x_vec)
        return sum(N[a] * full[2 * e + a] for a in range(4))


def exact_L(n):
    """The tine length of key n (tine_lengths.csv; log-log least-squares extrapolation over the nearest 12 keys)."""
    L = {}
    for line in open(LEN_PATH):
        if line.startswith("#") or line.startswith("midi"):
            continue
        k, v = line.strip().split(",")
        L[int(k)] = D(v)
    if n in L:
        return L[n]
    keys = sorted(L)
    near = keys[:12] if n < keys[0] else keys[-12:]
    lnf = lambda k: D(440).ln() + D(2).ln() * (k - 69) / 12
    x = [lnf(k) for k in near]
    y = [L[k].ln() for k in near]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    a = sum((u - mx) * (v - my) for u, v in zip(x, y)) / sum((u - mx) ** 2 for u in x)
    b = my - a * mx
    return (a * lnf(n) + b).exp()


def exact(p, n, pin):
    """The exact FE results of key n (the same fields as a pin), started from the pin's values (any start in the
    basin gives the same result; the inertia check proves the mode index)."""
    with localcontext() as c:
        c.prec = PREC
        pi = _pi()
        f0 = D(440) * (D(2).ln() * (n - 69) / 12).exp()
        w0 = (2 * pi * f0) ** 2
        L = exact_L(n)
        pos = D(repr(p["spring_pos"]))
        s = p["strike"]
        xc = min(D(repr(s["lo_m"])) + (D(repr(s["hi_m"])) - D(repr(s["lo_m"]))) * (n - 21) / (108 - 21), D("0.9") * L)
        lam = lambda f: (2 * pi * D(repr(f))) ** 2
        if pin["ms"] is None:                        # the bare tine is below f0: no spring
            ms = D(0)
            t = ExactTine(L, pos, ms)
            l0, _ = t.mode(0, lam(pin["f"][0]))
            assert l0 < w0, "key %d: the bare tine is above f0, it must have a spring" % n
        else:
            ms = D(repr(pin["ms"]))
            for _ in range(20):                      # Newton on the spring mass: d lambda / d ms = -lambda x^T P x
                t = ExactTine(L, pos, ms)
                l0, x = t.mode(0, lam(pin["f"][0]) if _ == 0 else l0)
                dl = -l0 * sum(v * x[i] * x[j] for (i, j), v in t.P.items() if i >= 0 and j >= 0)
                step = (l0 - w0) / dl
                ms -= step
                if abs(step) <= ms * D(10) ** (-PREC + 10):
                    break
            t = ExactTine(L, pos, ms)
        f, pc, pt = [], [], []
        for m in range(NMODE):
            lm, x = t.mode(m, lam(pin["f"][m]))
            f.append(lm.sqrt() / (2 * pi))
            pc.append(t.shape(x, xc))
            pt.append(t.shape(x, L))
        return dict(L=L, ms=ms if pin["ms"] is not None else None, f=f, psi_c=pc, psi_t=pt)
