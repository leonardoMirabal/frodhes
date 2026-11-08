# SPDX-License-Identifier: GPL-3.0-only
"""The independent host proof of the locked USB stream's resampler (usb.c uac_tap_lock, FELUCCA_UAC_LOCK 1): the
pipes, the exact time base of every output, the float references and the measures that tests/py/test_uac_resample.py
uses.

- uac_rs_test (tests/uac_rs_test.c): the locked resampler, open loop (the ppm sets the step each block).
- uac_rs_alt_test (tests/uac_rs_alt_test.c) with FELUCCA_UAC_LOCK 0 (the path before the lock: uac_tap48 at exactly
  160 / 147) or 2 (the diagnostic: one frame dropped or repeated, stretched over 32 frames).
Each takes raw int16 mono at 44100 Hz and gives raw int16 mono at the USB rate.

The references: the locked driver's output k is the resampler's value at input position pos[k - 1] (+ a constant
delay: the FIR's and the phase table's), with pos replayed here in exact integers from the ppm schedule (uac_lock_set's
arithmetic, block by block). A sine's reference is the sine itself at those positions (a band-limited resampler's
exact answer); any other signal's is a Kaiser-windowed sinc interpolation at them (sinc_ref)."""
import os
import subprocess

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "uac_test")
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
FS = 44100
BLK = 256
ONE = 2560 << 16                 # one 44.1 kHz frame in lk_P units (UA_LK_ONE)
S48 = 147 * 16 * 65536           # 48 kHz: 147 / 160 of a frame (UA_LK_S48)
UMAX = 1 << 42                   # UA_LK_MAX


def build(name, src, *defs):
    """cc the driver into build/uac_test/<name>.<worker> (atomic: no worker runs a half-written binary)"""
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"{name}.{WORKER}")
    tmp = f"{exe}.{os.getpid()}"
    subprocess.run(["cc", "-O1", "-w", *defs, "-I" + os.path.join(ROOT, "firmware", "src"), "-o", tmp,
                    os.path.join(ROOT, "tests", src)], check=True)
    os.replace(tmp, exe)
    return exe


def drivers():
    return {"lock": build("uac_rs_test", "uac_rs_test.c"),
            "old": build("uac_rs_alt0", "uac_rs_alt_test.c", "-DFELUCCA_UAC_LOCK=0"),
            "diag": build("uac_rs_alt2", "uac_rs_alt_test.c", "-DFELUCCA_UAC_LOCK=2")}


def run(exe, x, *args):
    """the driver on x (int16 mono at 44100): its output (int16 mono at the USB rate), its stderr"""
    p = subprocess.run([exe, *map(str, args)], input=np.asarray(x, dtype="<i2").tobytes(), capture_output=True)
    assert p.returncode == 0, (exe, args, p.stderr.decode()[-500:])
    return np.frombuffer(p.stdout, dtype="<i2").astype(np.float64), p.stderr.decode()


def schedule(nblocks, p0, p1=None, secs=0.0):
    """the ppm of each block, as the driver computes it (C doubles: the same operations in the same order)"""
    p1 = p0 if p1 is None else p1
    out = []
    for b in range(nblocks):
        t = b * BLK / 44100.0
        out.append(p0 + (p1 - p0) * t / secs if secs > 0.0 and t < secs else p1)
    return out


def step(rate, ppm):
    """uac_lock_set(q52(ppm)): the step (lk_S) in exact integers"""
    s0 = S48 if rate == 48000 else ONE
    u = int(ppm * 1e-6 * 4503599627370496.0)           # (C's cast truncates toward zero, as int())
    u = max(-UMAX, min(UMAX, u))
    return s0 + ((s0 * (u >> 20)) >> 32)


def positions(nframes, rate, p0, p1=None, secs=0.0):
    """the input position (frames, float) of every output of uac_rs_test on nframes of input: output 0 is the hold's
    silence (nan), output k the value at the (k - 1)th place the resampler stepped to; and the outputs' count"""
    nb = -(-nframes // BLK)
    A, pos = 0, []
    for b, ppm in enumerate(schedule(nb, p0, p1, secs)):
        S, end = step(rate, ppm), (b + 1) * BLK * ONE
        if A < end:
            k = -(-(end - A) // S)
            pos.append(A + S * np.arange(k, dtype=np.int64))
            A += S * k
    pos = np.concatenate(pos).astype(np.float64) / ONE
    return np.concatenate([[np.nan], pos[:-1]]), len(pos)


def nominal_count(nframes, rate, p0, p1=None, secs=0.0):
    """the outputs a perfect stream gives for the same input (whole blocks), from the ppm schedule alone"""
    nb = -(-nframes // BLK)
    return sum(BLK * rate / (44100.0 * (1.0 + p * 1e-6)) for p in schedule(nb, p0, p1, secs))


def nominal_positions(n, rate, ppm):
    """the ideal place of n outputs: a stream at RATE from an FM-1 PPM fast (the delay left to the fit)"""
    return np.arange(n) * (44100.0 * (1.0 + ppm * 1e-6) / rate)


def sine(f, n, amp=0.5, ph=0.3):
    return np.round(amp * 32767 * np.sin(2 * np.pi * f / FS * np.arange(n) + ph))


def sweep(freqs, seg=0.3, fade=0.01, amp=0.5):
    """the tones one after another, seg seconds each with raised-cosine fades: the signal and each tone's span"""
    n, nf = int(FS * seg), int(FS * fade)
    env = np.ones(n)
    env[:nf] = env[-nf:][::-1] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    x = np.concatenate([np.round(env * amp * 32767 * np.sin(2 * np.pi * f / FS * np.arange(n) + 0.3)) for f in freqs])
    return x, [(i * n, (i + 1) * n) for i in range(len(freqs))]


def fit_tone(y, t, f):
    """least squares of y on DC + the sine of f Hz at input positions t (the delay and gain aligned): (fit, residual)"""
    th = 2 * np.pi * f / FS * t
    A = np.stack([np.ones_like(t), np.sin(th), np.cos(th)], 1)
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    tone = A[:, 1:] @ c[1:]
    return tone, y - A @ c


def thdn(y, t, f):
    """dB of the residual against the exact tone (THD+N against the ideal resampler)"""
    tone, r = fit_tone(y, t, f)
    return 10 * np.log10((r ** 2).mean() / (tone ** 2).mean())


def span(t, a, b):
    """the outputs whose input position lies in [a, b)"""
    ok = ~np.isnan(t)
    idx = np.nonzero(ok & (t >= a) & (t < b))[0]
    return idx


def sweep_thdn(y, t, freqs, spans, skip=0.04):
    """each tone's THD+N over its span without the fades and the transients (skip seconds at each end)"""
    k = int(FS * skip)
    out = []
    for f, (a, b) in zip(freqs, spans):
        i = span(t, a + k, b - k)
        out.append(thdn(y[i], t[i], f))
    return out


_KT = {}


def _kernel(half, beta, fc, res=4096):
    """the windowed sinc tabulated every 1/res frame over [-half, half] (linear interpolation between: < -140 dB)"""
    key = (half, beta, fc, res)
    if key not in _KT:
        d = np.arange(-half * res, half * res + 2) / res
        w = np.i0(beta * np.sqrt(np.clip(1 - (d / half) ** 2, 0, None))) / np.i0(beta)
        _KT[key] = 2 * fc * np.sinc(2 * fc * d) * w
    return _KT[key]


def sinc_ref(x, t, half=32, beta=10.0, fc=0.5, res=4096):
    """the band-limited value of x (at 44100) at fractional positions t: Kaiser-windowed sinc (half-width `half`
    frames, cutoff fc cycles a frame, stopband ~-100 dB); nan positions give nan"""
    x = np.asarray(x, dtype=np.float64)
    tab = _kernel(half, beta, fc, res)
    out = np.full(len(t), np.nan)
    ok = np.nonzero(~np.isnan(t))[0]
    xp = np.concatenate([np.zeros(half + 1), x, np.zeros(half + 1)])
    taps = np.arange(-half + 1, half + 1)
    for s in range(0, len(ok), 20000):
        k = ok[s:s + 20000]
        tk = t[k]
        n0 = np.floor(tk).astype(np.int64)
        q = (tk - n0) * res                              # the fraction, in table steps
        qi = q.astype(np.int64)
        qf = q - qi
        j = (taps[None, :] * -res + half * res)[:, :] + qi[:, None]   # d = t - n = frac - tap
        h = tab[j] + (tab[j + 1] - tab[j]) * qf[:, None]
        idx = np.clip(n0[:, None] + taps[None, :] + half + 1, 0, len(xp) - 1)
        out[k] = (h * xp[idx]).sum(1)
    return out


def fit_delay(y, x, t, lo=0.0, hi=12.0):
    """the constant delay d (input frames) that best lines sinc_ref(x, t - d) up with y (least squares, a grid then a
    golden-section search)"""
    def err(d):
        r = sinc_ref(x, t - d)
        ok = ~np.isnan(r)
        g = (r[ok] @ y[ok]) / (r[ok] @ r[ok])
        return ((y[ok] - g * r[ok]) ** 2).sum()
    grid = np.arange(lo, hi, 0.5)
    e = [err(d) for d in grid]
    a, b = grid[max(0, int(np.argmin(e)) - 1)], grid[min(len(grid) - 1, int(np.argmin(e)) + 1)]
    g = (np.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(25):
        if err(c) < err(d):
            b, d = d, c
            c = b - g * (b - a)
        else:
            a, c = c, d
            d = a + g * (b - a)
    return (a + b) / 2


def windows(t, secs):
    """the outputs grouped by their input position into windows of secs seconds (whole windows only, the first
    skipped: the start): the same stretch of the input in every run, whatever the ppm"""
    ok = ~np.isnan(t)
    w = np.full(len(t), -1, dtype=np.int64)
    w[ok] = (t[ok] // (secs * FS)).astype(np.int64)
    last = int(w.max())                                 # (the last one may be partial)
    order = np.argsort(w, kind="stable")
    bounds = np.searchsorted(w[order], np.arange(1, last + 1))
    return [order[a:b] for a, b in zip(bounds[:-1], bounds[1:])]


def snr_windows(y, r, t, secs=0.5, floor=1e4):
    """dB of the error y - r against r in windows of the input (windows where r's mean square is under floor: nan)"""
    out = []
    for i in windows(t, secs):
        a, b = y[i], r[i]
        out.append(np.nan if (b ** 2).mean() < floor else 10 * np.log10(((a - b) ** 2).mean() / (b ** 2).mean()))
    return np.array(out)


def thdn_windows(y, t, f, secs=0.5):
    """a tone's THD+N in windows of the input"""
    return np.array([thdn(y[i], t[i], f) for i in windows(t, secs)])


def bh4(n):
    k = np.arange(n) / n
    return 0.35875 - 0.48829 * np.cos(2 * np.pi * k) + 0.14128 * np.cos(4 * np.pi * k) - 0.01168 * np.cos(6 * np.pi * k)


def sidebands(y, t, f, rate, ppm, win, band=1000.0):
    """the worst sideband: in windows of win outputs, the tone removed at its exact positions, the strongest
    component of the residual within +-band Hz of the tone (Blackman-Harris, the bin's level) against the tone's
    peak, dB; one value per window"""
    w = bh4(win)
    fo = f * (1.0 + ppm * 1e-6) if ppm is not None else None   # (the FM-1's clock fast: the tone higher)
    out = []
    for s in range(0, len(y) - win + 1, win):
        tk, yk = t[s:s + win], y[s:s + win]
        if np.isnan(tk).any():
            continue
        tone, r = fit_tone(yk, tk, f)
        T = np.abs(np.fft.rfft(tone * w))
        R = np.abs(np.fft.rfft(r * w))
        fr = np.fft.rfftfreq(win, 1.0 / rate)
        fk = fr[np.argmax(T)] if fo is None else fo
        sel = np.abs(fr - fk) <= band
        out.append(20 * np.log10(R[sel].max() / T.max()))
    return np.array(out)


def piano(n):
    """the host build's built-in demo (what make host renders to build/host/frodhes.wav), left channel, repeated to n
    samples with 50 ms of silence between the repeats (no click of its own at a seam). Rendered into this worker's
    own directory from the shared binary (hostrun: built once, atomically): `make host` here recompiled
    build/host/frodhes in place while another worker ran it, and failed under a parallel run (2026-11-08)"""
    import wave
    import hostrun
    out = hostrun.out("uacrs-piano")
    if not os.path.exists(out + ".wav"):
        subprocess.run([hostrun.build(), out], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    v = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())[:, 0].astype(float)
    nf = int(FS * 0.01)
    v[:nf] *= np.arange(nf) / nf
    v[-nf:] *= np.arange(nf)[::-1] / nf
    one = np.concatenate([v, np.zeros(int(FS * 0.05))])
    return np.tile(one, n // len(one) + 1)[:n]
