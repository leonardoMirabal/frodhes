#!/usr/bin/env python3
"""v3 review scratch: the radial pickup in integers, three interpolation forms over the SAME table grid
(N = 2048 cells over s = 0..32 gap^2, i.e. RH_PU_SH = 18 with s in Q24 gap^2):

  HERM   y = H(s[n]) - H(s[n-1]),  H = cubic Hermite of an int32 Phi~(s) table + an int32 slope table (Q30-scaled),
         Horner with a Q16 fraction: 3 multiplies per evaluation (the previous H is kept), + 2 for s.
  HERM15 the same with the slope table in Q15 (what V01 S1 proposed).
  MID    y = D(s_mid) * ds (V01 S4: midpoint gain, exact ds), D as a Q15 lerp table.
  LERP   y = L(s[n]) - L(s[n-1]) with a linear interpolation of the int32 Phi~ table (the bad case).

Geometry: R03 code family in gap units (L_hor = 1, R_p = 1), voicing offset L_ver = -0.2 (ESTIMATE, as V01).
The reference is the exact Phi(x[n]+L_ver) - Phi(x[n-1]+L_ver) in float64 with Phi from a 2^20-point quadrature.
Tip amplitudes 1.5 gap ("hard") and -30 dB ("soft"), exponentially decaying over 1 s (ESTIMATE, as V01).
Also: the alias ratio of the integer HERM form at C7/E7 (least-squares fit of all harmonics, as V01 alias.py),
the C5 two-peak pattern, and the S_MAX / D(end) assertion.
"""
import numpy as np

Lh, Rp, Lv = 1.0, 1.0, -0.2
FS = 44100
Q24 = 1 << 24
N = 2048
SMAX = 32.0
SH = 18                      # cell = 2^18 in Q24 = 1/64 gap^2
assert SMAX * Q24 / N == (1 << SH)


def g0(u):                   # R03 eq. 25 bracket with L_ver folded out (even in u)
    f1 = (u - Rp) ** 2 + Lh ** 2
    f2 = (u + Rp) ** 2 + Lh ** 2
    return (f1 - 2 * Lh ** 2) / f1 ** 2 - (f2 - 2 * Lh ** 2) / f2 ** 2


# exact Phi(u) by quadrature over |u| <= 8 (2^20 points)
W = 8.0
uu = np.linspace(-W, W, 2 ** 20 + 1)
G = g0(uu)
PHI = np.concatenate([[0], np.cumsum((G[1:] + G[:-1]) / 2 * np.diff(uu))])
PHI -= np.interp(0.0, uu, PHI)


def phi_exact(u):
    return np.interp(u, uu, PHI)


# radial tables over s: Phi~(s) = Phi(sqrt(s)), D(s) = dPhi~/ds = g0(sqrt s)/(2 sqrt s)
s_grid = np.arange(N + 1) * (SMAX / N)
rho = np.sqrt(s_grid)
P_f = phi_exact(rho)
D_f = np.where(rho > 1e-9, g0(np.maximum(rho, 1e-9)) / (2 * np.maximum(rho, 1e-9)), 0.0)
D_f[0] = (g0(1e-6) / (2e-6))                      # limit g0'(0)/2
h = SMAX / N
scale = (2 ** 30 - 1) / max(np.abs(P_f).max(), np.abs(D_f * h).max())
P_i = np.round(P_f * scale).astype(np.int64)      # Q30-scaled Phi~
M_i = np.round(D_f * h * scale).astype(np.int64)  # slope per cell, same units
M15 = np.round(D_f * h / np.abs(D_f * h).max() * 32767).astype(np.int64)
M15scale = np.abs(D_f * h).max() * scale / 32767  # to the P units
D15 = np.round(D_f / np.abs(D_f).max() * 32767).astype(np.int64)
D15scale = np.abs(D_f).max() / 32767
print("D(end)/max = %.2e  (assert < 3e-3)" % (abs(D_f[-1]) / np.abs(D_f).max()))
print("table words in ROM window:", int(np.sum((P_i & 0xFFFFFFFF >= 0xFFC00000) & (P_i & 0xFFFFFFFF < 0xFFD00000))),
      int(np.sum((M_i & 0xFFFFFFFF >= 0xFFC00000) & (M_i & 0xFFFFFFFF < 0xFFD00000))))


FR = 22                      # fraction bits taken straight from the Q48 product (cell = 2^42 in Q48)


def s_of(u, xh):
    s64 = u * u + xh * xh                          # Q48 gap^2, exact
    s64 = np.minimum(s64, (int(SMAX) << 48) - 1)
    return s64


def herm(s48, Mtab, mshift):
    i = s48 >> 42
    t = (s48 >> (42 - FR)) & ((1 << FR) - 1)       # Q22 fraction of the cell
    p0, p1 = P_i[i], P_i[i + 1]
    m0, m1 = Mtab[i] << mshift, Mtab[i + 1] << mshift
    c3 = 2 * (p0 - p1) + m0 + m1
    c2 = 3 * (p1 - p0) - 2 * m0 - m1
    return p0 + ((t * (m0 + ((t * (c2 + ((t * c3) >> FR))) >> FR))) >> FR)


def herm_q16(s, Mtab, mshift):         # the first-run variant: s truncated to Q24, 16-bit fraction
    i = s >> SH
    t = (s & ((1 << SH) - 1)) >> 2                # Q16 fraction
    p0, p1 = P_i[i], P_i[i + 1]
    m0, m1 = Mtab[i] << mshift, Mtab[i + 1] << mshift
    c3 = 2 * (p0 - p1) + m0 + m1
    c2 = 3 * (p1 - p0) - 2 * m0 - m1
    return p0 + ((t * (m0 + ((t * (c2 + ((t * c3) >> 16))) >> 16))) >> 16)


def lerp_tab(s48):
    s = s48 >> 24
    i = s >> SH
    f = s & ((1 << SH) - 1)
    return P_i[i] + (((P_i[i + 1] - P_i[i]) * f) >> SH)


def run_forms(x, xh=None):
    xi = np.round(x * Q24).astype(np.int64)
    hi = np.zeros_like(xi) if xh is None else np.round(xh * Q24).astype(np.int64)
    off = int(round(Lv * Q24))
    u = xi + off
    s = s_of(u, hi)
    out = {}
    H = herm(s, M_i, 0); out["HERM"] = np.diff(H) / scale
    Hq = herm_q16(s >> 24, M_i, 0); out["HERM16"] = np.diff(Hq) / scale     # the Q16-fraction variant (first run)
    L = lerp_tab(s); out["LERP"] = np.diff(L) / scale
    ds = ((u[1:] - u[:-1]) * (u[1:] + u[:-1]) + (hi[1:] - hi[:-1]) * (hi[1:] + hi[:-1])) >> 12   # Q36
    s = s >> 24
    sm = (s[1:] + s[:-1]) >> 1
    i = sm >> SH; f = sm & ((1 << SH) - 1)
    Dl = D15[i] + (((D15[i + 1] - D15[i]) * f) >> SH)
    out["MID"] = (Dl * ds) / 2 ** 36 * D15scale
    return out


def ref_of(x, xh=None):
    xh = np.zeros_like(x) if xh is None else xh
    rr = np.sqrt((x + Lv) ** 2 + xh ** 2)
    return np.diff(phi_exact(rr))


def snr(ref, y):
    return 10 * np.log10(np.sum(ref ** 2) / np.sum((y - ref) ** 2))


print("\nSNR (dB) vs exact radial Phi difference, 1 s decaying sine (tau = 1 s):")
print("%-28s %7s %7s %7s %7s" % ("case", "HERM22", "HERM16", "MID", "LERP"))
for f0, nm in ((41.2, "E1"), (440.0, "A4"), (2093.0, "C7"), (4186.0, "C8")):
    for A, lab in ((1.5, "hard 1.5 gap"), (1.5 * 10 ** (-30 / 20), "soft -30 dB")):
        n = np.arange(FS); x = A * np.sin(2 * np.pi * f0 * n / FS) * np.exp(-n / FS)
        o = run_forms(x); r = ref_of(x)
        print("%-28s %7.1f %7.1f %7.1f %7.1f" % (nm + " " + lab, snr(r, o["HERM"]), snr(r, o["HERM16"]), snr(r, o["MID"]), snr(r, o["LERP"])))
# with a horizontal component (H1 at a slightly different frequency, 0.25 of V)
for f0, nm in ((41.2, "E1"), (440.0, "A4")):
    n = np.arange(FS); x = 1.0 * np.sin(2 * np.pi * f0 * n / FS) * np.exp(-n / FS)
    xh = 0.25 * np.sin(2 * np.pi * f0 * 1.003 * n / FS + 0.7) * np.exp(-n / FS)
    o = run_forms(x, xh); r = ref_of(x, xh)
    print("%-28s %7.1f %7.1f %7.1f %7.1f" % (nm + " +H1 (0.25)", snr(r, o["HERM"]), snr(r, o["HERM16"]), snr(r, o["MID"]), snr(r, o["LERP"])))


def alias_ratio(y, f0):
    n = len(y); t = np.arange(n) / FS; K = int((FS / 2) // f0)
    B = np.concatenate([np.stack([np.cos(2 * np.pi * k * f0 * t), np.sin(2 * np.pi * k * f0 * t)], 1) for k in range(1, K + 1)], 1)
    c, *_ = np.linalg.lstsq(B, y, rcond=None); r = y - B @ c
    return 10 * np.log10(np.sum(r ** 2) / np.sum((B @ c) ** 2))


print("\nAlias ratio (dB, energy off the harmonic grid / on it), steady sine, 0.1 s:")
for f0, nm in ((2093.0, "C7"), (2637.0, "E7"), (4186.0, "C8")):
    for A in (0.5, 1.0, 1.5):
        n = np.arange(4410); x = A * np.sin(2 * np.pi * f0 * n / FS)
        o = run_forms(x); r = ref_of(x)
        print("  %s A=%.1f gap: exact ADAA %6.1f  HERM(int) %6.1f  MID(int) %6.1f" % (nm, A, alias_ratio(r[100:-100], f0), alias_ratio(o["HERM"][100:-100], f0), alias_ratio(o["MID"][100:-100], f0)))

print("\nC5 pattern (1.5 gap sine at 441 Hz, integer HERM):")
n = np.arange(4410); x = 1.5 * np.sin(2 * np.pi * 441.0 * n / FS)
y = run_forms(x)["HERM"]
pk = [y[i] for i in range(1, len(y) - 1) if y[i] > y[i - 1] and y[i] > y[i + 1] and y[i] > 0.2 * y.max()]
print("  positive peaks per period: %.1f, smaller/larger ratio %.2f, |max/min| %.4f" % (len(pk) / 44.1, min(pk[:4]) / max(pk[:4]), y.max() / -y.min()))
print("\nHorner intermediate bounds: |t*c3| <= 2^16 * 2^32 = 2^48; |c2| <= 5*2^30 -> fits int64; result <= 2^31? max|H| = %d (2^%.1f)" %
      (int(np.abs(herm(np.arange(0, (int(SMAX) << 48), 1 << 38, dtype=np.int64), M_i, 0)).max()), np.log2(int(np.abs(herm(np.arange(0, (int(SMAX) << 48), 1 << 38, dtype=np.int64), M_i, 0)).max()))))
