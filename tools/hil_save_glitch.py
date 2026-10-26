#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""save_glitch: the worst case played EXACTLY during an auto save (the FM-1 over USB-MIDI + USB audio + the jack).

    tools/hil_save_glitch.py [--rate 48000|44100|both] [--offsets 0,30,60] [--kinds setup,pattern] [--engine rhodes]
                             [--limit 4200] [--inv-restore 0] [--source both|usb] [--jack-dev M6] [--jack-ch 5]
                             [--no-self-test] [--dry-run]
    tools/hil_save_glitch.py --self-test     the proof on the host (also in make pytest: tests/py/test_hil_save_glitch.py)

Why: an auto save (the setup ~2 s after a setup change or a preset load, frodhes.c settings_poll; the pattern 1.5 s
after a pattern edit, seq.c seq_save_poll) starts only in silence, but runs ~85-125 ms in slices (fm1_flash.h
fl_op_start / fl_op_poll): the interrupts off up to ~0.45 ms every millisecond (a 250 us slice + up to 200 us of
suspend latency). A note, PLAY or a clock Start in that window plays while the slices delay the audio ISR's start:
worst ~0.45 + 4.9 ms against the 5.8 ms block, never measured before this tool.

One take (kind x offset; the takes in that order, per rate):
  setup     the sound: every effect heavy (hil_crackle's HEAVY, the heaviest drive TYPE, TAPE, the reverb INFINITE at
            DECAY 126, LOW CUT; SPREAD 127), USB VOL FIXED; CC 10 (the developer reset); calm: CC 116 every 0.25 s
            until 2.5 s without a flash write (item 17 = 0; any save the setup itself caused is done)
  change    setup:   CC 57 (chord mode's INVERSION, a setup item; chord mode off, so nothing heard) to the other value
                     than the prime (CC 57 sent before the calm: the change always differs from what the flash holds)
            pattern: (before the recording, in the calm's place) REC on stopped (step recording), one note v20, REC
                     off, CC 120 + CC 10 (silence), the recording 0.15 s later (PREP_SETTLE_S: in that silence):
                     the pattern edited; its save 1.5 s later
            undo:    CC 44 (UNDO) takes that note back: the pattern exactly as it was, saved again (the same events)
  catch     CC 116 every ~4 ms from just before the save is due: the first reply with item 17 > 0 (a flash slice ran)
  attack    OFFSET ms after the catch (default 0, 30, 60): the pedal, a 10-voice SPREAD chord at v127, a pitch bend
            sweep (every 5 ms, +-full, 1 s), new chords every 0.25 s for 1 s, INFINITE frozen (DECAY 127) at 0.6 s,
            the notes off at 1.6 s, the pedal up at 1.7 s; CC 116 at 0.2 s (the save's window), 0.7, 1.2 and 2.1 s
The gates (each take; all must hold):
  save seen        item 17 > 0 after the change, within --deadline s (10)
  overlap          item 17 > 0 in the first reply AFTER the chord was sent: the save still ran while it played
  window ISR       the whole ISR (CC 108/107) in the replies from the chord to 0.2 s after it <= --limit us (4200, the
                   margin rule); the take's (the chord to the end) <= --limit too; 0 voices shed
  USB counters     every reply carries them, the stream flowing at the rate, underruns / missed / overruns 0 (the
                   whole take: the change, the catch, the attack)
  glitches         0 events of hil_crackle's gated kinds (repeat, stale, zero, clip, click, step, track: the
                   stress_max detectors, proven there) on USB and on the jack; the jack's alignment to USB from one
                   measured line (jack_line: each 50 ms window's best lag, a line through the matching ones), given to
                   track as its drift line (its own fit took wrong lines on these 4 s takes: false "ncc < 0.97" and
                   a false slip)
  rig              the recorder without gaps or PortAudio flags (re-taken once; twice = RIG FAIL)
Information: the ISR entry spacing (item 10) in the window, the flash's own items 17-20, the engine's longest block.

The player's data: only CC 57 (INVERSION) changes in the setup record (it cannot be read over MIDI; with chord mode
off it is not heard): the run leaves it at --inv-restore (default 0, the firmware's default) and waits for that save.
The pattern: one note recorded, then UNDO takes exactly that pass back (seq.c seq_undo: the newest take only) and the
undo's save writes the pattern as it was (an empty pattern stays empty); if a run stops in between, the exit path
sends the UNDO and waits for its save. The sound panel is set as every stress tool sets it (not saved: no sound is).
The self-test (host, also before a device run): the host build plays the same attack; the clean render gives 0 events
on USB and the simulated jack; synthetic faults put inside the save's window (2 ms of zeros on USB at +60 and +130 ms
after the chord, an underrun's hold of 12 frames there, 3 ms of silence on the jack at +60 and at +80 ms) are found and
FAIL the take (the jack's detectors are partly blind in that window: the attack's guards, and the jack ~-40 dBFS under
the full-depth tremolo, under det_zero's 200 LSB; on the host a 3 ms jack gap at +30, +45, +100, +120 ms went unseen:
USB's exact detectors, which have no guards, are the window's sure judge); synthetic replies (missed 1, the ISR 4300 us, no overlap, no
save) FAIL it too; clean replies PASS.
Exit 0 PASS, 1 FAIL. Report: build/hil/save_glitch/report.txt + report.json, the takes with events as .npz. Needs the
macOS sandbox off (CoreAudio, MIDI). Takes the device lock (tools/lock.py DEVICE; inherited from run_final_rev.sh)."""
import argparse
import json
import os
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
sys.path.insert(1, os.path.join(TOOLS, "final"))
os.environ.setdefault("TREE", ROOT)

import numpy as np  # noqa: E402

import hil_autosave as A  # noqa: E402
import hil_crackle as C  # noqa: E402

OUT = os.path.join(ROOT, "build", "hil", "save_glitch")
LIMIT_US = 4200
OFFSETS_MS = (0, 30, 60)
KINDS = ("setup", "pattern")                 # pattern = the edit take + the undo take
INV_CC, UNDO_CC, REC_CC, PLAY_CC = 57, 44, 63, 60
SPREAD_CC = 105
INF_DECAY = 126                              # rev_hil INF_STRESS_DECAY: SUSTAIN (127 is FREEZE: the input closed)
CALM_S, CALM_MAX_S = 2.5, 25.0
DEADLINE_S = 10.0
FAST_FROM = dict(setup=1.6, pattern=0.9, undo=1.0)   # s after the change: the catch's fast polls from here
WINDOW_S = 0.2                               # the save's window after the chord: the first reply there
STEP_NOTE, STEP_VEL = 60, 20
PREP_SETTLE_S = 0.15                         # after the pattern's prep (CC 120 + CC 10: its silence) the recording
                                             # starts only this much later: the reset reaches the device's output
                                             # ~10-20 ms after it is sent (USB in, the audio blocks), and a recording
                                             # started at once caught the note before it, then exact zeros up to the
                                             # chord: "zero 1452 ms" on USB (BUGHUNT #36, 2026-10-26: 0.9.6's mix
                                             # fixes made that note > 200 LSB, det_zero's "sound on both sides")


# ---- the stimulus ----------------------------------------------------------------------------------------------------
def sound_ccs(eng="rhodes"):
    """(ccs, notes): every effect heavy as the final run's stress steps (rev_hil stress --full) + INFINITE + SPREAD"""
    import hil_cpu_fx as F
    ccs = list(C.engine_ccs(eng, C.HEAVY))
    notes = []
    dt = sorted([t for t in F.drive_types() if t[2] is not None], key=lambda t: -t[2])[:1]
    if dt:
        ccs.append((84, dt[0][3]))
        notes.append(f"drive {dt[0][0]}")
    tv = F.tape_cc()
    if tv is not None:
        ccs.append((88, tv))
        notes.append("delay TAPE")
    rc = F.rev_cc()
    pick, _ = F.rev_pick("INFINITE") if rc else (None, [])
    if pick and rc:
        ccs.append((rc, pick[3]))
        ccs.append((C.CC["RV_DECAY"], INF_DECAY))
        lc = F.param_entry("RV_LOCUT")
        if lc and lc[3]:
            ccs.append((lc[3], 64))
        notes.append(f"reverb {pick[0]} DECAY {INF_DECAY}, LOW CUT")
    else:
        notes.append("no reverb TYPE in this tree: HALL")
    ccs.append((SPREAD_CC, 127))
    notes.append("SPREAD 127")
    return ccs, notes


def attack(nv, t0=0.0):
    """the worst case from t0 (s): [(t, kind, a, b)], kind on / off / cc / bend (b: the 14-bit value)"""
    ev = [(t0, "cc", 64, 127)]
    regs = ("spread", "mid", "bass", "high", "spread")
    held = []
    for k, r in enumerate(regs):
        t = t0 + 0.002 + 0.25 * k
        ev += [(t - 0.001, "off", n, 0) for n in held]
        notes = C.REG[r][:nv]
        ev += [(t, "on", n, 127 if k % 2 == 0 else 100) for n in notes]
        held = notes
    for i in range(201):                                     # the bend: 0 -> +max -> -max -> 0 over 1 s, every 5 ms
        x = i / 200.0
        tri = 4 * x if x < 0.25 else 2 - 4 * x if x < 0.75 else 4 * x - 4
        ev.append((t0 + 0.003 + 0.005 * i, "bend", 0, int(round(8192 + 8191 * tri))))
    ev.append((t0 + 0.6, "cc", C.CC["RV_DECAY"], 127))       # INFINITE frozen (the tank full)
    ev += [(t0 + 1.6, "off", n, 0) for n in held] + [(t0 + 1.7, "cc", 64, 0)]
    return sorted(ev, key=lambda e: e[0])


POLLS_AFTER = (WINDOW_S, 0.7, 1.2, 2.1)
ATTACK_S = 2.3


# ---- the judgement (host-tested) -------------------------------------------------------------------------------------
def parse(msgs):
    """one CC 116 reply: hil_autosave.parse + the engine's block (CC 116/117 us / 4) and the voices shed (CC 115)"""
    r = A.parse(msgs)
    got = {c: v for c, v in msgs if c in (115, 116, 117)}
    if {116, 117} <= got.keys():
        r["engine_us"] = (got[116] << 7 | got[117]) * 4
    if 115 in got:
        r["shed"] = got[115]
    return r


def judge(polls, rate, t_change, t_chord, limit=LIMIT_US, audio=None, rig=(), deadline=DEADLINE_S):
    """one take: polls [(t s, parse())] on the take's clock, t_chord None when the save was never caught; audio:
    [(source, t, kind, detail)] the gated events (None: not recorded, a FAIL). -> dict(rows, verdict, ...)"""
    rows = []

    def row(check, value, crit, ok, info=False):
        rows.append((check, value, crit, "info" if info else "ok" if ok else "FAIL"))
    ans = [(t, p) for t, p in polls if p["answered"]]
    row("counters answer", f"{len(ans)}/{len(polls)} replies", "CC 116 answers every poll",
        bool(polls) and len(ans) == len(polls))
    usb = [(t, p) for t, p in ans if 1 in p["items"]]
    row("USB counters answer", f"{len(usb)}/{len(ans)} replies", "every reply carries them",
        bool(ans) and len(usb) == len(ans))
    bad = [t for t, p in ans if not A.stream_ok(p["items"].get(12), rate)]
    row("USB stream state", "flowing at the rate in every reply" if not bad else
        f"not flowing at {rate / 1000:g} kHz in {len(bad)} replies", f"flowing at {rate / 1000:g} kHz", not bad)
    for k, i in (("underruns", 1), ("missed", 2), ("overruns", 3)):
        v = sum(p["items"].get(i, 0) for _, p in usb)
        where = [f"{t - t_change:.2f}" for t, p in usb if p["items"].get(i, 0)]
        row(f"USB {k}", v if not where else f"{v} (at {', '.join(where[:6])} s after the change)", "0", v == 0)
    seen = next((t for t, p in polls if t >= t_change and p["items"].get(A.ITEM_OFF, 0) > 0), None)
    row("save seen", "no (item 17 never > 0)" if seen is None else f"item 17 at {seen - t_change:.2f} s after the "
        "change", f"within {deadline:g} s", seen is not None and seen - t_change <= deadline)
    after = [(t, p) for t, p in polls if t_chord is not None and t > t_chord]
    first = after[0] if after else None
    ov = first is not None and first[1]["items"].get(A.ITEM_OFF, 0) > 0
    row("overlap", "no chord (the save never caught)" if t_chord is None else
        f"item 17 = {first[1]['items'].get(A.ITEM_OFF, 0)} us in the reply {first[0] - t_chord:.3f} s after the chord"
        if first else "no reply after the chord", "> 0: the save's slices ran while the chord played", ov)
    win = [p for t, p in after if t <= t_chord + WINDOW_S + 0.1] if t_chord is not None else []
    wi = [p["isr_us"] for p in win if "isr_us" in p]
    row("whole ISR in the save's window (us)", max(wi) if wi else "no reply", f"<= {limit}",
        bool(wi) and max(wi) <= limit)
    ti = [p["isr_us"] for _, p in after if "isr_us" in p]
    row("whole ISR, the chord to the end (us)", max(ti) if ti else "no reply", f"<= {limit}",
        bool(ti) and max(ti) <= limit)
    sh = [p["shed"] for _, p in polls if "shed" in p]
    shed = sum((b - a) % 128 for a, b in zip(sh, sh[1:]))
    row("voices shed", shed, "0", shed == 0)
    if audio is None:
        row("glitches (USB + jack)", "not recorded", "0 events", False)
    else:
        for src in ("usb", "jack"):
            ev = [e for e in audio if e[0] == src]
            row(f"glitches on {src.upper() if src == 'usb' else 'the jack'}", len(ev) if not ev else
                f"{len(ev)}: " + "; ".join(f"{t:.3f} s {k} {d}" for _, t, k, d in ev[:4]),
                "0 events (hil_crackle's gated detectors)", not ev)
    row("rig", "clean" if not rig else "; ".join(rig[:3]), "no recorder gap, no PortAudio flag", not rig)
    gaps = [p["items"][A.ITEM_GAP] for p in win if A.ITEM_GAP in p["items"]]
    if gaps:
        row("ISR entry spacing in the window, longest (us)", max(gaps), "information (5805 nominal)", True, info=True)
    fl = {i: max([p["items"].get(i, 0) for _, p in polls] or [0]) for i in (17, 18, 19, 20)}
    row("flash items 17-20 (off us, suspends, erase /10 us, wall /10 us)", " / ".join(str(fl[i]) for i in fl),
        "information", True, info=True)
    eu = [p["engine_us"] for _, p in after if "engine_us" in p]
    if eu:
        row("engine block max (us)", max(eu), "information", True, info=True)
    verdict = "FAIL" if any(r[3] == "FAIL" for r in rows) else "PASS"
    return dict(rows=rows, verdict=verdict, isr_window=max(wi) if wi else None, isr_take=max(ti) if ti else None,
                overlap=ov, seen=None if seen is None else round(seen - t_change, 3))


def audio_events(u, j, fs, events, ccs, rate_u=None, rate_j=None, widen=C.DEV_JITTER, fit=True):
    """hil_crackle's every detector on one take: [(source, t, kind, detail)] gated, and the analysis' info"""
    ev = [e for e in events if e[1] != "bend"]
    if fit:
        fu = C.clock_fit(u, ev, fs, rate=rate_u)
        cu = (fu["lag"], fu["rate"])
        cj = None
        if j is not None:
            fj = C.clock_fit(j, ev, C.FS_JACK, rate=rate_j)
            cj = (fj["lag"], fj["rate"])
    else:
        cu, cj = (0.0, 0.0), (0.0, 0.0)
    line = None
    if j is not None:                                        # (the jack's clock from ONE measured line against USB,
        line = jack_line(u, j, fs)                           # track's drift line = this one: see jack_line)
        if line["fit"] is not None:
            k, b = line["fit"]
            r1 = (1.0 + cu[1]) / (1.0 + k / C.FS_JACK) - 1.0
            cj = ((cu[0] - b / C.FS_JACK) * (1.0 + r1) / (1.0 + cu[1]), r1)
    keep_fit = C._drift_fit
    try:
        if line is not None and line["fit"] is not None:     # (track's drift line = the measured one: its own fit,
            k, b = line["fit"]                               # even held to the clocks' prior, took 8 loud windows
            fixed = (float(b), float(k) / C.FS_JACK, line["n"])   # over ~0.4 s on a wrong slope, 354 ppm against
            C._drift_fit = lambda *x, **kw: fixed            # 552, ~18 samples off by 1.8 s: 48k-undo-30 ms,
        res = C.analyse(u, j, fs, ev, widen=widen, ccs=ccs, clock_usb=cu, clock_jack=cj)   # 2026-10-25, 2nd run)
    finally:
        C._drift_fit = keep_fit
    if line is not None:
        res["info"]["jack_line"] = line["text"]
    return [(s, t, k, d) for s, t, k, d in C.gated(res)], res["info"]


LINE_WIN, LINE_NCC, LINE_TOL = 2400, 0.97, 2.0


def jack_line(u, j, fs, win=LINE_WIN, span=400):
    """the jack against USB along ONE straight line, measured directly: per 50 ms
    jack window the best lag (a quarter sample, on USB at 192 kHz) within +-span samples of the whole take's lag, its
    ncc, then a line through the windows that match (ncc >= LINE_NCC), refitted without those more than LINE_TOL
    samples off. track (hil_crackle) fits its drift line on 100 ms windows +-0.3 s and needs 8 of them within 2 samples:
    on a 4 s take with chords every 0.25 s and the bend sweep it took a wrong line, ~9 samples off and 417 ppm against
    the clocks' ~550 (the FM-1, 48k-undo-30 / -60 ms, 2026-10-25: every window matched at 0.97-1.000 on the right
    line, residuals <= 1.1 samples, and track called 11 / 9 of them ncc 0.91-0.94; zero_vs_usb, on the same wrong warp,
    would have called a real jack gap the music's own). audio_events gives analyse the jack's clock from this line and
    hands track this line as its drift line (hil_crackle _drift_fit for that call; holding its own fit to the line
    within 2 samples was not enough: 8 loud windows over ~0.4 s still took a 354 ppm slope, a false slip of -18
    samples): the detectors themselves are unchanged and judge on the right alignment; a slip leaves the line after
    it and is still found as one"""
    from math import gcd
    from scipy.signal import correlate, resample_poly
    x = np.asarray(u, float)
    x = x[:, 0] if x.ndim == 2 else x
    g = gcd(4 * C.FS_JACK, fs)
    u4 = resample_poly(x, 4 * C.FS_JACK // g, fs // g)
    u48 = u4[::4]
    jj = np.asarray(j, float)
    n = min(len(u48), len(jj))
    loud = np.flatnonzero(np.abs(jj[:n]) > 0.1 * np.abs(jj[:n]).max())
    if len(loud) < win:
        return dict(rows=[], fit=None, text="no loud jack")
    a0 = int(loud[0])
    seg = jj[a0:min(n, a0 + C.FS_JACK)]
    c = correlate(u48[max(0, a0 - 2000):a0 + len(seg) + 2000], seg, "valid", method="fft")
    L0 = int(np.argmax(np.abs(c))) - min(a0, 2000)
    rows = []
    for a in range(int(0.3 * C.FS_JACK), n - win - span - 4, win):
        js = jj[a:a + win]
        if np.sqrt(np.mean(js ** 2)) < 8.0 or a + L0 - span < 0:
            continue
        us = u48[a + L0 - span:a + L0 + span + win]
        cc = correlate(us, js, "valid", method="fft")
        k = int(np.argmax(np.abs(cc)))
        best = (0.0, 0.0)
        for q in range(-4, 5):                               # (a quarter sample around the integer peak)
            b = 4 * (a + L0 - span + k) + q
            if b < 0 or b + 4 * win > len(u4):
                continue
            w = u4[b:b + 4 * win:4]
            v = abs(float(np.dot(w, js))) / (np.sqrt(np.dot(w, w) * np.dot(js, js)) + 1e-9)
            if v > best[0]:
                best = (v, L0 - span + k + q / 4.0)
        rows.append((a / C.FS_JACK, best[1], best[0]))
    P = np.array([(t, L) for t, L, v in rows if v >= LINE_NCC]) if rows else np.zeros((0, 2))
    fit = None
    for _ in range(3):
        if len(P) < 8:
            break
        k, b = np.polyfit(P[:, 0], P[:, 1], 1)
        fit = (float(k), float(b))
        P = P[np.abs(P[:, 1] - (k * P[:, 0] + b)) <= LINE_TOL]
    if fit is None or len(P) < 8 or P[:, 0].max() - P[:, 0].min() < 1.0:
        return dict(rows=rows, fit=None, text=f"no line ({len(P)} windows match)")
    return dict(rows=rows, fit=fit, n=int(len(P)), text=f"{len(P)} of {len(rows)} windows on one line ({fit[0] / C.FS_JACK * 1e6:.0f} "
                f"ppm), ncc >= {LINE_NCC} within {LINE_TOL:g} samples")


# ---- the self-test (host) --------------------------------------------------------------------------------------------
def host_render(ccs, events, dur, tag):
    """the host build plays ccs then events (bend included): (USB (n, 2) LSB at 44.1 kHz)"""
    import hil_scan
    H = hil_scan.hr()
    lines = [f"{5 + k // 16} midi 0xB0 {c} {v}" for k, (c, v) in enumerate(ccs)]
    R = 5 + len(ccs) // 16 + 300
    lines.append(f"{R - 60} midi 0xB0 {hil_scan.RESET_CC} 127")
    for t, kind, a, b in events:
        ms = int(round(R + 1000 * t))
        lines.append(f"{ms} midi 0x90 {a} {b}" if kind == "on" else f"{ms} midi 0x80 {a} 0" if kind == "off" else
                     f"{ms} midi 0xE0 {b & 127} {b >> 7}" if kind == "bend" else f"{ms} midi 0xB0 {a} {b}")
    r = H.run(tag, lines, R + int(np.ceil(1000 * dur)) + 20, check=False)
    i0 = int(R * C.FS_HOST / 1000)
    return np.stack([r.L[i0:], r.R[i0:]], 1)


def fake_polls(t_change, t_chord, isr=3900, missed_at=None, off_after=150, isr_win=None):
    """synthetic replies for the gate's self-test: the calm, the catch (item 17 > 0), the attack's replies"""
    def rep(item17=0, isr_us=isr, missed=0):
        it = {1: 0, 2: missed, 3: 0, 12: 2 | 4, 10: 5900, 17: item17}
        return dict(items=it, answered=True, isr_us=isr_us, engine_us=isr_us - 300, shed=0)
    P = [(t_change - 0.5, rep(isr_us=800)), (t_change + 0.5, rep(isr_us=800)), (t_chord - 0.001, rep(item17=240))]
    for k, dt in enumerate(POLLS_AFTER):
        P.append((t_chord + dt, rep(item17=off_after if k == 0 else 0,
                                    isr_us=(isr_win if k == 0 and isr_win else isr),
                                    missed=1 if missed_at == k else 0)))
    return P


def self_test(eng="rhodes", log=print):
    """(ok, lines): the clean render 0 events; each synthetic fault FAILs the take"""
    out, ok = [], True

    def say(s, good):
        nonlocal ok
        ok &= good
        out.append(f"self-test: {s}: {'ok' if good else 'FAIL'}")
        log(out[-1])
    ccs, _ = sound_ccs(eng)
    t_change, t_chord = 0.3, 2.6
    ev = [(t_change, "cc", INV_CC, 1)] + attack(C.voices(eng), t_chord)
    dur = t_chord + ATTACK_S
    u = host_render(ccs, ev, dur, "save_glitch-st")
    j = C.sim_jack(u)
    clean, _ = audio_events(u, j, C.FS_HOST, ev, ccs, fit=False)
    say(f"clean render: {len(clean)} event(s)" + "".join(f"; {s} {t:.3f} {k} {d}" for s, t, k, d in clean[:4]),
        not clean)
    P = fake_polls(t_change, t_chord)
    r = judge(P, 48000, t_change, t_chord, audio=clean)
    say(f"clean render + clean replies: {r['verdict']}", r["verdict"] == ("PASS" if not clean else "FAIL"))
    fs = C.FS_HOST
    # inside the save's ~85-125 ms, outside the attack's 20 ms guard. The jack: 3 ms at +60 / +80 ms, each alone
    # (found by track there; the jack's detectors are partly blind in this window: the attack's guards, and the jack
    # ~-40 dBFS under the full-depth tremolo, under det_zero's 200 LSB around; USB's exact detectors are not)
    for name, marks, ms in (("USB dropout 2 ms", (0.06, 0.13), 2), ("jack dropout 3 ms", (0.06,), 3),
                            ("jack dropout 3 ms", (0.08,), 3), ("USB underrun hold 12 frames", (0.06, 0.13), 0)):
        u2, j2 = u.copy(), j.copy()
        for m in marks:
            m += t_chord
            if name.startswith("USB dropout"):
                a = int(m * fs)
                u2[a:a + int(ms / 1000 * fs)] = 0.0
            elif name.startswith("jack"):
                a = int((m + C.JACK_DELAY) * C.FS_JACK)
                j2[a:a + int(ms / 1000 * C.FS_JACK)] = 0.0
            else:
                a = int(m * fs)
                u2 = np.concatenate([u2[:a], np.repeat(u2[a - 1:a], 12, 0), u2[a:]])[:len(u)]
        evs, _ = audio_events(u2, j2, fs, ev, ccs, fit=False)
        src = "jack" if name.startswith("jack") else "usb"
        hit = [any(s == src and (abs(t - m - t_chord) < 0.012 or k == "track" and -0.06 <= t - m - t_chord <= 0.1)
                   for s, t, k, _ in evs) for m in marks]
        r = judge(P, 48000, t_change, t_chord, audio=evs)
        say(f"{name} at +{', +'.join(f'{1000 * m:.0f}' for m in marks)} ms after the chord (the save's window): found "
            f"{sum(hit)}/{len(marks)}, the take {r['verdict']}", all(hit) and r["verdict"] == "FAIL")
    # the pattern's prep (REC on, the note v20, REC off, CC 120 + CC 10: silence) inside the recording, BUGHUNT #36: on
    # the FM-1 a recording started at once caught ~15 ms of that note (~1000 LSB; the reset reaches the output ~15 ms
    # after it is sent), then exact zeros up to the chord: det_zero's "sound on both sides", one USB zero run of
    # 1.45 s, the take FAIL with no fault. That shape, from the host's note and chord: found; the recording started
    # PREP_SETTLE_S after the prep (in the silence, the note's tail gone): nothing
    t_rst = 0.2
    pev = [(0.0, "cc", PLAY_CC, 0), (0.0, "cc", REC_CC, 127), (0.05, "on", STEP_NOTE, STEP_VEL),
           (0.15, "off", STEP_NOTE, 0), (t_rst, "cc", REC_CC, 0), (t_rst, "cc", 120, 0),
           (t_rst, "cc", C.hil_scan.RESET_CC, 127)]
    t_ch = t_rst + 1.2
    pu = host_render(ccs, pev + attack(C.voices(eng), t_ch), t_ch + 0.6, "save_glitch-prep")
    a = int(0.06 * fs)
    note = pu[a:a + int(0.015 * fs)]                     # (the note at its loud part: > 200 LSB, as the FM-1's)
    gap = int((t_ch - t_rst - PREP_SETTLE_S) * fs)
    for name, lead, want in (("the recording started at once (the note, then the reset's silence)", note, True),
                             ("the recording started PREP_SETTLE_S after the prep", note[:0], False)):
        u2 = np.concatenate([lead, np.zeros((gap, 2)), pu[int(t_ch * fs):]])
        t0 = (len(lead) + gap) / fs
        ev2 = [(t - t_ch + t0, k, x, y) for t, k, x, y in attack(C.voices(eng), t_ch)]
        evs, _ = audio_events(u2, C.sim_jack(u2), fs, ev2, ccs, fit=False)
        zero = [e for e in evs if e[0] == "usb" and e[2] == "zero"]
        say(f"{name}: {len(zero)} USB zero run(s)" + (f" ({zero[0][3]})" if zero else ""), bool(zero) == want)
    for name, kw in (("USB missed 1 in the window", dict(missed_at=0)), ("the ISR 4300 us in the window",
                     dict(isr_win=4300)), ("no overlap (the save done before the chord)", dict(off_after=0))):
        r = judge(fake_polls(t_change, t_chord, **kw), 48000, t_change, t_chord, audio=[])
        say(f"{name}: the take {r['verdict']}", r["verdict"] == "FAIL")
    r = judge(fake_polls(t_change, t_chord)[:2], 48000, t_change, None, audio=[])
    say(f"no save caught: the take {r['verdict']}", r["verdict"] == "FAIL")
    r = judge(fake_polls(t_change, t_chord), 48000, t_change, t_chord, audio=[])
    say(f"clean replies, no events: the take {r['verdict']}", r["verdict"] == "PASS")
    out.append(f"self-test: {'PASS' if ok else 'FAIL'}")
    log(out[-1])
    return ok, out


# ---- the plan and the run --------------------------------------------------------------------------------------------
def take_list(a):
    T = []
    for rate in (48000, 44100) if a.rate == "both" else (int(a.rate),):
        for kind in a.kinds:
            for off in a.offsets:
                if kind == "setup":
                    T.append((rate, "setup", off))
                else:
                    T += [(rate, "pattern", off), (rate, "undo", off)]
    return T


def plan(a):
    T = take_list(a)
    ccs, notes = sound_ccs(a.engine)
    per = 4.0 + 2.5 + ATTACK_S + 1.5
    L = [f"save_glitch plan: {len(T)} takes ({', '.join(f'{r // 1000}k {k} +{o} ms' for r, k, o in T)}); the "
         f"{a.engine}, {C.voices(a.engine)} voices; the sound: hil_crackle HEAVY + " + ", ".join(notes)
         + f" ({len(ccs)} CCs), USB VOL FIXED; " + ("USB + the jack" if a.source == "both" else "USB only"),
         f"  calm: CC 116 every 0.25 s until {CALM_S:g} s without a flash write (at most {CALM_MAX_S:g} s)",
         f"  setup: CC {INV_CC} (INVERSION) to the other value; pattern: REC on stopped, note {STEP_NOTE} v{STEP_VEL}, "
         f"REC off, CC 120 + CC 10, the take {PREP_SETTLE_S:g} s later; undo: CC {UNDO_CC} (UNDO: that note back out, the pattern as it was)",
         f"  catch: CC 116 every ~4 ms from {FAST_FROM['setup']:g} / {FAST_FROM['pattern']:g} s after the change: the "
         f"first item 17 > 0 (deadline {a.deadline:g} s)",
         f"  attack OFFSET ms after the catch: the pedal, {C.voices(a.engine)}-note chords every 0.25 s (SPREAD, mid, "
         f"bass, high, SPREAD; v127 / v100), the bend every 5 ms for 1 s, INFINITE frozen at 0.6 s, off at 1.6 s, the "
         f"pedal up at 1.7 s; CC 116 at {', '.join(f'{x:g}' for x in POLLS_AFTER)} s",
         f"  gates: save seen, overlap (item 17 > 0 in the reply after the chord), the whole ISR in the save's window "
         f"and to the end <= {a.limit} us, 0 voices shed, USB underruns / missed / overruns 0, the stream at the rate, "
         f"0 events of {', '.join(C.GATED)} on USB and the jack, no rig fault",
         f"  the end: CC {INV_CC} = {a.inv_restore} (its save waited for); the pattern: every edit undone (its save "
         "waited for)",
         f"total ~{per * len(T) / 60 + 0.5:.1f} min (+ the host self-test first, ~1 min)"]
    return L


class Run:
    def __init__(self, a, dev):
        self.a, self.dev, self.md = a, dev, dev.mdev
        self.pending_undo = False
        self.inv = None

    def fastq(self, maxwait=0.04):
        """one CC 116 query, returned as soon as the reply is whole (item 17's value and a quiet 3 ms)"""
        md = self.md
        for _ in md.inp.iter_pending():
            pass
        md.cc(116, 127)
        got, t0, last, cur, have17 = [], time.monotonic(), None, None, False
        while time.monotonic() - t0 < maxwait:
            new = False
            for m in md.inp.iter_pending():
                if m.type == "control_change":
                    got.append((m.control, m.value))
                    new = True
                    if m.control == 100:
                        cur = m.value
                    elif m.control == 102 and cur == A.ITEM_OFF:
                        have17 = True
            now = time.monotonic()
            if new:
                last = now
            elif have17 and last and now - last > 0.003:
                break
            time.sleep(0.0005)
        return parse(got)

    def calm(self, clock, polls):
        """CC 116 every 0.25 s until CALM_S without a flash write: True, else False after CALM_MAX_S"""
        t0, quiet_from = clock(), clock()
        while clock() - t0 < CALM_MAX_S:
            p = self.fastq()
            polls.append((clock(), p))
            if p["items"].get(A.ITEM_OFF, 0) > 0:
                quiet_from = clock()
            elif clock() - quiet_from >= CALM_S and clock() - t0 >= 3.0:
                return True
            time.sleep(0.25)
        return False

    def send(self, kind, a, b):
        if kind == "bend":
            M = self.md.mido.Message
            with self.md.lock:
                self.md.out.send(M("pitchwheel", pitch=b - 8192))
        else:
            self.md.send(kind, a, b)

    def setup_sound(self):
        ccs, _ = sound_ccs(self.a.engine)
        self.md.cc(120, 0)
        for c, v in ccs:
            self.md.cc(c, v)
        time.sleep(0.3)
        self.md.cc(C.hil_scan.RESET_CC, 127)
        return ccs

    def take(self, rate, kind, off):
        a, dev, md = self.a, self.dev, self.md
        ccs = self.setup_sound()
        if kind == "setup":                                  # the prime: INVERSION to the value the change will not
            self.inv = 1 if self.inv in (None, 0) else 0     # set; its save (when it differs from the flash's) falls
            md.cc(INV_CC, 1 - self.inv)                      # in the calm. Without it the first take's change can
        pre = []                                             # equal the flash (48k-setup-0ms, 2026-10-25: no save)
        tz = time.monotonic()
        if not self.calm(lambda: time.monotonic() - tz, pre):
            return dict(name=f"{rate}-{kind}-{off}", verdict="FAIL", rows=[("calm", "a flash write every few s for "
                        f"{CALM_MAX_S:g} s", f"{CALM_S:g} s without one", "FAIL")])
        if kind == "pattern":
            md.cc(PLAY_CC, 0); md.cc(REC_CC, 127); time.sleep(0.05)
            md.send("on", STEP_NOTE, STEP_VEL); time.sleep(0.1); md.send("off", STEP_NOTE)
            self.pending_undo = True
            time.sleep(0.05); md.cc(REC_CC, 0); md.cc(120, 0); md.cc(C.hil_scan.RESET_CC, 127)
            time.sleep(PREP_SETTLE_S)                # (the silence in the device's output before the recording)
        keys = list(dev.cap.rings)
        st = dev.cap.now()
        t0 = time.monotonic()
        clock = lambda: time.monotonic() - t0               # noqa: E731
        polls, events = [], []
        time.sleep(0.2)
        t_change = clock()
        if kind == "setup":                                 # (the prime, before the calm, made it the other
            md.cc(INV_CC, self.inv)                          # value: the change always differs from the flash's)
            events.append((t_change, "cc", INV_CC, self.inv))
        elif kind == "undo":
            md.cc(UNDO_CC, 127)
            self.pending_undo = False
            events.append((t_change, "cc", UNDO_CC, 127))
        polls.append((clock(), self.fastq()))
        t_det = None
        while clock() - t_change < a.deadline:
            fast = clock() - t_change >= FAST_FROM[kind]
            p = self.fastq()
            polls.append((clock(), p))
            if p["items"].get(A.ITEM_OFF, 0) > 0 and polls[-1][0] > t_change:
                t_det = clock()
                break
            time.sleep(0.003 if fast else 0.25)
        t_chord = None
        if t_det is not None:
            while clock() < t_det + off / 1000.0:
                time.sleep(0.0005)
            if off:
                polls.append((clock(), self.fastq()))
            t_chord = clock()
            evs = attack(C.voices(a.engine), t_chord)
            sched = sorted([(t, "ev", e) for e in evs for t in (e[0],)] +
                           [(t_chord + d, "poll", None) for d in POLLS_AFTER], key=lambda x: x[0])
            for t, what, e in sched:
                while clock() < t:
                    time.sleep(0.0003)
                if what == "poll":
                    polls.append((clock(), self.fastq()))
                else:
                    self.send(e[1], e[2], e[3])
            events += evs
        dur = clock() + 0.1
        time.sleep(0.1)
        md.cc(120, 0); md.cc(121, 0); md.cc(64, 0)
        got, rig, rates = {}, [], {}
        for k in keys:
            ring = dev.cap.rings[k]
            got[k] = np.asarray(dev.cap.cut(k, st[k], dur), float) * 32768.0
            fr = ring.rate(st[k], dur)
            rates[k] = None if fr is None else fr / ring.sr - 1.0
            rig += [f"{k}: {f}" for f in ring.flags_between(st[k], st[k] + dur)]
            rig += [f"{k}: recorder gap {1000 * g:+.0f} ms at {x - st[k]:.2f} s" for x, g in ring.gaps
                    if st[k] <= x <= st[k] + dur]
        md.cc(C.hil_scan.RESET_CC, 127)
        u = got.get("usb")
        j = got["jack"][:, 0] if "jack" in got else None
        audio, info = None, {}
        if u is not None and t_chord is not None:
            audio, info = audio_events(u, j, rate, events, ccs, rates.get("usb"), rates.get("jack"))
            if a.source == "both" and j is None:
                audio.append(("jack", 0.0, "missing", "no jack recording"))
        res = judge(polls, rate, t_change, t_chord, a.limit, audio, rig, a.deadline)
        res.update(name=f"{rate // 1000}k-{kind}-{off}ms", kind=kind, offset_ms=off, rate=rate, info=info,
                   t_change=round(t_change, 3), t_chord=None if t_chord is None else round(t_chord, 3),
                   catch_s=None if t_det is None else round(t_det - t_change, 3),
                   polls=[(round(t, 4), p) for t, p in polls])
        if audio:
            os.makedirs(a.out, exist_ok=True)
            np.savez_compressed(os.path.join(a.out, f"{res['name']}-take.npz"), fs=rate, events=json.dumps(events),
                                ccs=json.dumps(ccs), usb=np.clip(np.round(u), -32768, 32767).astype(np.int16),
                                jack=np.zeros(0, np.int16) if j is None else
                                np.clip(np.round(j), -32768, 32767).astype(np.int16))
        res["rig_fault"] = bool(rig)
        return res

    def restore(self):
        """the player's data as it was: the pattern's edit undone (its save waited for), INVERSION at --inv-restore"""
        md, lines = self.md, []
        md.cc(120, 0); md.cc(C.hil_scan.RESET_CC, 127)
        if self.pending_undo:
            md.cc(REC_CC, 0); md.cc(UNDO_CC, 127)
            self.pending_undo = False
            lines.append("the pattern: the test's note undone (CC 44)")
        if self.inv is not None and self.inv != self.a.inv_restore:
            md.cc(INV_CC, self.a.inv_restore)
            lines.append(f"INVERSION (CC 57) back to {self.a.inv_restore}")
        if lines:
            tz = time.monotonic()
            saw = False
            while time.monotonic() - tz < 12.0:
                p = self.fastq()
                saw |= p["items"].get(A.ITEM_OFF, 0) > 0
                if saw and time.monotonic() - tz > 4.0:
                    break
                time.sleep(0.25)
            lines.append("its save seen" if saw else "NO save seen within 12 s: check by hand (power-cycle)")
        return lines


def report_text(results, a, restore, st_lines):
    v = "FAIL" if not results or any(r["verdict"] != "PASS" for r in results) else "PASS"
    wi = [r["isr_window"] for r in results if r.get("isr_window")]
    head = (f"hil_save_glitch: {v} ({len(results)} take(s); whole ISR in the save's window max "
            f"{max(wi) if wi else '-'} us, limit {a.limit})")
    L = [head] + st_lines[-1:]
    for r in results:
        L.append(f"take {r['name']}: {r['verdict']}" + (f" (caught {r['catch_s']} s after the change)"
                                                       if r.get("catch_s") is not None else ""))
        for c, val, k, s in r["rows"]:
            L.append(f"  {s:4}  {c}: {val}   [{k}]")
    L += [f"restore: {x}" for x in restore]
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rate", default="48000", choices=("48000", "44100", "both"))
    ap.add_argument("--offsets", default=",".join(map(str, OFFSETS_MS)))
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--engine", default="rhodes", choices=C.ENGINES)
    ap.add_argument("--limit", type=int, default=int(os.environ.get("CPU_LIMIT_US", LIMIT_US)))
    ap.add_argument("--deadline", type=float, default=DEADLINE_S)
    ap.add_argument("--inv-restore", type=int, default=0, choices=(0, 1, 2, 3))
    ap.add_argument("--source", default="both", choices=("both", "usb"))
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    a.offsets = [int(x) for x in a.offsets.split(",") if x.strip()]
    a.kinds = [k for k in a.kinds.split(",") if k.strip()]
    if any(k not in KINDS for k in a.kinds):
        ap.error(f"--kinds: {', '.join(KINDS)}")
    if a.dry_run:
        print("\n".join(plan(a)))
        print("DRY RUN: no lock, no MIDI, no audio, no host run")
        return 0
    if a.self_test:
        ok, _ = self_test(a.engine)
        return 0 if ok else 1
    print("\n".join(plan(a)), flush=True)
    st_lines = ["self-test: skipped (--no-self-test)"]
    if not a.no_self_test:
        ok, st_lines = self_test(a.engine)
        if not ok:
            print("hil_save_glitch: FAIL: the self-test failed (the detectors are not proven): no device run")
            return 1
    import lock
    results, restore = [], []
    with lock.Lock("DEVICE", who="hil_save_glitch", wait_s=3600):
        dev = None
        try:
            for rate in sorted({r for r, _, _ in take_list(a)}, reverse=True):
                if dev is not None:
                    dev.cap.close()
                    dev.mdev.close()
                dev = C.Device(rate, a.source == "both", a.jack_dev, a.jack_ch, blocksize=128, poll=False)
                run = getattr(main, "_run", None)
                if run is None:
                    run = main._run = Run(a, dev)
                run.dev, run.md = dev, dev.mdev
                for r_, kind, off in [t for t in take_list(a) if t[0] == rate]:
                    for attempt in (1, 2):
                        print(f"take {rate // 1000}k-{kind}-{off}ms ...", flush=True)
                        res = run.take(rate, kind, off)
                        if not res.get("rig_fault") or attempt == 2:
                            break
                        print("  the recorder slipped: again", flush=True)
                    print(f"take {res['name']}: {res['verdict']}" + "".join(
                        f"\n    {c}: {v}" for c, v, k, s in res["rows"] if s == "FAIL"), flush=True)
                    results.append(res)
        finally:
            run = getattr(main, "_run", None)
            if run is not None:
                restore = run.restore()
                print("\n".join(f"restore: {x}" for x in restore), flush=True)
            if dev is not None:
                dev.cap.close()
                dev.mdev.close()
    txt = report_text(results, a, restore, st_lines)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "report.txt"), "w").write("\n".join(txt) + "\n")
    json.dump(dict(args=vars(a), takes=results, restore=restore), open(os.path.join(a.out, "report.json"), "w"),
              indent=1, default=str)
    print("\n".join(txt))
    print(f"report: {os.path.join(a.out, 'report.txt')}")
    return 0 if txt[0].startswith("hil_save_glitch: PASS") and not any("NO save" in x for x in restore) else 1


if __name__ == "__main__":
    sys.exit(main())
