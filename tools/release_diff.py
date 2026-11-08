#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The release diff: the candidate's sound against the last release, cell by cell, with pristine ceilings.

Why (2026-11-08): the 0.9.10 Rhodes distorted heavily at v110..127 with the effects off and 3285 passing tests did not
flag it (private/plans/dist-gate-audit.md). Every sound test was relative (A vs B in one build), a lower bound (a
control must change something) or a target that asked for more harmonics; nobody compared the candidate with the last
release as a whole. This does, in two sizes:

  fast  the pristine gate (`make pristine`, the first stage of `make test` and `make pytest`; seconds). Every TYPE of
        every engine (the four electric pianos, VIBES, MARIMBA) on the FM-1 build and the HQ build (the Move's
        settings, HQ on), effects off / RAW / the default effects, both RESPONSE methods; keys low / mid / high at
        v40 / 96 / 127, a v127 chord and a v127 chord on the pedal. Judged against tests/baselines/release_diff_fast.json
        (measured on the last release, LAST below; `--save-base` on that tree's binaries, never on a candidate).
  full  the release diff (a gate target, `make release-diff`; about a minute): the same plus keys across the range at
        v20 / 40 / 64 / 96 / 110 / 127, three chords and every factory preset (F01..F19). The last release is built
        from git (LAST) and measured live, so its numbers can never be re-saved from a candidate.

Measured per note (mono, 44.1 kHz; t = the strike): H2..H6 re H1 (dB, band peaks +-3 % of k f0, 50..450 ms), "inh"
(the energy off every harmonic band re H1: aliasing and any inharmonic product), "level" (RMS over the hold, dBFS),
"peak" (the attack's peak, first 100 ms), "cent" (spectral centroid, Hz), "decay" (dB lost over the hold), "keyup"
(the level 80..280 ms after the key-up re the last 200 ms before it), "clk" (the largest 1 ms second-difference peak
re the median, body and key-up: clicks), and in full mode "lu" (K-weighted loudness of the hold), "cb" (critical-band
loudness, phon-like, tools/cb_loud.py) and "lim" (the limiter's gain reduction: RAW minus effects-off level). Per
chord: "imd" (the energy off every harmonic band of the notes re their fundamentals), level, peak. Per script: "clip"
(samples at full scale) and "noise" (the floor 2..3 s after the last note).

The verdict (exit 1 on any failure):
  ceilings  pristine, from AGENTS.md section 1, NOT overridable by the expected-changes file:
            H2..H6 <= max(last + 3, -60) dB re H1, and H3 <= -25 dB at v127 where the last release was below that;
            inh / imd <= last + 3 dB; clk <= last + 6 dB; noise <= max(last + 3, -90) dBFS;
            no clip outside RAW; keyup <= +0.5 dB (never louder after the key-up; the pedal cells and the presets, whose
            delays repeat the strike, are exempt).
  diffs     every value that moves by more than TOL in EITHER direction must be listed, with a reason, in the
            expected-changes file (tests/expected_changes.yaml); anything unlisted fails.

  tools/release_diff.py [--grid fast|full] [--fm1 BIN] [--hq BIN]   judge (default: this tree's host builds)
      --report FILE      also write the report (the biggest moves first) to FILE
      --draft FILE       write the unlisted moves as expected-changes entries to fill in (reason: TODO fails)
      --save-base        (fast) measure --fm1/--hq and write the fast baseline: ONLY on the last release's tree
      --base-tree DIR    (full) the last release's checkout (default: git archive LAST into build/release_diff/last)
"""
import argparse
import fnmatch
import json
import os
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))     # (cb_loud; the pool's spawned workers too)
LAST = "v0.9.9"                    # the last release (456b5da4); move with every release, never to a candidate
OUT = os.path.join(ROOT, "build", "release_diff")
BASE_FAST = os.path.join(ROOT, "tests", "baselines", "release_diff_fast.json")
EXPECTED = os.path.join(ROOT, "tests", "expected_changes.yaml")
FS = 44100
HQ_DEFS = ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1", "-DHQ_DEFAULT=1"]
TYPES = (("ST77", 80, 0), ("SUIT", 80, 32), ("FELT", 80, 64), ("DYNO", 80, 96), ("VIBES", 79, 0), ("MARIMBA", 79, 127))
CONFIGS = {"FXOFF": [(111, 127)], "RAW": [(119, 127)], "FX": []}
METHODS = (("A", 0), ("B", 127))
GRID = {
    "fast": dict(keys_ep=(33, 57, 81), keys_bar=(57, 69, 84), vels=(40, 96, 127),
                 chords=(("mid", (57, 60, 64, 67)),), presets=()),
    "full": dict(keys_ep=(28, 33, 45, 57, 69, 81, 93), keys_bar=(45, 53, 60, 67, 74, 81, 89),
                 vels=(20, 40, 64, 96, 110, 127),
                 chords=(("low", (33, 40, 45, 49)), ("mid", (57, 60, 64, 67)), ("high", (69, 72, 76, 79))),
                 presets=tuple(range(16)) + (32, 33, 34)),
}
CONTROLS = (("TONE", 28), ("BARK", 29), ("BELL", 30), ("HAMMER", 31), ("DECAY", 102), ("NOISE", 104))
# (full: each voice control at 0 and 127 on every TYPE, effects off, the FM-1 build: the control rows elsewhere
# (test_every_control, hil_scan's VOICE rows) only ask that a control changes something; these hold its extremes
# to the last release's)
STEP, HOLD = 1500, 800              # ms: a note every STEP, released after HOLD
W0, W1 = 0.05, 0.45                 # s: the harmonic window after the strike
BAND, IBW, IREL = 0.03, 6.0, 0.003  # harmonic peaks +-3 %; inh / imd: a harmonic's band +-6 Hz or +-0.3 %
SILENT = -70.0                      # dBFS: a note below this (out of the engine's range) has no spectral cells
FLOOR_H, MARGIN, CEIL_H3 = -60.0, 3.0, -25.0
TOL = {"H": 3.0, "inh": 3.0, "imd": 3.0, "level": 1.5, "peak": 2.0, "cent": 0.10, "decay": 3.0, "keyup": 2.0,
       "clk": 6.0, "noise": 3.0, "clip": 0, "lu": 1.0, "cb": 1.5, "lim": 1.5}


def f0(n):
    return 440.0 * 2 ** ((n - 69) / 12)


# ---------------------------------------------------------------- the cases and their scripts

def cases(grid):
    """[(case id, build, setup CCs [(t, status, d1, d2)], keys, kind)]: one script each"""
    g, out = GRID[grid], []
    for build in ("fm1", "hq"):
        for tname, tcc, tval in TYPES:
            for cfg, ccs in CONFIGS.items():
                for m, mv in METHODS:
                    setup = [(110, 0xB0, tcc, tval), (120, 0xB0, 40, mv)] + [(100, 0xB0, c, v) for c, v in ccs]
                    keys = g["keys_ep"] if tcc == 80 else g["keys_bar"]
                    out.append((f"{build}/{tname}/{cfg}/{m}", build, setup, keys, "type"))
        if grid == "full" and build == "fm1":
            for tname, tcc, tval in TYPES:
                for cname, ccn in CONTROLS:
                    for cv in (0, 127):
                        setup = [(100, 0xB0, 111, 127), (110, 0xB0, tcc, tval), (120, 0xB0, 40, 0),
                                 (130, 0xB0, ccn, cv)]
                        out.append((f"{build}/{tname}/{cname}{cv}/A", build, setup,
                                    g["keys_ep"] if tcc == 80 else g["keys_bar"], "control"))
        for pc in g["presets"]:
            name = f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}"
            for m, mv in METHODS:
                out.append((f"{build}/{name}/PRESET/{m}", build, [(100, 0xC0, pc, None), (900, 0xB0, 40, mv)],
                            g["keys_bar"] if 12 <= pc < 16 else g["keys_ep"], "preset"))
    return out


def events(grid, keys):
    """[(label, t_ms, notes, vel, pedal)] and the script's end (ms)"""
    g, ev, t = GRID[grid], [], 1200
    for k in keys:
        for v in g["vels"]:
            ev.append((f"k{k}v{v}", t, (k,), v, False))
            t += STEP
    for name, ns in g["chords"]:
        ev.append((f"chord-{name}", t, ns, 127, False))
        t += STEP + 500
    ev.append(("pedal", t, g["chords"][-1][1], 127, True))
    t += STEP + 2500
    return ev, t + 3000                 # 3 s of silence: the noise floor


def script(grid, setup, keys):
    ev, end = events(grid, keys)
    ls = []
    for t, st, a, b in setup:
        ls.append(f"{t} midi 0x{st:02X} {a}" + ("" if b is None else f" {b}"))
    for _, t, ns, v, ped in ev:
        if ped:
            ls.append(f"{t - 50} midi 0xB0 64 127")
        ls += [f"{t + i} midi 0x90 {n} {v}" for i, n in enumerate(ns)]
        ls += [f"{t + HOLD} midi 0x80 {n} 0" for n in ns]
        if ped:
            ls.append(f"{t + HOLD + 1500} midi 0xB0 64 0")
    ls.append(f"{end} end")
    return sorted(ls, key=lambda l: int(l.split()[0]))


def render(binary, path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path + ".txt", "w").write("\n".join(lines) + "\n")
    env = dict(os.environ)
    env.pop("FRODHES_FLASH", None)
    p = subprocess.run([binary, path, path + ".txt"], capture_output=True, text=True, env=env)
    if p.returncode:
        raise RuntimeError(f"{binary} {path}.txt: exit {p.returncode}: {p.stderr[-400:]}")
    w = wave.open(path + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float)
    w.close()
    for ext in (".wav", ".txt"):
        os.remove(path + ext)
    for f in os.listdir(os.path.dirname(path)):         # (the host's screenshots of this script)
        if f.startswith(os.path.basename(path) + "-") and f.endswith(".ppm"):
            os.remove(os.path.join(os.path.dirname(path), f))
    return x


# ---------------------------------------------------------------- the measures

def db(p):
    return float(10 * np.log10(p + 1e-30))


def rms_db(x):
    return db(np.mean(np.square(x / 32768.0))) if len(x) else -300.0


def spectrum(x):
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    return p, np.fft.rfftfreq(len(x), 1 / FS)


def offharm(p, fr, funds):
    """the power off every harmonic band of the fundamentals (30 Hz .. 20 kHz) re their own bands (dB)"""
    use = (fr > 30) & (fr < 20000)
    on, h1 = np.zeros(len(fr), bool), np.zeros(len(fr), bool)
    for fu in funds:
        h1 |= np.abs(fr - fu) < max(IBW, fu * IREL)
        for k in range(1, int(20000 / fu) + 1):
            on |= np.abs(fr - k * fu) < max(IBW, k * fu * IREL)
    return db(p[use & ~on].sum() / (p[h1].sum() + 1e-30))


def clicks(x, ref):
    """dB: the largest 1 ms peak of the second difference in x re ref's median 1 ms peak"""
    def frames(y):
        d = np.abs(np.diff(y, 2))
        n = len(d) // 44
        return d[:n * 44].reshape(n, 44).max(1) if n else np.zeros(1)
    return db((frames(x).max() / (np.median(frames(ref)) + 1e-9)) ** 2)


_KW = None


def kweight(x):
    """BS.1770's K-weighting (the 48 kHz biquads, re-derived at 44.1 kHz) of x"""
    global _KW
    from scipy import signal
    if _KW is None:
        import math
        fc, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
        k, vh = math.tan(math.pi * fc / FS), 10 ** (g / 20)
        vb = vh ** 0.4996667741545416
        a0 = 1 + k / q + k * k
        b1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
        a1 = [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
        fc, q = 38.13547087602444, 0.5003270373238773
        k = math.tan(math.pi * fc / FS)
        a2 = [1, 2 * (k * k - 1) / (1 + k / q + k * k), (1 - k / q + k * k) / (1 + k / q + k * k)]
        _KW = (b1, a1, [1, -2, 1], a2)
    b1, a1, b2, a2 = _KW
    return signal.lfilter(b2, a2, signal.lfilter(b1, a1, x))


def measure(binary, case, grid, tmp):
    """{cell label: {metric: value}} of one case"""
    cid, _, setup, keys, kind = case
    x = render(binary, os.path.join(tmp, cid.replace("/", "_")), script(grid, setup, keys))
    mono = x.mean(1)
    ev, end = events(grid, keys)
    full = grid == "full"
    out = {}
    for label, t, ns, v, ped in ev:
        i = int(t / 1000 * FS)
        off = int((t + HOLD) / 1000 * FS)
        hold = mono[i:off]
        c = {"level": rms_db(hold), "peak": db((np.abs(mono[i:i + int(0.1 * FS)]).max() / 32768.0) ** 2)}
        if c["level"] < SILENT:
            out[label] = {"level": c["level"]}
            continue
        seg = mono[i + int(W0 * FS):i + int(W1 * FS)]
        p, fr = spectrum(seg)
        if len(ns) == 1:
            pk = []
            for k in range(1, 7):
                m = (fr > k * f0(ns[0]) * (1 - BAND)) & (fr < k * f0(ns[0]) * (1 + BAND))
                pk.append(p[m].max() if m.any() else 1e-30)
            for k in range(2, 7):
                c[f"H{k}"] = db(pk[k - 1] / pk[0])
            c["inh"] = offharm(p, fr, [f0(ns[0])])
        else:
            c["imd"] = offharm(p, fr, [f0(n) for n in ns])
        c["cent"] = float((p * fr).sum() / (p.sum() + 1e-30))
        c["decay"] = rms_db(mono[i + int(0.05 * FS):i + int(0.15 * FS)]) - rms_db(mono[off - int(0.15 * FS):off])
        body = mono[i + int(0.03 * FS):off]
        c["clk"] = clicks(body, body)
        if not ped:
            c["keyup"] = rms_db(mono[off + int(0.08 * FS):off + int(0.28 * FS)]) - rms_db(mono[off - int(0.2 * FS):off])
            c["clk_off"] = clicks(mono[off - int(0.01 * FS):off + int(0.3 * FS)], body)
        if full:
            import cb_loud
            c["lu"] = -0.691 + db(np.mean(np.square(kweight(hold / 32768.0))))
            c["cb"] = cb_loud.phon(cb_loud.bands(hold / 32768.0))
        out[label] = c
    n0 = int((end - 1000) / 1000 * FS)
    out["script"] = {"clip": int((np.abs(x) >= 32767).sum()), "noise": max(rms_db(mono[n0:]), -140.0)}
    return out


def _job(a):
    binary, case, grid, tmp = a
    return case[0], measure(binary, case, grid, tmp)


def measure_all(bins, grid, tag, n=None):
    """{case id: cells} for every case, the builds' binaries {fm1, hq}"""
    tmp = os.path.join(OUT, tag)
    jobs = [(bins[c[1]], c, grid, tmp) for c in cases(grid)]
    n = n or int(os.environ.get("PRISTINE_N", os.environ.get("PYTEST_N", "2")) or 2)
    with Pool(max(1, n)) as pool:
        res = dict(pool.map(_job, jobs, chunksize=2))
    if grid == "full":
        for cid in res:                                  # the limiter's gain reduction: RAW minus effects-off
            b, t, cfg, m = cid.split("/")
            if cfg == "FXOFF":
                raw = res.get(f"{b}/{t}/RAW/{m}", {})
                for lab, c in res[cid].items():
                    if "level" in c and "level" in raw.get(lab, {}) and lab != "script":
                        c["lim"] = raw[lab]["level"] - c["level"]
    return res


# ---------------------------------------------------------------- builds

def cc_build(tree, path, defs=()):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    subprocess.run([os.environ.get("CC", "cc"), "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST",
                    '-DAPP_VER="dev"', *defs, "-I" + os.path.join(tree, "firmware", "src"),
                    "-I" + os.path.join(tree, "firmware", "gen"), "-o", tmp,
                    os.path.join(tree, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    os.replace(tmp, path)
    return path


def tree_bins(tree, outdir):
    """the FM-1 and HQ host builds of a tree, built in parallel"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as ex:
        a = ex.submit(cc_build, tree, os.path.join(outdir, "fm1"))
        b = ex.submit(cc_build, tree, os.path.join(outdir, "hq"), HQ_DEFS)
        return {"fm1": a.result(), "hq": b.result()}


def last_tree(path=None):
    """the last release's tree (git archive LAST): build/release_diff/last"""
    path = path or os.path.join(OUT, "last")
    stamp = os.path.join(path, ".rev")
    rev = subprocess.run(["git", "-C", ROOT, "rev-parse", LAST + "^{commit}"], capture_output=True, text=True,
                         check=True).stdout.strip()
    if not (os.path.exists(stamp) and open(stamp).read() == rev):
        subprocess.run(["rm", "-rf", path])
        os.makedirs(path)
        a = subprocess.run(["git", "-C", ROOT, "archive", rev, "firmware", "tests/host.c"], capture_output=True,
                           check=True).stdout
        subprocess.run(["tar", "-x", "-C", path], input=a, check=True)
        open(stamp, "w").write(rev)
    return path


def last_cells(grid, base_tree=None):
    """the last release's cells, measured live from its tree; cached in build/release_diff/ by the release's commit
    and this tool's own bytes (a change to the measures re-measures it; nothing here can be saved from a candidate)"""
    import hashlib
    tree = last_tree(base_tree)
    key = hashlib.sha256(open(os.path.join(tree, ".rev")).read().encode() + open(__file__, "rb").read()).hexdigest()[:16]
    cache = os.path.join(OUT, f"last-{grid}-{key}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    cells = measure_all(tree_bins(tree, os.path.join(OUT, "last-bin")), grid, "last-" + grid)
    json.dump(cells, open(cache, "w"))
    return cells


# ---------------------------------------------------------------- the verdict

def load_expected(path):
    if not path or not os.path.exists(path):
        return []
    import yaml
    doc = yaml.safe_load(open(path)) or {}
    return doc.get("changes") or []


def tol_of(m):
    return TOL["H"] if m[0] == "H" and m[1:].isdigit() else TOL.get(m.replace("_off", ""), 3.0)


def moved(m, v, b):
    """the move of a metric beyond its tolerance (None: within, or both inaudible)"""
    if m == "cent":
        r = v / b if b else 1.0
        return r - 1 if abs(r - 1) > TOL["cent"] else None
    if m[0] == "H" and v <= FLOOR_H and b <= FLOOR_H:
        return None
    if m == "noise" and v <= -90 and b <= -90:
        return None
    d = v - b
    return d if abs(d) > tol_of(m) else None


def ceiling(cid, lab, m, v, b):
    """(limit, why) of a pristine ceiling for this value, or None"""
    cfg = cid.split("/")[2]
    if m[0] == "H" and m[1:].isdigit():
        lim = max(b + MARGIN, FLOOR_H)
        if m == "H3" and lab.endswith("v127") and b <= CEIL_H3:
            lim = min(lim, CEIL_H3)
        return lim, "harmonic distortion over the last release + 3 dB"
    if m in ("inh", "imd"):
        return b + MARGIN, "aliasing / inharmonic energy over the last release + 3 dB"
    if m in ("clk", "clk_off"):
        return b + 6.0, "clicks: over the last release + 6 dB"
    if m == "noise":
        return max(b + MARGIN, -90.0), "noise floor over the last release + 3 dB"
    if m == "clip" and cfg != "RAW":
        return 0, "a clip outside RAW"
    if m == "keyup" and cfg != "PRESET":            # (a preset's delay repeats the strike after the key-up: diff only;
        return 0.5, "louder after the key-up"       # 0.9.9's F18 HQ k69 v64 is +1.7 dB by its echo)
    return None


def judge(cand, base, expected):
    """(failures, moves, listed): failures [(cid, lab, metric, value, base, limit or None, why)]"""
    fails, moves, used = [], [], set()
    for cid in sorted(cand):
        bc = base.get(cid)
        if bc is None:
            fails.append((cid, "*", "*", None, None, None, "no baseline for this case"))
            continue
        for lab, ms in cand[cid].items():
            bl = bc.get(lab, {})
            for m, v in ms.items():
                b = bl.get(m)
                if b is None:
                    if m != "lim" and "level" in bl and bl["level"] >= SILENT:
                        fails.append((cid, lab, m, v, None, None, "no baseline value"))
                    continue
                c = ceiling(cid, lab, m, v, b)
                if c and v > c[0] + 1e-9:
                    fails.append((cid, lab, m, v, b, c[0], "CEILING: " + c[1]))
                d = moved(m, v, b)
                if d is None:
                    continue
                hit = None
                for i, e in enumerate(expected):
                    if fnmatch.fnmatch(f"{cid}/{lab}", e.get("match", "")) and \
                            any(fnmatch.fnmatch(m, pat) for pat in str(e.get("metric", "*")).split()):
                        lo, hi = e.get("min"), e.get("max")
                        if (lo is None or d >= lo) and (hi is None or d <= hi):
                            hit = i
                            break
                moves.append((cid, lab, m, v, b, d, hit))
                if hit is None:
                    mv = f"{d:+.0%} (tolerance {TOL['cent']:.0%})" if m == "cent" else f"{d:+.2f} (tolerance {tol_of(m)})"
                    fails.append((cid, lab, m, v, b, None, f"moved {mv}: not in the expected-changes file"))
                else:
                    used.add(hit)
                    r = str(expected[hit].get("reason", "")).strip()
                    if not r or r.upper().startswith("TODO"):
                        fails.append((cid, lab, m, v, b, None, f"listed (entry {hit + 1}) without a reason"))
    stale = [i for i in range(len(expected)) if i not in used]
    return fails, moves, stale


def report(cand, base, fails, moves, stale, expected, grid, top=25):
    nval = sum(len(ms) for c in cand.values() for ms in c.values())
    ceil = [f for f in fails if f[6].startswith("CEILING")]
    lines = [f"release diff ({grid}) against {LAST}: {len(cand)} cases, {nval} values; "
             f"{len(moves)} moved beyond tolerance ({sum(1 for x in moves if x[6] is not None)} listed); "
             f"{len(ceil)} over a pristine ceiling; {len(fails)} failures",
             "verdict: " + ("PASS" if not fails else "FAIL")]
    if ceil:
        lines.append(f"\npristine ceilings broken (worst {min(top, len(ceil))} of {len(ceil)}):")
        for cid, lab, m, v, b, lim, why in sorted(ceil, key=lambda f: -(f[3] - f[5]))[:top]:
            lines.append(f"  {cid}/{lab:10s} {m:7s} {v:8.1f}  last {b:8.1f}  limit {lim:8.1f}  {why[9:]}")
        by = {}
        for f in ceil:
            k = (f[0].split("/")[1], f[2])
            by[k] = by.get(k, 0) + 1
        lines.append("  by TYPE and metric: " + ", ".join(f"{t} {m} {n}" for (t, m), n in sorted(by.items())))
    other = [f for f in fails if not f[6].startswith("CEILING")]
    if other:
        lines.append(f"\nunlisted or unexplained moves (worst {min(top, len(other))} of {len(other)}):")
        for cid, lab, m, v, b, lim, why in sorted(other, key=lambda f: -abs((f[3] or 0) - (f[4] or 0)))[:top]:
            lines.append(f"  {cid}/{lab:10s} {m:7s} {v if v is None else round(v, 2)!s:>8}  last "
                         f"{b if b is None else round(b, 2)!s:>8}  {why}")
    big = sorted(moves, key=lambda x: -abs(x[5]) / max(tol_of(x[2]) if x[2] != "cent" else TOL["cent"], 0.1))[:top]
    if big:
        lines.append(f"\nthe biggest moves (in tolerances):")
        for cid, lab, m, v, b, d, hit in big:
            lines.append(f"  {cid}/{lab:10s} {m:7s} {v:8.2f}  last {b:8.2f}  move {d:+7.2f}  "
                         + ("listed: " + str(expected[hit].get("reason", ""))[:60] if hit is not None else "UNLISTED"))
    if stale:
        lines.append("\nexpected-changes entries that matched nothing (stale? remove them): "
                     + ", ".join(str(i + 1) for i in stale))
    return "\n".join(lines)


def draft(moves, path):
    """the unlisted moves as expected-changes entries, grouped by case and metric (reason: TODO)"""
    import yaml
    g = {}
    for cid, lab, m, v, b, d, hit in moves:
        if hit is None:
            k = (cid, m)
            lo, hi = g.get(k, (d, d))
            g[k] = (min(lo, d), max(hi, d))
    doc = {"changes": [{"match": f"{cid}/*", "metric": m, "min": round(lo - 0.5, 1), "max": round(hi + 0.5, 1),
                        "reason": "TODO"} for (cid, m), (lo, hi) in sorted(g.items())]}
    yaml.safe_dump(doc, open(path, "w"), sort_keys=False, width=120)


def run(grid="fast", bins=None, expected=EXPECTED, base_tree=None):
    """(ok, report text, cand, base, fails, moves)"""
    bins = bins or tree_bins(ROOT, os.path.join(OUT, "cand"))
    cand = measure_all(bins, grid, "cand-" + grid)
    if grid == "fast":
        doc = json.load(open(BASE_FAST))
        base = doc["cases"]
    else:
        base = last_cells(grid, base_tree)
    exp = load_expected(expected)
    fails, moves, stale = judge(cand, base, exp)
    return not fails, report(cand, base, fails, moves, stale, exp, grid), cand, base, fails, moves


def save_guard(name):
    """a gate baseline is re-saved only on a tree that passes the pristine gate (AGENTS.md 5b: a hash catches a
    change, not a bad change); the report's head goes in the commit message"""
    ok, text, *_ = run("fast")
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "save-guard.txt"), "w").write(text + "\n")
    print("\n".join(text.splitlines()[:2]) + f"  (build/release_diff/save-guard.txt: put its head in the commit)")
    if not ok:
        raise SystemExit(f"{name} --save refused: the pristine gate fails against {LAST} "
                         "(build/release_diff/save-guard.txt). Fix the sound, or list the intended moves in "
                         "tests/expected_changes.yaml with the coordinator's sign-off.")


def main():
    import time
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", choices=GRID, default="fast")
    ap.add_argument("--fm1")
    ap.add_argument("--hq")
    ap.add_argument("--expected", default=EXPECTED)
    ap.add_argument("--report")
    ap.add_argument("--draft")
    ap.add_argument("--save-base", action="store_true")
    ap.add_argument("--base-tree")
    ap.add_argument("--json", help="write the candidate's cells (JSON)")
    a = ap.parse_args()
    t0 = time.time()
    bins = {"fm1": os.path.abspath(a.fm1), "hq": os.path.abspath(a.hq)} if a.fm1 and a.hq else None
    if a.save_base:
        if a.grid != "fast" or not bins:
            raise SystemExit("--save-base: --grid fast with --fm1 and --hq of the last release's tree")
        cells = measure_all(bins, "fast", "base-fast")
        json.dump({"release": LAST, "cases": cells}, open(BASE_FAST, "w"), indent=0, sort_keys=True)
        print(f"{BASE_FAST}: {len(cells)} cases ({time.time() - t0:.1f} s)")
        return 0
    ok, text, cand, base, fails, moves = run(a.grid, bins, a.expected, a.base_tree)
    text += f"\n({time.time() - t0:.1f} s)"
    print(text)
    if a.report:
        open(a.report, "w").write(text + "\n")
    if a.json:
        json.dump(cand, open(a.json, "w"), indent=0, sort_keys=True)
    if a.draft:
        draft(moves, a.draft)
        print("draft:", a.draft)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
