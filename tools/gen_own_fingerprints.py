#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""plugin/own_fingerprints.txt: normalized-line hashes of the third-party blocks of the plugin's audit, read from a
git revision (default: the audit's, origin/main 9fc79da), one "group hash" per line. Only hashes are stored, never text. A line is
dropped when it is short or generic (tools/own_check.py significant()) or when it also occurs elsewhere in the
revision's own code (the other lines of the same files, plugin/), so an idiom Leo's code repeats is not a
fingerprint.

    tools/gen_own_fingerprints.py [--rev REV] [--check]

The list below names the blocks at the audit's revision (origin/main 9fc79da); a path alone means the whole file."""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import own_check as oc  # noqa: E402

REV = "9fc79da34"                      # origin/main at the audit (firmware identical to v0.9.13)
OUT = os.path.join(ROOT, oc.FINGERPRINTS)
UI_C = [(331, 456), (655, 656), (747, 749), (783, 783), (853, 862), (1416, 1416), (1545, 1545), (1579, 1579),
        (1584, 1585), (1900, 1901), (1978, 2016)]
FRODHES_C = [(1684, 1685), (1690, 1691), (1784, 1784), (1790, 1790), (1793, 1793), (2059, 2059), (2114, 2114)]
WEB_HAL = [(33, 34), (44, 46), (51, 67), (126, 143), (149, 174)]
# (group, path, [(first line, last line)] or None for the whole file)
BLOCKS = [
    ("dinghy", "firmware/src/platform.c", [(1, 55)]),
    ("dinghy", "web/web_hal.c", WEB_HAL),
    ("felucca-gfx", "firmware/src/gfx.c", None),
    ("felucca-gfx", "firmware/src/aatext.c", None),
    ("store", "firmware/src/store.h", None),
    ("store", "firmware/hal/fm1_flash_map.h", None),
    ("store", "firmware/hal/fm1_cc.h", None),
    ("keyvel", "firmware/src/keyvel.c", None),
    ("keyvel", "firmware/src/keyvel.h", None),
    ("tony", "firmware/src/ui.c", UI_C),
    ("tony", "firmware/src/frodhes.c", FRODHES_C),
    ("tony", "firmware/src/params.c", [(198, 200)]),
    ("pr3", "firmware/src/engine.c", [(113, 117)]),
    ("pr3", "firmware/src/tempo.c", [(396, 402)]),
    ("pr3", "firmware/src/rhodes.c", [(2058, 2097)]),
    ("fdrv", "firmware/src/fx.c", [(632, 911)]),            # the Felucca DRIVE kernel and its wrappers (part 3)
    ("felucca-delay", "firmware/src/fx.c", [(2116, 2180)]),
    ("felucca-dc", "firmware/src/fx.c", [(2813, 2819)]),
    ("felucca-hall", "firmware/src/fx.c", [(2500, 2712)]),
]


def show(rev, path):
    r = subprocess.run(["git", "show", "%s:%s" % (rev, path)], cwd=ROOT, capture_output=True)
    return r.stdout.decode(errors="replace") if r.returncode == 0 else None


def own_lines(rev, masked):
    """the normalized lines of the revision's own code, outside the fingerprinted blocks"""
    names = subprocess.run(["git", "ls-tree", "-r", "--name-only", rev, "firmware/src", "plugin"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
    seen = set()
    for p in names:
        if not p.endswith((".c", ".h", ".m", ".cpp")):
            continue
        t = show(rev, p)
        if t is None:
            continue
        for i, l in enumerate(oc.strip_comments(t).splitlines(), 1):
            if (p, i) not in masked:
                seen.add(oc.norm(l))
    return seen


def build(rev):
    masked, blocks = set(), []
    for group, path, ranges in BLOCKS:
        t = show(rev, path)
        if t is None:
            raise SystemExit("gen_own_fingerprints: %s:%s not found" % (rev, path))
        ls = oc.strip_comments(t).splitlines()
        sel = range(1, len(ls) + 1) if ranges is None else [i for a, b in ranges for i in range(a, min(b, len(ls)) + 1)]
        for i in sel:
            masked.add((path, i))
            blocks.append((group, oc.norm(ls[i - 1])))
    own = own_lines(rev, masked)
    out = set()
    for group, n in blocks:
        if oc.significant(n) and n not in own:
            out.add((group, oc.fp(n)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rev", default=REV)
    ap.add_argument("--check", action="store_true", help="fail when the file differs from the revision's")
    a = ap.parse_args()
    rows = build(a.rev)
    text = ("# SPDX-License-Identifier: GPL-3.0-only\n"
            "# group hash: normalized-line hashes of the third-party blocks of %s (tools/gen_own_fingerprints.py;\n"
            "# tools/own_check.py reads it). Hashes only: no text.\n" % a.rev
            + "".join("%s %s\n" % r for r in rows))
    if a.check:
        if not os.path.exists(OUT) or open(OUT).read() != text:
            print("%s is out of date: tools/gen_own_fingerprints.py" % oc.FINGERPRINTS)
            return 1
        return 0
    open(OUT, "w").write(text)
    groups = {}
    for g, _ in rows:
        groups[g] = groups.get(g, 0) + 1
    print("%s: %d fingerprints %s" % (oc.FINGERPRINTS, len(rows), groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
