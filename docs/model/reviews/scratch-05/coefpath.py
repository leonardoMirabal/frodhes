#!/usr/bin/env python3
"""v3 review scratch: the run-time coefficient path (S17) for the DF2 resonator, in integers, with no division.

Host tables per key and mode:  cos(theta) Q30, sin(theta) Q30, theta Q29 (rad), d = 1 - r^2 Q30 (rounded up).
Run time (main loop or ISR event time), for a pitch offset c cents (TUNE +-50, bend +-200) and a T60 scale S:
  k    = 1/S                                       (Q16 table over the DECAY knob, 128 entries)
  d'   = k d - k(k-1)/2 d^2 (+ k(k-1)(k-2)/6 d^3)  (1 - r^2 scaled: (1-d)^k expanded)
  r'   = 1 - d'/2 - d'^2/8 - d'^3/16              (sqrt(1 - d'))
  t    = 2^(c/1200) - 1                            (Q30: table for TUNE, exp2 polynomial for bend)
  dl   = theta * t                                 (the angle change)
  cos' = cos*cos(dl) - sin*sin(dl), with cos(dl) = 1 - dl^2/2 + dl^4/24, sin(dl) = dl - dl^3/6 + dl^5/120
  a1   = 2 r' cos',  a2 = -(1 - d') = -r'^2  (both Q30)
Error against double over keys 21..108, modes V1/O1/O2 (ratio 1, 7, 20 below 18 kHz), T60 per mode, at every
corner of c in {-250..+250} and S in {0.25, 0.5, 1, 2, 4}. Pass: <= 0.2 cent and <= 1 % T60.
All products are int64 of two int32 (one widening multiply each), shifts are floor.
"""
import numpy as np

FS = 44100.0
Q30 = 1 << 30
Q29 = 1 << 29
Q16 = 1 << 16


def mul(a, b, sh):                   # (int64)a*b >> sh, floor
    return (np.int64(a) * np.int64(b)) >> np.int64(sh)


def coef_int(cos30, sin30, th29, d30, k16, t30):
    # d' = k d - k(k-1)/2 d^2 + k(k-1)(k-2)/6 d^3   (all in Q30, k in Q16)
    kd = mul(k16, d30, 16)                                   # Q30
    km1 = k16 - Q16                                          # (k-1) Q16
    d2 = mul(d30, d30, 30)                                   # Q30
    term2 = mul(mul(k16, km1, 16), d2, 16) >> 1              # k(k-1)/2 d^2 Q30
    km2 = k16 - 2 * Q16
    d3 = mul(d2, d30, 30)
    term3 = mul(mul(mul(k16, km1, 16), km2, 16), d3, 16) // 6
    dp = kd - term2 + term3                                  # d' Q30, >= 0
    dp = np.maximum(dp, 1)                                   # never less damped than 1 LSB
    # r' = 1 - d'/2 - d'^2/8 - d'^3/16
    dp2 = mul(dp, dp, 30); dp3 = mul(dp2, dp, 30)
    rp = Q30 - (dp >> 1) - (dp2 >> 3) - (dp3 >> 4)
    # dl = theta * t   (theta Q29 * t Q30 >> 29 -> Q30)
    dl = mul(th29, t30, 29)
    dl2 = mul(dl, dl, 30); dl3 = mul(dl2, dl, 30); dl4 = mul(dl2, dl2, 30); dl5 = mul(dl4, dl, 30)
    cdl = Q30 - (dl2 >> 1) + dl4 // 24
    sdl = dl - dl3 // 6 + dl5 // 120
    cosp = mul(cos30, cdl, 30) - mul(sin30, sdl, 30)
    a1 = mul(2 * rp, cosp, 30)                               # 2 r' cos'   Q30 (|a1| < 2 fits int32)
    a2 = -(Q30 - dp)
    return a1, a2


worst_c, worst_t = 0.0, 0.0
keys = np.arange(21, 109)
for midi in keys:
    f0 = 440 * 2 ** ((midi - 69) / 12)
    for ratio, t60 in ((1.0, 18.0 if midi < 62 else 18.0 * 2 ** (-(midi - 62) / 10)), (7.0, 0.5), (20.0, 0.05)):
        f = f0 * ratio
        if f > 18000:
            continue
        th = 2 * np.pi * f / FS
        r = np.exp(-6.907755279 / (t60 * FS))
        cos30 = int(round(np.cos(th) * Q30)); sin30 = int(round(np.sin(th) * Q30)); th29 = int(round(th * Q29))
        d30 = int(np.ceil((1 - r * r) * Q30))
        for S in (0.25, 0.5, 1.0, 2.0, 4.0):
            k16 = int(round(Q16 / S))
            for c in (-250, -200, -50, -1, 0, 1, 50, 200, 250):
                t30 = int(round((2 ** (c / 1200) - 1) * Q30))
                a1, a2 = coef_int(cos30, sin30, th29, d30, k16, t30)
                rq = np.sqrt(-a2 / Q30); thq = np.arccos(a1 / Q30 / (2 * rq))
                f_t = f * 2 ** (c / 1200); t60_t = t60 * S
                cents = 1200 * np.log2(thq / (2 * np.pi * f_t / FS))
                t60q = -6.907755279 / (np.log(rq) * FS)
                worst_c = max(worst_c, abs(cents)); worst_t = max(worst_t, abs(t60q / t60_t - 1) * 100)
                if abs(cents) > 0.2 or abs(t60q / t60_t - 1) > 0.01:
                    print("FAIL midi %d ratio %.0f f %.1f T60 %.3f S %.2f c %d: %.3f cents, T60 err %.2f %%" % (midi, ratio, f, t60, S, c, cents, 100 * (t60q / t60_t - 1)))
print("worst pitch error %.4f cents, worst T60 error %.3f %% over keys 21..108, 3 modes, S in 0.25..4, c in -250..+250" % (worst_c, worst_t))

# the stability margin: complex poles need a1^2 < -4 a2
f = 41.2; th = 2 * np.pi * f / FS; r = np.exp(-6.907755279 / (20 * FS))
a1 = 2 * r * np.cos(th) * Q30; a2 = -r * r * Q30
print("E1: 4|a2| - a1^2 = %.3e (Q60 units), i.e. %.0f LSB of a1 margin" % (4 * abs(a2) * Q30 - a1 * a1, (4 * abs(a2) * Q30 - a1 * a1) / (2 * a1)))
# how many LSB of a1 per cent, per key (the resolution of amplitude->pitch)
for midi in (28, 40, 52, 64):
    f = 440 * 2 ** ((midi - 69) / 12); th = 2 * np.pi * f / FS
    print("midi %d: da1/dcent = %.0f LSB" % (midi, 2 * np.sin(th) * th * np.log(2) / 1200 * Q30))
