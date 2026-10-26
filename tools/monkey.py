#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Monkey test: a seeded random stream of everything a player or a DAW can send to Frodhes, run on the host build
(tests/host.c renders the firmware's own code), with invariants checked on every run, a differential click check
on a few of its events, and an automatic shrink of any failure to a minimal script.

    python3 tools/monkey.py [--seeds A-B | --minutes N] [-j 2] [--san] [--clicks K] [--no-shrink] [--keep]

The stream (gen): MIDI notes on any channel (chords, fast repeats, every key, velocity 1..127, lost note-offs,
release velocities), the pedal (half too), pitch bend and the mod wheel, every CC (mostly the ones the firmware
knows) and Program Change, MIDI clock at a random tempo with Start / Stop / Continue / Song Position, and the panel:
the FM-1's keys (with their velocity readings), every encoder (SELECT, ALGORITHM, K1-K4, PRESETS), every button
(taps and holds: SAVE's undo, SEQ's clear, REC's sustain), page jumps, screen shots, main-loop stalls. It leans
toward fast changes while notes ring ("ring" bursts: a pedalled chord and a run of control changes) and has
"mash" bursts (buttons and knobs every few ms).

The invariants (check), on every run:
  crash      the host exits 0 within its time limit; no sanitizer report (--san: ASan + UBSan build)
  race       the host's own voice checks (RACE STUCK / RACE LOST: a voice held that no note wants)
  peak       the output never passes the limiter's ceiling (24000)
  wrap       (--san / --san-every) no int16 narrowing in the audio path out of range: the sanitizer build has the
             (int16_t) casts of rhodes / bars / fx / the delay and reverbs taken out (a no-op in range) and checks the
             implicit ones; with signed overflow (UBSan) that is every int wrap. (A sample-step test cannot tell a
             wrap from loud bright audio under the 24000 limiter: steps up to 48000 are legal; 0.9.5's 5 "wraps" of
             40-43k were a +22 dB makeup into HP12 and a tape echo near the ceiling, bit-identical in this build)
  rail       no run of 32 equal samples above 20000 (an output stuck on a rail)
  stuck      no voice held, no note source or count left after the panic (the end: every note off on all 16
             channels, pedal up, arp, sequencer, chord mode, delay and reverb off)
  silence    exact silence (the gate's 0) from 10 s after the panic, so no stuck voice, tail or DC
  probe      a note over MIDI after the panic sounds (the device is alive; LEVEL set to 0 dB for it, MARIMBA at -12
             dB and HAMMER 0 is -43 dBFS), and with FX bypassed and VIBES' motor off
             (its pulse is in the engine, so FX keeps it) it is never louder after its key-up than before it (+1 dB)
  roundtrip  SAVE (into a user slot) then a power cycle (the flash kept: FRODHES_FLASH) gives the same parameters,
             preset, octave, chord and sequencer settings; the saved preset equals the panel (expect preset)
  screen     the screen renders (shots during the run) without a crash
The clicks (clicks): K events of the stream (a CC, a knob, a button, a Program Change, while the sound rings and
with no note starting near it) are rendered with and without that event (the stream ends at it); the largest second
difference from 6 ms before to 30 ms after it, against the larger of the run without it and the run with it once
settled (60-93 ms after), and the same on the difference of the two runs (the change alone: the reverb, the delay
and the other notes cancel), over 10x is a click (click_ratio). A click is named by what it changed (the parameters
that differ in the two runs' dumps); the ones private/BUGHUNT-095.md already lists are reported as known, the
others fail.

A failure is shrunk (delta debugging over the stream; the panic and the checks regenerated) to a minimal script in
tests/scripts/ format: build/monkey/fail-<seed>-<check>/repro.txt (and full.txt, info.json)."""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
SAN = os.path.join(ROOT, "build", "host", "frodhes-monkey-san")
OUTD = os.path.join(ROOT, "build", "monkey")
FS = 44100
SAN_FLAGS = ["-fsanitize=undefined,address,implicit-signed-integer-truncation", "-fno-sanitize=shift-base",
             "-fno-sanitize-recover=undefined", "-fsanitize-recover=implicit-signed-integer-truncation",
             "-fno-omit-frame-pointer"]
# the wrap check: the sanitizer build is compiled from a copy of the source with the audio path's (int16_t) casts
# taken out, so a narrowing that changes a sample (an int16 wrap) is a sanitizer report; in range they are no-ops
WRAP_SRC = ("rhodes.c", "bars.c", "fx.c", "dly_tape.c", "rev_plate.c", "rev_inf.c")
RAND_MS = 20000                    # the random part
LIM_T = 24000
DEV_CC = {119, 41}                 # not sent: RAW skips the limiter by design; key velocity's test modes

_src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
KNOWN_CC = sorted({int(c) for c in re.findall(r'\[P_\w+\] = \{"[^"]*", \d+, \d+, F_\w+, (\d+)', _src)}
                  | {64, 85, 86, 87, 111, 114, 120, 123, 43, 44, 45, 46, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62,
                     63, 79, 121, 1})
PNAME = re.search(r"enum \{ (P_TYPE.*?)P_COUNT \};", _src, re.S).group(1)
PNAME = [w.strip() for w in re.sub(r"/\*.*?\*/", "", PNAME, flags=re.S).replace("\n", " ").split(",") if w.strip()]

# what the clicks already found (private/BUGHUNT-095.md; inf-size / mix-fix): parameter -> finding
KNOWN_CLICK = {
    "P_CP_SW": "#2", "P_PH_SW": "#2", "P_DL_TIME": "#3", "P_DL_SYNC": "#3", "P_FL_TYPE": "#4", "P_DRIVE": "#5",
    "P_CMP_SW": "#6", "P_CMP_AMT": "#6", "P_CMP_GAIN": "#6", "P_TW_SW": "#7", "P_TW_MODE": "#7", "P_LEVEL": "#9",
    "P_RV_MOD": "#10", "P_TYPE": "#12", "P_FL_FREQ": "#13", "P_TW_RATE": "#14", "P_PH_FDBK": "#16",
    "P_DL_SW": "#17", "P_DL_TONE": "#23 (TAPE)", "P_RV_DEPTH": "#18", "P_RV_SIZE": "#19", "P_CP_DEPTH": "mix-fix",
    "P_DL_DEPTH": "mix-fix", "FX+SPREAD": "#25",
}


# ---- the stream
def gen(seed, dur=RAND_MS):
    """the random part: a list of (t ms, line), t in 50..dur"""
    rng = random.Random(seed)
    ev, held, pressed = [], [], set()
    t = 50
    keys = 0
    chan_main = 0 if rng.random() < 0.8 else rng.randrange(16)

    def ch():
        return chan_main if rng.random() < 0.85 else rng.randrange(16)

    def note_on(t, n, v, c=None):
        c = ch() if c is None else c
        ev.append((t, f"midi 0x{0x90 | c:02X} {n & 127} {v}"))
        held.append((n & 127, c))

    def note_off(t, n, c, rv=0):
        ev.append((t, f"midi 0x{0x80 | c:02X} {n} {rv}"))

    def a_cc(t, num=None, val=None, c=None):
        if num is None:
            num = rng.choice(KNOWN_CC) if rng.random() < 0.8 else rng.randrange(128)
        if num in DEV_CC:
            num = 118
        if val is None:
            val = rng.choice([0, 127]) if rng.random() < 0.25 else rng.randrange(128)
        ev.append((t, f"midi 0x{0xB0 | (ch() if c is None else c):02X} {num} {val}"))

    def a_turn(t):
        enc = rng.choices(range(7), weights=[3, 1, 3, 3, 3, 3, 1])[0]
        ev.append((t, f"turn {enc} {rng.choice([-5, -3, -2, -1, -1, 1, 1, 2, 3, 5])}"))

    def a_btn(t):
        b = rng.randrange(14)
        if rng.random() < 0.75:
            ev.append((t, f"btn {b}"))
        elif b not in pressed:
            pressed.add(b)
            ev.append((t, f"press {b}"))
            ev.append((t + rng.choice([rng.randint(10, 300), rng.randint(600, 1500)]), f"release {b}"))

    while t < dur:
        k = rng.random()
        if k < 0.16:                                       # notes: one, a chord, a fast repeat
            n = rng.randint(21, 108) if rng.random() < 0.92 else rng.randrange(128)
            v = rng.randint(1, 127)
            sub = rng.random()
            if sub < 0.5:
                note_on(t, n, v)
            elif sub < 0.8:
                c = ch()
                for d in rng.sample([0, 3, 4, 7, 10, 12, 14, 16, 19, 24], rng.randint(3, 6)):
                    note_on(t, n + d - 12, max(1, v - rng.randrange(20)), c)
            else:
                c = ch()
                gap = rng.randint(15, 80)
                for i in range(rng.randint(3, 12)):
                    note_on(t + i * gap, n, rng.randint(1, 127), c)
                    note_off(t + i * gap + gap // 2, n, c)
        elif k < 0.27 and held:                            # note offs (a few are never sent)
            for _ in range(rng.randint(1, max(1, len(held)))):
                n, c = held.pop(rng.randrange(len(held)))
                if rng.random() < 0.93:
                    note_off(t, n, c, rng.randrange(128) if rng.random() < 0.3 else 0)
                if not held:
                    break
        elif k < 0.34:                                     # the FM-1's keys (their velocity readings first)
            for _ in range(rng.randint(1, 3)):
                kk = rng.randrange(27)
                if rng.random() < 0.6 and kk < 20:
                    ev.append((t, f"vkey {kk} {rng.randint(0, 1023)}"))
                keys ^= 1 << kk
            ev.append((t + 1, f"keys {keys}"))
        elif k < 0.38:                                     # the pedal (half too)
            a_cc(t, 64, rng.choice([0, 127, rng.randrange(128)]))
        elif k < 0.41:                                     # bend and mod
            if rng.random() < 0.7:
                v = rng.choice([0, 8192, 16383, rng.randrange(16384)])
                ev.append((t, f"midi 0x{0xE0 | ch():02X} {v & 127} {v >> 7}"))
            else:
                a_cc(t, 1)
        elif k < 0.58:                                     # a CC
            a_cc(t)
        elif k < 0.60:                                     # a program change
            p = rng.randrange(40) if rng.random() < 0.9 else rng.randrange(128)
            ev.append((t, f"midi 0x{0xC0 | ch():02X} {p} 0"))
        elif k < 0.625:                                    # MIDI clock for a while at a random tempo, transport
            bpm = rng.choice([40, 60, 90, 120, 160, 200, 240, rng.randint(30, 300)])
            gap = max(1, round(2500 / bpm))
            for i in range(rng.randint(10, 120)):
                ev.append((t + i * gap, "midi 0xF8 0 0"))
            r = rng.random()
            if r < 0.3:
                ev.append((t + rng.randint(0, 300), "midi 0xFA 0 0"))
            elif r < 0.5:
                ev.append((t + rng.randint(0, 300), "midi 0xFC 0 0"))
            elif r < 0.6:
                ev.append((t + rng.randint(0, 300), "midi 0xFB 0 0"))
            elif r < 0.7:
                s = rng.randrange(16384)
                ev.append((t + rng.randint(0, 300), f"midi 0xF2 {s & 127} {s >> 7}"))
        elif k < 0.76:                                     # the knobs
            a_turn(t)
        elif k < 0.86:                                     # the buttons
            a_btn(t)
        elif k < 0.90:                                     # a ring burst: a pedalled chord, then fast changes
            c = ch()
            a_cc(t, 64, 127, c)
            for d in (0, 4, 7, 12):
                note_on(t + 1, 48 + rng.randrange(12) + d, rng.randint(60, 127), c)
            tt = t + rng.randint(100, 400)
            for _ in range(rng.randint(3, 12)):
                r = rng.random()
                if r < 0.55:
                    a_cc(tt)
                elif r < 0.85:
                    a_turn(tt)
                else:
                    a_btn(tt)
                tt += rng.randint(5, 60)
        elif k < 0.92:                                     # mashing: buttons and knobs every few ms
            tt = t
            for _ in range(rng.randint(10, 40)):
                if rng.random() < 0.6:
                    ev.append((tt, f"btn {rng.randrange(14)}"))
                else:
                    a_turn(tt)
                tt += rng.randint(2, 25)
        elif k < 0.93:                                     # garbage statuses (SysEx bytes, running data)
            st = rng.randrange(0x80, 0x100)
            d1 = rng.randrange(256)
            if st >> 4 == 0xB and (d1 & 127) in DEV_CC:
                d1 = 118
            ev.append((t, f"midi 0x{st:02X} {d1} {rng.randrange(256)}"))
        elif k < 0.935:
            ev.append((t, f"stall {rng.randint(5, 120)}"))
        elif k < 0.94:
            ev.append((t, "shot"))
        t += rng.randint(1, 40)
    return [(min(tt, dur), line) for tt, line in ev]


def panic(t0):
    """the end: every source of sound let go (on all 16 channels), then the checks' probes. (lines, marks)"""
    L = [(t0 - 17, "dump"), (t0 - 7, "dump")]
    L += [(t0, "keys 0")] + [(t0, f"release {b}") for b in range(14)] + [(t0 + 2, "btn 8"), (t0 + 3, "midi 0xFC 0 0")]
    for t in (t0 + 5, t0 + 700):            # (again after a PRESETS browse's load: 400 ms after its last detent)
        for c in range(16):
            for num, v in ((64, 0), (60, 0), (63, 0), (45, 0), (52, 0), (56, 0), (111, 0), (123, 0), (121, 0),
                           (88, 0), (26, 0), (95, 0), (92, 0), (102, 64), (103, 64)):
                L.append((t, f"midi 0x{0xB0 | c:02X} {num} {v}"))
            t += 1
    for c in range(16):
        L.append((t0 + 1000 + c, f"midi 0x{0xB0 | c:02X} 123 0"))      # (a strum still starting notes)
    quiet = (t0 + 10000, t0 + 12000)
    gone = t0 + 12000
    L.append((gone, "dump"))
    p0 = gone + 300                                   # the probe: FX bypassed, VIBES' motor off, a MIDI note
    L += [(p0 + c, f"midi 0x{0xB0 | c:02X} 111 127") for c in range(16)]
    L += [(p0 + 20 + c, f"midi 0x{0xB0 | c:02X} 47 0") for c in range(16)]   # (the motor is in the engine)
    L += [(p0 + 40 + c, f"midi 0x{0xB0 | c:02X} 110 64") for c in range(16)]  # (LEVEL at 0 dB: -12 is legal)
    n0 = p0 + 100
    L += [(n0 + c, f"midi 0x{0x90 | c:02X} 60 100") for c in range(16)]
    rel = n0 + 600
    L += [(rel + c, f"midi 0x{0x80 | c:02X} 60 0") for c in range(16)]
    s0 = rel + 3000
    L += [(s0 + c, f"midi 0x{0xB0 | c:02X} 111 0") for c in range(16)]
    L += [(s0 + 400, "btn 8"), (s0 + 600, "btn 9"), (s0 + 900, "btn 9")]   # SAVE: open, then into the slot
    chk = s0 + 4000
    L += [(chk, "expect preset"), (chk, "dump")]
    end = chk + 4000                                                  # (the settings' auto save: 2 s, then sliced)
    L.append((end - 10, "dump"))
    return L, dict(before=(t0 - 17, t0 - 7), quiet=quiet, gone=gone, note=(n0, rel), saved=chk, end=end)


def script_text(events, extra=(), end=None):
    ls = [f"{t} {l}" for t, l in sorted(list(events) + list(extra), key=lambda e: e[0])]
    if end is not None:
        ls.append(f"{end} end")
    return "\n".join(ls) + "\n"


# ---- running
class Res:
    def __init__(self, code, out, wav, timeout=False):
        self.code, self.out, self.timeout = code, out, timeout
        self.x = wav
        self.dumps = {}
        for m in re.finditer(r"^@ (\d+) (\S+) ?(.*)$", out, re.M):
            self.dumps.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)


def build(san=False, inject=None, out=SAN):
    """the host binary (make's), or the sanitizer build (to out; inject: (file, old, new) edits to its source copy,
    the self-tests' faults)"""
    if not san:
        subprocess.run(["make", "-s", "-C", ROOT, "build/host/frodhes"], check=True)
        return BIN
    src = os.path.join(ROOT, "firmware", "src")
    newest = max(os.path.getmtime(os.path.join(d, f)) for d in (src, os.path.join(ROOT, "tests"))
                 for f in os.listdir(d) if f.endswith((".c", ".h")))
    newest = max(newest, os.path.getmtime(__file__))
    if inject or not os.path.exists(out) or os.path.getmtime(out) < newest:
        tmp = f"{out}.tmp{os.getpid()}"
        mirror = tempfile.mkdtemp(dir=os.path.dirname(out), prefix="san-src-")
        msrc = os.path.join(mirror, "firmware", "src")
        try:                                    # (the tree's layout kept: platform.c includes ../../tests/host.c)
            shutil.copytree(src, msrc)
            shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(mirror, "tests"), ignore=shutil.ignore_patterns(
                "*.wav", "*.png", "scripts*", "py", "baselines", "smk", "golden*"))
            edits = list(inject or ()) + [(f, "(int16_t)", "") for f in WRAP_SRC]
            for f, old, new in edits:
                fp = os.path.join(msrc, f)
                with open(fp) as fh:
                    txt = fh.read()
                assert old in txt, (f, old)
                with open(fp, "w") as fh:
                    fh.write(txt.replace(old, new))
            subprocess.run(["cc", "-O1", "-g", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', *SAN_FLAGS, "-I" + msrc,
                            "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", tmp, os.path.join(msrc, "frodhes.c"),
                            "-lm"], check=True)
        finally:
            shutil.rmtree(mirror, ignore_errors=True)
        os.replace(tmp, out)
        shutil.rmtree(tmp + ".dSYM", ignore_errors=True)
    return out


def run(text, binary=BIN, flash=None, timeout=120):
    """one host run of a script: Res (the output text, the dumps, the stereo int16 render)"""
    os.makedirs(OUTD, exist_ok=True)
    d = tempfile.mkdtemp(dir=OUTD, prefix="run-")
    try:
        base = os.path.join(d, "r")
        with open(base + ".txt", "w") as f:
            f.write(text)
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="print_stacktrace=1")
        env.pop("FRODHES_FLASH", None)
        if flash:
            env["FRODHES_FLASH"] = flash
        try:
            p = subprocess.run([binary, base, base + ".txt"], capture_output=True, text=True, env=env,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            so = e.stdout or ""
            return Res(-999, so.decode(errors="replace") if isinstance(so, bytes) else so,
                       np.zeros((0, 2)), timeout=True)
        x = np.zeros((0, 2))
        if os.path.exists(base + ".wav"):
            with wave.open(base + ".wav") as w:
                x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.int32)
        return Res(p.returncode, p.stdout + p.stderr, x)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def ms(t):
    return int(t * FS / 1000)


def rms_db(x):
    return float(10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-9) - 20 * np.log10(32768))


# ---- the invariants
def analyse(r, mk):
    """the invariants on one run's output (Res) without the power cycle: a list of (check, message)"""
    if r.timeout:
        return [("crash", "the host hung (timeout)")]
    errs = re.findall(r"(\S+:\d+):\d+: runtime error: ([^\n]*)", r.out)
    wraps = sorted(set(f"{os.path.basename(f)}: {m}" for f, m in errs if "implicit conversion" in m
                       and "'int16_t'" in m))
    hard = sorted(set(f"{os.path.basename(f)}: {m}" for f, m in errs if "implicit conversion" not in m))
    if hard or "AddressSanitizer" in r.out:
        return [("crash", "sanitizer: " + ("; ".join(hard) or "AddressSanitizer")[:500])]
    fails = re.findall(r"^\s*\d+ ms  FAIL (.*)$", r.out, re.M)
    if r.code != 0 and not fails:
        return [("crash", f"exit {r.code}: {r.out[-400:]}")]
    if not len(r.x):
        return [("crash", "no sound file")]
    bad = []
    race = sorted(set(re.findall(r"RACE (?:STUCK|LOST)[^\n]*", r.out)))
    if race:
        bad.append(("race", "; ".join(race)[:300]))
    a = np.abs(r.x).max(1)
    q0 = ms(mk["quiet"][0])
    if a[:q0].max() > LIM_T:
        bad.append(("peak", f"{a[:q0].max()} at {np.argmax(a[:q0]) / FS:.3f} s"))
    if wraps:                                   # (the sanitizer build: an int16 narrowing out of range)
        bad.append(("wrap", "; ".join(wraps)[:400]))
    for c in (0, 1):
        y = r.x[:, c]
        same = np.concatenate([[False], np.diff(y) == 0]) & (np.abs(y) > 20000)
        if same.any():
            runs = np.diff(np.flatnonzero(np.diff(np.concatenate([[0], same.astype(int), [0]]))))[::2]
            if runs.max() >= 32:
                bad.append(("rail", f"{runs.max()} equal samples over 20000 (channel {c})"))
                break
    b0, b1 = (r.dumps.get(t, {}) for t in mk["before"])
    if b0 and b1 and all(int(b.get("held", 0)) > int(b.get("pitches", 0)) for b in (b0, b1)):
        bad.append(("stuck", f"{b0['held']} voices held for {b0['pitches']} notes before the panic"))
    st = r.dumps.get(mk["gone"], {})
    if st.get("held") != "0" or st.get("srcs") != "0" or st.get("refs") != "0":
        bad.append(("stuck", f"after the panic: held {st.get('held')} srcs {st.get('srcs')} refs {st.get('refs')} "
                             f"voices {st.get('voices')}"))
    qs = slice(ms(mk["quiet"][0]), ms(mk["quiet"][1]))
    if np.abs(r.x[qs]).max() > 0:
        bad.append(("silence", f"not silent 10-12 s after the panic: peak {np.abs(r.x[qs]).max()}, mean "
                               f"{r.x[qs].mean():.1f}"))
    n0, rel = mk["note"]
    held_db = rms_db(r.x[ms(rel - 60):ms(rel - 10)])
    onset_db = max(rms_db(r.x[ms(n0 + k):ms(n0 + k + 20)]) for k in range(0, 200, 10))
    if onset_db < -40:                          # (MARIMBA has died away by the key-up: its onset counts)
        bad.append(("probe", f"the probe note after the panic: {onset_db:.1f} dBFS at its onset (dead?)"))
    elif held_db > -70:
        after = max(rms_db(r.x[ms(rel + k):ms(rel + k + 10)]) for k in range(0, 300, 5))
        if after > held_db + 1.0:
            bad.append(("probe", f"louder after its key-up: {after:.1f} dBFS against {held_db:.1f} held"))
    for f in fails:
        bad.append(("roundtrip", f"the saved preset: {f}"[:300]))
    return bad


def check(events, binary=BIN, only=None):
    """every invariant on one stream, the power cycle too: a list of (check, message)"""
    pl, mk = panic(RAND_MS + 50)
    os.makedirs(OUTD, exist_ok=True)
    fd, flash = tempfile.mkstemp(dir=OUTD, prefix="flash-")
    os.close(fd)
    os.remove(flash)                            # (no file: a new FM-1; the run writes it at its end)
    try:
        r = run(script_text(events, pl, mk["end"]), binary, flash=flash)
        bad = analyse(r, mk)
        if not any(b[0] == "crash" for b in bad):
            saved = r.dumps.get(mk["end"] - 10, {})
            r2 = run(script_text([(1400, "dump")], (), 1500), binary, flash=flash)   # the power cycle: a boot
            boot = r2.dumps.get(1400, {})
            if r2.code != 0 or not boot:
                bad.append(("roundtrip", f"the boot after the run failed: exit {r2.code} {r2.out[-200:]}"))
            elif saved:
                keys = [k for k in saved if k.startswith("p.")] + ["preset", "oct", "chord", "bars"]
                diff = [f"{k} {saved.get(k)} -> {boot.get(k)}" for k in keys if saved.get(k) != boot.get(k)]
                if diff:
                    bad.append(("roundtrip", "after a power cycle: " + ", ".join(diff[:8])))
    finally:
        try:
            os.remove(flash)
        except OSError:
            pass
    return [b for b in bad if only is None or b[0] == only]


# ---- the differential clicks
def pname(i):
    return PNAME[i] if i < len(PNAME) else f"p.{i}"


NO_CLICK_CC = {64, 120, 123, 121, 10, 1, 2, 3, 11, 4, 8, 116, 9, 42}   # sound on purpose, or developer CCs


def starts_sound(line):
    """an event that may start (or end) a note: its onset would read as a click"""
    w = line.split()
    if w[0] in ("keys", "press", "release", "vkey"):
        return True
    if w[0] == "midi":
        hi = int(w[1], 16) >> 4
        return hi in (0x8, 0x9, 0xE) or (hi == 0xB and int(w[2]) in NO_CLICK_CC)
    return False


NOTE_BTN = {10, 11, 12, 13}                 # ARP, SEQ, PLAY, REC: they start or stop notes
NOTE_CC = set(range(43, 64)) | {79, 80}      # the arpeggiator, chord mode, the sequencer; (TYPE: a new engine)


def eligible(events, i):
    """event i can be click-checked: a CC on channel 1 (not the pedal / all-off / developer / note-making ones), a
    Program Change, a knob or a button (not ARP / SEQ / PLAY / REC)"""
    t, line = events[i]
    w = line.split()
    if w[0] == "midi":
        st = int(w[1], 16)
        if st >> 4 == 0xB:
            return not st & 15 and int(w[2]) not in NO_CLICK_CC | NOTE_CC
        return st >> 4 == 0xC
    if w[0] == "btn":
        return int(w[1]) not in NOTE_BTN
    return w[0] == "turn"


def click_stream(events, i):
    """the stream for event i's click check: the stream up to it, without the events that start a note from 60 ms
    before it (their onsets would hide or fake a click; the notes already ringing stay); nothing after it (a change
    in its window would be read as its click: a TYPE turn 12 ms after FX gave FX 16x, #12's)"""
    t = events[i][0]
    return [e for j, e in enumerate(events) if j == i or (e[0] <= t and not (t - 60 <= e[0] and starts_sound(e[1])))]


def _d2(x):
    return np.abs(np.diff(np.asarray(x, float), 2, axis=0)).max(1)


def click_ratio(a, b, t):
    """the click ratio of one event at t ms, from the renders with it (a) and without it (b), stereo int arrays:
    (ratio, plain, diff). plain: the largest second difference of a from 6 ms before (the event lands at the start of
    its 5.8 ms block) to 30 ms after it, against the larger of b there and both once settled (60-93 ms after). diff:
    the same on a - b, the change itself, against its settled self (and 5 % of plain's reference: a click must be
    heard over the sound). Everything the event does not touch (the reverb and delay tails, the other notes, the
    modulations) is equal in a and b and cancels in a - b, so a busy patch no longer masks a click (0.9.5's FILTER
    TYPE, BUGHUNT #4: 7x plain, 15x diff in seed 14's stream at 12227 ms). ratio: the larger"""
    i0, i1 = ms(t - 6), ms(t + 30)
    s0, s1 = ms(t + 60), ms(t + 93)
    ref = max(_d2(b[i0:i1]).max(), _d2(a[s0:s1]).max(), _d2(b[s0:s1]).max(), 1.0)
    plain = float(_d2(a[i0:i1]).max() / ref)
    d = np.asarray(a, float) - b
    diff = float(_d2(d[i0:i1]).max() / max(_d2(d[s0:s1]).max(), 0.05 * ref, 1.0))
    return max(plain, diff), plain, diff


def click_one(events, i, binary=BIN, detail=False):
    """(ratio, changed parameters, ringing dB) for event i: the stream up to it + 150 ms with and without it (detail:
    the plain and diff ratios too). The changed parameters: at 8 and 31 ms (a preset load still pending from a
    browse can land in between and undo the event's change)"""
    t, line = events[i]
    pre = click_stream(events, i)
    without = [e for e in pre if e is not events[i]]
    extra = [(t + 8, "dump"), (t + 31, "dump")]
    a = run(script_text(pre, extra, t + 150), binary)
    b = run(script_text(without, extra, t + 150), binary)
    if a.code or b.code or not len(a.x) or not len(b.x):
        return None
    ring = rms_db(b.x[ms(t - 50):ms(t)])
    ratio, plain, diff = click_ratio(a.x, b.x, t)
    changed = []
    for at in (t + 8, t + 31):
        da, db = a.dumps.get(at, {}), b.dumps.get(at, {})
        changed += [pname(int(k[2:])) for k in da if k.startswith("p.") and da.get(k) != db.get(k)
                    and pname(int(k[2:])) not in changed]
    w = line.split()
    fx = w[:2] == ["btn", "2"] or (w[0] == "midi" and int(w[1], 16) >> 4 == 0xB and int(w[2]) == 111)
    if fx and a.dumps.get(t + 8, {}).get(f"p.{PNAME.index('P_SPREAD')}", "0") != "0":
        changed = ["FX+SPREAD"] + changed               # (the bypass's crossfade misses SPREAD's side: #25)
    if not changed:
        changed = [f"({w[0]} {' '.join(w[1:3])}: no parameter)"]
    return (ratio, changed, ring, plain, diff) if detail else (ratio, changed, ring)


def known_click(changed):
    if len(changed) > 3:
        return "#1 (a preset load / undo: many parameters at once)"
    sw = {c[:-3] for c in changed if c.endswith("_SW") and c in KNOWN_CLICK}
    changed = [c for c in changed if c.endswith("_SW") or c[:c.rfind("_")] not in sw]   # (an effect switched on
    ks = [KNOWN_CLICK.get(c) for c in changed]                                         # at depth 0 gets a depth)
    return ", ".join(sorted(set(k for k in ks if k))) if all(ks) else None


def clicks(events, seed, k=3, binary=BIN):
    """k eligible events of the stream, at random: (event, ratio, changed, known)"""
    rng = random.Random(seed * 31 + 7)
    idx = [i for i in range(len(events)) if events[i][0] > 600 and eligible(events, i)]
    out = []
    for i in rng.sample(idx, min(k, len(idx))):
        r = click_one(events, i, binary)
        if r is None:
            continue
        ratio, changed, ring = r
        if ring < -55:                          # (nothing rings: no click to hear)
            continue
        if ratio > 10:
            out.append((i, events[i], round(ratio, 1), changed, known_click(changed)))
    return out


# ---- shrinking (delta debugging over the stream)
def ddmin(items, fails, budget=400):
    """the classic ddmin: a smaller list that still fails (fails(list) -> bool), within budget runs"""
    n, runs = 2, 0
    while len(items) >= 2 and runs < budget:
        chunk = max(1, len(items) // n)
        parts = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        for j in range(len(parts)):
            comp = [x for p in parts[:j] + parts[j + 1:] for x in p]
            runs += 1
            if comp and fails(comp):
                items, n, reduced = comp, max(n - 1, 2), True
                break
        if not reduced:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    return items


def cut_tail(events, fails):
    """the shortest prefix of the stream (by time) that still fails: a binary search over its events"""
    ev = sorted(events, key=lambda e: e[0])
    lo, hi = 1, len(ev)                         # ev[:hi] fails
    while lo < hi:
        mid = (lo + hi) // 2
        if fails(ev[:mid]):
            hi = mid
        else:
            lo = mid + 1
    return ev[:hi]


def shrink(events, fails, budget=1500):
    """the tail cut first (cheap: log2 n runs), then ddmin, then ddmin once more on what is left"""
    ev = cut_tail(events, fails)
    ev = ddmin(ev, fails, budget)
    return ddmin(ev, fails, budget // 3)


def shrink_invariant(events, kind, binary):
    return shrink(list(events), lambda ev: any(b[0] == kind for b in check(ev, binary, only=kind)))


def shrink_click(events, i, changed, binary):
    target = events[i]
    others = [e for j, e in enumerate(events) if j != i and e[0] <= target[0]]

    def fails(ev):
        allv = sorted(ev + [target], key=lambda e: e[0])
        r = click_one(allv, allv.index(target), binary)
        return r is not None and r[0] > 10 and r[1] == changed
    keep = ddmin(others, fails, 600)
    return sorted(keep + [target], key=lambda e: e[0])


def save_fail(seed, kind, events, minimal, info):
    d = os.path.join(OUTD, f"fail-{seed}-{kind}")
    os.makedirs(d, exist_ok=True)
    head = [f"# monkey seed {seed}: {kind} - {info.get('msg', '')}"[:300],
            "# shrunk by tools/monkey.py (ddmin); tests/scripts format; the panic below is the monkey's own"]
    end = None
    extra = ()
    if kind == "click":
        t = info["event"][0]
        extra = [(t + 31, "dump")]
        end = t + 150
    else:
        pl, mk = panic(RAND_MS + 50)
        extra, end = pl, mk["end"]
    with open(os.path.join(d, "repro.txt"), "w") as f:
        f.write("\n".join(head) + "\n" + script_text(minimal, extra, end))
    with open(os.path.join(d, "full.txt"), "w") as f:
        f.write(script_text(events, extra, end))
    json.dump(dict(info, seed=seed, kind=kind, events=len(events), minimal=len(minimal)),
              open(os.path.join(d, "info.json"), "w"), indent=1, default=str)
    return d


# ---- one seed
def one(seed, binary=BIN, k_clicks=3, do_shrink=True, strict=False):
    """every check on one seed: a dict (failures, known clicks, repro dirs)"""
    ev = gen(seed)
    res = {"seed": seed, "fail": [], "known": [], "repro": []}
    bad = check(ev, binary)
    seen = set()
    for kind, msg in bad:
        res["fail"].append((kind, msg))
        if do_shrink and kind not in seen and kind != "crash":
            seen.add(kind)
            m = shrink_invariant(ev, kind, binary if kind == "wrap" else BIN)   # (the plain build: ~5x faster)
            res["repro"].append(save_fail(seed, kind, ev, m, {"msg": msg}))
        elif do_shrink and kind == "crash" and kind not in seen:
            seen.add(kind)
            m = shrink(list(ev), lambda e: any(b[0] == "crash" for b in check(e, binary)))
            res["repro"].append(save_fail(seed, kind, ev, m, {"msg": msg}))
    for i, e, ratio, changed, known in clicks(ev, seed, k_clicks, binary):
        row = {"event": e, "ratio": ratio, "changed": changed, "known": known}
        if known and not strict:
            res["known"].append(row)
            continue
        res["fail"].append(("click", f"{e[1]} at {e[0]} ms: {ratio}x, changed {changed}"))
        if do_shrink:
            m = shrink_click(ev, i, changed, binary)
            res["repro"].append(save_fail(seed, "click", ev, m, dict(row, msg=f"{ratio}x {changed}")))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", default="0-15", help="A-B (inclusive), or a list A,B,C")
    ap.add_argument("--minutes", type=float, help="random seeds until this long")
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--san", action="store_true", help="the ASan + UBSan build (and the wrap check) for every seed")
    ap.add_argument("--san-every", type=int, default=0, metavar="N",
                    help="the sanitizer build for every N-th seed (the others: the plain build, about 3x faster)")
    ap.add_argument("--clicks", type=int, default=3)
    ap.add_argument("--no-shrink", action="store_true")
    ap.add_argument("--strict", action="store_true", help="known clicks fail too")
    ap.add_argument("--json")
    a = ap.parse_args()
    binary = build(a.san)
    sanb = build(True) if a.san_every else binary
    os.makedirs(OUTD, exist_ok=True)

    def job(s):
        b = sanb if a.san_every and s % a.san_every == 0 else binary
        return one(s, b, a.clicks, not a.no_shrink, a.strict)
    results = []
    if a.minutes:
        t_end = time.time() + 60 * a.minutes
        base = random.SystemRandom().randrange(1 << 30)
        nxt = [base]

        def seeds():
            while time.time() < t_end:
                nxt[0] += 1
                yield nxt[0]
        with ThreadPoolExecutor(a.j) as ex:
            pend = []
            it = seeds()
            for s in it:
                pend.append(ex.submit(job, s))
                if len(pend) >= 2 * a.j:
                    r = pend.pop(0).result()
                    results.append(r)
                    report(r)
            for p in pend:
                r = p.result()
                results.append(r)
                report(r)
    else:
        if "-" in a.seeds:
            lo, hi = map(int, a.seeds.split("-"))
            ss = list(range(lo, hi + 1))
        else:
            ss = [int(s) for s in a.seeds.split(",")]
        with ThreadPoolExecutor(a.j) as ex:
            for r in ex.map(job, ss):
                results.append(r)
                report(r)
    nf = sum(1 for r in results if r["fail"])
    print(f"{len(results)} seeds, {nf} failing; known clicks: {sum(len(r['known']) for r in results)}")
    if a.json:
        json.dump(results, open(a.json, "w"), indent=1, default=str)
    return 1 if nf else 0


def report(r):
    if r["fail"]:
        print(f"seed {r['seed']}: FAIL " + "; ".join(f"{k}: {m}" for k, m in r["fail"])[:900], flush=True)
        for d in r["repro"]:
            print(f"    repro: {d}/repro.txt", flush=True)
    else:
        print(f"seed {r['seed']}: ok" + (f" (known clicks: {', '.join(str(k['known']) for k in r['known'])})"
                                          if r["known"] else ""), flush=True)


if __name__ == "__main__":
    sys.exit(main())
