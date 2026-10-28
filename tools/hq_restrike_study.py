#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The HQ Rhodes' live contact (private plan 06 item 4), its audible size first: how much a re-strike's hammer
force differs from the at-rest pulse (Classic's and the dense table's, superposed on the ringing tine) when the
tine still moves.

A key is struck at v1 (the same coupled hammer-tine contact as the tables, tools/hammer.py); the tine's modes ring
freely for tau; then it is struck again at v2, the hammer starting at the tine's contact point with the tine in its
state (contact_batch q0 / qd0 / xh0). Over one period of the fundamental around tau, the impulse and the peak of the
re-strike's force against the at-rest contact at v2 (dB), and the force difference's energy against the at-rest
force (dB): what a live contact would change.

    python3 tools/hq_restrike_study.py [--keys 40,60,85,96,108] [--tau 0.05,0.2,0.5] [--v 127:127,127:64] [--classic]
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_rhodes as gr  # noqa: E402
import hammer as hm  # noqa: E402
import rhodes_model as gm  # noqa: E402
import rhodes_pins as rp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classic_scale(p, n, k, first, v1):
    """Classic's tip after the first strike (V1's peak, gaps, the golden model) in metres, over the contact
    simulation's own: the factor that puts the simulated tine at Classic's calibrated amplitude"""
    spec = {q["midi"]: q for q in json.load(open(os.path.join(ROOT, "docs/model/data/rhodes_spec.json")))["keys"]}[n]
    a_cl = gm.v1_peak(spec, v1) * 0.0254 / 32 * p["pickup"]["gap_rel"][k["fam"]]
    w0 = 2 * np.pi * k["fe_modes"][0][0]
    a_ph = abs(gr.PINS[n]["psi_t"][0]) * np.hypot(first["q"][0], first["qd"][0] / w0)
    return a_cl / a_ph


def free(q, qd, modes, t):
    """the tine's modes after t seconds of free, damped ringing (exact)"""
    q, qd = np.array(q, float), np.array(qd, float)
    out_q, out_qd = np.empty_like(q), np.empty_like(qd)
    for m, (f, s, _) in enumerate(modes):
        w0 = 2 * np.pi * f
        wd = np.sqrt(max(w0 * w0 - s * s, 1e-12))
        A = q[m]
        B = (qd[m] + s * q[m]) / wd
        e = np.exp(-s * t)
        c, sn = np.cos(wd * t), np.sin(wd * t)
        out_q[m] = e * (A * c + B * sn)
        out_qd[m] = e * (-s * (A * c + B * sn) + (-A * wd * sn + B * wd * c))
    return out_q, out_qd


def study(keys, taus, vpairs, nph=12, classic=False):
    p = gr.load_params()
    gr.PINS = rp.load(p)
    Ls = gr.tine_lengths()
    print("key  v1  v2   tau   impulse dB (min..max)   peak dB (min..max)   diff energy dB (max)   x_c/v_h")
    for n in keys:
        k = gr.key_physics(p, n, Ls[n])
        modes = k["fe_modes"]
        ph = np.array([m[2] for m in modes])
        for v1, v2 in vpairs:
            first, rest = hm.contact_batch(gr.contact_cases(p, k, [v1, v2]), fs=gr.FS, t_max=0.03)
            t_end = len(first["F"]) * first["dt"]
            sc = classic_scale(p, n, k, first, v1) if classic else 1.0
            T = 1.0 / modes[0][0]
            for tau in taus:
                cases = []
                vh = gr.hammer_speed(p, v2)
                rat = []
                for i in range(nph):
                    q, qd = free(first["q"] * sc, first["qd"] * sc, modes, tau + i * T / nph - t_end)
                    c = gr.contact_cases(p, k, [v2])[0]
                    c.update(q0=q, qd0=qd, xh0=float(ph @ q))
                    cases.append(c)
                    rat.append(abs(float(ph @ qd)) / vh)
                res = hm.contact_batch(cases, fs=gr.FS, t_max=0.03)
                I0 = rest["impulse"]
                P0 = rest["F"].max()
                Ii = [10 * np.log10(max(r["impulse"], 1e-30) / I0) * 2 for r in res]     # (20 log10)
                Pi = [20 * np.log10(max(r["F"].max(), 1e-30) / P0) for r in res]
                D = []
                for r in res:
                    L = max(len(r["F"]), len(rest["F"]))
                    a = np.pad(r["F"], (0, L - len(r["F"]))); b = np.pad(rest["F"], (0, L - len(rest["F"])))
                    D.append(10 * np.log10(max(((a - b) ** 2).sum(), 1e-30) / (b ** 2).sum()))
                print(f"{n:3} {v1:3} {v2:3} {tau:5.2f}   {min(Ii):6.1f} .. {max(Ii):5.1f}        {min(Pi):6.1f} .. "
                      f"{max(Pi):5.1f}        {max(D):6.1f}              {max(rat):.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keys", default="40,60,85,96,108")
    ap.add_argument("--tau", default="0.05,0.2,0.5")
    ap.add_argument("--v", default="127:127,127:64,64:127,64:64")
    ap.add_argument("--classic", action="store_true", help="the tine at Classic's calibrated tip amplitude (the HQ "
                    "voice's choice), not the contact simulation's own")
    a = ap.parse_args()
    study([int(x) for x in a.keys.split(",")], [float(x) for x in a.tau.split(",")],
          [tuple(int(y) for y in x.split(":")) for x in a.v.split(",")], classic=a.classic)


if __name__ == "__main__":
    main()
