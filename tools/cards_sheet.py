#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The design pass's comparison sheets (2026-10-18): the rows' variants (developer CC 11: 0 TODAY, 1 FADER, 2 LINE;
ui.c draw_row_cards) on the same pages, in the framebuffer's own colours (tools/manual_shots.py's default).

  tools/cards_sheet.py [OUT_DIR]     (default private/ui-simplify/design-pass)

Shoots every page per variant with tools/manual_shots.py --cards N (to build/design/vN), then writes to OUT_DIR:
  sheet-0-today.png, sheet-1-fader.png, sheet-2-line.png   one variant, the ten pages
  side-by-side.png                                           one row per page, one column per variant
  variant-N/<page>.png                                       the shots themselves"""
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VARIANTS = [(0, "today", "0 TODAY (the firmware's default)"),
            (1, "fader", "1 FADER: the row fill on every card, one value size, the edge clear of the text"),
            (2, "line", "2 LINE: one value size, a 3 px meter line at each row's bottom")]
PAGES = [("piano", "FRODHES (Rhodes)"), ("piano-vibes", "FRODHES (VIBES)"), ("voice1-vibes", "VIBES VOICE 1/3"),
         ("voice3-vibes", "VIBES VOICE 3/3"), ("voice1", "Rhodes VOICE 1/2"), ("filter", "FILTER"), ("lfo", "LFO"),
         ("delay", "DELAY"), ("comp", "COMPRESSOR"), ("midi", "MIDI")]
BG, INK, GAP, CAP = (24, 24, 24), (210, 210, 210), 16, 34


def font(px):
    return ImageFont.truetype(os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf"), px)


def shoot(n):
    out = os.path.join(ROOT, "build", "design", f"v{n}")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "manual_shots.py"), "--cards", str(n), "--out", out],
                   check=True, capture_output=True)
    return out


def grid(ims, cols, caps, title, w=480):
    rows = (len(ims) + cols - 1) // cols
    W = cols * w + (cols + 1) * GAP
    H = 60 + rows * (w + CAP + GAP) + GAP
    o = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(o)
    d.text((GAP, 16), title, fill=INK, font=font(26))
    for i, (im, c) in enumerate(zip(ims, caps)):
        x, y = GAP + (i % cols) * (w + GAP), 60 + (i // cols) * (w + CAP + GAP)
        d.text((x, y + 4), c, fill=INK, font=font(20))
        o.paste(im, (x, y + CAP))
    return o


def main():
    dst = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "private", "ui-simplify", "design-pass"))
    os.makedirs(dst, exist_ok=True)
    shots = {}
    for n, tag, title in VARIANTS:
        src = shoot(n)
        vd = os.path.join(dst, f"variant-{n}")
        os.makedirs(vd, exist_ok=True)
        shots[n] = []
        for p, _ in PAGES:
            shutil.copy(os.path.join(src, f"{p}.png"), os.path.join(vd, f"{p}.png"))
            shots[n].append(Image.open(os.path.join(src, f"{p}.png")).convert("RGB"))
        grid(shots[n], 5, [c for _, c in PAGES], f"Variant {title}").save(os.path.join(dst, f"sheet-{n}-{tag}.png"))
    ims, caps = [], []
    for i, (p, c) in enumerate(PAGES):
        for n, tag, _ in VARIANTS:
            ims.append(shots[n][i]); caps.append(f"{c}: {tag.upper()}")
    grid(ims, len(VARIANTS), caps, "Side by side: 0 TODAY | 1 FADER | 2 LINE").save(os.path.join(dst, "side-by-side.png"))
    print(dst)


if __name__ == "__main__":
    main()
