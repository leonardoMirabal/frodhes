#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""M0-04 (private plan 05): why Gate R's renders differ at the Move's 16 voices.

Every Gate R job (tools/gate_r.py jobs()) rendered three ways on the host: the default build (10 voices: the
baseline), FR_VOICES=16u, and FR_VOICES=16u with the bar engine's CPU budget off (FRODHES_NOBUDGET=1). Per job the
10-voice run's own report says whether it stole a voice (rhodes steals, every voice in use). A render that differs
at 16 voices is explained when its 10-voice run stole (16 voices keep the note that 10 had to steal) or when the
budget-free 16-voice run differs from the budgeted one (the FM-1-time cost model trims bar modes at 16 voices), or
when the script bends (the bend's retune spread is a quarter of the voices a block). Anything else is unexplained
(a bug to find). Prints one line per differing job and the counts.

    python3 tools/voices16_study.py [--jobs N]
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate_r  # noqa: E402

OUT = ROOT / "build" / "voices16"


def build(tag, defs):
    b = OUT / tag / "frodhes"
    b.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cc", *gate_r.CFLAGS, *defs, "-o", str(b), "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)
    return b


def run(b, tag, name, script, env=None):
    out = OUT / tag / name
    out.parent.mkdir(parents=True, exist_ok=True)
    e = dict(os.environ, **(env or {}))
    p = subprocess.run([str(b), str(out)] + ([str(script)] if script else []), cwd=ROOT, capture_output=True,
                       text=True, env=e)
    w = out.with_suffix(".wav")
    sha = hashlib.sha256(w.read_bytes()).hexdigest() if w.exists() else "MISSING"
    rep = p.stdout + p.stderr
    st = re.search(r"rhodes steals (\d+)", rep)
    vm = re.search(r"voices max rhodes (\d+) of (\d+) bars (\d+) of (\d+)", rep)
    return sha, int(st.group(1)) if st else -1, tuple(int(x) for x in vm.groups()) if vm else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    j = gate_r.jobs()
    b10, b16 = build("v10", []), build("v16", ["-DFR_VOICES=16u"])

    def one(kv):
        n, s = kv  # noqa: F841
        r10 = run(b10, "v10", n, s)
        r16 = run(b16, "v16", n, s)
        r16n = run(b16, "v16nb", n, s, {"FRODHES_NOBUDGET": "1"}) if r16[0] != r10[0] else None
        r10n = run(b10, "v10nb", n, s, {"FRODHES_NOBUDGET": "1"}) if r16[0] != r10[0] else None
        return n, r10, r16, r16n, r10n
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(one, j.items()))
    counts = {}
    for n, r10, r16, r16n, r10n in sorted(res):
        s = j[n]
        if r16[0] == r10[0]:
            counts["equal"] = counts.get("equal", 0) + 1
            continue
        stole = r10[1] > 0 or (r10[2] and (r10[2][0] >= r10[2][1] or r10[2][2] >= r10[2][3]))
        trims16 = r16n[0] != r16[0]
        same_nb = r16n[0] == r10n[0]
        why = ("steals at 10" if stole else "") + (" + " if stole and trims16 else "") + \
              ("bar budget trims at 16" if trims16 else "")
        bend = s is not None and re.search(r"midi 0xE[0-9A-F]", Path(s).read_text(), re.I)
        if not why and bend:
            why = "bend retune spread"          # (rhodes.c RH_RT_PER = (RH_NV + 3) / 4 voices a block: 3 at 10, 4 at
                                                # 16; with RH_RT_PER 3 the 16-voice render equals the 10-voice one)
        if not why:
            why = "UNEXPLAINED"
        if not stole and trims16 and same_nb:
            why += " (budget off: 16 == 10)"
        counts[why] = counts.get(why, 0) + 1
        print(f"{n:48} {why:40} 10v: steals {r10[1]} voices {r10[2]}")
    print("counts:", counts)
    return 1 if any(k.startswith("UNEXPLAINED") for k in counts) else 0


if __name__ == "__main__":
    sys.exit(main())
