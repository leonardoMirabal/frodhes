#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The WHURLI reference model: the electric-piano engine's structure in double precision, on the reed tables.

Per note: the hammer pulse (the key's group shapes at the two velocity anchors around v, cross-faded with the
engine's integers, played at the interpolated rate with linear interpolation), the MC3 kernel per mode with the
force injected into y and the coefficients from the C integer path (tools/rh_coefs.py via tools/rhodes_model.ED), the
damper as the engine's linear crossfade of (E, D) over rh_settle()'s sub-blocks, the pickup as the difference per
sample of the Hermite table (the header's own knots) at s = (x + off)^2, the key gain x the gain law, then the load
biquad and the body biquads. It renders notes before the firmware hook exists:

    tools/reed_model.py        build/whurli/*.wav (keys x velocities, two chords) and a harmonic report
"""
import math
import os
import sys
import wave

import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rhodes_model as rm  # noqa: E402

FS = 44100.0
SUB = 32
PLEN = 64


def vanch(v):
    """(anchor index, Q8 fraction) as the engine: i = v >> 5, f = (v & 31) << 3 (v = 128: the top anchor)"""
    if v >= 128:
        return 3, 256
    return v >> 5, (v & 31) << 3


def vlaw(tab, v):
    i, f = vanch(v)
    return tab[i] + (((tab[i + 1] - tab[i]) * f) >> 8)


def pulse_params(spec, v):
    """(shape, pamp Q15, prate Q16, pgain Q16) as the engine's strike: shapes cross-faded with Q15 weight f << 7"""
    pz = spec["pulse"]
    i, f = vanch(v)
    a, b = pz["shapes"][i], pz["shapes"][i + 1]
    wq = f << 7
    sh = [x + (((y - x) * wq) >> 15) for x, y in zip(a, b)]
    return sh, vlaw(pz["pamp"], v), vlaw(pz["prate"], v) << 4, vlaw(pz["pgain"], v)


def modes_batch(specs, vels, n, off_at=None):
    """tip displacement (units) per note, the engine's sub-block structure"""
    nn = len(specs)
    nm = max(len(s["modes"]) for s in specs)
    E0 = np.zeros((nn, nm)); D0 = np.zeros((nn, nm)); E1 = np.zeros((nn, nm)); D1 = np.zeros((nn, nm))
    B = np.zeros((nn, nm))
    shapes = np.zeros((nn, PLEN + 2)); pamp = np.zeros(nn); prate = np.zeros(nn)
    for i, (s, v) in enumerate(zip(specs, vels)):
        for m, md in enumerate(s["modes"]):
            E0[i, m], D0[i, m] = rm.ED(md["f"], md["t60"], md["t60_rel"])
            E1[i, m], D1[i, m] = rm.ED(md["f"], md["t60"], md["t60_rel"], release=True)
            B[i, m] = round(md.get("b", 0.0) * (1 << 30)) / (1 << 30) if E0[i, m] else 0.0
        sh, pa, pr, _ = pulse_params(s, v)
        shapes[i, :PLEN] = np.array(sh[:PLEN]) / 32768.0
        pamp[i] = pa / 32768.0
        prate[i] = pr / 65536.0
    x = np.zeros((nn, nm)); y = np.zeros((nn, nm))
    E, D = E0.copy(), D0.copy()
    Es, Ds = E.copy(), D.copy()
    Ls = np.array([float(rm.settle(int(s["midi"]))) for s in specs])[:, None]
    out = np.zeros((nn, n))
    ppos = np.zeros(nn)
    rows = np.arange(nn)
    off_sub = None if off_at is None else int(off_at * FS) // SUB
    for sb in range(0, n, SUB):
        k = sb // SUB
        if off_sub is not None and k == off_sub:
            Es, Ds = E.copy(), D.copy()
        if off_sub is not None and off_sub <= k < off_sub + Ls.max():
            fr = np.minimum((k - off_sub + 1) / Ls, 1.0)
            E = Es + (E1 - Es) * fr
            D = Ds + (D1 - Ds) * fr
        for j in range(sb, min(sb + SUB, n)):
            i = np.floor(ppos).astype(int)
            fr = ppos - i
            act = i < PLEN
            F = np.zeros(nn)
            ii = np.minimum(i, PLEN)
            F[act] = (shapes[rows, ii] + (shapes[rows, ii + 1] - shapes[rows, ii]) * fr)[act] * pamp[act]
            ppos = ppos + prate
            y = y + F[:, None] * B
            x = x + E * y
            y = D * y - E * x
            out[:, j] = x.sum(axis=1)
    return out


def mode1_peak(spec, vel, dur=0.15):
    s = dict(spec)
    s["modes"] = [dict(m) for m in spec["modes"]]
    for m in s["modes"][1:]:
        m["b"] = 0.0
    return float(np.max(np.abs(modes_batch([s], [vel], int(dur * FS)))))


def table(pick, s):
    import gen_reed as gw
    return gw.hermite(pick["P"], pick["M"], s)


def pickup_batch(tip, specs, pick):
    """the capacitance difference per sample, table units, sign as the engine's output"""
    out = np.zeros_like(tip)
    for i, s in enumerate(specs):
        u = tip[i] + s["off"]
        c = table(pick, u * u)
        c0 = table(pick, np.array([s["off"] ** 2]))[0]
        out[i] = np.diff(c, prepend=c0)
    return out


def render_batch(specs, pick, vels, dur, rlc=None, body=None, gains=None, off_at=None, scale=1.0):
    """Q15 output per note: (dC (Q28 table units) x gain (Q16) >> 24) >> 5 = dC / 2^28 x gain x 2^15 x pgain.
    scale multiplies every b (the BARK swing scale)"""
    n = int(dur * FS)
    sp = specs
    if scale != 1.0:
        sp = [dict(s, modes=[dict(m, b=m["b"] * scale) for m in s["modes"]]) for s in specs]
    tip = modes_batch(sp, vels, n, off_at)
    d = pickup_batch(tip, sp, pick)
    g = np.array([(s.get("gain", 1.0) if gains is None else gains[i]) * pulse_params(s, vels[i])[3] / 65536.0 / scale
                  for i, s in enumerate(specs)])
    y = d / (1 << 28) * g[:, None] * 32768.0
    if rlc is not None:
        y = lfilter(rlc[:3], [1.0, rlc[3], rlc[4]], y, axis=-1)
    for c in body or []:
        y = lfilter(c[:3], [1.0, c[3], c[4]], y, axis=-1)
    return y


def kill_level(spec, pick, gain):
    """tip amplitude (units) under which the output is below half an LSB: |dC~/du| at rest x 2 pi f0 / fs (as
    gen_rhodes / rhodes_model.kill_level)"""
    o, e = spec["off"], 1e-3
    g = abs(table(pick, np.array([(o + e) ** 2]))[0] - table(pick, np.array([(o - e) ** 2]))[0]) / (2 * e) / (1 << 28)
    return 0.5 / max(g * 2 * math.pi * spec["f0"] / FS * gain * 32768.0, 1e-30)


def harmonics(y, f0, t0, t1, nh=8):
    """harmonic levels (dB re the fundamental) over [t0, t1] s: Hann-windowed DFT at k f0"""
    a, b = int(t0 * FS), int(t1 * FS)
    seg = y[a:b] * np.hanning(b - a)
    t = np.arange(b - a) / FS
    amps = [abs(np.sum(seg * np.exp(-2j * math.pi * k * f0 * t))) for k in range(1, nh + 1)]
    return [20 * math.log10(max(x, 1e-12) / max(amps[0], 1e-12)) for x in amps]


def write_wav(path, y, norm=None):
    """16-bit mono; norm: the peak the file is scaled to (a common one keeps velocities comparable)"""
    pk = norm or max(1e-9, float(np.max(np.abs(y))))
    data = np.clip(np.round(y * (0.89 * 32767 / pk)), -32767, 32767).astype("<i2")
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(FS))
        f.writeframes(data.tobytes())


PLAY = [33, 45, 57, 60, 69, 81, 93]
VELS = [24, 64, 100, 127]


def render_set(spec):
    """{(key, vel): output} through the load and the body, a note held 2.5 s, 1.5 s of release"""
    keys = {s["midi"]: s for s in spec["keys"]}
    out = {}
    for v in VELS:
        ks = [keys[k] for k in PLAY]
        y = render_batch(ks, spec["pick"], [v] * len(ks), 4.0, rlc=spec["rlc"], body=spec["body"], off_at=2.5)
        for k, yy in zip(PLAY, y):
            out[(k, v)] = yy
    return out


def main():
    import gen_reed as gw
    spec = gw.build()
    keys = {s["midi"]: s for s in spec["keys"]}
    out = os.path.join(ROOT, "build", "whurli")
    os.makedirs(out, exist_ok=True)
    ys = render_set(spec)
    norm = max(float(np.max(np.abs(y))) for y in ys.values())
    print("harmonics re H1 (dB, 30..130 ms, after the body): H2 H3 H4 H5 H6 | peak dBFS")
    for (k, v), yy in sorted(ys.items()):
        h = harmonics(yy, keys[k]["f0"], 0.03, 0.13)
        print("  key %d v %3d: %s | %6.1f" % (k, v, " ".join("%6.1f" % x for x in h[1:6]),
                                              20 * math.log10(max(1e-9, np.max(np.abs(yy))) / 32767)))
        write_wav(os.path.join(out, "key%d_v%d.wav" % (k, v)), yy, norm)
    seq = np.zeros(int(7.0 * FS))
    for t0, notes, v in ((0.0, (57, 60, 64), 40), (1.6, (57, 60, 64), 120), (3.2, (45, 52, 57, 60, 64), 90),
                         (5.0, (69, 72, 76, 81), 70)):
        ks = [keys[k] for k in notes]
        y = render_batch(ks, spec["pick"], [v] * len(ks), 2.0, rlc=spec["rlc"], body=spec["body"], off_at=1.3)
        a = int(t0 * FS)
        seq[a:a + y.shape[1]] += y.sum(axis=0)[:len(seq) - a]
    write_wav(os.path.join(out, "chords.wav"), seq)
    print("wrote", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
