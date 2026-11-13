#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The dry engine's distortion: harmonics H2..H6 re H1 and the inharmonic residue of single notes, and the
intermodulation of loud chords, with the effects off. The must-pass gate against 0.9.11 (tests/py/test_dry_distortion.py;
the FM-1 counterpart: tools/hil_jack.py's "dry distortion" section).

Why (2026-11-08): the user heard heavy distortion at high velocity on the 0.9.10 Rhodes, effects off, on headphones;
3285 passing tests did not flag it. No test compared the dry engine's spectrum at v110..127 with a reference.

Cells (every one measured on the host build, 44.1 kHz):
  config    FXOFF (CC 111 127: the FX button's bypass) and RAW (CC 119 127: every effect and the limiter bypassed)
  TYPE      the four electric pianos (CC 80: 0 / 32 / 64 / 96)
  RESPONSE  CC 40 0 (A, pickup excursion) and 127 (B, velocity map); both judged against the base's A
            (one method, as when 0.9.9 had no CC 40)
  notes     keys 28 33 45 57 69 81 93 x velocities 64 96 110 127: H2..H6 re H1 (dB, each the peak within +-3 % of
            k f0 in a Blackman-Harris window 150..650 ms after the strike) and "inh": the energy off every harmonic band
            (+-15 Hz or +-0.3 %, around k x the f0 measured on H1) up to 20 kHz re the energy of H1 (the aliasing and any inharmonic product; the tine's own
            upper modes are in it too, so it is judged against 0.9.11, never alone)
  chords    4-note chords at v127 (low, mid, high): "imd", the energy off every harmonic band of the four notes re
            the energy of their fundamentals
  attack    every note and key cell also aH2..aH6: H2..H6 re H1 in the strike's own window, 0..150 ms (2026-11-08: the
            user heard the hard key presses distort; the 150..650 ms window had left the attack out)
  keys      the FM-1's own keys F3 A3 C4 E4 G4 C5 through the key path at their hardest (tests/host.c "vkey K 200": the
            reading under any floor, keyvel.c's curve at MEDIUM, its 127): "key<K>/max", judged as v127. Host only
            (nothing presses the FM-1's keys in tools/hil_jack.py --dist)

Limits (judge): every value <= 0.9.11's same cell + 3 dB, H2 and aH2 + 1.5 dB (or <= -60 dB re H1: never heard, the
path's floor); and at v127 and on the keys' cells the absolute ceiling H3 and aH3 re H1 <= -25 dB (the limit
min(base + 3, -25)). A cell where 0.9.11 itself is above -25 keeps base + 3 (0.9.11 is the reference).

  tools/dry_dist.py --bin BIN [--out FILE]          measure a host binary, write the cells (JSON)
  tools/dry_dist.py --save                          measure the host binary BIN (default build/host/frodhes) and write
                                                    tests/baselines/dry_dist_0911.json (run it on a 0.9.11 build)
  tools/dry_dist.py --judge CELLS.json [--base B]   the table and the verdict (exit 1 on a failure)
  --hq                                              the Move's HQ build instead: --save writes (and --judge reads)
                                                    tests/baselines/dry_dist_0911_hq.json; build one with --build-hq

The Move (and the plugin's HQ slots) have their own reference, dry_dist_0911_hq.json: 0.9.11 (279a7aff) built with the
Move's defines and HQ_DEFAULT=1 (HQ_DEFS). The FM-1's does not hold there: FX on the Move keeps the compressor
(FR_BYP_KEEP_CMP: the FXOFF chords), and the HQ voice has its own pickup from key 85 and its own pulse at every
velocity (key 93's H3 differs by 1-2 dB at v64..96).
  tools/dry_dist.py --build-hq OUT [--src TREE]     compile the HQ host build of TREE (default: this one) to OUT
"""
import argparse
import json
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE = os.path.join(ROOT, "tests", "baselines", "dry_dist_0911.json")
BASE_HQ = os.path.join(ROOT, "tests", "baselines", "dry_dist_0911_hq.json")
HQ_DEFS = ("-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1", "-DHQ_DEFAULT=1")   # (the Makefile's
                                  # MOVE_DEFS, the HQ Rhodes on: the Move as it loads; make test-move's host-hq build)
FS = 44100
# The TYPE cases' base (2026-11-10, the proposed 19): a TYPE is tested on its engine's voicing, not on whatever the boot
# preset F01 holds. Sent after the boot and before the TYPE: the voicing knobs at their params.c defaults, the
# compressor, the reverb and LEVEL as F01 had them when the 0.9.9 baselines and the ceilings were set (rc2: NOISE 48,
# 2:1 THR 50 ATTACK 80 RELEASE 72 MAKE-UP 8, HALL 18 LOW CUT 70, LEVEL 52). (cc, value); COMP's switch is left as the
# boot has it (on: CC 106 would wake it). Shared with tools/release_diff.py and tools/quick.py's TYPE level cases.
TYPE_BASE = ((28, 64), (29, 64), (30, 64), (31, 64), (102, 64), (103, 64), (104, 48), (105, 0),     # TONE .. SPREAD
             (107, 50), (112, 16), (108, 80), (109, 72), (113, 8),                                # the compressor
             (91, 18), (95, 32), (69, 70), (92, 64), (93, 64), (94, 0), (73, 0), (68, 64),       # the reverb
             (110, 52))                                                                           # LEVEL
TYPE_BASE_T = 105                  # ms: after the boot and the config's CC at 100, before the TYPE at 110


def type_base_lines(t=TYPE_BASE_T):
    return [f"{t} midi 0xB0 {c} {v}" for c, v in TYPE_BASE]


KEYS = (28, 33, 45, 57, 69, 81, 93)
VELS = (64, 96, 110, 127)
TYPES = (("ST77", 0), ("SUIT", 32), ("FELT", 64), ("DYNO", 96))
METHODS = (("A", 0), ("B", 127))
CONFIGS = (("FXOFF", 111), ("RAW", 119))
CHORDS = (("low", (33, 40, 45, 49)), ("mid", (57, 60, 64, 67)), ("high", (69, 72, 76, 79)))
STEP, HOLD = 1500, 800            # ms: a note every STEP, released after HOLD
W0, W1 = 0.15, 0.65               # s: the analysis window after the strike
A0, A1 = 0.0, 0.15                # s: the attack's window (aH2..aH6: the strike itself, which W0..W1 leaves out)
KEYK = (0, 4, 7, 11, 14, 19)      # the FM-1's own keys (analog: F3 A3 C4 E4 G4 C5 = notes 53 + k) through the key path
KEY_HARD = 200                    # their driven reading: under any floor (KV_FLOOR_MIN 217): the hardest press, the most
                                  # the key path delivers at the TOUCH in force (MEDIUM)
MARGIN_H2 = 1.5                   # H2 / aH2: <= 0.9.11 + 1.5 dB (the pickup's even-order growth, what a hard key press
                                  # brings up first)
BAND = 0.03                       # the harmonic peaks: within +-3 % of k f0
IBW, IREL = 15.0, 0.003           # inh / imd: a harmonic's band, +-15 Hz (the window's main lobe +-8 Hz, and a harmonic's own envelope: the harmonic decay glide from 0.4 s spreads H2 +-12 Hz)
                                  # or +-0.3 % (the pickup's products are exact multiples of the tine's frequency)
MARGIN, CEIL_H3 = 3.0, -25.0
FLOOR = -60.0                     # any value below -60 dB re H1 passes whatever 0.9.11 had: H5 at -90 dB moves by more
                                  # than 3 dB with any change and is not heard, and inh / imd down there are the path's
                                  # floor (the USB stream requantized to 16 bits at 48 kHz: 0.9.9's -91 dB chord imd read
                                  # -74 through the dry run's 44.1 -> 48 kHz path, 2026-11-08)


def f0(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def events(keys=True):
    """[(kind, label, t_ms, notes, vel)] in the order one script plays them; keys: and the FM-1's own keys at their
    hardest (kind "key", vel = the key index: the velocity is what keyvel.c's curve makes of KEY_HARD), last"""
    ev, t = [], 400
    for k in KEYS:
        for v in VELS:
            ev.append(("note", f"{k}/{v}", t, (k,), v))
            t += STEP
    for name, ns in CHORDS:
        ev.append(("chord", name, t, ns, 127))
        t += STEP + 500
    for k in KEYK if keys else ():
        ev.append(("key", f"key{k}/max", t, (53 + k,), k))
        t += STEP
    return ev, t


def script(cfg_cc, type_cc, meth_cc, keys=True):
    ev, end = events(keys)
    ls = [f"100 midi 0xB0 {cfg_cc} 127"] + type_base_lines() + [f"110 midi 0xB0 80 {type_cc}",
                                                                  f"120 midi 0xB0 40 {meth_cc}"]
    for kind, _, t, ns, v in ev:
        if kind == "key":             # the host's key path: the press lands with the reading (tests/host.c vkey)
            ls += [f"{t - 20} vkey {v} {KEY_HARD}", f"{t} keys {1 << v:#x}", f"{t + HOLD} keys 0"]
            continue
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


def spectrum(x, fs=FS, pad=1):
    """the power spectrum through a 4-term Blackman-Harris window (sidelobes -92 dB, main lobe +-4 bins = +-8 Hz at
    0.5 s). Hann's sidelobes (-31 dB at +-6 Hz) put the fundamental's own skirt in inh: at key 93 that skirt WAS inh
    (-45 dB at the host's clock), and an f0 a fraction of a bin off (the FM-1's clock on USB) moved it by 6 dB"""
    from scipy.signal.windows import blackmanharris
    s = x * blackmanharris(len(x))
    n = len(x) * pad                  # (zero-padded: the attack's short window has bins 6.7 Hz apart, wider than
                                      # +-3 % of a low key's f0; padded, every band has its peak)
    p = np.abs(np.fft.rfft(s, n)) ** 2
    return p, np.fft.rfftfreq(n, 1 / fs)


def tuned(p, fr, funds):
    """the fundamentals as they sound: each nominal f0 times the ratio measured on the H1 peaks (the median over the
    notes; a peak within +-3 %, refined by a parabola on the log power). The FM-1's clock is a few hundred ppm off the
    host's on USB (the device's 44.1 kHz against the computer's 48 kHz), so its harmonics sit k x that many ppm off the
    nominal k f0: at key 93, 500 ppm moves H11 by 10 Hz, out of its band, and inh read 10 dB worse with nothing
    in the sound (FM-1 2026-11-08: -33.6 dB at every velocity, the host -43; the host through usb.c's resampler at
    +-600 ppm: -32.2)"""
    rs = []
    for fu in funds:
        m = np.flatnonzero((fr > fu * (1 - BAND)) & (fr < fu * (1 + BAND)))
        if len(m) < 3:
            continue
        i = m[np.argmax(p[m])]
        if 0 < i < len(p) - 1:
            a, b, c = np.log(p[i - 1] + 1e-30), np.log(p[i] + 1e-30), np.log(p[i + 1] + 1e-30)
            d = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c < 0 else 0.0
            rs.append((fr[i] + d * (fr[1] - fr[0])) / fu)
    r = float(np.median(rs)) if rs else 1.0
    return [fu * r for fu in funds]


def harm_cells(seg, fund, fs=FS, pad=1, retune=True):
    """H2..H6 re H1 (dB, band peaks) and inh: the power off the harmonic bands re the power of H1 (dB). retune: the
    f0 measured (tuned); not in the strike's 150 ms window (aH): its main lobe is +-27 Hz wide, so the H1 band at the
    low keys holds no peak of its own, its edge was taken for one (A1 55 Hz locked at 45.7 .. 47.5 Hz, H6 read at
    ~280 Hz, and a 4 ms shift of the strike moved VIBE-B 33/64 aH6 -54.8 -> -26.8, 2026-11-10); the band peaks there
    (+-3 %) hold any clock's few hundred ppm without it"""
    p, fr = spectrum(seg, fs, pad)
    if retune:
        fund = tuned(p, fr, [fund])[0]
    pk = []
    for k in range(1, 7):
        m = (fr > k * fund * (1 - BAND)) & (fr < k * fund * (1 + BAND))
        pk.append(p[m].max() if m.any() else 1e-30)
    h = [10 * np.log10(pk[k] / pk[0] + 1e-30) for k in range(1, 6)]
    return h, (inharm(p, fr, [fund]) if retune else None)   # (inh: the analysis window's only; it retunes)


def inharm(p, fr, funds):
    """the power off every harmonic band of the fundamentals (30 Hz .. 20 kHz) re the power in the fundamentals' own
    bands (H1: so a distortion that fills the harmonic bands does not lower it), dB"""
    use = (fr > 30) & (fr < 20000)
    funds = tuned(p, fr, funds)
    on, h1 = np.zeros(len(fr), bool), np.zeros(len(fr), bool)
    for fu in funds:
        h1 |= np.abs(fr - fu) < max(IBW, fu * IREL)
        for k in range(1, int(20000 / fu) + 1):
            on |= np.abs(fr - k * fu) < max(IBW, k * fu * IREL)
    a, b = p[h1].sum(), p[use & ~on].sum()
    return float(10 * np.log10(b / (a + 1e-30) + 1e-30))


def strike(x, t, fs):
    """the strike's onset near its event time t (s): the first sample over a quarter of the peak in [t - 40, t + 120]
    ms. The FM-1's clock drifts against the recording's (500 ppm is 25 ms over a 50 s take), so a window placed by the
    take's first onset alone slid toward the attack on the last keys"""
    i0, i1 = max(0, int((t - 0.04) * fs)), int((t + 0.12) * fs)
    a = np.abs(x[i0:i1])
    if not len(a) or a.max() <= 0:
        return t
    return (i0 + int(np.argmax(a > 0.25 * a.max()))) / fs


def measure_wave(x, t0=0.0, fs=FS, keys=False):
    """the cells of one script's recording (x mono at fs; t0: where the script's 0 ms is in x, s). Every cell is a
    ratio, so the FM-1's USB at 48 kHz is judged against the host's 44.1 kHz reference as it is. keys: the script has
    the key cells (the host's)"""
    ev, _ = events(keys)
    out = {}
    for kind, label, t, ns, v in ev:
        ts = strike(x, t0 + t / 1000, fs)
        i0, i1 = int((ts + W0) * fs), int((ts + W1) * fs)
        seg = x[i0:i1]
        if kind in ("note", "key"):
            h, inh = harm_cells(seg, f0(ns[0]), fs)
            a, _ = harm_cells(x[int((ts + A0) * fs):int((ts + A1) * fs)], f0(ns[0]), fs, pad=8, retune=False)
            out[label] = dict(H2=h[0], H3=h[1], H4=h[2], H5=h[3], H6=h[4], inh=inh,
                              aH2=a[0], aH3=a[1], aH4=a[2], aH5=a[3], aH6=a[4])
        else:
            p, fr = spectrum(seg, fs)
            out["chord/" + label] = dict(imd=inharm(p, fr, [f0(n) for n in ns]))
    return out


def tags():
    """[(tag, config CC, TYPE CC value, CC 40 value)]: one script each"""
    return [(f"{c}-{t}-{m}", ccc, tcc, mcc) for c, ccc in CONFIGS for t, tcc in TYPES for m, mcc in METHODS]


def device_events(tag):
    """the same script for the FM-1 (tools/hil_jack.py --dist): [(t_s, kind, a, b)] and the take's length (s). No key
    cells there: nothing presses the FM-1's keys (the host's key cells prove the key path's notes equal MIDI's)"""
    ev = []
    for l in script(*next(x for x in tags() if x[0] == tag)[1:], keys=False):
        f = l.split()
        if f[1] == "end":
            return sorted(ev, key=lambda e: e[0]), int(f[0]) / 1000 + 0.3
        st, a, b = int(f[2], 16), int(f[3]), int(f[4])
        ev.append((int(f[0]) / 1000, {0xB0: "cc", 0x90: "on", 0x80: "off"}[st], a, b))


def measure_tag(binary, tag, outdir):
    _, ccc, tcc, mcc = next(x for x in tags() if x[0] == tag)
    return measure_wave(render(binary, tag, script(ccc, tcc, mcc), outdir), keys=True)


def measure(binary, outdir=None):
    """every cell: {config/type/method: {note or chord: {metric: dB}}}"""
    outdir = outdir or os.path.join(ROOT, "build", "dry_dist", os.path.basename(os.path.dirname(binary)) or "x")
    return {t[0]: measure_tag(binary, t[0], outdir) for t in tags()}


def build_hq(out, src=ROOT):
    """the Move's HQ host build of the tree src at out (move/tests/run_eq.sh's host-hq flags)"""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run([os.environ.get("CC", "cc"), "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", *HQ_DEFS,
                    '-DAPP_VER="dev"', "-I" + os.path.join(src, "firmware", "src"), "-I" + os.path.join(src, "firmware", "gen"),
                    "-o", out, os.path.join(src, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    return out


def judge(cells, base, methods_base="A"):
    """[(cell, metric, value, base, limit, ok)]; every method is held to the base's A (one
    method, as 0.9.9 had)"""
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
                lim = b + (MARGIN_H2 if m in ("H2", "aH2") else MARGIN)
                lim = max(lim, FLOOR)
                if m in ("H3", "aH3") and (label.endswith("/127") or label.endswith("/max")):
                    lim = min(b + MARGIN, CEIL_H3) if b <= CEIL_H3 else b + MARGIN
                rows.append((f"{tag} {label}", m, v, b, lim, bool(v <= lim + 1e-9)))
    return rows


def table(rows, only_bad=False, worst=40):
    bad = [r for r in rows if not r[5]]
    lines = [f"{len(rows)} values, {len(bad)} over the limit"]
    show = bad if only_bad else sorted(rows, key=lambda r: (r[4] - r[2]) if r[4] is not None else -99)[:worst]
    for c, m, v, b, lim, ok in sorted(show, key=lambda r: (r[4] - r[2]) if r[4] is not None else -99)[:worst]:
        lines.append(f"  {'ok  ' if ok else 'FAIL'} {c:28s} {m:4s} {v:7.1f} dB   0.9.11 {b if b is None else round(b, 1)!s:>6}"
                     f"   limit {lim if lim is None else round(lim, 1)!s:>6}   margin {'' if lim is None else round(lim - v, 1)}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "host", "frodhes"))
    ap.add_argument("--out")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--judge")
    ap.add_argument("--base")
    ap.add_argument("--hq", action="store_true", help="the Move's HQ reference (dry_dist_0911_hq.json)")
    ap.add_argument("--build-hq", metavar="OUT", help="compile the HQ host build to OUT and exit")
    ap.add_argument("--src", default=ROOT, help="--build-hq: the source tree")
    ap.add_argument("--all", action="store_true", help="--judge: every failing value (else the 40 closest)")
    a = ap.parse_args()
    if a.build_hq:
        print(build_hq(a.build_hq, os.path.abspath(a.src)))
        return
    a.base = a.base or (BASE_HQ if a.hq else BASE)
    if a.judge:
        rows = judge(json.load(open(a.judge)), json.load(open(a.base)))
        print(table(rows, only_bad=a.all, worst=10000 if a.all else 40))
        sys.exit(0 if all(r[5] for r in rows) else 1)
    cells = measure(os.path.abspath(a.bin))
    path = a.base if a.save else a.out
    if path:
        json.dump(cells, open(path, "w"), indent=1, sort_keys=True)
        print(path)
    else:
        print(json.dumps(cells, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
