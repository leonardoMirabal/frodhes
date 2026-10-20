# SPDX-License-Identifier: GPL-3.0-only
"""Gate VB-M (MARIMBA PLAN §8.2): the bar engine playing MARIMBA (firmware/src/bars.c on the host build, TYPE 5 by
CC 79 127) against the float golden model with the marimba spec (tools/bars_model.py, docs/model/data/
marimba_spec.json), note for note, as Gate VB does for VIBES (tests/py/test_bars_gate_b.py): residual energy <= -60 dB
of the signal (or an RMS under half an LSB). RAW (no effects), NOISE 0, the budget off (FRODHES_NOBUDGET: the golden
model has no mode budget). Cases: the plan's nine held notes (C2..C7 and the extrapolated ends 24 / 104) and key 108
(C8: its tube, damped 15 % at 4.3 kHz, runs bars.c's Q30 pair, bar_pair_*h; it read 6-8 LU under the golden model
before that path existed); MUTE 127
(the hand) and MUTE 0 (rings out) with the key-up at 0.5 s; BELL 0 (no tube); a roll (8 re-strikes at 12 Hz on key 48,
v117: the C superposes each strike on the ringing voice, the golden model is linear, so the roll is the sum of the
strikes at their blocks; the sleep off on both sides: FRODHES_NOSLEEP, sleep=False); the pedal (down: a key-up does
nothing whatever MUTE says; half: the hand lands at the pedal's weight x MUTE)."""
import json
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

import hostrun  # noqa: E402  (tests/py: the per-worker render paths)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bars_model as gm  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "marimba_spec.json")))
BLK = 256 / 44.1
KNOBS = "55 midi 0xB0 28 {tone}\n55 midi 0xB0 29 {bark}\n55 midi 0xB0 30 {bell}\n55 midi 0xB0 31 {hammer}\n" \
        "55 midi 0xB0 102 {decay}\n55 midi 0xB0 103 {release}\n"


def pedal_w(v):
    """rhodes_tables.h PEDAL_W[v] (bars.c bar_pedal: the damper weight of keys that are up at this CC 64)"""
    src = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    row = src.split("PEDAL_W[128] = {")[1].split("}")[0]
    return int(row.split(",")[v])


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def c_render(strikes, dur_ms, off_ms=None, pedal=None, tag="vbm", nosleep=False, **kn):
    """strikes: [(t_ms, note, vel)] or [(t_ms, note, vel, t_off_ms)]; MARIMBA, RAW, NOISE 0, the knobs by CC (MUTE =
    CC 103)"""
    k = dict(gm.DEFAULT_KNOBS, **kn)
    n0 = strikes[0][1]
    out = hostrun.out(f"{tag}-{n0}-{strikes[0][2]}")
    s = "50 midi 0xB0 119 127\n50 midi 0xB0 104 0\n52 midi 0xB0 79 127\n" + KNOBS.format(**k)
    if pedal is not None:
        s += f"56 midi 0xB0 64 {pedal}\n"
    ev = []
    for st in strikes:
        ev.append((st[0], f"midi 0x90 {st[1]} {st[2]}"))
        if len(st) > 3:
            ev.append((st[3], f"midi 0x80 {st[1]} 0"))
    for t, e in sorted(ev, key=lambda a: a[0]):
        s += f"{t} {e}\n"
    if off_ms:
        s += f"{off_ms} midi 0x80 {n0} 0\n"
    s += f"{dur_ms} end\n"
    with open(out + ".txt", "w") as f:
        f.write(s)
    env = dict(os.environ, FRODHES_NOBUDGET="1")
    if nosleep:
        env["FRODHES_NOSLEEP"] = "1"
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True, env=env)
    w = wave.open(out + ".wav")
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)


def residual_db(c, g):
    """aligned by cross-correlation (the C note starts at its block's start): the residual energy re the golden
    signal (dB) and its RMS in LSB"""
    first = int(np.argmax(np.abs(c) > 0))
    start = max(first - 64, 0)
    L = min(len(g), len(c) - start - 128)
    errs = [np.sum((c[lag:lag + L] - g[:L]) ** 2) for lag in range(start, start + 128)]
    i = int(np.argmin(errs))
    return 10 * np.log10(errs[i] / np.sum(g[:L] ** 2)), float(np.sqrt(errs[i] / L))


def blk(t_ms):
    """the block an event at t ms reaches the engine in (its start, in samples)"""
    return int(np.ceil(t_ms / BLK)) * 256


def off_rel(off_ms, start_ms=100.0):
    return (blk(off_ms) - blk(start_ms)) / 44100.0


def check(c, g, what):
    r, rms = residual_db(c, g)
    print(what, f"residual {r:.1f} dB, rms {rms:.2f} LSB")
    assert r <= -60.0 or rms <= 0.5, (what, r, rms)


@pytest.mark.parametrize("note,vel", [(36, 100), (48, 127), (54, 40), (60, 100), (72, 127), (84, 64), (96, 100),
                                      (24, 100), (104, 100), (108, 100), (108, 127)])
def test_c_matches_golden_held(note, vel):
    c = c_render([(100, note, vel)], 2600, tag="vbm-held")
    g = gm.render_batch(SPEC, [note], [vel], 2.2)[0]
    check(c, g, (note, vel))


@pytest.mark.parametrize("mute", [0, 127])
@pytest.mark.parametrize("note", [48, 72, 108])
def test_c_matches_golden_mute(note, mute):
    """the key-up at 0.5 s: MUTE 127 the hand stop, MUTE 0 the note rings on"""
    c = c_render([(100, note, 100)], 2200, off_ms=600, tag=f"vbm-mute{mute}", release=mute)
    g = gm.render_batch(SPEC, [note], [100], 1.8, knobs=dict(release=mute), off_at=off_rel(600))[0]
    check(c, g, (note, mute))


@pytest.mark.parametrize("note", [36, 60])
def test_c_matches_golden_no_tube(note):
    c = c_render([(100, note, 100)], 2600, tag="vbm-bell0", bell=0)
    g = gm.render_batch(SPEC, [note], [100], 2.2, knobs=dict(bell=0))[0]
    check(c, g, (note, "BELL 0"))


def test_c_matches_golden_roll():
    """8 re-strikes at 12 Hz on key 48, v117, each let go 40 ms later with MUTE 0 (the key-ups do nothing; a pitch
    struck again while held is not a new note): the C superposes each strike on the ringing voice, the golden model's
    strikes are summed at the blocks they reach the engine in (linear; the sleep off on both sides)"""
    ts = [100 + round(i * 1000 / 12) for i in range(8)]
    c = c_render([(t, 48, 117, t + 40) for t in ts], 2600, tag="vbm-roll", nosleep=True, release=0)
    one = gm.render_batch(SPEC, [48], [117], 2.2, knobs=dict(release=0), sleep=False)[0]
    g = np.zeros(len(one))
    for t in ts:
        d = blk(t) - blk(ts[0])
        g[d:] += one[:len(one) - d]
    check(c, g, "roll 12 Hz key 48 v117")


@pytest.mark.parametrize("ped", [127, 64, 50])
def test_c_matches_golden_pedal(ped):
    """MUTE 127, the key-up at 0.5 s with the pedal at ped: down (127, 64) nothing lands; half (50) the hand lands at
    PEDAL_W[50] x MUTE"""
    c = c_render([(100, 60, 100)], 2200, off_ms=600, pedal=ped, tag=f"vbm-ped{ped}", release=127)
    g = gm.render_batch(SPEC, [60], [100], 1.8, knobs=dict(release=127), off_at=off_rel(600),
                        pedal_w=pedal_w(ped))[0]
    check(c, g, ("pedal", ped))
