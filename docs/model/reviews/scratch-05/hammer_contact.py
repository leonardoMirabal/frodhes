#!/usr/bin/env python3
"""v3 review scratch: hammer contact sanity (S8). A hammer mass with a power-law felt F = K e^p + R d(e^p)/dt
hits a tine at rest represented by its first 4 modes (effective masses and frequencies from V01 meff.py-style
numbers, ESTIMATE), integrated with semi-implicit Euler at 16 x 44.1 kHz. Reports contact time T_c, peak force
and impulse against hammer speed v_h over the 30 dB velocity law, per tip band, so that RH_PLEN, prate and the
RH_VRATE/RH_VAMP ranges can be sized. It also checks the Hertz-law exponent T_c ~ v^-(p-1)/(p+1).
All physical constants are ESTIMATES (R03 K = 5e5 N m^-2.5 for felt; bands scale K by hardness).
"""
import numpy as np

FS = 44100.0; OS = 16; dt = 1 / (FS * OS)
P = 2.5


def sim(vh, mh, K, R, modes):
    """modes: list of (f_hz, m_eff_kg, sigma). returns T_c (s), F_peak, impulse"""
    w = np.array([2 * np.pi * f for f, m, s in modes]); m = np.array([mm for f, mm, s in modes]); sg = np.array([s for f, mm, s in modes])
    q = np.zeros(len(modes)); qd = np.zeros(len(modes))
    xh = 0.0; vh_ = vh          # hammer position (positive into the tine), velocity
    e_prev = 0.0; t = 0.0; tc0 = None; Fpk = 0.0; imp = 0.0; n = 0
    while n < int(0.02 / dt):
        tine = np.sum(q)          # tine displacement at the strike point (modal sum, unit shapes)
        e = xh - tine
        if e > 0:
            ep = e ** P; F = K * ep + R * (ep - e_prev) / dt
            F = max(F, 0.0)
            if tc0 is None: tc0 = t
            e_prev = ep
        else:
            F = 0.0; e_prev = 0.0
            if tc0 is not None and vh_ < 0:
                return t - tc0, Fpk, imp
        Fpk = max(Fpk, F); imp += F * dt
        # hammer
        vh_ -= F / mh * dt; xh += vh_ * dt
        # modes (semi-implicit Euler)
        qd += (F / m - 2 * sg * qd - w ** 2 * q) * dt; q += qd * dt
        t += dt; n += 1
    return (t - tc0 if tc0 else 0), Fpk, imp


# bands: K scaled with hardness (ESTIMATE): 30 duro soft .. wood very hard
bands = {"30duro(E1)": (dict(mh=0.011, K=2e5, R=40), [(41.2, 0.040, 0.3), (290, 0.004, 5), (800, 0.002, 30), (1700, 0.001, 100)]),
         "70duro(A3)": (dict(mh=0.011, K=1.5e6, R=100), [(220, 0.025, 0.5), (1500, 0.003, 10), (4200, 0.001, 50), (8000, 0.0006, 150)]),
         "90duro(C5)": (dict(mh=0.011, K=5e6, R=150), [(523, 0.020, 0.9), (3400, 0.002, 20), (9000, 0.0008, 60), (16000, 0.0005, 200)]),
         "wood(C7)": (dict(mh=0.011, K=3e7, R=300), [(2093, 0.015, 2.5), (13500, 0.0015, 50)])}
vmax = 4.0
for name, (hp, modes) in bands.items():
    rows = []
    for v in (1, 32, 64, 96, 127):
        vh = vmax * 2 ** (-30 / 6.0206 * (1 - (v - 1) / 126))
        tc, fpk, imp = sim(vh, hp["mh"], hp["K"], hp["R"], modes)
        rows.append((v, vh, tc, fpk, imp))
    tcs = [r[2] for r in rows]; vhs = [r[1] for r in rows]
    slope = np.polyfit(np.log(vhs), np.log(tcs), 1)[0]
    print("%s: " % name + " | ".join("v=%3d vh=%.2f m/s Tc=%.2f ms Fpk=%.0f N J=%.4f Ns" % (v, vh, tc * 1e3, fpk, imp) for v, vh, tc, fpk, imp in rows))
    print("    T_c ratio v1/v127 = %.2fx, log-log slope %.2f (Hertz -(p-1)/(p+1) = %.2f); samples at fs: %.0f..%.0f" %
          (tcs[0] / tcs[-1], slope, -(P - 1) / (P + 1), tcs[-1] * FS, tcs[0] * FS))
