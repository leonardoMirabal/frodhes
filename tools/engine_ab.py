#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Engine A/B: the host renders of a baseline (this tree built with -DRH_NO_GATE, or --ref) against the working tree, with the numbers that say whether
a change that moves the sound is audible (the engine CPU study, 2026-10-24: the inaudible-mode gate).

  tools/engine_ab.py [--ref REF] [--defs -DRH_VOICES=9u] [--jobs 2] [--pack]     Gate R's renders + the presets list: a table
  tools/engine_ab.py --pack                                         + the listening pairs in build/engine-ab/

Per render (the host WAV: the effects, the limiter and the 16-bit output included): the max abs difference (LSB),
the residual (new - old) RMS and its loudest 50 ms (dBFS, and A-weighted dBFS(A)), its loudest 1/3 octave (dBFS),
the masking margin (the residual's level in each critical band against the reference's own level there, the worst
band, over the bands where the reference is 20 dB over the output's rounding noise and the residual over -100 dBFS), the
residual's level in the bands where the reference has no signal (unmasked; two renders' different roundings alone
read the rounding floor + 3 dB, ROUND_FLOOR), the integrated loudness difference (BS.1770, LU),
the largest critical-band level change (bands over -90 dBFS) and the difference of where the sound ends (the
last 50 ms within 60 dB of the loudest, s). Levels are re digital full scale (32768). The metrics are also imported by tests/py/test_rh_gate.py.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy.signal import sosfilt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from bar_measure import k_weight_sos  # noqa: E402

FS = 44100
FSC = 32768.0
OUT = ROOT / "build" / "engine-ab"
BARK_EDGES = [20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400,
              5300, 6400, 7700, 9500, 12000, 15500, 20000]


# ---- metrics
def aweight_mag(f):
    f2 = np.asarray(f, float) ** 2
    ra = 12194 ** 2 * f2 * f2 / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
    return ra * 10 ** (2.0 / 20)                                    # 0 dB at 1 kHz


def aweight(x):
    n = len(x)
    return np.fft.irfft(np.fft.rfft(x, n) * aweight_mag(np.fft.rfftfreq(n, 1 / FS)), n)


def db(v):
    return float(20 * np.log10(max(float(v), 1e-12) / FSC))


def mono(x):
    x = np.asarray(x, float)
    return x.mean(axis=1) if x.ndim == 2 else x


def win_rms(x, w=2205):
    n = len(x) // w
    if n == 0:
        return np.array([np.sqrt(np.mean(x ** 2)) if len(x) else 0.0])
    return np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1))


def lufs(x):
    x = np.asarray(x, float) / FSC
    x = x[:, None] if x.ndim == 1 else x
    sos = k_weight_sos(FS)
    return float(-0.691 + 10 * np.log10(sum(np.mean(sosfilt(sos, x[:, c]) ** 2) for c in range(x.shape[1])) + 1e-20))


def band_levels(x, edges):
    """band powers in dBFS (re a full-scale sine) of the whole signal"""
    n = len(x)
    X = np.abs(np.fft.rfft(x)) ** 2 * 2 / (n * n) / (FSC ** 2 / 2)
    f = np.fft.rfftfreq(n, 1 / FS)
    return np.array([10 * np.log10(X[(f >= lo) & (f < hi)].sum() + 1e-30) for lo, hi in zip(edges[:-1], edges[1:])])


# the Q15 output's own rounding noise (+-0.5 LSB, white, the whole render long) per critical band, dBFS: a residual
# band at or under it + 3 dB is two different rounding sequences, not a signal
ROUND_FLOOR = np.array([20 * np.log10((1 / np.sqrt(12)) / (FSC / np.sqrt(2))) + 10 * np.log10((hi - lo) / (FS / 2))
                        for lo, hi in zip(BARK_EDGES[:-1], BARK_EDGES[1:])])
THIRD = [1000 * 2 ** ((k - 0.5) / 3) for k in range(-16, 15)]        # 1/3-octave edges, 25 Hz .. 20 kHz


def decay_time(x, drop=60):
    """where the sound ends: the end (s) of the last 50 ms window within drop dB of the loudest one"""
    e = win_rms(x)
    j = np.flatnonzero(e >= e.max() * 10 ** (-drop / 20))
    return float((j[-1] + 1) * 2205 / FS) if len(j) and e.max() > 0 else None


def compare(a, b):
    """a = the reference render, b = the new one (int scale)"""
    a, b = mono(a), mono(b)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    r = b - a
    if not n or not r.any():
        return dict(max_lsb=0.0, res_dbfs=-240.0, res_dba=-240.0, res_pk_dba=-240.0, res_pk_dbfs=-240.0,
                    res_band_max_dbfs=-240.0, mask_margin_db=-240.0, unmasked_dbfs=-240.0, lufs_diff=0.0, band_diff_db=0.0, decay_diff_s=0.0)
    ra = aweight(r)
    out = dict(max_lsb=float(np.abs(r).max()), res_dbfs=db(np.sqrt(np.mean(r ** 2))), res_dba=db(np.sqrt(np.mean(ra ** 2))),
               res_pk_dba=db(win_rms(ra).max()), res_pk_dbfs=db(win_rms(r).max()),
               res_band_max_dbfs=float(band_levels(r, THIRD).max()))
    la, lb, lr = band_levels(a, BARK_EDGES), band_levels(b, BARK_EDGES), band_levels(r, BARK_EDGES)
    sig = la > ROUND_FLOOR + 20                                     # the reference has a signal in the band
    ok = sig & (lr > -100)                                          # and the residual is over -100 dBFS there
    out["mask_margin_db"] = float((lr - la)[ok].max()) if ok.any() else -240.0
    out["unmasked_dbfs"] = float(lr[~sig].max()) if (~sig).any() else -240.0   # the residual where the reference has none
    ok = la > -90
    out["band_diff_db"] = float(np.abs(lb - la)[ok].max()) if ok.any() else 0.0
    la_, lb_ = lufs(a), lufs(b)
    out["lufs_diff"] = lb_ - la_ if la_ > -90 else 0.0
    ta, tb = decay_time(a), decay_time(b)
    out["decay_diff_s"] = (tb or 0.0) - (ta or 0.0) if (ta is None) == (tb is None) else 99.0
    return out


WORSE = ("max_lsb", "res_dbfs", "res_dba", "res_pk_dba", "res_pk_dbfs", "res_band_max_dbfs", "mask_margin_db",
         "unmasked_dbfs")


def worst(rows):
    """the worst value of every metric over rows (dicts with the metrics and a 'name')"""
    w = {}
    for k in WORSE + ("lufs_diff", "band_diff_db", "decay_diff_s"):
        r = max(rows, key=lambda x: x[k] if k in WORSE else abs(x[k]))
        w[k] = (r[k], r["name"])
    return w


def read_wav(p):
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels()).astype(float)


# ---- host renders
def preset_scripts():
    """the factory presets F01..F16 (F13..F16 the bar types: must not move), each: a soft and a hard chord, bass with the pedal, a top-octave run"""
    res = {}
    for p in range(16):
        ev = [f"50 midi 0xC0 {p} 0"]
        t = 300
        for v in (40, 120):                                         # soft / hard chord
            for n in (48, 52, 55, 59, 62):
                ev.append(f"{t} midi 0x90 {n} {v}")
            t += 1500
            for n in (48, 52, 55, 59, 62):
                ev.append(f"{t} midi 0x80 {n} 0")
            t += 1000
        ev.append(f"{t} midi 0xB0 64 127")                          # bass with the pedal
        for n in (28, 31, 35, 40):
            ev.append(f"{t} midi 0x90 {n} 110")
            t += 150
        t += 600
        for n in (28, 31, 35, 40):
            ev.append(f"{t} midi 0x80 {n} 0")
        t += 2500
        ev.append(f"{t} midi 0xB0 64 0")
        t += 1500
        for i, n in enumerate(range(96, 109)):                      # the top octave, staccato
            ev.append(f"{t + 120 * i} midi 0x90 {n} 90")
            ev.append(f"{t + 120 * i + 90} midi 0x80 {n} 0")
        t += 120 * 13 + 2000
        ev.append(f"{t} end")
        res[f"presets/F{p + 1:02d}"] = "\n".join(ev) + "\n"
    return res


def pack_scripts():
    """the listening pairs (F01 unless named): soft / hard chords, bass with the pedal, a top-octave run, F01..F04"""
    def chord(v, notes, hold=2500):
        return [(0, "on", n, v) for n in notes] + [(hold, "off", n, 0) for n in notes]
    res = {}
    lay = {
        "soft-chords": [("chord", 30, (48, 52, 55, 59, 62)), ("chord", 45, (53, 57, 60, 64)), ("chord", 35, (43, 50, 55, 59))],
        "hard-chords": [("chord", 120, (48, 52, 55, 59, 62)), ("chord", 127, (53, 57, 60, 64)), ("chord", 115, (43, 50, 55, 59))],
        "bass-pedal": [("bass", 127, (28, 31, 33, 35, 36, 38, 40, 41))],
        "top-run": [("run", 100, tuple(range(84, 109)))],
    }
    for name, parts in lay.items():
        ev, t = [], 300
        for kind, v, notes in parts:
            if kind == "chord":
                for n in notes:
                    ev.append(f"{t} midi 0x90 {n} {v}")
                t += 2500
                for n in notes:
                    ev.append(f"{t} midi 0x80 {n} 0")
                t += 1500
            elif kind == "bass":
                ev.append(f"{t} midi 0xB0 64 127")
                for n in notes:
                    ev.append(f"{t} midi 0x90 {n} {v}")
                    t += 250
                t += 2000
                for n in notes:
                    ev.append(f"{t} midi 0x80 {n} 0")
                t += 3000
                ev.append(f"{t} midi 0xB0 64 0")
                t += 2000
            else:
                for i, n in enumerate(notes):
                    ev.append(f"{t + 110 * i} midi 0x90 {n} {v}")
                    ev.append(f"{t + 110 * i + 100} midi 0x80 {n} 0")
                t += 110 * len(notes) + 2500
        ev.append(f"{t} end")
        res[f"pack/{name}"] = "\n".join(ev) + "\n"
    for p in range(4):
        res[f"pack/F{p + 1:02d}"] = preset_scripts()[f"presets/F{p + 1:02d}"]
    # the voice-lifetime case (the gate frees a released low note's voice once its audible partials die: the old
    # engine kept it for its 3 LSB V2, so the next chord stole it): 8 bass notes v127 released, a chord 0.6 s later
    # (Gate R's fx pattern), on F01 and with every effect (Gate R's fx/heavy)
    ev, t = [], 300
    for rep in range(2):
        for n in (28, 31, 33, 35, 36, 38, 40, 41):
            ev.append(f"{t} midi 0x90 {n} 127")
        t += 900
        for n in (28, 31, 33, 35, 36, 38, 40, 41):
            ev.append(f"{t} midi 0x80 {n} 0")
        t += 600
        for n in (48, 52, 55, 59, 62, 64, 67, 71):
            ev.append(f"{t} midi 0x90 {n} 107")
        t += 900
        for n in (48, 52, 55, 59, 62, 64, 67, 71):
            ev.append(f"{t} midi 0x80 {n} 0")
        t += 2000
    ev.append(f"{t} end")
    res["pack/bass-then-chord"] = "\n".join(ev) + "\n"
    import gate_r
    res["pack/heavy-fx"] = gate_r.fx_scripts()["fx/heavy"]
    return res


CFLAGS = ["-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"", "-Ifirmware/src", "-Ifirmware/gen"]


def build(src_root, binary, defs=()):
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cc", *CFLAGS, *defs, "-o", str(binary), "firmware/src/frodhes.c", "-lm"], cwd=src_root, check=True)


def base_tree(ref):
    """the ref's firmware/ and tests/host.c, extracted under build/engine-ab/base-src"""
    d = OUT / "base-src"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", ref, "firmware", "tests/host.c"], cwd=ROOT, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    return d


def render(binary, cwd, out, script):
    """the WAV and the run's Rhodes steal count (None if the binary does not print it)"""
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([str(binary), str(out)] + ([str(script)] if script else []), cwd=cwd, capture_output=True,
                       text=True, errors="replace")
    m = [ln for ln in p.stdout.splitlines() if ln.startswith("rhodes steals ")]
    return out.with_suffix(".wav"), int(m[-1].split()[2]) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="", help="the baseline: a ref's tree (default: this tree with -DRH_NO_GATE, "
                    "checked against tests/baselines/renders.sha256)")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--pack", action="store_true", help="also the listening pairs (build/engine-ab/*-before|after.wav)")
    ap.add_argument("--only", default="", help="a name prefix (e.g. presets/)")
    ap.add_argument("--defs", default="", help="extra defines for both builds (e.g. -DRH_VOICES=9u)")
    a = ap.parse_args()
    import gate_r
    gate_r.OUT = OUT / "scripts"
    jobs = {k: v for k, v in gate_r.jobs().items()}
    extra = dict(preset_scripts())
    if a.pack:
        extra.update(pack_scripts())
    for name, text in extra.items():
        p = OUT / "scripts" / (name + ".txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        jobs[name] = p
    jobs = {k: v for k, v in jobs.items() if k.startswith(a.only)}
    bb, bn = OUT / "bin" / "base", OUT / "bin" / "new"
    if a.ref:
        base = base_tree(a.ref)
        build(base, bb, a.defs.split())
    else:
        base = ROOT
        build(ROOT, bb, ["-DRH_NO_GATE", *a.defs.split()])
    build(ROOT, bn, a.defs.split())

    def one(kv):
        name, script = kv
        script = Path(script).resolve() if script else None
        wa, sa = render(bb, base, OUT / "before" / name, script)
        wb, sb = render(bn, ROOT, OUT / "after" / name, script)
        if not wa.exists() or not wb.exists():
            return dict(name=name, missing=True)
        r = compare(read_wav(wa), read_wav(wb))
        r.update(name=name, identical=wa.read_bytes() == wb.read_bytes(), steals=(sa, sb),
                 sha_before=hashlib.sha256(wa.read_bytes()).hexdigest(),
                 sha_after=hashlib.sha256(wb.read_bytes()).hexdigest())
        return r

    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, sorted(jobs.items(), key=lambda kv: kv[0])))
    rows = [r for r in rows if not r.get("missing")]
    want = dict(reversed(ln.split()) for ln in gate_r.BASE.read_text().splitlines() if ln.strip())
    gr = [r for r in rows if r["name"] in want]
    off = [r["name"] for r in gr if want[r["name"]] != r["sha_before"]]
    print(f"baseline: {len(gr) - len(off)} of {len(gr)} Gate R renders of the 'before' build match "
          f"tests/baselines/renders.sha256" + (f" (not: {off[:5]})" if off else ""))
    (OUT / "ab.json").write_text(json.dumps(rows, indent=1))
    moved = [r for r in rows if not r["identical"]]
    print(f"{len(rows)} renders, {len(moved)} moved, {len(rows) - len(moved)} byte-identical")
    print(f"{'render':34s} {'maxLSB':>6s} {'res dBFS':>8s} {'dB(A)':>7s} {'pk(A)':>7s} {'band':>7s} {'mask':>6s} "
          f"{'LU':>6s} {'bandΔ':>6s} {'endΔ':>6s} {'unmask':>6s} steals")
    for r in moved:
        print(f"{r['name']:34s} {r['max_lsb']:6.0f} {r['res_dbfs']:8.1f} {r['res_dba']:7.1f} {r['res_pk_dba']:7.1f} "
              f"{r['res_band_max_dbfs']:7.1f} {r['mask_margin_db']:6.1f} {r['lufs_diff']:6.3f} {r['band_diff_db']:6.2f} "
              f"{r['decay_diff_s']:6.2f} {r['unmasked_dbfs']:6.1f} {r['steals'][0]}->{r['steals'][1]}")
    if moved:
        print("worst: " + "; ".join(f"{k} {v:.2f} ({n})" for k, (v, n) in worst(moved).items()))
    if a.pack:
        for name in pack_scripts():
            for side in ("before", "after"):
                src = OUT / side / (name + ".wav")
                if src.exists():
                    shutil.copy(src, OUT / f"{name.split('/')[1]}-{side}.wav")
        print(f"listening pairs: {OUT.relative_to(ROOT)}/*-before.wav / *-after.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
