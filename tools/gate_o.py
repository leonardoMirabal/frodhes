#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate O: the plugin's own-code build switch (FR_OWN) changes no sound but its own drive's.

Builds the host app twice at the plugin's switches (tools/gate_d.py DEFS), once with -DFR_OWN=0 and once with
-DFR_OWN=1, renders every Gate D hq entry (the Gate R MIDI list and tests/hq/*.txt) with both, and fails on any
byte difference outside the expected list: the renders whose DRIVE runs TYPE 1 (the own-code build's TYPE 1 is its
own drive, drive.c drv_own1.c), GATE_O_EXPECT in tests/py/test_drive_own.py.

The list comes from --probe: the FR_OWN=0 host build with -DFR_DRV1_PROBE (drive.c: TYPE 1 wet or fading on a block
that is not silent) renders every entry and names the ones where that happened.

  tools/gate_o.py [--jobs 2] [--quick]     (--quick: tests/hq and a subset of 12)
  tools/gate_o.py --probe [--check]        the expected list (--check: fail unless it is GATE_O_EXPECT)
Light by default: --jobs 2, every process under nice -n 15 (gate_d.run).
"""
import argparse
import ast
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate_d  # noqa: E402
import gate_m  # noqa: E402


TEST = ROOT / "tests" / "py" / "test_drive_own.py"


def expected():
    """GATE_O_EXPECT from tests/py/test_drive_own.py (read as a literal: the test module is not imported)"""
    for node in ast.parse(TEST.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "GATE_O_EXPECT" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise SystemExit("gate_o: no GATE_O_EXPECT in " + str(TEST))


def all_entries(quick=False):
    midi, _ = gate_m.entries()
    hq = [(f"hq/{p.stem}", p.relative_to(ROOT)) for p in sorted((ROOT / "tests" / "hq").glob("*.txt"))]
    return hq + (gate_d.subset(midi, 12) if quick else midi)


def probe(entries, jobs):
    """the names of the entries whose DRIVE runs TYPE 1 (the FR_OWN=0 build with -DFR_DRV1_PROBE)"""
    base = [d for d in gate_d.DEFS if not d.startswith("-DFR_OWN=")]
    exe = gate_d.host_build("own0probe", ["-DHQ_DEFAULT=1", "-DFR_DRV1_PROBE"], defs=base + ["-DFR_OWN=0"])
    hits = []

    def one(e):
        n, s = e
        out = gate_d.OUT / "host" / exe.name / n.replace("/", "_")
        out.parent.mkdir(parents=True, exist_ok=True)
        r = gate_d.run([exe, out, s])
        if "FR_DRV1_PROBE used" in r.stderr:
            hits.append(n)

    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(one, entries))
    return sorted(hits)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--quick", action="store_true", help="tests/hq and a subset of 12")
    ap.add_argument("--probe", action="store_true", help="print the expected list (the renders that drive TYPE 1)")
    ap.add_argument("--check", action="store_true", help="with --probe: fail unless it is GATE_O_EXPECT")
    a = ap.parse_args()
    entries = all_entries(a.quick)
    if a.probe:
        hits = probe(entries, a.jobs)
        print("GATE_O_EXPECT = [")
        for h in hits:
            print(f"    {h!r},")
        print("]")
        if a.check:
            want = expected() & {n for n, _ in entries}
            ok = set(hits) == want
            print(f"Gate O probe: {'PASS' if ok else 'FAIL'} ({len(hits)} renders drive TYPE 1; GATE_O_EXPECT "
                  f"{'matches' if ok else 'differs: +' + str(sorted(set(hits) - want)) + ' -' + str(sorted(want - set(hits)))})")
            return 0 if ok else 1
        return 0
    expect = expected()
    base = [d for d in gate_d.DEFS if not d.startswith("-DFR_OWN=")]
    bins = {v: gate_d.host_build(f"own{v}", ["-DHQ_DEFAULT=1"], defs=base + [f"-DFR_OWN={v}"]) for v in (0, 1)}
    fails, moved, same = [], [], []

    def one(e):
        n, s = e
        r = [gate_d.host_render(bins[v], n.replace("/", "_"), s) for v in (0, 1)]
        eq = r[0].exists() and r[1].exists() and r[0].read_bytes() == r[1].read_bytes()
        if n in expect:
            (same if eq else moved).append(n)
        elif not eq:
            fails.append(n)
            print(f"DIFF  {n}: FR_OWN=0 {gate_d.sha(r[0])[:12]}, FR_OWN=1 {gate_d.sha(r[1])[:12]}", flush=True)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(one, entries))
    for n in sorted(same):
        print(f"note  {n}: on the expected list (DRIVE on TYPE 1) and identical", flush=True)
    n_exp = len(moved) + len(same)
    print(f"Gate O: {'PASS' if not fails else 'FAIL'} ({len(entries) - n_exp - len(fails)} of {len(entries) - n_exp} "
          f"renders identical, FR_OWN=0 against FR_OWN=1; {n_exp} on the expected list (DRIVE on TYPE 1), "
          f"{len(moved)} of them moved)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
