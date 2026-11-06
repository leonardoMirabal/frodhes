# SPDX-License-Identifier: GPL-3.0-only
"""SEQUENCER 2 (0.9.9): K1 STEP (the sequencer's cursor, bar.beat.16th), K2 CHANCE of the notes starting in that step
(100 % .. 10 % in 10 % steps; "-" on a step without notes). Stored in each note's spare byte (seq.c sq_ev_t.rsv: 0 =
100 %, every pattern before; k = 100 - 10 k %). One roll per step per pass, so a chord plays whole or not at all.

A 1-bar pattern at 120 BPM (a 2 s loop, QUANT 1/16): C E G step-recorded on step 1, B4 on step 5. Step 1 at 50 %
plays about half of 200 passes, its three notes always together, step 5 every pass; at 10 % rarely but sometimes;
the chance is saved with the pattern and comes back after a power cycle; the same pattern with the bytes 0 (as every
pattern before) plays every pass. The host clock is deterministic, so every count here is the same on every run."""
import os
import struct
import zlib
from collections import defaultdict

from hostrun import OUT, cc, run

SECTOR = 4096
SQ_MAGIC = 0x46535131
LOOP = 2000                                    # ms: 1 bar at 120 BPM
CHORD, LONE = (60, 64, 67), 71
PASSES = 200


def flash_file(tag):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, tag + ".flash")
    if os.path.exists(p):
        os.remove(p)
    return p


def detents(t, enc, d, n, gap=40):
    """n single detents (a card moves one step a poll)"""
    return [f"{t + gap * i} turn {enc} {d}" for i in range(n)], t + gap * n


def record():
    """SEQ (the SEQUENCER page), BARS 1, QUANT 1/16, step REC: the chord on step 1 (the cursor moves on 1), three steps
    skipped with ALGORITHM, B4 on step 5 (the cursor on step 6 after it), REC off; then SELECT to SEQUENCER 2"""
    lines = ["40 btn 11", cc(50, 61, 1), cc(51, 62, 3), cc(60, 63, 127)]
    lines += [f"300 midi 0x90 {n} 100" for n in CHORD] + [f"340 midi 0x80 {n} 0" for n in CHORD]
    lines += ["400 turn 1 1", "430 turn 1 1", "460 turn 1 1"]
    lines += [f"500 midi 0x90 {LONE} 100", f"540 midi 0x80 {LONE} 0", cc(600, 63, 0), "650 dump", "700 turn 0 1",
              "750 dump"]
    return lines


def passes(r, t0):
    """the notes sent on MIDI out, per pass of the loop started at t0"""
    got = defaultdict(set)
    for t, s, n, v in r.midi_out:
        if s == 0x90 and t >= t0:
            got[(t - t0) // LOOP].add(n)
    return [got[i] for i in range(PASSES)]


def pattern(path):
    """the newest valid pattern in the flash file: [(tick, len, note, vel, take, rsv)]"""
    img = open(path, "rb").read()
    best = None
    for s in range(len(img) // SECTOR):
        magic, seq, n, crc = struct.unpack_from("<IIII", img, s * SECTOR)
        if magic != SQ_MAGIC or n > 512:
            continue
        ev = img[s * SECTOR + 16:s * SECTOR + 16 + 8 * n]
        if zlib.crc32(ev) == crc and (best is None or seq >= best[0]):
            best = (seq, s, n)
    assert best, "no pattern saved"
    seq, s, n = best
    return s, [struct.unpack_from("<HHBBBB", img, s * SECTOR + 16 + 8 * i) for i in range(n)]


def set_rsv(path, sector, rsv):
    """every note's CHANCE byte in that pattern set to rsv (the CRC made good again)"""
    img = bytearray(open(path, "rb").read())
    o = sector * SECTOR
    n = struct.unpack_from("<I", img, o + 8)[0]
    for i in range(n):
        img[o + 16 + 8 * i + 7] = rsv
    struct.pack_into("<I", img, o + 12, zlib.crc32(bytes(img[o + 16:o + 16 + 8 * n])))
    open(path, "wb").write(img)


def check_together(ps):
    """a chord's notes play together or not at all; step 5 (100 %) every pass -> the passes the chord played"""
    for i, p in enumerate(ps):
        assert LONE in p, (i, sorted(p))
        assert set(CHORD) <= p or not (set(CHORD) & p), (i, sorted(p))
    return sum(1 for p in ps if CHORD[0] in p)


def make_50(tag, fl=None, play=True):
    """record, step 1 at 50 % (the cards checked on the way), then play 200 passes (or stop there and let it save)"""
    lines = record()
    back, t = detents(800, 2, -1, 5)                   # K1 STEP: from step 6 back to step 1
    lines += back + [f"{t} dump"]
    d1 = t
    dn, t = detents(t + 50, 3, -1, 5)                   # K2 CHANCE: 100 -> 50 %
    lines += dn + [f"{t} dump"]
    d2 = t
    fwd, t = detents(t + 50, 2, 1, 4)                   # step 5: 100 %; back to step 1
    lines += fwd + [f"{t} dump"]
    d3 = t
    back, t = detents(t + 50, 2, -1, 4)
    lines += back + [f"{t} dump"]
    d4 = t
    t0 = t + 100
    if play:
        lines += [cc(t0, 60, 127)]
        end = t0 + PASSES * LOOP + 100
    else:
        end = t0 + 4000                                 # stopped and silent: the pattern saves 1.5 s after the change
    r = run(tag, lines, end, flash=fl)
    return r, (d1, d2, d3, d4), t0


def test_cards():
    """STEP shows the cursor (bar.beat.16th: step 6 = 1.2.2 after the recording), CHANCE "-" on a step without notes
    (turning it changes nothing), 100 % by default on a step with notes, 50 % after five detents left; step 5 stays
    100 %; 10 % is the floor and 100 % the ceiling; STEP wraps around the loop"""
    r, (d1, d2, d3, d4), _ = make_50("chance-cards", play=False)
    s = r.state(650)
    assert (s["page"], s["notes"]) == ("SEQUENCER", "4"), (s["page"], s["notes"])
    s = r.state(750)
    assert (s["page"], s["label0"], s["card0"], s["label1"], s["card1"]) == ("SEQUENCER 2", "STEP", "1.2.2", "CHANCE", "-"), s
    assert (s["label2"], s["label3"]) == ("", ""), s
    assert (r.state(d1)["card0"], r.state(d1)["card1"]) == ("1.1.1", "100%")
    assert (r.state(d2)["card0"], r.state(d2)["card1"]) == ("1.1.1", "50%")
    assert (r.state(d3)["card0"], r.state(d3)["card1"]) == ("1.2.1", "100%")
    assert (r.state(d4)["card0"], r.state(d4)["card1"], r.state(d4)["notes"]) == ("1.1.1", "50%", "4")

    lines = record()
    l, t = detents(800, 3, 1, 3)                        # CHANCE on an empty step (6): nothing to change
    lines += l + [f"{t} dump"]
    e = t
    l, t = detents(t + 50, 2, -1, 6)                    # STEP: 6 -> 1 -> wraps to 16 (1.4.4)
    lines += l + [f"{t} dump"]
    w = t
    l, t = detents(t + 50, 2, 1, 1)                     # and on to 1
    l2, t = detents(t, 3, -1, 12)                       # CHANCE: down to the floor (10 %)
    lines += l + l2 + [f"{t} dump"]
    lo = t
    l, t = detents(t + 50, 3, 1, 12)                    # up to the ceiling (100 %)
    lines += l + [f"{t} dump"]
    hi = t
    r = run("chance-cards2", lines, t + 200)
    assert (r.state(e)["card0"], r.state(e)["card1"], r.state(e)["notes"], r.state(e)["dirty"]) == \
        ("1.2.2", "-", "4", r.state(650)["dirty"])
    assert r.state(w)["card0"] == "1.4.4" and r.state(w)["card1"] == "-"
    assert (r.state(lo)["card0"], r.state(lo)["card1"]) == ("1.1.1", "10%")
    assert r.state(hi)["card1"] == "100%"


def test_fifty_percent_plays_about_half():
    """step 1 at 50 %: over 200 passes the chord plays about half of them (60 .. 140), always all three notes
    together, step 5 (100 %) every pass; the same script gives the same notes again (the host's clock)"""
    r, _, t0 = make_50("chance-50")
    ps = passes(r, t0)
    n = check_together(ps)
    assert 60 <= n <= 140, n
    assert 0 < n < PASSES and any(CHORD[0] in p for p in ps[:20]) and any(CHORD[0] not in p for p in ps[:20]), n
    again, _, _ = make_50("chance-50b")
    assert again.midi_out == r.midi_out


def test_chance_saved_with_the_pattern():
    """the 50 % is saved with the pattern (the notes' byte 5) and comes back after a power cycle: SEQUENCER 2 shows it
    on step 1; set to 10 % it plays rarely but sometimes (2 .. 50 of 200); the same pattern with the bytes 0 (every
    pattern before CHANCE) shows 100 % and plays every pass"""
    fl = flash_file("chance-pc")
    r, _, _ = make_50("chance-pc1", fl=fl, play=False)
    assert r.seq_saves, "the pattern was not saved"
    sector, ev = pattern(fl)
    got = sorted((e[2], e[5]) for e in ev)
    assert got == [(60, 5), (64, 5), (67, 5), (71, 0)], got

    lines = ["100 dump", "200 btn 11", "300 turn 0 1", "400 dump"]
    l, t = detents(450, 3, -1, 4)                       # 50 -> 10 %
    lines += l + [f"{t} dump"]
    lo = t
    t0 = t + 100
    r2 = run("chance-pc2", lines + [cc(t0, 60, 127)], t0 + PASSES * LOOP + 100, flash=fl)
    assert r2.state(100)["notes"] == "4"
    s = r2.state(400)
    assert (s["page"], s["card0"], s["card1"]) == ("SEQUENCER 2", "1.1.1", "50%"), s
    assert r2.state(lo)["card1"] == "10%"
    n = check_together(passes(r2, t0))
    assert 2 <= n <= 50, n

    sector, _ = pattern(fl)                             # (the newest: run 2 may have saved its 10 %)
    set_rsv(fl, sector, 0)                              # an old pattern: every byte 0
    r3 = run("chance-pc3", ["100 dump", "200 btn 11", "300 turn 0 1", "400 dump", cc(500, 60, 127)],
             500 + PASSES * LOOP + 100, flash=fl)
    assert r3.state(400)["card1"] == "100%" and r3.state(100)["notes"] == "4"
    assert check_together(passes(r3, 500)) == PASSES


def test_step_moves_from_the_step_shown_while_playing():
    """while the loop plays and the cursor has sat for more than 4 s, STEP moves on from the step it shows (1.1.1 ->
    1.1.2), not from the step playing (it jumped to the playhead's next step); CHANCE set there while playing takes"""
    lines = record()
    l, t = detents(800, 2, -1, 5)
    lines += l + [f"{t} dump", cc(t + 100, 60, 127), "9000 dump", "9600 turn 2 1", "9650 dump"]
    l, t = detents(9700, 2, -1, 1)
    l2, t = detents(t, 3, -1, 2)                        # back on step 1: 80 %, while playing
    lines += l + l2 + [f"{t} dump"]
    r = run("chance-step-play", lines, t + 200)
    assert r.state(9000)["play"] == "1" and r.state(9000)["card0"] == "1.1.1"
    assert (r.state(9650)["card0"], r.state(9650)["card1"]) == ("1.1.2", "-")
    assert (r.state(t)["card0"], r.state(t)["card1"]) == ("1.1.1", "80%")
