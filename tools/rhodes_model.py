# SPDX-License-Identifier: GPL-3.0-only
"""The float golden model of the Frodhes voice (IMPLEMENTATION-v3 S18, §5).

It has exactly the C engine's structure (32-frame sub-blocks, the pulse player with linear interpolation, the MC3
kernel per mode with the force injected into y, the radial pickup as an exact flux difference, the damper as a
7-sub-block linear crossfade of E and eps, the post-mix RLC biquad) but in double precision and with the exact
flux function instead of the Hermite table. Gate B compares the C engine against it (residual <= -60 dB).

The model runs a batch of notes in parallel (numpy over notes x modes), so calibrating all 88 keys is quick.

    y = render_batch(specs, bands, fam_tabs, vels, dur, off_at=None, type_=0)   # [n_notes, n] Q15 units
    render(spec, band, fam_tabs, vel, dur, gain)                                  # one note, given gain
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pickup as pu  # noqa: E402
import rh_coefs as rc  # noqa: E402

FS = 44100.0
SUB = 32
RAMP_SUB = 7
PLEN = 64
LAYER_V = [8, 24, 40, 56, 72, 88, 104, 120]


def layer_of(v):
    return int(np.argmin([abs(v - c) for c in LAYER_V]))


def ED(f, t60, t60_rel=None, release=False):
    """MC3 coefficients (E, D = 1 - eps) as floats, from the SAME integer coefficient path as the C engine
    (rh_coefs.coef), so the golden model and the firmware share their pitch and decay to the last bit."""
    w = rc.mode_coefs(f, t60, t60_rel if t60_rel else t60)
    if not w["th"]:
        return 0.0, 0.0
    E, eps = rc.coef(w, w["drel"] if release else w["d"])
    return E / rc.Q30, 1.0 - eps / rc.Q30


V_ANCH = [1, 8, 24, 40, 56, 72, 88, 104, 120, 127]


def vpos(v):
    i = max(a for a in range(len(V_ANCH) - 1) if V_ANCH[a] <= v)
    return i, int(round(256 * (v - V_ANCH[i]) / (V_ANCH[i + 1] - V_ANCH[i])))


def pulse_params(spec, v):
    """(shape int16 list, pamp Q15, prate Q16) exactly as rh_load_pulse() computes them."""
    pz = spec["pulse"]
    i, f = vpos(v)
    f = min(f, 255)
    pa = pz["pamp"][i] + (((pz["pamp"][i + 1] - pz["pamp"][i]) * f) >> 8)
    pr = (pz["prate"][i] + (((pz["prate"][i + 1] - pz["prate"][i]) * f) >> 8)) << 4
    return pz["shapes"][layer_of(v)], pa, pr


def _prep(specs, vels):
    n = len(specs)
    nm = len(specs[0]["modes"])
    E0 = np.zeros((n, nm)); D0 = np.zeros((n, nm)); E1 = np.zeros((n, nm)); D1 = np.zeros((n, nm))
    B = np.zeros((n, nm))
    shapes = np.zeros((n, PLEN + 2)); pamp = np.zeros(n); prate = np.zeros(n)
    for i, (s, v) in enumerate(zip(specs, vels)):
        for m, md in enumerate(s["modes"]):
            E0[i, m], D0[i, m] = ED(md["f"], md["t60"], md["t60_rel"])
            E1[i, m], D1[i, m] = ED(md["f"], md["t60"], md["t60_rel"], release=True)
            B[i, m] = round(md.get("b", 0.0) * rc.Q30) / rc.Q30 if E0[i, m] else 0.0
        sh, pa, pr = pulse_params(s, v)
        shapes[i, :PLEN] = np.array(sh) / 32768.0
        pamp[i] = pa / 32768.0
        prate[i] = pr / 65536.0
    return E0, D0, E1, D1, B, shapes, pamp, prate


def modes_batch(specs, vels, n, off_at=None):
    """Tip displacement (gap units) per note: x[n_notes, n] = sum of the mode states, the exact C structure."""
    E0, D0, E1, D1, B, shapes, pamp, prate = _prep(specs, vels)
    nn, nm = E0.shape
    x = np.zeros((nn, nm)); y = np.zeros((nn, nm))
    E = E0.copy(); D = D0.copy()
    Es, Ds = E.copy(), D.copy()
    ramp = 0
    out = np.zeros((nn, n))
    ppos = np.zeros(nn)
    live = np.ones(nn, bool)
    rows = np.arange(nn)
    off_sub = None if off_at is None else int(off_at * FS) // SUB
    for sb in range(0, n, SUB):
        k = sb // SUB
        if off_sub is not None and k == off_sub:
            ramp = RAMP_SUB; Es, Ds = E.copy(), D.copy()
        if ramp:
            ramp -= 1
            f = (RAMP_SUB - ramp) / RAMP_SUB
            E = Es + (E1 - Es) * f
            D = Ds + (D1 - Ds) * f
        for j in range(sb, min(sb + SUB, n)):
            i = np.floor(ppos).astype(int)
            fr = ppos - i
            act = live & (i < PLEN)
            F = np.zeros(nn)
            ii = np.minimum(i, PLEN)
            F[act] = (shapes[rows, ii] + (shapes[rows, ii + 1] - shapes[rows, ii]) * fr)[act] * pamp[act]
            ppos = ppos + prate
            y = y + F[:, None] * B
            x = x + E * y
            y = D * y - E * x
            out[:, j] = x.sum(axis=1)
    return out


def pickup_batch(tip, offs, fams, fam_A, fam_R):
    """Q28-normalised flux difference per note: exactly y = Phi~(s[n]) - Phi~(s[n-1]) with s = (x + off)^2,
    the first difference taken against the rest position (so the first output is 0 at rest)."""
    out = np.zeros_like(tip)
    for i in range(tip.shape[0]):
        R, A = fam_R[fams[i]], fam_A[fams[i]]
        u = tip[i] + offs[i]
        P = pu.phi_s(u * u, R) / A
        P0 = pu.phi_s(np.array([offs[i] ** 2]), R)[0] / A
        out[i] = np.diff(P, prepend=P0)
    return out


def rlc(x, q, f0=5000.0):
    K = math.tan(math.pi * f0 / FS)
    nrm = 1 / (1 + K / q + K * K)
    b0 = K * K * nrm; b1 = 2 * b0; b2 = b0; a1 = 2 * (K * K - 1) * nrm; a2 = (1 - K / q + K * K) * nrm
    from scipy.signal import lfilter
    return lfilter([b0, b1, b2], [1, a1, a2], x, axis=-1)


def render_batch(specs, fams, vels, dur, off_at=None, gains=None, q=1.0):
    """Q15-unit output per note: ((dPhi~/A * 2^28) * (gain * 2^16) >> 24) >> 5 = dPhi~/A * gain * 2^15, then the RLC.
    fams: [{"A": table normalisation, "R": pole radius / gap}] per pickup family."""
    n = int(dur * FS)
    tip = modes_batch(specs, vels, n, off_at)
    d = pickup_batch(tip, [s["off"] for s in specs], [s["fam"] for s in specs],
                     [f["A"] for f in fams], [f["R"] for f in fams])
    g = np.array([1.0 if gains is None else gains[i] for i in range(len(specs))])
    return rlc(d * g[:, None] * 32768.0, q)


def v1_peak(spec, vel, dur=0.15):
    """Peak |x| of V1 alone for one strike (used to scale b so the tip reaches its target amplitude)."""
    s = dict(spec)
    s["modes"] = [dict(m) for m in spec["modes"]]
    for m in s["modes"][1:]:
        m["b"] = 0.0
    tip = modes_batch([s], [vel], int(dur * FS))
    return float(np.max(np.abs(tip)))


def kill_level(spec, fams, gain):
    """Tip amplitude (gap) below which the voice's output is under half an LSB: |dPhi~/du| at the rest offset times
    the per-sample tip motion 2 pi f0 / fs, times gain * 2^15."""
    f = fams[spec["fam"]]
    g = abs(pu.g(np.array([spec["off"]]), f["R"])[0]) / f["A"]
    per = 2 * math.pi * spec["f0"] / FS
    return 0.5 / max(g * per * gain * 32768.0, 1e-30)
