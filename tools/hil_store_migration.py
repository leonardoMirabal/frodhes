#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The store's migration from 0.9.5 on the FM-1 (BUGHUNT #28, private/STOCK-DATA-096.md section 6): the part MIDI
reaches. 0.9.5's setup and pattern made over MIDI, the candidate installed over it, then back and forth.

    tools/hil_store_migration.py --fwsc CANDIDATE [--old build/dl095/frodhes-0.9.5.fwsc] [--fresh] [--dry-run]
    tools/hil_store_migration.py --self-test       the read-back's proof on synthetic takes (also in make pytest)

THE FM-1'S PATTERN, SEQUENCER SETUP AND BOOT PRESET ARE OVERWRITTEN (and its 0.9.5 data, by the 0.9.5 install).
User presets, a real power cycle and a power cut stay manual (no MIDI SAVE, no power switch): section 7.

What is stored and read back (the setup items that MIDI sets and the sound shows):
  the pattern   step-recorded at QUANT 1/8 (stopped, REC on: each note on the next eighth): A = C4 E4 G4 C5 E5 G4
                (0.9.5), B = D4 A4 F4 (the candidate). Read back by playing it (CC 60) on a raw voice (CC 119, NOISE
                0, CC 10: none of them saved) into a USB take: the onsets (onsets_of: the semitone bands' rises),
                each note's pitch (the semitone with the largest harmonic sum), the eighth from the even notes'
                spacing
  SWING (CC 46) 64 (with A), 58 (B): read from the off-beat notes' delay (seq.c sq_at: (SWING - 50) / 50 eighth)
  BARS (CC 61)  2: read from the loop (the first note again after BARS x 8 eighths)
  QUANT (CC 62) 1/8 (2), the boot preset F01 (PC 0), chord mode and the arpeggiator off: set at the start (not read)
The tempo cannot be set over MIDI (PLAY taps it, the panel only): the unit's own is used and measured.
Every restart is an install (fm1_install.py: the update loader restarts the FM-1; it writes the app area only).

  1  install --old (its sha256 must be the 0.9.5 release asset's, OLD_SHA256)
  1f --fresh: a unit that never ran 0.9.6, updated from 0.9.5 (the user's path; BUGHUNT #40). On the FM-1 0.9.5's
     settings share the 0.9.6 record's two sectors (0xFC000 / 0xFD000), so a unit that ran 0.9.6 before keeps a
     0.9.6 copy there. FRESH_SWING: two 0.9.5 settings saves (SWING 61, then 62, SAVE_WAIT_S each), then step 2's
     own: three in one boot. 0.9.5's save (set_store.c at v0.9.5) writes the sector the load would not pick and
     then takes it as its own (set_at), so two saves in a row write both sectors whatever the start: no 0.9.6
     record is left (whichever sector the load took, or none: the first save goes to one, the second to the other).
     Not provable over MIDI: 0.9.6 takes 0.9.5's setup either way (#40's fix); the code is the proof
  2  the setup (QUANT 1/8, BARS 2, SWING 64, F01, chord / arp off), CLEAR, pattern A; 6 s (both auto saves);
     read back: A, 64, 2 (as set)
  3  restart (--old again): A, 64, 2 (0.9.5 kept them)
  4  install --fwsc: A, 64, 2 (migrated)
  5  restart (--fwsc again): A, 64, 2 (the migration's record)
  6  SWING 58, CLEAR, pattern B; 6 s; read back B, 58, 2; restart: B, 58, 2
  7  install --old: the pattern must be A (0.9.5's sectors 0x97000-0x9AFFF never written by the candidate); SWING
     and BARS noted (0.9.5's own 64 / 2, or its defaults 50 / 4 once the candidate has saved its record twice:
     information). Nothing is sent to 0.9.5 that it saves (a save would write over a copy of the candidate's record)
  8  install --fwsc: B, 58, 2 (its record; no second migration: A must not come back)
  9  install --fwsc again (an update over itself): B, 58, 2
  end  SWING 50, BARS 4, CLEAR (the pattern empty), 6 s: the FM-1 left on --fwsc
Exit 0 PASS, 1 FAIL. build/hil/store_migration/report.txt, the takes as .npz.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

OUT = os.path.join(ROOT, "build", "hil", "store_migration")
OLD = os.path.join(ROOT, "build", "dl095", "frodhes-0.9.5.fwsc")
OLD_SHA256 = "2ca1d517c4304c940384aac659357ad529500a2447535da362b704cb2a522320"   # the v0.9.5 release asset
PAT_A = (60, 64, 67, 72, 76, 67)
PAT_B = (62, 69, 65)
SET_A = dict(swing=64, bars=2)
SET_B = dict(swing=58, bars=2)
QUANT_8 = 2                                    # CC 62: 1/8 (seq.c SQ_GRID 48 ticks; a step-recorded note an eighth)
CC_PLAY, CC_BARS, CC_QUANT, CC_REC, CC_CLEAR, CC_SWING = 60, 61, 62, 63, 43, 46
CC_RAW, CC_NOISE, CC_RESET, CC_CHORD, CC_ARP = 119, 104, 10, 56, 52
FRESH_SWING = (61, 62)                         # --fresh: 0.9.5's two extra saves (each differs from the one before)
SAVE_WAIT_S = 6.0                              # the pattern's save 1.5 s, the setup's 2 s after the change, silent
TAKE_S = 14.0                                  # a loop of 2 bars and its first note again down to 70 BPM
SWING_TOL = 3.0                                # read within 3 % of the set SWING
MIN_BPM = 70
FLOOR_DBFS = -60.0                             # a note's frame above this (the raw voice at v100: ~-20 dBFS)


# ---- the read-back (pure: the self-test and pytest run it) --------------------------------------------------------

def semis(x, fs, lo=36, hi=108):
    """energy per semitone band (linear), MIDI lo..hi"""
    x = np.asarray(x, float)
    P = np.abs(np.fft.rfft(x * np.hanning(len(x)), 4 * len(x))) ** 2
    f = np.fft.rfftfreq(4 * len(x), 1.0 / fs)
    n = np.arange(lo, hi + 1)
    fc = 440.0 * 2 ** ((n - 69) / 12)
    return n, np.array([P[(f >= c * 2 ** (-1 / 24)) & (f < c * 2 ** (1 / 24))].sum() for c in fc])


def pitch_at(x, fs, t, lo=48, hi=84):
    """the note starting at t: the semitone n (lo..hi) whose harmonics n, +12, +19, +24 hold the most energy, 20..120
    ms after the onset (a Rhodes note's 2nd harmonic can be louder than its fundamental: the sum decides)"""
    seg = x[int((t + 0.02) * fs):int((t + 0.12) * fs)]
    n, e = semis(seg, fs)
    ix = {int(k): v for k, v in zip(n, e)}
    score = [ix.get(c, 0) + ix.get(c + 12, 0) + 0.5 * ix.get(c + 19, 0) + 0.25 * ix.get(c + 24, 0)
             for c in range(lo, hi + 1)]
    return lo + int(np.argmax(score))


def onsets_of(x, fs, min_gap=0.06, rel=0.25):
    """note onsets (s) by spectral flux: the semitone bands' (MIDI 48..100) level rises (dB, over 10 ms, 20 ms frames
    every 2 ms), summed; peaks at least min_gap apart, over rel x the take's 95th-percentile peak and in a frame over
    FLOOR_DBFS, each placed at the frame where the rise began. A note that starts while the last one still rings (a
    swung pattern: the next eighth 0.6 of one later) shows as its own pitch's rise, where the overall level hardly
    moves. Checked on a real FM-1 take too (hil_clock's seq_120: C4 G4 C5 E4 x 2 at 1/16, every note and pitch)"""
    from scipy.signal import find_peaks
    x = np.asarray(x, float)
    n, h = int(0.020 * fs), int(0.002 * fs)
    st = np.arange(0, len(x) - n, h)
    if not len(st):
        return []
    f = np.fft.rfftfreq(n, 1.0 / fs)
    notes = np.arange(48, 101)
    fc = 440.0 * 2 ** ((notes - 69) / 12)
    edges = np.searchsorted(f, np.concatenate((fc * 2 ** (-1 / 24), [fc[-1] * 2 ** (1 / 24)])))
    win = np.hanning(n)
    L = np.empty((len(st), len(notes)))
    for k in range(0, len(st), 512):
        idx = st[k:k + 512, None] + np.arange(n)[None, :]
        P = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        c = np.concatenate((np.zeros((P.shape[0], 1)), np.cumsum(P, axis=1)), axis=1)
        E = c[:, edges[1:]] - c[:, edges[:-1]]
        L[k:k + 512] = 10 * np.log10(E + 1e-9)
    L = np.maximum(L, L.max() - 60)
    lag = 5
    d = np.zeros(len(st))
    d[lag:] = np.maximum(L[lag:] - L[:-lag], 0).sum(axis=1)
    pk, pr = find_peaks(d, distance=max(1, int(min_gap / 0.002)))
    if not len(pk):
        return []
    top = np.percentile(d[pk], 95)
    lvl = np.array([10 * np.log10(np.mean(x[st[p]:st[p] + n] ** 2) + 1e-20) for p in pk])
    pk = pk[(d[pk] >= rel * top) & (lvl >= FLOOR_DBFS)]  # (a rise in the noise floor is no note)
    return [float((st[p - lag] + n / 2) / fs) for p in pk]


def read_playback(x, fs, t_play, onsets=None):
    """the pattern as played from t_play: dict(notes [(t, midi)] of the first pass, eighth (s), swing (%), bars,
    loop (s)); error when it cannot be read"""
    if onsets is None:
        onsets = onsets_of(x, fs)
    ons = [float(t) for t in onsets if t >= t_play - 0.01]
    if len(ons) < 2:
        return dict(error=f"{len(ons)} notes heard", notes=[])
    groups, cur = [], [ons[0]]                   # a pass: notes an eighth apart (swung: 0.5 .. 1.5 eighths)
    gap = 60.0 / MIN_BPM / 2 * 1.6
    for t in ons[1:]:
        if t - cur[-1] <= gap:
            cur.append(t)
        else:
            groups.append(cur)
            cur = [t]
    groups.append(cur)
    first = groups[0]
    notes = [(t, pitch_at(x, fs, t)) for t in first]
    res = dict(notes=notes, n=len(first))
    ev = first[::2]
    if len(ev) >= 2:
        e8 = (ev[-1] - ev[0]) / (2 * (len(ev) - 1))
    elif len(first) >= 2:
        e8 = first[1] - first[0]
    else:
        return dict(res, error="one note: no eighth to measure")
    res["eighth"] = e8
    res["bpm"] = 30.0 / e8
    odd = [first[i] - first[i - 1] - e8 for i in range(1, len(first), 2)]
    res["swing"] = 50 + 50 * float(np.median(odd)) / e8 if odd else None
    if len(groups) >= 2:
        res["loop"] = groups[1][0] - first[0]
        res["bars"] = res["loop"] / (8 * e8)
    else:
        res["loop"] = res["bars"] = None
    return res


def judge(r, pat, st, label):
    """rows (check, value, criterion, ok) of a read-back against the pattern pat and the setup st (None: noted)"""
    rows = []
    if r.get("error"):
        return [(f"{label}: the playback", r["error"], "readable", False)]
    got = [m for _, m in r["notes"]]
    pc_ok = sum((a - b) % 12 == 0 for a, b in zip(got, pat))
    same = len(got) == len(pat) and pc_ok >= len(pat) - 1
    rows.append((f"{label}: the pattern", " ".join(map(str, got)) or "-",
                 f"{' '.join(map(str, pat))} ({len(pat)} notes, the pitch classes, one may miss)", same))
    if st is None:
        rows.append((f"{label}: SWING / BARS", f"{fmt(r.get('swing'))} / {fmt(r.get('bars'))}", "noted", None))
        return rows
    sw = r.get("swing")
    rows.append((f"{label}: SWING", fmt(sw), f"{st['swing']} +-{SWING_TOL:g}",
                 sw is not None and abs(sw - st["swing"]) <= SWING_TOL))
    b = r.get("bars")
    rows.append((f"{label}: BARS", fmt(b), f"{st['bars']} (+-0.15)", b is not None and abs(b - st["bars"]) <= 0.15))
    rows.append((f"{label}: tempo", fmt(r.get("bpm")), "information (the unit's own)", None))
    return rows


def fmt(v):
    return "-" if v is None else f"{v:.2f}"


# ---- the self-test: synthetic playbacks ---------------------------------------------------------------------------

def synth(pat, swing, bars, bpm=120.0, fs=48000, secs=TAKE_S, t0=0.7, seed=0, sound="rhodes"):
    """a take of pat played from t0: each note an eighth (swung), the loop BARS x 8 eighths, a hammer click and a
    decaying tone (sound 'rhodes': the 2nd harmonic louder than the fundamental), noise"""
    rng = np.random.default_rng(seed)
    x = 1e-4 * rng.standard_normal(int(secs * fs))
    e8 = 30.0 / bpm
    loop = bars * 8 * e8
    amps = (0.6, 1.0, 0.35, 0.2, 0.1) if sound == "rhodes" else (1.0, 0.3, 0.1)
    k = 0
    while True:
        base = t0 + k * loop
        if base >= secs:
            break
        for i, m in enumerate(pat):
            t = base + i * e8 + ((swing - 50) / 50 * e8 if i % 2 else 0.0)
            i0 = int(t * fs)
            n = min(int(1.2 * fs), len(x) - i0)
            if n <= 0:
                continue
            tt = np.arange(n) / fs
            f = 440 * 2 ** ((m - 69) / 12)
            y = sum(a * np.sin(2 * np.pi * f * (h + 1) * tt) * np.exp(-tt * (3 + 2 * h)) for h, a in enumerate(amps))
            y *= np.minimum(1.0, tt / 0.002) * np.where(tt < e8 * 0.9, 1.0, np.exp(-(tt - e8 * 0.9) / 0.03))
            nc = min(n, int(0.003 * fs))
            y[:nc] += 0.3 * rng.standard_normal(nc) * np.exp(-np.arange(nc) / (0.0007 * fs))
            x[i0:i0 + n] += 0.2 * y
        k += 1
    return x, fs, t0


def self_test(log=print):
    cases = [(PAT_A, SET_A, PAT_A, SET_A, True, "A as set"),
             (PAT_B, SET_B, PAT_B, SET_B, True, "B as set"),
             (PAT_B, SET_B, PAT_A, SET_A, False, "B heard where A is due"),
             (PAT_A, SET_A, PAT_B, SET_B, False, "A heard where B is due (a second migration)"),
             (PAT_A, dict(swing=50, bars=4), PAT_A, SET_A, False, "A with the defaults where 64 / 2 are due"),
             (PAT_A, dict(swing=58, bars=2), PAT_A, SET_A, False, "SWING 58 where 64 is due"),
             (PAT_A, dict(swing=64, bars=1), PAT_A, SET_A, False, "BARS 1 where 2 is due")]
    ok_all = True
    for bpm in (90.0, 120.0, 160.0):
        for k, (pat, st, epat, est, want, what) in enumerate(cases):
            x, fs, t0 = synth(pat, st["swing"], st["bars"], bpm=bpm, seed=k)
            r = read_playback(x, fs, t0)
            rows = judge(r, epat, est, what)
            ok = all(v is not False for *_, v in rows)
            good = ok == want
            ok_all &= good
            log(f"{'ok  ' if good else 'FAIL'} {bpm:g} BPM, {what}: {'PASS' if ok else 'FAIL'} (want "
                f"{'PASS' if want else 'FAIL'}): " + "; ".join(f"{c} {v}" for c, v, _, o in rows if o is not None))
    return ok_all


# ---- the run on the FM-1 ------------------------------------------------------------------------------------------

STEPS = [("1", "install --old (0.9.5)", None, None),
         ("2", "the setup + pattern A, 6 s, read back", PAT_A, SET_A),
         ("3", "restart (--old again), read back", PAT_A, SET_A),
         ("4", "install --fwsc: migrated", PAT_A, SET_A),
         ("5", "restart (--fwsc again), read back", PAT_A, SET_A),
         ("6", "SWING 58 + pattern B, 6 s, read back; restart, read back", PAT_B, SET_B),
         ("7", "install --old: pattern A (0x97000-0x9AFFF untouched), SWING / BARS noted", PAT_A, None),
         ("8", "install --fwsc: B, 58, 2 (no second migration)", PAT_B, SET_B),
         ("9", "install --fwsc over itself (an update): B, 58, 2", PAT_B, SET_B)]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def plan_text(a):
    out = [f"hil_store_migration: --old {os.path.basename(a.old)}, --fwsc {os.path.basename(a.fwsc)}, mode "
           + ("--fresh (0.9.5's sectors rewritten first: a unit that never ran 0.9.6)" if a.fresh else
              "as found (a 0.9.6 record from an earlier run may survive in 0.9.5's sectors)"),
           "OVERWRITES the FM-1's pattern, sequencer setup and boot preset (F01); 6 installs (~80 s each)"]
    out += [f"  {n}  {what}" for n, what, _, _ in STEPS]
    if a.fresh:
        out.insert(3, f"  1f two 0.9.5 settings saves (SWING {FRESH_SWING[0]}, {FRESH_SWING[1]}, {SAVE_WAIT_S:g} s "
                      "each): both settings sectors 0.9.5's")
    out.append("  end  SWING 50, BARS 4, CLEAR, 6 s; the FM-1 left on --fwsc")
    out.append(f"~{6 * 100 // 60 + 6} min")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fwsc", help="the candidate package")
    ap.add_argument("--old", default=OLD, help="0.9.5's package (its sha256 must be OLD_SHA256)")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--fresh", action="store_true", help="after step 1, two 0.9.5 settings saves: both sectors "
                    "0.9.5's, no 0.9.6 record left (a unit that never ran 0.9.6)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        ok = self_test()
        print("self-test", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if not a.fwsc:
        ap.error("--fwsc is needed")
    if a.dry_run:
        print(plan_text(a))
        return 0
    for p in (a.fwsc, a.old):
        if not os.path.exists(p):
            raise SystemExit(f"no {p}")
    if sha256(a.old) != OLD_SHA256:
        raise SystemExit(f"{a.old} is not the 0.9.5 release asset (sha256 {sha256(a.old)}, want {OLD_SHA256})")
    if not self_test(log=lambda s: None):
        raise SystemExit("the self-test fails: the read-back is not proven, nothing run")
    import hil_must as hm
    a.prev = None
    m = hm.Must(a)
    os.makedirs(OUT, exist_ok=True)
    lines, rows_all = [time.strftime("%Y-%m-%d %H:%M:%S") + f"  old {os.path.basename(a.old)}, fwsc "
                       f"{os.path.basename(a.fwsc)} (sha256 {sha256(a.fwsc)[:16]}), {m.info()}, mode "
                       f"{'--fresh' if a.fresh else 'as found'}"], []

    def say(t):
        lines.append(t)
        print(t, flush=True)

    def cc(c, v):
        m.dev.cc(c, v)
        time.sleep(0.02)

    def readback(tag, pat, st, label):
        """the pattern played on a raw voice (nothing saved), read back and judged"""
        cc(64, 0); cc(CC_PLAY, 0); cc(120, 0)
        cc(CC_RAW, 127); cc(CC_NOISE, 0); cc(CC_RESET, 127)
        time.sleep(1.0)
        u = m.open_usb(48000, TAKE_S + 1.0, any_rate=True)
        time.sleep(0.5)
        t = u.mark()
        cc(CC_PLAY, 127)
        time.sleep(TAKE_S - 0.2)
        cc(CC_PLAY, 0)
        x = u.stop().mean(axis=1)
        cc(120, 0); cc(CC_RAW, 0)
        np.savez_compressed(os.path.join(OUT, tag + ".npz"), x=(x * 32767).astype(np.int16), fs=u.sr, t=t)
        r = read_playback(x, u.sr, t)
        rows = judge(r, pat, st, label)
        for c, v, crit, ok in rows:
            say(f"  {'info' if ok is None else 'ok  ' if ok else 'FAIL'} {c}: {v} ({crit})")
            rows_all.append(dict(check=c, value=v, crit=crit, ok=ok))
        return r

    def step_record(pat):
        cc(CC_PLAY, 0); cc(CC_CLEAR, 127)
        time.sleep(0.3)
        cc(CC_REC, 127)
        time.sleep(0.1)
        for n in pat:
            m.dev.send("on", n, 100)
            time.sleep(0.1)
            m.dev.send("off", n, 0)
            time.sleep(0.15)
        cc(CC_REC, 0)

    code = 1
    try:
        say("1  install --old"); m.install(a.old)
        if a.fresh:
            say(f"1f two 0.9.5 settings saves (SWING {FRESH_SWING[0]}, {FRESH_SWING[1]}): both sectors 0.9.5's")
            cc(64, 0); cc(CC_PLAY, 0); cc(120, 0)
            for v in FRESH_SWING:
                cc(CC_SWING, v)
                time.sleep(SAVE_WAIT_S)
        say("2  the setup + pattern A")
        cc(64, 0); cc(CC_CHORD, 0); cc(CC_ARP, 0)
        m.dev.send("pc", 0)
        time.sleep(0.4)
        cc(CC_QUANT, QUANT_8); cc(CC_BARS, SET_A["bars"]); cc(CC_SWING, SET_A["swing"])
        step_record(PAT_A)
        time.sleep(SAVE_WAIT_S)
        readback("2-old-set", PAT_A, SET_A, "2 0.9.5 as set")
        say("3  restart (--old)"); m.install(a.old)
        readback("3-old-restart", PAT_A, SET_A, "3 0.9.5 restarted")
        say("4  install --fwsc"); m.install(a.fwsc)
        readback("4-new-migrated", PAT_A, SET_A, "4 migrated")
        say("5  restart (--fwsc)"); m.install(a.fwsc)
        readback("5-new-restart", PAT_A, SET_A, "5 restarted")
        say("6  SWING 58 + pattern B")
        cc(CC_SWING, SET_B["swing"])
        step_record(PAT_B)
        time.sleep(SAVE_WAIT_S)
        readback("6-new-set", PAT_B, SET_B, "6 as set")
        m.install(a.fwsc)
        readback("6-new-restart", PAT_B, SET_B, "6 restarted")
        say("7  install --old"); m.install(a.old)
        r7 = readback("7-old-again", PAT_A, None, "7 0.9.5 again")
        sw, b = r7.get("swing"), r7.get("bars")
        which = ("0.9.5's own (64 / 2)" if sw is not None and abs(sw - 64) <= SWING_TOL and b and abs(b - 2) < 0.15
                 else "0.9.5's defaults (50 / 4)" if sw is not None and abs(sw - 50) <= SWING_TOL and b and
                 abs(b - 4) < 0.15 else "neither (see the values)")
        say(f"  info 7: 0.9.5's setup: {which}")
        say("8  install --fwsc"); m.install(a.fwsc)
        readback("8-new-again", PAT_B, SET_B, "8 the candidate again")
        say("9  install --fwsc over itself"); m.install(a.fwsc)
        readback("9-new-update", PAT_B, SET_B, "9 updated over itself")
        code = 1 if any(r["ok"] is False for r in rows_all) else 0
    except Exception as e:  # noqa: BLE001
        say(f"FAIL: stopped: {e!r}")
        code = 1
    finally:
        try:
            if not m.not_new:
                cc(CC_PLAY, 0); cc(CC_SWING, 50); cc(CC_BARS, 4); cc(CC_CLEAR, 127)
                time.sleep(SAVE_WAIT_S)
                say("end: SWING 50, BARS 4, the pattern cleared (saved); the FM-1 on --fwsc")
            else:
                say(f"*** THE FM-1 MAY NOT BE ON --fwsc: echo y | python3 tools/fm1_install.py {a.fwsc} ***")
        except Exception as e:  # noqa: BLE001
            say(f"WARNING: the end state not restored ({e!r})")
        with open(os.path.join(OUT, "report.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        json.dump(dict(rows=rows_all, verdict="PASS" if code == 0 else "FAIL"),
                  open(os.path.join(OUT, "report.json"), "w"), indent=1, default=str)
        print(f"{'PASS' if code == 0 else 'FAIL'} ({OUT}/report.txt)")
    return code


if __name__ == "__main__":
    sys.exit(main())
