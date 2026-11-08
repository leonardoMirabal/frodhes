#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes velocity map (rhodes.c rh_vm): per key and velocity the tip's swing scale X and the voice's gain scale G,
measured on the host (the C engine, effects bypassed by CC 119, NOISE 0) and written to firmware/gen/rh_velmap.h.

    tools/gen_velmap.py --write     fit X and G to docs/model/data/velmap.json, write the header
    tools/gen_velmap.py             the residual of the header as built: bark and H1 against the targets per key

The swing against the pickup's gap sets the colour (the bark: H2..H5 re H1 grows ~1 dB per dB of swing while the
pickup is near linear, slower once it folds); the gain sets the level. One velocity law into the pickup gives a bark
that grows a straight ~0.22 dB per velocity step; the map makes it flat to a knee, then steep, and lets the
fundamental saturate. The fit: (1) the engine without the map (X = G = 1): each key's bark and H1 at v 8, 16 .. 120,
127; (2) the bark against the swing at v100 (X from -30 to +30 dB): the first X per velocity by inversion, the swing
the velocity already gives taken from (1)'s H1; (3) iterations of the whole grid with the map (X += bark error / the
local slope); (4) G from the last render: the H1 target over H1 measured. The probe build (-DRH_VM_PROBE) reads the
tables from a file (RH_VM_TAB), so no rebuild per step. Physics only: nothing here reads recordings."""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "build", "velmap", "frodhes-probe")
HDR = os.path.join(ROOT, "firmware", "gen", "rh_velmap.h")
CFG = os.path.join(ROOT, "docs", "model", "data", "velmap.json")
FS = 44100
KEY_LO, NKEY = 21, 88
NP = 17                                  # table points: v = 0, 8 .. 128
VG = list(range(8, 128, 8)) + [127]      # the measured velocities (points 1..15, and v127 for point 16)
Q = 11
ONE = 1 << Q
JOBS = int(os.environ.get("VELMAP_JOBS", "3"))


def build_probe():
    os.makedirs(os.path.dirname(PROBE), exist_ok=True)
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-DRH_VM_PROBE",
                    "-Ifirmware/src", "-Ifirmware/gen", "-o", PROBE, "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)


def pts(points, k):
    return float(np.interp(k, [p[0] for p in points], [p[1] for p in points]))


# ------------------------------------------------------------------ render and measure
def render(key, vel, tab, binary):
    """one note in its own run (a fresh voice): RAW, NOISE 0, 0.45 s -> mono float from the onset"""
    with tempfile.TemporaryDirectory(dir=os.path.dirname(PROBE)) as td:
        base = os.path.join(td, "r")
        open(base + ".txt", "w").write("20 midi 0xB0 119 127\n25 midi 0xB0 40 127\n30 midi 0xB0 104 0\n100 midi 0x90 %d %d\n560 end\n" % (key, vel))
        env = dict(os.environ)
        if tab:
            env["RH_VM_TAB"] = tab
        subprocess.run(["nice", "-n", "10", binary, base, base + ".txt"], check=True, capture_output=True, env=env)
        w = wave.open(base + ".wav")
        ch = w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    if ch == 2:
        x = 0.5 * (x[0::2] + x[1::2])
    a = np.abs(x)
    pk = a.max()
    if pk <= 0:
        return x[:0], 0.0
    i = int(np.argmax(a > 0.02 * pk))
    return x[max(0, i - int(0.0005 * FS)):], float(pk)


def spec(seg, nfft=1 << 16):
    w = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * w, nfft)) ** 2 / (np.sum(w) ** 2 / 4.0)
    return np.fft.rfftfreq(nfft, 1.0 / FS), S


def band(fr, S, f, half):
    m = (fr >= f - half) & (fr <= f + half)
    return float(np.sum(S[m])) + 1e-30


def metrics(x, key):
    """bark (H2..H5 re H1, 30-150 ms) and H1 (dB, 30-300 ms), as the study's measure()"""
    f0 = 440.0 * 2 ** ((key - 69) / 12.0)
    fr, S = spec(x[int(0.03 * FS): int(0.30 * FS)])
    m = np.where((fr >= 0.94 * f0) & (fr <= 1.06 * f0))[0]
    f1 = fr[m[np.argmax(S[m])]] if len(m) else f0
    half = 0.25 * f0
    h1 = band(fr, S, f1, half)
    fr2, S2 = spec(x[int(0.03 * FS): int(0.15 * FS)])
    hp = [band(fr2, S2, h * f1, half) for h in range(1, 6)]
    return 10 * math.log10(sum(hp[1:]) / hp[0]), 10 * math.log10(h1)


def tabfile(xdb, gdb, path):
    """X, G in dB [key][point] -> the probe's file (uint16 [2][88][17], Q11); returns the integer tables"""
    X = np.clip(np.round(ONE * 10 ** (np.asarray(xdb) / 20)), 1, 65535).astype("<u2")
    G = np.clip(np.round(ONE * 10 ** (np.asarray(gdb) / 20)), 1, 65535).astype("<u2")
    with open(path, "wb") as f:
        f.write(np.ascontiguousarray(np.stack([X, G])).tobytes())
    return X, G


def grid(keys, vels, tab, binary=PROBE):
    jobs = [(k, v) for k in keys for v in vels]
    with ThreadPoolExecutor(JOBS) as ex:
        res = list(ex.map(lambda kv: render(kv[0], kv[1], tab, binary), jobs))
    out = {}
    for (k, v), (x, pk) in zip(jobs, res):
        b, h = metrics(x, k)
        out[(k, v)] = (b, h, 20 * math.log10(pk + 1e-12))
    return out


def sweep(keys, sdb, tab_dir):
    """the bark and H1 against the swing X (dB) at v100, G = -X - 6 dB (no clipping); H1 given back at G = 1"""
    out = {}
    for s in sdb:
        xd = np.full((NKEY, NP), s)
        p = os.path.join(tab_dir, "sweep.bin")
        tabfile(xd, -np.maximum(xd, 0) - 6.0, p)
        r = grid(keys, [100], p)
        for k in keys:
            b, h, pk = r[(k, 100)]
            out[(k, s)] = (b, h + max(s, 0) + 6.0, pk)
    return out


# ------------------------------------------------------------------ targets
def bark_target(c, k, v):
    kn = pts(c["knee_points"], k)
    fl = pts(c["flat_db_points"], k)
    if kn >= 127:
        return fl
    w = c["hinge_w"]
    sl = pts(c["slope_points"], k) if "slope_points" in c else c["slope_db_per_vel"]
    return fl + sl * w * float(np.logaddexp(0, (v - kn) / w))


def h1_target(c, k, v, h1b):
    """h1b: {v: H1 of the engine without the map} for this key"""
    if v <= 64:
        return h1b[v]
    D = pts(c["h1_top_db_points"], k)
    a = pts(c["h1_linear_points"], k)
    tau = c["h1_sat_tau"]
    sat = (1 - math.exp(-(v - 64) / tau)) / (1 - math.exp(-63 / tau))
    return h1b[64] + (1 - a) * D * sat + a * (h1b[v] - h1b[64])


def to_points(vals):
    """{v: dB} at VG -> 17 points (v = 0, 8 .. 128): 1..15 measured, 0 and 16 extrapolated (16 through v127)"""
    p = np.zeros(NP)
    for i in range(1, 16):
        p[i] = vals[8 * i]
    p[0] = p[1] - (p[2] - p[1])
    p[16] = p[15] + (vals[127] - p[15]) * 8.0 / 7.0
    return p


# ------------------------------------------------------------------ the fit
def fit(c, keys, log):
    td = os.path.dirname(PROBE)
    lo, hi = c["x_db_range"]
    log("baseline (no map)")
    base = grid(keys, VG, None)
    h1b = {k: {v: base[(k, v)][1] for v in VG} for k in keys}
    h100 = {k: float(np.interp(100, VG, [h1b[k][v] for v in VG])) for k in keys}
    sdb = list(np.arange(-30.0, 30.1, 3.0))
    log("swing sweep at v100 (%d steps)" % len(sdb))
    sw = sweep(keys, sdb, td)
    xdb = np.zeros((NKEY, NP))
    tgt = {}
    for k in keys:
        B = np.array([sw[(k, s)][0] for s in sdb])
        Bm = np.maximum.accumulate(B)                    # the rising part (beyond the bark's maximum: clamp)
        smax = sdb[int(np.argmax(B))]
        vals = {}
        for v in VG:
            bt = bark_target(c, k, v)
            tgt[(k, v)] = bt
            s_need = float(np.interp(bt, Bm + 1e-6 * np.arange(len(Bm)), sdb)) if bt < Bm[-1] else smax
            s_need = min(s_need, smax)
            vals[v] = s_need - (h1b[k][v] - h100[k])        # the swing v already gives (H1, near linear without the map)
        xdb[k - KEY_LO] = np.clip(to_points(vals), lo, hi)
    slope = {}
    for k in keys:
        B = np.array([sw[(k, s)][0] for s in sdb])
        slope[k] = (np.array(sdb), np.gradient(B, sdb))
    gdb = np.zeros((NKEY, NP))
    p = os.path.join(td, "fit.bin")
    for it in range(int(c["iterations"])):
        gprobe = -np.maximum(xdb, 0) - 6.0
        tabfile(xdb, gprobe, p)
        r = grid(keys, VG, p)
        err = []
        for k in keys:
            vals = {}
            for i, v in enumerate(VG):
                b = r[(k, v)][0]
                e = tgt[(k, v)] - b
                err.append(e)
                xv = float(np.interp(v, [8 * j for j in range(NP)], xdb[k - KEY_LO]))
                stot = xv + (h1b[k][v] - h100[k])
                sl = float(np.clip(np.interp(stot, *slope[k]), 0.25, 1.5))
                vals[v] = xv + 0.9 * e / sl
            xdb[k - KEY_LO] = np.clip(to_points(vals), lo, hi)
        err = np.abs(err)
        log("iteration %d: bark error median %.2f dB, 90%% %.2f, worst %.2f" % (it, np.median(err), np.percentile(err, 90), err.max()))
    gprobe = -np.maximum(xdb, 0) - 6.0
    tabfile(xdb, gprobe, p)
    r = grid(keys, VG, p)
    for k in keys:
        vals = {}
        for v in VG:
            g_now = float(np.interp(v, [8 * j for j in range(NP)], gprobe[k - KEY_LO]))
            vals[v] = h1_target(c, k, v, h1b[k]) - (r[(k, v)][1] - g_now)
        gdb[k - KEY_LO] = to_points(vals)
    return xdb, gdb, base, h1b, tgt


def emit(X, G, c, note, hdr=HDR):
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* GENERATED by tools/gen_velmap.py --write from docs/model/data/velmap.json - do not edit. The Rhodes velocity map",
         " * (rhodes.c rh_vm): per key (21..108) and velocity point (v = 0, 8 .. 128, linear in between) the swing scale X",
         " * and the gain scale G, Q11. %s */" % note,
         "static const uint16_t RH_VM_X[RH_NKEY][17] = {"]
    L += ["  {" + ",".join(str(int(q)) for q in row) + "}," for row in X]
    L += ["};", "static const uint16_t RH_VM_G[RH_NKEY][17] = {"]
    L += ["  {" + ",".join(str(int(q)) for q in row) + "}," for row in G]
    L += ["};"]
    for row in list(X) + list(G):
        for q in row:
            assert 0 < int(q) < 65536
    open(hdr, "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--keys", default=None, help="comma list (test runs; --write needs every key)")
    ap.add_argument("--cfg", default=CFG, help="the targets (default docs/model/data/velmap.json)")
    ap.add_argument("--hdr", default=HDR, help="the header to write")
    a = ap.parse_args()
    c = json.load(open(a.cfg))
    keys = [int(k) for k in a.keys.split(",")] if a.keys else list(range(KEY_LO, KEY_LO + NKEY))
    log = lambda s: print(s, file=sys.stderr, flush=True)
    if a.write:
        build_probe()
        xdb, gdb, base, h1b, tgt = fit(c, keys, log)
        X = np.clip(np.round(ONE * 10 ** (xdb / 20)), 1, 65535).astype(int)
        G = np.clip(np.round(ONE * 10 ** (gdb / 20)), 1, 65535).astype(int)
        rng = lambda A: (20 * math.log10(A.min() / ONE), 20 * math.log10(A.max() / ONE))
        log("X %.1f .. %.1f dB, G %.1f .. %.1f dB" % (rng(X) + rng(G)))
        if a.keys:
            json.dump(dict(xdb=xdb.tolist(), gdb=gdb.tolist()), open(os.path.join(os.path.dirname(PROBE), "partial.json"), "w"))
            log("partial fit (keys %s): not written" % a.keys)
            return
        emit(X, G, c, "Physics only (the host engine), no recordings.", a.hdr)
        log("wrote %s" % a.hdr)
        return
    # the residual with the header as built (build/host/frodhes)
    binary = os.path.join(ROOT, "build", "host", "frodhes")
    r = grid(keys, VG, None, binary)
    for k in keys:
        print("%3d bark %s" % (k, " ".join("%5.1f/%5.1f" % (r[(k, v)][0], bark_target(c, k, v)) for v in (8, 56, 72, 104, 127))))


if __name__ == "__main__":
    main()
