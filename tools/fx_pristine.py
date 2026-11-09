#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The effects' pristine gate (host): every effect but DRIVE clean at MIX / DEPTH 0, 64, 127 (the user 2026-11-08: "a
pristine and clean result for each efx beside overdrive"). From F01 with every effect off (COMP too), one effect on:

  lim     the look-ahead limiter idle (fx.c: sub-blocks with a gain under 1, the host's fx-clip line) on single notes
          (v64, v127) and a v127 4-note chord (C3 G3 C4 E4)
  clip    no clamp cut anywhere in the delay line (send, feedback, the write), the reverb's send and kernel inputs, the
          chorus line (fx.c FX_HC)
  lvl0    at MIX / DEPTH 0: the level of FX on within 0.5 dB of the bypass (FX off)
  peak    the effect adds no peak: its highest sample over every signal at most PK_TOL (2 dB: the touch wah's onset,
          +1.8) over the bypass' (2026-11-08: the phaser's comb, the pan's sides, a resonant LP / HP, the wah's make-up
          and ANA's bunched repeats lifted a chord 2.6 .. 3.6 dB, onto the limiter)
  nl      added non-linearity: the superposition residual |AB - A - B| in dBFS (A the 4-note chord, B a fifth note
          (C5) at the same time, AB both; v100) over the bypass' own (the engine's dither, the voices); a clipping stage, the limiter or a
          saturation shows here as harmonics and intermodulation. The designed saturations are exempt and reported
          (delay ANA / TAPE: the tape's softclip and curve; COMP and the touch WAH: level-dependent by design; INFINITE:
          its held-level control)

  python3 tools/fx_pristine.py [--builds one,c1,hq] [--quick] [--presets]   (build/fxp/<build>: see --make)
Exit 1 on a failure. The builds: one (the FM-1, the plugin's CLASSIC slots), c1 (FR_C1, two cores), hq (MOVE_DEFS:
the Move and the plugin's HQ slots)."""
import argparse
import math
import os
import re
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "fxp")
FS = 44100
LIM_T = 24000
BUILD_DEFS = {"one": [], "c1": ["-DFR_C1=1"],
              "hq": ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]}
# every effect off: COMP, DRIVE, TREM/WAH, CHO/PHA, DELAY, REVERB MIX, FILTER, SPREAD, EQ flat
CLEAN = [(106, 0), (84, 0), (17, 0), (85, 0), (88, 0), (91, 0), (70, 0), (105, 0), (34, 64), (35, 64), (37, 64)]
D = "{d}"
EFFECTS = {   # name: (CCs with {d} = the MIX / DEPTH under test, exempt from nl, MIX-0 level applies)
    "chorus": ([(85, 64), (86, D)], False, True),
    "chorus-fdbk": ([(85, 64), (86, D), (24, 127)], False, True),   # (no flanger: the chorus at FDBK 127 stands in)
    "phaser": ([(85, 127), (13, D)], False, True),
    "trem": ([(17, 64), (21, 127), (18, D)], False, True),
    "pan": ([(17, 64), (21, 0), (18, D)], False, True),
    "wah": ([(17, 127), (18, D)], True, True),
    "filter-lp18": ([(70, 40), (74, 64), (71, D)], False, False),
    "filter-hp12": ([(70, 60), (74, 64), (71, D)], False, False),
    "filter-bp": ([(70, 90), (74, 64), (71, D)], False, False),
    "eq": ([(34, D), (35, D), (37, D)], False, False),
    "comp": ([(106, 127), (107, D), (113, 0)], True, False),
    "delay-dig": ([(88, 40), (89, D)], False, True),
    "delay-ana": ([(88, 70), (89, D)], True, True),
    "delay-tape": ([(88, 127), (89, D)], True, True),
    "delay-dig-fb": ([(88, 40), (89, D), (26, 120)], False, True),
    "delay-ana-fb": ([(88, 70), (89, D), (26, 120)], True, True),
    "delay-tape-fb": ([(88, 127), (89, D), (26, 120)], True, True),
    "hall": ([(95, 40), (91, D)], False, True),
    "plate": ([(95, 70), (91, D)], False, True),
    "inf": ([(95, 127), (91, D)], True, True),
}
DEPTHS = (0, 64, 127)
CHORD = (48, 55, 60, 64)
B_NOTE = 72
NL_VEL = 100     # (the superposition renders: a v127 5-note chord reaches the limiter dry, 0.9.9 as 0.9.10)
END = 4500
PK_TOL = 2.0     # an effect's peak over the bypass' (every signal): the touch wah's onset +1.8 dB (its band-pass' own
                 # resonant peak before the make-up settles), every other effect +0.5 dB at most (EQ / filter 127 exempt)
NL_FLOOR, NL_TOL = -80.0, 6.0     # nl fails over max(the bypass' residual, -80 dBFS) + 6 dB (DIG's int16 line: ~-78)


def make(builds):
    os.makedirs(OUT, exist_ok=True)
    ps = []
    for b in builds:
        cmd = ["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-Ifirmware/src",
               "-Ifirmware/gen", *BUILD_DEFS[b], "-o", os.path.join(OUT, b + ".tmp"), "firmware/src/frodhes.c", "-lm"]
        ps.append((b, subprocess.Popen(cmd, cwd=ROOT)))
    for b, p in ps:
        assert p.wait() == 0, b
        os.replace(os.path.join(OUT, b + ".tmp"), os.path.join(OUT, b))


def render(job):
    """job: (build, tag, pc, ccs, byp, notes [(t, n, v)]) -> (tag, L, R, counters)"""
    build, tag, pc, ccs, byp, notes = job
    lines = ["100 midi 0xB0 111 0", f"200 midi 0xC0 {pc} 0"] + [f"300 midi 0xB0 {n} {v}" for n, v in ccs]
    if byp:
        lines.append("400 midi 0xB0 111 127")
    for t, n, v in notes:
        lines += [f"{t} midi 0x90 {n} {v}", f"{t + 1500} midi 0x80 {n} 0"]
    lines.sort(key=lambda l: int(l.split()[0]))
    lines.append(f"{END} end")
    d = os.path.join(OUT, "runs", build)
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, tag)
    with open(out + ".txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    p = subprocess.run([os.path.join(OUT, build), out, out + ".txt"], capture_output=True, text=True)
    m = re.search(r"fx-clip delay (\d+) reverb (\d+) chorus (\d+) limiter (\d+)", p.stdout)
    assert p.returncode == 0, f"{build} {tag}: rc {p.returncode}\n{p.stdout[-600:]}{p.stderr[-600:]}"
    cnt = tuple(int(v) for v in m.groups()) if m else (0, 0, 0, -1)   # (-1: a build without the counters)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    for e in (".wav", ".txt"):
        os.remove(out + e)
    return tag, x[:, 0], x[:, 1], cnt


def rms_db(L, R, t0=0.6, t1=2.0):
    a, b = L[int(t0 * FS):int(t1 * FS)], R[int(t0 * FS):int(t1 * FS)]
    return 10 * math.log10(np.mean((a * a + b * b) / 2) / 32768 ** 2 + 1e-20)


def resid_db(ab, a, b):
    """the superposition residual (AB - A - B), dBFS over 0.5 .. 4.5 s (the engine's own: ~-89 dBFS, its dither)"""
    i0 = int(0.5 * FS)
    r = np.concatenate([ab[0][i0:] - a[0][i0:] - b[0][i0:], ab[1][i0:] - a[1][i0:] - b[1][i0:]])
    return 10 * math.log10(np.mean(r * r) / 32768 ** 2 + 1e-20)


def signals():
    # (A and B start together: the output gate opens on the same block in A, B and AB, a gain the same in all three)
    return {"s64": [(500, 57, 64)], "s127": [(500, 57, 127)], "c127": [(500, n, 127) for n in CHORD],
            "A": [(500, n, NL_VEL) for n in CHORD], "B": [(500, B_NOTE, NL_VEL)],
            "AB": [(500, n, NL_VEL) for n in CHORD] + [(500, B_NOTE, NL_VEL)]}


def jobs_for(build, name, ccs, byp, pc=0):
    return [(build, f"{name}-{s}{'-byp' if byp else ''}", pc, ccs, byp, notes) for s, notes in signals().items()]


def analyse(res, build, name, byp=False):
    k = f"{'-byp' if byp else ''}"
    g = {s: res[(build, f"{name}-{s}{k}")] for s in signals()}
    lim = {s: g[s][2][3] for s in ("s64", "s127", "c127")}
    clip = tuple(sum(g[s][2][i] for s in g) for i in range(3))
    nl = resid_db(*((g[s][0], g[s][1]) for s in ("AB", "A", "B")))
    lvl = rms_db(g["c127"][0], g["c127"][1])
    pk = max(max(np.abs(g[s][0]).max(), np.abs(g[s][1]).max()) for s in g) / 32768
    return dict(lim=lim, clip=clip, nl=nl, lvl=lvl, pk=20 * math.log10(pk + 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--builds", default="one,c1,hq")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--presets", action="store_true")
    ap.add_argument("--make", action="store_true", help="build build/fxp/<build> first")
    ap.add_argument("-n", type=int, default=None)
    a = ap.parse_args()
    builds = a.builds.split(",")
    if a.make:
        make(builds)
    ok = gate(builds, a.quick, a.presets, a.n, verbose=True)
    return 0 if ok else 1


def gate(builds, quick=False, presets=False, n=None, verbose=False):
    effects = {k: v for k, v in EFFECTS.items() if not quick or k in ("chorus", "phaser", "trem", "delay-dig",
                                                                         "delay-tape-fb", "hall", "inf", "eq")}
    jobs = []
    for b in builds:
        jobs += jobs_for(b, "dry", CLEAN, 1)
        for name, (ccs, _, _) in effects.items():
            for d in DEPTHS:
                c = CLEAN + [(cc, d if v == D else v) for cc, v in ccs]
                jobs += jobs_for(b, f"{name}@{d}", c, 0)
        if presets:
            for pc in list(range(16)) + [32, 33, 34]:
                jobs += jobs_for(b, f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}", [], 0, pc)
                jobs += jobs_for(b, f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}", [], 1, pc)
    res = {}
    with Pool(n or int(os.environ.get("PYTEST_N", "2") or 2)) as pool:
        for job, (tag, L, R, c) in zip(jobs, pool.imap(render, jobs, chunksize=4)):
            res[(job[0], tag)] = (L, R, c)
    fails, rows = [], []
    for b in builds:
        dry = analyse(res, b, "dry", byp=True)
        if -1 in dry["lim"].values():
            rows.append(f"== {b}: no fx-clip counters in this build (older than 523d8fa7): lim / clip not measured")
        rows.append(f"== {b}: dry (bypass) chord {dry['lvl']:.2f} dBFS, peak {dry['pk']:.1f} dBFS, nl {dry['nl']:.1f} dBFS, "
                    f"limiter {dry['lim']}")
        rows.append(f"   {'effect':16s} {'d':>3s}  {'lim s64/s127/chord':>18s}  {'clip d/r/c':>10s}  {'nl':>6s}  "
                    f"{'lvl-byp':>7s}  {'peak':>5s}")
        for name, (ccs, exempt, mix0) in effects.items():
            for d in DEPTHS:
                r = analyse(res, b, f"{name}@{d}")
                dn, dl = r["nl"] - dry["nl"], r["lvl"] - dry["lvl"]
                bad = []
                if -1 in r["lim"].values():               # (a build without fx.c's fx-clip counters: not read as
                    bad.append("counters not measured")   # the limiter acting)
                elif any(r["lim"].values()):
                    bad.append("limiter")
                if any(r["clip"]):
                    bad.append("clip")
                if not exempt and r["nl"] > max(dry["nl"], NL_FLOOR) + NL_TOL:
                    bad.append("nl")
                if mix0 and d == 0 and abs(dl) > 0.5:
                    bad.append("lvl0")
                if r["pk"] > dry["pk"] + PK_TOL:
                    bad.append("peak")
                if name == "eq" and d == 127 or name.startswith("filter") and d == 127:
                    bad = [x for x in bad if x not in ("limiter", "peak")]   # (+15 dB on every band / RESO 127: a level
                    # the user set)
                rows.append(f"   {name:16s} {d:3d}  {r['lim']['s64']:5d}/{r['lim']['s127']:5d}/{r['lim']['c127']:5d}  "
                            f"{'%d/%d/%d' % r['clip']:>10s}  {r['nl']:6.1f}{'*' if exempt else ' '} {dl:+7.2f}  "
                            f"{r['pk']:5.1f}  {'FAIL ' + ','.join(bad) if bad else 'ok'}")
                if bad:
                    fails.append(f"{b} {name}@{d}: {','.join(bad)}")
        if presets:
            rows.append(f"   {'preset':16s}      {'lim s64/s127/chord':>18s}  {'clip d/r/c':>10s}  {'nl':>6s}  "
                        f"{'on-byp':>7s}  {'peak':>5s}")
            for pc in list(range(16)) + [32, 33, 34]:
                nm = f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}"
                r, o = analyse(res, b, nm), analyse(res, b, nm, byp=True)
                bad = []
                if -1 in r["lim"].values():
                    bad.append("counters not measured")
                elif any(r["lim"].values()):
                    bad.append("limiter")
                if any(r["clip"]):
                    bad.append("clip")
                rows.append(f"   {nm:16s}      {r['lim']['s64']:5d}/{r['lim']['s127']:5d}/{r['lim']['c127']:5d}  "
                            f"{'%d/%d/%d' % r['clip']:>10s}  {r['nl']:6.1f}  {r['lvl'] - o['lvl']:+7.2f}  "
                            f"{r['pk']:5.1f}  {'FAIL ' + ','.join(bad) if bad else 'ok'}")
                if bad:
                    fails.append(f"{b} {nm}: {','.join(bad)}")
    if verbose:
        print("\n".join(rows))
        print("FAILS:", len(fails))
        for f in fails:
            print("  " + f)
    gate.rows, gate.fails = rows, fails
    return not fails


if __name__ == "__main__":
    sys.exit(main())
