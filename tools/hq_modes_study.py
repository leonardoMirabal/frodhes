#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The HQ Rhodes' next item (private plan 06 item 5), its audible size first: the tine's FE modes 4..6, which the
Classic voice (and HQ v1) leave out (it runs mode 1 as V1 / V2, mode 2 as O1, mode 3 as O2).

Per key and velocity, the hammer contact (tools/hammer.py, the tables' own) leaves each FE mode with an amplitude
(its final state q, qd). At the pickup, linearised at the rest offset, a mode's voltage is omega psi_tip a (the flux's
rate), through the pickup's RLC (rhodes_model.rlc: 2nd order, 5 kHz, Q 1). Each mode decays at Classic's overtone
law, sigma = sigma_tine (f / f1)^overtone_damping_exp. Printed: each left-out mode's frequency, and its energy
against modes 1..3's (dB), over the first 10 ms (the attack, where they live) and over the whole note; plus mode
3's (Classic's O2) for scale. A mode above 20 kHz is not audible; one under -60 dB re the note's attack is skipped.

    python3 tools/hq_modes_study.py [--keys 28,40,52,60,72,84,96,108] [--v 40,100,127]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_rhodes as gr  # noqa: E402
import hammer as hm  # noqa: E402
import rhodes_pins as rp  # noqa: E402


def rlc_mag(f, f0=5000.0, q=1.0):
    r = f / f0
    return 1.0 / np.sqrt((1 - r * r) ** 2 + (r / q) ** 2)


def energies(f, sig, A, T):
    """a decaying sinusoid's energy (amplitude A, rate sig) over [0, T] (T None: all of it)"""
    full = A * A / 2.0 / (2.0 * sig)
    return full if T is None else full * (1.0 - np.exp(-2.0 * sig * T))


def study(keys, vels, T=0.010):
    p = gr.load_params()
    gr.PINS = rp.load(p)
    Ls = gr.tine_lengths()
    print("key  v    f4 kHz  f5 kHz  f6 kHz | attack dB: O2   m4     m5     m6   | whole note dB: O2   m4     m5     m6")
    rows = []
    for n in keys:
        k = gr.key_physics(p, n, Ls[n])
        md = k["fe_modes"]
        pt = np.array(gr.PINS[n]["psi_t"][:len(md)])
        res = hm.contact_batch(gr.contact_cases(p, k, vels), fs=gr.FS, t_max=0.03)
        for v, r in zip(vels, res):
            att, whole = [], []
            for m, (fq, sg, _) in enumerate(md):
                w = 2 * np.pi * fq
                a = np.hypot(r["q"][m], r["qd"][m] / w)
                A = w * abs(pt[m]) * a * rlc_mag(fq) * (fq < 20000.0)
                att.append(energies(fq, max(sg, 1e-3), A, T))
                whole.append(energies(fq, max(sg, 1e-3), A, None))
            ra, rw = sum(att[:3]), sum(whole[:3])
            db = lambda x, ref: 10 * np.log10(max(x, 1e-30) / ref)
            rows.append((n, v, [md[m][0] for m in (3, 4, 5)], [db(att[m], ra) for m in (2, 3, 4, 5)],
                         [db(whole[m], rw) for m in (2, 3, 4, 5)]))
            fz = " ".join(f"{md[m][0] / 1e3:7.1f}" for m in (3, 4, 5))
            print(f"{n:3} {v:3}  {fz} | " + " ".join(f"{x:6.1f}" for x in rows[-1][3]) + " | "
                  + " ".join(f"{x:6.1f}" for x in rows[-1][4]))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keys", default="28,40,52,60,72,84,96,108")
    ap.add_argument("--v", default="40,100,127")
    a = ap.parse_args()
    study([int(x) for x in a.keys.split(",")], [int(x) for x in a.v.split(",")])


if __name__ == "__main__":
    main()
