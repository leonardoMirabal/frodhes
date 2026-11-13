#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC port's CPU at its worst case (docs/MPC.md), one instance. The desktop plugin's worst case
(tools/plugin_cpu.py, hil_cpu.py --worst's) at the MPC's 16 voices: every effect on at its heaviest (DRIVE, the EQ,
the compressor, the wah, the phaser, the chorus, the delay at its longest with feedback, the reverb at full mix, the
filter LP18 with its LFO) and the strike at its edge (TONE, BARK, BELL, HAMMER 127); 16 keys at velocity 127 with
the sustain pedal down, struck again every 2 s (the strike is the costliest block); each REVERB TYPE; each TYPE (the
four Rhodes, VIBES with its motor, MARIMBA); at the MPC's 44.1 kHz.

  python3 mpc/tests/cpu.py [--where native|arm64] [--secs 20] [--block 128] [--quick]

native: build/mpc/native (this machine's CPU); arm64: the shipped frodhes-arm64.so on Linux in Docker
(mpc/tests/arm_run.sh: on an arm64 Mac the same cores, so it checks the Linux build's cost, not an MPC's). An
arm32 time under QEMU means nothing and is not measured. --quick: the first reverb type only. Prints per case the
share of real time (the instance's average load on one core) and its calls' p99.9 and worst against the block's
time (the worst single call is this machine's scheduling noise as much as the plugin: Docker's above all).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "mpc" / "cpu"
CC = dict(DRIVE=81, TW_SW=17, TW_DEPTH=18, CP_SW=85, CP_DEPTH=86, DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26,
          REV=91, PH_SW=12, PH_DEPTH=13, PH_FDBK=16, CMP_SW=106, CMP_AMT=107, FL_TYPE=70, FL_FREQ=74, FL_RESO=71,
          FL_LFO=72, TONE=28, BARK=29, BELL=30, HAMMER=31, RV_TYPE=95, EQ_LO=34, EQ_MID=35, EQ_HI=37)
HEAVY = dict(DRIVE=127, EQ_LO=127, EQ_MID=0, EQ_HI=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127,
             PH_DEPTH=127, PH_FDBK=127, CP_SW=64, CP_DEPTH=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127,
             REV=127, FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64, TONE=127, BARK=127, BELL=127, HAMMER=127)
RHODES16 = list(range(40, 54)) + [105, 108]      # (plugin_cpu.py's STAGE 77 voicing, mid + top, at 16)
BARS16 = [36 + 2 * k for k in range(16)]
ENGINES = {                                      # (the TYPE's CCs, the 16 keys): tools/plugin_cpu.py ENGINES
    "STAGE 77": ([(80, 0)], RHODES16), "SUITCASE": ([(80, 32)], RHODES16), "EARLY FELT": ([(80, 64)], RHODES16),
    "BELL DYNO": ([(80, 96)], RHODES16), "VIBES": ([(79, 0)], BARS16), "MARIMBA": ([(79, 127)], BARS16)}
RV_TYPES = {"OFF": 0, "HALL": 43, "PLATE": 85, "INFINITE": 127}   # CC 95 across its range (the texts: params_map.h)


def script(engine, rv, secs):
    tcc, keys = ENGINES[engine]
    ev = [(20, 0xB0, c, v) for c, v in tcc]
    ev += [(30, 0xB0, CC[k], v) for k, v in HEAVY.items()]
    ev += [(30, 0xB0, CC["RV_TYPE"], rv), (40, 0xB0, 64, 127)]
    t = 200
    while t + 2000 <= secs * 1000 - 100:
        ev += [(t, 0x90, k, 127) for k in keys]
        ev += [(t + 1900, 0x80, k, 0) for k in keys]   # (the pedal holds them: struck again at t + 2000)
        t += 2000
    return "".join(f"{ms} midi {st:#x} {d1} {d2}\n" for ms, st, d1, d2 in ev) + f"{secs * 1000} end\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--where", default="native", choices=["native", "arm64"])
    ap.add_argument("--secs", type=int, default=20)
    ap.add_argument("--block", type=int, default=128)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    ext = "dylib" if sys.platform == "darwin" else "so"
    if a.where == "native":
        tool, lib, pre = ROOT / "build/mpc/native/vst2_render", ROOT / f"build/mpc/native/frodhes.{ext}", []
    else:
        tool = ROOT / "build/mpc/stage/arm64/vst2_render"
        lib = ROOT / "build/mpc/Frodhes - VST - Frodhes/VST/frodhes-arm64.so"
        pre = ["sh", str(ROOT / "mpc/tests/arm_run.sh"), "arm64"]
    if not tool.exists() or not lib.exists():
        print(f"cpu: missing {tool if not tool.exists() else lib} (make mpc; python3 mpc/build.py --tools)")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    rvs = list(RV_TYPES.items())[:1] if a.quick else list(RV_TYPES.items())
    budget = a.block / 44100.0 * 1e6
    rows = []
    for eng in ENGINES:
        for rvn, rv in rvs:
            name = f"{eng}-{rvn}".replace(" ", "_")
            s = OUT / f"{name}.txt"
            s.write_text(script(eng, rv, a.secs))
            cmd = pre + [str(tool), str(lib), str(s), str(OUT / f"{name}.wav"), "--bench", "1", "--block", str(a.block)]
            p = subprocess.run(["nice", "-n", "15"] + cmd, cwd=ROOT, capture_output=True, text=True)
            m = re.search(r"([\d.]+) of real time; the worst call (\d+) us", p.stdout)
            q = re.search(r"p50 (\d+) us, p99 (\d+) us, p99.9 (\d+) us", p.stdout)
            if p.returncode or not m or not q:
                print(f"FAIL  {name}: {(p.stdout + p.stderr).strip()[-300:]}")
                return 1
            share, worst, p999 = float(m.group(1)), int(m.group(2)), int(q.group(3))
            rows.append((share, p999, worst, name))
            print(f"{name:20s} {share * 100:6.2f} % of real time   per call p99.9 {p999:5d} us, the worst {worst:5d} us "
                  f"of {budget:.0f}", flush=True)
    share, _, _, n1 = max(rows)
    _, p999, _, n2 = max(rows, key=lambda x: x[1])
    _, _, worst, n3 = max(rows, key=lambda x: x[2])
    print(f"worst ({a.where}, {a.block}-frame blocks): {share * 100:.2f} % of real time ({n1}); per call p99.9 {p999} us "
          f"= {p999 / budget * 100:.1f} % of its block ({n2}); the worst single call {worst} us ({n3})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
