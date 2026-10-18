# SPDX-License-Identifier: GPL-3.0-only
"""The VIBES motor keeps the level of the discs-open sound (FM-1 vibes_jump, round 2, 2026-10-18: +4.4 dB/s along the
hold and a +4.7 LU jump when switched off under a chord, measured the same on USB and the jack).

The cause was the model: the discs scaled the bar <-> tube coupling, so with the motor on the tube drained the bar
less and the bar rang about twice as long (chord C3 G3 C4 E4: -9.5 dB/s with the discs open, -5.0 with the motor),
and the fixed make-up (x 1.63, set on the first 3 s) only matched the start. Switching off then opened the tubes on a
bar that had kept its energy. Now the motor gates the tube's radiation only, with a make-up of 1 / sqrt(mean g^2) for
the disc law (bars.c bar_mot_mk), so the bar's decay is the same with the motor on or off.

The device suites' own measures on host renders (resampled to the analysis rate of tools/hil_scan.py): vibes_jump's
motor rows (on vs off over whole cycles, the slope re dry along the hold, the step at on / off under the chord) and
vibes_motor's tremolo (the V1 partial's AM at DEPTH 110 / 127 on F3 A4 F6, the SHAPE pulse, the rate, switching
under a held note)."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

import hostrun  # noqa: E402  (tests/py: the per-worker render paths)
from scipy.signal import resample_poly

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_scan as h  # noqa: E402

BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = h.FS                                           # the analysis rate (48 kHz, as the USB captures)
HZ = h.mot_hz(78)                                   # the default SPEED, 3.52 Hz
FXCH = h.FXCH                                       # vibes_jump's chord (C3 G3 C4 E4, v108)
VKEYS = h.VKEYS                                     # F3 A4 F6


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def take(tag, motor, notes, vel, on, off, end, cc=()):
    """VRig.reset (defaults, DRY: drive / reverb off, VIBES, the motor) + CC 10 50 ms before the notes; cc: (s, CC,
    value) at absolute times. The host's 44.1 kHz resampled to FS; returns (mono, the onset index)"""
    s = ["20 midi 0xB0 81 0", "21 midi 0xB0 91 0", "60 midi 0xB0 79 64", f"61 midi 0xB0 47 {127 if motor else 0}"]
    s += [f"{int(on * 1000) - 50} midi 0xB0 10 127"]
    s += [f"{int(on * 1000)} midi 0x90 {n} {vel}" for n in notes] + [f"{int(off * 1000)} midi 0x80 {n} 0" for n in notes]
    s += [f"{int(t * 1000)} midi 0xB0 {c} {v}" for t, c, v in cc] + [f"{int(end * 1000)} end"]
    s.sort(key=lambda ln: int(ln.split()[0]))
    out = hostrun.out(f"motlvl-{tag}")
    with open(out + ".txt", "w") as f:
        f.write("\n".join(s) + "\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0].astype(float) / 32768
    return resample_poly(x, 160, 147), int(on * FS)


@pytest.fixture(scope="module")
def jump():
    """vibes_jump's three takes: dry, the motor held on, the motor switched on at 1.2 s and off at 2.4 s"""
    off = take("ref", False, FXCH, 108, 0.5, 3.8, 4.5)
    held = take("held", True, FXCH, 108, 0.5, 3.8, 4.5)
    tog = take("tog", False, FXCH, 108, 0.5, 3.8, 4.5, cc=[(0.5 + 1.2, 47, 127), (0.5 + 2.4, 47, 0)])
    return off, held, tog


def lq(t, a, b):
    x, i = t
    return h.lu(x[max(0, i + int(a * FS)):i + int(b * FS)])


def test_motor_on_vs_off_held(jump):
    off, held, _ = jump
    hw = int(3.2 * HZ) / HZ                         # whole cycles in 0..3.2 s
    d = lq(held, 0, hw) - lq(off, 0, hw)
    assert abs(d) <= 1.0, d                         # JUMP_LU (the device: +0.47 before, ok)


def test_motor_level_re_dry_along_the_hold(jump):
    """vibes_jump slope(): windows of whole cycles (>= 0.25 s), 0.2..3.2 s; was +4.44 dB/s on host (FM-1 +4.43 / +4.49)"""
    off, held, _ = jump
    w = 1 / HZ
    while w < 0.25:
        w *= 2
    ks = np.arange(0.2, 3.2 - w, w / 2)
    slope = float(np.polyfit(ks, [lq(held, k, k + w) - lq(off, k, k + w) for k in ks], 1)[0])
    assert abs(slope) <= 1.0, slope


def test_motor_switched_under_a_chord(jump):
    """vibes_jump steps(): 2 cycles before / after each switch, the chord's own decay (dry) taken out; was -0.77 /
    +4.91 LU on host (FM-1 -1.21 / +4.66 USB)"""
    off, _, tog = jump
    sww = next(k / HZ for k in range(1, 9) if 0.38 <= k / HZ <= 0.6)
    st = []
    for s in (1.2, 2.4):
        aa, bb = (s + 0.05 - 0.1, s + 0.05 + sww - 0.1), (s - 0.05 - sww - 0.1, s - 0.05 - 0.1)
        st.append((lq(tog, *aa) - lq(tog, *bb)) - (lq(off, *aa) - lq(off, *bb)))
    assert all(abs(x) <= 1.5 for x in st), st
    x, i = tog
    ck = sum(h.clicks(x[i + int((s - 0.1) * FS):i + int((s + 0.2) * FS)] * 32768) for s in (1.2, 2.4))
    assert ck == 0


@pytest.mark.parametrize("note", [41, 53, 69, 89])
def test_motor_level_single_keys(note):
    """the make-up keeps a single key level too, along its decay (before: F6 died 4 s later with the motor)"""
    off = take(f"k{note}", False, (note,), 100, 0.5, 3.8, 4.5)
    on = take(f"k{note}m", True, (note,), 100, 0.5, 3.8, 4.5)
    hw = int(3.2 * HZ) / HZ
    assert abs(lq(on, 0, hw) - lq(off, 0, hw)) <= 1.0
    w = 2 / HZ
    ks = [k for k in np.arange(0.2, 3.2 - w, w / 2) if lq(off, k, k + w) > -66]   # (over the gate: F6 ends near 2.4 s
    slope = float(np.polyfit(ks, [lq(on, k, k + w) - lq(off, k, k + w) for k in ks], 1)[0])   # and its last LSBs differ)
    assert abs(slope) <= 1.0, slope


@pytest.fixture(scope="module")
def tremolo():
    T = {}
    for lab, cc, mot in (("off", [(0.1, 48, 127)], False), ("d0", [(0.1, 48, 0)], True), ("d110", [], True),
                         ("d127", [(0.1, 48, 127)], True), ("s127", [(0.1, 48, 127), (0.1, 50, 127)], True)):
        T[lab] = take("am-" + lab, mot, VKEYS, 100, 0.3, 4.8, 5.2, cc=cc)
    return T


def am(t, n):
    x, i = t
    seg = h.cut(x, i, 0.5, 3.0 if n < 80 else 2.0)
    tr, _ = h.partial_trace(seg, h.f1(n), n=2048, hop=240)
    return h.pfit(tr, FS / 240, HZ)


@pytest.mark.parametrize("note", VKEYS)
def test_motor_tremolo_depth_and_shape(tremolo, note):
    """vibes_motor: the V1 partial's AM >= 6 dB at DEPTH 110 (the default) and 127, < 1 dB at DEPTH 0 and OFF;
    SHAPE 127 a sharper pulse (H2/H1 x 1.5)"""
    r = {lab: am(t, note) for lab, t in tremolo.items()}
    assert r["d110"]["pp"] >= 6.0 and r["d127"]["pp"] >= 6.0, (r["d110"]["pp"], r["d127"]["pp"])
    assert r["d0"]["pp"] < 1.0 and r["off"]["pp"] < 1.0
    if note < 80:
        h21 = [r[k]["amps"][1] / (r[k]["amps"][0] + 1e-12) for k in ("d127", "s127")]
        assert h21[1] >= 1.5 * h21[0], h21


@pytest.mark.parametrize("v", [0, 78, 127])
def test_motor_rate(v):
    """vibes_motor SPEED: A4, the whole signal's envelope until it falls 50 dB (the note now ends as the dry one)"""
    x, i = take(f"rate{v}", True, (69,), 100, 0.3, 6.3, 6.6, cc=[(0.1, 48, 127), (0.1, 49, v)])
    e = h.env_db(h.cut(x, i, 0.5, 5.8))
    e = h.until_floor(e)
    want = h.mot_hz(v)
    got = h.rate_of(e, FS / 240, want)
    assert abs(got / want - 1) <= (0.10 if v == 0 else 0.05), (got, want)


def test_motor_switching_under_a_held_note():
    """vibes_motor: the pedal down, the motor on / off / on / off: no click, the AM comes and goes"""
    sw = [(0.05, 64, 127), (0.1, 48, 127)] + [(0.3 + t, 47, v) for t, v in ((0.7, 127), (1.7, 0), (2.7, 127), (3.2, 0))]
    x, i = take("switch", False, (57,), 100, 0.3, 5.3, 4.5, cc=sw)
    d2 = np.abs(np.diff(x, 2))
    ref = d2[i + int(0.3 * FS):i + int(0.65 * FS)].max()
    worst = max(d2[i + int((s - 0.02) * FS):i + int((s + 0.1) * FS)].max() for s in (0.7, 1.7, 2.7, 3.2)) / ref
    on = h.pfit(h.env_db(h.cut(x, i, 0.9, 1.65)), FS / 240, HZ)["pp"]
    off = h.pfit(h.env_db(h.cut(x, i, 1.9, 2.65)), FS / 240, HZ)["pp"]
    ck = sum(h.clicks(x[i + int((s - 0.05) * FS):i + int((s + 0.15) * FS)] * 32768) for s in (0.7, 1.7, 2.7, 3.2))
    assert worst <= 1.2 and on >= 4.0 and off < 1.0 and ck == 0, (worst, on, off, ck)
