#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's event timing (plugin/src/bridge.c: exact time), through plugin/tests/clap_render.c:

  relative  one note, played again N host frames later (N across a 256-frame app block: 0 .. 301), at every rate:
            the sound comes exactly N frames later, within 1 output frame (the cross-correlation's peak, interpolated)
  absolute  at each rate the note sounds where it does at 44.1 kHz (the render resampled to 44.1 kHz by FFT,
            cross-correlated): the latency the plugin reports is the true one, within 1 output frame
  null      the same render at each rate against the 44.1 kHz one, both band-limited to 20 kHz: the error's level
  blocks    random host block sizes (0 .. 2048 frames a call) render the same bytes as 512-frame blocks

  Each for a Rhodes (TYPE 0), VIBES (4) and MARIMBA (5) note. tools/plugin_timing.py [--quick]   (numpy)
"""
import argparse
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "plugin_timing"
PLUG_BUILD = Path(os.environ.get("FRP_BUILD", ROOT / "build" / "plugin"))
PLUGIN = PLUG_BUILD / "bundles" / "Frodhes.clap"
RENDER = PLUG_BUILD / "frp_clap_render"
RATES = [44100, 48000, 88200, 96000, 176400, 192000]
OFFSETS = [0, 1, 2, 3, 7, 50, 128, 200, 255, 256, 257, 301]
TYPES = {"rhodes": 0, "vibes": 4, "marimba": 5}


def script(ty, note=60, vel=100):
    p = OUT / f"note-{ty}.txt"
    p.write_text(f"0 param 0 {TYPES[ty]}\n100 midi 0x90 {note} {vel}\n300 midi 0x80 {note} 0\n600 end\n")
    return p


def render(scr, rate, offset=0, block="512"):
    out = OUT / f"{scr.stem}-{rate}-{offset}-{block.replace(':', '')}.wav"
    env = {k: v for k, v in os.environ.items() if k != "FRODHES_PLUGIN_TEST"}
    r = subprocess.run(["nice", "-n", "15", str(RENDER), str(PLUGIN), str(scr), str(out), "--rate", str(rate),
                        "--offset", str(offset), "--block", block], capture_output=True, text=True, env=env)
    if r.returncode:
        sys.exit(f"plugin_timing: render failed ({scr.name} {rate} {offset}): {(r.stdout + r.stderr).strip()[-300:]}")
    with wave.open(str(out)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float64)
    raw = out.read_bytes()
    out.unlink()
    return a[:, 0] + a[:, 1], raw


def lag(a, b, lo, hi):
    """the lag of b against a (b[n] ~ a[n - lag]), searched in lo .. hi, the peak interpolated"""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    ks = np.arange(lo, hi + 1)
    v = c[ks % n]
    i = int(np.argmax(v))
    if 0 < i < len(v) - 1:
        y0, y1, y2 = v[i - 1], v[i], v[i + 1]
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
    else:
        d = 0.0
    return ks[i] + d


def to441(x, rate):
    """x (rate) resampled to 44.1 kHz by FFT, band-limited to 20 kHz"""
    n = len(x) // (rate // 100 * 1) * (rate // 100)     # whole 10 ms
    m = n * 44100 // rate
    f = np.fft.rfft(x[:n])
    f[np.fft.rfftfreq(n, 1 / rate) > 20000.0] = 0
    g = np.zeros(m // 2 + 1, complex)
    k = min(len(g), len(f))
    g[:k] = f[:k]
    return np.fft.irfft(g, m) * (m / n)


def band(x, rate):
    f = np.fft.rfft(x)
    f[np.fft.rfftfreq(len(x), 1 / rate) > 20000.0] = 0
    return np.fft.irfft(f, len(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="44.1, 48 and 96 kHz, the Rhodes and VIBES, 6 offsets")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rates = [44100, 48000, 96000] if a.quick else RATES
    offsets = [0, 1, 7, 128, 255, 301] if a.quick else OFFSETS
    types = ["rhodes", "vibes"] if a.quick else list(TYPES)
    fails = []
    for ty in types:
        scr = script(ty)
        ref441 = None
        for rate in rates:
            base, raw512 = render(scr, rate)
            worst = 0.0
            for k in offsets[1:]:
                x, _ = render(scr, rate, k)
                d = lag(base, x, k - 64, k + 64) - k
                if abs(d) > abs(worst):
                    worst = d
            line = f"{ty:8s} {rate:6d} Hz: relative worst {worst:+.3f} frames over offsets {offsets[1:]}"
            ok = abs(worst) <= 1.0
            if rate == 44100:
                ref441 = base
            else:
                y = to441(base, rate)
                L = min(len(y), len(ref441))
                dl = lag(ref441[:L], y[:L], -64, 64) * rate / 44100.0
                rb = band(ref441[:L], 44100)
                e = y[:L] - rb
                s = slice(int(0.05 * L), int(0.95 * L))
                snr = 10 * np.log10(np.sum(rb[s] ** 2) / max(np.sum(e[s] ** 2), 1e-30))
                line += f"; absolute {dl:+.3f} frames; null vs 44.1 kHz {snr:.1f} dB (error RMS {np.sqrt(np.mean(e[s] ** 2)) / 2:.2f} LSB)"
                ok = ok and abs(dl) <= 1.0
            if rate in (44100, 96000):
                _, rawr = render(scr, rate, 0, "random:7")
                same = rawr == raw512
                line += f"; random blocks {'same bytes' if same else 'DIFFER'}"
                ok = ok and same
            print(f"{'ok  ' if ok else 'FAIL'}  {line}", flush=True)
            if not ok:
                fails.append(f"{ty} {rate}")
    if fails:
        sys.exit(f"plugin_timing: FAIL {fails}")
    print("plugin_timing: every note within 1 frame of its time at every rate")


if __name__ == "__main__":
    main()
