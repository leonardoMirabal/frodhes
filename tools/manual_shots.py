#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Screenshots for the manual: the host build walks every page (and a few states) and saves PNGs to docs/manual/img/.

  tools/manual_shots.py
"""
import os
import subprocess

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "manual", "img")
PAGES = ["piano", "voice", "decay", "comp", "trem", "phaser", "chorus", "delay", "tempo", "scale", "setup"]


def main():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "build", "host"), exist_ok=True)
    tag = os.path.join(ROOT, "build", "host", "manual")
    L, shots, t = ["400 shot"], {400: "splash"}, 100           # the start screen, before anything is touched
    # a sound with something on every page: F04 80S BALLAD, then the tremolo, phaser and compressor on
    t = 500
    L.append(f"{t} midi 0xC0 3 0"); t += 1300                # (the start screen holds the pages for 1.6 s)
    for cc, v in ((17, 64), (18, 70), (12, 127), (13, 70), (106, 127), (107, 64), (113, 15)):
        L.append(f"{t} midi 0xB0 {cc} {v}")
    t += 1500                                               # the preset name message has gone
    for i, name in enumerate(PAGES):
        if i:
            L.append(f"{t} turn 0 1"); t += 700
        if name == "comp":                                  # a held chord: the gain-reduction meter moves
            L.append(f"{t} keys 0x2491"); t += 600
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} keys 0x0"); t += 900
            continue
        L.append(f"{t} keys 0x1"); L.append(f"{t + 300} keys 0x0")   # a note: the SUS / level readouts are live
        t += 900
        L.append(f"{t} shot"); shots[t] = name; t += 100
    # states: browsing a preset (the footer names it), the save mode, the FX bypass
    L.append(f"{t} btn 8"); t += 1600
    L.append(f"{t} turn 6 1"); t += 150
    L.append(f"{t} shot"); shots[t] = "browse"; t += 1000
    L.append(f"{t} btn 9"); t += 300
    L.append(f"{t} shot"); shots[t] = "save"; t += 100
    L.append(f"{t} btn 8"); t += 1800
    L.append(f"{t} btn 2"); t += 300
    L.append(f"{t} shot"); shots[t] = "bypass"; t += 100
    L.append(f"{t} end")
    open(tag + ".txt", "w").write("\n".join(L) + "\n")
    subprocess.run([os.path.join(ROOT, "build", "host", "frodhes"), tag, tag + ".txt"], check=True, capture_output=True)
    for ms, name in shots.items():
        src = f"{tag}-{ms}.ppm"
        Image.open(src).resize((480, 480), Image.NEAREST).save(os.path.join(OUT, f"{name}.png"))
        os.remove(src)
    print(sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
