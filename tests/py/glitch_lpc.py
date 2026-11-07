# SPDX-License-Identifier: GPL-3.0-only
"""The glitch detector (a copy of the one the FM-1 takes are judged with; clean takes score <= 1.4 events a minute): per channel, LPC (order P) fitted on each 20 ms frame from the frame itself, residual; an event =
a residual sample >= THR x the robust local residual level, with the excess confined to <= W samples (a glitch is a
few bad samples; an attack keeps the residual high for ms). Also scores injected glitches when --inject."""
import sys, numpy as np, wave
from numpy.lib.stride_tricks import sliding_window_view as sw
P, THR, W = 24, 10.0, 30
def lpc(x, p):
    r = np.correlate(x, x, 'full')[len(x)-1:len(x)+p] ; r[0] *= 1.0001; r[0] += 1e-9
    a = np.zeros(p+1); a[0] = 1; e = r[0]
    for i in range(1, p+1):
        k = -(r[i] + a[1:i] @ r[i-1:0:-1]) / e
        a[1:i+1] += k * np.concatenate([a[i-1:0:-1], [1]]) ; e *= 1 - k*k
    return a
def resid(x, fs):
    N = int(fs*0.02); e = np.zeros_like(x)
    for s in range(P, len(x)-N, N):
        seg = x[s-P:s+N]; a = lpc(seg*np.hanning(len(seg)), P)
        e[s:s+N] = np.convolve(seg, a, 'valid')[:N]
    return e
def events(x, fs):
    e = np.abs(resid(x, fs)); k = int(fs*0.004); nb = len(e)//k
    bm = np.median(e[:nb*k].reshape(nb, k), 1); bm = np.maximum(np.maximum(bm, np.roll(bm, 1)), np.roll(bm, -1))
    lvl = np.concatenate([np.repeat(bm, k), np.full(len(e)-nb*k, bm[-1])])*1.4826 + 1.0
    s = e/lvl; cand = np.where(s > THR)[0]; ev = []
    for i in cand:
        if ev and i-ev[-1] < int(fs*0.003): continue
        w = s[max(0,i-60):i+60] > THR/2
        if w.sum() <= W: ev.append(i)
    return np.array(ev, dtype=int)
def load(p):
    w = wave.open(p); fs = w.getframerate(); ch = w.getnchannels(); n = w.getnframes()
    return np.frombuffer(w.readframes(n), dtype=np.int16).reshape(-1, ch).astype(float), fs
if __name__ == "__main__":
    X, fs = load(sys.argv[1]); inj = "--inject" in sys.argv
    if inj:
        rng = np.random.default_rng(1); pos = rng.integers(fs, len(X)-fs, 60); X = X.copy()
        for j, p in enumerate(pos):
            kind = j % 4; amp = np.std(X[p-500:p+500, 0]) + 50
            if kind == 0: X[p] += amp*1.5                      # one bad sample
            elif kind == 1: X[p:p+2] = X[p-2:p]                 # two repeated samples
            elif kind == 2: X[p:] = np.concatenate([X[p+1:], X[-1:]])  # one sample dropped
            else: X[p] = 0                                      # a zeroed sample
    evL, evR = events(X[:, 0], fs), events(X[:, 1], fs)
    both = [i for i in evL if len(evR) and np.min(np.abs(evR-i)) < 3]
    dur = len(X)/fs
    print(sys.argv[1].split('/')[-1], f"dur {dur:.0f}s  L {len(evL)}  R {len(evR)}  both {len(both)}  ({len(both)/dur*60:.1f}/min)")
    if inj:
        hit = sum(1 for p in pos if (len(evL) and np.min(np.abs(evL-p)) < 4) or (len(evR) and np.min(np.abs(evR-p)) < 4))
        print("  injected 60 (bad sample / repeat / drop / zero): found", hit)
    if not inj and len(both): print("  first", [round(i/fs, 3) for i in both[:12]])
