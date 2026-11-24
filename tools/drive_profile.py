#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Black-box profile of today's DRIVE TYPE 1 (FDRV): outputs only, measured on the host build through
tools/drive_scan.py's harness (tests/c/drive_scan.c runs fx_drive as fx_process does; integer-exact, so a re-run
reproduces the numbers). The targets for the plugin's own-code build's drive: its level, peak, harmonic profile,
critical-band spectrum and aliasing, per AMOUNT, drive row, TONE and MIX.

Per configuration (row, AMOUNT, TONE, MIX), 220 Hz sine (a bin-exact odd bin of N = 16384) at -12.3 dBFS (amp 8000)
and -24.3 dBFS (amp 2000), after a settle:
  g    RMS gain re the input (dB)          pk   output peak (dBFS)
  h    H2..H9 in dB re the fundamental (the harmonic bins; floor -120)
and a 5-note chord (130.81 .. 523.25 Hz, amp 2600 each): gain, peak, and the 24 critical bands (Bark, 0..15.5 kHz): the
band energy of the output in dB re the chord's input total energy ("bark", -120 floor), the input's in "bark_in".
Rows 0..3 are the Rhodes types' drive rows (params.c TYPE_DRIVE_ROW); the bar types (VIBES, MARIMBA) run row 0, so
their drive is row 0's.
Grids: AMOUNT 0..127 at TONE 64 / MIX 127 for every row ("main"); the TONE (0, 127) and MIX (0, 64) corners at AMOUNT
0, 8 .. 120, 127 for every row ("corner"); the aliasing figures (test_drive_os.py: stepped sweep C5 .. C8, worst
folded dB, two-tone, chirp) at AMOUNT 16 / 32 / 64 / 96 / 127 x TONE 0 / 64 / 127 on row 0, amps 8000 / 16000 / 26000
at AMOUNT 127 / TONE 64 on rows 0..3; level steps -30 .. +6 dBFS at AMOUNT 32 / 64 / 127.

  python3 tools/drive_profile.py [--out tests/baselines/drive_own_target.json] [--plots build/drive_own_plots]
  python3 tools/drive_profile.py --check [--full]     re-measure and compare with the JSON (default: a sample)
"""
import argparse
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_scan as ds  # noqa: E402

JSON = os.path.join(ROOT, "tests", "baselines", "drive_own_target.json")
FS, N, SETTLE = ds.FS, 16384, ds.SETTLE
AMPS = (8000, 2000)
CHORD_HZ = (130.81, 261.63, 329.63, 392.0, 523.25)
BARK_EDGES = (20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400,
              5300, 6400, 7700, 9500, 12000, 15500)
FLOOR = -120.0
TOL_DB = 0.05
CORNER_AMOUNTS = list(range(0, 128, 8)) + [127]
CORNERS = [(0, 127), (127, 127), (64, 0), (64, 64)]     # (TONE, MIX)
AL_GRID = [(a, t) for a in (16, 32, 64, 96, 127) for t in (0, 64, 127)]


def key(row, a, t, m):
    return f"r{row}/a{a}/t{t}/m{m}"


def _stims():
    k = ds.odd_bin(220, N)
    sines = [np.round(ds.sine(k, N, a, SETTLE + N)).astype(np.int64) for a in AMPS]
    ch = sum(ds.sine(int(round(hz * N / FS)), N, 2600, SETTLE + N, 0.9 * i) for i, hz in enumerate(CHORD_HZ))
    return k, sines, np.round(ch).astype(np.int64)


def _bark(y):
    P = np.abs(np.fft.rfft(y[-N:].astype(float))) ** 2
    f = np.arange(len(P)) * FS / N
    return np.array([P[(f >= lo) & (f < hi)].sum() for lo, hi in zip(BARK_EDGES[:-1], BARK_EDGES[1:])])


def _db(p, ref):
    return max(FLOOR, round(10 * math.log10(max(p, 1e-30) / max(ref, 1e-30)), 2))


def profile(exe, row, a, t, m, st):
    k, sines, chord = st
    prm = ds.params_for("FDRV", a, t, m, row)
    r = dict(g=[], pk=[], h=[])
    for x in sines:
        y, _, _ = ds.render(exe, x, prm)
        yo, xi = y[-N:].astype(float), x[-N:].astype(float)
        r["g"].append(_db(np.mean(yo * yo), np.mean(xi * xi)))
        r["pk"].append(round(20 * math.log10(max(np.abs(yo).max(), 1) / 32767), 2))
        Y = np.abs(np.fft.rfft(yo)) ** 2
        r["h"].append([_db(Y[h * k], Y[k]) if h * k < len(Y) else FLOOR for h in range(2, 10)])
    y, _, _ = ds.render(exe, chord, prm)
    yo, xi = y[-N:].astype(float), chord[-N:].astype(float)
    r["cg"] = _db(np.mean(yo * yo), np.mean(xi * xi))
    r["cpk"] = round(20 * math.log10(max(np.abs(yo).max(), 1) / 32767), 2)
    bi = _bark(chord)
    r["bark"] = [_db(p, bi.sum()) for p in _bark(y)]
    return r


def aliasing(exe, row, a, t, amp=8000):
    rows, floor, _ = ds.aliasing_tones(exe, ds.params_for("FDRV", a, t, 127, row), amp=amp)
    p = np.array([q["folded_db"] for q in rows])
    return dict(max_db=round(float(p.max()), 2), mean_db=round(10 * math.log10(np.mean(10 ** (p / 10))), 2),
                max_below_f0_db=round(max(q["below_f0_db"] for q in rows), 2), floor_db=round(floor, 2))


def aliasing_all(exe, row, a, t):
    r = aliasing(exe, row, a, t)
    r["twotone"] = [q["folded_db"] for q in ds.aliasing_twotone(exe, ds.params_for("FDRV", a, t, 127, row))]
    r["chirp"] = list(ds.chirp_subf0(exe, ds.params_for("FDRV", a, t, 127, row)))
    return r


def measure(exe, sel=None):
    """the whole profile, or only the configurations in sel (a set of keys, for --check)"""
    st = _stims()
    out = dict(main={}, corner={}, aliasing={}, alias_amp={}, steps={})
    want = (lambda k: True) if sel is None else (lambda k: k in sel)
    for row in range(4):
        for a in range(128):
            if want(key(row, a, 64, 127)):
                out["main"][key(row, a, 64, 127)] = profile(exe, row, a, 64, 127, st)
        for a in CORNER_AMOUNTS:
            for t, m in CORNERS:
                if want(key(row, a, t, m)):
                    out["corner"][key(row, a, t, m)] = profile(exe, row, a, t, m, st)
    for a, t in AL_GRID:
        if want(f"al/{a}/{t}"):
            out["aliasing"][f"al/{a}/{t}"] = aliasing_all(exe, 0, a, t)
    for row in range(4):
        for amp in (8000, 16000, 26000):
            if want(f"alamp/r{row}/{amp}"):
                out["alias_amp"][f"alamp/r{row}/{amp}"] = aliasing(exe, row, 127, 64, amp)
    for a in (32, 64, 127):
        if want(f"steps/{a}"):
            out["steps"][f"steps/{a}"] = ds.step_peaks(exe, "FDRV", a)
    return out


def meta():
    return dict(what="black-box profile of FDRV (DRIVE TYPE 1), measured outputs only", tool="tools/drive_profile.py",
                fs=FS, n=N, settle=SETTLE, sine_hz=ds.odd_bin(220, N) * FS / N, sine_amps=list(AMPS),
                chord_hz=list(CHORD_HZ), chord_amp=2600, bark_edges_hz=list(BARK_EDGES), floor_db=FLOOR,
                rows="0..3 Rhodes drive rows; the bar types run row 0", corners_tone_mix=CORNERS,
                corner_amounts=CORNER_AMOUNTS, alias_grid=AL_GRID, tol_db=TOL_DB)


def _flat(d, p=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _flat(v, f"{p}{k}.")
    elif isinstance(d, (list, tuple)):
        for i, v in enumerate(d):
            yield from _flat(v, f"{p}{i}.")
    else:
        yield p, d


def compare(a, b):
    """list of (path, a, b) beyond TOL_DB"""
    fa, fb = dict(_flat(a)), dict(_flat(b))
    bad = [(k, fa.get(k), fb.get(k)) for k in set(fa) | set(fb) if k not in fa or k not in fb]
    bad += [(k, fa[k], fb[k]) for k in fa if k in fb and abs(fa[k] - fb[k]) > TOL_DB]
    return bad


def sample_keys(js):
    ks = set()
    for row in range(4):
        for a in (0, 1, 37, 100, 127):
            ks.add(key(row, a, 64, 127))
    for k in js["corner"]:
        if k.endswith("a64/t0/m127") or k.endswith("a120/t64/m0") or k.endswith("a16/t127/m127"):
            ks.add(k)
    ks |= {"al/32/0", "al/127/64", "alamp/r2/16000", "steps/64"}
    return ks


def check(full):
    js = json.load(open(JSON))
    sel = None if full else sample_keys(js)
    exe = ds.build([], "prof")
    try:
        got = measure(exe, sel)
    finally:
        os.remove(exe)
    bad = []
    for sec in ("main", "corner", "aliasing", "alias_amp", "steps"):
        part = {k: js[sec][k] for k in got[sec]}
        if sel is not None:
            assert got[sec].keys() == part.keys()
        bad += [(f"{sec}.{p}", x, y) for p, x, y in compare(got[sec], part)]
    n = sum(len(v) for v in got.values())
    for b in bad[:20]:
        print("MISMATCH", *b)
    print(f"drive_profile --check: {n} entries, {len(bad)} mismatches (tol {TOL_DB} dB)")
    return 1 if bad else 0


def plots(js, d):
    from PIL import Image, ImageDraw
    os.makedirs(d, exist_ok=True)

    def plot(name, title, series, xs, ylab):
        W, H, L, B, T = 900, 520, 70, 50, 40
        im = Image.new("RGB", (W, H), "white")
        dr = ImageDraw.Draw(im)
        ys = [v for s in series.values() for v in s]
        y0, y1 = min(ys), max(ys)
        y1 = y1 if y1 > y0 else y0 + 1
        x0, x1 = min(xs), max(xs)
        px = lambda x: L + (x - x0) / (x1 - x0) * (W - L - 20)
        py = lambda y: H - B - (y - y0) / (y1 - y0) * (H - B - T)
        dr.rectangle([L, T, W - 20, H - B], outline="black")
        dr.text((L, 10), f"{title}   y: {ylab} [{y0:.1f} .. {y1:.1f}]   x: {x0} .. {x1}", fill="black")
        cols = ["#c00", "#06c", "#080", "#a0a", "#880", "#088", "#555", "#f80", "#04a"]
        for i, (lab, s) in enumerate(series.items()):
            c = cols[i % len(cols)]
            dr.line([(px(x), py(y)) for x, y in zip(xs, s)], fill=c, width=2)
            dr.text((W - 110, T + 4 + 12 * i), lab, fill=c)
        im.save(os.path.join(d, name))

    am = list(range(128))
    for row in range(4):
        m = js["main"]
        plot(f"gain_row{row}.png", f"FDRV row {row}: gain / peak vs AMOUNT",
             {"sine -12 gain": [m[key(row, a, 64, 127)]["g"][0] for a in am],
              "sine -24 gain": [m[key(row, a, 64, 127)]["g"][1] for a in am],
              "chord gain": [m[key(row, a, 64, 127)]["cg"] for a in am],
              "sine -12 peak": [m[key(row, a, 64, 127)]["pk"][0] for a in am]}, am, "dB / dBFS")
        plot(f"harm_row{row}.png", f"FDRV row {row}: H2..H9 vs AMOUNT (sine -12 dBFS, dB re H1)",
             {f"H{h}": [m[key(row, a, 64, 127)]["h"][0][h - 2] for a in am] for h in range(2, 10)}, am, "dB")
    m = js["main"]
    bk = {f"a{a}": m[key(0, a, 64, 127)]["bark"] for a in (8, 32, 64, 127)}
    plot("bark_row0.png", "FDRV row 0: chord critical-band energy (dB re input total) at AMOUNT 8/32/64/127",
         bk, list(range(24)), "dB")
    print("plots in", d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=JSON)
    ap.add_argument("--plots", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check(a.full)
    exe = ds.build([], "prof")
    try:
        res = measure(exe)
    finally:
        os.remove(exe)
    res = dict(meta=meta(), **res)
    with open(a.out, "w") as f:
        json.dump(res, f, separators=(",", ":"))
    print("wrote", a.out, os.path.getsize(a.out), "bytes")
    if a.plots:
        plots(res, a.plots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
