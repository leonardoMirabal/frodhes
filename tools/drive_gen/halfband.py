#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The drive's 2x oversampling filter (firmware/src/drive.c DRV_HB_*): a polyphase IIR half-band, two branches of
first-order allpass sections in z^-2 (the classic two-path structure, Valenzuela and Constantinides 1983; the elliptic
design in closed form). H(z) = (A0(z^2) + z^-1 A1(z^2)) / 2: 2x up = both branches on the input, interleaved; 2x down =
the even samples through A0, the odd ones through A1, their mean.

  python3 tools/drive_gen/halfband.py [NC]       the coefficients (Q30, as drive.c has them) and the response
Used by tests/py/test_drive_os.py (the constants in drive.c are these)."""
import math
import sys

NC, TBW = 4, 0.035                 # 4 coefficients (2 a branch, drive.c's default DRV_HB_NC); transition 0.035 fs (2x):
                                   # flat to 18.96 kHz, the stopband from 25.14 kHz (aliases fold below 18.96 kHz only
                                   # from there) at -47.7 dB; 6 coefficients (DRV_HB_NC 6): -71.5 dB


def design(nc=NC, tbw=TBW):
    k = math.tan((1 - tbw * 2) * math.pi / 4) ** 2
    kk = (1 - k * k) ** 0.25
    e = 0.5 * (1 - kk) / (1 + kk)
    e4 = e ** 4
    q = e * (1 + e4 * (2 + e4 * (15 + 150 * e4)))
    order = nc * 2 + 1
    out = []
    for idx in range(nc):
        c = idx + 1
        num, i = 0.0, 0
        while True:
            t = (-1) ** i * q ** (i * (i + 1)) * math.sin((2 * i + 1) * c * math.pi / order)
            num += t
            i += 1
            if abs(t) < 1e-30:
                break
        num *= q ** 0.25
        den, i = 0.5, 1
        while True:
            t = (-1) ** i * q ** (i * i) * math.cos(2 * i * c * math.pi / order)
            den += t
            i += 1
            if abs(t) < 1e-30:
                break
        ww = (num / den) ** 2
        x = math.sqrt((1 - ww * k) * (1 - ww / k)) / (1 + ww)
        out.append((1 - x) / (1 + x))
    return out


def q30(nc=NC, tbw=TBW):
    return [int(round(a * 2 ** 30)) for a in design(nc, tbw)]


def response(coefs, f, fs=88200.0):
    """|H| at f (Hz) at the 2x rate; coefs as floats; even index -> branch 0"""
    import cmath
    z = cmath.exp(1j * 2 * math.pi * f / fs)
    a0 = a1 = 1
    for i, a in enumerate(coefs):
        s = (a + z ** -2) / (1 + a * z ** -2)
        if i % 2 == 0:
            a0 *= s
        else:
            a1 *= s
    return abs(0.5 * (a0 + a1 / z))


if __name__ == "__main__":
    nc = int(sys.argv[1]) if len(sys.argv) > 1 else NC
    c = q30(nc)
    print("branch 0:", c[0::2], "branch 1:", c[1::2])
    fp, fs = 88200 / 4 - TBW * 88200, 88200 / 2 - (88200 / 4 - TBW * 88200)
    cf = [v / 2 ** 30 for v in c]   # (the response's own NC: nc)
    print(f"pass <= {fp:.0f} Hz: min {20 * math.log10(min(response(cf, f) for f in range(10, int(fp), 50))):.5f} dB")
    print(f"stop >= {fs:.0f} Hz: max {20 * math.log10(max(response(cf, f) for f in range(int(fs), 44100, 25))):.1f} dB")
    sys.exit(0)
