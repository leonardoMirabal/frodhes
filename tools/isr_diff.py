#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The audio ISR's code, before and after: an address-normalised diff of two firmware disassemblies.

  tools/isr_diff.py BASE.dis NEW.dis [--show] [--root NAME ...]

A change that may not move the FM-1's sound or its CPU (an upstream merge, a refactor elsewhere) must leave the ISR's
object code as it was. This compares, function by function, everything the audio and timer interrupts can run: the
call tree from fm1_alnk0_irq, fm1_timer5_irq and their isr_* entries, plus every function named app_audio, eng_*,
rh_*, bar_*, fx_* (inlined helpers that still have a body). Addresses and instruction bytes are dropped; a reference
keeps only its symbol and the offset into it (`<rh_F+0x10>`, `<app_audio+0x4C>`), so code that moved but did not
change compares equal, while a global moved inside a merged block (the rh_F / rh_x spill trap) does not. A constant
that objdump labels as `_data_load+off` (the linker's load address of .data, nearest below most large numbers:
`r0 = 2147483647 <_data_load+0x7DF8890E ...>`) is a plain number, not a reference: it keeps the number.
Exit 0: identical; 1: a function differs, is missing or new (listed; --show prints the diffs).
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

LABEL = re.compile(r"^([A-Za-z_.$][\w.$]*):$")
INSN = re.compile(r"^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2} )+\s*\t(.*)$")
REF = re.compile(r"(-?\d+) <([^>:]+?)\s*:\s*[0-9a-f]+ >")    # "75632 <fm1_cstart : 20128c6 >" -> "<fm1_cstart>"
NUMBER_SYMS = ("_data_load",)                                # objdump's label for a plain constant (see above)
REF2 = re.compile(r"<([^>:]+?)\s*:\s*[0-9a-f]+ >")
CALL = re.compile(r"\bcall\s+(?:-?\d+\s+)?<([A-Za-z_.$][\w.$]*)(?:\+0x[0-9A-Fa-f]+)?")
ROOTS = ("fm1_alnk0_irq", "fm1_timer5_irq", "isr_alnk0", "isr_timer5", "app_audio")
PREFIX = ("app_audio", "eng_", "rh_", "bar_", "fx_")


def parse(path):
    """name -> list of normalised instruction lines"""
    funcs, cur = {}, None
    for line in Path(path).read_text(errors="replace").splitlines():
        m = LABEL.match(line)
        if m:
            cur = funcs.setdefault(m[1], [])
            continue
        m = INSN.match(line)
        if m and cur is not None:
            t = REF.sub(lambda r: r[1] if r[2].strip().split("+")[0] in NUMBER_SYMS else "<" + r[2].strip() + ">", m[1])
            t = REF2.sub(lambda r: "" if r[1].strip().split("+")[0] in NUMBER_SYMS else "<" + r[1].strip() + ">", t)
            t = re.sub(r"\.GJTIE\d+_", ".GJTIE#_", t)          # a jump table's label: numbered over the whole file
            cur.append(re.sub(r"\s+", " ", t).strip())
    return {k: v for k, v in funcs.items() if v}


def tree(funcs, roots):
    """the roots, the prefixed functions and everything they call"""
    todo = [r for r in roots if r in funcs] + [f for f in funcs if f.startswith(PREFIX)]
    seen = set()
    while todo:
        f = todo.pop()
        if f in seen or f not in funcs:
            continue
        seen.add(f)
        for ln in funcs[f]:
            m = CALL.search(ln)
            if m:
                todo.append(m[1])
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("new")
    ap.add_argument("--show", action="store_true", help="print the diff of every function that differs")
    ap.add_argument("--root", action="append", default=[], help="another root function")
    a = ap.parse_args()
    b, n = parse(a.base), parse(a.new)
    roots = (*ROOTS, *a.root)
    names = sorted(tree(b, roots) | tree(n, roots))
    bad = 0
    for f in names:
        if f not in b or f not in n:
            print(f"{'new' if f not in b else 'gone':8} {f}")
            bad += 1
        elif b[f] != n[f]:
            print(f"differs  {f} ({len(b[f])} -> {len(n[f])} instructions)")
            bad += 1
            if a.show:
                sys.stdout.writelines(x + "\n" for x in difflib.unified_diff(b[f], n[f], f"base/{f}", f"new/{f}", lineterm="", n=2))
    print(f"isr_diff: {len(names)} functions in the ISR call tree, {bad} differ"
          + ("" if bad else ": the ISR code is identical"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
