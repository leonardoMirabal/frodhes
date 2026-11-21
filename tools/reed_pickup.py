#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The WHURLI electrostatic comb pickup: capacitance between a reed tip and its slotted pickup plate against the
reed's vertical position, from a 2-D Laplace solve of the cross-section (host only, used by tools/gen_reed.py).

Cross-section at the reed's free end (x lateral, z vertical, mm): the reed is a w x h rectangle at potential 0,
centred laterally in a slot of width S cut through a plate of thickness t (potential 1, both fingers), the plate's
mid-plane at z = 0, the reed's centre at z. The plate fingers reach `finger` mm out from the slot edge. A grounded
box far away closes the field (the shield over the reed bar). The charge on the reed per unit length (Gauss's law on
the grid) is C(z) per metre of overlap; the voltage a constant polarizing supply develops across the input load is
the time derivative of C (current mode) below the RC corner of the pickup capacitance and the load, C itself above
it (charge mode): tools/gen_reed.py puts the derivative in the engine's pickup difference and the corner in the
load filter.

    c = cap_curve(w, h, S, t, z)          # F/m at each z (mm)
"""
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl

EPS0 = 8.8541878128e-12


def _solve(w, h, S, t, z, finger=3.0, box=(16.0, 12.0), dx=0.02):
    """C per metre (F/m) between the reed (centre z) and the plate, one Laplace solve (5-point stencil)."""
    W, H = box
    nx, nz = int(round(W / dx)) + 1, int(round(H / dx)) + 1
    xs = (np.arange(nx) - (nx - 1) / 2) * dx
    zs = (np.arange(nz) - (nz - 1) / 2) * dx
    X, Z = np.meshgrid(xs, zs, indexing="ij")
    eps = 1e-9
    reed = (np.abs(X) <= w / 2 + eps) & (np.abs(Z - z) <= h / 2 + eps)
    plate = (np.abs(X) >= S / 2 - eps) & (np.abs(X) <= S / 2 + finger + eps) & (np.abs(Z) <= t / 2 + eps)
    assert not np.any(reed & plate), "reed touches the plate"
    edge = np.zeros_like(reed)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    fixed = reed | plate | edge
    val = np.where(plate, 1.0, 0.0)
    idx = -np.ones((nx, nz), dtype=np.int64)
    free = ~fixed
    idx[free] = np.arange(int(free.sum()))
    n = int(free.sum())
    rows, cols, data = [], [], []
    rhs = np.zeros(n)
    fi, fj = np.nonzero(free)
    me = idx[fi, fj]
    rows.append(me); cols.append(me); data.append(np.full(n, 4.0))
    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ni, nj = fi + di, fj + dj
        nb = idx[ni, nj]
        isf = nb >= 0
        rows.append(me[isf]); cols.append(nb[isf]); data.append(np.full(int(isf.sum()), -1.0))
        np.add.at(rhs, me[~isf], val[ni[~isf], nj[~isf]])
    A = sp.csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
    phi = val.copy()
    phi[free] = spl.spsolve(A.tocsc(), rhs)
    # Gauss: the flux into the reed's cells from their non-reed neighbours (per unit length, square cells)
    q = 0.0
    ri, rj = np.nonzero(reed)
    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ni, nj = ri + di, rj + dj
        outside = ~reed[ni, nj]
        q += float(np.sum(phi[ni[outside], nj[outside]]))
    return EPS0 * q


def cap_curve(w, h, S, t, zs, **kw):
    return np.array([_solve(w, h, S, t, float(z), **kw) for z in zs])


if __name__ == "__main__":
    zs = np.linspace(0, 2.0, 11)
    c = cap_curve(3.05, 0.66, 3.53, 0.8, zs)
    for z, v in zip(zs, c):
        print("z %.2f mm  C %.3f pF/m" % (z, v * 1e12))
