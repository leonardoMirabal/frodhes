#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""M2-01 study (the Move's HQ Rhodes, private plan 06): what the 8 velocity layers cost against an exact hammer.

Classic plays a hammer force pulse blended from the two velocity layers around v (8 layers, tools/gen_rhodes.py
key_pulses_all), with its amplitude and rate interpolated over 10 anchors. The exact pulse at v is the same
coupled hammer-tine contact simulation (tools/hammer.py) run at v itself, put in the same form (64 points over the
same window rule, its own rate and peak). Both are played the engine's way (the shape read at the rate, times the
peak) and compared by what the tine hears: the force spectrum at each of the key's tine modes. Per key and v:
the largest mode-excitation error in dB (blend against exact), and the error energy against the exact excitation
weighted by each mode's share at the strike line (phi^2). Small errors mean a dense table (every velocity
precomputed, no run-time cost) or a live contact gains nothing on single strikes.

    python3 tools/hq_hammer_study.py [--keys 21-108:8] [--json OUT]
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

VS = list(range(1, 128))


def play(shape, pamp, prate_q16):
    """the force per output sample, as the engine's pulse player (linear interpolation, shape[64] = 0)"""
    sh = np.concatenate([np.asarray(shape, float) / 32768.0, [0.0, 0.0]])
    out, pos, step = [], 0.0, prate_q16 / 65536.0
    while pos < gm.PLEN:
        i = int(pos)
        out.append((sh[i] + (sh[i + 1] - sh[i]) * (pos - i)) * pamp / 32768.0)
        pos += step
    return np.array(out)


def excite(F, freqs):
    n = np.arange(len(F))
    return np.array([abs(np.sum(F * np.exp(-2j * np.pi * f / gm.FS * n))) for f in freqs])


def exact_pulses(p, k):
    """{v: (shape, pamp Q15, prate Q16)} for every v, the same rules as key_pulses_all"""
    cases = gr.contact_cases(p, k, gr.V_ANCH + VS)
    res = hm.contact_batch(cases, fs=gr.FS, t_max=0.03)
    anch = dict(zip(gr.V_ANCH, res[:len(gr.V_ANCH)]))
    seg = {v: gr.contact_segments(anch[v]["F"]) for v in gr.V_ANCH}
    ratio = min(8.0, max(sg[-1][1] / sg[0][1] for sg in seg.values()))
    fmax = max(anch[v]["F"].max() for v in gr.V_ANCH)
    dt = anch[gr.V_ANCH[0]]["dt"]
    out = {}
    for v, r in zip(VS, res[len(gr.V_ANCH):]):
        win = ratio * gr.contact_segments(r["F"])[0][1]
        nn = max(2, int(round(win)))
        F = np.concatenate([r["F"], np.zeros(max(0, nn - len(r["F"])))])[:nn]
        prate = int(round(64.0 / (win * dt * gr.FS) * gr.Q12)) << 4
        out[v] = (gr.area_resample(F), max(1, int(round(r["F"].max() / fmax * 32767))), prate)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keys", default="21-108:8")
    ap.add_argument("--json")
    a = ap.parse_args()
    span, _, step = a.keys.partition(":")
    lo, _, hi = span.partition("-")
    keys = list(range(int(lo), int(hi or lo) + 1, int(step or 1)))
    p = gr.load_params()
    gr.PINS = rp.load(p)
    Ls = gr.tine_lengths()
    phys = {n: gr.key_physics(p, n, Ls[n]) for n in keys}
    pulses = gr.key_pulses_all(p, phys)
    rows = []
    for n in keys:
        md = [m for m in phys[n]["fe_modes"] if 0 < m[0] < 20000]
        freqs = [m[0] for m in md]
        wt = np.array([m[2] ** 2 for m in md])             # (the mode's shape at the strike line: its share)
        ex = exact_pulses(p, phys[n])
        errs, werrs = [], []
        for v in VS:
            sh, pa, pr = gm.pulse_params({"pulse": pulses[n]}, v)
            eb = excite(play(sh, pa, pr), freqs)
            ee = excite(play(*ex[v]), freqs)
            d = 20 * np.log10((eb + 1e-12) / (ee + 1e-12))
            errs.append(float(np.max(np.abs(d))))
            werrs.append(float(10 * np.log10(np.sum(wt * (eb - ee) ** 2) / np.sum(wt * ee ** 2) + 1e-20)))
        worst = int(np.argmax(errs))
        rows.append(dict(midi=n, modes=len(freqs), max_db=round(max(errs), 2), at_v=VS[worst],
                         mean_db=round(float(np.mean(errs)), 2), errs=[round(e, 2) for e in errs],
                         werr_db=[round(e, 1) for e in werrs]))
        lay = " ".join(f"{errs[v - 1]:.2f}" for v in gm.LAYER_V)
        print(f"{n:4} modes {len(freqs)}  worst {max(errs):5.2f} dB at v{VS[worst]:3}  mean {np.mean(errs):5.2f} dB  "
              f"weighted error {max(werrs):6.1f} dB (worst v{VS[int(np.argmax(werrs))]})  at the layers: {lay}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
