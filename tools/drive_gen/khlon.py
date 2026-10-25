# SPDX-License-Identifier: GPL-3.0-only
"""KHLON's tables (firmware/src/drv_khlon.c), from khlon_design.json: the float design of each knob row (a fit of the
circuit at 44.1 kHz, made offline) quantised here, deterministically. Coefficients Q24, a negated (y = ... + a1 y1 +
a2 y2), signals in volts Q20 inside the TYPE.

  KH_G[66][11]      the stored GAIN rows (AMOUNT KH_A_ROWS: 1, 3 .. 63, 64, 66 .. 124, 125, 126, 127; GAIN (a - 1) / 126):
                    the gain stage (b0 b1 b2 a1 a2), the clean paths (b0 b1 a1), the first-order slot (b0 b1 a1):
                    after the clipper (its own path into the sum) below KH_PRE_ROW, before it (the gain stage's third
                    order) from KH_PRE_ROW (AMOUNT 126, 127)
  KH_AMAP[127]      per AMOUNT: 2 x its stored row + 1 when it is the mean of that row and the next (the AMOUNTs between
                    two stored ones: the coefficients and the scale averaged)
  KH_GS[66]         per stored GAIN row: the output section's scale (Q24): the slot's fold gain k (pre rows) x the
                    level x 1/32 (volts Q20 -> the bus, 1.0 V = 32768)
  KH_T[65][5]       per stored TONE row (j = 0, 2 .. 128; j / 128): the output section (the summing amp's pole x the
                    treble control); an odd j the mean of its neighbours
  KH_LT[65]         per stored TONE row: the level (Q14)
  KH_GFS[2 x (2 KH_GN - 1)]  the clipper's nodes over -9.06 .. 9.06 V (the negative half mirrored: g odd, F even;
                    KH_GF = the node at 0: the half for e >= 0, as the AA clipper reads it)
  KH_GF             the clipper's nodes for e >= 0 (the curve is odd; e in Q20, 1/64 V nodes 0 .. 9.06 V: |e| stays
                    below 9 V): {g = G in Q21 (2 x the Q20 curve), F = the exact integral of the interpolated curve up
                    to the node (Q28: sum of g[i] + g[i + 1])}; read as one 64-bit load: 8-byte aligned
  KH_RCP[1024]      the reciprocal of a normalised mantissa (the anti-aliased clipper's quotient without a divide):
                    entry j ~ 2^26 / (j + 1024.5), uint16
  KH_AA_A           the first AMOUNT index (a - 1) with the anti-aliased clipper (GAIN >= KH_AA_GAIN); below it the
                    plain clipper on the same table
  KH_KC4, KH_RAIL   the coupling capacitor's update (Q32), the gain stage's rail (Q20)
Row tables are stored with the sign bit flipped (x ^ 0x80000000; drv_khlon.c undoes it when a row loads): some values
would otherwise read as an address in the chip ROM window. KH_GC is read per sample, so it is checked plain.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HALF = 640
GN = 581                                     # nodes for e >= 0 (|e| < 9 V = 576 cells)
AA_GAIN = 0.6                                # GAIN at and above: ADAA (below it the folding stays under -40 dB)
RB = 11
A_ROWS = list(range(1, 64, 2)) + list(range(64, 125, 2)) + [125, 126, 127]
T_ROWS = list(range(0, 129, 2))


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


def q24(v):
    r = int(round(v * (1 << 24)))
    assert -(1 << 31) <= r < (1 << 31), v
    return r


def design():
    return json.load(open(os.path.join(HERE, "khlon_design.json")))


def bq(s):                                   # [b0, b1, b2, a1, a2] -> Q24, a negated
    return [q24(s[0]), q24(s[1]), q24(s[2]), q24(-s[3]), q24(-s[4])]


def rows_g(J):
    out = []
    for r in [J["gain"][a - 1] for a in A_ROWS]:
        o1, cl, sl = bq(r["o1"]), bq(r["cl"]), bq(r["slot"])
        out.append(o1 + [cl[0], cl[1], cl[3]] + [sl[0], sl[1], sl[3]])
    return out


def pre_row(J):
    """the first stored row in the pre mode (every stored row from it on is pre, none before)"""
    p = [i for i, a in enumerate(A_ROWS) if J["gain"][a - 1]["pre"]]
    assert p == list(range(p[0], len(A_ROWS)))
    assert [a for a in range(1, 128) if J["gain"][a - 1]["pre"]] == A_ROWS[p[0]:]   # (no AMOUNT averages across)
    return p[0]


def check_stable(rs):
    """every averaged row (the mean of two neighbours) keeps its poles inside the unit circle"""
    import cmath
    for r0, r1 in zip(rs, rs[1:]):
        for o in (0,):
            a1, a2 = -((r0[o + 3] + r1[o + 3]) >> 1) / 2 ** 24, -((r0[o + 4] + r1[o + 4]) >> 1) / 2 ** 24
            d = cmath.sqrt(a1 * a1 - 4 * a2)
            assert max(abs((-a1 + d) / 2), abs((-a1 - d) / 2)) < 1, (r0, r1)


def rows_t(J):
    return [bq(J["tone"][j]) for j in T_ROWS]


def gs(J, unity=False):
    lg = J.get("lg_db", [0.0] * 127)
    return [q24(J["gain"][a - 1]["k"] * (1.0 if unity else 10 ** (lg[a - 1] / 20) / 32)) for a in A_ROWS]


def lt(J):
    lt_db = J.get("lt_db", [0.0] * 129)
    return [int(round(16384 * 10 ** (lt_db[j] / 20))) for j in T_ROWS]


def gf(J):
    g = [int(round(v * (1 << 20))) for v in J["clip"]]
    assert len(g) == 2 * HALF + 1
    assert all(g[HALF + k] == -g[HALF - k] for k in range(HALF + 1))     # odd: the negative half mirrors
    g = [2 * v for v in g[HALF:HALF + GN]]
    F = [0]
    for i in range(1, GN):
        F.append(F[-1] + g[i] + g[i - 1])
    assert max(F) < 2 ** 31
    out = []
    for a, b in zip(g, F):
        out += [a, b]
    return out


def gfs(c):
    """the node table over the whole range, -(GN - 1) .. GN - 1: the negative half mirrored (g odd, F even), so the
    plain clipper takes its cell and fraction from e itself (no |e|, no sign select)"""
    g, F = c[0::2], c[1::2]
    out = []
    for j in range(-(GN - 1), GN):
        out += [-g[-j] if j < 0 else g[j], F[abs(j)]]
    return out


def rcp():
    h = 1 << (RB - 1)
    r = [round(2 ** (15 + RB) / (J + 0.5)) for J in range(h, 2 * h)]
    assert 32768 < min(r) and max(r) < 65536
    return r


def aa_a(J):
    """the first AMOUNT index whose GAIN is at or above AA_GAIN"""
    return next(a - 1 for a in range(1, 128) if (a - 1) / 126 >= AA_GAIN)


def rows2d(name, rs):
    import gen_drive_tables as G
    rs = [[(v & 0xFFFFFFFF) ^ 0x80000000 for v in r] for r in rs]
    G.check(name, [v - (1 << 32) if v >> 31 else v for r in rs for v in r])
    body = ",\n  ".join("{" + ",".join(f"{v}u" for v in r) + "}" for r in rs)
    return f"static const uint32_t {name}[{len(rs)}][{len(rs[0])}] = {{\n  {body}\n}};\n"


def tables():
    import gen_drive_tables as G
    J = design()
    assert len(J["gain"]) == 127 and len(J["tone"]) == 129
    assert not J.get("partial") or os.environ.get("DRV_GEN_PARTIAL"), "khlon_design.json: some rows are placeholders (not fitted yet)"
    kc4 = 4 * int(round(J["kc"] * (1 << 30)))
    rail = int(round(J["rail"] * (1 << 20)))
    c = gf(J)
    G.check("KH_GF", c)
    g, t = rows_g(J), rows_t(J)
    check_stable(g[:pre_row(J)]); check_stable(t)
    head = (f"#define KH_PRE_ROW {pre_row(J)}u\n#define KH_KC4 {kc4}\n#define KH_RAIL {rail}\n"
            f"#define KH_GN {GN}\n#define KH_AA_A {aa_a(J)}u\n")
    cs = gfs(c)
    G.check("KH_GFS", cs)
    gct = G.arr("KH_GFS", "int32_t", cs, 8).replace("static const int32_t KH_GFS", "static const int32_t "
                                                   "__attribute__((aligned(8))) KH_GFS")
    gct += "#define KH_GF (KH_GFS + 2 * (KH_GN - 1))   /* the node at 0 V: e >= 0 reads from here, as before */\n"
    return [head, rows2d("KH_G", g), G.arr("KH_AMAP", "uint8_t", amap(A_ROWS, 127, 1)),
            G.arr("KH_GS", "int32_t", gs(J)), rows2d("KH_T", t), G.arr("KH_LT", "int32_t", lt(J)), gct,
            G.arr("KH_RCP", "uint16_t", rcp())]
