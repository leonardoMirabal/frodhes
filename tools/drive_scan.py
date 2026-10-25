#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE measured on the firmware's own code (what host renders cannot show: aliasing, the low band under a chord,
the level and peak per AMOUNT), for any drive TYPE (params.c N_DRV: FDRV, the classic drive, and drive.c's TYPEs).

tests/c/drive_scan.c includes fx.c whole and runs fx_drive on a signal written here, 256 frames per call as
fx_process does; integer-exact and deterministic. The analysis is here (numpy):

- aliasing: sines C5 .. C8 in semitone steps (a stepped log sweep) and two-tones (harmonics 4 and 5 of 109 / 219 / 440
  / 879 Hz). Every tone is bin-exact over the analysis window (N = 32768, an odd bin: the window holds a whole number
  of periods and no folded harmonic lands on the harmonic grid), after a settle. "folded" = the energy off the
  harmonic grid, 20 Hz .. 20 kHz, re the energy on it (dB); "below f0" = the part of it under the fundamental, where it
  is least masked. The floor is the rounded input's own (the 16-bit sine is not exactly a sine).
  Also a continuous log chirp C5 -> C8 (2 s): the energy under 0.8 x the instantaneous f0, per STFT frame, re the frame.
- low band: a bass sine (41 / 55 / 82 / 110 Hz) under a C4-E4-G4 chord, bin-exact (N = 65536): the 30 .. 120 Hz band
  of the output vs the dry input (dB), the bass tone itself vs dry (dB), the other products in 30 .. 120 Hz re the
  bass (the IMD that lands in the low band, dB), and the difference from dry in that band re the dry band (dB).
- levels: AMOUNT 0, 8 .. 120, 127: a 440 Hz sine at -12 dBFS and a 5-note chord: out RMS re in (dB), out peak (dBFS).

  python3 tools/drive_scan.py                      FDRV and every TYPE that is not a stub -> build/drive_scan/<type>/
  python3 tools/drive_scan.py --type KHLON --cflags=-DFOO=0 --tag khlon-foo     a variant build of fx.c

Each run writes report.txt and results.json (the tests read the JSON). A TYPE is a stub while its file
(firmware/src/drv_<type>.c) says DRV_STUB: it passes the dry signal.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import itertools

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "drive_scan")
FS = 44100


def type_names(tree=None):
    """params.c N_DRV: the P_DRV_SW value of each name (OFF 0, FDRV 1, ...); CLASSIC is FDRV's other name"""
    import re
    src = open(os.path.join(tree or ROOT, "firmware", "src", "params.c")).read()
    names = re.findall(r'"([^"]+)"', re.search(r"N_DRV\[\] = \{([^}]*)\}", src).group(1))
    return dict({n: i for i, n in enumerate(names)}, CLASSIC=1)


TYPES = type_names()
N_AL, SETTLE = 32768, 8192
_seq = itertools.count()


def is_stub(typ, tree=None):
    """a TYPE whose file still says DRV_STUB (it passes the dry signal); OFF and FDRV never are"""
    if TYPES[typ] <= 1:
        return False
    p = os.path.join(tree or ROOT, "firmware", "src", f"drv_{typ.lower()}.c")
    return "DRV_STUB" in open(p).read()


def present(tree=None):
    """the TYPEs with a model (FDRV first)"""
    return ["FDRV"] + [t for t, v in TYPES.items() if v >= 2 and not is_stub(t, tree)]


def build(cflags=(), tag="default", tree=None):
    """the harness on this tree's fx.c, or on another checkout's (tree: a path; e.g. lane A's worktree, read only)"""
    tree = tree or ROOT
    os.makedirs(os.path.join(OUT, "bin"), exist_ok=True)
    exe = os.path.join(OUT, "bin", f"drive_scan-{tag}-{os.getpid()}")
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", *cflags,
                    "-I" + os.path.join(tree, "firmware", "src"), "-I" + os.path.join(tree, "firmware", "gen"),
                    "-o", exe, os.path.join(ROOT, "tests", "c", "drive_scan.c"), "-lm"], check=True)
    return exe


def render(exe, x, params, switches=(), trace=False):
    """x: int samples; params: {NAME: v}; switches: [(block, NAME, v)]. Returns (y int64, cost_us, peak), and with
    trace a 4th: per block (cost_us, on, next) (drive.c's TYPE that runs and the one chosen that waits for it to
    fade out, 0: none)"""
    tmp = os.path.join(OUT, "tmp")
    os.makedirs(tmp, exist_ok=True)
    k = f"{os.getpid()}-{next(_seq)}"
    fi, fo = os.path.join(tmp, f"in{k}.raw"), os.path.join(tmp, f"out{k}.raw")
    np.asarray(x, dtype="<i4").tofile(fi)
    args = [f"{n}={v}" for n, v in params.items()] + [f"@{b}:{n}={v}" for b, n, v in switches]
    env = dict(os.environ, DRIVE_SCAN_TRACE="1") if trace else None
    p = subprocess.run([exe, fi, fo, *args], check=True, capture_output=True, text=True, env=env)
    r = p.stdout.split()
    y = np.fromfile(fo, dtype="<i4").astype(np.int64)
    os.remove(fi); os.remove(fo)
    if trace:
        tr = [tuple(int(v) for v in ln.split()[3::2]) for ln in p.stderr.splitlines() if ln.startswith("blk ")]
        return y, int(r[1]), int(r[3]), tr
    return y, int(r[1]), int(r[3])


def params_for(typ, amount, tone=64, mix=127, row=0):
    return {"DRV_SW": TYPES[typ], "DRIVE": amount, "DRV_TONE": tone, "DRV_MIX": mix, "DRIVE_ROW": row}


def odd_bin(hz, n):
    k = int(round(hz * n / FS))
    return k if k % 2 else k + 1


def sine(k, n, amp, length, phase=0.0):
    t = np.arange(length)
    return amp * np.sin(2 * np.pi * k * t / n + phase)


def db(p, ref):
    return 10 * math.log10(max(p, 1e-30) / max(ref, 1e-30))


def offgrid(y, q, n):
    """(folded dB, below-f0 dB) of the last n samples: energy off the grid of multiples of bin q, 20 Hz .. 20 kHz,
    re the energy on it (bins q, 2q, ...)"""
    Y = np.abs(np.fft.rfft(y[-n:].astype(float))) ** 2
    b = np.arange(len(Y))
    f = b * FS / n
    band = (f >= 20) & (f <= 20000)
    grid = (b % q) == 0
    on = Y[grid & (b > 0)].sum()
    return db(Y[band & ~grid].sum(), on), db(Y[band & ~grid & (b < q)].sum(), on)


NOTES_C5_C8 = [(f"{'C C# D D# E F F# G G# A A# B'.split()[i % 12]}{5 + i // 12}", 523.2511 * 2 ** (i / 12))
               for i in range(37)]


def aliasing_tones(exe, prm, amp=8000):
    """the stepped sweep C5 .. C8: one render, a segment per note (settle + N)"""
    segs, ks, floors = [], [], []
    for _, hz in NOTES_C5_C8:
        k = odd_bin(hz, N_AL)
        s = np.round(sine(k, N_AL, amp, SETTLE + N_AL)).astype(np.int64)
        segs.append(s); ks.append(k)
        floors.append(offgrid(s, k, N_AL)[0])
    y, cost, _ = render(exe, np.concatenate(segs), prm)
    L = SETTLE + N_AL
    rows = []
    for i, ((name, hz), k) in enumerate(zip(NOTES_C5_C8, ks)):
        fo, bl = offgrid(y[i * L:(i + 1) * L], k, N_AL)
        rows.append(dict(note=name, hz=round(k * FS / N_AL, 1), folded_db=round(fo, 2), below_f0_db=round(bl, 2)))
    return rows, max(floors), cost


def aliasing_twotone(exe, prm, amp=8000):
    rows = []
    for fq in (109.0, 219.0, 440.0, 879.0):
        q = odd_bin(fq, N_AL)
        s = np.round(sine(4 * q, N_AL, amp / 2, SETTLE + N_AL) + sine(5 * q, N_AL, amp / 2, SETTLE + N_AL, 0.7)).astype(np.int64)
        y, _, _ = render(exe, s, prm)
        fo, bl = offgrid(y, q, N_AL)
        rows.append(dict(f1=round(4 * q * FS / N_AL, 1), f2=round(5 * q * FS / N_AL, 1), folded_db=round(fo, 2),
                         below_f0_db=round(bl, 2)))
    return rows


def chirp_subf0(exe, prm, amp=8000, secs=2.0):
    """a continuous log chirp C5 -> C8; per 4096-sample frame (hop 1024), the energy under 0.8 f0 (and over 20 Hz)
    re the frame's energy; returns (max dB, mean dB over the frames)"""
    f0, f1, n = 523.2511, 4186.009, int(secs * FS)
    t = np.arange(n) / FS
    ph = 2 * np.pi * f0 * secs / math.log(f1 / f0) * (np.exp(t / secs * math.log(f1 / f0)) - 1)
    x = np.round(amp * np.sin(ph)).astype(np.int64)
    y, _, _ = render(exe, x, prm)
    win, F, out = np.hanning(4096), np.fft.rfftfreq(4096, 1 / FS), []
    for i in range(4096, n - 4096, 1024):
        fi = f0 * math.exp((i + 2048) / FS / secs * math.log(f1 / f0))
        Y = np.abs(np.fft.rfft(y[i:i + 4096] * win)) ** 2
        out.append(db(Y[(F > 20) & (F < 0.8 * fi)].sum(), Y.sum()))
    return round(max(out), 2), round(10 * math.log10(np.mean(10 ** (np.array(out) / 10))), 2)


CHORD_HZ = (261.63, 329.63, 392.0)


def lowband(exe, prm, bass_hz, bass_amp=6000, chord_amp=4000, n=65536):
    kb = int(round(bass_hz * n / FS))
    x = sine(kb, n, bass_amp, SETTLE + n)
    for i, hz in enumerate(CHORD_HZ):
        x = x + sine(int(round(hz * n / FS)), n, chord_amp, SETTLE + n, 1.1 * i)
    x = np.round(x).astype(np.int64)
    y, _, _ = render(exe, x, prm)
    X, Y = np.fft.rfft(x[-n:].astype(float)), np.fft.rfft(y[-n:].astype(float))
    f = np.arange(len(X)) * FS / n
    band = (f >= 30) & (f <= 120)
    other = band.copy(); other[kb] = False
    pX, pY = (np.abs(X[band]) ** 2).sum(), (np.abs(Y[band]) ** 2).sum()
    return dict(bass_hz=round(kb * FS / n, 2),
                band_vs_dry_db=round(db(pY, pX), 2),
                bass_vs_dry_db=round(db(abs(Y[kb]) ** 2, abs(X[kb]) ** 2), 2),
                imd_in_band_db=round(db((np.abs(Y[other]) ** 2).sum(), abs(Y[kb]) ** 2), 2),
                diff_from_dry_db=round(db((np.abs(Y[band] - X[band]) ** 2).sum(), pX), 2))


AMOUNTS = list(range(0, 128, 8)) + [127]


def levels(exe, typ, tone=64, mix=127, row=0):
    n = 16384
    k = odd_bin(440, n)
    s = np.round(sine(k, n, 8000, SETTLE + n)).astype(np.int64)
    ch = sum(sine(int(round(hz * n / FS)), n, 2600, SETTLE + n, 0.9 * i)
             for i, hz in enumerate((130.81, 261.63, 329.63, 392.0, 523.25)))
    ch = np.round(ch).astype(np.int64)
    rows = []
    for d in AMOUNTS:
        r = dict(amount=d)
        for name, x in (("sine", s), ("chord", ch)):
            y, cost, pk = render(exe, x, params_for(typ, d, tone, mix, row))
            xi, yo = x[-n:].astype(float), y[-n:].astype(float)
            r[name + "_gain_db"] = round(10 * math.log10(np.mean(yo * yo) / np.mean(xi * xi) + 1e-30), 2)
            r[name + "_peak_dbfs"] = round(20 * math.log10(max(np.abs(yo).max(), 1) / 32767), 2)
        r["cost_us"] = cost
        rows.append(r)
    return rows


def step_peaks(exe, typ, amount=127, tone=64):
    """level steps -30 .. +6 dBFS (a 440 Hz sine): out peak (dBFS) and RMS gain (dB): the curve's shape, the headroom"""
    n, k, rows = 16384, odd_bin(440, 16384), []
    for lv in range(-30, 7, 6):
        x = np.round(sine(k, n, 32767 * 10 ** (lv / 20), SETTLE + n)).astype(np.int64)
        y, _, _ = render(exe, x, params_for(typ, amount, tone))
        yo, xi = y[-n:].astype(float), x[-n:].astype(float)
        rows.append(dict(in_dbfs=lv, out_peak_dbfs=round(20 * math.log10(max(np.abs(yo).max(), 1) / 32767), 2),
                         gain_db=round(10 * math.log10(np.mean(yo * yo) / np.mean(xi * xi)), 2)))
    return rows


# ---- the scan

def scan(typ, cflags=(), tag=None, quick=False, tree=None):
    tag = tag or typ.lower()
    exe = build(cflags, tag, tree)
    res = dict(type=typ, cflags=list(cflags), aliasing={}, twotone={}, chirp={}, lowband={}, levels=None, steps=None)
    grid = [(127, 127), (127, 64), (64, 64)] if quick else [(a, t) for a in (32, 64, 127) for t in (0, 64, 127)]
    for a, t in grid:
        rows, floor, cost = aliasing_tones(exe, params_for(typ, a, t))
        p = np.array([r["folded_db"] for r in rows])
        res["aliasing"][f"{a}/{t}"] = dict(rows=rows, max_db=round(float(p.max()), 2),
                                           mean_db=round(10 * math.log10(np.mean(10 ** (p / 10))), 2),
                                           max_below_f0_db=round(max(r["below_f0_db"] for r in rows), 2),
                                           input_floor_db=round(floor, 2), cost_us=cost)
        res["twotone"][f"{a}/{t}"] = aliasing_twotone(exe, params_for(typ, a, t))
        res["chirp"][f"{a}/{t}"] = chirp_subf0(exe, params_for(typ, a, t))
    for a in ((127,) if quick else (64, 127)):
        res["lowband"][str(a)] = [lowband(exe, params_for(typ, a), hz) for hz in (41.2, 55.0, 82.4, 110.0)]
    res["levels"] = levels(exe, typ)
    res["steps"] = step_peaks(exe, typ)
    os.remove(exe)
    return res


def report(res):
    L = [f"DRIVE scan: {res['type']}  {' '.join(res['cflags'])}".rstrip(), "",
         "Aliasing, stepped sweep C5..C8 at -12 dBFS (folded energy re harmonics, dB; worst / power mean / worst under f0)"]
    L.append(f"{'AMT/TONE':>9} {'worst':>7} {'mean':>7} {'<f0':>7} {'floor':>7}   two-tone 109/219/440/879 Hz x(4,5)"
             "      chirp <0.8f0 worst/mean")
    for k, v in res["aliasing"].items():
        tt = " ".join(f"{r['folded_db']:7.1f}" for r in res["twotone"][k])
        ch = res["chirp"][k]
        L.append(f"{k:>9} {v['max_db']:7.1f} {v['mean_db']:7.1f} {v['max_below_f0_db']:7.1f} {v['input_floor_db']:7.1f}"
                 f"   {tt}      {ch[0]:6.1f} {ch[1]:6.1f}")
    L += ["", "Worst notes at AMOUNT 127 / TONE 127 (folded dB):" if "127/127" in res["aliasing"] else ""]
    if "127/127" in res["aliasing"]:
        rows = sorted(res["aliasing"]["127/127"]["rows"], key=lambda r: -r["folded_db"])[:5]
        L.append("  " + ", ".join(f"{r['note']} {r['folded_db']:.1f}" for r in rows))
    L += ["", "Low band: bass sine (-14.7 dBFS) under C4-E4-G4 (-18.3 dBFS each), 30..120 Hz (dB)",
          f"{'AMT':>4} {'bass Hz':>8} {'band/dry':>9} {'bass/dry':>9} {'IMD/bass':>9} {'diff/dry':>9}"]
    for a, rows in res["lowband"].items():
        for r in rows:
            L.append(f"{a:>4} {r['bass_hz']:8.1f} {r['band_vs_dry_db']:9.2f} {r['bass_vs_dry_db']:9.2f} "
                     f"{r['imd_in_band_db']:9.1f} {r['diff_from_dry_db']:9.1f}")
    L += ["", "Level per AMOUNT (TONE 64, MIX 127): out RMS re in (dB), out peak (dBFS); cost_us = fx_cost_us()",
          f"{'AMT':>4} {'sine gain':>9} {'sine pk':>8} {'chord gain':>10} {'chord pk':>8} {'cost':>5}"]
    for r in res["levels"]:
        L.append(f"{r['amount']:4} {r['sine_gain_db']:9.2f} {r['sine_peak_dbfs']:8.2f} {r['chord_gain_db']:10.2f} "
                 f"{r['chord_peak_dbfs']:8.2f} {r['cost_us']:5}")
    L += ["", "Level steps at AMOUNT 127 (440 Hz sine): in dBFS -> out peak dBFS, RMS gain dB"]
    L.append("  " + "  ".join(f"{r['in_dbfs']:+d}: {r['out_peak_dbfs']:.1f} / {r['gain_db']:+.1f}" for r in res["steps"]))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", action="append", choices=[t for t in TYPES if t != "OFF"],
                    help="FDRV (CLASSIC), KHLON, ... (default: FDRV and every TYPE that is not a stub)")
    ap.add_argument("--cflags", default="", help="extra compiler flags for fx.c (e.g. -DFOO=0)")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--tree", default=None, help="measure another checkout's firmware/ (read only)")
    a = ap.parse_args()
    types = a.type or present(a.tree)
    for t in types:
        res = scan(t, a.cflags.split(), a.tag if len(types) == 1 else None, a.quick, a.tree)
        d = os.path.join(OUT, a.tag if (a.tag and len(types) == 1) else t.lower())
        os.makedirs(d, exist_ok=True)
        json.dump(res, open(os.path.join(d, "results.json"), "w"), indent=1)
        txt = report(res)
        open(os.path.join(d, "report.txt"), "w").write(txt)
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
