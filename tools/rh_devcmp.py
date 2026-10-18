#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Rhodes on the FM-1, before / after a change that must not move the sound: a fixed take set recorded over USB
and the headphone jack at once (tools/hil_jack.py Jack.both: the M6, input 5), measured the same way each time.

    tools/rh_devcmp.py record NAME [--dev M6] [--ch 5]      build/devcmp/NAME/*.npz + NAME.json
    tools/rh_devcmp.py compare A B [--md FILE]              per take and measure: A, B, B - A, against the limits

Takes: every TYPE (ST77, SUIT, FELT, DYNO) x keys 33 / 45 / 57 / 69 / 81 / 93 x v40 / v80 / v127 (each note its own
take from silence, 2.5 s: 1.5 s held), a pedalled chord, a fast 8-voice pattern (steals), the every-effect chord (tools/hil_cpu.py's heavy
panel), a held note to its tail. Measures per note (USB, jack): loudness (BS.1770 K-weighted, LUFS-style, ungated),
1/3-octave bands 31.5 Hz..16 kHz, partials 1-6 (pitch in cents, level re the 1st), the T60 of the decay (fit from
-5 to -35 dB under the peak), the onset (10 % -> 90 % rise of the 2 ms envelope to its peak in the first 100 ms, ms), clicks (hil_scan.clicks), and
the onset-aligned residual (B - A after cross-correlation alignment, dB re A). Limits (the user, 2026-10-18): level
0.1 dB, bands 0.3 dB, T60 1 %, pitch 0.5 cent, 0 clicks. Two runs of the same firmware give the floor: record the
base twice. Needs the FM-1 on USB, the M6 (sandbox off for CoreAudio). It changes the panel (Rig.dry) and ends on
every effect off.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_scan as hs   # noqa: E402

FS = 48000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "devcmp")
TYPES = ["ST77", "SUIT", "FELT", "DYNO"]
KEYS = [33, 45, 57, 69, 81, 93]
VELS = [40, 80, 127]
NOTE_S = 2.5
HEAVY = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
             CP_SW=64, CP_DEPTH=127, DL_SW=127, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127,
             FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)
LIM = dict(level=0.1, band=0.3, t60=1.0, cents=0.5)


def takes():
    """name -> (panel kwargs for Rig.dry, duration s, events, [(segment name, t0, t1, midi note or None)])"""
    T = {}
    for ti, tn in enumerate(TYPES):                       # one take per note, each from silence: a note struck
        for k in KEYS:                                    # while the last one still rings is a re-strike (rh_strike
            for v in VELS:                                # superposes), and its sound depends on the phase it meets
                T[f"{tn}_{k}_v{v}"] = (dict(TYPE=ti * 32) if (k, v) == (KEYS[0], VELS[0]) else None, NOTE_S + 0.4,
                                       [(0.2, "on", k, v), (1.7, "off", k, 0)], [(f"{tn}_{k}_v{v}", 0.2, 0.2 + NOTE_S, k, 1.5)])
    ch = [48, 52, 55, 59, 62]
    ev = [(0.2, "cc", 64, 127)] + [(0.3, "on", n, 90) for n in ch] + [(1.3, "off", n, 0) for n in ch] + [(4.3, "cc", 64, 0)]
    T["pedal_chord"] = (dict(), 6.5, ev, [("pedal_chord", 0.3, 6.3, None, 4.0)])
    ev, t = [], 0.3
    for i in range(30):                                   # 30 notes, 100 ms apart, each held 0.5 s: steals
        n = [36, 43, 48, 52, 55, 60, 64, 67, 72, 76][i % 10]
        ev += [(t, "on", n, 100), (t + 0.5, "off", n, 0)]
        t += 0.1
    T["fast8"] = (dict(), t + 3.0, ev, [("fast8", 0.3, t + 2.5, None, 0)])
    ch8 = [48, 52, 55, 59, 62, 64, 67, 71]
    ev = [(0.3, "on", n, 100) for n in ch8] + [(2.3, "off", n, 0) for n in ch8]
    T["fx_chord"] = (dict(HEAVY), 7.0, ev, [("fx_chord", 0.3, 6.8, None, 2.0)])
    T["held45"] = (dict(), 11.0, [(0.3, "on", 45, 100), (9.3, "off", 45, 0)], [("held45", 0.3, 10.8, 45, 9.0)])
    only = os.environ.get("DEVCMP_ONLY")              # a pilot: the takes whose name starts with it (the first keeps its panel)
    return {k: v for k, v in T.items() if not only or k.startswith(only) or k.startswith("ST77_33_v40")}


def both44(J, dur, events):
    """hil_jack Jack.both with the FM-1's USB stream at its own 44.1 kHz (the device switched, no resampler between:
    the firmware's samples as they are) and the jack at 48 kHz"""
    sd, r = J.sd, J.r
    nu, nj = int(dur * 44100), int(dur * FS)
    u = np.zeros(nu, np.int16)
    j = np.zeros(nj)
    ca = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True)
    s1 = sd.InputStream(device=r.dev, samplerate=44100, channels=2, dtype="int16", extra_settings=ca)
    s2 = sd.InputStream(device=J.jdev, samplerate=FS, channels=J.jn, dtype="float32")
    s1.start()
    s2.start()
    t0, k, g1, g2 = time.monotonic(), 0, 0, 0
    evs = sorted(events, key=lambda e: e[0])
    while g1 < nu or g2 < nj:
        while k < len(evs) and time.monotonic() - t0 >= evs[k][0]:
            t, kind, a, b = evs[k]
            M = r.mido.Message
            r.out.send(M("note_on", note=a, velocity=b) if kind == "on" else
                       M("note_off", note=a) if kind == "off" else M("control_change", control=a, value=b))
            k += 1
        if g1 < nu:
            d, _ = s1.read(min(512, nu - g1))
            u[g1:g1 + len(d)] = d[:, 0]
            g1 += len(d)
        if g2 < nj:
            d, _ = s2.read(min(512, nj - g2))
            j[g2:g2 + len(d)] = d[:, J.jch]
            g2 += len(d)
    s1.stop()
    s2.stop()
    return u, j


def record(name, dev, ch):
    import hil_jack as hj
    J = hj.Jack(dev, ch)
    r = J.r
    os.makedirs(os.path.join(OUT, name), exist_ok=True)
    r.cc_raw = lambda c, v: r.out.send(r.mido.Message("control_change", control=c, value=v))
    typ = 0
    for tk, (panel, dur, ev, seg) in takes().items():
        if panel is not None:
            p = dict(panel)
            typ = p.pop("TYPE", 0)
            r.dry(**p)
        r.cc_raw(47, 0)
        r.cc_raw(79, 64)                                  # VIBES and back: every Rhodes voice ends (eng_switch kills
        time.sleep(0.2)                                   # them, the RLC / body states zeroed). The gate's silence
        r.cc_raw(80, typ)                                 # alone leaves tails ringing under -66 dBFS that come back
        time.sleep(0.3)                                   # with the next note (+-0.4 dB run to run)
        r.wait_silence()
        r.cc_raw(114, 127)                                # USB VOL FIXED: USB = the app's output before MASTER
        u44, j = both44(J, dur, ev)
        from scipy.signal import resample_poly
        u = resample_poly(u44.astype(float) / 32768, 160, 147)          # the measures run at 48 kHz on both
        np.savez_compressed(os.path.join(OUT, name, tk + ".npz"), usb=u.astype(np.float32), jack=j.astype(np.float32),
                            usb44=u44)
        print(f"{name} {tk}: USB peak {20 * np.log10(np.abs(u).max() + 1e-9):.1f} dBFS, jack "
              f"{20 * np.log10(np.abs(j).max() + 1e-9):.1f} dBFS", flush=True)
    r.cc_raw(114, 0)
    r.dry()


def exact(A, B, tk, t0, t1, hold):
    """USB at 44.1 kHz over the held part, run B aligned on run A to the sample (cross-correlation of the attack):
    the samples that differ and the largest difference (LSB); 0 = the firmware's output identical to the sample"""
    a = np.load(os.path.join(OUT, A, tk + ".npz"))["usb44"].astype(int)
    b = np.load(os.path.join(OUT, B, tk + ".npz"))["usb44"].astype(int)
    i0 = int(t0 * 44100) - 2205
    a, b = a[i0:int(t1 * 44100)], b[i0:int(t1 * 44100)]

    def on(x):
        h = np.abs(x[:int(0.35 * 44100)])
        return int(np.argmax(h > 0.1 * max(h.max(), 1)))
    oa, ob = on(a), on(b)
    w = 2205
    best = max(range(-64, 65), key=lambda k: np.dot(a[oa - 100:oa - 100 + w].astype(float),
                                                    b[ob - 100 + k:ob - 100 + k + w].astype(float)))
    sa, sb = oa - 441, ob - 441 + best                    # from 10 ms before the attack
    n = min((int((hold - 0.1) * 44100) if hold else len(a)) + 441, len(a) - sa, len(b) - sb)   # (the 10 ms lead-in inside the clamp)
    d = b[sb:sb + n] - a[sa:sa + n]
    res = 20 * np.log10(np.sqrt(np.mean(d.astype(float) ** 2)) / (np.sqrt(np.mean(a[sa:sa + n].astype(float) ** 2)) + 1e-9) + 1e-12)
    return int(np.count_nonzero(d)), int(np.abs(d).max()), n, float(res)


# ---------------------------------------------------------------- measures
def kweight(x):
    from scipy.signal import lfilter
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(b2, a2, lfilter(b1, a1, x))


def loud(x):
    return float(-0.691 + 10 * np.log10(np.mean(kweight(x) ** 2) + 1e-20))


def bands(x):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / FS)
    out = []
    for i in range(-17, 13):                              # 31.5 Hz .. 16 kHz (1 kHz = 0)
        fc = 1000 * 2 ** (i / 3)
        m = (fr >= fc * 2 ** -(1 / 6)) & (fr < fc * 2 ** (1 / 6))
        out.append(float(10 * np.log10(sp[m].sum() + 1e-20)))
    return out


def env2ms(x):
    n = int(0.002 * FS)
    m = len(x) // n
    return np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1) + 1e-20)


def onset(x):
    """the note-on: the first sample above 10 % of the peak of the first 0.35 s (x starts 50 ms before the event;
    an earlier note's damped tail stays below that)"""
    h = np.abs(x[:int(0.35 * FS)])
    return int(np.argmax(h > 0.1 * max(h.max(), 1e-9)))


def measure(x, note, hold, ons=(0.0,)):
    """x: the segment from 50 ms before its first note-on; ons: its note-ons (s re the first). Measured from the
    detected onset over a fixed length (the segment minus 0.15 s: the next note's jittered start stays out)"""
    on = onset(x)
    y = x[on:on + (int((hold - 0.1) * FS) if hold else len(x) - int(0.2 * FS))]   # the held part: the note-off
                                                          # comes with its own MIDI / block jitter (+-6 ms)
    g = [on + int(t * FS) for t in ons]                   # the notes' own attacks are not clicks (bar_scan S1)
    e = env2ms(y)
    ed = 20 * np.log10(e)
    pk = int(np.argmax(e[:50]))                           # the attack: the peak of the first 100 ms
    r10 = int(np.argmax(e > 0.1 * e[pk])); r90 = int(np.argmax(e > 0.9 * e[pk]))
    d = {"lufs": loud(y), "bands": bands(y), "onset_ms": 2.0 * (r90 - r10), "clicks": hs.clicks_guarded(x * 32768, g, 10, 30)}
    if hold:                                              # the decay while held (the key or the pedal): a line
        a, b = int(0.1 / 0.002), min(len(ed), int((hold - 0.1) / 0.002))   # fit of the 2 ms envelope in dB
        k = np.polyfit(np.arange(a, b) * 0.002, ed[a:b], 1)[0]
        d["t60"] = float(-60 / k) if k < 0 else float("inf")
    if note:
        f0 = 440 * 2 ** ((note - 69) / 12)
        s = y[int(0.05 * FS):int(1.05 * FS)]
        s = s * np.hanning(len(s))
        sp = np.abs(np.fft.rfft(s, 8 * len(s)))
        df = FS / (8 * len(s))
        P = []
        for h in range(1, 7):
            if h * f0 > 18000:
                break
            c = int(round(h * f0 / df)); w = max(3, int(h * f0 * 0.03 / df))
            i = c - w + int(np.argmax(sp[c - w:c + w]))
            al, be, ga = np.log(sp[i - 1:i + 2] + 1e-20)
            p = 0.5 * (al - ga) / (al - 2 * be + ga)
            P.append((float(1200 * np.log2((i + p) * df / (h * f0))), float(20 * np.log10(sp[i] + 1e-20))))
        d["cents"] = [c for c, lv in P if lv - P[0][1] > -50]    # partials within 50 dB of the 1st
        d["plev"] = [lv - P[0][1] for _, lv in P]
    return d, on


def analyse(name):
    res = {}
    for tk, (panel, dur, ev, seg) in takes().items():
        z = np.load(os.path.join(OUT, name, tk + ".npz"))
        for src in ("usb", "jack"):
            x = z[src].astype(float)
            ons = sorted({e[0] for e in ev if e[1] == "on"})
            for sn, t0, t1, note, hold in seg:
                part = x[int(t0 * FS) - int(0.05 * FS):int(t1 * FS)]
                res[f"{sn}/{src}"] = measure(part, note, hold, [t - t0 for t in ons if t0 <= t < t1])[0]
    return res


def residual(A, B, tk, sn, t0, t1, src):
    a = np.load(os.path.join(OUT, A, tk + ".npz"))[src].astype(float)
    b = np.load(os.path.join(OUT, B, tk + ".npz"))[src].astype(float)
    i0, i1 = int(t0 * FS) - int(0.05 * FS), int(t1 * FS)
    a, b = a[i0:i1], b[i0:i1]
    oa, ob = onset(a), onset(b)
    oa, ob = max(oa - 48, 0), max(ob - 48, 0)             # 1 ms before the onsets, then aligned by correlation
    n = min(len(a) - oa, len(b) - ob) - int(0.2 * FS)
    w = int(0.05 * FS)
    best = None
    for s in range(-24, 25):                              # the onset to the sample (the block grid is not aligned)
        if ob + s < 0:
            continue
        r = np.dot(a[oa:oa + w], b[ob + s:ob + s + w])
        if best is None or r > best[0]:
            best = (r, s)
    s = best[1]
    aa, bb = a[oa:oa + n], b[ob + s:ob + s + n]
    return float(20 * np.log10(np.sqrt(np.mean((bb - aa) ** 2)) / (np.sqrt(np.mean(aa ** 2)) + 1e-20) + 1e-20))


def compare(A, B, md=None):
    ra, rb = analyse(A), analyse(B)
    rows, fails = [], 0
    segs = [(tk, s) for tk, (_, _, _, seg) in takes().items() for s in seg]
    hdr = "| take | src | LUFS A | LUFS B | d | max band d | T60 A | T60 B | d % | pitch max d (c) | partial lvl max d | onset A / B ms | clicks A / B | residual dB (USB: 44.1 kHz aligned) | USB 44.1k samples differing (max LSB) |"
    rows.append(hdr)
    rows.append("|" + "---|" * 15)
    nex = 0
    for tk, (sn, t0, t1, note, hold) in segs:
        for src in ("usb", "jack"):
            a, b = ra[f"{sn}/{src}"], rb[f"{sn}/{src}"]
            dl = b["lufs"] - a["lufs"]
            top = max(a["bands"])                         # bands within 40 dB of the loudest (the jack's hiss)
            db_ = max(abs(p - q) for p, q in zip(a["bands"], b["bands"]) if p > top - 40)
            dt = 100 * (b["t60"] / a["t60"] - 1) if "t60" in a and "t60" in b else float("nan")
            dc = max([abs(p - q) for p, q in zip(a.get("cents", []), b.get("cents", []))] or [0])
            dp = max([abs(p - q) for p, q in zip(a.get("plev", [0])[1:], b.get("plev", [0])[1:]) if p > -50] or [0])
            res = residual(A, B, tk, sn, t0, t1, src)
            ex = ""
            if src == "usb":
                try:
                    nd, mx, n, res = exact(A, B, tk, t0, t1, hold)   # USB: the residual from the 44.1 kHz samples
                    ex = f"{nd} of {n} ({mx})"
                    nex += nd == 0
                except KeyError:
                    pass
            bad = [k for k, v in (("level", abs(dl)), ("band", db_), ("t60", abs(dt)), ("cents", dc)) if v > LIM[k]]
            bad += ["clicks"] if a["clicks"] or b["clicks"] else []
            fails += bool(bad)
            rows.append(f"| {sn} | {src} | {a['lufs']:.2f} | {b['lufs']:.2f} | {dl:+.2f} | {db_:.2f} | "
                        f"{a.get('t60', float('nan')):.2f} | {b.get('t60', float('nan')):.2f} | {dt:+.2f} | {dc:.2f} | "
                        f"{dp:.2f} | {a['onset_ms']:.0f} / {b['onset_ms']:.0f} | {a['clicks']} / {b['clicks']} | "
                        f"{res:.1f} | {ex} |" + (f" **{', '.join(bad)}**" if bad else ""))
    txt = "\n".join(rows) + (f"\n\n{fails} of {2 * len(segs)} rows beyond the limits {LIM} (clicks 0); "
                              f"{nex} of {len(segs)} takes sample-identical on USB (44.1 kHz, onset-aligned)\n")
    print(txt)
    if md:
        open(md, "w").write(f"# Rhodes device comparison: {A} -> {B}\n\n" + txt)
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["record", "compare"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--md")
    a = ap.parse_args()
    if a.cmd == "record":
        record(a.names[0], a.dev, a.ch)
    else:
        compare(a.names[0], a.names[1], a.md)


if __name__ == "__main__":
    main()
