#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""WHURLI on the FM-1: the jack and USB recorded together (hil_jack.Jack) while MIDI plays F20..F23 and a velocity sweep over
keys 33..93; each note's level against the host build's render of the same MIDI (within 1 dB), the click check (the
bark-aware detector: tests/py/test_whurli.py excess), the floor with WHURLI selected against STAGE 77's (no hum, no rise).

  tools/hil_whurli_jack.py [--out build/whurli-fm1] [--dev M6] [--ch 5]

WHURLI has no CC: program change 35..38 = F20..F23 (presets.c preset_of_pc: 32.. = F17..). WAVs (48 kHz mono, USB, jack, the host's) go to --out for listening."""
import argparse
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tests", "py"))
import hil_scan as hs  # noqa: E402
import glitch_lpc as G  # noqa: E402

FS = 48000
KEYS = (33, 39, 45, 51, 57, 63, 69, 75, 81, 87, 93)
VELS = (30, 64, 100, 127)
STEP, HOLD, T0 = 0.8, 0.3, 1.5
EXCESS_MAX = 3.0
JACK_RE_USB_DB = -12.7   # hil_jack.py's own figure (build/hil/jack.json 2026-11-08: -12.7, jack-vibes.json 2026-10-20: -12.6): a loud
                         # note's jack level re its USB level at MASTER up, the unit's volume at 100 and the interface input as the rig has it


def save(path, x, sr=FS):
    f = wave.open(path, "wb")
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
    f.writeframes(np.clip(np.asarray(x) * 32767, -32768, 32767).astype("<i2").tobytes())
    f.close()


def excess(x, n, f0, fs):
    """tests/py/test_whurli.py excess, at any rate: a spike's size over the largest 2nd difference of the cycle before / after"""
    d = np.abs(np.diff(np.asarray(x, float), 2))
    P = fs / f0
    s0 = d[max(n - 3, 0):n + 4].max()

    def cyc(c):
        a, b = int(c - P / 2), int(c + P / 2)
        return d[a:b].max() if a >= 0 and b < len(d) else 0.0
    return s0 / max(cyc(n - P), cyc(n + P), 1e-9)


def notes_of_sweep():
    out = []
    t = T0
    for v in VELS:
        for k in KEYS:
            out.append((t, k, v)); t += STEP
    return out


def events_of(notes, pc, raw=True):
    """program change pc (it loads 400 ms later), then RAW (CC 119: the engine without the effects: their LFOs run free on the
    device and from zero on the host, so only the engine's level can be matched), then the notes"""
    ev = ([(0.0, "pc", pc, 0)] if pc is not None else []) + ([(1.0, "cc", 119, 127)] if raw else [])
    for t, k, v in notes:
        ev += [(t, "on", k, v), (t + HOLD, "off", k, 0)]
    return ev


def host_render(tag, ev, dur):
    import hostrun as H
    ls = [f"{int(t * 1000)} midi 0xC0 {a}" if kind == "pc" else f"{int(t * 1000)} midi 0x90 {a} {b}" if kind == "on" else
          f"{int(t * 1000)} midi 0xB0 {a} {b}" if kind == "cc" else f"{int(t * 1000)} midi 0x80 {a} 0" for t, kind, a, b in ev]
    r = H.run(tag, ls, int(dur * 1000))
    return hs.to_fs(r.L / 32768.0, 44100, dur)


def level(x, t, lo=0.06, hi=0.30):
    s = np.asarray(x[int((t + lo) * FS):int((t + hi) * FS)], float)
    return 20 * np.log10(np.sqrt(np.mean(s * s)) + 1e-12)


NEAR_FLOOR_SNR_DB = 20.0     # a note under this far over the jack's floor (on USB's scale) is reported "near floor", not gated


def _env(x, a, n, fs=FS):
    s = np.asarray(x[a:a + n], float)
    m = max(int(0.002 * fs), 1)
    return np.sqrt(np.convolve(s * s, np.ones(m) / m, "same"))


def align_lag(dev, host, t, fs=FS, span=0.04, pre=0.02, post=0.25):
    """the device's delay at the note struck at t: the lag (s, 0..span, 1 ms steps) at which its envelope best fits the host's. The
    device audio lags the MIDI event by its own latency (11-12 ms on the FM-1, other per build and block size): a window anchored at
    the event time sees an earlier part of the note, louder by the decay x the lag (key 93: 0.07 dB/ms)"""
    a, n = int((t - pre) * fs), int((pre + post) * fs)
    eh = _env(host, a, n, fs)
    best, step = (-1.0, 0), max(int(fs / 1000), 1)
    for lag in range(0, int(span * fs) + 1, step):
        ed = _env(dev, a + lag, n, fs)
        c = float(np.dot(ed, eh) / (np.linalg.norm(ed) * np.linalg.norm(eh) + 1e-30))
        if c > best[0]:
            best = (c, lag)
    return best[1] / fs


def score_sweep(u, j, h, notes, floor_jack_db, fs=FS):
    """USB against the host per note (each note's own delay found, the take's median where a note is too quiet to align), the jack
    against USB; notes within NEAR_FLOOR_SNR_DB of the jack's floor (its level on USB's scale) are listed with their SNR, not gated"""
    lags = [align_lag(u, h, t, fs) for t, k, v in notes]
    loud = [lg for lg, (t, k, v) in zip(lags, notes) if level(h, t) > -60]
    med = float(np.median(loud)) if loud else 0.0
    jo = [level(j, t + lg) - level(u, t + lg) for lg, (t, k, v) in zip(lags, notes)]
    jm = float(np.median(jo))
    floor_usb = floor_jack_db - jm
    rows = []
    for (t, k, v), lg in zip(notes, lags):
        lu = level(u, t)
        snr = level(u, t) - floor_usb
        lg = lg if snr >= NEAR_FLOOR_SNR_DB else med
        rows.append(dict(key=k, vel=v, lag_ms=lg * 1000, d_db=level(u, t + lg) - level(h, t),
                         jack_re_usb=level(j, t + lg) - level(u, t + lg), snr_db=snr, near_floor=snr < NEAR_FLOOR_SNR_DB))
    ok_rows = [r for r in rows if not r["near_floor"]]
    return dict(rows=rows, max_abs=max(abs(r["d_db"]) for r in ok_rows), mean=float(np.mean([r["d_db"] for r in ok_rows])),
                jack_median=jm, jack_spread=max(r["jack_re_usb"] for r in ok_rows) - min(r["jack_re_usb"] for r in ok_rows),
                near_floor=[(r["key"], r["vel"], round(r["d_db"], 2), round(r["snr_db"], 1)) for r in rows if r["near_floor"]],
                median_lag_ms=med * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "build", "whurli-fm1"))
    ap.add_argument("--dev", default="M6")
    ap.add_argument("--ch", type=int, default=5)
    ap.add_argument("--rescore", metavar="DIR", help="no device: score the sweep WAVs saved in DIR again (sweep-usb / -jack / -host)")
    ap.add_argument("--ref-db", type=float, default=JACK_RE_USB_DB, help="the preflight's expected jack level re USB (dB)")
    ap.add_argument("--ref-tol", type=float, default=1.5)
    ap.add_argument("--reference", action="store_true", help="the same sweep on STAGE 77 (no program change; keys 36..93): the rig's own "
                    "device-against-host spread, to read WHURLI's against")
    a = ap.parse_args()
    if a.rescore:
        def rd(n):
            w = wave.open(os.path.join(a.rescore, n))
            return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32768
        rj = json.load(open(os.path.join(a.rescore, "jack-report.json")))
        sc = score_sweep(rd("sweep-usb.wav"), rd("sweep-jack.wav"), rd("sweep-host.wav"), notes_of_sweep(),
                         rj["floor"]["whurli_jack"]["total"])
        for key in KEYS:
            ds = [r["d_db"] for r in sc["rows"] if r["key"] == key and not r["near_floor"]]
            print("key %2d  USB vs host: %s dB" % (key, " ".join("%+.2f" % x for x in ds)))
        print("max |d| %.2f dB (gated notes), mean %+.2f, median delay %.1f ms; jack re USB median %+.2f spread %.2f dB" % (
            sc["max_abs"], sc["mean"], sc["median_lag_ms"], sc["jack_median"], sc["jack_spread"]))
        print("near floor (key, vel, d dB, SNR dB), reported not gated:", sc["near_floor"])
        return 0
    os.makedirs(a.out, exist_ok=True)
    import hil_jack as hj
    J = hj.Jack(a.dev, a.ch)
    r = J.r
    rep, ok = {}, True
    # preflight (2026-11-22: the unit's volume had been turned down and nothing said so; hil_jack only REPORTS jack_re_usb_db,
    # hil_scan's preflight reads the panel acoustically on USB, which the volume does not touch): STAGE 77 C4 v100 RAW, the jack's
    # level re the USB's must be the rig's own figure, else the run stops before it records anything
    r.dry()
    up, jp = J.both(2.0, [(0.0, "cc", 119, 127), (0.3, "on", 60, 100), (1.3, "off", 60, 0)])
    pre = level(jp, 0.3, 0.05, 0.9) - level(up, 0.3, 0.05, 0.9)
    print("preflight: jack re USB %+.2f dB (expected %+.1f +- %.1f)" % (pre, a.ref_db, a.ref_tol))
    if abs(pre - a.ref_db) > a.ref_tol:
        print("PREFLIGHT FAIL: the jack is %+.1f dB from its usual level re USB: set the FM-1's volume to 100 (and check the "
              "interface's input gain), then run again" % (pre - a.ref_db))
        return 3
    rep["preflight_jack_re_usb_db"] = pre
    # floors: STAGE 77, then WHURLI (F20), nothing played, 4 s each
    r.dry()
    u0, j0 = J.both(4.0, [])
    r.dry()
    u1, j1 = J.both(4.0, [(0.0, "pc", 35, 0)])
    for nm, x in (("floor-st77-jack", j0), ("floor-whurli-jack", j1), ("floor-st77-usb", u0), ("floor-whurli-usb", u1)):
        save(os.path.join(a.out, nm + ".wav"), x)
    s = slice(int(1.0 * FS), None)
    f = lambda x, lo, hi: float(hj.band_db(np.asarray(x[s], float), lo, hi))
    rep["floor"] = {n: dict(total=float(hj.db(np.asarray(x[s], float))), hum_20_200=f(x, 20, 200), hf_8k_20k=f(x, 8000, 20000))
                    for n, x in (("st77_jack", j0), ("whurli_jack", j1), ("st77_usb", u0), ("whurli_usb", u1))}
    d_tot = rep["floor"]["whurli_jack"]["total"] - rep["floor"]["st77_jack"]["total"]
    d_hum = rep["floor"]["whurli_jack"]["hum_20_200"] - rep["floor"]["st77_jack"]["hum_20_200"]
    rep["floor_rise_db"] = dict(total=d_tot, hum=d_hum)
    ok &= d_tot < 1.0 and d_hum < 1.0
    # the sweep
    notes = notes_of_sweep()
    dur = T0 + STEP * len(notes) + 1.5
    if a.reference:
        notes = [(t, max(k, 36), v) for t, k, v in notes]
    r.dry()
    u, j = J.both(dur, events_of(notes, None if a.reference else 35))
    save(os.path.join(a.out, "sweep-usb.wav"), u)
    save(os.path.join(a.out, "sweep-jack.wav"), j / max(np.abs(j).max(), 1e-9) * min(1.0, np.abs(j).max()))
    h = host_render("whurli-sweep-host", events_of(notes, None if a.reference else 35), dur)
    save(os.path.join(a.out, "sweep-host.wav"), h)
    # the level: USB against the host per note; the jack against USB as one offset
    sc = score_sweep(u, j, h, notes, rep["floor"]["whurli_jack"]["total"])
    rep["sweep_usb_vs_host_db"] = dict(max_abs=sc["max_abs"], mean=sc["mean"], near_floor=sc["near_floor"],
                                      median_lag_ms=sc["median_lag_ms"],
                                      per_note=[(r["key"], r["vel"], round(r["d_db"], 2)) for r in sc["rows"]])
    rep["jack_minus_usb_db"] = dict(median=sc["jack_median"], spread=sc["jack_spread"])
    ok &= sc["max_abs"] <= 1.0
    ok &= sc["jack_spread"] <= 1.0
    # the clicks, USB and jack, the bark-aware detector (every LPC event judged by its excess over the periodic waveform)
    for nm, x in (("usb", u), ("jack", j)):
        x = np.asarray(x, float)
        ev = G.events(x, FS)
        bad = []
        for e in ev:
            i = int(np.clip((e / FS - T0) // STEP, 0, len(notes) - 1))
            t, k, v = notes[i]
            if e / FS - t < 0.04:           # inside a note-on's guard (scan: 40 ms)
                continue
            if excess(x, e, 440 * 2 ** ((k - 69) / 12), FS) >= EXCESS_MAX:
                bad.append((round(e / FS, 3), k, v))
        rep["clicks_" + nm] = dict(lpc_events=len(ev), clicks=bad)
        ok &= not bad
    if a.reference:
        print("reference sweep: max |d| %.2f dB, mean %+.2f" % (rep["sweep_usb_vs_host_db"]["max_abs"], rep["sweep_usb_vs_host_db"]["mean"]))
        print("reference jack re USB: median %+.2f dB, spread %.2f dB" % (rep["jack_minus_usb_db"]["median"], rep["jack_minus_usb_db"]["spread"]))
        lv = sorted(((level(u, t), level(j, t) - level(u, t), k, v) for t, k, v in notes))[:3]
        print("quietest notes (USB dBFS, jack-USB, key, vel):", [(round(a, 1), round(b, 1), k, v) for a, b, k, v in lv])
        return 0
    # F20..F23: a chord and single notes each
    for pc in (35, 36, 37, 38):
        nts = [(1.2, 48, 90), (2.4, 60, 90), (3.6, 72, 90), (4.8, 36, 110)]
        ev = events_of(nts, pc)
        ev += [(1.2, "on", 55, 90), (1.2 + HOLD, "off", 55, 0)]
        r.dry()
        uu, jj = J.both(6.5, ev)
        r.dry()                                    # (and with the preset's own effects, for listening)
        ue, je = J.both(6.5, events_of(nts, pc, raw=False))
        save(os.path.join(a.out, f"F{pc - 15}-fx-usb.wav"), ue)
        save(os.path.join(a.out, f"F{pc - 15}-fx-jack.wav"), je)
        save(os.path.join(a.out, f"F{pc - 15}-usb.wav"), uu)
        save(os.path.join(a.out, f"F{pc - 15}-jack.wav"), jj)
        hh = host_render(f"whurli-f{pc - 15}-host", ev, 6.5)
        save(os.path.join(a.out, f"F{pc - 15}-host.wav"), hh)
        rep[f"F{pc - 15}"] = dict(usb_vs_host_db=[round(level(uu, t + align_lag(uu, hh, t), 0.06, 0.5) - level(hh, t, 0.06, 0.5), 2)
                                                  for t, k, v in nts])
        ok &= max(abs(x) for x in rep[f"F{pc - 15}"]["usb_vs_host_db"]) <= 1.0
    if a.reference:
        print(json.dumps(rep["sweep_usb_vs_host_db"], indent=0)[:3000])
        return 0
    rep["ok"] = bool(ok)
    json.dump(rep, open(os.path.join(a.out, "jack-report.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "sweep_usb_vs_host_db"}, indent=1))
    print("sweep usb vs host: max |d| %.2f dB, mean %+.2f" % (rep["sweep_usb_vs_host_db"]["max_abs"], rep["sweep_usb_vs_host_db"]["mean"]))
    print("hil_whurli_jack:", "ok" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
