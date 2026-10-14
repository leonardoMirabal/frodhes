# SPDX-License-Identifier: GPL-3.0-only
"""Analysis primitives for the Frodhes realism checks.

These functions run unchanged on reference recordings, the float golden model and the C renders, so all three
are measured the same way (IMPLEMENTATION §4).

    band(x, fs, f, bw)       analytic signal of x band-limited to f*(1 +- bw) (FFT-domain, zero phase)
    env_db(z, fs, win)       envelope in dB of an analytic signal, moving-RMS smoothed
    inst_f(z, fs, t0, t1)    instantaneous frequency (Hz): linear fit of the unwrapped phase over [t0, t1]
    harm(x, fs, f0, k, t0, t1)  power of partial k*f0 (+-3 %) from a Hann-windowed FFT over [t0, t1]
    decay_fit(e_db, fs, ...)    two-segment piecewise-linear fit of an envelope: early/late T60, breakpoint
    noise_floor_db(e_db, fs)    median envelope level of the last 0.2 s
    cents(f, ref)            1200*log2(f/ref)
"""
import numpy as np

__all__ = ["band", "env_db", "inst_f", "harm", "decay_fit", "noise_floor_db", "cents", "t60_from_slope"]


def cents(f, ref):
    return 1200.0 * np.log2(np.asarray(f, float) / ref)


def band(x, fs, f, bw=0.1):
    """Analytic (complex) signal of x restricted to [f(1-bw), f(1+bw)], zero-phase, via the FFT."""
    x = np.asarray(x, float)
    n = len(x)
    nfft = 1 << int(np.ceil(np.log2(max(n, 2))))
    X = np.fft.fft(x, nfft)
    freqs = np.fft.fftfreq(nfft, 1.0 / fs)
    lo, hi = f * (1.0 - bw), f * (1.0 + bw)
    H = np.zeros(nfft)
    pos = (freqs >= lo) & (freqs <= hi)
    H[pos] = 2.0                                  # positive band only, doubled: analytic signal
    # raised-cosine skirts (10 % of the band) to limit ringing
    skirt = 0.1 * (hi - lo) + 1e-9
    for edge, sgn in ((lo, -1), (hi, 1)):
        m = (freqs > 0) & (np.abs(freqs - edge) < skirt) & ~pos
        H[m] = 2.0 * 0.5 * (1 + np.cos(np.pi * np.abs(freqs[m] - edge) / skirt))
    return np.fft.ifft(X * H)[:n]


def env_db(z, fs, win=0.010):
    """Envelope in dB re full scale (amplitude 1.0), smoothed by a moving RMS of `win` seconds."""
    p = np.abs(z) ** 2
    w = max(1, int(round(win * fs)))
    k = np.ones(w)
    num = np.convolve(p, k, mode="same")
    cnt = np.convolve(np.ones_like(p), k, mode="same")   # edge-normalised: no fake fade at the ends
    return 10.0 * np.log10(num / cnt + 1e-30)


def inst_f(z, fs, t0, t1):
    """Mean instantaneous frequency over [t0, t1] s: slope of a least-squares line through the unwrapped phase."""
    i0, i1 = int(t0 * fs), int(t1 * fs)
    ph = np.unwrap(np.angle(z[i0:i1]))
    t = np.arange(len(ph)) / fs
    slope = np.polyfit(t, ph, 1)[0]
    return slope / (2.0 * np.pi)


def harm(x, fs, f0, k, t0, t1):
    """Power (linear, re full-scale sine) of partial k*f0 +- 3 % over [t0, t1], Hann-windowed FFT peak sum."""
    i0, i1 = int(t0 * fs), int(t1 * fs)
    seg = np.asarray(x[i0:i1], float)
    w = np.hanning(len(seg))
    S = np.fft.rfft(seg * w)
    fr = np.fft.rfftfreq(len(seg), 1.0 / fs)
    m = (fr >= k * f0 * 0.97) & (fr <= k * f0 * 1.03)
    if not m.any():
        return 0.0
    return float(np.sum(np.abs(S[m]) ** 2) / (np.sum(w) ** 2 / 4.0) / 2.0) * 2.0


def noise_floor_db(e_db, fs, tail=0.2):
    n = max(1, int(tail * fs))
    return float(np.median(e_db[-n:]))


def t60_from_slope(slope_db_per_s):
    return float("inf") if slope_db_per_s >= 0 else -60.0 / slope_db_per_s


def decay_fit(e_db, fs, top_db=3.0, bottom_db=45.0, noise_margin_db=10.0, decim=0.005, min_span_db=8.0):
    """Two-segment piecewise-linear least-squares fit of an envelope (dB) from (peak - top_db) down to
    max(peak - bottom_db, noise floor + noise_margin_db). The breakpoint is searched exhaustively.

    Returns dict: t60_early, t60_late (s), break_db (level at the breakpoint re peak), break_t (s), rms_err (dB),
    and the fit window. A single-slope signal returns t60_early == t60_late (within the fit noise)."""
    e = np.asarray(e_db, float)
    step = max(1, int(decim * fs))
    ip = int(np.argmax(e))
    peak = e[ip]
    floor = noise_floor_db(e, fs)
    stop_level = max(peak - bottom_db, floor + noise_margin_db)
    idx = np.arange(ip, len(e), step)
    lv = e[idx]
    start = np.argmax(lv <= peak - top_db) if np.any(lv <= peak - top_db) else 0
    below = np.nonzero(lv[start:] <= stop_level)[0]
    end = start + (below[0] if len(below) else len(lv) - start)
    t = idx[start:end] / fs
    y = lv[start:end]
    if len(t) < 8:
        raise ValueError("decay_fit: window too short")

    def lin(tt, yy):
        A = np.vstack([tt, np.ones_like(tt)]).T
        c, res, *_ = np.linalg.lstsq(A, yy, rcond=None)
        r = float(res[0]) if len(res) else float(np.sum((A @ c - yy) ** 2))
        return c, r

    best = None
    c1, r1 = lin(t, y)
    best = (r1, None, c1, c1)
    for b in range(4, len(t) - 4):
        if y[0] - y[b] < min_span_db or y[b] - y[-1] < min_span_db:
            continue
        ca, ra = lin(t[:b], y[:b])
        cb, rb = lin(t[b:], y[b:])
        if ra + rb < best[0] * 0.7:               # a break must cut the error by >= 30 % (noise bends do not)
            best = (ra + rb, b, ca, cb)
    err, b, ca, cb = best
    if b is None:
        return dict(t60_early=t60_from_slope(c1[0]), t60_late=t60_from_slope(c1[0]), break_db=None, break_t=None,
                    rms_err=float(np.sqrt(err / len(t))), window=(float(t[0]), float(t[-1])))
    return dict(t60_early=t60_from_slope(ca[0]), t60_late=t60_from_slope(cb[0]),
                break_db=float(ca[0] * t[b] + ca[1] - peak), break_t=float(t[b]),
                rms_err=float(np.sqrt(err / len(t))), window=(float(t[0]), float(t[-1])))
