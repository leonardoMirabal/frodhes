# SPDX-License-Identifier: GPL-3.0-only
"""Gate VB (VIBES PLAN section 10): the bar engine (firmware/src/bars.c on the host build) against the float golden
model (tools/bars_model.py), note for note: residual energy <= -60 dB of the signal (held), <= -50 dB with the
motor, -60 dB with BELL 0 (no tube). RAW (no effects), NOISE 0, the budget off (FRODHES_NOBUDGET: the golden model
has no mode budget; S12 checks the budget itself)."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bars_model as gm  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
SPEC = gm.load_spec()
BLK = 256 / 44.1
KNOBS = "55 midi 0xB0 28 {tone}\n55 midi 0xB0 29 {bark}\n55 midi 0xB0 30 {bell}\n55 midi 0xB0 31 {hammer}\n" \
        "55 midi 0xB0 102 64\n55 midi 0xB0 103 64\n"


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def c_render(note, vel, dur_ms, off_ms=None, motor=None, tag="vb", **kn):
    k = dict(gm.DEFAULT_KNOBS, **kn)
    out = os.path.join(ROOT, "build", "host", f"{tag}-{note}-{vel}")
    s = "50 midi 0xB0 119 127\n50 midi 0xB0 104 0\n52 midi 0xB0 79 64\n53 midi 0xB0 47 0\n" + KNOBS.format(**k)
    if motor:                                       # the motor (records v3: CC 47 ON, 48 DEPTH, 49 RATE, 50 SHAPE)
        s += f"56 midi 0xB0 47 127\n56 midi 0xB0 48 {motor['depth']}\n" \
             f"56 midi 0xB0 49 {motor['rate']}\n56 midi 0xB0 50 {motor.get('shape', 0)}\n"
    s += f"100 midi 0x90 {note} {vel}\n"
    if off_ms:
        s += f"{off_ms} midi 0x80 {note} 0\n"
    s += f"{dur_ms} end\n"
    with open(out + ".txt", "w") as f:
        f.write(s)
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True, env=dict(os.environ, FRODHES_NOBUDGET="1"))
    w = wave.open(out + ".wav")
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)


def residual_db(c, g):
    """Align by cross-correlation (the C note starts at the next block after its MIDI event): the residual energy
    re the golden signal (dB) and its RMS in LSB."""
    first = int(np.argmax(np.abs(c) > 0))
    start = max(first - 64, 0)
    L = min(len(g), len(c) - start - 128)
    errs = [np.sum((c[lag:lag + L] - g[:L]) ** 2) for lag in range(start, start + 128)]
    i = int(np.argmin(errs))
    return 10 * np.log10(errs[i] / np.sum(g[:L] ** 2)), float(np.sqrt(errs[i] / L))


def off_rel(off_ms, start_ms=100.0):
    """the key-up reaches the engine at the first block after its time; relative to the note's own start"""
    return (np.ceil(off_ms / BLK) * BLK - np.ceil(start_ms / BLK) * BLK) / 1000.0


@pytest.mark.parametrize("note,vel", [(53, 100), (60, 127), (65, 40), (69, 100), (77, 127), (89, 64), (40, 100),
                                      (100, 100)])
def test_c_matches_golden_held(note, vel):
    c = c_render(note, vel, 2200)
    g = gm.render_batch(SPEC, [note], [vel], 1.8)[0]
    r, rms = residual_db(c, g)
    assert r <= -60.0 or rms <= 0.5, (note, vel, r, rms)


@pytest.mark.parametrize("note", [53, 72])
def test_c_matches_golden_with_damper(note):
    c = c_render(note, 100, 1600, off_ms=700, tag="vbd")
    g = gm.render_batch(SPEC, [note], [100], 1.2, off_at=off_rel(700))[0]
    r, rms = residual_db(c, g)
    assert r <= -60.0 or rms <= 0.5, (note, r, rms)


@pytest.mark.parametrize("note", [53, 77])
def test_c_matches_golden_no_tube(note):
    c = c_render(note, 100, 2200, tag="vbt", bell=0)
    g = gm.render_batch(SPEC, [note], [100], 1.8, knobs=dict(bell=0))[0]
    r, rms = residual_db(c, g)
    assert r <= -60.0 or rms <= 0.5, (note, r, rms)


@pytest.mark.parametrize("note,tone,bark", [(60, 127, 127), (72, 100, 0)])
def test_c_matches_golden_edge_strikes(note, tone, bark):
    c = c_render(note, 127, 2200, tag="vbe", tone=tone, bark=bark, hammer=127)
    g = gm.render_batch(SPEC, [note], [127], 1.8, knobs=dict(tone=tone, bark=bark, hammer=127))[0]
    r, rms = residual_db(c, g)
    assert r <= -60.0 or rms <= 0.5, (note, r, rms)


def c_render_motor(note, mot):
    """the motor set well before the note (its depth slewed in), CC 10 at 60 ms (the phase to 0 at the next block)"""
    out = os.path.join(ROOT, "build", "host", f"vbm-{note}")
    s = ("20 midi 0xB0 119 127\n20 midi 0xB0 104 0\n21 midi 0xB0 79 64\n"
         + KNOBS.format(**gm.DEFAULT_KNOBS).replace("55 midi", "22 midi")
         + f"23 midi 0xB0 47 127\n23 midi 0xB0 48 {mot['depth']}\n23 midi 0xB0 49 {mot['rate']}\n"
         f"23 midi 0xB0 50 {mot.get('shape', 0)}\n60 midi 0xB0 10 127\n100 midi 0x90 {note} 100\n2200 end\n")
    with open(out + ".txt", "w") as f:
        f.write(s)
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True, env=dict(os.environ, FRODHES_NOBUDGET="1"))
    w = wave.open(out + ".wav")
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)


@pytest.mark.parametrize("note,mot", [(53, dict(depth=110, rate=78)), (69, dict(depth=127, rate=105, shape=127)),
                                      (89, dict(depth=64, rate=40))])
def test_c_matches_golden_with_motor(note, mot):
    """the motor (the tube's radiation gated, x its make-up; the coupling untouched): <= -50 dB. The golden model's
    phase at the note is k blocks after the reset; the best k of 12 (the host's block grid, not a free fit: one k
    matches, its neighbours are 20+ dB worse)"""
    import bartube as bt
    c = c_render_motor(note, mot)
    best = min(residual_db(c, gm.render_batch(SPEC, [note], [100], 1.8, motor=dict(
        mot, ph0=(k * 256 * bt.mot_inc(mot["rate"])) & 0xFFFFFFFF))[0]) for k in range(12))
    assert best[0] <= -50.0 or best[1] <= 0.5, (note, best)
