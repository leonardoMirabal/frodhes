# SPDX-License-Identifier: GPL-3.0-only
"""Shared pieces of the bar-engine scans (tools/bar_scan.py, tools/bar_freq_scan.py): rendering through the host build
(build/host/frodhes, the tests/host.c script interface), the CC map, and the measurements (clicks, HP bursts,
loudness, band envelopes, frequency, T60, t_90, AM depth). Everything at the host's 44.1 kHz; nothing here reads
recordings."""
import os
import re
import subprocess
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100

# parameter CCs (params.c PARAMS[].cc) and the fixed ones (frodhes.c midi_cc)
CC = dict(TYPE=80, DRIVE=81, TW_SW=17, TW_DEPTH=18, TW_RATE=19, TW_SYNC=20, TW_MODE=21, TW_SHAPE=22, CP_SW=85,
          CP_DEPTH=86, CP_SPEED=87, DL_SW=88, DL_DEPTH=89, DL_TIME=90, DL_FDBK=26, REV=91, TONE=28, BARK=29, BELL=30,
          HAMMER=31, DECAY=102, RELEASE=103, NOISE=104, PH_SW=12, PH_DEPTH=13, PH_SPEED=14, PH_FDBK=16, CMP_SW=106,
          CMP_AMT=107, CMP_ATK=108, CMP_REL=109, CMP_RAT=112, CMP_GAIN=113, FL_TYPE=70, FL_FREQ=74, FL_RESO=71,
          FL_LFO=72, SUS=64, RAW=119, ARP_MODE=52, ARP_RATE=53, ARP_OCT=54, SEQ_PLAY=60, SEQ_BARS=61, SEQ_QUANT=62,
          SEQ_REC=63, SEQ_CLEAR=43)
CARDS = ["TONE", "BARK", "BELL", "HAMMER", "DECAY", "RELEASE", "NOISE"]      # (+ TUNE, set from the panel)
BAR_NAME = dict(TONE="STRIKE", BARK="MALLET", BELL="TUBE", HAMMER="WEIGHT", DECAY="RING", RELEASE="DAMPER",
                NOISE="NOISE", TUNE="TUNE")


def n_types():
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    return int(re.search(r'\[P_TYPE\] = \{"[^"]*", (\d+),', src).group(1)) + 1


def type_cc(t):
    """the CC value selecting TYPE t on the CC that type_ccnum(t) names: CC 80 (by 32) for the Rhodes types 0..3 as
    before, CC 79 for the bar types (one band each over 0..127)"""
    if t < 4:
        return t * 32 + 8
    w = 128 / max(1, n_types() - 4)
    return int((t - 4) * w + w / 2)


def type_ccnum(t):
    return 80 if t < 4 else 79


def type_name(t):
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    names = re.search(r"N_TYPE_LONG\[\] = \{([^}]*)\}", src).group(1)
    return [s.strip().strip('"') for s in names.split(",")][t]


def build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def tune_lines(t, cents):
    """TUNE is a panel setting (no CC): EDIT (VOICE), SELECT to DECAY, K4"""
    if not cents:
        return []
    return [(t, "btn 6"), (t + 10, "turn 0 1"), (t + 20, f"turn 5 {int(cents)}"), (t + 30, "btn 8")]


def render(tag, events, end_ms, outdir, env=None):
    """events: (ms, line-without-time) pairs, e.g. (100, "midi 0x90 60 100"); env: extra environment for the host
    build (its switches: FRODHES_NOSLEEP, FRODHES_NOBUDGET, FRODHES_FXUS, FRODHES_MSLOG); returns (left float array,
    log text)"""
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, tag)
    lines = sorted(((int(t), s) for t, s in events), key=lambda e: e[0])
    open(out + ".txt", "w").write("".join(f"{t} {s}\n" for t, s in lines) + f"{int(end_ms)} end\n")
    r = subprocess.run([BIN, out, out + ".txt"], capture_output=True, text=True,
                       env=dict(os.environ, **env) if env else None)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float)
    return x, r.stdout + r.stderr


def counters(log):
    """the host's engine counters, when the build prints them (lane C: "sat28 hits N", "mode-samples N",
    "ev_drop N", "steals N"); missing ones are None"""
    out = {}
    for k in ("sat28 hits", "mode-samples", "ev_drop", "steals", "sheds", "active"):
        m = re.findall(re.escape(k) + r"\s*[=:]?\s*(\d+)", log)
        out[k] = int(m[-1]) if m else None
    return out


def setup_events(t0=20, typ=0, raw=True, noise=None, **cc):
    ev = [(t0, f"midi 0xB0 {type_ccnum(typ)} {type_cc(typ)}")]
    if raw:
        ev.append((t0, "midi 0xB0 119 127"))
    if noise is not None:
        ev.append((t0, f"midi 0xB0 104 {noise}"))
    for k, v in cc.items():
        ev.append((t0, f"midi 0xB0 {CC[k]} {int(v)}"))
    return ev


def note(t, n, v, dur=None):
    ev = [(t, f"midi 0x90 {n} {v}")]
    if dur is not None:
        ev.append((t + dur, f"midi 0x80 {n} 0"))
    return ev


# ---------------------------------------------------------------- measurements
def mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def db(x):
    x = np.asarray(x, float)
    return 20 * np.log10(np.sqrt(np.mean(x * x)) / 32767 + 1e-12) if x.size else -240.0


def clicks(x):
    """hil_scan.clicks: second-difference spikes far above the local level"""
    m = mono(x)
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(441) / 441, "same")[1:-1] + 50
    return int((d2 > 6 * lvl + 2000).sum())


def hp_bursts(x, events_ms, rel_db=-40.0, guard_ms=3.0, floor_db=-80.0):
    """1 ms windows whose energy above 8 kHz is more than rel_db re the window's full-band energy, outside
    +-guard_ms of a note-on / key-up / any event; windows quieter than floor_db (dBFS) are not judged"""
    from scipy.signal import butter, sosfilt
    m = mono(x)
    hp = sosfilt(butter(4, 8000, "hp", fs=FS, output="sos"), m)
    w = FS // 1000
    n = len(m) // w
    e_all = (m[:n * w].reshape(n, w) ** 2).mean(axis=1)
    e_hp = (hp[:n * w].reshape(n, w) ** 2).mean(axis=1)
    ok = np.ones(n, bool)
    for t in events_ms:
        i = int(t)
        ok[max(0, i - int(guard_ms) - 6):i + int(guard_ms) + 6] = False   # (+ the one-block event latency, 5.8 ms)
    loud = e_all > (32767 * 10 ** (floor_db / 20)) ** 2
    bad = ok & loud & (10 * np.log10(e_hp / (e_all + 1e-20) + 1e-20) > rel_db)
    return int(bad.sum()), np.flatnonzero(bad)


def k_weight_sos(fs=FS):
    """BS.1770 K-weighting at any rate: the shelf and the RLB high-pass from their analog prototypes"""
    import math
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    a0 = 1 + K / Q + K * K
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return np.array([b + a, b2 + a2])


def lufs(x):
    from scipy.signal import sosfilt
    x = np.atleast_2d(np.asarray(x, float).T).T / 32767
    sos = k_weight_sos()
    return -0.691 + 10 * np.log10(sum(np.mean(sosfilt(sos, x[:, c]) ** 2) for c in range(x.shape[1])) + 1e-15)


def centroid(x):
    m = mono(x)
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    fr = np.fft.rfftfreq(len(m), 1 / FS)
    return float((sp * fr).sum() / (sp.sum() + 1e-12))


def band_env(x, f, rel=0.015, n=8192, hop=1024):
    """the envelope (dB re full scale, per hop) of the strongest STFT bin within +-rel of f; times in s"""
    m = mono(x)
    if len(m) < n:
        m = np.pad(m, (0, n - len(m)))
    win = np.hanning(n)
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = (fr >= f * (1 - rel) - FS / n) & (fr <= f * (1 + rel) + FS / n)
    frames = range(0, len(m) - n + 1, hop)
    env = np.array([np.abs(np.fft.rfft(m[i:i + n] * win))[sel].max() if sel.any() else 0 for i in frames])
    t = (np.array(list(frames)) + n / 2) / FS
    return t, 20 * np.log10(env * 2 / win.sum() / 32767 + 1e-12)


def inst_f(x, f, t0=0.3, t1=2.3, rel=0.03):
    """a partial's frequency near f (Hz) over t0..t1 s: zero-padded FFT peak, Gaussian (log-parabolic) refinement"""
    m = mono(x)[int(t0 * FS):int(t1 * FS)]
    if len(m) < 2048:
        return None
    nfft = 1 << int(np.ceil(np.log2(len(m) * 4)))
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), nfft))
    fr = np.fft.rfftfreq(nfft, 1 / FS)
    sel = np.flatnonzero((fr > f * (1 - rel)) & (fr < f * (1 + rel)))
    if not len(sel):
        return None
    i = sel[np.argmax(sp[sel])]
    if not (0 < i < len(sp) - 1) or sp[i] <= 0:
        return None
    a, b, c = np.log(sp[i - 1:i + 2] + 1e-20)
    p = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0
    return float((i + p) * FS / nfft), float(20 * np.log10(sp[i] * 2 / np.hanning(len(m)).sum() / 32767 + 1e-12))


def cents(f, ref):
    return 1200 * np.log2(f / ref)


def decay_fit(t, env, t0=0.3, t1=8.0, span_db=40.0, floor_db=-100.0):
    """T60 (s) from a linear fit of the envelope (dB) between t0 and t1, over at most span_db of fall and above
    floor_db + 10; None when there is not enough to fit"""
    m = (t >= t0) & (t <= t1) & (env > floor_db + 10)
    if m.sum() < 4:
        return None
    tt, ee = t[m], env[m]
    keep = ee >= ee[0] - span_db
    tt, ee = tt[keep], ee[keep]
    if len(tt) < 4:
        return None
    s = np.polyfit(tt, ee, 1)[0]
    return float(-60 / s) if s < 0 else float("inf")


def t_frac(t, env, frac_db=-0.92):
    """the time the envelope first comes within frac_db (90 % amplitude = -0.92 dB) of its peak"""
    if not len(env):
        return None
    i = int(np.argmax(env))
    j = np.flatnonzero(env[:i + 1] >= env[i] + frac_db)
    return float(t[j[0]]) if len(j) else float(t[i])


def am_depth(x, t0, t1, rate_hint=None):
    """the full-band AM: peak-to-trough (dB) of the 10 ms RMS envelope over t0..t1 (the decay trend removed) and its
    rate (Hz, the envelope's spectral peak 0.3..15 Hz)"""
    m = mono(x)[int(t0 * FS):int(t1 * FS)]
    if len(m) < FS // 2:
        return None, None
    e = np.sqrt(np.convolve(m * m, np.ones(441) / 441, "same") + 1e-9)[441:-441]
    le = 20 * np.log10(e + 1e-9)
    tt = np.arange(len(le)) / FS
    le = le - np.polyval(np.polyfit(tt, le, 1), tt)
    sp = np.abs(np.fft.rfft((le - le.mean()) * np.hanning(len(le)), 1 << 20))
    fr = np.fft.rfftfreq(1 << 20, 1 / FS)
    sel = (fr > 0.3) & (fr < 15)
    rate = float(fr[sel][np.argmax(sp[sel])])
    return float(np.percentile(le, 98) - np.percentile(le, 2)), rate


def load_spec(path):
    """a key -> modes table: bars_spec.json (gen_bars.py) or rhodes_spec.json, both {"keys": [{"midi", "modes":
    [{"name", "f", "t60", ...}]}]}; None when missing"""
    import json
    if not path or not os.path.exists(path):
        return None
    s = json.load(open(path))
    return {k["midi"]: k for k in s.get("keys", [])}


def load_design(path):
    """bars_spec.json's design rows (tools/gen_bars.py: f0, peak_ms, ...), [] when missing or a Rhodes spec"""
    import json
    if not path or not os.path.exists(path):
        return []
    return json.load(open(path)).get("design", [])


def spec_for_type(t):
    """the spec file for TYPE t: the bar types' bars_spec.json, the Rhodes types' rhodes_spec.json (STAGE 77)"""
    d = os.path.join(ROOT, "docs", "model", "data")
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    eng = re.search(r"TYPE_ENGINE\[N_TYPE_N\] = \{([^}]*)\}", src)
    bars = eng and [int(v) for v in eng.group(1).split(",")][t] != 0
    return os.path.join(d, "bars_spec.json" if bars else "rhodes_spec.json"), bool(bars)


# ---------------------------------------------------------------- small PNG plots (Pillow; no matplotlib here)
def plot_panels(path, panels, w=520, h=260, cols=2):
    """panels: [(title, [(label, color, xs, ys)], xlabel, ylabel, (ymin, ymax) or None)]"""
    from PIL import Image, ImageDraw
    rows = (len(panels) + cols - 1) // cols
    img = Image.new("RGB", (w * cols, h * rows), (250, 248, 243))
    d = ImageDraw.Draw(img)
    for pi, (title, series, xl, yl, yr) in enumerate(panels):
        ox, oy = (pi % cols) * w, (pi // cols) * h
        xs = [x for _, _, X, _ in series for x in X]
        ys = [y for _, _, _, Y in series for y in Y if y is not None and np.isfinite(y)]
        if not xs or not ys:
            d.text((ox + 44, oy + 4), title + " (no data)", fill=(0, 0, 0))
            continue
        x0, x1 = min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1
        y0, y1 = yr if yr else (min(ys), max(ys) if max(ys) > min(ys) else min(ys) + 1)
        fx = lambda x: ox + 50 + (w - 70) * (x - x0) / (x1 - x0)
        fy = lambda y: oy + 24 + (h - 60) * (y1 - max(y0, min(y1, y))) / (y1 - y0)
        d.text((ox + 50, oy + 4), title, fill=(0, 0, 0))
        for k in range(5):
            yv = y0 + (y1 - y0) * k / 4
            d.line([(ox + 50, fy(yv)), (ox + w - 20, fy(yv))], fill=(220, 215, 205))
            d.text((ox + 4, fy(yv) - 6), f"{yv:.3g}", fill=(120, 110, 100))
        for k in range(5):
            xv = x0 + (x1 - x0) * k / 4
            d.text((fx(xv) - 8, oy + h - 32), f"{xv:.0f}", fill=(120, 110, 100))
        d.text((ox + w // 2 - 20, oy + h - 18), xl, fill=(80, 80, 80))
        for li, (label, col, X, Y) in enumerate(series):
            pts = [(fx(a), fy(b)) for a, b in zip(X, Y) if b is not None and np.isfinite(b)]
            if len(pts) > 1:
                d.line(pts, fill=col, width=2)
            for p in pts:
                d.ellipse([p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2], fill=col)
            d.text((ox + 60 + 110 * li, oy + 14), label, fill=col)
    img.save(path)
