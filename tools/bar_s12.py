#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""S12 for the VIBES mode budget (PLAN section 9): the bar engine on the host build, each chord rendered with the
budget and without it (FRODHES_NOBUDGET), the residual re the signal over 0.2 .. 1.2 s (the plan's limit: -40 dB),
clicks (tools/hil_scan.py's second-difference test) and the cost model's worst block (cpu-proxy, us).

    python3 tools/bar_s12.py [--caps 11,16,24,32]

Each case runs RAW (the voice engine alone) with the budget planned as if every effect were on (FRODHES_FXUS, the
effects' cost from fx.c's table), then at fixed caps (MIDI CC 9, the developer override) to show what the drop order
gives when the CPU model does have to cut. A residual of -inf dB means nothing was dropped (bit-identical)."""
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
FX_ALL_US = 90 + 150 + 80 + 40 + 150 + 160 + 150 + 260     # fx.c FX_US_*: comp filter drive trem phaser chorus delay
                                                            # reverb (the heavy set of tools/hil_cpu.py, trem = MOTOR)
F3B = [53, 55, 57, 59, 60, 62, 64, 65]
CHORD8 = [53, 57, 60, 64, 65, 69, 72, 76]
YARN, CORD, RUBBER = 42, 85, 127                            # MALLET (BARK) at the families' centres (ui.c BAR_FAM_V)
CASES = [
    ("2 mallets, YARN v100", [60, 64], 100, dict(bark=YARN)),
    ("3 mallets, YARN v100", [57, 60, 64], 100, dict(bark=YARN)),
    ("4 mallets, YARN v100", [55, 59, 62, 65], 100, dict(bark=YARN)),
    ("4 mallets, CORD v110, edge", [55, 59, 62, 65], 110, dict(bark=CORD, tone=110)),
    ("8-note chord, YARN v100", CHORD8, 100, dict(bark=YARN)),
    ("8-note chord, CORD v100", CHORD8, 100, dict(bark=CORD)),
    ("8 x RUBBER edge v127 (extreme)", F3B, 127, dict(bark=RUBBER, tone=127, bell=127, hammer=127)),
]


def script(notes, vel, kn, cap=None):
    k = dict(tone=64, bark=64, bell=64, hammer=64)
    k.update(kn)
    s = "50 midi 0xB0 119 127\n52 midi 0xB0 79 64\n"
    s += f"55 midi 0xB0 28 {k['tone']}\n55 midi 0xB0 29 {k['bark']}\n55 midi 0xB0 30 {k['bell']}\n"
    s += f"55 midi 0xB0 31 {k['hammer']}\n56 midi 0xB0 104 0\n70 midi 0xB0 64 127\n"
    if cap is not None:
        s += f"57 midi 0xB0 9 {cap + 1}\n"
    s += "".join(f"200 midi 0x90 {n} {vel}\n" for n in notes)
    return s + "2000 end\n"


def render(tag, s, env):
    out = os.path.join(ROOT, "build", "host", f"s12-{tag}")
    with open(out + ".txt", "w") as f:
        f.write(s)
    r = subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True, text=True,
                       env=dict(os.environ, **env))
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0].astype(float)
    us = int(next(ln for ln in r.stdout.splitlines() if ln.startswith("cpu-proxy")).split()[2])
    return x, us


def clicks(m):
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def residual(a, ref):
    w = slice(int(0.2 * FS), int(1.2 * FS))
    e = np.sum((a[w] - ref[w]) ** 2)
    return 10 * np.log10(e / np.sum(ref[w] ** 2)) if e else -np.inf


def run(caps=(11, 16, 24, 32)):
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    rows = []
    for i, (name, notes, vel, kn) in enumerate(CASES):
        ref, us_ref = render(f"{i}-ref", script(notes, vel, kn), dict(FRODHES_NOBUDGET="1"))
        bud, us = render(f"{i}-fx", script(notes, vel, kn), dict(FRODHES_FXUS=str(FX_ALL_US)))
        row = dict(name=name, model=residual(bud, ref), model_us=us, model_clicks=clicks(bud), caps={})
        for c in caps:
            y, usc = render(f"{i}-c{c}", script(notes, vel, kn, c), {})
            row["caps"][c] = (residual(y, ref), clicks(y))
        rows.append(row)
    return rows


def main():
    caps = (11, 16, 24, 32)
    if "--caps" in sys.argv:
        caps = tuple(int(c) for c in sys.argv[sys.argv.index("--caps") + 1].split(","))
    rows = run(caps)
    hdr = f"{'case':34s} {'every effect (model)':>26s}" + "".join(f" {'cap ' + str(c):>12s}" for c in caps)
    print(hdr)
    for r in rows:
        line = f"{r['name']:34s} {r['model']:7.1f} dB {r['model_us']:5d} us {r['model_clicks']:2d} cl"
        line += "".join(f" {v[0]:7.1f} dB{'*' if v[1] else ' '}" for v in r["caps"].values())
        print(line)
    print("residual re the signal, 0.2 .. 1.2 s (S12 limit -40 dB); us = the cost model's worst block with every effect; "
          "* = clicks")


if __name__ == "__main__":
    main()
