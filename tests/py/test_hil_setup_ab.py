# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_setup_ab.py's chord detector on synthetic notes (one note with its second harmonic louder than the
fundamental, octaves C2..C6; a major and a minor triad) and its --dry-run plan (ends with chord mode off)."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import hil_setup_ab as sab  # noqa: E402

SR = 48000


def tone(f, amps=(0.5, 1.0, 0.4, 0.2, 0.1), seed=0):
    t = np.arange(SR) / SR
    rng = np.random.default_rng(seed)
    x = sum(a * np.sin(2 * np.pi * f * (h + 1) * t + rng.uniform(0, 6)) * np.exp(-t * (1 + h)) for h, a in enumerate(amps))
    return x + 1e-4 * rng.standard_normal(len(t))


@pytest.mark.parametrize("midi", [36, 48, 60, 72, 84])
def test_one_note_is_not_a_chord(midi):
    f = 440 * 2 ** ((midi - 69) / 12)
    db, f0 = sab.chord_db(tone(f), SR)
    assert not sab.is_chord(db), (midi, db, f0)


@pytest.mark.parametrize("shape", [(0, 4, 7), (0, 3, 7)])
@pytest.mark.parametrize("midi", [48, 60, 72])
def test_a_triad_is_a_chord(shape, midi):
    x = sum(tone(440 * 2 ** ((midi + s - 69) / 12), seed=s) for s in shape)
    db, f0 = sab.chord_db(x, SR)
    assert sab.is_chord(db), (midi, shape, db, f0)


def test_dry_run_ends_off():
    r = subprocess.run([sys.executable, "tools/hil_setup_ab.py", "--fwsc", "x.fwsc", "--rounds", "3", "--dry-run"],
                       cwd=ROOT, capture_output=True, text=True, check=True)
    rounds = [ln for ln in r.stdout.splitlines() if ln.startswith("round")]
    assert len(rounds) == 4 and "CC 56 0" in rounds[-1] and "CC 56 3" in rounds[0]


def vib(f, depth=0.006, rate=5.5, seed=0):
    """a note with a vibrato (a chorus-like smear, +-depth), its harmonics, decaying"""
    t = np.arange(SR) / SR
    rng = np.random.default_rng(seed)
    ph = 2 * np.pi * np.cumsum(f * (1 + depth * np.sin(2 * np.pi * rate * t))) / SR
    x = sum(a * np.sin((h + 1) * ph) * np.exp(-t * (1 + h)) for h, a in enumerate((0.6, 1.0, 0.3, 0.15)))
    return x + 3e-3 * rng.standard_normal(len(t))


def test_verdict_by_the_references():
    c4 = 261.63
    off, on = vib(c4), sum(vib(c4 * 2 ** (s / 12), seed=s) for s in (0, 4, 7))
    r_off, r_on = sab.profile(off, SR), sab.profile(on, SR)
    assert sab.prof_dist(r_on, r_off) >= sab.REF_DIST_DB
    for seed in (5, 6):                                # takes again, another noise and vibrato rate
        one = vib(c4, rate=4.0, seed=seed)
        tri = sum(vib(c4 * 2 ** (s / 12), rate=4.0, seed=seed + s) for s in (0, 4, 7))
        assert sab.verdict(sab.profile(one, SR), r_on, r_off)[0] == "one note"
        assert sab.verdict(sab.profile(tri, SR), r_on, r_off)[0] == "chord"


def test_onset_cut():
    x = np.zeros(3 * SR)
    x[int(1.234 * SR):] = 0.5
    y = sab.onset_cut(x, SR, 1.2)
    assert len(y) == SR and y[0] == 0.5
