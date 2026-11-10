#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's oversampler (plugin/src/frp_os.h) against its targets, through plugin/tests/os_test.c:

  ripple     a 44.1 kHz sine from 20 Hz to 20 kHz (64 tones, log-spaced, 20 kHz itself included) comes out at its own
             level within +-0.05 dB at 88.2, 176.4, 352.8, 48, 96, 192 and 384 kHz
  rejection  every other component of the output (the tone's images, aliases, the arithmetic's noise) at least 100 dB
             under the tone: a Kaiser window (beta 40, ~ -300 dB side lobes), the tone's main lobe and DC left out
  time       the tone's phase says where it is: output frame T is the app's frame (T - latency) x 44100 / rate, the
             latency the chain reports, within 0.01 output frame
  cpu        the chain's time a second of stereo output (this computer; informative)

  tools/plugin_os.py [--quick]          (builds build/rates/os_test; numpy)
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "plugin_os"
BIN = OUT / "os_test"
RATES = [88200, 176400, 352800, 48000, 96000, 192000, 384000]
RIPPLE_DB = 0.05
REJECT_DB = 100.0


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-ffp-contract=off", "-o", str(BIN),
                        str(ROOT / "plugin/tests/os_test.c"), "-lm"], capture_output=True, text=True)
    if r.returncode:
        sys.exit("os_test: build failed\n" + r.stderr)


def tone(rate, f):
    n_an = 1 << 16
    skip = 4096 + int(0.003 * rate)
    path = OUT / f"t-{rate}-{f:.0f}.f32"
    r = subprocess.run([str(BIN), "tone", str(rate), f"{f:.6f}", str(skip + n_an), str(path)], capture_output=True, text=True)
    lat = int(r.stdout.split()[1])
    x = np.fromfile(path, dtype=np.float32).reshape(-1, 2)[skip:, 0].astype(np.float64)
    path.unlink()
    t = np.arange(skip, skip + n_an)
    tt = (t - lat) * 44100.0 / rate                       # the app's frame each output frame stands for
    w = 2 * np.pi * f / 44100.0
    a = np.column_stack([np.sin(w * tt), np.cos(w * tt)])
    (s, c), *_ = np.linalg.lstsq(a, x, rcond=None)
    amp = np.hypot(s, c)
    ripple = 20 * np.log10(amp / 0.5)
    dt = -np.arctan2(c, s) / w * rate / 44100.0           # the tone's offset from where it should be (output frames)
    win = np.kaiser(n_an, 40.0)
    spec = np.abs(np.fft.rfft(x * win))
    k0 = f / rate * n_an
    bins = np.arange(len(spec))
    lobe = np.abs(bins - k0) <= 40                        # (beta 40: the main lobe ~ +-26 bins)
    peak = spec[lobe].max()
    rest = spec[~lobe & (bins > 40)]
    worst = rest.argmax()
    rej = 20 * np.log10(peak / max(rest[worst], 1e-300))
    fw = bins[~lobe & (bins > 40)][worst] * rate / n_an
    return ripple, rej, fw, dt, lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="16 tones a rate")
    a = ap.parse_args()
    build()
    freqs = list(np.geomspace(20.0, 20000.0, 16 if a.quick else 64))
    fails = []
    for rate in RATES:
        worst_rip, worst_rej, worst_dt, at_rej = 0.0, 1e9, 0.0, None
        for f in freqs:
            rip, rej, fw, dt, lat = tone(rate, f)
            if abs(rip) > abs(worst_rip):
                worst_rip = rip
            if rej < worst_rej:
                worst_rej, at_rej = rej, (f, fw)
            if abs(dt) > abs(worst_dt):
                worst_dt = dt
        cpu = subprocess.run([str(BIN), "cpu", str(rate), "2"], capture_output=True, text=True).stdout.split()
        ok = abs(worst_rip) <= RIPPLE_DB and worst_rej >= REJECT_DB and abs(worst_dt) <= 0.01
        print(f"{'ok  ' if ok else 'FAIL'}  {rate:6d} Hz: ripple {worst_rip:+.5f} dB, rejection {worst_rej:.1f} dB "
              f"(tone {at_rej[0]:.0f} Hz, worst at {at_rej[1]:.0f} Hz), time {worst_dt:+.4f} frames, latency {lat}, "
              f"cpu {float(cpu[2]):.0f} us/s", flush=True)
        if not ok:
            fails.append(rate)
    if fails:
        sys.exit(f"plugin_os: FAIL at {fails}")
    print("plugin_os: every rate within the targets (ripple <= 0.05 dB to 20 kHz, rejection >= 100 dB, the latency exact)")


if __name__ == "__main__":
    main()
