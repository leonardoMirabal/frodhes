#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FILTER's frequency response, measured on the firmware's own code and compared with what it is meant to be.

tests/c/filter_scan.c runs fx.c's filter (copied out between its markers into build/filter_scan/filter_core.c) on a
sine sweep, 25 Hz .. 20 kHz, for every TYPE (LP12 LP18 LP24 HP12 HP18 HP24 BP NOTCH) x FREQ (20 40 60 80 100 120)
x RESO (0 64 127). The reference is the analog prototype each type is built from (one or two state-variable stages,
an 18 dB one-pole), with the bilinear frequency warping the TPT design has: f_a = tan(pi f / fs) / tan(pi fc / fs).

  python3 tools/filter_scan.py          -> build/filter_scan/report.txt, scan.csv, curves.png

Pass: within 0.5 dB of the reference from 40 Hz to 15 kHz wherever the reference is above -30 dB (deeper, and in
the few cycles of the lowest test tones, the 16-bit test level and the window set a floor), and finite / bounded
output everywhere. The DC column is the mean over the window: a resonant low cutoff rings a non-whole number of
cycles in it, so it is reported, not judged.
"""
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "filter_scan")
FS = 44100.0
NAMES = {1: "LP12", 2: "LP18", 3: "LP24", 4: "HP12", 5: "HP18", 6: "HP24", 7: "BP", 8: "NOTCH"}


def fc_of(v):                       # fx.c: fl_pos = v * 516 (Q8 over 256 table steps), 30 Hz .. 16 kHz
    return 30 * (16000 / 30) ** (v * 516 / 65536)


def k_of(v):                        # FX_FL_K: k = 1 / Q, Q 0.7071 .. 20
    return 1 / (0.7071 * (20 / 0.7071) ** (v / 127))


def ideal_db(t, v, r, hz):
    fc = fc_of(v)
    w = math.tan(math.pi * min(hz, FS / 2 - 1) / FS) / math.tan(math.pi * fc / FS)
    s = complex(0, w)
    def stage(k, mode):
        d = s * s + k * s + 1
        return {0: 1 / d, 1: s * s / d, 2: k * s / d, 3: (s * s + 1) / d}[mode]
    mode = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1, 7: 2, 8: 3}[t]
    h = stage(k_of(r), mode)
    if t in (3, 6):
        h *= stage(1 / 0.7071, mode)
    if t in (2, 5):
        h *= 1 / (1 + s) if mode == 0 else s / (1 + s)
    return 20 * math.log10(abs(h) + 1e-12)


def main():
    os.makedirs(OUT, exist_ok=True)
    src = open(os.path.join(ROOT, "firmware", "src", "fx.c")).read()
    a = src.index("static uint32_t lfo_ph, lfo_rnd")
    b = src.index("/* ---- DRIVE (Felucca track_dist;")
    open(os.path.join(OUT, "filter_core.c"), "w").write("#include <string.h>\n" + src[a:b])
    exe = os.path.join(OUT, "filter_scan")
    subprocess.run(["cc", "-O2", "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                    "-o", exe, os.path.join(ROOT, "tests", "c", "filter_scan.c"), "-lm"], check=True)
    rows = []
    for line in subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split("\n"):
        if line.strip():
            t, v, r, hz, db, dc = line.split()
            rows.append((int(t), int(v), int(r), float(hz), float(db)))
            maxdc = max(globals().get("maxdc", 0.0), abs(float(dc)))
            globals()["maxdc"] = maxdc
    open(os.path.join(OUT, "scan.csv"), "w").write("type,freq,reso,hz,db,ideal\n" + "".join(
        f"{NAMES[t]},{v},{r},{hz},{db},{ideal_db(t, v, r, hz):.2f}\n" for t, v, r, hz, db in rows))
    cases = {}
    for t, v, r, hz, db in rows:
        cases.setdefault((t, v, r), []).append((hz, db, ideal_db(t, v, r, hz)))
    lines, bad = [], 0
    lines.append("FILTER frequency response vs its reference (dB; error 40 Hz .. 15 kHz where the reference > -30 dB)")
    lines.append(f"{'type':6} {'freq':>4} {'fc Hz':>7} {'reso':>4} {'Q':>5}  {'max err':>7}  {'peak':>6} {'ideal':>6}")
    for (t, v, r), pts in sorted(cases.items()):
        err = max((abs(db - idb) for hz, db, idb in pts if idb > -30 and 40 <= hz < 15000), default=0)
        pk, ipk = max(db for _, db, _ in pts), max(idb for _, _, idb in pts if _ < 15000)
        finite = all(math.isfinite(db) and db < 40 for _, db, _ in pts)
        ok = err <= 0.5 and finite
        bad += not ok
        lines.append(f"{NAMES[t]:6} {v:4} {fc_of(v):7.0f} {r:4} {1 / k_of(r):5.1f}  {err:7.2f}  {pk:6.1f} {ipk:6.1f}"
                     + ("" if ok else "   <-- out of tolerance"))
    lines.append(f"\n{len(cases)} cases, {bad} out of tolerance (0.5 dB); largest DC offset {globals().get('maxdc', 0):.1f} LSB (Q15)")
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines[-1:]))
    try:
        plot(cases)
    except ImportError:
        pass
    return 1 if bad else 0


def plot(cases):                    # a grid of curves: one panel per type, the 6 cutoffs at RESO 0 and 127
    from PIL import Image, ImageDraw
    W, H, cols = 420, 260, 4
    img = Image.new("RGB", (W * cols, H * 2), (250, 248, 243))
    d = ImageDraw.Draw(img)
    fx = lambda hz: 40 + (W - 60) * math.log10(hz / 25) / math.log10(20000 / 25)
    fy = lambda db: 20 + (H - 50) * (24 - max(-60, min(24, db))) / 84
    for ti, t in enumerate(range(1, 9)):
        ox, oy = (ti % cols) * W, (ti // cols) * H
        d.text((ox + 44, oy + 4), NAMES[t] + "   measured (colour) / reference (grey)   RESO 0 / 127", fill=(0, 0, 0))
        for db in (12, 0, -12, -24, -36, -48):
            y = oy + fy(db)
            d.line([(ox + 40, y), (ox + W - 20, y)], fill=(220, 215, 205))
            d.text((ox + 8, y - 6), f"{db:+d}", fill=(120, 110, 100))
        for hz in (100, 1000, 10000):
            x = ox + fx(hz)
            d.line([(x, oy + 20), (x, oy + H - 30)], fill=(220, 215, 205))
            d.text((x - 10, oy + H - 26), f"{hz if hz < 1000 else str(hz // 1000) + 'k'}", fill=(120, 110, 100))
        for (tt, v, r), pts in cases.items():
            if tt != t or r == 64:
                continue
            col = (200, 110, 20) if r == 0 else (40, 90, 200)
            d.line([(ox + fx(hz), oy + fy(idb)) for hz, _, idb in pts], fill=(170, 170, 170), width=1)
            d.line([(ox + fx(hz), oy + fy(db)) for hz, db, _ in pts], fill=col, width=2)
    img.save(os.path.join(OUT, "curves.png"))


if __name__ == "__main__":
    sys.exit(main())
