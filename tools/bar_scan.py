#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The SCAN of a bar TYPE (PLAN §9): every key, velocity, card, pedal, motor, effect, polyphony, sequencer / arp,
engine-swap and long-run case rendered by the host build (build/host/frodhes, tests/host.c scripts), each render
judged by the checks S1..S13 with their pass numbers. A type is done when --full reports 0 failures on S1..S10,
S12, S13 (and S11 flags nothing the FM-1 does not clear) and the user has listened.

  tools/bar_scan.py --quick                 VIBES (TYPE 4): F3..F6 every key + every 3rd outside, the main cases
  tools/bar_scan.py --full                  everything (every key 21..108, all velocities, the motor grid, 60 s run)
  tools/bar_scan.py --type 0 --quick        a Rhodes TYPE (the plumbing: Rhodes fails S3 by design, its pickup is
                                            not linear, and some others; numbers only)
  tools/bar_scan.py --smoke                 a handful of renders (the pytest)
  --checks S1,S9   only these   --jobs N   parallel renders
  -> build/bar_scan/report.txt, scan.json, summary.png, worst/<check>/*.wav (the 20 worst renders per check)

The checks (numbers from PLAN §9.2):
  S1  clicks: hil_scan's second-difference detector = 0, and HP bursts = 0: 1 ms windows whose energy above 8 kHz is
      > -40 dB re the window, > 10 dB over the HF energy 3 ms either side (a transient, not a high partial) and
      above -90 dBFS (not the last bit's rounding),
      outside 9 ms before / 17 ms after any event (3 ms + one block before: a block sounds from its start, so an
      event 0..5.8 ms early; after: one block of latency + the 8 ms damper noise)
  S2  clipping: output samples >= 32700 = 0; the host's "sat28 hits" = 0 (when the build prints it); the polyphony
      cases (8..16 voices at once) judge the clipping through the master (their "fx" copy: the limiter), RAW only sat28
  S3  linearity: a 3-note chord vs the sum of its notes alone (RAW, NOISE 0): residual <= -60 dB; no spectral line
      off the spec's partials (+-3 %) above -80 dB re V1, 0.2..2 s after the strike (s3_lines: why lines)
  S4  steals / ends: S1 in the polyphony and steal cases; the last non-zero sample before the end <= 1 LSB
      (-90 dBFS); exact zeros at the end (RAW: no limit cycles)
  S5  instability: after the fundamental's peak, its band never rises > 0.5 dB over its running maximum outside
      50 ms after an event (motor off); every render finite; exact zero once all notes end
  S6  pitch and decay vs the spec: tools/bar_freq_scan.py (+-1 c, T60 +-10 %)
  S7  unwanted noise: between-partial floor <= -80 dB re V1 (0.2..2 s, NOISE 0, RAW; Blackman-Harris, the lines'
      bands widened by the 64-bin smoothing's half width, which otherwise averages a line into the floor); every 1/3-octave band
      2..8 s after a released strike <= -96 dBFS; motor on: no line at k x 1378 Hz (the sub-block rate) above
      -80 dB re V1; NOISE 64: its difference to NOISE 0 <= -80 dB re the peak from 150 ms to the key-up (the
      damper's knock at the key-up is NOISE's too)
  S8  attack spike: peak / RMS(50..300 ms) at v127, no key > 3 dB above its neighbours' median (and the spec's
      peak_dbfs cap when the spec has one)
  S9  level evenness (LUFS 0..1 s, v100): neighbour step <= 1.5 dB; deviation from the +-3-key median <= 1.5 dB over
      F3..F6 (53..89), <= 3 dB outside; velocity monotonic (no drop > 0.5 dB); the type vs ST77 on the A3-E4-A4 chord
      within +-1 LU; each card's sweep moves the 0..300 ms level <= 6 LU
  S10 knob continuity: each card in 32 steps, a strike at each (1.5 s apart: on a bar at rest): level step <= 1.5 dB,
      centroid step <= 15 %
  S11 CPU proxy: the host's "cpu-proxy max" (the bar engine's cost model: its FM-1 time of the worst block, effects
      included) <= 4500 us, every render; the heavy ones (polyphony, corners, the long run, 8 edge strikes) once more
      planned as if every effect ran (FRODHES_FXUS=1080, RAW renders otherwise plan with none). A flag, not a gate:
      tools/hil_cpu.py on the FM-1 clears it (P7)
  S12 sleep / budget transparency: a render vs the same with the sleep and the budget off (FRODHES_NOSLEEP /
      FRODHES_NOBUDGET): sleep <= -50 dB, budget (8 notes, planned with every effect and with nearly twice that)
      <= -40 dB re the signal, in every 1 s window whose residual is above the 16-bit rounding (0.6 LSB RMS)
  S13 swap: no S1 hit outside the fade block (the 6 ms after the swap event); "ev_drop" = 0 (when printed); no stuck
      notes (exact zero at the end, RAW)
"""
import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bar_measure as bm  # noqa: E402

OUT = os.path.join(bm.ROOT, "build", "bar_scan")
VELS = [1, 8, 24, 40, 64, 88, 104, 120, 127]
CARD_VALS = [0, 32, 64, 96, 127]
FX_ALL_US = 1080                        # fx.c FX_US_*: tools/hil_cpu.py's heavy set (the trem slot = MOTOR)
S11_FLAG_US = 4500
# S9 card sweeps: the 0..300 ms level a card may move over 0..127 (PLAN 9.2: <= 6 LU "unless that is its job"). Since the
# cards-audible pass (2026-10-17) TUBE's level is its job (vibes.json card_level bell_span_db 12: the bare bar is the
# quieter one), WEIGHT's partly (the heavier mallet's body: T7b level_comp 0.6, ~5..7 LU), STRIKE's a little (the fuller
# centre, tone_span_db 3); an unintended jump on any other card still fails at 6
S9_CARD_LU = dict(TONE=4.0, BELL=13.0, HAMMER=8.0)


class Case:
    def __init__(self, name, group, events, end, checks, env=None, **meta):
        self.name, self.group, self.events, self.end, self.checks, self.meta = name, group, events, end, checks, meta
        self.env = env
        self.x = self.log = None


def fname(name):
    import re
    return re.sub(r"[^A-Za-z0-9.-]+", "_", name)


def ev_times(events):
    return [t for t, s in events if not s.startswith("midi 0xF8")]


# ---------------------------------------------------------------- the matrix (§9.1)
def keys_for(full):
    if full:
        return list(range(21, 109))
    return sorted(set(range(53, 90)) | set(range(21, 53, 3)) | set(range(90, 109, 3)))


def strikes(keys, vel, gap=1200, hold=700, t0=200):
    ev = []
    for i, n in enumerate(keys):
        ev += bm.note(t0 + i * gap, n, vel, hold)
    return ev, t0 + len(keys) * gap + 600


def L18():
    """a strength-2 orthogonal array for 7 three-level factors (Taguchi L18's columns 2..8): every pair of levels of
    every two cards appears"""
    return [[0, 0, 0, 0, 0, 0, 0], [0, 1, 1, 1, 1, 1, 1], [0, 2, 2, 2, 2, 2, 2], [1, 0, 0, 1, 1, 2, 2],
            [1, 1, 1, 2, 2, 0, 0], [1, 2, 2, 0, 0, 1, 1], [2, 0, 1, 0, 2, 1, 2], [2, 1, 2, 1, 0, 2, 0],
            [2, 2, 0, 2, 1, 0, 1], [0, 0, 2, 2, 1, 1, 0], [0, 1, 0, 0, 2, 2, 1], [0, 2, 1, 1, 0, 0, 2],
            [1, 0, 1, 2, 0, 2, 1], [1, 1, 2, 0, 1, 0, 2], [1, 2, 0, 1, 2, 1, 0], [2, 0, 2, 1, 2, 0, 1],
            [2, 1, 0, 2, 0, 1, 2], [2, 2, 1, 0, 1, 2, 0]]


def clock_lines(t0, t1, bpm=240):
    """MIDI clock (CLOCK EXT) at about bpm (24 a beat; the host steps whole ms)"""
    dt = max(8, int(round(60000 / bpm / 24)))
    return [(t, "midi 0xF8 0 0") for t in range(t0, t1, dt)]


def matrix(typ, full, smoke=False):
    C = []
    keys = keys_for(full)
    other = 0 if typ >= 4 else (1 if typ != 1 else 0)          # the swap partner (a Rhodes TYPE)
    S = lambda **kw: bm.setup_events(typ=typ, **kw)
    bars = bm.spec_for_type(typ)[1]          # the motor is the engine's on a bar TYPE (RAW keeps it), fx.c's AM else
    if smoke:
        keys = [57, 69, 81]
    # keyboard at v100 and v127 (S1 S2 S5 S8 S9), every key in one render (1.2 s apart)
    for v in (100, 127):
        ev, end = strikes(keys, v)
        C.append(Case(f"keys v{v}", "keys", S(noise=0) + ev, end, ["S1", "S2", "S5", "S8" if v == 127 else "S9"],
                      keys=keys, vel=v, gap=1200))
    # velocity: 4 keys x the velocity list (S9 monotonic), or every key with --full
    for n in (keys if full else [53, 65, 77, 89])[:3 if smoke else None]:
        ev = []
        for i, v in enumerate(VELS):
            ev += bm.note(200 + i * 900, n, v, 500)
        C.append(Case(f"vel {n}", "vel", S(noise=0) + ev, 200 + len(VELS) * 900 + 400, ["S1", "S2", "S9v"],
                      key=n, vels=VELS, gap=900))
    if smoke:
        return C
    # the type vs ST77 on A3-E4-A4 (S9)
    for t in sorted({typ, 0}):
        ev = bm.setup_events(typ=t, noise=0) + sum((bm.note(200, n, 100, 1200) for n in (57, 64, 69)), [])
        C.append(Case(f"chord type {t}", "chordcmp", ev, 2000, ["S1"], typ=t))
    # cards one at a time (S1 S2 S5, S9 card level), TUNE from the panel
    for card in bm.CARDS:
        for val in CARD_VALS:
            ev, end = strikes([53, 69, 89], 100, gap=1500, hold=900)
            C.append(Case(f"{card}={val}", "card", S(noise=0, **{card: val}) + ev, end, ["S1", "S2", "S5"],
                          card=card, val=val))
    for cents in (-50, 0, 50):
        ev, end = strikes([53, 69, 89], 100, gap=1500, hold=900)
        C.append(Case(f"TUNE={cents}", "card", S(noise=0) + bm.tune_lines(30, cents) + ev, end, ["S1", "S2"],
                      card="TUNE", val=cents))
    # pairs (L18) and the overflow corners (S1 S2)
    lv = [0, 64, 127]
    for i, row in enumerate(L18()):
        cc = {c: lv[k] for c, k in zip(bm.CARDS, row)}
        ev = []
        for j, (n, v) in enumerate(((53, 100), (69, 127), (89, 100), (60, 127))):
            ev += bm.note(200 + j * 1200, n, v, 800)
        C.append(Case(f"L18 #{i + 1}", "pairs", S(noise=cc.pop("NOISE"), **cc) + ev, 5400, ["S1", "S2", "S5"], cards=row))
    corners = [dict(BARK=127, BELL=127, TONE=0, HAMMER=127, DECAY=127), dict(BARK=127, BELL=127, TONE=127, HAMMER=127, DECAY=127),
               dict(BARK=127, BELL=0, TONE=127, HAMMER=127, DECAY=127), dict(BARK=0, BELL=127, TONE=0, HAMMER=0, DECAY=127)]
    for i, cc in enumerate(corners):
        ev = []
        for n in (41, 53, 69, 89, 101):
            for r in range(4):                                     # re-strikes 60 ms apart at v127
                ev += bm.note(200 + (n - 41) * 30 + r * 60 + 400 * (n // 12), n, 127)
        ev += [(4200, f"midi 0x80 {n} 0") for n in (41, 53, 69, 89, 101)]
        C.append(Case(f"corner {i + 1}", "corner", S(noise=0, **cc) + ev, 6000, ["S1", "S2", "S5"]))
    # S3 linearity: the chord and its notes alone
    for tag, notes in (("chord", (57, 64, 69)), ("57", (57,)), ("64", (64,)), ("69", (69,))):
        ev = S(noise=0) + sum((bm.note(200, n, 100, 1500) for n in notes), [])
        C.append(Case(f"lin {tag}", "lin", ev, 2400, ["S1"], notes=notes))
    # S7: sustained single notes; released tails; NOISE 64 vs 0; motor lines
    for n in (53, 69, 89):
        C.append(Case(f"floor {n}", "floor", S(noise=0) + bm.note(200, n, 100, 2200), 2600, ["S1", "S3t", "S7f"], key=n))
        C.append(Case(f"tail {n}", "tail", S(noise=0) + bm.note(200, n, 100, 300), 8400, ["S1", "S4", "S7t"], key=n))
        for nz in (0, 64):
            C.append(Case(f"noise{nz} {n}", "noise", S(noise=nz) + bm.note(200, n, 100, 600), 1600, ["S1"], key=n, nz=nz))
    # pedal: up, down, half, toggling, sweep (S1 S2 S4 S5)
    chord = (48, 55, 60, 64, 67, 72)
    ped = dict(up=[], down=[(150, "midi 0xB0 64 127")], half=[(150, "midi 0xB0 64 50")],
               toggle=[(400 + 100 * k, f"midi 0xB0 64 {127 if k % 2 == 0 else 0}") for k in range(20)],
               sweep=[(400 + 16 * k, f"midi 0xB0 64 {min(k, 254 - k) if k <= 254 else 0}") for k in range(255)])
    for name, pe in ped.items():
        ev = S(noise=None) + pe + sum((bm.note(200, n, 100, 500) for n in chord), []) + [(4600, "midi 0xB0 64 0")]
        C.append(Case(f"pedal {name}", "pedal", ev, 9000, ["S1", "S2", "S4", "S5"]))
    # the motor: rate x depth x shape (and synced 1/16 at 240 BPM)
    rates = [0, 64, 120, 127, "sync"] if full else [0, 64, 127, "sync"]
    depths = [0, 64, 127] if full else [0, 127]
    shapes = [0, 127] if full else [0]
    for r in rates:
        for d in depths:
            for sh in shapes:
                ev = S(noise=0, raw=bars, TW_SW=64, TW_MODE=127, TW_DEPTH=d, TW_SHAPE=sh)
                if r == "sync":
                    ev += [(20, "midi 0xB0 20 32")] + clock_lines(30, 3400)          # TREM SYNC 1/16
                else:
                    ev.append((20, f"midi 0xB0 19 {r}"))
                ev += bm.note(200, 69, 100, 3000)
                C.append(Case(f"motor r{r} d{d} s{sh}", "motor", ev, 3600, ["S1", "S2", "S7m"], rate=r, depth=d, shape=sh))
    # effects at their heaviest, each and all (not RAW)
    FX = [("drive", dict(DRIVE=127)), ("comp", dict(CMP_SW=127, CMP_AMT=127, CMP_RAT=127, CMP_GAIN=127)),
          ("filter", dict(FL_TYPE=40, FL_FREQ=60, FL_RESO=127, FL_LFO=127)), ("trem", dict(TW_SW=64, TW_DEPTH=127, TW_MODE=64)),
          ("wah", dict(TW_SW=127, TW_DEPTH=127)), ("phaser", dict(PH_SW=127, PH_DEPTH=127, PH_FDBK=127)),
          ("chorus", dict(CP_SW=64, CP_DEPTH=127)), ("delay", dict(DL_SW=80, DL_DEPTH=127, DL_FDBK=127)),
          ("reverb", dict(REV=127))]
    allfx = {}
    for _, d in FX:
        if "TW_SW" not in d or not allfx.get("TW_SW"):
            allfx.update(d)
    for name, d in FX + [("all", allfx)]:
        ev = bm.setup_events(typ=typ, raw=False, **d) + sum((bm.note(200, n, 100, 1500) for n in (57, 64, 69, 76)), [])
        C.append(Case(f"fx {name}", "fx", ev, 5000, ["S1c", "S2"]))
    # polyphony and steals (S1 S4)
    for nn in (8, 9, 12, 16):                                      # 9 = every voice (rhodes.c BAR_VOICES)
        notes = list(range(48, 48 + 3 * nn, 3))[:nn]
        ev = S(noise=None) + [(150, "midi 0xB0 64 127")] + sum((bm.note(200 + 10 * i, n, 100, 400) for i, n in enumerate(notes)), [])
        ev += [(4000, "midi 0xB0 64 0")]
        C.append(Case(f"poly {nn}", "poly", ev, 8000, ["S1", "S2m", "S4"]))
    ev = S(noise=None)
    chords = [(36, 48, 55, 60, 64, 67, 72, 76, 79, 84), (38, 50, 57, 62, 65, 69, 74, 77, 81, 86)]
    for k in range(16):
        ev += sum((bm.note(200 + 250 * k, n, 127, 240) for n in chords[k % 2]), [])
    C.append(Case("poly repeat", "poly", ev, 14000, ["S1", "S2m", "S4"]))
    # S2 for many voices at once: the same through the master (F01's effects, the look-ahead limiter)
    for c in [c for c in C if c.group == "poly"]:
        C.append(Case(f"{c.name} fx", "polyfx", [e for e in c.events if e[1] != "midi 0xB0 119 127"], c.end, ["S2"]))
    # the arpeggiator (UP, 1/16, 4 octaves, the pedal) and a recorded 2-bar pattern, 240 BPM
    ev = S(noise=None, ARP_MODE=1, ARP_RATE=3, ARP_OCT=3) + [(150, "midi 0xB0 64 127")] + clock_lines(30, 4200)
    ev += sum((bm.note(300, n, 100, 3000) for n in (60, 64, 67)), []) + [(3500, "midi 0xB0 64 0"), (3600, "midi 0xB0 52 0")]
    C.append(Case("arp up 1/16", "seqarp", ev, 9000, ["S1", "S2", "S13"]))
    ev = S(noise=None, SEQ_BARS=2, SEQ_QUANT=3) + [(25, "midi 0xB0 43 127")] + clock_lines(30, 9000)
    ev += [(100, "midi 0xB0 63 127"), (110, "midi 0xB0 60 127")]
    for k in range(16):                                            # 1/16 notes over two bars at 240 BPM (125 ms)
        ev += bm.note(120 + 125 * k, 60 + (k * 5) % 12, 100, 100)
    ev += [(2200, "midi 0xB0 63 0"), (6200, "midi 0xB0 60 0"), (6250, "midi 0xB0 123 0")]
    C.append(Case("seq 2 bars", "seqarp", ev, 12000, ["S1", "S2", "S13"]))
    # engine swaps while notes ring (S13)
    ev = S(noise=None) + sum((bm.note(200 + 5 * i, n, 100, 3000) for i, n in enumerate((48, 52, 55, 60, 64, 67, 71, 74))), [])
    for k in range(6):
        ev.append((600 + 400 * k, f"midi 0xB0 {bm.type_ccnum(other if k % 2 == 0 else typ)} {bm.type_cc(other if k % 2 == 0 else typ)}"))
    C.append(Case("swap cc80", "swap", ev, 8000, ["S13"], swaps=[600 + 400 * k for k in range(6)]))
    pc_other, pc_this = (12, 0) if typ >= 4 else (2, 0)            # F13 (VIBES) / F01; Rhodes: F03 / F01
    ev = [(20, "midi 0xB0 119 127")] + sum((bm.note(200 + 5 * i, n, 100, 2500) for i, n in enumerate((57, 64, 69, 76))), [])
    ev += [(700, f"midi 0xC0 {pc_other} 0"), (1400, f"midi 0xC0 {pc_this} 0"), (2100, f"midi 0xC0 {pc_other} 0")]
    C.append(Case("swap preset", "swap", ev, 7000, ["S13"], swaps=[700, 1400, 2100]))
    ev = S(noise=None, ARP_MODE=1, ARP_RATE=3, ARP_OCT=1) + clock_lines(30, 4300) + sum((bm.note(300, n, 100, 3500) for n in (60, 64, 67)), [])
    sweep = [(400 + 25 * k, f"midi 0xB0 80 {(k * 4) % 128}") for k in range(128)]
    ev += sweep + [(3700, "midi 0xB0 52 0"), (3800, f"midi 0xB0 {bm.type_ccnum(typ)} {bm.type_cc(typ)}")]
    C.append(Case("swap sweep arp", "swap", ev, 9000, ["S13"], swaps=[t for t, _ in sweep]))
    # the long run: pedal held, the 37 F3..F6 keys struck once each, the motor deep and fast
    dur = 60000 if full else 20000
    ev = S(noise=None, raw=bars, TW_SW=64, TW_MODE=127, TW_DEPTH=127, TW_RATE=120) + [(150, "midi 0xB0 64 127")]
    for i, n in enumerate(range(53, 90)):
        ev += bm.note(200 + i * (dur - 4000) // 37, n, 100, 200)
    ev += [(dur - 1500, "midi 0xB0 64 0")]
    C.append(Case(f"long {dur // 1000} s", "long", ev, dur + 12000, ["S1", "S2", "S4", "S5"]))
    # S11: the heavy renders planned as if every effect ran; S12: sleep and budget vs a render without them
    if bars:
        for c in [c for c in C if c.group in ("poly", "corner", "long")]:
            C.append(Case(f"cpu {c.name}", "cpu", c.events, c.end, [], env={"FRODHES_FXUS": str(FX_ALL_US)}))
        f3b = [53, 55, 57, 59, 60, 62, 64, 65]
        edge = S(noise=0, TONE=127, BARK=127, BELL=127, HAMMER=127) + [(150, "midi 0xB0 64 127")]
        for r in range(4):
            edge += [(200 + 400 * r, f"midi 0x90 {n} 127") for n in f3b]
        C.append(Case("cpu edge 8", "cpu", edge, 3000, [], env={"FRODHES_FXUS": str(FX_ALL_US)}))
        for tag, cc in (("TONE 64", {}), ("TONE 127", dict(TONE=127)), ("RUBBER edge", dict(TONE=127, BARK=127, HAMMER=127))):
            ev = S(noise=0, **cc) + [(150, "midi 0xB0 64 127")] + [(200, f"midi 0x90 {n} 100") for n in f3b]
            ks = list(range(53, 90, 3))
            ev2 = S(noise=0, **cc) + sum((bm.note(200 + 700 * i, n, 100, 500) for i, n in enumerate(ks)), [])
            for nm, e, end in ((f"chord {tag}", ev, 4000), (f"keys {tag}", ev2, 200 + 700 * len(ks) + 800)):
                C.append(Case(f"s12 sleep {nm}", "s12", e, end, [], env={"FRODHES_NOBUDGET": "1"}, kind="sleep", pair=nm))
                C.append(Case(f"s12 ref {nm}", "s12", e, end, [], env={"FRODHES_NOBUDGET": "1", "FRODHES_NOSLEEP": "1"},
                              kind="ref", pair=nm))
        for us in (FX_ALL_US, 2000):
            for nm, cc, v in (("yarn v100", {}, 100), ("rubber edge v127", dict(TONE=127, BARK=127, BELL=127, HAMMER=127), 127)):
                ev = S(noise=0, **cc) + [(150, "midi 0xB0 64 127")] + [(200, f"midi 0x90 {n} {v}") for n in f3b]
                C.append(Case(f"s12 budget {nm} fx {us}", "s12", ev, 2200, [], env={"FRODHES_FXUS": str(us)},
                              kind="budget", pair=f"b {nm} {us}"))
                C.append(Case(f"s12 nobudget {nm} fx {us}", "s12", ev, 2200, [], env={"FRODHES_NOBUDGET": "1"},
                              kind="ref", pair=f"b {nm} {us}"))
    # S10: each card in 32 steps, a strike at each on a bar at rest: 1.5 s apart, the key held 300 ms (the felt's
    # T60 is about 0.5 s, so the last strike is gone). 500 ms apart the bar was still ringing about -25 dB at the
    # next strike and the superposition's phase swung its 0..300 ms level by up to +-2.5 dB at any fixed card value
    G10 = 1500
    for card in bm.CARDS:
        ev = S(noise=0 if card != "NOISE" else None)
        for k in range(32):
            ev += [(150 + G10 * k, f"midi 0xB0 {bm.CC[card]} {min(127, k * 4 + (k == 31) * 3)}")] + bm.note(200 + G10 * k, 69, 100, 300)
        C.append(Case(f"steps {card}", "steps", ev, 200 + G10 * 32 + 500, ["S1", "S10"], card=card, gap=G10))
    if short_type():
        # MARIMBA: MALLET's family knees on the keys where the family ceiling turns HARD / RUBBER down most (G3 C4 E4:
        # up to 12 dB under DEFAULT, the steepest 4-raw steps of the linear map, 1.5..1.7 dB; A4 above is the
        # general case)
        for n in (55, 60, 64):
            ev = S(noise=0)
            for k in range(32):
                ev += [(150 + G10 * k, f"midi 0xB0 {bm.CC['BARK']} {min(127, k * 4 + (k == 31) * 3)}")] + bm.note(200 + G10 * k, n, 100, 300)
            C.append(Case(f"steps BARK {n}", "steps", ev, 200 + G10 * 32 + 500, ["S1", "S10"], card="BARK", gap=G10))
    return C


# ---------------------------------------------------------------- the checks (§9.2)
def onset_ms(t_ms):
    """where a strike scripted at t_ms starts sounding: the start of the block that holds it. tests/host.c renders 256
    frames whenever a block's worth of ms is owed and the main loop reads the MIDI after it, so the note's block is
    the next one and begins at floor(t FS / 256000) x 256 frames: 0..5.8 ms BEFORE t_ms (measured, MARIMBA steps BARK:
    0.05..4.7 ms). A strike window from t_ms missed that much of the attack: on a short note (MARIMBA C7, T60 0.11 s)
    the 0..300 ms level of the same strike then swung up to 2.8 dB with the block phase (S10's alternating steps)"""
    return (int(t_ms * bm.FS) // 256000) * 256 * 1000.0 / bm.FS + 1e-6     # (+ 1e-6: seg()'s int() lands on the frame)


def seg(x, t_ms, dur_ms):
    a = int(t_ms * bm.FS / 1000)
    return x[a:a + int(dur_ms * bm.FS / 1000)]


def strike_starts(case):
    return sorted(t for t, s in case.events if s.startswith("midi 0x90"))


# the guard before an event: 3 ms + one block (5.8 ms). The host (tests/host.c platform_poll), as the FM-1's ISR, renders
# the block that holds an event from that block's start, so a note sounds 0..5.8 ms BEFORE its script time (measured:
# MARIMBA steps BARK onsets 0.05..4.7 ms early); with 3 ms only, a hard mallet's own attack (RUBBER: a 0.2 ms contact,
# its first ms 10 dB over the HF 3 ms later) read as a "burst" whenever its block began over 3 ms early. As
# bar_measure.hp_bursts already guards (+ 6 ms)
PRE_MS = 9


def click_count(x, events, guard_ms=17):
    """S1 (a) + (b): clicks outside the event guards (PRE_MS before, guard_ms after), transient HF bursts"""
    m = bm.mono(x)
    d2 = np.abs(np.diff(m, 2))
    lvl = np.convolve(np.abs(m), np.ones(441) / 441, "same")[1:-1] + 50
    hit = np.flatnonzero(d2 > 6 * lvl + 2000)
    ok = np.ones(len(m), bool)
    for t in events:
        i = int(t * bm.FS / 1000)
        ok[max(0, i - int(PRE_MS * bm.FS / 1000)):i + int(guard_ms * bm.FS / 1000)] = False
    a = int(sum(ok[h] for h in hit))
    from scipy.signal import butter, sosfilt
    hp = sosfilt(butter(4, 8000, "hp", fs=bm.FS, output="sos"), m)
    w = bm.FS // 1000
    n = len(m) // w
    ea = (m[:n * w].reshape(n, w) ** 2).mean(axis=1)
    eh = (hp[:n * w].reshape(n, w) ** 2).mean(axis=1)
    share = 10 * np.log10(eh / (ea + 1e-20) + 1e-20)
    nb = np.maximum(np.concatenate([eh[:3], eh[:-3]]), np.concatenate([eh[3:], eh[-3:]]))   # the HF 3 ms either side
    g = np.ones(n, bool)
    for t in events:
        # the windows are w = 44 samples (0.998 ms), so an event's window is its sample // w, not its ms: int(t) drifted
        # 2.3 ms a second early (82 ms at 36 s), leaving a strike's own onset unguarded late in a long render (steps
        # BARK, MARIMBA HARD / RUBBER at 36..47 s: every "burst" was a strike's onset, within 1 ms)
        i = int(t * bm.FS / 1000) // w
        g[max(0, i - PRE_MS):i + guard_ms] = False
    loud = (ea > (32767 * 1e-4) ** 2) & (eh > (32767 * 10 ** (-90 / 20)) ** 2)   # above -80 dBFS, HF above -90
    b = int((g & loud & (share > -40) & (eh > 10 * nb)).sum())
    return a, b


DESIGN = {}                                         # the spec's design rows by key (main)


def note_window(sk, design=None):
    """S3 / S7's window after the strike: 0.2..2 s (VIBES: every V1 rings 2.4 s or more), on a note whose V1 (with its
    tube: the design's t60_v1) rings under 2 s (MARIMBA: 0.04..1.6 s) 0.05 s .. 0.05 s + its T60: the 60 dB it falls
    there. Past it the window reads the last LSBs of a dying sine, whose int16 rounding makes lines at its odd
    harmonics (A4 v100: -25 dB re V1 at 11 f0 over 0.2..2 s)"""
    t = (design or {}).get("t60_v1") or sk["modes"][0].get("t60") or 9.0
    return (0.2, 2.0) if t >= 2.0 else (0.05, 0.05 + t)


def short_type():
    """a type of short notes: its design's C4 V1 (with the tube) rings under 2 s (MARIMBA 0.88 s; VIBES 6 s)"""
    t = (DESIGN.get(60) or {}).get("t60_v1")
    return bool(t) and t < 2.0


def s8_window(n):
    """S8's body window for the peak / RMS: 50..300 ms (VIBES, every key as before), and on a type of short notes
    (short_type: MARIMBA, the design's t60_v1 2.3 .. 0.04 s over 21..108) T60 / 32 .. 3 T60 / 16 of the key's own V1,
    the same share of its decay on every key (= 50..300 ms at C2's 1.6 s). A fixed 50..300 ms read how far a short note
    had decayed, not its attack: peak / RMS rose with the key 4 -> 98 dB (C8, T60 39 ms: gone by 50 ms), and the
    fastest-changing keys at the top (102 / 105 / 108) stood 3 dB over their neighbours' median by the decay alone"""
    t = (DESIGN.get(n) or {}).get("t60_v1")
    return (t / 32, 3 * t / 16) if t and short_type() else (0.05, 0.3)


_GOLD = {}


def golden_ref(n):
    """the golden model (tools/bars_model.py, the scan's spec) for the floor case's note: v100, default cards, sleep as
    the C, rounded to int16 as the host's output: what a linear modal render reads on a short note's S3 / S7 window
    (its partials' skirts and the rounding of their last LSBs), the reference for notes under 2 s"""
    if n not in _GOLD:
        import bars_model as gm
        g = np.round(gm.render_batch(SPEC_FULL[0], [n], [100], 2.3)[0])
        _GOLD[n] = np.stack([g, g], 1)
    return _GOLD[n]


SPEC_FULL = [None]                                  # the scan's spec file, whole (main)


def s7_floor(x, parts, w0, w1):
    """S7 (single note): the between-partial floor, 64-bin smoothed, re the strongest line, w0..w1 s after the strike"""
    from scipy.signal import find_peaks
    from scipy.signal.windows import blackmanharris
    from scipy.ndimage import uniform_filter1d
    s_ = bm.mono(x)
    st = int(np.argmax(np.abs(s_) > 0))
    s_ = s_[st + int(w0 * bm.FS):st + int(w1 * bm.FS)]
    sp = 20 * np.log10(np.abs(np.fft.rfft(s_ * blackmanharris(len(s_)))) + 1e-9)
    fr = np.fft.rfftfreq(len(s_), 1 / bm.FS)
    pk = sp.max()
    idx, _ = find_peaks(sp, height=pk - 60, distance=20)        # every line within 60 dB: a partial, kept
    keep = np.ones(len(sp), bool)
    for f in parts + list(fr[idx]):                            # the band, widened by the smoothing's half
        keep &= ~((fr > f * 0.97 - 32 * fr[1]) & (fr < f * 1.03 + 32 * fr[1]))   # width (64 bins)
    keep &= (fr > 30) & (fr < 16000)
    return float(uniform_filter1d(sp, 64)[keep].max() - pk) if keep.any() else -240


def s3_lines(x, sk, t0=0.2, t1=2.0):
    """S3 (single note): the strongest spectral line that is not a designed partial (+-3 %), its power (+-4 bins) re
    V1's (+-3 %), 0.2..2 s after the strike; a line is a local peak 10 dB over the median floor (401 bins). Why lines,
    not the broadband energy outside the partials (the plan's first wording): a linear modal engine's distortion or
    intermodulation can only show as lines at harmonic / combination frequencies, while the energy between them is
    the 16-bit output's rounding: a pure decaying sine rounded to int16 at the render's level reads -57 dB re V1 at
    F6 (-62 at F3) on that broadband measure, exactly what the engine reads. Blackman-Harris (sidelobes -92 dB): a
    Hann window leaks -63 dB of a decaying F3 line into its +-3 % neighbourhood. Returns (dB, Hz, broadband dB)."""
    from scipy.signal import find_peaks
    from scipy.signal.windows import blackmanharris
    from scipy.ndimage import median_filter
    m = bm.mono(x)
    st = int(np.argmax(np.abs(m) > 0))
    s_ = m[st + int(t0 * bm.FS):st + int(t1 * bm.FS)]
    sp = np.abs(np.fft.rfft(s_ * blackmanharris(len(s_)))) ** 2
    fr = np.fft.rfftfreq(len(s_), 1 / bm.FS)
    parts = [md["f"] for md in sk["modes"] if md.get("f", 0) > 1]
    v1 = (fr > parts[0] * 0.97) & (fr < parts[0] * 1.03)
    e1 = sp[v1].sum() + 1e-30
    keep = np.ones(len(sp), bool)
    for f in parts:
        keep &= ~((fr > f * 0.97) & (fr < f * 1.03))
    bb = 10 * np.log10(sp[keep].sum() / e1 + 1e-30)
    L = 10 * np.log10(sp + 1e-30)
    flo = median_filter(L, 401)
    idx, _ = find_peaks(L, distance=4)
    worst, wf = -240.0, 0.0
    for i in idx:
        if fr[i] < 20 or not keep[i] or L[i] < flo[i] + 10:
            continue
        r = 10 * np.log10(sp[max(0, i - 4):i + 5].sum() / e1)
        if r > worst:
            worst, wf = r, float(fr[i])
    return worst, wf, bb


def run_checks(case, spec, typ, all_cases):
    x, log, R = case.x, case.log, {}
    evs = ev_times(case.events)
    cnt = bm.counters(log)
    for ck in case.checks:
        if ck in ("S1", "S1c"):
            a, b = click_count(x, evs)
            if ck == "S1c":
                R["S1"] = (a == 0, a, f"clicks {a} (effects: bursts not judged)")
            else:
                R["S1"] = (a == 0 and b == 0, a + b, f"clicks {a}, HF bursts {b}")
        elif ck == "S2":
            clip = int((np.abs(x) >= 32700).sum())
            s28 = cnt["sat28 hits"]
            R["S2"] = (clip == 0 and not s28, clip + (s28 or 0),
                       f"samples >= 32700: {clip}; sat28 hits {'n/a' if s28 is None else s28}")
        elif ck == "S2m":
            # several voices at once in RAW: the sum of 8..10 voices at v127, each at the Rhodes level, is over full
            # scale by arithmetic (+18..20 dB over one voice; the Rhodes clips 1041 samples on "poly repeat" RAW,
            # VIBES 80); RAW taps the engine ahead of the master, where the limiter is. Judged here: no internal
            # overflow (sat28); the clipping on the "<case> fx" render through the master, the per-voice peak on the
            # single-voice renders (S2)
            clip = int((np.abs(x) >= 32700).sum())
            s28 = cnt["sat28 hits"]
            R["S2"] = (not s28, s28 or 0, f"sat28 hits {'n/a' if s28 is None else s28}; RAW sum of voices: samples >= "
                                          f"32700: {clip} (not judged: no limiter in RAW; see '{case.name} fx')")
        elif ck == "S4":
            m = np.abs(bm.mono(x))
            nz = np.flatnonzero(m > 0)
            tail_zero = len(nz) == 0 or nz[-1] < len(m) - int(0.3 * bm.FS)
            last = float(m[nz[-1]]) if len(nz) else 0.0
            a, b = click_count(x, evs)
            R["S4"] = (tail_zero and last <= 1.0 and a + b == 0, a + b + (0 if tail_zero else 1),
                       f"clicks {a}, bursts {b}, last non-zero {last:.0f} LSB at {nz[-1] / bm.FS if len(nz) else 0:.2f} s, "
                       f"{'exact zero at the end' if tail_zero else 'NOT silent at the end'}")
        elif ck == "S5":
            m = bm.mono(x)
            finite = bool(np.all(np.isfinite(m)))
            worst = 0.0
            if case.group in ("keys", "card", "pairs", "corner"):
                starts = strike_starts(case)
                notes = [int(s.split()[2]) for t, s in sorted(case.events) if s.startswith("midi 0x90")]
                for t, n in zip(starts, notes):
                    f0 = 440 * 2 ** ((n - 69) / 12)
                    if spec and n in spec:
                        f0 = spec[n]["modes"][0]["f"]
                    nxt = [u for u in evs if u > t + 1]
                    end = min([u for u in nxt] + [case.end]) - t
                    s_ = seg(x, t, max(200, end))
                    tt, env = bm.band_env(s_, f0, n=4096, hop=512)
                    i0 = int(np.argmax(env))
                    if len(env) > i0 + 2:                  # a rise over the running maximum after the peak
                        later = env[i0 + 1:] - np.maximum.accumulate(env[i0:-1])
                        worst = max(worst, float(later.max()))
            R["S5"] = (finite and worst <= 0.5, worst, f"largest rise over the running maximum {worst:.2f} dB")
        elif ck == "S8":
            keys, gap = case.meta["keys"], case.meta["gap"]
            pr = []
            for i, n in enumerate(keys):
                s_ = bm.mono(seg(x, onset_ms(200 + i * gap), gap))
                st = int(np.argmax(np.abs(s_) > 0))
                s_ = s_[st:]
                w0, w1 = s8_window(n)
                pk = np.abs(s_[:int(max(0.3, w1) * bm.FS)]).max() if len(s_) else 0
                rms = np.sqrt(np.mean(s_[int(w0 * bm.FS):int(w1 * bm.FS)] ** 2)) + 1e-9
                pr.append(20 * np.log10(pk / rms + 1e-12))
            pr = np.array(pr)
            med = np.array([np.median(np.delete(pr[max(0, i - 3):i + 4], min(i, 3))) for i in range(len(pr))])
            over = pr - med
            bad = [keys[i] for i in np.flatnonzero(over > 3)]
            case.meta["peak_rms"] = pr.tolist()
            R["S8"] = (not bad, float(over.max()), f"peak/RMS {pr.min():.1f}..{pr.max():.1f} dB; > 3 dB over the "
                                                   f"neighbours: {bad or 'none'}")
        elif ck == "S9":
            keys, gap = case.meta["keys"], case.meta["gap"]
            lv = np.array([bm.lufs(seg(x, onset_ms(200 + i * gap), 1000)) for i in range(len(keys))])
            case.meta["lufs"] = lv.tolist()
            step = np.abs(np.diff(lv)) if len(lv) > 1 else np.zeros(1)
            med = np.array([np.median(lv[max(0, i - 3):i + 4]) for i in range(len(lv))])
            dev = np.abs(lv - med)
            lim = np.array([1.5 if 53 <= n <= 89 else 3.0 for n in keys])
            bad = [keys[i] for i in np.flatnonzero(dev > lim)] + [f"{keys[i]}-{keys[i + 1]}" for i in np.flatnonzero(step > 1.5)
                                                                  if abs(keys[i + 1] - keys[i]) == 1]
            R["S9"] = (not bad, float(max(step.max(), dev.max())), f"LUFS {lv.min():.1f}..{lv.max():.1f}; largest "
                                                                   f"neighbour step {step.max():.2f}, deviation {dev.max():.2f}; out: {bad or 'none'}")
        elif ck == "S9v":
            lv = [bm.lufs(seg(x, onset_ms(200 + i * case.meta["gap"]), 500)) for i in range(len(case.meta["vels"]))]
            drop = float(max(0.0, -np.diff(lv).min()))
            R["S9"] = (drop <= 0.5, drop, f"key {case.meta['key']}: LUFS v1..v127 {lv[0]:.1f}..{lv[-1]:.1f}, largest drop {drop:.2f} dB")
        elif ck == "S10":
            card, gap = case.meta["card"], case.meta["gap"]
            lv, ce = [], []
            for k in range(32):
                s_ = seg(x, onset_ms(200 + gap * k), 300)
                lv.append(bm.lufs(s_)); ce.append(bm.centroid(s_))
            dl = float(np.abs(np.diff(lv)).max())
            dc = float((np.abs(np.diff(ce)) / (np.array(ce[:-1]) + 1e-9)).max())
            case.meta["sweep_lufs"] = lv
            R["S10"] = (dl <= 1.5 and dc <= 0.15, max(dl / 1.5, dc / 0.15),
                        f"{card}: largest level step {dl:.2f} dB, centroid step {100 * dc:.1f} %; sweep range "
                        f"{max(lv) - min(lv):.1f} LU")
        elif ck == "S3t":
            n = case.meta["key"]
            if not spec or n not in spec:
                R["S3"] = (None, None, "n/a: needs the spec's partials")
                continue
            w0, w1 = note_window(spec[n], DESIGN.get(n))
            r, f, bb = s3_lines(x, spec[n], w0, w1)
            if (w0, w1) == (0.2, 2.0):
                R["S3"] = (r <= -80, float(r), f"strongest line off the partials {r:.1f} dB re V1"
                                              + (f" at {f:.0f} Hz" if f else "") + f" (broadband outside {bb:.1f} dB, the 16-bit floor)")
            else:
                g = s3_lines(golden_ref(n), spec[n], w0, w1)[0]
                R["S3"] = (r <= max(-80.0, g + 3.0), float(r), f"strongest line off the partials {r:.1f} dB re V1"
                           + (f" at {f:.0f} Hz" if f else "") + f", the golden model's int16 render {g:.1f} dB ({w0:.2f}.."
                           f"{w1:.2f} s; <= -80 or within 3 dB of it)")
        elif ck == "S7f":
            n = case.meta["key"]
            w0, w1 = note_window(spec[n], DESIGN.get(n)) if spec and n in spec else (0.2, 2.0)
            parts = [m["f"] for m in spec[n]["modes"] if m.get("f", 0) > 1] if spec and n in spec else []
            floor = s7_floor(seg(x, 200, 2200), parts, w0, w1)
            if (w0, w1) == (0.2, 2.0):
                R["S7"] = (floor <= -80, floor, f"between-partial floor {floor:.1f} dB re the strongest line")
            else:
                g = s7_floor(golden_ref(n), parts, w0, w1)
                R["S7"] = (floor <= max(-80.0, g + 3.0), floor, f"between-partial floor {floor:.1f} dB re the strongest "
                           f"line, the golden model's int16 render {g:.1f} dB ({w0:.2f}..{w1:.2f} s; <= -80 or within 3 dB)")
        elif ck == "S7t":
            s_ = bm.mono(seg(x, 200 + 2000, 6000))
            sp = np.abs(np.fft.rfft(s_ * np.hanning(len(s_)) if len(s_) else np.zeros(16))) ** 2
            fr = np.fft.rfftfreq(len(s_), 1 / bm.FS)
            worst = -240.0
            f = 25.0
            while f < 16000:
                b = (fr >= f / 2 ** (1 / 6)) & (fr < f * 2 ** (1 / 6))
                if b.any():
                    e = sp[b].sum() * 2 / (np.hanning(len(s_)) ** 2).sum() / len(s_)
                    worst = max(worst, 10 * np.log10(e / 32767 ** 2 + 1e-30))
                f *= 2 ** (1 / 3)
            R["S7"] = (worst <= -96, worst, f"loudest 1/3-octave band 2..8 s after the strike {worst:.1f} dBFS")
        elif ck == "S7m":
            if not bm.type_has_motor(typ):
                R["S7"] = (None, None, "n/a: no motor on this type (CC 47-51 inert: test_marimba_controls_audible)")
                continue
            s_ = bm.mono(seg(x, 700, 2400))
            sp = 20 * np.log10(np.abs(np.fft.rfft(s_ * np.hanning(len(s_)))) + 1e-9)
            fr = np.fft.rfftfreq(len(s_), 1 / bm.FS)
            pk = sp.max()
            worst = -240.0
            for k in range(1, 16):
                f = k * bm.FS / 32
                if f > 20000:
                    break
                b = np.abs(fr - f) < 3
                nb = (np.abs(fr - f) > 20) & (np.abs(fr - f) < 120)
                if b.any() and nb.any() and sp[b].max() > np.median(sp[nb]) + 10:
                    worst = max(worst, float(sp[b].max() - pk))
            am, rate = bm.am_depth(x, 1.7, 2.7)
            case.meta["am_db"], case.meta["am_hz"] = am, rate
            R["S7"] = (worst <= -80, worst, f"sub-block lines {'none' if worst < -200 else f'{worst:.1f} dB re the peak'};"
                                            f" AM {am:.1f} dB at {rate:.2f} Hz")
        elif ck == "S13":
            sw = case.meta.get("swaps", [])
            a, b = click_count(x, [t for t in evs if t not in sw] + sw)
            m = np.abs(bm.mono(x))
            nz = np.flatnonzero(m > 0)
            silent = len(nz) == 0 or nz[-1] < len(m) - int(0.3 * bm.FS)
            evd = cnt["ev_drop"]
            R["S13"] = (a + b == 0 and silent and not evd, a + b + (0 if silent else 1),
                        f"clicks {a}, bursts {b} (outside the fade blocks); {'silent at the end' if silent else 'STUCK: sound at the end'}; "
                        f"ev_drop {'n/a' if evd is None else evd}")
    import re
    m = re.search(r"cpu-proxy max (\d+) us per block \(bars", log or "")
    fxus = (case.env or {}).get("FRODHES_FXUS")
    if m and fxus in (None, str(FX_ALL_US)):          # (the S12 stress renders plan with a load that does not exist)
        us = int(m.group(1))
        R["S11"] = (us <= S11_FLAG_US, us, f"cost model's worst block {us} us"
                    + (f" (planned with every effect, {FX_ALL_US} us)" if fxus else ""))
    return R


def group_checks(cases, typ):
    """the checks over several renders: S3 (chord vs notes), S7 noise difference, S9 type vs ST77 and card sweeps"""
    G = []
    by = {c.name: c for c in cases}
    if "lin chord" in by:
        ch = bm.mono(by["lin chord"].x)
        s = sum(bm.mono(by[f"lin {n}"].x) for n in ("57", "64", "69"))
        n = min(len(ch), len(s))
        r = 10 * np.log10(np.sum((ch[:n] - s[:n]) ** 2) / (np.sum(ch[:n] ** 2) + 1e-20) + 1e-20)
        G.append(("S3", "lin chord vs notes", r <= -60, float(r), f"chord - sum of notes {r:.1f} dB re the chord"))
    for n in (53, 69, 89):
        a, b = by.get(f"noise0 {n}"), by.get(f"noise64 {n}")
        if a and b:
            d = bm.mono(b.x) - bm.mono(a.x)
            pk = np.abs(bm.mono(b.x)).max() + 1e-9
            st = int(np.argmax(np.abs(bm.mono(a.x)) > 0))
            # the contact burst only: 150 ms after the strike up to 10 ms before the key-up (its block). NOISE also sets the
            # damper's knock and felt noise at the key-up (bars.c bar_note_off), which is the card's job too and
            # not the burst the plan's "<= -80 dB by 150 ms" is about (a 1.0 s window read the knock: -22 dB)
            off = max(t for t, e in b.events if e.startswith("midi 0x80"))
            tail = d[st + int(0.15 * bm.FS):int((off - 10) * bm.FS / 1000)]      # (- one block of latency)
            r = 20 * np.log10(np.sqrt(np.mean(tail ** 2)) / pk + 1e-12) if len(tail) else -240
            G.append(("S7", f"noise64 {n}", r <= -80, float(r), f"NOISE 64 - NOISE 0, 150 ms after the strike to the "
                                                                 f"key-up, {r:.1f} dB re the peak"))
    ct = [c for c in cases if c.group == "chordcmp"]
    if len(ct) == 2:
        l = {c.meta["typ"]: bm.lufs(seg(c.x, onset_ms(200), 1000)) for c in ct}
        off = json.load(open(bm.data_for_type(typ) or os.path.join(bm.ROOT, "docs", "model", "data", "vibes.json"))
                        )["calib"].get("st77_offset_db", 0.0)
        d = l[typ] - l[0]
        G.append(("S9", "type vs ST77 chord", abs(d - off) <= 1.0, float(d), f"A3-E4-A4 v100: {d:+.2f} LU re ST77 "
                                                                           f"(target {off:+.1f}, the user's offset)"))
    for card in bm.CARDS:
        cs = [c for c in cases if c.group == "card" and c.meta.get("card") == card]
        if cs:
            lv = [np.mean([bm.lufs(seg(c.x, onset_ms(200 + i * 1500), 300)) for i in range(3)]) for c in cs]
            span = max(lv) - min(lv)
            lim = S9_CARD_LU.get(card, 6.0)
            if card == "DECAY" and short_type():
                # RING on a type of short notes (MARIMBA: F3 / A4 / F6 ring 1.0 / 0.7 / 0.2 s): the 0..300 ms window
                # holds most of the decay that is RING's job (RING 0 -> 64 +13 LU on F3, +7 on A4), where on VIBES
                # (T60 >= 2.4 s) it is the attack alone. Judged instead: the strike's peak (0..300 ms from its true
                # onset), the level a decay card must not move, <= 6 dB; the 0..300 ms LU reported. RING's own job
                # (V1 T60 0 < 64 < 127, 127 / 0 >= 2x on C2 / C4 / C6) is tests/py/test_marimba_controls_audible.py
                pk = [np.mean([20 * np.log10(np.abs(bm.mono(seg(c.x, onset_ms(200 + i * 1500), 300))).max() + 1e-9)
                               for i in range(3)]) for c in cs]
                ps = max(pk) - min(pk)
                G.append(("S9", f"card {card}", ps <= 6.0, float(ps), f"{bm.BAR_NAME[card]} ({card}) 0..127 on a type "
                          f"of short notes: the strike's peak moves {ps:.1f} dB (limit 6.0); the 0..300 ms level "
                          f"{span:.1f} LU (the decay: RING's job, not judged)"))
                continue
            if card == "HAMMER" and short_type():
                # WEIGHT with the type's own mass law (T7b): MARIMBA's level_comp 0 (VIBES 0.6) keeps the heavier
                # mallet's full impulse (m v) by design (marimba.json T7b _doc_2026_10_19: the WEIGHT card must move
                # the level >= 2.5 LU also where T7f caps the contact), so the most it can move V1 is
                # (1 - level_comp) x 20 log10(m_hi / m_lo): 13.2 dB at 12 / 55 g. VIBES' 8 LU stands for its 0.6
                t7b = json.load(open(bm.data_for_type(typ)))["T7b"]
                import math
                lim = round((1 - t7b["level_comp"]) * 20 * math.log10(t7b["m_g"][-1] / t7b["m_g"][0]), 1)
                G.append(("S9", f"card {card}", 2.5 <= span <= lim, float(span), f"{bm.BAR_NAME[card]} ({card}) 0..127: "
                          f"the 0..300 ms level moves {span:.1f} LU (the type's mass law: 2.5 .. {lim:.1f}, T7b level_comp "
                          f"{t7b['level_comp']}, m_g {t7b['m_g'][0]:g} .. {t7b['m_g'][-1]:g} g)"))
                continue
            G.append(("S9", f"card {card}", span <= lim, float(span), f"{bm.BAR_NAME[card]} ({card}) 0..127: the 0..300 ms "
                                                                       f"level moves {span:.1f} LU (limit {lim:.1f})"))
    s12c = [c for c in cases if c.group == "s12"]
    for pr in sorted({c.meta["pair"] for c in s12c}):
        a = next((c for c in s12c if c.meta["pair"] == pr and c.meta["kind"] != "ref"), None)
        b = next((c for c in s12c if c.meta["pair"] == pr and c.meta["kind"] == "ref"), None)
        if a is None or b is None:
            continue
        lim = -50.0 if a.meta["kind"] == "sleep" else -40.0
        r = s12_residual(bm.mono(a.x), bm.mono(b.x))
        # planned with nearly twice the every-effect load: a load the FM-1 never has (fx.c's heaviest set is
        # FX_ALL_US), so the budget must cut audible partials there: reported, not judged (test_bars_engine bounds
        # the order it cuts in against the best possible one)
        stress = (a.env or {}).get("FRODHES_FXUS") not in (None, str(FX_ALL_US))
        G.append(("S12", a.name, None if stress else r <= lim, float(r), f"{a.meta['kind']} vs off: worst 1 s window "
                  f"{'exact' if r == -np.inf else f'{r:.1f} dB'} re the signal" + (" (a load the FM-1 never has: reported)"
                                                                                 if stress else f" (limit {lim:.0f})")))
    return G


def s12_residual(a, b):
    """the worst 1 s window (0.25 s hops) of the residual a - b re b, dB; windows whose residual is within the 16-bit
    rounding (two independent roundings of the 16-bit output: <= 0.75 LSB RMS and <= 2 LSB peak, i.e. -84 dBFS) are
    not judged; -inf when identical"""
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if np.array_equal(a, b):
        return -np.inf
    worst = -240.0
    for t0 in np.arange(0.2, n / bm.FS - 1.0, 0.25):
        w = slice(int(t0 * bm.FS), int((t0 + 1.0) * bm.FS))
        d = a[w] - b[w]
        if np.sqrt(np.mean(d * d)) > 0.75 or np.abs(d).max() > 2:
            worst = max(worst, 10 * np.log10(np.sum(d * d) / (np.sum(b[w] ** 2) + 1e-20) + 1e-30))
    return worst


def s6(typ, keys, spec, design=None):
    import bar_freq_scan as fs
    if spec is None:
        return [("S6", "pitch / decay", None, None, "n/a: no spec file")]

    class A:
        pass
    a = A()
    a.type, a.vel, a.dur, a.motor, a.rate, a.depth, a.sleep = typ, 100, 8200, False, 78, 127, False
    out = []
    fs.OUT = os.path.join(OUT, "s6")
    D = {d["midi"]: d for d in (design or [])}
    for n in keys:
        if n not in spec:
            continue
        pair = "regime" in D.get(n, {})            # MARIMBA: V1 and its tube, one coupled pair (strong below C4)
        r = fs.measure_key(n, a, spec, design, keep_x=pair)
        note = ""
        if pair:
            # the f0 band holds the pair's two normal modes (0.8 Hz apart at F3, strongly coupled): its decay is not one
            # line, and the 40 dB fit of VIBES reads their beat (F3 1.25 s vs the design mode's 1.00). The plan's B1:
            # V1 by the 30 dB fit (tools/marimba_va.t60_fit), host vs the golden model's output (Gate VA-M2's measure)
            import bars_model as gm
            import marimba_va as mva
            f0 = D[n]["f0"]
            th = mva.t60_fit(bm.mono(r.pop("x")), f0)
            tg = mva.t60_fit(gm.render_batch(SPEC_FULL[0], [n], [100], a.dur / 1000 - 0.2, sleep=False)[0], f0)
            v1 = r["modes"][0]
            v1["t60"], v1["t60_spec"], v1["long"] = th, tg, False
            note = f" (V1 by the 30 dB fit: {th:.3f} s, golden {tg:.3f} s)"
        bad = fs.judge(r, True)
        out.append(("S6", f"key {n}", not bad, len(bad), ("; ".join(bad) or "pitch and T60 within tolerance") + note))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", type=int, default=4)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true")
    g.add_argument("--full", action="store_true")
    g.add_argument("--smoke", action="store_true")
    ap.add_argument("--checks", help="only these, e.g. S1,S9")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--spec")
    a = ap.parse_args()
    t_start = time.time()
    bm.build()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    spath, bars = bm.spec_for_type(a.type)
    spec = bm.load_spec(a.spec or spath)
    SPEC_FULL[0] = json.load(open(a.spec or spath)) if (a.spec or spath) and os.path.exists(a.spec or spath) else None
    DESIGN.clear()
    DESIGN.update({d["midi"]: d for d in bm.load_design(a.spec or spath)})
    cases = matrix(a.type, a.full, a.smoke)
    only = set(a.checks.split(",")) if a.checks else None
    from concurrent.futures import ThreadPoolExecutor

    def go(c):
        c.x, c.log = bm.render(fname(c.name), c.events, c.end, os.path.join(OUT, "wav"), env=c.env)
        return c
    with ThreadPoolExecutor(a.jobs) as ex:
        cases = list(ex.map(go, cases))
    rows = []                                                       # (check, case, pass, metric, detail)
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda c: run_checks(c, spec, a.type, cases), cases))
    for c, R in zip(cases, res):
        for ck, (ok, metric, det) in R.items():
            rows.append((ck, c.name, ok, metric, det))
    if not a.smoke:
        rows += group_checks(cases, a.type)
        rows += s6(a.type, [53, 60, 65, 69, 77, 84, 89] if not a.full else keys_for(True), spec,
                   bm.load_design(a.spec or spath))
    if only:
        rows = [r for r in rows if r[0] in only]
    order = ["S%d" % i for i in range(1, 14)]
    lines = [f"bar_scan: TYPE {a.type} {bm.type_name(a.type)} ({'bar' if bars else 'Rhodes'} engine), "
             f"{'full' if a.full else 'smoke' if a.smoke else 'quick'}, {len(cases)} renders, "
             f"spec {'none' if spec is None else os.path.relpath(a.spec or spath, bm.ROOT)}, {time.time() - t_start:.0f} s", ""]
    summary = {}
    for ck in order:
        rs = [r for r in rows if r[0] == ck]
        judged = [r for r in rs if r[2] is not None]
        fails = [r for r in judged if not r[2]]
        summary[ck] = dict(cases=len(rs), judged=len(judged), fails=len(fails))
        state = "n/a" if not judged else "PASS" if not fails else "FAIL"
        lines.append(f"{ck:4} {state:4}  {len(judged) - len(fails)}/{len(judged)} pass" + (f", {len(rs) - len(judged)} n/a" if len(rs) > len(judged) else ""))
        for r in sorted(fails, key=lambda r: -(r[3] or 0))[:12]:
            lines.append(f"        {r[1]:22} {r[4]}")
        for r in [r for r in rs if r[2] is None][:2]:
            lines.append(f"        {r[1]:22} {r[4]}")
    lines += ["", "per render:"]
    for r in rows:
        lines.append(f"  {r[0]:4} {'ok  ' if r[2] else 'n/a ' if r[2] is None else 'FAIL'} {r[1]:22} {r[4]}")
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    json.dump(dict(type=a.type, summary=summary, rows=[dict(check=r[0], case=r[1], ok=r[2], metric=r[3], detail=r[4]) for r in rows],
                   meta={c.name: {k: v for k, v in c.meta.items() if isinstance(v, (int, float, str, list, type(None)))} for c in cases}),
              open(os.path.join(OUT, "scan.json"), "w"), indent=1, default=float)
    by = {c.name: c for c in cases}
    for ck in order:                                               # the 20 worst renders of each failing check
        fl = sorted([r for r in rows if r[0] == ck and r[2] is False and r[1] in by], key=lambda r: -(r[3] or 0))[:20]
        if fl:
            d = os.path.join(OUT, "worst", ck)
            os.makedirs(d, exist_ok=True)
            for r in fl:
                src = os.path.join(OUT, "wav", fname(by[r[1]].name) + ".wav")
                if os.path.exists(src):
                    shutil.copy(src, d)
    bm.plot_panels(os.path.join(OUT, "summary.png"), [
        ("checks: renders passing (orange) / judged (blue)", [
            ("pass", (200, 110, 20), list(range(1, 14)), [summary[c]["judged"] - summary[c]["fails"] for c in order]),
            ("judged", (40, 90, 200), list(range(1, 14)), [summary[c]["judged"] for c in order])], "S", "renders", None),
        ("S9 LUFS per key, v100", [("LUFS", (200, 110, 20), by["keys v100"].meta.get("keys", []),
                                    by["keys v100"].meta.get("lufs", []))], "key", "LUFS", None),
    ])
    for l in lines[2:15]:
        print(l)
    print("->", os.path.relpath(OUT, bm.ROOT) + "/report.txt, scan.json, summary.png, worst/")
    fails = sum(summary[c]["fails"] for c in order if c != "S11")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
