#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""DRIVE TYPE 1 of the plugin's own-code build (firmware/src/drv_own1.c, FR_OWN): its design and its tables ->
firmware/gen/drive_own_tables.h (only that build includes it; the firmware's drive_tables.h is not touched).

The design (Frodhes' own; the level targets are the black-box profile tests/baselines/drive_own_target.json):

  in -> bass pre-emphasis (a low shelf cut, 150 Hz, 0 .. -4.4 dB with AMOUNT)
     -> gain G (per drive row and AMOUNT) -> 2x (drive.c's half-band) -> the curve with ADAA-1 -> 1x
     -> bass de-emphasis (the exact inverse shelf) -> TONE: a state-variable low-pass (Q 0.71) whose corner follows
        TONE and opens toward 20 kHz as AMOUNT falls -> DC high-pass (25 Hz) -> make-up M (per drive row and AMOUNT)

  The curve: F(u) = s + b s^2, s = u / sqrt(1 + u^2) (the algebraic sigmoid: smooth everywhere, slope 1 at 0, a
  gradual approach to its ceiling), b the even part per drive row and AMOUNT (BETA_LO at AMOUNT 1 to BETA_HI at 127:
  warmer where it is driven lightly, less lopsided where it is driven hard). Its output ceiling is 1 + b on the
  positive side and -(1 - b) on the negative one; monotonic while b < 1/2. The kernel keeps s and s^2 as two tables
  and mixes their anti-aliased means by b (ADAA is linear in the curve).

  The pre-emphasis cuts the bass before the curve and the de-emphasis gives it back after it: the small-signal
  response stays flat, chords drive the curve less with their low notes (less intermodulation in the low band).

  G and M per (row, AMOUNT): G sets how hard the curve is driven, fitted so that the RMS gain at -12.3 and -24.3 dBFS
  (220 Hz) and on the 5-note chord lands on the target's within the least spread; M then centres the three on the
  target (a float model of the chain here: --fit), and a last pass measures the integer kernel on the host
  (tools/drive_scan.py at -DFR_OWN=1) and corrects M by what is left (--cal).

  python3 tools/drive_gen/own1.py              write firmware/gen/drive_own_tables.h from own1_fit.json
  python3 tools/drive_gen/own1.py --check      fail if the header is not what this writes
  python3 tools/drive_gen/own1.py --fit        re-fit G and M on the float model -> own1_fit.json
  python3 tools/drive_gen/own1.py --cal [N]    N passes of M against the integer kernel -> own1_fit.json

Tables (integers, deterministic from own1_fit.json and the constants here):
  OW_SC, OW_QC[2 OW_HALF + 1]   s and s^2: Q23 out (2^22 = 1), at x = (i - OW_HALF) 2^OW_SH in the input's Q24 (u = 1
                           at 2^23: u in +-32); OW_SF, OW_QF the same at a step of 2^OW_SHF (u in +-1/2)
  OW_PSC, OW_PQC, OW_PSF, OW_PQF   P at their nodes: 2 x the integral of the interpolated curve from 0 (int64, one
                           function across the fine table's edges; ADAA-1)
  OW_B[4][128]             the even part b per drive row and AMOUNT, Q15
  OW_G[4][128], OW_M[4][128]   the gain to the curve and the make-up, Q16 (index 0 = index 1)
  OW_KE[128], OW_KD[128]   the pre-emphasis depth K and the de-emphasis boost K / (1 - K), Q15
  OW_GD[128]               the de-emphasis pole's TPT G (Q30): g' = (1 - K) tan(pi fe / fs)
  OW_TW[128]               AMOUNT's weight on TONE's corner (Q15): log fc = w log fc(TONE) + (1 - w) log 20 kHz
  OW_SVF[OW_NT][3]         the low-pass's TPT SVF coefficients (a1, a2, a3; Q30) on a log grid OW_F0 .. 20 kHz
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.path.join(ROOT, "firmware", "gen", "drive_own_tables.h")
FIT = os.path.join(HERE, "own1_fit.json")
TARGET = os.path.join(ROOT, "tests", "baselines", "drive_own_target.json")
FS = 44100.0

HALF, SH = 2048, 17                 # the curve's table: 4097 points, a step of 2^17 in Q24 (u / 64), u in +-32
SHF = 11                            # ... and a fine one over u in +-1/2: 4097 points, a step of 2^11 (u / 4096): the
                                    # chords of s^2 near 0 (|u| < 1/64 in one coarse step) made a |u| there, an even
                                    # distortion -53 dB re a quiet note whatever its level; fine: under -88 dB
BND = HALF >> (SH - SHF)            # the fine table's edge in coarse steps from 0 (32: u = 1/2)
UQ = 23                             # u = 1 at 2^23 (the curve's input is Q24 of the bus: 2^24 = full scale x G / 2)
YQ = 22                             # the curve's output: 2^22 = 1 (the ceiling's scale), Q23 of the bus (0.5 FS)
BETA_LO = (0.34, 0.30, 0.26, 0.42)  # the curve's even part per drive row (params.c TYPE_DRIVE_ROW) at AMOUNT 1 ...
BETA_HI = (0.20, 0.16, 0.12, 0.26)  # ... and at AMOUNT 127, linear between
# the emphasis shelf's corner, the output DC high-pass (Hz). No high-pass before the curve: the bus has no DC, and a
# high-pass there leaves a slow low-frequency remainder after a note stops that the gain would drive into the curve
# (a tail of 60 ms and more at AMOUNT 127); the 25 Hz one after it takes the curve's even part's DC out in ~6 ms.
F_PE, F_DC = 150.0, 25.0
KE_MAX, KE_EXP = 0.40, 0.8          # the emphasis depth at AMOUNT 127 (-4.4 dB) and its law (a / 127)^KE_EXP
FT0, FT1, FTOP = 1600.0, 9000.0, 20000.0   # TONE 0 / 127's corner at AMOUNT 127, the corner AMOUNT opens toward
TW_EXP = 0.6                        # AMOUNT's weight on the corner: (a / 127)^TW_EXP
SVF_Q = 0.7071
NT, F0 = 256, 1000.0                # the SVF grid: 256 points, log, 1 kHz .. 20 kHz


def q(v, bits):
    r = int(round(v * (1 << bits)))
    assert -(1 << 31) <= r < (1 << 31), (v, bits)
    return r


def tpt_g(fc):
    g = math.tan(math.pi * fc / FS)
    return g / (1 + g)


def beta(row, a):
    return BETA_LO[row] + (BETA_HI[row] - BETA_LO[row]) * (a - 1) / 126.0


def sig(u):
    return u / math.sqrt(1 + u * u)


def _nodes(fn, sh):
    return [int(round((1 << YQ) * fn((i - HALF) * 2 ** sh / 2 ** UQ))) for i in range(2 * HALF + 1)]


def _prims(f, sh, start=None):
    """P at the nodes, 2 x the integral of the interpolated curve from u = 0 (exact integers: a segment adds
    (f[j] + f[j + 1]) 2^sh); start: (node, value) to grow from instead of 0 at the centre, outward both ways"""
    i0, v0 = start or (HALF, 0)
    p = [0] * len(f)
    p[i0] = v0
    for j in range(i0, len(f) - 1):
        p[j + 1] = p[j] + (f[j] + f[j + 1]) * (1 << sh)
    for j in range(i0, 0, -1):
        p[j - 1] = p[j] - (f[j - 1] + f[j]) * (1 << sh)
    return p


def curves():
    """the two curves, s (odd) and s^2 (even), Q23 out, each a fine table over u in +-1/2 and a coarse one over +-32,
    with P at their nodes (2 x the integral from 0, the same units in both: the coarse table's P grows outward from the
    fine one's at its edges, so P is one function across the edge): [fine f, fine P, coarse f, coarse P] per curve.
    F = s + b s^2 is their sum"""
    out = []
    for fn in (sig, lambda u: sig(u) ** 2):
        ff, fc = _nodes(fn, SHF), _nodes(fn, SH)
        assert ff[HALF] == 0 and ff[0] == fc[HALF - BND] and ff[-1] == fc[HALF + BND]
        pf = _prims(ff, SHF)
        hi = _prims(fc, SH, (HALF + BND, pf[-1]))
        lo = _prims(fc, SH, (HALF - BND, pf[0]))
        pc = lo[:HALF] + hi[HALF:]
        assert max(abs(v) for v in pc) < 2 ** 60                             # drv_own1.c ow_prim: no overflow
        out.append((ff, pf, fc, pc))
    (sf, _, sc, _), (qf, _, qc, _) = out
    for b in BETA_LO + BETA_HI:                         # s + b s^2 never falls (b < 1/2), also as rounded
        for x1, y1 in ((sf, qf), (sc, qc)):
            assert all(x + b * y <= x2 + b * y2 + 1 for x, y, x2, y2 in zip(x1, y1, x1[1:], y1[1:]))   # (1 LSB)
    return out


def ke(a):
    return KE_MAX * (a / 127.0) ** KE_EXP


def tw(a):
    return (a / 127.0) ** TW_EXP


def fc_of(a, t):
    lt = math.log(FT0) + t / 127.0 * (math.log(FT1) - math.log(FT0))
    w = tw(a)
    return math.exp(w * lt + (1 - w) * math.log(FTOP))


def svf_coef(fc):
    g = math.tan(math.pi * fc / FS)
    k = 1 / SVF_Q
    a1 = 1 / (1 + g * (g + k))
    a2 = g * a1
    return a1, a2, g * a2


def grid_fc(i):
    return F0 * (FTOP / F0) ** (i / (NT - 1))


def grid_pos(fc):
    """fc -> the SVF grid's position x 256"""
    return (NT - 1) * math.log(fc / F0) / math.log(FTOP / F0) * 256


# ---- the float model (the level fit)

def _lfilt():
    import numpy as np
    from scipy import signal
    return np, signal


def _onepole_lp(fc):
    np, signal = _lfilt()
    g = math.tan(math.pi * fc / FS)
    return np.array([g, g]) / (1 + g), np.array([1, (g - 1) / (1 + g)])


def model(x, row, a, t, G, M=1.0):
    """the chain on x (float, full scale 1), at 1x; returns the output (full scale 1)"""
    np, signal = _lfilt()
    K = ke(a)
    b, aa = _onepole_lp(F_PE)
    x = x - K * signal.lfilter(b, aa, x)
    u = x * 2 * G
    s = u / np.sqrt(1 + u * u)
    y = 0.5 * (s + beta(row, a) * s * s)
    if K > 0:
        b, aa = _onepole_lp(F_PE * (1 - K))
        y = y + K / (1 - K) * signal.lfilter(b, aa, y)
    fc = fc_of(a, t)
    w = 2 * FS * math.tan(math.pi * fc / FS)
    bz, az = signal.bilinear([w * w], [1, w / SVF_Q, w * w], FS)
    y = signal.lfilter(bz, az, y)
    b, aa = _onepole_lp(F_DC)
    y = y - signal.lfilter(b, aa, y)
    return y * M


def stimuli():
    np, _ = _lfilt()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import drive_profile as dp
    k, sines, chord = dp._stims()
    return [s.astype(float) / 32768 for s in sines] + [chord.astype(float) / 32768], dp.N


def gains(row, a, t, G, st):
    np, _ = _lfilt()
    xs, n = st
    out = []
    for x in xs:
        y = model(x, row, a, t, G)
        out.append(10 * math.log10(np.mean(y[-n:] ** 2) / np.mean(x[-n:] ** 2)))
    return out                                          # g12, g24, chord


def targets(js, row, a):
    r = js["main"][f"r{row}/a{a}/t64/m127"]
    return [r["g"][0], r["g"][1], r["cg"]]


def fit():
    import numpy as np
    js = json.load(open(TARGET))
    st = stimuli()
    lg = np.linspace(math.log(0.02), math.log(60.0), 241)
    res = {"G": [], "M": [], "spread": []}
    for row in range(4):
        Gs, Ms, sp = [], [], []
        for a in range(1, 128):
            T = np.array(targets(js, row, a))
            best = None
            for l in lg:
                e = np.array(gains(row, a, 64, math.exp(l), st)) - T
                s = (e.max() - e.min()) / 2
                if best is None or s < best[0] - 1e-9:
                    best = (s, l, -(e.max() + e.min()) / 2)
            Gs.append(best[1]); Ms.append(best[2]); sp.append(best[0])
        # G rises with AMOUNT: the running maximum (a knob that never drives less as it turns up)
        for i in range(1, len(Gs)):
            Gs[i] = max(Gs[i], Gs[i - 1])
        G = [math.exp(v) for v in Gs]
        M = []
        for a, g in zip(range(1, 128), G):
            T = np.array(targets(js, row, a))
            e = np.array(gains(row, a, 64, g, st)) - T
            M.append(10 ** ((-(e.max() + e.min()) / 2) / 20))
        res["G"].append(G); res["M"].append(M); res["spread"].append(sp)
        print(f"row {row}: G {G[0]:.3f} .. {G[-1]:.2f}, worst spread {max(sp):.2f} dB at AMOUNT {1 + int(np.argmax(sp))}")
    json.dump({"G": res["G"], "M": res["M"]}, open(FIT, "w"), indent=0)
    print("wrote", os.path.relpath(FIT, ROOT))


def cal(passes=1):
    """M against the integer kernel: measure every (row, AMOUNT) on the host (FR_OWN 1) and centre the three gains"""
    import numpy as np
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import drive_profile as dp
    import drive_scan as ds
    import subprocess
    js = json.load(open(TARGET))
    st = dp._stims()
    for p in range(passes):
        main()
        exe = ds.build(["-DFR_OWN=1"], "own1cal")
        J = json.load(open(FIT))
        worst = 0
        try:
            for row in range(4):
                for a in range(1, 128):
                    r = dp.profile(exe, row, a, 64, 127, st)
                    e = np.array([r["g"][0], r["g"][1], r["cg"]]) - np.array(targets(js, row, a))
                    c = -(e.max() + e.min()) / 2
                    worst = max(worst, float(np.abs(e).max()))
                    J["M"][row][a - 1] *= 10 ** (c / 20)
        finally:
            os.remove(exe)
        json.dump(J, open(FIT, "w"), indent=0)
        print(f"cal pass {p + 1}: worst |error| before it {worst:.3f} dB")
    main()


# ---- the tables

def arr(name, ctype, vals, per=12):
    rows = [",".join(str(v) for v in vals[i:i + per]) for i in range(0, len(vals), per)]
    return f"static const {ctype} {name}[{len(vals)}] = {{\n  " + ",\n  ".join(rows) + "\n};\n"


def arr2(name, ctype, rs, per=12):
    body = []
    for r in rs:
        body.append("{" + ",\n   ".join(",".join(str(v) for v in r[i:i + per]) for i in range(0, len(r), per)) + "}")
    return f"static const {ctype} {name}[{len(rs)}][{len(rs[0])}] = {{\n  " + ",\n  ".join(body) + "\n};\n"


def text():
    J = json.load(open(FIT))
    (sf, psf, sc, psc), (qf, pqf, qc, pqc) = curves()
    B = [[q(beta(r, 1), 15)] + [q(beta(r, a), 15) for a in range(1, 128)] for r in range(4)]
    G = [[q(r[0], 16)] + [q(v, 16) for v in r] for r in J["G"]]
    M = [[q(r[0], 16)] + [q(v, 16) for v in r] for r in J["M"]]
    KE = [0] + [q(ke(a), 15) for a in range(1, 128)]
    KD = [0] + [q(ke(a) / (1 - ke(a)), 15) for a in range(1, 128)]
    gpe = math.tan(math.pi * F_PE / FS)
    GD = [q(gpe / (1 + gpe), 30)] + [q((1 - ke(a)) * gpe / (1 + (1 - ke(a)) * gpe), 30) for a in range(1, 128)]
    TW = [0] + [q(tw(a), 15) for a in range(1, 128)]
    SVF = [[q(c, 30) for c in svf_coef(grid_fc(i))] for i in range(NT)]
    o = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* GENERATED by tools/drive_gen/own1.py - do not edit. DRIVE TYPE 1 of the plugin's own-code build "
         "(drv_own1.c). */", "#ifndef DRIVE_OWN_TABLES_H", "#define DRIVE_OWN_TABLES_H", ""]
    o.append(f"#define OW_HALF {HALF}u\n#define OW_SH {SH}u\n#define OW_X ({HALF} << {SH})\n#define OW_SHF {SHF}u\n"
             f"#define OW_XF ({HALF} << {SHF})\n#define OW_NT {NT}u")
    o.append(f"#define OW_GPE {q(tpt_g(F_PE), 30)}     /* the emphasis shelf's low-pass ({F_PE:g} Hz) */")
    o.append(f"#define OW_GDC {q(tpt_g(F_DC), 30)}      /* the output DC high-pass ({F_DC:g} Hz) */")
    p0, p1, pt = (int(round(grid_pos(v))) for v in (FT0, FT1, FTOP))
    o.append(f"#define OW_TP0 {p0}         /* TONE 0's corner on the SVF grid (x 256) */")
    o.append(f"#define OW_TP1 {p1}         /* TONE 127's */")
    o.append(f"#define OW_TPT {pt}         /* 20 kHz, where AMOUNT opens it toward */\n")
    o.append(arr("OW_SF", "int32_t", sf, 10))
    o.append(arr("OW_PSF", "int64_t", psf, 6))
    o.append(arr("OW_SC", "int32_t", sc, 10))
    o.append(arr("OW_PSC", "int64_t", psc, 6))
    o.append(arr("OW_QF", "int32_t", qf, 10))
    o.append(arr("OW_PQF", "int64_t", pqf, 6))
    o.append(arr("OW_QC", "int32_t", qc, 10))
    o.append(arr("OW_PQC", "int64_t", pqc, 6))
    o.append(arr2("OW_B", "int32_t", B))
    o.append(arr2("OW_G", "int32_t", G))
    o.append(arr2("OW_M", "int32_t", M))
    o.append(arr("OW_KE", "int32_t", KE))
    o.append(arr("OW_KD", "int32_t", KD))
    o.append(arr("OW_GD", "int32_t", GD))
    o.append(arr("OW_TW", "int32_t", TW))
    o.append(arr2("OW_SVF", "int32_t", SVF, 3))
    o.append("#endif")
    return "\n".join(o) + "\n"


def main():
    open(OUT, "w").write(text())
    print("wrote", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    if "--fit" in sys.argv:
        fit()
        main()
    elif "--cal" in sys.argv:
        i = sys.argv.index("--cal")
        cal(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 1)
    elif "--check" in sys.argv:
        ok = open(OUT).read() == text()
        print("drive_own_tables.h:", "up to date" if ok else "STALE (run tools/drive_gen/own1.py)")
        sys.exit(0 if ok else 1)
    else:
        main()
