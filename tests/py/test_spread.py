# SPDX-License-Identifier: GPL-3.0-only
"""SPREAD (S13, params.c P_SPREAD, CC 105): each Rhodes key's stereo position on the 88-key harp (A0 left .. C8
right), a side signal S next to the mono sum M (rhodes.c rh_side), L = M + S, R = M - S before the stereo effects,
level-matched to the mono stages (fx.c fx_spread). SPREAD 0 (the default) is the mono sum with the dry width's side
(fx.c fx_width, stage 2: SPREAD's own side replaces it); the centre stays the mono sum; the position follows the key; with DRIVE / COMP on the image stays."""
import os
import subprocess
import wave

import numpy as np
import pytest

import hostrun  # noqa: E402  (tests/py: the per-worker render paths)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
DRY = [(81, 0), (91, 0)]                     # DRIVE 0, REVERB 0: no stereo effect, nothing nonlinear (F01's are on)
LOW, MID, TOP = 33, 64, 96


@pytest.fixture(scope="module", autouse=True)
def host_build():
    subprocess.run(["make", "-C", ROOT, "build/host/frodhes"], check=True, capture_output=True)


def run(tag, ccs, notes, end_ms=None, spread=None, pc=None):
    out = hostrun.out(f"spread-{tag}")
    lines = [] if pc is None else [f"10 midi 0xC0 {pc} 0"]
    lines += [f"20 midi 0xB0 {cc} {v}" for cc, v in ccs]
    if spread is not None:
        lines.append(f"25 midi 0xB0 105 {spread}")
    t = 300
    for n in notes:
        lines += [f"{t} midi 0x90 {n} 100", f"{t + 500} midi 0x80 {n} 0"]
        t += 700
    lines.append(f"{end_ms or t + 500} end")
    open(out + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    return x[:, 0], x[:, 1]


def balance(L, R, i):
    """L/R level (dB) of the i-th note's 50..450 ms"""
    a, b = int((0.35 + 0.7 * i) * FS), int((0.75 + 0.7 * i) * FS)
    return 20 * np.log10(np.sqrt(np.mean(L[a:b] ** 2)) / np.sqrt(np.mean(R[a:b] ** 2)))


def test_spread0_is_the_mono_sum():
    """SPREAD 0 sent (and the default, not sent): the same samples; on a dry setup L - R is only the dry width's side
    (stage 2: L == R before), under -18 dB re the mono sum"""
    a = run("off", DRY, [LOW, MID, TOP])
    b = run("zero", DRY, [LOW, MID, TOP], spread=0)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    m, s = (a[0] + a[1]) / 2, (a[0] - a[1]) / 2
    assert 10 * np.log10(np.mean(s ** 2) / np.mean(m ** 2)) < -18.0


def test_position_follows_the_key():
    """SPREAD 127 dry: A1 left, C7 right, by the law's |p| (A1 -0.72: L/R = 0.28 / 1.72, -15.8 dB; C7 +0.72);
    E4 near the centre (-0.01); SPREAD 64 about half as wide"""
    L, R = run("127", DRY, [LOW, MID, TOP], spread=127)
    lo, mid, top = (balance(L, R, i) for i in range(3))
    assert -17.0 < lo < -14.5 and 14.5 < top < 17.0 and abs(mid) < 0.5, (lo, mid, top)
    L2, R2 = run("64", DRY, [LOW, MID, TOP], spread=64)
    lo2, top2 = balance(L2, R2, 0), balance(L2, R2, 2)
    assert -8.0 < lo2 < -5.5 and 5.5 < top2 < 8.0, (lo2, top2)


def test_centre_is_the_mono_sum():
    """dry, SPREAD 127: (L + R) / 2 is the SPREAD 0 output's within the rounding (the side's own filters and the DC
    blockers round per channel): <= 2 LSB (SPREAD 0's (L + R) / 2: the dry width's side cancels there)"""
    L0, R0 = run("c0", DRY, [LOW, MID, TOP], spread=0)
    M = (L0 + R0) / 2
    L, R = run("c127", DRY, [LOW, MID, TOP], spread=127)
    assert np.max(np.abs((L + R) / 2 - M)) <= 2


def test_image_through_drive_and_comp():
    """DRIVE 90 (the boot TYPE) and COMP on: the side is level-matched to what the mono stages did, so the image
    stays on its side and at least 60 % as wide in dB as the dry one (the drive's own harmonics of the sum are in the
    centre: A1 -11.1 dB against -15.9 dry, C7 +14.7 against +15.9). 0.9.9: FDRV's curve at 2x delays the mono 1.84
    samples; the side follows it (fx_spread, dr_sal); unaligned, C7 came out +7.2 dB, so the top keeps 85 %"""
    L, R = run("dry", DRY, [LOW, TOP], spread=127)
    d0 = [balance(L, R, i) for i in range(2)]
    L, R = run("drv", [(81, 90), (91, 0), (106, 127)], [LOW, TOP], spread=127)
    d1 = [balance(L, R, i) for i in range(2)]
    assert all(a * b > 0 and abs(b) >= 0.6 * abs(a) for a, b in zip(d0, d1)), (d0, d1)
    assert abs(d1[1]) >= 0.85 * abs(d0[1]), (d0, d1)
    assert np.all(np.isfinite(L)) and np.max(np.abs(L)) < 32767


def test_bars_engine_ignores_spread():
    """VIBES (F13, the bar engine) with SPREAD 127: the same samples as with SPREAD 0"""
    a = run("vb0", [], [LOW + 24, MID], spread=0, pc=12)
    b = run("vb127", [], [LOW + 24, MID], spread=127, pc=12)
    assert np.max(np.abs(a[0])) > 1000
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_drive_fades_keep_the_side_smooth():
    """the side follows FDRV's wet share (fx.c fx_spread, dr_sal: z^-1 + an allpass, the half-bands' delay): a
    held C7 at SPREAD 127 while DRIVE goes 0 -> 90 -> 0 and MIX 127 -> 40 -> 127 (fades and glides): the side
    (L - R) / 2 has no step: its second difference around each move stays within 1.1x the held note's"""
    out = hostrun.out("spread-fades")
    ev = [(400, 81, 90), (900, 83, 40), (1400, 83, 127), (1900, 81, 0)]
    lines = ["20 midi 0xB0 81 0", "20 midi 0xB0 91 0", "25 midi 0xB0 105 127", "100 midi 0x90 96 127"]
    lines += [f"{t} midi 0xB0 {c} {v}" for t, c, v in ev] + ["2400 midi 0x80 96 0", "2500 end"]
    open(out + ".txt", "w").write("\n".join(lines) + "\n")
    subprocess.run([BIN, out, out + ".txt"], check=True, capture_output=True)
    w = wave.open(out + ".wav")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float).reshape(-1, 2)
    s = (x[:, 0] - x[:, 1]) / 2
    d2 = np.abs(np.diff(s, 2))

    def peak(t0, t1):
        return d2[int(t0 * FS / 1000):int(t1 * FS / 1000)].max()
    for t, _, _ in ev:
        held = max(peak(t - 120, t - 20), peak(t + 200, t + 300))
        assert peak(t - 10, t + 120) <= 1.1 * held + 2, (t, peak(t - 10, t + 120), held)
