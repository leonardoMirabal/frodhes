# SPDX-License-Identifier: GPL-3.0-only
"""TONE above its middle changes the colour, not the loudness (rhodes.c rh_tone_gain, firmware/gen/rh_tone_g.h from
tools/tone_level.py --write).

Above 64 the pickup offset moves toward the pole axis and the note got quieter, up to ~10 dB at 127 (STAGE 77, soft
notes). The voice gain takes that back at the note-on (0 cost per sample), from the loudness against each key's own
offset:
- the mean over C2 C3 A3 C5 C6 stays within 0.75 LU of TONE 64 on every TYPE at v40 / 80 / 100 / 127 and TONE 80 ..
  127 (the uncompensated engine: -2 .. -10 LU), any one key (A0, C2 .. C6, C7) within 2 LU;
- the colour still moves: H2/H1 at TONE 127 is well above TONE 64's;
- TONE <= 64 is untouched (Gate R: those renders byte-identical), and a TONE turned while a note sounds does not
  change it (the gain is taken at the note-on, as the offset)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from hostrun import FS, run  # noqa: E402
from tone_level import KEYS, lufs  # noqa: E402

TYPES = ["STAGE77", "SUITCASE", "FELT", "DYNO"]


def note(ty, tone, n, vel, extra=()):
    ev = ["20 midi 0xB0 119 127", "30 midi 0xB0 104 0", f"40 midi 0xB0 80 {ty * 32}", f"50 midi 0xB0 28 {tone}",
          f"200 midi 0x90 {n} {vel}", *extra, f"800 midi 0x80 {n} 0"]
    x = run(f"tone-{ty}-{tone}-{n}-{vel}-{len(extra)}", ev, 900).L.astype(float)
    seg = x[int(0.2 * FS):int(0.8 * FS)]
    return seg[int(np.argmax(np.abs(seg) > 0)):]


def loud(seg):
    return lufs(seg[int(0.05 * FS):int(0.6 * FS)])


@pytest.mark.parametrize("ty", range(4))
def test_tone_keeps_the_loudness(ty):
    for vel in (40, 80, 100, 127):
        ref = {n: loud(note(ty, 64, n, vel)) for n in KEYS + (21, 96)}
        for tone in (80, 96, 112, 127):
            d = {n: loud(note(ty, tone, n, vel)) - ref[n] for n in ref}
            mean = float(np.mean([d[n] for n in KEYS]))
            assert abs(mean) < 0.75, f"{TYPES[ty]} v{vel} TONE {tone}: the level moved {mean:+.2f} LU"
            worst = max(d.values(), key=abs)
            assert abs(worst) < 2.0, f"{TYPES[ty]} v{vel} TONE {tone}: a key moved {worst:+.2f} LU"


def h21(seg, n):
    s = seg[int(0.03 * FS):int(0.3 * FS)]
    S = np.abs(np.fft.rfft(s * np.hanning(len(s)), 1 << 17)) ** 2
    fr = np.fft.rfftfreq(1 << 17, 1 / FS)
    f0 = 440.0 * 2 ** ((n - 69) / 12)
    h = [np.sum(S[(fr >= 0.97 * k * f0) & (fr <= 1.03 * k * f0)]) for k in (1, 2)]
    return 10 * np.log10(h[1] / h[0])


def test_tone_still_brightens():
    for ty in (0, 3):
        lo, hi = h21(note(ty, 64, 57, 100), 57), h21(note(ty, 127, 57, 100), 57)
        assert hi - lo > 3, f"{TYPES[ty]}: H2/H1 {lo:.1f} at TONE 64, {hi:.1f} at 127"


def test_tone_taken_at_the_note_on():
    """TONE turned after the strike: the sounding note keeps its offset and its level (both at the note-on)"""
    a = note(0, 64, 57, 100)
    b = note(0, 64, 57, 100, extra=("300 midi 0xB0 28 127",))
    assert np.array_equal(a[:int(0.5 * FS)], b[:int(0.5 * FS)])
