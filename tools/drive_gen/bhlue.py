# SPDX-License-Identifier: GPL-3.0-only
"""DRIVE TYPE 5, BHLUE (firmware/src/drv_bhlue.c): its tables, for tools/gen_drive_tables.py.

The circuit (the stock values): input 10 nF / 1M; stage 1 non-inverting, DRIVE 100k B as its feedback with 47 pF
across it, the shunt leg 4k7 || (3k3 + 10 nF) then 10 nF to the reference; stage 2 inverting, 220k feedback with
two + two silicon diodes in series with 6k8 across it (soft, symmetric, never flat), fed through the DRIVE pot's
other half and 110 nF + 9k4; a passive treble-cut tone (1k, TONE 25k B, 6k8, two 10 nF) loaded by VOLUME 100k.

  BHLUE_PRE[n][14]    the two coupling high-pass poles (Q30) and the stage 1 + stage 2 biquad (b Q15 x the input scale,
                      a Q28) at 64 grid steps of AMOUNT (fit steps round(j 127 / 63)), and extra rows (int16 pairs)
  BHLUE_POST[n][10]   the tone + volume-load biquad (c Q25, the level applied when a knob moves; d Q28), the same way
  BHLUE_PA[128], BHLUE_PT[128]   per AMOUNT / TONE step: row << 8 | w (Q8): the firmware interpolates rows[row] ..
                      rows[row + 1] when a step moves; a step whose two grid rows are realized differently (the fits
                      place a pole or zero elsewhere: interpolated, more than 0.1 dB off) runs its own row (an extra)
  BHLUE_V[1025], BHLUE_D[1025]   the clipper: vo at u = k / 16 V (Q16 V), and the next node minus this one (int16);
                      with AA only BHLUE_D: the firmware builds {g, F} (gf_table) from it in RAM once, at boot
  BHLUE_LVA[n], BHLUE_LVT[n]     the level per PRE / POST row (Q24 / Q15: the loudness match to OFF), interpolated with
                      the rows

bhlue_fit.json holds the pole / zero fits (offline, per step, against the exact analog response); this file only
quantizes them, as the private 1x model does (bit for bit). VARIANT "early" (27k / 33k / 10 pF in stage 1) is in the
fits too: pre_row(step, "early") gives its rows; the firmware uses the stock ones."""
import json
import os

import numpy as np

STEPS = 128
VIN_FS = 1.0                 # volts at the input for the engine's full scale (Q15)
VARIANT = "stock"
AA = True                    # (on: the user, 2026-10-24) the anti-aliased kernel (drv_bhlue.c BHLUE_AA): the P shelf after the high-passes, the
                             # clipper with its antiderivative, the tone rows refitted x Q (bhlue_fit.json post_q)
AA_SF = 9                    # F >> 9 (Q32 V^2 node sums)
HERE = os.path.dirname(os.path.abspath(__file__))
FIT = os.path.join(HERE, "bhlue_fit.json")
LEVELS = os.path.join(HERE, "bhlue_levels.json")
R5, R6 = 220e3, 6.8e3
RAIL = 3.0                                   # the op-amp's swing, volts around the reference (an estimate)
IS, NN, VT, RS = 2.52e-9, 1.752, 0.025852, 0.568   # the standard small-signal silicon diode model (Is, N, Rs)
_FITS = None


def fits():
    global _FITS
    if _FITS is None:
        _FITS = json.load(open(FIT))
    return _FITS


def clip_curve():
    """(u, vo) exact pairs by the diode-branch current: u = vo + R5 i, vo = 2 Vd(i) + i (R6 + 2 RS)"""
    i = np.concatenate([[0.0], np.logspace(-15, -1.6, 300000)])
    vo = 2 * NN * VT * np.log1p(i / IS) + i * (R6 + 2 * RS)
    return vo + R5 * i, vo


def clip_table():
    u, vo = clip_curve()
    x = np.arange(1025) / 16
    c = np.round(np.minimum(np.interp(x, u, vo), RAIL) * 65536).astype(np.int64)
    return [int(v) for v in c], [int(v) for v in np.concatenate([np.diff(c), [0]])]


def gf_table():
    """the anti-aliased clipper's table: per 1/16 V node (1025, the last repeated: 1026) g (Q16 V, low word) and F
    (high word) = the exact antiderivative of the interpolated g (node width 4096) >> AA_SF"""
    import sys
    sys.path.insert(0, HERE) if HERE not in sys.path else None
    import aa
    v, _ = clip_table()
    g = v + [v[-1]]
    F = aa.antiderivative(g, [4096] * (len(g) - 1), AA_SF)
    assert min(g) >= 0 and F.min() >= 0
    return [int(a) | (int(b) << 32) for a, b in zip(g, F)]


def shelf_row():
    """the P shelf (Q28: b0, b1, pole) the anti-aliased kernel runs after the high-passes"""
    b0, b1, pole = fits()["p_shelf"]
    return [r(b0, 28), r(b1, 28), r(pole, 28)]


def r(v, s):
    x = int(round(float(v) * 2 ** s))
    assert -(1 << 31) <= x < (1 << 31)
    return x


def _pre(var):
    return fits()["pre" if var == "stock" else "pre_early"]


def _resp(r, f):
    pa, ph, p1, p2, q1, q2, k = r
    z = np.exp(-2j * np.pi * f / 44100.0)
    return k * (1 - z) ** 2 * (1 - q1 * z) * (1 - q2 * z) / ((1 - pa * z) * (1 - ph * z) * (1 - p1 * z) * (1 - p2 * z))


def amount_fit(a, var=VARIANT):
    """the fit the firmware runs at AMOUNT a. The last step's fit (the knob at its end) is a different realization of
    its neighbour's response (the second high-pass pole moved into the biquad, a pole near Nyquist): switching to it
    while the sound plays rang at Nyquist for ~20 samples (tests/py/test_drive_kernels.py, the AMOUNT sweep). So
    AMOUNT 127 runs the step before it with its gain and second high-pass pole refitted to the last step's response
    (least squares in dB, 30 Hz - 10 kHz: within 0.07 dB of it)."""
    s = amount_step(a)
    if s < STEPS - 1:
        return _pre(var)[s]
    if (var, s) not in _LAST:
        from scipy.optimize import least_squares
        f = np.geomspace(30, 10000, 200)
        tgt = _resp(_pre(var)[s], f)
        base = list(_pre(var)[s - 1])

        def res(x):
            r_ = list(base)
            r_[6], r_[1] = x
            return 20 * np.log10(np.abs(_resp(r_, f) / tgt))
        x = least_squares(res, [base[6], base[1]]).x
        base[6], base[1] = float(x[0]), float(x[1])
        _LAST[(var, s)] = base
    return _LAST[(var, s)]


_LAST = {}


def hp_row(step, var=VARIANT, fit=None):
    pa, ph = (fit or _pre(var)[step])[:2]
    return [r(pa, 30), r(ph, 30)]


def pre_row(step, var=VARIANT, vin_fs=VIN_FS, fit=None):
    _, _, p1, p2, q1, q2, k = fit or _pre(var)[step]
    b = np.round(k * vin_fs * np.array([1.0, -(q1 + q2), q1 * q2]) * 2 ** 15)
    a = np.round(np.poly([p1, p2])[1:] * 2 ** 28)
    assert np.abs(b).max() < 2 ** 31
    return [int(v) for v in b] + [int(v) for v in a]


def post_row(step, out_gain=1.0):
    P1, P2, Z1, Z2, kp = fits()["post_q" if AA else "post"][step]
    c = np.round(kp * out_gain * np.array([1.0, -(Z1 + Z2), Z1 * Z2]) * 2 ** 25)
    d = np.round(np.array([-(P1 + P2), P1 * P2]) * 2 ** 28)
    assert np.abs(c).max() < 2 ** 31
    return [int(v) for v in c] + [int(v) for v in d]


def amount_step(a):
    """AMOUNT 1..127 -> the fit step (0..127, rotation / 127) nearest the DRIVE knob's whole travel (1 = its minimum)"""
    return int(round(max(a - 1, 0) * 127 / 126))


NROW = 64                    # grid rows per knob (fit steps round(j 127 / 63)); a step between two interpolated
BAD_DB = 0.1                 # ... unless the two are realized differently: then the step keeps its own row (an extra)
_F = np.geomspace(30, 10000, 200)
_Z = np.exp(-2j * np.pi * _F / 44100.0)


def lerp(r0, r1, w):
    """drv_bhlue.c bh_lerp: r0 + ((r1 - r0) w >> 8), 64-bit, per word"""
    return [a + (((b - a) * w) >> 8) for a, b in zip(r0, r1)]


def _hpre(row):                                  # (pa, ph, b0, b1, b2, a1, a2) -> the response on _F
    pa, ph = row[0] / 2 ** 30, row[1] / 2 ** 30
    b = np.array(row[2:5]) / 2 ** 15
    a = np.array([1, row[5] / 2 ** 28, row[6] / 2 ** 28])
    z = _Z
    return (1 - z) ** 2 / ((1 - pa * z) * (1 - ph * z)) * np.polyval(b[::-1], z) / np.polyval(a[::-1], z)


def _hpost(row):
    c = np.array(row[:3]) / 2 ** 25
    d = np.array([1, row[3] / 2 ** 28, row[4] / 2 ** 28])
    return np.polyval(c[::-1], _Z) / np.polyval(d[::-1], _Z)


def _err(h0, h1):
    return float(np.abs(20 * np.log10(np.abs(h0 / h1))).max())


def _step_of(j):
    return int(round(j * 127 / (NROW - 1)))


def amount_grid():
    """(rows [pa, ph, b0..a2] per grid row and the extras, pos per AMOUNT step (row, w Q8))"""
    def full(fit):
        return hp_row(0, fit=fit) + pre_row(0, fit=fit)
    rows = [full(amount_fit(1 + round(_step_of(j) * 126 / 127))) for j in range(NROW)]
    pos = []
    for a in range(STEPS):
        p = max(a - 1, 0)
        j, w = p >> 1, (p & 1) * 128
        if w:
            want = full(amount_fit(a))
            if _err(_hpre(lerp(rows[j], rows[j + 1], w)), _hpre(want)) > BAD_DB:
                rows.append(want)
                j, w = len(rows) - 1, 0
        pos.append((j, w))
    return rows, pos


def tone_grid():
    rows = [post_row(_step_of(j)) for j in range(NROW)]
    pos = []
    for t in range(STEPS):
        p = t * (NROW - 1) * 256 // (STEPS - 1)
        j, w = p >> 8, p & 255
        if w and _err(_hpost(lerp(rows[j], rows[j + 1], w)), _hpost(post_row(t))) > BAD_DB:
            rows.append(post_row(t))
            j, w = len(rows) - 1, 0
        pos.append((j, w))
    return rows, pos


def grids():
    if not hasattr(grids, "cache"):
        grids.cache = amount_grid() + tone_grid()
    return grids.cache


def at(rows, pos):
    j, w = pos
    return list(rows[j]) if w == 0 else lerp(rows[j], rows[j + 1], w)


def levels_per_step():
    """the loudness match to OFF per knob step (bhlue_levels.json, tools/drive_gen/calib_levels.py), else unity"""
    if os.path.exists(LEVELS):
        d = json.load(open(LEVELS))
        return d["amount"], d["tone"]
    return [1.0] * STEPS, [1.0] * STEPS


def _row_levels(per_step, pos, n):
    """a level per row: a row a step runs exactly takes that step's; a grid row no step runs exactly, the per-step
    levels interpolated over the grid positions"""
    out = [None] * n
    for s, (j, w) in enumerate(pos):
        if w == 0 and out[j] is None:
            out[j] = per_step[s]
    g = [(j + w / 256, per_step[s]) for s, (j, w) in enumerate(pos) if j < NROW]
    g.sort()
    for j in range(n):
        if out[j] is None:
            out[j] = float(np.interp(j, [x for x, _ in g], [v for _, v in g]))
    return out


def levels():
    """(LVA per PRE row Q24, LVT per POST row Q15), interpolated by the firmware with the rows"""
    ra, pa, rt, pt = grids()
    la, lt = levels_per_step()
    return [r(v, 24) for v in _row_levels(la, pa, len(ra))], [r(v, 15) for v in _row_levels(lt, pt, len(rt))]


def rows_at(a, t):
    """what drv_bhlue.c bh_set runs at steps (a, t): pa ph b0 b1 b2 a1 a2 c0 c1 c2 d1 d2 (c x the level)"""
    ra, pa, rt, pt = grids()
    lva, lvt = levels()
    k = at(ra, pa[a])
    c = at(rt, pt[t])
    L = (at([[v] for v in lva], pa[a])[0] * at([[v] for v in lvt], pt[t])[0]) >> 15
    return k + [(c[i] * L) >> 24 for i in range(3)] + c[3:]


def split16(G, name, rs):
    """rows of int32 words as int16 pairs (high, low): words in the chip-ROM window (0xFFC00000..0xFFCFFFFF) never
    appear in the image; the firmware joins them when it copies a row to RAM (a knob step)"""
    out = []
    for r_ in rs:
        q = []
        for v in r_:
            u = v & 0xFFFFFFFF
            hi, lo = u >> 16, u & 0xFFFF
            q += [hi - 65536 if hi >= 32768 else hi, lo - 65536 if lo >= 32768 else lo]
        out.append(q)
    k = len(out[0])
    return G.rows(name, "int16_t", out).replace(f"{name}[{len(out)}]", f"{name}[{len(out)}][{k}]")


def tables():
    import gen_drive_tables as G
    v, d = clip_table()
    assert max(d) < 32768 and min(d) >= 0 and v[0] == 0    # (g = the running sum of d: drv_bhlue.c bh_gf_build)
    ra, pa, rt, pt = grids()
    lva, lvt = levels()
    return [
        f"#define BHLUE_NPRE {len(ra)}\n#define BHLUE_NPOST {len(rt)}\n",
        split16(G, "BHLUE_PRE", ra),                     # pa ph b0 b1 b2 a1 a2: 14 int16 a row
        split16(G, "BHLUE_POST", rt),
        G.arr("BHLUE_PA", "uint16_t", [(j << 8) | w for j, w in pa]),
        G.arr("BHLUE_PT", "uint16_t", [(j << 8) | w for j, w in pt]),
        "#define BHLUE_NV 1025\n",
    ] + ([f"#define BHLUE_AA 1\n", G.arr("BHLUE_SH", "int32_t", shelf_row()), G.arr("BHLUE_D", "int16_t", d)]
         if AA else ["#define BHLUE_AA 0\n", G.arr("BHLUE_V", "int32_t", v), G.arr("BHLUE_D", "int16_t", d)]) + [
        G.arr("BHLUE_LVA", "int32_t", lva),
        G.arr("BHLUE_LVT", "int32_t", lvt),
    ]
