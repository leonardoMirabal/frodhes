#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Measure reference vibraphone recordings - for the VIBES engine's calibration targets ONLY.

The recordings are read where they are (a path on the command line) and never copied, played back or fitted into
the engine or its tables: only measured NUMBERS come out (private/vibraphone/ref_measure.md, plots in
build/vib_ref/). Per note:

  partials     the strongest peaks of the spectrum after the attack: frequency, ratio to f0, cents vs the 1:4:10
               tuning targets (and other ratios as found)
  T60          per partial: the slope of its level over time (a straight-line fit in dB over the stretch from its
               peak down to 10 dB above the noise floor), extrapolated to 60 dB
  attack       10 % -> 90 % of the broadband envelope's peak (ms)
  f0 peak      the time the fundamental's envelope peaks (the tube's build-up), ms after the onset
  motor        amplitude modulation of the fundamental: the log envelope with its decay removed, its strongest
               component between 0.8 and 15 Hz (rate Hz, peak-to-trough depth dB); "off" if no clear peak

  python3 tools/vib_ref_measure.py "<folder with Vibraphone *.wav>" [more files ...]
"""
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_MD = os.path.join(ROOT, "private", "vibraphone", "ref_measure.md")
OUT_PNG = os.path.join(ROOT, "build", "vib_ref")
NOTES = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8,
         "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}


def load(path):
    """mono float in [-1, 1] and the sample rate; AIFF is converted with afconvert to a temporary WAV"""
    tmp = None
    needs = not path.lower().endswith(".wav")
    if not needs:
        try:
            wave.open(path).close()
        except wave.Error:                            # e.g. a 32-bit float WAV: converted too
            needs = True
    if needs:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", path, tmp], check=True, capture_output=True)
        path = tmp
    w = wave.open(path)
    n, ch, sw, fs = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
    raw = w.readframes(n)
    if tmp:
        os.unlink(tmp)
    dt = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw)
    if sw == 3:                                       # 24-bit
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) | b[:, 1].astype(np.int32) << 8 | b[:, 2].astype(np.int32) << 16)
        x = np.where(x >= 1 << 23, x - (1 << 24), x).astype(float) / (1 << 23)
    else:
        x = np.frombuffer(raw, dt).astype(float) / float(np.iinfo(dt).max)
    x = x.reshape(-1, ch).mean(axis=1)
    return x, fs


def nominal(name):                                    # "Vibraphone C#4.wav" -> MIDI note, Hz
    m = re.search(r"([A-Ga-g][#b]?)(-?\d)", os.path.basename(name))
    if not m:
        return None, None
    pc = NOTES[m.group(1).upper().replace("B", "B") if len(m.group(1)) == 1 else m.group(1)[0].upper() + m.group(1)[1].upper()]
    midi = 12 * (int(m.group(2)) + 1) + pc
    return midi, 440 * 2 ** ((midi - 69) / 12)


def envelope_db(x, fs, hop):
    n = len(x) // hop
    e = np.array([np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2)) for i in range(n)])
    return 20 * np.log10(e + 1e-9)


def band_env(x, fs, f, bw, hop):
    """the level of a narrow band around f over time: a short-time Fourier magnitude at f (Hann, 4096)"""
    N = 4096
    win = np.hanning(N)
    k = np.arange(N)
    ph = np.exp(-2j * np.pi * f * k / fs)
    out = []
    for i in range(0, len(x) - N, hop):
        out.append(abs(np.sum(x[i:i + N] * win * ph)) * 2 / np.sum(win))
    return 20 * np.log10(np.array(out) + 1e-9), N // 2


def fit_t60(lv, dt, floor):
    """T60 from a line fit over [peak .. 10 dB above the floor]; None if under 12 dB of usable range"""
    p = int(np.argmax(lv))
    stop = p
    while stop < len(lv) - 1 and lv[stop] > floor + 10:
        stop += 1
    seg = lv[p:stop]
    if len(seg) < 4 or seg[0] - seg[-1] < 12:
        return None, 0
    t = np.arange(len(seg)) * dt
    slope = np.polyfit(t, seg, 1)[0]
    return (-60 / slope if slope < 0 else None), seg[0] - seg[-1]


def measure(path):
    x, fs = load(path)
    midi, fnom = nominal(path)
    if fnom is None:
        return None
    hop = int(0.005 * fs)
    env = envelope_db(x, fs, hop)
    pk = int(np.argmax(env))
    onset = next((i for i in range(pk + 1) if env[i] > env[pk] - 40), 0)
    a10 = next(i for i in range(onset, pk + 1) if env[i] >= env[pk] + 20 * np.log10(0.1))
    a90 = next(i for i in range(onset, pk + 1) if env[i] >= env[pk] + 20 * np.log10(0.9))
    attack_ms = (a90 - a10) * hop / fs * 1000
    t0 = onset * hop
    # the spectrum of 30..530 ms after the onset: the partials
    seg = x[t0 + int(0.03 * fs): t0 + int(0.53 * fs)]
    N = 1 << int(np.ceil(np.log2(len(seg))) + 1)
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    fr = np.fft.rfftfreq(N, 1 / fs)
    db = 20 * np.log10(sp / sp.max() + 1e-12)
    best, f0, octave = -1.0, fnom, 0                # the fundamental: the strongest peak near the name's pitch or
    for k in (-1, 0, 1, 2):                         # an octave off (libraries differ: "C3" may be middle C)
        m = (fr > fnom * 2 ** k / 1.06) & (fr < fnom * 2 ** k * 1.06)
        if m.any() and sp[m].max() > best:
            best, f0, octave = float(sp[m].max()), float(fr[m][np.argmax(sp[m])]), k
    fnom *= 2 ** octave
    midi += 12 * octave
    peaks = []
    for i in range(2, len(sp) - 2):                 # local maxima within 50 dB of the strongest, above 0.8 f0
        if fr[i] > 0.8 * f0 and fr[i] < 18000 and db[i] > -50 and sp[i] == sp[max(0, i - 6):i + 7].max():
            peaks.append((fr[i], db[i]))
    peaks.sort(key=lambda p: -p[1])
    peaks = sorted(peaks[:10])
    floor = float(np.percentile(env[-max(10, len(env) // 10):], 50))
    rows = []
    for f, lvl in peaks:
        lv, _ = band_env(x[t0:], fs, f, 0, hop)
        t60, rng = fit_t60(lv, hop / fs, floor - 6)
        r = f / f0
        tgt = min((1, 2, 3, 4, 7.35, 10, 11.5, 13.76, 20), key=lambda q: abs(1200 * np.log2(r / q)))
        rows.append(dict(f=f, ratio=r, lvl=lvl, t60=t60, rng=rng, tgt=tgt, cents=1200 * np.log2(r / tgt)))
    # the fundamental's envelope: its peak time and the motor's AM
    lv0, _ = band_env(x[t0:], fs, f0, 0, hop)
    f0pk_ms = int(np.argmax(lv0)) * hop / fs * 1000
    p0 = int(np.argmax(lv0))
    end = p0
    while end < len(lv0) - 1 and lv0[end] > floor + 6:
        end += 1
    motor = None
    if end - p0 > int(1.0 / (hop / fs)):
        y = lv0[p0:end]
        t = np.arange(len(y)) * hop / fs
        res = y - np.polyval(np.polyfit(t, y, 2), t)
        sp2 = np.abs(np.fft.rfft(res * np.hanning(len(res)), 8 * len(res)))
        f2 = np.fft.rfftfreq(8 * len(res), hop / fs)
        mm = (f2 > 0.8) & (f2 < 15)                   # real motors: 0.8 .. 10 Hz; slower is the file length
        if mm.any():
            i = np.argmax(sp2[mm])
            rate = float(f2[mm][i])
            depth = float(np.percentile(res, 95) - np.percentile(res, 5))
            strength = sp2[mm][i] / (np.median(sp2[mm]) + 1e-12)
            motor = dict(rate=rate, depth=depth, clear=strength > 6 and depth > 1.0)
    return dict(octave=octave, name=os.path.basename(path), midi=midi, fnom=fnom, f0=f0, cents0=1200 * np.log2(f0 / fnom),
                attack_ms=attack_ms, f0pk_ms=f0pk_ms, floor=floor, rows=rows, motor=motor, dur=len(x) / fs)


def main(args):
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(os.path.join(a, f) for f in os.listdir(a) if f.lower().endswith((".wav", ".aif", ".aiff")))
        else:
            files.append(a)
    res = [r for r in (measure(f) for f in files) if r]
    res.sort(key=lambda r: r["midi"])
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    L = ["# Reference vibraphone measurements (numbers only; the recordings stay where they are)", "",
         "Source: " + ", ".join(sorted(set(os.path.dirname(f) for f in files))), "",
         "| note | f0 Hz | vs ET | attack ms | f0 peak ms | motor | partials: ratio (cents vs target) level dB, T60 s |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in res:
        mt = r["motor"]
        mot = "off" if not mt or not mt["clear"] else f"{mt['rate']:.2f} Hz, {mt['depth']:.1f} dB"
        parts = "; ".join(f"{p['ratio']:.2f} ({p['cents']:+.0f}c vs {p['tgt']}) {p['lvl']:.0f} dB, "
                          + (f"{p['t60']:.1f} s" if p["t60"] else "-") for p in r["rows"])
        L.append(f"| {r['name'].rsplit('.', 1)[0]} ({'+' if r['octave'] > 0 else ''}{r['octave']} oct) | {r['f0']:.1f} | {r['cents0']:+.0f}c | {r['attack_ms']:.1f} | "
                 f"{r['f0pk_ms']:.0f} | {mot} | {parts} |")
    L += ["", "T60 = a line fit of the partial's level from its peak to 10 dB above the noise floor (extrapolated to 60 "
          "dB); '-' = under 12 dB of usable range (the file ends too soon or the partial is weak). Each file is ~6 s, "
          "so long fundamentals are extrapolated from their first ~40 dB."]
    open(OUT_MD, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1:])
