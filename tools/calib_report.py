#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Calibration report of the Frodhes voice: every key, many velocities, measured the same way every time.

    tools/calib_report.py                       golden model (tools/rhodes_model.py), keys 28..103
    tools/calib_report.py --host                + the C engine on the host (build/host/frodhes): the 4 TYPEs
    tools/calib_report.py --name before         save build/calib/before.{json,txt}
    tools/calib_report.py --compare before      print this run against build/calib/before.json
    tools/calib_report.py --keys 40-80 --quick  fewer velocities, shorter renders

Per key (golden model, the generated gains, STAGE 77):
  level   RMS over 50..300 ms (dBFS) per velocity, monotonicity (largest drop), range v1..v127 and v8..v127,
          the curve's deviation from the median curve of all keys (shape), neighbour differences
  timbre  spectral centroid (Hz and in harmonics of f0), H2/H1, H3/H1 (dB, 30..300 ms), bark B(v) = 10 log10(sum
          of harmonics 2..5 / H1) (IMPLEMENTATION-v3 C6), the bell partial (O1, ~7 f0) re H1 over the first 80 ms
  attack  peak over the first 250 ms (dBFS) and peak / RMS at v = 127
  decay   T60 of the fundamental (late slope 0.3..3 s; early 20..300 ms) at v = 100 against params t60_v1, the
          release T60 after key-up against params release_t60, the voice life (life_blocks) against the time the
          voice takes to fall under its kill level
  tuning  cents of the sustain (0.5..2.5 s) re f0, and the attack glide (20..100 ms re 0.4..0.9 s), v = 127 and 40
Per TYPE (--host): level of every key at v = 100 and its timbre, re STAGE 77.

Physics-only instrument: nothing here reads recordings; the targets are params.json (Shear tine Q, release priors).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rhodes_dsp as dsp      # noqa: E402
import rhodes_model as gm     # noqa: E402

FS = 44100
VELS = [1, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 127]
VELS_QUICK = [1, 8, 24, 40, 56, 72, 88, 100, 104, 120, 127]
TYPES = ["STAGE77", "SUITCASE", "FELT", "DYNO"]
BIN = os.path.join(ROOT, "build", "host", "frodhes")


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-12))


def rms_db(x):
    x = np.asarray(x, float)
    return db(np.sqrt(np.mean(x * x)) / 32767.0)


def spectrum(x, f0, t0=0.03, t1=0.30):
    """Hann-windowed spectrum of [t0, t1]: centroid (Hz, power-weighted up to 12 kHz), H1..H5 power, O1 power."""
    seg = np.asarray(x[int(t0 * FS):int(t1 * FS)], float)
    w = np.hanning(len(seg))
    nfft = 1 << 18                                # zero-padded: the +-3 % bands hold bins even at 41 Hz
    S = np.abs(np.fft.rfft(seg * w, nfft)) ** 2
    fr = np.fft.rfftfreq(nfft, 1.0 / FS)
    m = (fr > 20) & (fr < 12000)
    cen = float(np.sum(fr[m] * S[m]) / max(np.sum(S[m]), 1e-30))

    def pw(f, rel=0.03):
        k = (fr >= f * (1 - rel)) & (fr <= f * (1 + rel))
        return float(np.sum(S[k])) if k.any() else 0.0
    H = [pw(k * f0) for k in range(1, 6)]
    return cen, H, S, fr


def key_specs(spec, keys):
    by = {k["midi"]: k for k in spec["keys"]}
    return [by[n] for n in keys]


def render(specs, fams, vels, dur, off_at=None):
    return gm.render_batch(specs, fams, vels, dur, off_at=off_at, gains=[s["gain"] for s in specs])


# ---------------------------------------------------------------- golden-model measurements
def measure_velocity(spec, keys, vels):
    ks = key_specs(spec, keys)
    fams = spec["pickup"]
    nk, nv = len(ks), len(vels)
    L = np.zeros((nk, nv)); PK = np.zeros((nk, nv)); CEN = np.zeros((nk, nv))
    OV = np.zeros((nk, nv)); CR = np.zeros((nk, nv))
    H21 = np.zeros((nk, nv)); H31 = np.zeros((nk, nv)); BARK = np.zeros((nk, nv)); BELL = np.zeros((nk, nv))
    for j, v in enumerate(vels):
        y = render(ks, fams, [v] * nk, 0.55)
        for i, (k, r) in enumerate(zip(ks, y)):
            f0 = k["f0"]
            L[i, j] = rms_db(r[int(0.05 * FS):int(0.3 * FS)])
            PK[i, j] = db(np.max(np.abs(r[:int(0.25 * FS)])) / 32767.0)
            CR[i, j] = PK[i, j] - rms_db(r[int(0.05 * FS):int(0.3 * FS)])     # crest: peak re the body (50..300 ms)
            # attack overshoot: the strike's peak (0..30 ms) over the settled waveform's peak (30..60 ms)
            OV[i, j] = db(np.max(np.abs(r[:int(0.03 * FS)])) / max(np.max(np.abs(r[int(0.03 * FS):int(0.06 * FS)])), 1e-9))
            cen, H, S, fr = spectrum(r, f0)
            CEN[i, j] = cen
            h1 = max(H[0], 1e-30)
            H21[i, j] = 10 * math.log10(max(H[1], 1e-30) / h1)
            H31[i, j] = 10 * math.log10(max(H[2], 1e-30) / h1)
            BARK[i, j] = 10 * math.log10(max(sum(H[1:5]), 1e-30) / h1)
            fo1 = k["modes"][2]["f"]
            _, Ha, S, fr = spectrum(r, f0, 0.003, 0.083)       # the bell is an attack partial: first 80 ms
            h1 = max(Ha[0], 1e-30)
            kk = (fr >= fo1 * 0.97) & (fr <= fo1 * 1.03)
            BELL[i, j] = 10 * math.log10(max(float(np.sum(S[kk])), 1e-30) / h1) if fo1 < 12000 else float("nan")
    return dict(L=L, PK=PK, OV=OV, CR=CR, CEN=CEN, H21=H21, H31=H31, BARK=BARK, BELL=BELL)


def measure_all_velocities(spec, keys, tol=0.1):
    """Level (RMS 50..300 ms) at every MIDI velocity 1..127: the C tables interpolate between anchors and switch the
    pulse shape between layers, so a monotonic curve at the anchors is not enough. Returns the levels and, per key,
    the largest drop (dB) from one velocity to the next."""
    ks = key_specs(spec, keys)
    vels = list(range(1, 128))
    specs = [k for k in ks for _ in vels]
    y = render(specs, spec["pickup"], vels * len(ks), 0.5)
    L = np.array([rms_db(r[int(0.05 * FS):]) for r in y]).reshape(len(ks), len(vels))
    drops = np.maximum(0.0, -np.diff(L, axis=1).min(axis=1))
    return L, drops


def measure_sustain(spec, keys, dur=3.0):
    """T60 (late, early) of the fundamental band and the tuning at v = 100."""
    ks = key_specs(spec, keys)
    y = render(ks, spec["pickup"], [100] * len(ks), dur)
    out = []
    p = spec["params"]
    for k, r in zip(ks, y):
        f0 = k["f0"]
        z = dsp.band(r / 32767.0, FS, f0, 0.08)
        e = dsp.env_db(z, FS, 0.02)
        t = np.arange(len(e)) / FS

        def slope(t0, t1):
            m = (t >= t0) & (t <= t1)
            return np.polyfit(t[m], e[m], 1)[0]
        late, early = slope(0.3, dur - 0.1), slope(0.02, 0.3)
        f = dsp.inst_f(z, FS, 0.5, min(2.5, dur - 0.2))
        # the whole output (all partials): the pickup's harmonics fade faster than the fundamental, a two-stage decay
        ew = 10 * np.log10(np.convolve((r / 32767.0) ** 2, np.ones(882) / 882, mode="same") + 1e-30)
        mw = lambda t0, t1: np.polyfit(t[(t >= t0) & (t <= t1)], ew[(t >= t0) & (t <= t1)], 1)[0]
        out.append(dict(t60=dsp.t60_from_slope(late), t60_early=dsp.t60_from_slope(early),
                        t60_out_early=dsp.t60_from_slope(mw(0.05, 0.5)), t60_out_late=dsp.t60_from_slope(mw(1.0, dur - 0.1)),
                        t60_target=t60_target(p, k["midi"]), cents=float(dsp.cents(f, f0))))
    return out


def measure_glide(spec, keys):
    """Attack pitch: inst_f over 20..100 ms minus 0.4..0.9 s, cents, at v = 127 and v = 40."""
    ks = key_specs(spec, keys)
    res = {}
    for v in (127, 40):
        y = render(ks, spec["pickup"], [v] * len(ks), 1.0)
        g = []
        for k, r in zip(ks, y):
            z = dsp.band(r / 32767.0, FS, k["f0"], 0.08)
            fa = dsp.inst_f(z, FS, 0.02, 0.10)
            fs_ = dsp.inst_f(z, FS, 0.4, 0.9)
            g.append(float(dsp.cents(fa, fs_)))
        res[v] = g
    return res


def measure_release(spec, keys):
    """Key-up at 0.5 s (v = 100): T60 of the output RMS envelope from +10 ms down 40 dB (or 0.8 s)."""
    ks = key_specs(spec, keys)
    off = 0.5
    y = render(ks, spec["pickup"], [100] * len(ks), 1.5, off_at=off)
    out = []
    for k, r in zip(ks, y):
        e = dsp.env_db(dsp.band(r / 32767.0, FS, k["f0"], 0.08), FS, 0.005)
        i0 = int((off + 0.012) * FS)
        e0 = e[i0]
        seg = e[i0:i0 + int(0.8 * FS)]
        below = np.nonzero(seg < e0 - 40)[0]
        i1 = below[0] if len(below) else len(seg)
        i1 = max(i1, int(0.02 * FS))
        tt = np.arange(i1) / FS
        sl = np.polyfit(tt, seg[:i1], 1)[0]
        out.append(dict(t60_rel=dsp.t60_from_slope(sl), target=release_target(spec["params"], k["midi"])))
    return out


def life_check(spec, keys):
    """The voice's life (life_blocks) against its decay: V1's tip peak at v = 127 (golden model) falls to the kill
    level after t_kill = T60 * dB(peak / kill) / 60; a life shorter than that ends the note early, and the level
    it is cut at (dB re its v = 127 peak, -60 life / T60) tells whether that can be heard."""
    out = []
    for k in key_specs(spec, keys):
        m = k["modes"][0]
        peak_gap = gm.v1_peak(k, 127)
        t_kill = m["t60"] * db(peak_gap / max(k["kill"], 1e-12)) / 60.0
        life = k["life_blocks"] * 256 / FS
        out.append(dict(t_kill=t_kill, life=life, cut_db=-60.0 * life / m["t60"]))
    return out


def t60_target(p, n):
    c = p["t60_v1"]
    pts = c["points"]
    if n <= pts[0][0]:
        return pts[0][1]
    if n >= pts[-1][0]:
        return max(pts[-1][1] * 2 ** (-(n - pts[-1][0]) / c["halve_keys"]), c["floor_s"])
    return float(np.exp(np.interp(n, [q[0] for q in pts], [math.log(q[1]) for q in pts])))


def release_target(p, n):
    r = p["release_t60"]
    for k in sorted(int(x) for x in r if x.isdigit()):
        if n <= k:
            return float(r[str(k)])
    return 0.2


# ---------------------------------------------------------------- host (C engine) measurements
def host_types(keys, vel=100):
    """Every key at vel, per TYPE, through the C engine with the effects bypassed: level (RMS 50..300 ms) and timbre."""
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)
    os.makedirs(os.path.join(ROOT, "build", "calib"), exist_ok=True)     # (a fresh checkout has no build/calib)
    res = {}
    gap = 1.2
    for ti, name in enumerate(TYPES):
        out = os.path.join(ROOT, "build", "calib", f"types-{name}-{vel}")
        lines = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0", f"40 midi 0xB0 80 {ti * 32}"]
        t0 = 200
        starts = []
        for i, n in enumerate(keys):
            t = t0 + int(i * gap * 1000)
            starts.append(t)
            lines += [f"{t} midi 0x90 {n} {vel}", f"{t + 700} midi 0x80 {n} 0"]
        lines.append(f"{t0 + int(len(keys) * gap * 1000) + 500} end")
        open(out + ".txt", "w").write("\n".join(lines) + "\n")
        subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
        w = wave.open(out + ".wav")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
        lv, cen, h21 = [], [], []
        for n, t in zip(keys, starts):
            i0 = int(t * FS / 1000)
            seg = x[i0:i0 + int(0.7 * FS)]
            st = int(np.argmax(np.abs(seg) > 0))
            seg = seg[st:]
            lv.append(rms_db(seg[int(0.05 * FS):int(0.3 * FS)]))
            f0 = 440 * 2 ** ((n - 69) / 12)
            c, H, _, _ = spectrum(seg, f0)
            cen.append(c)
            h21.append(10 * math.log10(max(H[1], 1e-30) / max(H[0], 1e-30)))
        res[name] = dict(L=lv, CEN=cen, H21=h21)
    return res


# ---------------------------------------------------------------- summaries
def smooth_dev(a, half=3):
    """Deviation of each entry from the median of its +-half neighbours (outliers against the local trend)."""
    a = np.asarray(a, float)
    out = np.zeros_like(a)
    for i in range(len(a)):
        lo, hi = max(0, i - half), min(len(a), i + half + 1)
        nb = np.concatenate([a[lo:i], a[i + 1:hi]])
        out[i] = a[i] - np.median(nb)
    return out


def summarise(keys, vels, M, sus, glide, rel, life, types):
    S = {}
    L = M["L"]
    j = {v: vels.index(v) for v in vels}
    jv = lambda v: j[v] if v in j else int(np.argmin([abs(v - q) for q in vels]))
    # evenness across keys at fixed velocities
    ev = {}
    for v in (40, 64, 100, 127):
        c = L[:, jv(v)]
        nb = np.abs(np.diff(c))
        ev[v] = dict(spread=float(c.max() - c.min()), std=float(c.std()), max_nb=float(nb.max()),
                     worst_nb=[int(keys[int(np.argmax(nb))]), int(keys[int(np.argmax(nb)) + 1])],
                     max_local=float(np.abs(smooth_dev(c)).max()), worst_local=int(keys[int(np.argmax(np.abs(smooth_dev(c))))]))
    S["evenness"] = ev
    # monotonicity and range
    drops = np.zeros(len(keys))
    for i in range(len(keys)):
        d = np.diff(L[i])
        drops[i] = max(0.0, -d.min())
    S["nonmono_keys"] = [int(keys[i]) for i in range(len(keys)) if drops[i] > 0.5]
    S["max_drop"] = dict(db=float(drops.max()), key=int(keys[int(np.argmax(drops))]))
    rng = L[:, jv(127)] - L[:, jv(1)]
    rng8 = L[:, jv(127)] - L[:, jv(8)]
    S["range_v1_v127"] = dict(min=float(rng.min()), med=float(np.median(rng)), max=float(rng.max()))
    S["range_v8_v127"] = dict(min=float(rng8.min()), med=float(np.median(rng8)), max=float(rng8.max()))
    rel_ = L - L[:, [jv(104)]]
    med = np.median(rel_, axis=0)
    dev = np.abs(rel_ - med[None, :]).max(axis=1)
    S["shape_dev"] = dict(max=float(dev.max()), med=float(np.median(dev)), worst=[int(keys[i]) for i in np.argsort(-dev)[:5]])
    S["median_curve"] = [float(x) for x in med]
    # timbre continuity (v = 100 and 127): neighbour jumps and local outliers
    tim = {}
    for v in (64, 100, 127):
        jj = jv(v)
        cen_h = np.log2(M["CEN"][:, jj] / np.array([440 * 2 ** ((n - 69) / 12) for n in keys]))
        row = {}
        for nm, arr in (("centroid_oct", cen_h), ("H2H1", M["H21"][:, jj]), ("H3H1", M["H31"][:, jj]),
                        ("bark", M["BARK"][:, jj])):
            sd = smooth_dev(arr)
            row[nm] = dict(max_nb=float(np.abs(np.diff(arr)).max()), max_local=float(np.abs(sd).max()),
                           worst=int(keys[int(np.argmax(np.abs(sd)))]))
        tim[v] = row
    S["timbre"] = tim
    # bark rising with velocity: B(127) - B(40); share of the rise above v = 84
    b40, b84, b127 = M["BARK"][:, jv(40)], M["BARK"][:, jv(84)], M["BARK"][:, jv(127)]
    rise = b127 - M["BARK"][:, jv(8)]
    above = (b127 - b84) / np.where(np.abs(rise) > 1e-6, rise, 1e-6)
    S["bark"] = dict(rise_8_127_med=float(np.median(rise)), rise_min=float(rise.min()),
                     share_above_84_med=float(np.median(above)),
                     nonmono_keys=[int(keys[i]) for i in range(len(keys)) if np.min(np.diff(M["BARK"][i, jv(40):])) < -1.5])
    # attack peaks at v = 127
    pk = M["PK"][:, jv(127)]
    pr = pk - L[:, jv(127)]
    S["peak127"] = dict(max=float(pk.max()), key=int(keys[int(np.argmax(pk))]), p90=float(np.percentile(pk, 90)),
                        peak_rms_med=float(np.median(pr)), peak_rms_max=float(pr.max()),
                        peak_rms_worst=[int(keys[i]) for i in np.argsort(-pr)[:5]],
                        peak_rms_spread=float(pr.max() - pr.min()),
                        overshoot_med=float(np.median(M["OV"][:, jv(127)])), overshoot_max=float(M["OV"][:, jv(127)].max()),
                        overshoot_worst=[int(keys[i]) for i in np.argsort(-M["OV"][:, jv(127)])[:5]],
                        overshoot_v100_max=float(M["OV"][:, jv(100)].max()),
                        crest100_max=float(M["CR"][:, jv(100)].max()), crest100_med=float(np.median(M["CR"][:, jv(100)])),
                        crest127_max=float(M["CR"][:, jv(127)].max()), crest_worst=[int(keys[i]) for i in np.argsort(-M["CR"][:, jv(100)])[:5]],
                        crest100_max_nb=float(np.abs(np.diff(M["CR"][:, jv(100)])).max()))
    S["h2h1_median_by_v"] = {int(v): float(np.median(M["H21"][:, jv(v)])) for v in (40, 64, 80, 96, 100, 112, 127)}
    S["bark_median_by_v"] = {int(v): float(np.median(M["BARK"][:, jv(v)])) for v in (40, 64, 80, 96, 100, 112, 127)}
    if sus:
        r = np.array([s["t60"] / s["t60_target"] for s in sus])
        c = np.array([s["cents"] for s in sus])
        e2 = np.array([s["t60_out_early"] / s["t60_out_late"] for s in sus])
        S["t60"] = dict(ratio_min=float(r.min()), ratio_med=float(np.median(r)), ratio_max=float(r.max()),
                        worst=int(keys[int(np.argmax(np.abs(np.log(r))))]),
                        two_stage_early_over_late_med=float(np.median(e2)), two_stage_min=float(e2.min()),
                        two_stage_max=float(e2.max()))
        S["tuning"] = dict(max_abs_cents=float(np.abs(c).max()), key=int(keys[int(np.argmax(np.abs(c)))]),
                           med_abs=float(np.median(np.abs(c))))
    if glide:
        g = np.array(glide[127]); g40 = np.array(glide[40])
        S["glide"] = dict(v127_max_abs=float(np.abs(g).max()), v127_key=int(keys[int(np.argmax(np.abs(g)))]),
                          v127_med=float(np.median(g)), v40_max_abs=float(np.abs(g40).max()))
    if rel:
        r = np.array([x["t60_rel"] / x["target"] for x in rel])
        S["release"] = dict(ratio_min=float(r.min()), ratio_med=float(np.median(r)), ratio_max=float(r.max()),
                            worst=int(keys[int(np.argmax(np.abs(np.log(r))))]))
    if life:
        bad = [int(n) for n, x in zip(keys, life) if x["life"] < x["t_kill"]]
        S["life"] = dict(cut_early_keys=bad, min_margin_s=float(min(x["life"] - x["t_kill"] for x in life)),
                         loudest_cut_db=float(max(x["cut_db"] if x["life"] < x["t_kill"] else -200.0 for x in life)))
    if types:
        ref = np.array(types["STAGE77"]["L"])
        S["types"] = {t: dict(level_re_stage_med=float(np.median(np.array(types[t]["L"]) - ref)),
                              level_spread=float(np.ptp(np.array(types[t]["L"]))),
                              max_nb=float(np.abs(np.diff(types[t]["L"])).max()),
                              centroid_med=float(np.median(types[t]["CEN"])),
                              H2H1_med=float(np.median(types[t]["H21"]))) for t in TYPES}
    return S


def fmt_table(keys, vels, M, sus, glide, rel):
    lines = []
    hdr = "key  " + " ".join(f"{v:>6d}" for v in vels) + " | drop  cen/f0 H2H1  H3H1  bark127 bell  pk127 pk/rms"
    if sus:
        hdr += " | T60   tgt  early cents glide127"
    if rel:
        hdr += " | rel  tgt"
    lines.append("level (dBFS, RMS 50..300 ms) per velocity; timbre at v = 100 (bark, peak at v = 127)")
    lines.append(hdr)
    j100 = vels.index(104) if 100 not in vels else vels.index(100)
    j127 = vels.index(127)
    for i, n in enumerate(keys):
        d = np.diff(M["L"][i])
        f0 = 440 * 2 ** ((n - 69) / 12)
        s = f"{n:3d}  " + " ".join(f"{x:6.1f}" for x in M["L"][i])
        s += f" | {max(0, -d.min()):4.1f}  {M['CEN'][i, j100] / f0:5.2f} {M['H21'][i, j100]:5.1f} {M['H31'][i, j100]:5.1f} " \
             f"{M['BARK'][i, j127]:6.1f} {M['BELL'][i, j100]:6.1f} {M['PK'][i, j127]:6.1f} {M['PK'][i, j127] - M['L'][i, j127]:5.1f}"
        if sus:
            x = sus[i]
            s += f" | {x['t60']:5.1f} {x['t60_target']:5.1f} {x['t60_early']:5.1f} {x['cents']:5.1f} {glide[127][i]:6.1f}"
        if rel:
            s += f" | {rel[i]['t60_rel']:4.2f} {rel[i]['target']:4.2f}"
        lines.append(s)
    return "\n".join(lines)


def fmt_summary(S):
    L = []
    for v, e in S["evenness"].items():
        L.append(f"evenness v{v}: spread {e['spread']:.1f} dB, std {e['std']:.2f}, max neighbour {e['max_nb']:.1f} dB "
                 f"{e['worst_nb']}, max local outlier {e['max_local']:.1f} dB (key {e['worst_local']})")
    L.append(f"monotonic: {len(S['nonmono_keys'])} keys with a drop > 0.5 dB {S['nonmono_keys']}; "
             f"largest drop {S['max_drop']['db']:.1f} dB (key {S['max_drop']['key']})")
    r, r8 = S["range_v1_v127"], S["range_v8_v127"]
    L.append(f"range v1..v127: {r['min']:.1f} / {r['med']:.1f} / {r['max']:.1f} dB (min/med/max); "
             f"v8..v127 {r8['min']:.1f} / {r8['med']:.1f} / {r8['max']:.1f}")
    L.append(f"curve shape: max deviation from the median curve {S['shape_dev']['max']:.1f} dB (median "
             f"{S['shape_dev']['med']:.1f}); worst {S['shape_dev']['worst']}")
    for v, row in S["timbre"].items():
        L.append(f"timbre v{v}: " + "; ".join(f"{k} nb {x['max_nb']:.2f} local {x['max_local']:.2f} (key {x['worst']})"
                                            for k, x in row.items()))
    b = S["bark"]
    L.append(f"bark: rise v8..127 median {b['rise_8_127_med']:.1f} dB (min {b['rise_min']:.1f}), share above v84 "
             f"{b['share_above_84_med']:.2f}, keys with bark falling > 1.5 dB above v40: {b['nonmono_keys']}")
    p = S["peak127"]
    L.append(f"peak v127: max {p['max']:.1f} dBFS (key {p['key']}), p90 {p['p90']:.1f}; peak/RMS median "
             f"{p['peak_rms_med']:.1f} dB, max {p['peak_rms_max']:.1f} {p['peak_rms_worst']}, spread {p['peak_rms_spread']:.1f}; "
             f"attack overshoot (peak 0..30 ms / 30..60 ms) median {p['overshoot_med']:.1f} dB, max {p['overshoot_max']:.1f} "
             f"{p['overshoot_worst']} (v100 max {p['overshoot_v100_max']:.1f})")
    L.append(f"crest (peak 0..250 ms - RMS 50..300 ms): v100 median {p['crest100_med']:.1f} max {p['crest100_max']:.1f} dB "
             f"{p['crest_worst']}, max neighbour step {p['crest100_max_nb']:.1f}; v127 max {p['crest127_max']:.1f}")
    L.append("H2/H1 median by velocity: " + ", ".join(f"v{v} {x:.1f}" for v, x in S["h2h1_median_by_v"].items()))
    L.append("bark (H2..H5 / H1) median by velocity: " + ", ".join(f"v{v} {x:.1f}" for v, x in S["bark_median_by_v"].items()))
    if "t60" in S:
        t = S["t60"]
        L.append(f"T60 / target: {t['ratio_min']:.2f} / {t['ratio_med']:.2f} / {t['ratio_max']:.2f} (worst key {t['worst']}); "
                 f"two-stage decay of the whole output, T60 early (0.05..0.5 s) / late (1..3 s): median "
                 f"{t['two_stage_early_over_late_med']:.2f} [{t['two_stage_min']:.2f}..{t['two_stage_max']:.2f}]")
        u = S["tuning"]
        L.append(f"tuning: max |cents| {u['max_abs_cents']:.2f} (key {u['key']}), median {u['med_abs']:.2f}")
    if "glide" in S:
        g = S["glide"]
        L.append(f"attack glide: v127 max |{g['v127_max_abs']:.1f}| c (key {g['v127_key']}), median {g['v127_med']:.1f}; "
                 f"v40 max |{g['v40_max_abs']:.1f}|")
    if "release" in S:
        r = S["release"]
        L.append(f"release T60 / target: {r['ratio_min']:.2f} / {r['ratio_med']:.2f} / {r['ratio_max']:.2f} (worst {r['worst']})")
    if "life" in S:
        L.append(f"life: keys cut before the kill level {S['life']['cut_early_keys']} (the loudest cut at "
                 f"{S['life']['loudest_cut_db']:.0f} dB re the v127 peak), min margin {S['life']['min_margin_s']:.1f} s")
    if "allvel" in S:
        a = S["allvel"]
        L.append(f"every velocity 1..127: {len(a['nonmono_keys'])} keys with a drop > 0.1 dB {a['nonmono_keys']}; largest "
                 f"drop {a['max_drop']:.2f} dB (key {a['max_drop_key']}); largest rise per velocity step "
                 f"{a['max_step']:.2f} dB (key {a['max_step_key']})")
    if "types" in S:
        for t, x in S["types"].items():
            L.append(f"type {t:9s}: level re STAGE77 {x['level_re_stage_med']:+.1f} dB (median), spread over keys "
                     f"{x['level_spread']:.1f} dB, max neighbour {x['max_nb']:.1f}, centroid {x['centroid_med']:.0f} Hz, "
                     f"H2/H1 {x['H2H1_med']:.1f} dB")
    return "\n".join(L)


def compare(S, B):
    """Before -> after for the headline numbers."""
    rows = []

    def add(name, a, b):
        rows.append(f"  {name:42s} {a:>9.2f} -> {b:>9.2f}")
    for v in ("40", "64", "100", "127"):
        add(f"spread over keys v{v} (dB)", B["evenness"][v]["spread"], S["evenness"][v]["spread"])
        add(f"max neighbour diff v{v} (dB)", B["evenness"][v]["max_nb"], S["evenness"][v]["max_nb"])
    add("non-monotonic keys", len(B["nonmono_keys"]), len(S["nonmono_keys"]))
    add("largest velocity drop (dB)", B["max_drop"]["db"], S["max_drop"]["db"])
    add("range v1..v127 median (dB)", B["range_v1_v127"]["med"], S["range_v1_v127"]["med"])
    add("curve shape max dev (dB)", B["shape_dev"]["max"], S["shape_dev"]["max"])
    for v in ("100", "127"):
        for k in ("centroid_oct", "H2H1", "H3H1"):
            add(f"timbre v{v} {k} local outlier", B["timbre"][v][k]["max_local"], S["timbre"][v][k]["max_local"])
    add("bark rise v8..127 median (dB)", B["bark"]["rise_8_127_med"], S["bark"]["rise_8_127_med"])
    add("peak v127 max (dBFS)", B["peak127"]["max"], S["peak127"]["max"])
    add("peak/RMS v127 max (dB)", B["peak127"]["peak_rms_max"], S["peak127"]["peak_rms_max"])
    add("peak/RMS v127 spread (dB)", B["peak127"]["peak_rms_spread"], S["peak127"]["peak_rms_spread"])
    if "overshoot_max" in B["peak127"]:
        add("attack overshoot v127 max (dB)", B["peak127"]["overshoot_max"], S["peak127"]["overshoot_max"])
        add("attack overshoot v127 median (dB)", B["peak127"]["overshoot_med"], S["peak127"]["overshoot_med"])
    if "t60" in S and "t60" in B:
        add("T60/target min", B["t60"]["ratio_min"], S["t60"]["ratio_min"])
        add("T60/target max", B["t60"]["ratio_max"], S["t60"]["ratio_max"])
        add("tuning max |cents|", B["tuning"]["max_abs_cents"], S["tuning"]["max_abs_cents"])
    if "glide" in S and "glide" in B:
        add("attack glide v127 max |cents|", B["glide"]["v127_max_abs"], S["glide"]["v127_max_abs"])
    if "types" in S and "types" in B:
        for t in TYPES:
            add(f"type {t} level re STAGE77 (dB)", B["types"][t]["level_re_stage_med"], S["types"][t]["level_re_stage_med"])
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=os.path.join(ROOT, "docs/model/data/rhodes_spec.json"))
    ap.add_argument("--keys", default="28-103")
    ap.add_argument("--name", default="latest")
    ap.add_argument("--host", action="store_true", help="also render the 4 TYPEs through the C engine")
    ap.add_argument("--quick", action="store_true", help="fewer velocities, no decay / tuning renders")
    ap.add_argument("--allvel", action="store_true", help="levels at every velocity 1..127 (monotonic check)")
    ap.add_argument("--compare", default=None, help="a previous run's name (build/calib/<name>.json)")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.keys.split("-"))
    keys = list(range(lo, hi + 1))
    spec = json.load(open(a.spec))
    vels = VELS_QUICK if a.quick else VELS
    M = measure_velocity(spec, keys, vels)
    sus = glide = rel = life = None
    if not a.quick:
        sus = measure_sustain(spec, keys)
        glide = measure_glide(spec, keys)
        rel = measure_release(spec, keys)
        life = life_check(spec, keys)
    types = host_types(keys) if a.host else None
    S = summarise(keys, vels, M, sus, glide, rel, life, types)
    if a.allvel:
        La, drops = measure_all_velocities(spec, keys)
        steps = np.diff(La, axis=1)
        S["allvel"] = dict(nonmono_keys=[int(n) for n, d in zip(keys, drops) if d > 0.1],
                           max_drop=float(drops.max()), max_drop_key=int(keys[int(np.argmax(drops))]),
                           max_step=float(steps.max()), max_step_key=int(keys[int(np.argmax(steps.max(axis=1)))]),
                           L=La.tolist())
    table = fmt_table(keys, vels, M, sus, glide, rel)
    summ = fmt_summary(S)
    os.makedirs(os.path.join(ROOT, "build", "calib"), exist_ok=True)
    base = os.path.join(ROOT, "build", "calib", a.name)
    data = dict(keys=keys, vels=vels, summary=S, **{k: v.tolist() for k, v in M.items()},
                sustain=sus, glide=glide, release=rel, life=life, types=types)
    json.dump(data, open(base + ".json", "w"), indent=1, default=float)
    txt = table + "\n\n" + summ + "\n"
    if a.compare:
        B = json.load(open(os.path.join(ROOT, "build", "calib", a.compare + ".json")))["summary"]
        S2 = json.loads(json.dumps(S, default=float))
        txt += f"\nbefore ({a.compare}) -> after ({a.name}):\n" + compare(S2, B) + "\n"
    open(base + ".txt", "w").write(txt)
    print(txt)
    print("->", base + ".txt", base + ".json")


if __name__ == "__main__":
    main()
