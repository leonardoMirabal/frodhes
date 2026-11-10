#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The clean top (rhodes.c rh_top_q12, firmware/gen/rh_top.h RH_TOP_R), on the host.

    tools/rh_top.py --write     measure every key of every TYPE at v127 (RESPONSE A, effects bypassed by CC 119, NOISE
                                0) on a host build without the cut (-DRH_NO_TOP) and with a cut of PROBE_R on every key:
                                the pickup's share of H2 and of the attack's H2 from the two, each cut to bring it to
                                TARGET, write the header
    tools/rh_top.py             the table: H2 / aH2 at v110 and v127, the level (RMS 0 .. 650 ms) of v127 re v110, the
                                spectral centroid of both (whole, attack), per TYPE and key, on a fresh host build

The pickup's H2 re H1 grows in proportion to the tine's swing; the cut takes the swing down by R (1/32 octave) at v127,
linearly in dB from the knee 127 - 1.25 R, and the voice's output gain takes the same factor back. The pickup's H2 falls
by the cut, 1 dB a dB; what else sits at 2 f0 (the tine's own partials, the contact) does not, so it is taken apart (two
swings, --write) and not cut for. The fundamental's level stays. Windows as
tools/dry_dist.py: H2 150 .. 650 ms after the strike, aH2 0 .. 150 ms. Physics only: nothing here reads recordings."""
import argparse
import os
import subprocess
import sys
import wave
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dry_dist as dd  # noqa: E402
import release_diff as rd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "rh_top")
HDR = os.path.join(ROOT, "firmware", "gen", "rh_top.h")
FS = 44100
KEY_LO, NKEY = 21, 88
TYPES = (0, 1, 2, 3)                   # CC 80 = TYPE x 32
TARGET = -14.0                         # dB re H1: the pickup's H2 and aH2 at v127 (the user's -12; what else
                                       # sits at 2 f0 adds ~1 dB)
UNIT = 6.0206 / 32                     # dB: 1/32 octave
R_MAX = 120                            # (rhodes.c rh_top_x: the code stays >= 64; R + G <= 127)
PROBE_R = 64                           # the probe's cut (12 dB)
G_MAX = 8                              # the loudness's gain back at most 1.5 dB (DYNO's top keys: their bark, the
                                       # attack's 2 f0 up to 6 dB over H1, read 5 dB of loudness; the fundamental
                                       # that much hotter is the headroom's)
CFLAGS = ["-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-Ifirmware/src",
          "-Ifirmware/gen"]
STEP = 1500                            # ms a note (held 800)


def build(name, extra=()):
    b = os.path.join(OUT, name)
    os.makedirs(OUT, exist_ok=True)
    subprocess.run(["cc", *extra, *CFLAGS, "-o", b + ".tmp", "firmware/src/frodhes.c", "-lm"], cwd=ROOT,
                   check=True)                                       # (extra first: a -I before firmware/gen's)
    os.replace(b + ".tmp", b)
    return b


def spec_peaks(seg, fund, pad):
    from scipy.signal.windows import blackmanharris
    n = len(seg) * pad
    p = np.abs(np.fft.rfft(seg * blackmanharris(len(seg)), n)) ** 2
    fr = np.fft.rfftfreq(n, 1 / FS)
    fund = dd.tuned(p, fr, [fund])[0]
    pk = [p[(fr > k * fund * (1 - dd.BAND)) & (fr < k * fund * (1 + dd.BAND))].max() for k in (1, 2)]
    return 10 * np.log10(pk[1] / pk[0] + 1e-30)


def run(job):
    """one TYPE / method, keys x vels in one script: {(key, vel): (H2, aH2, level dB, centroid Hz)}"""
    binary, ty, meth, keys, vels = job
    d = os.path.join(OUT, "r")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{os.path.basename(binary)}-{ty}-{meth}-{os.getpid()}")
    ls, ev, t = dd.type_base_lines(10) + ["20 midi 0xB0 119 127",   # (the voicing at its defaults, not F01's)
                  "30 midi 0xB0 104 0", f"40 midi 0xB0 80 {ty * 32}",
                 f"50 midi 0xB0 40 {meth}"], [], 400
    for k in keys:
        for v in vels:
            ev.append((k, v, t))
            ls += [f"{t} midi 0x90 {k} {v}", f"{t + 800} midi 0x80 {k} 0"]
            t += STEP
    ls.append(f"{t + 200} end")
    open(path + ".txt", "w").write("\n".join(sorted(ls, key=lambda l: int(l.split()[0]))) + "\n")
    subprocess.run(["nice", "-n", "10", binary, path, path + ".txt"], check=True, capture_output=True)
    w = wave.open(path + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float).mean(1)
    w.close()
    for f in os.listdir(d):
        if f.startswith(os.path.basename(path)):
            os.remove(os.path.join(d, f))
    out = {}
    for k, v, t in ev:
        ts = dd.strike(x, t / 1000, FS)
        h2 = spec_peaks(x[int((ts + dd.W0) * FS):int((ts + dd.W1) * FS)], dd.f0(k), 1)
        a2 = spec_peaks(x[int(ts * FS):int((ts + 0.15) * FS)], dd.f0(k), 8)
        seg = x[int(ts * FS):int((ts + 0.65) * FS)]
        p = np.abs(np.fft.rfft(seg)) ** 2
        fr = np.fft.rfftfreq(len(seg), 1 / FS)
        sa = x[int(ts * FS):int((ts + 0.15) * FS)]
        pa = np.abs(np.fft.rfft(sa)) ** 2
        fa = np.fft.rfftfreq(len(sa), 1 / FS)
        out[(k, v)] = (h2, a2, 10 * np.log10(np.mean((seg / 32768) ** 2) + 1e-30), float((p * fr).sum() / p.sum()),
                       float((pa * fa).sum() / pa.sum()), -0.691 + 10 * np.log10(np.mean(rd.kweight(seg / 32768) ** 2)))
    return ty, meth, out


def measure(binary, keys, vels, meths=(0,), jobs=4):
    js = [(binary, ty, m, keys, vels) for ty in TYPES for m in meths]
    with ProcessPoolExecutor(jobs) as ex:
        return {(ty, m): o for ty, m, o in ex.map(run, js)}


def write(R, L):
    def tab(T):
        return ",\n".join("    {" + ",".join("%d" % r for r in row) + "}" for row in T)
    open(HDR, "w").write(
        "/* SPDX-License-Identifier: GPL-3.0-only */\n"
        "/* generated by tools/rh_top.py --write: the clean top (rhodes.c rh_top_x) per TYPE x key, 1/32 octave at\n"
        " * v127:"
        " RH_TOP_R the swing's cut (the pickup's H2 and the attack's H2 to %.0f dB re H1; 0 = none), RH_TOP_G the\n"
        " * output's gain on top of the cut's own back (the K-weighted loudness at v127 as without the cut, never\n"
        " * less, at most 1.5 dB).\n"
        " * RESPONSE A, effects bypassed, NOISE 0 */\n"
        "#define RH_TOP_Q 1\n"
        "static const uint8_t RH_TOP_R[4][RH_NKEY] = {\n%s\n};\n"
        "static const uint8_t RH_TOP_G[4][RH_NKEY] = {\n%s\n};\n" % (TARGET, tab(R), tab(L)))


def worst(o, k, v):
    return max(o[(k, v)][0], o[(k, v)][1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--bin", help="the table on this binary instead of a fresh build")
    ap.add_argument("--keys", help="the table's keys (comma-separated)")
    ap.add_argument("-j", type=int, default=int(os.environ.get("PYTEST_N", "2") or 2))
    a = ap.parse_args()
    keys = list(range(KEY_LO, KEY_LO + NKEY))
    if a.write:
        # the pickup's H2 (in proportion to the swing s, re H1) and what else sits at 2 f0 (the tine's and the tone
        # bar's own partials there, the hammer's contact: linear, the same re H1 at any swing; DYNO's top keys have
        # their attack's 2 f0 over H1 at every velocity, 0.9.9 too) apart, from two swings: v127 as it is and cut by
        # PROBE_R. In power, H2(s)^2 = N^2 s^2 + L^2: N^2 = (H2(1)^2 - H2(c)^2) / (1 - c^2). The cut brings N s to
        # TARGET
        b0 = build("notop", ["-DRH_NO_TOP"])
        pdir = os.path.join(OUT, "probe-hdr")
        os.makedirs(pdir, exist_ok=True)
        hdr0 = HDR
        globals()["HDR"] = os.path.join(pdir, "rh_top.h")
        write(np.full((4, NKEY), PROBE_R, int), np.zeros((4, NKEY), int))
        globals()["HDR"] = hdr0
        b1 = build("probe", ["-I" + os.path.relpath(pdir, ROOT)])
        m0, m1 = measure(b0, keys, (127,), jobs=a.j), measure(b1, keys, (127,), jobs=a.j)
        c2 = 2.0 ** (-2 * PROBE_R / 32)
        R = np.zeros((4, NKEY), int)
        for ty in TYPES:
            for j, k in enumerate(keys):
                r = 0.0
                for w in (0, 1):                                   # H2, aH2
                    p0, p1 = 10 ** (m0[(ty, 0)][(k, 127)][w] / 10), 10 ** (m1[(ty, 0)][(k, 127)][w] / 10)
                    n2 = max(p0 - p1, 0.0) / (1 - c2)
                    if n2 > 0:
                        r = max(r, (10 * np.log10(n2) - TARGET) / UNIT)
                R[ty, j] = min(R_MAX, max(0, int(np.ceil(r))))
        L = np.zeros((4, NKEY), int)
        write(R, L)
        m2 = measure(build("top"), keys, (127,), jobs=a.j)          # the loudness the cut took (the bark's)
        for ty in TYPES:
            for j, k in enumerate(keys):
                if R[ty, j]:
                    d = m0[(ty, 0)][(k, 127)][5] - m2[(ty, 0)][(k, 127)][5]
                    L[ty, j] = min(G_MAX, 127 - R[ty, j], max(0, int(round(d / UNIT))))
        write(R, L)
        print("R max %d (%.1f dB), keys cut %d of %d; G max %d (%.1f dB)" % (R.max(), R.max() * UNIT, (R > 0).sum(),
                                                                          R.size, L.max(), L.max() * UNIT))
        return
    b = a.bin or build("cur")
    ks = [int(k) for k in a.keys.split(",")] if a.keys else keys[7::4] + [93]
    m = measure(b, ks, (110, 127), meths=(0, 127), jobs=a.j)
    for (ty, meth), o in sorted(m.items()):
        print("TYPE %d RESPONSE %s: key  H2/aH2 v110  H2/aH2 v127  lvl127-110  cent v110 v127  attack cent v110 v127"
              % (ty, "AB"[meth > 0]))
        for k in sorted({k for k, _ in o}):
            h, g = o[(k, 110)], o[(k, 127)]
            print("   %3d  %6.1f %6.1f  %6.1f %6.1f  %+6.2f  %6.0f %6.0f  %6.0f %6.0f" % (k, h[0], h[1], g[0], g[1],
                                                                                   g[2] - h[2], h[3], g[3], h[4], g[4]))


if __name__ == "__main__":
    main()
