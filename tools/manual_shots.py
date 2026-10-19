#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Screenshots for the manual: the host build walks every page (and a few states) and saves PNGs to docs/manual/img/,
then composes overview.png (PERFORM, VOICE, PHASER, COMPRESSOR; the README's Screens section: its intro picture is piano.png, the FRODHES page). The page list is the firmware's: a first host run
walks SELECT and reads each page's name back (the engine's VOICE pages are dynamic: voice1.png, voice2.png on the
Rhodes; voice1-vibes.png .. voice3-vibes.png on VIBES). Files left in the folder from pages that went are listed.

  tools/manual_shots.py           the "device look": the colours as the FM-1's panel shows them
  tools/manual_shots.py           the framebuffer's own colours (what the firmware writes: the default since
                                  2026-10-18, the user: the panel model's yellow is not what the eye sees)
  tools/manual_shots.py --device-look   the colours through the panel model below (camera-calibrated)
  tools/manual_shots.py --cards N --out DIR   the rows' design variant N (developer CC 11: 0 TODAY, 1 FADER, 2 LINE),
                                  the PNGs to DIR (tools/cards_sheet.py; overview.png and the stale list skipped)

The device look. The FM-1's ST7789 panel, behind its window, lifts the dark tones a lot and shifts the hue towards
yellow: the AMBER palette's darkest step C_LINE = RGB(60,26,0) (frames) reads light khaki, the title UI_ACC =
RGB(225,120,8) yellow, the values C_HI = RGB(255,166,40) near-white yellow, black a dark blue-grey. The transfer was
measured by tools/display_cal.py from two photos of the FM-1 taken 2026-10-17 (private/display/, not in git):
  1. the colour test pattern (developer CC 3): 39 flat swatches (the 6 palettes, a grey ramp, R G B Y K W), which
     fix the panel's shape: per input channel x ** gamma, a 3 x 3 crosstalk matrix, a flare (rms 15 / 255 over
     the swatches; that photo is exposed for its bright swatches);
  2. the FRODHES page (TYPE VIBES): mostly black, so the camera, like the eye, opens up; its black and its gauge
     tracks (C_LINE) set the viewing exposure per channel in linear light (k R G B = 4.64 5.53 3.89, f = 0.021
     0.028 0.049); the brighter steps are clipped in that photo (lower bounds only).
Combined (x = the framebuffer colour 0..1, as host.c's write_ppm expands RGB565):
  t = x ** GAMMA;  out = 255 * clip(FLARE + MATRIX @ t, 0, 1) ** (1 / 2.2)
Black -> (56, 55, 70), C_LINE -> (201, 194, 76), UI_ACC -> (255, 255, 139), C_HI -> (255, 255, 229); the photo
reads (56, 55, 61), (201, 193, 107), and the bright steps clipped at about 254. web/app.js uses the same numbers.
"""
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

DEVICE_LOOK = {                                         # tools/display_cal.py, 2026-10-17 (see above)
    "gamma": [1.0907, 0.8288, 0.9032],
    "matrix": [[2.4821, 0.5194, -0.2992], [0.1402, 3.4473, 0.2092], [0.0378, 0.0352, 3.4810]],
    "flare": [0.0356, 0.0340, 0.0583],
}


def device_look(im):
    """an RGB image of the framebuffer -> the colours as the FM-1's panel shows them"""
    a = np.asarray(im.convert("RGB"), float) / 255.0
    t = a ** np.asarray(DEVICE_LOOK["gamma"])
    lin = np.clip(np.asarray(DEVICE_LOOK["flare"]) + t @ np.asarray(DEVICE_LOOK["matrix"]).T, 0, 1)
    return Image.fromarray(np.round(255 * lin ** (1 / 2.2)).astype(np.uint8))


def overview(names, gap=16, bg=(24, 24, 24)):
    """the README's picture: four screenshots 2 x 2 on a dark grey"""
    ims = [Image.open(os.path.join(OUT, f"{n}.png")) for n in names]
    w = ims[0].width
    o = Image.new("RGB", (2 * w + 3 * gap, 2 * w + 3 * gap), bg)
    for i, im in enumerate(ims):
        o.paste(im, (gap + (i % 2) * (w + gap), gap + (i // 2) * (w + gap)))
    o.save(os.path.join(OUT, "overview.png"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "manual", "img")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
# a page's file name, by its base title (host_state "page"); the VOICE pages are numbered in the SELECT order
# (voice1.png, voice2.png; VIBES: voice1-vibes.png ...), as the engine's table makes them. (DECAY / MOTOR: the pages
# of the builds before the dynamic VOICE pages.)
NAME = {"PERFORM": "perform", "FRODHES": "piano", "DECAY": "decay", "MOTOR": "motor", "COMPRESSOR": "comp",
        "FILTER": "filter", "DRIVE": "drive", "TREM/WAH": "trem", "PHASER": "phaser", "CHORUS": "chorus",
        "DELAY": "delay", "REVERB": "reverb", "TEMPO": "tempo", "SCALE": "scale", "CHORD": "chord", "ARPEGGIO": "arp",
        "LFO": "lfo", "SEQUENCER": "seq", "ASSIGN": "assign", "SETUP": "setup", "MIDI": "midi"}


def walk_pages(pre=(), t0=2500, n=30):
    """the pages SELECT walks, as the host build says: [(page, title)] from PERFORM, up to the first one seen again
    (SELECT stops at the last page). Read with "expect state page ~" probes: a failed expect prints the value.
    pre: script lines first (a TYPE)"""
    tag = os.path.join(ROOT, "build", "host", "manual_walk")
    L, t = list(pre), t0
    for i in range(n):
        if i:
            L.append(f"{t} turn 0 1"); t += 300
        L += [f"{t} expect state page ~", f"{t} expect state title ~"]; t += 10
    L.append(f"{t} end")
    open(tag + ".txt", "w").write("\n".join(L) + "\n")
    out = subprocess.run([BIN, tag, tag + ".txt"], capture_output=True, text=True).stdout
    got = {}
    for ms, k, v in re.findall(r'^\s*(\d+) ms  FAIL expect state (\w+) = "~" \(is "(.*)"\)$', out, re.M):
        got.setdefault(int(ms), {})[k] = v
    pages = []
    for ms in sorted(got):
        p = (got[ms].get("page"), got[ms].get("title", got[ms].get("page")))
        if p in pages:
            break
        pages.append(p)
    return pages


def names(pages, suffix=""):
    """file names for the walked pages: NAME by the base title, the VOICE pages numbered"""
    out, nv = [], 0
    for page, title in pages:
        base = page if page in NAME else (title or page or "").split(" ")[0]
        if base == "VOICE":
            nv += 1
            out.append(f"voice{nv}{suffix}")
        else:
            out.append(NAME.get(base, base.lower().replace("/", "")) + suffix)
    return out


def opt(name, default=None):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def main():
    global OUT, BIN
    raw = "--device-look" not in sys.argv[1:]
    cards = opt("--cards")
    OUT = os.path.abspath(opt("--out", OUT))
    if os.environ.get("FRODHES_HOST_BIN"):                  # another host build (tools/cards_sheet.py: a face size)
        BIN = os.environ["FRODHES_HOST_BIN"]
    else:
        subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "build", "host"), exist_ok=True)
    tag = os.path.join(ROOT, "build", "host", "manual")
    PAGES = names(walk_pages())                             # the SELECT order, as the firmware has it
    vibes = names(walk_pages(["100 midi 0xB0 79 0"]), "-vibes")
    vibes = vibes[vibes.index("piano-vibes") + 1:vibes.index("comp-vibes")]   # VIBES' own pages (VOICE 1..3)
    L, shots, t = ["400 shot"], {400: "splash"}, 100
    if cards is not None:                                   # the rows' design variant (developer CC 11)
        L.insert(0, f"50 midi 0xB0 11 {int(cards)}")           # the start screen, before anything is touched
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
        if name in ("drive", "reverb"):                     # a little growl, darker; a hall with a pre-delay
            ccs = ((81, 64), (82, 50), (83, 110)) if name == "drive" else ((91, 70), (92, 80), (93, 56), (94, 40))
            for cc, v in ccs:
                L.append(f"{t} midi 0xB0 {cc} {v}"); t += 20
            L.append(f"{t} keys 0x1"); L.append(f"{t + 300} keys 0x0"); t += 900
            L.append(f"{t} shot"); shots[t] = name; t += 100
            continue
        if name == "chord":                                 # chord mode MAJ7 with a strum, a key held
            L.append(f"{t} midi 0xB0 56 6"); L.append(f"{t + 20} midi 0xB0 59 4"); L.append(f"{t + 100} keys 0x80"); t += 700
            L.append(f"{t} shot"); shots[t] = name
            L.append(f"{t + 50} keys 0x0"); L.append(f"{t + 100} midi 0xB0 56 0"); t += 300   # chord mode off again
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
    # VIBES (a bar TYPE): its own VOICE pages from the engine's table (VOICE 1/3 .. 3/3, the motor on the last), after
    # the shots above, so they stay as they were
    L.append(f"{t} btn 2"); t += 100                        # effects on again
    L.append(f"{t} midi 0xC0 12 0"); t += 100               # F13 VIBES MOTOR (TYPE VIBES, the motor at 3.5 Hz)
    L.append(f"{t} btn 8"); t += 100                        # HOME: the FRODHES page with TYPE VIBES
    L.append(f"{t} keys 0x80"); L.append(f"{t + 300} keys 0x0"); t += 1700
    L.append(f"{t} shot"); shots[t] = "piano-vibes"; t += 100
    for name in vibes:                                      # SELECT through them, a note on each (live readouts)
        L.append(f"{t} turn 0 1"); t += 100
        L.append(f"{t} keys 0x80"); L.append(f"{t + 300} keys 0x0"); t += 1700   # (the TYPE message has gone)
        L.append(f"{t} shot"); shots[t] = name; t += 100
    L.append(f"{t} end")
    open(tag + ".txt", "w").write("\n".join(L) + "\n")
    subprocess.run([BIN, tag, tag + ".txt"], check=True, capture_output=True)
    for ms, name in shots.items():
        src = f"{tag}-{ms}.ppm"
        im = Image.open(src).convert("RGB")
        (im if raw else device_look(im)).resize((480, 480), Image.NEAREST).save(os.path.join(OUT, f"{name}.png"))
        os.remove(src)
    if cards is not None:
        print(sorted(set(shots.values())))
        return
    overview(["perform", "voice1", "phaser", "comp"])
    made = set(shots.values()) | {"overview"}
    print(sorted(made))
    stale = sorted(f for f in os.listdir(OUT) if f.endswith(".png") and f[:-4] not in made)
    if stale:
        print("not made by this run (pages that went: git rm them):", stale)


if __name__ == "__main__":
    main()
