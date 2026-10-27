#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""T1, the must-pass tier of the release test plan (private TEST-PLAN v2): six real-use scenarios on the FM-1, judged
first by the firmware's own counters (CC 116: USB underruns / missed / overruns, the ring's true low point, the whole
audio ISR, the ISR entry spacing, voices shed; read every 0.5 s), then by the audio (USB: repeated / stale frames by
tools/hil_crackle.py's own repeat and stale detectors, hil_crackle.usb_judge, the one definition T1 judges USB audio
with; clicks outside the note-on guards, on USB and on the headphone jack). Every case with an audio event keeps its
whole take (build/hil/must/takes/<scenario>-<case>.npz: USB, the jack, the events, the panel), to judge it again on
the host (--rejudge). Run through hil_scan:

    tools/hil_scan.py --tier must [--fwsc PKG [--prev OLDPKG]] [--scenarios S1,S3] [--engines rhodes,vibes]
                      [--rates 48000,44100] [--jack-dev M6] [--jack-ch 5] [--cpu-runs 3] [--dry-run]
    tools/hil_scan.py --tier must --rejudge build/hil/must/takes/X.npz ...   saved takes judged again (no FM-1)
    tools/hil_scan.py --tier full [hil_scan options]      T2: every classic suite, then --vibes and --marimba

S1 DAW recording   USB open at 48 and 44.1 kHz (blocksize 128, the device switched to that rate), every engine:
                   every effect heavy (pre-delay 127, drive mix 64, reverb decay 127), the pedal (re-pressed every
                   20 s), all voices, chords every 0.15-0.4 s across registers at v100 / v127; then the user's recipe
                   (dry, notes added one by one to 9, then to 12, under the pedal; then with moderate effects).
                   When workstream B's `stress_max` is in hil_scan's TESTS it runs instead of the built-in stimulus
                   (called by name, per rate; the counters are polled around it all the same).
S2 live on jack    no USB stream (the FM-1 connected, nobody reading its audio): the same stimulus, the jack
                   recorded through the interface; then the idle floor (USB: exact digital zero; jack: its level).
S3 switching       F01..F16 by program change and TYPE (ST77 SUIT FELT DYNO VIBES MARIMBA) while a chord is held
                   under the pedal, struck again after each switch; then F01, release: exact zero within 10 s (no
                   stuck note). Clicks inside a switch's 300 ms (the designed fade) are information, outside a FAIL.
S4 controls        every parameter CC (params.c, but TYPE) swept 0 -> 127 and back to its default under a held
                   chord + pedal, on every engine.
S5 update          (with --fwsc) F03 loaded and its sound edited on the running firmware (--prev: install the
                   previous release first, as a user would have it), the package installed over USB; after the reboot
                   the same note must match one of the factory presets F01..F19 loaded on the new firmware (the boot
                   plays a stored preset, not the edits: 0.9.5's design; which one is the old firmware's record:
                   information; matching none is a broken boot or migration). The OCT- rescue is a hands-on step, listed as MANUAL. After every install the MIDI port and the USB input
                   are waited for, PortAudio restarted and the devices found by name again (the FM-1 re-enumerates);
                   if S5 fails with the FM-1 not on --fwsc, --fwsc is installed again (a FAIL either way); if that
                   fails too, the later scenarios FAIL unrun and the hand-install command is printed.
S6 CPU             tools/hil_cpu.py --worst --usb 48000 per engine, --cpu-runs times: worst <= 4500 us, the whole ISR
                   <= 4500 us, 0 shed, its USB counters clean.

Gates (every case): the counters must answer (a firmware before the USB ring fix has none: FAIL), the stream state
must be what the scenario set up, underruns = missed = overruns = 0, the ring's low point >= one packet (45 / 48
frames), the whole ISR <= 4500 us, 0 voices shed; the audio: 0 repeat / stale events (hil_crackle's) on USB, 0 clicks
outside the guards on USB and the jack. The jack is optional (no interface: the jack rows SKIP and S2 is
incomplete).

Report: build/hil/must/report.md + report.json (rows per scenario and case, the counters of every case). Exit 0 =
every gating row passed and nothing gating was skipped; 1 = a FAIL; 2 = no FAIL but incomplete (a SKIP: no jack
interface, no --fwsc). --dry-run prints the plan and its time (the default plan stays under 20 min:
tests/py/test_hil_must.py) and opens no MIDI / audio.
"""
import argparse
import inspect
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_crackle as hc  # noqa: E402  (T1 judges USB audio with stress_max's detectors)
import hil_scan as hs  # noqa: E402
from hil_cpu import USB_ITEMS, usb_merge  # noqa: E402

ROOT = hs.ROOT
OUT = os.path.join(ROOT, "build", "hil", "must")
PY = os.path.join(ROOT, ".local", "venv", "bin", "python")
ENGINES = ("rhodes", "vibes", "marimba")
RATES = (48000, 44100)
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6")
TITLES = dict(S1="DAW recording: stress_max (USB stream, every effect heavy, the user's recipe)",
              S2="live on the jack, no USB stream; the idle floor",
              S3="preset and TYPE switching while notes ring",
              S4="every control swept under load",
              S5="update over USB: the setup survives; OCT- rescue",
              S6="CPU per engine (hil_cpu --worst --usb 48000)")
ISR_LIMIT_US = 4500
POLL_S = 0.5
BUDGET_S = 20 * 60                                   # T1 must run in <= 20 min (the plan)
NV = 9                                               # voices per engine (B may set Rhodes to 8: more notes steal)

# ---- the panel ---------------------------------------------------------------------------------------------------
S5_PC = 2                                             # S5: F03 loaded on the old firmware (not the default F01)
S5_FACTORY_PCS = tuple(range(16)) + (32, 33, 34)     # F01..F16, F17..F19 (presets.c preset_of_pc)
ENGINE_SEL = dict(rhodes=[(80, hs.type_cc(0))], vibes=[(79, hs.type_cc(4))], marimba=[(79, hs.type_cc(5))])
HEAVY = dict(DRIVE=127, CMP_SW=127, CMP_AMT=100, TW_SW=127, TW_DEPTH=127, PH_SW=127, PH_DEPTH=127, PH_FDBK=127,
             CP_SW=64, CP_DEPTH=127, DL_SW=80, DL_DEPTH=127, DL_TIME=127, DL_FDBK=127, REV=127,
             FL_TYPE=45, FL_FREQ=60, FL_RESO=100, FL_LFO=64)          # hil_cpu's heavy (the filter LP18 + its LFO)
HEAVY_CC = {94: 127, 83: 64, 92: 127}                 # + RV_PREDLY 127, DRV_MIX 64, RV_DECAY 127 (B's worst case)
MODERATE = dict(DRIVE=30, CP_SW=64, CP_DEPTH=50, PH_SW=127, PH_DEPTH=60, DL_SW=48, DL_DEPTH=40, REV=40)


def panel_msgs(engine, fx=None, raw=None):
    """the CC messages that set a known panel: CC 120, every parameter at its default, the DRY dict + fx (CC 85
    before the phaser's CCs), raw {cc: v}, then the engine (VIBES: its motor on with the effects, as hil_cpu)"""
    m = [(120, 0)] + list(hs.param_defaults())
    d = dict(hs.DRY)
    d.update(fx or {})
    if d.get("CP_SW"):
        m.append((hs.CC["CP_SW"], d.pop("CP_SW")))
    m += [(hs.CC[k], v) for k, v in d.items()]
    m += sorted((raw or {}).items())
    m += ENGINE_SEL[engine]
    if engine == "vibes":
        m.append((47, 127 if fx else 0))
    return m


# ---- the stimuli (pure: [(t_s, kind, a, b)], kind on / off / cc / pc) ---------------------------------------------
REGS = dict(bass=[28, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48], mid=[48, 52, 55, 59, 60, 62, 64, 67, 69, 71, 72, 74],
            high=[67, 71, 72, 74, 76, 79, 81, 83, 84, 86, 88, 91],
            spread=[28, 36, 43, 48, 55, 60, 64, 67, 72, 76, 84, 91])
RECIPE = [36, 48, 55, 60, 64, 67, 72, 76, 79, 43, 84, 88]      # added one by one: 9 notes, then 12


def stim_worst(seconds, nv=NV, seed=1):
    """B's (a): the pedal down (re-pressed every 20 s), nv-note chords every 0.15-0.4 s across bass / mid / high /
    spread at v100 / v127, each released just before the next (they ring on under the pedal)"""
    rng = np.random.default_rng(seed)
    ev, t, k, prev = [(0.0, "cc", 64, 127)], 0.1, 0, []
    regs = list(REGS)
    next_pedal = 20.0
    while t < seconds - 0.5:
        notes = REGS[regs[k % len(regs)]][:nv]
        for n in prev:
            ev.append((t - 0.005, "off", n, 0))
        for n in notes:
            ev.append((t, "on", n, 127 if k % 2 else 100))
        prev, k = notes, k + 1
        t += float(rng.uniform(0.15, 0.4))
        if t >= next_pedal:
            ev += [(t - 0.12, "cc", 64, 0), (t - 0.02, "cc", 64, 127)]
            next_pedal += 20.0
    ev += [(t, "off", n, 0) for n in prev] + [(t + 0.05, "cc", 64, 0)]
    return sorted(ev, key=lambda e: e[0])


def stim_recipe(seconds, step=0.35):
    """B's (b), the user's recipe: under the pedal, notes added one by one to 9, held, then to 12, held, all released,
    the pedal up; again until seconds"""
    ev, t = [], 0.0
    while t + 12 * step + 2.5 <= seconds:
        ev.append((t, "cc", 64, 127))
        for i, n in enumerate(RECIPE):
            ev.append((t + 0.05 + i * step + (0.8 if i >= 9 else 0.0), "on", n, 100))
        t += 0.05 + 12 * step + 0.8 + 1.0
        ev += [(t, "off", n, 0) for n in RECIPE] + [(t + 0.05, "cc", 64, 0)]
        t += 0.6
    return sorted(ev, key=lambda e: e[0])


SWITCH_CHORD = (48, 55, 60, 64, 67)
TYPE_SEQ = [(80, hs.type_cc(t)) for t in range(4)] + [(79, hs.type_cc(4)), (79, hs.type_cc(5)), (80, hs.type_cc(0))]


def stim_switch(dwell=0.8, n_presets=16, type_cycles=2):
    """S3: the chord held under the pedal; F01..F16 by program change, then the TYPEs, each followed 0.15 s later by
    the chord struck again; then F01, the chord, everything released. Returns (events, switch times, release time)"""
    ev, sw, t = [(0.0, "cc", 64, 127)] + [(0.05, "on", n, 100) for n in SWITCH_CHORD], [], 0.6
    steps = [("pc", p, 0) for p in range(n_presets)] + [("cc", c, v) for _ in range(type_cycles) for c, v in TYPE_SEQ]
    steps += [("pc", 0, 0)]
    for kind, a, b in steps:
        ev.append((t, kind, a, b))
        sw.append(t)
        ev += [(t + 0.15, "on", n, 100) for n in SWITCH_CHORD]
        t += dwell
    ev += [(t, "off", n, 0) for n in SWITCH_CHORD] + [(t + 0.05, "cc", 64, 0)]
    return sorted(ev, key=lambda e: e[0]), sw, t + 0.05


SWEEP_SKIP = {80}                                      # TYPE: S3 switches it (a sweep would cross every engine)


def stim_sweep(params=None, per=0.45, steps=8, restrike=2.0):
    """S4: a chord held under the pedal (struck again every restrike s: MARIMBA's bars ring ~1-2 s); each parameter's
    CC from 0 to 127 in steps over 0.3 s, then back to its default. Returns (events, the swept CCs)"""
    params = [(c, v) for c, v in (params if params is not None else hs.param_defaults()) if c not in SWEEP_SKIP]
    ev = [(0.0, "cc", 64, 127)] + [(0.05, "on", n, 100) for n in SWITCH_CHORD]
    t = 0.4
    for c, de in params:
        for i in range(steps):
            ev.append((t + i * 0.3 / steps, "cc", c, round(127 * (i + 1) / steps)))
        ev.append((t + 0.33, "cc", c, de))
        t += per
    end = t
    k = restrike
    while k < end:
        ev += [(k - 0.01, "off", n, 0) for n in SWITCH_CHORD] + [(k, "on", n, 100) for n in SWITCH_CHORD]
        k += restrike
    ev += [(end, "off", n, 0) for n in SWITCH_CHORD] + [(end + 0.05, "cc", 64, 0)]
    return sorted(ev, key=lambda e: e[0]), [c for c, _ in params]


def dur_of(events, tail=1.0):
    return (max(e[0] for e in events) if events else 0.0) + tail


# ---- the counters (CC 116 replies) --------------------------------------------------------------------------------

def parse_reply(msgs):
    """one CC 116 reply [(control, value)] -> {engine_us, shed, isr_us, tail_us, usb: {name: value}}: the profiler
    (CC 116/117 us / 4, CC 115 voices shed mod 128), the whole ISR (CC 108/107 us / 4), the tail (110/109), the USB
    items (CC 100 id, 101 / 102 value; hil_cpu USB_ITEMS)"""
    got, usb, cur = {}, {}, dict(id=None, msb=0)
    for c, v in msgs:
        if c in (115, 116, 117, 118, 107, 108, 109, 110, 112, 113):
            got[c] = v
        elif c == 100:
            cur["id"] = v
        elif c == 101:
            cur["msb"] = v
        elif c == 102 and cur["id"] in USB_ITEMS:
            usb[USB_ITEMS[cur["id"]]] = cur["msb"] << 7 | v
    r = dict(usb=usb)
    if {116, 117} <= got.keys():
        r["engine_us"] = (got[116] << 7 | got[117]) * 4
    if 115 in got:
        r["shed"] = got[115]
    if {107, 108} <= got.keys():
        r["isr_us"] = (got[108] << 7 | got[107]) * 4
    if {109, 110} <= got.keys():
        r["tail_us"] = (got[110] << 7 | got[109]) * 4
    return r


class Acc:
    """a case's counters over every 0.5 s reply: the USB items merged as hil_cpu (sums, minima, maxima), the whole ISR
    and engine maxima, the voices shed (the mod-128 counter's increments), the replies without USB items"""

    def __init__(self):
        self.usb, self.isr_us, self.engine_us, self.tail_us = {}, 0, 0, 0
        self.shed, self.replies, self.no_usb, self.last_shed = 0, 0, 0, None
        self.isr_trace = []

    def add(self, r):
        if "engine_us" not in r:
            return
        self.replies += 1
        self.engine_us = max(self.engine_us, r["engine_us"])
        if "isr_us" in r:
            self.isr_us = max(self.isr_us, r["isr_us"])
            self.isr_trace.append(r["isr_us"])
        self.tail_us = max(self.tail_us, r.get("tail_us", 0))
        if "shed" in r:
            if self.last_shed is not None:
                self.shed += (r["shed"] - self.last_shed) % 128
            self.last_shed = r["shed"]
        if r["usb"]:
            usb_merge(self.usb, r["usb"])
        else:
            self.no_usb += 1

    def summary(self):
        return dict(replies=self.replies, replies_without_usb=self.no_usb, isr_max_us=self.isr_us,
                    engine_max_us=self.engine_us, tail_max_us=self.tail_us, shed=self.shed, usb=dict(self.usb))


def packet(rate):
    return -(-rate // 1000)                           # frames in one 1 ms packet: 45 at 44.1 kHz, 48 at 48 kHz


def gate_counters(acc, stream_rate):
    """the primary gate: [(check, value, criterion, status)] for one case. stream_rate: the USB stream the scenario
    holds open (44100 / 48000), or None (no stream: S2)"""
    s, rows = acc.summary(), []
    u = s["usb"]

    def row(check, value, crit, ok, info=False):
        rows.append((check, value, crit, "info" if info else "ok" if ok else "FAIL"))
    if not s["replies"]:
        row("counters answer", "no reply", "CC 116 answers every 0.5 s", False)
        return rows
    have = bool(u) and "underruns" in u
    row("USB counters answer", f"{s['replies'] - s['replies_without_usb']}/{s['replies']} replies" if have else
        "none (a firmware before the USB ring fix)", "every reply carries them", have and not s["replies_without_usb"])
    if have:
        st = u.get("state", 0)
        flowing, r48 = bool(st & 2), bool(st & 4)
        if stream_rate:
            ok = flowing and r48 == (stream_rate == 48000)
            row("USB stream state", f"{'flowing' if flowing else 'not flowing'}, {'48' if r48 else '44.1'} kHz",
                f"flowing at {stream_rate / 1000:g} kHz", ok)
        else:
            row("USB stream state", "flowing" if flowing else "no stream", "no stream (nothing reads the USB audio)",
                not flowing)
        for k in ("underruns", "missed", "overruns"):
            row(f"USB {k}", u.get(k, 0), "0", u.get(k, 0) == 0)
        if "sie_to" in u:                       # (0.9.7 on: a timed-out SIE access in the EP4 poll, CC 116 item 21)
            row("USB SIE timeouts (EP4 polls)", u["sie_to"], "0", u["sie_to"] == 0)
        if stream_rate:
            lo = u.get("fill_low")
            row("USB ring low point (frames)", lo, f">= {packet(stream_rate)} (one packet)",
                lo is not None and lo >= packet(stream_rate))
        row("USB adj up / down, stalls", f"{u.get('adj_up', 0)} / {u.get('adj_down', 0)}, {u.get('stalls', 0)}",
            "information (the rate adaptation)", True, info=True)
        if "per_min" in u:
            row("ISR entry spacing (us)", f"{u.get('per_min')} .. {u.get('per_max')}", "information (5805 nominal)",
                True, info=True)
    if s["isr_max_us"] or acc.isr_trace:
        row("whole ISR max (us)", s["isr_max_us"], f"<= {ISR_LIMIT_US}", s["isr_max_us"] <= ISR_LIMIT_US)
    else:
        row("whole ISR max (us)", "no reply", f"<= {ISR_LIMIT_US} (CC 108/107)", False)
    row("voices shed", s["shed"], "0", s["shed"] == 0)
    return rows


# ---- the audio (secondary) ----------------------------------------------------------------------------------------

USB_ROW = "repeated / stale frames (hil_crackle repeat + stale)"


def usb_rows(src, xi, sr, events, offset, ccs=()):
    """the USB stream's own glitches, judged by hil_crackle.usb_judge (stress_max's repeat and stale detectors: the
    run between two steps the same way with the smoother-without-the-copies test, the stale block with the seam rule;
    the recording's own clock): the one definition T1 judges USB audio with (S1 is stress_max itself). The old
    'exact repeated frames on a slope' here (2 equal frames, a 40 LSB move across them) counted the music's own
    turns: 1-2 in S4 VIBES on the FM-1 (every counter clean), 2 in the host render of the same sweep, both in the
    chord's re-strikes (2.048 s: steps +5 / 0 / +48; 24.0 s: +7 / 0 / +62), 0 by hil_crackle's (2026-10-24).
    xi (n, 2) LSB; events on the event clock (offset: the recorder's mark, information: the fit starts from the
    take's first sound)"""
    ev, fit = hc.usb_judge(np.round(np.asarray(xi, float)), sr, events, ccs)
    rows = [(f"{src}: {USB_ROW}", len(ev), "0", "ok" if not ev else "FAIL")]
    if ev:
        rows.append((f"{src}: the first events", "; ".join(f"{t:.3f} s {k}: {d}" for t, k, d in ev[:3]),
                     f"information (the clock: lag {fit['lag']:.3f} s, mark {offset:.3f} s, {fit['how']})", "info"))
    return rows


def guard_mask(n, sr, times, before=0.010, after=0.060, offset=0.0):
    """True where a sample lies in an event's guard (s before / after each time, shifted by offset s: the recording
    starts before the event clock and the sound comes ~10 ms after the MIDI)"""
    m = np.zeros(n, bool)
    for t in times:
        a, b = int((t + offset - before) * sr), int((t + offset + after) * sr)
        m[max(0, a):max(0, min(n, b))] = True
    return m


def click_idx(x):
    """hil_scan.clicks()'s detector, the sample indices (second-difference spikes far above the local level)"""
    m = x.mean(axis=1) if x.ndim == 2 else x
    m = np.asarray(m, float)
    if len(m) < 3:
        return np.zeros(0, int)
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(480) / 480, "same")[1:-1] + 50
    return np.flatnonzero(d2 > 6 * lvl + 2000) + 1


def audio_rows(src, x, sr, events, offset, switch_times=(), switch_guard=0.3, repeats_gate=True, clicks_gate=True,
               ccs=()):
    """the secondary rows of one recording: repeated / stale frames (USB only: hil_crackle's detectors, usb_rows),
    clicks outside the note-on guards (and the switches' fade windows: information inside)"""
    rows = []
    if x is None:
        return [(f"{src}: recorded", "no", "the source records", "SKIP")]
    xi = np.asarray(x, float) * (32768 if np.issubdtype(np.asarray(x).dtype, np.floating) else 1)
    if repeats_gate:
        rows += usb_rows(src, xi if xi.ndim == 2 else np.stack([xi, xi], 1), sr, events, offset, ccs)
    ck = click_idx(xi)
    on = [e[0] for e in events if e[1] == "on"]
    g = guard_mask(len(xi), sr, on, offset=offset)
    sw = guard_mask(len(xi), sr, switch_times, before=0.01, after=switch_guard, offset=offset)
    out = [i for i in ck if not g[i] and not sw[i]]
    ins = [i for i in ck if sw[i] and not g[i]]
    rows.append((f"{src}: clicks outside the guards", len(out), "0",
                 ("ok" if not out else "FAIL") if clicks_gate else "info"))
    if len(switch_times):
        rows.append((f"{src}: clicks inside the switches' {int(switch_guard * 1000)} ms", len(ins),
                     "information (the designed fade)", "info"))
    pk = float(np.abs(xi).max()) if len(xi) else 0.0
    rows.append((f"{src}: peak", f"{20 * np.log10(pk / 32768 + 1e-12):.1f} dBFS", "information", "info"))
    return rows


def has_event(rows):
    """an audio row counted something (USB repeated / stale frames, clicks outside the guards, gated or not)"""
    return any((r[0].endswith(USB_ROW) or r[0].endswith("clicks outside the guards")) and isinstance(r[1], int)
               and r[1] > 0 for r in rows)


def _i16(x):
    return np.clip(np.round(np.asarray(x, float) * 32768), -32768, 32767).astype(np.int16)


def save_take(sc, name, xu, fs_usb, xj, fs_jack, events, ccs, mu, mj, switch_times=(), out=None):
    """the whole take (as hil_crackle keeps one with an event): USB (n, 2) and the jack int16 LSB, their rates, the
    events, the panel's CCs and the recorders' marks, in OUT/takes/<sc>-<case>.npz; rejudge() reads it back"""
    d = os.path.join(out or OUT, "takes")
    os.makedirs(d, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-." else "-" for c in f"{sc}-{name}")[:80].strip("-")
    p = os.path.join(d, f"{slug}.npz")
    np.savez_compressed(p, scenario=sc, case=name, fs_usb=fs_usb or 0, fs_jack=fs_jack or 0, mark_usb=mu,
                        mark_jack=mj, events=json.dumps(events), ccs=json.dumps(ccs),
                        switch_times=np.asarray(switch_times, float),
                        usb=np.zeros((0, 2), np.int16) if xu is None else _i16(xu),
                        jack=np.zeros((0, 1), np.int16) if xj is None else _i16(xj))
    return p


def rejudge(path):
    """a saved take judged again (the USB rows with hil_crackle's detectors, the clicks): [rows]"""
    z = np.load(path)
    ev = [tuple(e) for e in json.loads(str(z["events"]))]
    ccs = [tuple(c) for c in json.loads(str(z["ccs"]))]
    sw = list(z["switch_times"]) if "switch_times" in z else []
    rows = []
    if len(z["usb"]):
        rows += audio_rows("USB", z["usb"].astype(np.int64), int(z["fs_usb"]), ev, float(z["mark_usb"]) + 0.01, sw,
                           ccs=ccs)
    if len(z["jack"]):
        rows += audio_rows("jack", z["jack"][:, 0].astype(np.int64), int(z["fs_jack"]), ev,
                           float(z["mark_jack"]) + 0.01, sw, repeats_gate=False, ccs=ccs)
    return rows


# ---- the device ---------------------------------------------------------------------------------------------------

class NoDevice(RuntimeError):
    """the FM-1's MIDI port or USB audio input is not there (yet: an install re-enumerates it)"""


class InstallFailed(RuntimeError):
    pass


# seconds / counts of the reconnection after an install (and before each scenario); the host tests shorten them
WAIT = dict(back=90.0, poll=1.0, settle=2.0, tries=5, backoff=0.5)


def pa_reinit(sd):
    """restart PortAudio: sounddevice reads the device list once, at its first use, and keeps it. An install
    re-enumerates the FM-1 on USB (a new Core Audio device id, the device indexes shift), so the cached index points
    at a device that is gone: 'Error opening InputStream: Internal PortAudio error' (-9986, AUHAL -10851 Invalid
    Property Value). Only with no stream open (it closes them all)."""
    try:
        sd._terminate()
    except Exception:
        pass
    sd._initialize()


def offered(sd, dev, rate, channels=2):
    """the device takes this rate natively (the device switched to it, no conversion: as Rec opens it)"""
    try:
        sd.check_input_settings(device=dev, samplerate=rate, channels=channels, dtype="float32",
                                extra_settings=sd.CoreAudioSettings(change_device_parameters=True,
                                                                    fail_if_conversion_required=True))
        return True
    except Exception:
        return False


class Dev:
    """the FM-1 over USB-MIDI: one output (a lock: the events and the poller's CC 116 share it), one input. The port
    by name: PORT_KEYS (Frodhes; macOS CoreMIDI may keep the older 'Felucca' name of the same device)"""

    def __init__(self):
        import mido
        self.mido = mido
        outs = [n for n in mido.get_output_names() if any(k in n.lower() for k in hs.PORT_KEYS)]
        ins = [n for n in mido.get_input_names() if any(k in n.lower() for k in hs.PORT_KEYS)]
        if not outs or not ins:
            raise NoDevice("no FM-1 MIDI port")
        import fm1_midi
        self.out = fm1_midi.ChannelOut(mido.open_output(outs[0]))   # every channel message on FRODHES_MIDI_CH (8)
        self.raw = self.out.raw                                     # the port itself (the monkey's own channels)
        self.inp, self.lock = mido.open_input(ins[0]), threading.Lock()
        print(fm1_midi.say(self.out.ch), flush=True)

    def send(self, kind, a, b=0):
        M = self.mido.Message
        msg = (M("note_on", note=a, velocity=b) if kind == "on" else M("note_off", note=a) if kind == "off" else
               M("program_change", program=a) if kind == "pc" else M("control_change", control=a, value=b))
        with self.lock:
            self.out.send(msg)

    def cc(self, c, v):
        self.send("cc", c, v)

    def panel(self, msgs):
        for c, v in msgs:
            self.cc(c, v)
        time.sleep(0.3)

    def query(self, wait=0.25):
        """one CC 116 query: the reply's (control, value) pairs"""
        for _ in self.inp.iter_pending():
            pass
        self.cc(116, 127)
        got, t0 = [], time.monotonic()
        while time.monotonic() - t0 < wait:
            for m in self.inp.iter_pending():
                if m.type == "control_change":
                    got.append((m.control, m.value))
            time.sleep(0.005)
        return got

    def close(self):
        for p in (self.out, self.inp):
            try:
                p.close()
            except Exception:
                pass


class Poller:
    """CC 116 every POLL_S s in a thread while a case plays, into an Acc"""

    def __init__(self, dev):
        self.dev, self.acc, self.stop_ = dev, Acc(), threading.Event()
        self.th = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop_.is_set():
            t0 = time.monotonic()
            self.acc.add(parse_reply(self.dev.query()))
            self.stop_.wait(max(0.0, POLL_S - (time.monotonic() - t0)))

    def __enter__(self):
        self.dev.query()                              # the maxima / ranges restart here
        self.th.start()
        return self.acc

    def __exit__(self, *a):
        self.stop_.set()
        self.th.join(2.0)
        self.acc.add(parse_reply(self.dev.query()))   # the last part, the release blocks included


class Rec:
    """an input stream recorded whole into a preallocated buffer (the callback only copies). USB: the FM-1 at rate,
    the device switched to it (CoreAudio, as hil_cpu --usb), blocksize 128; jack: the interface's input ch (1-based)"""

    def __init__(self, dev, sr, channels, keep, secs, blocksize=128, switch_rate=False, sd=None):
        if sd is None:
            import sounddevice as sd
        self.sr, self.keep = int(sr), list(keep)
        self.buf = np.zeros((int(secs * sr), len(self.keep)), np.float32)
        self.w, self.adc0, self.flags, self.closed = 0, None, 0, False
        extra = sd.CoreAudioSettings(change_device_parameters=True, fail_if_conversion_required=True) \
            if switch_rate else None
        self.s = sd.InputStream(device=dev, samplerate=self.sr, channels=channels, dtype="float32",
                                blocksize=blocksize, callback=self.cb, extra_settings=extra)
        self.s.start()

    def cb(self, indata, frames, ti, status):
        if self.adc0 is None:
            self.adc0 = ti.inputBufferAdcTime or ti.currentTime
        if status:
            self.flags += 1
        n = min(frames, len(self.buf) - self.w)
        if n > 0:
            self.buf[self.w:self.w + n] = indata[:n, self.keep]
            self.w += n

    def mark(self):
        """the recording's position (s from its first frame) now: the event clock's zero"""
        return (self.s.time - self.adc0) if self.adc0 is not None else 0.0

    def stop(self):
        self.closed = True
        try:
            self.s.stop()
            self.s.close()
        except Exception:
            pass
        return self.buf[:self.w]


def find_usb(sd):
    d = [i for i, x in enumerate(sd.query_devices()) if any(k in x["name"].lower() for k in hs.PORT_KEYS)
         and x["max_input_channels"] >= 2]
    return d[0] if d else None


def find_jack(sd, name, ch):
    d = [i for i, x in enumerate(sd.query_devices()) if x["name"] == name and x["max_input_channels"] >= ch]
    return d[0] if d else None


def play(dev, events):
    t0 = time.monotonic()
    for t, kind, a, b in sorted(events, key=lambda e: e[0]):
        while time.monotonic() - t0 < t:
            time.sleep(0.0005)
        dev.send(kind, a, b)


# ---- the run ------------------------------------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.rows, self.cases, self.t0 = [], [], time.time()

    def add(self, sc, case, rows, counters=None):
        for check, value, crit, status in rows:
            self.rows.append(dict(scenario=sc, case=case, check=check, value=str(value), crit=crit, status=status))
        self.cases.append(dict(scenario=sc, case=case, counters=counters))
        bad = [r for r in rows if r[3] == "FAIL"]
        print(f"  {sc} {case}: " + ("FAIL " + "; ".join(f"{r[0]} {r[1]}" for r in bad) if bad else
                                    "SKIP" if rows and all(r[3] in ("SKIP", "info") for r in rows) and
                                    any(r[3] == "SKIP" for r in rows) else "ok"), flush=True)

    def verdict(self):
        st = [r["status"] for r in self.rows]
        return "FAIL" if "FAIL" in st else "INCOMPLETE" if "SKIP" in st else "PASS"

    def write(self, meta):
        os.makedirs(OUT, exist_ok=True)
        v = self.verdict()
        js = dict(meta, verdict=v, seconds=round(time.time() - self.t0), rows=self.rows, cases=self.cases)
        json.dump(js, open(os.path.join(OUT, "report.json"), "w"), indent=1, default=str)
        md = [f"# T1 must-pass (hil_scan --tier must): {v}", "",
              f"- date {time.strftime('%Y-%m-%d %H:%M')}, {js['seconds']} s; firmware: {meta.get('firmware', '?')}"
              + (f"; after S5: {meta['firmware_after']}" if "firmware_after" in meta else ""),
              "- primary gate: the FM-1's own counters (CC 116 every 0.5 s); secondary: the audio. MANUAL rows are "
              "hands-on steps for the coordinator / the user.", ""]
        for sc in SCENARIOS:
            rr = [r for r in self.rows if r["scenario"] == sc]
            if not rr:
                continue
            md += [f"## {sc}: {TITLES[sc]}", "", "| case | check | value | criterion | result |", "|---|---|---|---|---|"]
            md += [f"| {r['case']} | {r['check']} | {r['value']} | {r['crit']} | "
                   f"{'**FAIL**' if r['status'] == 'FAIL' else r['status']} |" for r in rr]
            md.append("")
        md += ["## The counters per case", "", "| scenario | case | replies | whole ISR max | engine max | shed | "
               "underruns / missed / overruns | ring low | spacing |", "|---|---|---|---|---|---|---|---|---|"]
        for c in self.cases:
            k = c["counters"]
            if not k:
                continue
            u = k["usb"]
            md.append(f"| {c['scenario']} | {c['case']} | {k['replies']} | {k['isr_max_us']} | {k['engine_max_us']} | "
                      f"{k['shed']} | {u.get('underruns', '-')} / {u.get('missed', '-')} / {u.get('overruns', '-')} | "
                      f"{u.get('fill_low', '-')} | {u.get('per_min', '-')}..{u.get('per_max', '-')} |")
        open(os.path.join(OUT, "report.md"), "w").write("\n".join(md) + "\n")
        print(f"\nT1 {v} ({js['seconds']} s); report build/hil/must/report.md, report.json")
        return v


class Must:
    def __init__(self, a, sd=None, dev_factory=Dev, installer=None, info=None):
        """sd / dev_factory / installer (pkg -> exit code, output) / info (-> the FM-1's identity line): the host tests
        pass fakes; the defaults are sounddevice, the MIDI ports, fm1_install.py"""
        if sd is None:
            import sounddevice as sd
        self.a, self.sd, self.rep = a, sd, Report()
        self.dev_factory, self.installer, self.info = dev_factory, installer or run_install, info or fm1_info
        self.live, self.not_new, self.stranded = [], False, None
        try:
            self.dev = dev_factory()
        except NoDevice as e:
            raise SystemExit(str(e))
        self.usb = find_usb(sd)
        if self.usb is None:
            raise SystemExit("no FM-1 audio input")
        self.jack = find_jack(sd, a.jack_dev, a.jack_ch)

    # the devices, found again (an install re-enumerates the FM-1: new Core Audio id, shifted indexes)
    def refresh(self, wait=None):
        """PortAudio restarted (when no stream of ours is open) and the FM-1's USB input + the jack interface found
        by name again; waits up to wait s (WAIT['back']) for the FM-1's input to appear"""
        lim = time.monotonic() + (WAIT["back"] if wait is None else wait)
        while True:
            if not any(not r.closed for r in self.live):
                self.live = []
                pa_reinit(self.sd)
            self.usb = find_usb(self.sd)
            if self.usb is not None:
                self.jack = find_jack(self.sd, self.a.jack_dev, self.a.jack_ch)
                return
            if time.monotonic() > lim:
                raise NoDevice("the FM-1's USB audio input did not come back")
            time.sleep(WAIT["poll"])

    def reconnect(self):
        """after an install: the MIDI port back (Frodhes or Felucca), then the USB audio input, PortAudio restarted"""
        lim = time.monotonic() + WAIT["back"]
        while True:
            try:
                self.dev = self.dev_factory()
                break
            except NoDevice:
                if time.monotonic() > lim:
                    raise NoDevice(f"the FM-1's MIDI port did not come back in {WAIT['back']:g} s")
                time.sleep(WAIT["poll"])
        time.sleep(WAIT["settle"])                    # Core Audio lists the device a moment after USB-MIDI
        self.refresh(max(1.0, lim - time.monotonic()))

    def open_usb(self, rate, secs, any_rate=False):
        """the FM-1's USB input as a Rec at rate, the device switched to it; any_rate: another rate the firmware offers
        when it does not take this one (RATES' order). Retried WAIT['tries'] times with backoff, the devices found
        again between tries; a rate the firmware does not offer is an error naming it"""
        last = None
        for k in range(WAIT["tries"]):
            if k:
                time.sleep(WAIT["backoff"] * 2 ** (k - 1))
                try:
                    self.refresh()
                except NoDevice as e:
                    last = e
                    continue
            rates = [rate] + ([r for r in RATES if r != rate] if any_rate else [])
            use = next((r for r in rates if offered(self.sd, self.usb, r)), None)
            if use is None:
                last = RuntimeError(f"the FM-1's USB input (device {self.usb}) offers none of "
                                    f"{', '.join(f'{r / 1000:g}' for r in rates)} kHz")
                continue
            try:
                r = Rec(self.usb, use, 2, (0, 1), secs, switch_rate=True, sd=self.sd)
            except self.sd.PortAudioError as e:
                last = e
                continue
            self.live.append(r)
            return r
        raise RuntimeError(f"USB input at {rate / 1000:g} kHz: {last!r} (after {WAIT['tries']} tries)")

    def open_jack(self, secs):
        """the jack interface's input (None: not connected); retried (no PortAudio restart: the USB stream is open)"""
        last = None
        for k in range(WAIT["tries"]):
            if self.jack is None:
                return None
            if k:
                time.sleep(WAIT["backoff"] * 2 ** (k - 1))
            try:
                q = self.sd.query_devices(self.jack)
                r = Rec(self.jack, int(q["default_samplerate"]), int(q["max_input_channels"]), (self.a.jack_ch - 1,),
                        secs, blocksize=0, sd=self.sd)
            except self.sd.PortAudioError as e:
                last = e
                continue
            self.live.append(r)
            return r
        raise RuntimeError(f"jack input {self.a.jack_dev!r}: {last!r} (after {WAIT['tries']} tries)")

    def case(self, sc, name, engine, events, stream_rate, fx=None, raw=None, switch_times=(), clicks_gate=True,
             jack=True, panel=True):
        """one case: the panel, the recordings (USB at stream_rate / none; the jack), the counters, the events"""
        if panel:
            self.dev.panel(panel_msgs(engine, fx, raw))
        dur = dur_of(events, 1.5)
        u = self.open_usb(stream_rate, dur + 3) if stream_rate else None
        j = self.open_jack(dur + 3) if jack else None
        time.sleep(1.0 if u else 0.3)
        mu, mj = (u.mark() if u else 0.0), (j.mark() if j else 0.0)
        with Poller(self.dev) as acc:
            play(self.dev, events)
            time.sleep(1.5)
        xu, xj = (u.stop() if u else None), (j.stop() if j else None)
        rows = gate_counters(acc, stream_rate)
        pm = panel_msgs(engine, fx, raw) if panel else []
        audio = []
        if stream_rate:
            audio += audio_rows("USB", xu, stream_rate, events, mu + 0.01, switch_times, clicks_gate=clicks_gate,
                                ccs=pm)
        if jack:
            if xj is None:
                audio.append(("jack: recorded", f"no interface {self.a.jack_dev!r} input {self.a.jack_ch}",
                              "the jack recorded (TEST-PLAN: the jack is the reference)", "SKIP"))
            else:
                audio += audio_rows("jack", xj[:, 0], j.sr, events, mj + 0.01, switch_times, repeats_gate=False,
                                    clicks_gate=clicks_gate, ccs=pm)
        rows += audio
        if has_event(audio):
            p = save_take(sc, name, xu, stream_rate, xj, j.sr if j else None, events, pm, mu, mj, switch_times)
            rows.append(("the whole take (npz: judge it again on the host)", os.path.relpath(p, ROOT),
                         "tools/hil_scan.py --tier must --rejudge FILE", "info"))
        self.rep.add(sc, name, rows, acc.summary())
        return xu, xj, mu

    # S1
    def s1(self):
        sm = hs.TESTS.get("stress_max")
        for rate in self.a.rates:
            if sm is not None:
                self.stress_max(sm, rate)
                continue
            for eng in self.a.engines:
                self.case("S1", f"{eng} {rate / 1000:g} kHz worst", eng, stim_worst(self.a.s1_worst), rate, HEAVY,
                          HEAVY_CC)
                ev = stim_recipe(self.a.s1_recipe / 2)
                self.case("S1", f"{eng} {rate / 1000:g} kHz recipe dry", eng, ev, rate)
                self.case("S1", f"{eng} {rate / 1000:g} kHz recipe moderate fx", eng, ev, rate, MODERATE)
        if sm is None:
            self.rep.add("S1", "stress_max", [("workstream B's stress_max", "not in hil_scan's TESTS on this build",
                                               "information: the built-in stimulus ran instead", "info")])

    def stress_max(self, fn, rate):
        """B's stress_max, called by name with the keyword arguments it accepts (engine / rate / seconds / quick /
        tier), the USB stream held open at rate and the counters polled around it"""
        want = dict(rate=rate, seconds=self.a.s1_worst + self.a.s1_recipe, quick=True, tier="must",
                    engines=tuple(self.a.engines))
        sig = inspect.signature(fn).parameters
        kw = {k: v for k, v in want.items() if k in sig}
        u = self.open_usb(rate, 1.0)                                   # the device switched to rate, kept there
        time.sleep(1.0)
        st = parse_reply(self.dev.query())["usb"].get("state")         # the FM-1's own view, before the run
        pre = [("USB stream state before the run", "no reply" if st is None else
                f"{'flowing' if st & 2 else 'not flowing'}, {'48' if st & 4 else '44.1'} kHz",
                f"flowing at {rate / 1000:g} kHz",
                "ok" if st is not None and st & 2 and bool(st & 4) == (rate == 48000) else "FAIL")]
        r = hs.Rig()
        rep = {}
        with Poller(self.dev) as acc:
            try:
                ok, msg = fn(r, rep, **kw)
            except Exception as e:  # keep the tier going
                ok, msg = False, f"error: {e!r}"
        u.stop()
        rows = pre + gate_counters(acc, rate) + [("stress_max (B: its own gates, the detector)", msg[:200], "pass",
                                                  "ok" if ok else "FAIL")]
        self.rep.add("S1", f"stress_max {rate / 1000:g} kHz", rows, acc.summary())

    # S2
    def s2(self):
        for eng in self.a.engines:
            ev = stim_worst(self.a.s2_secs * 0.6) + [(t + self.a.s2_secs * 0.6, k, a_, b)
                                                     for t, k, a_, b in stim_recipe(self.a.s2_secs * 0.4)]
            self.case("S2", f"{eng} jack only, no USB stream", eng, ev, None, MODERATE)
        # the idle floor: everything off, then 3 s; USB exact zero, the jack's level
        self.dev.panel(panel_msgs("rhodes"))
        self.dev.cc(10, 127)
        time.sleep(3.0)
        u = self.open_usb(48000, 4.0)
        j = self.open_jack(4.0)
        time.sleep(3.0)
        xu, xj = u.stop(), (j.stop() if j else None)
        xu = xu[int(0.5 * 48000):]
        rows = [("USB idle: non-zero samples", int(np.count_nonzero(xu)), "0 (exact digital zero)",
                 "ok" if not np.count_nonzero(xu) else "FAIL")]
        if xj is not None:
            rms = float(np.sqrt(np.mean(xj[int(0.5 * j.sr):, 0] ** 2)) + 1e-12)
            rows.append(("jack idle floor", f"{20 * np.log10(rms):.1f} dBFS rms", "information (hil_jack gates SNR)",
                         "info"))
        else:
            rows.append(("jack idle floor", "no interface", "the jack recorded", "SKIP"))
        self.rep.add("S2", "idle floor", rows)

    # S3
    def s3(self):
        ev, sw, rel = stim_switch()
        ev = ev + [(rel + 10.0, "cc", 64, 0)]                 # (the recording runs 10 s past the release)
        xu, _, mu = self.case("S3", "F01..F16 + TYPEs, held chord + pedal, 48 kHz", "rhodes", ev, 48000, switch_times=sw)
        # no stuck note: exact zero within 10 s of the release (F01, the pedal up)
        if xu is not None:
            i = int((mu + rel + 0.01) * 48000)
            nz = np.flatnonzero(np.abs(xu[i:]).max(axis=1) > 0) if len(xu) > i else np.zeros(0, int)
            last = (nz[-1] / 48000.0) if len(nz) else 0.0
            self.rep.add("S3", "after the release", [("exact zero after the release (s)", f"{last:.1f}",
                                                      "<= 10 s: no stuck note", "ok" if last <= 10.0 else "FAIL")])

    # S4
    def s4(self):
        for eng in self.a.engines:
            ev, ccs = stim_sweep()
            self.case("S4", f"{eng}: {len(ccs)} controls swept, held chord + pedal, 48 kHz", eng, ev, 48000,
                      clicks_gate=False)

    # S5
    def s5(self):
        rows = [("OCT- rescue", "hands-on", "once per release: switch off, hold OCT-, switch on, then fm1_install.py "
                 "the release package; the FM-1 boots it (MANUAL 'It does not start')", "MANUAL")]
        if not self.a.fwsc:
            rows.insert(0, ("update over USB", "no --fwsc", "--fwsc PACKAGE (release.py passes the release build)",
                            "SKIP"))
            self.rep.add("S5", "update", rows)
            return
        # 0.9.5: auto save keeps the setup, not the sound; after the update the FM-1 plays the preset the record
        # names, as that firmware stores it (presets.c preset_boot), not the panel it was left on. So: F03 loaded and
        # the sound edited on top (not kept); after the update the take must match one of the factory presets loaded
        # on the new firmware by Program Change (which one: the old record's, information; 0.9.4 kept F01 here)
        pc = S5_PC
        edits = {81: 127, 70: 27, 74: 40}                 # DRIVE 127, LP12 (CC 70 band 1) at FREQ 40: not kept
        note = [(0.1, "on", 60, 100), (1.3, "off", 60, 0)]

        rate = [48000]

        def take():
            u = self.open_usb(rate[0], 3.0, any_rate=True)  # 48 kHz (every take at the first one's rate), or what
            rate[0] = u.sr                                  # the running firmware offers
            time.sleep(0.8)
            play(self.dev, note)
            time.sleep(1.2)
            x = u.stop()[int(0.5 * u.sr):]
            return hs.lufs(x), hs.centroid(x[:, 0]) * u.sr / hs.FS   # (hs.centroid's bins assume 48 kHz)

        def rel(a, b):
            return a[0] - b[0], a[1] / max(b[1], 1e-9) - 1
        try:
            if self.a.prev:
                self.install(self.a.prev)
            self.dev.panel(panel_msgs("rhodes"))
            self.dev.send("pc", pc)                       # the preset (setup: its number is saved)
            time.sleep(0.5)
            for c, v in sorted(edits.items()):            # the sound edited on top
                self.dev.cc(c, v)
            time.sleep(5.0)                               # no USB stream: the settings saved 2 s after the change
            before = take()
            self.install(self.a.fwsc)
        except BaseException:
            if self.not_new:                              # never leave the FM-1 on the old firmware
                self.restore()
            raise
        after = take()                                    # nothing sent: what the saved setup plays
        refs = []                                         # every factory preset as the new firmware stores it
        for k, pcn in enumerate(S5_FACTORY_PCS):
            self.dev.send("pc", pcn)
            time.sleep(0.5)
            refs.append((f"F{k + 1:02d}", pcn, take()))
        self.dev.panel(panel_msgs("rhodes"))
        dflt = take()

        def near(r):
            l_, c_ = rel(after, r)
            return abs(l_) <= 0.5 and abs(c_) <= 0.05, abs(l_) / 0.5 + abs(c_) / 0.05
        scored = sorted(((near(r)[1], n, pcn, r) for n, pcn, r in refs), key=lambda t: t[0])
        hits = [n for n, pcn, r in refs if near(r)[0]]
        _, bn, bpc, br = scored[0]
        dl, dc = rel(after, br)
        el, ec = rel(before, br)
        fl, fc = rel(dflt, br)
        rows = [("installed", os.path.basename(self.a.fwsc) + (f" over {os.path.basename(self.a.prev)}"
                                                               if self.a.prev else ""), "fm1_install exit 0", "ok"),
                ("after the update vs the factory presets loaded on it (F01..F19, Program Change)",
                 (f"matches {', '.join(hits)}" if hits else "matches none") + f"; nearest {bn} (PC {bpc}): "
                 f"{dl:+.2f} LU / {100 * dc:+.1f} %", "some factory preset within 0.5 LU and 5 %: the boot plays a "
                 "stored preset (0.9.5's design; which one is 0.9.4's record)", "ok" if hits else "FAIL"),
                (f"the preset it booted ({bn})", f"PC {bpc}" + ("" if bpc == S5_PC else
                 f" (F{S5_PC + 1:02d} was loaded on the old firmware)"), "information", "info"),
                ("before the update (the edited panel) vs it", f"{el:+.2f} LU / {100 * ec:+.1f} %",
                 "information: the edits (not kept) and the release's own change", "info"),
                ("the defaults vs it", f"{fl:+.2f} LU / {100 * fc:+.1f} %", "information", "info"),
                ("user presets / the pattern", "no MIDI read-back", "host: tests/py/test_persistence.py; the user "
                 "checks U01 and the pattern after the update", "MANUAL")] + rows
        self.rep.add("S5", "update", rows)

    def install(self, pkg):
        """pkg over USB, then the FM-1 found again (MIDI port, USB audio input, PortAudio restarted). self.not_new:
        the FM-1 may not be on --fwsc (set before any install, cleared once --fwsc is installed and back)"""
        self.not_new = True
        self.dev.close()
        code, out = self.installer(pkg)
        if code:
            raise InstallFailed(f"fm1_install {pkg} exited {code}:\n{out[-2000:]}")
        self.reconnect()
        if os.path.abspath(pkg) == os.path.abspath(self.a.fwsc or ""):
            self.not_new = False

    def restore(self):
        """S5 failed with the FM-1 maybe on --prev (or half way): install --fwsc again, loudly; twice at most. Still
        not on --fwsc: self.stranded, the scenarios after it are not run (they would test the old firmware)"""
        fw = self.a.fwsc
        print(f"\n*** S5 FAILED with the FM-1 not on {os.path.basename(fw)}: installing it again ***", file=sys.stderr,
              flush=True)
        err = None
        for _ in range(2):
            try:
                self.install(fw)
                break
            except Exception as e:  # noqa: PERF203
                err = e
                try:
                    self.dev.close()
                except Exception:
                    pass
        ok = not self.not_new
        self.rep.add("S5", "restore", [("--fwsc installed again after the failure", os.path.basename(fw) if ok else
                                        f"error: {err!r}", "the FM-1 left on --fwsc", "ok" if ok else "FAIL")])
        if not ok:
            self.stranded = (f"THE FM-1 IS LEFT ON {os.path.basename(self.a.prev or '?')} (or in update mode): "
                             f"install the release by hand: echo y | {PY} tools/fm1_install.py {fw}")
            print(f"*** {self.stranded} ***", file=sys.stderr, flush=True)

    # S6
    def s6(self):
        for eng in self.a.engines:
            for k in range(self.a.cpu_runs):
                cmd = [PY, "tools/hil_cpu.py", "--worst", "--usb", "48000"] + ([] if eng == "rhodes" else
                                                                             ["--engine", eng])
                p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=900)
                self.rep.add("S6", f"{eng} run {k + 1}", cpu_rows(p.returncode, p.stdout + p.stderr))

    def run(self):
        meta = dict(firmware=self.info(), plan=[s for s in self.a.scenarios], engines=self.a.engines,
                    rates=self.a.rates)
        for sc in self.a.scenarios:
            print(f"\n{sc}: {TITLES[sc]}", flush=True)
            if self.stranded:
                self.rep.add(sc, "not run", [("scenario ran", self.stranded, "the FM-1 on --fwsc", "FAIL")])
                continue
            for r in self.live:                   # a stream an earlier scenario's error left open
                r.stop()
            try:
                self.refresh()                    # the devices found again (an earlier install, a replug)
                if sc == "S6":                    # hil_cpu opens its own MIDI ports and USB stream
                    self.dev.close()
                    self.s6()
                    self.dev = self.dev_factory()
                else:
                    getattr(self, sc.lower())()
            except SystemExit:
                raise
            except Exception as e:  # one scenario's error is its FAIL, the tier goes on
                self.rep.add(sc, "error", [("scenario ran", f"error: {e!r}", "no error", "FAIL")])
        try:
            self.dev.panel(panel_msgs("rhodes"))
            self.dev.send("pc", 0)
        except Exception:
            pass
        if "S5" in self.a.scenarios and self.a.fwsc:
            meta["firmware_after"] = self.info()
        return self.rep.write(meta)


def run_install(pkg):
    """fm1_install.py pkg (answered yes): (exit code, output)"""
    p = subprocess.run(f"echo y | {PY} tools/fm1_install.py {pkg}", shell=True, cwd=ROOT, text=True,
                       capture_output=True, timeout=600)
    return p.returncode, p.stdout + p.stderr


def cpu_rows(code, text):
    """hil_cpu --worst --usb's output -> rows: the engine worst, the whole ISR, voices shed, its USB verdict"""
    import re
    rows = []
    m = re.search(r"worst (\d+) us", text)
    w = int(m.group(1)) if m else None
    rows.append(("hil_cpu worst block (us)", w if m else "no 'worst' line", f"<= {ISR_LIMIT_US}",
                 "ok" if m and w <= ISR_LIMIT_US else "FAIL"))
    isr = [int(x) for x in re.findall(r"whole ISR\s+(\d+) us", text)]
    rows.append(("whole ISR max (us)", max(isr) if isr else "no reply", f"<= {ISR_LIMIT_US}",
                 "ok" if isr and max(isr) <= ISR_LIMIT_US else "FAIL"))
    shed = [int(x) for x in re.findall(r"voices shed (\d+)", text)]
    rows.append(("voices shed", sum(shed), "0", "ok" if shed and not any(shed) else "FAIL"))
    usb_fail = "FAIL: USB" in text or "no reply (a firmware before" in text
    rows.append(("hil_cpu USB counters", "FAIL" if usb_fail else "clean", "0 underruns / missed / overruns",
                 "FAIL" if usb_fail else "ok"))
    rows.append(("hil_cpu exit", code, "0", "ok" if code == 0 else "FAIL"))
    return rows


def fm1_info():
    try:
        p = subprocess.run([PY, "tools/fm1_install.py", "--info"], cwd=ROOT, text=True, capture_output=True, timeout=60)
        return (p.stdout.strip().splitlines() or ["?"])[-1]
    except Exception as e:
        return f"? ({e!r})"


# ---- the plan and its time ----------------------------------------------------------------------------------------
SETUP_S = 2.8                    # per case: the panel (0.3), the stream's start (1.0), the tail (1.5)
CPU_RUN_S = dict(rhodes=25.0, vibes=40.0, marimba=40.0)     # hil_cpu --worst --usb: its scenarios x 3 takes
INSTALL_S = 120.0


def plan(a):
    """[(scenario, case, seconds)] of the run a describes (the dry-run's list and its time)"""
    out = []
    for sc in a.scenarios:
        if sc == "S1":
            for rate in a.rates:
                for eng in a.engines:
                    out.append((sc, f"{eng} {rate / 1000:g} kHz worst", dur_of(stim_worst(a.s1_worst)) + SETUP_S))
                    for k in ("dry", "moderate fx"):
                        out.append((sc, f"{eng} {rate / 1000:g} kHz recipe {k}",
                                    dur_of(stim_recipe(a.s1_recipe / 2)) + SETUP_S))
        elif sc == "S2":
            out += [(sc, f"{eng} jack only", a.s2_secs + 1.0 + SETUP_S) for eng in a.engines]
            out.append((sc, "idle floor", 7.0))
        elif sc == "S3":
            ev, _, rel = stim_switch()
            out.append((sc, "switching", rel + 10.0 + 1.0 + SETUP_S))
        elif sc == "S4":
            out += [(sc, f"{eng} sweep", dur_of(stim_sweep()[0]) + SETUP_S) for eng in a.engines]
        elif sc == "S5":
            out.append((sc, "update", (2 if a.prev else 1) * INSTALL_S + 5.0 + 3 * 4.0 if a.fwsc else 0.0))
        elif sc == "S6":
            out += [(sc, f"{eng} x{a.cpu_runs}", a.cpu_runs * CPU_RUN_S[eng]) for eng in a.engines]
    return out


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="hil_scan.py --tier must", description="T1: the must-pass scenarios")
    ap.add_argument("--tier", choices=("must", "full"), required=True)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS), help="S1..S6, comma separated")
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--rates", default=",".join(str(r) for r in RATES))
    ap.add_argument("--fwsc", help="S5: the package to install (the release build)")
    ap.add_argument("--prev", help="S5: install this package first (the previous release), then --fwsc over it")
    ap.add_argument("--jack-dev", default="M6")
    ap.add_argument("--jack-ch", type=int, default=5)
    ap.add_argument("--cpu-runs", type=int, default=3)
    ap.add_argument("--s1-worst", type=float, default=40.0, help="S1: seconds of the worst case per engine and rate")
    ap.add_argument("--s1-recipe", type=float, default=20.0, help="S1: seconds of the recipe (dry + moderate fx)")
    ap.add_argument("--s2-secs", type=float, default=30.0)
    ap.add_argument("--dry-run", action="store_true", help="the plan and its time; no MIDI / audio")
    ap.add_argument("--rejudge", nargs="+", metavar="NPZ", help="judge saved takes (build/hil/must/takes/*.npz) "
                    "again on the host: their audio rows; no MIDI / audio")
    a, rest = ap.parse_known_args(argv)
    a.rest = rest
    a.scenarios = [s.strip().upper() for s in a.scenarios.split(",") if s.strip()]
    a.engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    a.rates = [int(r) for r in a.rates.split(",") if r.strip()]
    for s in a.scenarios:
        if s not in SCENARIOS:
            ap.error(f"unknown scenario {s}")
    for e in a.engines:
        if e not in ENGINES:
            ap.error(f"unknown engine {e}")
    return a


def tier_main(argv):
    a = parse_args(argv)
    if a.tier == "full":                              # T2: today's suites, every one (overnight, unattended)
        names = list(hs.TESTS) + [n for n in hs.VORDER] + [n for n in hs.MORDER]
        return hs.main(["--only", ",".join(names)] + a.rest + (["--dry-run"] if a.dry_run else []))
    if a.rest:
        raise SystemExit(f"--tier must: unknown options {a.rest}")
    if a.rejudge:
        bad = False
        for f in a.rejudge:
            print(f)
            for check, value, crit, status in rejudge(f):
                print(f"  {status:5s} {check}: {value}  ({crit})")
                bad |= status == "FAIL"
        return 1 if bad else 0
    p = plan(a)
    tot = sum(s for _, _, s in p)
    if a.dry_run:
        print("T1 must-pass plan (hil_scan --tier must):")
        for sc in a.scenarios:
            secs = sum(s for x, _, s in p if x == sc)
            print(f"  {sc} {TITLES[sc]}: {secs / 60:.1f} min")
            for x, case, s in p:
                if x == sc:
                    print(f"      {case:44s} {s:6.0f} s")
        print(f"  total {tot / 60:.1f} min (budget {BUDGET_S / 60:.0f} min)"
              + ("" if "stress_max" not in hs.TESTS else "; S1 runs B's stress_max at its own length"))
        print("  stress_max (workstream B): " + ("in TESTS, S1 calls it by name" if "stress_max" in hs.TESTS
                                                 else "not on this build: S1 runs the built-in stimulus"))
        return 0
    v = Must(a).run()
    return 0 if v == "PASS" else 1 if v == "FAIL" else 2


if __name__ == "__main__":
    sys.exit(tier_main(sys.argv[1:]))
