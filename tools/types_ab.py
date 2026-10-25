#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes TYPE voicing checks on the host (the C engine, effects bypassed by CC 119): the renders and the three
tables the voicing is judged by.

    tools/types_ab.py --out build/ab-types/before      render + print the tables, save <out>/report.json
    tools/types_ab.py --out build/ab-types/after --compare build/ab-types/before

  types   C3 / C5 (MIDI 48 / 72) at v100 per TYPE: the spectrum in octave bands (dB re the note's total, 30..600 ms),
          the level (RMS 50..300 ms) and H2/H1; the cabinet's high shelf shows as the bands from 4 kHz up
  bark    H2/H1 (30..300 ms) and the level against velocity on A2 / A3 / A4 (STAGE 77, the default panel), and the
          growth of H2/H1 per dB of level above v96 (the bark onset)
  tone    the TONE knob (CC 28) swept at A3 v100: the level, H1, H2 and H2/H1 (the top of the range puts the tine near
          the pole axis, where the pickup's response is even and the fundamental cancels)
Physics-only: nothing here reads recordings.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
TYPES = ["STAGE77", "SUITCASE", "FELT", "DYNO"]          # CC 80 = index * 32
TYPE_KEYS = (48, 72, 84)                                   # C3, C5, C6
BARK_KEYS = (45, 57, 69)                                   # A2, A3, A4
BARK_VELS = (40, 60, 80, 96, 110, 127)
TONE_VALS = (0, 16, 32, 48, 64, 80, 90, 96, 100, 104, 112, 120, 127)
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


def hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def render(path, lines, hold_ms=1500):
    """each (cc list, key, vel) of the list in its own host run (a note still sounding is re-struck on the same voice,
    which keeps its pickup offset and force gains: one run per note measures what a fresh strike plays);
    -> the left channels from the onset; the WAVs stay as <path>-<i>.wav"""
    segs = []
    for i, (ccs, n, v) in enumerate(lines):
        script = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0"]
        script += [f"{40 + j} midi 0xB0 {c} {val}" for j, (c, val) in enumerate(ccs)]
        script += [f"200 midi 0x90 {n} {v}", f"{200 + hold_ms} midi 0x80 {n} 0", f"{900 + hold_ms} end"]
        out = f"{path}-{i}"
        open(out + ".txt", "w").write("\n".join(script) + "\n")
        subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
        w = wave.open(out + ".wav")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
        seg = x[int(0.2 * FS):int((0.2 + hold_ms / 1000) * FS)]
        segs.append(seg[int(np.argmax(np.abs(seg) > 0)):])
    return segs


def db(p):
    return 10 * math.log10(max(p, 1e-30))


def power_spectrum(seg, t0=0.03, t1=0.30):
    s = seg[int(t0 * FS):int(t1 * FS)]
    S = np.abs(np.fft.rfft(s * np.hanning(len(s)), 1 << 17)) ** 2
    return np.fft.rfftfreq(1 << 17, 1 / FS), S


def harm(fr, S, f):
    return float(np.sum(S[(fr >= 0.97 * f) & (fr <= 1.03 * f)]))


def level(seg):
    s = seg[int(0.05 * FS):int(0.3 * FS)]
    return 20 * math.log10(math.sqrt(float(np.mean(s * s))) / 32768 + 1e-12)


def note_stats(seg, n):
    fr, S = power_spectrum(seg)
    f0 = hz(n)
    h1, h2 = harm(fr, S, f0), harm(fr, S, 2 * f0)
    fb, Sb = power_spectrum(seg, 0.03, 0.6)
    tot = float(np.sum(Sb))
    bands = [db(float(np.sum(Sb[(fb >= b / math.sqrt(2)) & (fb < b * math.sqrt(2))])) / tot) for b in BANDS]
    return dict(L=level(seg), H1=db(h1 / 32768 ** 2), H2=db(h2 / 32768 ** 2), H21=db(h2 / max(h1, 1e-30)),
                bands=bands)


def measure(out):
    os.makedirs(out, exist_ok=True)
    R = {}
    ev = [([(80, ti * 32)], n, 100) for ti in range(4) for n in TYPE_KEYS]
    segs = render(os.path.join(out, "types"), ev)
    R["types"] = {f"{TYPES[ti]}/{n}": note_stats(segs[ti * len(TYPE_KEYS) + j], n)
                  for ti in range(4) for j, n in enumerate(TYPE_KEYS)}
    ev = [([], n, v) for n in BARK_KEYS for v in BARK_VELS]
    segs = render(os.path.join(out, "bark"), ev, hold_ms=800)
    R["bark"] = {f"{n}/{v}": note_stats(segs[i * len(BARK_VELS) + j], n)
                 for i, n in enumerate(BARK_KEYS) for j, v in enumerate(BARK_VELS)}
    ev = [([(28, tv)], 57, 100) for tv in TONE_VALS]
    segs = render(os.path.join(out, "tone"), ev, hold_ms=800)
    R["tone"] = {str(tv): note_stats(s, 57) for tv, s in zip(TONE_VALS, segs)}
    json.dump(R, open(os.path.join(out, "report.json"), "w"), indent=1)
    return R


def bark_growth(R, n, v0=96, v1=127):
    """dB of H2/H1 per dB of level from v0 to v1"""
    a, b = R["bark"][f"{n}/{v0}"], R["bark"][f"{n}/{v1}"]
    return (b["H21"] - a["H21"]) / max(b["L"] - a["L"], 1e-6)


def report(R, B=None):
    o = []
    d = lambda k, path: "" if B is None else " (%+.1f)" % (R[path][k[0]][k[1]] - B[path][k[0]][k[1]])
    o.append("TYPES  v100, octave bands dB re total (30..600 ms): " + " ".join("%5d" % b for b in BANDS))
    for key, s in R["types"].items():
        if B is not None and key not in B["types"]:
            continue
        bd = " ".join("%5.1f" % x for x in s["bands"])
        if B is not None:
            bd += "  | delta " + " ".join("%+5.1f" % (x - y) for x, y in zip(s["bands"], B["types"][key]["bands"]))
        o.append("  %-12s L %6.1f%s H2/H1 %6.1f%s  %s" % (key, s["L"], d((key, "L"), "types"), s["H21"],
                                                        d((key, "H21"), "types"), bd))
    o.append("BARK  STAGE 77: H2/H1 dB (level dBFS) per velocity " + " ".join("v%d" % v for v in BARK_VELS))
    for n in BARK_KEYS:
        row = " ".join("%6.1f(%5.1f)" % (R["bark"][f"{n}/{v}"]["H21"], R["bark"][f"{n}/{v}"]["L"]) for v in BARK_VELS)
        g = bark_growth(R, n)
        o.append("  key %3d %s  growth v96..127 %.2f dB/dB%s" % (n, row, g, "" if B is None else
                                                                   " (was %.2f)" % bark_growth(B, n)))
    o.append("TONE  A3 v100: value: L / H1 / H2 / H2/H1 dB")
    for tv in TONE_VALS:
        s = R["tone"][str(tv)]
        o.append("  %3d: %6.1f %6.1f %6.1f %6.1f%s" % (tv, s["L"], s["H1"], s["H2"], s["H21"],
                                                      d((str(tv), "L"), "tone")))
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "ab-types", "latest"))
    ap.add_argument("--compare", default=None, help="a previous --out directory")
    ap.add_argument("--bin", default=None, help="another host build (e.g. of the commit before), not rebuilt")
    a = ap.parse_args()
    global BIN
    if a.bin:
        BIN = os.path.abspath(a.bin)
    else:
        subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    R = measure(a.out)
    B = json.load(open(os.path.join(a.compare, "report.json"))) if a.compare else None
    txt = report(R, B)
    open(os.path.join(a.out, "report.txt"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    sys.exit(main())
