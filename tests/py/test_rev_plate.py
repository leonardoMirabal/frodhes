# SPDX-License-Identifier: GPL-3.0-only
"""The PLATE reverb kernel (firmware/src/rev_plate.c), driven directly through tests/c/rev_plate_test.c with the
framework's input (decimated, at the lines' level):

- a start over another type's leftovers (garbage in every byte of its state) sounds exactly like a start over zeros;
- every tail ends in exact zeros and rev_plate_active() goes 0 after it (fx_quiet() and the flash writes rely on it);
- T60 follows DECAY (0.8 .. 6 s, exponential) within +-15 % at the default TONE and MOD, at every SIZE;
- signals up to many times full scale never overflow (|mid|, |side| < 2^18: the framework's exact return);
- its level at the middle settings is HALL's (+-2 dB), and SIZE, MOD, TONE each move what they should;
- the cost: its loops no longer than REV_US_PLATE's table says (build/frodhes.dis, when built) and PLATE + the
  decimator within HALL's share."""
import os
import re
import subprocess

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "rev_plate", os.environ.get("PYTEST_XDIST_WORKER", "") or "main")
SRC = os.path.join(ROOT, "firmware", "src", "rev_plate.c")
FS = 22050


@pytest.fixture(scope="module")
def exe():
    os.makedirs(OUT, exist_ok=True)
    e = os.path.join(OUT, "rev_plate_test")
    subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-I" + os.path.join(ROOT, "firmware", "src"),
                    "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", e,
                    os.path.join(ROOT, "tests", "c", "rev_plate_test.c"), "-lm"], check=True)
    return e


def run(exe, *a):
    return subprocess.run([exe] + [str(x) for x in a], check=True, capture_output=True, text=True).stdout.split()


def ir(exe, decay=64, damp=60, size=64, mod=64, level=60000, secs=10.0, name="ir"):
    f = os.path.join(OUT, name + ".bin")
    run(exe, "ir", decay, damp, size, mod, level, int(secs * FS / 16), f)
    a = np.fromfile(f, dtype=np.int32).reshape(-1, 2).astype(float)
    return a[:, 0] + a[:, 1], a[:, 0] - a[:, 1]


def hall(exe, size, damp=60, level=60000, secs=10.0):
    f = os.path.join(OUT, "hall.bin")
    run(exe, "hall", size, damp, level, int(secs * FS / 16), f)
    a = np.fromfile(f, dtype=np.int32).reshape(-1, 2).astype(float)
    return a[:, 0] + a[:, 1], a[:, 0] - a[:, 1]


def t60(L, R):
    """the mid-band T60 (the 354 .. 1414 Hz octaves, as a reverb time is quoted: TONE damps the top), Schroeder's
    backward integral, -5 .. -25 dB, x 3"""
    m = L + R
    X = np.fft.rfft(m)
    f = np.fft.rfftfreq(len(m), 1 / FS)
    X[(f < 354) | (f > 1414)] = 0
    e = np.fft.irfft(X, len(m)) ** 2
    edc = np.cumsum(e[::-1])[::-1]
    edc = 10 * np.log10(edc / edc[0] + 1e-30)
    return (np.argmax(edc < -25) - np.argmax(edc < -5)) / FS * 3


def rms(L, R, a=0.05, b=0.5):
    i, j = int(a * FS), int(b * FS)
    return np.sqrt(np.mean((L[i:j] ** 2 + R[i:j] ** 2) / 2))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_start_over_garbage_is_a_clean_start(exe, seed):
    assert run(exe, "stale", seed) == ["same", "4000"]


@pytest.mark.parametrize("decay,damp,size,mod", [(0, 60, 64, 64), (64, 60, 64, 64), (127, 60, 64, 64),
                                                  (127, 0, 0, 0), (127, 0, 127, 127), (127, 150, 127, 127),
                                                  (0, 150, 0, 127)])
def test_tail_ends_in_exact_silence(exe, decay, damp, size, mod):
    off, last, after = (int(x) for x in run(exe, "tail", decay, damp, size, mod))
    assert off > 0, "rev_plate_active() never went 0"
    assert last < off and after == 0, f"output after the kernel went inactive (last {last}, off {off}, {after})"
    assert off - last < 16384 // 16 + 64, "inactive much later than the last sound"


@pytest.mark.parametrize("size", [0, 64, 127])
@pytest.mark.parametrize("decay", [0, 32, 64, 96, 127])
def test_t60_follows_decay(exe, decay, size):
    want = 0.8 * 7.5 ** (decay / 127)
    got = t60(*ir(exe, decay=decay, size=size, level=8000, secs=max(4.0, want * 1.6)))
    assert abs(got / want - 1) < 0.15, f"DECAY {decay} SIZE {size}: T60 {got:.2f} s, want {want:.2f}"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_overflow(exe, seed):
    mx, _ = run(exe, "fuzz", seed, 30000)
    assert int(mx) < 1 << 18


def test_level_is_halls(exe):
    p = rms(*ir(exe, decay=64, size=64, secs=3))
    h = rms(*hall(exe, 75, secs=3))                  # HALL at MIX 64: size 40 + 64 * 70 / 127
    assert abs(20 * np.log10(p / h)) < 2.0, f"PLATE {p:.0f} vs HALL {h:.0f}"


def onset(L, R, frac=0.05):
    e = np.abs(L) + np.abs(R)
    return int(np.argmax(e > frac * e.max()))


def centroid(L, R, a=0.1, b=0.6):
    x = L[int(a * FS):int(b * FS)]
    s = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / FS)
    return float((s * f).sum() / s.sum())


def test_knobs_move_what_they_should(exe):
    # SIZE: the lines and taps x0.5 .. x1: the first reflections arrive twice as late
    small, big = onset(*ir(exe, size=0, secs=1, name="s0")), onset(*ir(exe, size=127, secs=1, name="s1"))
    assert big > 1.6 * small, (small, big)
    # T60 does not follow SIZE (DECAY sets it)
    assert abs(t60(*ir(exe, size=0, secs=4)) / t60(*ir(exe, size=127, secs=4)) - 1) < 0.15
    # MOD: 0 = no modulation; the tails differ, the level stays
    m0, m1 = ir(exe, mod=0, secs=2, name="m0"), ir(exe, mod=127, secs=2, name="m1")
    assert not np.array_equal(m0[0], m1[0])
    assert abs(20 * np.log10(rms(*m0) / rms(*m1))) < 1.5
    # TONE: dark (damp 150) has a lower centroid than bright (0)
    dark, bright = ir(exe, damp=150, secs=2, name="d1"), ir(exe, damp=0, secs=2, name="d0")
    assert centroid(*dark) < 0.7 * centroid(*bright)


def test_cost_within_hall():
    src = open(SRC).read()
    us = int(re.search(r"#define REV_US_PLATE (\d+)u", src).group(1))
    rt = open(os.path.join(ROOT, "firmware", "src", "rev_types.c")).read()
    dec = int(re.search(r"#define REV_US_DEC (\d+)u", rt).group(1))
    front = int(re.search(r"#define REV_US_FRONT (\d+)u", rt).group(1))
    fx = open(os.path.join(ROOT, "firmware", "src", "fx.c")).read()
    hall_us = int(re.search(r"#define FX_US_REVERB (\d+)u", fx).group(1)) - front
    assert us + dec <= hall_us, "PLATE + the decimator must cost no more than HALL"


# the loops REV_US_PLATE's table counts: function -> the most instructions each of its inner loops may take
LOOPS = {"pl_in": [62], "pl_half": [38, 32], "pl_taps": [16], "pl_taps_ms": [34]}


def test_loop_counts_as_the_table():
    dis = os.path.join(ROOT, "build", "frodhes.dis")
    if not os.path.exists(dis):
        pytest.skip("no firmware build (./build.sh)")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build as B
    text = open(dis).read()
    if "pl_half:" not in text:
        pytest.skip("the firmware build is not this tree's")
    for fn, lim in LOOPS.items():
        lps = B.hot_loops(text, fn)
        assert lps, f"{fn} not in the disassembly (inlined?)"
        assert len(lps) == len(lim), f"{fn}: {len(lps)} loops"
        for lp, n in zip(lps, lim):
            assert lp["n"] <= n and not lp["calls"], f"{fn}: a loop of {lp['n']} instructions (table: {n})"


def test_clean_room_text():
    src = open(SRC).read()
    assert "Dattorro" in src                      # the public paper it follows
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    assert not re.search(r"\b(float|double)\b", code)
