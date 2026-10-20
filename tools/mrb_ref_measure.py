#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Measure reference marimba recordings - for the MARIMBA engine's calibration targets ONLY (MARIMBA PLAN D10, §2.6).

The recordings are read where they are and never copied, played back, fitted or embedded: only NUMBERS come out
(private/marimba/ref_measure.md, plots in build/mrb_ref/). The same `measure()` runs on the golden model
(tools/bars_model.py on docs/model/data/marimba_spec.json), on the host build (build/host/frodhes, MARIMBA = CC 79 127)
and, through tools/vib_sampleback.py --engine marimba, on the FM-1's USB audio: every number in a comparison comes
from one procedure.

Sources
  --pack DIR   an Ableton pack (e.g. ".../Factory Packs/Orchestral Mallets"): the note boundaries, root keys and
               velocity layers come from its Sampler presets (Sounds/**/*.adg: gzipped XML, MultiSamplePart =
               RootKey, KeyRange, VelocityRange, SampleStart / SampleEnd in frames, the file name); each zone is read
               in place from the combined AIFF (a seek and a read of its frames, in memory). An AIFF whose
               compression is not plain PCM (Ableton's protected pack audio, compression type 'able') is NOT read:
               the tool says so and measures nothing from it (no decoding of protected content)
  FILES/DIRS   one-shot WAV / AIFF files named with their note (as vib_ref_measure.py)
  (always)     the golden model and the host render at v100, keys 36..96, for the comparison

Per note (measure()):
  f0, cents vs ET   a 2^18-point FFT of the ring (from 20 ms, up to 1 s), parabolic interpolation (~0.1 c)
  V2/V1, V3/V1      the peaks near 4 f0 (3.8-4.2) and 9.0-10.6 f0 from 3 ms over max(0.1 s, 40 periods) (flat-start
                    window), interpolated; '-' when no real peak (10 dB over the band's median, not on its edge)
  v2_db, v3_db      the partial's energy re V1's over 5-300 ms (Gate VD-M's window, marimba_va.partial_db) and over
                    5-50 ms (R1b's window: the onset)
  T60s              t60_v1_30db: the f0 band's 30 dB line fit x2 (marimba_va.t60_fit, the gates' measure, its +-3 %
                    brick-wall band); t60_v1: the same fit on a band of +-clip(16 Hz / f0, 3 %, 15 %): the +-3 % band's
                    1/t onset leakage reads a pure exponential 26 % long at C2, 8 % at C3, 6 % at C4, 0-1 % from C5
                    (tested on synthetic decays), the wider one within 1.2 %; V2 / V3: 20 dB fits x3 (the +-3 % band);
                    '-' when the band does not fall the whole fit before the end
  attack_ms         10 % -> 90 % of the 0.25 ms peak envelope up to its peak in the first 15 ms (the strike)
  f0_peak_ms        bartube.f0_peak_ms (the bloom, exactly as Gate VA-M4 reads it)
  beat              the f0 band's envelope, its decay removed: the strongest AM at 0.25..8 Hz (the strongly coupled
                    bar-tube pair's split), rate and depth, for keys <= 62; 'none' without a clear peak
  lufs              K-weighted over 0..0.4 s (calib lufs_t; for the level curve's shape: the recordings' gain is
                    unknown, so curves are compared re C4)
  centroid          the attack centroid over 0..50 ms (marimba_va.ATT, flat-start window) and over 0..300 ms

    python3 tools/mrb_ref_measure.py --pack ".../Orchestral Mallets" [--out-md PATH] [--plots DIR] [--keys 36-96]
    python3 tools/mrb_ref_measure.py --no-ref          (the golden model and the host only)
"""
import argparse
import glob
import gzip
import json
import math
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vib_ref_measure as vrm  # noqa: E402  (the VIBES reference procedure: envelopes, its T60 fit, note names)

FS = 44100                                            # every measure at the host's rate (recordings resampled in memory)
OUT_MD = os.path.join(ROOT, "private", "marimba", "ref_measure.md")
OUT_PNG = os.path.join(ROOT, "build", "mrb_ref")
DATA = os.path.join(ROOT, "docs", "model", "data", "marimba.json")
SPEC = os.path.join(ROOT, "docs", "model", "data", "marimba_spec.json")
PCM_COMP = (b"NONE", b"twos", b"sowt", b"fl32", b"FL32", b"in24", b"in32", b"raw ")
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def et(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# ------------------------------------------------------------------------------------------------ the pack's zones
def pack_zones(pack, match="Marimba"):
    """every MultiSamplePart of the pack's Sampler / Simpler presets whose name contains match: dict(preset, file,
    root, keys (lo, hi), vel (lo, hi), vel_full (crossfade lo, hi), start, end, sr, detune_c, volume_db), one per
    distinct (file, start, end, velocity layer)"""
    out, seen = [], set()
    for p in sorted(glob.glob(os.path.join(pack, "Sounds", "**", "*.ad[gv]"), recursive=True)):
        if match.lower() not in os.path.basename(p).lower():
            continue
        x = gzip.open(p).read().decode("utf-8", "replace")
        for part in re.findall(r"<MultiSamplePart>(.*?)</MultiSamplePart>", x, re.S):
            def rng(tag):
                m = re.search(r"<%s>\s*<Min Value=\"(\d+)\" />\s*<Max Value=\"(\d+)\" />\s*<CrossfadeMin Value=\"(\d+)\""
                              r" />\s*<CrossfadeMax Value=\"(\d+)\" />" % tag, part, re.S)
                return tuple(int(v) for v in m.groups())

            def val(tag):
                return re.search(r"<%s Value=\"([^\"]+)\"" % tag, part).group(1)
            fn = re.search(r"<SampleRef>.*?<Name Value=\"([^\"]+)\"", part, re.S).group(1)
            k, v = rng("KeyRange"), rng("VelocityRange")
            z = dict(preset=os.path.basename(p), file=fn, root=int(val("RootKey")), keys=k[:2], vel=v[:2],
                     vel_full=v[2:], start=int(val("SampleStart")), end=int(val("SampleEnd")),
                     sr=int(val("DefaultSampleRate")), detune_c=int(val("Detune")),
                     volume_db=20 * math.log10(max(float(val("Volume")), 1e-9)))
            key = (z["file"], z["start"], z["end"], z["vel"])
            if key not in seen:
                seen.add(key)
                out.append(z)
    return out


def find_file(pack, fn):
    hits = glob.glob(os.path.join(pack, "Samples", "**", fn), recursive=True)
    return hits[0] if hits else None


# ------------------------------------------------------------------------------------------- AIFF, read in place
def aiff_info(path):
    """the COMM / SSND facts of an AIFF / AIFC: channels, frames, bits, rate, compression, the sample data's offset"""
    f = open(path, "rb")
    h = f.read(12)
    if h[:4] != b"FORM" or h[8:12] not in (b"AIFF", b"AIFC"):
        raise ValueError(f"{path}: not an AIFF")
    info = dict(comp=b"NONE", aifc=h[8:12] == b"AIFC")
    while True:
        c = f.read(8)
        if len(c) < 8:
            break
        cid, sz = c[:4], struct.unpack(">I", c[4:])[0]
        pos = f.tell()
        if cid == b"COMM":
            d = f.read(min(sz, 64))
            ch, nfr, bits = struct.unpack(">hIh", d[:8])
            e, hi, lo = struct.unpack(">HII", d[8:18])        # 80-bit extended rate
            rate = (hi * 2.0 ** -31 + lo * 2.0 ** -63) * 2.0 ** ((e & 0x7FFF) - 16383)
            info.update(ch=ch, frames=nfr, bits=bits, fs=int(round(rate)))
            if info["aifc"] and len(d) >= 22:
                info["comp"] = d[18:22]
        elif cid == b"SSND":
            off = struct.unpack(">I", f.read(4))[0]
            info["data"] = pos + 8 + off
        f.seek(pos + sz + (sz & 1))
    f.close()
    return info


class Protected(Exception):
    pass


def aiff_read(path, start, n, info=None):
    """frames [start, start + n) as mono float in [-1, 1], read in place (seek + read, nothing written)"""
    info = info or aiff_info(path)
    if info["comp"] not in PCM_COMP:
        raise Protected(f"{os.path.basename(path)}: compression '{info['comp'].decode('latin-1')}' is not PCM "
                        "(protected pack audio): not read")
    bps = info["bits"] // 8
    fr = bps * info["ch"]
    with open(path, "rb") as f:
        f.seek(info["data"] + start * fr)
        raw = f.read(n * fr)
    le = info["comp"] == b"sowt"
    if info["comp"] in (b"fl32", b"FL32"):
        x = np.frombuffer(raw, ">f4").astype(float)
    elif bps == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        if le:
            b = b[:, ::-1]
        v = b[:, 0].astype(np.int32) << 16 | b[:, 1].astype(np.int32) << 8 | b[:, 2].astype(np.int32)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(float) / (1 << 23)
    else:
        dt = {1: "i1", 2: "i2", 4: "i4"}[bps]
        x = np.frombuffer(raw, ("<" if le else ">") + dt).astype(float) / float(1 << (8 * bps - 1))
    return x.reshape(-1, info["ch"]).mean(axis=1)


def to_fs(x, fs):
    if fs == FS:
        return np.asarray(x, float)
    from scipy.signal import resample_poly
    g = math.gcd(int(fs), FS)
    return resample_poly(np.asarray(x, float), FS // g, int(fs) // g)


# ------------------------------------------------------------------------------------------------- the measures
def _va():
    import marimba_va as mva                      # (late: it pulls in the golden model)
    return mva


def fine_peak(x, f, lo, hi, t0, t1, nfft=1 << 18, prom_db=10.0):
    """the strongest peak in [lo f, hi f] of x[t0:t1] (flat-start window), parabolic on the log magnitude; None when it
    is not a real peak: on the search band's edge (the skirt of a stronger line outside it) or under prom_db over the
    band's median (a partial the mallet did not excite)"""
    mva = _va()
    seg = np.asarray(x[int(t0 * FS):int(t1 * FS)], float)
    if len(seg) < 64 or hi * f > 0.45 * FS:
        return None
    S = np.abs(np.fft.rfft(seg * mva.flat_start(len(seg)), nfft))
    k0, k1 = int(lo * f * nfft / FS), int(hi * f * nfft / FS) + 1
    k = k0 + int(np.argmax(S[k0:k1]))
    if k <= k0 + 1 or k >= k1 - 2:
        return None
    m0, m1 = int(0.8 * lo * f * nfft / FS), min(len(S), int(1.2 * hi * f * nfft / FS) + 1)
    if 20 * math.log10(S[k] / (np.median(S[m0:m1]) + 1e-30)) < prom_db:
        return None
    a, b, c = np.log(S[k - 1:k + 2] + 1e-30)
    d = a - 2 * b + c
    return (k + (0.5 * (a - c) / d if d else 0.0)) * FS / nfft


def band_t60(x, f, drop, rel=0.03):
    """marimba_va.t60_fit (from 3 dB under the band's peak, a drop dB line fit, x 60 / drop) when the band falls the
    whole drop before the end (a recording's noise floor, a short file), else None. x is zero-padded to 3x its length
    first: the brick-wall band filter is circular, and without the pad the strike wraps onto the tail, so a take cut
    20 ms later read C2's T60 up to 20 % apart (the dry-run sample-back found it)"""
    mva = _va()
    if f >= 0.45 * FS:
        return None
    x = np.concatenate([np.asarray(x, float), np.zeros(2 * len(x))])
    e, _ = mva.band_env_db(x, f, rel)
    i = int(np.argmax(e))
    if not np.any(e[i:] <= e[i] - 3.0 - drop):
        return None
    t = mva.t60_fit(x, f, drop=drop, rel=rel)
    return None if not np.isfinite(t) else float(t)


def beat(x, f0):
    """the f0 band's AM (the pair's split): rate Hz, depth dB (p95 - p5 of the residual), clear (vib_ref_measure's
    motor criteria: the peak 6x the median, depth over 1 dB)"""
    mva = _va()
    e, t = mva.band_env_db(x, f0, rel=0.03, hop=64)
    i = int(np.argmax(e))
    stop = e[i] - 40.0                                     # (a recording's floor: 40 dB under the peak or the end)
    j = i + int(np.argmax(e[i:] < stop)) if np.any(e[i:] < stop) else len(e) - 1
    if t[j] - t[i] < 1.0:
        return None
    y, tt = e[i:j], t[i:j] - t[i]
    res = y - np.polyval(np.polyfit(tt, y, 2), tt)
    n = 8 * len(res)
    sp = np.abs(np.fft.rfft(res * np.hanning(len(res)), n))
    fr = np.fft.rfftfreq(n, tt[1] - tt[0])
    m = (fr > max(0.25, 1.5 / tt[-1])) & (fr < 8.0)
    if not m.any():
        return None
    k = int(np.argmax(sp[m]))
    depth = float(np.percentile(res, 95) - np.percentile(res, 5))
    clear = bool(sp[m][k] / (np.median(sp[m]) + 1e-12) > 6 and depth > 1.0)
    return dict(rate=float(fr[m][k]), depth=depth, clear=clear, span_s=float(tt[-1]))


def measure(x, midi, fs=FS, f0_hint=None):
    """every number of the module docstring for one note; x mono, floats in [-1, 1] or the int16 scale (the absolute
    LUFS of a recording depends on its gain: the curve's shape is what compares)"""
    import bartube as btm
    mva = _va()
    x = to_fs(x, fs)
    a = np.abs(x)
    if a.max() <= 0:
        return None
    x = x * 32767.0 if a.max() < 2.0 else x          # floats in [-1, 1] to the int16 scale (one gain for every note)
    a = np.abs(x)
    i0 = max(0, int(np.argmax(a > 1e-3 * a.max())) - int(0.001 * FS))
    x = x[i0:]
    a = a[i0:]
    fn = f0_hint or et(midi)
    # the decay span: until the 10 ms RMS is 40 dB under its peak (the f0 FFT window and the V2/V3 windows)
    hop = int(0.01 * FS)
    env = 20 * np.log10(np.sqrt(np.convolve(x * x, np.ones(hop) / hop, "valid")[::hop]) + 1e-9)
    pk = int(np.argmax(env))
    j40 = pk + int(np.argmax(env[pk:] < env[pk] - 40)) if np.any(env[pk:] < env[pk] - 40) else len(env) - 1
    t_ring = j40 * hop / FS
    f0 = fine_peak(x, fn, 0.94, 1.06, 0.02, 0.02 + float(np.clip(t_ring, 0.1, 1.0)))
    if f0 is None:
        return None
    r = dict(midi=midi, f0=f0, cents=1200 * math.log2(f0 / et(midi)))
    tw = 0.003 + max(0.1, 40.0 / f0)                 # 40 periods of f0 (>= 0.1 s): V2's line resolved on the low bars
    for nm, lo, hi in (("v2", 3.8, 4.2), ("v3", 9.0, 10.6)):
        f = fine_peak(x, f0, lo, hi, 0.003, tw)
        r[nm + "_ratio"] = f / f0 if f else None
        if f:
            l1 = mva.partial_db(x, f0, 0.005, 0.3)
            r[nm + "_db"] = mva.partial_db(x, f, 0.005, 0.3) - l1
            r[nm + "_db_onset"] = mva.partial_db(x, f, 0.005, 0.05) - mva.partial_db(x, f0, 0.005, 0.05)
            r[nm + "_t60"] = band_t60(x, f, 20.0)
        else:
            r[nm + "_db"] = r[nm + "_db_onset"] = r[nm + "_t60"] = None
    r["t60_v1_30db"] = band_t60(x, f0, 30.0)
    r["t60_v1"] = band_t60(x, f0, 30.0, rel=float(np.clip(16.0 / f0, 0.03, 0.15)))   # the unbiased one (docstring)
    lv, _ = vrm.band_env(x, FS, f0, 0, int(0.005 * FS))           # the VIBES reference's own fit, for continuity
    fl = float(np.median(lv[-max(8, len(lv) // 10):]))
    r["t60_v1_ref"] = vrm.fit_t60(lv, 0.005, fl - 6)[0] if len(lv) > 8 else None
    h = 11                                                           # 0.25 ms peak envelope: the attack
    m = len(a) // h
    pe = a[:m * h].reshape(m, h).max(axis=1)
    p = int(np.argmax(pe[:int(0.015 * FS) // h]))     # the strike's own peak (the tube's bloom comes later)
    a10 = int(np.argmax(pe[:p + 1] >= 0.1 * pe[p]))
    a90 = int(np.argmax(pe[:p + 1] >= 0.9 * pe[p]))
    r["attack_ms"] = (a90 - a10) * h / FS * 1000
    r["f0_peak_ms"] = float(btm.f0_peak_ms(x, f0, fs=FS))
    r["beat"] = beat(x, f0) if midi <= 62 else None
    r["lufs"] = mva.lufs(x, 0.0, 0.4)
    r["centroid_att"] = mva.centroid(x, 0, mva.ATT)
    r["centroid_300"] = mva.centroid(x, 0, 0.3)
    r["t_ring_s"] = t_ring
    return r


# ---------------------------------------------------------------------------------------------- the three sources
def measure_pack(pack, keys):
    """(zones measured, a list of notes on what could not be read)"""
    zones = pack_zones(pack)
    rows, notes, infos = [], [], {}
    for z in zones:
        if not keys[0] <= z["root"] <= keys[1]:
            continue
        p = find_file(pack, z["file"])
        if not p:
            notes.append(f"{z['file']}: not found under Samples/")
            continue
        try:
            info = infos.setdefault(p, aiff_info(p))
            x = aiff_read(p, z["start"], z["end"] - z["start"], info)
        except Protected as e:
            if str(e) not in notes:
                notes.append(str(e))
            continue
        r = measure(x, z["root"], info["fs"])
        if r:
            r.update(zone=z)
            rows.append(r)
    return zones, rows, notes, infos


def octave_fix(x, fs, midi):
    """libraries differ on the octave names ('C4' may be middle C or the C above): the name's key or an octave off,
    whichever has the strongest line within +-6 % of its ET pitch over 20..520 ms (as vib_ref_measure does)"""
    y = to_fs(x, fs)
    seg = y[int(0.02 * FS):int(0.52 * FS)]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 17))
    fr = np.fft.rfftfreq(1 << 17, 1 / FS)
    best = max((-12, 0, 12), key=lambda k: S[(fr > et(midi + k) / 1.06) & (fr < et(midi + k) * 1.06)].max())
    return midi + best


def measure_files(paths, keys, shift=None):
    """shift: semitones added to the file names' notes instead of octave_fix (a library named two octaves off, e.g.
    Pigments' 'Bells & Mallets/Marimba C1' sounds C3: a marimba's V2 is two octaves up, so +-24 can't be guessed)"""
    rows = []
    for p in paths:
        midi, _ = vrm.nominal(p)
        if midi is None:
            continue
        if shift is not None:
            midi += shift
        if not keys[0] <= midi <= keys[1]:
            continue
        x, fs = vrm.load(p)
        if shift is None:
            midi = octave_fix(x, fs, midi)
        if not keys[0] <= midi <= keys[1]:
            continue
        r = measure(x, midi, fs)
        if r:
            r.update(zone=dict(file=os.path.basename(p), preset=os.path.basename(os.path.dirname(p)) + "/" +
                               os.path.basename(p), vel=(0, 127)))
            rows.append(r)
    return rows


def golden(keys, vel=100, dur=3.0, knobs=None):
    """keys: (lo, hi) or a list; knobs: card values over bars_model.DEFAULT_KNOBS (e.g. dict(bark=127))"""
    import bars_model as gm
    mva = _va()
    spec = mva.load_spec()
    D = {d["midi"]: d for d in spec["design"]}
    ks = list(range(keys[0], keys[1] + 1)) if isinstance(keys, tuple) else list(keys)
    y = mva.render(spec, ks, [vel] * len(ks), dur, dict(gm.DEFAULT_KNOBS, **(knobs or {})))
    return [measure(x, n, FS, D[n]["f0"]) for n, x in zip(ks, y)]


def host(keys, vel=100, dur=3.0, cc=()):
    """the host build, RAW (the engine alone, as the golden model), NOISE 0 (the golden model has none), the cards at
    their defaults, one strike per run; cc: extra (cc, value) pairs (e.g. ((29, 127),): MALLET RUBBER)"""
    import bar_measure as bm
    bm.build()
    out = os.path.join(ROOT, "build", "mrb_ref", "host")
    rows = []
    for n in (range(keys[0], keys[1] + 1) if isinstance(keys, tuple) else keys):
        tag = f"host_{n}_v{vel}_" + "_".join(f"{c}-{v}" for c, v in cc)
        ev = bm.setup_events(typ=5, raw=True, noise=0) + [(20, f"midi 0xB0 {c} {v}") for c, v in cc]
        ev += bm.note(100, n, vel)
        x, _ = bm.render(tag, ev, 100 + int(dur * 1000), out)
        for e in (".wav", ".txt"):
            try:
                os.remove(os.path.join(out, tag + e))
            except OSError:
                pass
        rows.append(measure(bm.mono(x)[int(0.1 * FS):], n, FS))
    return rows


# ------------------------------------------------------------------------------------------------ the targets
def interp_key(tab, n):
    ks = sorted(int(k) for k in tab)
    return float(np.interp(n, ks, [tab[str(k)] if str(k) in tab else tab[k] for k in ks]))


def targets():
    d = json.load(open(DATA))
    sp = json.load(open(SPEC))
    t = dict(t60={int(k): v for k, v in d["T1b"]["t60_s"].items()},
             bracket={int(k): v for k, v in d["T1b"]["bracket"].items()},
             bloom={int(k): v for k, v in d["T5b"]["bloom_ms_targets"].items()},
             r1b={m: {int(k): v for k, v in d["R1b"]["level_db"][m].items()} for m in ("V2", "V3")},
             offset=d["calib"]["st77_offset_db"])
    k0 = sp["key_lo"] if "key_lo" in sp else 21
    st = np.array(sp["st77_lufs"])
    ro = np.array(sp.get("rolloff_db", [0.0] * len(st)))
    t["level"] = {k0 + i: float(st[i] + t["offset"] + ro[i]) for i in range(len(st))}
    t["rolloff"] = {k0 + i: float(ro[i]) for i in range(len(st))}
    return t


# ------------------------------------------------------------------------------------------------ the report
def f_(v, fmt, none="-"):
    return none if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v, fmt)


def layer_for(rows, vel):
    """the zone rows of one velocity layer: the layer that plays vel at full weight (crossfade range), per preset"""
    by = {}
    for r in rows:
        z = r["zone"]
        lo, hi = z.get("vel_full", z["vel"])
        if lo <= vel <= hi or z["vel"][0] <= vel <= z["vel"][1]:
            by.setdefault((z["preset"], r["midi"]), []).append(r)
    out = {}
    for (pr, m), rs in by.items():
        rs.sort(key=lambda r: (not (r["zone"].get("vel_full", r["zone"]["vel"])[0] <= vel <=
                                    r["zone"].get("vel_full", r["zone"]["vel"])[1]), -r["zone"]["vel"][0]))
        out.setdefault(pr, {})[m] = rs[0]
    return out


def note_table(rows, title):
    L = [f"### {title}", "",
         "| key | f0 Hz | vs ET c | V2/V1 | V3/V1 | V2 dB 5-300 / 5-50 ms | V3 dB 5-300 / 5-50 ms | T60 V1 wide / gate band / VIBES ref fit s | "
         "T60 V2 / V3 s | attack ms | f0 peak ms | beat (Hz, dB) | LUFS 0-0.4 | centroid 0-50 / 0-300 ms Hz |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        if not r:
            continue
        b = r["beat"]
        bt = "-" if b is None else (f"{b['rate']:.2f}, {b['depth']:.1f}" + ("" if b["clear"] else " (unclear)"))
        L.append(f"| {name(r['midi'])} | {r['f0']:.2f} | {r['cents']:+.1f} | {f_(r['v2_ratio'], '.3f')} | "
                 f"{f_(r['v3_ratio'], '.2f')} | {f_(r['v2_db'], '.1f')} / {f_(r['v2_db_onset'], '.1f')} | "
                 f"{f_(r['v3_db'], '.1f')} / {f_(r['v3_db_onset'], '.1f')} | {f_(r['t60_v1'], '.2f')} / {f_(r['t60_v1_30db'], '.2f')} / "
                 f"{f_(r['t60_v1_ref'], '.2f')} | {f_(r['v2_t60'], '.2f')} / {f_(r['v3_t60'], '.3f')} | "
                 f"{r['attack_ms']:.1f} | {r['f0_peak_ms']:.0f} | {bt} | {r['lufs']:.1f} | "
                 f"{r['centroid_att']:.0f} / {r['centroid_300']:.0f} |")
    return L + [""]


def rel_curve(rows, ref_key=60):
    by = {r["midi"]: r for r in rows if r}
    if ref_key not in by:
        return {}
    return {m: r["lufs"] - by[ref_key]["lufs"] for m, r in by.items()}


BRIGHT_KEYS = [36, 42, 48, 54, 60, 66, 72, 84]          # (a list: a tuple is a (lo, hi) range)


def compare(G, H, R, t, GB=(), HB=(), RB=()):
    """the comparison rows: (item, target, reference, golden, host, verdict). G / H / R at v100 with the DEFAULT
    mallet; GB / HB / RB the brightest strike (RUBBER v127; the reference's loudest rubber layer): the partial ratios
    need the partials excited (the DEFAULT mallet's pulse leaves V3 under the leakage from C3 up)"""
    spec = json.load(open(SPEC))
    D = {d["midi"]: d for d in spec["design"]}
    g, h, rf = ({r["midi"]: r for r in X if r} for X in (G, H, R or ()))
    gb, hb, rb = ({r["midi"]: r for r in X if r} for X in (GB, HB, RB or ()))
    rows = []

    def val(d, n, k):
        return d[n][k] if n in d and d[n].get(k) is not None else None

    def vd(v, ok, who):
        return f"{'n/a' if v is None else ('inside' if ok(v) else 'OUTSIDE')} ({who})"

    for n in sorted(t["bracket"]):
        lo, hi = t["bracket"][n]
        vg, vh, vr = val(g, n, "t60_v1"), val(h, n, "t60_v1"), val(rf, n, "t60_v1")
        gg, gh, gr = val(g, n, "t60_v1_30db"), val(h, n, "t60_v1_30db"), val(rf, n, "t60_v1_30db")
        strict = lambda v: lo <= v <= hi                             # noqa: E731
        loose = lambda v: 0.8 * lo <= v <= 1.2 * hi                  # noqa: E731  (VA-M2's +-20 % on the output)
        rows.append(("T1b V1 T60 (output, 30 dB fit; the gate's +-3 % band in brackets) " + name(n),
                     f"{t['t60'][n]} [{lo}-{hi}]", f"{f_(vr, '.2f')} ({f_(gr, '.2f')})",
                     f"{f_(vg, '.2f')} ({f_(gg, '.2f')})", f"{f_(vh, '.2f')} ({f_(gh, '.2f')})",
                     f"bracket: {vd(vg, strict, 'golden')}, {vd(vh, strict, 'host')}"
                     + ("" if vr is None else f", {vd(vr, strict, 'ref')}")
                     + f"; +-20 %: {vd(vg, loose, 'golden')}"))
    for n in sorted(t["bloom"]):
        tg = t["bloom"][n]
        tol = max(0.3 * tg, 15.0) if n < 72 else None
        ok = lambda v: (abs(v - tg) <= tol) if tol else v <= max(15.0, tg)     # noqa: E731
        vg, vh, vr = val(g, n, "f0_peak_ms"), val(h, n, "f0_peak_ms"), val(rf, n, "f0_peak_ms")
        rows.append(("T5b bloom (f0 band peak, ms) " + name(n), f"{tg} ({'+-30 %' if tol else '<= 15'})", f_(vr, ".0f"),
                     f_(vg, ".0f"), f_(vh, ".0f"), f"{vd(vg, ok, 'golden')}, {vd(vh, ok, 'host')}"
                     + ("" if vr is None else f", {vd(vr, ok, 'ref')}")))
    for n in (36, 42, 48, 54, 60, 72, 84, 96):
        vg, vh, vr = val(g, n, "cents"), val(h, n, "cents"), val(rf, n, "cents")
        ok = lambda v: abs(v) <= 3.0                                  # noqa: E731
        rows.append(("pitch: the output's f0 vs ET (c) " + name(n), "0 (V1 mode at ET; +-3 c marked here)",
                     f_(vr, "+.1f"), f_(vg, "+.1f"), f_(vh, "+.1f"), f"{vd(vg, ok, 'golden')}, {vd(vh, ok, 'host')}"))
    for n in (36, 42, 48, 54):
        b_g, b_h, b_r = (d.get(n, {}).get("beat") if d.get(n) else None for d in (g, h, rf))
        fb = lambda b: "-" if not b else f"{b['rate']:.2f} Hz {b['depth']:.0f} dB" + ("" if b["clear"] else "?")   # noqa: E731
        rows.append(("low pair: beat (the normal-mode split) " + name(n), f"design split {D[n]['split_hz']:.2f} Hz",
                     fb(b_r), fb(b_g), fb(b_h), "report ('?': no clear AM peak)"))
    for mode in ("V2", "V3"):
        for n in sorted(t["r1b"][mode]):
            tg = t["r1b"][mode][n]
            k = mode.lower() + "_db_onset"
            vg, vh, vr = val(g, n, k), val(h, n, k), val(rf, n, k)
            rows.append((f"R1b {mode}/V1 at the onset, dB " + name(n), f"{tg} (slot peaks, 5-50 ms)", f_(vr, ".1f"),
                         f_(vg, ".1f"), f_(vh, ".1f"),
                         "report: the output's 5-50 ms band energy (not R1b's slot peaks); golden - target "
                         + f_(None if vg is None else vg - tg, "+.1f") + " dB" + (" (not excited)" if vg is None else "")))
    for mode, nm, ok, fmt in (("V2", "v2_ratio", lambda v: abs(v - 4.0) <= 0.02, ".3f"),
                              ("V3", "v3_ratio", lambda v: 9.4 <= v <= 10.02, ".2f")):
        for n in BRIGHT_KEYS:
            des = [m["ratio"] for m in D[n]["modes"] if m["name"] == mode][0]
            vg, vh, vr = val(gb, n, nm), val(hb, n, nm), val(rb, n, nm)
            rows.append((f"§2.1 {mode}/V1 (RUBBER v127) " + name(n), ("4.00 +-0.02" if mode == "V2" else "9.4-10.0")
                         + f"; design {des:.3f}", f_(vr, fmt), f_(vg, fmt), f_(vh, fmt),
                         f"{vd(vg, ok, 'golden')}, {vd(vh, ok, 'host')}" + ("" if vr is None else f", {vd(vr, ok, 'ref')}")))
    cg, ch, cr = rel_curve(G), rel_curve(H), (rel_curve(R) if R else {})
    lv = t["level"]
    for n in (36, 48, 72, 84, 90, 96):
        tg = lv[n] - lv[60]
        vg, vh, vr = cg.get(n), ch.get(n), cr.get(n)
        ok = lambda v: abs(v - tg) <= 0.5                             # noqa: E731
        rows.append(("level curve, LUFS 0-0.4 s re C4 " + name(n), f"{tg:+.1f} (ST77 + offset + roll-off)",
                     f_(vr, "+.1f"), f_(vg, "+.1f"), f_(vh, "+.1f"), f"+-0.5 LU: {vd(vg, ok, 'golden')}, {vd(vh, ok, 'host')}"))
    ro = t["rolloff"]
    for n in (78, 84, 90, 96):
        rows.append(("top roll-off under the ST77 curve (dB) " + name(n), f"{ro[n]:+.1f} (the design's own)", "-", "-",
                     "-", "report (the reference's curve shape would test it)"))
    return rows


def plot(G, H, R, t, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(outdir, exist_ok=True)
    series = [("golden", G, "C0"), ("host", H, "C1")] + ([("reference", R, "C2")] if R else [])
    items = [("cents", "f0 vs ET (c)"), ("v2_ratio", "V2 / V1"), ("v3_ratio", "V3 / V1"), ("v2_db", "V2 re V1, dB 5-300 ms"),
             ("v3_db", "V3 re V1, dB 5-300 ms"), ("t60_v1", "V1 T60 (30 dB fit, wide band), s"), ("f0_peak_ms", "f0 peak (bloom), ms"),
             ("lufs_rel", "LUFS re C4"), ("centroid_att", "attack centroid 0-50 ms, Hz"), ("attack_ms", "attack 10-90 %, ms")]
    for k, lab in items:
        fig, ax = plt.subplots(figsize=(8, 4))
        for nm, rows, c in series:
            rr = [r for r in rows if r]
            if k == "lufs_rel":
                cv = rel_curve(rr)
                xs, ys = list(cv), list(cv.values())
            else:
                xs = [r["midi"] for r in rr if r.get(k) is not None]
                ys = [r[k] for r in rr if r.get(k) is not None]
            ax.plot(xs, ys, ".-", color=c, label=nm, lw=1)
        if k == "t60_v1":
            for n, (lo, hi) in t["bracket"].items():
                ax.plot([n, n], [lo, hi], "k-", lw=3, alpha=0.4)
            ax.set_yscale("log")
        if k == "f0_peak_ms":
            ax.plot(list(t["bloom"]), list(t["bloom"].values()), "kx", label="T5b target")
        if k == "lufs_rel":
            ks = sorted(t["level"])
            ax.plot(ks, [t["level"][n] - t["level"][60] for n in ks], "k--", lw=1, label="target curve")
        ax.set_xlabel("MIDI key")
        ax.set_title(lab)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, k + ".png"), dpi=90)
        plt.close(fig)


def write_md(path, src_lines, zones, R, notes, G, H, extra, t, cmp_rows):
    L = ["# Reference marimba measurements (numbers only; the recordings stay where they are)", "",
         "Made by tools/mrb_ref_measure.py (MARIMBA PLAN D10, approved 2026-10-19 for numbers only). Nothing of the "
         "recordings is copied, played back, fitted or embedded.", ""] + src_lines + [""]
    if notes:
        L += ["## Not measured", ""] + [f"- {n}" for n in notes] + [""]
    if zones:
        by = {}
        for z in zones:
            by.setdefault((z["preset"], z["file"]), []).append(z)
        L += ["## The note boundaries (from the pack's presets, not from the audio)", "",
              "| preset | file | zones | root keys | velocity layers (full weight) | detune c | volume dB | zone length s (min / median / max) |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for (pr, fn), zs in sorted(by.items()):
            roots = sorted(set(z["root"] for z in zs))
            lay = sorted(set((z["vel"], z["vel_full"]) for z in zs))
            ln = [(z["end"] - z["start"]) / z["sr"] for z in zs]
            L.append(f"| {pr} | {fn} | {len(zs)} | {name(roots[0])}-{name(roots[-1])} ({len(roots)}) | "
                     + ", ".join(f"{a[0]}-{a[1]} ({b[0]}-{b[1]})" for a, b in lay)
                     + f" | {sorted(set(z['detune_c'] for z in zs))} | {sorted(set(round(z['volume_db'], 1) for z in zs))} | "
                     f"{min(ln):.2f} / {float(np.median(ln)):.2f} / {max(ln):.2f} |")
        L += ["", "Zone lengths are where the pack's editor cut each note (its own judgement of the end), not a decay "
              "measure; by register (the loudest Hits layer):", ""]
        L += zone_length_rows(zones) + [""]
    if R:
        for pr, rows in sorted(layer_for(R, 100).items()):
            L += note_table(sorted(rows.values(), key=lambda r: r["midi"]), f"Reference: {pr}, the layer at v100")
    L += ["## The golden model and the host render (the same procedure), v100, DEFAULT cards", ""]
    L += note_table(G, "Golden model (tools/bars_model.py, marimba_spec.json)")
    L += note_table(H, "Host build (RAW, NOISE 0, CC 79 127)")
    L += extra
    L += ["## Comparison: targets (marimba.json) vs reference vs golden vs host", "",
          "| item | target | reference | golden | host | verdict |", "| --- | --- | --- | --- | --- | --- |"]
    L += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in cmp_rows]
    L += ["", "T60 'inside' = within the T1b bracket +-20 % (Gate VA-M2's tolerance on the output's f0-band 30 dB fit); "
          "bloom = VA-M4's tolerance; level = VA-M10's +-0.5 LU re C4.", ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("\n".join(L) + "\n")
    return L


def zone_length_rows(zones):
    hits = [z for z in zones if "Hits" in z["file"]]
    L = ["| file | " + " | ".join(name(n) for n in (45, 48, 54, 60, 66, 72, 78, 84, 90, 96)) + " |",
         "| --- |" + " --- |" * 10]
    for fn in sorted(set(z["file"] for z in hits)):
        top = max(z["vel"][1] for z in hits if z["file"] == fn)
        zs = {z["root"]: (z["end"] - z["start"]) / z["sr"] for z in hits if z["file"] == fn and z["vel"][1] == top}
        L.append(f"| {fn} (v..{top}) | " + " | ".join(f_(zs.get(n), ".2f") for n in (45, 48, 54, 60, 66, 72, 78, 84, 90, 96))
                 + " |")
    return L


def brightness(R, G):
    """cloth vs rubber (the reference) and SOFT / MED vs RUBBER (the golden model): the attack centroid ratio at C4"""
    mva = _va()
    out = ["## Brightness: the attack centroid (0-50 ms), rubber re cloth / soft", ""]
    if R:
        lay = layer_for(R, 117)
        rub = next((v for k, v in lay.items() if "Rubber Hits" in k), {})
        clo = next((v for k, v in lay.items() if "Cloth Hits" in k), {})
        for n in (48, 60, 72, 84):
            if n in rub and n in clo:
                out.append(f"- reference {name(n)} v117 layers: rubber {rub[n]['centroid_att']:.0f} Hz, cloth "
                           f"{clo[n]['centroid_att']:.0f} Hz, ratio {rub[n]['centroid_att'] / clo[n]['centroid_att']:.2f}")
    spec = mva.load_spec()
    import bars_model as gm
    kn = dict(gm.DEFAULT_KNOBS)
    ys = mva.render(spec, [60] * 3, [117] * 3, 0.4, [dict(kn, bark=mva.RUBBER), dict(kn, bark=mva.SOFT), dict(kn, bark=mva.MED)])
    c = [mva.centroid(y, 0, mva.ATT) for y in ys]
    out.append(f"- golden C4 v117: RUBBER {c[0]:.0f} Hz, SOFT {c[1]:.0f} Hz, MED {c[2]:.0f} Hz; RUBBER / SOFT "
               f"{c[0] / c[1]:.2f} (VA-M5 >= 4), RUBBER / MED {c[0] / c[2]:.2f}")
    return out + [""]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", help="one-shot WAV / AIFF files or folders (note names in the file names)")
    ap.add_argument("--pack", help="an Ableton pack folder: zones from its presets")
    ap.add_argument("--no-ref", action="store_true", help="the golden model and the host only")
    ap.add_argument("--keys", default="36-96")
    ap.add_argument("--out-md", default=OUT_MD)
    ap.add_argument("--plots", default=OUT_PNG)
    ap.add_argument("--no-host", action="store_true")
    ap.add_argument("--shift", type=int, help="semitones added to the files' note names (skips the octave guess)")
    ap.add_argument("--note", action="append", default=[], help="a line for the report's 'Not measured' section")
    a = ap.parse_args(argv)
    keys = tuple(int(v) for v in a.keys.split("-"))
    zones, R, notes, src = [], [], list(a.note), []
    if a.pack and not a.no_ref:
        zones, R, notes, infos = measure_pack(a.pack, keys)
        src.append(f"Pack: {a.pack} (zones from Sounds/**/*Marimba*.adg)")
        for p, i in infos.items():
            src.append(f"- {os.path.basename(p)}: {i.get('ch')} ch, {i.get('bits')} bit, {i.get('fs')} Hz, "
                       f"{i.get('frames', 0) / max(1, i.get('fs', 1)):.0f} s, compression "
                       f"'{i['comp'].decode('latin-1')}'")
    if a.files and not a.no_ref:
        paths = []
        for f in a.files:
            paths += sorted(glob.glob(os.path.join(f, "*"))) if os.path.isdir(f) else [f]
        R += measure_files([p for p in paths if p.lower().endswith((".wav", ".aif", ".aiff"))], keys, a.shift)
        src.append("Files: " + ", ".join(sorted(set(os.path.dirname(p) or "." for p in paths))))
    if not R and not a.no_ref:
        src.append("No reference note was measured (see 'Not measured').")
    G = golden(keys)
    H = [] if a.no_host else host(keys)
    GB = golden(BRIGHT_KEYS, 127, 1.0, dict(bark=127))
    HB = [] if a.no_host else host(BRIGHT_KEYS, 127, 1.0, ((29, 127),))
    RB = []
    if R:
        rub = next((v for k, v in layer_for(R, 127).items() if "Rubber Hits" in k), {})
        RB = list(rub.values())
    t = targets()
    cmp_rows = compare(G, H, R, t, GB, HB, RB)
    extra = brightness(R, G)
    L = write_md(a.out_md, src, zones, R, notes, G, H, extra, t, cmp_rows)
    plot(G, H, R, t, a.plots)
    js = os.path.join(a.plots, "measures.json")
    json.dump(dict(golden=G, host=H, golden_rubber127=GB, host_rubber127=HB, reference=[{k: v for k, v in r.items()} for r in R], notes=notes),
              open(js, "w"), indent=1, default=str)
    print("\n".join(L))
    print(f"\nwrote {a.out_md}, plots and measures.json in {a.plots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
