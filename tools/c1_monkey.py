#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The two-core monkey: tools/monkey.py's seeded random streams (notes, the pedal, every CC, Program Change, the drive
TYPEs, the panel; then its panic and probes) rendered by the two-core host build (FR_C1: 14 voices, the full-rate
reverb, c1_host.h's twin) and by the one-core build with the same voices and reverb (-DFR_VOICES=14u -DFR_REV_HQ=1),
compared sample for sample. The two-core build runs every effect in C1_SUB chunks with no added latency, so the two
renders must be equal except where an effect reads the block's future (frodhes.c / fx.c FR_C1: COMP switched on from
rest, KHLON's mode-change dip gain): each differing stretch is listed with its size and the events just before it.

    python3 tools/c1_monkey.py [--seeds A-B] [-j 2] [--keep] [--json FILE]

Each seed's two-core run takes a hand-off order (FRODHES_C1 order=0 / 1, by seed) and every third seed refused jobs
(refuse=7): the samples must not depend on either. Exit 1 when any stretch differs that no known estimate explains
(--strict: any stretch at all)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monkey  # noqa: E402

ROOT = monkey.ROOT
FS = monkey.FS
C1 = os.path.join(ROOT, "build", "host-c1m", "frodhes")
REF = os.path.join(ROOT, "build", "host-c1mref", "frodhes")
_FXUS = __import__("re").search(r"#define FR_C1_FX_US (\d+)u", open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read())
# the one-core reference with the two-core build's bar cost model (bars.c FX_COST_C0: core 0's share of the effects,
# FR_C1_FX_US, while core 1 runs them): else VIBES / MARIMBA keep fewer upper modes under heavy effects, by design
SUB = os.environ.get("C1M_SUB")                  # (C1M_SUB=n: the two-core build at C1_SUB n, a probe)
if SUB:
    C1 = C1 + f"-sub{SUB}"
DEFS = {C1: ["-DFR_C1=1"] + ([f"-DC1_SUB={SUB}u"] if SUB else []), REF: ["-DFR_VOICES=14u", "-DFR_REV_HQ=1", f"-DFX_COST_C0()={_FXUS.group(1)}u"]}
GAP_MS = 50                        # differing frames closer than this are one stretch
LOOK_MS = 300                      # the events listed before a stretch
CMP_CC = {106, 107, 108, 109}      # the compressor (switch, amount, ...): COMP on from rest
DRV_CC = {81, 84}                  # the drive (AMOUNT, TYPE): KHLON's dip (AMOUNT across 125 / 126)


ORACLE = os.environ.get("C1M_ORACLE") == "1"
# C1M_ORACLE=1: the two-core build at one chunk a block (C1_SUB 1) with its two estimates put back to the one-core
# formulas (at one chunk the first chunk is the whole block, so they are exact there): COMP's detector from this
# block's peak alone, KHLON's dip gain from the dip's block alone. That build must equal the one-core reference on every
# seed: whatever else differs is a real difference of the two-core path (run with --strict). Built from a patched copy
# of the sources (build/host-c1m-oracle/src), the tree untouched.
ORACLE_EDITS = [("fx.c", "int32_t pk = cmp_pkb > cmp_pkc ? cmp_pkb : cmp_pkc;", "int32_t pk = cmp_pkc;"),
                ("drv_khlon.c", "kh_dg_of(pw + cw, pd + cd);", "kh_dg_of(cw, cd);")]
# C1M_ORACLE=8: the two-core build at its own C1_SUB (8) against the reference, both with COMP's start from rest taken
# from the block before's peak alone (a rule both can follow exactly at any chunk size): with that one estimate
# made the same, every stretch left must follow a KHLON mode change (its dip's dry gain; under BYPASS its crossfade
# waits and shows the difference when BYPASS is let go): any other is a real difference and fails
ORACLE8_C1 = [("fx.c", "int32_t pk = cmp_pkb > cmp_pkc ? cmp_pkb : cmp_pkc;", "int32_t pk = cmp_pkb;")]
ORACLE8_REF = [("fx.c", "#else\n    if (!on && !cmp_xw) { fx_cmp_gr = 0; return; }\n",
                "#else\n    static int32_t pkb_, pkc_;\n    { int32_t pk_ = 0; uint32_t q_; for (q_ = 0; q_ < n; q_++) "
                "if (iabs(m[q_]) > pk_) pk_ = iabs(m[q_]); pkb_ = pkc_; pkc_ = pk_; }\n"
                "    if (!on && !cmp_xw) { fx_cmp_gr = 0; return; }\n"),
               ("fx.c", "cmp_from(m, n, mk, thr, slope);",
                "{ cmp_env = pkb_ << 8; cmp_g = exp2_q16(mk - cmp_gr_at(cmp_env, thr, slope)); }")]
ORACLE8 = os.environ.get("C1M_ORACLE") == "8"
PATCH = {}
if ORACLE:
    C1 = os.path.join(ROOT, "build", "host-c1m-oracle", "frodhes")
    DEFS[C1] = ["-DFR_C1=1", "-DC1_SUB=1u"]
    PATCH[C1] = ORACLE_EDITS
elif ORACLE8:
    C1, REF = (os.path.join(ROOT, "build", f"host-c1m-oracle8{t}", "frodhes") for t in ("", "ref"))
    DEFS[C1], DEFS[REF] = ["-DFR_C1=1"], DEFS[os.path.join(ROOT, "build", "host-c1mref", "frodhes")]
    PATCH[C1], PATCH[REF] = ORACLE8_C1, ORACLE8_REF


def build(binary):
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    src = os.path.join(ROOT, "firmware", "src")
    newest = max(os.path.getmtime(os.path.join(d, f)) for d in (src, os.path.join(ROOT, "tests"))
                 for f in os.listdir(d) if f.endswith((".c", ".h")))
    if os.path.exists(binary) and os.path.getmtime(binary) >= newest:
        return
    tmp = f"{binary}.tmp{os.getpid()}"
    if binary in PATCH:                          # (the patched copy: the tree's layout kept for the relative includes)
        m = os.path.join(os.path.dirname(binary), "src")
        shutil.rmtree(m, ignore_errors=True)
        shutil.copytree(src, os.path.join(m, "firmware", "src"))
        for d in ("firmware/hal", "tests", "web"):
            os.symlink(os.path.join(ROOT, d), os.path.join(m, d))
        for f, old, new in PATCH[binary]:
            fp = os.path.join(m, "firmware", "src", f)
            txt = open(fp).read()
            assert txt.count(old) == 1, (f, old)
            open(fp, "w").write(txt.replace(old, new))
        src = os.path.join(m, "firmware", "src")
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', *DEFS[binary],
                    "-I" + src, "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", tmp,
                    os.path.join(src, "frodhes.c"), "-lm"], check=True)
    os.replace(tmp, binary)


def stream(seed):
    ev = monkey.gen(seed)
    pl, mk = monkey.panic(monkey.RAND_MS + 50)
    return ev, monkey.script_text(ev, pl, mk["end"])


def c1_env(seed):
    e = f"order={seed % 2}"
    return e + (",refuse=7" if seed % 3 == 0 else "")


def render(text, binary, env):
    old = os.environ.get("FRODHES_C1")
    try:
        if env:
            os.environ["FRODHES_C1"] = env
        else:
            os.environ.pop("FRODHES_C1", None)
        return monkey.run(text, binary)
    finally:
        if old is None:
            os.environ.pop("FRODHES_C1", None)
        else:
            os.environ["FRODHES_C1"] = old


def stretches(a, b):
    n = min(len(a), len(b))
    d = np.flatnonzero(np.any(a[:n] != b[:n], axis=1))
    out = []
    if d.size == 0:
        return out
    gap = GAP_MS * FS // 1000
    cut = np.flatnonzero(np.diff(d) > gap)
    s = 0
    for e in list(cut) + [d.size - 1]:
        f0, f1 = int(d[s]), int(d[e])
        seg = np.abs(a[f0:f1 + 1] - b[f0:f1 + 1])
        ref = np.abs(b[f0:f1 + 1]).max()
        out.append(dict(t0=round(f0 * 1000 / FS, 1), t1=round(f1 * 1000 / FS, 1), frames=int(e - s + 1),
                        maxdiff=int(seg.max()), ref_peak=int(ref)))
        s = e + 1
    return out


def near(events, t0):
    """the events in the LOOK_MS before t0 ms, and what they may explain"""
    ls = [(t, l) for t, l in events if t0 - LOOK_MS <= t <= t0 + 1]
    why = set()
    for t, l in ls:
        w = l.split()
        if len(w) >= 4 and w[0] == "midi" and (int(w[1], 16) & 0xF0) == 0xB0:
            c = int(w[2])
            if c in CMP_CC:
                why.add("comp")
            if c in DRV_CC:
                why.add("drive")
        elif len(w) >= 2 and w[0] == "midi" and (int(w[1], 16) & 0xF0) == 0xC0:
            why.add("preset")
        elif w and w[0] in ("turn", "btn", "release", "hold"):
            why.add("panel")
    return ls, sorted(why)


P_IX = {n: i for i, n in enumerate(monkey.PNAME)}
DUMP_MS, DUMP_BACK = 10, 600       # the reference's state every 10 ms; a cause up to 600 ms before a stretch


def causes(ev, st):
    """the known estimates, from the reference's own state every DUMP_MS ms up to the last stretch (whatever set it: a
    CC, Program Change, the panel): "comp" the compressor on from off, or BYPASS let go with it on (frodhes.c FR_C1:
    its detector from the block before and the first chunk), "khlon" KHLON's mode change (AMOUNT across 125 / 126: the
    dip's dry gain). A stretch with one in the DUMP_BACK ms before it is put down to it; one with an estimate earlier
    in the stream only, "residue": the same divergence carried on (the compressor's detector and gain, the delay and
    reverb tails; under BYPASS the effects' crossfades wait, then finish, from the state the estimate left)"""
    end = int(max(s["t0"] for s in st)) + 3 * DUMP_MS
    ts = list(range(DUMP_MS, end + 1, DUMP_MS))
    pl, mk = monkey.panic(monkey.RAND_MS + 50)
    r = monkey.run(monkey.script_text(ev, pl + [(t, "dump") for t in ts], mk["end"]), REF)
    cs, dr, sw = (f"p.{P_IX[k]}" for k in ("P_CMP_SW", "P_DRIVE", "P_DRV_SW"))
    marks, prev = [], None             # (t, "comp" / "khlon")
    for t in ts:
        d = r.dumps.get(t)
        if not d:
            continue
        cur = (d.get(cs, "0") != "0", d.get(sw), int(d.get(dr, "0")) >= 126, d.get("byp", "0") != "0")
        if prev:
            if cur[0] and (not prev[0] or (prev[3] and not cur[3])):
                marks.append((t, "comp"))
            if "2" in (cur[1], prev[1]) and cur[2] != prev[2]:
                marks.append((t, "khlon"))
        prev = cur
    for s in st:
        t0 = s["t0"]
        why = {k for t, k in marks if t0 - DUMP_BACK <= t <= t0 + 3 * DUMP_MS}
        s["prior"] = sorted({k for t, k in marks if t <= t0 + 3 * DUMP_MS})   # every estimate before it
        if not why and s["prior"]:
            why = {"residue"}
        s["cause"] = sorted(why)


def one(seed, keep=False):
    ev, text = stream(seed)
    env = c1_env(seed)
    a = render(text, C1, env)
    b = render(text, REF, None)
    res = dict(seed=seed, env=env, code=(a.code, b.code), frames=(len(a.x), len(b.x)))
    st = stretches(a.x, b.x)
    for s in st:
        ls, why = near(ev, s["t0"])
        s["why"] = why
        s["events"] = [f"{t} {l}" for t, l in ls][-12:]
    if st:
        causes(ev, st)
    res["stretches"] = st
    if keep and st:
        d = os.path.join(monkey.OUTD, f"c1-{seed}")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "stream.txt"), "w").write(text)
    return res


def shrink(seed, budget=600):
    """a minimal script on which the two builds still differ (monkey.shrink: the tail cut, then ddmin), the stream
    alone (no panic), rendered to its last event + 2 s; written to build/monkey/c1-<seed>/repro.txt"""
    ev, _ = stream(seed)
    env = c1_env(seed)

    def differs(e):
        end = max(t for t, _ in e) + 2000
        text = monkey.script_text(e, (), end)
        a, b = render(text, C1, env), render(text, REF, None)
        return bool(stretches(a.x, b.x))
    m = monkey.shrink(ev, differs, budget)
    d = os.path.join(monkey.OUTD, f"c1-{seed}")
    os.makedirs(d, exist_ok=True)
    text = monkey.script_text(m, (), max(t for t, _ in m) + 2000)
    open(os.path.join(d, "repro.txt"), "w").write(text)
    a, b = render(text, C1, env), render(text, REF, None)
    print(f"seed {seed} ({env}): {len(m)} events, {d}/repro.txt")
    print(text)
    for s in stretches(a.x, b.x):
        print(f"  {s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", default="1-20", help="A-B, or a list: 3,17,40-42")
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--keep", action="store_true", help="keep each differing seed's stream (build/monkey/c1-<seed>)")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--strict", action="store_true", help="fail on any difference, the known estimates too")
    ap.add_argument("--shrink", type=int, metavar="SEED", help="shrink one differing seed to a minimal script")
    a = ap.parse_args()
    if a.shrink is not None:
        for b in (C1, REF):
            build(b)
        shrink(a.shrink)
        return 0
    seeds = []
    for part in a.seeds.split(","):                  # A-B, or a list: 3,17,40-42
        lo, _, hi = part.partition("-")
        seeds += range(int(lo), int(hi or lo) + 1)
    for b in (C1, REF):
        build(b)
    with ProcessPoolExecutor(a.j) as ex:              # (processes: each run sets its own FRODHES_C1)
        rs = list(ex.map(one, seeds, [a.keep] * len(seeds)))
    bad = 0
    for r in rs:
        st = r["stretches"]
        crash = r["code"] != (0, 0) or r["frames"][0] != r["frames"][1]
        tag = "CRASH" if crash else "same " if not st else "diff "
        print(f"{tag} seed {r['seed']:5d} ({r['env']}): {len(st)} stretch(es)")
        for s in st:
            print(f"      {s['t0']:9.1f}-{s['t1']:9.1f} ms  {s['frames']:6d} frames  max |d| {s['maxdiff']:6d} "
                  f"(peak {s['ref_peak']})  cause: {','.join(s['cause']) or 'UNEXPLAINED'}  near: {','.join(s['why']) or '-'}")
            for e in s["events"][-6:]:
                print(f"            {e}")
        if ORACLE8:                    # (COMP's estimate made the same: only KHLON's may be behind a stretch)
            unexp = [s for s in st if "khlon" not in s["prior"]]
        else:
            unexp = [s for s in st if a.strict or not s["cause"]]
        if crash or unexp:
            bad += 1
    n = sum(1 for r in rs if not r["stretches"])
    print(f"{n} of {len(rs)} seeds sample for sample equal; {bad} unexplained")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rs, f, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
