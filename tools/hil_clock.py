#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Does every clock-synced thing follow an external MIDI clock? (the FM-1 over USB-MIDI + its USB audio; the clock
from the FM-1's own USB-MIDI port or from a computer MIDI interface wired to the TRS IN)

    tools/hil_clock.py [--via usb,trs,int,trsoff] [--port NAME] [--items a,b] [--bpm 90,120,137] [--change 120:100]
                       [--int-bpm 137] [--channel N]
    tools/hil_clock.py --list          the MIDI ports (for --port)
    tools/hil_clock.py --summary       the table row | INT | EXT-USB | EXT-TRS from the last runs' reports
    tools/hil_clock.py --dry-run       the plan: items, takes, tempos, judged windows; no MIDI, no audio, no lock
    tools/hil_clock.py --selftest      the analysis on synthetic recordings: a firmware that follows must PASS, one
                                       free-running at a wrong rate and one at the right rate never re-phased by a
                                       Start must FAIL
    tools/hil_clock.py --reanalyze DIR judge the takes saved by an earlier run (DIR/<take>.npz) again

Every synced item of tempo.c tempo_sync_publish, each measured by its effect on the audio:
  trem        TREM SYNC 1/4 (TREM/WAH = TREM, AM, DEPTH 64, sine): the amplitude envelope
  motor       VIBES MOTOR SYNC 1/4 (CC 79 VIBES, 47 / 48 / 50 / 51): the amplitude envelope
  phaser      PHA SYNC 1/4 (CC 85 phaser, DEPTH 127, FEEDBACK 100, CC 15): third-octave band levels against the
              total (a spectral feature), the band with the clearest fundamental
  chorus      CHORUS SYNC 1/4 (CC 85 chorus, DEPTH 64, CC 23): the same spectral feature; MANUAL when its
              modulation is too small to read (the comb's movement on a held chord can be < 1 dB)
  filter      the filter LFO's SYNC 1/4 (LP12, CUTOFF 60, RESO 80, LFO 110, sine, free, CC 77): the spectral feature
  delay_dig / delay_ana / delay_tape   DLY SYNC 1/4 for each delay type (CC 88 / 89 / 26 / 25): the echo spacing
  seq         the sequencer (a 1-bar pattern step-recorded at QUANT 1/16: 8 notes on steps 1-8, then 8 empty; the
              pattern is CLEARED first and after: the user's pattern is lost, as with tools/hil_seq.py): every note
              on the 1/16 grid of the sent clock, the first note after each Start on bar 1, nothing after a Stop
  arp         the arpeggiator (UP, 1/16, a held C E G): every step on the 1/16 grid of the sent clock
  delay_long  DLY SYNC 1/1 on DIG: 4 beats (2.0 s at 120, 2.4 s at 100) is over the line's 1.48 s: the echo
              must come at half (1.0 / 1.2 s), the MANUAL's 'plays at half (or a quarter)'
  range       out-of-range clock holds the tempo: 12 beats at 120 (Start), then 4 beats at 30 BPM (83 ms pulses)
              and 8 at 400 BPM (6.25 ms), no Start: the DIG 1/4 echo must stay at 500 ms
  tap         MANUAL: PLAY is a panel button (no MIDI)
  wah         MANUAL, never measured: the touch wah has no LFO (fx.c fx_trem sw == 2: the input's envelope drives
              the cutoff), so its SYNC changes nothing audible
The runs (--via, a column each of the table in build/hil/clock/summary.txt):
  usb     CLOCK EXT, the clock over the FM-1's USB-MIDI                                     column EXT-USB
  trs     CLOCK EXT, the clock over --port into the TRS IN (TRS IN ON)                         column EXT-TRS
  int     CLOCK INT and BPM --int-bpm set on the panel by hand (no MIDI for either): every item once, no change
          or restart, judged on the rate alone (no beat is sent) while a decoy clock at 100 BPM goes over USB
          (it must change nothing)                                                            column INT
  trsoff  TRS IN OFF set on the panel: 12 beats at 120 over USB, then a Start and 100 BPM over the TRS port: the
          echo must stay at 500 ms                                                            row TRS IN OFF
The CHANNEL row: the FM-1's channel is found by playing a note on each (the MIDI page's CHANNEL filters notes and
CCs, never the clock); with a CHANNEL other than ALL / 1 every EXT run proves the clock passes it (DIG followed).
Takes: every item at 120 BPM with a tempo change and a restart in the same take (--change FROM:TO): Start (FA),
20 beats at FROM, 12 at TO (the grid goes on: a change mid-take), Stop (FC), 1.37 beats with no clock, Start, 16
beats at TO, Stop; trem and phaser also at the other --bpm (Start, 20 beats, Stop). The LFO takes hold a chord
(struck again every 8 beats); the delay takes play short C6 notes 8 and 14 beats into each run (2 and 8 after the
change); every take starts with two reference notes 0.4 s apart before any clock (the MIDI -> audio latency: the
smaller of their onsets' delays, both in the report, flagged when over 5 ms apart: a flash write held one up; the seq
take waits 1.6 s after its pattern recording and silence, past the pattern's auto save).

The setup (dry: every parameter at its default, then the item's CCs per docs/MANUAL.md's CC table) and the notes go
over USB-MIDI; only the clock (F8, 24 per beat) and the transport (FA, FC) over the chosen path: --via usb the FM-1's
own port, --via trs the interface output --port NAME wired to the TRS IN (MIDI page: TRS IN ON, the default). CLOCK
must be EXT (the default; it has no CC). A Start starts the sequencer: outside the seq item CC 60 = 0 follows 20 ms
later. The clock comes from a dedicated thread (real-time priority where granted, the GC off) on absolute times from
time.perf_counter (tick n of a run at run start + n * 60 / (24 BPM): no accumulated jitter); every message's real send
time is stamped and the analysis uses the stamps. Afterwards 3 beats at 120 BPM (--restore-bpm), then the dry state.

Measured on the USB recording laid on the stream clock (the capture's block times; the FM-1's USB runs ~600 ppm off),
the sent beats moved by the take's latency:
  LFO items: period = the drift of the feature's phase per beat against the sent beats (every band, each turned by
      its mean phase and weighted by its mean; was: the peak of its windowed DTFT, 0.6..1.6 x the beat rate, summed over
      the bands) over 8 beats from 2 bars after a Start / 2 beats after a change: within 0.5 % of the sent beat.
      Phase per 4-beat window (hop 2) against the sent beats: within 5 % of a beat of the item's reference
      (--phase-ref for trem / motor, else the circular mean of the item's judged windows: one alignment for every
      tempo, the change and both Starts; a free-running LFO keeps its phase across the restart's pause, 0.37 beat off)
  delay: dry -> 1st and 1st -> 2nd echo spacing (envelope NCC) within 1 ms + 0.5 % of the sent beat
  seq / arp: every onset from 2 bars after a Start (2 beats after a change) on the 1/16 grid: within 2 ms + half an
      audio block (2.9 ms: the FM-1 starts a note at a block boundary) of the segment's median offset, the median
      within 8 ms (the latency's own block); at least 75 % of the expected notes; seq: the pattern's steps, the first
      note after each Start on bar 1, no note from 40 ms after a Stop to the next Start
A take with a USB gap or a clock message over 4 ms late is taken again once, then INCOMPLETE.
The clock counters (a firmware that answers CC 116 = 99 with CC 100 id / 101-102 value, ids 30-42: midi-clock
fafd16b): queried 30 ms after each Start / Continue and 30 ms before each Stop in the seq takes, and at every
take's end; logged next to the pulses sent since the Start, a FAIL when SPP now (40) or the beat count (41), both
in pulses (24 a beat, as the firmware counts them: clk_sp, clk_k), is more than COUNT_TOL pulses off them. A firmware without the readout (no id 30-42 in its answer): skipped, silently.

Report build/hil/clock/<via>/report.txt + report.json (a row per item: PASS / FAIL / MANUAL), the takes as
<take>.npz (--reanalyze). Exit 0 PASS (MANUAL items named on the verdict line), 1 FAIL, 2 INCOMPLETE / setup error.
Takes the DEVICE lock (tools/lock.py). Needs the macOS sandbox off (CoreAudio, CoreMIDI). ~8 min per --via."""
import argparse
import json
import os
import random
import re
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "build", "hil", "clock")
PPQN = 24
BPMS = (90, 120, 137)
CHANGE = (120, 100)
RESTORE_BPM = 120
PROBE = 84                       # C6, short: the delay's probe and every take's reference note
LOCK_BEATS = 8                   # judged from 2 bars after a Start
TRACK_BEATS = 2                  # ... and from 2 beats after a tempo change
PERIOD_BEATS = 8
PHASE_WIN, PHASE_HOP = 4, 2
PERIOD_TOL = 0.005               # 0.5 %
PHASE_TOL = 0.05                 # 5 % of a beat
DLY_TOL_S, DLY_TOL_REL = 0.001, 0.005
DLY_MIN_REL = 0.1                # an echo: a tenth of the dry note's envelope at least (an older probe's feedback
                                 # chain, decayed, ran into the next probe's window: a false 'echo' at 346 / 485 ms)
GRID_TOL_S = 0.002 + 0.5 * 256 / 44100     # 2 ms + half an audio block
GRID_MED_S = 0.008
GRID_MIN_FOUND = 0.75
STOP_GUARD_S = 0.040
MIN_DEPTH_DB = 1.0               # the fundamental (peak to peak) under this: no modulation
JITTER_MAX_S = 0.004
STRIKE_MASK_S = 0.25
REF_T = (0.2, 0.6)               # the two reference notes (s into the take; 0.4 s apart: the first one's release out)
T_CHORD = 0.9                    # the LFO takes' chord, the arpeggiator's (after the references)
T_CLOCK = 1.3                    # the first clock / Start
REF_SPREAD_S = 0.005             # the two references' latencies further apart: flagged (a stall: a flash write)
SAVE_WAIT_S = 1.6                # after the seq take's pattern recording and silence: the pattern's auto save (1.5 s
                                 # after its last change) is over before the first reference note
PAUSE_BEATS = 1.37
CC_SEQ = 60
SEQ_NOTES = (60, 67, 72, 64, 60, 67, 72, 64)   # steps 1-8 of a 1-bar pattern, steps 9-16 empty
ARP_CHORD = (60, 64, 67)

# ---- the firmware's numbers: the SYNC index of 1/4 and the CC values that select it ------------------------------


def _src(name):
    return open(os.path.join(ROOT, "firmware", "src", name)).read()


def cc_for(param, idx):
    """(cc, value) selecting index idx of params.c's P_<param> (frodhes.c midi_cc: index = v * (hi + 1) >> 7)"""
    m = re.search(r'\[P_%s\] = \{"[^"]*", (\d+), \d+, F_\w+, (\d+)' % param, _src("params.c"))
    hi, cc = int(m.group(1)), int(m.group(2))
    v = min(127, (idx * 128 + 64) // (hi + 1))
    assert v * (hi + 1) >> 7 == idx
    return cc, v


def quarter_idx():
    units = [int(u) for u in re.search(r"SYNC_UNITS\[\d+\] = \{([^}]*)\}", _src("tempo.c")).group(1).split(",")]
    return units.index(96)


def sync_quarter_cc(param):
    return cc_for(param, quarter_idx())


LONG_IDX = 12                    # DLY SYNC 1/1: 4 beats, 2.0 s at 120 BPM, over the line's 1.48 s: half, 1.0 s
DLY_MAX_S = 1.486
DECOY_BPM = 100                  # CLOCK INT: a clock at another tempo goes out meanwhile (it must change nothing)
INT_BPM = 137


def items():
    """every synced item: kind (lfo / delay / seq / arp / manual), feature, the CCs after the dry state, the chord"""
    q = sync_quarter_cc
    trem = [(17, 64), (21, 127), (18, 64), (22, 0), q("TW_SYNC")]
    d = lambda t: [(88, t), (89, 100), (26, 50), q("DL_SYNC")]  # noqa: E731
    lp12 = cc_for("FL_TYPE", 1)
    assert [int(u) for u in re.search(r"SYNC_UNITS\[\d+\] = \{([^}]*)\}", _src("tempo.c")).group(1).split(",")][
        LONG_IDX] == 384
    return {
        "trem": dict(kind="lfo", feature="amp", ccs=[(102, 110)] + trem, chord=(48, 60), all_bpm=True),
        "motor": dict(kind="lfo", feature="amp", ccs=[(79, 0), (47, 127), (48, 110), (50, 0), q("MOT_SYNC")],
                      chord=(65, 77)),
        "phaser": dict(kind="lfo", feature="spec", ccs=[(102, 110), (85, 127), (13, 127), (16, 100), q("PH_SYNC")],
                       chord=(48, 60, 72), vel=120, all_bpm=True),
        "chorus": dict(kind="lfo", feature="spec", ccs=[(102, 110), (85, 64), (86, 64), q("CP_SYNC")],
                       chord=(48, 60, 72), vel=120, manual_if_weak=True),
        "filter": dict(kind="lfo", feature="spec", ccs=[(102, 110), lp12, (74, 60), (71, 80), (72, 110), (75, 0),
                                                         (78, 0), q("LFO_SYNC")], chord=(48, 60, 72), vel=120),
        "delay_dig": dict(kind="delay", ccs=d(40)),
        "delay_ana": dict(kind="delay", ccs=d(80)),
        "delay_tape": dict(kind="delay", ccs=d(112)),
        "delay_long": dict(kind="delay", ccs=[(88, 40), (89, 100), (26, 50), cc_for("DL_SYNC", LONG_IDX)],
                           div=96 * 4, ext_only=False),
        "range": dict(kind="delay", ccs=d(40), special="range"),
        "seq": dict(kind="seq", ccs=[(104, 0)]),                      # (NOISE 0: no release knock between steps)
        "arp": dict(kind="arp", ccs=[(104, 0), (53, 3), (54, 0), (55, 50)]),
        "tap": dict(kind="manual", why="PLAY is a panel button (no MIDI): tap PLAY 4 times at a known tempo with a "
                    "SYNC item on, the TEMPO page's BPM shows it and PLAY blinks on the beat"),
        "wah": dict(kind="manual", why="the touch wah has no LFO (fx.c fx_trem sw == 2: the input's envelope drives "
                    "the cutoff): its SYNC changes nothing audible. By hand: TREM/WAH on WAH, TREM SYNC 1/4, RATE shows "
                    "the resonance, the sound is the touch wah's at any tempo"),
    }


# ---- the plan ----------------------------------------------------------------------------------------------------


class Timeline:
    """a take's schedule: [(t_s, dest, msg)] with dest 'clk' (the clock path) or 'usb', msg a tuple ('clock',),
    ('start',), ('stop',), ('note_on', n, v), ('note_off', n), ('cc', c, v). Ticks on absolute times from each run's
    start; beats = the schedule indices of every 24th tick of a run"""

    def __init__(self, t0):
        self.t, self.ev, self.beats, self.segs, self.starts, self.stops = float(t0), [], [], [], [], []

    def add(self, t, dest, msg):
        self.ev.append((float(t), dest, msg))
        return len(self.ev) - 1

    def start(self, seq_stop=True, msg="start"):
        self.starts.append(self.add(self.t - 0.001, "clk", (msg,)))
        if seq_stop:
            self.add(self.t + 0.020, "usb", ("cc", CC_SEQ, 0))

    def stop(self):
        self.stops.append(self.add(self.t, "clk", ("stop",)))

    def pause(self, s):
        self.t += s

    def run(self, bpm, beats, kind, dest="clk", lock=None):
        """beats of clock at bpm; dest 'virt': not sent (CLOCK INT: the panel's tempo, the grid the FM-1 should
        keep by itself)"""
        dt = 60.0 / (PPQN * bpm)
        i0 = len(self.beats)
        for k in range(int(beats) * PPQN):
            j = self.add(self.t + k * dt, dest, ("clock",))
            if k % PPQN == 0:
                self.beats.append(j)
        if kind:
            self.segs.append(dict(kind=kind, i0=i0, i1=len(self.beats), bpm=bpm,
                                  lock=lock if lock is not None else LOCK_BEATS if kind == "start" else TRACK_BEATS))
        self.t += beats * 60.0 / bpm

    def at_beat(self, b):
        t = [self.ev[j][0] for j in self.beats]
        return float(np.interp(b, np.arange(len(t)), t))


def plan(bpms=BPMS, change=CHANGE, only=None, mode="ext", int_bpm=INT_BPM):
    """the takes of one run: mode 'ext' (CLOCK EXT: the clock over the run's path), 'int' (CLOCK INT, the panel's
    BPM = int_bpm: no clock to follow, a decoy clock at DECOY_BPM over USB meanwhile) or 'trsoff' (TRS IN OFF: the
    TRS clock must change nothing)"""
    if mode == "trsoff":
        return [plan_trsoff()]
    takes = []
    for name, it in items().items():
        if it["kind"] == "manual" or (only and name not in only):
            continue
        if it.get("special") == "range":
            if mode == "ext":
                takes.append(plan_range())
            continue
        if mode == "int":
            takes.append(plan_take(name, it, int_bpm, None, mode="int"))
            continue
        for bpm in (bpms if it.get("all_bpm") else [change[0]]):
            takes.append(plan_take(name, it, bpm, change if bpm == change[0] else None))
    return takes


def _probe(tl, t, kw):
    kw.setdefault("probes", []).append(tl.add(t, "usb", ("note_on", PROBE, 110)))
    tl.add(t + 0.05, "usb", ("note_off", PROBE))


def _refs(tl, note):
    """the take's two reference notes (REF_T), each 50 ms: [their note-on indices]; the latency is the smaller of
    their two (a flash write can hold the first one up)"""
    out = []
    for t in REF_T:
        out.append(tl.add(t, "usb", ("note_on", note, 110)))
        tl.add(t + 0.05, "usb", ("note_off", note))
    return out


def plan_take(name, it, bpm, change, mode="ext"):
    kind = it["kind"]
    tl = Timeline(T_CLOCK)
    rn = it["chord"][0] if kind == "lfo" else PROBE      # (the LFO takes: the chord's low note, through a closed filter)
    refs = _refs(tl, rn)
    seq = kind == "seq"
    if kind == "lfo":
        for k, n in enumerate(it["chord"]):
            tl.add(T_CHORD + 0.001 * k, "usb", ("note_on", n, it.get("vel", 100)))
    if kind == "arp":
        tl.add(T_CHORD - 0.1, "usb", ("cc", 52, 1))
        for k, n in enumerate(ARP_CHORD):
            tl.add(T_CHORD + 0.001 * k, "usb", ("note_on", n, 100))
    if mode == "int":                                   # the panel's tempo: a grid not sent; a decoy clock instead
        t0 = tl.t
        if seq:
            tl.starts.append(tl.add(t0 - 0.001, "usb", ("cc", CC_SEQ, 127)))
        tl.run(bpm, 28, "start", dest="virt", lock=TRACK_BEATS)
        dt = 60.0 / (PPQN * DECOY_BPM)
        for k in range(int((tl.t - t0) / dt)):
            tl.add(t0 + k * dt, "usb", ("clock",))
        if seq:
            tl.stops.append(tl.add(tl.t, "usb", ("cc", CC_SEQ, 0)))
        strikes_b, probes_b = [7.5, 15.5, 23.5], [8, 14, 20]
    else:
        tl.start(not seq)
        tl.run(bpm, 20, "start")
        if change:
            tl.run(change[1], 12, "change")
            tl.stop()
            tl.pause(PAUSE_BEATS * 60.0 / change[1])
            tl.start(not seq, "continue" if seq else "start")   # (the sequencer: a Continue starts from bar 1 too)
            tl.run(change[1], 16, "start")
            strikes_b, probes_b = [7.5, 15.5, 23.5, 29.5, 39.5, 45.5], [8, 14, 22, 28, 40, 46]
        else:
            strikes_b, probes_b = [7.5, 15.5], [8, 14]
        tl.stop()
    kw = {}
    if kind == "lfo":
        kw["strikes"] = []
        for b in strikes_b:
            t = tl.at_beat(b)
            for n in it["chord"]:
                tl.add(t - 0.012, "usb", ("note_off", n))
            kw["strikes"].append(tl.add(t, "usb", ("note_on", it["chord"][0], it.get("vel", 100))))
            for k, n in enumerate(it["chord"][1:]):
                tl.add(t + 0.001 * (k + 1), "usb", ("note_on", n, it.get("vel", 100)))
    if kind == "delay":
        for b in probes_b:
            _probe(tl, tl.at_beat(b), kw)
        _delay_on_after_ref(tl, it["ccs"])
    end = tl.t
    for n in it.get("chord", ARP_CHORD if kind == "arp" else ()):
        tl.add(end + 0.05, "usb", ("note_off", n))
    if kind == "arp":
        tl.add(end + 0.06, "usb", ("cc", 52, 0))
    tail = {"delay": 2.6 if it.get("div") else 1.6, "lfo": 1.0}.get(kind, 0.6)
    q, ql = [], []                                       # the clock counters' queries (CC 116 = 99)
    if seq and mode == "ext":
        for k, j in enumerate(tl.starts):
            q.append(tl.add(tl.ev[j][0] + 0.03, "usb", ("cc", CC_QUERY, QUERY_CLOCK)))
            ql.append(f"after start {k + 1}")
        for k, j in enumerate(tl.stops):
            q.append(tl.add(tl.ev[j][0] - 0.03, "usb", ("cc", CC_QUERY, QUERY_CLOCK)))
            ql.append(f"before stop {k + 1}")
    q.append(tl.add(end + 0.1, "usb", ("cc", CC_QUERY, QUERY_CLOCK)))
    ql.append("take end")
    t = _take(f"{name}_{bpm}" + ("_int" if mode == "int" else ""), name, kind, tl, end + tail, first=refs[0], refs=refs,
              starts=tl.starts, stops=tl.stops, queries=q, **kw)
    t["query_labels"] = ql
    t.update(feature=it.get("feature"), ccs=list(it["ccs"]), restart=bool(change), mode=mode, div=it.get("div"))
    return t


def _delay_on_after_ref(tl, ccs):
    """the delay switched on (its CC 88) only after the reference note: struck with the delay on, the reference
    echoed at the tempo the FM-1 still held from before the take (DLY SYNC 1/1 at the last take's 100 BPM: 1.2 s)
    and its repeats ran into the first probe (485 + 513 ms; diagnosed on dly-long 3029f47)"""
    for c, v in ccs:
        if c == 88:
            tl.add(T_CHORD + 0.15, "usb", ("cc", 88, v))


def plan_range():
    """out-of-range clock holds the tempo: a Start and 12 beats at 120 (the tempo set), then 4 beats at 30 BPM
    (83 ms pulses) and 8 beats at 400 BPM (6.25 ms) with no Start: a probe in each; the echo must stay at 500 ms"""
    tl = Timeline(T_CLOCK)
    refs = _refs(tl, PROBE)
    tl.start()
    tl.run(120, 12, "start")
    kw = {}
    _probe(tl, tl.at_beat(8), kw)
    t1 = tl.t
    tl.run(30, 4, None)
    _probe(tl, t1 + 3.0, kw)
    t2 = tl.t
    tl.run(400, 8, None)
    _probe(tl, t2 + 0.6, kw)
    tl.pause(1.2)
    tl.stop()
    _delay_on_after_ref(tl, items()["range"]["ccs"])
    t = _take("range_120", "range", "delay", tl, tl.t + 1.6, first=refs[0], refs=refs, starts=tl.starts, stops=tl.stops, **kw)
    t.update(feature=None, ccs=list(items()["range"]["ccs"]), restart=False, mode="ext", div=None,
             probe_T=[0.5, 0.5, 0.5])
    return t


def plan_trsoff():
    """TRS IN OFF ignores the TRS clock: 12 beats at 120 over USB (the tempo set), then a Start and 16 beats at 100
    over the TRS path ('clk'): probes in it; the echo must stay at 500 ms"""
    tl = Timeline(T_CLOCK)
    refs = _refs(tl, PROBE)
    tl.starts.append(tl.add(tl.t - 0.001, "usb", ("start",)))
    tl.add(tl.t + 0.02, "usb", ("cc", CC_SEQ, 0))
    tl.run(120, 12, "start", dest="usb")
    tl.stops.append(tl.add(tl.t, "usb", ("stop",)))
    tl.pause(0.3)
    tl.start()
    tl.run(100, 16, None)
    tl.stop()
    kw = {}
    _probe(tl, tl.at_beat(8), kw)
    for k in (6, 12):
        _probe(tl, tl.t - (16 - k) * 0.6, kw)
    _delay_on_after_ref(tl, items()["delay_dig"]["ccs"])
    t = _take("trsoff_120", "trs_off", "delay", tl, tl.t + 1.6, first=refs[0], refs=refs, starts=tl.starts, stops=tl.stops, **kw)
    t.update(feature=None, ccs=list(items()["delay_dig"]["ccs"]), restart=False, mode="ext", div=None,
             probe_T=[0.5, 0.5, 0.5])
    return t


CC_QUERY, QUERY_CLOCK = 116, 99   # the firmware's clock counters (midi-clock fafd16b): replies CC 100 id, 101-102 value
CLOCK_IDS = {30: "pulses taken", 31: "count jumps", 32: "pulses the jumps added", 33: "pulses a gap estimate added",
             34: "out-of-range ends", 35: "source changes", 36: "history restarts", 37: "ignored-source pulses",
             38: "SPP at the last Start/Continue", 39: "SPP at its first pulse", 40: "SPP now", 41: "beat count",
             42: "sequencer step"}
COUNT_TOL = 2                    # pulses: SPP now (40) and the beat count (41) are both in pulses (fafd16b: clk_sp,
                                 # clk_k), not 16ths and beats (480 pulses read as 480 sixteenths: a false MISMATCH)


def parse_counters(msgs):
    """[(t, control, value)] of the FM-1's CC 100 / 101 / 102 replies -> [(t, {id: value})], a group per query
    (ids 30-42; value = CC 101 * 128 + CC 102: mod 16384)"""
    groups, cur, cur_t, pend = [], {}, None, None
    for t, c, v in msgs:
        if c == 100:
            if v in CLOCK_IDS and v in cur:              # an id again: the next query's answer
                groups.append((cur_t, cur))
                cur, cur_t = {}, None
            pend = [v, None, None]
            cur_t = t if cur_t is None else cur_t
        elif c == 101 and pend:
            pend[1] = v
        elif c == 102 and pend and pend[1] is not None:
            if pend[0] in CLOCK_IDS:
                cur[pend[0]] = pend[1] * 128 + v
            pend = None
    if cur:
        groups.append((cur_t, cur))
    return groups


def sent_counts(take, stamps, qi):
    """what was sent before query qi (schedule index): the pulses since the last Start / Continue, its SPP (a 16th =
    6 pulses; no SPP sent, so from 0) and its beats"""
    tq = stamps[qi]
    st = [stamps[j] for j in take["starts"] if stamps[j] <= tq]
    t0 = max(st) if st else -np.inf
    n = sum(1 for i, (_, _, m) in enumerate(take["sched"]) if m == ("clock",) and take["sched"][i][1] != "virt"
            and t0 < stamps[i] < tq)
    return dict(pulses=n, spp=n // 6, beats=n // 24)


def match_counters(take, stamps, replies):
    """each query with the reply that came after it (within 0.5 s) and the sent counts; a mismatch: SPP now (40) or
    the beat count (41), both pulses, more than COUNT_TOL pulses off the pulses sent. Returns [] when the firmware
    never answered"""
    if not replies:
        return []
    out = []
    for qi, lab in zip(take["queries"], take["query_labels"]):
        tq = stamps[qi]
        got = next((g for t, g in replies if tq - 0.005 <= t <= tq + 0.5), None)
        sent = sent_counts(take, stamps, qi)
        row = dict(label=lab, sent=sent, got={CLOCK_IDS[k]: v for k, v in sorted((got or {}).items())}, mismatch=[])
        if got is None:
            row["mismatch"].append("no reply")
        else:
            for k in (40, 41):
                if k in got:
                    d = (got[k] - sent["pulses"]) % 16384      # (the reply is mod 16384)
                    if min(d, 16384 - d) > COUNT_TOL:
                        row["mismatch"].append(f"{CLOCK_IDS[k]} {got[k]} pulses ({got[k] / 6:g} sixteenths, "
                                               f"{got[k] / 24:g} beats), sent {sent['pulses']} pulses")
        out.append(row)
    return out


def _take(name, item, kind, tl, dur, **kw):
    order = sorted(range(len(tl.ev)), key=lambda i: tl.ev[i][0])
    pos = {j: k for k, j in enumerate(order)}
    remap = lambda v: [pos[j] for j in v] if isinstance(v, list) else pos[v]  # noqa: E731
    return dict(name=name, item=item, kind=kind, sched=[tl.ev[i] for i in order], beats=[pos[j] for j in tl.beats],
                segs=tl.segs, dur=float(dur), **{k: remap(v) for k, v in kw.items()})


def restore_take(bpm=RESTORE_BPM):
    tl = Timeline(0.1)
    tl.run(bpm, 3, None)
    tl.stop()
    return dict(name="restore", sched=sorted(tl.ev, key=lambda e: e[0]), dur=tl.t + 0.1)


def take_tau(take, stamps, T0, rate, fs):
    """the take's events on the recording's clock: tau (s) = (stream time - T0) * rate / fs"""
    tau = (np.asarray(stamps, float) - T0) * rate / fs
    ev = dict(beats=[float(tau[j]) for j in take["beats"]], segs=take["segs"], first=float(tau[take["first"]]),
              item=take["item"], kind=take["kind"], feature=take.get("feature"), restart=bool(take.get("restart")),
              mode=take.get("mode", "ext"), div=take.get("div"), scale=float(rate / fs))
    if take.get("probe_T"):
        ev["probe_T"] = [float(v * rate / fs) for v in take["probe_T"]]
    for k in ("strikes", "probes", "starts", "stops", "refs"):
        if k in take:
            ev[k] = [float(tau[j]) for j in take[k]]
    return ev


# ---- the analysis (pure) -----------------------------------------------------------------------------------------


def onset(x, fs, t, thr=0.006, span=0.3):
    """the first sample over thr, or over a tenth of the window's peak when that is lower (a reference note behind a
    closed filter: 162 ms late on an absolute threshold), from 5 ms before t"""
    a = int(max(0, (t - 0.005) * fs))
    seg = np.abs(x[a:a + int(span * fs)])
    if len(seg):
        thr = max(0.0015, min(thr, 0.1 * float(seg.max())))
    hit = np.nonzero(seg > thr)[0]
    return (a + int(hit[0])) / fs if len(hit) else None


def envelope_db(x, fs, win=0.020, hop=0.002):
    w, h = max(1, int(win * fs)), max(1, int(hop * fs))
    c = np.concatenate(([0.0], np.cumsum(x.astype(float) ** 2)))
    st = np.arange(0, len(x) - w, h)
    return (st + w / 2) / fs, 10 * np.log10((c[st + w] - c[st]) / w + 1e-14)


def features(x, fs, kind):
    """(t, F (bands, frames) in dB, total dB): 'amp' the RMS envelope; 'spec' third-octave band levels against the
    total (a spectral shape: a level change alone moves none of them)"""
    if kind == "amp":
        t, e = envelope_db(x, fs)
        return t, e[None, :], e
    win, hop = 2048 if fs > 30000 else 1024, max(1, int(0.005 * fs))
    f = np.fft.rfftfreq(win, 1 / fs)
    edges = 150.0 * 2 ** (np.arange(0, 40) / 3.0)
    edges = edges[edges < min(10000.0, 0.45 * fs)]
    idx = np.unique(np.searchsorted(f, edges))
    st = np.arange(0, len(x) - win, hop)
    w = np.hanning(win).astype(np.float32)
    B = np.empty((len(idx) - 1, len(st)))
    tot = np.empty(len(st))
    xx = x.astype(np.float32)
    for c in range(0, len(st), 512):
        s = st[c:c + 512]
        P = np.abs(np.fft.rfft(xx[s[:, None] + np.arange(win)] * w, axis=1)) ** 2
        tot[c:c + 512] = 10 * np.log10(P.sum(1) + 1e-14)
        B[:, c:c + 512] = np.add.reduceat(P[:, idx[0]:idx[-1]], idx[:-1] - idx[0], axis=1).T
    t = (st + win / 2) / fs
    return t, 10 * np.log10(B + 1e-14) - tot[None, :], tot


def _detrend(t, E, w, cuts=()):
    """E (bands, n) less each band's weighted least-squares quadratic; a strike (cuts) starts a new level and slope"""
    u = (t - t.mean()) / (np.ptp(t) or 1.0)
    cols = [np.ones_like(u), u, u * u]
    for c in cuts:
        if t[0] < c < t[-1]:
            h = (t >= c).astype(float)
            cols += [h, h * u]
    A = np.stack(cols, 1)
    W = A * w[:, None]
    C = np.linalg.solve(W.T @ A + 1e-9 * np.eye(A.shape[1]), W.T @ E.T)
    return E - (A @ C).T


def _dtft(t, R, w, f):
    return np.sum(w * R * np.exp(-2j * np.pi * f * t), axis=-1)


def peak_freq(t, R, w, f_lo, f_hi, n=500):
    """the frequency of the largest summed |DTFT|^2 over the bands in [f_lo, f_hi], refined; and each band's p-p dB"""
    from scipy.optimize import minimize_scalar
    tt = t - t.mean()
    fs_ = np.linspace(f_lo, f_hi, n)
    E = np.exp(-2j * np.pi * np.outer(fs_, tt))                # (n, frames)
    mags = np.sum(np.abs((R * w) @ E.T) ** 2, axis=0)
    k = int(np.argmax(mags))
    d = fs_[1] - fs_[0]
    res = minimize_scalar(lambda f: -np.sum(np.abs(_dtft(tt, R, w, f)) ** 2), bounds=(fs_[k] - d, fs_[k] + d),
                          method="bounded", options=dict(xatol=1e-7))
    f = float(res.x)
    return f, 4 * np.abs(_dtft(tt, R, w, f)) / max(np.sum(w), 1e-9)


def _seg_grid(ev, si):
    """the segment's beat times (+ the next segment's first beat when a change continues the grid)"""
    segs, beats = ev["segs"], ev["beats"]
    sg = segs[si]
    cont = si + 1 < len(segs) and segs[si + 1]["kind"] == "change"
    return np.asarray(beats[sg["i0"]:sg["i1"] + (1 if cont else 0)], float)


def _pos(bb, tau):
    """the beat position at tau on the grid bb (extrapolated past its ends by its last / first interval)"""
    tau = np.asarray(tau, float)
    p = np.interp(tau, bb, np.arange(len(bb)))
    hi, lo = tau > bb[-1], tau < bb[0]
    p[hi] = len(bb) - 1 + (tau[hi] - bb[-1]) / (bb[-1] - bb[-2])
    p[lo] = (tau[lo] - bb[0]) / (bb[1] - bb[0])
    return p


def ref_latency(x, fs, ev, ons=None):
    """(the take's latency: the smaller of its reference notes' (s, None: no onset), {ref_latency_ms: each one's,
    ref_spread: True when they differ by over REF_SPREAD_S}). An onset from the window's very start (the sound already
    there: the reference before it still ringing) is none. ons: note_onsets (seq / arp), else onset()"""
    refs = ev.get("refs") or [ev["first"]]             # (an older take: the one reference)
    lats = []
    for t in refs:
        if ons is None:
            o = onset(x, fs, t)
            o = None if o is not None and o <= int(max(0.0, t - 0.005) * fs) / fs + 0.5 / fs else o   # (its 1st sample)
        else:
            near = ons[(ons >= t - 0.005) & (ons < t + 0.15)]
            o = float(near[0]) if len(near) else None
        lats.append(None if o is None else o - t)
    ok = [v for v in lats if v is not None]
    info = dict(ref_latency_ms=[None if v is None else round(1000 * v, 2) for v in lats])
    if len(ok) > 1:
        info["ref_spread"] = bool(max(ok) - min(ok) > REF_SPREAD_S)
    return (min(ok) if ok else None), info


def lfo_analyze(x, fs, ev):
    lat, info = ref_latency(x, fs, ev)
    res = dict(latency_ms=None if lat is None else round(1000 * lat, 2), periods=[], phases=[], **info)
    if lat is None:
        res["error"] = "no sound after the reference note"
        return res
    t, F, tot = features(x, fs, ev["feature"])
    mask = np.ones_like(t)
    for s in ev.get("strikes", []) + (ev.get("refs") or [ev["first"]]):
        mask[(t >= s + lat - 0.01) & (t < s + lat + STRIKE_MASK_S)] = 0
    mask[tot < -75] = 0
    cuts = [s + lat + STRIKE_MASK_S for s in ev.get("strikes", [])]
    for si, sg in enumerate(ev["segs"]):
        bb = _seg_grid(ev, si) + lat
        nb = sg["i1"] - sg["i0"]

        def window(a, n):
            lo_, hi_ = _beat_time(bb, a), _beat_time(bb, a + n)
            sel = (t >= lo_) & (t < hi_)
            w = np.hanning(sel.sum()) * mask[sel] if sel.sum() > 8 else mask[sel]
            return t[sel], F[:, sel], w, (hi_ - lo_) / n
        a = sg["lock"]
        # the period: the drift of the phase against the sent beats, one window a beat from the lock to the
        # segment's end (a rate eps off moves the phase eps of a cycle a beat; the DTFT peak of a short decaying,
        # re-struck window scattered by +-0.7 % on the FM-1)
        zs, bs, Ts = [], [], []
        g0, g1 = _beat_time(bb, a), _beat_time(bb, nb)
        gsel = (t >= g0) & (t < g1)
        Rg = _detrend(t[gsel], F[:, gsel], mask[gsel], cuts) if gsel.sum() > 16 else None   # (the whole span: a
        for b in range(a, nb if Rg is not None else a):                                      # quadratic a beat long
            lo_, hi_ = _beat_time(bb, b), _beat_time(bb, b + 1)                              # took the cycle away)
            sel = (t[gsel] >= lo_) & (t[gsel] < hi_)
            tw, w1, T = t[gsel][sel], mask[gsel][sel], hi_ - lo_
            if len(tw) > 8 and w1.mean() >= 0.7:
                R = Rg[:, sel]
                zs.append(np.sum(w1 * R * np.exp(-2j * np.pi * _pos(bb, tw)), axis=-1) / max(w1.sum(), 1e-9))
                bs.append(b + 0.5)
                Ts.append(T)
        if len(zs) >= max(4, (nb - a) // 2):
            Z = np.array(zs)                              # (windows, bands)
            k = int(np.argmax(np.abs(Z).mean(0)))
            # every band at once, each turned by its mean phase and weighted by its mean (one band, 'amp': the
            # same as that band alone): the phaser's band with the largest mean alone scattered 0.05-0.1 cycle a
            # beat (a restrike's decay, the partials' beating), its period -0.6 % on the host's exact LFO (its phase
            # snapshots: 0.00 %), -0.53 % on the FM-1 (int-final 82c6bed, USB and TRS)
            ph = np.unwrap(-np.angle(np.sum(Z * np.conj(Z.mean(0)), axis=1))) / (2 * np.pi)
            slope = float(np.polyfit(bs, ph, 1)[0])       # cycles a beat: the period is slope longer
            T = float(np.median(Ts))
            dep = float(4 * np.abs(Z[:, k]).mean())
            res["periods"].append(dict(seg=si, kind=sg["kind"], bpm=sg["bpm"], from_beat=a, sent_ms=round(1000 * T, 3),
                                       meas_ms=round(1000 * T * (1 + slope), 3), err_pct=round(100 * slope, 3),
                                       depth_db=round(dep, 2), windows=len(zs),
                                       ok=bool(abs(slope) <= PERIOD_TOL and dep >= MIN_DEPTH_DB)))
        while a + PHASE_WIN <= nb:
            tw, Fw, w, T = window(a, PHASE_WIN)
            if w.sum() > 10:
                R = _detrend(tw, Fw, w, cuts)
                C = np.sum(w * R * np.exp(-2j * np.pi * _pos(bb, tw)), axis=-1)
                res["phases"].append(dict(seg=si, kind=sg["kind"], bpm=sg["bpm"], from_beat=a,
                                          ph=[float((-np.angle(c) / (2 * np.pi)) % 1.0) for c in C],
                                          amp=[float(4 * abs(c) / w.sum()) for c in C]))
            a += PHASE_HOP
    return res


def _beat_time(bb, b):
    return float(bb[b]) if b < len(bb) else float(bb[-1] + (bb[-1] - bb[-2]) * (b - len(bb) + 1))


def expected_echo(ev, pi):
    """the echo time probe pi should get: the beat at the probe (the long division halved until it fits the line:
    the MANUAL's 'a synced echo longer than the delay's 1.48 s plays at half (or a quarter)'), or the take's own"""
    if ev.get("probe_T"):
        return float(ev["probe_T"][pi])
    beats, tp = np.asarray(ev["beats"], float), ev["probes"][pi]
    k0 = int(np.argmin(np.abs(beats - tp)))
    T = float(beats[k0 + 1] - beats[k0]) if k0 + 1 < len(beats) and beats[k0 + 1] - beats[k0] < 1.6 \
        else float(beats[k0] - beats[k0 - 1])
    if ev.get("div"):
        T *= ev["div"] / 96.0
        while T > DLY_MAX_S * ev.get("scale", 1.0):
            T /= 2
    return T


def delay_analyze(x, fs, ev):
    from numpy.lib.stride_tricks import sliding_window_view
    from scipy.signal import find_peaks
    step = max(1, int(round(fs / 4000)))
    k = max(1, int(0.001 * fs))
    env = np.convolve(np.abs(x.astype(float)), np.ones(k) / k, "same")[::step]
    efs = fs / step
    beats = np.asarray(ev["beats"], float)
    lat_, info = ref_latency(x, fs, ev)
    res = dict(probes=[], latency_ms=None if lat_ is None else round(1000 * lat_, 2), **info)
    probes = ev["probes"]
    for pi, tp in enumerate(probes):
        T = expected_echo(ev, pi)
        row = dict(probe=pi, sent_ms=round(1000 * T, 3))
        res["probes"].append(row)
        i0 = int(tp * efs)
        seg = env[i0:i0 + int(0.25 * efs)]
        if not len(seg) or seg.max() < 0.003:
            row.update(ok=False, error="no dry note")
            continue
        on = i0 + int(np.argmax(seg > 0.1 * seg.max()))
        L, ref = int(0.100 * efs), on - int(0.003 * efs)
        tpl = env[ref:ref + L].copy()
        dry_peak = tpl.max()
        tpl -= tpl.mean()
        t_end = probes[pi + 1] if pi + 1 < len(probes) else len(x) / fs
        hi = min(len(env) - L, int((t_end - 0.02) * efs))
        lo = ref + int(0.12 * efs)
        if hi - lo < 4:
            row.update(ok=False, error="no room for an echo")
            continue
        sw = sliding_window_view(env[lo:hi + L], L)
        swc = sw - sw.mean(1, keepdims=True)
        ncc = (swc @ tpl) / (np.linalg.norm(swc, axis=1) * np.linalg.norm(tpl) + 1e-12)
        pk, _ = find_peaks(ncc, height=0.7, distance=int(0.1 * efs))
        k2 = max(1, int(0.003 * efs))                    # an echo rises out of what was there before it
        pk = [p for p in pk if sw[p].max() >= DLY_MIN_REL * dry_peak and sw[p].max() >= 4 * sw[p][:k2].mean()]

        def lag(p):
            if 0 < p < len(ncc) - 1:
                y0, y1, y2 = ncc[p - 1], ncc[p], ncc[p + 1]
                den = y0 - 2 * y1 + y2
                p = p + (0.5 * (y0 - y2) / den if den else 0.0)
            return (lo + p - ref) / efs
        echoes = [lag(p) for p in pk][:2]
        row["echo_ms"] = [round(1000 * e, 3) for e in echoes]
        row["t_dry"], row["T"] = ref / efs, T
        if not echoes:
            row.update(ok=False, error="no echo")
            continue
        sp = [echoes[0]] + ([echoes[1] - echoes[0]] if len(echoes) > 1 else [])
        tol = DLY_TOL_S + DLY_TOL_REL * T
        row.update(spacing_ms=[round(1000 * s, 3) for s in sp], err_ms=[round(1000 * (s - T), 3) for s in sp],
                   tol_ms=round(1000 * tol, 3), ok=bool(all(abs(s - T) <= tol for s in sp)))
    if ev.get("item") == "delay_tape" and not ev.get("probe_T"):
        tape_judge(res, ev)
    return res


TAPE_TAU_UP, TAPE_TAU_DOWN = 1.89, 1.07   # the TAPE delay's designed glide to a new time (s): longer / shorter


def tape_time(t, T, d0, t0):
    """the TAPE delay's time at t: from d0 to T, exponentially from t0 (tau by the direction)"""
    if t <= t0 or d0 == T:
        return d0 if t <= t0 else T
    tau = TAPE_TAU_UP if T > d0 else TAPE_TAU_DOWN
    return T + (d0 - T) * np.exp(-(t - t0) / tau)


def tape_echoes(t_in, n, T, d0, t0):
    """the spacings of n echoes of a note at t_in: each read when t_e - d(t_e) = its input time"""
    out = []
    for _ in range(n):
        te = t_in + tape_time(t_in, T, d0, t0)
        for _ in range(30):
            te = t_in + tape_time(te, T, d0, t0)
        out.append(te - t_in)
        t_in = te
    return out


def tape_judge(res, ev):
    """the TAPE echoes against its designed glide: per segment the glide from the previous segment's time (the first
    segment: from a fitted time, the FM-1's tempo before the take) starting between the segment's first beat and a
    beat after it (the tempo is measured over a beat), fitted; each echo within the usual tolerance of the model"""
    beats, segs = ev["beats"], ev["segs"]
    rows = [r for r in res["probes"] if r.get("spacing_ms")]
    prevT = None
    for si, sg in enumerate(segs):
        b0 = beats[sg["i0"]]
        b1 = beats[sg["i1"]] if sg["i1"] < len(beats) else float("inf")
        mine = [r for r in rows if b0 - 0.05 <= r["t_dry"] < b1 - 0.05]
        T_seg = mine[0]["T"] if mine else prevT
        if mine:
            beat = 60.0 / sg["bpm"] * ev.get("scale", 1.0)
            d0s = [prevT] if prevT is not None else list(np.arange(0.2, 1.6, 0.005))
            best = None
            for d0 in d0s:
                for t0 in np.arange(b0 - 0.1, b0 + beat + 0.1, 0.01):
                    e = max(abs(m / 1000 - x) for r in mine for m, x in
                            zip(r["spacing_ms"], tape_echoes(r["t_dry"], len(r["spacing_ms"]), r["T"], d0, t0)))
                    if best is None or e < best[0]:
                        best = (e, d0, t0)
            _, d0, t0 = best
            for r in mine:
                exp = tape_echoes(r["t_dry"], len(r["spacing_ms"]), r["T"], d0, t0)
                tol = DLY_TOL_S + DLY_TOL_REL * r["T"]
                r["model_ms"] = [round(1000 * x, 3) for x in exp]
                r["err_ms"] = [round(m - 1000 * x, 3) for m, x in zip(r["spacing_ms"], exp)]
                r["ok"] = bool(all(abs(e) <= 1000 * tol for e in r["err_ms"]))
                r["tape_fit"] = dict(from_ms=round(1000 * d0, 1), start_beats=round((t0 - b0) / beat, 2))
        prevT = T_seg if T_seg is not None else prevT


def note_onsets(x, fs):
    """note onsets (s): on the signal high-passed at 2 kHz (the hammer's attack; a Rhodes note's body and a
    ringing previous note masked the rise), rises of >= 10 dB in 2 ms of the 1 ms RMS (0.25 ms hop) within 50 dB of
    the loudest, the largest within 80 ms (the FM-1: a second, smaller rise ~45 ms into a note), each placed where
    the level first comes within 6 dB of the rise's top"""
    from scipy.signal import butter, find_peaks, sosfilt
    if fs > 8000:
        x = sosfilt(butter(4, 2000, "hp", fs=fs, output="sos"), x.astype(float))
    t, e = envelope_db(x, fs, win=0.001, hop=0.00025)
    k = 8
    d = np.full_like(e, -99.0)
    d[k:] = e[k:] - e[:-k]
    pk, _ = find_peaks(np.where(e > e.max() - 50, d, -99), height=10.0, distance=int(0.080 / 0.00025))
    out, tops = [], []
    for p in pk:
        top = e[p:p + 12].max()
        a = max(0, p - k)
        i = a + int(np.argmax(e[a:p + 12] >= top - 6))
        out.append(float(t[i]))
        tops.append(top)
    tops = np.array(tops)
    keep = tops >= np.median(tops) - 15 if len(tops) else tops.astype(bool)   # (a note's attack, not a ripple)
    return np.array(out)[keep]


def grid_analyze(x, fs, ev):
    """seq / arp: every onset against the 1/16 grid of its segment, the pattern, bar 1 after each Start, Stop"""
    ons = note_onsets(x, fs)
    lat, info = ref_latency(x, fs, ev, ons)
    res = dict(latency_ms=None, segs=[], notes=len(ons), **info)
    if lat is None:
        res["error"] = "no sound after the reference note"
        return res
    res["latency_ms"] = round(1000 * lat, 2)
    segs, starts, stops = ev["segs"], ev.get("starts", []), ev.get("stops", [])
    seq = ev["kind"] == "seq"
    for si, sg in enumerate(segs):
        bb = _seg_grid(ev, si) + lat
        nb = sg["i1"] - sg["i0"]
        T = float(np.median(np.diff(bb)))
        t_from, t_to = _beat_time(bb, sg["lock"]) - 0.25 * T / 4, _beat_time(bb, nb) - 0.5 * T / 4
        o = ons[(ons >= t_from) & (ons < t_to)]
        p16 = _pos(bb, o) * 4 if len(o) else np.array([])
        n16 = np.round(p16)
        err = (p16 - n16) * T / 4
        med = float(np.median(err)) if len(err) else 0.0
        bad = np.abs(err - med) > GRID_TOL_S
        steps = (n16.astype(int) % 16) if len(n16) else np.array([], int)
        n_exp = sum(1 for i in range(4 * sg["lock"], 4 * nb) if not seq or i % 16 < len(SEQ_NOTES))
        row = dict(seg=si, kind=sg["kind"], bpm=sg["bpm"], from_beat=sg["lock"], found=int(len(o)), expected=n_exp,
                   median_ms=round(1000 * med, 2), worst_ms=round(1000 * float(np.max(np.abs(err - med))), 2) if len(err) else None,
                   off_grid=int(bad.sum()))
        probs = []
        if len(o) < GRID_MIN_FOUND * n_exp:
            probs.append(f"{len(o)} notes of {n_exp} expected")
        if bad.any():
            probs.append(f"{int(bad.sum())} notes off the 1/16 grid by up to {row['worst_ms']} ms (tolerance "
                         f"{1000 * GRID_TOL_S:.1f} ms around the median {row['median_ms']} ms)")
        if abs(med) > GRID_MED_S and ev.get("mode") != "int":
            probs.append(f"the notes {1000 * med:+.1f} ms off the sent grid (median)")
        if seq and len(steps) and np.any(steps >= len(SEQ_NOTES)):
            probs.append(f"notes on steps {sorted(set((steps[steps >= len(SEQ_NOTES)] + 1).tolist()))} (the pattern: 1-8)")
        if seq and sg["kind"] == "start" and ev.get("mode") != "int":   # bar 1: the first note after this Start
            s0 = bb[0]
            first = ons[(ons >= s0 - 0.02) & (ons < s0 + 2 * T)]
            if not len(first):
                probs.append("no note after the Start")
            else:
                e0 = float(first[0] - s0)
                row["bar1_ms"] = round(1000 * e0, 2)
                if abs(e0) > GRID_MED_S + GRID_TOL_S:
                    probs.append(f"the first note after the Start {1000 * e0:+.1f} ms off bar 1")
        row["problems"], row["ok"] = probs, not probs
        res["segs"].append(row)
    if seq:                                               # nothing from 40 ms after a Stop to the next Start
        for k, sp in enumerate(stops):
            nxt = [s for s in starts if s > sp]
            hi = (nxt[0] + lat - 0.005) if nxt else len(x) / fs
            late = ons[(ons > sp + lat + STOP_GUARD_S) & (ons < hi)]
            res.setdefault("stops", []).append(dict(stop=k, notes_after=int(len(late)), ok=not len(late)))
    return res


def _wrap(p):
    return (p + 0.5) % 1.0 - 0.5


def analyze(x, fs, ev):
    k = ev["kind"]
    r = (lfo_analyze if k == "lfo" else delay_analyze if k == "delay" else grid_analyze)(x, fs, ev)
    r.update(kind=k, item=ev["item"], mode=ev.get("mode", "ext"))
    return r


def judge(results, phase_ref=None):
    """results {take: analysis} -> (verdict, {item: dict(verdict, problems, ...)}): FAIL when any item fails; MANUAL
    items (the wah always; the chorus when its modulation is too small to read) named, never passed"""
    its = items()
    by = {}
    for name, r in results.items():
        by.setdefault(r["item"], []).append((name, r))
    out = {}
    for item, rs in by.items():
        it, probs, info = its.get(item, {}), [], {}
        for name, r in rs:
            if r.get("error"):
                probs.append(f"{name}: {r['error']}")
        kind = rs[0][1]["kind"]
        if kind == "lfo":
            wins = [(name, p) for name, r in rs for p in r.get("phases", [])]
            pers = [(name, p) for name, r in rs for p in r.get("periods", [])]
            if rs[0][1].get("mode") == "int":            # CLOCK INT: no beat to be on, the rate alone is judged
                wins = []
                info["phase"] = "not judged (CLOCK INT: no beat sent)"
            if wins:
                band = int(np.argmax(np.sum([p["amp"] for _, p in wins], axis=0)))
                ref = phase_ref if (phase_ref is not None and it.get("feature") == "amp") else \
                    float((np.angle(np.mean([np.exp(2j * np.pi * p["ph"][band]) for _, p in wins])) / (2 * np.pi)) % 1)
                info.update(band=band, phase_ref=round(ref, 4))
                for name, p in wins:
                    p["phase"], p["depth_db"] = round(p["ph"][band], 4), round(p["amp"][band], 2)
                    p["err"] = round(_wrap(p["ph"][band] - ref), 4)
                    p["ok"] = bool(abs(p["err"]) < PHASE_TOL and p["depth_db"] >= MIN_DEPTH_DB)
                    if not p["ok"]:
                        probs.append(f"{name}: phase {p['phase']:.3f} beat, {p['err']:+.3f} off the reference {ref:.3f} "
                                     f"(beats {p['from_beat']}..{p['from_beat'] + PHASE_WIN} of the {p['kind']} at "
                                     f"{p['bpm']} BPM; depth {p['depth_db']} dB)")
                rst = [r.get("restart_shift") for _, r in rs]
                for name, r in rs:
                    segs = sorted({p["seg"] for p in r.get("phases", [])})
                    if r.get("restart") is None and len(segs) >= 3:
                        a_ = [p for p in r["phases"] if p["seg"] == segs[1]][-1]
                        b_ = [p for p in r["phases"] if p["seg"] == segs[2]][0]
                        r["restart_shift"] = round(_wrap(b_["phase"] - a_["phase"]), 4)
                del rst
            for name, p in pers:
                if not p["ok"]:
                    probs.append(f"{name}: period {p['meas_ms']} ms against the sent {p['sent_ms']} ms "
                                 f"({p['err_pct']:+.2f} %, depth {p['depth_db']} dB) from beat {p['from_beat']} of the "
                                 f"{p['kind']} at {p['bpm']} BPM")
            if not pers or (not wins and rs[0][1].get("mode") != "int"):
                probs.append(f"{item}: no judged window")
            weak = bool(pers) and max(p["depth_db"] for _, p in pers) < MIN_DEPTH_DB
            if it.get("manual_if_weak") and (weak or not pers):
                out[item] = dict(verdict="MANUAL", problems=[], info=info,
                                 why=f"the modulation is too small to read ({max([p['depth_db'] for _, p in pers], default=0)}"
                                     f" dB p-p < {MIN_DEPTH_DB}): listen that it follows the tempo")
                continue
        elif kind == "delay":
            for name, r in rs:
                for p in r.get("probes", []):
                    if not p.get("ok"):
                        probs.append(f"{name}: probe {p['probe']}: " + (p.get("error") or
                                     f"echo spacing {p['spacing_ms']} ms against the sent {p['sent_ms']} ms "
                                     f"(tol {p['tol_ms']})"))
        else:
            for name, r in rs:
                for s in r.get("segs", []):
                    probs += [f"{name}: {s['kind']} at {s['bpm']} BPM from beat {s['from_beat']}: {q}" for q in s["problems"]]
                for s in r.get("stops", []):
                    if not s["ok"]:
                        probs.append(f"{name}: {s['notes_after']} notes after Stop {s['stop'] + 1}")
                if not r.get("segs") and not r.get("error"):
                    probs.append(f"{name}: no segment judged")
        out[item] = dict(verdict="FAIL" if probs else "PASS", problems=probs, info=info)
    for item, it in its.items():
        if it["kind"] == "manual" and item not in out:
            out[item] = dict(verdict="MANUAL", problems=[], why=it["why"])
    for item, rs in by.items():                          # the clock counters against what was sent
        for name, r in rs:
            for c in r.get("counters", []):
                for m in c["mismatch"]:
                    out[item]["problems"].append(f"{name}: clock counters {c['label']}: {m}")
                    out[item]["verdict"] = "FAIL"
    meas = [v for k, v in out.items() if k in by]
    fail = any(v["verdict"] == "FAIL" for v in out.values()) or not meas
    man = [k for k, v in out.items() if v["verdict"] == "MANUAL"]
    verdict = "FAIL" if fail else "PASS" + (f" (MANUAL: {', '.join(man)})" if man else "")
    return verdict, out


# ---- synthetic recordings (--selftest, tests/py/test_hil_clock.py) -------------------------------------------------

MODES = ("good", "wrongrate", "freephase")
BLOCK_S = 256 / 44100


def _burst(x, fs, s, f0, a=0.3, tau=0.06, click=0.0, rng=None):
    i = int(np.ceil(s * fs))
    if i >= len(x) or i < 0:
        return
    m = min(len(x) - i, int(6 * tau * fs))
    tt = np.arange(m) / fs + (i / fs - s)
    fade = np.minimum(1, (m - 1 - np.arange(m)) / max(1, int(0.01 * fs)))   # (no step at its end)
    x[i:i + m] += a * np.sin(2 * np.pi * f0 * tt) * np.exp(-tt / tau) * np.minimum(1, tt / 0.0005) * fade
    if click:                                            # the hammer: a short broadband knock
        c = min(m, int(0.004 * fs))
        x[i:i + c] += click * (rng or np.random.default_rng(0)).normal(0, 1, c) * np.exp(-np.arange(c) / (0.0008 * fs))


def _lfo_cycles(tau, ev, mode, beats, rng, factor, start_bpm=120):
    n = len(tau)
    fs = 1 / (tau[1] - tau[0])
    segs = ev["segs"]
    if mode == "wrongrate":
        return factor * segs[0]["bpm"] / 60.0 * tau + rng.random()
    fr = np.full(n, start_bpm / 60.0)
    for sg in segs:
        b = beats[sg["i0"]:sg["i1"]]
        if len(b) > 1:
            fr[tau >= b[1]] = sg["bpm"] / 60.0
    cyc = np.cumsum(fr) / fs + rng.random()
    if mode == "good":
        for si, sg in enumerate(segs):
            bb = _seg_grid(dict(ev, beats=list(beats)), si)
            cont = si + 1 < len(segs) and segs[si + 1]["kind"] == "change"
            lo = bb[1] if sg["kind"] == "start" else bb[0]
            hi = beats[segs[si + 1]["i0"]] if si + 1 < len(segs) and not cont else \
                (bb[-1] if cont else np.inf)
            sel = (tau >= lo) & (tau < hi)
            cyc[sel] = _pos(bb, tau[sel])
    return cyc


def synth(take, mode="good", fs=48000, seed=0, lat=0.0152, ppm=620.0, jitter=0.0004, factor=1.02):
    """a recording of the take as a firmware would play it, and its events on the recording clock. good: the LFOs on
    the sent beats (+ the latency) from a beat after each Start, the tempo followed, notes on the grid (+ up to an
    audio block late); wrongrate: never locks, factor x the take's first tempo (the delay, the grids too);
    freephase: the right rate, but a Start never re-phases (the LFOs, the arpeggiator)"""
    rng = np.random.default_rng(seed)
    sched = take["sched"]
    T = np.array([e[0] for e in sched]) + rng.normal(0, jitter, len(sched)) * [e[1] == "clk" for e in sched]
    rate = fs * (1 + ppm * 1e-6)
    ev = take_tau(take, T + 0.003 * rng.random(), 0.0, rate, fs)
    n = int(take["dur"] * fs)
    tau = np.arange(n) / fs
    beats = np.asarray(ev["beats"]) + lat
    bpm0 = ev["segs"][0]["bpm"]
    x = np.zeros(n)
    blk = lambda: rng.uniform(0, BLOCK_S)  # noqa: E731
    for t_ in ev.get("refs") or [ev["first"]]:
        _burst(x, fs, t_ + lat + blk(), 1046.5, click=0.05, rng=rng)
    t_chord = (ev.get("refs") or [ev["first"]])[-1] + T_CHORD - REF_T[-1]
    k = take["kind"]
    if k == "lfo":
        cyc = _lfo_cycles(tau, ev, mode, beats, rng, factor)
        s = np.sin(2 * np.pi * cyc)
        on = sorted(ev["strikes"] + [t_chord])
        chord = np.zeros(n)
        hi = np.zeros(n)
        for j, st in enumerate(on):
            st += lat
            e_ = on[j + 1] + lat if j + 1 < len(on) else n / fs
            sel = (tau >= st) & (tau < e_)
            tt = tau[sel] - st
            dec = np.exp(-tt / 6.0) * np.minimum(1, tt / 0.002)
            for m, f0 in enumerate((130.81, 261.63)):
                chord[sel] += (0.2 / (m + 1)) * np.sin(2 * np.pi * f0 * tt + m) * dec
            for m, f0 in enumerate((2093.0, 3136.0, 4186.0)):
                if f0 < 0.45 * fs:
                    hi[sel] += 0.05 * np.sin(2 * np.pi * f0 * tt + m) * dec
        if take["feature"] == "amp":
            x += (chord + hi) * (1.0 - 0.25 * (1 - s))
        else:
            x += chord * (1 + 0.3 * s) + hi * (1 - 0.3 * s)
    elif k == "delay":
        tape = take["item"] == "delay_tape" and not ev.get("probe_T")
        for pi, tp in enumerate(ev["probes"]):
            Tb = expected_echo(ev, pi) / (factor if mode == "wrongrate" else 1.0)
            sp = [Tb] * 4
            if tape and mode == "good":                   # the glide from the previous segment's time
                si = max(i for i, sg in enumerate(ev["segs"]) if ev["beats"][sg["i0"]] <= tp + 0.05)
                if si > 0:
                    sg = ev["segs"][si]
                    prev = [expected_echo(ev, j) for j, q in enumerate(ev["probes"]) if q < ev["beats"][sg["i0"]]]
                    t0 = ev["beats"][sg["i0"]] + 60.0 / sg["bpm"]
                    sp = tape_echoes(tp + lat, 4, Tb, prev[-1] if prev else Tb, t0 + lat)
            t_ = tp + lat
            for j in range(4):
                _burst(x, fs, t_, 1046.5, 0.4 * 0.45 ** j, 0.04)
                t_ += sp[j]
    else:
        times = []
        starts, stops = ev["starts"] or [ev["beats"][0]], ev["stops"]      # (CLOCK INT, the arp: no Start)
        for si, st in enumerate(starts):
            stop = min([s for s in stops if s > st], default=n / fs)
            segs_here = [j for j, sg in enumerate(ev["segs"]) if ev["beats"][sg["i0"]] >= st - 0.01
                         and ev["beats"][sg["i0"]] < stop]
            bb = np.concatenate([np.asarray(ev["beats"][ev["segs"][j]["i0"]:ev["segs"][j]["i1"]]) for j in segs_here])
            if mode == "wrongrate":
                d = 60.0 / (factor * bpm0) / 4
                g = bb[0] + d * np.arange(int((stop - bb[0]) / d) + 1)
            else:
                g = np.interp(np.arange(len(bb) * 4) / 4, np.arange(len(bb)), bb)
            if k == "seq":
                times += [tt for i, tt in enumerate(g) if i % 16 < len(SEQ_NOTES) and tt < stop]
            else:
                if mode == "freephase" and si > 0:
                    g = g + (times_ph := (prev_end - bb[0]) % (g[1] - g[0]))   # noqa: F841 (the old grid goes on)
                if si + 1 < len(starts):
                    prev_end = bb[0] + 0  # noqa: F841
                times += [tt for tt in g if tt < (starts[si + 1] if si + 1 < len(starts) else n / fs - 0.3)]
                prev_end = g[-1] + (g[1] - g[0])
        if k == "arp" and ev["starts"]:
            d0 = 60.0 / 120 / 4
            ph = rng.random() * d0
            times += [t_chord + ph + i * d0 for i in range(int((starts[0] - t_chord) / d0))]
        for j, tt in enumerate(sorted(times)):
            _burst(x, fs, tt + lat + blk(), 261.63 * 2 ** ((j % 4) / 12), 0.25, 0.25, click=0.05, rng=rng)
    x += rng.normal(0, 10 ** (-80 / 20), n)
    return x.astype(np.float32), ev


def selftest(fs=48000, verbose=True, runs=None):
    """every run's plan (CLOCK EXT, INT, TRS IN OFF): good must PASS (the MANUAL items named), the others FAIL
    (freephase: only where a Start must re-phase: EXT). Returns True when they do"""
    runs = runs or {"ext": plan(), "int": plan(mode="int"), "trsoff": plan(mode="trsoff")}
    ok = True
    for run, takes in runs.items():
        for mode in MODES:
            if mode == "freephase" and run != "ext":
                continue
            res = {}
            for tk in takes:
                x, ev = synth(tk, mode, fs, seed=sum(map(ord, tk["name"])))
                res[tk["name"]] = analyze(x, fs, ev)
            v, per = judge(res)
            good = v.startswith("PASS")
            ok &= good == (mode == "good")
            if verbose:
                fails = [k for k, p in per.items() if p["verdict"] == "FAIL"]
                print(f"{'ok  ' if good == (mode == 'good') else 'BAD '} selftest {run} {mode}: {v}; FAIL items: "
                      f"{', '.join(fails) or '-'}" + (f"; {per[fails[0]]['problems'][0]}" if fails else ""))
    return ok


# ---- the report --------------------------------------------------------------------------------------------------


def report_txt(via, results, verdict, per, meta):
    L = [f"hil_clock --via {via}: {verdict}"] + ([f"  {meta}"] if meta else [])
    for item, p in per.items():
        extra = p.get("why", "") or (f"band {p['info']['band']}, phase reference {p['info']['phase_ref']} beat"
                                     if p.get("info", {}).get("band") is not None else "")
        L.append(f"{p['verdict']:7s} {item}" + (f": {extra}" if extra else ""))
    for name, r in results.items():
        refs = r.get("ref_latency_ms")
        L.append(f"{name}: latency {r.get('latency_ms')} ms" + (f", {r['error']}" if r.get("error") else "")
                 + (f" (references {' / '.join(str(v) for v in refs)} ms" + (
                     f": over {1000 * REF_SPREAD_S:g} ms apart, a reference held up" if r.get("ref_spread") else "")
                    + ")" if refs and len(refs) > 1 else "")
                 + (f", clock late max {r['jitter_ms']} ms" if "jitter_ms" in r else ""))
        for p in r.get("periods", []):
            L.append(f"  {'ok  ' if p['ok'] else 'FAIL'} period {p['kind']} {p['bpm']} BPM from beat {p['from_beat']}: "
                     f"{p['meas_ms']} ms / sent {p['sent_ms']} ms ({p['err_pct']:+.3f} %), depth {p['depth_db']} dB")
        for p in r.get("phases", []):
            if "phase" in p:
                L.append(f"  {'ok  ' if p['ok'] else 'FAIL'} phase {p['kind']} {p['bpm']} BPM beats {p['from_beat']}.."
                         f"{p['from_beat'] + PHASE_WIN}: {p['phase']:.3f} ({p['err']:+.3f}), depth {p['depth_db']} dB")
        if r.get("restart_shift") is not None:
            L.append(f"  restart: the phase moved {r['restart_shift']:+.3f} beat across the Stop / Start")
        for p in r.get("probes", []):
            L.append(f"  {'ok  ' if p.get('ok') else 'FAIL'} delay probe {p['probe']}: spacing {p.get('spacing_ms')} ms / "
                     f"sent {p['sent_ms']} ms (tol {p.get('tol_ms')})" + (f" {p['error']}" if p.get("error") else ""))
        for s in r.get("segs", []):
            L.append(f"  {'ok  ' if s['ok'] else 'FAIL'} grid {s['kind']} {s['bpm']} BPM from beat {s['from_beat']}: "
                     f"{s['found']}/{s['expected']} notes, median {s['median_ms']} ms, worst {s['worst_ms']} ms"
                     + (f", bar 1 {s['bar1_ms']} ms" if "bar1_ms" in s else ""))
        for s in r.get("stops", []):
            L.append(f"  {'ok  ' if s['ok'] else 'FAIL'} stop {s['stop'] + 1}: {s['notes_after']} notes after it")
        for c in r.get("counters", []):
            g = c["got"]
            L.append(f"  {'FAIL' if c['mismatch'] else 'ok  '} counters {c['label']}: sent {c['sent']['pulses']} pulses "
                     f"(SPP {c['sent']['spp']}, beat {c['sent']['beats']}); FM-1 " +
                     ", ".join(f"{k} {v}" for k, v in g.items()) + (f"  MISMATCH {c['mismatch']}" if c["mismatch"] else ""))
    L += [f"problem: {q}" for p in per.values() for q in p["problems"]]
    return "\n".join(L) + "\n"


ROWS = [("trem", "TREM SYNC (tremolo)"), ("wah", "TREM SYNC on WAH"), ("phaser", "PHA SYNC"), ("chorus", "CHORUS SYNC"),
        ("filter", "LFO SYNC (the filter's LFO)"), ("motor", "VIBES MOTOR SYNC"), ("delay_dig", "DLY SYNC, DIG"),
        ("delay_ana", "DLY SYNC, ANA"), ("delay_tape", "DLY SYNC, TAPE"),
        ("delay_long", "DLY SYNC 1/1: the long echo halved"), ("arp", "the arpeggiator on the tempo"),
        ("seq", "the sequencer: Start / Continue from bar 1, Stop"), ("range", "out-of-range clock holds the tempo"),
        ("channel", "the CHANNEL filter never blocks the clock"), ("trs_off", "TRS IN OFF ignores the TRS clock"),
        ("int_clock", "CLOCK INT: an incoming clock changes nothing"), ("tap", "TAP tempo (PLAY)")]
COLS = [("int", "INT"), ("usb", "EXT-USB"), ("trs", "EXT-TRS")]


def summary_table(root=None):
    """row | INT | EXT-USB | EXT-TRS from the runs' report.json under root (OUT): PASS / FAIL / MANUAL, '-' where
    the row does not apply, 'not run' where its run is missing"""
    root = root or OUT
    rep_ = {}
    for c in ("int", "usb", "trs", "trsoff"):
        f = os.path.join(root, c, "report.json")
        if os.path.exists(f):
            rep_[c] = json.load(open(f))

    def cell(row, col):
        r = rep_.get(col)
        if row == "tap":
            return "MANUAL" if col == "int" else "-"
        if row == "trs_off":
            if col != "trs":
                return "-"
            t = rep_.get("trsoff")
            return t["items"].get("trs_off", {}).get("verdict", "?") if t else "MANUAL (run --via trsoff)"
        if row == "int_clock":
            if col != "int":
                return "-"
            if not r:
                return "not run"
            vs = [v["verdict"] for k, v in r["items"].items() if k in dict(ROWS) and v["verdict"] != "MANUAL"]
            return "PASS" if vs and all(v == "PASS" for v in vs) else "FAIL"
        if row in ("range", "channel") and col == "int":
            return "-"
        if not r:
            return "not run"
        if row == "channel":
            ch = r.get("channel")
            dd = r["items"].get("delay_dig", {}).get("verdict")
            if not ch or ch == 1:
                return "MANUAL (CHANNEL ALL / 1: set another to check)"
            return f"{'PASS' if dd == 'PASS' else 'FAIL'} (CHANNEL {ch})"
        return r["items"].get(row, {}).get("verdict", "not run")
    w = max(len(lbl) for _, lbl in ROWS)
    L = [f"{'row':{w}s} | " + " | ".join(f"{h:9s}" for _, h in COLS), "-" * (w + 40)]
    for k, lbl in ROWS:
        L.append(f"{lbl:{w}s} | " + " | ".join(f"{cell(k, c):9s}" for c, _ in COLS))
    txt = "\n".join(L) + "\n"
    os.makedirs(root, exist_ok=True)
    open(os.path.join(root, "summary.txt"), "w").write(txt)
    return txt


def write_report(via, results, meta="", phase_ref=None, out=None, channel=None):
    verdict, per = judge(results, phase_ref)
    if verdict.startswith("PASS") and any(r.get("incomplete") for r in results.values()):
        verdict = "INCOMPLETE"
    d = out or os.path.join(OUT, via)
    os.makedirs(d, exist_ok=True)
    txt = report_txt(via, results, verdict, per, meta)
    open(os.path.join(d, "report.txt"), "w").write(txt)
    json.dump(dict(via=via, verdict=verdict, items=per, takes=results, meta=meta, channel=channel,
                   limits=dict(period_pct=100 * PERIOD_TOL, phase_beats=PHASE_TOL, delay_ms=1000 * DLY_TOL_S,
                               delay_pct=100 * DLY_TOL_REL, grid_ms=1000 * GRID_TOL_S, grid_median_ms=1000 * GRID_MED_S,
                               lock_beats=LOCK_BEATS, track_beats=TRACK_BEATS)),
              open(os.path.join(d, "report.json"), "w"), indent=1, default=float)
    return verdict, txt


# ---- the device -------------------------------------------------------------------------------------------------


def try_realtime():
    """real-time scheduling for the calling thread where the OS allows it (best effort): what was granted"""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"))

        class SP(ctypes.Structure):
            _fields_ = [("prio", ctypes.c_int), ("pad", ctypes.c_char * 4)]
        libc.pthread_self.restype = ctypes.c_void_p
        for prio in (47, 40, 31):                            # SCHED_RR = 2 (macOS and Linux)
            if libc.pthread_setschedparam(ctypes.c_void_p(libc.pthread_self()), 2, ctypes.byref(SP(prio))) == 0:
                return f"SCHED_RR {prio}"
    except Exception as e:                                   # noqa: BLE001
        return f"normal priority ({e.__class__.__name__})"
    return "normal priority (SCHED_RR refused)"


class Sender(threading.Thread):
    """sends a schedule on absolute perf_counter times from t_start; stamps[i] = when message i went out"""

    def __init__(self, sched, ports, t_start, mido):
        super().__init__(daemon=True)
        self.sched, self.ports, self.t_start = sched, ports, t_start
        M = mido.Message
        self.msgs = []
        for _, dest, m in sched:
            k = m[0]
            self.msgs.append((dest, M("clock") if k == "clock" else M("start") if k == "start" else M("stop") if k == "stop"
                              else M("continue") if k == "continue"
                              else M("note_on", note=m[1], velocity=m[2]) if k == "note_on"
                              else M("note_off", note=m[1]) if k == "note_off" else M("control_change", control=m[1], value=m[2])))
        self.stamps = np.zeros(len(sched))
        self.prio = ""

    def run(self):
        import gc
        self.prio = try_realtime()
        gc_on = gc.isenabled()
        gc.disable()
        pc = time.perf_counter
        try:
            for i, (t, dst, _) in enumerate(self.sched):
                target = self.t_start + t
                if dst == "virt":                        # (CLOCK INT: the grid the FM-1 keeps by itself, not sent)
                    self.stamps[i] = target
                    continue
                while True:
                    d = target - pc()
                    if d <= 0:
                        break
                    time.sleep(d - 0.002 if d > 0.003 else 0)
                dest, msg = self.msgs[i]
                self.ports[dest].send(msg)
                self.stamps[i] = pc()
        finally:
            if gc_on:
                gc.enable()


def clock_offset(stream, n=15):
    best = None
    for _ in range(n):
        p0 = time.perf_counter()
        st = stream.time
        p1 = time.perf_counter()
        if best is None or p1 - p0 < best[0]:
            best = (p1 - p0, st - (p0 + p1) / 2)
    return best[1]


def find_port(names, want):
    hit = [n for n in names if want.lower() in n.lower()]
    if not hit:
        sys.exit(f"no MIDI output matching '{want}': {names}")
    return hit[0]


class ChannelPort:
    """a MIDI output whose channel messages go out on channel ch (0-based): the FM-1's MIDI page CHANNEL filters
    notes, CCs and program changes (clock, Start and Stop always pass)"""

    def __init__(self, port, ch):
        self.port, self.ch, self.name = port, ch, getattr(port, "name", "?")

    def send(self, msg):
        self.port.send(msg.copy(channel=self.ch) if hasattr(msg, "channel") else msg)


def find_channel(rig, cap, want=None):
    """the channel the FM-1 listens on: want (1-16), else the first of 1..16 whose note sounds on the USB audio
    (the MIDI page's CHANNEL is not readable over MIDI; ALL answers on 1). Returns 0-based, or None"""
    M = rig.mido.Message
    order = [want - 1] if want else list(range(16))
    for ch in order:
        rig.out.send(M("control_change", control=123, value=0, channel=ch))
    time.sleep(0.3)
    rig.wait_silence(10.0)
    for ch in order:
        st = cap.now()["usb"]
        rig.out.send(M("note_on", note=72, velocity=110, channel=ch))
        time.sleep(0.15)
        rig.out.send(M("note_off", note=72, channel=ch))
        x = cap.cut("usb", st, 0.3)
        if np.abs(x).max() * 32768 > 200:
            rig.wait_silence(10.0)
            return ch
    return None


def seq_record(rig):
    """the sequencer's test pattern: cleared, 1 bar, QUANT 1/16, no swing; stopped (the cursor on bar 1 step 1),
    REC, SEQ_NOTES step by step, REC off (tools/hil_seq.py's step recording)"""
    c = rig.cc_raw
    c(60, 0)
    c(43, 127)
    c(61, 1)
    c(62, 3)
    c(46, 50)
    time.sleep(0.05)
    c(60, 127)
    time.sleep(0.03)
    c(60, 0)
    time.sleep(0.15)
    c(63, 127)
    time.sleep(0.1)
    for n in SEQ_NOTES:
        rig._send("on", n, 100)
        time.sleep(0.08)
        rig._send("off", n, 0)
        time.sleep(0.08)
    c(63, 0)
    time.sleep(0.2)


def settle(rig, kind):
    """before a take: a random pause (a random LFO phase at the Start, whatever the firmware), silence, and for the
    seq take SAVE_WAIT_S more: the pattern's auto save (1.5 s after its last change, when silent) held the first
    reference note up on the FM-1"""
    time.sleep(random.uniform(0.3, 1.2))
    rig.wait_silence(15.0)
    if kind == "seq":
        time.sleep(SAVE_WAIT_S)


VIA_MODE = {"usb": "ext", "trs": "ext", "int": "int", "trsoff": "trsoff"}
PANEL = {"int": "CLOCK INT (MIDI page) and BPM {bpm} (TEMPO page) set on the panel (no MIDI for either)",
         "trsoff": "TRS IN OFF (MIDI page) set on the panel, the interface output --port wired to the TRS IN"}


def run_device(a, vias):
    sys.path.insert(0, HERE)
    import hil_scan as H
    import lock
    with lock.Lock("DEVICE", who="hil_clock", why=f"midi clock {','.join(vias)}", wait_s=a.lock_wait):
        rig = H.Rig()
        cap = rig.capture()
        ring, stream = cap.rings["usb"], cap.streams["usb"]
        fs = ring.sr
        ch = find_channel(rig, cap, a.channel)
        if ch is None:
            print("no note sounded on any MIDI channel (" + (f"channel {a.channel}" if a.channel else "1-16") +
                  "): is the FM-1's USB audio the input, LEVEL up?")
            return 2
        print(f"the FM-1 listens on MIDI channel {ch + 1}" + ("" if ch == 0 else " (its MIDI page CHANNEL)"), flush=True)
        rig.out = ChannelPort(rig.out, ch)
        replies = []                                      # the FM-1's CC 100-102 (the clock counters), timestamped
        try:
            inn = rig.mido.open_input(next(n for n in rig.mido.get_input_names()
                                           if any(k in n.lower() for k in H.PORT_KEYS)))
            inn.callback = lambda m: replies.append((time.perf_counter(), m.control, m.value)) \
                if m.type == "control_change" and m.control in (100, 101, 102) else None
        except (StopIteration, OSError):
            inn = None
        sys.setswitchinterval(0.001)
        codes = []
        for via in vias:
            takes = plan(a.bpm, a.change, a.items, VIA_MODE[via], a.int_bpm)
            if via in PANEL:
                print("needs " + PANEL[via].format(bpm=a.int_bpm), flush=True)
            ports = {"usb": rig.out, "clk": rig.out}
            if via in ("trs", "trsoff"):
                pn = find_port(rig.mido.get_output_names(), a.port)
                if any(k in pn.lower() for k in H.PORT_KEYS):
                    sys.exit(f"--port {a.port} is the FM-1's own USB port ({pn}): the TRS path needs the interface's")
                ports["clk"] = rig.mido.open_output(pn)
            d = os.path.join(OUT, via)
            os.makedirs(d, exist_ok=True)
            results, prio = {}, ""
            for tk in takes:
                for attempt in (1, 2):
                    rig.dry()
                    if tk["kind"] == "seq":
                        seq_record(rig)
                    for c, v in tk["ccs"]:
                        if not (tk["kind"] == "delay" and c == 88):   # (the type goes on after the reference)
                            rig.cc_raw(c, v)
                    settle(rig, tk["kind"])
                    off0 = clock_offset(stream)
                    t_start = time.perf_counter() + 0.1
                    T0 = t_start + off0
                    snd = Sender(tk["sched"], ports, t_start, rig.mido)
                    snd.start()
                    snd.join()
                    prio = snd.prio
                    x = cap.cut("usb", T0, tk["dur"], timeout=10.0)
                    gaps = list(ring.gaps)
                    off1 = clock_offset(stream)
                    rate = ring.rate(T0, tk["dur"]) or float(fs)
                    sp = snd.stamps
                    w = (sp - sp[0]) / max(sp[-1] - sp[0], 1e-9)
                    stamps = sp + off0 + w * (off1 - off0)
                    late = sp - (t_start + np.array([e[0] for e in tk["sched"]]))
                    clk = np.array([e[1] == "clk" for e in tk["sched"]])
                    jit = float(late[clk].max()) if clk.any() else 0.0
                    ev = take_tau(tk, stamps, T0, rate, fs)
                    r = analyze(x.mean(axis=1), fs, ev)
                    mine = [m for m in replies if sp[0] - 0.1 <= m[0] <= sp[-1] + 1.0]
                    cnt = match_counters(tk, list(sp), parse_counters(mine)) if "queries" in tk else []
                    if cnt:                               # (a firmware without the readout: nothing, silently)
                        r["counters"] = cnt
                    r.update(jitter_ms=round(1000 * jit, 2), rate_ppm=round((rate / fs - 1) * 1e6, 1), attempt=attempt,
                             gaps=[(round(t_ - T0, 3), round(1000 * g, 1)) for t_, g in gaps],
                             incomplete=bool(gaps or jit > JITTER_MAX_S))
                    np.savez_compressed(os.path.join(d, tk["name"] + ".npz"), x=(x * 32767).astype(np.int16), fs=fs,
                                        ev=json.dumps(ev))
                    print(f"{tk['name']} ({via}, attempt {attempt}): latency {r.get('latency_ms')} ms (references "
                          f"{r.get('ref_latency_ms')}{', HELD UP' if r.get('ref_spread') else ''}), clock late max "
                          f"{r['jitter_ms']} ms, USB {r['rate_ppm']:+.0f} ppm" + (f", gaps {r['gaps']}" if gaps else ""),
                          flush=True)
                    if not r["incomplete"]:
                        break
                results[tk["name"]] = r
            rt = restore_take(a.restore_bpm)
            Sender(rt["sched"], ports, time.perf_counter() + 0.05, rig.mido).run()
            verdict, txt = write_report(via, results, meta=f"fs {fs}, MIDI channel {ch + 1}, sender {prio}, clock over "
                                        f"{a.port if via in ('trs', 'trsoff') else 'the FM-1 USB port'}"
                                        + (f"; {PANEL[via].format(bpm=a.int_bpm)}" if via in PANEL else ""),
                                        phase_ref=a.phase_ref, channel=ch + 1)
            print(txt, end="")
            if via == "int" and verdict == "FAIL":
                print("(a FAIL in INT: is CLOCK INT set, BPM %d? A FM-1 on EXT follows the decoy clock at %d)"
                      % (a.int_bpm, DECOY_BPM))
            if via == "trs" and verdict == "FAIL":
                print("(a FAIL on TRS alone: is TRS IN ON on the MIDI page, CLOCK EXT, the cable in?)")
            codes.append(0 if verdict.startswith("PASS") else 1 if verdict == "FAIL" else 2)
        rig.dry()
        rig.cc_raw(43, 127)                                   # (the test pattern cleared)
        rig.cc_raw(120, 0)
    print(summary_table(), end="")
    return 1 if 1 in codes else max(codes)


def reanalyze(d, phase_ref=None):
    results = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".npz"):
            z = np.load(os.path.join(d, f))
            ev = json.loads(str(z["ev"]))
            if "kind" not in ev:
                print(f"({f}: from an older hil_clock, skipped)")
                continue
            results[f[:-4]] = analyze(z["x"].astype(float).mean(axis=1) / 32767, int(z["fs"]), ev)
    via = os.path.basename(d.rstrip("/"))
    old = os.path.join(d, "report.json")
    prev = json.load(open(old)) if os.path.exists(old) else {}
    # (every take in DIR judged again, into DIR's report: a run of a few --items leaves the others' takes there)
    verdict, txt = write_report(via, results, meta=(prev.get("meta") or "") + "; reanalyzed", phase_ref=phase_ref,
                                out=d, channel=prev.get("channel"))
    print(txt, end="")
    if os.path.dirname(os.path.abspath(d)) == os.path.abspath(OUT):
        print(summary_table(), end="")
    return 0 if verdict.startswith("PASS") else 1


def dry_run(a, vias):
    total = 0.0
    print(f"hil_clock plan, --via {','.join(vias)}" + (f" (the clock on '{a.port}')" if "trs" in vias else ""))
    takes = []
    for via in vias:
        tk = plan(a.bpm, a.change, a.items, VIA_MODE[via], a.int_bpm)
        print(f" --via {via}: {len(tk)} takes" + (f"; needs {PANEL[via].format(bpm=a.int_bpm)}" if via in PANEL else ""))
        takes += tk
    print("  per take over USB-MIDI: dry (every parameter's default + hil_scan DRY), the item's CCs; CLOCK EXT and "
          "TRS IN ON assumed (no CC)")
    for tk in takes:
        sch = tk["sched"]
        n_clk = sum(1 for e in sch if e[2] == ("clock",))
        tr = " ".join(f"{e[2][0].upper()}@{e[0]:.2f}" for e in sch if e[2][0] in ("start", "stop"))
        segs = ", ".join(f"{s['kind']} {s['bpm']} BPM beats {s['i0']}..{s['i1']} (judged from +{s['lock']})"
                         for s in tk["segs"])
        extra = " + the pattern step-recorded (CLEARED before and after)" if tk["kind"] == "seq" else ""
        print(f"  {tk['name']:15s} {tk['dur']:5.1f} s  CC {tk['ccs']}{extra}; {n_clk} clocks: {segs}; {tr}")
        total += tk["dur"] + (6.0 if tk["kind"] == "seq" else 4.0)
    for k, it in items().items():
        if it["kind"] == "manual":
            print(f"  {k:15s} MANUAL: {it['why']}")
    print(f"  then 3 beats at {a.restore_bpm} BPM, the dry state, the pattern cleared")
    print(f"  pass: LFO period within {100 * PERIOD_TOL} %, phase within {PHASE_TOL} beat (from {LOCK_BEATS} beats after "
          f"a Start / {TRACK_BEATS} after a change); delay spacing within {1000 * DLY_TOL_S:.0f} ms + "
          f"{100 * DLY_TOL_REL} %; seq / arp notes within {1000 * GRID_TOL_S:.1f} ms of the 1/16 grid")
    print(f"  ~{total / 60:.1f} min; reports {OUT}/<via>/report.txt, the table {OUT}/summary.txt")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--via", default="usb", help="a list of usb (CLOCK EXT, the clock over USB), trs (over --port into "
                    "the TRS IN), int (CLOCK INT + --int-bpm set on the panel), trsoff (TRS IN OFF set on the panel)")
    ap.add_argument("--int-bpm", type=int, default=INT_BPM, help="--via int: the BPM set on the TEMPO page")
    ap.add_argument("--summary", action="store_true", help="print the table from the last runs' reports")
    ap.add_argument("--port", default=os.environ.get("FRODHES_TRS_PORT", ""),
                    help="the interface output wired to the TRS IN (a substring; default $FRODHES_TRS_PORT)")
    ap.add_argument("--list", action="store_true", help="the MIDI ports")
    ap.add_argument("--items", default=None, type=lambda s: [v for v in s.split(",") if v],
                    help=f"a subset of {','.join(k for k, v in items().items() if v['kind'] != 'manual')}")
    ap.add_argument("--bpm", default=",".join(map(str, BPMS)), type=lambda s: [int(v) for v in s.split(",") if v])
    ap.add_argument("--change", default=f"{CHANGE[0]}:{CHANGE[1]}", type=lambda s: tuple(int(v) for v in s.split(":")),
                    help="FROM:TO: every item's take at FROM, with the change to TO and the restart")
    ap.add_argument("--restore-bpm", type=int, default=RESTORE_BPM)
    ap.add_argument("--channel", type=int, default=None, help="the FM-1's MIDI CHANNEL (1-16; default: found by "
                    "playing a note on each until one sounds)")
    ap.add_argument("--phase-ref", type=float, default=None, help="trem / motor: the expected phase (beats; a sine "
                    "LFO at 0 on the beat peaks at 0.25), else the item's common one")
    ap.add_argument("--lock-wait", type=float, default=600.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reanalyze", metavar="DIR")
    a = ap.parse_args(argv)
    a.vias = [v for v in a.via.split(",") if v]
    if any(v not in VIA_MODE for v in a.vias):
        ap.error("--via: a list of usb, trs, int, trsoff")
    if a.change[0] not in a.bpm:
        ap.error("--change FROM must be one of --bpm")
    bad = [i for i in (a.items or []) if i not in items() or items()[i]["kind"] == "manual"]
    if bad:
        ap.error(f"--items: unknown or not measured: {bad}")
    if {"trs", "trsoff"} & set(a.vias) and not a.port and not (a.list or a.selftest or a.reanalyze or a.summary):
        ap.error("--via trs needs --port NAME (or FRODHES_TRS_PORT); --list shows the ports")
    return a


def main(argv=None):
    a = parse_args(argv)
    if a.list:
        import mido
        print("outputs:\n  " + "\n  ".join(mido.get_output_names()) + "\ninputs:\n  " + "\n  ".join(mido.get_input_names()))
        return 0
    if a.selftest:
        return 0 if selftest() else 1
    if a.summary:
        print(summary_table(), end="")
        return 0
    if a.reanalyze:
        return reanalyze(a.reanalyze, a.phase_ref)
    if a.dry_run:
        return dry_run(a, a.vias)
    return run_device(a, a.vias)


if __name__ == "__main__":
    sys.exit(main())
