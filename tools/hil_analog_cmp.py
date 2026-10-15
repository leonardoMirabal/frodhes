#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Compare the FM-1's analog output (recorded by an audio interface) with its USB audio of the same performance.

A least-squares FIR (the analog path's linear response: DAC filter, coupling caps, the jack) maps USB -> analog;
what is left is what the analog hardware adds: distortion and noise. Per segment it reports the signal, the
residual (dB re the signal) and, in silence, the noise floor.

  hil_analog_cmp.py USB.npy ANALOG.npy CHANNEL [seg_start:seg_end,...]   (seconds; float arrays at 48 kHz)
"""
import sys

import numpy as np

FS = 48000


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-15)


def align(u, a):
    n = 1 << 16
    i = int(np.argmax(np.abs(u) > 0.01))
    seg = slice(i, i + n)
    c = np.fft.irfft(np.fft.rfft(a[seg], 2 * n) * np.conj(np.fft.rfft(u[seg], 2 * n)))
    lag = int(np.argmax(np.abs(c[:4000])))
    return lag


def fir_fit(u, a, taps=256, pre=32):
    """a[n] ~ sum h[k] u[n - k + pre]"""
    n = len(u)
    U = np.zeros((n, taps))
    for k in range(taps):
        s = k - pre
        if s >= 0:
            U[s:, k] = u[:n - s]
        else:
            U[:n + s, k] = u[-s:]
    h, *_ = np.linalg.lstsq(U, a, rcond=None)
    return h, U @ h


def main():
    u = np.load(sys.argv[1])
    a = np.load(sys.argv[2])
    ch = int(sys.argv[3])
    segs = [tuple(map(float, s.split(":"))) for s in sys.argv[4].split(",")] if len(sys.argv) > 4 else []
    usb = u[:, 0] if u.ndim == 2 else u
    an = a[:, ch] if a.ndim == 2 else a
    lag = align(usb, an)
    an = an[lag:]
    usb = usb[:len(an)]
    # the two clocks drift (FM-1 USB vs the interface): fractional lag in 0.25 s windows, a straight line, resample
    W = FS // 4
    tl, ll = [], []
    for st in range(0, len(usb) - W, W):
        x, y = usb[st:st + W], an[st:st + W]
        if np.sqrt(np.mean(x ** 2)) < 1e-3:
            continue
        c = np.fft.irfft(np.fft.rfft(y, 2 * W) * np.conj(np.fft.rfft(x, 2 * W)))
        c = np.concatenate([c[-64:], c[:65]])
        k = int(np.argmax(np.abs(c)))
        if 0 < k < len(c) - 1:
            p0, p1, p2 = np.abs(c[k - 1:k + 2])
            frac = 0.5 * (p0 - p2) / (p0 - 2 * p1 + p2 + 1e-30)
        else:
            frac = 0.0
        tl.append(st + W / 2)
        ll.append(k - 64 + frac)
    if len(tl) >= 2:
        sl, ic = np.polyfit(tl, ll, 1)
        idx = np.arange(len(usb)) * (1 + sl) + ic
        an = np.interp(idx, np.arange(len(an)), an)
        print(f"clock drift {sl * 1e6:.0f} ppm, residual lag spread {np.std(np.array(ll) - (np.array(tl) * sl + ic)):.2f} samples")
    # the linear response, fitted on the loudest second (64 taps, 16 ahead)
    e = np.convolve(usb ** 2, np.ones(FS) / FS, "same")
    c = int(np.argmax(e))
    fs = slice(max(0, c - FS // 2), c + FS // 2)
    h, _ = fir_fit(usb[fs], an[fs], taps=64, pre=16)
    pred = np.convolve(usb, h, "full")[16:16 + len(usb)]
    res = an - pred
    print(f"lag {lag} samples ({lag / FS * 1000:.2f} ms), analog/USB level {20 * np.log10(np.sqrt(np.sum(h ** 2))):.1f} dB")
    for t0, t1 in segs or [(0, len(an) / FS)]:
        s = slice(int(t0 * FS), int(t1 * FS))
        print(f"  {t0:4.1f}-{t1:4.1f} s: analog {db(an[s]):6.1f} dB, residual {db(res[s]):6.1f} dB "
              f"({db(res[s]) - db(an[s]):6.1f} dB re signal), usb {db(usb[s]):6.1f} dBFS, usb peak "
              f"{20 * np.log10(np.abs(usb[s]).max() + 1e-12):5.1f}")


if __name__ == "__main__":
    main()
