# SPDX-License-Identifier: GPL-3.0-only
"""The USB audio's 44.1 -> 48 kHz resampler (usb.c uac_tap48) against a plain reference built from uac_fir.h, bit for
bit: tests/uac_fir_test.c runs the firmware's own code on a seeded random signal (full scale, over full scale, quiet,
a square) and prints every 48 kHz frame; the reference is the polyphase FIR as specified there (each input frame
clamped to 16 bits enters the history, then the outputs whose newest input it is: phase ph, the 12 frames ending at
it, the taps of phase ph or of 159 - ph backwards, a 32-bit sum with the rounding 2^14, >> 15, clamped). A faster
uac_tap48 must leave the USB stream unchanged."""
import os
import re
import subprocess

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "uac_test")


def fir():
    src = open(os.path.join(ROOT, "firmware", "src", "uac_fir.h")).read()
    rows = re.findall(r"\{([-\d,\s]+)\}", src.split("UAC_FIR[80][12] =", 1)[1])
    t = np.array([[int(v) for v in r.split(",")] for r in rows], dtype=np.int64)
    assert t.shape == (80, 12)
    return t


def reference(x, taps):
    """x: (n, 2) int input frames -> (m, 2) output frames"""
    x = np.clip(x, -32768, 32767).astype(np.int64)
    hist = np.zeros((12, 2), dtype=np.int64)
    out, ph = [], 0
    for f in x:
        hist = np.vstack([hist[1:], f])
        while ph < 160:
            c = taps[ph] if ph < 80 else taps[159 - ph][::-1]
            s = (c[:, None] * hist).sum(axis=0) + (1 << 14)
            out.append(np.clip(s >> 15, -32768, 32767))
            ph += 147
        ph -= 160
    return np.array(out)


def run(secs, seed, pieces=False):
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, "uac_fir_test")
    subprocess.run(["cc", "-O1", "-w", "-I" + os.path.join(ROOT, "firmware", "src"), "-o", exe,
                    os.path.join(ROOT, "tests", "uac_fir_test.c")], check=True)
    txt = subprocess.run([exe, str(secs), str(seed)] + (["pieces"] if pieces else []), check=True, capture_output=True, text=True).stdout
    return np.array([[int(v) for v in ln.split()] for ln in txt.splitlines()], dtype=np.int64)


@pytest.mark.parametrize("pieces", [False, True])
def test_resampler_bit_exact(pieces):
    """the audio ISR's pieces of 32 frames, and pieces of 1 .. 100 frames (uac_tap48's passes split otherwise)"""
    secs, seed = 0.4, 7
    got = run(secs, seed, pieces)
    # the same input, regenerated here (tests/uac_fir_test.c: xorshift32, blocks of 256 frames, 4 kinds by 8 blocks)
    rng, blocks, frames = seed, int(secs * 44100.0 / 256), []
    for b in range(blocks):
        kind = (b // 8) % 4
        blk = []
        for i in range(512):
            rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
            v = (rng & 0xFFFF) - 32768
            if kind == 1:
                v *= 4
            elif kind == 2:
                v >>= 9
            elif kind == 3:
                v = 32767 if (i >> 5) & 1 else -32767
            blk.append(v)
        frames += [(blk[2 * i], blk[2 * i + 1]) for i in range(256)]
    want = reference(np.array(frames), fir())
    assert len(got) == len(want) and len(got) > blocks * 278
    bad = np.nonzero((got != want).any(axis=1))[0]
    assert not len(bad), f"{len(bad)} frames differ, first at {bad[0]}: {got[bad[0]]} vs {want[bad[0]]}"
