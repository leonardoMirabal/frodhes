#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The METERS look's colour candidates (developer CC 2: 0 = A, 1 = B, 2 = C; ui.c MT_LOOKS), for choosing by eye on
the FM-1. Its panel lifts the dark tones a lot (tools/manual_shots.py DEVICE_LOOK): the mock-up's near-black row
surface RGB(16,16,18) shows as light teal, a single RGB565 green unit lifts black from L* 24 to 41.

  tools/meters_palettes.py search      candidate C: for each colour role, the RGB565 value whose device look is the
                                       closest to the mock-up's colour (private/display/meters_final.py) as the panel
                                       could show it (the mock-up over the panel's own black, in linear light),
                                       keeping the order surface < track < fill < edge and fill < dim < label < value, each
                                       at least STEP L* above the one before, the fill and the edge within HUE_TOL of
                                       the mock-up's amber hue. Prints ui.c's RGB() values.
  tools/meters_palettes.py sheet [OUT] every page of both engines (Rhodes ST77, VIBES) in each candidate, in the device
                                       look: OUT/<candidate>-<page>.png and OUT/sheet.png (candidates as rows, the
                                       Rhodes pages then the VIBES pages). OUT defaults to private/ui-simplify/palettes.

The dark end of the model is an extrapolation (the CC 3 test pattern's dark ramp 0 4 8 12 16 20 is the cross-check:
a photo of it exposed for the dark swatches pins it). The user's eye on the FM-1 decides."""
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manual_shots as ms                                     # noqa: E402  (DEVICE_LOOK, device_look, walk_pages)

ROOT = ms.ROOT
STEP = 6.0                                                    # L*: a visible step in the model
# the mock-up's colours (private/display/vertical_mockup.py, meters_compare.py: BG SURF TRACK ACC_D ACC LAB VAL DIM)
MOCK = {"surf": (20, 20, 23), "track": (40, 40, 44), "fill": (70, 42, 10), "edge": (255, 150, 30),
        "dim": (96, 96, 102), "lab": (150, 150, 156), "val": (240, 240, 236)}
CHAINS = (("surf", "track", "fill", "edge"), ("dim", "lab", "val"))
HUED = ("fill", "edge")                                       # amber: within HUE_TOL of the mock-up's own hue
HUE_TOL = 20.0


def expand(c565):
    """RGB565 (r5, g6, b5) -> the 8-bit colour as tests/host.c's write_ppm writes it"""
    r, g, b = np.asarray(c565, int).T
    return np.stack([r * 255 // 31, g * 255 // 63, b * 255 // 31], -1)


def shown(rgb8):
    """8-bit framebuffer colours (..., 3) -> as the FM-1 shows them (manual_shots.device_look, as floats)"""
    d = ms.DEVICE_LOOK
    t = (np.asarray(rgb8, float) / 255.0) ** np.asarray(d["gamma"])
    lin = np.clip(np.asarray(d["flare"]) + t @ np.asarray(d["matrix"]).T, 0, 1)
    return 255 * lin ** (1 / 2.2)


def lab(rgb):
    """sRGB 0..255 -> CIELAB (D65)"""
    c = np.asarray(rgb, float) / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    x = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    x = x / np.array([0.95047, 1.0, 1.08883])
    f = np.where(x > 0.008856, np.cbrt(x), 7.787 * x + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lin(rgb):
    return (np.asarray(rgb, float) / 255) ** 2.2


def target(mock):
    """the mock-up's colour as the panel could show it: added to the panel's black in linear light"""
    k = lin(shown((0, 0, 0)))
    return 255 * (k + lin(mock) * (1 - k)) ** (1 / 2.2)


def search():
    """{role: (r5, g6, b5)} for candidate C"""
    allc = np.array([(r, g, b) for r in range(32) for g in range(64) for b in range(32)])
    L = lab(shown(expand(allc)))
    out = {}
    for chain in CHAINS:
        lo = lab(shown(expand([out["fill"]])))[0, 0] if chain[0] == "dim" else -1.0   # text sits on the fill too
        for role in chain:
            t = lab(target(MOCK[role]))
            d = np.sqrt((2 * (L[:, 0] - t[0])) ** 2 + (L[:, 1] - t[1]) ** 2 + (L[:, 2] - t[2]) ** 2)
            if lo >= 0:
                d[L[:, 0] < lo + STEP] = np.inf
            if role in HUED:                                  # (the lifted target alone drifts to red: the panel's
                m = lab(MOCK[role])                           # green leak makes any dark amber olive or red)
                h = np.degrees(np.arctan2(L[:, 2], L[:, 1]))
                dh = np.abs((h - np.degrees(np.arctan2(m[2], m[1])) + 180) % 360 - 180)
                d[(dh > HUE_TOL) | (np.hypot(L[:, 1], L[:, 2]) < 15)] = np.inf
            i = int(np.argmin(d))
            out[role] = tuple(int(v) for v in allc[i])
            lo = L[i, 0]
    return out


def describe(c565):
    s = shown(expand([c565]))[0]
    return f"RGB({c565[0] << 3}, {c565[1] << 2}, {c565[2] << 3})", np.round(s).astype(int), lab(s)[0]


def cmd_search():
    res = search()
    for role, c in res.items():
        m, s, l_ = describe(c)
        t = target(MOCK[role])
        print(f"{role:6} {m:20} RGB565 {c}  shown {tuple(int(x) for x in s)} L* {l_:5.1f}   (mock-up {MOCK[role]}, "
              f"its target {tuple(int(x) for x in np.round(t))} L* {lab(t)[0]:.1f})")


# ---------------------------------------------------------------- the sheet

LOOKS = (("A", "black"), ("B", "dim outline"), ("C", "model-matched"))
SETUP = (("CC", 17, 64), ("CC", 18, 70), ("CC", 12, 127), ("CC", 13, 70), ("CC", 106, 127), ("CC", 107, 64),
         ("CC", 113, 15))                                     # manual_shots' sound: tremolo, phaser, compressor on


def shots(engine, look, pages):
    """[(name, image)]: every page, the candidate set by CC 2, a chord held on the COMPRESSOR page"""
    tag = os.path.join(ROOT, "build", "host", f"palettes_{engine}_{look}")
    L, t = [f"100 midi 0xB0 2 {look}", "500 midi 0xC0 3 0"], 1800
    for _, cc, v in SETUP:
        L.append(f"{t} midi 0xB0 {cc} {v}"); t += 20
    if engine == "vibes":
        L.append(f"{t} midi 0xB0 79 0"); t += 20
    t += 1500
    out = []
    for i, name in enumerate(pages):
        if i:
            L.append(f"{t} turn 0 1"); t += 700
        L.append(f"{t} keys {0x2491 if name.startswith('comp') else 0x1:#x}"); t += 500
        L.append(f"{t} shot"); out.append((name, t))
        L.append(f"{t + 50} keys 0x0"); t += 400
    L.append(f"{t} end")
    open(tag + ".txt", "w").write("\n".join(L) + "\n")
    subprocess.run([ms.BIN, tag, tag + ".txt"], check=True, capture_output=True)
    res = []
    for name, ms_t in out:
        src = f"{tag}-{ms_t}.ppm"
        res.append((name, ms.device_look(Image.open(src).convert("RGB"))))
        os.remove(src)
    return res


def cmd_sheet(out):
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    os.makedirs(out, exist_ok=True)
    pages = {"rhodes": ms.names(ms.walk_pages()), "vibes": ms.names(ms.walk_pages(["100 midi 0xB0 79 0"]), "-vibes")}
    rows = []
    for engine in ("rhodes", "vibes"):
        for look, (letter, what) in enumerate(LOOKS):
            ims = shots(engine, look, pages[engine])
            for name, im in ims:
                im.save(os.path.join(out, f"{letter}-{name}.png"))
            rows.append((f"{letter}  {what}  ({engine})", ims))
    gap, lw, top = 8, 150, 40
    ncol = max(len(r[1]) for r in rows)
    sheet = Image.new("RGB", (lw + ncol * (240 + gap), top + len(rows) * (240 + gap)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), "METERS palette candidates (developer CC 2: 0 A, 1 B, 2 C), the FM-1 device look "
                   "(tools/manual_shots.py DEVICE_LOOK)", fill=(220, 220, 220))
    for j, (name, _) in enumerate(rows[0][1]):
        d.text((lw + j * (240 + gap), top - 14), name, fill=(170, 170, 170))
    for i, (label, ims) in enumerate(rows):
        y = top + i * (240 + gap)
        d.text((8, y + 110), label.replace("  (", "\n("), fill=(230, 230, 230))
        for j, (_, im) in enumerate(ims):
            sheet.paste(im, (lw + j * (240 + gap), y))
    sheet.save(os.path.join(out, "sheet.png"))
    print(os.path.join(out, "sheet.png"), sheet.size)


def main():
    a = sys.argv[1:]
    if a[:1] == ["search"]:
        cmd_search()
    elif a[:1] == ["sheet"]:
        cmd_sheet(os.path.abspath(a[1]) if len(a) > 1 else os.path.join(ROOT, "private", "ui-simplify", "palettes"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
