# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes electromagnetic pickup (IMPLEMENTATION S5, S6; research/03 eq. 24-27; review 01 #1, #3).

Units: lengths in "gaps" (1.0 = the horizontal tine-to-pickup distance L_hor of the key's register).
R = the pole/coil radius in gaps. u = vertical tip displacement + vertical offset (L_ver), x_h = horizontal tip
displacement.

The flux rate of R03 eq. 25 is g(u)*du/dt with
    g(u) = (f1 - 2)/f1^2 - (f2 - 2)/f2^2,  f1 = (u - R)^2 + 1,  f2 = (u + R)^2 + 1.
Each term integrates in closed form, d/dv [v / (v^2 + 1)] = (1 - v^2)/(v^2 + 1)^2, so the flux is
    Phi(u) = (u + R)/((u + R)^2 + 1) - (u - R)/((u - R)^2 + 1)
(up to a constant factor). Phi is even in u, so it depends only on the distance from the coil axis. The 2-D
(radial) version used for the horizontal polarisation is Phi~(s) = Phi(sqrt(s)), s = u^2 + x_h^2.

The voice output is the exact flux difference y[n] = Phi~(s[n]) - Phi~(s[n-1]) (the EMF averaged over a sample).
"""
import numpy as np

S_MAX = 32.0          # table range in gap^2 (review 01 #1: D(32) < 3e-3 of max for R = 1)


def phi(u, R=1.0):
    u = np.asarray(u, float)
    return (u + R) / ((u + R) ** 2 + 1) - (u - R) / ((u - R) ** 2 + 1)


def g(u, R=1.0):
    """dPhi/du (the R03 eq. 25 bracket, sign convention matching phi)."""
    u = np.asarray(u, float)
    a, b = (u + R) ** 2 + 1, (u - R) ** 2 + 1
    return (1 - (u + R) ** 2) / a**2 - (1 - (u - R) ** 2) / b**2


def phi_s(s, R=1.0):
    return phi(np.sqrt(np.maximum(s, 0.0)), R)


def dphi_ds(s, R=1.0):
    """dPhi~/ds = g(sqrt s) / (2 sqrt s); at s = 0 the limit g'(0)/2."""
    s = np.asarray(s, float)
    r = np.sqrt(np.maximum(s, 0.0))
    out = np.empty_like(r)
    small = r < 1e-6
    out[~small] = g(r[~small], R) / (2 * r[~small])
    h = 1e-5
    out[small] = (g(h, R) - g(-h, R)) / (2 * h) / 2
    return out


def pickup_out(xv, off, xh=None, R=1.0):
    """Float reference pickup: y[n] = Phi~(s[n]) - Phi~(s[n-1]), s = (xv + off)^2 + xh^2, xv/xh in gaps."""
    u = np.asarray(xv, float) + off
    s = u * u + (0.0 if xh is None else np.asarray(xh, float) ** 2)
    p = phi_s(s, R)
    return np.diff(p, prepend=p[0])


def tables(R=1.0, n=2048, s_max=S_MAX):
    """(Phi~ samples, dPhi~/ds samples) on s = k*s_max/n, k = 0..n+1 (two guard entries), float."""
    s = np.arange(n + 2) * (s_max / n)
    return phi_s(s, R), dphi_ds(s, R)
