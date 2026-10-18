# SPDX-License-Identifier: GPL-3.0-only
"""Run the host build (tests/host.c) with a script and read back everything it says: the sound (L, R), the state
dumps ("T dump": every page's cards, the parameters, the LEDs ...), the expects, MIDI out, the flash writes. Shared
by the display-vs-sound, state, persistence and fuzz tests."""
import os
import re
import subprocess
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
OUT = os.path.join(ROOT, "build", "host", "py")
FS = 44100
_built = {}


def build(binary=BIN, extra=()):
    """the host build (make's rule), or a variant with extra compiler flags (sanitizers) at binary"""
    if binary in _built:
        return binary
    if not extra:
        subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    else:
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DAPP_VER=\"dev\"",
                        *extra, *os.environ.get("HOST_DEFS", "").split(),   # the Makefile's experiment flags too
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", binary, os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    _built[binary] = True
    return binary


class Run:
    def __init__(self, tag, out, code, wav):
        self.tag, self.out, self.code = tag, out, code
        self.L, self.R = wav
        self.dumps = {}                                  # ms -> {key: value}
        for m in re.finditer(r"^@ (\d+) (\S+) ?(.*)$", out, re.M):
            self.dumps.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
        self.fails = re.findall(r"^\s*\d+ ms  FAIL (.*)$", out, re.M)
        self.midi_out = [(int(t), int(s, 16), int(a), int(b))
                         for t, s, a, b in re.findall(r"^\s*(\d+) ms  MIDI out ([0-9A-F]+) (\d+) (\d+)$", out, re.M)]
        self.saves = [int(t) for t in re.findall(r"^\s*(\d+) ms  settings saved", out, re.M)]
        self.bank_saves = [int(t) for t in re.findall(r"^\s*(\d+) ms  preset bank . erased", out, re.M)]
        self.seq_saves = [int(t) for t in re.findall(r"^\s*(\d+) ms  pattern . erased", out, re.M)]
        m = re.search(r"cpu-proxy max (\d+) us", out)
        self.cpu_proxy = int(m.group(1)) if m else None

    def state(self, t):
        """the dump taken at t ms"""
        return self.dumps[t]

    def seg(self, t0, t1, ch=None):
        x = self.L if ch is None or ch == 0 else self.R
        return x[int(t0 * FS):int(t1 * FS)]


def script(lines):
    """lines: "T what ..." strings (or (t, text) pairs); sorted by time, keeping the given order at equal times"""
    ls = [l if isinstance(l, str) else f"{l[0]} {l[1]}" for l in lines]
    return sorted(ls, key=lambda l: int(l.split()[0]))


def run(tag, lines, end_ms=None, flash=None, env=None, binary=BIN, check=True):
    """run a script; flash: a file kept across runs (FRODHES_FLASH); returns a Run. check: a failed expect or a
    crash raises"""
    build(binary)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, tag)
    ls = script(lines)
    if end_ms is not None:
        ls.append(f"{end_ms} end")
    with open(out + ".txt", "w") as f:
        f.write("\n".join(ls) + "\n")
    e = dict(os.environ)
    e.pop("FRODHES_FLASH", None)
    if flash:
        e["FRODHES_FLASH"] = flash
    if env:
        e.update(env)
    p = subprocess.run([binary, out, out + ".txt"], capture_output=True, text=True, env=e)
    wav = (np.zeros(0), np.zeros(0))
    if os.path.exists(out + ".wav") and p.returncode >= 0:
        w = wave.open(out + ".wav")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
        wav = (x[:, 0], x[:, 1])
    r = Run(tag, p.stdout + p.stderr, p.returncode, wav)
    if check:
        assert p.returncode >= 0, f"{tag}: crashed (signal {-p.returncode})\n{p.stderr[-2000:]}"
        assert not r.fails and p.returncode == 0, f"{tag}: {r.fails or p.stderr[-2000:]}"
    return r


def cc(t, n, v, ch=0):
    return f"{t} midi 0x{0xB0 | ch:02X} {n} {v}"


def note(t, n, v, dur, ch=0):
    return [f"{t} midi 0x{0x90 | ch:02X} {n} {v}", f"{t + dur} midi 0x{0x80 | ch:02X} {n} 0"]


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x, float) ** 2)) / 32767 + 1e-12)


def env_rms(x, hop):
    """RMS in frames of hop samples"""
    n = len(x) // hop
    return np.sqrt(np.mean(np.asarray(x[:n * hop], float).reshape(n, hop) ** 2, axis=1) + 1e-9)


def rate_of(feature, fps, guess, lo=0.4, hi=2.6, keep=None):
    """the repetition rate of a feature sampled at fps (Hz), searched from lo to hi times the guess (so a card that
    shows half or double the rate is found out): a weighted harmonic sum (h = 1..3, weights 1, 1/2, 1/3) picks the
    fundamental, which must carry power of its own (a half rate only reaches its 2nd harmonic), a fine search on the
    fundamental refines it. keep: the frames to use (e.g. not the ones right after a strike)"""
    x = np.asarray(feature, float)
    t = np.arange(len(x)) / fps
    if keep is not None:
        x, t = x[keep], t[keep]
    x = x - np.polyval(np.polyfit(t, x, 2), t)                     # the notes' decay out

    def power(f, h):
        return abs(np.sum(x * np.exp(-2j * np.pi * h * f * t))) ** 2

    fs = guess * np.exp(np.linspace(np.log(lo), np.log(hi), 600))
    hs = [sum(power(f, h) / h for h in (1, 2, 3)) for f in fs]
    f0 = fs[int(np.argmax(hs))]
    # the fine search: a least-squares fit of a periodic wave (3 harmonics) on a quadratic trend; exact with few cycles
    tn = (t - t.mean()) / (t.max() - t.min() + 1e-9)
    trend = [np.ones_like(t), tn, tn * tn]

    def resid(f):
        a = np.column_stack(trend + [g(2 * np.pi * h * f * t) for h in (1, 2, 3) for g in (np.cos, np.sin)])
        return np.sum((x - a @ np.linalg.lstsq(a, x, rcond=None)[0]) ** 2)

    fine = f0 * np.linspace(0.94, 1.06, 241)
    return float(fine[int(np.argmin([resid(f) for f in fine]))])


def card_hz(text):
    """a rate card's text ("4.7", "12", "3.5HZ") in Hz"""
    return float(text.upper().replace("HZ", ""))
