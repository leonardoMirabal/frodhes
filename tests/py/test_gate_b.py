# SPDX-License-Identifier: GPL-3.0-only
"""Gate B (IMPLEMENTATION-v3 S18, check C7): the C engine, run on the host, must match the float golden model
(tools/rhodes_model.py) note for note: residual energy <= -60 dB of the signal.

Each case plays one note over USB-MIDI into the host build of the firmware (build/host/frodhes) and the same note
through the golden model with the same generated parameters (docs/model/data/rhodes_spec.json)."""
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
import rhodes_model as gm  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
SPEC = json.load(open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")))
KEYS = {k["midi"]: k for k in SPEC["keys"]}


def c_render(note, vel, dur_ms, off_ms=None, tag="gb"):
    out = hostrun.out(f"{tag}-{note}-{vel}")
    scr = out + ".txt"
    with open(scr, "w") as f:
        f.write("50 midi 0xB0 119 127\n")                 # developer: bypass the effects, test the voice
        f.write("50 midi 0xB0 104 0\n")                   # NOISE off: the golden model has no thump / felt noise
        f.write(f"100 midi 0x90 {note} {vel}\n")
        if off_ms:
            f.write(f"{off_ms} midi 0x80 {note} 0\n")
        f.write(f"{dur_ms} end\n")
    subprocess.run([BIN, out, scr], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")[0::2].astype(float)
    return x


def gold(note, vel, dur_s, off_s=None):
    k = KEYS[note]
    return gm.render_batch([k], SPEC["pickup"], [vel], dur_s, off_at=off_s,
                           gains=[k["gain"]])[0]


def residual_db(c, g):
    """Align by cross-correlation (the C note starts at the next ISR block after its MIDI event) and return the
    residual energy relative to the golden signal, in dB."""
    n = min(len(g), len(c) - 4096)
    first = int(np.argmax(np.abs(c) > 0))           # the C note's first non-zero sample
    start = max(first - 64, 0)
    lags = range(start, start + 128)
    errs = []
    L = min(n, len(c) - start - 128)
    for lag in lags:
        d = c[lag:lag + L] - g[:L]
        errs.append(np.sum(d * d))
    i = int(np.argmin(errs))
    return 10 * np.log10(errs[i] / np.sum(g[:L] ** 2)), lags[i], float(np.sqrt(errs[i] / L))


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


@pytest.mark.parametrize("note,vel", [(28, 100), (45, 64), (60, 100), (69, 127), (84, 40), (96, 100)])
def test_c_matches_golden_held(note, vel):
    c = c_render(note, vel, 1600)
    g = gold(note, vel, 1.2)
    r, lag, rms = residual_db(c, g)
    # -60 dB re the signal, or (quiet notes) a residual within 1 LSB RMS: the 16-bit output's own rounding (the
    # 0.6 velocity calibration made key 84 at v = 40 a very quiet note: 0.72 LSB of rounding, -44 dB re it)
    assert r <= -60.0 or rms <= 1.0, (note, vel, r, rms, lag)


@pytest.mark.parametrize("note", [40, 72])
def test_c_matches_golden_with_damper(note):
    c = c_render(note, 100, 1600, off_ms=700, tag="gbd")
    # the note-off reaches the engine at the first block after 700 ms; the golden model takes it relative to the
    # note start, so measure where the C note started and place the damper at the same offset
    nz = int(np.argmax(np.abs(c) > 0))
    start_ms = 100.0
    blk = 256 / 44.1
    off_rel = (np.ceil((700.0) / blk) * blk - np.ceil(start_ms / blk) * blk) / 1000.0
    g = gold(note, 100, 1.2, off_s=off_rel)
    r, _, rms = residual_db(c, g)
    assert r <= -50.0 or rms <= 0.5, (note, r, rms, nz)
