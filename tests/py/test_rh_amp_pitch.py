# SPDX-License-Identifier: GPL-3.0-only
"""S4, amplitude -> pitch in the bass (rhodes.c rh_s4_e, RH_KAP): a hard-struck low note starts a few cents sharp and
settles as it rings down; soft notes, BARK and the keys from 48 up are left alone; nothing clicks.

The engine alone (tests/c/rh_gate.c: rhodes.c, its output before the effects) against -DRH_NO_S4 (the engine before
S4): the pitch of V1 tracked in 0.4 s windows, the difference in cents."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402

OUT = os.path.join(ROOT, "build", "rh_s4", os.environ.get("PYTEST_XDIST_WORKER", "main"))
FS = 44100


def binary(tag, defs=()):
    exe = os.path.join(OUT, f"rh_gate_{tag}")

    def make(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DRH_VOICES=9u", *defs,
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", path, os.path.join(ROOT, "tests", "c", "rh_gate.c")], check=True)
    return hostrun.once(exe, make)


@pytest.fixture(scope="module")
def bins():
    return binary("s4"), binary("ref", ["-DRH_NO_S4"])


def render(exe, note, vel, bark=64, typ=0, blocks=700, offblk=-1, chord=()):
    p = subprocess.run([exe, "render", *map(str, (note, vel, typ, bark, 64, 64, 64, 64, 64, offblk, 0, blocks)),
                        *map(str, chord)], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.int32).astype(float)


def track(x, f0, t0, t1, hop=0.1, win=0.4):
    """V1's frequency (Hz) in windows from t0 to t1: the spectrum's peak near f0, zero-padded, parabolic"""
    n, N, out = int(win * FS), 1 << 18, []
    f = np.fft.rfftfreq(N, 1 / FS)
    band = (f > f0 * 0.93) & (f < f0 * 1.07)
    for t in np.arange(t0, t1 - win + 1e-9, hop):
        X = np.abs(np.fft.rfft(x[int(t * FS):int(t * FS) + n] * np.hanning(n), N))
        i = int(np.argmax(X * band))
        a, b, c = np.log(X[i - 1:i + 2] + 1e-12)
        out.append((i + 0.5 * (a - c) / (a - 2 * b + c)) * FS / N)
    return np.array(out)


def cents(bins, note, vel, **kw):
    """S4's offset in cents (S4 re the reference) in the first window and at 3 s"""
    f0 = 440 * 2 ** ((note - 69) / 12)
    a, b = track(render(bins[0], note, vel, **kw), f0, 0.0, 3.4), track(render(bins[1], note, vel, **kw), f0, 0.0, 3.4)
    c = 1200 * np.log2(a / b)
    return c[0], c[-1]


@pytest.mark.parametrize("note", [28, 33])
def test_hard_bass_starts_sharp_and_settles(bins, note):
    """E1 / A1 v127: +3 .. +8 cents at the start (the plan's range), less than half of that by 3 s; v100 well under;
    v40 inaudible (< 0.3 c)"""
    s, e = cents(bins, note, 127)
    assert 3.0 < s < 8.0 and e < s / 2, (s, e)
    s100, _ = cents(bins, note, 100)
    assert 0.5 < s100 < s / 2, s100
    s40, _ = cents(bins, note, 40)
    assert abs(s40) < 0.3, s40


def test_tapers_to_nothing_from_key_48(bins):
    """less on the way up the bass (key 40 under E1's), none from 48 (C3) up: those renders are the reference's exactly"""
    s28, _ = cents(bins, 28, 127)
    s40, _ = cents(bins, 40, 127)
    assert 0.5 < s40 < s28, (s40, s28)
    for n in (48, 60, 72):
        assert np.array_equal(render(bins[0], n, 127, blocks=200), render(bins[1], n, 127, blocks=200)), n


@pytest.mark.parametrize("bark", [0, 127])
def test_bark_does_not_detune(bins, bark):
    """BARK scales the strike's force (the swing): kap is divided by its square, so the offset stays the velocity's"""
    s, _ = cents(bins, 28, 127, bark=bark)
    s64, _ = cents(bins, 28, 127)
    assert abs(s - s64) < 1.0, (s, s64)


def test_bass_chord_no_click(bins):
    """a v127 bass chord, released and struck again: S4's per-sub-block E steps add no click (the second difference
    stays that of the reference's within 5 %)"""
    chord = (33, 40, 43)
    a = render(bins[0], 28, 127, blocks=400, offblk=200, chord=chord)
    b = render(bins[1], 28, 127, blocks=400, offblk=200, chord=chord)
    d2 = lambda x: np.abs(np.diff(x, 2)).max()
    assert d2(a) < 1.05 * d2(b) + 8, (d2(a), d2(b))
    assert np.all(np.isfinite(a))
