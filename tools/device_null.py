#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The device null test, its offline part (M1-25): a device's recording of a script against the host's render of the
same events. Both start with a marker click; the recording is aligned on it and then judged:

  digital   (the FM-1's USB at 44.1 kHz with USB VOL FIXED; the Move's dsp.so output from the dev ring): sample-exact
            after the integer alignment, or the first differing frame
  analog    (the FM-1's jack, the Move's line out, through the MOTU): the recording against the stored transfer model
            of that path (tests/baselines/device-golden.json, recorded in D2: gain in dB, delay in samples, the
            residual floor in dBFS): the fitted gain within GAIN_TOL_DB of the model's, the fitted delay within
            DELAY_TOL samples, and no 10 ms window of the residual (recording - the model applied to the render) more
            than WIN_MARGIN_DB over the floor

    tools/device_null.py --self-test           the proof: a synthetic render through a model path passes; a glitch,
                                               a 1-sample shift and a 0.1 dB gain error each fail (also in pytest)
    tools/device_null.py --dry-run             the takes per engine and device, and their minutes
    tools/device_null.py REF.wav REC.wav [--digital | --model MODEL.json]   judge one recording

The takes are recorded in the device sessions (tools/move_hil.py null, hil_* for the FM-1); this tool only reads
WAVs. Mono analysis of the left channel unless --channel 1."""
import argparse
import json
import sys
import wave

import numpy as np

FS = 44100
MARKER_S = 0.05                     # the marker click within the first 50 ms of the render
SEARCH_S = 0.5                      # the recording may start up to 0.5 s early or late
GAIN_TOL_DB = 0.05
DELAY_TOL = 0.25                    # samples
WIN_MS = 10.0
WIN_MARGIN_DB = 10.0
# the null-test script set: (take, seconds) per engine; the same MIDI on every device and the host
TAKES = {e: [(e + "-marker-scale", 20), (e + "-chords-pedal", 30), (e + "-velocity-ramp", 25), (e + "-fx", 30)]
         for e in ("rhodes", "vibes", "marimba")}
DEVICES = {"fm1": ("usb", "jack"), "move": ("ring", "line")}


def read_wav(path, channel=0):
    w = wave.open(path)
    n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, "<i2").astype(float)
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = ((b[:, 0].astype(np.int32) | b[:, 1].astype(np.int32) << 8 | b[:, 2].astype(np.int32) << 16) << 8 >> 8)
        x = x.astype(float) / 256.0
    else:
        raise SystemExit("device_null: %d-byte samples not supported" % sw)
    return x.reshape(-1, ch)[:, min(channel, ch - 1)], w.getframerate()


def frac_shift(x, d):
    """x delayed by d samples (fractional; zero-padded, FFT phase shift)"""
    n = len(x)
    m = 1 << int(np.ceil(np.log2(n + abs(int(d)) + 64)))
    X = np.fft.rfft(np.r_[x, np.zeros(m - n)])
    f = np.fft.rfftfreq(m)
    return np.fft.irfft(X * np.exp(-2j * np.pi * f * d), m)[:n]


def align(ref, rec, fs=FS):
    """the delay of rec against ref (samples, fractional) from the marker region: cross-correlation of the first
    MARKER_S of ref within +-SEARCH_S, refined by a parabola"""
    a = ref[:int((MARKER_S + 0.05) * fs)]
    s = int(SEARCH_S * fs)
    b = rec[:len(a) + s]
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    lags = np.r_[np.arange(0, s + 1), np.arange(-s, 0)]
    idx = np.r_[np.arange(0, s + 1), np.arange(n - s, n)]
    k = int(np.argmax(c[idx]))
    lag, i = lags[k], idx[k]
    y0, y1, y2 = c[(i - 1) % n], c[i], c[(i + 1) % n]
    den = y0 - 2 * y1 + y2
    return float(lag + (0.5 * (y0 - y2) / den if den else 0.0))


def judge_digital(ref, rec, fs=FS):
    """(ok, detail): sample-exact after the integer alignment"""
    d = int(round(align(ref, rec, fs)))
    a, b = (ref, rec[d:]) if d >= 0 else (ref[-d:], rec)
    n = min(len(a), len(b))
    diff = np.flatnonzero(a[:n] != b[:n])
    if len(diff):
        return False, "differs from frame %d (offset %d): %d of %d frames" % (diff[0], d, len(diff), n)
    return True, "sample-exact, offset %d, %d frames" % (d, n)


def fit(ref, rec, fs=FS, mask=None):
    """(delay, gain_db, residual): rec ~ g * ref delayed by d; d from the marker (align), refined over the whole take
    by the least residual (a golden-section search within +-1 sample: the marker alone is too short for 0.25).
    mask: frames left out of the fit (a glitch must not bend the fit and smear its residual over the take)"""
    n = min(len(ref), len(rec))
    r = rec[:n]
    wt = np.ones(n) if mask is None else (~mask[:n]).astype(float)

    def at(d):
        y = frac_shift(ref[:n], d)
        g = float(np.dot(y * wt, r) / max(np.dot(y * wt, y), 1e-12))
        return float(np.sum(wt * (r - g * y) ** 2)), g, y
    lo, hi = align(ref, rec, fs) - 1.0, align(ref, rec, fs) + 1.0
    k = (np.sqrt(5) - 1) / 2
    a, b = hi - k * (hi - lo), lo + k * (hi - lo)
    fa, fb = at(a)[0], at(b)[0]
    for _ in range(30):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - k * (hi - lo)
            fa = at(a)[0]
        else:
            lo, a, fa = a, b, fb
            b = lo + k * (hi - lo)
            fb = at(b)[0]
    d = (lo + hi) / 2
    _, g, y = at(d)
    return d, 20 * np.log10(abs(g) + 1e-12), r - g * y


def hot_windows(res, floor_db, fs=FS):
    """the 10 ms windows of the residual more than WIN_MARGIN_DB over the floor: (window indices, levels, w)"""
    w = int(WIN_MS * fs / 1000)
    m = len(res) // w
    if not m:
        return np.zeros(0, int), np.zeros(0), w
    lvl = 10 * np.log10(np.mean(res[:m * w].reshape(m, w) ** 2, axis=1) / 32768.0 ** 2 + 1e-30)
    hot = np.flatnonzero(lvl > floor_db + WIN_MARGIN_DB)
    return hot[(hot > 0) & (hot < m - 1)], lvl, w             # (the ends: the shift's edges)


def judge_analog(ref, rec, model, fs=FS):
    """(ok, [reasons]) against model {gain_db, delay, floor_db}; a second fit leaves the hot windows out (+-1)"""
    d, gdb, res = fit(ref, rec, fs)
    hot, lvl, w = hot_windows(res, model["floor_db"], fs)
    if len(hot):
        mask = np.zeros(len(res), bool)
        for h in hot:
            mask[max(0, (h - 1) * w):(h + 2) * w] = True
        d, gdb, res = fit(ref, rec, fs, mask)
        hot, lvl, w = hot_windows(res, model["floor_db"], fs)
    bad = []
    if abs(gdb - model["gain_db"]) > GAIN_TOL_DB:
        bad.append("gain %.3f dB, the model %.3f dB" % (gdb, model["gain_db"]))
    if abs(d - model["delay"]) > DELAY_TOL:
        bad.append("delay %.2f samples, the model %.2f" % (d, model["delay"]))
    if len(hot):
        bad.append("residual %.1f dBFS at %.3f s (floor %.1f; %d window(s))" % (
            lvl[hot[0]], hot[0] * w / fs, model["floor_db"], len(hot)))
    return not bad, bad or ["delay %.2f, gain %.3f dB" % (d, gdb)]


def synth_render(seconds=3.0, fs=FS, seed=1):
    """a stand-in for a host render: a marker click, then decaying partials struck at random times"""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    x = np.zeros(n)
    m = int(0.01 * fs)
    x[m:m + 32] = 20000.0 * np.hanning(32) * np.sin(np.arange(32) * 1.3)   # (a short band-limited burst)
    t = np.arange(n) / fs
    for k in range(12):
        t0 = 0.08 + rng.uniform(0, seconds - 0.5)
        f = 110 * 2 ** rng.uniform(0, 4)
        on = t >= t0
        x[on] += 3000 * np.sin(2 * np.pi * f * (t[on] - t0)) * np.exp(-(t[on] - t0) * 2.5)
    return np.round(x)


def model_path(x, gain_db=-3.0, delay=37.4, floor_db=-96.0, seed=2):
    """the render through an analog path: gain, a fractional delay, noise at floor_db"""
    rng = np.random.default_rng(seed)
    y = 10 ** (gain_db / 20) * frac_shift(x, delay)
    return y + rng.normal(0, 32768.0 * 10 ** (floor_db / 20), len(y))


def self_test(out=print):
    """[(case, want_ok, got_ok, detail)]: every case must give what it wants"""
    ref = synth_render()
    model = {"gain_db": -3.0, "delay": 37.4, "floor_db": -96.0}
    rec = model_path(ref)
    glitch = rec.copy()
    at = int(0.5 * FS) + int(np.argmax(np.abs(rec[int(0.5 * FS):-int(0.5 * FS)])))   # (where it sounds)
    glitch[at:at + 32] = 0.0
    cases = [("analog clean", True, judge_analog(ref, rec, model)),
             ("analog glitch (32 frames dropped)", False, judge_analog(ref, glitch, model)),
             ("analog 1-sample shift", False, judge_analog(ref, model_path(ref, delay=38.4), model)),
             ("analog 0.1 dB gain error", False, judge_analog(ref, model_path(ref, gain_db=-2.9), model))]
    dig = np.r_[np.zeros(100), ref]
    bad1 = dig.copy()
    bad1[50000] += 1
    cases += [("digital clean", True, judge_digital(ref, dig)),
              ("digital 1 LSB off", False, judge_digital(ref, bad1)),
              ("digital 1-sample shift kept against the marker", True, judge_digital(ref, np.r_[0.0, dig]))]
    res = []
    for name, want, (ok, det) in cases:
        res.append((name, want, ok, det))
        out("%-48s %s  %s" % (name, "ok  " if ok == want else "FAIL", det))
    out("(the glitch at %.3f s)" % (at / FS))
    return res


def dry_run(out=print):
    total = 0
    for dev, paths in DEVICES.items():
        for e, ts in TAKES.items():
            for name, s in ts:
                out("%-5s %-26s %3d s  (%s)" % (dev, name, s, " + ".join(paths)))
                total += s
    out("%d takes, %.1f min of recording (both paths of a device at once)" % (
        sum(len(t) for t in TAKES.values()) * len(DEVICES), total / 60))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ref", nargs="?")
    ap.add_argument("rec", nargs="?")
    ap.add_argument("--digital", action="store_true")
    ap.add_argument("--model", help="the path's transfer model (JSON: gain_db, delay, floor_db)")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return 0 if all(w == g for _, w, g, _ in self_test()) else 1
    if a.dry_run or not a.ref:
        dry_run()
        return 0
    ref, fr = read_wav(a.ref, a.channel)
    rec, fc = read_wav(a.rec, a.channel)
    if fr != fc:
        raise SystemExit("device_null: %d Hz against %d Hz: resample first" % (fr, fc))
    if a.digital:
        ok, det = judge_digital(ref, rec, fr)
        print(("PASS " if ok else "FAIL ") + det)
    else:
        if not a.model:
            raise SystemExit("device_null: an analog path needs --model (tests/baselines/device-golden.json)")
        ok, det = judge_analog(ref, rec, json.load(open(a.model)), fr)
        print(("PASS " if ok else "FAIL ") + "; ".join(det))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
