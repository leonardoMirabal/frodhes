#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/coverage.sh's summary of llvm-cov's export (text JSON): per file the lines, branches and functions covered;
every function never executed (file:line name); and per file the branches one way never taken (file:line, the
taken count of the side that ran), outside the never-run functions.

    tools/coverage_sum.py build/cov/export.json [--branches]"""
import json
import os
import sys


def main(argv):
    js = json.load(open(argv[0]))["data"][0]
    files = sorted(js["files"], key=lambda f: f["filename"])
    print(f"{'file':<22} {'lines':>15} {'branches':>15} {'functions':>12}")
    for f in files:
        s = f["summary"]

        def pc(k):
            c, n = s[k]["covered"], s[k]["count"]
            return f"{c}/{n} {100 * c / n:5.1f}%" if n else "-"
        print(f"{os.path.basename(f['filename']):<22} {pc('lines'):>15} {pc('branches'):>15} {pc('functions'):>12}")
    t = js["totals"]
    print(f"{'TOTAL':<22} {t['lines']['covered']}/{t['lines']['count']} lines, "
          f"{t['branches']['covered']}/{t['branches']['count']} branches, "
          f"{t['functions']['covered']}/{t['functions']['count']} functions")
    print("\n# functions never executed (file:line name)")
    dead = set()
    for fn in sorted(js["functions"], key=lambda f: (f["filenames"][0], f["regions"][0][0] if f["regions"] else 0)):
        if fn["count"] == 0:
            line = fn["regions"][0][0] if fn["regions"] else 0
            print(f"{os.path.basename(fn['filenames'][0])}:{line} {fn['name'].split(':')[-1]}")
            dead.add(fn["name"])
    if "--branches" in argv:
        print("\n# conditions with one side never taken (file:line:column count-true/count-false function), in functions"
              " that ran: a line with several conditions has a row for each (the column tells them apart)")
        for fn in js["functions"]:
            if fn["count"] == 0:
                continue
            for b in fn.get("branches", []):
                if (b[4] == 0) != (b[5] == 0):
                    print(f"{os.path.basename(fn['filenames'][0])}:{b[0]}:{b[1]} {b[4]}/{b[5]} {fn['name'].split(':')[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
