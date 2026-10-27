#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The monkey on the FM-1: tools/monkey.py's seeded random stream, its MIDI part, sent over USB-MIDI for N minutes
while the FM-1's USB audio is recorded, with the device's own glitch detector, its counters and a liveness probe.

    tools/hil_monkey.py [--minutes 10] [--seed N] [--rate 48000|44100] [--out build/hil/monkey] [--dry-run]

The stream: segments of 20 s, each one monkey seed (seed, seed + 1, ...): monkey.gen's MIDI lines only (notes on any
channel, chords, fast repeats, lost note-offs, the pedal, bend, the mod wheel, every CC, Program Change, MIDI clock
at a random tempo, Start / Stop / Continue / Song Position). The panel's keys, knobs and buttons have no MIDI path
and are left out, so are SysEx and undefined statuses (mido sends only valid messages) and the developer CCs that
change the test itself (DEV_CC: the CPU query 116, RAW 119, the key-velocity test 41, USB VOL 114, the reset 10,
the battery / screen / card-design ones 2, 3, 4, 8, 11, the mode cap 9, 42). USB VOL is held FIXED (CC 114 127), so
USB is the output before MASTER.
The stream's channels are moved by FRODHES_MIDI_CH - 1 (tools/fm1_midi.py rotate: its main channel 1 becomes 8 by
default), so an FM-1 on CHANNEL 8 or ALL hears it as the host does; the panic and the probe still cover all 16.

Each segment, one take (USB recorded at --rate, the device switched to it, as hil_must S1): the stream, then the
panic (all notes off, all sound off, pedal up, reset controllers, MIDI Stop on all 16 channels), 2 s, then the probe:
FX bypassed (CC 111 127) and VIBES' motor off (CC 47 0) on every channel, note 60 v100 on every channel for 0.6 s,
then the bypass off again. The gates, per take:
  counters  CC 116 every 0.5 s while it plays (tools/hil_must.py Poller, judged by hil_must.gate_counters): the
            replies come, the USB stream flows at --rate, underruns = missed = overruns = 0, the ring's low point
            >= one packet, the whole ISR <= 4500 us (hil_cpu's CC 116 / 117 and 107 / 108), 0 voices shed
  glitches  hil_crackle.usb_judge on the USB take (stress_max's repeat and stale detectors, the one definition the
            release judges USB audio with): 0 events. Full scale on USB (|x| >= 32767; the limiter keeps 24000):
            0 samples at 44.1 kHz, and at 48 kHz from 0.9.7 (usb.c's knee before the resampler's clamp, #38b);
            with --old-48k (a firmware before it: the resampler overshoots a loud high passage to full scale, #38)
            full scale is information at 48 kHz and it fails on a clamp that could be heard (clamp_rows: a run > 3
            samples, > 8 in 10 ms, or an isolated spike). Clicks are not gated: a random stream makes the parameter changes 0.9.5 still
            clicks on (private/BUGHUNT-095.md); hil_crackle's click detector counts them as information
  alive     the FM-1 answers a CC 116 query after the probe; the probe sounds on USB (> -50 dBFS, judged from the
            recorder's mark); the stream kept running (the frames recorded >= 98 % of the take's length)
Every take runs (--stop-on-fail: stop after the first failing one; a device that no longer answers stops the run
too). A failing take is saved: OUT/fail-<seed>.npz (USB from LEAD_S before the first message, the events, the
counters); a passing take with note-ons silent before its first sound as OUT/silent-<seed>.npz (to classify
them); the seed's stream on the host: python3 tools/monkey.py --seeds N (from a fresh boot: the FM-1 carries
the takes before it, so its sound differs).
A take that fails an audio check is triaged in the same run (triage, ~10 s): the host build plays the same seed and is
judged the same way, then one line in the report: HOST TOO (the host fails the same check: the test or the music),
COUNTED (a device counter explains it) or DEVICE ONLY (the event: mono / stereo, its dB evidence, the counters); the
host render is kept as OUT/host-<seed>.npz. Every take records the computer's 1-minute load before and after it.

--rejudge FILE.npz ...: the audio gates again on saved takes, offline (no MIDI, no audio; the counters as saved).

--dry-run: the plan (segments, seeds, messages per kind, the time) and a check of every message (mido builds it,
no DEV_CC); no MIDI, no audio. The main session runs it on the FM-1 (USB only: the jack is not recorded here).
Report: OUT/report.md + report.json."""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm1_midi  # noqa: E402
import monkey  # noqa: E402

ROOT = monkey.ROOT
OUT = os.path.join(ROOT, "build", "hil", "monkey")
SEG_S = monkey.RAND_MS / 1000.0
DEV_CC = {2, 3, 4, 8, 9, 10, 11, 41, 42, 114, 116, 119}
REALTIME = {0xF8, 0xFA, 0xFB, 0xFC}
PANIC_S, PROBE_AT, PROBE_LEN, TAIL_S = 0.3, 2.3, 0.6, 1.6
PROBE_DB = -50.0
LEAD_S = 0.02                                   # the take judged from this long before its first message (2026-10-26:
                                                # the whole recording, 0.76 s of it before the stream, the last take's
                                                # tail in it, gave the clock fit a lag of -0.010 s: the guards off)
CLIP = 32767
LAG_MAX = 0.1                                   # (s) the clock fit's lag past LEAD_S beyond which its anchor is
                                                # wrong (25 takes on the FM-1, 2026-10-26: -0.01 .. 0.05; 1012: 0.75)
LIM_T = 24000                                   # fx.c's limiter threshold (-2.7 dBFS)
CLAMP_RUN, CLAMP_DENSE, CLAMP_LOUD = 3, 8, 8    # the 48 kHz full-scale gate (clamp_rows)
STRICT48 = True                                 # 0.9.7 (usb.c's knee, #38b): 0 at full scale at 48 kHz too


def midi_of(line):
    """a monkey stream line -> the MIDI bytes to send, or None (the panel, a developer CC, SysEx / undefined)"""
    w = line.split()
    if w[0] != "midi":
        return None
    st, d1, d2 = int(w[1], 16), int(w[2]) & 127, int(w[3]) & 127
    hi = st >> 4
    if st in REALTIME:
        return [st]
    if st == 0xF2:
        return [st, d1, d2]
    if st >= 0xF0:
        return None
    if hi == 0xB and d1 in DEV_CC:
        return None
    if hi in (0xC, 0xD):
        return [st, d1]
    return [st, d1, d2]


def segment(seed):
    """one seed's MIDI part: [(t s, bytes)] sorted, t in 0..SEG_S"""
    ev = []
    for t, line in monkey.gen(seed):
        b = midi_of(line)
        if b is not None:
            ev.append((t / 1000.0, b))
    return sorted(ev, key=lambda e: e[0])


def panic_msgs():
    out = []
    for c in range(16):
        out += [[0xB0 | c, 64, 0], [0xB0 | c, 123, 0], [0xB0 | c, 120, 0], [0xB0 | c, 121, 0]]
    return out + [[0xFC]]


def probe_msgs(on):
    """the probe's set-up (on) or its end: FX bypass and the motor on every channel, the note on / off"""
    if on:
        return ([[0xB0 | c, 111, 127] for c in range(16)] + [[0xB0 | c, 47, 0] for c in range(16)])
    return [[0xB0 | c, 111, 0] for c in range(16)]


def judge_events(seg):
    """the take's events in hil_crackle's form ((t, kind, a, b): the note-ons and the CCs place its guards)"""
    out = []
    for t, b in seg:
        hi = b[0] >> 4
        if hi == 0x9 and len(b) == 3 and b[2]:
            out.append((t, "on", b[1], b[2]))
        elif hi in (0x8, 0x9):
            out.append((t, "off", b[1], 0))
        elif hi == 0xB:
            out.append((t, "cc", b[1], b[2]))
    t = SEG_S + PROBE_AT + 0.05
    out.append((t, "on", 60, 100))
    out.append((t + PROBE_LEN, "off", 60, 0))
    return out


def take_len():
    return SEG_S + PROBE_AT + PROBE_LEN + 0.05 + TAIL_S


def plan(minutes, seed):
    n = max(1, int(np.ceil(minutes * 60 / take_len())))
    return [seed + k for k in range(n)]


def kinds(seg):
    names = {0x8: "note off", 0x9: "note on", 0xA: "poly AT", 0xB: "CC", 0xC: "program", 0xD: "chan AT",
             0xE: "bend"}
    c = {}
    for _, b in seg:
        k = names.get(b[0] >> 4, {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop",
                                  0xF2: "song position"}.get(b[0], "?"))
        c[k] = c.get(k, 0) + 1
    return c


def dry_run(a):
    import mido
    seeds = plan(a.minutes, a.seed)
    tot, bad = {}, []
    for s in seeds:
        seg = segment(s)
        for t, b in seg:
            try:
                mido.Message.from_bytes(b)
            except Exception as e:
                bad.append((s, t, b, str(e)))
            if b[0] >> 4 == 0xB and b[1] in DEV_CC:
                bad.append((s, t, b, "a developer CC"))
            if not 0 <= t <= SEG_S:
                bad.append((s, t, b, "outside the segment"))
        for k, v in kinds(seg).items():
            tot[k] = tot.get(k, 0) + v
        print(f"seed {s}: {len(seg)} messages, " + ", ".join(f"{k} {v}" for k, v in sorted(kinds(seg).items())))
    for m in panic_msgs() + probe_msgs(True) + probe_msgs(False):
        mido.Message.from_bytes(m)
    print(f"\n{len(seeds)} takes of {take_len():.1f} s at {a.rate / 1000:g} kHz: {len(seeds) * take_len() / 60:.1f} min"
          f" (+ ~3 s set-up per take); {sum(tot.values())} messages: "
          + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())))
    print("gates per take: counters (hil_must.gate_counters), USB repeat / stale = 0 (hil_crackle.usb_judge), "
          "full scale = 0 (both rates; --old-48k: at 48 kHz no audible clamp run or spike), alive (CC 116 answers, "
          "the probe > -50 dBFS, frames >= 98 %)")
    if bad:
        print(f"BAD messages: {len(bad)}; the first: {bad[:3]}")
        return 1
    print("every message valid; no developer CC; no MIDI or audio opened (--dry-run)")
    return 0


def clamp_runs(c):
    """the lengths of the runs of clamped samples (|x| >= CLIP) in one channel"""
    m = np.concatenate(([0], (np.abs(c) >= CLIP).astype(np.int8), [0]))
    d = np.diff(m)
    return np.flatnonzero(d == -1) - np.flatnonzero(d == 1)


def clamp_rows(xi, rate, strict48=None):
    """the full-scale gate. 44.1 kHz: USB is the limiter's output (<= LIM_T 24000), so 0 samples at full scale.
    48 kHz: usb.c's 160/147 resampler (12 taps, a per-phase gain up to 1.667) overshoots between the samples of a
    loud high passage the limiter kept at 24000 and clamps it to int16 (#38: fail-1022, 1 sample, the burst 99 %
    at 6-12 kHz; 0.9.5 the same). Full scale is information there; the gate is a clamp that could be heard:
    a run of more than CLAMP_RUN clamped samples in a channel, more than CLAMP_DENSE in any 10 ms, or an isolated
    one (fewer than CLAMP_LOUD samples >= LIM_T within 1 ms: a spike, not the overshoot of a loud passage).
    From 0.9.7 (#38b) usb.c has a knee before the clamp (above 24576 the excess halved): a passage the limiter kept
    at 24000 can no longer reach full scale, so 0 samples at full scale is the gate at 48 kHz too (strict48,
    STRICT48; --old-48k judges a firmware before 0.9.7 as before: full scale information, the audible rows gate)"""
    x = np.asarray(xi).reshape(len(xi), -1)
    a = np.abs(x)
    nclip = int((a >= CLIP).sum())
    if rate != 48000:
        return [("USB at full scale (samples)", nclip, "0", "ok" if not nclip else "FAIL")]
    if strict48 is None:
        strict48 = STRICT48
    rows = [("USB at full scale (samples)", nclip, "0 (48 kHz: usb.c's knee, 0.9.7)", "ok" if not nclip else "FAIL")
            if strict48 else
            ("USB at full scale (samples)", nclip, "information at 48 kHz (the resampler's overshoot)", "info")]
    run = max([int(r.max()) for r in map(clamp_runs, x.T) if len(r)] or [0])
    rows.append(("USB clamped run (longest, samples)", run, f"<= {CLAMP_RUN}", "ok" if run <= CLAMP_RUN else "FAIL"))
    w = rate // 100
    hit = (a >= CLIP).any(axis=1).astype(np.int64)
    cs = np.concatenate(([0], np.cumsum(hit)))
    dense = int((cs[w:] - cs[:-w]).max()) if len(hit) >= w else int(hit.sum())
    rows.append(("USB clamped frames in 10 ms (most)", dense, f"<= {CLAMP_DENSE}",
                 "ok" if dense <= CLAMP_DENSE else "FAIL"))
    ms = rate // 1000
    loud = ((a >= LIM_T) & (a < CLIP)).sum(axis=1)
    cl = np.concatenate(([0], np.cumsum(loud)))
    iso = 0
    for k in np.flatnonzero(hit):
        lo, hi = max(0, k - ms), min(len(hit), k + ms + 1)
        iso += int(cl[hi] - cl[lo] < CLAMP_LOUD)
    rows.append(("USB isolated clamps (spikes)", iso, f"0 (< {CLAMP_LOUD} samples >= {LIM_T} within 1 ms)",
                 "ok" if not iso else "FAIL"))
    return rows


def saved_name(seed, rows, bad):
    """the file a take is kept in: fail-<seed>.npz when a row failed, silent-<seed>.npz when it passed with note-ons
    silent before its first sound (judge_audio's row: the audio is needed to classify them), else None"""
    if bad:
        return f"fail-{seed}.npz"
    if any(r[0].startswith("note-ons silent") for r in rows):
        return f"silent-{seed}.npz"
    return None


AUDIO_CHECKS = ("USB repeat / stale", "USB at full scale", "USB clamped", "USB isolated clamps", "the probe note",
                "frames recorded")
TRIAGE_COUNTERS = ("underruns", "missed", "overruns", "stalls", "sie_to")


def host_load():
    """the computer's 1-minute load average (a recorder starved by a busy host is the rig's problem, not the FM-1's)"""
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def event_detail(xi, rate, t, kind):
    """one glitch at t s (recording time) described: for a repeat the run of equal frames, mono or stereo (L == R over
    256 frames either side), and per channel how much smoother the wave is without the copies (hil_crackle's
    evidence, dB)"""
    import hil_crackle as hc
    q = np.asarray(xi, np.int64)
    f = int(round(t * rate))
    if kind != "repeat" or not 260 <= f < len(q) - 300:
        return f"{t:.3f} s {kind}"
    eq = np.all(q[1:] == q[:-1], axis=1)
    near = [i for i in range(f - 30, f + 31) if eq[i]]   # (t printed to 1 ms: +-22 frames)
    if not near:
        return f"{t:.3f} s repeat"
    p0 = min(near, key=lambda i: abs(i - f))
    while p0 > 0 and eq[p0 - 1]:
        p0 -= 1
    p1 = p0
    while eq[p1]:
        p1 += 1
    w = q[p0 - 256:p1 + 257]
    mono = bool((w[:, 0] == w[:, 1]).all())
    dbs = []
    for c in ([0] if mono else range(q.shape[1])):
        ea, eb, _ = hc._copy_evidence(q, p0, p1, c, 256, 4)
        dbs.append(f"ch{c} {10 * np.log10(max(ea, 1e-9) / max(eb, 1e-9)):.1f} dB")
    return (f"{t:.3f} s repeat, {p1 - p0 + 1} equal frames, {'mono' if mono else 'stereo'}, smoother without the "
            f"copies: {' / '.join(dbs)}")


def triage(seed, rows, xi, rate, ev, cnt, out=None, render=None, load=None):
    """a take that failed an audio check, classified in the same run: the host build plays the same seed (host_take,
    the device's lead) and is judged the same way (judge_audio, at the host's 44.1 kHz). Returns the report row or None
    (no audio check failed). The class:
      HOST TOO     the host fails the same check: the test or the music, not the device
      COUNTED      the host is clean and a device counter (underruns, missed, overruns, stalls, sie_to) explains it
      DEVICE ONLY  the host is clean, every counter 0: the event (mono / stereo, its dB evidence) and the counters
    out: the host render saved there as host-<seed>.npz (next to fail-<seed>.npz). render: host_take (tests)"""
    import re
    t0 = time.monotonic()
    bad = [r[0] for r in rows if r[3] == "FAIL" and r[0].startswith(AUDIO_CHECKS)]
    if not bad:
        return None
    if render is None:
        monkey.build()
    xh, evh = (render or host_take)(seed, lead_ms=int(1000 * LEAD_S))
    hrows = judge_audio(xh, 44100, evh)
    hbad = [r[0] for r in hrows if r[3] == "FAIL" and r[0].startswith(AUDIO_CHECKS)]
    if out:
        np.savez_compressed(os.path.join(out, f"host-{seed}.npz"), usb=np.clip(xh, -32768, 32767).astype(np.int16),
                            fs=44100, events=json.dumps(evh), lead=LEAD_S)
    u = (cnt or {}).get("usb") or {}
    ctr = ", ".join(f"{k} {u.get(k, 0)}" for k in TRIAGE_COUNTERS)
    same = [b for b in bad if b in hbad]
    if same:
        cls = f"HOST TOO -> test/music, not the device ({'; '.join(same)}: the host build fails it too)"
    elif any(u.get(k, 0) for k in TRIAGE_COUNTERS):
        cls = f"COUNTED ({'; '.join(bad)}; {ctr})"
    else:
        info = [r[1] for r in rows if r[0] == "the first glitches"]
        ev1 = re.match(r"([\d.]+) s (\w+)", str(info[0])) if info else None
        what = event_detail(xi, rate, float(ev1.group(1)), ev1.group(2)) if ev1 else "no glitch event"
        cls = f"DEVICE ONLY ({'; '.join(bad)}; {what}; {ctr})"
    if load is not None:
        cls += f"; host load {load}"
    return ("triage (the host build, the same seed)", f"{cls} [{time.monotonic() - t0:.0f} s]", "information", "info")


def rejudge(paths):
    """the audio gates on saved takes (fail-<seed>.npz): 0 when every take passes them now"""
    bad = 0
    for p in paths:
        d = np.load(p)
        rows = judge_audio(d["usb"].astype(np.int64), int(d["fs"]), json.loads(str(d["events"])),
                           lead=float(d["lead"]) if "lead" in d else LEAD_S)
        fails = [r for r in rows if r[3] == "FAIL"]
        bad += bool(fails)
        print(f"{os.path.basename(p)}: " + ("FAIL" if fails else "ok"))
        for r in rows:
            print(f"  {r[0]}: {r[1]} ({r[2]}) {r[3]}")
    return 1 if bad else 0


def first_sound_fit(xi, rate, ev, lead=LEAD_S):
    """hil_crackle.usb_judge, its clock fit anchored on the first note-on that sounds: (glitches, fit, the note-ons
    silent before the first sound). The fit takes the first sound for the first note-on's (onset_lag); a first note
    the FM-1 does not sound puts the whole take's clock off by the gap (2026-10-26, seed 1012 on 2771d0a and 880e780:
    the first note-on at 0.05 s silent on USB, the first sound the second note's at 0.79 s, the lag 0.746 s for
    ~0.03 s and the probe window 0.8 s late: -77.5 dBFS on the probe's release tail and silence). A lag more than
    LAG_MAX past the recording's lead: the note-ons more than 0.1 s before the first sound dropped, fitted again"""
    import hil_crackle as hc
    gl, fit = hc.usb_judge(xi, rate, ev)
    if fit["lag"] - lead <= LAG_MAX:
        return gl, fit, 0
    t1 = min(e[0] for e in ev if e[1] == "on") + hc.onset_lag(xi, ev, rate)    # (the first sound, recording time)
    ev2 = [e for e in ev if not (e[1] == "on" and e[0] + lead < t1 - 0.1)]
    silent = len(ev) - len(ev2)
    if not silent or not any(e[1] == "on" for e in ev2):
        return gl, fit, 0
    gl2, fit2 = hc.usb_judge(xi, rate, ev2)
    if fit2["lag"] - lead > LAG_MAX or fit2["lag"] - lead < -0.05:
        fit2 = dict(fit2, lag=lead, rate=0.0, how="the nominal clock (no plausible fit)")
    return gl2, dict(fit2, how=f"{fit2.get('how', '')}, from the first note that sounds"), silent


def judge_audio(xi, rate, ev, flags=0, lead=LEAD_S):
    """the audio gates of one take: xi (n, 2) int LSB at rate, from lead s before the first message; ev
    judge_events'; [(check, value, criterion, status)]. The probe is found on the recording's own clock
    (first_sound_fit: hil_crackle's clock fit from the onsets)"""
    import hil_crackle as hc
    rows = []
    gl, fit, silent = first_sound_fit(xi, rate, ev, lead)
    rows.append(("USB repeat / stale (hil_crackle.usb_judge)", len(gl), "0", "ok" if not gl else "FAIL"))
    if gl:
        rows.append(("the first glitches", "; ".join(f"{t:.3f} s {k}: {d}" for t, k, d in gl[:3]), "information",
                     "info"))
    rows += clamp_rows(xi, rate)
    p0 = fit["lag"] + (SEG_S + PROBE_AT + 0.05) * (1 + fit["rate"])     # (lag: from the recording's first frame)
    w = xi[max(0, int(p0 * rate)):max(0, int((p0 + PROBE_LEN) * rate))]
    pdb = monkey.rms_db(w) if len(w) else -200.0
    rows.append(("the probe note on USB (dBFS)", f"{pdb:.1f}", f"> {PROBE_DB:g}", "ok" if pdb > PROBE_DB else "FAIL"))
    need = int(0.98 * take_len() * rate)
    rows.append(("frames recorded", f"{len(xi)} of {int(take_len() * rate)}", ">= 98 %",
                 "ok" if len(xi) >= need and not flags else "FAIL"))
    if flags:
        rows.append(("the recorder's status flags", flags, "0 (a RIG problem, not the firmware)", "info"))
    try:
        ck = hc.det_click(xi, rate, hc._guard_mask(len(xi), rate, hc.guards(ev, (), clock=(fit["lag"], fit["rate"])),
                                                   0.0))
        rows.append(("clicks (hil_crackle det_click)", len(ck), "information (0.9.5's known clicks)", "info"))
    except Exception as e:                          # (information only: never stop the run on it)
        rows.append(("clicks (hil_crackle det_click)", f"not judged: {e!r}"[:120], "information", "info"))
    if silent:
        rows.append(("note-ons silent before the first sound", silent, "information (the clock fitted from the "
                     "first that sounds)", "info"))
    rows.append(("the clock", f"lag {fit['lag']:.3f} s, {1e6 * fit['rate']:.0f} ppm, {fit.get('how', '')}",
                 "information", "info"))
    return rows


def host_take(seed, lead_ms=500):
    """the host build plays one take as the FM-1 would get it (the stream, the panic, the probe): (xi (n, 2) at
    44.1 kHz, ev); lead_ms of silence first, as a recorder opened before the first message"""
    seg = segment(seed)
    lines = [(lead_ms + int(round(1000 * t)), "midi " + " ".join(f"0x{v:02X}" if k == 0 else str(v) for k, v in
                                                                 enumerate((b + [0, 0])[:3]))) for t, b in seg]

    def at(t, msgs):
        lines.extend((lead_ms + int(round(1000 * t)), "midi " + " ".join(f"0x{v:02X}" if k == 0 else str(v)
                                                                          for k, v in enumerate((m + [0, 0])[:3])))
                     for m in msgs)
    at(SEG_S + PANIC_S, panic_msgs())
    at(SEG_S + PROBE_AT, probe_msgs(True))
    at(SEG_S + PROBE_AT + 0.05, [[0x90 | c, 60, 100] for c in range(16)])
    at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN, [[0x80 | c, 60, 0] for c in range(16)])
    at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN + 0.8, probe_msgs(False))
    r = monkey.run(monkey.script_text([(1, "midi 0xB0 114 127")] + lines, (), lead_ms + int(1000 * take_len())))
    return r.x.astype(np.int64), judge_events(seg)


# ---- the device run ------------------------------------------------------------------------------------------------
def run_take(dev, sd, usb, a, s):
    import hil_must as hm
    seg = segment(s)
    rec = hm.Rec(usb, a.rate, 2, (0, 1), take_len() + 5.0, switch_rate=True, sd=sd)
    rows = []
    try:
        time.sleep(0.5)
        M = dev.mido.Message
        raw = getattr(dev, "raw", dev.out)           # its own channels (notes on any channel), rotated
        with hm.Poller(dev) as acc:
            mark = rec.mark()                   # (the take is cut here: the stream's clock)
            t0 = time.monotonic()
            for t, b in seg:
                while time.monotonic() - t0 < t:
                    time.sleep(0.0005)
                with dev.lock:
                    raw.send(M.from_bytes(fm1_midi.rotate(b)))   # the stream's channels, moved to FRODHES_MIDI_CH

            def at(t, msgs):
                while time.monotonic() - t0 < t:
                    time.sleep(0.001)
                with dev.lock:
                    for m in msgs:
                        raw.send(M.from_bytes(fm1_midi.rotate(m)))
            at(SEG_S + PANIC_S, panic_msgs())
            at(SEG_S + PROBE_AT, probe_msgs(True))
            at(SEG_S + PROBE_AT + 0.05, [[0x90 | c, 60, 100] for c in range(16)])
            at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN, [[0x80 | c, 60, 0] for c in range(16)])
            at(SEG_S + PROBE_AT + 0.05 + PROBE_LEN + 0.8, probe_msgs(False))
            at(take_len(), [])
        alive = hm.parse_reply(dev.query(0.5))
    finally:
        x = rec.stop()
    xi = np.round(np.asarray(x, float) * 32768).astype(np.int64)
    xi = xi[max(0, int((mark - LEAD_S) * a.rate)):]   # (from the first message: the fit's first sound is near it)
    rows += hm.gate_counters(acc, a.rate)
    rows.append(("CC 116 answers after the probe", "yes" if "engine_us" in alive else "no", "yes",
                 "ok" if "engine_us" in alive else "FAIL"))
    ev = judge_events(seg)
    rows += judge_audio(xi, a.rate, ev, rec.flags)
    rows.append(("the recorder's mark", f"{mark:.3f} s", f"information (the take judged from {LEAD_S} s before it)",
                 "info"))
    return rows, xi, ev, acc.summary()


def alive_ok(dev):
    """the FM-1 still answers a CC 116 query (a failed liveness row on a device that answers: the run goes on)"""
    import hil_must as hm
    return "engine_us" in hm.parse_reply(dev.query(0.5))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--rate", type=int, default=48000, choices=(44100, 48000))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop-on-fail", action="store_true", help="stop after the first failing take")
    ap.add_argument("--rejudge", nargs="+", metavar="NPZ", help="judge saved takes again (offline)")
    ap.add_argument("--old-48k", action="store_true", help="a firmware before 0.9.7 (no knee in usb.c): full scale "
                    "at 48 kHz is information, only an audible clamp fails")
    a = ap.parse_args(argv)
    global STRICT48
    STRICT48 = not a.old_48k
    if a.rejudge:
        return rejudge(a.rejudge)
    if a.dry_run:
        return dry_run(a)
    import sounddevice as sd
    import hil_must as hm
    dev = hm.Dev()
    usb = hm.find_usb(sd)
    if usb is None:
        print("no FM-1 USB audio input")
        return 2
    os.makedirs(a.out, exist_ok=True)
    dev.cc(114, 127)                                # USB VOL fixed: USB is the output before MASTER
    dev.send("pc", 0)
    time.sleep(1.0)
    report, verdict = [], "PASS"
    try:
        for s in plan(a.minutes, a.seed):
            load0 = host_load()
            rows, xi, ev, cnt = run_take(dev, sd, usb, a, s)
            load1 = host_load()
            rows.append(("host load (1 min) before / after", f"{load0} / {load1}", "information", "info"))
            bad = [r for r in rows if r[3] == "FAIL"]
            if bad:
                try:
                    tri = triage(s, rows, xi, a.rate, ev, cnt, a.out, load=f"{load0} / {load1}")
                except Exception as e:                  # (information: never stop the run on it)
                    tri = ("triage (the host build, the same seed)", f"not run: {e!r}"[:160], "information", "info")
                if tri:
                    rows.append(tri)
                    print(f"  {tri[1]}", flush=True)
            report.append(dict(seed=s, rows=[list(map(str, r)) for r in rows], counters=cnt, load=[load0, load1]))
            print(f"{time.strftime('%H:%M:%S')} seed {s}: " + ("FAIL " + "; ".join(f"{r[0]} {r[1]}" for r in bad)
                                                                if bad else "ok"), flush=True)
            keep = saved_name(s, rows, bad)
            if keep:                            # (a take with silent note-ons too: kept to classify them)
                np.savez_compressed(os.path.join(a.out, keep),
                                    usb=np.clip(xi, -32768, 32767).astype(np.int16), fs=a.rate, events=json.dumps(ev),
                                    counters=json.dumps(cnt, default=str), lead=LEAD_S)
            if bad:
                verdict = "FAIL"
                if a.stop_on_fail or any(r[0].startswith(("CC 116 answers", "frames recorded", "the probe"))
                                         for r in bad) and not alive_ok(dev):
                    break
    finally:
        dev.close()
    js = dict(verdict=verdict, date=time.strftime("%Y-%m-%d %H:%M"), rate=a.rate, minutes=a.minutes, takes=report)
    json.dump(js, open(os.path.join(a.out, "report.json"), "w"), indent=1, default=str)
    md = [f"# hil_monkey: {verdict}", "", f"- {js['date']}, {len(report)} takes of {take_len():.1f} s, USB at "
          f"{a.rate / 1000:g} kHz, seeds from {a.seed}", "", "| seed | check | value | criterion | result |",
          "|---|---|---|---|---|"]
    md += [f"| {t['seed']} | {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for t in report for r in t["rows"]]
    open(os.path.join(a.out, "report.md"), "w").write("\n".join(md) + "\n")
    print(f"hil_monkey {verdict}: {a.out}/report.md")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
