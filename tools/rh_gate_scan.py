#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes inaudible-mode gate's proof on the engine alone (rhodes.c, its Q15 output before the effects): every key
x velocity x TYPE x held / released / pedal, and the voicing knobs' ends on the gated keys, rendered with the gate and
with -DRH_NO_GATE (tests/c/rh_gate.c), compared with tools/engine_ab.py's metrics (max abs difference, the
residual's level and A-weighted level, the masking margin per critical band, loudness, band levels, where the sound
ends). Writes build/rh_gate/scan.json and prints the summary.

  tools/rh_gate_scan.py [--jobs 2] [--quick]
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from engine_ab import compare  # noqa: E402

OUT = ROOT / "build" / "rh_gate"
SCEN = {"held": (-1, 0), "release": (86, 0), "pedal": (86, 1)}       # (note-off block, pedal)
KNOBS = ({}, {"bark": 0}, {"bark": 127}, {"bell": 127}, {"tone": 0}, {"tone": 127}, {"ham": 0}, {"ham": 127},
         {"decay": 127}, {"rel": 127}, {"bark": 127, "bell": 127, "tone": 127}, {"bark": 0, "tone": 0})
GATED = range(29, 49)


def build(defs, name):
    OUT.mkdir(parents=True, exist_ok=True)
    exe = OUT / name
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", *defs, "-I" + str(ROOT / "firmware/src"),
                    "-I" + str(ROOT / "firmware/gen"), "-o", str(exe), str(ROOT / "tests/c/rh_gate.c")], check=True)
    return exe


def render(exe, note, vel, typ, scen, blocks=520, bark=64, bell=64, tone=64, ham=64, decay=64, rel=64):
    off, ped = SCEN[scen]
    p = subprocess.run([str(exe), "render", *map(str, (note, vel, typ, bark, bell, tone, ham, decay, rel, off, ped, blocks))],
                       capture_output=True, check=True)
    return p.stdout


def cases(quick):
    vels = (127, 64) if quick else (1, 20, 40, 64, 100, 127)
    for n in range(21, 109):
        for v in vels:
            for t in range(4):
                for s in SCEN:
                    yield dict(note=n, vel=v, typ=t, scen=s)
    for n in (GATED[::4] if quick else GATED):
        for t in range(4):
            for s in ("held", "pedal"):
                for kn in KNOBS[1:]:
                    yield dict(note=n, vel=127, typ=t, scen=s, **kn)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    on, off = build([], "rh_gate_on"), build(["-DRH_NO_GATE"], "rh_gate_off")

    def one(c):
        x, y = render(off, **c), render(on, **c)
        if x == y:
            return dict(c, identical=True)
        xa = np.frombuffer(x, dtype=np.int32).astype(float)
        ya = np.frombuffer(y, dtype=np.int32).astype(float)
        return dict(c, identical=False, peak=float(np.abs(xa).max()), **compare(xa, ya))

    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, cases(a.quick)))
    (OUT / "scan.json").write_text(json.dumps(rows))
    moved = [r for r in rows if not r["identical"]]
    print(f"{len(rows)} cases: {len(rows) - len(moved)} byte-identical, {len(moved)} moved "
          f"(keys {sorted(set(r['note'] for r in moved))})")
    outside = [r for r in moved if r["note"] not in GATED]
    print(f"moved outside the gated keys {GATED.start}..{GATED.stop - 1}: {len(outside)}")
    for title, sel in (("default knobs", [r for r in moved if not any(k in r for k in ("bark", "bell", "tone", "ham", "decay", "rel"))]),
                       ("knob ends", [r for r in moved if any(k in r for k in ("bark", "bell", "tone", "ham", "decay", "rel"))])):
        if not sel:
            continue
        print(f"{title}: {len(sel)} moved")
        for k, worse in (("max_lsb", max), ("res_pk_dbfs", max), ("res_pk_dba", max), ("res_dba", max),
                         ("mask_margin_db", max), ("unmasked_dbfs", max), ("lufs_diff", None), ("band_diff_db", max),
                         ("decay_diff_s", None)):
            r = max(sel, key=(lambda x: abs(x[k])) if worse is None else (lambda x: x[k]))
            desc = {kk: vv for kk, vv in r.items() if kk in ("note", "vel", "typ", "scen", "bark", "bell", "tone", "ham", "decay", "rel")}
            print(f"  worst {k:16s} {r[k]:9.3f}  {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
