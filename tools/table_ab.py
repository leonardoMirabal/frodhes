#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Is a new firmware/gen/bars_tables.h audible? Two headers, A (the committed one) and B (a candidate), compared as
tables and as sound on the host build, against stated audibility thresholds.

  tools/table_ab.py [A] [B]           A defaults to firmware/gen/bars_tables.h; B defaults to A
                    [--quick] [--jobs N]
  tools/table_ab.py perturb SRC OUT TABLE:WORD:DELTA [...]   a copy of SRC with flat words changed (tests)

1. Tables: every `static const` array (and every #define) parsed to its flat list of integers; per table the words
   changed, max |B - A| in LSB, max |B - A| / |A|, and which keys (BAR_KEYS by key and field, e.g. C4 s4.b; tables
   indexed by key; tables indexed by band list the keys of that band).
2. Sound: two host binaries (tests/host.c, the Makefile's flags) differ only in the header (an include directory
   with that bars_tables.h, searched before firmware/gen). The same VIBES takes on both, same host = sample-exact
   timing: F3..F6 at v40 / v100 / v127, motor on and off (MALLET 64); F3..F6 v100 at MALLET 0 and 127; every key
   21..108 at v100; a pedalled chord; the Gate R VIBES renders (fx/v_*, tests/scripts with the bar engine). Per take:
   LUFS, peak, 1/3-octave bands, the partials (peaks of A's spectrum: level re the first, pitch), T60 of the held
   decay and the residual B - A (sample-aligned) re A and in dBFS (full band and 8-20 kHz).
3. A verdict table on stdout and build/table_ab/report.md (thresholds and why; the FM-1 follow-up plan).
4. The Rhodes is not touched: every Rhodes-only Gate R render (calib/*, fx/* without v_, the default run) plus an
   ST77 key sweep must be byte-identical between the two builds. A's Gate R renders are also checked against
   tests/baselines/renders.sha256 (A is the committed sound).
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402
import gate_r  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "table_ab"
GEN = ROOT / "firmware" / "gen"
FS = 44100
SLOTS = ["V1", "TUBE", "V2", "V3", "V4", "T1", "T2", "X"]
MODE_F = ["sh", "ch", "th", "d", "drel", "cr", "b"]          # rh_mode_t (rhodes.c)
KEY_F = [f"s{s}.{f}" for s in range(8) for f in MODE_F] + ["k_bt", "k_tb", "wt", "gain", "kill", "life", "band",
                                                            "flags"]   # bar_key_t (bars.h)
NOTE_N = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Audibility thresholds (the why: report.md, THRESHOLDS_WHY)
TH = dict(res_rel=-90.0,      # dB re the take's RMS
          res_hf=-100.0,      # dBFS, residual 8-20 kHz
          res_abs=-90.0,      # dBFS, residual full band
          lufs=0.01, peak=0.01, band=0.05, plev=0.05, cents=0.1, t60=0.5)
THRESHOLDS_WHY = """\
| measure | limit | why |
| --- | --- | --- |
| residual B - A re the take | <= -90 dB | The residual is the whole difference, sample by sample. A difference 90 dB under the note changes its level by at most 20 log10(1 + 10^(-90/20)) = 0.0003 dB and sits 30+ dB under masking thresholds for a sound in the same bands (simultaneous masking leaves a component inaudible 20-40 dB under the masker). |
| residual, full band | <= -90 dBFS | One 16-bit LSB (the output's own grain) is -90.3 dBFS; the jack's floor is far above (SNR 74 dB re a loud chord, private/testplan). |
| residual 8-20 kHz | <= -100 dBFS | The band where the jack hiss is measured (AGENTS.md: the 8-20 kHz floor); the codec's hiss there is well above -100 dBFS, so a residual under it is buried in the device's own noise. |
| LUFS | <= 0.01 dB | The level JND is 0.2-0.5 dB; the device A/B limit (tools/rh_devcmp.py) is 0.1 dB; the host has no noise, so 10x tighter. |
| peak | <= 0.01 dB | As LUFS: the limiter and clip margins move with the peak. |
| 1/3-octave bands | <= 0.05 dB | Timbre JND per band is ~0.5-1 dB; rh_devcmp's device limit 0.3 dB; 6x tighter. Bands within 60 dB of the loudest. |
| partial level re the first | <= 0.05 dB | The vibraphone's colour is its mode ratios and their levels; 10x under any spectral-shape JND. Partials within 50 dB of the first. |
| partial pitch | <= 0.1 cent | Pitch JND ~3-5 cents, beating between two notes matters at ~0.5 cent (rh_devcmp 0.5 cent). |
| T60 | <= 0.5 % | The decay-time JND is ~5-10 %; rh_devcmp's device limit 1 %. |
"""


# ---------------------------------------------------------------- the header
def _mask_comments(s):
    return re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " * len(m.group(0)), s, flags=re.S)


NUM = re.compile(r"(?<![\w.])-?(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*(?![\w.])")
ARR = re.compile(r"static\s+const\s+(\w+)\s+(\w+)((?:\s*\[[^\]]*\])+)\s*=\s*\{", re.S)


def _int(t):
    return int(t.rstrip("uUlL"), 0)


def parse(path):
    """{name: dict(type, dims, vals, spans)}, {define: value text}; spans: (start, end) of each number in the file"""
    src = Path(path).read_text()
    m = _mask_comments(src)
    defs = dict(re.findall(r"^#define\s+(\w+)\s+([^\s/]+)", m, re.M))
    tabs = {}
    for a in ARR.finditer(m):
        i, depth = a.end(), 1
        while depth:                                       # the matching closing brace
            depth += {"{": 1, "}": -1}.get(m[i], 0)
            i += 1
        body0 = a.end()
        nums = list(NUM.finditer(m, body0, i - 1))
        tabs[a.group(2)] = dict(type=a.group(1), dims=re.findall(r"\[([^\]]*)\]", a.group(3)),
                                vals=[_int(n.group(0)) for n in nums], spans=[n.span() for n in nums])
    return tabs, defs


def _eval_dim(expr, defs):
    e = expr
    for _ in range(4):
        e = re.sub(r"[A-Za-z_]\w*", lambda m: defs.get(m.group(0), m.group(0)), e)
    try:
        return int(eval(re.sub(r"[uU]\b", "", e), {}, {}))
    except Exception:
        return None


def note_name(n):
    return f"{NOTE_N[n % 12]}{n // 12 - 1}"


def diff_tables(pa, pb):
    """per table: words changed, max |diff| LSB, max relative, and the changes with their labels"""
    ta, da = parse(pa)
    tb, db_ = parse(pb)
    defs = dict(parse(GEN / "rhodes_tables.h")[1])
    defs.update(da)
    lo = int(da.get("BAR_KEY_LO", 21))
    nkey = _eval_dim("BAR_NKEY", defs) or 88
    ntype = _eval_dim("BAR_NTYPE", defs) or 1             # MARIMBA: BAR_KEYS[BAR_NTYPE][BAR_NKEY] (type 0 VIBES)
    keys = ta.get("BAR_KEYS")
    per_key = len(keys["vals"]) // (nkey * ntype) if keys else 0
    band_keys = {}
    if keys and per_key == len(KEY_F):
        for k in range(nkey):
            band_keys.setdefault(keys["vals"][k * per_key + KEY_F.index("band")], []).append(lo + k)
    res = []
    for name in sorted(set(ta) | set(tb)):
        A, B = ta.get(name), tb.get(name)
        if A is None or B is None or len(A["vals"]) != len(B["vals"]):
            res.append(dict(name=name, n=len((A or B)["vals"]), changed=-1, note="missing or a different size"))
            continue
        a, b = np.array(A["vals"], dtype=np.int64), np.array(B["vals"], dtype=np.int64)
        d = b - a
        idx = np.nonzero(d)[0]
        dims = [_eval_dim(x, defs) for x in A["dims"]]
        row = dict(name=name, n=len(a), changed=len(idx), maxlsb=int(np.abs(d).max()) if len(idx) else 0,
                   maxrel=float(np.max(np.abs(d[idx]) / np.maximum(np.abs(a[idx]), 1))) if len(idx) else 0.0,
                   items=[])
        for i in idx:
            lab, ks = _label(name, int(i), dims, per_key, lo, band_keys, nkey)
            row["items"].append(dict(i=int(i), a=int(a[i]), b=int(b[i]), d=int(d[i]), label=lab, keys=ks))
        row["keys"] = sorted({k for it in row["items"] for k in it["keys"]})
        res.append(row)
    dd = [(k, da.get(k), db_.get(k)) for k in sorted(set(da) | set(db_)) if da.get(k) != db_.get(k)]
    return res, dd


def _label(name, i, dims, per_key, lo, band_keys, nkey=88):
    if name == "BAR_KEYS" and per_key:
        t, r = divmod(i, nkey * per_key)                  # the bar type (0 VIBES: no prefix, as before)
        k, w = divmod(r, per_key)
        f = KEY_F[w] if per_key == len(KEY_F) else f"w{w}"
        if f.startswith("s"):
            f = f"{f} ({SLOTS[int(f[1])]})"
        return f"{'T%d ' % t if t else ''}{note_name(lo + k)} {f}", [lo + k]
    if dims and None not in dims and int(np.prod(dims)) > 0:
        sub, r = [], i
        for n in reversed(dims):
            r, q = divmod(r, n)
            sub.append(q)
        sub = sub[::-1]
        lab = name + "".join(f"[{s}]" for s in sub)
        if dims[0] == 88:
            return lab, [lo + sub[0]]
        if len(dims) > 1 and dims[1] == 88:               # per type: [BAR_NTYPE][BAR_NKEY]...
            return lab, [lo + sub[1]]
        if name in ("BAR_PULSE", "BAR_POS", "BAR_POSG", "BAR_TUBEG"):     # [BAR_NBAND]...: the band's keys
            return lab + " (band)", band_keys.get(sub[0], [])
        return lab, []
    return f"{name}#{i}", []


def perturb(src, out, specs):
    """write a copy of src with TABLE:WORD:DELTA applied (WORD: the flat index)"""
    text = Path(src).read_text()
    tabs, _ = parse(src)
    edits = []
    for s in specs:
        name, w, dl = s.split(":")
        t = tabs[name]
        a, b = t["spans"][int(w)]
        edits.append((a, b, str(t["vals"][int(w)] + int(dl))))
    for a, b, v in sorted(edits, reverse=True):
        text = text[:a] + v + text[b:]
    Path(out).write_text(text)


# ---------------------------------------------------------------- builds and renders
def build(tag, header):
    d = OUT / tag
    inc = d / "gen"
    inc.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(header, inc / "bars_tables.h")
    binary = d / "frodhes"
    subprocess.run([os.environ.get("CC", "cc"), "-I" + str(inc)] + gate_r.CFLAGS +
                   ["-o", str(binary), "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True)
    return binary


def render(binary, outdir, name, script_text):
    """the WAV (int16, frames x 2) and its sha256"""
    out = outdir / name
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [str(binary), str(out)]
    if script_text is not None:
        p = out.with_suffix(".txt")
        p.write_text(script_text)
        args.append(str(p))
    e = dict(os.environ)
    e.pop("FRODHES_FLASH", None)
    subprocess.run(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=e)
    w = out.with_suffix(".wav")
    if not w.exists():
        return None, "MISSING"
    raw = w.read_bytes()
    with wave.open(str(w)) as f:
        x = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").reshape(-1, f.getnchannels())
    return x, hashlib.sha256(raw).hexdigest()


NOTE_GAP, HOLD, T_ON = 2500, 1500, 400


def note_take(key, vel, mallet=64, motor=True, typ=4):
    """one take from boot (its own host run: nothing carries over from another take, the reverb tail included)"""
    ev = [f"50 midi 0xB0 {bm.type_ccnum(typ)} {bm.type_cc(typ)}"]
    if typ >= 4:
        ev += [f"100 midi 0xB0 29 {mallet}", f"110 midi 0xB0 47 {127 if motor else 0}"]
    ev += [f"{T_ON} midi 0x90 {key} {vel}", f"{T_ON + HOLD} midi 0x80 {key} 0", f"{T_ON + NOTE_GAP} end"]
    return "\n".join(ev) + "\n"


def chord_take():
    ns = [53, 57, 60, 64, 67, 72, 76, 79, 84]
    ev = [f"50 midi 0xB0 79 {bm.type_cc(4)}", "300 midi 0xB0 64 127"]
    ev += [f"{T_ON + 15 * i} midi 0x90 {n} 100" for i, n in enumerate(ns)]
    ev += [f"{T_ON + HOLD} midi 0x80 {n} 0" for n in ns]
    ev += [f"{T_ON + 4000} midi 0xB0 64 0", f"{T_ON + 5200} end"]
    return "\n".join(ev) + "\n"


def vibes_jobs(quick=False):
    """name -> (script, group, take, key or None); key None: measured over the whole file"""
    f36 = list(range(53, 90, 4 if quick else 1))
    j = {}

    def add(group, key, vel, **kw):
        j[f"{group}/{note_name(key)}_v{vel}"] = (note_take(key, vel, **kw), group, f"{note_name(key)} v{vel}", key)
    for mot in (1, 0):
        for k in f36:
            for v in (40, 100, 127):
                add(f"vibes/m64_mot{mot}", k, v, motor=bool(mot))
    for mal in (0, 127):
        for k in f36:
            add(f"vibes/m{mal}_mot1", k, 100, mallet=mal)
    for k in range(21, 109, 6 if quick else 1):
        add("vibes/all_v100", k, 100)
    j["vibes/chord"] = (chord_take(), "vibes/chord", "chord F3..C6 pedal v100", 60)
    for name, text in gate_r.fx_scripts().items():
        if name.startswith("fx/v_"):
            j["gate/" + name] = (text, "gate/" + name, name, None)
    for p in sorted((ROOT / "tests" / "scripts").glob("*.txt")):
        if bar_script(p.read_text()):
            j["gate/scripts/" + p.stem] = (p.read_text(), "gate/scripts/" + p.stem, p.stem, None)
    return j


def bar_script(text):
    """a script that plays the bar engine: CC 79 or a TYPE CC 80 in VIBES's band, or a VIBES preset / page"""
    return bool(re.search(r"0xB0 79 |VIBES|vibes|MOTOR", text) or
                any(int(v) >= 103 for v in re.findall(r"0xB0 80 (\d+)", text)))


def rhodes_jobs(quick=False):
    """Rhodes-only material: never selects a bar type"""
    j = {"gate/host/frodhes": None}
    for name, text in list(gate_r.fx_scripts().items()) + list(gate_r.calib_scripts().items()):
        if not name.startswith("fx/v_"):
            j["gate/" + name] = text
    for k in range(53, 90, 4 if quick else 1):
        for v in (40, 127):
            j[f"rhodes/st77/{note_name(k)}_v{v}"] = note_take(k, v, typ=0)
    return j


def other_gate_jobs():
    """tests/scripts that are neither: reported, not judged"""
    return {"gate/scripts/" + p.stem: p.read_text() for p in sorted((ROOT / "tests" / "scripts").glob("*.txt"))
            if not bar_script(p.read_text())}


# ---------------------------------------------------------------- measures
def _db(x, ref=1.0):
    return 20 * np.log10(x / ref) if x > 0 else float("-inf")


def bands(x):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    out = []
    for i in range(-17, 13):                                     # 31.5 Hz .. 16 kHz
        fc = 1000 * 2 ** (i / 3)
        m = (fr >= fc * 2 ** (-1 / 6)) & (fr < fc * 2 ** (1 / 6))
        out.append(10 * np.log10(sp[m].sum() + 1e-30))
    return np.array(out)


def partials(a, b, key):
    """peaks of A's spectrum (within 50 dB of the first: the one nearest the key's f0), and B at A's peaks: level re
    the first (dB) and pitch (cents) each"""
    from scipy.signal import find_peaks
    n = len(a)
    win = np.hanning(n)
    sa = np.abs(np.fft.rfft(a * win, 8 * n)) + 1e-20
    sb = np.abs(np.fft.rfft(b * win, 8 * n)) + 1e-20
    df = FS / (8 * n)
    f0 = 440 * 2 ** ((key - 69) / 12)
    la = 20 * np.log10(sa)
    pk, _ = find_peaks(la, prominence=12, distance=max(1, int(0.25 * f0 / df)))
    pk = pk[(pk * df > 20) & (pk * df < 18000)]
    if not len(pk):
        return []
    first = pk[np.argmin(np.abs(pk * df - f0))]
    pk = sorted(p for p in pk if la[p] > la[first] - 50)
    pk = sorted(sorted(pk, key=lambda p: -la[p])[:8])

    def interp(s, i):
        al, be, ga = np.log(s[i - 1:i + 2])
        p = 0.5 * (al - ga) / (al - 2 * be + ga)
        return (i + p) * df, 20 * np.log10(s[i])

    fa1, la1 = interp(sa, first)
    fb1, lb1 = interp(sb, first)
    out = []
    for p in pk:
        fa, lva = interp(sa, p)
        fb, lvb = interp(sb, p)
        out.append(dict(f=fa, plev=(lvb - lb1) - (lva - la1), cents=1200 * np.log2(fb / fa)))
    return out


def t60(x):
    n = int(0.002 * FS)
    m = len(x) // n
    e = 20 * np.log10(np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1) + 1e-20))
    a, b = int(0.1 / 0.002), m
    k = np.polyfit(np.arange(a, b) * 0.002, e[a:b], 1)[0]
    return -60 / k if k < 0 else float("inf")


def compare_take(xa, xb, key=None):
    """xa, xb: int16 frames x 2; key: a note take (its note-on at T_ON; the residual over the whole file, the other
    measures over the held part), None: everything over the whole file"""
    i0, i1, h0, h1 = 0, len(xa), 0, len(xa)
    if key is not None:
        h0, h1 = int(T_ON * FS / 1000) + int(0.01 * FS), int((T_ON + HOLD) * FS / 1000)   # the held part
    A = xa[i0:i1].astype(float)
    B = xb[i0:i1].astype(float)
    d = B - A
    ra = np.sqrt(np.mean(A ** 2))
    rd = np.sqrt(np.mean(d ** 2))
    sp = np.abs(np.fft.rfft(d, axis=0)) ** 2
    fr = np.fft.rfftfreq(len(d), 1 / FS)
    hf = np.sqrt(2 * sp[(fr >= 8000) & (fr <= 20000)].sum() / len(d) ** 2 / d.shape[1])   # Parseval, per channel
    r = dict(n_diff=int(np.count_nonzero(d)), max_lsb=int(np.abs(d).max()) if d.size else 0,
             res_rel=_db(rd, ra), res_abs=_db(rd, 32768), res_hf=_db(hf, 32768),
             lufs=bm.lufs(B) - bm.lufs(A),
             peak=_db(np.abs(B).max(), 1) - _db(np.abs(A).max(), 1) if np.abs(A).max() > 0 else 0.0)
    ma, mb = bm.mono(xa[h0:h1].astype(float)), bm.mono(xb[h0:h1].astype(float))
    ba, bb = bands(ma), bands(mb)
    keep = ba > ba.max() - 60
    r["band"] = float(np.max(np.abs(bb - ba)[keep])) if keep.any() else 0.0
    if key is not None:
        P = partials(ma, mb, key)
        r["plev"] = max((abs(p["plev"]) for p in P), default=0.0)
        r["cents"] = max((abs(p["cents"]) for p in P), default=0.0)
        r["npart"] = len(P)
        ta, tb = t60(ma), t60(mb)
        r["t60"] = 100 * abs(tb - ta) / ta if np.isfinite(ta) and ta > 0 else 0.0
    return r


def passes(r):
    bad = []
    if r["res_rel"] > TH["res_rel"]:
        bad.append("res_rel")
    if r["res_abs"] > TH["res_abs"]:
        bad.append("res_abs")
    if r["res_hf"] > TH["res_hf"]:
        bad.append("res_hf")
    for k in ("lufs", "peak", "band", "plev", "cents", "t60"):
        if k in r and abs(r[k]) > TH[k]:
            bad.append(k)
    return bad


# ---------------------------------------------------------------- report
def fmt_db(v):
    return "-inf" if v == float("-inf") else f"{v:.1f}"


def main_ab(pa, pb, quick=False, jobs=None):
    OUT.mkdir(parents=True, exist_ok=True)
    tabs, ddefs = diff_tables(pa, pb)
    lines = []
    P = lines.append
    P("# bars_tables.h A/B: is the new table audible?\n")
    P(f"A: `{pa}`  \nB: `{pb}`  \n{'(quick: a subset of keys)' if quick else ''}\n")
    P("## 1. The tables\n")
    P("| table | words | changed | max abs LSB | max rel | keys |\n| --- | --- | --- | --- | --- | --- |")
    for t in tabs:
        if t["changed"] < 0:
            P(f"| {t['name']} | {t['n']} | {t['note']} | | | |")
            continue
        ks = ", ".join(note_name(k) for k in t["keys"][:12]) + (" ..." if len(t["keys"]) > 12 else "")
        P(f"| {t['name']} | {t['n']} | {t['changed']} | {t['maxlsb']} | {t['maxrel']:.2e} | {ks} |")
    for k, a, b in ddefs:
        P(f"\n#define {k}: A {a}, B {b}")
    big = sorted((it for t in tabs if t["changed"] > 0 for it in t["items"]), key=lambda it: -abs(it["d"]))
    if big:
        P("\nThe largest changes:\n\n| word | A | B | B - A | rel |\n| --- | --- | --- | --- | --- |")
        for it in big[:15]:
            P(f"| {it['label']} | {it['a']} | {it['b']} | {it['d']:+d} | {abs(it['d']) / max(abs(it['a']), 1):.2e} |")
    nchg = sum(t["changed"] for t in tabs if t["changed"] > 0)

    print(f"tables: {nchg} words changed in {sum(1 for t in tabs if t['changed'] > 0)} tables"
          + (f", largest {big[0]['label']} {big[0]['d']:+d} LSB" if big else ""))
    ba, bbin = build("A", pa), build("B", pb)
    vj, rj, oj = vibes_jobs(quick), rhodes_jobs(quick), other_gate_jobs()
    names = list(vj) + list(rj) + list(oj)
    text = {**{n: v[0] for n, v in vj.items()}, **rj, **oj}

    def job(arg):
        tag, name = arg
        return (tag, name), render(ba if tag == "A" else bbin, OUT / tag / "r", name, text[name])
    with ThreadPoolExecutor(jobs or os.cpu_count() or 4) as ex:
        got = dict(ex.map(job, [(t, n) for n in names for t in ("A", "B")]))

    # 4. the Rhodes and the rest: bytes
    rh_bad = [n for n in rj if got[("A", n)][1] != got[("B", n)][1] or got[("A", n)][1] == "MISSING"]
    oth = {n: got[("A", n)][1] == got[("B", n)][1] for n in oj}
    want = dict(reversed(ln.split()) for ln in gate_r.BASE.read_text().splitlines() if ln.strip())
    base_bad = [n for n in names if n.startswith("gate/") and n[5:] in want and got[("A", n)][1] != want[n[5:]]]
    nbase = sum(1 for n in names if n.startswith("gate/") and n[5:] in want)

    # 2. VIBES takes
    rows = []
    for name, (_, group, take, key) in vj.items():
        xa, xb = got[("A", name)][0], got[("B", name)][0]
        if xa is None or xb is None or xa.shape != xb.shape:
            rows.append(dict(take=name, group=group, bad=["render"], r={}))
            continue
        r = compare_take(xa, xb, key)
        r["identical"] = got[("A", name)][1] == got[("B", name)][1]
        rows.append(dict(take=f"{group.split('/', 1)[1]}: {take}", group=group, r=r, bad=passes(r)))
    ok = all(not x["bad"] for x in rows)

    P("\n## 2. The VIBES renders (B - A, same host: sample-exact)\n")
    P("Worst per group (|Δ|; residual: the loudest one):\n")
    P("| group | takes | identical | samples differ | max LSB | res re A dB | res dBFS | res 8-20k dBFS | LUFS dB "
      "| peak dB | band dB | partial dB | cents | T60 % | verdict |")
    P("| --- " * 15 + "|")
    con = []
    for g in dict.fromkeys(v[1] for v in vj.values()):
        rs = [x for x in rows if x["group"] == g and x["r"]]
        if not rs:
            P(f"| {g} | 0 | render failed |" + " |" * 12)
            continue

        def w(k, f=max, absval=True):
            v = [abs(x["r"][k]) if absval else x["r"][k] for x in rs if k in x["r"]]
            return f(v) if v else None
        nb = sum(1 for x in rs if x["bad"])
        cells = [g, len(rs), "yes" if all(x["r"]["identical"] for x in rs) else "no",
                 sum(x["r"]["n_diff"] for x in rs), w("max_lsb"), fmt_db(w("res_rel", absval=False)),
                 fmt_db(w("res_abs", absval=False)), fmt_db(w("res_hf", absval=False))]
        for k in ("lufs", "peak", "band", "plev", "cents", "t60"):
            v = w(k)
            cells.append("-" if v is None else f"{v:.4f}")
        cells.append("PASS" if not nb else f"FAIL {nb}")
        P("| " + " | ".join(str(c) for c in cells) + " |")
        con.append(cells)
    worst = sorted((x for x in rows if x["r"]), key=lambda x: -x["r"]["res_rel"])[:12]
    if worst and worst[0]["r"]["res_rel"] > float("-inf"):
        P("\nThe largest residuals:\n\n| take | res re A dB | res dBFS | 8-20k dBFS | max LSB | LUFS | partial dB |"
          " cents | T60 % | fails |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for x in worst:
            r = x["r"]
            if r["res_rel"] == float("-inf"):
                break
            P(f"| {x['take']} | {fmt_db(r['res_rel'])} | {fmt_db(r['res_abs'])} | {fmt_db(r['res_hf'])} | "
              f"{r['max_lsb']} | {r['lufs']:+.5f} | {r.get('plev', 0):.4f} | {r.get('cents', 0):.4f} | "
              f"{r.get('t60', 0):.3f} | {', '.join(x['bad']) or '-'} |")
    P("\n## 3. Thresholds (inaudible if every take is under all of them)\n")
    P(THRESHOLDS_WHY)
    P("\n## 4. The Rhodes and the other renders\n")
    P(f"- Rhodes-only renders (Gate R calib/*, fx/* without v_, the default run, an ST77 F3..F6 sweep at v40 / v127): "
      f"{len(rj) - len(rh_bad)} of {len(rj)} byte-identical between A and B"
      + (f"; DIFFER: {', '.join(rh_bad)}" if rh_bad else "") + ".")
    P(f"- The other tests/scripts (no bar engine): {sum(oth.values())} of {len(oth)} byte-identical"
      + (f"; differ: {', '.join(n for n, s in oth.items() if not s)}" if not all(oth.values()) else "") + ".")
    P(f"- A's Gate R renders against tests/baselines/renders.sha256: {nbase - len(base_bad)} of {nbase} equal"
      + (f" (differ: {', '.join(base_bad[:6])}{' ...' if len(base_bad) > 6 else ''}: A is not the committed sound,"
         " or the baseline is stale)" if base_bad else " (A is the committed sound)") + ".")
    verdict = ("INAUDIBLE: every VIBES take under every threshold" if ok else
               f"NOT PROVEN INAUDIBLE: {sum(1 for x in rows if x['bad'])} takes over a threshold")
    if rh_bad:
        verdict += "; the RHODES CHANGED"
    P(f"\n## Verdict\n\n**{verdict}.** {nchg} table words changed.\n")
    P(FM1_PLAN)
    (OUT / "report.md").write_text("\n".join(lines) + "\n")

    hdr = ["group", "takes", "ident", "ndiff", "maxLSB", "resA", "resFS", "res8k", "LUFS", "peak", "band", "part",
           "cents", "T60%", "verdict"]
    wd = [max(len(str(c[i])) for c in con + [hdr]) for i in range(len(hdr))]
    for c in [hdr] + con:
        print("  ".join(str(v).ljust(wd[i]) for i, v in enumerate(c)))
    print(f"Rhodes: {len(rj) - len(rh_bad)} of {len(rj)} renders byte-identical A vs B"
          + (f" (DIFFER: {', '.join(rh_bad)})" if rh_bad else ""))
    print(f"other scripts: {sum(oth.values())} of {len(oth)} identical; A vs Gate R baseline: "
          f"{nbase - len(base_bad)} of {nbase} equal")
    print(f"VERDICT: {verdict}\nreport: {(OUT / 'report.md').relative_to(ROOT)}")
    return 0 if ok and not rh_bad else 1


FM1_PLAN = """## 5. The FM-1 follow-up (planned, not run)

The host proves the arithmetic; the device must confirm it through the jack (the user's ears hear the jack).
1. Build A (the committed header) and B (the candidate) from clean worktrees (`build/wt`, `./build.sh`); keep both
   .fwsc files.
2. Install A (`echo y | python3 tools/fm1_install.py ...`, `--info`), MASTER fully up, no headphones, SETUP USB VOL
   FIXED. Record the VIBES take set twice (A1, A2: the floor) over USB (44.1 kHz) and the M6 input 5 at once
   (`tools/hil_jack.py Jack.both`), the `tools/rh_devcmp.py` way, with developer CC 10 before each take (it resets
   the LFOs / motor phase, lines and noise):
   - VIBES (CC 79 0): keys F3 A3 C#4 F4 A4 C#5 F5 A5 C#6 F6 at v40 / v100 / v127 (2.5 s each, 1.5 s held),
     motor on (default) and off (CC 47 0);
   - MALLET (CC 29) 0 and 127 at v100 on the same keys; the pedalled 9-note chord; a held C4 to its tail;
   - the keys this report lists as changed (every one of them, v100, motor off), plus the largest-change key at
     all three velocities.
3. Install B, record the same set (B1). Re-install A afterwards and record A3 if the session was long (drift check).
4. Compare: USB at 44.1 kHz sample-aligned (`rh_devcmp.exact`): the number of differing samples and the residual,
   expected to match this host report take by take. The jack decides: per take LUFS, 1/3-octave bands, partials,
   T60 and the aligned residual B1 - A1 must lie inside the A1 - A2 floor (the device's own repeatability) and under
   the device limits (level 0.1 dB, bands 0.3 dB, T60 1 %, pitch 0.5 cent, 0 clicks).
   `rh_devcmp.py` has only the Rhodes TYPEs today: its `takes()` needs the VIBES set above (same measures).
5. Listening: the largest-change takes A1 / B1 for the user, blind (ABX); the verdict "inaudible" is the user's.
"""


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "perturb":
        ap = argparse.ArgumentParser(prog="table_ab.py perturb")
        ap.add_argument("src")
        ap.add_argument("out")
        ap.add_argument("spec", nargs="+", help="TABLE:WORD:DELTA (WORD: the flat index of the integer)")
        a = ap.parse_args(sys.argv[2:])
        perturb(a.src, a.out, a.spec)
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", nargs="?", default=str(GEN / "bars_tables.h"))
    ap.add_argument("b", nargs="?")
    ap.add_argument("--quick", action="store_true", help="every 4th key (6th over the full range)")
    ap.add_argument("--jobs", type=int)
    a = ap.parse_args()
    return main_ab(os.path.abspath(a.a), os.path.abspath(a.b or a.a), a.quick, a.jobs)


if __name__ == "__main__":
    sys.exit(main())
