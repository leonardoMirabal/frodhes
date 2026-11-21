#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The WHURLI reed: a clamped steel bar with a solder tuning mass at its free end (host only, tools/gen_reed.py).

Euler-Bernoulli finite elements (cubic Hermite, consistent mass) over the vibrating length, thickness varying along
the bar (the bass reeds are flat, the others ground thinner toward the tongue), a point mass plus its rotary inertia
at the tip. The tip mass is solved per key so mode 1 sits exactly on the key's pitch (the solder is how the reeds are
tuned); mode 2 and the mode shapes at the hammer line and at the tip follow. Shear and rotary inertia of the bar
itself are left out: the slenderness L / h is 24 or more on every reed.

Reed data per key (length, width, thickness by reed group) are published reed-replacement figures (the private
resource list); the clamp length, the taper's extent and the strike line are estimates (private/plans).

    r = reed(key)        # dict: L, w, h(x), tip mass, f, mass-normalised mode shapes at the strike line and tip
"""
import math

import numpy as np
import scipy.linalg as sla

E_STEEL = 200e9          # Pa, spring steel
RHO_STEEL = 7850.0       # kg/m^3
RHO_SOLDER = 9500.0      # kg/m^3, tin-lead solder (the tuning blob: its size gives the rotary inertia)
IN = 0.0254
KEY_LO, KEY_HI = 33, 96  # 64 reeds, A1 .. C7 (reed number = key - 32)
CLAMP = 0.375 * IN       # ESTIMATE: the reed's mounted foot (screw hole and clamp), not vibrating
STRIKE = 0.30            # ESTIMATE: hammer line as a fraction of the vibrating length from the clamp
TAPER = 0.40             # ESTIMATE: ground reeds (21..64) taper from the base to the tongue over this fraction
NEL = 40


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def geometry(key):
    """(total length m, width m, base thickness m, tongue thickness m) of reed number key - 32."""
    r = key - KEY_LO + 1
    assert 1 <= r <= 64
    if r <= 20:
        Lt = (2 + 19 / 20 - (r - 1) / 20) * IN          # 2 19/20" .. 2" in 1/20" steps
    else:
        Lt = (2 - (r - 20) / 44) * IN                   # minus 1/44" per reed: 1" at reed 64
    w = (0.155 if r <= 14 else 0.129 if r <= 20 else 0.120 if r <= 42 else 0.110 if r <= 50 else 0.097) * IN
    if r <= 20:
        hb = ht = 0.020 * IN                            # flat, no grind
    else:
        hb, ht = 0.033 * IN, 0.026 * IN                 # ground: thick base, thinner tongue
    return Lt, w, hb, ht


def _matrices(L, w, hb, ht, m_tip, J_tip):
    le = L / NEL
    n = 2 * (NEL + 1)
    K = np.zeros((n, n))
    M = np.zeros((n, n))
    for e in range(NEL):
        xm = (e + 0.5) / NEL
        h = hb + (ht - hb) * min(1.0, xm / TAPER) if TAPER > 0 else ht
        EI = E_STEEL * w * h ** 3 / 12
        rA = RHO_STEEL * w * h
        ke = EI / le ** 3 * np.array([[12, 6 * le, -12, 6 * le], [6 * le, 4 * le ** 2, -6 * le, 2 * le ** 2],
                                      [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le ** 2, -6 * le, 4 * le ** 2]])
        me = rA * le / 420 * np.array([[156, 22 * le, 54, -13 * le], [22 * le, 4 * le ** 2, 13 * le, -3 * le ** 2],
                                       [54, 13 * le, 156, -22 * le], [-13 * le, -3 * le ** 2, -22 * le, 4 * le ** 2]])
        s = slice(2 * e, 2 * e + 4)
        K[s, s] += ke
        M[s, s] += me
    M[-2, -2] += m_tip
    M[-1, -1] += J_tip
    return K[2:, 2:], M[2:, 2:]                         # clamped: w = w' = 0 at node 0


def modes(L, w, hb, ht, m_tip, nmodes=3):
    """(f [Hz], shapes [nmodes x nodes] mass-normalised displacement at the nodes 1..NEL)"""
    blob = (m_tip / RHO_SOLDER) ** (1 / 3) if m_tip > 0 else 0.0      # a cube-ish blob: its rotary inertia
    J = m_tip * blob ** 2 / 6
    K, M = _matrices(L, w, hb, ht, m_tip, J)
    lam, V = sla.eigh(K, M, subset_by_index=[0, nmodes - 1])
    f = np.sqrt(np.maximum(lam, 0)) / (2 * math.pi)
    disp = V[0::2, :].T                                  # displacement dofs (nodes 1..NEL)
    for i in range(nmodes):                              # sign: tip moves +
        if disp[i, -1] < 0:
            disp[i] *= -1
    return f, disp


def bar_mass(key):
    Lt, w, hb, ht = geometry(key)
    L = Lt - CLAMP
    xs = (np.arange(NEL) + 0.5) / NEL
    h = hb + (ht - hb) * np.minimum(1.0, xs / TAPER)
    return float(np.sum(RHO_STEEL * w * h * L / NEL))


def reed(key):
    Lt, w, hb, ht = geometry(key)
    L = Lt - CLAMP
    f0 = midi_hz(key)
    fb, _ = modes(L, w, hb, ht, 0.0, 1)
    if fb[0] < f0:
        raise ValueError("key %d: bare reed %.1f Hz is below the pitch %.1f Hz" % (key, fb[0], f0))
    lo, hi = 0.0, 0.05
    for _ in range(80):                                  # f1 falls with the tip mass: bisect
        mid = 0.5 * (lo + hi)
        if modes(L, w, hb, ht, mid, 1)[0][0] > f0:
            lo = mid
        else:
            hi = mid
    mt = 0.5 * (lo + hi)
    f, sh = modes(L, w, hb, ht, mt, 3)
    js = max(0, min(NEL - 1, int(round(STRIKE * NEL)) - 1))           # the node at the strike line
    return dict(key=key, L=L, w=w, hb=hb, ht=ht, f_bare=float(fb[0]), m_tip=mt, m_bar=bar_mass(key),
                f=[float(x) for x in f], phi_strike=[float(x) for x in sh[:, js]],
                phi_tip=[float(x) for x in sh[:, -1]], strike=(js + 1) / NEL)


if __name__ == "__main__":
    for k in (33, 40, 46, 52, 53, 60, 69, 74, 75, 82, 83, 90, 96):
        r = reed(k)
        print("key %d L %.1f mm f0 %.1f bare %.1f tip %.3f g (bar %.3f g) f2/f1 %.2f f3/f1 %.2f  phi_s/phi_t %.3f %.3f" % (
            k, r["L"] * 1e3, r["f"][0], r["f_bare"], r["m_tip"] * 1e3, r["m_bar"] * 1e3, r["f"][1] / r["f"][0],
            r["f"][2] / r["f"][0], r["phi_strike"][0] / r["phi_tip"][0], r["phi_strike"][1] / r["phi_tip"][1]))
