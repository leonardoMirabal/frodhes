#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The calibration SCAN of a piano TYPE on the host build: every setting rendered, every render judged, one pass/fail
table and a JSON report. Run it on a new TYPE before the user listens; run it on the existing TYPEs as the baseline.

  tools/type_scan.py --type 0                 STAGE 77, the quick profile (a few minutes)
  tools/type_scan.py --type all               every existing TYPE (0..5) one after the other
  tools/type_scan.py --type 6 --type-cc 80:112   a TYPE the tool does not know yet: its selecting CC and value
  tools/type_scan.py --type 0 --full          every key 21..108, ten velocities, every knob in 4 positions, 17-step
                                              sweeps, every effect at MIX 0 / 64 / 127
  tools/type_scan.py --smoke                  a handful of renders (tests/py/test_type_scan.py)
  --checks CLK,ALI  only these   --jobs N  parallel renders (default 2)   --no-c1  skip the 14-voice build and its cases
  --selftest   prove every detector fires on an injected defect (and stays quiet on the clean note), then stop
  -> build/type_scan/<type>/report.txt + report.json (rows of every check; the 20 worst renders keep their wav)

The binary: this tree's host build (one core, 10 voices), compiled by the tool into build/type_scan/bin; the worst-case
and per-voice CPU cases use the two-core build's voice count (-DFR_C1=1, 14 voices), built the same way.

Growl against defect. A reed's bark is HARMONIC: strong H2..H6 that grow with the strike, every partial an integer
multiple of the note's own f0. It is reported (bark: 10 log10 of H2..H5 over H1, never judged) and never fails a
check. A defect is anything else: energy OFF the harmonic series (aliasing and intermodulation, ALI), samples that do
not follow their neighbours (a click, CLK / GLT), a level that jumps or runs away (ZIP, RUN), a floor that is not
silent (NSE, IDL), a note that gets louder after the key went up (REL). So a hard strike that barks passes; the same
strike with aliased products 35 dB under the fundamental fails ALI.

The checks (codes; numbers calibrated on the existing TYPEs, see KNOWN for what they do not meet):
  CLK  clicks: hil_scan's second-difference detector (2nd difference > 6 x the local level + 2000) and HP bursts
       (1 ms windows with the energy above 8 kHz over -40 dB of the window, 10 dB over the HF 3 ms either side, above
       -90 dBFS), both outside the guards (9 ms before / 17 ms after any note, pedal or knob-less event) = 0
  GLT  glitches: the FM-1 takes' LPC detector (tests/py/glitch_lpc.py: order 24 on 20 ms frames, a residual sample
       >= 10 x the local residual, excess confined to 30 samples), outside 40 ms guards = 0
  CLP  clipping: output samples >= 32700 (dry single notes) / >= 32767 (the limiter in the chain) = 0
  RUN  runaway / instability: after the peak the 50 ms RMS never rises more than RUN_RISE dB over its own running
       minimum while the key is down; every render finite and inside the 16-bit range
  ALI  aliasing and inharmonic energy: the power off every harmonic band of the note (+-15 Hz or +-0.3 %), re H1,
       over the whole band (inh) and above 10 kHz (inh_hf): <= ALI_INH / ALI_HF dB, never under the path's white floor
       + 3 dB (tools/dry_dist.py's rule), 150..650 ms after the strike, v >= 64 (quieter notes are the 16-bit floor)
  LEV  level evenness, LUFS (BS.1770 K-weighted, 0..1 s, v100): neighbour step <= 1.5 dB, deviation from the +-3 key
       median <= 1.5 dB over keys 53..89 and <= 3 dB outside
  CBL  the same on the critical-band loudness (tools/cb_loud.py, Zwicker bands, spread of masking, smoothed over 5
       bands): step <= 3 phon, deviation <= 2.5 / 4.5. A level that agrees in LUFS and not in the bands is a tone
       change, and fails here (never LUFS alone)
  VEL  velocity monotonic: no drop > 0.5 dB between neighbouring velocities (LUFS 0..500 ms), per key; v1 .. v127 range
       reported
  TYP  the type's level against STAGE 77 on the A3-E4-A4 chord at v100 (LUFS and bands) within +-3 LU
  NSE  the noise floor: the between-partial floor (the median off-harmonic bin, NOISE at its default) <= NSE_DB re H1 or <= NSE_ABS dBFS (the
       16-bit rounding's own floor is -94 dBFS: a quiet high key is under it re H1) at v >= 64
  IDL  digital zero when idle: dry (RAW) renders end in exact zeros 4.5 s after the last key-up, with the effects
       within 2 LSB
  REL  release: the 25..325 ms after a key-up (or pedal-up) is never louder than the 300 ms before it (RMS +0.5 dB,
       peak +1 dB)
  KNB  every knob at its extremes (BARK HAMMER TONE DECAY RELEASE NOISE SPREAD RESPONSE; 0 and 127, full: 32 / 96 too),
       keys 36 / 60 / 96 at v1 / 64 / 127: CLK GLT CLP RUN ALI REL IDL as above, and the level against the default
       setting within +-18 dB (a sanity bound; a card's own level span is its job); full: a 17-step sweep, level step
       <= 3 dB, centroid step <= 30 %
  ZIP  zipper: each knob swept through the whole range under a held chord (slow, 128 steps in 1.3 s; fast, in 128 ms;
       one jump), the events not guarded: CLK = 0 and GLT = 0, and (slow / fast) the 10 ms level never moves > 1.5 dB
       more than the same chord's own decay
  REP  fast repeats (16 a second, v127, staccato holds 8 ms, mixed velocity), a ten-note release storm and all-notes-off
       (CC 123 / 120): the last 20 repeats' peak over the 12 before them <= 3 dB (build-up plateaus, runaway does not) (this code), and CLK GLT CLP IDL over the whole render
  CHD  the superposition residual of a 4-note chord (nl, reported, never judged: a growl is non-linear by design)
  Cases (the group line of the table, rows of the codes above): pedal (pedal down, eight notes, release by pedal-up,
  flutter, a held note under the pedal; REL after the pedal-up), repeat (above), chord (4 / 6 notes in three registers,
  v64 / v127, the limiter in), fx, cpu, zip, knob.
  FXS  every effect on (MIX / DEPTH 127) and off: the effects' pristine rules on this TYPE (tools/fx_pristine.py): CLK
       CLP, the effects' clamps (delay / reverb / chorus / wah) = 0, the effect adds <= 2 dB peak over the bypass (6 dB for the touch wah:
       level-dependent by design, 5 dB for a delay at feedback, 3.1 for the constant-power pan: its bound is 3.01,
       3 for the TREM slot on a bar TYPE, which is the motor's amplitude modulation), a tail that ends (the last 0.5 s under -70 dBFS; a delay's repeats do not rise)
  CPU  the two-core build's 14 voices: the cost model's us per block (cpu-proxy: voices only, P0 costs; the host's
       model, a ratio against STAGE 77 more than an absolute) <= 5500 (a bar TYPE 4500), the cost per voice against STAGE 77's (<= 1.25 x + 5 us), a 14-voice worst case
       (all fingers, pedal, every effect, 20 notes stolen): CLK CLP and every voice taken
  SLF  the detectors fire on injected defects (--selftest)

Physics-only instrument: nothing here reads recordings or samples. Known failures of the existing TYPEs:
tests/baselines/type_scan_known.json (a listed failure is reported KNOWN, not FAIL; a new one fails)."""
import argparse
import json
import math
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
import bar_measure as bm  # noqa: E402
import bar_scan as bs  # noqa: E402
import cb_loud as cb  # noqa: E402
import dry_dist as dd  # noqa: E402
import fx_pristine as fxp  # noqa: E402
import glitch_lpc as gl  # noqa: E402

warnings.filterwarnings("ignore", category=RuntimeWarning)    # (an empty band of a silent window: the row fails by its own rule)
FS = 44100
OUT = os.path.join(ROOT, "build", "type_scan")
BINDIR = os.path.join(OUT, "bin")
KNOWN_FILE = os.path.join(ROOT, "tests", "baselines", "type_scan_known.json")
Pool = multiprocessing.get_context("spawn").Pool

# ---------------------------------------------------------------- the numbers
CLP_RAW, CLP_LIM = 32700, 32767
RUN_HARD = 15.0             # dB: a swell beyond this is judged runaway even if it falls again
RUN_RISE = 3.0              # dB: the 50 ms RMS over its running minimum after the peak (beating of the tine's own pair)
ALI_INH, ALI_HF = -40.0, -50.0   # dB re H1; never under the white floor + ALI_WHITE
ALI_WHITE = 3.0
ALI_VMIN = 64
ALI_KMIN = 28               # (the lowest keys' f0 is under the 30 Hz the spectrum starts at: no off-harmonic band to read)
NSE_DB, NSE_ABS = -55.0, -88.0     # dB re H1, or dBFS: the 16-bit rounding's own floor reads -94 dBFS
LEV_STEP, LEV_DEV_MID, LEV_DEV_OUT = 1.5, 1.5, 3.0
LEV_KMIN, LEV_KMAX = 28, 103   # the Rhodes' 73 notes (tools/calib_report.py): above 103 EARLY FELT falls 3 dB a key by design of the clean top
CBL_STEP, CBL_DEV_MID, CBL_DEV_OUT = 3.0, 2.5, 4.5   # the band loudness moves more from key to key (a partial's place
                                                       # in the bands; key 44's f0 sits on the 100 Hz edge: STAGE 77
                                                       # 2.8): STAGE 77 reads 1.7 deviation, MARIMBA 2.2
VEL_DROP = 0.5
TYP_LU, TYP_PHON_BAR = 3.0, 6.0   # the strike (0..300 ms): a percussive bar's 1 s integral is its decay, not its level
                                  # (MARIMBA: +1.4 LU at the strike, -1.6 over 1 s); band loudness of a pure bar partial
                                  # against a tine's spread spectrum differs more (VIBES +2.3 LU, +5.6 phon: documented +2.5 dB)
REL_RMS, REL_PEAK = 0.5, 1.0
KNB_LEVEL = 18.0
KNB_STEP, KNB_CENT = 3.0, 0.30
ZIP_STEP = 1.5
REP_PEAK = 3.0   # dB: the repeats' peak still growing at the end (a key struck again builds, then plateaus)
FX_PK, FX_PK_FB, FX_PK_WAH, FX_PK_PAN, FX_PK_MOTOR = 2.0, 5.0, 6.0, 3.1, 3.0       # a delay at high feedback stacks its repeats on the note: 5 dB
CPU_US_ONE, CPU_US_C1, CPU_VOICE_X, CPU_VOICE_ADD = 4500, 5500, 1.25, 5.0   # the host's cost model: voices only (P0
# costs, tests/host.c cpu-proxy) for the electric pianos; a bar TYPE's is its whole block (S11: 4500). STAGE 77 reads
# 5424 at 14 voices on the two-core build and clears the FM-1's 4800 gate there, so 5500 is 'no dearer than STAGE 77'
GUARD_PRE, GUARD_POST, GUARD_LPC = 9.0, 17.0, 40.0
CC = dict(bm.CC, SPREAD=105, RESPONSE=40, TONE=28, BARK=29, HAMMER=31, DECAY=102, RELEASE=103, NOISE=104)
KNOBS = ["BARK", "HAMMER", "TONE", "DECAY", "RELEASE", "NOISE", "SPREAD", "RESPONSE"]
DEFAULT_KNOB = dict(TONE=64, BARK=64, HAMMER=64, DECAY=64, RELEASE=64, NOISE=48, SPREAD=0, RESPONSE=0)   # dd.TYPE_BASE
TAIL = 4500                 # ms of idle after the last key-up (the voices' life + the release tail)

PROFILES = {
    "smoke": dict(keys=[48, 60, 84], vels=[1, 127], alikeys=[48, 60, 84], knob_vals=[0, 127], knobs=["BARK", "DECAY"],
                  knob_keys=[60], knob_vels=[1, 127], sweep=False, zip_knobs=["BARK"], fx=["chorus", "hall"],
                  depths=[127], chords=["mid"], rep=["mid"], c1=False, nvoices=[1, 4], ped=True),
    "quick": dict(keys=sorted(set(range(53, 90)) | set(range(21, 53, 3)) | set(range(90, 109, 3))), vels=[1, 64, 100, 127],
                  alikeys=list(range(24, 106, 5)), knob_vals=[0, 127], knobs=KNOBS, knob_keys=[36, 60, 96],
                  knob_vels=[1, 64, 127], sweep=False, zip_knobs=KNOBS, fx=list(fxp.EFFECTS), depths=[127],
                  chords=["low", "mid", "high"], rep=["low", "mid", "high"], c1=True, nvoices=[1, 4, 8, 14], ped=True),
    "full": dict(keys=list(range(21, 109)), vels=[1, 8, 16, 32, 48, 64, 80, 100, 115, 127], alikeys=list(range(21, 109)),
                 knob_vals=[0, 32, 96, 127], knobs=KNOBS, knob_keys=[28, 36, 48, 60, 72, 84, 96, 105],
                 knob_vels=[1, 32, 64, 100, 127], sweep=True, zip_knobs=KNOBS, fx=list(fxp.EFFECTS), depths=[0, 64, 127],
                 chords=["low", "mid", "high"], rep=["low", "mid", "high"], c1=True, nvoices=[1, 2, 4, 6, 8, 10, 12, 14],
                 ped=True),
}
CHORDS = {"low": (33, 40, 45, 49), "mid": (57, 60, 64, 67), "high": (69, 72, 76, 79)}
CHORDS6 = {"low": (28, 35, 40, 47, 52, 56), "mid": (48, 55, 60, 64, 67, 72), "high": (60, 67, 72, 76, 79, 84)}


# ---------------------------------------------------------------- the TYPE
class Type:
    """a TYPE as the CCs select it: bar_measure's map for the existing ones (CC 80 by 32 for the electric pianos,
    CC 79 for the bar types), or an explicit CC:VALUE (--type-cc) for a new one"""

    def __init__(self, idx, name, ccnum, ccval, partials=()):
        self.idx, self.name, self.ccnum, self.ccval, self.partials = idx, name, ccnum, ccval, tuple(partials)

    @property
    def tag(self):
        return re.sub(r"[^A-Za-z0-9]+", "", self.name) or f"T{self.idx}"

    def dump(self):
        return (self.idx, self.name, self.ccnum, self.ccval, self.partials)


MODAL = (5.0, 8.0)          # x f0: where a tine's first overtone (a cantilever's 6.27 f0, the Rhodes tines 6.2 .. 6.8 on the
                            # host) and its pickup sidebands at +-f0 sit. Review 2026-11-21: BELL DYNO key 60 v127 DECAY=127
                            # read inh -31.6 dB (limit -40): a line at 6.80 f0 (-35 dB) with neighbours at 5.80 and 7.80
                            # f0, the same ratio class at keys 40 / 72 (6.18 / 6.70): the tine's own mode ringing into
                            # the 150..650 ms window at the long DECAY, not an alias (an alias does not scale with f0).
                            # ALI leaves this band out of inh and judges it on its own ceiling
MODAL_MAX = -25.0           # dB re H1: the overtone line and its sidebands together


def modal_of(ty):
    """the modal band of an electric piano TYPE (a bar TYPE's partials are all inharmonic: not judged here)"""
    return MODAL if judges_alias(ty) else None


def judges_alias(ty):
    """ALI and NSE are judged on the electric pianos: a bar TYPE's partials are inharmonic by design (vibes 1 : 4 : 10),
    so its off-harmonic energy is the instrument; its own scan is tools/bar_scan.py (S3, S7). Reported here, not judged"""
    return ty.idx not in (4, 5)


def resolve_type(arg, cc=None, partials=()):
    n = bm.n_types()
    if cc:
        c, v = cc.split(":")
        idx = int(arg) if str(arg).isdigit() else 99
        return Type(idx, str(arg) if not str(arg).isdigit() else f"TYPE{arg}", int(c), int(v), partials)
    if str(arg).isdigit():
        i = int(arg)
        if i >= 6:
            raise SystemExit(f"TYPE {i}: the tool does not know its selecting CC yet, pass --type-cc CC:VALUE")
    else:
        names = [bm.type_name(k) for k in range(n)]
        hit = [k for k, s in enumerate(names) if s.upper().startswith(str(arg).upper())]
        if len(hit) != 1:
            raise SystemExit(f"TYPE {arg}: {names}")
        i = hit[0]
    return Type(i, bm.type_name(i), bm.type_ccnum(i), bm.type_cc(i), partials)


# ---------------------------------------------------------------- binaries and rendering
def _newest_source():
    t = 0.0
    for d in ("firmware/src", "firmware/gen"):
        for r, _, fs in os.walk(os.path.join(ROOT, d)):
            for f in fs:
                if f.endswith((".c", ".h")):
                    t = max(t, os.path.getmtime(os.path.join(r, f)))
    return t


def build_bin(name, defs=()):
    """build/type_scan/bin/<name>: frodhes.c's host build with defs, compiled again only when a source is newer"""
    os.makedirs(BINDIR, exist_ok=True)
    path = os.path.join(BINDIR, name)
    stamp = path + ".defs"
    old = open(stamp).read() if os.path.exists(stamp) else None
    if os.path.exists(path) and os.path.getmtime(path) > _newest_source() and old == " ".join(defs):
        return path
    tmp = f"{path}.tmp{os.getpid()}"
    subprocess.run(["nice", "-n", "10", "cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"',
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"), *defs,
                    "-o", tmp, os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    os.replace(tmp, path)
    open(stamp, "w").write(" ".join(defs))
    return path


BUILDS = {"one": (), "c1": ("-DFR_C1=1",)}


def render(binary, events, end_ms, env=None, keep=None):
    """events: [(ms, "midi 0x90 60 100"), ...]; returns (x (n, 2) float, host log). keep: a path to keep the wav at"""
    d = tempfile.mkdtemp(prefix="ts", dir=OUT)
    try:
        out = os.path.join(d, "r")
        lines = sorted(((int(t), s) for t, s in events), key=lambda e: e[0])
        with open(out + ".txt", "w") as f:
            f.write("".join(f"{t} {s}\n" for t, s in lines) + f"{int(end_ms)} end\n")
        e = dict(os.environ)
        e.pop("FRODHES_FLASH", None)
        e.update(env or {})
        p = subprocess.run(["nice", "-n", "10", binary, out, out + ".txt"], capture_output=True, text=True, env=e)
        if p.returncode != 0 or not os.path.exists(out + ".wav"):
            raise RuntimeError(f"host render failed rc {p.returncode}: {(p.stdout + p.stderr)[-500:]}")
        w = wave.open(out + ".wav")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
        if keep:
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy(out + ".wav", keep)
        return x, p.stdout + p.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def setup(ty, mode, t0=20, **knobs):
    """the voicing at dry_dist.TYPE_BASE, the TYPE, then mode: "raw" (CC 119: every effect and the limiter bypassed) or
    "norm" (tools/fx_pristine.py's every-effect-off chain, the limiter in); knobs: name=value after the TYPE"""
    ev = [(t0, f"midi 0xB0 {c} {v}") for c, v in dd.TYPE_BASE]
    ev.append((t0 + 5, f"midi 0xB0 {ty.ccnum} {ty.ccval}"))
    if ty.idx in (4, 5):
        ev.append((t0 + 6, "midi 0xB0 47 0"))                   # the bar TYPEs' motor off
    if mode == "raw":
        ev.append((t0 + 8, "midi 0xB0 119 127"))
    else:
        ev += [(t0 + 8, f"midi 0xB0 {c} {v}") for c, v in fxp.CLEAN[len(dd.TYPE_BASE):]]
    ev += [(t0 + 10, f"midi 0xB0 {CC[k]} {int(v)}") for k, v in knobs.items()]
    return ev


def strikes_at(keys, vel, gap=1700, hold=900, t0=300):
    """[(key, vel, on_ms, off_ms)], the events and the render's end"""
    st, ev = [], []
    for i, n in enumerate(keys):
        v = vel[i % len(vel)] if isinstance(vel, (list, tuple)) else vel
        a = t0 + i * gap
        st.append((n, v, a, a + hold))
        ev += bm.note(a, n, v, hold)
    return st, ev, (st[-1][3] if st else t0) + TAIL


def guards(events):
    return [t for t, s in events if s.startswith(("midi 0x90", "midi 0x80", "midi 0xB0 64", "midi 0xB0 123",
                                                  "midi 0xB0 120"))]


# ---------------------------------------------------------------- the detectors (pure: arrays in, numbers out)
def mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def seg(x, t_ms, dur_ms):
    return bs.seg(x, t_ms, dur_ms)


HP_FLOOR_DB = -78.0         # dBFS: the 8 kHz+ energy of a burst window must be 4 LSB RMS or more (see det_hp)


def det_hp(x, ev_ms, guard_ms=int(GUARD_POST)):
    """HP bursts, bar_scan.click_count's rule with a higher floor: 1 ms windows whose energy above 8 kHz is over -40 dB
    of the window's, over 10 dB above the HF 3 ms either side, and above HP_FLOOR_DB (bar_scan: -90). Review
    2026-11-21: at -90 dBFS (1.5 LSB RMS) a one-LSB stair step of a quiet bass note (BELL DYNO key 36 v1 TONE=0, 224 ms,
    HF -86.5 dBFS, second difference 11 of a 115 LSB signal) counted as a click; a click that is audible has 4 LSB or
    more of HF energy even on a quiet note"""
    from scipy.signal import butter, sosfilt
    m = mono(x)
    hp = sosfilt(butter(4, 8000, "hp", fs=FS, output="sos"), m)
    w = FS // 1000
    n = len(m) // w
    ea = (m[:n * w].reshape(n, w) ** 2).mean(axis=1)
    eh = (hp[:n * w].reshape(n, w) ** 2).mean(axis=1)
    share = 10 * np.log10(eh / (ea + 1e-20) + 1e-20)
    nb = np.maximum(np.concatenate([eh[:3], eh[:-3]]), np.concatenate([eh[3:], eh[-3:]]))
    g = np.ones(n, bool)
    for t in ev_ms:
        i = int(t * FS / 1000) // w
        g[max(0, i - bs.PRE_MS):i + guard_ms] = False
    loud = (ea > (32767 * 1e-4) ** 2) & (eh > (32767 * 10 ** (HP_FLOOR_DB / 20)) ** 2)
    return int((g & loud & (share > -40) & (eh > 10 * nb)).sum())


def det_clicks(x, ev_ms):
    """CLK: (second-difference hits, HP bursts) outside the guards: bar_scan.click_count's second difference (S1) and
    det_hp"""
    return bs.click_count(x, ev_ms, int(GUARD_POST))[0], det_hp(x, ev_ms)


def det_lpc(x, ev_ms):
    """GLT: the LPC glitch detector on both channels (one for a mono array), the events outside the guards: returns the
    events' times (s)"""
    chans = [x[:, 0], x[:, 1]] if x.ndim == 2 else [x]
    hit = set()
    for c in chans:
        for i in gl.events(c, FS):
            hit.add(int(i))
    keep = []
    for i in sorted(hit):
        t = i * 1000.0 / FS
        if any(-GUARD_LPC <= t - g <= GUARD_LPC for g in ev_ms):
            continue
        if keep and i - keep[-1] < FS * 0.003:
            continue
        keep.append(i)
    return [i / FS for i in keep]


def det_clip(x, limit):
    return int((np.abs(x) >= limit).sum())


def det_swell(x, t_on, t_off):
    """(the largest rise of the 50 ms RMS over its running minimum after its peak, dB; still rising at the end of the
    held note: the last 150 ms over the 150 ms before it by more than 1 dB)"""
    m = seg(mono(x), t_on, t_off - t_on)
    h = int(0.05 * FS)
    n = len(m) // h
    if n < 6:
        return 0.0, False
    e = 10 * np.log10(np.mean(m[:n * h].reshape(n, h) ** 2, axis=1) + 1e-3)
    pk = int(np.argmax(e[:max(3, int(0.15 / 0.05))]))
    lo = np.minimum.accumulate(e[pk:])
    return float(np.max(e[pk:] - lo)), bool(e[-3:].mean() - e[-6:-3].mean() > 1.0)


def det_rise(x, t_on, t_off, rise=RUN_RISE):
    """RUN: runaway, not swell. A note whose level grows and is still growing when the key goes up (or grows by more
    than RUN_HARD) is unstable; one that swells and then falls is not (review 2026-11-21: the low bars of VIBES and
    MARIMBA swell 5-9 dB over 0.4-0.7 s, velocity-independent, then decay, and counted as runaway). Returns the rise in
    dB when it is runaway, else 0"""
    r, rising = det_swell(x, t_on, t_off)
    return r if (rising and r > rise) or r > RUN_HARD else 0.0


def det_release(x, t_off):
    """REL: (RMS dB, peak dB) of the 25..325 ms after a release over the 300 ms before it (> 0: louder after)"""
    m = mono(x)
    pre, post = seg(m, t_off - 300, 300), seg(m, t_off + 25, 300)
    if not len(pre) or not len(post):
        return -99.0, -99.0
    r = lambda a: 20 * math.log10(math.sqrt(np.mean(a ** 2)) + 1e-9)       # noqa: E731
    pk = lambda a: 20 * math.log10(np.max(np.abs(a)) + 1e-9)               # noqa: E731
    return r(post) - r(pre), pk(seg(m, t_off + 25, 400)) - pk(pre)


def det_idle(x, tail_ms=1000):
    """IDL: (peak abs, nonzero samples) over the last tail_ms of the render"""
    a = np.abs(x[-int(tail_ms * FS / 1000):])
    return float(a.max()), int((a > 0).sum())


def alias_cells(window, f0, partials=(), modal=None):
    """ALI: (inh, inh_hf, inhw, h1, bark, floor, modal) of a held note's window (mono, int16 units; partials: extra bands, the
    modes' ratios to f0, counted as the note's own): the power off the harmonic bands
    re H1 over 30 Hz .. 20 kHz and over 10 .. 20 kHz, the white floor, H1's dBFS, and the growl (H2..H5 over H1, dB:
    harmonic energy, reported never judged)"""
    p, fr = dd.spectrum(window)
    fund = dd.tuned(p, fr, [f0])[0]
    on, h1m = np.zeros(len(fr), bool), np.zeros(len(fr), bool)
    h1m |= np.abs(fr - fund) < max(dd.IBW, fund * dd.IREL)
    for k in range(1, int(20000 / fund) + 1):
        on |= np.abs(fr - k * fund) < max(dd.IBW, k * fund * dd.IREL)
    mod = np.zeros(len(fr), bool)
    if modal:                                # the tine's first overtone and its pickup sidebands: see MODAL
        mod = (fr > modal[0] * fund) & (fr < modal[1] * fund) & ~on
        on |= mod
    for r in partials:                       # the TYPE's own modes (--partials: ratios of f0), physical, not a defect
        on |= np.abs(fr - r * fund) < max(dd.IBW, r * fund * dd.IREL)
    a = p[h1m].sum() + 1e-30
    use = (fr > 30) & (fr < 20000)
    hf = (fr > 10000) & (fr < 20000)
    b, bh = p[use & ~on].sum(), p[hf & ~on].sum()
    inh, inhw = dd.inharm_white(p, fr, [fund], len(window))
    off = use & ~on
    floor = 10 * np.log10(float(np.median(p[off])) / np.log(2) * int(off.sum()) / a + 1e-30)   # (inhw, uncapped)
    _, h1db = dd.harm_cells(window, f0)[0::3]
    gr = 0.0
    for k in range(2, 6):
        m = (fr > k * fund * 0.97) & (fr < k * fund * 1.03)
        gr += p[m].max() if m.any() else 0.0
    return (float(10 * np.log10(b / a + 1e-30)), float(10 * np.log10(bh / a + 1e-30)), inhw, float(h1db),
            float(10 * np.log10(gr / (p[h1m].max() + 1e-30) + 1e-30)), float(floor),
            float(10 * np.log10(p[mod].sum() / a + 1e-30)))


def ali_limits(inhw):
    return max(ALI_INH, inhw + ALI_WHITE), max(ALI_HF, inhw + ALI_WHITE)


def level_lufs(x, t_ms, dur_ms=1000):
    return bm.lufs(seg(x, t_ms, dur_ms))


SMOOTH = np.array([1, 4, 6, 4, 1]) / 16.0


def level_phon(x, t_ms, dur_ms=1000):
    """critical-band loudness of a window (tools/cb_loud.py) on the band powers smoothed over 5 bands: cb_loud's 24 bands
    have hard edges, and a partial that sits on one (STAGE 77 key 90, f0 = 1480 Hz = the 11th band's edge) split over two
    bands reads 4.7 phon louder, an artefact of the edge, not of the note (its LUFS is smooth); the binomial smoothing
    (an excitation pattern) takes it to 0.3 phon and keeps a real tone change"""
    b = cb.bands(seg(mono(x), t_ms, dur_ms))
    return cb.phon(np.convolve(np.pad(b, 2, mode="edge"), SMOOTH, "valid"))


def det_jump(x, t0_ms, t1_ms, ref=None):
    """ZIP: the largest 10 ms-to-10 ms level move (dB) in [t0, t1] beyond the move the same window shows in ref (the
    chord's own decay, a render without the sweep), in dB; ref None: the raw largest move"""
    def env(a):
        m = seg(mono(a), t0_ms, t1_ms - t0_ms)
        h = int(0.01 * FS)
        n = len(m) // h
        return 10 * np.log10(np.mean(m[:n * h].reshape(n, h) ** 2, axis=1) + 1.0)
    d = np.abs(np.diff(env(x)))
    if ref is not None:
        d = np.abs(np.diff(env(x)) - np.diff(env(ref)))
    return float(d.max()) if len(d) else 0.0


# ---------------------------------------------------------------- rows
def row(check, case, value, limit, ok, info="", judged=True):
    return dict(check=check, case=case, value=None if value is None else round(float(value), 3),
                limit=None if limit is None else round(float(limit), 3), ok=bool(ok), info=info, judged=judged)


def common(x, ev, st, case, raw, hold_runs=True, lpc=True, rel=True, idle=True):
    """the render-wide checks CLK GLT CLP RUN REL IDL of a strike list"""
    rows = []
    g = guards(ev)
    a, b = det_clicks(x, g)
    rows.append(row("CLK", case, a + b, 0, a + b == 0, f"d2 {a}, hp {b}"))
    if lpc:
        ev_l = det_lpc(x, g)
        rows.append(row("GLT", case, len(ev_l), 0, not ev_l, "at " + ", ".join(f"{t:.3f}" for t in ev_l[:6]) if ev_l else ""))
    n = det_clip(x, CLP_RAW if raw else CLP_LIM)
    rows.append(row("CLP", case, n, 0, n == 0))
    if hold_runs:
        worst, swell = 0.0, 0.0
        for (_, _, on, off) in st:
            if off - on >= 600:
                worst = max(worst, det_rise(x, on, off))
                swell = max(swell, det_swell(x, on, off)[0])
        rows.append(row("RUN", case, worst, RUN_RISE, worst <= RUN_RISE, f"largest swell {swell:.1f} dB (reported)"))
    if rel:
        w = [det_release(x, off) for (_, _, on, off) in st if off - on >= 500]
        if w:
            r, p = max(a for a, _ in w), max(b for _, b in w)
            rows.append(row("REL", case, max(r - REL_RMS, p - REL_PEAK) + 0.0, 0, r <= REL_RMS and p <= REL_PEAK,
                            f"rms {r:+.2f} dB, peak {p:+.2f} dB"))
    if idle:
        pk, nz = det_idle(x)
        lim = 0 if raw else 2
        rows.append(row("IDL", case, pk, lim, pk <= lim, f"{nz} nonzero samples in the last second"))
    return rows


# ---------------------------------------------------------------- the cases
def case_keys(spec):
    """every key at one velocity: the common checks, levels, aliasing cells"""
    ty, vel, keys, alikeys = Type(*spec["ty"]), spec["vel"], spec["keys"], spec["alikeys"]
    judge_ali = judges_alias(ty)
    st, ev, end = strikes_at(keys, vel)
    x, log = render(spec["bin"], setup(ty, "raw") + ev, end, keep=spec.get("keep"))
    rows = common(x, ev, st, f"keys v{vel}", True)
    data = dict(keys={}, vel=vel)
    for (n, v, on, off) in st:
        o = bs.onset_ms(on)
        d = dict(lufs=level_lufs(x, o, 1000), phon=level_phon(x, o, 1000), lufs5=level_lufs(x, o, 500))
        if n in alikeys and n >= ALI_KMIN and vel >= ALI_VMIN:
            w = mono(x)[int((o + 150) * FS / 1000):int((o + 650) * FS / 1000)]
            inh, hf, inhw, h1, bark, floor, mdl = alias_cells(w, dd.f0(n), ty.partials, modal_of(ty))
            li, lh = ali_limits(inhw)
            d.update(inh=inh, inh_hf=hf, inhw=inhw, h1=h1, bark=bark, floor=floor, modal=mdl)
            rows.append(row("ALI", f"v{vel} key {n}", max(inh - li, hf - lh, mdl - MODAL_MAX), 0,
                            inh <= li and hf <= lh and mdl <= MODAL_MAX,
                            f"inh {inh:.1f} (limit {li:.1f}), hf {hf:.1f} ({lh:.1f}), modal {mdl:.1f} ({MODAL_MAX:.0f}), bark {bark:.1f} dB",
                            judged=judge_ali))
            nse_ok = floor <= NSE_DB or floor + h1 <= NSE_ABS
            rows.append(row("NSE", f"v{vel} key {n}", floor + h1, NSE_ABS, nse_ok, f"between-partial floor {floor:.1f} dB re H1 = {floor + h1:.1f} dBFS", judged=judge_ali))
        data["keys"][n] = d
    return rows, data


def spread_rows(data_by_vel, vel_ref=100, scoped=True):
    """LEV CBL VEL from the key renders' data"""
    rows = []
    d = data_by_vel.get(vel_ref)
    if d:
        ks = sorted(d["keys"])
        for code, field, st_lim, dm, do in (("LEV", "lufs", LEV_STEP, LEV_DEV_MID, LEV_DEV_OUT),
                                            ("CBL", "phon", CBL_STEP, CBL_DEV_MID, CBL_DEV_OUT)):
            ks = [k for k in sorted(d["keys"]) if k in range(LEV_KMIN, LEV_KMAX + 1)] if scoped else sorted(d["keys"])
            lv = {k: d["keys"][k][field] for k in ks}
            worst_step, worst_dev = 0.0, 0.0
            for a, b in zip(ks, ks[1:]):
                if b - a == 1:
                    worst_step = max(worst_step, abs(lv[b] - lv[a]))
            for k in ks:
                nb = [lv[j] for j in ks if j != k and abs(j - k) <= 3]
                if len(nb) >= 2:
                    dev = abs(lv[k] - float(np.median(nb)))
                    lim = dm if 53 <= k <= 89 else do
                    worst_dev = max(worst_dev, dev - lim)
            rows.append(row(code, f"neighbour step v{vel_ref}", worst_step, st_lim, worst_step <= st_lim,
                            f"{field}, keys {ks[0]}..{ks[-1]}"))
            rows.append(row(code, f"median deviation v{vel_ref}", worst_dev, 0, worst_dev <= 0,
                            f"{field}, worst excess over {dm} (keys 53..89) / {do} (outside)"))
    vels = sorted(data_by_vel)
    worst, rng = 0.0, []
    if len(vels) >= 2:
        for k in data_by_vel[vels[0]]["keys"]:
            seq = [data_by_vel[v]["keys"][k]["lufs5"] for v in vels if k in data_by_vel[v]["keys"]]
            for a, b in zip(seq, seq[1:]):
                worst = max(worst, a - b)
            rng.append(seq[-1] - seq[0])
        rows.append(row("VEL", "monotonic", worst, VEL_DROP, worst <= VEL_DROP,
                        f"v{vels[0]}..v{vels[-1]} range {min(rng):.1f} .. {max(rng):.1f} dB over the keys"))
    return rows


def case_type_level(spec):
    """TYP: the A3-E4-A4 chord at v100, this type's level (the caller compares it with STAGE 77's)"""
    ty = Type(*spec["ty"])
    ev = []
    for n in (57, 64, 69):
        ev += bm.note(300, n, 100, 1500)
    x, _ = render(spec["bin"], setup(ty, "raw") + ev, 3000)
    o = bs.onset_ms(300)
    return [], dict(lufs=level_lufs(x, o, 300), phon=level_phon(x, o, 300), lufs1=level_lufs(x, o, 1000),
                    phon1=level_phon(x, o, 1000))


def case_knob(spec):
    ty, k, val = Type(*spec["ty"]), spec["knob"], spec["val"]
    keys, vels = spec["keys"], spec["vels"]
    pairs = [(n, v) for n in keys for v in vels]
    st, ev, end = strikes_at([p[0] for p in pairs], [p[1] for p in pairs])
    # (strikes_at cycles vels over keys: pair i gets vel[i % len]: build the lists explicitly)
    st = [(p[0], p[1], 300 + i * 1700, 300 + i * 1700 + 900) for i, p in enumerate(pairs)]
    ev = [e for (n, v, a, b) in st for e in bm.note(a, n, v, b - a)]
    end = st[-1][3] + TAIL
    x, _ = render(spec["bin"], setup(ty, "raw", **{k: val}) + ev, end)
    rows = common(x, ev, st, f"{k}={val}", True)
    lv = {}
    for (n, v, on, off) in st:
        o = bs.onset_ms(on)
        lv[(n, v)] = level_lufs(x, o, 500)
        if v >= ALI_VMIN and n >= ALI_KMIN:
            w = mono(x)[int((o + 150) * FS / 1000):int((o + 650) * FS / 1000)]
            inh, hf, inhw, h1, bark, floor, mdl = alias_cells(w, dd.f0(n), ty.partials, modal_of(ty))
            li, lh = ali_limits(inhw)
            rows.append(row("ALI", f"{k}={val} key {n} v{v}", max(inh - li, hf - lh, mdl - MODAL_MAX), 0,
                            inh <= li and hf <= lh and mdl <= MODAL_MAX,
                            f"inh {inh:.1f} ({li:.1f}), hf {hf:.1f} ({lh:.1f}), modal {mdl:.1f} ({MODAL_MAX:.0f}), bark {bark:.1f} dB",
                            judged=judges_alias(ty)))
    return rows, dict(knob=k, val=val, lv={f"{n}/{v}": l for (n, v), l in lv.items()})


def case_knob_default(spec):
    ty = Type(*spec["ty"])
    pairs = [(n, v) for n in spec["keys"] for v in spec["vels"]]
    st = [(p[0], p[1], 300 + i * 1700, 300 + i * 1700 + 900) for i, p in enumerate(pairs)]
    ev = [e for (n, v, a, b) in st for e in bm.note(a, n, v, b - a)]
    x, _ = render(spec["bin"], setup(ty, "raw") + ev, st[-1][3] + 1500)
    return [], dict(lv={f"{n}/{v}": level_lufs(x, bs.onset_ms(a), 500) for (n, v, a, b) in st})


def case_sweep(spec):
    """KNB continuity: one knob in 17 steps, key 60 v100 struck at each (a fresh strike 1.7 s apart)"""
    ty, k = Type(*spec["ty"]), spec["knob"]
    vals = [min(127, i * 8) for i in range(17)]
    ev, lv, ce = [], [], []
    t = 300
    for v in vals:
        ev.append((t - 60, f"midi 0xB0 {CC[k]} {v}"))
        ev += bm.note(t, 60, 100, 900)
        t += 1700
    x, _ = render(spec["bin"], setup(ty, "raw") + ev, t + 500)
    t = 300
    for v in vals:
        o = bs.onset_ms(t)
        lv.append(level_lufs(x, o, 500))
        ce.append(bm.centroid(seg(x, o, 500)))
        t += 1700
    ds = max(abs(a - b) for a, b in zip(lv, lv[1:]))
    dc = max(abs(a - b) / max(a, b) for a, b in zip(ce, ce[1:]))
    return [row("KNB", f"{k} 17-step level", ds, KNB_STEP, ds <= KNB_STEP, f"levels {min(lv):.1f} .. {max(lv):.1f} LUFS"),
            row("KNB", f"{k} 17-step centroid", dc, KNB_CENT, dc <= KNB_CENT, f"{min(ce):.0f} .. {max(ce):.0f} Hz")], {}


def case_zip(spec):
    """ZIP: a held chord, one knob swept slow, fast and as a jump; the knob events not guarded"""
    ty, k = Type(*spec["ty"]), spec["knob"]
    chord = (60, 64, 67)
    rows = []
    base = [e for n in chord for e in bm.note(300, n, 100, 9800)]
    ref, _ = render(spec["bin"], setup(ty, "raw") + base, 11000)
    sw = []
    for t in range(0, 128):
        sw.append((1000 + 10 * t, f"midi 0xB0 {CC[k]} {t}"))              # slow up: 10 ms a step
    for t in range(0, 128):
        sw.append((3500 + 10 * t, f"midi 0xB0 {CC[k]} {127 - t}"))        # slow down
    for t in range(0, 128):
        sw.append((6000 + t, f"midi 0xB0 {CC[k]} {t}"))                   # fast up: 1 ms a step
    for t in range(0, 128):
        sw.append((7000 + t, f"midi 0xB0 {CC[k]} {127 - t}"))             # fast down
    sw += [(8500, f"midi 0xB0 {CC[k]} 127"), (9200, f"midi 0xB0 {CC[k]} 0")]   # jumps
    x, _ = render(spec["bin"], setup(ty, "raw") + base + sw, 11000)
    g = guards(base)                                                       # (the note's own events only)
    a, b = det_clicks(x, g)
    rows.append(row("ZIP", f"{k} clicks", a + b, 0, a + b == 0, f"d2 {a}, hp {b}"))
    ev_l = det_lpc(x, g)
    rows.append(row("ZIP", f"{k} glitches", len(ev_l), 0, not ev_l, "at " + ", ".join(f"{t:.3f}" for t in ev_l[:6]) if ev_l else ""))
    for name, t0, t1 in (("slow up", 900, 2400), ("slow down", 3400, 4900), ("fast", 5900, 7300)):
        j = det_jump(x, t0, t1, ref)
        rows.append(row("ZIP", f"{k} {name} level step", j, ZIP_STEP, j <= ZIP_STEP, "10 ms level move beyond the held chord's"))
    return rows, {}


def case_pedal(spec):
    ty = Type(*spec["ty"])
    ev, st = [], []
    ev.append((200, "midi 0xB0 64 127"))
    t = 400
    for i, n in enumerate((48, 52, 55, 60, 64, 67, 72, 76)):
        ev += bm.note(t, n, 100 if i % 2 else 127, 150)
        t += 250
    ev.append((3200, "midi 0xB0 64 0"))                                       # the pedal up: the held tails damp
    for i in range(5):                                                          # flutter
        ev.append((3800 + 240 * i, "midi 0xB0 64 127"))
        ev.append((3920 + 240 * i, "midi 0xB0 64 0"))
    ev += bm.note(5300, 60, 127, 3000) + [(5400, "midi 0xB0 64 127"), (7000, "midi 0xB0 64 0")]   # half-pedal-ish: pedal under a held note
    x, _ = render(spec["bin"], setup(ty, "norm") + ev, 7000 + TAIL)
    rows = common(x, ev, [], "pedal", False, rel=False)
    r, p = det_release(x, 3200)
    rows.append(row("REL", "pedal up", max(r - REL_RMS, p - REL_PEAK), 0, r <= REL_RMS and p <= REL_PEAK,
                    f"rms {r:+.2f} dB, peak {p:+.2f} dB"))
    return rows, {}


def case_repeat(spec):
    ty, reg = Type(*spec["ty"]), spec["reg"]
    key = {"low": 36, "mid": 60, "high": 96}[reg]
    ev, onsets = [], []
    for i in range(40):                                                         # 16 a second, v127
        t = 300 + i * 62
        ev += bm.note(t, key, 127, 40)
        onsets.append(t)
    t0 = 300 + 40 * 62 + 800
    for i in range(30):                                                         # staccato, alternating velocity
        t = t0 + i * 95
        ev += bm.note(t, key + (i % 3), 1 if i % 2 else 127, 8)
    t1 = t0 + 30 * 95 + 600
    chord = [key + k for k in (0, 2, 4, 5, 7, 9, 11, 12, 14, 16)]
    for n in chord:                                                             # ten notes, all released together
        ev.append((t1, f"midi 0x90 {n} 110"))
        ev.append((t1 + 700, f"midi 0x80 {n} 0"))
    t2 = t1 + 2200
    for n in chord:
        ev.append((t2, f"midi 0x90 {n} 110"))
    ev.append((t2 + 600, "midi 0xB0 123 0"))                                    # all notes off
    t3 = t2 + 2200
    for n in chord:
        ev.append((t3, f"midi 0x90 {n} 110"))
    ev.append((t3 + 600, "midi 0xB0 120 0"))                                    # all sound off
    x, _ = render(spec["bin"], setup(ty, "norm") + ev, t3 + 600 + TAIL)
    rows = common(x, ev, [], f"repeat {reg}", False, rel=False)
    m = mono(x)
    pk = [20 * math.log10(np.max(np.abs(seg(m, bs.onset_ms(t), 60))) + 1e-9) for t in onsets]
    # a struck bar or tine rings on and the next strike adds to it: the level builds, then plateaus (VIBES key 36: first
    # strike 69 dB, plateau 78 dB, its own sub-bass swell). Runaway is growth that does not stop: the last 20 repeats'
    # peak against the 12 before them
    worst = max(pk[20:]) - max(pk[8:20])
    rows.append(row("REP", f"repeat {reg} peak", worst, REP_PEAK, worst <= REP_PEAK,
                    f"first {pk[0]:.1f}, plateau {max(pk[8:20]):.1f}, end {max(pk[20:]):.1f} dB (peak per repeat, 60 ms)"))
    return rows, {}


def case_chord(spec):
    ty, reg = Type(*spec["ty"]), spec["reg"]
    rows = []
    for name, notes in (("4", CHORDS[reg]), ("6", CHORDS6[reg])):
        for v in (64, 127):
            ev = [e for n in notes for e in bm.note(300, n, v, 1800)]
            x, _ = render(spec["bin"], setup(ty, "norm") + ev, 2100 + TAIL)
            st = [(n, v, 300, 2100) for n in notes[:1]]
            rows += common(x, ev, st, f"chord {reg}/{name} v{v}", False, rel=True)
            if name == "4":
                a = [e for n in notes for e in bm.note(300, n, v, 1800)]
                b = bm.note(300, notes[-1] + 5, v, 1800)
                xa, _ = render(spec["bin"], setup(ty, "raw") + a, 2500)
                xb, _ = render(spec["bin"], setup(ty, "raw") + b, 2500)
                xab, _ = render(spec["bin"], setup(ty, "raw") + a + b, 2500)
                i0, i1 = int(0.5 * FS), int(2.0 * FS)
                r = xab[i0:i1] - xa[i0:i1] - xb[i0:i1]
                nl = 10 * math.log10(np.mean(r * r) / 32768 ** 2 + 1e-20)
                ref = 10 * math.log10(np.mean(xab[i0:i1] ** 2) / 32768 ** 2 + 1e-20)
                rows.append(row("CHD", f"chord {reg}/4 v{v} nl", nl - ref, None, True,
                                f"superposition residual {nl - ref:.1f} dB re the chord (reported)", judged=False))
    return rows, {}


def case_fx(spec):
    """FXS: one effect on and (bypass) off, at one depth: fx_pristine's rules on this TYPE"""
    ty, name, d = Type(*spec["ty"]), spec["fx"], spec["depth"]
    ccs, exempt, lvl0 = fxp.EFFECTS[name]
    ccs = [(n, d if v == fxp.D else v) for n, v in ccs]
    notes = [(500, n, 100) for n in fxp.CHORD] + [(500, 72, 127)]
    ev = [e for t, n, v in notes for e in bm.note(t, n, v, 1500)]
    out = {}
    for byp in (False, True):
        s = setup(ty, "norm") + [(60, f"midi 0xB0 {c} {v}") for c, v in ccs]
        if byp:
            s.append((90, "midi 0xB0 111 127"))
        x, log = render(spec["bin"], s + ev, 2000 + 6500)
        out[byp] = (x, log)
    (x, log), (xb, _) = out[False], out[True]
    rows = []
    c = f"{name} {d}"
    g = guards(ev)
    a, b = det_clicks(x, g)
    rows.append(row("FXS", f"{c} clicks", a + b, 0, a + b == 0, f"d2 {a}, hp {b}"))
    n = det_clip(x, CLP_LIM)
    rows.append(row("FXS", f"{c} clip", n, 0, n == 0))
    m = re.search(r"fx-clip delay (\d+) reverb (\d+) chorus (\d+) limiter (\d+)(?: wah (\d+))?", log)
    if m:
        cl = [int(v or 0) for v in m.groups()]
        bad = cl[0] + cl[1] + cl[2] + (cl[4] if len(cl) > 4 else 0)
        rows.append(row("FXS", f"{c} clamps", bad, 0, bad == 0, f"delay {cl[0]} reverb {cl[1]} chorus {cl[2]} wah {cl[4]}; limiter {cl[3]}"))
    pk = 20 * math.log10(np.max(np.abs(x)) + 1) - 20 * math.log10(np.max(np.abs(xb)) + 1)
    fb = "fb" in name or name == "inf"
    lim = FX_PK_FB if fb else FX_PK_WAH if name == "wah" else FX_PK_PAN if name == "pan" else \
        FX_PK_MOTOR if name == "trem" and ty.idx in (4, 5) else FX_PK
    ok = pk <= lim or name.startswith(("eq", "filter")) and d >= 127
    rows.append(row("FXS", f"{c} peak over bypass", pk, lim, ok, f"{pk:+.2f} dB"))
    rms = lambda a: 20 * math.log10(math.sqrt(np.mean(a ** 2)) / 32768 + 1e-9)       # noqa: E731
    tail, prev = rms(x[-int(0.5 * FS):]), rms(x[-int(1.0 * FS):-int(0.5 * FS)])
    if fb:      # high feedback and the infinite hold ring on by design (reported); a plain delay's repeats must not rise
        rows.append(row("FXS", f"{c} tail", tail - prev, 0.3, tail - prev <= 0.3, f"{prev:.1f} -> {tail:.1f} dBFS", judged=False))
    elif name.startswith("delay"):
        rows.append(row("FXS", f"{c} tail decays", tail - prev, 0.3, tail - prev <= 0.3, f"{prev:.1f} -> {tail:.1f} dBFS"))
    else:
        rows.append(row("FXS", f"{c} tail ends", tail, -70, tail <= -70, f"{tail:.1f} dBFS in the last 0.5 s"))
    return rows, {}


PROXY = re.compile(r"cpu-proxy max (\d+) us per block \(([^)]*)\)")


def proxy_us(log):
    m = PROXY.search(log)
    if not m:
        return None
    return int(m.group(1))


FX_ALL = [(85, 64), (86, 100), (17, 64), (21, 127), (18, 100), (88, 40), (89, 90), (26, 80), (95, 40), (91, 100),
          (106, 127), (107, 100), (70, 40), (74, 100), (71, 64), (81, 80), (34, 90), (35, 90), (37, 90)]


def case_cpu(spec):
    """CPU: the proxy at n voices (two-core build, every effect planned), then the 14-voice worst case"""
    ty = Type(*spec["ty"])
    n = spec["n"]
    keys = [36 + 3 * i for i in range(n)]
    ev = [e for k in keys for e in bm.note(300, k, 127, 1500)]
    x, log = render(spec["bin"], setup(ty, "raw") + ev, 2600, env={"FRODHES_FXUS": "1080"})
    return [], dict(n=n, us=proxy_us(log))


def cpu_limit(ty):
    return CPU_US_ONE if ty.idx in (4, 5) else CPU_US_C1


def case_worst(spec):
    ty = Type(*spec["ty"])
    nv = spec["voices"]
    ev = [(200, "midi 0xB0 64 127")]
    keys = [31 + 4 * i for i in range(nv)]
    for i, k in enumerate(keys):
        ev.append((400 + (i // 4) * 6, f"midi 0x90 {k} 127"))                  # a blur of fingers
    for i in range(20):                                                          # then 20 more: steals
        ev.append((1500 + i * 55, f"midi 0x90 {48 + i * 2} 127"))
    ev.append((3500, "midi 0xB0 123 0"))
    ev.append((3800, "midi 0xB0 64 0"))
    s = setup(ty, "norm") + [(60, f"midi 0xB0 {c} {v}") for c, v in FX_ALL]
    x, log = render(spec["bin"], s + ev, 3800 + TAIL, env={"FRODHES_FXUS": "1080"})
    rows = []
    g = guards(ev)
    a, b = det_clicks(x, g)
    rows.append(row("CPU", "worst case clicks", a + b, 0, a + b == 0, f"d2 {a}, hp {b} (steals guarded)"))
    n = det_clip(x, CLP_LIM)
    rows.append(row("CPU", "worst case clip", n, 0, n == 0))
    us = proxy_us(log)
    lim = cpu_limit(ty)
    rows.append(row("CPU", "worst case proxy", us, lim, us is not None and us <= lim, f"{us} us per block (the cost model, 14 voices, 20 steals)"))
    m = re.search(r"voices max rhodes (\d+) of (\d+)", log)
    if m:
        rows.append(row("CPU", "every voice taken", int(m.group(1)), int(m.group(2)), int(m.group(1)) >= int(m.group(2)) or ty.idx in (4, 5),
                        f"{m.group(1)} of {m.group(2)}"))
    return rows, dict(us=us)


# ---------------------------------------------------------------- the self-test: every detector fires on a defect
def selftest_rows(binary):
    """SLF: a clean note renders quiet on every detector; each injected defect makes its own fire"""
    ty = resolve_type(0)
    ev = bm.note(300, 60, 100, 5200)
    x, _ = render(binary, setup(ty, "raw") + ev, 8000)
    g = guards(ev)
    rows = []
    c0 = det_clicks(x, g)
    l0 = det_lpc(x, g)
    rows.append(row("SLF", "clean note CLK/GLT quiet", sum(c0) + len(l0), 0, sum(c0) == 0 and not l0, f"d2 {c0[0]}, hp {c0[1]}, lpc {len(l0)}"))
    rng = np.random.default_rng(7)
    pos = (np.array([0.9, 1.1, 1.3, 1.5, 1.7, 1.9, 2.1, 2.3, 2.5, 2.7, 2.9, 3.1]) * FS).astype(int)
    big, sub = x.copy(), x.copy()
    for j, p in enumerate(pos):
        kind = j % 4
        amp = np.std(sub[p - 500:p + 500, 0]) + 50
        if j % 2 == 0:
            big[p:p + 2, :] += 9000 * (1 if j % 4 == 0 else -1)         # a click: two samples
        if kind == 0:
            sub[p, :] += amp * 1.5                                      # one bad sample
        elif kind == 1:
            sub[p:p + 2, :] = sub[p - 2:p, :]                           # two repeated samples
        elif kind == 2:
            sub[p:, :] = np.concatenate([sub[p + 1:, :], sub[-1:, :]])  # one sample dropped
        else:
            sub[p, :] = 0                                               # a zeroed sample
    cb, ch = det_clicks(big, g)
    rows.append(row("SLF", "CLK d2 fires on 5 injected clicks", cb, 5, cb >= 5, f"{cb} hits"))
    rows.append(row("SLF", "CLK hp burst fires on injected clicks", ch, 1, ch >= 1, f"{ch} windows"))
    ev_l = det_lpc(sub, g)
    found = sum(1 for p in pos if any(abs(t - p / FS) < 0.004 for t in ev_l))
    per = [sum(1 for p in pos[k::4] if any(abs(t - p / FS) < 0.004 for t in ev_l)) for k in range(4)]
    rows.append(row("SLF", "GLT LPC finds injected sample defects", found, 9, found >= 9,
                    f"{found} of {len(pos)}: bad sample {per[0]}, repeated {per[1]}, dropped {per[2]}, zeroed {per[3]} of {len(pos[0::4])} each"))
    ev_b = det_lpc(big, g)
    fb = sum(1 for p in pos[::2][:5] if any(abs(t - p / FS) < 0.004 for t in ev_b))
    rows.append(row("SLF", "GLT LPC finds injected clicks", fb, 5, fb >= 5, f"{fb} of 5"))
    # clipping
    cl = x.copy()
    cl[int(2 * FS):int(2 * FS) + 5] = 32767
    rows.append(row("SLF", "CLP fires on rail samples", det_clip(cl, CLP_RAW), 1, det_clip(cl, CLP_RAW) >= 1) )
    rows.append(row("SLF", "CLP quiet on the clean note", det_clip(x, CLP_RAW), 0, det_clip(x, CLP_RAW) == 0))
    # runaway: the tail grows 9 dB a second after 1.5 s
    rn = x.copy()
    t = np.arange(len(rn)) / FS
    rn *= np.where(t > 1.5, 10 ** (9 * (t - 1.5) / 20), 1.0)[:, None]
    r0, r1 = det_rise(x, 300, 5500), det_rise(np.clip(rn, -32768, 32767), 300, 5500)
    rows.append(row("SLF", "RUN fires on a growing tail, quiet on the clean note", r1, RUN_RISE, r1 > RUN_RISE and r0 <= RUN_RISE, f"clean {r0:.2f} dB, grown {r1:.2f} dB"))
    # a swell that then falls is not a runaway (low bars of the bar TYPEs); a rise still going at the key-up is
    sw = x.copy()
    ramp = np.where(t < 1.1, 10 ** (-14 * (1.1 - t) / 0.8 / 20), 1.0)
    sw *= ramp[:, None]
    q0 = det_rise(sw, 300, 5500)
    rows.append(row("SLF", "RUN quiet on a swell that then decays", q0, RUN_RISE, q0 == 0.0 and det_swell(sw, 300, 5500)[0] > 3,
                    f"swell {det_swell(sw, 300, 5500)[0]:.1f} dB, runaway {q0:.1f}"))
    # level jump (zipper): a 3 dB step at 3 s
    jp = x.copy()
    jp[int(3 * FS):] *= 1.41
    j0, j1 = det_jump(x, 2000, 4000, x), det_jump(jp, 2000, 4000, x)
    rows.append(row("SLF", "ZIP level step fires on a 3 dB jump", j1, ZIP_STEP, j1 > ZIP_STEP and j0 <= ZIP_STEP, f"clean {j0:.2f}, jump {j1:.2f} dB"))
    # louder after key-up
    lo = x.copy()
    lo[int(5.53 * FS):int(5.83 * FS)] *= 60.0
    b, bp = det_release(np.clip(lo, -32768, 32767), 5500)
    rows.append(row("SLF", "REL fires on a note louder after the key-up", b, REL_RMS, b > REL_RMS and bp > REL_PEAK, f"{b:+.2f} dB rms, {bp:+.2f} peak"))
    # idle
    idl = x.copy()
    idl[-int(0.6 * FS):] += rng.integers(-3, 4, size=(int(0.6 * FS), 2))
    pk0, _ = det_idle(x)
    pk1, _ = det_idle(idl)
    rows.append(row("SLF", "IDL fires on a noisy idle, quiet on a clean one", pk1, 0, pk1 > 0 and pk0 == 0, f"clean {pk0:.0f}, noisy {pk1:.0f} LSB"))
    # aliasing and noise floor: harmonic bark passes, an off-harmonic tone fails, white noise fails the floor
    f0 = 261.6256
    tt = np.arange(int(0.5 * FS)) / FS
    amps = [1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1]
    harm = sum(a * np.sin(2 * np.pi * f0 * (k + 1) * tt + k) for k, a in enumerate(amps)) * 2000
    alias = harm + 2000 * 10 ** (-32 / 20) * np.sin(2 * np.pi * 7123.0 * tt)
    white = harm + rng.normal(0, 2000 * 10 ** (-45 / 20) / math.sqrt(2) * 30, len(tt))
    h = np.round(harm)
    ch = alias_cells(h, f0)
    ca = alias_cells(np.round(alias), f0)
    cw = alias_cells(np.round(white), f0)
    li, _ = ali_limits(ch[2])
    la, _ = ali_limits(ca[2])
    rows.append(row("SLF", "ALI quiet on pure harmonic growl, fires on an off-harmonic tone", ca[0], la, ca[0] > la and ch[0] <= li,
                    f"harmonic {ch[0]:.1f} (limit {li:.1f}), aliased {ca[0]:.1f} dB, bark {ch[4]:.1f} dB"))
    mod_t = harm + 2000 * 10 ** (-33 / 20) * np.sin(2 * np.pi * 6.5 * f0 * tt)
    mod_l = harm + 2000 * 10 ** (-20 / 20) * np.sin(2 * np.pi * 6.5 * f0 * tt)
    cm, cl = alias_cells(np.round(mod_t), f0, (), MODAL), alias_cells(np.round(mod_l), f0, (), MODAL)
    cn = alias_cells(np.round(mod_t), f0)
    rows.append(row("SLF", "ALI: a tine overtone at 6.5 f0 passes under the modal ceiling, fails over it", cl[6], MODAL_MAX,
                    cm[6] <= MODAL_MAX and cl[6] > MODAL_MAX and cm[0] <= li and cn[0] > li,
                    f"-33 dB: modal {cm[6]:.1f}, inh {cm[0]:.1f} (without the band {cn[0]:.1f}); -20 dB: modal {cl[6]:.1f}"))
    rows.append(row("SLF", "NSE fires on a white floor", cw[5], NSE_DB, cw[5] > NSE_DB and cw[5] + cw[3] > NSE_ABS and ch[5] <= NSE_DB,
                    f"clean {ch[5]:.1f}, noisy {cw[5]:.1f} dB re H1 ({cw[5] + cw[3]:.1f} dBFS)"))
    return rows


# ---------------------------------------------------------------- the driver
CASES = {"keys": case_keys, "typelevel": case_type_level, "knob": case_knob, "knobdef": case_knob_default,
         "sweep": case_sweep, "zip": case_zip, "pedal": case_pedal, "repeat": case_repeat, "chord": case_chord,
         "fx": case_fx, "cpu": case_cpu, "worst": case_worst}


def run_spec(spec):
    t = time.time()
    try:
        rows, data = CASES[spec["kind"]](spec)
    except Exception as e:                                 # a failed render is a failed case, not a crashed scan
        rows, data = [row("SCAN", spec["label"], None, None, False, f"{type(e).__name__}: {e}")], {}
    return spec["label"], spec["kind"], rows, data, time.time() - t


def plan(ty, prof, bins, checks):
    P = PROFILES[prof]
    d = ty.dump()
    S = []
    want = lambda c: not checks or c in checks                                       # noqa: E731
    keyset = want("CLK") or want("GLT") or want("CLP") or want("RUN") or want("ALI") or want("LEV") or want("CBL") \
        or want("VEL") or want("NSE") or want("IDL") or want("REL")
    if keyset:
        vels = sorted(set(P["vels"]) | {100})
        for v in vels:
            S.append(dict(kind="keys", label=f"keys v{v}", ty=d, bin=bins["one"], vel=v, keys=P["keys"], alikeys=P["alikeys"]))
    if want("TYP"):
        S.append(dict(kind="typelevel", label="typelevel", ty=d, bin=bins["one"]))
    if want("KNB") or any(want(c) for c in ("CLK", "GLT", "CLP", "RUN", "ALI", "REL", "IDL")):
        S.append(dict(kind="knobdef", label="knob default", ty=d, bin=bins["one"], keys=P["knob_keys"], vels=P["knob_vels"]))
        for k in P["knobs"]:
            for val in P["knob_vals"]:
                S.append(dict(kind="knob", label=f"knob {k}={val}", ty=d, bin=bins["one"], knob=k, val=val,
                              keys=P["knob_keys"], vels=P["knob_vels"]))
            if P["sweep"]:
                S.append(dict(kind="sweep", label=f"sweep {k}", ty=d, bin=bins["one"], knob=k))
    if want("ZIP"):
        for k in P["zip_knobs"]:
            S.append(dict(kind="zip", label=f"zip {k}", ty=d, bin=bins["one"], knob=k))
    if want("PED") and P["ped"]:
        S.append(dict(kind="pedal", label="pedal", ty=d, bin=bins["one"]))
    if want("REP"):
        for r in P["rep"]:
            S.append(dict(kind="repeat", label=f"repeat {r}", ty=d, bin=bins["one"], reg=r))
    if want("CHD"):
        for r in P["chords"]:
            S.append(dict(kind="chord", label=f"chord {r}", ty=d, bin=bins["one"], reg=r))
    if want("FXS"):
        for name in P["fx"]:
            for dep in P["depths"]:
                S.append(dict(kind="fx", label=f"fx {name} {dep}", ty=d, bin=bins["one"], fx=name, depth=dep))
    if want("CPU") and P["c1"] and "c1" in bins:
        for n in P["nvoices"]:
            S.append(dict(kind="cpu", label=f"cpu n={n}", ty=d, bin=bins["c1"], n=n))
        S.append(dict(kind="worst", label="worst case", ty=d, bin=bins["c1"], voices=14))
    return S


def per_voice(pts):
    """us per voice: the slope of the proxy over the voice count (a least-squares line)"""
    p = sorted((n, u) for n, u in pts if u is not None)
    if len(p) < 2:
        return None, None
    a = np.polyfit([n for n, _ in p], [u for _, u in p], 1)
    return float(a[0]), float(a[1])


def scan_type(ty, prof, jobs, checks, bins, ref=None, quiet=False):
    """-> rows. ref: STAGE 77's {typelevel, cpu} for TYP and CPU's relative checks (None: this type is STAGE 77, or the
    reference is measured from this run)"""
    specs = plan(ty, prof, bins, checks)
    t0 = time.time()
    res = []
    with Pool(jobs) as pool:
        for i, r in enumerate(pool.imap_unordered(run_spec, specs)):
            res.append(r)
            if not quiet:
                print(f"  [{i + 1}/{len(specs)}] {r[0]} {r[4]:.0f}s", flush=True)
    rows, bykind = [], {}
    for label, kind, rr, data, _ in sorted(res, key=lambda r: r[0]):
        for r in rr:
            r["group"] = kind
        rows += rr
        bykind.setdefault(kind, []).append((label, data))
    kv = {d["vel"]: d for _, d in bykind.get("keys", [])}
    if kv:
        rows += spread_rows(kv, scoped=judges_alias(ty))
    # KNB: the level against the default setting (a sanity bound)
    kd = dict(bykind.get("knobdef", [("", {})])[0][1]).get("lv") if bykind.get("knobdef") else None
    if kd:
        for _, d in bykind.get("knob", []):
            worst = max(abs(d["lv"][k] - kd[k]) for k in kd if k in d["lv"] and kd[k] > -60)
            rows.append(row("KNB", f"{d['knob']}={d['val']} level vs default", worst, KNB_LEVEL, worst <= KNB_LEVEL,
                            "worst over the keys and velocities, LUFS 0..500 ms"))
    me = dict(typelevel=bykind["typelevel"][0][1] if bykind.get("typelevel") else None)
    cpu = [(d["n"], d["us"]) for _, d in bykind.get("cpu", [])]
    slope, fixed = per_voice(cpu)
    me["cpu_slope"], me["cpu_fixed"] = slope, fixed
    if slope is not None:
        worst_us = max(u for _, u in cpu if u)
        rows.append(row("CPU", "proxy at the most voices", worst_us, cpu_limit(ty), worst_us <= cpu_limit(ty),
                        f"{worst_us} us per block at {max(n for n, _ in cpu)} voices, {slope:.1f} us per voice"))
    if ref is None and ty.idx == 0:
        ref = me
    if ref:
        if me["typelevel"] and ref.get("typelevel"):
            dl = me["typelevel"]["lufs"] - ref["typelevel"]["lufs"]
            dp = me["typelevel"]["phon"] - ref["typelevel"]["phon"]
            d1 = me["typelevel"]["lufs1"] - ref["typelevel"]["lufs1"]
            lim_p = TYP_LU if judges_alias(ty) else TYP_PHON_BAR
            rows.append(row("TYP", "A3-E4-A4 v100 LUFS vs STAGE 77", abs(dl), TYP_LU, abs(dl) <= TYP_LU,
                            f"{dl:+.2f} LU over the strike (0..300 ms), {d1:+.2f} over 1 s"))
            rows.append(row("TYP", "A3-E4-A4 v100 bands vs STAGE 77", abs(dp), lim_p, abs(dp) <= lim_p,
                            f"{dp:+.2f} phon over the strike, {me['typelevel']['phon1'] - ref['typelevel']['phon1']:+.2f} over 1 s"))
        if slope is not None and ref.get("cpu_slope") is not None:
            lim = ref["cpu_slope"] * CPU_VOICE_X + CPU_VOICE_ADD
            rows.append(row("CPU", "cost per voice vs STAGE 77", slope, lim, slope <= lim, f"{slope:.1f} us (STAGE 77 {ref['cpu_slope']:.1f})"))
    rows.append(dict(check="TIME", case="scan", value=round(time.time() - t0, 1), limit=None, ok=True, info="seconds", judged=False))
    return rows, me


def load_known():
    try:
        return json.load(open(KNOWN_FILE))
    except OSError:
        return {}


def judge(rows, ty):
    """-> (code -> (status, n, nfail, worst row)): PASS, FAIL, KNOWN (every failure listed in the known file) or INFO"""
    known = load_known().get(ty.tag, {})
    summary = {}
    for r in rows:
        if r["check"] in ("TIME",):
            continue
        s = summary.setdefault(r["check"], dict(n=0, fail=0, rows=[], known=0))
        s["n"] += 1
        if r["judged"] and not r["ok"]:
            s["fail"] += 1
            s["rows"].append(r)
            if any(re.fullmatch(pat, r["case"]) for pat in known.get(r["check"], {}).get("cases", [])) or \
                    ("*" in known.get(r["check"], {}).get("cases", [])):
                s["known"] += 1
    return summary


def update_known(ty, rows):
    """write this run's failures of ty into the known file (exact case names; edit the patterns and the reasons by hand)"""
    allk = load_known()
    mine = {}
    for r in rows:
        if r["judged"] and not r["ok"] and r["check"] != "SCAN":
            mine.setdefault(r["check"], []).append(re.escape(r["case"]))
    allk[ty.tag] = {c: {"cases": v, "why": allk.get(ty.tag, {}).get(c, {}).get("why", "baseline, to be explained")}
                    for c, v in sorted(mine.items())}
    os.makedirs(os.path.dirname(KNOWN_FILE), exist_ok=True)
    json.dump(allk, open(KNOWN_FILE, "w"), indent=1, sort_keys=True)
    open(KNOWN_FILE, "a").write("\n")


def table(ty, rows):
    s = judge(rows, ty)
    out = [f"TYPE {ty.name}  (idx {ty.idx})", f"  {'check':5} {'status':6} {'cases':>6} {'fail':>5}  worst"]
    bad = 0
    for code in sorted(s, key=lambda c: ["CLK", "GLT", "CLP", "RUN", "ALI", "LEV", "CBL", "VEL", "TYP", "NSE", "IDL", "REL",
                                         "KNB", "ZIP", "PED", "REP", "CHD", "FXS", "CPU", "SLF", "SCAN"].index(c) if c in
                      ["CLK", "GLT", "CLP", "RUN", "ALI", "LEV", "CBL", "VEL", "TYP", "NSE", "IDL", "REL", "KNB", "ZIP", "PED",
                       "REP", "CHD", "FXS", "CPU", "SLF", "SCAN"] else 99):
        v = s[code]
        st = "PASS" if not v["fail"] else ("KNOWN" if v["known"] == v["fail"] else "FAIL")
        if st == "FAIL":
            bad += 1
        w = ""
        if v["rows"]:
            r = max(v["rows"], key=lambda r: (r["value"] or 0) - (r["limit"] or 0))
            w = f"{r['case']}: {r['info'] or r['value']}"
        out.append(f"  {code:5} {st:6} {v['n']:>6} {v['fail']:>5}  {w[:110]}")
    g = {}
    for r in rows:
        if r.get("group") and r["judged"]:
            v = g.setdefault(r["group"], [0, 0])
            v[0] += 1
            v[1] += not r["ok"]
    out.append("  groups (rows / failed): " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(g.items())))
    t = [r for r in rows if r["check"] == "TIME"]
    if t:
        out.append(f"  scan time {t[0]['value']:.0f} s")
    return "\n".join(out), bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--type", default="0", help="a TYPE index or name, or 'all' (the existing TYPEs)")
    ap.add_argument("--type-cc", help="CC:VALUE selecting a TYPE the tool does not know")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true")
    g.add_argument("--smoke", action="store_true")
    ap.add_argument("--checks", default="")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--no-c1", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--partials", default="", help="the TYPE's own non-harmonic modes as ratios of f0 (e.g. 6.27,17.5): "
                                                   "their bands count as the note's own in ALI")
    ap.add_argument("--update-known", action="store_true", help="write this run's failures into the known file")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    prof = "full" if a.full else "smoke" if a.smoke else "quick"
    checks = set(c for c in a.checks.split(",") if c)
    os.makedirs(OUT, exist_ok=True)
    bins = {"one": build_bin("one", BUILDS["one"])}
    if not a.no_c1 and PROFILES[prof]["c1"] and (not checks or "CPU" in checks):
        bins["c1"] = build_bin("c1", BUILDS["c1"])
    if a.selftest:
        rows = selftest_rows(bins["one"])
        ty = resolve_type(0)
        print(table(ty, rows)[0])
        for r in rows:
            print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['case']}: {r['info']}")
        return 0 if all(r["ok"] for r in rows) else 1
    pr = tuple(float(v) for v in a.partials.split(",") if v)
    types = [resolve_type(i) for i in range(bm.n_types())] if a.type == "all" else [resolve_type(a.type, a.type_cc, pr)]
    types.sort(key=lambda t: t.idx != 0)                      # STAGE 77 first: the reference of TYP and CPU
    ref, code = None, 0
    for ty in types:
        print(f"== {ty.name} ({prof})", flush=True)
        rows, me = scan_type(ty, prof, a.jobs, checks, bins, ref)
        if ty.idx == 0:
            ref = me
        txt, bad = table(ty, rows)
        d = a.out or os.path.join(OUT, ty.tag)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "report.txt"), "w").write(txt + "\n")
        json.dump(dict(type=ty.dump(), profile=prof, rows=rows, summary={k: dict(n=v["n"], fail=v["fail"], known=v["known"])
                                                                         for k, v in judge(rows, ty).items()}),
                  open(os.path.join(d, "report.json"), "w"), indent=1)
        print(txt, flush=True)
        if a.update_known:
            update_known(ty, rows)
            txt, bad = table(ty, rows)
            print("  (known file updated)\n" + txt, flush=True)
        code |= bool(bad)
    return code


if __name__ == "__main__":
    sys.exit(main())
