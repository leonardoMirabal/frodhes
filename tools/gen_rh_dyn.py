#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""RESPONSE A's velocity level curve (rhodes.c RH_DYN_V, the dynamics stage rh_dyn): a gain on the voice's output at
the strike, so the level moves and the tone (the swing through the pickup, the harmonics) stays.

    tools/gen_rh_dyn.py            print the table (Q15, v 0..126; v127: 32768)
    tools/gen_rh_dyn.py --check    exit 1 if rhodes.c's table differs from it

Two parts, in dB, added:
  - the soft end (0.9.10, 2026-11-09): -9 s((40 - v) / 30), s(t) = 3t^2 - 2t^3 on [0, 1]: 0 dB from v40, -9 dB at v10
    and below;
  - the convex curve (0.9.13): a cut from v24 to v126 through POINTS (a monotone cubic, Fritsch-Carlson: no step, no
    corner, no overshoot), 0 at v24 and below and at v127. With it the level re v127 (the engine alone, RMS 50-500 ms,
    the median over keys 28..88) is about -19 dB at v64, -13 at v88 and -7.6 at v104 (0.9.11: -14.6 / -9.1 / -5.3),
    a curve that rises slowly through the middle and steeply at the top, as a struck instrument's; v127 and the soft
    end (v24 and below) keep their levels, so the range v20 -> v127 stays ~32 dB, and the cut is only ever a cut (no
    chord gets louder, the headroom is v127's).
RESPONSE B (LIGHT) does not use the table (0.9.9's velocity law)."""
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware", "src", "rhodes.c")
HI = 127                                  # rhodes.c RH_DYN_V_HI: the table's length (v127 and over: 32768)
# the convex curve's cut (dB) at these velocities: the 0.9.11 engine's level re v127 (-14.6 at v64, -9.1 at v88, -5.3
# at v104 ...) moved to the target curve (-19 / -13 / -7.6, the soft end kept)
POINTS = [(16, 0.0), (24, 0.0), (32, -3.0), (40, -4.0), (48, -4.4), (56, -4.5), (64, -4.4), (72, -4.1), (80, -4.0), (88, -3.9),
          (96, -3.4), (104, -2.4), (112, -1.5), (120, -0.6), (127, 0.0)]


def smooth(t):
    t = min(1.0, max(0.0, t))
    return 3 * t * t - 2 * t * t * t


def soft_db(v):
    return -9.0 * smooth((40 - v) / 30.0) if v < 40 else 0.0


def pchip(pts):
    """Fritsch-Carlson monotone cubic through pts -> f(x)"""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    if d[0] * m[0] <= 0:
        m[0] = 0.0
    if d[-1] * m[-1] <= 0:
        m[-1] = 0.0

    def f(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = max(j for j in range(n - 1) if xs[j] <= x)
        t = (x - xs[i]) / h[i]
        h00, h10, h01, h11 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t, -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
        return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]
    return f


CUT = pchip(POINTS)


def curve_db(v):
    return soft_db(v) + CUT(v)


def table():
    return [min(32768, int(round(32768 * 10 ** (curve_db(v) / 20.0)))) for v in range(HI)]


def c_rows(t):
    return ["    " + ", ".join(str(x) for x in t[i:i + 16]) + ("," if i + 16 < len(t) else "") for i in range(0, len(t), 16)]


def in_source():
    s = open(SRC).read()
    m = re.search(r"static const uint16_t RH_DYN_V\[RH_DYN_V_HI\] = \{[^\n]*\n(.*?)\};", s, re.S)
    hi = re.search(r"#define RH_DYN_V_HI (\d+)u", s)
    return int(hi.group(1)), [int(x) for x in re.findall(r"\d+", m.group(1))]


def main():
    t = table()
    if "--check" in sys.argv:
        hi, src = in_source()
        ok = hi == HI and src == t
        print("RH_DYN_V %s" % ("matches" if ok else "DIFFERS from tools/gen_rh_dyn.py"))
        return 0 if ok else 1
    print("\n".join(c_rows(t)))
    for v in (8, 20, 24, 32, 40, 48, 64, 88, 104, 120, 126):
        print("v%-3d %6.2f dB (soft %5.2f, cut %5.2f)" % (v, curve_db(v), soft_db(v), CUT(v)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
