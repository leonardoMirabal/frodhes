# SPDX-License-Identifier: GPL-3.0-only
"""OHCD's tables (firmware/src/drv_ohcd.c), from ohcd_design.json: the float design of each knob row (a fit of the
circuit at 44.1 kHz, made offline) quantised here, deterministically. The full range (0.9.5): AMOUNT 1..127 is the
circuit's DRIVE knob from its minimum to its end (rot[a] in the design: AMOUNT <= 32 as the earlier capped range,
above it the first stage's peak gain rises evenly in dB), both op-amp rails modelled.

  OHCD_S1[65][5]    stage 1 x P per stored AMOUNT (A_ROWS: 1, 3 .. 63, 64, 66 .. 126, 127) (b0 b1 b2, a1 a2; Q28 out;
                    the input HP's x 2^8 and the 0.3 V input scale folded into b; P: the clipper's ADAA droop split,
                    sinc^sx / cos). Stored with the sign bit flipped (x ^ 0x80000000, drv_ohcd.c undoes it when a row
                    loads): some of its values would otherwise read as an address in the chip ROM window
  OHCD_AMAP[127]    per AMOUNT: 2 x its stored row + 1 when it is the mean of that row and the next
  OHCD_S2Q[9][5]    stage 2 x Q (1 / sinc^sx, after the clipper) per AMOUNT group: u x 2^12 in, u x 2^8 out (b Q24,
                    a Q28), before the IC1B rail (OHCD_RAIL2, ADAA-1)
  OHCD_SG[127]      the group per AMOUNT (its S1 row carries the matching P before the clipper)
  OHCD_OUT[65][5]   the tone network x 1 / cos (the rail's ADAA droop) per stored TONE row (j = 0, 2 .. 128; j / 128)
                    as one biquad (b0 b1 b2 a1 a2, Q28); an odd j the mean of its neighbours
  OHCD_CF[66]       the clipper's curve (u = 2.8 V / 2^17, 64 cells of 2^11 u, a guard point): the reference's form
  OHCD_GF[66][4]    the curve and its antiderivative per node, {2 g, F, g[k + 1] - g[k], 0} (F in u^2 / 4:
                    aa.antiderivative, sf 2): the anti-aliased clipper (ADAA-1, the shared reciprocal table DRV_AA_RCP)
  OHCD_LG[65]       per stored AMOUNT: the output level (Q16, the volts -> bus units and the level to the drive switched
                    off)
  OHCD_LT[65]       per stored TONE row: the level (Q14)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AA = True                    # anti-aliasing always on (the user, 2026-10-24): S1 x P, the clipper with ADAA-1, stage 2
                             # x Q, the IC1B rail with ADAA-1, the tone x 1 / cos (the plain kernel is gone)
QS2 = 28                     # stage 2's output shift (b x 2^24: u x 2^12 in, u x 2^8 out)
RAIL2 = 1 << 25              # the IC1B rail, +-2.8 V, in u x 2^8 (the same 2.8 V as the clipper's 2^17 u)
A_ROWS = list(range(1, 64, 2)) + list(range(64, 127, 2)) + [127]
T_ROWS = list(range(0, 129, 2))


def _q(b, a, gain, qs):
    v = [int(round((x * gain) * 2 ** qs)) for x in b] + [int(round(x * 2 ** qs)) for x in a]
    assert all(abs(x) < 2 ** 31 for x in v)
    return v


def design():
    return json.load(open(os.path.join(HERE, "ohcd_design.json")))


def rows_s1(J):
    """stage 1 x P per stored AMOUNT (P: sinc^sx / cos, the clipper's droop split before it)"""
    return [_q(*J["s1aa"][a - 1], J["s1_gain"], J["qs1"]) for a in A_ROWS]


def rows_s2(J):
    """stage 2 x Q (1 / sinc^sx) per AMOUNT group: u x 2^12 in, u x 2^8 out (b x 2^24, a x 2^28)"""
    return [_q(b, a, 1 / 16, QS2) for b, a in J["s2aa"]]


def gf(J):
    """{g, F} per node: the curve (u) and its running antiderivative (u^2 / 4), a guard node at the end"""
    sys.path.insert(0, HERE)
    import aa
    cf = J["cf"]
    F = aa.antiderivative(cf, [1 << J["csh"]] * (len(cf) - 1), 2)
    return [[2 * int(cf[i]), int(F[i]), int(cf[i + 1] - cf[i]) if i + 1 < len(cf) else 0, 0] for i in range(len(cf))]


def rows_out(J):
    """the tone x 1 / cos (the IC1B rail's ADAA droop) per stored TONE row: b0 b1 b2 a1 a2, Q28"""
    return [_q(*J["out2"][j], 1.0, 28) for j in T_ROWS]


def levels(J):
    conv = 32768 / (J["u_per_volt"] * 16)                # Q4u volts -> bus units (32768 = 1.0)
    lg_db, lt_db = J.get("lg_db", [0.0] * 127), J.get("lt_db", [0.0] * 129)
    cal = os.path.join(HERE, "ohcd_levels.json")              # tools/drive_gen/calib_levels.py OHCD (x on top)
    C = json.load(open(cal)) if os.path.exists(cal) else {"amount": [1.0] * 128, "tone": [1.0] * 128}
    lg = [int(round(65536 * conv * 10 ** (lg_db[a - 1] / 20) * C["amount"][a])) for a in A_ROWS]
    lt = [int(round(16384 * 10 ** (lt_db[j] / 20) * C["tone"][min(j, 127)])) for j in T_ROWS]
    return lg, lt


def rows2d(name, ctype, rs, flip=False):
    """a 2-D table [len(rs)][len(rs[0])]; flip: each value stored with its sign bit flipped (as uint32 bits)"""
    import gen_drive_tables as G
    if flip:
        rs = [[(v & 0xFFFFFFFF) ^ 0x80000000 for v in r] for r in rs]
        G.check(name, [v - (1 << 32) if v >> 31 else v for r in rs for v in r])
        ctype = "uint32_t"
    else:
        G.check(name, [v for r in rs for v in r])
    body = ",\n  ".join("{" + ",".join(f"{v}u" if flip else str(v) for v in r) + "}" for r in rs)
    return f"static const {ctype} {name}[{len(rs)}][{len(rs[0])}] = {{\n  {body}\n}};\n"


def amap(rows, n, first):
    """per knob value (first .. first + n - 1): 2 x stored index (+1: the mean of it and the next)"""
    out = []
    for a in range(first, first + n):
        if a in rows:
            out.append(2 * rows.index(a))
        else:
            assert a - 1 in rows and a + 1 in rows, a
            out.append(2 * rows.index(a - 1) + 1)
    return out


def check_stable(rs, every=False):
    """every averaged row (the mean of two neighbours; every: each row too) keeps its poles inside the unit circle
    (a1 a2 Q28)"""
    import cmath
    pairs = list(zip(rs, rs[1:])) + ([(r, r) for r in rs] if every else [])
    for r0, r1 in pairs:
        a1, a2 = ((r0[3] + r1[3]) >> 1) / 2 ** 28, ((r0[4] + r1[4]) >> 1) / 2 ** 28
        d = cmath.sqrt(a1 * a1 - 4 * a2)
        assert max(abs((-a1 + d) / 2), abs((-a1 - d) / 2)) < 1, (r0, r1)


def rows_at(a, t, J=None):
    """what drv_ohcd.c oh_set runs at AMOUNT step a (1..127), TONE step t (0..127): (s1[5] as the reference takes it,
    a not negated; out[5] (b0 b1 b2 a1 a2, a not negated); the level, Q16): a step between two stored rows their mean
    (Python >> = C's)"""
    J = J or design()
    s1, outs = rows_s1(J), rows_out(J)
    lg, lt = levels(J)
    ga = amap(A_ROWS, 127, 1)[a - 1]
    gr, gh = ga >> 1, ga & 1
    tj = t + ((t + 1) >> 7)
    tr, th = tj >> 1, tj & 1
    s = [(s1[gr][i] + s1[gr + 1][i]) >> 1 if gh else s1[gr][i] for i in range(5)]
    o = [(outs[tr][i] + outs[tr + 1][i]) >> 1 if th else outs[tr][i] for i in range(5)]
    g = (lg[gr] + lg[gr + 1]) >> 1 if gh else lg[gr]
    h = (lt[tr] + lt[tr + 1]) >> 1 if th else lt[tr]
    return s, o, (g * h + (1 << 13)) >> 14


def fw_words(a, t, J=None):
    """the 15 words oh_set leaves in oh_k at (a, t): S1 (a negated), S2 x Q of the AMOUNT's group (a negated), the tone
    (b x the level, a negated)"""
    J = J or design()
    s, o, lv = rows_at(a, t, J)
    s2 = rows_s2(J)[J["sgrp"][a - 1]]
    return (s[:3] + [-s[3], -s[4]] + s2[:3] + [-s2[3], -s2[4]] +
            [(o[0] * lv) >> 16, (o[1] * lv) >> 16, (o[2] * lv) >> 16, -o[3], -o[4]])


def rail_ref(v, p):
    """the IC1B rail with ADAA-1, as drv_ohcd.c oh_rail (integers): the mean of v and p inside the rail, the rail when
    both are past it on one side, else the mean less (E(v) - E(p)) / (v - p), E(x) = (|x| - R)^2 / 2 past the rail
    (u units: >> 8; the quotient by the shared reciprocal table), kept within the rail"""
    import aa
    R = RAIL2
    av, ap = abs(v), abs(p)
    if av <= R and ap <= R:
        return (v + p) >> 1
    if v > R and p > R:
        return R
    if v < -R and p < -R:
        return -R
    ev = (av - R) >> 8 if av > R else 0
    ep = (ap - R) >> 8 if ap > R else 0
    d = v - p
    ad = abs(d) >> 8
    q = 0
    if ad:
        nz = 32 - ad.bit_length()
        r = aa.rcp()[((ad << nz) >> (32 - aa.RB)) - (1 << (aa.RB - 1))]
        q = (((ev * ev - ep * ep) * r) >> (48 - nz)) << 8
    return max(-R, min(R, ((v + p) >> 1) - (-q if d < 0 else q)))


def tables():
    import gen_drive_tables as G
    J = design()
    lg, lt = levels(J)
    s1 = rows_s1(J)
    assert len(J["s1aa"]) == 127 and len(J["out2"]) == 129 and len(J["sgrp"]) == 127 and len(J["rot"]) == 127
    check_stable(s1)
    check_stable(rows_out(J), every=True)
    head = [f"#define OHCD_AA {int(AA)}\n#define OHCD_QS1 {J['qs1']}\n#define OHCD_QS2 {QS2}\n"
            f"#define OHCD_CSH {J['csh']}\n#define OHCD_RAIL2 {RAIL2}\n",
            rows2d("OHCD_S1", "int32_t", s1, flip=True),
            G.arr("OHCD_AMAP", "uint8_t", amap(A_ROWS, 127, 1)),
            rows2d("OHCD_OUT", "int32_t", rows_out(J)),
            G.arr("OHCD_CF", "int16_t", J["cf"]),
            rows2d("OHCD_S2Q", "int32_t", rows_s2(J)), G.arr("OHCD_SG", "uint8_t", J["sgrp"]),
            rows2d("OHCD_GF", "int32_t", gf(J)).replace("static const int32_t",
                                                       "static const int32_t __attribute__((aligned(8)))")]
    return head + [G.arr("OHCD_LG", "int32_t", lg), G.arr("OHCD_LT", "int32_t", lt)]
