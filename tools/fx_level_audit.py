#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The effects' level against the bypass by the ear's measures (host), the audit of 2026-11-09 (the user on rc3's jack:
CHORUS / PHASER on sound much louder and richer than FX off; DELAY and COMP do not). Per cell (engine x signal x
effect) the effect on vs the bypass (CC 111 127), each from every effect off (CLEAN):

  lufs    integrated K-weighted loudness, stereo (BS.1770 channel sum, ungated) and of the mono sum (L + R) / 2
  cb      critical-band loudness (tools/cb_loud.py, phon-like): the binaural sum (L ear + R ear: headphones / the jack
          in stereo), the mono sum, and its 1-5 kHz bands alone (presence)
  att     the attack's RMS (the first 60 ms of the note) and the crest factor (peak - RMS, mono sum)
  pk/rms  per channel; width: side over mid (dB) and the L-R correlation

  python3 tools/fx_level_audit.py [--bin build/fxla/one] [--engines R,V,M] [--fx chorus,phaser,...] [-n 2]
  python3 tools/fx_level_audit.py --presets       factory presets: on vs bypass, and each with its CHO / PHA off
  python3 tools/fx_level_audit.py --dry BIN_A BIN_B   the dry (bypass) output level of two builds (e.g. 0.9.9 vs rc3)
"""
import argparse
import math
import os
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402
import cb_loud as cb  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "build", "fxla")
FS = 44100
T_ON, T_OFF, END = 500, 2000, 2300
CLEAN = [(106, 0), (84, 0), (17, 0), (85, 0), (12, 0), (88, 0), (91, 0), (70, 0), (105, 0), (34, 64), (35, 64),
         (37, 64)]
ENGINES = {"R": [], "V": [(79, 0)], "M": [(79, 127)]}   # (on F01: the plain Rhodes ST77, VIBES, MARIMBA)
FX = {
    "chorus40": [(85, 64), (86, 40)], "chorus56": [(85, 64), (86, 56)], "chorus64": [(85, 64), (86, 64)],
    "chorus127": [(85, 64), (86, 127)],
    "phaser0": [(12, 127), (13, 0)], "phaser40": [(12, 127), (13, 40)], "phaser64": [(12, 127), (13, 64)],
    "phaser85": [(12, 127), (13, 85)], "phaser127": [(12, 127), (13, 127)],
    "trem64": [(17, 64), (21, 127), (18, 64)], "pan64": [(17, 64), (21, 0), (18, 64)],
    "wah64": [(17, 127), (18, 64)],
    "delay40": [(88, 40), (89, 40)], "hall40": [(95, 40), (91, 40)],
    "comp64": [(106, 127), (107, 64), (113, 0)], "lp18": [(70, 40), (74, 64), (71, 64)],
}
SIGNALS = {}
for v in (40, 80, 127):
    for n, nm in ((36, "C2"), (60, "C4"), (84, "C6")):
        SIGNALS[f"{nm}v{v}"] = [(n, v)]
    SIGNALS[f"chd{v}"] = [(n, v) for n in (48, 55, 60, 64)]
_PRES = np.array([1000 <= f <= 5000 for f in cb._FC])
BANDS = {"low": cb._FC < 250, "body": (cb._FC >= 250) & (cb._FC < 1000), "pres": _PRES, "air": cb._FC > 5000}


def render(job):
    """job: (bin, tag, pc, ccs, byp, notes) -> (tag, L, R)"""
    b, tag, pc, ccs, byp, notes = job
    ls = ["100 midi 0xB0 111 0"] + ([f"200 midi 0xC0 {pc} 0"] if pc >= 0 else []) + [f"300 midi 0xB0 {c} {v}" for c, v in ccs]
    if byp:
        ls.append("400 midi 0xB0 111 127")
    for n, v in notes:
        ls += [f"{T_ON} midi 0x90 {n} {v}", f"{T_OFF} midi 0x80 {n} 0"]
    ls.append(f"{END} end")
    d = os.path.join(OUT, "runs", os.path.basename(b))
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, tag.replace("/", "_"))
    with open(out + ".txt", "w") as f:
        f.write("\n".join(ls) + "\n")
    p = subprocess.run([b, out, out + ".txt"], capture_output=True, text=True)
    assert p.returncode == 0, (tag, p.stdout[-400:], p.stderr[-400:])
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2) / 32768
    for e in (".wav", ".txt"):
        os.remove(out + e)
    return tag, x[:, 0], x[:, 1]


def spec_loud(bnd):
    e = np.asarray(bnd, float) * 10 ** ((cb.CAL + 3.01) / 10)
    es = e @ cb._SPREAD
    return np.maximum(0.08 * cb._ET ** 0.23 * ((0.5 + 0.5 * es / cb._ET) ** 0.23 - 1), 0)


def ph(s):
    return 10 * math.log2(max(float(s), 1e-6))


def db(x):
    return 10 * math.log10(float(x) + 1e-20)


def measure(L, R):
    a, e = int(T_ON * FS / 1000), int(T_OFF * FS / 1000)
    l, r = L[a:e], R[a:e]
    m = (l + r) / 2
    bl, br = cb.bands(l), cb.bands(r)
    sl, sr, sm = spec_loud(bl), spec_loud(br), spec_loud(cb.bands(m))
    att = m[:int(0.06 * FS)]
    mid, side = (l + r) / 2, (l - r) / 2
    return dict(
        lufs=bm.lufs(np.stack([l, r], 1) * 32767), lufs_m=bm.lufs(m * 32767),
        cb=ph(sl.sum() + sr.sum()), cb_m=ph(sm.sum()), cb_p=ph(sm[_PRES].sum()),
        cb_pb=ph(sl[_PRES].sum() + sr[_PRES].sum()),
        **{"e_" + k: db(bl[v].sum() + br[v].sum()) for k, v in BANDS.items()},   # (band energy, dB: robust near threshold)
        sus=db(np.mean(m[int(1.0 * FS):] ** 2)),
        att=db(np.mean(att * att)), crest=20 * math.log10(np.abs(m).max() + 1e-12) - db(np.mean(m * m)),
        pkL=20 * math.log10(np.abs(l).max() + 1e-12), pkR=20 * math.log10(np.abs(r).max() + 1e-12),
        rmsL=db(np.mean(l * l)), rmsR=db(np.mean(r * r)),
        width=max(-99.0, db(np.mean(side * side)) - db(np.mean(mid * mid))),
        corr=float(np.corrcoef(l, r)[0, 1]) if l.std() > 0 and r.std() > 0 else 1.0)


def run_jobs(jobs, n):
    res = {}
    with Pool(n) as pool:
        for job, (tag, L, R) in zip(jobs, pool.imap(render, jobs, chunksize=4)):
            res[tag] = measure(L, R)
    return res


KEYS = ("lufs", "lufs_m", "cb", "cb_m", "cb_pb", "att", "crest")


def delta_rows(res, pairs):
    """pairs: [(label, tag_on, tag_off)] -> rows of on - off and the on's width / corr"""
    rows = []
    for lab, ton, toff in pairs:
        a, b = res[ton], res[toff]
        rows.append((lab, {k: a[k] - b[k] for k in KEYS}, a["width"], a["corr"], b["width"], max(a["pkL"], a["pkR"])))
    return rows


def print_rows(rows, title):
    print(f"== {title}")
    print(f"   {'cell':28s} {'LUFS':>6s} {'LUFSm':>6s} {'CBbin':>6s} {'CBmon':>6s} {'CB1-5k':>6s} {'att':>6s} "
          f"{'crest':>6s} {'width':>6s} {'corr':>5s} {'w.off':>6s} {'pk':>6s}")
    for lab, d, w, c, w0, pk in rows:
        print(f"   {lab:28s} {d['lufs']:+6.2f} {d['lufs_m']:+6.2f} {d['cb']:+6.2f} {d['cb_m']:+6.2f} {d['cb_pb']:+6.2f} "
              f"{d['att']:+6.2f} {d['crest']:+6.2f} {w:6.1f} {c:5.2f} {w0:6.1f} {pk:6.1f}")


def summary(rows, groups):
    print("== summary: mean (min .. max) of on - bypass over the cells")
    print(f"   {'effect':12s} {'LUFS':>18s} {'CB binaural':>18s} {'CB mono':>18s} {'CB 1-5k':>18s} {'width':>6s}")
    for g in groups:
        sel = [r for r in rows if r[0].split()[1] == g]
        if not sel:
            continue
        def st(k):
            v = np.array([r[1][k] for r in sel])
            return f"{v.mean():+5.2f} ({v.min():+5.2f}..{v.max():+5.2f})"
        print(f"   {g:12s} {st('lufs'):>18s} {st('cb'):>18s} {st('cb_m'):>18s} {st('cb_pb'):>18s} "
              f"{np.mean([r[2] for r in sel]):6.1f}")


def compare(bins, n):
    cells = []
    for v in (20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 127):
        for nm, notes in (("C2", [36]), ("C4", [60]), ("C6", [84]), ("chd", [48, 55, 60, 64])):
            nn = [(k, v) for k in notes]
            cells.append((f"F01dry {nm} v{v}", 0, CLEAN, 1, nn))
            if v in (40, 80, 127):
                cells.append((f"LIGHT {nm} v{v}", 0, CLEAN + [(40, 127)], 1, nn))
                for t, tv in (("SUIT", 32), ("FELT", 64), ("DYNO", 96)):
                    cells.append((f"{t}dry {nm} v{v}", 0, CLEAN + [(80, tv)], 1, nn))
                cells.append((f"boot {nm} v{v}", -1, [], 0, nn))
    for pc in list(range(16)) + [32, 33, 34]:
        nm = f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}"
        for v in (60, 100):
            cells.append((f"{nm} chd v{v}", pc, [], 0, [(k, v) for k in (48, 55, 60, 64)]))
            cells.append((f"{nm} C4 v{v}", pc, [], 0, [(60, v)]))
    jobs = []
    for b in bins:
        for lab, pc, ccs, byp, nn in cells:
            jobs.append((b, f"{b}|{lab}", pc, ccs, byp, nn))
    res = run_jobs(jobs, n)
    ks = ("lufs", "cb", "e_low", "e_body", "e_pres", "e_air", "att", "sus", "crest")
    print(f"== {bins[1]} - {bins[0]} (dry: bypass, every effect off; boot: no program change; Fxx: the preset as played)")
    print(f"   {'cell':20s} " + " ".join(f"{k:>7s}" for k in ks) + f" {'pkA':>6s} {'pkB':>6s}")
    for lab, *_ in cells:
        x, y = res[f"{bins[0]}|{lab}"], res[f"{bins[1]}|{lab}"]
        print(f"   {lab:20s} " + " ".join(f"{y[k] - x[k]:+7.2f}" for k in ks) +
              f" {max(x['pkL'], x['pkR']):6.1f} {max(y['pkL'], y['pkR']):6.1f}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=os.path.join(OUT, "one"))
    ap.add_argument("--engines", default="R,V,M")
    ap.add_argument("--fx", default=",".join(FX))
    ap.add_argument("--signals", default=",".join(SIGNALS))
    ap.add_argument("--presets", action="store_true")
    ap.add_argument("--dry", nargs=2, metavar=("BIN_A", "BIN_B"))
    ap.add_argument("--cmp", nargs=2, metavar=("BIN_A", "BIN_B"),
                    help="the dry sound of two builds per band: velocity sweep, boot sound, presets as played")
    ap.add_argument("--all", action="store_true", help="every cell's row, not only the summary")
    ap.add_argument("-n", type=int, default=int(os.environ.get("PYTEST_N", "2") or 2))
    a = ap.parse_args()
    sigs = a.signals.split(",")
    if a.cmp:
        return compare(a.cmp, a.n)
    if a.dry:
        jobs = []
        for bi in a.dry:
            for e in a.engines.split(","):
                for lv in ("pre", 64):
                    for s in sigs:
                        ccs = CLEAN + ENGINES[e] + ([] if lv == "pre" else [(110, 64)])
                        jobs.append((bi, f"{bi}|{e}|{lv}|{s}", 0, ccs, 1, SIGNALS[s]))
        res = run_jobs(jobs, a.n)
        print(f"== dry (bypass, every effect off): {a.dry[1]} - {a.dry[0]}, LEVEL = F01's ('pre') or 64")
        print(f"   {'cell':20s} {'LUFS':>6s} {'CBbin':>6s} {'pkA':>6s} {'pkB':>6s} {'rmsB':>6s}")
        for e in a.engines.split(","):
            for lv in ("pre", 64):
                for s in sigs:
                    x, y = res[f"{a.dry[0]}|{e}|{lv}|{s}"], res[f"{a.dry[1]}|{e}|{lv}|{s}"]
                    print(f"   {e + ' ' + str(lv) + ' ' + s:20s} {y['lufs'] - x['lufs']:+6.2f} {y['cb'] - x['cb']:+6.2f} "
                          f"{max(x['pkL'], x['pkR']):6.1f} {max(y['pkL'], y['pkR']):6.1f} {y['rmsL']:6.1f}")
        return 0
    if a.presets:
        pcs = list(range(16)) + [32, 33, 34]
        jobs, pairs = [], []
        for pc in pcs:
            nm = f"F{pc + 1:02d}" if pc < 16 else f"F{pc - 15:02d}"
            for s in ("C4v80", "chd80", "chd127"):
                for k, ccs, byp in (("on", [], 0), ("byp", [], 1), ("nocp", [(85, 0)], 0)):
                    jobs.append((a.bin, f"{nm}|{s}|{k}", pc, ccs, byp, SIGNALS[s]))
                pairs.append((f"{nm} fx-on-vs-byp {s}", f"{nm}|{s}|on", f"{nm}|{s}|byp"))
                pairs.append((f"{nm} cho/pha-only {s}", f"{nm}|{s}|on", f"{nm}|{s}|nocp"))
        res = run_jobs(jobs, a.n)
        print_rows(delta_rows(res, pairs), f"presets ({a.bin})")
        return 0
    jobs, pairs = [], []
    for e in a.engines.split(","):
        for s in sigs:
            jobs.append((a.bin, f"{e}|{s}|byp", 0, CLEAN + ENGINES[e], 1, SIGNALS[s]))
            for f in a.fx.split(","):
                jobs.append((a.bin, f"{e}|{s}|{f}", 0, CLEAN + ENGINES[e] + FX[f], 0, SIGNALS[s]))
                pairs.append((f"{e} {f} {s}", f"{e}|{s}|{f}", f"{e}|{s}|byp"))
    res = run_jobs(jobs, a.n)
    rows = delta_rows(res, pairs)
    if a.all:
        print_rows(rows, f"effects on vs bypass ({a.bin})")
    summary(rows, a.fx.split(","))
    return 0


if __name__ == "__main__":
    sys.exit(main())
