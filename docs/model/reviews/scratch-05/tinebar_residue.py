#!/usr/bin/env python3
"""v3 review scratch: the 2-DOF tine-bar solve (S3), its residues, and whether a pure 2-pole per normal mode
(force injected into the velocity-like state, no numerator zero) reproduces the tine-coordinate response.

M = diag(1, mu), K = [[wt^2 + k, -k], [-k, mu wb^2 + k]], C = diag(2 st, 2 mu sb); force on the tine only;
output = the tine coordinate. H(s) = e1' (M s^2 + C s + K)^-1 e1 = sum_m [R_m/(s - l_m) + conj].
Each pair realised as y_m'' + 2 s_m y_m' + w_m^2 y_m = b_m F gives the term b_m e^{-s t} sin(w t)/w, whose
residue is purely imaginary. The error of dropping the residue phase is reported per mode, together with the
retune that puts the long mode exactly at f0 and the release re-solve (damper felt on the tine coordinate).
Parameters are ESTIMATES (the fit sets them): bar detune, coupling, mass ratio, dampings per register.
"""
import numpy as np


def solve(f0, det_c, k_rel, mu, st, sb, sd=0.0):
    wt = 2 * np.pi * f0
    wb = wt * 2 ** (det_c / 1200)
    k = k_rel * wt ** 2
    M = np.diag([1.0, mu]); K = np.array([[wt ** 2 + k, -k], [-k, mu * wb ** 2 + k]]); C = np.diag([2 * (st + sd), 2 * mu * sb])
    Mi = np.linalg.inv(M)
    A = np.block([[np.zeros((2, 2)), np.eye(2)], [-Mi @ K, -Mi @ C]])
    lam, V = np.linalg.eig(A)
    Vi = np.linalg.inv(V)
    B = np.array([0, 0, 1, 0]); Cc = np.array([1, 0, 0, 0])
    res = (Cc @ V) * (Vi @ B)          # residues of H(s) = sum res_i / (s - lam_i)
    out = []
    for i in range(4):
        if lam[i].imag > 0:
            w = lam[i].imag; sig = -lam[i].real
            # pure-2-pole equivalent: b = 2 w |R| if the residue were -j |R| ... compare phase to -90 deg
            ph = np.degrees(np.angle(res[i])) + 90.0
            late = abs(res[i]) * np.exp(-sig * 2.0)
            out.append((w / (2 * np.pi), sig, 2 * abs(res[i]) * w, ph, late))
    out.sort(key=lambda o: -o[4])
    return out


print("mode: f (Hz), sigma (1/s), T60 (s), b (eq. 2-pole weight), residue phase error vs pure 2-pole (deg), late weight")
for lab, f0, det, k_rel, mu, st, sb, sd in (
        ("F1 held", 43.65, 8, 0.004, 30.0, 0.5, 0.12, 0.0),
        ("F1 damped", 43.65, 8, 0.004, 30.0, 0.5, 0.12, 60.0),
        ("A4 held", 440.0, 5, 0.004, 30.0, 0.9, 0.3, 0.0),
        ("A4 damped", 440.0, 5, 0.004, 30.0, 0.9, 0.3, 200.0),
        ("C7 held", 2093.0, 3, 0.004, 30.0, 2.5, 1.0, 0.0),
        ("F1 weak k", 43.65, 8, 0.0005, 30.0, 0.5, 0.12, 0.0),
        ("F1 strong k", 43.65, 8, 0.02, 30.0, 0.5, 0.12, 0.0)):
    o = solve(f0, det, k_rel, mu, st, sb, sd)
    s = "; ".join("f=%.3f s=%.3f T60=%.2f b=%.3e ph=%+.2f late=%.2e" % (f, sg, 6.9078 / sg, b, ph, late) for f, sg, b, ph, late in o)
    retune = f0 / o[0][0]
    print("%-11s V1 is mode 0 -> scale both f by %.5f (%.2f c): %s" % (lab, retune, 1200 * np.log2(retune), s))

# ripple depth from two modes at 2 s: 20 log10 ((a+b)/(a-b)) with a,b the late weights
o = solve(43.65, 8, 0.004, 30.0, 0.5, 0.12)
a, b = o[0][4], o[1][4]
print("F1 beat depth at 2 s: %.2f dB at %.2f Hz" % (20 * np.log10((a + b) / (a - b)), abs(o[0][0] - o[1][0])))
