#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The REVERB's TYPEs measured on the firmware's own code (tests/c/rev_scan.c: frodhes.c whole, fx_reverb on a stereo
signal, 256 frames a call as fx_process calls it; integer-exact, deterministic). Shared by tests/py/test_rev_types.py
and the kernels' own checks.

  python3 tools/rev_scan.py            a table per TYPE: T60 at DECAY 0 / 64 / 127 (INFINITE: 119 too), the cost,
                                       the tail's end, the level re HALL -> build/rev_scan/report.txt

The TYPE values (private/reverb/CONTRACT.md): FXP_RV_TYPE 0 OFF, 1 HALL, 2 PLATE, 3 INFINITE. A tree without
FXP_RV_TYPE (before the framework's interface) has HALL only: has_types() says so and the tests skip.
"""
import itertools
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "rev_scan", os.environ.get("PYTEST_XDIST_WORKER", ""))
FS = 44100
BLK = 256
TYPES = dict(OFF=0, HALL=1, PLATE=2, INFINITE=3)
_seq = itertools.count()


def _strip(s):
    return re.sub(r"/\*.*?\*/|//[^\n]*", " ", s, flags=re.S)


def fxp_ids(tree=None):
    """{name without FXP_: id} from fx.c's enum (FXP_DRIVE .. FXP_N)"""
    src = _strip(open(os.path.join(tree or ROOT, "firmware", "src", "fx.c")).read())
    m = re.search(r"enum\s*\{\s*(FXP_DRIVE\b[^}]*)\}", src)
    names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    out = {}
    for i, n in enumerate(names):
        assert "=" not in n, n                       # (a plain enum: the ids are the order)
        out[n[4:]] = i
    return out


def has_types(tree=None):
    return "RV_TYPE" in fxp_ids(tree)


def src_text(tree=None):
    """the reverb's sources: fx.c, rev_types.c and the kernels' files (what exists)"""
    d = os.path.join(tree or ROOT, "firmware", "src")
    return "".join(open(os.path.join(d, f)).read() for f in ("fx.c", "rev_types.c", "rev_plate.c", "rev_inf.c")
                   if os.path.exists(os.path.join(d, f)))


def define(name, tree=None):
    """a plain integer #define of the reverb's sources (the first one), or None"""
    m = re.search(rf"#define\s+{name}\s+\(?\s*(\d+)u?\s*\)?", src_text(tree))
    return int(m.group(1)) if m else None


def build(cflags=(), tag="default", tree=None):
    tree = tree or ROOT
    os.makedirs(os.path.join(OUT, "bin"), exist_ok=True)
    exe = os.path.join(OUT, "bin", f"rev_scan-{tag}-{os.getpid()}")
    if "rev_poll(" in src_text(tree):
        cflags = ["-DREV_SCAN_POLL", *cflags]
    cflags = [*cflags, *os.environ.get("REV_SCAN_CFLAGS", "").split()]   # (a variant: -DREV_INF_QUARTER=0)
    subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', *cflags,
                    "-I" + os.path.join(tree, "firmware", "src"), "-I" + os.path.join(tree, "firmware", "gen"),
                    "-o", exe, os.path.join(ROOT, "tests", "c", "rev_scan.c"), "-lm"], check=True)
    return exe


def _args(params, switches, ids):
    a = [f"#{ids[n]}={v}" for n, v in params.items()]
    a += [f"@{b}:#{ids[n]}={v}" for b, n, v in switches]
    return a


def render(exe, x, params, switches=(), blocks=None, trace=False, reset=None, ids=None):
    """x: (n, 2) or (n,) int samples (mono goes to both sides); params {NAME: v} (FXP names without FXP_);
    switches [(block, NAME, v)]. Returns dict(y (n, 2) int64 out, wet (n, 2) = out - in, cost, peak, last (the last
    block with a wet sample != 0, -1 none), trace [cost per block] when asked)"""
    ids = ids or fxp_ids()
    x = np.asarray(x, dtype=np.int64)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    tmp = os.path.join(OUT, "tmp")
    os.makedirs(tmp, exist_ok=True)
    k = f"{os.getpid()}-{next(_seq)}"
    fi, fo = os.path.join(tmp, f"in{k}.raw"), os.path.join(tmp, f"out{k}.raw")
    x.astype("<i4").tofile(fi)
    env = dict(os.environ)
    for v in ("REV_SCAN_WIN", "REV_SCAN_TRACE", "REV_SCAN_BLOCKS", "REV_SCAN_RESET"):
        env.pop(v, None)
    if blocks is not None:
        env["REV_SCAN_BLOCKS"] = str(blocks)
    if trace:
        env["REV_SCAN_TRACE"] = "1"
    if reset is not None:
        env["REV_SCAN_RESET"] = str(reset)
    p = subprocess.run([exe, fi, fo, *_args(params, switches, ids)], check=True, capture_output=True, text=True,
                       env=env)
    r = p.stdout.split()
    y = np.fromfile(fo, dtype="<i4").astype(np.int64).reshape(-1, 2)
    os.remove(fi), os.remove(fo)
    n = len(y)
    xin = np.zeros((n, 2), np.int64)
    m = min(n, len(x))
    xin[:m] = x[:m]
    out = dict(y=y, wet=y - xin, cost=int(r[1]), peak=int(r[3]), last=int(r[7]))
    if trace:
        out["trace"] = [int(ln.split()[3]) for ln in p.stderr.splitlines() if ln.startswith("blk ")]
    return out


def stats(exe, x, params, blocks, win=BLK * 16, switches=(), ids=None):
    """a long run's wet statistics per window of win frames: dict(rms (n, 2), mean (n, 2), max (n, 2), nonzero (n,),
    cost (n,), peak, last); the samples are not kept (10 minutes of audio)"""
    ids = ids or fxp_ids()
    x = np.asarray(x, dtype=np.int64)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    tmp = os.path.join(OUT, "tmp")
    os.makedirs(tmp, exist_ok=True)
    k = f"{os.getpid()}-{next(_seq)}"
    fi, fo = os.path.join(tmp, f"in{k}.raw"), os.path.join(tmp, f"st{k}.raw")
    x.astype("<i4").tofile(fi)
    env = dict(os.environ, REV_SCAN_BLOCKS=str(blocks), REV_SCAN_WIN=str(win))
    env.pop("REV_SCAN_TRACE", None), env.pop("REV_SCAN_RESET", None)
    p = subprocess.run([exe, fi, fo, *_args(params, switches, ids)], check=True, capture_output=True, text=True,
                       env=env)
    r = p.stdout.split()
    s = np.fromfile(fo, dtype="<f8").reshape(-1, 8)
    os.remove(fi), os.remove(fo)
    return dict(rms=np.sqrt(s[:, [0, 3]] / win), mean=s[:, [1, 4]] / win, max=s[:, [2, 5]], nonzero=s[:, 6],
                cost=s[:, 7], peak=int(r[3]), last=int(r[7]))


# ---- signals

def chord(n, amp=6000.0, seed=0):
    """a bright chord with an attack (a struck chord's spectrum, no samples): 5 notes with harmonics, decaying"""
    t = np.arange(n) / FS
    x = np.zeros(n)
    for i, f in enumerate((130.81, 196.0, 261.63, 329.63, 392.0)):
        for h in range(1, 6):
            x += (amp / 5) / h * np.sin(2 * np.pi * f * h * t + i + h) * np.exp(-t * (1.5 + h))
    return np.round(x).astype(np.int64)


def burst(n, amp=8000.0, seed=1):
    """a short noise burst (an impulse with a spectrum): the decay measurements' stimulus"""
    rng = np.random.default_rng(seed)
    x = rng.uniform(-amp, amp, n)
    k = min(n, int(0.02 * FS))
    x[k:] = 0
    return np.round(x).astype(np.int64)


def sine(n, hz, amp=6000.0):
    return np.round(amp * np.sin(2 * np.pi * hz * np.arange(n) / FS)).astype(np.int64)


# ---- analysis

def edc_db(wet, a=0):
    """the energy decay curve (Schroeder's backward integral) of the wet from sample a, mid (L + R), in dB re its
    start"""
    w = np.asarray(wet, float)
    w = (w[:, 0] + w[:, 1]) if w.ndim == 2 else w
    e = np.cumsum((w[a:] ** 2)[::-1])[::-1]
    return 10 * np.log10(e / e[0] + 1e-300)


def t60_from_edc(e, lo=-5.0, hi=-25.0):
    """T60 (s) from an EDC: the least-squares line between lo and hi dB, extrapolated to -60"""
    i0 = int(np.argmax(e <= lo))
    i1 = int(np.argmax(e <= hi))
    if i1 <= i0 + 10:
        return None
    t = np.arange(i0, i1) / FS
    k = np.polyfit(t, e[i0:i1], 1)[0]
    return -60.0 / k if k < 0 else float("inf")


def band(wet, f0=354.0, f1=1414.0):
    """the wet's mid (L + R) between f0 and f1 Hz (an FFT mask over the whole take): by default the 500 Hz and 1 kHz
    octaves, where a reverb time is quoted"""
    w = np.asarray(wet, float)
    m = w[:, 0] + w[:, 1] if w.ndim == 2 else w
    X = np.fft.rfft(m)
    f = np.fft.rfftfreq(len(m), 1 / FS)
    X[(f < f0) | (f >= f1)] = 0
    return np.fft.irfft(X, len(m))


def t60_mid(exe, params, secs, ids=None):
    """T60 (s) of the 500 Hz + 1 kHz octaves after a noise burst: the EDC of the band-passed wet, -5 .. -25 dB"""
    r = render(exe, burst(FS), params, blocks=int(secs * FS / BLK), ids=ids)
    return t60_from_edc(edc_db(band(r["wet"])))


def t60_from_rms(rms, win, lo_db=-5.0, hi_db=-25.0):
    """T60 (s) from a windowed RMS (the stats() run): the line through the level from lo to hi dB under its first
    peak (no EDC: the windows are coarse and a long tail is measured this way)"""
    lv = 20 * np.log10(np.asarray(rms, float) + 1e-9)
    i0 = int(np.argmax(lv))
    ref = lv[i0]
    seg = np.arange(i0, len(lv))
    a = seg[np.argmax(lv[seg] <= ref + lo_db)]
    b = seg[np.argmax(lv[seg] <= ref + hi_db)] if np.any(lv[seg] <= ref + hi_db) else None
    if b is None or b <= a + 2:
        return None
    t = np.arange(a, b) * win / FS
    k = np.polyfit(t, lv[a:b], 1)[0]
    return -60.0 / k if k < 0 else float("inf")


KFILE = dict(PLATE="rev_plate.c", INFINITE="rev_inf.c")


def is_stub(typ, tree=None):
    """a TYPE whose kernel file still says STUB in its head comment (agent FW's placeholder: a tail to fade and end,
    not the TYPE's sound)"""
    p = os.path.join(tree or ROOT, "firmware", "src", KFILE[typ])
    return not os.path.exists(p) or "STUB" in open(p).read()[:600]


def is_silent_type(exe, typ, ids=None):
    """a TYPE that gives no wet at all (a stub that only fills its place): its sound tests skip"""
    x = chord(BLK * 64)
    r = render(exe, x, dict(RV_TYPE=TYPES[typ], RV_DEPTH=100), blocks=128, ids=ids)
    return not np.any(r["wet"])


def main():
    if not has_types():
        print("this tree has no reverb TYPEs (fx.c without FXP_RV_TYPE): HALL only")
        return 0
    exe = build(tag="report")
    lines = []
    for typ in ("HALL", "PLATE", "INFINITE"):
        if typ != "HALL" and is_silent_type(exe, typ):
            lines.append(f"{typ:9s} silent (a stub)")
            continue
        row = []
        for d in ((0, 64, 119, 127) if typ == "INFINITE" else (0, 64, 127)):
            win = BLK * 16
            s = stats(exe, burst(FS), dict(RV_TYPE=TYPES[typ], RV_DEPTH=100, RV_DECAY=d), blocks=int(90 * FS / BLK),
                      win=win)
            t = t60_from_rms(s["rms"].mean(axis=1), win)
            row.append(f"DECAY {d}: T60 {t:.2f} s" if t else f"DECAY {d}: T60 -")
        lines.append(f"{typ:9s} " + "; ".join(row))
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    os.remove(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
