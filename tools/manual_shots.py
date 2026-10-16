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
PAGES = ["perform", "assign", "piano", "voice", "decay", "comp", "filter", "trem", "phaser", "chorus", "delay", "tempo", "scale", "chord", "seq", "arp", "lfo", "setup", "midi"]


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
        if name == "filter":                                # LP18 at 1.2 kHz, some resonance, the LFO moving it
            for cc, v in ((70, 45), (74, 75), (71, 50), (72, 40)):
                L.append(f"{t} midi 0xB0 {cc} {v}"); t += 20
            L.append(f"{t} keys 0x80"); t += 600
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} keys 0x0"); t += 300
            continue
        if name == "chord":                                 # chord mode MAJ7 with a strum, a key held
            L.append(f"{t} midi 0xB0 56 6"); L.append(f"{t + 20} midi 0xB0 59 4"); L.append(f"{t + 100} keys 0x80"); t += 700
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} keys 0x0"); t += 300
            continue
        if name == "seq":                                   # a 2-bar pattern recorded live (chord mode, then notes)
            L.append(f"{t} midi 0xB0 61 2"); L.append(f"{t + 20} midi 0xB0 62 3"); L.append(f"{t + 40} midi 0xB0 43 127")
            L.append(f"{t + 60} midi 0xB0 63 127"); L.append(f"{t + 80} midi 0xB0 56 7"); t += 200   # MIN7 chords
            for k, dt, hold in ((12, 0, 900), (16, 1000, 900), (9, 2000, 400), (14, 2500, 400)):  # F, A, D, G
                L.append(f"{t + dt} keys {1 << k:#x}"); L.append(f"{t + dt + hold} keys 0x0")
            L.append(f"{t + 3200} midi 0xB0 56 0"); t += 3300
            for k, dt in ((19, 0), (21, 250), (23, 500), (24, 750)):          # a little line on top
                L.append(f"{t + dt} keys {1 << k:#x}"); L.append(f"{t + dt + 150} keys 0x0")
            t += 1200
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} midi 0xB0 63 0"); L.append(f"{t + 60} midi 0xB0 60 0"); t += 300
            continue
        if name == "arp":                                   # the arpeggiator on (UP) over a held chord, then off
            L.append(f"{t} turn 2 1"); L.append(f"{t + 100} keys 0x4880"); t += 900
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} keys 0x0"); L.append(f"{t + 100} turn 2 -1"); t += 900
            continue
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
    # VIBES (a bar TYPE): the VOICE and DECAY cards renamed, and the TREM/WAH page's MOTOR (after the shots above,
    # so they stay as they were)
    L.append(f"{t} btn 2"); t += 100                        # effects on again
    L.append(f"{t} midi 0xC0 12 0"); t += 100               # F13 VIBES MOTOR (TYPE VIBES, the motor at 3.5 Hz)
    L.append(f"{t} btn 6"); t += 100                        # EDIT: VOICE
    L.append(f"{t} keys 0x80"); L.append(f"{t + 300} keys 0x0"); t += 1700   # (the TYPE message has gone)
    L.append(f"{t} shot"); shots[t] = "voice-vibes"; t += 100
    L.append(f"{t} turn 0 1"); t += 700
    L.append(f"{t} shot"); shots[t] = "decay-vibes"; t += 100
    L.append(f"{t} btn 5"); t += 100                        # LFO: TREM/WAH, the preset's motor
    L.append(f"{t} keys 0x80"); L.append(f"{t + 300} keys 0x0"); t += 900
    L.append(f"{t} shot"); shots[t] = "trem-motor"; t += 100
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
