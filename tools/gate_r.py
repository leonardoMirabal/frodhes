#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate R: every host render byte-identical to the committed baseline (tests/baselines/renders.sha256).

  tools/gate_r.py            render the list, compare, exit 1 on any difference (prints the first ones)
  tools/gate_r.py --save     render the list and write the baseline (only from committed code, on purpose)

The list: every tests/scripts/*.txt WAV (as `make test` renders them), the default run (`make host`), 36 effect
combinations (Rhodes and VIBES: every effect, the filter types, 8 bass notes v127, a chord, the pedal) and
tools/calib_report.py --host's renders (the 4 Rhodes TYPEs, keys 28-103 at v100). The host binary is built here
(build/gate_r/frodhes) with the Makefile's flags. A change that may not move the sound (a CPU rewrite, a refactor)
must leave every sha256 equal; a change that moves it on purpose re-saves the baseline in the same commit and says so.
"""
import argparse
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "gate_r"
BASE = ROOT / "tests" / "baselines" / "renders.sha256"
CFLAGS = ["-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"",
          "-Ifirmware/src", "-Ifirmware/gen"]
CC = dict(DRIVE=81, TW_SW=17, TW_DEPTH=18, TW_MODE=21, CP_SW=85, CP_DEPTH=86, DL_SW=88, DL_DEPTH=89, DL_TIME=90,
          DL_FDBK=26, REV=91, PH_SW=12, PH_DEPTH=13, PH_SPEED=14, PH_FDBK=16, CMP_SW=106, CMP_AMT=107, CMP_ATK=108,
          CMP_REL=109, FL_TYPE=70, FL_FREQ=74, FL_RESO=71, FL_LFO=72)       # tools/hil_scan.py CC
TYPES = ["STAGE77", "SUITCASE", "FELT", "DYNO"]                          # tools/calib_report.py TYPES (CC 80)


def fx_scripts():
    """the 36 effect cases (from build/cpu/mkfx.py, 2026-10-16): name -> script text"""
    heavy = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
                 DL_SW=127, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127, FL_TYPE=45, FL_FREQ=60, FL_RESO=100,
                 FL_LFO=64)
    cases = {
        'heavy': heavy,
        'heavy_cho': dict(heavy, PH_SW=0, CP_SW=64, CP_DEPTH=127),
        'trem_am': dict(TW_SW=64, TW_DEPTH=127, TW_MODE=127, REV=60),
        'trem_pan': dict(TW_SW=64, TW_DEPTH=100, TW_MODE=0, PH_SW=127, PH_DEPTH=40),
        'ph_fb': dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127),
        'ph_32': dict(PH_SW=127, PH_DEPTH=32, PH_FDBK=0, PH_SPEED=100),
        'cho': dict(CP_SW=64, CP_DEPTH=64, DL_SW=64, DL_DEPTH=80, DL_TIME=40),
        'dl_ana': dict(DL_SW=127, DL_DEPTH=127, DL_TIME=20, DL_FDBK=120),
        'rev32': dict(REV=32), 'rev127': dict(REV=127, DRIVE=60),
        'comp': dict(CMP_SW=127, CMP_AMT=127, CMP_ATK=10, CMP_REL=100),
    }
    for t in (14, 30, 45, 60, 75, 90, 110):
        cases[f'filt{t}'] = dict(FL_TYPE=t, FL_FREQ=50 + t // 3, FL_RESO=90, FL_LFO=100, PH_SW=127, PH_DEPTH=90)
    res = {}
    for vib in (0, 1):
        for name, fx in cases.items():
            ev, t = [], 50
            if vib:
                ev.append(f'{t} midi 0xB0 79 0'); t += 10
            d = dict(fx)
            if 'CP_SW' in d:
                ev.append(f'{t} midi 0xB0 85 {d.pop("CP_SW")}'); t += 5
            for k, v in d.items():
                ev.append(f'{t} midi 0xB0 {CC[k]} {v}'); t += 5
            t = 400
            for rep in range(3):
                notes = [28, 31, 33, 35, 36, 38, 40, 41] if rep != 1 else [48, 52, 55, 59, 62, 64, 67, 71]
                if rep == 2:
                    ev.append(f'{t} midi 0xB0 64 127')
                for n in notes:
                    ev.append(f'{t} midi 0x90 {n} {127 - 20 * rep}')
                t += 900
                for n in notes:
                    ev.append(f'{t} midi 0x80 {n} 0')
                t += 600
            ev.append(f'{t} midi 0xB0 64 0'); t += 2500
            ev.append(f'{t} end')
            res[f'fx/{"v_" if vib else ""}{name}'] = '\n'.join(ev) + '\n'
    return res


def calib_scripts(keys=range(28, 104), vel=100, gap=1.2):
    """tools/calib_report.py host_types()'s renders"""
    res = {}
    for ti, name in enumerate(TYPES):
        lines = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0", f"40 midi 0xB0 80 {ti * 32}"]
        t0 = 200
        for i, n in enumerate(keys):
            t = t0 + int(i * gap * 1000)
            lines += [f"{t} midi 0x90 {n} {vel}", f"{t + 700} midi 0x80 {n} 0"]
        lines.append(f"{t0 + int(len(keys) * gap * 1000) + 500} end")
        res[f'calib/types-{name}-{vel}'] = "\n".join(lines) + "\n"
    return res


def jobs():
    """name -> script path (None: the default run)"""
    j = {f'scripts/{p.stem}': p for p in sorted((ROOT / 'tests' / 'scripts').glob('*.txt'))}
    j['host/frodhes'] = None
    for name, text in list(fx_scripts().items()) + list(calib_scripts().items()):
        p = OUT / (name + '.txt')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        j[name] = p
    return j


def render(binary, name, script):
    out = OUT / name
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [str(binary), str(out)] + ([str(script)] if script else [])
    subprocess.run(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # expects may fail: the
    w = out.with_suffix('.wav')                                                           # sound is what counts
    return name, hashlib.sha256(w.read_bytes()).hexdigest() if w.exists() else 'MISSING'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--save', action='store_true', help='write tests/baselines/renders.sha256')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    binary = OUT / 'frodhes'
    cc = os.environ.get('CC', 'cc')
    subprocess.run([cc] + CFLAGS + ['-o', str(binary), 'firmware/src/frodhes.c', '-lm'], cwd=ROOT, check=True)
    j = jobs()
    with ThreadPoolExecutor(a.jobs) as ex:
        got = dict(ex.map(lambda kv: render(binary, *kv), j.items()))
    if a.save:
        BASE.parent.mkdir(parents=True, exist_ok=True)
        BASE.write_text(''.join(f'{got[n]}  {n}\n' for n in sorted(got)))
        print(f'Gate R: saved {len(got)} renders to {BASE.relative_to(ROOT)}')
        return 0
    want = dict(reversed(ln.split()) for ln in BASE.read_text().splitlines() if ln.strip())
    bad = [n for n in sorted(set(want) | set(got)) if want.get(n) != got.get(n)]
    for n in bad[:10]:
        print(f'DIFF  {n}: baseline {want.get(n, "-")[:12]}, now {got.get(n, "-")[:12]}')
    if bad:
        print(f'Gate R: {len(bad)} of {len(want)} renders differ (renders in {OUT.relative_to(ROOT)})')
        return 1
    print(f'Gate R: {len(got)} of {len(want)} renders byte-identical')
    return 0


if __name__ == '__main__':
    sys.exit(main())
