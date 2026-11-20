#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate O: the plugin's own-code build switch (FR_OWN) alone changes no sound.

Builds the host app twice at the plugin's switches (tools/gate_d.py DEFS), once with -DFR_OWN=0 and once with
-DFR_OWN=1, renders every Gate D hq entry (the Gate R MIDI list and tests/hq/*.txt) with both, and fails on any
byte difference.

  tools/gate_o.py [--jobs 2] [--quick]     (--quick: tests/hq and a subset of 12)
Light by default: --jobs 2, every process under nice -n 15 (gate_d.run).
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate_d  # noqa: E402
import gate_m  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--quick", action="store_true", help="tests/hq and a subset of 12")
    a = ap.parse_args()
    base = [d for d in gate_d.DEFS if not d.startswith("-DFR_OWN=")]
    bins = {v: gate_d.host_build(f"own{v}", ["-DHQ_DEFAULT=1"], defs=base + [f"-DFR_OWN={v}"]) for v in (0, 1)}
    midi, _ = gate_m.entries()
    hq = [(f"hq/{p.stem}", p.relative_to(ROOT)) for p in sorted((ROOT / "tests" / "hq").glob("*.txt"))]
    entries = hq + (gate_d.subset(midi, 12) if a.quick else midi)
    fails = []

    def one(e):
        n, s = e
        r = [gate_d.host_render(bins[v], n.replace("/", "_"), s) for v in (0, 1)]
        if not (r[0].exists() and r[1].exists()) or r[0].read_bytes() != r[1].read_bytes():
            fails.append(n)
            print(f"DIFF  {n}: FR_OWN=0 {gate_d.sha(r[0])[:12]}, FR_OWN=1 {gate_d.sha(r[1])[:12]}", flush=True)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(one, entries))
    print(f"Gate O: {'PASS' if not fails else 'FAIL'} ({len(entries) - len(fails)} of {len(entries)} renders "
          f"identical, FR_OWN=0 against FR_OWN=1)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
