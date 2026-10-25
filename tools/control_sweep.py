#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Control sweep: every user control on every effect page and engine, turned 0 -> 127 -> 0 at a human rate (one CC
step every 20 ms) under a held chord whose tail rings, measured against static renders of the same control. Host
build only (tests/host.c renders the firmware's own code); tests/py/test_control_sweep.py runs it in `make pytest`.

    python3 tools/control_sweep.py [NAME ...] [--bin build/host/frodhes] [-j 2] [--json FILE]

NAME selects cases by substring (none: all). It prints one row per case: every check's value, its limit, and OK /
FLAG. The checks (each against static renders at 0, 32, 64, 96 and 127, or at an enum's band centres):

  pitch   the main partials (A4's 440 and 880 Hz, in the chord) along the sweep, cents against the effect switched
          off; the sweep's p90 |cents| minus the largest static p90 (the effect's own vibrato is not drift). Over
          5 cents flags, unless the control is a pitch control (TUNE, delay TIME, PRE-DELAY, MOD, LFO rate ...)
  zipper  per 1024-sample window, the largest |second difference| in the sweep against the largest of the statics in
          the same window: over 2x flags (a click or a stepped gain)
  jump    the level in 1024-sample windows: a step between two windows larger than the statics' largest step there
          by over 1.5 dB flags
  dropout a window more than 20 dB under the quietest static (where that one is above -80 dBFS)
  mix0    MIX / DEPTH-type controls: the control at 0 is the effect off, bit for bit
  mono    where the MANUAL says "how much": the wet level (render minus off) does not fall as the control rises
          (1 dB of tolerance)
  click   the DIFFERENTIAL click detector: the control moved once (FLIPS: its two ends both ways, one detent at the
          middle; a selector: every neighbouring pair of options both ways) at T_FLIP while the pedalled chord rings;
          the largest |second difference| of either channel from 3 ms before to 30 ms after the move, against the
          larger of the two renders held at the start and at the end value over the same span: over 10x flags. (The
          absolute detector, d2 > 6 x level + 2000, missed steps of 1700 LSB on a 5000 LSB chord: private BUGHUNT-095)
  trans   the moved render's level, 20 ms windows over 300 ms after the move, outside the two held renders' range
          (3 dB over the louder, 6 dB under the quieter) flags: a dropout or a burst after the move (the wet alone
          for delay and reverb); a control that glides THROUGH values (a cutoff: 'through') takes the renders held at
          the statics between its ends into the range too (a band-pass swept 16 kHz -> 30 Hz passes over the chord:
          louder at 1 kHz than at either end), over the first 60 ms only (the glide's 52 ms); after that the two ends.
          (0.9.5's BP "burst", +8.8 dB over the ends, was that pass at 3 ms: under every static it crossed; its
          #13 gate is the click, 25.5x)
  xjump   (crossfading selectors: FILTER TYPE) the moved render's largest step between 512-sample windows, beyond
          the held renders' own, over the held renders' level difference: over 0.5 flags (a 46 ms crossfade spreads
          the difference over ~4 windows; a one-sample switch puts most of it into one)
  noeff   every held value renders the same (at most 1 LSB apart): a control that does nothing
  stuck   after the sweep is back at 0, its last 300 ms against the render held at 0: over 1.5 dB flags (a state the
          control left behind)

For delay and reverb (additive: the dry passes) pitch and zipper are also measured on the wet alone (render minus
off), which is where a bent tail shows. Each render is cached by the binary's hash and its script, so a re-run and
the pytest workers share them (build/host/ctl_sweep/).

Material: the chord C3 E4 G4 A4 C5 at velocity 100, held by the pedal from 0.5 s (no other harmonic of the chord
within 4 % of 440 or 880 Hz). MARIMBA rings out within a second, so there the chord is struck again every 800 ms.
Setup: every effect off (COMP, DRIVE, FILTER, TREM, CHO/PHA, DELAY, REVERB, SPREAD 0, LEVEL 64), then only the
control's own effect; CC 10 (the developer reset) at 300 ms makes every render deterministic.

MASTER is not in the host's sound (the WAV is the app's output, before platform.c's MASTER pass): the case MASTER
applies platform.c's MASTER law (the knob's smoothing, dead band and square law, read once per 256-frame block) to a
static render, as a model of that code."""
import argparse
import hashlib
import glob
import json
import os
import subprocess
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
CACHE = os.path.join(ROOT, "build", "host", "ctl_sweep")
FS = 44100
CHORD = [48, 64, 67, 69, 72]
T_NOTE, T_SW0, STEP = 500, 1000, 20            # the sweep 0..127..0: a CC step every 20 ms, 1.0 .. 6.08 s
T_SW1 = T_SW0 + 254 * STEP
T_END = T_SW1 + 500
STATICS = [0, 32, 64, 96, 127]
PARTIALS = [440.0, 880.0]
LIMITS = {"pitch": 5.0, "zipper": 2.0, "jump": 1.5, "dropout": 20.0, "mono": 1.0, "click": 10.0, "trans_hi": 3.0,
          "trans_lo": 6.0, "stuck": 1.5,
          "xjump": 0.5}
T_FLIP = 2000                                  # the one-move renders: the control moved here (the chord rings)

# every effect off: COMP, its THRESH, DRIVE type, AMOUNT, FILTER, its LFO, TREM, CHO/PHA, PHASER, DELAY, its MIX,
# REVERB MIX, TYPE, SIZE AUTO, SPREAD, LEVEL 0 dB, STAGE 77
BASE = [(106, 0), (107, 0), (84, 0), (81, 0), (70, 0), (72, 0), (17, 0), (85, 0), (12, 0), (88, 0), (89, 0),
        (91, 0), (95, 0), (73, 0), (105, 0), (110, 64), (80, 0)]
ENGINE = {"rhodes": [], "vibes": [(79, 0), (47, 0)], "marimba": [(79, 100)]}   # (VIBES: the motor off but on MOTOR)


def bands(hi):
    """the CC values at the band centres of an enum 0..hi (frodhes.c midi_cc: x = v * (hi + 1) >> 7)"""
    return [min(127, (k * 128 + 64) // (hi + 1)) for k in range(hi + 1)]


class Case:
    """one control: name, its CC, the setup CCs that switch its effect on, the engine, and what it is:
    kind 'mix' (0 = the effect off; mono: the wet must not fall as it rises), 'pitch' (moves the pitch by design: no
    pitch check), 'knob'. additive: the dry passes untouched (delay, reverb): the wet is measured too. statics: the
    static values (default 0, 32, 64, 96, 127; an enum: its band centres)"""

    def __init__(self, name, cc, setup=(), engine="rhodes", kind="knob", additive=False, mono=False, statics=None,
                 page="", skip=(), through=False, xfade=False):
        self.name, self.cc, self.setup, self.engine, self.kind = name, cc, list(setup), engine, kind
        self.xfade = xfade                       # xjump: a crossfade between options of different levels (below)
        self.through = through                   # trans: the move glides through the values between its ends (a
        # cutoff): the range is every static from the start to the end value, not the two ends alone
        self.additive, self.mono, self.page = additive, mono, page
        self.skip = set(skip)                    # checks that do not apply to this case (the reasons: _cases)
        self.statics = list(statics) if statics else list(STATICS)

    def __repr__(self):
        return self.name


def _cases():
    C = []
    add = C.append
    # the engines' own cards (VOICE pages; CC 28-31, 102-104), TYPE
    for eng, labels in (("rhodes", ["TONE", "BARK", "BELL", "HAMMER", "DECAY", "RELEASE", "NOISE"]),
                        ("vibes", ["STRIKE", "MALLET", "TUBE", "WEIGHT", "RING", "DAMPER", "NOISE"]),
                        ("marimba", ["STRIKE", "MALLET", "TUBE", "WEIGHT", "RING", "MUTE", "NOISE"])):
        for lab, cc in zip(labels, (28, 29, 30, 31, 102, 103, 104)):
            sk = set()
            if cc == 103:                        # RELEASE / DAMPER / MUTE act at the key-up: the pedal holds the
                sk.add("noeff")                  # chord all through (test_every_control lets go)
            if eng == "marimba":                 # struck again every 800 ms during the sweep, each strike latching the
                sk |= {"jump", "stuck", "pitch"}  # knob's value then: the sweep's level and pitch follow the strikes
            add(Case(f"{eng} {lab}", cc, engine=eng, page="VOICE", skip=sk))
    add(Case("rhodes TYPE", 80, kind="select", statics=[16, 48, 80, 112], page="FRODHES"))
    add(Case("rhodes TYPE SPREAD", 80, [(105, 100)], kind="select", statics=[16, 48, 80, 112], page="FRODHES"))
    for lab, cc in (("DEPTH", 48), ("SPEED", 49), ("SHAPE", 50)):
        add(Case(f"vibes MOTOR {lab}", cc, [(47, 127)], engine="vibes", kind="pitch" if lab == "SPEED" else "knob",
                 page="MOTOR"))
    add(Case("vibes MOTOR on/off", 47, engine="vibes", kind="mix", statics=[0, 127], page="MOTOR",
             skip={"jump"}))                     # (the window-to-window level of a pulsing note: its phase restarts)
    # COMPRESSOR
    on = [(106, 127), (107, 60), (113, 20)]
    add(Case("comp THRESH", 107, [(106, 127), (113, 20)], page="COMPRESSOR"))
    add(Case("comp RATIO", 112, on, kind="select", statics=bands(7), page="COMPRESSOR"))
    add(Case("comp ATTACK", 108, on, page="COMPRESSOR"))
    add(Case("comp RELEASE", 109, on, page="COMPRESSOR"))
    add(Case("comp MAKEUP", 113, [(106, 127), (107, 60)], page="COMPRESSOR"))
    # DRIVE: each type
    for t, v in (("FDRV", 120), ("KHLON", 70), ("TSH8", 80), ("OHCD", 92), ("BHLUE", 104)):
        add(Case(f"drive {t} AMOUNT", 81, [(84, v)], page="DRIVE"))
        add(Case(f"drive {t} TONE", 82, [(84, v), (81, 80)], page="DRIVE"))
        add(Case(f"drive {t} MIX", 83, [(84, v), (81, 80)], kind="mix", page="DRIVE"))
    add(Case("drive TYPE", 84, [(81, 80)], kind="select", statics=[0, 70, 80, 92, 104, 120], page="DRIVE",
             skip={"jump"}))                     # (MANUAL: the old type fades out, then the new one in, through dry)
    # FILTER (and its LFO page)
    for t, v in (("LP12", 27), ("LP18", 46), ("HP12", 64), ("HP18", 82), ("BP", 100), ("NOTCH", 118)):
        add(Case(f"filter {t} FREQ", 74, [(70, v), (71, 30)], page="FILTER", through=True,
                 skip={"jump"}))                 # (a cutoff swept through the chord moves its level by design; a
        # band-pass gliding 16 kHz -> 30 Hz passes through the chord, louder than at either end: 'through')
    add(Case("filter RESO", 71, [(70, 46), (74, 80)], page="FILTER"))
    add(Case("filter LFO", 72, [(70, 46), (74, 80), (71, 40)], page="FILTER"))   # (at 0 the filter still filters)
    add(Case("filter TYPE", 70, [(74, 80), (71, 40)], kind="select", statics=bands(6), page="FILTER",
             skip={"jump"}, xfade=True))         # (the TYPEs differ by up to ~23 dB on the chord at 1.5 kHz (LP vs HP):
    # the level has to move by that much, so the sweep's jump check does not apply; xjump bounds how fast it moves:
    # a 46 ms crossfade spreads it over ~4 windows of 512 samples, 0.9.5's one-sample switch put up to 0.8 of it
    # into one window)
    add(Case("lfo RATE", 76, [(70, 46), (74, 80), (71, 40), (72, 60)], page="LFO"))
    add(Case("lfo SHAPE", 75, [(70, 46), (74, 80), (71, 40), (72, 60)], kind="select", statics=bands(5), page="LFO"))
    # TREM/WAH
    for m, s in (("AM", [(17, 60), (21, 100)]), ("PAN", [(17, 60), (21, 0)])):
        add(Case(f"trem {m} DEPTH", 18, s + [(19, 70)], kind="mix", page="TREM/WAH"))
        add(Case(f"trem {m} RATE", 19, s + [(18, 90)], kind="pitch", page="TREM/WAH"))
        add(Case(f"trem {m} SHAPE", 22, s + [(18, 90), (19, 70)], page="TREM/WAH"))
    add(Case("wah DEPTH", 18, [(17, 100), (19, 70)], page="TREM/WAH"))
    add(Case("wah RATE", 19, [(17, 100), (18, 90)], page="TREM/WAH"))
    add(Case("wah SHAPE", 22, [(17, 100), (18, 90), (19, 70)], page="TREM/WAH"))
    add(Case("trem/wah EFFECT", 17, [(18, 90), (19, 70)], kind="select", statics=[21, 64, 107], page="TREM/WAH"))
    # PHASER, CHORUS
    add(Case("phaser DEPTH", 13, [(85, 100)], page="PHASER"))
    add(Case("phaser SPEED", 14, [(85, 100), (13, 90)], kind="pitch", page="PHASER"))
    add(Case("phaser FDBK", 16, [(85, 100), (13, 90)], page="PHASER"))
    add(Case("chorus DEPTH", 86, [(85, 60)], kind="mix", page="CHORUS"))
    add(Case("chorus SPEED", 87, [(85, 60), (86, 80)], kind="pitch", page="CHORUS"))
    add(Case("cho/pha EFFECT", 85, [(86, 80), (13, 90)], kind="select", statics=[21, 64, 107], page="CHORUS"))
    # DELAY: each type
    for t, v in (("DIG", 40), ("ANA", 70), ("TAPE", 110)):
        add(Case(f"delay {t} MIX", 89, [(88, v)], kind="mix", additive=True, mono=True, page="DELAY"))
        add(Case(f"delay {t} TIME", 90, [(88, v), (89, 64)], kind="pitch", additive=True, page="DELAY"))
        add(Case(f"delay {t} FDBK", 26, [(88, v), (89, 64)], additive=True, page="DELAY",
                 skip={"jump"} if t != "DIG" else ()))   # (ANA / TAPE from ~115: the drone builds up, MANUAL)
        add(Case(f"delay {t} TONE", 27, [(88, v), (89, 64)], additive=True, page="DELAY"))
    add(Case("delay EFFECT", 88, [(89, 64)], kind="select", statics=[16, 48, 80, 112], additive=True, page="DELAY"))
    # REVERB and REVERB 2: each type, SIZE AUTO and fixed
    for t, v in (("HALL", 40), ("PLATE", 70), ("INF", 110)):
        for zs, z in (("auto", 0), ("size64", 64)):
            add(Case(f"rev {t} {zs} MIX", 91, [(95, v), (73, z)], kind="mix", additive=True, mono=True,
                     page="REVERB"))
        s = [(95, v), (91, 64)]
        add(Case(f"rev {t} DECAY", 92, s, additive=True, page="REVERB"))
        add(Case(f"rev {t} TONE", 93, s, additive=True, page="REVERB"))
        add(Case(f"rev {t} PRE-DELAY", 94, s, kind="pitch", additive=True, page="REVERB 2"))
        add(Case(f"rev {t} SIZE", 73, s, additive=True, page="REVERB 2"))
        add(Case(f"rev {t} MOD", 68, s, kind="pitch", additive=True, page="REVERB 2"))
        add(Case(f"rev {t} LOW CUT", 69, s, additive=True, page="REVERB 2"))
    add(Case("rev TYPE", 95, [(91, 64)], kind="select", statics=[16, 48, 80, 112], additive=True, page="REVERB",
             skip={"trans"}))                    # (MANUAL: the old tail fades out, the new TYPE starts empty)
    # SPREAD, LEVEL, MASTER
    add(Case("SPREAD", 105, kind="mix", page="MIDI"))
    add(Case("LEVEL", 110, page="SETUP"))
    add(Case("MASTER", None, page="(the MASTER knob)"))
    return C


CASES = _cases()
BY_NAME = {c.name: c for c in CASES}


def script(case, value=None, sweep=False, off=False, flip=None):
    """a render's script: the effect off (off), the control held at value, swept 0 -> 127 -> 0, or held at value
    and moved to flip at T_FLIP (that render ends T_FLIP + 800 ms)"""
    L = [f"50 midi 0xB0 {c} {v}" for c, v in BASE]
    L += [f"100 midi 0xB0 {c} {v}" for c, v in ENGINE[case.engine]]
    if not off:
        L += [f"150 midi 0xB0 {c} {v}" for c, v in case.setup]
        if case.cc is not None:
            L.append(f"200 midi 0xB0 {case.cc} {0 if sweep else value}")
    L.append("300 midi 0xB0 10 127")                       # the developer reset: deterministic lines and LFOs
    L.append(f"{T_NOTE} midi 0xB0 64 127")                 # the pedal: the chord rings through the sweep
    if case.engine == "marimba":                           # (rings out within a second: struck again)
        t = T_NOTE
        while t < T_SW1:
            L += [f"{t} midi 0x90 {n} 100" for n in CHORD] + [f"{t + 100} midi 0x80 {n} 0" for n in CHORD]
            t += 800
    else:
        L += [f"{T_NOTE} midi 0x90 {n} 100" for n in CHORD]
    if sweep:
        for k, v in enumerate(list(range(1, 128)) + list(range(126, -1, -1))):
            L.append(f"{T_SW0 + k * STEP} midi 0xB0 {case.cc} {v}")
    end = T_END
    if flip is not None:
        L.append(f"{T_FLIP} midi 0xB0 {case.cc} {flip}")
        end = T_FLIP + 800
        L = [x for x in L if int(x.split()[0]) < end]
    L.append(f"{end} end")
    return "\n".join(sorted(L, key=lambda s: int(s.split()[0]))) + "\n"


_bin_hash = {}


def bin_hash(binary):
    st = os.stat(binary)
    k = (binary, st.st_mtime_ns, st.st_size)
    if k not in _bin_hash:
        _bin_hash[k] = hashlib.sha1(open(binary, "rb").read()).hexdigest()[:16]
    return _bin_hash[k]


def render(text, binary=BIN):
    """the host build's stereo render of a script (float, int16 scale), cached by the binary and the script"""
    h = hashlib.sha1((bin_hash(binary) + text).encode()).hexdigest()[:20]
    os.makedirs(CACHE, exist_ok=True)
    npy = os.path.join(CACHE, h + ".npy")
    if os.path.exists(npy):
        try:
            return np.load(npy).astype(np.float64)
        except (ValueError, OSError):
            pass
    base = os.path.join(CACHE, f"{h}.{os.getpid()}.{threading.get_ident()}")   # (unique per process and thread)
    with open(base + ".txt", "w") as f:
        f.write(text)
    p = subprocess.run([binary, base, base + ".txt"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"host render failed ({p.returncode}): {p.stdout[-500:]}{p.stderr[-500:]}")
    with wave.open(base + ".wav") as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).copy()
    tmp = base + ".tmp"
    with open(tmp, "wb") as f:                             # (a file object: np.save adds no ".npy" to it)
        np.save(f, a)
    os.replace(tmp, npy)                                   # (atomic: a parallel reader never sees half a file)
    for e in glob.glob(glob.escape(base) + "*"):           # the script, the WAV, the screen shots
        try:
            os.remove(e)
        except OSError:
            pass
    return a.astype(np.float64)


def ms(t):
    return int(t * FS / 1000)


def rms_db(x):
    return float(10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-9) - 20 * np.log10(32768))


# ---- the measurements (the MIX audit's analysis, 2026-10-25)
def peakf(x, f0, nfft=1 << 15):
    """the spectral peak within 4 % of f0, parabolic interpolation on the log magnitude: (Hz, magnitude)"""
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    fr = np.fft.rfftfreq(nfft, 1 / FS)
    idx = np.where((fr > f0 * 0.96) & (fr < f0 * 1.04))[0]
    i = idx[np.argmax(X[idx])]
    a, b, c = np.log(X[i - 1] + 1e-12), np.log(X[i] + 1e-12), np.log(X[i + 1] + 1e-12)
    d = a - 2 * b + c
    p = 0.5 * (a - c) / d if d != 0 else 0.0
    return (i + p) * FS / nfft, X[i]


def pitch_p90(sig, ref):
    """p90 of |cents| of the 440 / 880 Hz partials, sig against ref, windows of 8192 (hop 4096) over the sweep's
    span; windows where sig's partial is under 3 % of ref's are skipped (the wet at MIX 0). None: no window"""
    al = []
    for s0 in range(ms(T_SW0), ms(T_SW1) - 8192, 4096):
        a, b = sig[s0:s0 + 8192].mean(1), ref[s0:s0 + 8192].mean(1)
        cs = []
        for f0 in PARTIALS:
            fa, ma = peakf(a, f0)
            fb, mb = peakf(b, f0)
            if ma < 0.03 * mb or not (np.isfinite(fa) and np.isfinite(fb)) or fa <= 0 or fb <= 0:
                continue
            cs.append(1200 * np.log2(fa / fb))
        if cs:
            al.append(abs(float(np.mean(cs))))
    return float(np.percentile(al, 90)) if al else None


def d2_max(x, n=1024):
    """the largest |second difference| (of the L / R mean) in each n-sample window"""
    d = np.abs(np.diff(x.mean(1), 2))
    k = len(d) // n
    return d[:k * n].reshape(k, n).max(1)


def win_db(x, n=1024):
    k = len(x) // n
    e = (x[:k * n].mean(1).reshape(k, n) ** 2).mean(1)
    return 10 * np.log10(e + 1e-3) - 20 * np.log10(32768)


def zipper_ratio(sweep, statics):
    a, b = ms(T_SW0) // 1024, ms(T_SW1) // 1024
    m_s = d2_max(sweep)[a:b]
    M = np.max([d2_max(s)[a:b] for s in statics], 0)
    return float((m_s / (M + 1.0)).max())


def jump_db(sweep, statics):
    """the sweep's largest window-to-window level step over the statics' largest step in the same window"""
    a, b = ms(T_SW0) // 1024, ms(T_SW1) // 1024
    w = win_db(sweep)
    ds = np.abs(np.diff(w))[a:b]
    D = np.max([np.abs(np.diff(win_db(s)))[a:b] for s in statics], 0)
    live = np.minimum(w[a:b], w[a + 1:b + 1]) > -90       # (not across the silence gate: -66 dBFS, then exact 0)
    return float((ds - D)[live].max()) if live.any() else 0.0


def dropout_db(sweep, statics):
    """how far the sweep falls under the quietest static, in the windows where that one is above -80 dBFS"""
    a, b = ms(T_SW0) // 1024, ms(T_SW1) // 1024
    w = win_db(sweep)[a:b]
    m = np.min([win_db(s)[a:b] for s in statics], 0)
    keep = m > -80
    return float((m[keep] - w[keep]).max()) if keep.any() else 0.0


def flips(case):
    """the one-move pairs (from, to): a knob its two ends both ways and one detent at the middle; a selector every
    neighbouring pair of its options both ways"""
    if case.kind == "select" or len(case.statics) == 2:
        v = case.statics
        return [p for a, b in zip(v, v[1:]) for p in ((a, b), (b, a))]
    return [(0, 127), (127, 0), (64, 65)]


def d2c(x):
    """|second difference| of each channel, the larger of the two per sample"""
    return np.abs(np.diff(x, 2, axis=0)).max(1)


def click_ratio(moved, start, end):
    """the moved render's largest second difference around the move against the held renders' (start, end)"""
    a, b = ms(T_FLIP - 3), ms(T_FLIP + 30)
    ref = max(d2c(start[a:b]).max(), d2c(end[a:b]).max())
    return float(d2c(moved[a:b]).max() / max(ref, 1.0))


def win20_db(x, t0, n):
    """the level of n 20 ms windows from t0 (ms), dBFS (L / R power mean)"""
    w = ms(20)
    return np.array([10 * np.log10(np.mean(x[ms(t0) + k * w:ms(t0) + (k + 1) * w] ** 2) + 1e-3) - 20 * np.log10(32768)
                     for k in range(n)])


def xjump_frac(moved, start, end, w=512, nw=20):
    """a crossfade's speed: the moved render's largest step between w-sample windows (from 50 ms before the move),
    beyond the held renders' own largest step, as a fraction of the held renders' level difference there (0: the
    held ones are within 0.1 dB of each other)"""
    a = ms(T_FLIP - 50)
    def lv(x):
        return np.array([20 * np.log10(np.sqrt(np.mean(x[a + k * w:a + (k + 1) * w].astype(float) ** 2)) / 32767 + 1e-12)
                         for k in range(nw)])
    m, ls, le = lv(moved), lv(start), lv(end)
    d = float(np.abs(ls - le).max())
    if d < 0.1:
        return 0.0
    st = float(np.abs(np.diff(m)).max()) - float(np.maximum(np.abs(np.diff(ls)), np.abs(np.diff(le))).max())
    return max(st, 0.0) / d


def trans_db(moved, start, end, mid=()):
    """(over, under): how far the moved render's level leaves the held renders' range after the move (windows where
    the louder held one is above -70 dBFS); mid: renders held at values the move glides through (they raise the top)"""
    m, s, e = (win20_db(x, T_FLIP, 15) for x in (moved, start, end))
    hi, lo = np.maximum(s, e), np.minimum(s, e)
    for x in mid:                                # (only while it glides: FILTER FREQ's 9 octaves at 1/8 octave a
        hi[:3] = np.maximum(hi[:3], win20_db(x, T_FLIP, 15)[:3])   # sub-block take 52 ms; then the ends alone)
    keep = hi > -70
    if not keep.any():
        return 0.0, 0.0
    return float(max((m - hi)[keep].max(), 0.0)), float(max((lo - m)[keep].max(), 0.0))


def master_model(x, knob):
    """platform.c's MASTER pass on a render: knob (the ADC reading 0..1023 at each 256-frame block), smoothed as
    platform_poll does (knob_avg += (a * 16 - knob_avg) / 64, polled once per block here), its dead band of 6 and its
    square law (master_q12 = k10 * k10 >> 8), read once per block, scaled (x * m) >> 12"""
    y = np.array(x, dtype=np.int64)
    avg, used = 512 * 16, 512
    m = (used * used) >> 8
    nb = len(y) // 256
    for k in range(nb):
        a = int(knob[min(k, len(knob) - 1)])
        avg += int((a * 16 - avg) / 64)
        k10 = avg // 16
        if k10 + 6 < used or k10 > used + 6 or (k10 == 0) != (used == 0) or (k10 >= 1020) != (used >= 1020):
            used = k10
            m = (k10 * k10) >> 8
        y[k * 256:(k + 1) * 256] = (y[k * 256:(k + 1) * 256] * m) >> 12
    return y[:nb * 256].astype(np.float64)


def master_knob(sweep=False, value=None):
    """the MASTER knob's ADC readings per block: held at value (0..127 as a fraction of 1023) or swept with the
    other controls' human rate (0 -> 1023 -> 0 over the sweep's span)"""
    nb = ms(T_END) // 256 + 1
    t = np.arange(nb) * 256 * 1000.0 / FS
    if not sweep:
        return np.full(nb, int(round(value * 1023 / 127)))
    u = np.clip((t - T_SW0) / (T_SW1 - T_SW0), 0, 1)
    return np.round(1023 * (1 - np.abs(2 * u - 1))).astype(int)


def measure(case, binary=BIN):
    """every check's value for one case"""
    if case.name == "MASTER":
        base = render(script(BY_NAME["LEVEL"], value=64), binary)
        off = master_model(base, master_knob(value=127))
        st = {v: master_model(base, master_knob(value=v)) for v in case.statics}
        sw = master_model(base, master_knob(sweep=True))
    else:
        off = render(script(case, off=True), binary)
        st = {v: render(script(case, value=v), binary) for v in case.statics}
        sw = render(script(case, sweep=True), binary)
    stl = list(st.values())
    r = {"pitch": None, "pitch_wet": None}
    if case.name != "MASTER":                          # the one-move renders (the differential click detector)
        r["click"], r["trans"], r["xjump"] = {}, {}, {}
        for a, b in flips(case):
            for v in (a, b):
                if v not in st:
                    st[v] = render(script(case, value=v), binary)
            mv = render(script(case, value=a, flip=b), binary)
            n = len(mv)
            sa, sb = st[a][:n], st[b][:n]
            r["click"][f"{a}>{b}"] = round(click_ratio(mv, sa, sb), 1)
            if case.xfade:
                r["xjump"][f"{a}>{b}"] = round(xjump_frac(mv, sa, sb), 2)
            if case.additive:
                o = off[:n]
                mv, sa, sb = mv - o, sa - o, sb - o
            mid = [st[v][:n] - (off[:n] if case.additive else 0) for v in case.statics
                   if case.through and min(a, b) < v < max(a, b)]
            hi, lo = trans_db(mv, sa, sb, mid)
            r["trans"][f"{a}>{b}"] = (round(hi, 1), round(lo, 1))
        r["noeff"] = float(max(np.abs(x - stl[0]).max() for x in stl[1:])) if len(stl) > 1 else None
        z = st[case.statics[0]] if case.statics[0] == 0 or case.kind == "select" else render(script(case, value=0), binary)
        a, b = ms(T_SW1 + 200), ms(T_END)
        r["stuck"] = round(abs(rms_db(sw[a:b]) - rms_db(z[a:b])), 2)
    if case.kind not in ("pitch",) and case.name != "MASTER":
        ps = [pitch_p90(s, off) for s in stl]
        p = pitch_p90(sw, off)
        if p is not None:
            r["pitch"] = round(p - max([x for x in ps if x is not None] or [0.0]), 1)
        if case.additive:
            pw = [pitch_p90(s - off, off) for s in stl]
            p = pitch_p90(sw - off, off)
            if p is not None:
                r["pitch_wet"] = round(p - max([x for x in pw if x is not None] or [0.0]), 1)
    r["zipper"] = round(zipper_ratio(sw, stl), 2)
    r["zipper_wet"] = round(zipper_ratio(sw - off, [s - off for s in stl]), 2) if case.additive else None
    r["jump"] = round(jump_db(sw, stl), 2)
    r["dropout"] = round(dropout_db(sw, stl), 1)
    if case.kind == "mix":
        r["mix0"] = float(np.abs(st[case.statics[0]] - off).max())
    a, b = ms(T_SW0), ms(T_SW1)
    r["level_db"] = {v: round(rms_db(st[v][a:b]) - rms_db(off[a:b]), 2) for v in case.statics}
    if case.additive:
        r["wet_db"] = {v: round(rms_db((st[v] - off)[a:b]) - rms_db(off[a:b]), 2) for v in case.statics}
    return r


def checks(case, r):
    """check -> (value, limit, ok) for the checks that apply to the case"""
    out = {}
    sk = set(case.skip)
    if case.kind == "pitch":
        sk |= {"trans", "stuck", "jump"}         # a rate / time: the LFO's phase, the echoes' times differ after it
    if case.additive:
        sk.add("stuck")                          # the lines still hold what the sweep put in them
    if r.get("pitch") is not None:
        out["pitch"] = (r["pitch"], LIMITS["pitch"], r["pitch"] <= LIMITS["pitch"])
    if r.get("pitch_wet") is not None:
        out["pitch_wet"] = (r["pitch_wet"], LIMITS["pitch"], r["pitch_wet"] <= LIMITS["pitch"])
    out["zipper"] = (r["zipper"], LIMITS["zipper"], r["zipper"] <= LIMITS["zipper"])
    if r.get("zipper_wet") is not None:
        out["zipper_wet"] = (r["zipper_wet"], LIMITS["zipper"], r["zipper_wet"] <= LIMITS["zipper"])
    out["jump"] = (r["jump"], LIMITS["jump"], r["jump"] <= LIMITS["jump"])
    out["dropout"] = (r["dropout"], LIMITS["dropout"], r["dropout"] <= LIMITS["dropout"])
    if "mix0" in r:
        out["mix0"] = (r["mix0"], 1.0, r["mix0"] <= 1.0)   # (1 LSB: PAN's equal-power rounding at DEPTH 0)
    if r.get("click"):
        k, v = max(r["click"].items(), key=lambda kv: kv[1])
        out["click"] = (f"{v} ({k})", LIMITS["click"], v <= LIMITS["click"])
        k, v = max(r["trans"].items(), key=lambda kv: max(kv[1][0] - LIMITS["trans_hi"], kv[1][1] - LIMITS["trans_lo"]))
        out["trans"] = (f"+{v[0]}/-{v[1]} ({k})", f"+{LIMITS['trans_hi']}/-{LIMITS['trans_lo']}",
                        v[0] <= LIMITS["trans_hi"] and v[1] <= LIMITS["trans_lo"])
    if r.get("xjump"):
        k, v = max(r["xjump"].items(), key=lambda kv: kv[1])
        out["xjump"] = (f"{v} ({k})", LIMITS["xjump"], v <= LIMITS["xjump"])
    if r.get("noeff") is not None:
        out["noeff"] = (r["noeff"], 1.0, r["noeff"] > 1.0)
    if r.get("stuck") is not None:
        out["stuck"] = (r["stuck"], LIMITS["stuck"], r["stuck"] <= LIMITS["stuck"])
    if case.mono and "wet_db" in r:
        w = [r["wet_db"][v] for v in case.statics]
        fall = max([max(w[:i + 1]) - w[i] for i in range(1, len(w))] + [0.0])   # the largest fall from a peak
        out["mono"] = (round(fall, 2), LIMITS["mono"], fall <= LIMITS["mono"])
    return {k: v for k, v in out.items() if k.split("_")[0] not in sk}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--bin", default=BIN)
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--json")
    a = ap.parse_args()
    if not os.path.exists(a.bin):
        subprocess.run(["make", "-s", "-C", ROOT, "build/host/frodhes"], check=True)
    cs = [c for c in CASES if not a.names or any(n in c.name for n in a.names)]
    with ThreadPoolExecutor(a.j) as ex:
        res = list(ex.map(lambda c: (c, measure(c, a.bin)), cs))
    out = {}
    for c, r in res:
        ck = checks(c, r)
        flags = [k for k, v in ck.items() if not v[2]]
        cells = "  ".join(f"{k} {v[0]}" for k, v in ck.items())
        print(f"{c.name:24s} {'FLAG ' + ','.join(flags) if flags else 'OK'}\n    {cells}", flush=True)
        out[c.name] = {"checks": {k: list(v) for k, v in ck.items()}, **r}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
