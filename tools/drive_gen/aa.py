# SPDX-License-Identifier: GPL-3.0-only
"""Anti-aliasing (first-order antiderivative, ADAA-1) without a divide, shared by the DRIVE TYPEs (drv_aa.h): its
tables, for tools/gen_drive_tables.py.

  the quotient (F(x) - F(x1)) / (x - x1): |d| = |x - x1| normalised by count-leading-zeros, its top RB bits index a
  reciprocal table, then one 32 x 32 -> 64 multiply and one 64-bit shift (drv_aa.h drv_aa_quot):
    DRV_AA_RCP[2^(RB-1)]   r = round(2^(15 + RB) / (J + 0.5)) for the mantissa bucket J = 2^(RB-1) .. 2^RB - 1
                           (centred: the relative error is symmetric, <= 2^-RB; uint16, every r in (2^15, 2^16))
  the antiderivative of a clipper table g given at nodes with widths w (linear interpolation between them):
    F[i] = (sum over j < i of w_j (g_j + g_j+1) / 2) >> sf          (exact while every w_j (g_j + g_j+1) is even)
  gf(g, w, sf) interleaves {g, F} per node (one 64-bit load a node on pi32v2).

The method, its precision and its cost: the private drive notes (aa/README.md)."""
import numpy as np

RB = 11                      # the reciprocal table's index bits: 1024 x uint16 = 2 KB, relative error <= 2^-11


def rcp(rb=RB):
    h = 1 << (rb - 1)
    r = [round(2 ** (15 + rb) / (j + 0.5)) for j in range(h, 2 * h)]
    assert 32768 < min(r) and max(r) < 65536
    return r


def antiderivative(g, w, sf):
    """F per node (int64 sums >> sf) of the piecewise-linear g with node widths w (len(w) = len(g) - 1)"""
    g = np.asarray(g, np.int64)
    w = np.asarray(w, np.int64)
    s = w * (g[:-1] + g[1:])
    assert (s % 2 == 0).all()
    F = np.concatenate([[0], np.cumsum(s // 2)]) >> sf
    assert np.abs(F).max() < 2 ** 31
    return F


def gf(g, w, sf):
    """{g, F} interleaved (int32 pairs)"""
    F = antiderivative(g, w, sf)
    out = np.empty(2 * len(g), np.int64)
    out[0::2] = g
    out[1::2] = F
    return out


def tables():
    import gen_drive_tables as G
    return [f"#define DRV_AA_RB {RB}\n", G.arr("DRV_AA_RCP", "uint16_t", rcp())]
