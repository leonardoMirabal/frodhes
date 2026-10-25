# SPDX-License-Identifier: GPL-3.0-only
"""Mode coefficients shared by the generator and the golden model (IMPLEMENTATION-v3 §5.6, §6.5).

mode_coefs(f, t60, t60_rel)  -> the per-mode table words (sh, ch, th, d, drel, cr), as the generator emits them
coef(words, d, t30, k16)     -> (E, eps) Q30 ints: the run-time coefficient path, bit-identical to rh_coef() in
                                firmware/src/rhodes.c (C integer semantics: truncating division, floor shifts)
"""
import math

FS = 44100.0
Q30, Q29, Q16 = 1 << 30, 1 << 29, 1 << 16


def mode_coefs(f, t60, t60_rel):
    absent = dict(sh=0, ch=0, th=0, d=0, drel=0, cr=0)
    if f <= 0 or f > 18000:
        return absent
    th = 2 * math.pi * f / FS
    if Q16 / (8 * math.sin(th / 2) ** 2) >= 2 ** 31:        # below ~27.5 Hz cr overflows int32: leave the mode out
        return absent
    r = math.exp(-6.907755279 / (t60 * FS))
    rr = math.exp(-6.907755279 / (t60_rel * FS))
    return dict(sh=int(round(math.sin(th / 2) * Q30)), ch=int(round(math.cos(th / 2) * Q30)),
                th=max(1, int(round(th * Q29))), d=int(math.ceil((1 - r * r) * Q30)),
                drel=int(math.ceil((1 - rr * rr) * Q30)), cr=int(round(Q16 / (8 * math.sin(th / 2) ** 2))))


def _i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _mul(a, b, sh):
    return _i32((a * b) >> sh)


def _tdiv(a, b):
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def coef(w, d30, t30=0, k16=Q16):
    """(E, eps) exactly as rh_coef() computes them."""
    a = _i32((k16 * (k16 - 65536)) >> 16)
    c = _i32((a * (k16 - 131072)) >> 16)
    d2 = _mul(d30, d30, 30); d3 = _mul(d2, d30, 30)
    dp = ((k16 * d30) >> 16) - (((a * d2) >> 16) >> 1) + _tdiv(_i32((c * d3) >> 16), 6)
    dpi = 1 if dp < 1 else _i32(dp)
    dp2 = _mul(dpi, dpi, 30); dp3 = _mul(dp2, dpi, 30)
    q = _i32(Q30 - (dpi >> 2) - 3 * (dp2 >> 5))
    om = _i32((dpi >> 1) + (dp2 >> 3) + (dp3 >> 4))
    dl = _mul(w["th"], t30, 30)
    dl2 = _mul(dl, dl, 30); dl3 = _mul(dl2, dl, 30); dl4 = _mul(dl2, dl2, 30); dl5 = _mul(dl4, dl, 30)
    cdl = _i32(Q30 - (dl2 >> 1) + _tdiv(dl4, 24)); sdl = _i32(dl - _tdiv(dl3, 6) + _tdiv(dl5, 120))
    sp = _i32(_mul(w["sh"], cdl, 30) + _mul(w["ch"], sdl, 30))
    cr = w["cr"] - 2 * _mul(w["cr"], t30, 30) + 3 * _mul(w["cr"], _mul(t30, t30, 30), 30)   # int64 (may pass 2^31)
    corr = Q30 + ((_mul(om, om, 30) * cr) >> 16)                                         # int64
    t1 = _i32((q * sp) >> 29)
    return _i32((t1 * corr) >> 30), dpi
