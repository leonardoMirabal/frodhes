# SPDX-License-Identifier: GPL-3.0-only
"""Factory preset review on the host build: render each factory preset (A, as presets.c has it) and proposed variants
(B, C ...: parameter changes over the current record) with one musical phrase per engine, measure them and write the
listening set.

    python3 tools/preset_review.py VARIANTS.json [--out build/presets-redo] [--only F01,F07] [-j 2]

VARIANTS.json: {"F01": {"note": "...", "pick": "B", "why": "...",
                        "B": {"desc": "delay DIG mix 18 ...", "set": {"P_DL_SW": 1, ...}, "level": "auto"}, ...}, ...}
"level": "auto" solves P_LEVEL so the variant sits at its preset's target re F01 A (Rhodes: half way between the chord
and the phrase at 0, as the MANUAL says; VIBES / MARIMBA: the chord at tools/hil_scan.py's PRESET_TARGET), or
{"mode": "mid" | "chord" | "phrase", "target": LU re F01 A} for another target. A is never changed.

Per render: LUFS-I (BS.1770-4, gated), true peak (4x oversampled), sample peak, clipped samples, samples at the master
limiter's ceiling (fx.c LIM_T), the FFT balance over the 24 critical bands (Bark) and five summary regions, the chord
level hil_scan.py's presets suite judges (A3 C4 E4 v100, 1.1 s from the onset), the predicted whole-ISR CPU
(tests/py/test_presets_cpu.py) and the host's cpu-proxy. Every render's parameters are read back from the host's dump
and must equal the intended record.

Out: <out>/Fxx/{A,B,C}.wav (the phrase, 44.1 kHz), <out>/Fxx/matched/ (each preset's variants at its quietest one's
loudness), <out>/results.json, INDEX.md, choices.txt, proposed_records.txt (the full id = value list per variant).
Host only: no FM-1, no MIDI ports, no audio devices."""
import argparse
import json
import os
import re
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "firmware", "src")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
LIM_T = 29204                    # fx.c: the look-ahead limiter's ceiling (-1.0 dBFS; 24000 before 0.9.10)
LEVEL_DB = 0.1875                # params.c P_LEVEL: dB a step
sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
sys.path.insert(0, os.path.join(ROOT, "tools"))


# ---- the firmware's tables
def params():
    """[(name, id, hi, def, cc)] from params.c (the enum order is the id)"""
    s = open(os.path.join(SRC, "params.c")).read()
    enum = re.search(r"enum \{ (P_TYPE.*?)P_COUNT \};", s, re.S).group(1)
    names = re.findall(r"\b(P_[A-Z0-9_]+)\b", re.sub(r"/\*.*?\*/", "", enum, flags=re.S))
    rows = {m.group(1): (int(m.group(2)), int(m.group(3)), int(m.group(4)))
            for m in re.finditer(r"\[(P_[A-Z0-9_]+)\] = \{\"[^\"]*\", (\d+), (\d+), F_\w+, (\d+)", s)}
    drv = [int(v) for v in re.search(r"DRV_CC84\[\d*\] = \{([^}]*)\}", s).group(1).split(",")]
    return [(n, i, *rows[n]) for i, n in enumerate(names)], drv


def factory():
    """[(name, {P_X: v})] of presets.c PR_FNAME / PR_FDATA"""
    s = open(os.path.join(SRC, "presets.c")).read()
    names = re.findall(r'"([^"]*)"', re.search(r"PR_FNAME\[PR_NF\] = \{(.*?)\};", s, re.S).group(1))
    body = re.search(r"PR_FDATA\[PR_NF\]\[PR_FDN\] = \{(.*?)\n\};", s, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    recs = []
    for row in re.findall(r"\{([^{}]*)\}", body):
        t = [x.strip() for x in row.split(",") if x.strip()]
        t = t[:t.index("0xFF")]
        recs.append(dict(zip(t[0::2], (int(v) for v in t[1::2]))))
    assert len(names) == len(recs) == 19, (len(names), len(recs))
    return list(zip(names, recs))


def preset_pc(p):
    """row p (0-based) -> program change (presets.c preset_of_pc)"""
    return p if p < 16 else p + 16


def cc_of(name, x, hi, drv):
    """(CC number offset unused) the CC value that sets parameter name to x (frodhes.c midi_cc's bands)"""
    if name == "P_TYPE":
        return (80, x * 32 + 8) if x < 4 else (79, {4: 64, 5: 127}[x])
    if name == "P_DRV_SW":
        return (None, 0 if x == 0 else 127 if x == 1 else drv[x] + 4)
    if hi == 127:
        return (None, x)
    return (None, min(127, -(-x * 128 // (hi + 1))))


# ---- the phrases (s, kind, note, velocity)
def _hold(t, notes, v, dur):
    return [(t, "on", n, v) for n in notes] + [(t + dur, "off", n, 0) for n in notes]


def phrase_rhodes():
    """chords and a melody, soft to hard: Dm9, G13, Cmaj9, A7alt; ~8 s played, 3.5 s of tail"""
    ev = []
    ev += _hold(0.0, [50, 53, 57, 60, 64], 38, 1.95)                 # Dm9 pp
    ev += _hold(0.5, [69], 46, 0.45) + _hold(1.0, [72], 50, 0.45) + _hold(1.5, [74], 56, 0.45)
    ev += _hold(2.0, [43, 53, 57, 59, 64], 62, 1.95)                 # G13 mp
    ev += _hold(2.5, [76], 66, 0.45) + _hold(3.0, [74], 72, 0.45) + _hold(3.5, [71], 76, 0.45)
    ev += _hold(4.0, [48, 52, 55, 59, 62], 92, 1.95)                 # Cmaj9 f
    ev += _hold(4.5, [76], 96, 0.4) + _hold(5.0, [79], 100, 0.4) + _hold(5.5, [81], 104, 0.4)
    ev += _hold(6.0, [45, 55, 61, 65, 72], 122, 1.9)                 # A7(b9 #5) ff
    ev += _hold(6.5, [84], 118, 0.2) + _hold(6.75, [81], 112, 0.2) + _hold(7.0, [79], 120, 0.9)
    return sorted(ev), 11.5


def phrase_bars():
    """a mallet line, soft to hard: a soft arpeggio, double stops mf, a hard run down, a held chord; ~8 s, tail"""
    ev = []
    for k, n in enumerate([60, 64, 67, 71, 74, 71, 67, 64]):         # soft eighths at 120 BPM
        ev += _hold(0.25 * k, [n], 34 + 3 * k, 0.24)
    for k, (a, b) in enumerate([(62, 69), (64, 71), (65, 72), (67, 74)]):   # double stops, quarters, mf
        ev += _hold(2.0 + 0.5 * k, [a, b], 70 + 4 * k, 0.48)
    for k, n in enumerate([84, 83, 81, 79, 77, 76, 74, 72]):         # hard sixteenths down
        ev += _hold(4.0 + 0.125 * k, [n], 112 + (k % 2) * 8, 0.12)
    ev += _hold(5.25, [79], 120, 0.7) + _hold(6.0, [53, 60, 64, 69], 118, 1.5)   # the accent, then a chord f
    return sorted(ev), 11.0


CHORD = [(0.0, "on", n, 100) for n in (57, 60, 64)] + [(1.5, "off", n, 0) for n in (57, 60, 64)]


# ---- measurement
def k_weight(x, fs=FS):
    """BS.1770 K-weighting at fs (the two biquads designed for fs)"""
    from scipy.signal import lfilter
    import math
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    a2 = [1, 2 * (K * K - 1) / (1 + K / Q + K * K), (1 - K / Q + K * K) / (1 + K / Q + K * K)]
    b2 = [1, -2, 1]
    return lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)


def lufs_i(x):
    """integrated loudness, BS.1770-4 (400 ms blocks, 75 % overlap, -70 LUFS absolute and -10 LU relative gates)"""
    y = k_weight(x)
    n, hop = int(0.4 * FS), int(0.1 * FS)
    if len(y) < n:
        return -70.0
    z = np.array([np.sum(np.mean(y[i:i + n] ** 2, axis=0)) for i in range(0, len(y) - n + 1, hop)])
    lk = -0.691 + 10 * np.log10(z + 1e-20)
    g = z[lk > -70]
    if not len(g):
        return -70.0
    rel = -0.691 + 10 * np.log10(np.mean(g)) - 10
    g = z[(lk > -70) & (lk > rel)]
    return float(-0.691 + 10 * np.log10(np.mean(g)))


def lufs_ungated(x):
    """hil_scan.py lufs(): the ungated K-weighted level (the presets suite's chord measure)"""
    y = k_weight(x)
    return float(-0.691 + 10 * np.log10(np.sum(np.mean(y ** 2, axis=0)) + 1e-20))


def true_peak(x):
    from scipy.signal import resample_poly
    return float(20 * np.log10(np.abs(resample_poly(x, 4, 1, axis=0)).max() + 1e-12))


BARK = [20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300,
        6400, 7700, 9500, 12000, 15500]
REGIONS = [("low <200", 20, 200), ("lowmid 200-630", 200, 630), ("mid 630-2k", 630, 2000),
           ("presence 2-6.4k", 2000, 6400), ("air >6.4k", 6400, 20000)]


def bands(x):
    """the power per critical band (dB re the total) and per summary region, from a Welch PSD of the mid signal"""
    from scipy.signal import welch
    f, p = welch(x.mean(axis=1), FS, nperseg=8192)
    tot = p[(f >= 20) & (f < 20000)].sum() + 1e-30
    bk = [float(10 * np.log10(p[(f >= lo) & (f < hi)].sum() / tot + 1e-30)) for lo, hi in zip(BARK[:-1], BARK[1:])]
    rg = {k: float(10 * np.log10(p[(f >= lo) & (f < hi)].sum() / tot + 1e-30)) for k, lo, hi in REGIONS}
    return bk, rg


def measure(x):
    """x: (n, 2) int16-scale floats"""
    xf = x / 32768.0
    a = np.abs(x)
    bk, rg = bands(xf)
    return dict(lufs=lufs_i(xf), tp=true_peak(xf), peak=float(20 * np.log10(a.max() / 32768 + 1e-12)),
                clipped=int((a >= 32767).sum()), at_limit=int((a >= LIM_T - 150).sum()), bark=bk, regions=rg,
                dyn=lufs_ungated(xf[int(6.0 * FS):int(8.0 * FS)]) - lufs_ungated(xf[0:int(2.0 * FS)]),
                tail=lufs_ungated(xf[int(8.5 * FS):int(10.5 * FS)]) - lufs_ungated(xf[int(6.0 * FS):int(8.0 * FS)]),
                width=float(10 * np.log10(np.mean((xf[:, 0] - xf[:, 1]) ** 2) / (np.mean((xf[:, 0] + xf[:, 1]) ** 2) + 1e-20) + 1e-20)))


# ---- rendering
PRE_MS = 600                     # program change, the CCs, the developer reset (CC 10) at 550 ms, the phrase at 600


def render(tag, pidx, full, base, events, dur, P, drv, outdir):
    """one host run: the program change of preset pidx, CCs for every parameter where full differs from base (the
    record the program change loads), the phrase; returns (wav (n, 2) float, dumped parameters, cpu-proxy)"""
    lines = [f"10 midi 0xC0 {preset_pc(pidx)}"]
    t = 30
    order = sorted((k for k in full if full[k] != base[k]), key=lambda k: (k.endswith("_SW") or k in ("P_MOT_ON", "P_RV_TYPE"), k))
    byname = {r[0]: r for r in P}
    for k in order:                                      # amounts first, the switches last (an amount up from 0 can wake one)
        n, i, hi, de, cc = byname[k]
        alt, v = cc_of(n, full[k], hi, drv)
        lines.append(f"{t} midi 0xB0 {alt or cc} {v}")
        t += 4
    assert t < PRE_MS - 60, "too many CCs for the preroll"
    lines.append(f"{PRE_MS - 50} midi 0xB0 10 127")         # fx.c fx_reset_state: every LFO / line from its boot state
    for s, kind, n, v in events:
        ms = PRE_MS + int(round(1000 * s))
        lines.append(f"{ms} midi 0x90 {n} {v}" if kind == "on" else f"{ms} midi 0x80 {n} 0")
    end = PRE_MS + int(1000 * dur)
    lines.append(f"{end - 10} dump")
    lines.append(f"{end} end")
    lines.sort(key=lambda l: int(l.split()[0]))
    base_p = os.path.join(outdir, tag)
    open(base_p + ".txt", "w").write("\n".join(lines) + "\n")
    p = subprocess.run([BIN, base_p, base_p + ".txt"], capture_output=True, text=True)
    assert p.returncode == 0, (tag, p.stdout[-500:], p.stderr[-500:])
    w = wave.open(base_p + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    for f in os.listdir(outdir):                         # (the host's screenshots: not needed)
        if f.startswith(tag + "-") and f.endswith(".ppm"):
            os.remove(os.path.join(outdir, f))
    os.remove(base_p + ".wav")
    dumped = {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"^@ \d+ p\.(\d+) (\d+)$", p.stdout, re.M)}
    got = {r[0]: dumped[r[1]] for r in P}
    bad = {k: (full[k], got[k]) for k in full if got[k] != full[k]}
    assert not bad, f"{tag}: parameters not as intended (want, got): {bad}"
    m = re.search(r"cpu-proxy max (\d+) us", p.stdout)
    return x[int(PRE_MS * FS / 1000):], got, int(m.group(1)) if m else None


def chord_level(x):
    """hil_scan.py presets(): the ungated K-weighted level of 1.1 s from the chord's onset"""
    a = np.abs(x).max(axis=1)
    i = int(np.argmax(a > 200))
    return lufs_ungated(x[i:i + int(1.1 * FS)] / 32768.0)


def write_wav(path, x):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y = np.clip(np.round(x), -32767, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(y.tobytes())


def is_bars(rec):
    return rec.get("P_TYPE", 0) >= 4


# chord level targets re F01 A (LU): tools/hil_scan.py PRESET_TARGET (1-based preset numbers)
CHORD_TARGET = {13: 2.5, 14: 2.5, 15: -0.2, 16: 2.5}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("variants")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "presets-redo"))
    ap.add_argument("--only", default="")
    ap.add_argument("-j", type=int, default=2)
    a = ap.parse_args()
    subprocess.run(["make", "-C", ROOT, "-j2", "build/host/frodhes"], check=True, capture_output=True)
    P, drv = params()
    defaults = {r[0]: r[3] for r in P}
    his = {r[0]: r[2] for r in P}
    fac = factory()
    V = json.load(open(a.variants))
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    tmp = os.path.join(a.out, "_runs")
    os.makedirs(tmp, exist_ok=True)
    import test_presets_cpu as tpc
    import cpu_budget as cb
    T = cb.load()
    PP = tpc.params()

    def full_of(rec, sets):
        f = dict(defaults)
        f.update(rec)
        f.update(sets)
        for k, v in f.items():
            assert k in defaults and 0 <= v <= his[k] and int(v) == v, (k, v)
        return f

    def run_one(pidx, var, full, base):
        rec = fac[pidx][1]
        ph, dur = phrase_bars() if is_bars(rec) else phrase_rhodes()
        tag = f"F{pidx + 1:02d}-{var}"
        x, got, cpu = render(tag, pidx, full, base, ph, dur, P, drv, tmp)
        xc, _, _ = render(tag + "-chord", pidx, full, base, CHORD, 2.0, P, drv, tmp)
        m = measure(x)
        m["chord"] = chord_level(xc)
        m["cpu_proxy"] = cpu
        us, on = tpc.predict({k: v for k, v in full.items() if k in PP}, PP, T)
        m["cpu_pred"], m["fx_on"] = int(us), on
        return x, m

    jobs = []
    for pidx, (name, rec) in enumerate(fac):
        key = f"F{pidx + 1:02d}"
        if only and key not in only and key != "F01":
            continue
        base = full_of(rec, {})
        jobs.append((pidx, "A", base, base, None))
        for var, spec in sorted((V.get(key) or {}).items()):
            if len(var) == 1 and var != "A":
                jobs.append((pidx, var, full_of(rec, spec.get("set", {})), base, spec.get("level")))
    results, wavs = {}, {}
    with ThreadPoolExecutor(a.j) as ex:
        out = list(ex.map(lambda j: run_one(j[0], j[1], j[2], j[3]), jobs))
    for j, (x, m) in zip(jobs, out):
        results[(j[0], j[1])], wavs[(j[0], j[1])] = m, x
    ref = results[(0, "A")]

    def offs(m):
        return m["lufs"] - ref["lufs"], m["chord"] - ref["chord"]

    # the auto LEVEL: Rhodes half way between the phrase and the chord at 0 re F01 A; bars the chord at its target
    redo = []
    for j in jobs:
        pidx, var, full, base, lv = j
        if not lv:
            continue
        bars = is_bars(fac[pidx][1])
        mode = lv.get("mode") if isinstance(lv, dict) else "chord" if bars else "mid"
        tgt = lv.get("target") if isinstance(lv, dict) and "target" in lv else CHORD_TARGET.get(pidx + 1, 0.0) if bars else 0.0
        for it in range(4):
            m = results[(pidx, var)]
            dp, dc = offs(m)
            dev = (dc if mode == "chord" else dp if mode == "phrase" else (dp + dc) / 2) - tgt
            if abs(dev) < LEVEL_DB * 0.75:
                break
            nl = int(min(127, max(0, full["P_LEVEL"] - round(dev / LEVEL_DB))))
            if nl == full["P_LEVEL"]:
                break
            full = dict(full)
            full["P_LEVEL"] = nl
            x, m = run_one(pidx, var, full, base)
            results[(pidx, var)], wavs[(pidx, var)] = m, x
        redo.append((pidx, var, full))
    fulls = {(j[0], j[1]): j[2] for j in jobs}
    for pidx, var, full in redo:
        fulls[(pidx, var)] = full

    # write: the WAVs, the matched copies, results.json, proposed_records.txt, choices.txt, INDEX.md
    res_json = {}
    for pidx, (name, rec) in enumerate(fac):
        key = f"F{pidx + 1:02d}"
        vs = sorted(v for (p, v) in results if p == pidx)
        if not vs:
            continue
        for v in vs:
            write_wav(os.path.join(a.out, key, f"{v}.wav"), wavs[(pidx, v)])
        lo = min(results[(pidx, v)]["lufs"] for v in vs)
        for v in vs:
            g = 10 ** ((lo - results[(pidx, v)]["lufs"]) / 20)
            write_wav(os.path.join(a.out, key, "matched", f"{v}.wav"), wavs[(pidx, v)] * g)
        res_json[key] = dict(name=name, variants={v: dict(results[(pidx, v)], params=fulls[(pidx, v)],
                                                         phrase_re_f01=offs(results[(pidx, v)])[0],
                                                         chord_re_f01=offs(results[(pidx, v)])[1]) for v in vs})
    json.dump(res_json, open(os.path.join(a.out, "results.json"), "w"), indent=1)
    write_docs(a.out, res_json, V, P, fac)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)


def diff_words(full, base, P):
    names = [r[0] for r in P]
    return ", ".join(f"{k[2:]} {base[k]}->{full[k]}" for k in names if full[k] != base[k]) or "(current record)"


def write_docs(out, R, V, P, fac):
    order = [r[0] for r in P]
    ids = {r[0]: r[1] for r in P}
    with open(os.path.join(out, "proposed_records.txt"), "w") as f:
        f.write("# Full parameter lists per variant: NAME[id]=value for every parameter (P_COUNT ids, the defaults\n"
                "# included). A = the current presets.c record. Generated by tools/preset_review.py.\n\n")
        for key, r in R.items():
            for v, m in r["variants"].items():
                f.write(f"{key} {v} ({r['name']}): " + " ".join(f"{k}[{ids[k]}]={m['params'][k]}" for k in order) + "\n")
            f.write("\n")
    with open(os.path.join(out, "choices.txt"), "w") as f:
        f.write("# Fill in one letter per preset (keep A for the current sound); a note after # is welcome.\n")
        for key, r in R.items():
            pk = V.get(key, {}).get("pick")
            f.write(f"{key} {r['name']}: " + "|".join(r["variants"]) + (f"    # recommended: {pk}" if pk else "") + "\n")
    try:
        rev = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = "?"
    L = ["# Factory preset review", "",
         f"Host renders (build/host/frodhes at {rev}; tools/preset_review.py), 44.1 kHz.Rhodes presets play one phrase (Dm9, G13, "
         "Cmaj9, A7alt, chords and a melody from pp v38 to ff v122); VIBES / MARIMBA one mallet phrase (soft "
         "arpeggio v34, double stops mf, a hard run v112-120, a chord f). `matched/` holds each preset's variants at "
         "its quietest variant's loudness (attenuation only), to compare tone, not level.", "",
         "Columns: **phrase** = LUFS-I of the phrase re F01 A; **chord** = hil_scan.py's presets measure (A3 C4 E4 "
         "v100, 1.1 s) re F01 A, the number the FM-1 gate judges (Rhodes 0 +- 1.5, F12 0 +- 2, VIBES +2.5, F15 -0.2, F16 "
         "+2.5, each +- 1.0); **TP** true peak dBTP; **lim** samples at the master limiter ceiling (-2.7 dBFS); "
         "**low / lowmid / mid / pres / air** the FFT power share per region (dB re total; critical bands summed); "
         "**width** side/mid power (dB); **dyn** the ff bar (6-8 s) re the pp bar (0-2 s), LU; **tail** 8.5-10.5 s "
         "re the ff bar (echo and reverb tail); "
         "**CPU** the predicted whole-ISR worst us (tests/py/test_presets_cpu.py, limit 4200).", ""]
    ref = R["F01"]["variants"]["A"]
    L.append(f"Reference F01 A: phrase {ref['lufs']:.1f} LUFS-I, chord {ref['chord']:.1f} LUFS (ungated).")
    L.append("")
    picks = {k: V.get(k, {}).get("pick", "A") for k in R}
    L += ["## Summary", "", "| preset | pick | phrase A -> pick | chord A -> pick | TP pick | CPU pick |", "| --- | --- | --- | --- | --- | --- |"]
    for key, r in R.items():
        a, p = r["variants"]["A"], r["variants"].get(picks[key], r["variants"]["A"])
        L.append(f"| {key} {r['name']} | {picks[key]} | {a['phrase_re_f01']:+.1f} -> {p['phrase_re_f01']:+.1f} | "
                 f"{a['chord_re_f01']:+.1f} -> {p['chord_re_f01']:+.1f} | {p['tp']:.1f} | {p['cpu_pred']} |")
    rh = [k for k, r in R.items() if r["variants"]["A"]["params"]["P_TYPE"] < 4]
    if rh:
        def span(var, what):
            v = [R[k]["variants"].get(var(k), R[k]["variants"]["A"])[what] for k in rh]
            return f"{min(v):+.1f} .. {max(v):+.1f} ({max(v) - min(v):.1f} LU)"
        L += ["", f"Rhodes presets ({len(rh)}), re F01 A: phrase {span(lambda k: 'A', 'phrase_re_f01')} now, "
              f"{span(lambda k: picks[k], 'phrase_re_f01')} with the picks; chord {span(lambda k: 'A', 'chord_re_f01')} now, "
              f"{span(lambda k: picks[k], 'chord_re_f01')} with the picks."]
    L.append("")
    for key, r in R.items():
        spec = V.get(key, {})
        L.append(f"## {key} {r['name']}")
        if spec.get("note"):
            L += ["", spec["note"]]
        L += ["", "| | change (vs the current record) | phrase | chord | TP | lim | low | lowmid | mid | pres | air | width | dyn | tail | CPU |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        base = r["variants"]["A"]["params"]
        for v, m in r["variants"].items():
            desc = "current" if v == "A" else spec.get(v, {}).get("desc", "")
            dw = "" if v == "A" else f" ({diff_words(m['params'], base, P)})"
            rg = list(m["regions"].values())
            L.append(f"| {v} | {desc}{dw} | {m['phrase_re_f01']:+.1f} | {m['chord_re_f01']:+.1f} | {m['tp']:.1f} | "
                     f"{m['at_limit']} | " + " | ".join(f"{x:.1f}" for x in rg) + f" | {m['width']:.0f} | {m['dyn']:+.1f} | "
                     f"{m['tail']:+.1f} | {m['cpu_pred']} |")
        if spec.get("pick"):
            L += ["", f"**Recommendation: {spec['pick']}.** {spec.get('why', '')}"]
        L.append("")
    open(os.path.join(out, "INDEX.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
