#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate H: the HQ Rhodes' renders (FR_HQ, the Move; private plan 06) pinned from committed code.

Every script in tests/hq/ is rendered by the host build at the Move's settings with HQ on for every note
(-DFR_VOICES=16u -DFR_HQ=1 -DHQ_DEFAULT=1, build/gate_h/hq) and its WAV's sha256 compared with
tests/baselines/renders-hq.sha256. The HQ code is float with no fused multiply-add and no reassociation, so the
sums are the same on the Mac (clang) and on Linux / the Move (gcc). --save writes the file (after a deliberate
change to the HQ sound, with the listening and the measurements that justify it).

    python3 tools/gate_h.py [--save]
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "tests/baselines/renders-hq.sha256"
OUT = ROOT / "build/gate_h"
DEFS = ["-DFR_VOICES=16u", "-DFR_HQ=1"]


def build(hq, path, extra=()):
    """the host build at the Move's settings, HQ on for every note (hq) or Classic; extra: more -D flags"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([os.environ.get("CC", "cc"), "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST",
                    '-DAPP_VER="dev"', *DEFS, f"-DHQ_DEFAULT={1 if hq else 0}", *extra, "-Ifirmware/src", "-Ifirmware/gen",
                    "-o", str(path), "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)
    return path


def render(binary, script, tag):
    out = OUT / tag
    p = subprocess.run([str(binary), str(out), str(script)], cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"Gate H: {script.name} failed ({p.returncode}): {p.stderr[-800:]}")
    return Path(str(out) + ".wav")


def sums(binary):
    return {s.stem: hashlib.sha256(render(binary, s, "hq-" + s.stem).read_bytes()).hexdigest()
            for s in sorted((ROOT / "tests/hq").glob("*.txt"))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    got = sums(build(True, OUT / "hq"))
    if a.save:
        BASE.write_text("# Gate H (tools/gate_h.py --save): the HQ Rhodes' renders of tests/hq/*.txt\n"
                        + "".join(f"{h}  {n}\n" for n, h in got.items()))
        print(f"Gate H: saved {len(got)} renders")
        return 0
    want = {ln.split()[1]: ln.split()[0] for ln in BASE.read_text().splitlines() if ln and not ln.startswith("#")}
    bad = sorted(n for n in set(got) | set(want) if got.get(n) != want.get(n))
    if bad:
        print("Gate H: differs: " + ", ".join(bad))
        return 1
    print(f"Gate H: {len(got)} of {len(got)} HQ renders equal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
