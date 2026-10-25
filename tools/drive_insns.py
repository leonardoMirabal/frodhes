#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Instructions per sample of the DRIVE's loops, compiled by the firmware's own compiler (pi32v2 clang -Os, build.py's
flags, through Docker as build.sh does): an estimate of the CPU before the FM-1 measures it (tools/hil_cpu.py decides).

  tools/drive_insns.py                       the shared kernels (tests/c/drive_kernels.c): curve, ADAA-1, biquad, ...,
                                             the framework's loops and each TYPE's (k_<type>: drv_<type>_run)
  tools/drive_insns.py -D 'DRV_FILE_KHLON="/work/tests/c/drv_demo.c"'   another file in a TYPE's place (/work: the repo)
  tools/drive_insns.py --src FILE.c FUNC ..  any file and functions (FILE.c may include fx.c, as drive_kernels.c does)
  tools/drive_insns.py --firmware FUNC ..    frodhes.c as the firmware builds it (e.g. fx_drive_classic, fx_drive_types)

Every loop of each function is split into its paths (the back edge cut); each path's instructions are counted (a
hardware divide counts 1, its latency is not known; a 64-bit multiply too). us per block ~ 1.6 x instructions per
sample (private FX-CPU-STUDY: the classic drive, 54 instructions, measured ~85 us).
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "drive_insns")
US_PER_INSN = 1.6
FLAGS = ["-target", "pi32v2", "-Os", "-ffunction-sections", "-fno-builtin", "-Wall", "-Wno-unused-function",
         "-Ifirmware/hal", "-Ifirmware/src", "-Ifirmware/gen", '-DFELUCCA_ID="FM-1_958"', '-DAPP_VER="0.0"']
KERNELS = ["k_curve", "k_adaa", "k_adaa_eps", "k_adaa_div64", "k_bq", "k_tpt", "drv_blend", "fx_drive_types", "drv_switch",
           "fx_drive_classic", "k_khlon", "k_tsh8", "k_ohcd", "k_bhlue", "drv_khlon_run", "drv_tsh8_run",
           "drv_ohcd_run", "drv_bhlue_run"]


def compile_s(src, out, defs=()):
    """src relative to the repo root -> out (.s); the toolchain in .local/jieli (Docker, as build.sh)"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tc = os.path.realpath(os.path.join(ROOT, ".local", "jieli", "toolchain"))
    cmd = ["docker", "run", "--rm", "--cpus=2", "--platform", "linux/amd64", "-v", f"{ROOT}:/work", "-v", f"{tc}:/opt/jieli:ro",
           "-w", "/work", "debian:bookworm-slim", "/opt/jieli/pi32v2/bin/clang", *FLAGS, *defs, "-S", src,
           "-o", "/work/" + os.path.relpath(out, ROOT)]
    subprocess.run(cmd, check=True)


def func_lines(asm, func):
    lines = asm.split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith(func + ":"))
    out = []
    for l in lines[i + 1:]:
        if l.startswith(".Lfunc_end"):
            break
        out.append(l)
    return out


def blocks_of(lines):
    blocks, order, cur = {"ENTRY": []}, ["ENTRY"], "ENTRY"
    for l in lines:
        m = re.match(r"^(\.LBB\d+_\d+):", l) or re.match(r"^; (BB#\d+):", l)
        if m:
            cur = m.group(1); blocks[cur] = []; order.append(cur)
            continue
        t = l.split(";")[0].strip()
        if t and not t.startswith("."):
            blocks[cur].append(t)
    return blocks, order


def succ(blocks, order, b):
    ins, out = blocks[b], []
    for t in ins:
        m = re.search(r"goto (\.LBB\d+_\d+)", t)
        if m:
            out.append(m.group(1))
    last = ins[-1] if ins else ""
    if not (last.startswith("goto ") or last.startswith("rts") or last.startswith("{pc") or "pc}" in last):
        k = order.index(b)
        if k + 1 < len(order):
            out.append(order[k + 1])
    return out


def loop_paths(lines, limit=64):
    """[(header, [(insns, divides), ...])] for every back edge (a goto to an earlier block)"""
    blocks, order = blocks_of(lines)
    pos = {b: k for k, b in enumerate(order)}
    res = []
    for b in order:
        for s in succ(blocks, order, b):
            if s in pos and pos[s] <= pos[b]:
                head, latch, paths = s, b, []

                def walk(x, acc, dv, seen):
                    if len(paths) > limit:
                        return
                    n = len(blocks[x])
                    d = sum(1 for t in blocks[x] if re.search(r"= r[\d_r]+ / r\d+", t))
                    if x == latch:
                        paths.append((acc + n, dv + d)); return
                    for y in succ(blocks, order, x):
                        if y == head or y in seen or y not in pos or pos[y] < pos[head] or pos[y] > pos[latch]:
                            continue
                        walk(y, acc + n, dv + d, seen | {y})
                walk(head, 0, 0, {head})
                res.append((head, sorted(set(paths))))
    return res


def report(asm, funcs):
    rows = []
    for f in funcs:
        try:
            lps = loop_paths(func_lines(asm, f))
        except StopIteration:
            rows.append(f"{f}: not in the assembly (inlined?)"); continue
        if not lps:
            rows.append(f"{f}: no loop"); continue
        for head, paths in lps:
            ns = [n for n, _ in paths]
            if not ns:
                rows.append(f"{f} {head}: no path through the loop found (read the assembly)"); continue
            dv = max((d for _, d in paths), default=0)
            rows.append(f"{f} {head}: {min(ns)}..{max(ns)} insns per pass ({len(paths)} paths, up to {dv} divide(s));"
                        f" ~{US_PER_INSN * max(ns):.0f} us per block if per sample")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", nargs="+", help="FILE.c FUNC ...")
    ap.add_argument("--firmware", nargs="+", help="FUNC ...: frodhes.c as built")
    ap.add_argument("-D", action="append", default=[], help="a define (e.g. -D FOO=1)")
    a = ap.parse_args()
    defs = [f"-D{d}" for d in a.D]
    if a.firmware:
        src, funcs = "firmware/src/frodhes.c", a.firmware
    elif a.src:
        src, funcs = a.src[0], a.src[1:]
    else:
        src, funcs = "tests/c/drive_kernels.c", KERNELS
    out = os.path.join(OUT, os.path.splitext(os.path.basename(src))[0] + ".s")
    compile_s(src, out, defs)
    print(report(open(out).read(), funcs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
