#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Hardware-in-the-loop scan of a connected FM-1 running Frodhes: MIDI in, USB audio back.

The FM-1's USB audio is exactly what its output plays (after MASTER), so this checks the real thing:
silence, latency, every key (level, tuning), velocity, the TYPEs, every effect (level against dry,
clipping, what it is for), the bypass, sustain, the held-note tail, polyphony stress (dropouts) and
every factory preset. Captures and a report go to build/hil/.

  tools/hil_scan.py [--only NAME[,NAME]] [--list]

The VIBES suites (not in the default run; every VIBES control at min / default / max with an audible-effect limit,
the effects' loudness jumps, recorded on USB and on the headphone jack through an interface at once, side by side):

  tools/hil_scan.py --vibes [--source usb|jack|both] [--jack-dev M6] [--jack-ch 5] [--wav]
  tools/hil_scan.py --only vibes_jump,vibes_fx ...      (vibes_jump vibes_voice vibes_decay vibes_motor vibes_vel
                                                         vibes_fx; report build/hil/vibes-report.md + .json)
  tools/hil_scan.py --vibes --dry-run                   the planned events and the time estimate; no MIDI / audio

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
          PH_SPEED2=14, CP_SPEED2=87, TW_SHAPE=22, DL_TONE=27, LEVEL=110, BYPASS=111, ARP=52, CHORD=56, SEQ=60, SQREC=63,
          FL_TYPE=70, FL_FREQ=74, FL_RESO=71, FL_LFO=72)
DRY = dict(DRIVE=0, REV=0, TW_SW=0, CP_SW=0, PH_SW=0, DL_SW=0, CMP_SW=0, RAW=0, SUS=0, ARP=0, CHORD=0, SQREC=0, SEQ=0,
           FL_TYPE=0)
# (ARP, CHORD, SEQ, SQREC: the arpeggiator, chord mode, the sequencer and its recording off)


RHODES_TYPES = 4           # TYPE 0..3 (ST77 SUIT FELT DYNO); 4.. are bar types (VIBES), scanned by their own tests


def n_types():
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    return int(re.search(r'\[P_TYPE\] = \{"[^"]*", (\d+),', src).group(1)) + 1


def type_cc(t):
    """the CC value selecting TYPE t on the CC that type_ccnum(t) names: CC 80 (by 32) for the Rhodes types 0..3 as
    before, CC 79 for the bar types (one band each over 0..127)"""
    if t < 4:
        return t * 32 + 8
    w = 128 / max(1, n_types() - 4)
    return int((t - 4) * w + w / 2)


def type_ccnum(t):
    return 80 if t < 4 else 79


def param_defaults():
    """every parameter's CC and the CC value that sets its default (parsed from firmware/src/params.c), so each test
    starts from the same state whatever the FM-1 had saved"""
    import re
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    out = []
    for name, hi, de, cc in re.findall(r'\[P_(\w+)\] = \{"[^"]*", (\d+), (\d+), F_\w+, (\d+)', src):
        hi, de, cc = int(hi), int(de), int(cc)
        if not cc:                                   # (records v3: a parameter without a CC yet)
            continue
        if name == "TYPE":
            v = type_cc(de)
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


def bands_db(x):
    """third-octave band levels (dB) 100 Hz .. 10 kHz of a mono signal"""
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / FS)
    c = 100 * 2 ** (np.arange(0, 21) / 3)
    return np.array([10 * np.log10(sp[(f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))].sum() + 1e-12) for fc in c])


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
    for t in range(RHODES_TYPES):                   # the Rhodes TYPEs (the bar types have their own tests)
        r.dry(TYPE=type_cc(t))
        x = r.take(1.6, [(0.05, "on", 57, 100), (0.7, "off", 57, 0), (0.8, "on", 69, 100), (1.4, "off", 69, 0)])
        a = x[int(0.1 * FS):int(0.6 * FS), 0]
        b = x[int(0.85 * FS):int(1.3 * FS), 0]
        out[t] = dict(level=(db(a) + db(b)) / 2, centroid_a3=centroid(a), centroid_a4=centroid(b),
                      bands=bands_db(np.concatenate([a, b])))
    rep["types"] = {t: {k: v for k, v in d.items() if k != "bands"} for t, d in out.items()}
    lv = [out[t]["level"] for t in out]
    # distinct: every pair of types differs in its spectral envelope (third-octave bands 100 Hz - 10 kHz, levels
    # aligned), by more than 1.5 dB RMS. One centroid ratio (> 1.3, the old limit) never held: the types differ in
    # their bell, bark and decay more than in their mean brightness.
    dist = [float(np.sqrt(np.mean(((out[i]["bands"] - out[i]["bands"].mean()) - (out[j]["bands"] - out[j]["bands"].mean())) ** 2)))
            for i in range(RHODES_TYPES) for j in range(i + 1, RHODES_TYPES)]
    rep["types_distance_db"] = dist
    return min(dist) > 1.5 and max(lv) - min(lv) < 3, ("levels " + ", ".join(f"{lv[t]:.1f}" for t in range(RHODES_TYPES)) +
                                                     " dB; spectral distance between types min " + f"{min(dist):.1f} dB (" +
                                                     ", ".join(f"{d:.1f}" for d in dist) + ")")


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
        lu = (lufs(x[i:i + int(0.25 * FS)] / 32768.0) - lufs(ref[i0:i0 + int(0.25 * FS)] / 32768.0) if timed else
              lufs(x[i:i + int(1.1 * FS)] / 32768.0) - lufs(ref[i0:i0 + int(1.1 * FS)] / 32768.0))
        clip = int((np.abs(x) >= 32700).sum())
        row = dict(name=name, delta_db=lv - base, delta_lu=lu, peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12), clipped=clip,
                   lr_corr=float(np.corrcoef(x[i:i + FS, 0], x[i:i + FS, 1])[0, 1]) if i + FS < len(x) else 1.0,
                   tail_db=db(x[i + int(1.3 * FS):i + int(1.9 * FS)]), clicks=clicks(x))
        rows.append(row)
        row["with_tail_db"] = db(x[i:i + int(1.1 * FS)]) - base
        lim = 99 if name.startswith(("comp", "everything")) else 3   # a compressor's level depends on the playing
        # loudness, not RMS: the wah's resonance adds +4.5 dB RMS in the mids but +0.2 LU (fx_all) to the ear
        wide = row["lr_corr"] < 0.8
        target = -2.5 if wide and name.startswith(("phaser", "chorus")) else 0.0   # width compensation (0.8)
        if abs(row["delta_lu"] - target) > lim or clip or row["clicks"]:
            bad.append(name)
    rep["effects"] = dict(dry_db=base, rows=rows)
    summary = "; ".join(f"{q['name']} {q['delta_lu']:+.1f}" for q in rows)
    return not bad, (f"loudness vs dry (LU; delay/reverb: the direct sound; compressor: information): {summary}; "
                     f"problems: {bad or 'none'}")


@test
def bypass(r, rep):
    """the FX button (CC 111): with typical effects on vs bypassed, and bypassed vs no effects at all"""
    r.dry()
    a = r.take(1.5, CHORD)
    r.dry(PH_SW=127, PH_DEPTH=80, CP_SW=64, CP_DEPTH=64, REV=50, DRIVE=30, CMP_SW=127, CMP_AMT=50, CMP_GAIN=15)
    b = r.take(2.2, CHORD)
    r.out.send(r.mido.Message("control_change", control=111, value=127))
    r.wait_silence()
    c = r.take(1.5, CHORD)
    r.out.send(r.mido.Message("control_change", control=111, value=0))
    lv = []
    for x in (a, b, c):
        i = max(onset(x), 0)
        lv.append(lufs(x[i:i + int(1.1 * FS)] / 32768.0))
    rep["bypass"] = dict(no_fx=lv[0], fx_on=lv[1], bypassed=lv[2])
    # the compressor as the panel switches it on (THRESH up: +2.8 dB makeup); before, it ran with no makeup and the
    # effects-on level sat 5 dB under the bypass by construction
    ok = abs(lv[2] - lv[0]) < 0.5 and abs(lv[1] - lv[2]) < 3
    return ok, f"no effects {lv[0]:.1f}, effects on {lv[1]:.1f}, bypassed {lv[2]:.1f} LUFS"


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
        if name.startswith("delay"):                  # the echoes add loudness by design (full mix: about +3 LU)
            target, tol = 1.5, 3.0
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
    starts = dict(TW_DEPTH=64, PH_DEPTH=80, CP_DEPTH=64, DL_DEPTH=50, CMP_AMT=64, CMP_RAT=64, CMP_GAIN=15)
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
        lim = 3.5 if name in ("phaser", "chorus", "wah") else 4.0 if name == "comp" else 2.0   # wah: a moving
        # resonance (0.4 s windows catch its sweep); comp: the +2.8 dB makeup plus its attack / release
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
        rows.append(dict(preset=p + 1, level=lufs(x[i:i + int(1.1 * FS)] / 32768.0), rms_db=db(x[i:i + int(1.1 * FS)]),
                         peak_db=20 * np.log10(np.abs(x).max() / 32767 + 1e-12),
                         clipped=int((np.abs(x) >= 32700).sum()), clicks=clicks(x)))
    rep["presets"] = rows
    lv = [q["level"] for q in rows]
    bad = [q["preset"] for q in rows if q["clipped"] or q["clicks"]]
    r.out.send(r.mido.Message("program_change", program=0))
    return max(lv) - min(lv) < 8 and not bad, ("F01..F12 loudness " + ", ".join(f"{v:.0f}" for v in lv) +
                                               f" LUFS (span {max(lv) - min(lv):.1f}); clipping/clicks {bad or 'none'}")


# ==================================================================================================================
# VIBES: every control on the FM-1, each heard (device suites). Not in the default run: --vibes, or --only vibes_*.
#
# Every take records the USB audio and / or the headphone jack through an interface input (--source usb|jack|both,
# default both; the jack is --jack-dev "M6" input --jack-ch 5, as tools/hil_jack.py). The levels and spectra are
# judged on the USB's LEFT channel (what the jack cable carries) and on the jack, side by side; a level difference
# that the two disagree on by more than MISMATCH_DB is flagged. Stereo-only measures (pan, width, the reverb's
# pre-delay through its side signal) are USB only. Every take is also checked for full-scale samples on USB and for
# clicks (hil_scan's clicks(), outside 3 ms before / 17 ms after each note event as tools/bar_scan.py S1).
# The thresholds: tests/py/test_bars_cards_audible.py (VOICE), tests/py/test_vibes_controls_audible.py (DECAY,
# MOTOR, velocity), tools/bar_scan.py S9 (key evenness), hil_scan fx_all / fx_toggle (the effects), the task's
# 1.5 LU loudness-jump flag. The expected rates and times come from firmware/gen/fx_tables.h, the CCs from params.c.
# ==================================================================================================================
MISMATCH_DB = 1.0              # USB (left) vs jack: a level change both must agree on
JUMP_LU = 1.5                  # an effect switched on at its default settings: loudness step flagged above this
VIBES_CC79 = 64                # CC 79 = 64: TYPE VIBES (the bar types' bands over 0..127)
RESET_S, TAKE_S = 1.2, 0.3     # the time estimate: a reset (CCs, settle, wait_silence) and a take's stream overhead
VTESTS = {}
VDEBUG = False                 # --dry-run: measurement exceptions are printed
_VP, _FXT, _SPEC = None, {}, None


def vtest(fn):
    VTESTS[fn.__name__] = fn
    return fn


def vp():
    """every parameter in params.c PARAMS[]: name -> dict(cc, hi, de, fmt); the CCs are read here, never typed in"""
    global _VP
    if _VP is None:
        import re
        src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
        _VP = {n: dict(cc=int(c), hi=int(h), de=int(d), fmt=f)
               for n, h, d, f, c in re.findall(r'\[P_(\w+)\] = \{"[^"]*", (\d+), (\d+), (F_\w+), (\d+)', src)}
        names = re.search(r'N_SYNC\[\] = \{([^}]*)\}', src).group(1)
        _VP["_SYNC"] = re.findall(r'"([^"]*)"', names)
    return _VP


def sync_idx(name):
    return vp()["_SYNC"].index(name)


def fx_table(name):
    if name not in _FXT:
        import re
        src = open(os.path.join(ROOT, "firmware", "gen", "fx_tables.h")).read()
        m = re.search(r'%s\[\d+\] = \{([^}]*)\}' % name, src)
        _FXT[name] = [int(v) for v in re.findall(r'-?\d+', m.group(1))]
    return _FXT[name]


def lfo_hz(v, half=False):
    """F_HZ (tremolo, filter LFO, motor) and F_HZH (chorus, phaser: half): FX_LFO_INC[v] * 44100 / 2^32"""
    hz = fx_table("FX_LFO_INC")[int(v)] * 44100 / 2 ** 32
    return hz / 2 if half else hz


def mot_hz(v):
    return lfo_hz(min(int(v), 120))            # params.c MOT_RATE_V: 120 and up = 10.1 Hz


def dly_ms(v):
    return fx_table("FX_DLY_T")[int(v)] * 1000 / 44100


def pdly_ms(v):
    return (int(v) * 3528 // 127) * 1000 / 44100   # fx.c FX_PDLY_SAMPLES


def lvl_db(v):
    t = fx_table("FX_LVL")
    return 20 * np.log10(t[int(v)] / t[64])


def bspec():
    """the bar spec (docs/model/data/bars_spec.json, as tools/bar_cards.py): midi -> {mode name: Hz}"""
    global _SPEC
    if _SPEC is None:
        d = json.load(open(os.path.join(ROOT, "docs", "model", "data", "bars_spec.json")))
        _SPEC = {k["midi"]: {m["name"]: m["f"] for m in k["modes"]} for k in d["keys"]}
    return _SPEC


def f1(n):
    return bspec().get(n, {}).get("V1") or A4 * 2 ** ((n - 69) / 12)


def ccv(name, x):
    """the CC value that sets parameter name to x (its index for an enum / switch, else 0..127), as midi_cc() reads it"""
    p = vp()[name]
    if name == "CP_SW":                        # CC 85, the reface CHO/PHA switch: 43..85 chorus (86..: the phaser)
        return 64 if x else 0
    if p["hi"] >= 127:
        return int(x)
    if p["hi"] == 2:
        return (0, 64, 127)[int(x)]
    return min(127, (int(x) * 128 + 64) // (p["hi"] + 1))


def ccs(settings):
    return [(vp()[n]["cc"], ccv(n, x)) for n, x in settings]


def with_(base, *kv):
    """base settings with some replaced in place (order kept: CC 85 before the chorus' 86 / 87), others appended"""
    out = list(base)
    for k, v in kv:
        for i, (n, _) in enumerate(out):
            if n == k:
                out[i] = (k, v)
                break
        else:
            out.append((k, v))
    return out


# ---------------------------------------------------------------- the rig: USB and the jack at once
class Take:
    def __init__(self, tag, dur, ev, usb, jack):
        self.tag, self.dur, self.ev, self.usb, self.jack = tag, dur, ev, usb, jack
        self.sched = [t for t, k, a, b in ev if k == "on"]
        self._on = {}

    def srcs(self):
        return [s for s, x in (("usb", self.usb), ("jack", self.jack)) if x is not None]

    def m(self, src):
        return self.usb[:, 0] if src == "usb" else self.jack

    def on(self, src):
        """the onsets (samples) of the note-ons, in event order"""
        if src not in self._on:
            self._on[src] = align(self.m(src), self.sched)
        return self._on[src][0]

    def lat(self, src):
        self.on(src)
        return self._on[src][1]

    def at(self, src, t):
        """a sample index for event time t (s) in this source"""
        return int(round((t + self.lat(src)) * FS))


def align(m, sched, lat_max=0.3):
    """onsets of the scheduled note-ons: the first sound (5 % of the take's peak) belongs to the last strike due
    before it (within lat_max): that gives the latency; each onset is then refined to a 6 dB rise within +-8 ms"""
    if not sched:
        return [], 0.0
    a = np.abs(m)
    mx = float(a.max()) if len(a) else 0.0
    if mx <= 0:
        return [int(s * FS) for s in sched], 0.0
    i = int(np.argmax(a > 0.05 * mx))
    t = i / FS
    ks = [k for k, s in enumerate(sched) if s - 0.01 <= t <= s + lat_max]
    k = ks[-1] if ks else int(np.argmin([abs(t - s) for s in sched]))
    lat = t - sched[k]
    out = []
    w = FS // 1000
    for s in sched:
        j = int(round((s + lat) * FS))
        a0, a1 = max(0, j - 10 * w), min(len(m), j + 8 * w)
        x = m[a0:a1]
        nf = len(x) // w
        if nf > 3:
            e = np.sqrt(np.mean(x[:nf * w].reshape(nf, w) ** 2, axis=1)) + 1e-9
            r = np.flatnonzero(e[2:] > 2 * e[:-2])
            if len(r):
                j = a0 + (int(r[0]) + 1) * w
        out.append(j)
    return out, lat


def synth(dur, ev):
    """--dry-run: a stand-in for the FM-1 (bar-like partials 1 / 4 / 10, damped at the key-up unless the pedal is
    down), so every measurement runs; its numbers mean nothing"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    x = np.zeros(n)
    ped = [(e[0], e[3]) for e in ev if e[1] == "cc" and e[2] == 64]
    for k, (t0, kind, note, vel) in enumerate(ev):
        if kind != "on":
            continue
        offs = [e[0] for e in ev[k + 1:] if e[1] == "off" and e[2] == note]
        toff = offs[0] if offs else dur
        held = any(v >= 64 for tp, v in ped if tp <= toff)
        f = A4 * 2 ** ((note - 69) / 12)
        s = (t >= t0 + 0.004)
        tt = t[s] - t0
        env = 0.15 * (vel / 127) ** 2 * np.exp(-tt / 2.5)
        if not held:
            env = env * np.where(t[s] > toff, np.exp(-np.clip(t[s] - toff, 0, None) / 0.05), 1.0)
        x[s] += env * sum(a * np.sin(2 * np.pi * f * h * tt) for h, a in ((1, 1.0), (4, 0.3), (10, 0.05)))
    rng = np.random.default_rng(len(ev) + n)
    L = np.round(np.clip(x + 1e-5 * rng.standard_normal(n), -1, 32767 / 32768) * 32768) / 32768
    R = np.round(np.clip(0.97 * x + 1e-5 * rng.standard_normal(n), -1, 32767 / 32768) * 32768) / 32768
    return np.stack([L, R], 1), 0.35 * x + 2e-5 * rng.standard_normal(n)


class VRig:
    def __init__(self, source="both", jack_dev="M6", jack_ch=5, dry_run=False, wav=False):
        self.source, self.dry_run, self.wav = source, dry_run, wav
        self.want_usb, self.want_jack = source in ("usb", "both"), source in ("jack", "both")
        self.plan_s, self.takes, self.r, self.jack_name = 0.0, [], None, None
        if dry_run:
            return
        self.r = Rig()
        self.sd = self.r.sd
        if self.want_jack:
            devs = self.sd.query_devices()
            js = [i for i, d in enumerate(devs) if d["name"] == jack_dev and d["max_input_channels"] > 0] or \
                 [i for i, d in enumerate(devs) if jack_dev.lower() in d["name"].lower() and d["max_input_channels"] > 0]
            if not js:
                sys.exit(f"no '{jack_dev}' audio input for the headphone jack: connect it, or --source usb")
            self.jdev, self.jn, self.jch = js[0], devs[js[0]]["max_input_channels"], jack_ch - 1
            self.jack_name = f"{devs[js[0]]['name']} input {jack_ch}"
            if not 0 <= self.jch < self.jn:
                sys.exit(f"--jack-ch {jack_ch}: '{jack_dev}' has {self.jn} inputs")

    def sources(self):
        return [s for s, w in (("usb", self.want_usb), ("jack", self.want_jack)) if w]

    def send(self, cc, v):
        if self.r:
            self.r.out.send(self.r.mido.Message("control_change", control=int(cc), value=int(v)))

    def reset(self, settings=(), motor=False, label=""):
        """CC 120, every parameter at its default (TYPE not touched), DRY, VIBES (CC 79), the motor (CC 47), then the
        settings in order; waits for the gate's exact silence"""
        self.plan_s += RESET_S
        sets = ccs(settings)
        if self.dry_run:
            print(f"  reset{' [' + label + ']' if label else ''}: defaults + DRY, CC 79={VIBES_CC79}, CC 47="
                  f"{127 if motor else 0}" + "".join(f", {n}={x} (CC {c}={v})" for (n, x), (c, v) in zip(settings, sets)))
            return
        self.send(120, 0)
        if not hasattr(self, "defaults"):
            self.defaults = [(c, v) for c, v in param_defaults() if c not in (79, 80)]
        for c, v in self.defaults:
            self.send(c, v)
        for k, v in DRY.items():
            self.r.cc(k, v)
        self.send(79, VIBES_CC79)
        self.send(vp()["MOT_ON"]["cc"], 127 if motor else 0)
        for c, v in sets:
            self.send(c, v)
        time.sleep(0.3)
        self.r.wait_silence()

    def take(self, tag, dur, events):
        ev = sorted(events, key=lambda e: e[0])
        self.plan_s += dur + TAKE_S
        if self.dry_run:
            print(f"    take {tag} ({dur:.1f} s): " + "; ".join(
                f"{t:.2f} {k} {a}" + (f" v{b}" if k == "on" else f"={b}" if k == "cc" else "") for t, k, a, b in ev))
            u, j = synth(dur, ev)
            u, j = (u if self.want_usb else None), (j if self.want_jack else None)
        else:
            u, j = self._rec(dur, ev)
        T = Take(tag, dur, ev, u, j)
        self.takes.append(take_stats(T))
        if self.wav:
            save_wav(T)
        return T

    def _rec(self, dur, ev):
        """both inputs by callback while the events go out on time (one thread: the sends are not held up by reads)"""
        sd, n = self.sd, int(dur * FS)
        bufs, pos, streams = {}, {}, []

        def mk(key, ch, dt):
            bufs[key] = np.zeros((n, ch), dt)
            pos[key] = 0

            def cb(indata, frames, ti, status):
                p = pos[key]
                k = min(frames, n - p)
                if k > 0:
                    bufs[key][p:p + k] = indata[:k]
                    pos[key] = p + k
            return cb
        if self.want_usb:
            streams.append(sd.InputStream(device=self.r.dev, samplerate=FS, channels=2, dtype="int16",
                                          callback=mk("usb", 2, np.int16)))
        if self.want_jack:
            streams.append(sd.InputStream(device=self.jdev, samplerate=FS, channels=self.jn, dtype="float32",
                                          callback=mk("jack", self.jn, np.float32)))
        for s in streams:
            s.start()
        M = self.r.mido.Message
        t0 = time.monotonic()
        for t, kind, a, b in ev:
            while time.monotonic() - t0 < t:
                time.sleep(0.0005)
            if kind == "on":
                self.r.out.send(M("note_on", note=a, velocity=b))
            elif kind == "off":
                self.r.out.send(M("note_off", note=a))
            elif kind == "cc":
                self.r.out.send(M("control_change", control=a, value=b))
        while any(pos[k] < n for k in pos) and time.monotonic() - t0 < dur + 5:
            time.sleep(0.01)
        for s in streams:
            s.stop()
            s.close()
        u = bufs["usb"].astype(float) / 32768 if self.want_usb else None
        j = bufs["jack"][:, self.jch].astype(float) if self.want_jack else None
        return u, j

    def finish(self):
        if self.r:
            self.reset()
            self.r.out.send(self.r.mido.Message("program_change", program=0))


def save_wav(T):
    import wave
    d = os.path.join(OUT, "vibes")
    os.makedirs(d, exist_ok=True)
    for s, x in (("usb", T.usb), ("jack", T.jack)):
        if x is None:
            continue
        x = np.atleast_2d(np.asarray(x).T).T
        if s == "jack":
            x = x / (np.abs(x).max() + 1e-12) * 0.5
        w = wave.open(os.path.join(d, f"{T.tag}-{s}.wav"), "wb")
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(np.clip(np.round(x * 32768), -32768, 32767).astype("<i2").tobytes())
        w.close()


def clicks_guarded(m16, guards, before_ms=3, after_ms=17):
    """hil_scan's clicks() detector (the same formula) with bar_scan S1's guards around the note events"""
    d2 = np.abs(np.diff(m16, 2))
    lvl = np.convolve(np.abs(m16), np.ones(480) / 480, "same")[1:-1] + 50
    hit = np.flatnonzero(d2 > 6 * lvl + 2000) + 1
    ok = np.ones(len(m16), bool)
    for i in guards:
        ok[max(0, i - before_ms * FS // 1000):max(0, i + after_ms * FS // 1000)] = False
    return int(ok[hit].sum())


def take_stats(T):
    """every take: full-scale samples on USB (-32768 / +32767), the peak, clicks on each channel / the jack"""
    st = dict(tag=T.tag, dur=T.dur)
    notes = [t for t, k, a, b in T.ev if k in ("on", "off")]
    if T.usb is not None:
        raw = np.round(T.usb * 32768)
        st["usb_fs"] = int(((raw >= 32767) | (raw <= -32768)).sum())
        st["usb_near"] = int((np.abs(raw) >= 32700).sum())
        st["usb_peak_db"] = float(20 * np.log10(np.abs(T.usb).max() + 1e-12))
        g = [T.at("usb", t) for t in notes]
        st["usb_clicks"] = clicks_guarded(raw[:, 0], g) + clicks_guarded(raw[:, 1], g)
        st["usb_clicks_raw"] = clicks(raw[:, 0]) + clicks(raw[:, 1])
    if T.jack is not None:
        st["jack_peak_db"] = float(20 * np.log10(np.abs(T.jack).max() + 1e-12))
        st["jack_over"] = int((np.abs(T.jack) >= 0.999).sum())
        ref = T.usb[:, 0] if T.usb is not None else None
        gain = (np.sqrt(np.mean(ref ** 2)) / (np.sqrt(np.mean(T.jack ** 2)) + 1e-12)) if ref is not None else 1.0
        st["jack_clicks"] = clicks_guarded(T.jack * gain * 32768, [T.at("jack", t) for t in notes])
    return st


# ---------------------------------------------------------------- measures (FS 48 kHz; mono unless named)
def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        if VDEBUG:
            import traceback
            traceback.print_exc()
        return None


def rdb(x):
    x = np.asarray(x, float)
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12)) if x.size else float("nan")


def cut(m, i, a, b):
    return m[max(0, i + int(a * FS)):max(0, i + int(b * FS))]


def lu(x):
    return float(lufs(x))


def cen(x):
    return centroid(np.asarray(x, float))


def third(x):
    """1/3-octave band levels (dB), 50 Hz .. 15.9 kHz (tools/bar_cards.py BANDS)"""
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    return np.array([10 * np.log10(S[(fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))].sum() + 1e-30)
                     for c in [50 * 2 ** (i / 3) for i in range(26)]])


def sdist(b1, b2):
    """tools/bar_cards.py dist(): level-aligned spectral distance, dB RMS (3 dB = a clear timbre change)"""
    b1, b2 = np.asarray(b1), np.asarray(b2)
    t1, t2 = 10 * np.log10(np.sum(10 ** (b1 / 10))), 10 * np.log10(np.sum(10 ** (b2 / 10)))
    r1, r2 = b1 - t1, b2 - t2
    fl = max(r1.max(), r2.max()) - 60.0
    use = (r1 > fl) | (r2 > fl)
    d = np.maximum(r1, fl) - np.maximum(r2, fl)
    return float(np.sqrt(np.mean(d[use] ** 2)))


def pk_db(x, f, rel=0.03):
    nf = 1 << max(15, int(np.ceil(np.log2(len(x)))) + 2)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)
    return float(10 * np.log10(S[(fr >= (1 - rel) * f) & (fr <= (1 + rel) * f)].sum() + 1e-30))


def strike_feats(m, i, n):
    """tools/bar_cards.py features() at 48 kHz: LUFS 0..1 s, attack centroid 0..0.3 s, V2 / V3 re V1 (5..300 ms),
    bands 0..0.5 s"""
    sp = bspec().get(n, {})
    att = cut(m, i, 0.005, 0.3)
    l1 = pk_db(att, sp.get("V1", f1(n)))
    return dict(lufs=lu(cut(m, i, 0, 1.0)), cen_a=cen(cut(m, i, 0, 0.3)),
                v2=pk_db(att, sp["V2"]) - l1 if "V2" in sp else float("nan"),
                v3=pk_db(att, sp["V3"]) - l1 if "V3" in sp else float("nan"), bands=third(cut(m, i, 0, 0.5)))


def partial_trace(x, f, n=4096, hop=480):
    """a partial's level (dB) in frames: the peak within +-6 % of f"""
    w = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = np.abs(fr - f) <= max(0.06 * f, fr[1])          # (at least the nearest bins: short frames, low keys)
    idx = np.arange(0, len(x) - n, hop)
    out = np.array([20 * np.log10(np.abs(np.fft.rfft(x[i:i + n] * w))[sel].max() / (n / 4) + 1e-12) for i in idx])
    return out, (idx + n / 2) / FS


def t60(x, f, hi=6.0, lo=36.0):
    """tests/py/test_vibes_controls_audible.py t60(): the partial's T60 (s) fitted from 6 to 36 dB under its peak"""
    e, t = partial_trace(x, f)
    ip = int(np.argmax(e))
    a = np.where((np.arange(len(e)) > ip) & (e < e[ip] - hi))[0]
    b = np.where((np.arange(len(e)) > ip) & (e < e[ip] - lo))[0]
    i0, i1 = a[0], (b[0] if len(b) else len(e) - 1)
    return float(-60.0 / np.polyfit(t[i0:i1], e[i0:i1], 1)[0])


def release_rate(x, f, t_off):
    """test_vibes_controls_audible after_release(): the partial's decay (dB/s) 30..300 ms after the key-up"""
    e, t = partial_trace(x, f, hop=240)
    k = (t >= t_off + 0.03) & (t <= t_off + 0.3) & (e > e.max() - 100)
    return float(-np.polyfit(t[k], e[k], 1)[0])


def excess_db(x, ref):
    """what x adds to ref (two takes of one stimulus, the same window): the per-bin power above ref's (+1 dB of
    take-to-take slack), as a mean-square level (dB). The engine is deterministic apart from NOISE, so the tonal
    bins cancel without a sample-exact alignment (the device's takes are not aligned to the sample)."""
    nn = min(len(x), len(ref))
    w = np.hanning(nn)
    P = np.abs(np.fft.rfft(x[:nn] * w)) ** 2
    Q = np.abs(np.fft.rfft(ref[:nn] * w)) ** 2
    ex = np.maximum(P - 1.26 * Q, 0).sum() * 2 / (nn * (w * w).sum())
    return float(10 * np.log10(ex + 1e-24))


def ms_db(x):
    w = np.hanning(len(x))
    return float(10 * np.log10((np.abs(np.fft.rfft(x * w)) ** 2).sum() * 2 / (len(x) * (w * w).sum()) + 1e-24))


def hr():
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    return hostrun


def env_db(x, hop=240):
    n = len(x) // hop
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x[:n * hop], float).reshape(n, hop) ** 2, axis=1)) + 1e-9)


def pfit(tr, fps, rate, H=3):
    """a periodic wave (H harmonics of rate) on a quadratic trend: peak-to-peak of the periodic part, the harmonics'
    amplitudes, and the share of the detrended variance the fundamental explains (r2)"""
    tr = np.asarray(tr, float)
    t = np.arange(len(tr)) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    base = [np.ones_like(t), tn, tn * tn]
    A = np.column_stack(base + [g(2 * np.pi * h * rate * t) for h in range(1, H + 1) for g in (np.cos, np.sin)])
    c = np.linalg.lstsq(A, tr, rcond=None)[0]
    per = A[:, 3:] @ c[3:]
    det = tr - np.column_stack(base) @ np.linalg.lstsq(np.column_stack(base), tr, rcond=None)[0]
    h1 = A[:, 3:5] @ c[3:5]
    return dict(pp=float(per.max() - per.min()), amps=[float(np.hypot(c[3 + 2 * k], c[4 + 2 * k])) for k in range(H)],
                r2=float(np.var(h1) / (np.var(det) + 1e-12)))


def rate_of(tr, fps, guess, lo=0.4, hi=2.6):
    return float(hr().rate_of(np.asarray(tr, float), fps, guess, lo=lo, hi=hi))


def stft(x, n=1024, hop=240):
    w = np.hanning(n)
    idx = np.arange(0, len(x) - n, hop)
    F = np.abs(np.fft.rfft(np.stack([x[i:i + n] * w for i in idx]), axis=1)) ** 2
    return F, np.fft.rfftfreq(n, 1 / FS), FS / hop


def cen_trace(x):
    F, fr, fps = stft(x)
    s = (fr > 100) & (fr < 12000)
    return (F[:, s] * fr[s]).sum(1) / (F[:, s].sum(1) + 1e-30), fps


def motion_db(x):
    """spectral motion: 1/3-octave bands (250 Hz .. 10 kHz) in 5 ms frames, each band's level with its quadratic
    trend (the notes' decay) taken out: the mean standard deviation (dB) over the bands within 40 dB of the
    loudest. A swept filter / phaser / chorus moves it, a static sound leaves it near 0."""
    F, fr, fps = stft(x)
    rows = []
    for c in [250 * 2 ** (i / 3) for i in range(17)]:
        s = (fr >= c * 2 ** (-1 / 6)) & (fr < c * 2 ** (1 / 6))
        if s.any():
            rows.append(10 * np.log10(F[:, s].sum(1) + 1e-30))
    B = np.array(rows)
    t = np.arange(B.shape[1]) / fps
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    A = np.column_stack([np.ones_like(t), tn, tn * tn])
    R = B - (A @ np.linalg.lstsq(A, B.T, rcond=None)[0]).T
    keep = B.mean(1) > B.mean(1).max() - 40
    return float(R[keep].std(1).mean())


def hd_db(x, f):
    """harmonic distortion: H2 + H3 + H5 + H6 re H1 (dB); a bar's own partials sit at 4x and 10x, not there"""
    nf = 1 << (int(np.ceil(np.log2(len(x)))) + 1)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1 / FS)

    def p(g):
        return S[(fr > g * 0.98) & (fr < g * 1.02)].sum()
    return float(10 * np.log10(sum(p(k * f) for k in (2, 3, 5, 6)) / (p(f) + 1e-30) + 1e-30))


def env_lin(x, w=96, hop=48):
    return np.sqrt(np.convolve(x * x, np.ones(w) / w, "same"))[::hop]       # 1 ms frames


def echo_gap_ms(x, exp_ms=None):
    """the first echo after the direct sound (x starts at the onset): envelope peaks as hil_scan delay_time()"""
    from scipy.signal import find_peaks
    e = env_lin(x)
    p0 = int(np.argmax(e[:50]))                                 # the direct sound's peak (ms)
    dist = max(8, int(0.6 * exp_ms)) if exp_ms else 100
    pk, _ = find_peaks(e, distance=dist, prominence=max(1e-9, 0.05 * e.max()))
    pk = [p for p in pk if p > p0 + 8]
    return float(pk[0] - p0) if pk else float("nan")


def echo_levels(x, T_ms, kmax=10):
    """the echoes' levels (dB re the direct sound's peak) at k * T, k = 1 .. kmax"""
    e = env_lin(x)
    p0 = int(np.argmax(e[:50]))
    d = e[p0] + 1e-12
    out = []
    for k in range(1, kmax + 1):
        c = p0 + int(k * T_ms)
        a, b = c - max(10, int(0.1 * T_ms)), c + max(15, int(0.1 * T_ms))
        if b >= len(e):
            break
        out.append(float(20 * np.log10(e[max(0, a):b].max() / d + 1e-12)))
    return out


def n_echoes(lv, thr=-40.0):
    n = 0
    for v in lv:
        if v < thr:
            break
        n += 1
    return n


def tail_t60(x):
    """x starts 0.2 s after the key-up: the tail's T60 (s) from its envelope (20 ms), fitted down 30 dB"""
    e = env_db(x, 960)
    e0 = e[0]
    k = np.flatnonzero(e < e0 - 30)
    n = int(k[0]) if len(k) else len(e)
    n = max(n, 4)
    t = np.arange(n) * 0.02
    return float(-60.0 / np.polyfit(t, e[:n], 1)[0])


def side_onset_ms(st, i):
    """the reverb's arrival: the side signal (L - R; the dry VIBES is mono) re the onset, ms"""
    s = (st[:, 0] - st[:, 1]) / 2
    e = env_lin(s[i:i + int(0.5 * FS)])
    k = np.flatnonzero(e > 0.1 * e.max())
    return float(k[0]) if len(k) and e.max() > 1e-7 else float("nan")


def corr_lr(st):
    return float(np.corrcoef(st[:, 0], st[:, 1])[0, 1])


def finite(v):
    try:
        return all(np.isfinite(np.asarray(v, float)).ravel())
    except Exception:
        return v is not None


# ---------------------------------------------------------------- checks and the report
class VSuite:
    def __init__(self, name, V):
        self.name, self.V, self.rows, self.t0 = name, V, [], time.time()

    def per(self, fn, srcs=None):
        """fn(src) on every recorded source (USB: its left channel, as the jack's cable)"""
        return {s: _safe(fn, s) for s in (srcs or self.V.sources())}

    def usb(self, fn):
        return {"usb": _safe(fn, "usb")} if self.V.want_usb else {}

    def check(self, check, setting, vals, ok, crit, level=False, fmt=None, info=False):
        """one row: vals {src: value}; ok(value) -> bool on each source; level: a level change in dB / LU, compared
        between USB and the jack"""
        fmt = fmt or (lambda v: f"{v:+.2f}")
        res, txt = {}, {}
        for s in ("usb", "jack"):
            if s not in vals:
                txt[s] = "n/a" if s in self.V.sources() else "-"
                continue
            v = vals[s]
            if v is None or not finite(v):
                res[s], txt[s] = False, "no value"
                continue
            try:
                res[s] = bool(ok(v))
            except Exception:
                res[s] = False
            txt[s] = _safe(fmt, v) or str(v)
        mm = None
        if level and all(s in vals and vals[s] is not None and finite(vals[s]) for s in ("usb", "jack")):
            mm = float(vals["jack"] - vals["usb"])
        if not res and not info:                       # (a USB-only measure with --source jack)
            self.rows.append(dict(check=check, setting=setting, usb=txt["usb"], jack=txt["jack"], crit=crit,
                                  mismatch=None, status="skip", ok=True))
            return
        passed = info or all(res.values())
        bad_mm = mm is not None and abs(mm) > MISMATCH_DB
        status = ("info" if info else "ok" if passed else "FAIL") + (" MISMATCH" if bad_mm else "")
        self.rows.append(dict(check=check, setting=setting, usb=txt["usb"], jack=txt["jack"], crit=crit,
                              mismatch=mm, status=status, ok=bool(passed and not bad_mm)))

    def fails(self):
        return [r for r in self.rows if not r["ok"]]


def v_report(suites, V, path, dry):
    tk = V.takes
    L = [f"# VIBES device scan (tools/hil_scan.py){' - DRY RUN (synthetic audio, the numbers mean nothing)' if dry else ''}",
         "", f"- date: {time.strftime('%Y-%m-%d %H:%M')}; sources: {', '.join(V.sources())}"
         + (f" (jack: {V.jack_name})" if V.jack_name else ""),
         "- USB = the FM-1's USB audio, LEFT channel (what the jack cable carries) unless the row says stereo; jack = "
         "the headphone jack through the interface. Levels are changes within one source (the interface's gain "
         f"cancels); 'USB-jack' is their difference, flagged (MISMATCH) above {MISMATCH_DB:.1f} dB.",
         f"- VIBES by CC 79 = {VIBES_CC79}; every reset: CC 120, every parameter at its default, the DRY dict, the "
         "motor off unless the row is about it. CCs read from firmware/src/params.c PARAMS[].",
         "- Not testable over MIDI: KEY VEL (the FM-1's own keys only), TUNE's other values (DECAY page K4 has no CC; "
         "only the current value is measured), SPREAD (not in the engine), the physical keys and screen.", "",
         "## Summary", "", "| suite | checks | failed / flagged | takes | time |", "|---|---|---|---|---|"]
    for S in suites:
        L.append(f"| {S.name} | {len(S.rows)} | {len(S.fails())} | {S.ntakes} | {S.secs:.0f} s |")
    for S in suites:
        L += ["", f"## {S.name}", "", "| check | setting | USB | jack | criterion | USB-jack | result |",
              "|---|---|---|---|---|---|---|"]
        for r in S.rows:
            mm = f"{r['mismatch']:+.2f}" if r["mismatch"] is not None else ""
            L.append(f"| {r['check']} | {r['setting']} | {r['usb']} | {r['jack']} | {r['crit']} | {mm} | "
                     f"{'**' + r['status'] + '**' if not r['ok'] else r['status']} |")
    bad = [t for t in tk if t.get("usb_fs") or t.get("usb_clicks") or t.get("jack_clicks") or t.get("jack_over")]
    L += ["", "## Every take: clipping and clicks", "",
          "Criteria: no USB sample at full scale (-32768 / +32767); 0 clicks (hil_scan clicks(), outside 3 ms before / "
          "17 ms after each note event, a switch is not guarded); the jack's interface input not overloaded "
          "(|x| >= 0.999: its numbers are then not valid). 'raw' = clicks() with no guard (information).", "",
          "| take | s | USB peak dBFS | full-scale | >= 32700 | clicks L+R (raw) | jack peak dBFS | jack over | jack clicks |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t in tk:
        L.append(f"| {t['tag']} | {t['dur']:.1f} | {t.get('usb_peak_db', float('nan')):.1f} | {t.get('usb_fs', '-')} | "
                 f"{t.get('usb_near', '-')} | {t.get('usb_clicks', '-')} ({t.get('usb_clicks_raw', '-')}) | "
                 f"{t.get('jack_peak_db', float('nan')):.1f} | {t.get('jack_over', '-')} | {t.get('jack_clicks', '-')} |")
    L += ["", f"Takes with a problem: {', '.join(t['tag'] for t in bad) or 'none'}", ""]
    open(path, "w").write("\n".join(L))
    json.dump(dict(suites={S.name: S.rows for S in suites}, takes=tk), open(path[:-3] + ".json", "w"), indent=1,
              default=float)
    return bad


# ---------------------------------------------------------------- stimuli
def strikes(keys, vels, t0=0.2, gap=2.5, hold=1.2):
    """one strike per (key, velocity), gap apart, each held hold s: events and the take's length"""
    ev, t = [], t0
    for n in keys:
        for v in vels:
            ev += [(t, "on", n, v), (t + hold, "off", n, 0)]
            t += gap
    return ev, t - gap + hold + 0.8


def chord(notes, vel, on, off):
    return [(on, "on", n, vel) for n in notes] + [(off, "off", n, 0) for n in notes]


VKEYS = (53, 69, 89)                      # F3 A4 F6, the host tests' keys
FXCH = (48, 55, 60, 64)                   # hil_scan fx_all's chord, v108 (the FM-1 keys' velocity)
ST_CH = (3.6, chord(FXCH, 108, 0.1, 2.1))         # the chord, 2 s, then 1.5 s of tail
ST_LONG = (6.0, chord(FXCH, 108, 0.1, 5.6))       # held 5.5 s: slow rates
ST_NOTE = (2.0, chord((69,), 108, 0.1, 1.6))      # A4 alone: harmonics (the drive)
ST_STAC = (3.4, chord((69,), 108, 0.1, 0.25))     # A4 staccato: echoes, the reverb's tail
ST_QUIET = (2.0, chord((69,), 40, 0.1, 1.6))      # A4 v40: LEVEL's +12 dB stays under the limiter
ST_REL = (2.0, chord(FXCH, 127, 0.1, 0.6) + chord((69,), 40, 0.9, 1.6))   # loud, then soft: the release


def fmt_s(v):
    return " / ".join(f"{x:.2f}" for x in v)


# ---------------------------------------------------------------- VOICE
@vtest
def vibes_voice(V, S):
    """VOICE: STRIKE (TONE), MALLET (BARK), TUBE (BELL), WEIGHT (HAMMER) at 0 / 64 / 127; F3 A4 F6 at v100"""
    ev, dur = strikes(VKEYS, (100,))
    V.reset(label="VOICE all 64")
    T = {64: V.take("voice-default", dur, ev)}
    for card in ("TONE", "BARK", "BELL", "HAMMER"):
        for x in (0, 127):
            V.reset([(card, x)], label=f"VOICE {card} {x}")
            T[(card, x)] = V.take(f"voice-{card}-{x}", dur, ev)
    cache = {}

    def F(key, src, k):
        if (key, src, k) not in cache:
            t = T[key]
            cache[(key, src, k)] = strike_feats(t.m(src), t.on(src)[k], VKEYS[k])
        return cache[(key, src, k)]

    def key(c, x):
        return 64 if x == 64 else (c, x)
    for k, n in enumerate(VKEYS):
        def d(c, a, b, src):
            return sdist(F(key(c, a), src, k)["bands"], F(key(c, b), src, k)["bands"])

        def lv(c, a, b, src):
            return F(key(c, b), src, k)["lufs"] - F(key(c, a), src, k)["lufs"]
        s = f"key {n} v100"
        lab = dict(TONE="STRIKE (CC %d)" % vp()["TONE"]["cc"], BARK="MALLET (CC %d)" % vp()["BARK"]["cc"],
                   BELL="TUBE (CC %d)" % vp()["BELL"]["cc"], HAMMER="WEIGHT (CC %d)" % vp()["HAMMER"]["cc"])
        S.check(f"{lab['TONE']} 0 vs 127", s, S.per(lambda q: d("TONE", 0, 127, q)), lambda v: v >= 3,
                ">= 3 dB spectral distance", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['TONE']} 64 vs 127", s, S.per(lambda q: d("TONE", 64, 127, q)), lambda v: v >= 3,
                ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['TONE']} 0 vs 64", s, S.per(lambda q: (d("TONE", 0, 64, q), lv("TONE", 64, 0, q))),
                lambda v: v[0] >= 3 or v[1] >= 2, ">= 3 dB, or 0 louder by >= 2 LU",
                fmt=lambda v: f"{v[0]:.1f} dB / {v[1]:+.1f} LU")
        S.check(f"{lab['BARK']} 0 vs 127", s, S.per(lambda q: d("BARK", 0, 127, q)), lambda v: v >= 3, ">= 3 dB",
                fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['BARK']} 64 vs 127", s, S.per(lambda q: d("BARK", 64, 127, q)), lambda v: v >= 3, ">= 3 dB",
                fmt=lambda v: f"{v:.1f} dB")
        S.check(f"{lab['BARK']} 0 vs 64", s, S.per(lambda q: d("BARK", 0, 64, q)), lambda v: v >= 3,
                ">= 3 dB" + (" (F6: information, the default is already a pure fundamental)" if n == 89 else ""),
                fmt=lambda v: f"{v:.1f} dB", info=n == 89)
        S.check(f"{lab['BELL']} level 0 -> 127", s, S.per(lambda q: lv("BELL", 0, 127, q)),
                lambda v: 6.0 <= v <= 12.5, "+6 .. +12.5 LU", level=True)
        S.check(f"{lab['BELL']} level 0 -> 64", s, S.per(lambda q: lv("BELL", 0, 64, q)), lambda v: v >= 4.0,
                ">= +4 LU", level=True)
        S.check(f"{lab['BELL']} level 64 -> 127", s, S.per(lambda q: lv("BELL", 64, 127, q)), lambda v: v >= 0.5,
                ">= +0.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 0 -> 127", s, S.per(lambda q: lv("HAMMER", 0, 127, q)), lambda v: v >= 2.5,
                ">= +2.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 0 -> 64", s, S.per(lambda q: lv("HAMMER", 0, 64, q)), lambda v: v >= 1.5,
                ">= +1.5 LU", level=True)
        S.check(f"{lab['HAMMER']} level 64 -> 127", s, S.per(lambda q: lv("HAMMER", 64, 127, q)), lambda v: v >= 0.5,
                ">= +0.5 LU", level=True)
        S.check(f"{lab['HAMMER']} 0 vs 127 timbre, attack centroid", s,
                S.per(lambda q: (d("HAMMER", 0, 127, q), F(("HAMMER", 0), q, k)["cen_a"] / F(("HAMMER", 127), q, k)["cen_a"])),
                lambda v: v[0] >= 3 and v[1] > 1.05,
                ">= 3 dB and light brighter (centroid ratio > 1.05)" + (" (F6: information)" if n == 89 else ""),
                fmt=lambda v: f"{v[0]:.1f} dB / x{v[1]:.2f}", info=n == 89)
        S.check("V2 / V3 re V1 at the defaults", s, S.per(lambda q: (F(64, q, k)["v2"], F(64, q, k)["v3"])),
                lambda v: True, "information (bar_cards V2 V3)", fmt=lambda v: f"{v[0]:.1f} / {v[1]:.1f} dB", info=True)


# ---------------------------------------------------------------- DECAY page, the pedal, TUNE
@vtest
def vibes_decay(V, S):
    """DECAY: RING (DECAY), DAMPER (RELEASE), NOISE at 0 / default / max, the sustain pedal (CC 64, half pedal 50),
    TUNE (its current value)"""
    P = vp()
    # RING: F3 + A4 + F6 held 10.5 s (no partial of one near another's fundamental)
    ev = chord(VKEYS, 100, 0.2, 10.7)
    T = {}
    for x in (0, P["DECAY"]["de"], 127):
        V.reset([("DECAY", x)], label=f"RING {x}")
        T[x] = V.take(f"ring-{x}", 11.2, ev)
    for k, n in enumerate(VKEYS):
        def t3(q):
            return [t60(cut(T[x].m(q), T[x].on(q)[k], 0, 10.4), f1(n)) for x in sorted(T)]
        S.check(f"RING (CC {P['DECAY']['cc']}) T60 at 0 / {P['DECAY']['de']} / 127", f"key {n} v100, held",
                S.per(t3), lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 2.0, "in order, 127 / 0 >= 2x",
                fmt=lambda v: fmt_s(v) + " s")
    # DAMPER and the pedal: the decay 30..300 ms after the key-up (NOISE 0: no knock)
    ev = chord(VKEYS, 100, 0.2, 1.7)
    for what, name, xs in (("DAMPER", "RELEASE", (0, P["RELEASE"]["de"], 127)), ("pedal", None, (0, 50, 127))):
        T = {}
        for x in xs:
            if name:
                V.reset([(name, x), ("NOISE", 0)], label=f"{what} {x}")
                T[x] = V.take(f"{what.lower()}-{x}", 2.6, ev)
            else:
                V.reset([("NOISE", 0)], label=f"pedal {x}")
                T[x] = V.take(f"pedal-{x}", 2.6, ev + [(0.05, "cc", 64, x)])
        for k, n in enumerate(VKEYS):
            def r3(q):
                return [release_rate(cut(T[x].m(q), T[x].on(q)[k], 0, 2.3), f1(n), 1.5) for x in xs]
            if name:
                S.check(f"DAMPER (CC {P['RELEASE']['cc']}) stop 0 vs 127", f"key {n}, decay after the key-up",
                        S.per(r3), lambda v: v[0] / v[2] >= 3.0, "firm / soft >= 3x (dB/s)",
                        fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s (x{v[0] / v[2]:.1f})")
                S.check("DAMPER default in between", f"key {n}", S.per(r3), lambda v: v[0] > v[1] > v[2],
                        "0 > 64 > 127", fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f}")
            else:
                S.check("sustain pedal CC 64: up vs down", f"key {n}, decay after the key-up", S.per(r3),
                        lambda v: v[0] / v[2] >= 3.0, "up / down >= 3x",
                        fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f} dB/s (x{v[0] / v[2]:.1f})")
                S.check("half pedal (CC 64 = 50) in between", f"key {n}", S.per(r3), lambda v: v[2] < v[1] < v[0],
                        "down < half < up", fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} / {v[2]:.0f}")
    # NOISE: what it adds to the strike (0..60 ms) and after the key-up (0..150 ms: the damper's knock)
    ev, dur = strikes(VKEYS, (100,), gap=3.0, hold=1.5)
    T = {}
    for x in (0, P["NOISE"]["de"], 127):
        V.reset([("NOISE", x)], label=f"NOISE {x}")
        T[x] = V.take(f"noise-{x}", dur, ev)
    de = P["NOISE"]["de"]
    for k, n in enumerate(VKEYS):
        def nz(q):
            o = {x: T[x].on(q)[k] for x in T}
            m = {x: T[x].m(q) for x in T}
            ring = ms_db(cut(m[0], o[0], 0, 0.06))
            c = {x: excess_db(cut(m[x], o[x], 0, 0.06), cut(m[0], o[0], 0, 0.06)) - ring for x in (de, 127)}
            kn = {x: excess_db(cut(m[x], o[x], 1.5, 1.65), cut(m[0], o[0], 1.5, 1.65)) for x in (de, 127)}
            return dict(c=c, k=kn, strike=rdb(cut(m[0], o[0], 0.05, 0.3)), note=rdb(cut(m[0], o[0], 1.4, 1.5)))
        vals = S.per(nz)
        CC = P["NOISE"]["cc"]
        S.check(f"NOISE (CC {CC}) contact {de} -> 127", f"key {n}, 0..60 ms", {s: v and v["c"][127] - v["c"][de] for s, v in vals.items()},
                lambda v: v >= 6, ">= +6 dB", level=True)
        S.check("NOISE 127 contact re the note", f"key {n}", {s: v and v["c"][127] for s, v in vals.items()},
                lambda v: v > -35, "> -35 dB (a clear tick)", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"NOISE knock {de} -> 127", f"key {n}, 0..150 ms after the key-up",
                {s: v and v["k"][127] - v["k"][de] for s, v in vals.items()}, lambda v: v >= 6, ">= +6 dB", level=True)
        S.check(f"NOISE {de} knock under the strike / the note", f"key {n}",
                {s: v and (v["k"][de] - v["strike"], v["k"][de] - v["note"]) for s, v in vals.items()},
                lambda v: v[0] < -18 and v[1] < 0, "< -18 dB re the strike, < the note it damps",
                fmt=lambda v: f"{v[0]:.1f} / {v[1]:.1f} dB")
    # TUNE: no CC (DECAY page K4 on the panel): the current value, which should be 0
    V.reset(label="TUNE (current)")
    t = V.take("tune-a4", 2.6, chord((69,), 100, 0.2, 2.2))
    S.check("TUNE (panel only, no MIDI CC): A4 re the spec", "the FM-1's current TUNE",
            S.per(lambda q: 1200 * np.log2(f0_of(cut(t.m(q), t.on(q)[0], 0.3, 1.8), f1(69)) / f1(69))),
            lambda v: abs(v) <= 2.0, "|cents| <= 2 (TUNE 0; other values cannot be set over MIDI)",
            fmt=lambda v: f"{v:+.2f} c")


# ---------------------------------------------------------------- MOTOR page
@vtest
def vibes_motor(V, S):
    """MOTOR (CC 47-51): ON, DEPTH, SPEED, SHAPE, SYNC; switching without a click"""
    P = vp()
    dflt = P["MOT_RATE"]["de"]
    ev = chord(VKEYS, 100, 0.3, 4.8)
    T = {}
    for lab, kv, mot in (("off", [("MOT_DEPTH", 127)], False), ("d0", [("MOT_DEPTH", 0)], True),
                         ("d110", [("MOT_DEPTH", P["MOT_DEPTH"]["de"])], True), ("d127", [("MOT_DEPTH", 127)], True),
                         ("s127", [("MOT_DEPTH", 127), ("MOT_SHAPE", 127)], True)):
        V.reset(kv, motor=mot, label=f"MOTOR {lab}")
        T[lab] = V.take(f"motor-{lab}", 5.2, ev)
    hz = mot_hz(dflt)
    fps = FS / 240

    def am(lab, q, k, rate=None):
        n = VKEYS[k]
        x = cut(T[lab].m(q), T[lab].on(q)[k], 0.5, 3.0 if n < 80 else 2.0)
        tr, _ = partial_trace(x, f1(n), n=2048, hop=240)
        return pfit(tr, fps, rate or hz)
    for k, n in enumerate(VKEYS):
        s = f"key {n}, {hz:.2f} Hz"
        S.check(f"MOTOR DEPTH (CC {P['MOT_DEPTH']['cc']}) 127", s, S.per(lambda q: am("d127", q, k)["pp"]),
                lambda v: v >= 6, ">= 6 dB AM", fmt=lambda v: f"{v:.1f} dB")
        S.check(f"MOTOR DEPTH default {P['MOT_DEPTH']['de']}", s, S.per(lambda q: am("d110", q, k)["pp"]),
                lambda v: v >= 6, ">= 6 dB AM (params.c: 6-9 dB)", fmt=lambda v: f"{v:.1f} dB")
        S.check("MOTOR DEPTH 0", s, S.per(lambda q: am("d0", q, k)["pp"]), lambda v: v < 1, "< 1 dB",
                fmt=lambda v: f"{v:.2f} dB")
        S.check(f"MOTOR OFF (CC {P['MOT_ON']['cc']} = 0), DEPTH 127", s, S.per(lambda q: am("off", q, k)["pp"]),
                lambda v: v < 1, "< 1 dB", fmt=lambda v: f"{v:.2f} dB")
        if n < 80:
            def h21(lab, q):
                a = am(lab, q, k)["amps"]
                return a[1] / (a[0] + 1e-12)
            S.check(f"MOTOR SHAPE (CC {P['MOT_SHAPE']['cc']}) 0 vs 127", s,
                    S.per(lambda q: (h21("d127", q), h21("s127", q))), lambda v: v[1] >= 1.5 * v[0],
                    "the pulse's H2/H1 x1.5 or more", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    # SPEED: A4 alone, the whole signal's envelope
    one = chord((69,), 100, 0.3, 6.3)

    def rate(t, q, guess, lo=0.4, hi=2.6):
        return rate_of(env_db(cut(t.m(q), t.on(q)[0], 0.5, 5.8)), fps, guess, lo, hi)
    for x in (0, dflt, 127):
        V.reset([("MOT_DEPTH", 127), ("MOT_RATE", x)], motor=True, label=f"MOTOR SPEED {x}")
        t = V.take(f"motor-rate-{x}", 6.6, one)
        e = mot_hz(x)
        tol = 0.10 if x == 0 else 0.05
        S.check(f"MOTOR SPEED (CC {P['MOT_RATE']['cc']}) {x}", f"A4, expected {e:.2f} Hz",
                S.per(lambda q: rate(t, q, e)), lambda v: abs(v / e - 1) <= tol, f"within {tol:.0%}",
                fmt=lambda v: f"{v:.2f} Hz")
    R = {}
    for nm in ("1/4", "1/8"):
        V.reset([("MOT_DEPTH", 127), ("MOT_SYNC", sync_idx(nm))], motor=True, label=f"MOTOR SYNC {nm}")
        R[nm] = V.take(f"motor-sync-{nm.replace('/', '')}", 6.6, one)
    S.check(f"MOTOR SYNC (CC {P['MOT_SYNC']['cc']}) 1/8 vs 1/4", "A4; BPM = the FM-1's tempo",
            S.per(lambda q: (rate(R["1/8"], q, 2.0, 0.2, 5.0), rate(R["1/4"], q, 2.0, 0.2, 5.0))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "rate ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz (x{v[0] / v[1]:.2f}, {60 * v[1]:.0f} BPM)")
    # switching on / off while a note rings (the pedal down): no click, the AM comes and goes
    sw = P["MOT_ON"]["cc"]
    V.reset([("MOT_DEPTH", 127), ("MOT_RATE", dflt)], label="MOTOR switching")
    ev = [(0.05, "cc", 64, 127)] + chord((57,), 100, 0.3, 5.3) + [(0.3 + t, "cc", sw, v) for t, v in
                                                                   ((0.7, 127), (1.7, 0), (2.7, 127), (3.2, 0))]
    t = V.take("motor-switch", 4.5, ev)

    def sw_meas(q):
        m, i = t.m(q), t.on(q)[0]
        d2 = np.abs(np.diff(m, 2))
        ref = d2[i + int(0.3 * FS):i + int(0.65 * FS)].max()
        worst = max(d2[i + int((s - 0.02) * FS):i + int((s + 0.1) * FS)].max() for s in (0.7, 1.7, 2.7, 3.2)) / ref
        on = pfit(env_db(cut(m, i, 0.9, 1.65)), fps, hz)["pp"]
        off = pfit(env_db(cut(m, i, 1.9, 2.65)), fps, hz)["pp"]
        gain = 32768 if q == "usb" else 32768 * np.sqrt(np.mean(t.usb[:, 0] ** 2) / (np.mean(m ** 2) + 1e-20)) \
            if t.usb is not None else 32768 / (np.abs(m).max() + 1e-12) * 0.3
        ck = sum(clicks(m[i + int((s - 0.05) * FS):i + int((s + 0.15) * FS)] * gain) for s in (0.7, 1.7, 2.7, 3.2))
        return dict(worst=worst, on=on, off=off, ck=ck)
    v = S.per(sw_meas)
    S.check("MOTOR switched on / off under a held note: clicks", "4 switches", {s: x and x["ck"] for s, x in v.items()},
            lambda c: c == 0, "0 (clicks() around each switch)", fmt=lambda c: str(c))
    S.check("MOTOR switch: largest sample step", "re the note's own", {s: x and x["worst"] for s, x in v.items()},
            lambda c: c <= 1.2, "<= 1.2x (test_motor_switches_without_a_click)", fmt=lambda c: f"x{c:.2f}")
    S.check("MOTOR switch: AM on / off", "", {s: x and (x["on"], x["off"]) for s, x in v.items()},
            lambda c: c[0] >= 4 and c[1] < 1, "on >= 4 dB, off < 1 dB", fmt=lambda c: f"{c[0]:.1f} / {c[1]:.2f} dB")


# ---------------------------------------------------------------- velocity, every key
VELS = (1, 8, 24, 40, 64, 88, 104, 117, 120, 127)      # bar_scan VELS + 117 (the user's KEY VEL)


@vtest
def vibes_vel(V, S):
    """MIDI velocity on F3 A4 F6 (KEY VEL itself only plays the FM-1's own keys: not over MIDI); every key's level
    and tuning (bar_scan S9 / S6)"""
    for n in VKEYS:
        ev, dur = strikes((n,), VELS, gap=1.2, hold=0.7)
        V.reset(label=f"velocity {n}")
        t = V.take(f"vel-{n}", dur, ev)

        def lv(q):
            return [lu(cut(t.m(q), i, 0, 0.9)) for i in t.on(q)]
        vals = S.per(lv)
        S.check("velocity: no level drop as it rises", f"key {n}, v{VELS[0]}..v{VELS[-1]}",
                {s: v and float(min(np.diff(v[2:]))) for s, v in vals.items()}, lambda d: d >= -0.5,
                "every step >= -0.5 dB (from v24; v1 / v8 may be under the gate)", fmt=lambda d: f"{d:+.2f}")
        S.check("velocity: v40 -> v117", f"key {n}", {s: v and v[VELS.index(117)] - v[VELS.index(40)] for s, v in vals.items()},
                lambda d: d >= 10, ">= +10 LU", level=True)
        S.check("velocity: v1 .. v127 levels", f"key {n}", vals, lambda v: True, "information",
                fmt=lambda v: " ".join(f"{x:.0f}" for x in v), info=True)

        def bright(q):
            o = t.on(q)
            r = {}
            for v in (40, 117):
                x = cut(t.m(q), o[VELS.index(v)], 0, 0.05)
                sp = np.fft.rfft(x)
                sp[np.fft.rfftfreq(len(x), 1 / FS) < 3000] = 0
                r[v] = rdb(np.fft.irfft(sp, len(x))) - rdb(x)
            return r[117] - r[40]
        S.check("velocity: harder = brighter (> 3 kHz re level, 0..50 ms)", f"key {n}", S.per(bright),
                lambda d: d >= 3, ">= +3 dB (host: an expected failure, the VOICE work)", fmt=lambda d: f"{d:+.1f} dB",
                info=True)
    keys = list(range(21, 53, 3)) + list(range(53, 90)) + list(range(92, 109, 3))
    ev, dur = strikes(keys, (100,), gap=1.0, hold=0.6)
    V.reset(label="every key")
    t = V.take("keys", dur, ev)

    def kl(q):
        o = t.on(q)
        lv = np.array([lu(cut(t.m(q), i, 0, 0.9)) for i in o])
        c = np.array([1200 * np.log2(max(f0_of(cut(t.m(q), i, 0.05, 0.55), f1(n)), 1e-9) / f1(n)) for i, n in zip(o, keys)])
        return lv, c
    vals = S.per(kl)
    ins = [i for i, n in enumerate(keys) if 53 <= n <= 89]
    out_ = [i for i, n in enumerate(keys) if not 53 <= n <= 89]

    def med_dev(lv, idx):
        return [abs(lv[i] - np.median(lv[max(0, i - 3):i + 4])) for i in idx]
    S.check("keys F3..F6: neighbour step", "every key, v100", {s: v and float(np.abs(np.diff(v[0][ins])).max()) for s, v in vals.items()},
            lambda d: d <= 1.5, "<= 1.5 dB (bar_scan S9)", fmt=lambda d: f"{d:.2f} dB")
    S.check("keys F3..F6: off the +-3-key median", "", {s: v and float(max(med_dev(v[0], ins))) for s, v in vals.items()},
            lambda d: d <= 1.5, "<= 1.5 dB", fmt=lambda d: f"{d:.2f} dB")
    S.check("keys outside F3..F6 (every 3rd): off the median", "21..50, 92..107",
            {s: v and float(max(med_dev(v[0], out_))) for s, v in vals.items()}, lambda d: d <= 3, "<= 3 dB",
            fmt=lambda d: f"{d:.2f} dB")
    S.check("keys F3..F6: tuning re the spec", "", {s: v and float(np.abs(v[1][ins]).max()) for s, v in vals.items()},
            lambda d: d <= 3, "|cents| <= 3 (host S6: 1)", fmt=lambda d: f"{d:.2f} c")
    S.check("keys outside: tuning", "", {s: v and float(np.abs(v[1][out_]).max()) for s, v in vals.items()},
            lambda d: d <= 10, "|cents| <= 10 (a 0.5 s window on the lowest keys)", fmt=lambda d: f"{d:.1f} c")
    S.check("keys: level span", "", {s: v and float(v[0].max() - v[0].min()) for s, v in vals.items()}, lambda d: True,
            "information", fmt=lambda d: f"{d:.1f} dB", info=True)


# ---------------------------------------------------------------- every shared effect page
@vtest
def vibes_fx(V, S):
    """each effect page's every control at min / default / max (or the values that show it) on VIBES"""
    P = vp()
    fps = FS / 240

    def run(label, settings, stim, motor=False):
        V.reset(settings, motor=motor, label=label)
        return V.take(label.replace(" ", "-").replace("/", ""), *stim)

    def L(t, q, a=0.0, b=1.8, k=0):
        return lu(cut(t.m(q), t.on(q)[k], a, b))

    def B(t, q, a=0.05, b=1.05):
        return third(cut(t.m(q), t.on(q)[0], a, b))

    def nm(n):
        return f"{n} (CC {P[n]['cc']})"
    dry = {k: run(f"fx-dry-{k}", [], st) for k, st in (("ch", ST_CH), ("long", ST_LONG))}

    # COMPRESSOR
    cb = [("CMP_SW", 1), ("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 0), ("CMP_ATK", 40), ("CMP_REL", 50)]
    T = {x: run(f"comp-thr-{x}", with_(cb, ("CMP_AMT", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"COMP THRESH {nm('CMP_AMT')} 0 -> 127", "4:1, MKUP 0, chord v108", S.per(lambda q: L(T[127], q) - L(T[0], q)),
            lambda v: v <= -3, "<= -3 LU", level=True)
    S.check("COMP THRESH 0 / 64 / 127 in order", "", S.per(lambda q: [L(T[x], q) for x in (0, 64, 127)]),
            lambda v: v[0] > v[1] > v[2], "louder > ... > quieter", fmt=fmt_s)
    T = {x: run(f"comp-ratio-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", x)), ST_CH) for x in (0, 3, 7)}
    S.check(f"COMP RATIO {nm('CMP_RATIO')} 1.5:1 -> 20:1", "THRESH 100 (-31 dB)", S.per(lambda q: L(T[7], q) - L(T[0], q)),
            lambda v: v <= -2, "<= -2 LU", level=True)
    S.check("COMP RATIO 1.5:1 / 4:1 / 20:1 in order", "", S.per(lambda q: [L(T[x], q) for x in (0, 3, 7)]),
            lambda v: v[0] > v[1] > v[2], "in order", fmt=fmt_s)

    def crest(t, q):
        m, i = t.m(q), t.on(q)[0]
        return 20 * np.log10(np.abs(cut(m, i, 0, 0.03)).max() + 1e-12) - rdb(cut(m, i, 0.1, 0.4))
    T = {x: run(f"comp-atk-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", 7), ("CMP_ATK", x)), ST_CH) for x in (0, 40, 127)}
    S.check(f"COMP ATTACK {nm('CMP_ATK')} 0 -> 127", "20:1, THRESH 100: the strike's crest",
            S.per(lambda q: crest(T[127], q) - crest(T[0], q)), lambda v: v >= 2, "slow attack: crest >= +2 dB",
            fmt=lambda v: f"{v:+.1f} dB")
    S.check("COMP ATTACK crest 0 / 40 / 127", "", S.per(lambda q: [crest(T[x], q) for x in (0, 40, 127)]), lambda v: True,
            "information", fmt=fmt_s, info=True)

    def soft(t, q):
        return rdb(cut(t.m(q), t.on(q)[-1], 0.02, 0.3))
    T = {x: run(f"comp-rel-{x}", with_(cb, ("CMP_AMT", 100), ("CMP_RATIO", 7), ("CMP_REL", x)), ST_REL) for x in (0, 50, 127)}
    S.check(f"COMP RELEASE {nm('CMP_REL')} 0 -> 127", "a soft A4 0.3 s after a loud chord",
            S.per(lambda q: soft(T[0], q) - soft(T[127], q)), lambda v: v >= 2, "slow release holds it down >= 2 dB",
            level=True)
    T = {x: run(f"comp-mkup-{x}", with_(cb, ("CMP_GAIN", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"COMP MAKEUP {nm('CMP_GAIN')} 0 -> 127", "THRESH 64, 4:1", S.per(lambda q: L(T[127], q) - L(T[0], q)),
            lambda v: v >= 6, ">= +6 LU (the limiter caps +24 dB)", level=True)
    S.check("COMP MAKEUP 0 -> 64", "", S.per(lambda q: L(T[64], q) - L(T[0], q)), lambda v: v >= 2, ">= +2 LU", level=True)

    # FILTER
    fb = [("FL_TYPE", 1), ("FL_FREQ", 64), ("FL_RESO", 0), ("FL_LFO", 0), ("LFO_SHAPE", 0), ("LFO_RATE", P["LFO_RATE"]["de"]),
          ("LFO_SYNC", 0), ("LFO_RETRIG", 0)]
    names = ("OFF", "LP12", "LP18", "HP12", "HP18", "BP", "NOTCH")
    T = {x: run(f"filter-type-{names[x]}", with_(fb, ("FL_TYPE", x)), ST_CH) for x in range(7)}
    S.check(f"FILTER TYPE {nm('FL_TYPE')} OFF passes", "FREQ 64", S.per(lambda q: sdist(B(T[0], q), B(dry['ch'], q))),
            lambda v: v <= 0.5, "<= 0.5 dB re no filter", fmt=lambda v: f"{v:.2f} dB")
    for x in range(1, 7):
        lim = 2.0 if names[x] == "NOTCH" else 3.0
        S.check(f"FILTER TYPE {names[x]}", "FREQ 64, RESO 0, vs no filter",
                S.per(lambda q: sdist(B(T[x], q), B(dry['ch'], q))), lambda v: v >= lim, f">= {lim:.0f} dB",
                fmt=lambda v: f"{v:.1f} dB")
    for a, b in ((1, 2), (3, 4)):
        S.check(f"FILTER {names[a]} vs {names[b]}", "the slope", S.per(lambda q: sdist(B(T[a], q), B(T[b], q))),
                lambda v: v >= 1, ">= 1 dB", fmt=lambda v: f"{v:.1f} dB")
    T = {x: run(f"filter-freq-{x}", with_(fb, ("FL_FREQ", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"FILTER FREQ {nm('FL_FREQ')} 0 vs 127", "LP12", S.per(lambda q: sdist(B(T[0], q), B(T[127], q))),
            lambda v: v >= 3, ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("FILTER FREQ 0 / 64 / 127: centroid", "LP12", S.per(lambda q: [cen(cut(T[x].m(q), T[x].on(q)[0], 0.05, 1.05)) for x in (0, 64, 127)]),
            lambda v: v[0] < v[1] < v[2], "rising", fmt=lambda v: " / ".join(f"{c:.0f}" for c in v) + " Hz")
    T = {x: run(f"filter-reso-{x}", with_(fb, ("FL_RESO", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"FILTER RESO {nm('FL_RESO')} 0 vs 127", "LP12, FREQ 64", S.per(lambda q: sdist(B(T[0], q), B(T[127], q))),
            lambda v: v >= 2, ">= 2 dB (the peak at the cutoff)", fmt=lambda v: f"{v:.1f} dB")
    S.check("FILTER RESO 0 vs 64", "", S.per(lambda q: sdist(B(T[0], q), B(T[64], q))), lambda v: True, "information",
            fmt=lambda v: f"{v:.1f} dB", info=True)

    def mot(t, q, a=0.2, b=5.3):
        return motion_db(cut(t.m(q), t.on(q)[0], a, b))
    T = {x: run(f"filter-lfo-{x}", with_(fb, ("FL_LFO", x)), ST_LONG) for x in (0, 64, 127)}
    S.check(f"FILTER LFO depth {nm('FL_LFO')} 0 -> 127", "LP12 FREQ 64, LFO SINE 1.4 Hz",
            S.per(lambda q: mot(T[127], q) - mot(T[0], q)), lambda v: v >= 3, "spectral motion >= +3 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("FILTER LFO 0 -> 64", "", S.per(lambda q: mot(T[64], q) - mot(T[0], q)), lambda v: v >= 1, ">= +1 dB",
            fmt=lambda v: f"{v:+.1f} dB")
    lb = with_(fb, ("FL_LFO", 127))

    def ctr(t, q, a=0.2, b=5.3):
        return cen_trace(cut(t.m(q), t.on(q)[0], a, b))[0]
    e = lfo_hz(P["LFO_RATE"]["de"])
    T = {x: run(f"filter-shape-{x}", with_(lb, ("LFO_SHAPE", x)), ST_LONG) for x in (0, 4, 5)}

    def shp(t, q):
        f = pfit(ctr(t, q), fps, rate_of(ctr(t, q), fps, e))
        return f["amps"][2] / (f["amps"][0] + 1e-12), f["r2"]
    S.check(f"LFO SHAPE {nm('LFO_SHAPE')} SINE vs SQR", "the filter's sweep: H3/H1",
            S.per(lambda q: (shp(T[0], q)[0], shp(T[4], q)[0])), lambda v: v[1] >= max(0.2, 2 * v[0]),
            "SQR >= 0.2 and >= 2x SINE", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    S.check("LFO SHAPE SINE vs S&H", "the sweep's share at the LFO's fundamental (r2)",
            S.per(lambda q: (shp(T[0], q)[1], shp(T[5], q)[1])), lambda v: v[1] <= 0.5 * v[0], "S&H <= half SINE's",
            fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    for x in (0, P["LFO_RATE"]["de"], 127):
        t = run(f"filter-rate-{x}", with_(lb, ("LFO_RATE", x)), ST_LONG)
        ex = lfo_hz(x)
        tol = 0.15 if x == 0 else 0.10
        S.check(f"LFO RATE {nm('LFO_RATE')} {x}", f"expected {ex:.2f} Hz", S.per(lambda q: rate_of(ctr(t, q), fps, ex)),
                lambda v: abs(v / ex - 1) <= tol, f"within {tol:.0%}", fmt=lambda v: f"{v:.2f} Hz")
    T = {k: run(f"filter-sync-{k.replace('/', '')}", with_(lb, ("LFO_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    S.check(f"LFO SYNC {nm('LFO_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (rate_of(ctr(T['1/8'], q), fps, 2, 0.2, 5),
                                                                       rate_of(ctr(T['1/4'], q), fps, 2, 0.2, 5))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")
    rt = [0.1, 1.47, 2.93, 4.31]
    st_rt = (5.7, sum((chord((69,), 108, s, s + 1.2) for s in rt), []))
    T = {x: run(f"filter-retrig-{x}", with_(lb, ("LFO_RETRIG", x)), st_rt) for x in (0, 1)}

    def same_phase(t, q):
        o = t.on(q)
        tr = [cen_trace(cut(t.m(q), i, 0.05, 0.75))[0] for i in o]
        n = min(len(x) for x in tr)
        tr = [x[:n] - np.polyval(np.polyfit(np.arange(n), x[:n], 1), np.arange(n)) for x in tr]
        return float(np.mean([np.corrcoef(tr[a], tr[b])[0, 1] for a in range(len(tr)) for b in range(a + 1, len(tr))]))
    S.check(f"LFO TRIG {nm('LFO_RETRIG')} KEY", "4 strikes off the LFO's period: the sweep restarts",
            S.per(lambda q: (same_phase(T[1], q), same_phase(T[0], q))), lambda v: v[0] >= 0.8,
            "KEY: mean correlation of the sweeps >= 0.8 (FREE: information)", fmt=lambda v: f"KEY {v[0]:.2f} / FREE {v[1]:.2f}")

    # DRIVE
    db_ = [("DRV_SW", 1), ("DRIVE", 40), ("DRV_TONE", 64), ("DRV_MIX", 127)]

    def hd(t, q):
        return hd_db(cut(t.m(q), t.on(q)[0], 0.1, 1.1), f1(69))
    T = {x: run(f"drive-{x}", with_(db_, ("DRIVE", x)), ST_NOTE) for x in (0, 40, 127)}
    S.check(f"DRIVE {nm('DRIVE')} 0 -> 127", "A4 v108: H2+H3+H5+H6 re H1", S.per(lambda q: hd(T[127], q) - hd(T[0], q)),
            lambda v: v >= 10, ">= +10 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("DRIVE 0 -> 40 (the panel's start)", "", S.per(lambda q: hd(T[40], q) - hd(T[0], q)), lambda v: v >= 3,
            ">= +3 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("DRIVE 0 -> 127 loudness", "", S.per(lambda q: L(T[127], q, 0, 1.4) - L(T[0], q, 0, 1.4)), lambda v: abs(v) <= 3,
            "within +-3 LU (fx_all: +-2 on the Rhodes)", level=True)
    T = {x: run(f"drive-tone-{x}", with_(db_, ("DRIVE", 64), ("DRV_TONE", x)), ST_NOTE) for x in (0, 64, 127)}
    S.check(f"DRIVE TONE {nm('DRV_TONE')} 0 vs 127", "DRIVE 64",
            S.per(lambda q: (sdist(B(T[0], q), B(T[127], q)), cen(cut(T[127].m(q), T[127].on(q)[0], 0.1, 1.1)) /
                             cen(cut(T[0].m(q), T[0].on(q)[0], 0.1, 1.1)))),
            lambda v: v[0] >= 3 or v[1] >= 1.2, ">= 3 dB or centroid x1.2", fmt=lambda v: f"{v[0]:.1f} dB / x{v[1]:.2f}")
    T = {x: run(f"drive-mix-{x}", with_(db_, ("DRIVE", 64), ("DRV_MIX", x)), ST_NOTE) for x in (0, 64, 127)}
    S.check(f"DRIVE MIX {nm('DRV_MIX')} 0 -> 127", "DRIVE 64", S.per(lambda q: hd(T[127], q) - hd(T[0], q)),
            lambda v: v >= 6, ">= +6 dB distortion", fmt=lambda v: f"{v:+.1f} dB")
    T = {x: run(f"drive-sw-{x}", with_(db_, ("DRIVE", 64), ("DRV_SW", x)), ST_NOTE) for x in (0, 1)}
    S.check(f"DRIVE ON {nm('DRV_SW')} off -> on", "DRIVE 64", S.per(lambda q: hd(T[1], q) - hd(T[0], q)),
            lambda v: v >= 6, ">= +6 dB distortion", fmt=lambda v: f"{v:+.1f} dB")

    # TREMOLO / WAH
    tb = [("TW_SW", 1), ("TW_DEPTH", 64), ("TW_MODE", 1), ("TW_RATE", P["TW_RATE"]["de"]), ("TW_SYNC", 0), ("TW_SHAPE", 0)]
    er = lfo_hz(P["TW_RATE"]["de"])

    def amq(t, q, rate, sig=None):
        m = sig if sig is not None else t.m(q)
        return pfit(env_db(cut(m, t.on(q)[0], 0.3, 1.9)), fps, rate)
    T = {x: run(f"trem-depth-{x}", with_(tb, ("TW_DEPTH", x)), ST_CH) for x in (0, 64, 127)}
    S.check(f"TREM DEPTH {nm('TW_DEPTH')} 127", f"AM, {er:.1f} Hz", S.per(lambda q: amq(T[127], q, er)["pp"]),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM DEPTH 64", "", S.per(lambda q: amq(T[64], q, er)["pp"]), lambda v: v >= 2, ">= 2 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM DEPTH 0", "", S.per(lambda q: amq(T[0], q, er)["pp"]), lambda v: v < 1, "< 1 dB", fmt=lambda v: f"{v:.2f} dB")
    T = {x: run(f"trem-mode-{x}", with_(tb, ("TW_DEPTH", 127), ("TW_MODE", x)), ST_CH) for x in (0, 1)}

    def pan(t):
        st, i = t.usb, t.on("usb")[0]
        d = env_db(cut(st[:, 0], i, 0.3, 1.9)) - env_db(cut(st[:, 1], i, 0.3, 1.9))
        return pfit(d, fps, er)["pp"]
    S.check(f"TREM MODE {nm('TW_MODE')} PAN: L-R swing", "USB stereo, DEPTH 127", S.usb(lambda q: pan(T[0])),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE AM: L-R swing", "USB stereo", S.usb(lambda q: pan(T[1])), lambda v: v < 2, "< 2 dB",
            fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE AM: the sum's AM", "USB L+R", S.usb(lambda q: amq(T[1], q, er, T[1].usb.sum(1))["pp"]),
            lambda v: v >= 6, ">= 6 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("TREM MODE PAN: one channel's AM", "USB L / the jack", S.per(lambda q: amq(T[0], q, er)["pp"]), lambda v: True,
            "information", fmt=lambda v: f"{v:.1f} dB", info=True)
    tb127 = with_(tb, ("TW_DEPTH", 127))
    for x in (0, P["TW_RATE"]["de"], 127):
        t = run(f"trem-rate-{x}", with_(tb127, ("TW_RATE", x)), ST_LONG)
        ex = lfo_hz(x)
        tol = 0.15 if x == 0 else 0.10
        S.check(f"TREM RATE {nm('TW_RATE')} {x}", f"AM, expected {ex:.2f} Hz",
                S.per(lambda q: rate_of(env_db(cut(t.m(q), t.on(q)[0], 0.3, 5.3)), fps, ex)),
                lambda v: abs(v / ex - 1) <= tol, f"within {tol:.0%}", fmt=lambda v: f"{v:.2f} Hz")
    T = {k: run(f"trem-sync-{k.replace('/', '')}", with_(tb127, ("TW_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}

    def trate(t, q):
        return rate_of(env_db(cut(t.m(q), t.on(q)[0], 0.3, 5.3)), fps, 2, 0.2, 5)
    S.check(f"TREM SYNC {nm('TW_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (trate(T['1/8'], q), trate(T['1/4'], q))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")
    T = {x: run(f"trem-shape-{x}", with_(tb127, ("TW_SHAPE", x)), ST_CH) for x in (0, 127)}

    def hh(t, q):
        a = amq(t, q, er)["amps"]
        return (a[1] + a[2]) / (a[0] + 1e-12)
    S.check(f"TREM SHAPE {nm('TW_SHAPE')} 0 vs 127", "AM, DEPTH 127: (H2+H3)/H1", S.per(lambda q: (hh(T[0], q), hh(T[127], q))),
            lambda v: v[1] >= max(0.15, 1.5 * v[0]), ">= 0.15 and >= 1.5x", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    T = {x: run(f"wah-{x}", [("TW_SW", 2), ("TW_DEPTH", x)], ST_CH) for x in (0, 64, 127)}
    S.check(f"WAH (TREM/WAH {nm('TW_SW')} = WAH) DEPTH 127", "vs no effect", S.per(lambda q: sdist(B(T[127], q), B(dry['ch'], q))),
            lambda v: v >= 3, ">= 3 dB", fmt=lambda v: f"{v:.1f} dB")
    S.check("WAH DEPTH 127 vs 0", "", S.per(lambda q: sdist(B(T[127], q), B(dry['ch'], q)) - sdist(B(T[0], q), B(dry['ch'], q))),
            lambda v: v >= 1, ">= +1 dB more", fmt=lambda v: f"{v:+.1f} dB")
    S.check("WAH DEPTH 64", "vs no effect", S.per(lambda q: sdist(B(T[64], q), B(dry['ch'], q))), lambda v: v >= 1.5,
            ">= 1.5 dB", fmt=lambda v: f"{v:.1f} dB")

    # PHASER
    pb = [("PH_SW", 1), ("PH_DEPTH", 80), ("PH_SPEED", P["PH_SPEED"]["de"]), ("PH_FDBK", P["PH_FDBK"]["de"]), ("PH_SYNC", 0)]
    T = {x: run(f"phaser-depth-{x}", with_(pb, ("PH_DEPTH", x)), ST_LONG) for x in (0, 80, 127)}
    S.check(f"PHASER DEPTH {nm('PH_DEPTH')} 0 -> 127", "spectral motion", S.per(lambda q: mot(T[127], q) - mot(T[0], q)),
            lambda v: v >= 2, ">= +2 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("PHASER DEPTH 0 -> 80 (the panel's start)", "", S.per(lambda q: mot(T[80], q) - mot(T[0], q)), lambda v: v >= 1,
            ">= +1 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("PHASER DEPTH 80 loudness re no effect", "information (vibes_jump judges it)",
            S.per(lambda q: L(T[80], q, 0, 5.0) - L(dry["long"], q, 0, 5.0)), lambda v: True, "information", level=True, info=True)
    pb127 = with_(pb, ("PH_DEPTH", 127))
    sp = {}
    for x in (0, P["PH_SPEED"]["de"], 127):
        t = run(f"phaser-speed-{x}", with_(pb127, ("PH_SPEED", x)), ST_LONG)
        ex = lfo_hz(x, half=True)
        sp[x] = S.per(lambda q: rate_of(ctr(t, q), fps, ex))
        if x:
            S.check(f"PHASER SPEED {nm('PH_SPEED')} {x}", f"expected {ex:.2f} Hz (centroid sweep)", sp[x],
                    lambda v: abs(v / ex - 1) <= 0.10, "within 10 %", fmt=lambda v: f"{v:.2f} Hz")
    d0 = P["PH_SPEED"]["de"]
    S.check(f"PHASER SPEED 0 slower than {d0}", f"expected {lfo_hz(0, True):.2f} Hz",
            {s: (sp[0][s], sp[d0][s]) if sp[0].get(s) is not None and sp[d0].get(s) is not None else None for s in sp[0]},
            lambda v: v[0] <= 0.7 * v[1], "<= 0.7x (a period longer than the take)", fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz")
    T = {x: run(f"phaser-fdbk-{x}", with_(pb, ("PH_FDBK", x)), ST_LONG) for x in (0, P["PH_FDBK"]["de"], 127)}
    S.check(f"PHASER FDBK {nm('PH_FDBK')} 0 vs 127", "DEPTH 80",
            S.per(lambda q: (mot(T[127], q) - mot(T[0], q), sdist(B(T[127], q), B(T[0], q)))),
            lambda v: v[0] >= 1.5 or v[1] >= 2, "motion >= +1.5 dB or >= 2 dB apart", fmt=lambda v: f"{v[0]:+.1f} / {v[1]:.1f} dB")
    T = {k: run(f"phaser-sync-{k.replace('/', '')}", with_(pb127, ("PH_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    S.check(f"PHASER SYNC {nm('PH_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (rate_of(ctr(T['1/8'], q), fps, 2, 0.2, 5),
                                                                         rate_of(ctr(T['1/4'], q), fps, 2, 0.2, 5))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")

    # CHORUS
    chb = [("CP_SW", 1), ("CP_DEPTH", 64), ("CP_SPEED", P["CP_SPEED"]["de"]), ("CP_FDBK", P["CP_FDBK"]["de"]), ("CP_SYNC", 0)]
    T = {x: run(f"chorus-depth-{x}", with_(chb, ("CP_DEPTH", x)), ST_LONG) for x in (0, 64, 127)}
    S.check(f"CHORUS DEPTH {nm('CP_DEPTH')} 0 -> 127", "spectral motion", S.per(lambda q: mot(T[127], q) - mot(T[0], q)),
            lambda v: v >= 1, ">= +1 dB", fmt=lambda v: f"{v:+.1f} dB")
    S.check("CHORUS DEPTH 0 -> 127: width", "USB stereo L/R correlation",
            S.usb(lambda q: (corr_lr(cut(T[0].usb, T[0].on(q)[0], 0.1, 5.0)), corr_lr(cut(T[127].usb, T[127].on(q)[0], 0.1, 5.0)))),
            lambda v: v[0] - v[1] >= 0.1, "drops >= 0.1", fmt=lambda v: f"{v[0]:.2f} -> {v[1]:.2f}")
    cb127 = with_(chb, ("CP_DEPTH", 127))
    sp = {}
    for x in (0, P["CP_SPEED"]["de"], 127):
        t = run(f"chorus-speed-{x}", with_(cb127, ("CP_SPEED", x)), ST_LONG)
        ex = lfo_hz(x, half=True)
        sp[x] = S.per(lambda q: rate_of(ctr(t, q), fps, ex))
        if x:
            S.check(f"CHORUS SPEED {nm('CP_SPEED')} {x}", f"expected {ex:.2f} Hz (centroid)", sp[x],
                    lambda v: abs(v / ex - 1) <= 0.10, "within 10 %", fmt=lambda v: f"{v:.2f} Hz")
    d0 = P["CP_SPEED"]["de"]
    S.check(f"CHORUS SPEED 0 slower than {d0}", f"expected {lfo_hz(0, True):.2f} Hz",
            {s: (sp[0][s], sp[d0][s]) if sp[0].get(s) is not None and sp[d0].get(s) is not None else None for s in sp[0]},
            lambda v: v[0] <= 0.7 * v[1], "<= 0.7x", fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz")
    T = {x: run(f"chorus-fdbk-{x}", with_(chb, ("CP_FDBK", x)), ST_LONG) for x in (0, P["CP_FDBK"]["de"], 127)}
    S.check(f"CHORUS FDBK {nm('CP_FDBK')} 0 vs 127", "DEPTH 64",
            S.per(lambda q: (mot(T[127], q) - mot(T[0], q), sdist(B(T[127], q), B(T[0], q)))),
            lambda v: v[0] >= 1 or v[1] >= 2, "motion >= +1 dB or >= 2 dB apart", fmt=lambda v: f"{v[0]:+.1f} / {v[1]:.1f} dB")
    T = {k: run(f"chorus-sync-{k.replace('/', '')}", with_(cb127, ("CP_SYNC", sync_idx(k))), ST_LONG) for k in ("1/4", "1/8")}
    S.check(f"CHORUS SYNC {nm('CP_SYNC')} 1/8 vs 1/4", "", S.per(lambda q: (rate_of(ctr(T['1/8'], q), fps, 2, 0.2, 5),
                                                                         rate_of(ctr(T['1/4'], q), fps, 2, 0.2, 5))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.2f} / {v[1]:.2f} Hz ({60 * v[1]:.0f} BPM)")

    # DELAY (an A4 staccato)
    dlb = [("DL_SW", 1), ("DL_DEPTH", 50), ("DL_TIME", P["DL_TIME"]["de"]), ("DL_FDBK", P["DL_FDBK"]["de"]),
           ("DL_TONE", P["DL_TONE"]["de"]), ("DL_SYNC", 0)]

    def xs(t, q, a=0.0, b=3.2):
        return cut(t.m(q), t.on(q)[0], a, b)
    T0 = dly_ms(P["DL_TIME"]["de"])
    T = {x: run(f"delay-depth-{x}", with_(dlb, ("DL_DEPTH", x)), ST_STAC) for x in (0, 50, 127)}
    S.check(f"DELAY DEPTH {nm('DL_DEPTH')} 0 -> 50", f"the 1st echo ({T0:.0f} ms) re the direct",
            S.per(lambda q: (echo_levels(xs(T[0], q), T0, 1)[0], echo_levels(xs(T[50], q), T0, 1)[0])),
            lambda v: v[1] - v[0] >= 6, "50 stands >= 6 dB over 0 (0: the note's own residue)",
            fmt=lambda v: f"{v[0]:.1f} -> {v[1]:.1f} dB")
    S.check("DELAY DEPTH 50 -> 127", "the 1st echo", S.per(lambda q: echo_levels(xs(T[127], q), T0, 1)[0] - echo_levels(xs(T[50], q), T0, 1)[0]),
            lambda v: v >= 3, ">= +3 dB", level=True)
    dl100 = with_(dlb, ("DL_DEPTH", 100))
    for x in (0, P["DL_TIME"]["de"], 127):
        t = run(f"delay-time-{x}", with_(dl100, ("DL_TIME", x)), ST_STAC)
        ex = dly_ms(x)
        tol = max(8.0, 0.10 * ex) if x == 0 else max(5.0, 0.05 * ex)
        S.check(f"DELAY TIME {nm('DL_TIME')} {x}", f"expected {ex:.0f} ms", S.per(lambda q: echo_gap_ms(xs(t, q), ex)),
                lambda v: abs(v - ex) <= tol, f"within {tol:.0f} ms", fmt=lambda v: f"{v:.0f} ms")
    T1 = dly_ms(64)
    T = {x: run(f"delay-fdbk-{x}", with_(dl100, ("DL_TIME", 64), ("DL_FDBK", x)), ST_STAC) for x in (0, P["DL_FDBK"]["de"], 127)}
    S.check(f"DELAY FDBK {nm('DL_FDBK')} 0 vs 127", f"TIME 64 ({T1:.0f} ms): echoes above -40 dB",
            S.per(lambda q: (n_echoes(echo_levels(xs(T[0], q), T1, 15)), n_echoes(echo_levels(xs(T[127], q), T1, 15)))),
            lambda v: v[1] >= v[0] + 3, "127: >= 3 more", fmt=lambda v: f"{v[0]} -> {v[1]}")
    T = {x: run(f"delay-tone-{x}", with_(dl100, ("DL_TIME", 64), ("DL_TONE", x)), ST_STAC) for x in (0, P["DL_TONE"]["de"], 127)}
    S.check(f"DELAY TONE {nm('DL_TONE')} 0 vs 127", "the echoes 2..5: centroid",
            S.per(lambda q: cen(xs(T[127], q, 1.5 * T1 / 1000, 5.5 * T1 / 1000)) / cen(xs(T[0], q, 1.5 * T1 / 1000, 5.5 * T1 / 1000))),
            lambda v: v >= 1.2, "x1.2 brighter", fmt=lambda v: f"x{v:.2f}")
    T = {x: run(f"delay-type-{x}", with_(dl100, ("DL_SW", x)), ST_STAC) for x in (1, 2)}

    def eb(t, q):
        return third(xs(t, q, T0 / 1000 - 0.02, 3 * T0 / 1000))
    S.check(f"DELAY {nm('DL_SW')} DIG vs ANA", "the echoes' spectrum",
            S.per(lambda q: (sdist(eb(T[1], q), eb(T[2], q)), cen(xs(T[2], q, T0 / 1000 - 0.02, 3 * T0 / 1000)) /
                             cen(xs(T[1], q, T0 / 1000 - 0.02, 3 * T0 / 1000)))),
            lambda v: v[0] >= 2 or abs(np.log(v[1])) >= np.log(1.15), ">= 2 dB or centroid x1.15 either way",
            fmt=lambda v: f"{v[0]:.1f} dB / x{v[1]:.2f}")
    T = {k: run(f"delay-sync-{k.replace('/', '')}", with_(dl100, ("DL_SYNC", sync_idx(k))), ST_STAC) for k in ("1/4", "1/8")}
    S.check(f"DELAY SYNC {nm('DL_SYNC')} 1/4 vs 1/8", "", S.per(lambda q: (echo_gap_ms(xs(T['1/4'], q)), echo_gap_ms(xs(T['1/8'], q)))),
            lambda v: abs(v[0] / v[1] / 2 - 1) <= 0.05, "ratio 2 +-5 %",
            fmt=lambda v: f"{v[0]:.0f} / {v[1]:.0f} ms ({60000 / v[0]:.0f} BPM)")

    # REVERB (the staccato: its tail after the key-up at 0.15 s)
    rb = [("RV_DEPTH", 64), ("RV_DECAY", P["RV_DECAY"]["de"]), ("RV_TONE", P["RV_TONE"]["de"]), ("RV_PREDLY", 0)]

    def tail(t, q):
        return max(rdb(xs(t, q, 0.45, 0.95)), -120) - rdb(xs(t, q, 0, 0.15))
    T = {x: run(f"reverb-{x}", with_(rb, ("RV_DEPTH", x)), ST_STAC) for x in (0, 64, 127)}
    S.check(f"REVERB {nm('RV_DEPTH')} 0 -> 127", "the tail 0.3..0.8 s after the key-up re the direct",
            S.per(lambda q: tail(T[127], q) - tail(T[0], q)), lambda v: v >= 10, ">= +10 dB", level=True)
    S.check("REVERB 0 / 64 / 127 in order", "", S.per(lambda q: [tail(T[x], q) for x in (0, 64, 127)]),
            lambda v: v[1] >= v[0] + 3 and v[1] <= v[2] + 0.5, "64: >= 3 dB over 0, not over 127", fmt=fmt_s)
    r100 = with_(rb, ("RV_DEPTH", 100))
    T = {x: run(f"reverb-decay-{x}", with_(r100, ("RV_DECAY", x)), ST_STAC) for x in (0, P["RV_DECAY"]["de"], 127)}
    S.check(f"REVERB DECAY {nm('RV_DECAY')} 0 / {P['RV_DECAY']['de']} / 127", "the tail's T60",
            S.per(lambda q: [tail_t60(xs(T[x], q, 0.35, 3.2)) for x in sorted(T)]),
            lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 2, "in order, 127 / 0 >= 2x", fmt=lambda v: fmt_s(v) + " s")
    T = {x: run(f"reverb-tone-{x}", with_(r100, ("RV_TONE", x)), ST_STAC) for x in (0, P["RV_TONE"]["de"], 127)}
    S.check(f"REVERB TONE {nm('RV_TONE')} 0 / {P['RV_TONE']['de']} / 127", "the tail's centroid",
            S.per(lambda q: [cen(xs(T[x], q, 0.35, 1.15)) for x in sorted(T)]),
            lambda v: v[0] < v[1] < v[2] and v[2] / v[0] >= 1.2, "rising, x1.2", fmt=lambda v: " / ".join(f"{c:.0f}" for c in v) + " Hz")
    T = {x: run(f"reverb-pdly-{x}", with_(r100, ("RV_PREDLY", x)), ST_STAC) for x in (0, 64, 127)}
    for x in (64, 127):
        ex = pdly_ms(x)
        S.check(f"REVERB PRE-DELAY {nm('RV_PREDLY')} {x}", f"the side signal's arrival re PDLY 0, expected {ex:.0f} ms",
                S.usb(lambda q: side_onset_ms(T[x].usb, T[x].on(q)[0]) - side_onset_ms(T[0].usb, T[0].on(q)[0])),
                lambda v: abs(v - ex) <= 15, "within 15 ms (USB stereo)", fmt=lambda v: f"{v:.0f} ms")

    # LEVEL (CC 110): the preset's trim, A4 v40
    T = {x: run(f"level-{x}", [("LEVEL", x)], ST_QUIET) for x in (0, 64, 127)}
    for x in (0, 127):
        ex = lvl_db(x)
        S.check(f"LEVEL {nm('LEVEL')} {x} re 64", f"A4 v40, expected {ex:+.1f} dB",
                S.per(lambda q: L(T[x], q, 0, 1.4) - L(T[64], q, 0, 1.4)), lambda v: abs(v - ex) <= 1.0, "within 1 dB",
                level=True)


# ---------------------------------------------------------------- loudness jumps
JUMPS = [  # (name, settings, the switch (parameter, on value)): each at the panel's starting settings (ui.c fx_wake)
    ("drive (DRV ON, DRIVE 40)", [("DRIVE", 40)], ("DRV_SW", 1)),
    ("comp (-20 dB 4:1, MKUP 15)", [("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 15)], ("CMP_SW", 1)),
    ("filter LP12 (FREQ 127)", [], ("FL_TYPE", 1)),
    ("tremolo PAN 64", [("TW_DEPTH", 64)], ("TW_SW", 1)),
    ("wah 64", [("TW_DEPTH", 64)], ("TW_SW", 2)),
    ("phaser 80", [("PH_DEPTH", 80)], ("PH_SW", 1)),
    ("chorus 64", [("CP_DEPTH", 64)], ("CP_SW", 1)),
    ("delay DIG 50", [("DL_DEPTH", 50)], ("DL_SW", 1)),
    ("delay ANA 50", [("DL_DEPTH", 50)], ("DL_SW", 2)),
    ("reverb 50 (no switch: REV 0 -> 50)", [], ("RV_DEPTH", 50)),
    ("motor (DEPTH 110, 3.5 Hz)", [], ("MOT_ON", 1)),
]
ALL_ON = [("DRIVE", 30), ("CMP_SW", 1), ("CMP_AMT", 64), ("CMP_RATIO", 3), ("CMP_GAIN", 23), ("TW_SW", 1), ("TW_DEPTH", 50),
          ("CP_SW", 1), ("CP_DEPTH", 50), ("PH_SW", 1), ("PH_DEPTH", 60), ("DL_SW", 1), ("DL_DEPTH", 40), ("RV_DEPTH", 40)]
# (CC 85 first: it switches the phaser off; fx_all's "all on" sends it after CC 12, so its phaser is not on)


@vtest
def vibes_jump(V, S):
    """every effect switched on at its default settings: the loudness step (LUFS) on vs off, held on vs never on, and
    switched while a chord sounds (fx_toggle); clicks at the switch; the FX bypass"""
    P = vp()
    ch = chord(FXCH, 108, 0.1, 3.4)
    st = (3.6, ch)
    refs = []
    V.reset(label="jump ref")
    refs.append(V.take("jump-ref-a", *st))

    def lq(t, q, a, b, stereo=False):
        x = t.usb if stereo else t.m(q)
        i = t.on(q)[0]
        return lu(x[max(0, i + int(a * FS)):i + int(b * FS)])
    rows = []
    for name, base, (sw, on) in JUMPS:
        mot = sw == "MOT_ON"
        V.reset(base + ([] if mot else [(sw, on)]), motor=mot, label=f"jump {name} held on")
        held = V.take(f"jump-{sw}-{on}-held", *st)
        V.reset(base + [(sw, 0)], label=f"jump {name} switched")   # (DRV ON defaults to on)
        c, v = P[sw]["cc"], ccv(sw, on)
        tog = V.take(f"jump-{sw}-{on}-switched", 3.6, ch + [(1.2, "cc", c, v), (2.4, "cc", c, 0)])
        rows.append((name, held, tog))
    V.reset(label="jump ref (end)")
    refs.append(V.take("jump-ref-b", *st))
    for name, held, tog in rows:
        def static(q, stereo=False):
            return lq(held, q, 0, 1.8, stereo) - np.mean([lq(r, q, 0, 1.8, stereo) for r in refs])

        def direct(q):
            return lq(held, q, 0, 0.25) - np.mean([lq(r, q, 0, 0.25) for r in refs])
        S.check(f"{name}: on vs off", "held on, 0..1.8 s, USB stereo (as heard)", S.usb(lambda q: static(q, True)),
                lambda d: abs(d) <= JUMP_LU, f"|step| <= {JUMP_LU} LU", fmt=lambda d: f"{d:+.2f} LU")
        S.check(f"{name}: on vs off, USB L vs jack", "0..1.8 s", S.per(static), lambda d: abs(d) <= JUMP_LU,
                f"|step| <= {JUMP_LU} LU", level=True)
        S.check(f"{name}: the direct sound", "0..0.25 s (echoes / the hall not yet in)", S.per(direct), lambda d: True,
                "information", level=True, info=True)

        def steps(q):
            r = refs[0]

            def sl(t, a, b):
                return lq(t, q, a - 0.1, b - 0.1)
            up = (sl(tog, 1.25, 1.65) - sl(tog, 0.75, 1.15)) - (sl(r, 1.25, 1.65) - sl(r, 0.75, 1.15))
            dn = (sl(tog, 2.45, 2.85) - sl(tog, 1.95, 2.35)) - (sl(r, 2.45, 2.85) - sl(r, 1.95, 2.35))
            m = tog.m(q)
            g = 32768 if q == "usb" else 32768 * np.sqrt(np.mean(tog.usb[:, 0] ** 2) / (np.mean(m ** 2) + 1e-20)) \
                if tog.usb is not None else 32768 * 0.3 / (np.abs(m).max() + 1e-12)
            ck = sum(clicks(m[tog.at(q, s) - int(0.1 * FS):tog.at(q, s) + int(0.2 * FS)] * g) for s in (1.2, 2.4))
            return up, dn, ck
        v = S.per(steps)
        S.check(f"{name}: switched under a chord", "the step at on / at off (fx_toggle)",
                {s: x and (x[0], x[1]) for s, x in v.items()}, lambda d: abs(d[0]) <= JUMP_LU and abs(d[1]) <= JUMP_LU,
                f"|step| <= {JUMP_LU} LU", fmt=lambda d: f"{d[0]:+.2f} / {d[1]:+.2f} LU")
        S.check(f"{name}: clicks at the switches", "", {s: x and x[2] for s, x in v.items()}, lambda c: c == 0, "0",
                fmt=lambda c: str(c))
    S.check("reference drift", "the dry chord, start vs end", S.per(lambda q: lq(refs[1], q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: abs(d) <= 0.3, "within 0.3 LU (else the scan was disturbed)", level=True)
    V.reset(ALL_ON, label="all on")
    a = V.take("jump-all-on", *st)
    V.reset(ALL_ON, label="all on, FX bypass")
    V.send(111, 127)
    b = V.take("jump-bypass", *st)
    V.send(111, 0)
    S.check("FX bypass (CC 111) with everything on", "re no effects", S.per(lambda q: lq(b, q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: abs(d) <= 0.5, "within 0.5 LU (fx_all)", level=True)
    S.check("everything on re no effects", "fx_all 'all on'", S.per(lambda q: lq(a, q, 0, 1.8) - lq(refs[0], q, 0, 1.8)),
            lambda d: True, "information", level=True, info=True)


VORDER = ("vibes_jump", "vibes_voice", "vibes_decay", "vibes_motor", "vibes_vel", "vibes_fx")


def vibes_main(names, a):
    global VDEBUG
    VDEBUG = a.dry_run
    V = VRig(a.source, a.jack_dev, a.jack_ch, a.dry_run, a.wav)
    os.makedirs(OUT, exist_ok=True)
    suites, fails = [], 0
    for n in names:
        S = VSuite(n, V)
        t0, k0, p0 = time.time(), len(V.takes), V.plan_s
        print(f"== {n}: {VTESTS[n].__doc__.strip().splitlines()[0]}", flush=True)
        try:
            VTESTS[n](V, S)
        except Exception as e:  # keep scanning
            import traceback
            traceback.print_exc()
            S.rows.append(dict(check="suite error", setting="", usb=repr(e), jack="", crit="", mismatch=None,
                               status="FAIL", ok=False))
        S.ntakes = len(V.takes) - k0
        S.secs = (V.plan_s - p0) if a.dry_run else time.time() - t0
        S.ttakes = V.takes[k0:]
        bad_t = [t["tag"] for t in S.ttakes if t.get("usb_fs") or t.get("usb_clicks") or t.get("jack_clicks")]
        if bad_t:
            S.rows.append(dict(check="takes: full scale / clicks", setting="", usb=", ".join(bad_t), jack="", crit="none",
                               mismatch=None, status="FAIL", ok=False))
        fails += bool(S.fails())
        for r in S.rows:
            if not r["ok"] or a.dry_run:
                mm = f"  USB-jack {r['mismatch']:+.2f}" if r["mismatch"] is not None else ""
                print(f"   {r['status']:13s} {r['check']} [{r['setting']}]  USB {r['usb']}  jack {r['jack']}  ({r['crit']}){mm}")
        print(f"{'ok  ' if not S.fails() else 'FAIL'}  {n}: {len(S.rows)} checks, {len(S.fails())} failed / flagged, "
              f"{S.ntakes} takes, {S.secs:.0f} s{' (estimated)' if a.dry_run else ''}", flush=True)
        suites.append(S)
    V.finish()
    path = os.path.join(OUT, "vibes-dryrun.md" if a.dry_run else "vibes-report.md")
    v_report(suites, V, path, a.dry_run)
    if a.dry_run:
        print(f"estimated device time: {V.plan_s / 60:.1f} min ({len(V.takes)} takes); "
              f"{', '.join(f'{S.name} {S.secs / 60:.1f}' for S in suites)} min")
    print(f"report: {os.path.relpath(path, ROOT)} (+ .json)")
    return fails


def run_classic(names):
    """the Rhodes-era tests (unchanged): report.txt / report.json, ends on F01"""
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
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--vibes", action="store_true", help="every VIBES suite (not part of the default run)")
    ap.add_argument("--source", default="both", choices=("usb", "jack", "both"), help="VIBES suites: what to record")
    ap.add_argument("--jack-dev", default="M6", help="VIBES suites: the interface recording the headphone jack")
    ap.add_argument("--jack-ch", type=int, default=5, help="VIBES suites: its input (1-based)")
    ap.add_argument("--dry-run", action="store_true", help="VIBES suites: print the planned events, open no MIDI / audio")
    ap.add_argument("--wav", action="store_true", help="VIBES suites: save every take to build/hil/vibes/")
    a = ap.parse_args()
    if a.list:
        print(" ".join(TESTS))
        print("VIBES (--vibes or --only):", " ".join(VORDER))
        return 0
    names = a.only.split(",") if a.only else ([] if a.vibes else list(TESTS))
    if a.vibes:
        names += [n for n in VORDER if n not in names]
    bad = [n for n in names if n not in TESTS and n not in VTESTS]
    if bad:
        sys.exit(f"unknown test(s) {bad}; --list")
    classic, vib = [n for n in names if n in TESTS], [n for n in names if n in VTESTS]
    os.makedirs(OUT, exist_ok=True)
    fails = 0
    if a.dry_run:
        if classic:
            print(f"--dry-run covers the VIBES suites only: not running {', '.join(classic)}")
        return 1 if vibes_main(vib, a) else 0
    if classic:
        fails += run_classic(classic)
    if vib:
        fails += vibes_main(vib, a)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
