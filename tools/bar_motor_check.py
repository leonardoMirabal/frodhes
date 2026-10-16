#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate VA-M (PLAN §10, §7, T6d): the vibraphone motor rendered through the bar-tube model.

The V1 <-> tube pair of tools/bartube.py is rendered sample by sample in the bars.c order, from its integer words
(pair_words: G, kc, the discrete retune), with the motor gain of bar_motor_block (mot_gains: the Q15 edge gains and
the per-sample ramp) on k_bt, k_tb and wt (T6e "both"). The bar's upper partials (V2, V3) are added unmodulated at
the reference levels (§3.0). Checks, at A4 (key 69), depth 127, 3.5 Hz unless set:

  full    full-band peak-to-trough 1.5-2.5 s into the note: 7..11 dB                    (gate)
  f0      the f0 band over the same window: >= 10 dB                                     (report)
  v3      the V3 band's AM from 0.05 s over 0.6 T60_V3 (at least one AM period): <= 1 dB                                      (gate)
  attack  the first 50 ms, worst of 8 strike phases, vs motor off: <= 0.5 dB             (gate)
  rate    the f0-band AM rate vs the set rate: +-5 %                                     (gate)
  f3      full-band depth at F3 (key 53) over 1, 3.5, 7, 10 Hz: monotone decreasing      (gate)
  fm      V1 instantaneous-frequency wobble: <= +-10 c                                   (gate, value reported)

    python3 tools/bar_motor_check.py [--key 69] [--gc 0.15] [--depth 127] [--shape 0] [--hz 3.5] [--ratio-db 10]
Writes build/bartube/motor_check.txt; the exit code is the number of failed gates.
"""
import argparse
import math
import os
import sys

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bartube as bt_  # noqa: E402

FS = bt_.FS
# (ratio, T60 s, level dB re the f0 component 30-530 ms) per key: the §3.0 reference rows (A4 has no V3 there:
# the plan's low-half V3 0.3-1.3 s, -22..-48 dB is used at -30 dB, 1.0 s so the V3 band has something to measure)
UPPER = {53: [(4.00, 6.9, -17.0), (9.79, 0.9, -34.0)],
         60: [(4.01, 4.9, -19.0), (10.21, 0.3, -39.0)],
         69: [(4.01, 0.5, -46.0), (10.0, 1.0, -30.0)]}


def env_db(x, lp=40.0):
    """Hilbert envelope, power low-passed at lp Hz (zero phase), in dB."""
    sos = butter(4, lp, fs=FS, output="sos")
    p = sosfiltfilt(sos, np.abs(hilbert(x)) ** 2)
    return 10 * np.log10(np.maximum(p, 1e-30))


def band(x, f, rel):
    sos = butter(3, [f * (1 - rel), f * (1 + rel)], btype="band", fs=FS, output="sos")
    return sosfiltfilt(sos, x)


def depth(e, t0, t1, hz):
    """Mean per-AM-cycle peak-to-trough (dB) over [t0, t1] after removing the decay (quadratic fit)."""
    i0, i1 = int(t0 * FS), int(t1 * FS)
    y = e[i0:i1]
    t = np.arange(len(y)) / FS
    r = y - np.polyval(np.polyfit(t, y, 2), t)
    per = int(FS / hz)
    cyc = [r[k:k + per] for k in range(0, len(r) - per + 1, per)]
    return float(np.mean([c.max() - c.min() for c in cyc])) if cyc else float(r.max() - r.min())


def am_rate(e, t0, t1):
    i0, i1 = int(t0 * FS), int(t1 * FS)
    y = e[i0:i1:32]
    t = np.arange(len(y)) * 32 / FS
    r = y - np.polyval(np.polyfit(t, y, 2), t)
    n = 16 * len(r)
    sp = np.abs(np.fft.rfft(r * np.hanning(len(r)), n))
    f = np.fft.rfftfreq(n, 32 / FS)
    m = (f > 0.4) & (f < 15)
    return float(f[m][np.argmax(sp[m])])


def design(n, ratio_db):
    rows = bt_.design_keys(keys=[n], ratio_db=ratio_db, check=False)
    r = rows[0]
    b, _ = bt_.build_pair(n, r["zeta_r"], r["t1b"])
    w = bt_.pair_words(b, r["wt"], bt_.felt_t60(n), f0=bt_.midi_hz(n))
    return b, w, r


def render(n, w, dur, hz=None, depth_=127, shape=0, gc=bt_.G_C, ph0=0, upper=True):
    N = int(dur * FS)
    g = None
    if hz:
        inc = int(round(hz / FS * 2 ** 32))
        _, g = bt_.mot_gains(N, inc, depth_, shape, int(round(gc * 32768)), True, ph0)
    out, bar, tube = bt_.render_pair(w, N, g)
    if upper and n in UPPER:
        f0 = bt_.midi_hz(n)
        ref = math.sqrt(2) * np.sqrt(np.mean(band(out, f0, 0.15)[int(0.03 * FS):int(0.53 * FS)] ** 2))
        t = np.arange(N) / FS
        for r, t60, db in UPPER[n]:
            out = out + ref * 10 ** (db / 20) * np.sin(2 * np.pi * r * f0 * t) * np.exp(-bt_.LN1000 / t60 * t)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--key", type=int, default=69)
    ap.add_argument("--gc", type=float, default=bt_.G_C)
    ap.add_argument("--depth", type=int, default=127)
    ap.add_argument("--shape", type=int, default=0)
    ap.add_argument("--hz", type=float, default=3.5)
    ap.add_argument("--ratio-db", type=float, default=bt_.RATIO_DB)
    a = ap.parse_args(argv)
    n, f0 = a.key, bt_.midi_hz(a.key)
    b, w, r = design(n, a.ratio_db)
    L = ["# VA-M motor check (tools/bar_motor_check.py), key %d (%.1f Hz), depth %d, shape %d, g_c %.3f, %.2f Hz, "
         "tube +%.0f dB at 0.2 s" % (n, f0, a.depth, a.shape, a.gc, a.hz, a.ratio_db),
         "pair: sigma %.4f, zeta_r %.2f %%, wt %.1f, G %d, kc %.3f; V1 T60 open %.2f s, closed (g_c) %.2f s" % (
             b.sigma, 100 * r["zeta_r"], r["wt"], w["G"], w["kc"], b.t60_v1(), b.with_motor(a.gc).t60_v1())]
    fails = 0

    def gate(name, ok, text):
        nonlocal fails
        fails += 0 if ok else 1
        L.append("%-7s %-4s %s" % (name, "ok" if ok else "FAIL", text))

    dur = 3.0
    on = render(n, w, dur, a.hz, a.depth, a.shape, a.gc)
    off = render(n, w, dur)
    e_full = env_db(on)
    d_full = depth(e_full, 1.5, 2.5, a.hz)
    gate("full", 7.0 <= d_full <= 11.0, "full-band peak-to-trough 1.5-2.5 s: %.2f dB (7..11)" % d_full)
    on_f0 = band(on, f0, 0.15)
    e_f0 = env_db(on_f0)
    d_f0 = depth(e_f0, 1.5, 2.5, a.hz)
    L.append("%-7s %-4s f0 band 1.5-2.5 s: %.2f dB (>= 10, report)" % ("f0", "ok" if d_f0 >= 10 else "low", d_f0))
    v3 = [u for u in UPPER.get(n, []) if 8.5 < u[0] < 12]
    if v3:
        e3 = env_db(band(on, v3[0][0] * f0, 0.05))
        t3 = 0.05 + max(1.0 / a.hz, 0.6 * v3[0][1])        # while V3 is within ~36 dB of its start
        d3 = depth(e3, 0.05, t3, a.hz)
        gate("v3", d3 <= 1.0, "V3 band (%.2fx) 0.05-%.2f s: %.3f dB (<= 1)" % (v3[0][0], t3, d3))
    rate = am_rate(e_f0, 0.5, 2.9)
    gate("rate", abs(rate / a.hz - 1) <= 0.05, "f0-band AM rate %.3f Hz vs %.2f (+-5 %%)" % (rate, a.hz))
    worst = 0.0
    n50 = int(0.05 * FS)
    ref50 = 10 * math.log10(np.mean(off[:n50] ** 2))
    pk_off = 20 * math.log10(np.max(np.abs(off[:n50])))
    worst_pk = w10 = 0.0
    n10 = int(0.01 * FS)
    ref10 = 10 * math.log10(np.mean(off[:n10] ** 2))
    for k in range(8):
        x = render(n, w, 0.06, a.hz, a.depth, a.shape, a.gc, ph0=k << 29)
        worst = max(worst, abs(10 * math.log10(np.mean(x[:n50] ** 2)) - ref50))
        worst_pk = max(worst_pk, abs(20 * math.log10(np.max(np.abs(x[:n50]))) - pk_off))
        w10 = max(w10, abs(10 * math.log10(np.mean(x[:n10] ** 2)) - ref10))
    gate("attack", worst <= 0.5, "first 50 ms vs motor off, worst of 8 phases: RMS %.2f dB, peak %.2f dB (<= 0.5); "
         "first 10 ms RMS %.2f dB" % (worst, worst_pk, w10))
    ia = hilbert(on_f0)
    i0, i1 = int(1.5 * FS), int(2.5 * FS)
    fi = np.diff(np.unwrap(np.angle(ia))) * FS / (2 * np.pi)
    fi = sosfiltfilt(butter(4, 40.0, fs=FS, output="sos"), fi)[i0:i1]
    cents = 1200 * np.log2(fi / np.median(fi))
    wob = float(np.max(np.abs(cents)))
    gate("fm", wob <= 10.0, "V1 instantaneous-frequency wobble 1.5-2.5 s: +-%.2f c (<= 10)" % wob)
    # depth vs rate at F3
    _, w3, _ = design(53, a.ratio_db)
    ds = []
    for hz in (1.0, 3.5, 7.0, 10.0):
        x = render(53, w3, 4.0, hz, a.depth, a.shape, a.gc)
        ds.append(depth(env_db(x), 1.5, 3.5, hz))
    mono = all(ds[i] > ds[i + 1] for i in range(len(ds) - 1))
    gate("f3", mono, "F3 full-band depth 1.5-3.5 s at 1 / 3.5 / 7 / 10 Hz: %s dB (monotone decreasing)"
         % " / ".join("%.2f" % d for d in ds))
    L.append("failed gates: %d" % fails)
    out = os.path.join(bt_.ROOT, "build", "bartube")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "motor_check.txt"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    return fails


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
