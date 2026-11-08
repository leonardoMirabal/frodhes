#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The dry engine's distortion: harmonics H2..H6 re H1 and the inharmonic residue of single notes, and the
intermodulation of loud chords, with the effects off. The must-pass gate against 0.9.9 (tests/py/test_dry_distortion.py;
the FM-1 counterpart: tools/hil_jack.py's "dry distortion" section).

Why (2026-11-08): the user heard heavy distortion at high velocity on the 0.9.10 Rhodes, effects off, on headphones;
3285 passing tests did not flag it. No test compared the dry engine's spectrum at v110..127 with a reference.

Cells (every one measured on the host build, 44.1 kHz):
  config    FXOFF (CC 111 127: the FX button's bypass) and RAW (CC 119 127: every effect and the limiter bypassed)
  TYPE      the four electric pianos (CC 80: 0 / 32 / 64 / 96)
  RESPONSE  CC 40 0 (A, pickup excursion) and 127 (B, velocity map); 0.9.9 has no CC 40 (ignored: one method)
  notes     keys 28 33 45 57 69 81 93 x velocities 64 96 110 127: H2..H6 re H1 (dB, each the peak within +-3 % of
            k f0 in a Hann window 150..650 ms after the strike) and "inh": the energy off every harmonic band
            (+-6 Hz or +-0.3 %) up to 20 kHz re the energy of H1 (the aliasing and any inharmonic product; the tine's own
            upper modes are in it too, so it is judged against 0.9.9, never alone)
  chords    4-note chords at v127 (low, mid, high): "imd", the energy off every harmonic band of the four notes re
            the energy of their fundamentals

Limits (judge): every value <= 0.9.9's same cell + 3 dB (a harmonic: or <= -60 dB re H1, never heard); and at v127 the absolute ceiling H3 re H1 <= -25 dB (the
limit min(base + 3, -25)). A cell where 0.9.9 itself is above -25 keeps base + 3 (0.9.9 is the reference).

  tools/dry_dist.py --bin BIN [--out FILE]          measure a host binary, write the cells (JSON)
  tools/dry_dist.py --save                          measure the host binary BIN (default build/host/frodhes) and write
                                                    tests/baselines/dry_dist_099.json (run it on a 0.9.9 build)
  tools/dry_dist.py --judge CELLS.json [--base B]   the table and the verdict (exit 1 on a failure)
"""
import argparse
import json
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE = os.path.join(ROOT, "tests", "baselines", "dry_dist_099.json")
FS = 44100
KEYS = (28, 33, 45, 57, 69, 81, 93)
VELS = (64, 96, 110, 127)
TYPES = (("ST77", 0), ("SUIT", 32), ("FELT", 64), ("DYNO", 96))
METHODS = (("A", 0), ("B", 127))
CONFIGS = (("FXOFF", 111), ("RAW", 119))
CHORDS = (("low", (33, 40, 45, 49)), ("mid", (57, 60, 64, 67)), ("high", (69, 72, 76, 79)))
STEP, HOLD = 1500, 800            # ms: a note every STEP, released after HOLD
W0, W1 = 0.15, 0.65               # s: the analysis window after the strike
BAND = 0.03                       # the harmonic peaks: within +-3 % of k f0
IBW, IREL = 6.0, 0.003            # inh / imd: a harmonic's band, +-6 Hz (the 0.5 s Hann window's main lobe is +-4 Hz)
                                  # or +-0.3 % (the pickup's products are exact multiples of the tine's frequency)
MARGIN, CEIL_H3 = 3.0, -25.0
FLOOR = -60.0                     # a harmonic below -60 dB re H1 passes whatever 0.9.9 had (H5 at -90 dB moves by
                                  # more than 3 dB with any change and is not heard); inh / imd have no floor


def f0(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def events():
    """[(kind, label, t_ms, notes, vel)] in the order one script plays them"""
    ev, t = [], 400
    for k in KEYS:
        for v in VELS:
            ev.append(("note", f"{k}/{v}", t, (k,), v))
            t += STEP
    for name, ns in CHORDS:
        ev.append(("chord", name, t, ns, 127))
        t += STEP + 500
    return ev, t


def script(cfg_cc, type_cc, meth_cc):
    ev, end = events()
    ls = [f"100 midi 0xB0 {cfg_cc} 127", f"110 midi 0xB0 80 {type_cc}", f"120 midi 0xB0 40 {meth_cc}"]
    for _, _, t, ns, v in ev:
        for n in ns:
            ls.append(f"{t} midi 0x90 {n} {v}")
        for n in ns:
            ls.append(f"{t + HOLD} midi 0x80 {n} 0")
    ls.append(f"{end} end")
    return ls


def render(binary, tag, lines, outdir):
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, tag)
    with open(out + ".txt", "w") as f:
        f.write("\n".join(sorted(lines, key=lambda l: int(l.split()[0]))) + "\n")
    e = dict(os.environ)
    e.pop("FRODHES_FLASH", None)
    subprocess.run([binary, out, out + ".txt"], capture_output=True, text=True, env=e, check=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    return x.mean(1)


def spectrum(x, fs=FS):
    s = x * np.hanning(len(x))
    p = np.abs(np.fft.rfft(s)) ** 2
    return p, np.fft.rfftfreq(len(x), 1 / fs)


def harm_cells(seg, fund, fs=FS):
    """H2..H6 re H1 (dB, band peaks) and inh: the power off the harmonic bands re the power of H1 (dB)"""
    p, fr = spectrum(seg, fs)
    pk = []
    for k in range(1, 7):
        m = (fr > k * fund * (1 - BAND)) & (fr < k * fund * (1 + BAND))
        pk.append(p[m].max() if m.any() else 1e-30)
    h = [10 * np.log10(pk[k] / pk[0] + 1e-30) for k in range(1, 6)]
    return h, inharm(p, fr, [fund])


def inharm(p, fr, funds):
    """the power off every harmonic band of the fundamentals (30 Hz .. 20 kHz) re the power in the fundamentals' own
    bands (H1: so a distortion that fills the harmonic bands does not lower it), dB"""
    use = (fr > 30) & (fr < 20000)
    on, h1 = np.zeros(len(fr), bool), np.zeros(len(fr), bool)
    for fu in funds:
        h1 |= np.abs(fr - fu) < max(IBW, fu * IREL)
        for k in range(1, int(20000 / fu) + 1):
            on |= np.abs(fr - k * fu) < max(IBW, k * fu * IREL)
    a, b = p[h1].sum(), p[use & ~on].sum()
    return float(10 * np.log10(b / (a + 1e-30) + 1e-30))


def measure_wave(x, t0=0.0, fs=FS):
    """the cells of one script's recording (x mono at fs; t0: where the script's 0 ms is in x, s). Every cell is a
    ratio, so the FM-1's USB at 48 kHz is judged against the host's 44.1 kHz reference as it is"""
    ev, _ = events()
    out = {}
    for kind, label, t, ns, v in ev:
        i0, i1 = int((t0 + t / 1000 + W0) * fs), int((t0 + t / 1000 + W1) * fs)
        seg = x[i0:i1]
        if kind == "note":
            h, inh = harm_cells(seg, f0(ns[0]), fs)
            out[label] = dict(H2=h[0], H3=h[1], H4=h[2], H5=h[3], H6=h[4], inh=inh)
        else:
            p, fr = spectrum(seg, fs)
            out["chord/" + label] = dict(imd=inharm(p, fr, [f0(n) for n in ns]))
    return out


def tags():
    """[(tag, config CC, TYPE CC value, CC 40 value)]: one script each"""
    return [(f"{c}-{t}-{m}", ccc, tcc, mcc) for c, ccc in CONFIGS for t, tcc in TYPES for m, mcc in METHODS]


def device_events(tag):
    """the same script for the FM-1 (tools/hil_jack.py --dist): [(t_s, kind, a, b)] and the take's length (s)"""
    ev = []
    for l in script(*next(x for x in tags() if x[0] == tag)[1:]):
        f = l.split()
        if f[1] == "end":
            return sorted(ev, key=lambda e: e[0]), int(f[0]) / 1000 + 0.3
        st, a, b = int(f[2], 16), int(f[3]), int(f[4])
        ev.append((int(f[0]) / 1000, {0xB0: "cc", 0x90: "on", 0x80: "off"}[st], a, b))


def measure_tag(binary, tag, outdir):
    _, ccc, tcc, mcc = next(x for x in tags() if x[0] == tag)
    return measure_wave(render(binary, tag, script(ccc, tcc, mcc), outdir))


def measure(binary, outdir=None):
    """every cell: {config/type/method: {note or chord: {metric: dB}}}"""
    outdir = outdir or os.path.join(ROOT, "build", "dry_dist", os.path.basename(os.path.dirname(binary)) or "x")
    return {t[0]: measure_tag(binary, t[0], outdir) for t in tags()}


def judge(cells, base, methods_base="A"):
    """[(cell, metric, value, base, limit, ok)]; the base has one method (0.9.9): every method is held to it"""
    rows = []
    for tag, notes in cells.items():
        cfg, ty, _ = tag.split("-")
        bt = base.get(f"{cfg}-{ty}-{methods_base}") or {}
        for label, ms in notes.items():
            bn = bt.get(label, {})
            for m, v in ms.items():
                b = bn.get(m)
                if b is None:
                    rows.append((f"{tag} {label}", m, v, None, None, False))
                    continue
                lim = b + MARGIN
                if m.startswith("H"):
                    lim = max(lim, FLOOR)
                if m == "H3" and label.endswith("/127"):
                    lim = min(b + MARGIN, CEIL_H3) if b <= CEIL_H3 else b + MARGIN
                rows.append((f"{tag} {label}", m, v, b, lim, bool(v <= lim + 1e-9)))
    return rows


def table(rows, only_bad=False, worst=40):
    bad = [r for r in rows if not r[5]]
    lines = [f"{len(rows)} values, {len(bad)} over the limit"]
    show = bad if only_bad else sorted(rows, key=lambda r: (r[4] - r[2]) if r[4] is not None else -99)[:worst]
    for c, m, v, b, lim, ok in sorted(show, key=lambda r: (r[4] - r[2]) if r[4] is not None else -99)[:worst]:
        lines.append(f"  {'ok  ' if ok else 'FAIL'} {c:28s} {m:4s} {v:7.1f} dB   0.9.9 {b if b is None else round(b, 1)!s:>6}"
                     f"   limit {lim if lim is None else round(lim, 1)!s:>6}   margin {'' if lim is None else round(lim - v, 1)}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "host", "frodhes"))
    ap.add_argument("--out")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--judge")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--all", action="store_true", help="--judge: every failing value (else the 40 closest)")
    a = ap.parse_args()
    if a.judge:
        rows = judge(json.load(open(a.judge)), json.load(open(a.base)))
        print(table(rows, only_bad=a.all, worst=10000 if a.all else 40))
        sys.exit(0 if all(r[5] for r in rows) else 1)
    cells = measure(os.path.abspath(a.bin))
    path = BASE if a.save else a.out
    if path:
        json.dump(cells, open(path, "w"), indent=1, sort_keys=True)
        print(path)
    else:
        print(json.dumps(cells, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
