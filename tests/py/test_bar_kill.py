# SPDX-License-Identifier: GPL-3.0-only
"""A released VIBES / MARIMBA voice ends once it falls under audibility (0.9.8). bars.c ends a voice when its modes'
level sum falls under kill1 (1 LSB out). An upper slot's integer recursion (the Q32 two-multiply form with first-order
error feedback) can, at zero input and a few hundred LSB of x, sit in a rounding limit cycle instead of decaying: in
tests/scripts/burst VIBES voice 5 (note 56) holds its V4 at 670 - 672 against kill1 672 for 144 blocks; with 0.9.7's
damping table (PEDAL_W 32767) V2 holds 55 beside it, the sum stays ~725 and the voice keeps its slot (and its CPU) until
a steal or its life (~15 s) ends it, changing the stealing after it. The fix: an upper slot whose level did not fall since the
last block while under 2 LSB out goes to sleep (faded over a sub-block, as the other sleeps). The host build with
-DBAR_LVL_LOG prints every live voice's level once a block: "bl blk voice note held sum kill1 life amp0 .. amp7"."""
import os
import re

import pytest

from hostrun import HOST, ROOT, build, run

BIN = os.path.join(HOST, "frodhes-barlvl")
OLD = os.path.join(HOST, "barlvl-gen097")           # rhodes_tables.h with 0.9.7's PEDAL_W (full damping 32767)
BIN_OLD = os.path.join(HOST, "frodhes-barlvl-097")
UP0 = 2                                              # bars.h BAR_UP0: the first upper slot


def bin097():
    os.makedirs(OLD, exist_ok=True)
    s = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    m = re.search(r"PEDAL_W\[128\] = \{([^}]*)\}", s)
    t = s[:m.start(1)] + m.group(1).replace("32768", "32767") + s[m.end(1):]
    p = os.path.join(OLD, "rhodes_tables.h")
    if not os.path.exists(p) or open(p).read() != t:
        open(p, "w").write(t)
    return build(BIN_OLD, extra=("-DBAR_LVL_LOG", "-I" + OLD))


def rows(out):
    for l in out.splitlines():
        f = l.split()
        if f and f[0] == "bl":
            blk, vi, note, held, s, k, life = map(int, f[1:8])
            yield blk, vi, note, held, s, k, [int(x) for x in f[8:]]


def releases(out):
    """each voice's runs (a new one at a new note, a re-strike or a gap): for every released run, the blocks from
    its sum's first fall under 4 x kill1 (4 LSB out) to its end"""
    runs, cur = [], {}
    for blk, vi, note, held, s, k, amps in rows(out):
        r = cur.get(vi)
        if r is None or r["note"] != note or blk != r["last"] + 1 or (held and not r["held"]):
            r = dict(v=vi, note=note, k=k, aud=None); cur[vi] = r; runs.append(r)
        r["last"], r["held"], r["sum"] = blk, held, s
        if not held and r["aud"] is None and s < 4 * k:
            r["aud"] = blk
    return [(r["last"] - r["aud"], r["v"], r["note"], r["sum"], r["k"]) for r in runs if r["aud"] is not None]


def stuck(out):
    """the longest run of blocks an upper slot of a released voice stays live, under 2 LSB out, within 1 % of one
    level (a free mode falls ~0.3 - 7 % a block)"""
    best, st = (0,), {}
    for blk, vi, note, held, s, k, amps in rows(out):
        for m in range(UP0, len(amps)):
            a, r = amps[m], st.get((vi, m))
            ok = a > 0 and not held and a < 2 * k
            if ok and r and r["note"] == note and r["last"] == blk - 1 and abs(a - r["a0"]) <= max(2, r["a0"] // 100):
                r["last"] = blk; r["n"] += 1
                best = max(best, (r["n"], vi, note, m, a, k))
            else:
                st[(vi, m)] = dict(note=note, last=blk, a0=a, n=1) if ok else None
    return best


def burst(tag, binary, end):
    ls = [l for l in open(os.path.join(ROOT, "tests", "scripts", "burst.txt")).read().splitlines()
          if l.strip() and not l.startswith("#") and not l.split()[1] == "end"]
    return run(tag, ls, end, binary=binary).out


def test_upper_slot_never_stuck():
    """burst: no released upper slot holds one level under 2 LSB for more than 20 blocks (VIBES v5's V4: 144 blocks
    at 670 - 672 before)"""
    b = stuck(burst("barlvl-burst", build(BIN, extra=("-DBAR_LVL_LOG",)), 7100))
    assert b[0] <= 20, b


def test_burst_voices_end_097_damping():
    """the recorded case (0.9.7's damping table): every released voice ends within 80 blocks of falling under 4 LSB
    (VIBES v5, note 56, before: its sum held ~725 against kill1 672 until the MIDI burst at 5.2 s took its slot, 124
    blocks on; without that steal its life, ~15 s, would have ended it)"""
    rel = releases(burst("barlvl-burst097", bin097(), 12000))
    assert rel and max(rel)[0] <= 80, sorted(rel, reverse=True)[:3]


@pytest.mark.parametrize("eng,sel", [("vibes", 0), ("marimba", 127)])
def test_released_notes_end(eng, sel):
    """every key (every 3rd) at three velocities, held 0.5 s, damped: each voice ends within 80 blocks of falling
    under 4 LSB (a normal decay from there: 5 - 45 blocks), and no upper slot is stuck"""
    ev, t = ["20 midi 0xB0 119 127", f"30 midi 0xB0 79 {sel}"], 300
    for vel in (40, 90, 127):
        for n in range(36, 100, 3):
            ev += [f"{t} midi 0x90 {n} {vel}", f"{t + 500} midi 0x80 {n} 0"]
            t += 2200
    out = run(f"barlvl-{eng}", ev, t + 3000, binary=build(BIN, extra=("-DBAR_LVL_LOG",))).out
    rel = releases(out)
    assert len(rel) > 40, len(rel)
    assert max(rel)[0] <= 80, sorted(rel, reverse=True)[:3]
    assert stuck(out)[0] <= 20, stuck(out)
