#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Is a moved release-diff cell linear reverb content, or distortion? (rev-grain, 0.9.10)

Per cell: the output of a tree minus the output of the same tree with its reverb lines 4x further above their int16
rounding (the send x4 (v0.9.9) or x2 (RV_SEND, 0.9.10), the return / 4 or / 2: the same wet level, a closer
approximation of the exact, linear reverb). That residual is the reverb's own non-linear part (its rounding, a tail
truncated to zero). The candidate's extra content is linear reverb when its residual stays at or under v0.9.9's in the
quantity the gate reads (the median of the gate's 4 strike times): the harmonic band's power (H2..H6, absolute), the 12..20 kHz grain, or the residual's rms
(LSB) over the hold (decay, cb, level, lu, cent, inh), after the key-up (keyup; a residual under 0.5 LSB rms is under
the 1-LSB silence floor, 1d3c1ca9) or over the key-up's 300 ms (clk_off). A cell whose residual grows is a defect.

  tools/rev_lin_proof.py CELLS.json OUT.json      CELLS: [[grid, cid, cell, metric], ...]
  REV_LIN_SRC=DIR|REV: judge that tree (uncommitted sources) in place of HEAD
The x4 builds count the int16 lines' clamps (none may clip: the reference must stay linear); where v0.9.9's x4 clips
(a loud MIX), its x3 or x2 is the reference."""
import json
import os
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_diff as rd  # noqa: E402

OUT = os.environ.get("REV_LIN_OUT", os.path.join(rd.ROOT, "build", "rev_lin"))
X4 = {   # (file, old, new) per tree kind
    "new": [("#define RV_SEND(v) ((v) * 258 * (254 - (v)) / 127)", "#define RV_SEND(v) ((v) * 258 * (254 - (v)) / 127 * 2)"),
            ("#define RV_RK ((1 << 29) + 1)", "#define RV_RK ((1 << 28) + 1)")],
    **{("o99" if f == 4 else f"o99/{f}"): [("int32_t send = tgt ? v * 258 : 0,", f"int32_t send = tgt ? v * 258 * {f} : 0,"),
                         ("rk = (254 - vl) * 4227330 + (4 * (254 - vl)) / 254 + 1;",
                          f"rk = ((254 - vl) * 4227330 + (4 * (254 - vl)) / 254) / {f} + 1;")] for f in (4, 3, 2)}}
CLAMP = ("static inline __attribute__((always_inline)) int32_t hl_c16(int32_t v) { v = v > -32768",
         'static uint32_t hl_ncl;\nstatic void hl_rep(void) { fprintf(stderr, "HLCLAMPS %u\\n", hl_ncl); }\n'
         "static inline __attribute__((always_inline)) int32_t hl_c16(int32_t v) { { static int reg; if (!reg) { reg = 1; "
         "atexit(hl_rep); } if (v > 32767 || v < -32768) hl_ncl++; } v = v > -32768")


def tree(rev, name, x4):
    path = os.path.join(OUT, name)
    subprocess.run(["rm", "-rf", path], check=True)
    os.makedirs(path)
    if os.path.isdir(rev):              # (a source tree: its firmware/ and tests/host.c as they are, uncommitted)
        a = subprocess.run(["tar", "-cf", "-", "-C", rev, "firmware", "tests/host.c"], capture_output=True, check=True).stdout
    else:
        a = subprocess.run(["git", "-C", rd.ROOT, "archive", rev, "firmware", "tests/host.c"], capture_output=True,
                           check=True).stdout
    subprocess.run(["tar", "-x", "-C", path], input=a, check=True)
    if x4:
        for f in ("rev_types.c", "rev_hq.c"):
            p = os.path.join(path, "firmware", "src", f)
            s = open(p).read()
            for old, new in X4[x4]:
                assert s.count(old) == 1, (name, f, old)
                s = s.replace(old, new)
            open(p, "w").write(s)
        p = os.path.join(path, "firmware", "src", "fx.c")
        s = open(p).read()
        assert s.count(CLAMP[0]) == 1
        open(p, "w").write(s.replace(CLAMP[0], CLAMP[1]))
    bins = {}
    for b, defs in (("fm1", []), ("hq", rd.HQ_DEFS)):
        bins[b] = os.path.join(path, b)
        subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-include", "stdio.h", "-include", "stdlib.h",
                        *defs, "-I" + os.path.join(path, "firmware", "src"), "-I" + os.path.join(path, "firmware", "gen"),
                        "-o", bins[b], os.path.join(path, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    return bins


def render(binp, name, lines):
    path = os.path.join(OUT, "r", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path + ".txt", "w").write("\n".join(lines) + "\n")
    p = subprocess.run([binp, path, path + ".txt"], capture_output=True, text=True)
    cl = [int(l.split()[1]) for l in p.stderr.splitlines() if l.startswith("HLCLAMPS")]
    w = wave.open(path + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float).mean(1)
    w.close()
    for f in os.listdir(os.path.dirname(path)):
        if f.startswith(os.path.basename(path)):
            os.remove(os.path.join(os.path.dirname(path), f))
    return x, (cl[0] if cl else 0)


def quantities(x, d, t, ns):
    i, off = int(t / 1000 * rd.FS), int((t + rd.HOLD) / 1000 * rd.FS)
    w0, w1 = i + int(rd.W0 * rd.FS), i + int(rd.W1 * rd.FS)
    p, fr = rd.spectrum(x[w0:w1])
    pd, _ = rd.spectrum(d[w0:w1])
    m = {}
    if len(ns) == 1:
        f0 = rd.f0(ns[0])
        for k in range(2, 7):
            m[f"H{k}"] = rd.db(pd[(fr > k * f0 * (1 - rd.BAND)) & (fr < k * f0 * (1 + rd.BAND))].max())
    on = np.zeros(len(fr), bool)
    for fu in [rd.f0(n) for n in ns]:
        for k in range(1, int(20000 / fu) + 1):
            on |= np.abs(fr - k * fu) < max(rd.IBW, k * fu * rd.IREL)
    hf = (fr >= 12000) & (fr < 20000) & ~on
    n = w1 - w0
    m["grain"] = rd.db(pd[hf].sum() * 2 / (n * 0.375 * n) / 32768.0 ** 2)
    lsb = lambda y: float(np.sqrt(np.mean(y ** 2)))
    m["keyup"] = lsb(d[off + int(0.08 * rd.FS):off + int(0.28 * rd.FS)])
    m["clk_off"] = lsb(d[off:off + int(0.3 * rd.FS)])
    m["hold"] = lsb(d[i:off])
    return m


def main():
    cells, out = json.load(open(sys.argv[1])), sys.argv[2]
    src = os.environ.get("REV_LIN_SRC", "HEAD")    # (a tree to judge in place of HEAD: a directory or a git rev)
    bins = {"new": tree(src, "new", None), "newx4": tree(src, "newx4", "new"),
            "o99": tree(rd.LAST, "o99", None), "o99x4": tree(rd.LAST, "o99x4", "o99"),
            "o99x3": tree(rd.LAST, "o99x3", "o99/3"), "o99x2": tree(rd.LAST, "o99x2", "o99/2")}
    cases = {g: {c[0]: c for c in rd.cases(g)} for g in ("fast", "full")}

    def job(key):
        g, cid = key
        case, b = cases[g][cid], "fm1" if cid.startswith("fm1") else "hq"
        per, CL = [], {t: 0 for t in bins}
        for sh in rd.SHIFTS:            # (the gate's 4 strike times: the median of each quantity, as release_diff's)
            lines = rd.script(g, case[2], case[3], sh)
            ev, _ = rd.events(g, case[3], sh)
            X = {}
            for t in bins:
                X[t], c = render(bins[t][b], f"{t}-{g}-{cid.replace('/', '_')}-{sh}", lines)
                CL[t] += c
            per.append((X, ev))
        # (v0.9.9's reference: x4, or the most precise of x3 / x2 whose lines do not clip: a clipped reference is not linear)
        ref = next((r for r in ("o99x4", "o99x3", "o99x2") if CL[r] == 0), "o99x4")
        per = [{lab: {tr: quantities(X[tr], X[tr] - X[rf], t, ns) for tr, rf in (("new", "newx4"), ("o99", ref))}
                for lab, t, ns, v, ped in ev} for X, ev in per]
        res = {lab: {tr: {q: float(np.median([p[lab][tr][q] for p in per])) for q in per[0][lab][tr]}
                     for tr in ("new", "o99")} for lab in per[0]}
        return key, (res, {"new": CL["newx4"], ref: CL[ref]})
    with ThreadPoolExecutor(2) as ex:
        R = dict(ex.map(job, sorted({(c[0], c[1]) for c in cells})))
    rows = []
    for g, cid, cell, m in cells:
        res, CL = R[(g, cid)]
        q = m if m[0] == "H" or m in ("grain", "keyup", "clk_off") else "hold"
        a, o = res[cell]["new"][q], res[cell]["o99"][q]
        if q in ("keyup", "clk_off", "hold"):
            lin = a <= o * 1.06 + 0.02 or (q != "hold" and a <= 0.5)
        else:
            lin = a <= o + 0.5
        rows.append({"grid": g, "cell": f"{cid}/{cell}", "metric": m, "quantity": q, "residual": round(a, 3),
                     "residual_099": round(o, 3), "linear": bool(lin and max(CL.values()) == 0), "x4_clamps": CL})
    json.dump(rows, open(out, "w"), indent=0)
    print(f"{len(rows)} cells: {sum(r['linear'] for r in rows)} linear, {sum(not r['linear'] for r in rows)} not")
    return 0


if __name__ == "__main__":
    sys.exit(main())
