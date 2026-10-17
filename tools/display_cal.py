#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Measure the FM-1 panel's colour transfer from a photo of its screen, for the manual's "device look".

The FM-1's ST7789 panel, behind its window, lifts the dark tones a lot and shifts the hue towards yellow: the AMBER
palette's darkest step reads light khaki, black a dark blue-grey. This tool lines a photo up with the host build's
framebuffer of the same screen, samples every firmware colour where it covers a solid area, and fits the transfer
that tools/manual_shots.py applies (DEVICE_LOOK there; web/app.js uses the same numbers).

  tools/display_cal.py page PHOTO [--corners TLx,TLy TRx,TRy BLx,BLy BRx,BRy]
      a photo of the FRODHES page in the state of the 2026-10-17 photo (F01, TYPE VIBES, DRIVE 0, REVERB 0, O-1,
      SUS; firmware before 0.9.3's gauges, so the gauge tracks are solid C_LINE). Corners: the card grid's outer
      corners in the photo (screen pixels (1,63) (239,63) (1,203) (239,203)).
  tools/display_cal.py testpat PHOTO --corners ...
      a photo of the colour test pattern (developer CC 3 >= 64, ui.c ui_draw_testpat: the 6 palettes' 5 steps +
      white, a grey ramp 0 32 64 128 192 255, pure R G B Y K W; 40 x 30 swatches). Corners: the screen's corners
      (screen (0,0) (240,0) (0,240) (240,240)). This is the better source: every swatch is a large flat area.

The corners are a first guess (to a few photo pixels); the tool refines them by correlating the photo with the
host render. Take the photo straight on, the screen filling a good part of the frame, and expose for the bright
swatches (a phone camera exposing for the dark screen clips the text: such samples are only used as lower
bounds). Writes build/display_cal/<mode>-*.png (the photo warped onto the 240 x 240 grid, side by side with the
fitted look) and prints the model for manual_shots.py.

The model, per output channel c (R, G, B; x = the firmware colour as the RGB565 framebuffer expands it, 0..1):
  t_R = x_R ** g_R,  t_G = x_G ** g_G
  t_B = max(x_B ** g_B, k * ((t_R + t_G) / 2) ** p)          (blue leaks in with brightness)
  out_c = black_c + (255 - black_c) * t_c
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "display_cal")
HOST = os.path.join(ROOT, "build", "host", "frodhes")

MODES = {
    # host script, the screen points the corners name, regions to leave out (y0, y1, x0, x1)
    "page": (["100 midi 0xC0 0 0", "1900 midi 0xB0 79 0", "1920 midi 0xB0 81 0", "1940 midi 0xB0 91 0",
              "1960 btn 0", "1980 btn 13", "2000 btn 8", "4000 shot", "4100 end"],
             [(1, 63), (239, 63), (1, 203), (239, 203)],
             [(0, 20, 100, 240), (33, 41, 0, 240), (135, 204, 121, 240)]),   # BPM / SUS, page marks, KEY VEL
    "testpat": (["100 midi 0xB0 3 127", "2500 shot", "2600 end"],
                [(0, 0), (240, 0), (0, 240), (240, 240)], []),
}


def homog(src, dst):
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    return np.linalg.svd(np.array(a, float))[2][-1].reshape(3, 3)


def warp(ph, h, sub=4):
    """each screen pixel = the mean of sub x sub bilinear photo samples over its central half"""
    out = np.zeros((240, 240, 3))
    off = (np.arange(sub) + 0.5) / sub * 0.5 + 0.25
    ys, xs = np.mgrid[0:240, 0:240]
    for dy in off:
        for dx in off:
            p = h @ np.stack([xs.ravel() + dx, ys.ravel() + dy, np.ones(240 * 240)])
            u, v = p[0] / p[2], p[1] / p[2]
            ui = np.clip(u.astype(int), 0, ph.shape[1] - 2); vi = np.clip(v.astype(int), 0, ph.shape[0] - 2)
            fu, fv = np.clip(u - ui, 0, 1)[:, None], np.clip(v - vi, 0, 1)[:, None]
            out += (ph[vi, ui] * (1 - fu) * (1 - fv) + ph[vi, ui + 1] * fu * (1 - fv)
                    + ph[vi + 1, ui] * (1 - fu) * fv + ph[vi + 1, ui + 1] * fu * fv).reshape(240, 240, 3)
    return out / sub / sub


def render(lines, tag):
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    os.makedirs(OUT, exist_ok=True)
    base = os.path.join(OUT, tag)
    open(base + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([HOST, base, base + ".txt"], check=True, capture_output=True)
    t = [l.split()[0] for l in lines if l.endswith("shot")][-1]
    return np.asarray(Image.open(f"{base}-{t}.ppm").convert("RGB")).astype(int)


def refine(ph, src, dst, host, keep):
    def score(d):
        w = warp(ph, homog(src, d), 2).mean(2)[keep]; g = host.mean(2)[keep]
        a, b = w - w.mean(), g - g.mean()
        return (a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum())
    best = score(dst)
    for _ in range(4):
        moved = False
        for i in range(4):
            for k in range(2):
                for d in (-3, -1, 1, 3):
                    t = [list(p) for p in dst]; t[i][k] += d
                    s = score(t)
                    if s > best: best, dst, moved = s, t, True
        if not moved: break
    return dst, best


def model(look, x):
    """x: N x 3 firmware colours 0..255 -> N x 3 colours as photographed (see the module doc)"""
    g, m, f = np.asarray(look["gamma"]), np.asarray(look["matrix"]), np.asarray(look["flare"])
    t = (np.asarray(x, float) / 255.0) ** g
    return 255.0 * np.clip(f + t @ m.T, 0, 1) ** (1 / 2.2)


def unpack(q):
    return {"gamma": list(q[0:3]), "matrix": [list(q[3:6]), list(q[6:9]), list(q[9:12])], "flare": list(q[12:15])}


def fit_panel(fw, meas, clip=248):
    """the panel as the test-pattern photo shows it; a camera-clipped channel is only a lower bound"""
    from scipy.optimize import least_squares
    fw, meas = np.asarray(fw, float), np.asarray(meas, float)

    def res(q):
        d = model(unpack(q), fw) - meas
        c = meas >= clip
        d[c] = np.minimum(d[c], 0)
        return (d * wt).ravel()
    wt = np.where(fw.sum(1) == 0, 6.0, np.where(fw.max(1) <= 64, 2.0, 1.0))[:, None]   # black and the dark steps matter most
    q0 = np.r_[[2.2] * 3, 1, 0, 0, 0, 1, 0, 0, 0, 1, [0.002] * 3]
    lo = np.r_[[0.3] * 3, [-0.5] * 9, [0] * 3]; hi = np.r_[[5] * 3, [3] * 9, [0.3] * 3]
    r = least_squares(res, q0, bounds=(lo, hi))
    return unpack(r.x), np.sqrt(np.mean(r.fun ** 2))


def fit_view(panel, fw, meas, clip=248):
    """the eye's (and the page photo's) exposure and white balance, per channel in linear light: lin' = k * lin +
    f, fitted on a photo of a page (mostly black, so the camera, like the eye, opens up; the test pattern's photo is
    exposed for its bright swatches). A camera-clipped channel is only a lower bound."""
    from scipy.optimize import least_squares
    lin = lambda v: (np.clip(np.asarray(v, float), 0, 255) / 255.0) ** 2.2
    b, m = lin(model(panel, fw)), np.asarray(meas, float)
    k, f = np.zeros(3), np.zeros(3)
    for c in range(3):
        def res(q):
            d = 255 * np.clip(q[0] * b[:, c] + q[1], 0, 1) ** (1 / 2.2) - m[:, c]
            cl = m[:, c] >= clip
            d[cl] = np.minimum(d[cl], 0)
            return d * np.where(np.asarray(fw).sum(1) == 0, 4.0, 1.0)
        k[c], f[c] = least_squares(res, [4.0, 0.02], bounds=([0.5, -0.1], [40, 0.3])).x
    return {"gamma": panel["gamma"], "matrix": [list(k[i] * np.asarray(r)) for i, r in enumerate(panel["matrix"])],
            "flare": list(k * np.asarray(panel["flare"]) + f)}, k, f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=MODES)
    ap.add_argument("photo")
    ap.add_argument("--panel", help="page mode: the panel fit (default build/display_cal/panel.json)")
    ap.add_argument("--corners", nargs=4, help="x,y in the photo for the mode's four screen points (TL TR BL BR)")
    a = ap.parse_args()
    lines, src, excl = MODES[a.mode]
    host = render(lines, a.mode)
    ph = np.asarray(Image.open(a.photo).convert("RGB")).astype(float)
    if a.corners:
        dst = [[float(v) for v in c.split(",")] for c in a.corners]
    elif a.mode == "page":                              # the 2026-10-17 photo
        dst = [[212, 742], [1177, 739], [202, 1312], [1193, 1303]]
    else:
        sys.exit("--corners needed")
    keep = np.ones((240, 240), bool)
    for y0, y1, x0, x1 in excl: keep[y0:y1, x0:x1] = False
    cls_img = host.copy()
    if a.mode == "page":                                # the photo's firmware drew solid C_LINE gauge tracks
        line = host[63, 1]
        for x0, y0 in ((121, 63), (1, 135)):
            cls_img[y0 + 57:y0 + 61, x0 + 6:x0 + 112] = line
    dst, score = refine(ph, src, dst, cls_img, keep)
    w = warp(ph, homog(src, dst))
    from scipy import ndimage
    fw, meas, rows = [], [], []
    cols = sorted({tuple(c) for c in cls_img.reshape(-1, 3)})
    for c in cols:
        m = np.all(cls_img == c, 2) & keep
        e = ndimage.binary_erosion(m, np.ones((5, 5)) if a.mode == "testpat" or sum(c) == 0 else np.ones((3, 1)))
        if e.sum() >= 20: m = e
        v = w[m]
        if not len(v): continue
        if a.mode == "page" and sum(c) and e.sum() < 20:  # thin text: its cores (the brighter half)
            v = v[v[:, 1] >= np.median(v[:, 1])]
        med = np.median(v, 0)
        fw.append(tuple(int(v) for v in c)); meas.append(med); rows.append((c, int(m.sum()), med))
    print(f"corners {dst}  (correlation {score:.3f})")
    pj, lj = os.path.join(OUT, "panel.json"), os.path.join(OUT, "device_look.json")
    if a.mode == "testpat":
        look, rms = fit_panel(fw, meas)
        json.dump(look, open(pj, "w"), indent=1)
        print(f"panel fit: rms {rms:.1f} (0..255) over {len(fw)} swatches -> {pj}")
    else:
        panel = json.load(open(a.panel or pj))
        look, k, f = fit_view(panel, fw, meas)
        print(f"view: exposure k {np.round(k, 3)}, flare f {np.round(f, 4)} (linear light, per channel R G B)")
        json.dump(look, open(lj, "w"), indent=1)
        print("->", lj)
    pred = model(look, fw)
    print("firmware (as the framebuffer expands it): photo median -> model")
    for (c, n, med), pr in zip(rows, pred):
        flag = "  (photo clipped: a lower bound)" if (med >= 248).any() else ""
        print(f"  {str(tuple(int(v) for v in c)):16s} n={n:5d}  {np.round(med).astype(int)}  {np.round(pr).astype(int)}{flag}")
    print("DEVICE_LOOK =", json.dumps({k2: np.round(np.asarray(v), 4).tolist() for k2, v in look.items()}))
    sim = model(look, host.reshape(-1, 3)).reshape(240, 240, 3)
    im = Image.new("RGB", (960, 480))
    im.paste(Image.fromarray(np.clip(w, 0, 255).astype(np.uint8)).resize((480, 480), Image.NEAREST), (0, 0))
    im.paste(Image.fromarray(np.clip(sim, 0, 255).astype(np.uint8)).resize((480, 480), Image.NEAREST), (480, 0))
    im.save(os.path.join(OUT, f"{a.mode}-photo-vs-look.png"))
    print("->", os.path.join(OUT, f"{a.mode}-photo-vs-look.png"))


if __name__ == "__main__":
    main()
