# SPDX-License-Identifier: GPL-3.0-only
"""Race fuzz (2026-10-18): the bug class of fuzz seeds 513 / 780 (a note waiting for a steal's fade, every voice busy,
an all-off in the same audio block: the note started held for good) hunted on purpose. Each case fills all the voices
(FR_VOICES; held, under the pedal, or half held and half let go), optionally strikes one more note so a steal is
pending, then puts two (or three) events into the same audio block (0 / 1 / 3 ms apart: a block is 5.8 ms) or into
adjacent ones (6 / 12 ms): note-ons, the note-off of the pending or of the stolen note, CC 120 / 123, the pedal (down,
up, half), program changes (the other engine and the same one), CC 79 / 80 TYPE, the arpeggiator (on, off, HOLD),
chord mode, the sequencer (play, stop, REC), tap tempo, MIDI clock start / stop (CLOCK EXT is on), the FM-1's keys,
REC (the sustain button) and other panel buttons. Then everything is let go the normal way (no all-off), and:

  - the host's race check (frodhes.c host_race_check) never fires: no voice held down without a note the notes
    layer wants sounding ("RACE STUCK"), no note lost while it waited for a steal's fade ("RACE LOST");
  - 600 ms after the release nothing is held: no voice held, no source, no counted pitch, no dropped event;
  - every voice ends within 12 s (the longest natural tail, a low key's life, is ~10.5 s) and the output (effects
    and all) is exact digital zero by then;
  - no click at the events (tools/bar_measure.py / hil_scan clicks(): the second difference far above the local
    level) in the 300 ms from the burst on, beyond what the same case shows without the burst (the strikes' own).

Default: 64 cases per engine (a seeded sample of the full set) and 16 triples; --long: every ordered pair of events
at every gap, setup and steal state (32670), 2000 triples and 3000 bursts of six, per engine; --race-cases N: N sampled cases per engine. A failing case
keeps its script and WAV in build/host/py (race-*.txt) to replay."""
import itertools
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from hostrun import FS, OUT, cc, run
from test_fuzz import clicks
from test_alloff import voices

NV = voices()
N9 = [33, 40, 47, 54, 61, 68, 75, 82, 89, 94, 99, 52, 57, 64, 71, 78][:NV]   # the setup's notes, one per voice
PEND, NEW, KEY = 96, 98, 7                     # the note that waits for a steal; a new note; the key (C4 at OCT 0)
T0 = 700                                       # the burst
GAPS = [0, 1, 3, 6, 12]                        # ms between the burst's events (5.8 ms a block)
SETUPS = ["held", "pedal", "mixed"]


def ev_lines(name, t, eng):
    """the script lines of one burst event at t ms"""
    other = 0 if eng == "vibes" else 12        # a program change to the other engine's first preset
    same = 13 if eng == "vibes" else 1
    E = {
        "on": [f"{t} midi 0x90 {NEW} 100"],
        "on_pend": [f"{t} midi 0x90 {PEND} 90"],                 # the pending note again
        "off_pend": [f"{t} midi 0x80 {PEND} 0"],
        "off_stolen": [f"{t} midi 0x80 {N9[0]} 0"],              # the oldest (held: the steal's victim)
        "off_quiet": [f"{t} midi 0x80 {N9[-1]} 0"],
        "cc120": [cc(t, 120, 0)], "cc123": [cc(t, 123, 0)],
        "ped_dn": [cc(t, 64, 127)], "ped_up": [cc(t, 64, 0)], "ped_half": [cc(t, 64, 50)],
        "pc_other": [f"{t} midi 0xC0 {other} 0"], "pc_same": [f"{t} midi 0xC0 {same} 0"],
        "cc79": [cc(t, 79, 64)], "cc80": [cc(t, 80, 0)],
        "arp_on": [cc(t, 52, 1)], "arp_off": [cc(t, 52, 0)],
        "hold": [f"{t} btn 3"],                                  # SEL: HOLD while the arp is on (else DELAY's page)
        "chord_on": [cc(t, 56, 6)], "chord_off": [cc(t, 56, 0)], # (MAJ7: four notes)
        "seq_play": [cc(t, 60, 127)], "seq_stop": [cc(t, 60, 0)], "rec_on": [cc(t, 63, 127)], "rec_off": [cc(t, 63, 0)],
        "tap": [f"{t} btn 12"],
        "clk_start": [f"{t} midi 0xFA 0 0"] + [f"{t + 1 + 20 * i} midi 0xF8 0 0" for i in range(12)],
        "clk_stop": [f"{t} midi 0xFC 0 0"],
        "key_on": [f"{t} keys {1 << KEY | 1 << (KEY + 4)}"], "key_off": [f"{t} keys 0"],
        "sus_tap": [f"{t} btn 13"],                              # REC: a tap latches the sustain (again: lets go)
        "oct_up": [f"{t} btn 1"], "edit": [f"{t} btn 6"], "arp_btn": [f"{t} btn 10"], "home": [f"{t} btn 8"],
    }
    return E[name]


EVENTS = ["on", "on_pend", "off_pend", "off_stolen", "off_quiet", "cc120", "cc123", "ped_dn", "ped_up", "ped_half",
          "pc_other", "pc_same", "cc79", "cc80", "arp_on", "arp_off", "hold", "chord_on", "chord_off", "seq_play",
          "seq_stop", "rec_on", "rec_off", "tap", "clk_start", "clk_stop", "key_on", "key_off", "sus_tap", "oct_up",
          "edit", "arp_btn", "home"]


def case_lines(eng, setup, steal, burst):
    """burst: [(event, t)]; returns (lines, marks)"""
    L = [cc(5, 79, 64), cc(6, 47, 0)] if eng == "vibes" else []           # VIBES, its motor off
    L += ["20 btn 7", "40 btn 7", "60 btn 7", "80 turn 4 1", "100 btn 8"]  # GLO x3: MIDI, CLOCK EXT; HOME
    if setup == "pedal":
        L.append(cc(110, 64, 127))
    for i, n in enumerate(N9):
        L.append(f"{150 + 3 * i} midi 0x90 {n} {60 + 6 * i}")
        if setup == "pedal" or (setup == "mixed" and i % 2):         # (mixed: let go just before the burst, still
            L.append(f"{(300 if setup == 'pedal' else T0 - 60) + 3 * i} midi 0x80 {n} 0")   # sounding)
    if steal:
        L.append(f"{T0} midi 0x90 {PEND} 110")
    for name, t in burst:
        L += ev_lines(name, t, eng)
    c = T0 + 300                                                           # let go of everything, the normal way
    L += [f"{c} keys 0", cc(c, 64, 0), cc(c, 52, 0), cc(c, 63, 0), cc(c, 60, 0), f"{c} midi 0xFC 0 0",
          f"{c} press 13", f"{c + 400} release 13"]                       # (a held REC clears a latched sustain)
    L += [f"{c + 1} midi 0x80 {n} 0" for n in N9 + [PEND, NEW]]
    chk, end = c + 600, c + 12600
    L += [f"{T0 - 2} dump", f"{chk} dump", f"{end - 600} dump"]
    return L, dict(chk=chk, end=end, burst=T0)


def judge(eng, setup, steal, burst):
    tag = "race-" + eng + "-" + setup + ("-steal" if steal else "") + "-" + "-".join(f"{n}@{t - T0}" for n, t in burst)
    ls, mk = case_lines(eng, setup, steal, burst)
    r = run(tag, ls, mk["end"], check=False)
    bad = []
    if r.code != 0 or not len(r.L):
        return tag, [f"exit {r.code}: {r.out[-400:]}"], 0
    bad += sorted(set(re.findall(r"RACE (?:STUCK|LOST)[^\n]*", r.out)))[:3]
    s = r.state(mk["chk"])
    for k in ("held", "srcs", "refs", "pitches", "drops"):
        if s[k] != "0":
            bad.append(f"{k} {s[k]} after the release")
    if r.state(mk["end"] - 600)["voices"] != "0":
        bad.append(f"{r.state(mk['end'] - 600)['voices']} voices alive 11.4 s after the release")
    x = np.maximum(np.abs(r.L), np.abs(r.R))
    tail = x[int((mk["end"] - 500) / 1000 * FS):]
    if tail.max() != 0:
        bad.append(f"not digital zero at the end: peak {tail.max():.0f}")
    w = r.L[int((mk["burst"] - 5) / 1000 * FS):int((mk["burst"] + 300) / 1000 * FS)]
    k = clicks(w)
    if not bad:
        for ext in (".wav", ".txt", "-1000.ppm", "-3000.ppm"):   # (the host's two screenshots too)
            try:
                os.remove(os.path.join(OUT, tag) + ext)
            except OSError:
                pass
    return tag, bad, k


def all_cases(eng):
    out = []
    for a, b in itertools.product(EVENTS, EVENTS):
        for g in GAPS:
            for setup in SETUPS:
                for steal in (True, False):
                    out.append((eng, setup, steal, ((a, T0), (b, T0 + g))))
    return out


def triples(eng, n, seed, k=3):
    """n random bursts of k events, each 0 / 1 / 3 ms after the one before"""
    rng = random.Random(seed)
    return [(eng, rng.choice(SETUPS), rng.random() < 0.7,
             tuple(sorted(((e, T0 + rng.choice(GAPS[:3]) * i) for i, e in enumerate(rng.sample(EVENTS, k))),
                          key=lambda p: p[1]))) for _ in range(n)]


def run_cases(cases):
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        keys = sorted({c[:3] for c in cases})          # the clicks without a burst: each setup's own (the strikes')
        base = dict(zip(keys, (k for _, _, k in ex.map(lambda c: judge(*c, ()), keys))))

        def go(c):
            tag, bad, k = judge(*c)
            if k > base[c[:3]]:
                bad = bad + [f"{k} clicks after the burst (without it {base[c[:3]]})"]
            return tag, bad
        return [x for x in ex.map(go, cases) if x[1]]


@pytest.mark.parametrize("eng", ["rhodes", "vibes"])
def test_race_fuzz(eng, request):
    n = request.config.getoption("--race-cases")
    full = all_cases(eng)
    if request.config.getoption("--long") and not n:
        cases = full + triples(eng, 2000, 1 + (eng == "vibes")) + triples(eng, 3000, 5 + (eng == "vibes"), 6)
    else:
        rng = random.Random(7 + (eng == "vibes"))
        cases = rng.sample(full, n or 64) + triples(eng, (n or 64) // 4, 3 + (eng == "vibes"))
    failed = run_cases(cases)
    msg = "\n".join(f"{tag}: {'; '.join(bad)}" for tag, bad in failed)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"race-failed-{eng}.txt"), "w") as f:     # every failure (the message: the first 12)
        f.write(f"{len(failed)} of {len(cases)}\n{msg}\n")
    msg = "\n".join(msg.split("\n")[:12])
    assert not failed, f"{len(failed)} of {len(cases)} cases failed:\n{msg}"
