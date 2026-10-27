#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""M2-01 study (the Move's HQ Rhodes, private plan 06): how much aliasing the pickup nonlinearity makes at 44.1 kHz.

The golden model's voice (tools/rhodes_model.py: the tine modes, the hammer pulse, the radial pickup as a flux
difference) is rendered per key. Its tip displacement x is band-limited (the modes and the pulse are below about
20 kHz), and the pickup is a static nonlinearity Phi(s), s = (bark x + off)^2, then a first difference. So:
  at 1x:   y1 = diff(Phi(s[n]))                                   what Classic does (with the exact Phi)
  at Kx:   x upsampled K times (a long polyphase FIR), Phi at K fs, low-passed and decimated, then diff
  the reference: the same at 16x with ideal (FFT) resampling
The aliasing of a path is its difference from the reference, in dB against the reference's energy, over the whole
note and over the attack (first 50 ms), both below 18 kHz, and the share of it above 5 kHz, where the ear finds inharmonic partials.
Run per TYPE (its pickup offset and bark), at v127 and v100.

    python3 tools/hq_alias_study.py [--keys 21-108:3] [--dur 0.4] [--json OUT]
"""
import argparse
import json
import os
import sys

import numpy as np
from scipy.signal import resample, resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pickup as pu  # noqa: E402
import rhodes_model as gm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = json.load(open(os.path.join(ROOT, "docs/model/data/rhodes_spec.json")))
P = json.load(open(os.path.join(ROOT, "docs/model/data/params.json")))
TYPES = {k: v for k, v in P["types"].items() if not k.startswith("_")}


def flux(u, fam):
    f = S["pickup"][fam]
    return pu.phi_s(u * u, f["R"]) / f["A"]


def path(dx, off, fam, k):
    """the pickup output of the tip motion dx (gap units, at fs, from rest) at the rest offset off, with the
    nonlinearity at k x fs. The offset and the rest flux are taken out around the filters (a DC step at the
    render's edges would ring through them)"""
    p0 = flux(np.array([off]), fam)[0]
    if k == 1:
        p = flux(dx + off, fam) - p0
    else:
        w = ("kaiser", 12.0)
        p = resample_poly(flux(resample_poly(dx, k, 1, window=w) + off, fam) - p0, 1, k, window=w)
    return np.diff(p, prepend=0.0)


def path_ideal(dx, off, fam, k):
    """the reference: x band-limited exactly (FFT resampling, a brick wall at fs/2) to k fs, the flux, back the same
    way. (resample_poly's own filters droop from about 13 kHz: a reference made with them flatters every path that
    uses them, and marks a flatter half-band wrong.) dx must end at rest: the FFT wraps"""
    p0 = flux(np.array([off]), fam)[0]
    p = resample(flux(resample(dx, k * len(dx)) + off, fam) - p0, len(dx))
    return np.diff(p, prepend=0.0)


def hb_taps(P, beta):
    """a half-band low-pass of 4P - 1 taps (Kaiser-windowed sinc), its P non-zero side taps g_k = h[c + 2k + 1]"""
    k = np.arange(P)
    t = (2 * k + 1) / 2.0
    L = 4 * P - 1
    w = np.kaiser(L, beta)[2 * P - 1 + 2 * k + 1]
    return 0.5 * np.sin(np.pi * t) / (np.pi * t) * w


def hb_up(x, g):
    """x at 2x by the polyphase half-band (the HQ voice's form): pairs (FIR at n - P + 1/2, x[n - P + 1])"""
    P = len(g)
    xp = np.concatenate([np.zeros(2 * P), x])
    n = np.arange(len(x)) + 2 * P
    a = sum(2 * g[k] * (xp[n - P + 1 + k] + xp[n - P - k]) for k in range(P))
    out = np.empty(2 * len(x))
    out[0::2], out[1::2] = a, xp[n - P + 1]
    return out


def hb_down(y, g):
    """2x -> 1x by the same half-band: the even phase through the FIR, the odd one delayed, summed and halved"""
    P = len(g)
    e, o = y[0::2], y[1::2]
    ep = np.concatenate([np.zeros(2 * P), e])
    n = np.arange(len(e)) + 2 * P
    a = sum(2 * g[k] * (ep[n - P + 1 + k] + ep[n - P - k]) for k in range(P))
    op = np.concatenate([np.zeros(2 * P), o])
    return 0.5 * (a + op[n - P])


def path_hb(dx, off, fam, P, beta):
    """the HQ voice's 2x pickup (half-band up, the flux, half-band down, then diff), its delay taken out (2P - 1)"""
    g = hb_taps(P, beta)
    p0 = flux(np.array([off]), fam)[0]
    p = hb_down(flux(hb_up(dx, g) + off, fam) - p0, g)
    d = 2 * P - 1
    p = np.concatenate([p[d:], np.zeros(d)])
    return np.diff(p, prepend=0.0)


BAND = 18000.0                              # the error counted below this (the resampling filters' transition band
                                            # above it is not aliasing: it is the same at every factor)


def band_energy(x, lo=0.0, hi=BAND):
    sp = np.abs(np.fft.rfft(x)) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / gm.FS)
    return sp[(fr >= lo) & (fr < hi)].sum()


def db(a, b):
    return 10 * np.log10((band_energy(a) + 1e-30) / (band_energy(b) + 1e-30))


def hf_share(e):
    return 10 * np.log10((band_energy(e, 5000.0) + 1e-30) / (band_energy(e) + 1e-30))


HB = []                                     # (P, beta) of the half-band 2x paths to measure (--hb)


def study(keys, dur, vels, factors):
    rows = []
    n = int(dur * gm.FS)
    att = int(0.05 * gm.FS)
    cut = int(0.02 * gm.FS)                     # (the render's last 20 ms: the filters' edge, and the fade)
    fade = np.ones(n)
    fade[-cut:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(cut) / cut)
    for k in keys:
        tip = gm.modes_batch([k] * len(vels), vels, n)
        for tname, t in TYPES.items():
            for vi, v in enumerate(vels):
                dx, off = t["bark"] * tip[vi], k["off"] + t["off"]
                dx = dx * fade                  # (to rest at the end: the reference's FFT wraps)
                ref = path_ideal(dx, off, k["fam"], 16)[:-cut]
                r = {"midi": k["midi"], "type": tname, "vel": v}
                for f in factors:
                    e = path(dx, off, k["fam"], f)[:-cut] - ref
                    r[f"x{f}"] = round(db(e, ref), 1)
                    r[f"x{f}_att"] = round(db(e[:att], ref[:att]), 1)
                    if f == 1:
                        r["x1_hf"] = round(hf_share(e), 1)
                for P, beta in HB:
                    e = path_hb(dx, off, k["fam"], P, beta)[:-cut] - ref
                    r[f"hb{P}_{beta:g}"] = round(db(e, ref), 1)
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keys", default="21-108:3")
    ap.add_argument("--dur", type=float, default=0.4)
    ap.add_argument("--json")
    ap.add_argument("--hb", default="", help="half-band 2x paths to measure: P:beta,P:beta (P pairs, 4P - 1 taps)")
    a = ap.parse_args()
    HB.extend((int(q.split(":")[0]), float(q.split(":")[1])) for q in a.hb.split(",") if q)
    span, _, step = a.keys.partition(":")
    lo, _, hi = span.partition("-")
    want = set(range(int(lo), int(hi or lo) + 1, int(step or 1)))
    keys = [k for k in S["keys"] if k["midi"] in want]
    rows = study(keys, a.dur, [127, 100], [1, 2, 4])
    print("midi type     vel   x1 dB (att)  hf>5k   x2 dB (att)   x4 dB (att)")
    for r in rows:
        print(f"{r['midi']:4} {r['type']:8} {r['vel']:3}  {r['x1']:6.1f} ({r['x1_att']:6.1f}) {r['x1_hf']:6.1f}  "
              f"{r['x2']:6.1f} ({r['x2_att']:6.1f})  {r['x4']:6.1f} ({r['x4_att']:6.1f})")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1)
    worst = {f: max(r[f"x{f}"] for r in rows) for f in (1, 2, 4)}
    for P, beta in HB:
        worst[f"hb{P}_{beta:g}"] = max(r[f"hb{P}_{beta:g}"] for r in rows)
    print("worst over keys / types / velocities:", worst)


if __name__ == "__main__":
    main()
