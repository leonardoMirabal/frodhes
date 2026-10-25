#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""release_level, the "no louder after the key-up" must-pass on the FM-1 (private/TEST-PLAN.md, its last section):
releasing a held key must never make the sound louder (0.9.5's test: a soft note played 9-12 dB under its own damper
thump, "I press a key and get 2 sounds").

    tools/hil_release_level.py [--rate 48000|44100] [--source both|usb] [--quick] [--jack-dev M6] [--jack-ch 5]
                               [--lock-wait S]
    tools/hil_release_level.py --dry-run      the planned takes, notes and time; no MIDI, no audio, no lock
    tools/hil_release_level.py --self-test    the analysis on synthetic audio (also tests/py/test_hil_release_level.py)

The stimulus (over USB-MIDI; every take from every parameter's default, DRY, USB VOL FIXED): the Rhodes TYPEs
(STAGE 77, SUITCASE, EARLY FELT, BELL DYNO: CC 80) x NOISE 48 (the default) and 127 (CC 104), one take each: v15 /
v60 / v127 (--quick: v15 only) x C2 / C4 / C6, each held 2 s, then the key-up with the pedal up, the next note 0.65 s
later. The NOISE 127 take ends with the pedal case: the pedal down, C4 v15 held 2 s, the key-up under the pedal, 1 s
later the pedal up (judged at the key-up and at the pedal-up). Recorded on USB and the headphone jack together
(hil_crackle.Device: the same recorder, rate switch and rig checks as stress_max), under build/hil/DEVICE.lockdir
(tools/lock.py; a run_final*.sh holding it is our ancestor: inherited).

The rule, per release (the key-up, or the pedal-up), on each source: the level 0..600 ms after it, as the loudest
10 ms RMS window and the peak, must not exceed the level just before it (the loudest 10 ms RMS window and the peak in
the 50 ms before) by more than +0.5 dB; and no new onset after it (the 10 ms RMS envelope rising >= 6 dB over its
lowest point since the release, to within 10 dB of the level before it and 10 dB over the take's floor: a damper's
knock well under the note is the NOISE knock, information, its rise reported).
The jack's level rows count only where the note stands >= 12 dB over the jack's own floor (the median 10 ms RMS in
the idle 200..20 ms before the note): under it, the jack's hiss against its hiss, the level rule is judged on USB
alone (the exact digital path; the report says "under the jack floor: judged on USB" with USB's numbers); a release
whose USB row fails is always a FAIL; the onset rule holds on both (2026-10-25: 6 jack-only FAILs, C6 v15 1-5 dB
over the jack's hiss, USB digital silence). Over that floor the jack's dRMS counts, and its dPeak only where the note's
peak stands >= 25 dB over the jack's hiss PEAK (the idle 400..120 ms before each note-on joined, the median peak of
its 600 ms blocks: as long as the 'after' window); under it the peak is judged on USB and the row says so (the jack
sits ~13 dB under USB at ~-75 dBFS: 1 LSB = 0.8 dB there, and one hiss sample in the 600 ms after the release moved
the peak: 2026-10-25 STAGE 77 NOISE 48 C6 v60 jack +0.7, NOISE 127 C4 v15 pedal-up jack +1.2, USB -0.5). --rejudge DIR judges a run's saved takes again (both sources are kept
for a failing take; --save-all keeps every take).
Where the release lands: each note's own onset (found near its note-on on the recording's clock,
hil_crackle.clock_fit) plus the hold on that clock; the 'before' window ends 12 ms before it (the event-to-block
jitter: clear of the release's sound a block early), the 'after' window starts at it. Report: build/hil/release_level/<rate>/report.txt +
report.json; exit 1 on any failure.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_crackle as HC  # noqa: E402
from hil_scan import type_cc  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "hil", "release_level")
FS_JACK = HC.FS_JACK
TYPES = (("STAGE 77", 0), ("SUITCASE", 1), ("EARLY FELT", 2), ("BELL DYNO", 3))
NOISES = (48, 127)                    # params.c P_NOISE default 48, max 127
VELS = (15, 60, 127)
NOTES = (("C2", 36), ("C4", 60), ("C6", 84))
HOLD, GAP, LEAD = 2.0, 0.65, 0.3      # s: the hold, the key-up to the next note-on, the silence before the first
PEDAL_GAP = 1.0                       # s: the key-up under the pedal to the pedal-up
TAIL = 0.8                            # s after the last release
TOL_DB = 0.5                          # the rule: after <= before + 0.5 dB
PRE, POST, GUARD, WIN, HOP = 0.050, 0.600, 0.012, 0.010, 0.0025
ONSET_RISE, ONSET_REL, ONSET_FLOOR = 6.0, 10.0, 10.0   # dB (see the docstring)
JACK_MIN_DB = 12.0                    # the jack's level rows count with the note this far over the jack's floor
JACK_PEAK_MIN_DB = 25.0               # the jack's dPeak counts with the note's peak this far over the jack's hiss peak
HISS_IDLE = (0.40, 0.12)              # s before each note-on: the idle the jack's hiss peak is measured in
FS_LSB = 32768.0


# ------------------------------------------------------------------------------------------------- the analysis
def _db(v):
    return 20.0 * np.log10(np.maximum(np.asarray(v, float), 1e-3) / FS_LSB)


def envelope(x, fs, t0, t1, win=WIN, hop=HOP):
    """(start times, RMS LSB, peak LSB) of the win-long windows starting every hop in [t0, t1 - win]; on a stereo
    recording the louder channel per window"""
    x = np.asarray(x, float)
    x = x[:, None] if x.ndim == 1 else x
    n, w = len(x), max(1, int(round(win * fs)))
    a, b = max(0, int(round(t0 * fs))), min(n, int(round(t1 * fs)))
    if b - a < w:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    starts = np.arange(a, b - w + 1, max(1, int(round(hop * fs))))
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x[a:b] ** 2, axis=0)])
    r = np.sqrt(np.maximum(0.0, (cs[starts - a + w] - cs[starts - a]) / w)).max(axis=1)
    ax = np.abs(x[a:b]).max(axis=1)
    pk = np.array([ax[s - a:s - a + w].max() for s in starts])
    return starts / fs, r, pk


def floor_db(x, fs, t0=0.02, t1=0.25):
    """the take's floor: the loudest 10 ms RMS in its silence before the first note (dBFS)"""
    _, r, _ = envelope(x, fs, t0, t1)
    return float(_db(r.max())) if len(r) else -96.0


def judge_release(x, fs, t_up, floor=-96.0, tol_db=TOL_DB, pre=PRE, post=POST, guard=GUARD):
    """one release at t_up (s, on x's clock): the levels before / after, the onset check, ok"""
    _, rb, pb = envelope(x, fs, t_up - guard - pre, t_up - guard)
    ta, ra, pa = envelope(x, fs, t_up, t_up + post)
    if not len(rb) or not len(ra):
        return dict(ok=False, why="no audio around the release")
    b_rms, b_pk = float(_db(rb.max())), float(_db(pb.max()))
    a_rms, a_pk = float(_db(ra.max())), float(_db(pa.max()))
    e = _db(ra)
    lo = np.minimum.accumulate(e)
    rise = e - lo
    hit = np.flatnonzero((rise >= ONSET_RISE) & (e >= b_rms - ONSET_REL) & (e >= floor + ONSET_FLOOR))
    k = int(np.argmax(rise))
    res = dict(before_rms=round(b_rms, 2), after_rms=round(a_rms, 2), before_peak=round(b_pk, 2),
               after_peak=round(a_pk, 2), d_rms=round(a_rms - b_rms, 2), d_peak=round(a_pk - b_pk, 2),
               rise_db=round(float(rise[k]), 1), rise_at_ms=round(1000 * (float(ta[k]) - t_up), 1),
               onset_ms=round(1000 * (float(ta[hit[0]]) - t_up), 1) if len(hit) else None)
    lv = []
    if res["d_rms"] > tol_db:
        lv.append(f"RMS {res['d_rms']:+.1f} dB after the release")
    if res["d_peak"] > tol_db:
        lv.append(f"peak {res['d_peak']:+.1f} dB after the release")
    on = [f"a new onset {res['onset_ms']:.0f} ms after the release"] if res["onset_ms"] is not None else []
    res.update(level_ok=not lv, onset_ok=not on, level_why="; ".join(lv), level_judged=True,
               rms_ok=res["d_rms"] <= tol_db, peak_ok=res["d_peak"] <= tol_db, peak_judged=True)
    res.update(ok=not (lv or on), why="; ".join(lv + on))
    return res


def apply_jack_floor(usb_rows, jack_rows, min_db=JACK_MIN_DB, peak_min_db=JACK_PEAK_MIN_DB):
    """the jack's level rows count only where the note stands >= min_db over the jack's own floor (the idle before
    the note): under it the jack's hiss against its hiss decides nothing, and the level rule is judged on USB alone
    (the exact digital path; a release whose USB row fails is a FAIL whatever the jack says). Over it, the jack's
    dRMS counts, and its dPeak only where the note's peak stands >= peak_min_db over the jack's hiss PEAK (measured
    over as long as the 'after' window): under it one hiss sample in the 600 ms 'after' window (1 LSB ~ 0.8 dB at
    -75 dBFS) moves the peak, and the peak is judged on USB (2026-10-25: STAGE 77 NOISE 48 C6 v60 jack +0.7, NOISE
    127 C4 v15 pedal-up jack +1.2, USB -0.5). The onset rule stays on both. In place on jack_rows; returns them"""
    for i, r in enumerate(jack_rows):
        if "over_floor" not in r or r["over_floor"] is None:
            continue
        u = usb_rows[i] if usb_rows and i < len(usb_rows) else None
        if r["over_floor"] >= min_db:
            po = r.get("peak_over_hiss")
            if po is None or po >= peak_min_db:
                continue
            r["peak_judged"] = False
            pk_ok = u is not None and "d_peak" in u and u["d_peak"] <= TOL_DB
            lv = [] if r.get("rms_ok", True) else [f"RMS {r['d_rms']:+.1f} dB after the release"]
            note = (f"the peak judged on USB (the note's peak {po:.1f} dB over the jack's hiss peak, "
                    f"< {peak_min_db:g}; jack dPeak {r['d_peak']:+.1f} dB)"
                    + (f": USB dPeak {u['d_peak']:+.1f} dB: {'ok' if pk_ok else 'FAIL'}" if u and "d_peak" in u
                       else ": no USB row, FAIL"))
            on = [] if r.get("onset_ok", True) else [f"a new onset {r['onset_ms']:.0f} ms after the release"]
            r["level_ok"] = not lv and pk_ok
            r["ok"] = r["level_ok"] and not on
            r["why"] = "; ".join(lv + on + [note])
            continue
        r["level_judged"] = False
        note = (f"under the jack floor (the note {r['over_floor']:.1f} dB over it, < {min_db:g}): judged on USB"
                + (f" (USB dRMS {u['d_rms']:+.1f} dB, dPeak {u['d_peak']:+.1f} dB: {'ok' if u['ok'] else 'FAIL'})"
                   if u and "d_rms" in u else ": no USB row, FAIL"))
        r["ok"] = bool(r.get("onset_ok", True)) and u is not None and "d_rms" in u
        r["why"] = "; ".join(x for x in ([] if r.get("onset_ok", True) else [f"a new onset {r['onset_ms']:.0f} ms "
                                                                             "after the release"]) + [note])
    return jack_rows


def note_onset(x, fs, t_pred, back=0.015, fwd=0.040):
    """the note's onset near its predicted time (s): the first sample over a tenth of its first 100 ms's peak"""
    x = np.asarray(x, float)
    a = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    s, e = max(0, int((t_pred - back) * fs)), min(len(a), int((t_pred + 0.100) * fs))
    if e - s < 8:
        return t_pred
    thr = 0.1 * float(a[s:e].max())
    i = np.flatnonzero(a[s:min(e, int((t_pred + fwd) * fs))] > thr)
    return (s + int(i[0])) / fs if len(i) and thr > 0 else t_pred


# ------------------------------------------------------------------------------------------------- the stimulus
def plan(quick=False):
    """the takes: [(name, setup ccs, events, dur, releases)]; releases: [(label, t_on, t_release, kind)] on the
    event clock (kind 'key' or 'pedal'; t_on: the note whose onset places it)"""
    vels = VELS[:1] if quick else VELS
    out = []
    for tname, t in TYPES:
        for nz in NOISES:
            ccs = HC.engine_ccs("rhodes", dict(TYPE=type_cc(t), NOISE=nz))
            ev, rel, tt = [], [], LEAD
            for v in vels:
                for nn, n in NOTES:
                    ev += [(tt, "on", n, v), (tt + HOLD, "off", n, 0)]
                    rel.append((f"{nn} v{v}", tt, tt + HOLD, "key"))
                    tt += HOLD + GAP
            if nz == NOISES[-1]:                         # the pedal case: down, C4 v15, key-up held, pedal up
                ev.append((tt, "cc", 64, 127))
                on = tt + 0.1
                ev += [(on, "on", 60, 15), (on + HOLD, "off", 60, 0), (on + HOLD + PEDAL_GAP, "cc", 64, 0)]
                rel += [("C4 v15 pedal: key-up", on, on + HOLD, "key"),
                        ("C4 v15 pedal: pedal-up", on, on + HOLD + PEDAL_GAP, "pedal")]
                tt = on + HOLD + PEDAL_GAP + GAP
            out.append((f"{tname} NOISE {nz}", ccs, sorted(ev, key=lambda e: e[0]), tt - GAP + TAIL, rel))
    return out


def plan_text(a):
    P = plan(a.quick)
    total = sum(d + 1.5 for _, _, _, d, _ in P)
    L = [f"release_level (tools/hil_release_level.py): the Rhodes TYPEs x NOISE {' / '.join(map(str, NOISES))}, "
         f"v{' / v'.join(map(str, VELS[:1] if a.quick else VELS))} x {' / '.join(n for n, _ in NOTES)} "
         f"held {HOLD:g} s; "
         f"USB at {a.rate} Hz" + (" + the jack (48 kHz)" if a.source == "both" else "")
         + f"; {len(P)} takes, {sum(len(r) for *_, r in P)} releases, ~{total / 60:.1f} min on the FM-1"]
    for name, ccs, ev, dur, rel in P:
        L.append(f"  {name:22s} {dur:5.1f} s  {len(ccs):3d} setup CCs  {sum(e[1] == 'on' for e in ev):2d} notes  "
                 f"{len(rel):2d} releases" + ("  + the pedal case" if any(r[3] == "pedal" for r in rel) else ""))
    L.append(f"rule: per release, 0..{1000 * POST:.0f} ms after it (10 ms RMS and the peak) <= the {1000 * PRE:.0f} ms "
             f"before + {TOL_DB} dB, no new onset; USB and the jack")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------- the self-test
def synth(kind, fs=48000, f0=261.6, t_up=1.0, dur=1.8, vel=0.05, seed=0):
    """a decaying note released at t_up (the damper: 40 ms), then: 'clean'; 'thump' (a 70 Hz knock 30 ms after the
    key-up, 10 dB over the note's level there: the 0.9.5 defect); 'knock' (the same 20 dB under: the NOISE knock);
    'swell' (the release's tail swelling 4 dB over the note, no new onset)"""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * fs)) / fs
    env = vel * np.exp(-t / 1.5) * np.minimum(1.0, t / 0.002)
    env = np.where(t < t_up, env, env * np.exp(-(t - t_up) / 0.040))
    x = env * (np.sin(2 * np.pi * f0 * t) + 0.3 * np.sin(2 * np.pi * 2 * f0 * t))
    lvl = vel * np.exp(-t_up / 1.5)
    if kind in ("thump", "knock"):
        g = lvl * (10 ** (10 / 20) if kind == "thump" else 10 ** (-20 / 20))
        tk = t - (t_up + 0.030)
        x += np.where(tk >= 0, g * np.exp(-tk / 0.025) * np.sin(2 * np.pi * 70 * tk), 0.0)
    elif kind == "swell":
        x = np.where(t < t_up, x, (lvl * 10 ** (4 / 20)) * np.exp(-(t - t_up) / 0.3)
                     * np.sin(2 * np.pi * f0 * t))
    x = x * FS_LSB + rng.normal(0, 2.0, len(t))
    return x, t_up


def self_test(log=print):
    want = dict(clean=True, knock=True, thump=False, swell=False)
    ok = True
    for f0 in (65.4, 261.6, 1046.5):
        for kind, exp in want.items():
            x, tu = synth(kind, f0=f0)
            r = judge_release(x, 48000, tu, floor=-84.0)    # (the synthetic noise: 2 LSB RMS)
            good = r["ok"] == exp
            ok &= good
            log(f"  {f0:7.1f} Hz {kind:6s} {'ok  ' if good else 'FAIL'} d_rms {r['d_rms']:+5.1f} d_peak "
                f"{r['d_peak']:+5.1f} rise {r['rise_db']:4.1f} dB {r['why'] or 'clean'}")
    return ok


# ------------------------------------------------------------------------------------------------- the device run
def note_floor(x, fs, t_on, t0=0.200, t1=0.020):
    """the floor in the idle before a note (s on x's clock): the median 10 ms RMS from t0 to t1 before its onset
    (dBFS; the take's lead silence for the first note, the previous note's damped gap for the others)"""
    _, r, _ = envelope(x, fs, t_on - t0, t_on - t1)
    return float(_db(np.median(r))) if len(r) else -96.0


def hiss_peak(x, fs, ons, length=POST, idle=HISS_IDLE):
    """the recording's hiss PEAK over a window as long as the 'after' window (dBFS): the idle idle[0]..idle[1] s
    before each note-on (ons, s on x's clock; clear of the previous note's damped tail and of the pedal-down 0.1 s
    before the pedal note) joined, cut in length-long blocks, the median block's peak (one block holding a leftover
    tail does not move it); shorter than length: its peak"""
    x = np.asarray(x, float)
    a = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    seg = [a[max(0, int((t - idle[0]) * fs)):max(0, int((t - idle[1]) * fs))] for t in sorted(set(ons))]
    j = np.concatenate([s for s in seg if len(s)] or [np.zeros(1)])
    w = int(round(length * fs))
    pk = [j[k:k + w].max() for k in range(0, len(j) - w + 1, w)] or [j.max()]
    return float(_db(np.median(pk)))


def judge_take(x, fs, ev, rel, clock):
    fl = floor_db(x, fs, 0.02, LEAD - 0.05)
    out, ons = [], {}
    for label, t_on, t_rel, kind in rel:
        if t_on not in ons:
            ons[t_on] = note_onset(x, fs, float(HC.map_clock([t_on], clock)[0]))
    hp = hiss_peak(x, fs, list(ons.values()))
    for label, t_on, t_rel, kind in rel:
        on_rec = ons[t_on]
        t_up = on_rec + (t_rel - t_on) * (1.0 + clock[1])
        r = judge_release(x, fs, t_up, floor=fl)
        nf = note_floor(x, fs, on_rec)
        r.update(label=label, kind=kind, t_up=round(t_up, 3), floor_db=round(nf, 1),
                 over_floor=round(r["before_rms"] - nf, 1) if "before_rms" in r else None, hiss_peak_db=round(hp, 1),
                 peak_over_hiss=round(r["before_peak"] - hp, 1) if "before_peak" in r else None)
        out.append(r)
    return fl, out


def run(a, log=print):
    import lock
    od = os.path.join(OUT, str(a.rate))
    os.makedirs(od, exist_ok=True)
    rep = dict(rate=a.rate, source=a.source, quick=a.quick, tol_db=TOL_DB, takes=[], ok=True, rig=[])
    with lock.Lock("DEVICE", who="hil_release_level", why="release_level", wait_s=a.lock_wait):
        try:
            dev = HC.Device(a.rate, a.source == "both", a.jack_dev, a.jack_ch, poll=False)
        except HC.RateError as e:
            rep.update(ok=False, error=str(e))
            return rep, od
        try:
            for name, ccs, ev, dur, rel in plan(a.quick):
                for attempt in (1, 2):
                    u, j, _, rig = dev.take(ccs, ev, dur)
                    if not rig:
                        break
                    log(f"  {name}: the recorder slipped ({'; '.join(rig[:3])}): "
                        + ("again" if attempt == 1 else "RIG"))
                rep["rig"] += [f"{name}: {r}" for r in rig]
                T = dict(name=name, dur=round(dur, 2), src={})
                for src, x, fs in (("usb", u, a.rate), ("jack", j, FS_JACK)):
                    if x is None:
                        continue
                    fit = HC.clock_fit(x, ev, fs, rate=dev.clock.get(src))
                    fl, rows = judge_take(x, fs, ev, rel, (fit["lag"], fit["rate"]))
                    T["src"][src] = dict(floor_db=round(fl, 1), clock=fit, releases=rows)
                if "jack" in T["src"]:
                    apply_jack_floor(T["src"].get("usb", {}).get("releases"), T["src"]["jack"]["releases"])
                T["ok"] = all(r["ok"] for s in T["src"].values() for r in s["releases"])
                if not T["ok"] or a.save_all:                # (both sources, to judge them again later)
                    for src, x, fs in (("usb", u, a.rate), ("jack", j, FS_JACK)):
                        if x is not None:
                            np.savez_compressed(os.path.join(od, f"{name.replace(' ', '_')}-{src}.npz"), fs=fs,
                                                x=np.clip(np.round(x), -32768, 32767).astype(np.int16),
                                                events=json.dumps(ev), rate=dev.clock.get(src) or 0.0)
                rep["takes"].append(T)
                rep["ok"] &= T["ok"]
                log(f"  {name:22s} {'ok' if T['ok'] else 'FAIL'}")
        finally:
            dev.cap.close()
    rep["ok"] &= not rep["rig"]
    return rep, od


def old_rate(d):
    try:
        return json.load(open(os.path.join(d, "report.json"))).get("rate") or 48000
    except (OSError, ValueError):
        return 48000


def rejudge(d):
    """the saved takes in d (<name>-usb.npz / -jack.npz) judged again with this tool's rules; a take whose USB
    recording is not there takes its USB rows from d/report.json (the run's own)"""
    old = {}
    try:
        old = {T["name"]: T for T in json.load(open(os.path.join(d, "report.json")))["takes"]}
    except (OSError, ValueError, KeyError):
        pass
    P = {}                                           # name: the plans' releases by their note-on count (full, quick)
    for q in (False, True):
        for name, _, ev, _, rel in plan(q):
            P.setdefault(name, {})[sum(e[1] == "on" for e in ev)] = rel
    rep = dict(rate=None, source="rejudged " + d, quick=False, tol_db=TOL_DB, takes=[], ok=True, rig=[])
    for name, rels in P.items():
        T = dict(name=name, src={})
        for src in ("usb", "jack"):
            p = os.path.join(d, f"{name.replace(' ', '_')}-{src}.npz")
            if os.path.exists(p):
                z = np.load(p)
                x, fs = z["x"].astype(float), int(z["fs"])
                ev = json.loads(str(z["events"]))
                ev = [tuple(e) for e in ev]
                rl = rels.get(sum(e[1] == "on" for e in ev))
                if rl is None:
                    continue
                rt = float(z["rate"]) if "rate" in z.files and float(z["rate"]) else None
                fit = HC.clock_fit(x, ev, fs, rate=rt)
                fl, rows = judge_take(x, fs, ev, rl, (fit["lag"], fit["rate"]))
                T["src"][src] = dict(floor_db=round(fl, 1), clock=fit, releases=rows)
            elif name in old and src in old[name]["src"]:
                T["src"][src] = old[name]["src"][src]
        if not T["src"]:
            continue
        if "jack" in T["src"]:
            apply_jack_floor(T["src"].get("usb", {}).get("releases"), T["src"]["jack"]["releases"])
        T["ok"] = all(r["ok"] for s in T["src"].values() for r in s["releases"])
        rep["takes"].append(T)
        rep["ok"] &= T["ok"]
    rep["rate"] = int(old_rate(d))
    return rep


def report_text(rep):
    n = sum(len(s["releases"]) for T in rep["takes"] for s in T["src"].values())
    bad = sum(not r["ok"] for T in rep["takes"] for s in T["src"].values() for r in s["releases"])
    L = [f"release_level: USB {rep['rate']} Hz, {rep['source']}{', quick' if rep['quick'] else ''}: "
         f"{'PASS' if rep['ok'] else 'FAIL'} ({bad} of {n} releases fail; rule: after <= before + {rep['tol_db']} dB, "
         f"no new onset)"]
    if rep.get("error"):
        L.append(f"FAIL before any take: {rep['error']}")
    L.append(f"{'take':22s} {'release':24s} {'src':4s} {'dRMS':>6s} {'dPeak':>6s} {'rise':>5s} {'>flr':>5s}  verdict")
    for T in rep["takes"]:
        for src, S in T["src"].items():
            for r in S["releases"]:
                L.append(f"{T['name']:22s} {r.get('label', '?'):24s} {src:4s} {r.get('d_rms', float('nan')):+6.1f} "
                         f"{r.get('d_peak', float('nan')):+6.1f} {r.get('rise_db', float('nan')):5.1f} "
                         f"{r.get('over_floor') if r.get('over_floor') is not None else float('nan'):5.1f}  "
                         + (("ok" + (": " + r["why"] if r.get("why") else "")) if r["ok"] else "FAIL: " + r["why"]))
    L += [f"RIG: {x}" for x in rep["rig"]]
    return "\n".join(L)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--source", default="both", choices=("both", "usb"))
    ap.add_argument("--quick", action="store_true", help="v15 only")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--lock-wait", type=float, default=600.0)
    ap.add_argument("--save-all", action="store_true", help="keep every take's recordings (else the failing ones)")
    ap.add_argument("--rejudge", metavar="DIR", help="judge the takes saved in DIR again (no device); the USB rows "
                    "from DIR/report.json where a take's USB recording was not kept")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    if a.dry_run:
        print(plan_text(a))
        return 0
    if a.rejudge:
        rep = rejudge(a.rejudge)
        print(report_text(rep))
        return 0 if rep["ok"] else 1
    ok = self_test(log=(print if a.self_test else (lambda *_: None)))
    if a.self_test or not ok:
        print("self-test: " + ("PASS" if ok else "FAIL (the analysis is not proven: no device run)"))
        return 0 if ok else 1
    print(plan_text(a))
    t0 = time.monotonic()
    rep, od = run(a)
    rep["seconds"] = round(time.monotonic() - t0, 1)
    txt = report_text(rep)
    print(txt)
    open(os.path.join(od, "report.txt"), "w").write(txt + "\n")
    json.dump(rep, open(os.path.join(od, "report.json"), "w"), indent=1, default=str)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
