#!/usr/bin/env python3
"""v3 review scratch: the run-time coefficient path for the CHOSEN kernel (MC3 + EF1), in integers, no division.
Host per key and mode: sh = sin(theta/2) Q30, ch = cos(theta/2) Q30, th = theta Q29, d = 1 - r^2 Q30 (ceil),
cr = 1/(8 sin^2(theta/2)) Q16 (the (1-r)^2 correction weight; < 2^31 for theta >= 2 pi 27.5/44100),
rescaled at run time to the shifted angle: cr' = cr (1 - 2t + 3t^2).
Run time for cents c (TUNE +-50 or bend +-200) and T60 scale S (k = 1/S, Q16):
  d'  = k d - k(k-1)/2 d^2 + k(k-1)(k-2)/6 d^3                  (1 - r'^2)
  q   = 1 - d'/4 - 3 d'^2/32                                     (sqrt r' = (1-d')^(1/4))
  om  = 1 - r' = d'/2 + d'^2/8 + d'^3/16                         (1 - sqrt(1-d'))
  dl  = th * t / 2, t = 2^(c/1200) - 1                           (half the angle change)
  s'  = sh cos(dl) + ch sin(dl)                                  (sin(theta'/2), series to dl^5)
  E   = 2 q s' (1 + om^2 cr)                                     (sqrt(1 + r'^2 - 2 r' cos theta'))
  eps = d'
Pass: pitch <= 0.2 cent (held), T60 <= 1 %, over keys 21..108, 3 modes (ratios 1, 7, 20 < 18 kHz), c in
-250..250, S in 0.25..4, T60 per mode (18 s bass V1 .. 0.05 s O2) plus the release case (T60 0.2..0.5 s, where
a 2 cent tolerance is enough).
"""
import numpy as np

FS = 44100.0; Q30 = 1 << 30; Q29 = 1 << 29; Q16 = 1 << 16


def mul(a, b, sh):
    return (np.int64(a) * np.int64(b)) >> np.int64(sh)


def coef_mc3(sh30, ch30, th29, d30, cr16, k16, t30):
    kd = mul(k16, d30, 16); km1 = k16 - Q16; km2 = k16 - 2 * Q16
    d2 = mul(d30, d30, 30); d3 = mul(d2, d30, 30)
    dp = kd - (mul(mul(k16, km1, 16), d2, 16) >> 1) + mul(mul(mul(k16, km1, 16), km2, 16), d3, 16) // 6
    dp = max(int(dp), 1)
    dp2 = mul(dp, dp, 30); dp3 = mul(dp2, dp, 30)
    q = Q30 - (dp >> 2) - 3 * (dp2 >> 5)
    om = (dp >> 1) + (dp2 >> 3) + (dp3 >> 4)
    dl = mul(th29, t30, 30)                         # theta*t/2: Q29*Q30>>30 = Q29 -> interpret as Q30 of theta*t/2
    dl2 = mul(dl, dl, 30); dl3 = mul(dl2, dl, 30); dl4 = mul(dl2, dl2, 30); dl5 = mul(dl4, dl, 30)
    cdl = Q30 - (dl2 >> 1) + dl4 // 24; sdl = dl - dl3 // 6 + dl5 // 120
    sp = mul(sh30, cdl, 30) + mul(ch30, sdl, 30)
    # the correction weight at the shifted angle: cr' = cr (1 - 2t + 3t^2), since sin(theta'/2) ~ (1+t) sin(theta/2)
    cr_s = cr16 - 2 * mul(cr16, t30, 30) + 3 * mul(cr16, mul(t30, t30, 30), 30)
    corr = Q30 + mul(mul(om, om, 30), cr_s, 16)
    E = mul(mul(2 * q, sp, 30), corr, 30)
    return int(E), int(dp)


def poles(E, eps):
    Dq = 1 - eps / Q30; Eq = E / Q30
    r = np.sqrt(Dq); th = np.arccos((1 + Dq - Eq * Eq) / (2 * r))
    return r, th


worst = {}
for midi in range(21, 109):
    f0 = 440 * 2 ** ((midi - 69) / 12)
    cases = [(1.0, 18.0 if midi < 62 else 18.0 * 2 ** (-(midi - 62) / 10), "held"), (7.0, 0.5, "held"), (20.0, 0.05, "held"),
             (1.0, 0.5 if midi < 43 else 0.4 if midi < 61 else 0.3 if midi < 84 else 0.2, "release")]
    for ratio, t60, kind in cases:
        f = f0 * ratio
        if f > 18000:
            continue
        th = 2 * np.pi * f / FS; r = np.exp(-6.907755279 / (t60 * FS))
        sh30 = int(round(np.sin(th / 2) * Q30)); ch30 = int(round(np.cos(th / 2) * Q30)); th29 = int(round(th * Q29))
        d30 = int(np.ceil((1 - r * r) * Q30)); cr16 = int(round(Q16 / (8 * np.sin(th / 2) ** 2)))
        assert cr16 < 2 ** 31
        for S in (0.25, 0.5, 1.0, 2.0, 4.0):
            k16 = int(round(Q16 / S))
            for c in (-250, -200, -50, 0, 50, 200, 250):
                t30 = int(round((2 ** (c / 1200) - 1) * Q30))
                E, eps = coef_mc3(sh30, ch30, th29, d30, cr16, k16, t30)
                rq, thq = poles(E, eps)
                cents = 1200 * np.log2(thq / (th * 2 ** (c / 1200)))
                t60q = -6.907755279 / (np.log(rq) * FS)
                terr = 100 * (t60q / (t60 * S) - 1)
                w = worst.setdefault(kind, [0, 0, None])
                if abs(cents) > w[0]: w[0] = abs(cents); w[2] = (midi, ratio, S, c, cents, terr)
                w[1] = max(w[1], abs(terr))
for kind, (wc, wt, at) in worst.items():
    print("%s: worst pitch %.4f cents (at %s), worst T60 %.3f %%" % (kind, wc, at, wt))
# E's range and the E == 0 'absent' sentinel; the Q30 overflow check: E < 2 always
print("E max over the key range (C8 O2 would be above 18 kHz): 2 sin(theta/2) at 18 kHz = %.3f (< 2 OK)" % (2 * np.sin(np.pi * 18000 / FS)))
