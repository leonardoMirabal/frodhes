#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Hardware-in-the-loop scan of a connected FM-1 running Frodhes: MIDI in, USB audio back.

The FM-1's USB audio is exactly what its output plays (after MASTER), so this checks the real thing:
silence, latency, every key (level, tuning), velocity, the TYPEs, every effect (level against dry,
clipping, what it is for), the bypass, sustain, the held-note tail, polyphony stress (dropouts) and
every factory preset. Captures and a report go to build/hil/.

  tools/hil_scan.py [--only NAME[,NAME]] [--list]

Before: MASTER fully up and left alone. The scan changes the current panel settings (not the user
presets); it ends on F01. Needs mido + python-rtmidi, sounddevice, numpy.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil")
FS = 48000
A4 = 440.0

# the CC map (firmware/src/params.c)
CC = dict(TYPE=80, DRIVE=81, TW_SW=17, TW_DEPTH=18, TW_RATE=19, TW_MODE=21, CP_SW=85, CP_DEPTH=86, CP_SPEED=87,
          DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26, REV=91, PH_SW=12, PH_DEPTH=13, PH_SPEED=14, PH_FDBK=16,
          CMP_SW=106, CMP_AMT=107, CMP_ATK=108, CMP_REL=109, NOISE=104, SUS=64, RAW=119, CMP_RAT=112, CMP_GAIN=113,
          PH_SPEED2=14, CP_SPEED2=87, TW_SHAPE=22, DL_TONE=27, LEVEL=110, BYPASS=111)
DRY = dict(DRIVE=0, REV=0, TW_SW=0, CP_SW=0, PH_SW=0, DL_SW=0, CMP_SW=0, RAW=0, SUS=0)


def param_defaults():
    """every parameter's CC and the CC value that sets its default (parsed from firmware/src/params.c), so each test
    starts from the same state whatever the FM-1 had saved"""
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    out = []
    for name, hi, de, cc in re.findall(r'\[P_(\w+)\] = \{"[^"]*", (\d+), (\d+), F_\w+, (\d+)', src):
        hi, de, cc = int(hi), int(de), int(cc)
        if name == "TYPE":
            v = de * 32 + 8
        elif hi == 2:
            v = (0, 64, 127)[de]
        elif hi < 127:
            v = min(127, (de * 128 + 64) // (hi + 1))
        else:
            v = de
        out.append((cc, v))
    return out


def db(x):
    x = np.asarray(x, float)
    return 20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12) if x.size else -240.0


class Rig:
    def __init__(self):
        import mido
        import sounddevice as sd
        self.sd = sd
        names = [n for n in mido.get_output_names() if any(k in n.lower() for k in ("felucca", "fm-1"))]
        if not names:
            sys.exit("no FM-1 MIDI port")
        self.out = mido.open_output(names[0])
        self.mido = mido
        devs = [i for i, d in enumerate(sd.query_devices())
                if "felucca" in d["name"].lower() and d["max_input_channels"] >= 2]
        if not devs:
            sys.exit("no FM-1 audio input")
        self.dev = devs[0]

    def cc(self, name, v):
        self.out.send(self.mido.Message("control_change", control=CC[name], value=int(v)))

    def setup(self, **kv):
        for k, v in kv.items():
            self.cc(k, v)
        time.sleep(0.05)

    def dry(self, **kv):
        self.out.send(self.mido.Message("control_change", control=120, value=0))
        if not hasattr(self, "defaults"):
            self.defaults = param_defaults()
        for cc, v in self.defaults:                    # a known state: every parameter at its default
            self.out.send(self.mido.Message("control_change", control=cc, value=v))
        d = dict(DRY)
        d.update(kv)
        self.setup(**d)
        self.wait_silence()

    def wait_silence(self, limit=10.0):
        """until the output is exact zero (the gate), so no delay / reverb tail of the last test leaks in"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit:
            x = self.sd.rec(int(0.3 * FS), samplerate=FS, channels=2, device=self.dev, dtype="int16")
            self.sd.wait()
            if not np.any(x):
                return True
        return False

    def take(self, dur, events):
        """record dur s; events: [(t_s, kind, a, b)], kind on/off/cc/pc"""
        rec = self.sd.rec(int(dur * FS), samplerate=FS, channels=2, device=self.dev, dtype="int16")
        t0 = time.monotonic()
        for t, kind, a, b in sorted(events, key=lambda e: e[0]):
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            if kind == "on":
                self.out.send(self.mido.Message("note_on", note=a, velocity=b))
            elif kind == "off":
                self.out.send(self.mido.Message("note_off", note=a))
            elif kind == "cc":
                self.out.send(self.mido.Message("control_change", control=a, value=b))
            elif kind == "pc":
                self.out.send(self.mido.Message("program_change", program=a))
        self.sd.wait()
        return rec.astype(float)


def onset(x, thr=200):
    a = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    i = np.argmax(a > thr)
    return int(i) if a[i] > thr else -1


def f0_of(x, guess):
    """fundamental near guess (Hz): FFT peak, parabolic refinement"""
    n = len(x)
    w = np.hanning(n)
    sp = np.abs(np.fft.rfft(x * w, 8 * n))
    fr = np.fft.rfftfreq(8 * n, 1 / FS)
    m = (fr > guess * 0.94) & (fr < guess * 1.06)
    if not m.any():
        return 0.0
    i = np.flatnonzero(m)[np.argmax(sp[m])]
    if 0 < i < len(sp) - 1:
        a, b, c = np.log(sp[i - 1:i + 2] + 1e-12)
        p = 0.5 * (a - c) / (a - 2 * b + c)
        return (i + p) * FS / (8 * n)
    return fr[i]


def centroid(x):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return float((sp * fr).sum() / (sp.sum() + 1e-12))


def clicks(x):
    """dropout / click detector: second difference spikes far above the local level"""
    m = x.mean(axis=1) if x.ndim == 2 else x
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(480) / 480, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def zero_runs(x, lo=0.05, hi=None):
    """runs of exact zeros (>= 64 samples) inside a stretch that should be sounding"""
    m = np.abs(x).max(axis=1)
    z = (m == 0).astype(int)
    runs, n = 0, 0
    for v in z:
        n = n + 1 if v else 0
        if n == 64:
            runs += 1
    return runs


TESTS = {}


def test(fn):
    TESTS[fn.__name__] = fn
    return fn


@test
def silence(r, rep):
    r.dry()
    x = r.take(2.0, [])
    rep["silence"] = dict(peak=float(np.abs(x).max()), rms_db=db(x), nonzero=int((x != 0).sum()))
    ok = rep["silence"]["peak"] == 0
    return ok, f"peak {rep['silence']['peak']:.0f} LSB, {rep['silence']['nonzero']} non-zero samples"


@test
def latency(r, rep):
    r.dry()
    lat = []
    for k in range(5):
        x = r.take(0.8, [(0.2, "on", 72, 110), (0.5, "off", 72, 0)])
        i = onset(x)
        if i > 0:
            lat.append((i / FS - 0.2) * 1000)
    rep["latency_ms"] = lat
    if not lat:
        return False, "no onset"
    return True, f"MIDI -> USB audio {np.median(lat):.1f} ms (min {min(lat):.1f}, max {max(lat):.1f}); includes USB + host buffers"


@test
def keyboard(r, rep):
    r.dry()
    rows = []
    for n in range(28, 104):
        x = r.take(0.75, [(0.05, "on", n, 100), (0.6, "off", n, 0)])
        i = onset(x, 100)
        if i < 0:
            rows.append(dict(note=n, level=-240.0, cents=None, peak=0.0))
            continue
        seg = x[i + int(0.05 * FS):i + int(0.45 * FS), 0]
        f = A4 * 2 ** ((n - 69) / 12)
        fm = f0_of(seg, f)
        rows.append(dict(note=n, level=db(x[i:i + int(0.3 * FS)]), cents=1200 * np.log2(fm / f) if fm else None,
                         peak=float(np.abs(x).max()), centroid=centroid(seg)))
    rep["keyboard"] = rows
    lv = np.array([q["level"] for q in rows])
    jumps = [(rows[k]["note"], round(lv[k] - (lv[k - 1] + lv[k + 1]) / 2, 1)) for k in range(1, len(rows) - 1)
             if abs(lv[k] - (lv[k - 1] + lv[k + 1]) / 2) > 3]
    cents = [abs(q["cents"]) for q in rows if q["cents"] is not None]
    dead = [q["note"] for q in rows if q["level"] < -70]
    rep["keyboard_summary"] = dict(jumps=jumps, worst_cents=max(cents) if cents else None, dead=dead,
                                   level_span=float(lv.max() - lv[lv > -100].min()))
    ok = not dead and not jumps and (max(cents) < 15 if cents else False)
    return ok, (f"76 keys: level span {rep['keyboard_summary']['level_span']:.1f} dB, "
                f"uneven keys {jumps or 'none'}, worst tuning {max(cents):.1f} cents, silent {dead or 'none'}")


@test
def velocity(r, rep):
    r.dry()
    out = {}
    for n in (45, 57, 72, 84):
        lv, ce = [], []
        for v in range(8, 128, 8):
            x = r.take(0.6, [(0.05, "on", n, v), (0.45, "off", n, 0)])
            i = onset(x, 20)
            seg = x[max(i, 0):max(i, 0) + int(0.3 * FS), 0]
            lv.append(db(seg))
            ce.append(centroid(seg) if len(seg) else 0)
        out[n] = dict(level=lv, centroid=ce)
    rep["velocity"] = out
    bad = [n for n, d in out.items() if np.any(np.diff(d["level"]) < -0.5)]
    rng = {n: round(max(d["level"]) - min(d["level"]), 1) for n, d in out.items()}
    return not bad, f"level range per key {rng} dB; non-monotonic {bad or 'none'}"


@test
def types(r, rep):
    out = {}
    for t in range(4):
        r.dry(TYPE=t * 32 + 8)
        x = r.take(1.6, [(0.05, "on", 57, 100), (0.7, "off", 57, 0), (0.8, "on", 69, 100), (1.4, "off", 69, 0)])
        a = x[int(0.1 * FS):int(0.6 * FS), 0]
        b = x[int(0.85 * FS):int(1.3 * FS), 0]
        out[t] = dict(level=(db(a) + db(b)) / 2, centroid_a3=centroid(a), centroid_a4=centroid(b))
    rep["types"] = out
    lv = [out[t]["level"] for t in out]
    ce = [out[t]["centroid_a3"] for t in out]
    distinct = max(ce) / max(min(ce), 1) > 1.3
    return distinct and max(lv) - min(lv) < 3, ("levels " + ", ".join(f"{lv[t]:.1f}" for t in range(4)) +
                                                " dB; A3 centroid " + ", ".join(f"{ce[t]:.0f}" for t in range(4)) + " Hz")


CHORD = [(0.05, "on", n, 100) for n in (57, 64, 69)] + [(1.25, "off", n, 0) for n in (57, 64, 69)]
FX = [
    ("drive 64", dict(DRIVE=64)), ("drive 127", dict(DRIVE=127)),
    ("comp 64", dict(CMP_SW=127, CMP_AMT=64)), ("comp 127", dict(CMP_SW=127, CMP_AMT=127)),
    ("trem pan", dict(TW_SW=64, TW_DEPTH=100, TW_MODE=0)), ("trem am", dict(TW_SW=64, TW_DEPTH=100, TW_MODE=127)),
    ("wah", dict(TW_SW=127, TW_DEPTH=100)),
    ("phaser", dict(PH_SW=127, PH_DEPTH=100, PH_FDBK=64)), ("chorus", dict(CP_SW=64, CP_DEPTH=100)),
    ("delay dig", dict(DL_SW=64, DL_DEPTH=80, DL_TIME=64)), ("delay ana", dict(DL_SW=127, DL_DEPTH=80, DL_TIME=64)),
    ("reverb 64", dict(REV=64)), ("reverb 127", dict(REV=127)),
    ("everything", dict(DRIVE=40, CMP_SW=127, CMP_AMT=64, TW_SW=64, TW_DEPTH=64, PH_SW=127, PH_DEPTH=64,
                        CP_SW=64, CP_DEPTH=64, DL_SW=64, DL_DEPTH=64, REV=64)),
]


@test
def effects(r, rep):
    r.dry()
    ref = r.take(1.5, CHORD)
    i0 = max(onset(ref), 0)
    base = db(ref[i0:i0 + int(1.1 * FS)])
    base_direct = db(ref[i0:i0 + int(0.25 * FS)])
    rows, bad = [], []
    for name, kv in FX:
        r.dry(**kv)
        x = r.take(2.2, CHORD)
        i = max(onset(x), 0)
        timed = name.startswith(("delay", "reverb"))     # echoes / a tail add level by design: check the direct
        lv = db(x[i:i + int(0.25 * FS)]) - base_direct + base if timed else db(x[i:i + int(1.1 * FS)])
        clip = int((np.abs(x) >= 32700).sum())
        row = dict(name=name, delta_db=lv - base, peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12), clipped=clip,
                   lr_corr=float(np.corrcoef(x[i:i + FS, 0], x[i:i + FS, 1])[0, 1]) if i + FS < len(x) else 1.0,
                   tail_db=db(x[i + int(1.3 * FS):i + int(1.9 * FS)]), clicks=clicks(x))
        rows.append(row)
        row["with_tail_db"] = db(x[i:i + int(1.1 * FS)]) - base
        lim = 99 if name.startswith(("comp", "everything")) else 3   # a compressor's level depends on the playing
        if abs(row["delta_db"]) > lim or clip or row["clicks"]:
            bad.append(name)
    rep["effects"] = dict(dry_db=base, rows=rows)
    summary = "; ".join(f"{q['name']} {q['delta_db']:+.1f}" for q in rows)
    return not bad, (f"level vs dry (dB; delay/reverb: the direct sound; compressor: information): {summary}; "
                     f"problems: {bad or 'none'}")


@test
def bypass(r, rep):
    """the FX button (CC 111): with typical effects on vs bypassed, and bypassed vs no effects at all"""
    r.dry()
    a = r.take(1.5, CHORD)
    r.dry(PH_SW=127, PH_DEPTH=80, CP_SW=64, CP_DEPTH=64, REV=50, DRIVE=30, CMP_SW=127, CMP_AMT=50)
    b = r.take(2.2, CHORD)
    r.out.send(r.mido.Message("control_change", control=111, value=127))
    r.wait_silence()
    c = r.take(1.5, CHORD)
    r.out.send(r.mido.Message("control_change", control=111, value=0))
    lv = []
    for x in (a, b, c):
        i = max(onset(x), 0)
        lv.append(db(x[i:i + int(1.1 * FS)]))
    rep["bypass"] = dict(no_fx=lv[0], fx_on=lv[1], bypassed=lv[2])
    ok = abs(lv[2] - lv[0]) < 0.5 and abs(lv[1] - lv[2]) < 3
    return ok, f"no effects {lv[0]:.1f}, effects on {lv[1]:.1f}, bypassed {lv[2]:.1f} dBFS"


FXALL = [  # (name, settings, expected: LU re clean within +-tol)
    ("drive 20", dict(DRIVE=20)), ("drive 64", dict(DRIVE=64)), ("drive 127", dict(DRIVE=127)),
    ("comp on (-20 dB 4:1 +4.3)", dict(CMP_SW=127, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=23)),
    ("comp -40 dB 20:1 +4.3", dict(CMP_SW=127, CMP_AMT=127, CMP_RAT=127, CMP_GAIN=23)),
    ("trem pan 32", dict(TW_SW=64, TW_DEPTH=32)), ("trem pan 127", dict(TW_SW=64, TW_DEPTH=127)),
    ("trem am 64", dict(TW_SW=64, TW_DEPTH=64, TW_MODE=127)), ("trem am 127 square", dict(TW_SW=64, TW_DEPTH=127, TW_MODE=127, TW_SHAPE=127)),
    ("wah 32", dict(TW_SW=127, TW_DEPTH=32)), ("wah 127", dict(TW_SW=127, TW_DEPTH=127)),
    ("phaser 32", dict(PH_SW=127, PH_DEPTH=32)), ("phaser 80", dict(PH_SW=127, PH_DEPTH=80)),
    ("phaser 127 fb127", dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127)),
    ("chorus 32", dict(CP_SW=64, CP_DEPTH=32)), ("chorus 64", dict(CP_SW=64, CP_DEPTH=64)), ("chorus 127", dict(CP_SW=64, CP_DEPTH=127)),
    ("delay dig 50", dict(DL_SW=64, DL_DEPTH=50)), ("delay ana 127", dict(DL_SW=127, DL_DEPTH=127)),
    ("reverb 32", dict(REV=32)), ("reverb 127", dict(REV=127)),
    ("all on", dict(DRIVE=30, CMP_SW=127, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=23, TW_SW=64, TW_DEPTH=50, PH_SW=127, PH_DEPTH=60,
                    CP_SW=64, CP_DEPTH=50, DL_SW=64, DL_DEPTH=40, REV=40)),
]


def lufs(x):
    """ITU-R BS.1770 K-weighted loudness of a (n, 2) or (n,) float array at 48 kHz"""
    from scipy.signal import lfilter
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621]
    x = np.atleast_2d(np.asarray(x, float).T).T
    return -0.691 + 10 * np.log10(sum(np.mean(lfilter(b2, a2, lfilter(b1, a1, x[:, c])) ** 2) for c in range(x.shape[1])) + 1e-15)


@test
def fx_all(r, rep):
    """every effect at several settings: loudness (LUFS) re clean on USB (and the jack when an interface is there),
    stereo width, peak, clipping, clicks; a 4-note chord at v108 (the FM-1's own keys' velocity)"""
    jack = None
    try:
        import hil_jack as hj
        jack = hj.Jack("M6", 5)
        jack.r = r
    except Exception:
        pass
    ch = [(0.1, "on", n, 108) for n in (48, 55, 60, 64)] + [(2.1, "off", n, 0) for n in (48, 55, 60, 64)]
    def take():
        if jack:
            u, j = jack.both(2.6, ch)
            x = r.take(2.6, ch) if False else None
            return u, j
        x = r.take(2.6, ch) / 32768.0
        return x, None
    def seg(x):
        a = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
        i = int(np.argmax(a > 0.3 * a.max()))
        return x[i:i + int(1.8 * FS)]
    r.dry()
    if jack:
        u0 = r.take(2.6, ch) / 32768.0
        _, j0 = jack.both(2.6, ch)
    else:
        u0, j0 = take()
    base_u, base_j = lufs(seg(u0)), (lufs(seg(j0)) if j0 is not None else None)
    rows, bad = [], []
    for name, kv in FXALL:
        r.dry(**kv)
        u = r.take(2.6, ch) / 32768.0
        j = jack.both(2.6, ch)[1] if jack else None
        su = seg(u)
        row = dict(name=name, usb_lu=lufs(su) - base_u, peak_db=float(20 * np.log10(np.abs(u).max() + 1e-12)),
                   clipped=int((np.abs(u) >= 0.9995).sum()), clicks=clicks(u * 32768),
                   corr=float(np.corrcoef(su[:, 0], su[:, 1])[0, 1]))
        if j is not None:
            row["jack_lu"] = lufs(seg(j)) - base_j
            row["usbL_lu"] = lufs(su[:, 0]) - lufs(seg(u0)[:, 0])        # the jack cable carries the left channel
        wide = row["corr"] < 0.8
        tol = 99.0 if name.startswith(("comp -40", "all on")) else 2.0     # extreme comp: MAKEUP is the player's
        target = -2.5 if wide and name.startswith(("phaser", "chorus")) else 0.0   # width compensation (0.8)
        if abs(row["usb_lu"] - target) > tol or row["clipped"] or row["clicks"] or \
                ("jack_lu" in row and abs(row["jack_lu"] - row["usbL_lu"]) > 1.0):
            bad.append(name)
        rows.append(row)
        print(f"    {name:28s} USB {row['usb_lu']:+5.1f} LU" + (f"  jack {row['jack_lu']:+5.1f}" if "jack_lu" in row else "") +
              f"  L/R {row['corr']:.2f}  peak {row['peak_db']:6.1f}  clip {row['clipped']}  clicks {row['clicks']}", flush=True)
    # the FX bypass (CC 111) with everything on: back to the clean level (clean measured again right before)
    r.dry()
    base_u = lufs(seg(r.take(2.6, ch) / 32768.0))
    r.dry(**dict(FXALL[-1][1]))
    r.out.send(r.mido.Message("control_change", control=111, value=127))
    r.wait_silence()
    ub = r.take(2.6, ch) / 32768.0
    r.out.send(r.mido.Message("control_change", control=111, value=0))
    byp = lufs(seg(ub)) - base_u
    print(f"    {'FX bypass (all on)':28s} USB {byp:+5.1f} LU", flush=True)
    if abs(byp) > 0.5:
        bad.append("bypass")
    rep["fx_all"] = dict(rows=rows, bypass_lu=byp, base_usb_lufs=base_u)
    return not bad, f"{len(FXALL)} settings + bypass; out of tolerance: {bad or 'none'}"


TOGGLES = [("drive 64", "DRIVE", 64, 0), ("comp", "CMP_SW", 127, 0), ("trem pan", "TW_SW", 64, 0), ("wah", "TW_SW", 127, 0),
           ("phaser", "PH_SW", 127, 0), ("chorus", "CP_SW", 64, 0), ("delay", "DL_SW", 64, 0), ("reverb 50", "REV", 50, 0)]


@test
def fx_toggle(r, rep):
    """each effect switched on and off while a chord sounds (as the panel does, at its starting depth): the loudness
    step at each switch (0.4 s before vs 0.4 s after, the chord's own decay taken out) and clicks around it"""
    starts = dict(TW_DEPTH=64, PH_DEPTH=80, CP_DEPTH=64, DL_DEPTH=50, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=23)
    rows, bad = [], []
    ch = [(0.1, "on", n, 108) for n in (48, 55, 60, 64)] + [(3.4, "off", n, 0) for n in (48, 55, 60, 64)]
    def st(x, a, b):
        return lufs(x[int(a * FS):int(b * FS)] / 32768.0)
    r.dry(**starts)
    ref = r.take(3.6, ch)
    for name, key, on, off in TOGGLES:
        r.dry(**starts)
        x = r.take(3.6, ch + [(1.2, "cc", CC[key], on), (2.4, "cc", CC[key], off)])
        # the step at a switch, the chord's natural decay (ref) taken out
        up = (st(x, 1.25, 1.65) - st(x, 0.75, 1.15)) - (st(ref, 1.25, 1.65) - st(ref, 0.75, 1.15))
        dn = (st(x, 2.45, 2.85) - st(x, 1.95, 2.35)) - (st(ref, 2.45, 2.85) - st(ref, 1.95, 2.35))
        ck = clicks(x[int(1.1 * FS):int(1.4 * FS)]) + clicks(x[int(2.3 * FS):int(2.6 * FS)])
        rows.append(dict(name=name, on_step=up, off_step=dn, clicks=ck))
        lim = 3.5 if name in ("phaser", "chorus") else 2.0
        if abs(up) > lim or abs(dn) > lim or ck:
            bad.append(name)
        print(f"    {name:10s} on {up:+5.1f} LU   off {dn:+5.1f} LU   clicks {ck}", flush=True)
    rep["fx_toggle"] = rows
    return not bad, f"{len(TOGGLES)} effects switched on/off under a chord; out of tolerance: {bad or 'none'}"


@test
def delay_time(r, rep):
    r.dry(DL_SW=64, DL_DEPTH=100, DL_TIME=64, DL_FDBK=60)
    x = r.take(1.6, [(0.05, "on", 72, 110), (0.1, "off", 72, 0)])
    from scipy.signal import find_peaks
    env = np.sqrt(np.convolve(x[:, 0] ** 2, np.ones(480) / 480, "same"))
    i = onset(x)
    # echoes: envelope peaks at least 100 ms apart, standing out of the decay between them
    pk, _ = find_peaks(env[i:], distance=int(0.1 * FS), prominence=max(10.0, 0.05 * env[i:].max()))
    pk = [i + k for k in pk if k > int(0.05 * FS)]
    exp_ms = 20 * 74 ** (64 / 127)
    gaps = np.diff([i] + pk) / FS * 1000 if pk else []
    rep["delay"] = dict(expected_ms=exp_ms, echoes_ms=list(map(float, gaps)))
    ok = len(gaps) >= 2 and abs(gaps[0] - exp_ms) < 15
    return ok, f"expected {exp_ms:.0f} ms, echoes at {[round(g) for g in gaps[:4]]} ms"


@test
def tremolo_rate(r, rep):
    r.dry(TW_SW=64, TW_DEPTH=127, TW_MODE=127, TW_RATE=64)
    x = r.take(3.0, [(0.05, "on", 60, 100), (2.9, "off", 60, 0)])
    env = np.sqrt(np.convolve(x[:, 0] ** 2, np.ones(480) / 480, "same"))[int(0.3 * FS):int(2.8 * FS)]
    env = env / (np.convolve(env, np.ones(9600) / 9600, "same") + 1e-9) - 1
    sp = np.abs(np.fft.rfft(env * np.hanning(len(env)), 1 << 18))
    fr = np.fft.rfftfreq(1 << 18, 1 / FS)
    m = (fr > 0.3) & (fr < 15)
    f = float(fr[m][np.argmax(sp[m])])
    exp = 0.5 * 24 ** (64 / 127)
    rep["tremolo"] = dict(expected_hz=exp, measured_hz=f)
    return abs(f - exp) / exp < 0.15, f"expected {exp:.2f} Hz, measured {f:.2f} Hz"


@test
def sustain(r, rep):
    r.dry()
    a = r.take(2.0, [(0.05, "on", 60, 100), (0.5, "off", 60, 0)])
    r.dry()
    b = r.take(2.0, [(0.02, "cc", 64, 127), (0.05, "on", 60, 100), (0.5, "off", 60, 0)])
    r.setup(SUS=0)
    la, lb = db(a[int(1.0 * FS):int(1.5 * FS)]), db(b[int(1.0 * FS):int(1.5 * FS)])
    rep["sustain"] = dict(released_db=la, pedal_db=lb)
    return lb - la > 15, f"0.5 s after the key: damped {la:.1f} dB, with the pedal {lb:.1f} dB"


@test
def held_tail(r, rep):
    r.dry(REV=40)
    x = r.take(16.0, [(0.05, "on", 57, 100)])
    r.out.send(r.mido.Message("note_off", note=57))
    env = [db(x[int(t * FS):int((t + 0.5) * FS)]) for t in np.arange(0, 15.5, 0.5)]
    nz = np.flatnonzero(np.abs(x).max(axis=1) > 0)
    rep["held_tail"] = dict(env_db=env, last_nonzero_s=float(nz[-1] / FS) if len(nz) else 0.0,
                            hiss_8k_db=None)
    last = rep["held_tail"]["last_nonzero_s"]
    return last < 15.0, f"held A3 with reverb: output reaches exact zero at {last:.1f} s (gate)"


@test
def stress(r, rep):
    """8+ voices, fast repeats, pedal, all effects: dropouts show as clicks / zero runs"""
    r.dry(**dict(FX[-1][1]))
    ev = [(0.02, "cc", 64, 127)]
    t = 0.1
    chords = [(36, 48, 55, 60, 64, 67, 72, 76, 79, 84), (38, 50, 57, 62, 65, 69, 74, 77, 81, 86)]
    for k in range(16):
        for n in chords[k % 2]:
            ev.append((t, "on", n, 127))
        t += 0.25
        for n in chords[k % 2]:
            ev.append((t - 0.01, "off", n, 0))
    ev.append((t + 0.1, "cc", 64, 0))
    x = r.take(t + 1.5, ev)
    c, z = clicks(x[int(0.1 * FS):int(t * FS)]), zero_runs(x[int(0.2 * FS):int(t * FS)])
    rep["stress"] = dict(clicks=c, zero_runs=z, peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12),
                         clipped=int((np.abs(x) >= 32700).sum()))
    return c == 0 and z == 0, f"clicks {c}, zero runs {z}, peak {rep['stress']['peak_db']:.1f} dBFS, clipped {rep['stress']['clipped']}"


@test
def presets(r, rep):
    rows = []
    for p in range(12):
        r.out.send(r.mido.Message("control_change", control=120, value=0))
        r.out.send(r.mido.Message("program_change", program=p))
        r.wait_silence()
        x = r.take(2.0, CHORD)
        i = max(onset(x), 0)
        rows.append(dict(preset=p + 1, level=db(x[i:i + int(1.1 * FS)]),
                         peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12),
                         clipped=int((np.abs(x) >= 32700).sum()), clicks=clicks(x)))
    rep["presets"] = rows
    lv = [q["level"] for q in rows]
    bad = [q["preset"] for q in rows if q["clipped"] or q["clicks"]]
    r.out.send(r.mido.Message("program_change", program=0))
    return max(lv) - min(lv) < 8 and not bad, ("F01..F12 levels " + ", ".join(f"{v:.0f}" for v in lv) +
                                               f" dB (span {max(lv) - min(lv):.1f}); clipping/clicks {bad or 'none'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print(" ".join(TESTS))
        return 0
    names = a.only.split(",") if a.only else list(TESTS)
    os.makedirs(OUT, exist_ok=True)
    r = Rig()
    rep, lines, fails = {}, [], 0
    for n in names:
        t0 = time.time()
        try:
            ok, msg = TESTS[n](r, rep)
        except Exception as e:  # keep scanning
            ok, msg = False, f"error: {e!r}"
        fails += not ok
        line = f"{'ok  ' if ok else 'FAIL'}  {n:13s} {msg}  ({time.time() - t0:.0f} s)"
        print(line, flush=True)
        lines.append(line)
    r.dry()
    r.out.send(r.mido.Message("program_change", program=0))
    json.dump(rep, open(os.path.join(OUT, "report.json"), "w"), indent=1, default=float)
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    print(f"{fails} failed; report: build/hil/report.txt, report.json")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
