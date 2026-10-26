# SPDX-License-Identifier: GPL-3.0-only
"""KHLON and OHCD (firmware/src/drv_khlon.c, drv_ohcd.c), on the firmware's own code:

- bit for bit (KHLON; OHCD in tests/py/test_drive_kernels.py) the reference kernels they were designed and measured with (tests/c/drive_exact.c: the same
  coefficients, the reference's own block layout and loops), on noise and on a chord, over a grid of knobs;
- every knob moved while a chord sounds (AMOUNT 1 -> 127 -> 1, TONE 0 -> 127 -> 0, both at once, jumps): no frame
  with more highs or a higher peak than the knobs held at the settings passed (frame by frame, the same input); the
  detector is shown to catch KHLON's mode change made without its dip (a test build);
- KHLON's mode change (AMOUNT 125 <-> 126) dips through the dry signal (3 blocks) and never runs two modes;
- the level: about the loudness of the drive switched off at every AMOUNT and TONE (a chord, K-weighted).
tools/drive_scan.py runs the framework (fx_drive, 256 frames a call)."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bar_measure as bm  # noqa: E402
import drive_scan as ds  # noqa: E402

FS = 44100
TYPES = ["KHLON", "OHCD"]
OUT = os.path.join(ROOT, "build", "drive_exact")


def chord(n, amp=1.0, seed=0):
    """a Rhodes-like chord with a bass and a high line, slowly decaying notes re-struck every 0.5 s"""
    t = np.arange(n) / FS
    env = np.exp(-(t % 0.5) * 3.0) * np.minimum(1, (t % 0.5) * 2000)
    x = 6000 * np.sin(2 * np.pi * 55.0 * t) + sum(3500 * np.sin(2 * np.pi * f * t + i) for i, f in
                                                   enumerate((261.63, 329.63, 392.0, 493.88)))
    x = x * env + 2000 * np.sin(2 * np.pi * 1046.5 * t) * env
    return np.round(x * amp).astype(np.int64)


def noise(n, seed=1):
    return np.round(np.random.default_rng(seed).standard_normal(n) * 7000).astype(np.int64)


@pytest.fixture(scope="module")
def exact():
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"drive_exact-{os.getpid()}")
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-I" + os.path.join(ROOT, "firmware", "src"),
                    "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", exe,
                    os.path.join(ROOT, "tests", "c", "drive_exact.c"), "-lm"], check=True)
    yield exe
    os.remove(exe)


def run_exact(exe, typ, a, t, x):
    k = f"{os.getpid()}-{typ}-{a}-{t}"
    fi, ff, fr = (os.path.join(OUT, f"{p}{k}.raw") for p in ("in", "fw", "ref"))
    np.asarray(x, "<i4").tofile(fi)
    subprocess.run([exe, typ.lower(), str(a), str(t), fi, ff, fr], check=True)
    y = np.fromfile(ff, "<i4"), np.fromfile(fr, "<i4")
    for f in (fi, ff, fr):
        os.remove(f)
    return y


# (OHCD's kernel against its reference: tests/py/test_drive_kernels.py; its plain kernel is gone, 0.9.5)
@pytest.mark.parametrize("typ", ["KHLON"])
def test_bit_exact_with_the_reference_kernel(exact, typ):
    """the firmware TYPE (32-sample sub-blocks, its tables, its level fold) = the reference kernel (256-sample
    blocks) with the same coefficients, sample for sample: on noise and a chord, at a grid of AMOUNT x TONE (both
    of KHLON's modes)"""
    xs = [noise(3 * 8192), chord(3 * 8192, 1.5)]
    for a in (1, 2, 32, 64, 100, 125, 126, 127):
        for t in (0, 1, 64, 126, 127):
            for x in xs:
                f, r = run_exact(exact, typ, a, t, x)
                assert np.array_equal(f, r), (typ, a, t)
                assert np.abs(f).max() < 2 ** 22                      # (headroom: the bus is Q15 with headroom)


@pytest.fixture(scope="module")
def scan():
    e = ds.build(tag="pytest-kb")
    yield e
    os.remove(e)


def p(typ, amount=127, tone=64, mix=127):
    return ds.params_for(typ, amount, tone, mix)


FR = 64                                                     # a frame: 1.45 ms


def frames(y):
    """per frame: the energy above 8 kHz, and the peak (a click is a burst of highs, a thump a peak)"""
    from scipy.signal import butter, sosfilt
    y = np.asarray(y, float)
    h = sosfilt(butter(4, 8000, "hp", fs=FS, output="sos"), y)
    n = len(y) // FR
    return (h[:n * FR].reshape(n, FR) ** 2).mean(1), np.abs(y[:n * FR]).reshape(n, FR).max(1)


def ceiling(exe, typ, x, settings):
    """the frames' highest values over steady renders (the knobs held) at the settings a move passes through, and
    the dry signal (KHLON's mode change dips through it)"""
    hf, pk = frames(x)
    for a, t in settings:
        y, _, _ = ds.render(exe, x, p(typ, a, t))
        h, k = frames(y)
        hf = h if hf is None else np.maximum(hf, h)
        pk = k if pk is None else np.maximum(pk, k)
    return hf, pk


def clicks(y, ceil, skip=2048):
    """frames whose highs or peak stand above everything the held knobs give on the same input: 6 dB in highs
    (and no less than 60 dB under the frame's own peak: a step 70 dB down is no click), 3 dB in peak"""
    hf, pk = frames(y)
    c_hf, c_pk = ceil
    s = skip // FR
    bad = (hf[s:] > 4 * c_hf[s:] + (pk[s:] * 1e-3) ** 2 + 1.0) | (pk[s:] > 1.41 * c_pk[s:] + 64)
    return int(bad.sum())


def sweep(n_blocks, name, path):
    """[(block, NAME, value)]: one step a block along path (a list of values), from block 4"""
    return [(4 + i, name, v) for i, v in enumerate(path) if 4 + i < n_blocks]


GRID_A = [1, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 125, 126, 127]
GRID_T = [0, 16, 32, 48, 64, 80, 96, 112, 127]


@pytest.mark.parametrize("typ", TYPES)
def test_knob_sweeps_click_free(scan, typ):
    """AMOUNT 1 -> 127 -> 1, TONE 0 -> 127 -> 0 (one step a block), both together, and jumps (1 <-> 127, 0 <-> 127)
    while the chord sounds: no frame with more highs or a higher peak than the knobs held at the settings it passes
    (the steady renders' ceiling, frame by frame on the same input)"""
    n = 600 * 256
    x = chord(n, 1.5)
    up = list(range(1, 128)) + list(range(126, 0, -1))
    tu = list(range(0, 128)) + list(range(126, -1, -1))
    ca = ceiling(scan, typ, x, [(a, 64) for a in GRID_A])
    ct = ceiling(scan, typ, x, [(1, t) for t in GRID_T])
    cb = ceiling(scan, typ, x, [(a, t) for a in GRID_A[::2] for t in GRID_T[::2]])
    cj = ceiling(scan, typ, x, [(a, t) for a in (1, 127) for t in (0, 127)])
    cases = {
        "amount": (1, 64, sweep(600, "DRIVE", up + up), ca),
        "tone": (1, 0, [(b, "DRV_TONE", v) for b, _, v in sweep(600, "DRV_TONE", tu + tu)], ct),
        "both": (1, 127, sweep(600, "DRIVE", up + up) +
                 [(b, "DRV_TONE", v) for b, _, v in sweep(600, "DRV_TONE", tu[::-1] * 2)], cb),
        "jumps": (1, 0, [(b, "DRIVE", 127 if (b // 40) % 2 else 1) for b in range(40, 600, 40)] +
                  [(b + 20, "DRV_TONE", 127 if (b // 40) % 2 else 0) for b in range(40, 580, 40)], cj),
    }
    for name, (a0, t0, sw, ceil) in cases.items():
        y, _, _ = ds.render(scan, x, p(typ, a0, t0), sw)
        assert clicks(y, ceil) == 0, (typ, name)


def test_the_detector_catches_a_mode_change_without_the_dip(scan):
    """a test build whose KHLON changes mode without the dip (KH_DIP_TEST_OFF: the new mode from rest at once) is
    caught by the same detector on AMOUNT 125 <-> 126; the firmware (with the dip) is not"""
    e = ds.build(cflags=["-DKH_DIP_TEST_OFF"], tag="pytest-kb-nodip")
    try:
        x = chord(300 * 256, 1.5)
        sw = [(b, "DRIVE", 125 if (b // 20) % 2 else 126) for b in range(20, 300, 20)]
        ceil = ceiling(scan, "KHLON", x, [(125, 64), (126, 64)])
        y, _, _ = ds.render(e, x, p("KHLON", 126, 64), sw)
        assert clicks(y, ceil) >= 5
        y, _, _ = ds.render(scan, x, p("KHLON", 126, 64), sw)
        assert clicks(y, ceil) == 0
    finally:
        os.remove(e)


def test_khlon_mode_change_dips_through_dry(scan):
    """AMOUNT 125 -> 126 while the chord sounds: a block fading to the dry signal, a block of dry (the new mode
    starting from rest under it), a block fading in; the dry block is the input at the wet's level (0.9.6, #5: x g
    in Q12, g the wet / dry RMS ratio of the first dip block, 1/4 .. 4; the plain dry was a 3.9 dB level jump),
    bit for bit"""
    x = chord(80 * 256, 1.5)
    y, _, _ = ds.render(scan, x, p("KHLON", 125, 64), [(40, "DRIVE", 126)])
    i = 40 * 256
    assert not np.array_equal(y[i:i + 256], x[i:i + 256])
    d, o = x[i + 256:i + 512].astype(np.int64), y[i + 256:i + 512].astype(np.int64)
    g0 = int(round(4096 * np.sqrt(np.mean(o.astype(float) ** 2) / np.mean(d.astype(float) ** 2))))
    assert any(np.array_equal(o, (d * g) >> 12) for g in range(max(1024, g0 - 64), min(16384, g0 + 64) + 1))
    w = y[i - 512:i].astype(float)
    assert abs(20 * np.log10(np.sqrt(np.mean(o.astype(float) ** 2) / np.mean(w ** 2)))) < 1.5   # (the wet's level)
    assert not np.array_equal(y[i + 768:i + 1024], x[i + 768:i + 1024])


@pytest.mark.parametrize("typ", TYPES)
def test_level_about_the_drive_off(scan, typ):
    """K-weighted loudness of the chord through the TYPE within 3.5 dB of the dry chord (the drive switched off) at
    every AMOUNT x TONE corner and the middle (the levels were matched on three recorded phrases, each within about
    +-3 dB: the circuits change the spectrum, the bass-heavy phrase the most)"""
    x = chord(16 * 8192, 1.0)
    l0 = bm.lufs(x[8192:].astype(float))
    for a in (1, 32, 64, 96, 127):
        for t in (0, 64, 127):
            y, _, _ = ds.render(scan, x, p(typ, a, t))
            d = bm.lufs(y[8192:].astype(float)) - l0
            assert abs(d) < 3.5, (typ, a, t, round(d, 1))
