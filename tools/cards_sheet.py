#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The design pass's comparison sheets (2026-10-18): the rows' variants (developer CC 11, ui.c draw_row_cards) on the
same pages, in the framebuffer's own colours (tools/manual_shots.py's default), at the large face's sizes (a host
build per size: -DUI_VAL_PX=28 / 25 / 24, aatext.c).

  tools/cards_sheet.py [OUT_DIR]     (default private/ui-simplify/design-pass)
  tools/cards_sheet.py --breathe [OUT_DIR]   only breathe.png: A at 24 px, the tight first version (UI_BREATHE=0)
                                             next to the breathing one (the default)

Writes to OUT_DIR:
  sheet-<combo>-<px>px.png     the ten pages, one combination at one size (A = SEGMENTS + LED, B = PIPS + LED,
                               C = SEGMENTS + BACK; amounts always the FADER fill)
  choices-<combo>-<px>px.png   the selector / toggle pages (FILTER LFO DELAY CHORUS COMPRESSOR DRIVE MIDI SETUP
                               ARPEGGIO, VIBES VOICE 3/3)
  side-by-side-24px.png        one row per page: A | B | C at 24 px
  sizes-A.png                  one row per page: A at 28 | 25 | 24 px
  earlier/                     the first round (0 TODAY, 1 FADER, 2 LINE at 28 px)
  shots/<combo>-<px>/<page>.png  the shots themselves"""
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMBOS = [(3, "A", "A: selectors SEGMENTS, toggles LED"), (4, "B", "B: selectors PIPS, toggles LED"),
          (5, "C", "C: selectors SEGMENTS, toggles BACK")]
EARLIER = [(0, "0-today", "0 TODAY (the firmware's default)"), (1, "1-fader", "1 FADER: the fill on every card"),
           (2, "2-line", "2 LINE: a meter line under each row")]
SIZES = [28, 25, 24]
PAGES = [("piano", "FRODHES (Rhodes)"), ("piano-vibes", "FRODHES (VIBES)"), ("voice1-vibes", "VIBES VOICE 1/3"),
         ("voice3-vibes", "VIBES VOICE 3/3"), ("voice1", "Rhodes VOICE 1/2"), ("filter", "FILTER"), ("lfo", "LFO"),
         ("delay", "DELAY"), ("comp", "COMPRESSOR"), ("midi", "MIDI")]
CHOICES = [("filter", "FILTER"), ("lfo", "LFO"), ("delay", "DELAY"), ("chorus", "CHORUS"), ("comp", "COMPRESSOR"),
           ("drive", "DRIVE"), ("midi", "MIDI"), ("setup", "SETUP"), ("arp", "ARPEGGIO"), ("voice3-vibes", "VIBES VOICE 3/3")]
BG, INK, GAP, CAP = (24, 24, 24), (210, 210, 210), 16, 34


def font(px):
    return ImageFont.truetype(os.path.join(ROOT, "assets", "fonts", "InterTight[wght].ttf"), px)


def host(px, breathe=1):
    b = os.path.join(ROOT, "build", "host", f"frodhes_v{px}" + ("" if breathe else "_tight"))
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"", f"-DUI_VAL_PX={px}",
                    f"-DUI_BREATHE={breathe}", "-Ifirmware/src", "-Ifirmware/gen", "-o", b, "firmware/src/frodhes.c",
                    "-lm"], cwd=ROOT, check=True)
    return b


def shoot(n, px, breathe=1):
    out = os.path.join(ROOT, "build", "design", f"c{n}-{px}" + ("" if breathe else "-tight"))
    env = dict(os.environ, FRODHES_HOST_BIN=host(px, breathe))
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "manual_shots.py"), "--cards", str(n), "--out", out],
                   check=True, capture_output=True, env=env)
    return out


def grid(ims, cols, caps, title, w=480):
    rows = (len(ims) + cols - 1) // cols
    o = Image.new("RGB", (cols * w + (cols + 1) * GAP, 60 + rows * (w + CAP + GAP) + GAP), BG)
    d = ImageDraw.Draw(o)
    d.text((GAP, 16), title, fill=INK, font=font(26))
    for i, (im, c) in enumerate(zip(ims, caps)):
        x, y = GAP + (i % cols) * (w + GAP), 60 + (i // cols) * (w + CAP + GAP)
        d.text((x, y + 4), c, fill=INK, font=font(20))
        o.paste(im, (x, y + CAP))
    return o


BREATHE = [("piano", "FRODHES (Rhodes)"), ("piano-vibes", "FRODHES (VIBES)"), ("voice1-vibes", "VIBES VOICE 1/3")] + CHOICES


def breathe(dst):
    """breathe.png: A at 24 px, the tight first version (UI_BREATHE=0) next to the breathing one"""
    tight, air = shoot(3, 24, 0), shoot(3, 24, 1)
    ims, caps = [], []
    for p, c in BREATHE:
        for d, t in ((tight, "now"), (air, "breathing")):
            ims.append(Image.open(os.path.join(d, f"{p}.png")).convert("RGB")); caps.append(f"{c}: {t}")
    grid(ims, 4, caps, "A at 24 px: now | breathing (+0.75 px between the value's letters, the value 4 px further "
         "in, the unit 2 px further out, 14 px after the label)").save(os.path.join(dst, "breathe.png"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dst = os.path.abspath(args[0] if args else os.path.join(ROOT, "private", "ui-simplify", "design-pass"))
    os.makedirs(dst, exist_ok=True)
    if "--breathe" in sys.argv:
        breathe(dst)
        print(os.path.join(dst, "breathe.png"))
        return
    src = {}

    def get(n, tag, px, page):
        if (n, px) not in src:
            src[(n, px)] = shoot(n, px)
            sd = os.path.join(dst, "shots", f"{tag}-{px}")
            os.makedirs(sd, exist_ok=True)
            for f in os.listdir(src[(n, px)]):
                shutil.copy(os.path.join(src[(n, px)], f), sd)
        return Image.open(os.path.join(src[(n, px)], f"{page}.png")).convert("RGB")

    for n, tag, title in COMBOS:
        for px in SIZES:
            grid([get(n, tag, px, p) for p, _ in PAGES], 5, [c for _, c in PAGES],
                 f"{title}; amounts FADER; values {px} px").save(os.path.join(dst, f"sheet-{tag}-{px}px.png"))
            grid([get(n, tag, px, p) for p, _ in CHOICES], 5, [c for _, c in CHOICES],
                 f"{title}: the selector / toggle pages; values {px} px").save(os.path.join(dst, f"choices-{tag}-{px}px.png"))
    ims, caps = [], []
    for p, c in PAGES:
        for n, tag, _ in COMBOS:
            ims.append(get(n, tag, 24, p)); caps.append(f"{c}: {tag}")
    grid(ims, 3, caps, "Side by side at 24 px: A (SEGMENTS + LED) | B (PIPS + LED) | C (SEGMENTS + BACK)").save(
        os.path.join(dst, "side-by-side-24px.png"))
    ims, caps = [], []
    for p, c in PAGES:
        for px in SIZES:
            ims.append(get(3, "A", px, p)); caps.append(f"{c}: {px} px")
    grid(ims, 3, caps, "A at the three value sizes: 28 px (today) | 25 px (-10 %) | 24 px (-15 %)").save(
        os.path.join(dst, "sizes-A.png"))
    ed = os.path.join(dst, "earlier")
    os.makedirs(ed, exist_ok=True)
    for n, tag, title in EARLIER:
        grid([get(n, tag, 28, p) for p, _ in PAGES], 5, [c for _, c in PAGES], f"Variant {title}").save(
            os.path.join(ed, f"sheet-{tag}.png"))
    print(dst)


if __name__ == "__main__":
    main()
