# SPDX-License-Identifier: GPL-3.0-only
"""DRIVE TYPE 3, TSH8 (firmware/src/drv_tsh8.c): its tables, for tools/gen_drive_tables.py.

The circuit: an emitter-follower input (coupling high-passes 17.8 Hz and 15.9 Hz), a non-inverting clipping amplifier
whose feedback branch (R2 = 51k + DRIVE 500k, A 15 % taper, 51 pF across it; 4k7 + 47 nF to ground) holds two
antiparallel silicon diodes, so the output is the clean input plus the clipped branch (Vo = Vi + V), then a passive
723 Hz low-pass into an active tone stage (TONE 20k, W taper, clockwise brighter). The model (one published
"simplified, physically informed" form, Yeh 2009): V_lin = Vi s C3 R2 / ((1 + s R4 C3)(1 + s R2 C4)), then the static
diode law V + 2 Is R2 sinh(V / Vt) = V_lin (Is 2.52 nA, Vt 45.3 mV), no oversampling, no ADAA.

  TSH8_HP[2]          the input high-pass (one corner standing for both couplings): b0, a1 (Q28)
  TSH8_BR[64][10]     the branch biquad (b Q22, a Q28 negated; int16 pairs: split16) at 64 values of R2 uniform in log
                      R2; poles matched-z, the numerator fitted to the analog magnitude
  TSH8_TONE[64][10]   the tone biquad (Q28) at 64 values of the pot fraction uniform in its logit
  TSH8_PA[128], TSH8_PT[128]   per AMOUNT / TONE step: its place between two rows (row << 8 | w, Q8): the firmware
                      interpolates the rows when a step moves (the grids make that exact to the fits: private notes)
  TSH8_KD[8][505]     the diode table g(u) at 8 values of R2, log-spaced over the DRIVE range, as node-to-node steps
                      (uint8; V Q15 at |u| = 0 .. 2 V by 1/128 V, then to 64 V by 1/4 V; u Q14)
  TSH8_KPOS[128]      per AMOUNT step: the key below (bits 9..) and the weight of the one above (Q8, 0 .. 256): the
                      firmware builds the step's table by linear interpolation in log R2 (within 5 LSB of the exact
                      table at every step), so 8 keys (4 KB) stand for 128 tables (129 KB)
  TSH8_LVA[64]        the level per AMOUNT row (Q24: the output scale 1 / TSH8_IN_K x the loudness match to OFF)
  TSH8_LVT[64]        the level per TONE row (Q15), the second factor of the same match (tsh8_levels.json, per step;
                      the rows' values interpolated from it)

Bit for bit the formulas of the private 1x model (its coefficients reproduced exactly: tests/py/test_drive_tsh8.py
checks the C kernel against the reference loops; the private notes record the check against the model itself).
"""
import json
import os

import numpy as np

FS = 44100.0
STEPS = 128
OSH = 12                     # drv_tsh8.c TSH8_OSH: the tone stage runs 2^12 finer than the output
IN_K = 154                   # the input: x (Q15 full scale) x 154 = volts Q24 at 154 / 512 = 0.301 V full scale
UQ, VQ = 14, 15
FINE_W, FINE_END, COARSE_W, U_END = 1 << (UQ - 7), 2 << UQ, 1 << (UQ - 2), 128 << UQ   # (to 128 V: the anti-aliased
#                                                                      clipper has no clamp below it; the plain one clamps at 64 V)
N_FINE = FINE_END // FINE_W
N_COARSE = (U_END - FINE_END) // COARSE_W
NODES = N_FINE + N_COARSE + 1            # 505
NKEY = 8
AA = True                    # the anti-aliased kernel (ADAA-1, drv_tsh8.c TSH8_AA): tone rows x Q, input x P. On (the
#                              user, 2026-10-24): 98 instructions a sample compiled (drv_tsh8.c DRV_US_TSH8)
NROW = 64                    # coefficient rows per knob, interpolated between (pos tables)
HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = os.path.join(HERE, "tsh8_levels.json")

C1, ZIN, C2, R5 = 20e-9, 1e3 + 1 / (1 / 510e3 + 1 / (351 * 10e3)), 1e-6, 10e3
R4, C3, R6, P1, C4 = 4.7e3, 47e-9, 51e3, 500e3, 51e-12
IS, VT = 2.52e-9, 45.3e-3
R7, C5, R9, P2, R8, C6, R11 = 1e3, 0.22e-6, 10e3, 20e3, 220.0, 0.22e-6, 1e3
TAPER_A15 = [0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.32, 0.49, 0.66, 0.83, 1.0]
TAPER_W = [0, 0.015, 0.03, 0.10, 0.25, 0.50, 0.75, 0.90, 0.97, 0.985, 1.0]


def amount_rot(a):
    """AMOUNT 1..127 -> the DRIVE knob's whole travel (1 = its minimum; 0 never runs: drive.c makes it dry)"""
    return max(a - 1, 0) / 126


def drive_knob(rot):
    return float(np.interp(rot, np.linspace(0, 1, 11), TAPER_A15))


def tone_knob(rot):
    return float(np.interp(rot, np.linspace(0, 1, 11), TAPER_W))


def drive_r(d):
    return R6 + d * P1


def q30(v):
    return np.round(np.asarray(v, float) * (1 << 30)) / (1 << 30)


def q(v, s):
    r = int(round(float(v) * 2 ** s))
    assert -(1 << 31) <= r < (1 << 31)
    return r


def freqs():
    return np.geomspace(20, 20000, 500)


def sresp(num, den, f):
    s = 2j * np.pi * f
    return np.polyval(num, s) / np.polyval(den, s)


def zresp(b, a, f):
    z1 = np.exp(-2j * np.pi * f / FS)
    return np.polyval(b[::-1], z1) / np.polyval(a[::-1], z1)


def matched_den(den):
    return np.real(np.poly(np.exp(np.roots(den) / FS)))


def fit_num(target, a, f, w_hi=16000.0, nb=3, iters=60):
    """numerator (nb taps) for a fixed denominator, fitting the magnitude of target (relative): a complex least squares
    whose target phase is replaced, each pass, by the digital filter's own"""
    z1 = np.exp(-2j * np.pi * f / FS)
    A = np.polyval(a[::-1], z1)
    mag = np.abs(target)
    tgt = target
    M0 = np.stack([z1 ** k for k in range(nb)], 1)
    for _ in range(iters):
        ta = tgt * A
        w = 1 / np.abs(ta)
        w = np.where(f > w_hi, w * 0.05, w)
        M = M0 * w[:, None]
        rhs = ta * w
        b, *_ = np.linalg.lstsq(np.concatenate([M.real, M.imag]), np.concatenate([rhs.real, rhs.imag]), rcond=None)
        h = (M0 @ b) / A
        tgt = mag * np.exp(1j * np.angle(h))
    return b


def hp1(f, fc):
    s = 1j * np.asarray(f) / fc
    return s / (1 + s)


def hp1_bilinear(fc):
    k = np.tan(np.pi * fc / FS)
    return q30(np.array([1, -1]) / (1 + k)), q30(np.array([1, (k - 1) / (1 + k)]))


def hp_fc():
    """the corner of the one-pole standing for both input couplings (the smallest error, 30-500 Hz)"""
    f = np.geomspace(30, 500, 200)
    ref = 20 * np.log10(np.abs(hp1(f, 1 / (2 * np.pi * C1 * ZIN)) * hp1(f, 1 / (2 * np.pi * C2 * R5))))
    best = None
    for fc in np.arange(20, 32, 0.05):
        b, a = hp1_bilinear(fc)
        e = np.max(np.abs(20 * np.log10(np.abs(zresp(b, a, f))) - ref))
        if best is None or e < best[0]:
            best = (e, fc)
    return best[1]


def hp_design():
    return hp1_bilinear(hp_fc())


# ---- the anti-aliased variant: ADAA-1 averages two samples (|H| = cos(pi f / fs) where linear, ~sinc(f / fs) where it
# clips, half a sample of delay); compensated in the coefficients only: the input biquad = the high-pass x P (sinc /
# cos), the tone = its fit x Q (1 / sinc), both to 16 kHz (P cos Q = 1 linear, sinc Q = 1 clipped)
COMP_TO, P_POLE = 16000.0, -0.7


def p_target(f):
    g = np.minimum(f, COMP_TO)
    return np.sinc(g / FS) / np.cos(np.pi * g / FS)


def q_target(f):
    return 1 / np.sinc(np.minimum(f, COMP_TO) / FS)


def in_pq_row():
    """the input biquad (Q28, a negated): the coupling high-pass x P"""
    fc = hp_fc()
    f = freqs()
    a = np.real(np.poly([np.exp(-2 * np.pi * fc / FS), P_POLE]))
    b = q30(fit_num(hp1(f, 1 / (2 * np.pi * C1 * ZIN)) * hp1(f, 1 / (2 * np.pi * C2 * R5)) * p_target(f), a, f))
    a = q30(a)
    return [q(b[0], 28), q(b[1], 28), q(b[2], 28), q(-a[1], 28), q(-a[2], 28)]


def tone_frac_q(tf):
    """the tone row of pot fraction tf, its numerator fitted to the response x Q"""
    num, den = tone_sdomain(tf)
    a = matched_den(den)
    f = freqs()
    b, a = q30(fit_num(sresp(num, den, f) * q_target(f), a, f)), q30(a)
    return [q(b[0], 28), q(b[1], 28), q(b[2], 28), q(-a[1], 28), q(-a[2], 28)]


def branch_design(d):
    r2 = drive_r(d)
    num, den = [C3 * r2, 0.0], np.polymul([R4 * C3, 1.0], [r2 * C4, 1.0])
    a = matched_den(den)
    f = freqs()
    return q30(fit_num(sresp(num, den, f), a, f)), q30(a)


def tone_sdomain(t):
    rl, rr = max(t, 1e-6) * P2, max(1 - t, 1e-6) * P2
    par = rl * rr / (rl + rr)
    y = (rl + rr) * (R8 + par)
    w = y / (rl * R11 + y)
    x = rr / (rl + rr) / ((R8 + par) * C6)
    wz = 1 / (C6 * (R8 + par))
    wp = 1 / (C5 * (R7 * R9 / (R7 + R9)))
    k = (rl * R11 + y) / (y * R7 * C5)
    return [k, k * w * wz], np.polyadd(np.polymul([1, wp], [1, wz]), [x, 0])


def tone_design(t):
    num, den = tone_sdomain(t)
    a = matched_den(den)
    f = freqs()
    return q30(fit_num(sresp(num, den, f), a, f)), q30(a)


def g_solve(u, r2):
    """V with V + a sinh(V / Vt) = u, a = 2 Is R2 (Newton from an upper bound: monotone convergence)"""
    a = 2 * IS * r2
    u = np.asarray(u, float)
    au = np.abs(u)
    v = np.minimum(au, VT * np.arcsinh(au / a))
    for _ in range(60):
        dv = (v + a * np.sinh(v / VT) - au) / (1 + a / VT * np.cosh(v / VT))
        v = v - dv
        if np.max(np.abs(dv)) < 1e-13:
            break
    return np.sign(u) * v


def node_u():
    return np.concatenate([np.arange(N_FINE) * FINE_W, FINE_END + np.arange(N_COARSE + 1) * COARSE_W]).astype(np.int64)


def table(r2):
    """g at the nodes (V Q15) for the feedback resistance r2"""
    return np.round(g_solve(node_u() / (1 << UQ), r2) * (1 << VQ)).astype(np.int64)


def hp_row():
    b, a = hp_design()
    return [q(b[0], 28), q(-a[1], 28)]


def br_row(rot):
    b, a = branch_design(drive_knob(rot))
    return [q(b[0], 22), q(b[1], 22), q(b[2], 22), q(-a[1], 28), q(-a[2], 28)]


def tone_row(rot):
    b, a = tone_design(tone_knob(rot))
    return [q(b[0], 28), q(b[1], 28), q(b[2], 28), q(-a[1], 28), q(-a[2], 28)]


def key_r2():
    lo, hi = np.log(drive_r(drive_knob(0.0))), np.log(drive_r(drive_knob(1.0)))
    return np.exp(np.linspace(lo, hi, NKEY))


def kpos():
    """per AMOUNT step: (key, weight Q8) of its R2 between the keys (log R2)"""
    lk = np.log(key_r2())
    out = []
    for a in range(STEPS):
        fr = float(np.interp(np.log(drive_r(drive_knob(amount_rot(a)))), lk, np.arange(NKEY)))
        i0 = min(int(fr), NKEY - 2)
        out.append((i0, int(round((fr - i0) * 256))))      # (w 0 .. 256: 256 is the key above, exactly)
    return out


def keys():
    return np.array([table(r) for r in key_r2()])


def key_deltas(K):
    """each key as node-to-node steps (uint8: 5 .. 255; every key starts at g(0) = 0), a 0 at the end"""
    d = np.concatenate([np.diff(K, axis=1), np.zeros((len(K), 1), np.int64)], axis=1)
    assert (K[:, 0] == 0).all() and d.min() >= 0 and d.max() <= 255
    return d


def built_table(K, i0, w):
    """what drv_tsh8.c builds for (i0, w): K[i0] + ((K[i0 + 1] - K[i0]) w >> 8) (an arithmetic shift)"""
    return K[i0] + (((K[i0 + 1] - K[i0]) * w) >> 8)


AA_SF = 5                    # the anti-aliased table's F >> 5 (|F| < 2^31 over |u| <= 128 V)


def node_w():
    """the node widths (u Q14): 1/128 V to 2 V, then 1/4 V"""
    return np.diff(node_u())


def built_gf(K, i0, w):
    """what drv_tsh8.c ts_build_gf builds (the anti-aliased variant): {g, F} per node, g = built_table, F its exact
    antiderivative >> AA_SF (tools/drive_gen/aa.py)"""
    import sys
    sys.path.insert(0, HERE) if HERE not in sys.path else None
    import aa
    return aa.gf(built_table(K, i0, w), node_w(), AA_SF)


# ---- the coefficient rows: NROW per knob on a grid where the coefficients move smoothly (interpolation adds
# nothing measurable: the branch uniform in log R2, the tone uniform in logit of the pot fraction), and per knob step
# its position on that grid: pos = row << 8 | w (Q8), the firmware interpolating rows[row] .. rows[row + 1] (w = 0:
# the row itself)
LOGIT_EPS = 1e-6


def _logit(v):
    v = min(max(v, LOGIT_EPS), 1 - LOGIT_EPS)
    return np.log(v / (1 - v))


def br_r2(r2):
    num, den = [C3 * r2, 0.0], np.polymul([R4 * C3, 1.0], [r2 * C4, 1.0])
    a = matched_den(den)
    f = freqs()
    b, a = q30(fit_num(sresp(num, den, f), a, f)), q30(a)
    return [q(b[0], 22), q(b[1], 22), q(b[2], 22), q(-a[1], 28), q(-a[2], 28)]


def tone_frac(tf):
    num, den = tone_sdomain(tf)
    a = matched_den(den)
    f = freqs()
    b, a = q30(fit_num(sresp(num, den, f), a, f)), q30(a)
    return [q(b[0], 28), q(b[1], 28), q(b[2], 28), q(-a[1], 28), q(-a[2], 28)]


def _grid(xs):
    lo, hi = min(xs), max(xs)
    return lo, hi, [(x - lo) / (hi - lo) * (NROW - 1) for x in xs]


def _pos(p):
    j = min(int(p), NROW - 2)
    w = int(round((p - j) * 256))
    return (j + 1, 0) if w == 256 else (j, w)


def amount_grid():
    """(rows, pos per AMOUNT step): the branch rows uniform in log R2"""
    lo, hi, ps = _grid([np.log(drive_r(drive_knob(amount_rot(a)))) for a in range(STEPS)])
    rows = [br_r2(np.exp(lo + (hi - lo) * j / (NROW - 1))) for j in range(NROW)]
    return rows, [_pos(p) for p in ps]


def tone_grid():
    lo, hi, ps = _grid([_logit(tone_knob(t / (STEPS - 1))) for t in range(STEPS)])
    rows = []
    for j in range(NROW):
        x = lo + (hi - lo) * j / (NROW - 1)
        rows.append((tone_frac_q if AA else tone_frac)(1 / (1 + np.exp(-x))))
    return rows, [_pos(p) for p in ps]


def lerp(r0, r1, w):
    """drv_tsh8.c ts_lerp: r0 + ((r1 - r0) w >> 8), 64-bit, per word"""
    return [a + (((b - a) * w) >> 8) for a, b in zip(r0, r1)]


def at(rows, pos):
    j, w = pos
    return list(rows[j]) if w == 0 else lerp(rows[j], rows[j + 1], w)


def levels_per_step():
    """the loudness match to OFF per knob step (tsh8_levels.json, tools/drive_gen/calib_levels.py), else unity"""
    if os.path.exists(LEVELS):
        d = json.load(open(LEVELS))
        return d["amount"], d["tone"]
    return [1.0] * STEPS, [1.0] * STEPS


def _row_levels(per_step, pos):
    """a level per row: the per-step levels interpolated over the steps' positions"""
    p = [j + w / 256 for j, w in pos]
    o = np.argsort(p)
    return [float(np.interp(j, np.array(p)[o], np.array(per_step)[o])) for j in range(NROW)]


def levels():
    """(LVA rows Q24 x 1 / IN_K, LVT rows Q15): what the firmware interpolates by the same positions"""
    la, lt = levels_per_step()
    _, pa, _, pt = grids()
    return ([q(v / IN_K, 24) for v in _row_levels(la, pa)], [q(v, 15) for v in _row_levels(lt, pt)])


def grids():
    """(amount rows, amount pos, tone rows, tone pos), computed once"""
    if not hasattr(amount_grid, "cache"):
        amount_grid.cache = amount_grid()
        tone_grid.cache = tone_grid()
    return amount_grid.cache + tone_grid.cache


def rows_at(a, t):
    """what drv_tsh8.c ts_set runs at steps (a, t): branch[5], tone[5] (b x the level)"""
    ra, pa, rt, pt = grids()
    lva, lvt = levels()
    br = at(ra, pa[a])
    tone = at(rt, pt[t])
    la = at([[v] for v in lva], pa[a])[0]
    lt = at([[v] for v in lvt], pt[t])[0]
    L = (la * lt) >> 15
    return br, [(tone[i] * L) >> (24 - OSH) for i in range(3)] + tone[3:]


def split16(G, name, rs):
    """rows of int32 words as int16 pairs (high, low): words in the chip-ROM window (0xFFC00000..0xFFCFFFFF) never
    appear in the image; the firmware joins them when it copies a row to RAM (a knob step)"""
    out = []
    for r in rs:
        q_ = []
        for v in r:
            u = v & 0xFFFFFFFF
            hi, lo = u >> 16, u & 0xFFFF
            q_ += [hi - 65536 if hi >= 32768 else hi, lo - 65536 if lo >= 32768 else lo]
        out.append(q_)
    k = len(out[0])
    return G.rows(name, "int16_t", out).replace(f"{name}[{len(out)}]", f"{name}[{len(out)}][{k}]")


def tables():
    import gen_drive_tables as G
    K = keys()
    assert K.min() >= 0 and K.max() < 32768
    ra, pa, rt, pt = grids()
    lva, lvt = levels()
    kp = kpos()
    return [
        f"#define TSH8_NODES {NODES}\n#define TSH8_NKEY {NKEY}\n#define TSH8_IN_K {IN_K}\n#define TSH8_NROW {NROW}\n"
        f"#define TSH8_AA {int(AA)}\n",
        G.arr("TSH8_HP", "int32_t", hp_row()),
        split16(G, "TSH8_IN", [in_pq_row()]),
        split16(G, "TSH8_BR", ra),
        split16(G, "TSH8_TONE", rt),
        G.arr("TSH8_PA", "uint16_t", [(j << 8) | w for j, w in pa]),
        G.arr("TSH8_PT", "uint16_t", [(j << 8) | w for j, w in pt]),
        G.rows("TSH8_KD", "uint8_t", [[int(v) for v in k] for k in key_deltas(K)]).replace(
            f"TSH8_KD[{NKEY}]", f"TSH8_KD[{NKEY}][{NODES}]"),
        G.arr("TSH8_KPOS", "uint16_t", [(i0 << 9) | w for i0, w in kp]),
        G.arr("TSH8_LVA", "int32_t", lva),
        G.arr("TSH8_LVT", "int32_t", lvt),
    ]
