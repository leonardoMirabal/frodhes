# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE framework's shared helpers (firmware/src/drive.c), on the firmware's own code (tests/c/drive_core.c) against
exact references computed here: the curve and its integral (exact rationals), ADAA-1 (the exact mean of the curve over
each step, within 1 LSB), the biquad and the one-pole (against float), and the table generator
(tools/gen_drive_tables.py: deterministic, the committed header, the classic drive's own curve)."""
import math
import os
import subprocess
import sys
from fractions import Fraction

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_drive_tables as gdt  # noqa: E402

OUT = os.path.join(ROOT, "build", "drive_core")
FS = 44100


@pytest.fixture(scope="module")
def exe():
    os.makedirs(OUT, exist_ok=True)
    e = os.path.join(OUT, f"drive_core-{os.getpid()}")
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", e,
                    os.path.join(ROOT, "tests", "c", "drive_core.c")], check=True)
    yield e
    os.remove(e)


def run(exe, x, curve, half, sh, mode, *args, out64=False):
    r = subprocess.run([exe, curve, str(half), str(sh), mode, *map(str, args)], check=True, capture_output=True,
                       input=np.asarray(x, dtype="<i4").tobytes())
    return np.frombuffer(r.stdout, dtype="<i8" if out64 else "<i4").astype(np.int64)


def fx_tanh_header():
    import re
    s = open(os.path.join(ROOT, "firmware", "gen", "fx_tables.h")).read()
    body = re.search(r"FX_TANH\[257\] = \{([^}]*)\}", s).group(1)
    return [int(v) for v in body.replace("\n", "").split(",")]


TANH = fx_tanh_header()


def write_curve(f, half):
    h = gdt.integral(f)
    p = os.path.join(OUT, f"curve-{os.getpid()}-{half}.bin")
    open(p, "wb").write(np.asarray(f, "<i2").tobytes() + np.asarray(h, "<i4").tobytes())
    return p


def exact_P(f, half, sh, x):
    """2^(sh + 1) x the integral of the interpolated curve from -X to x (flat beyond the ends), exactly"""
    X, S = half << sh, 1 << sh
    if x <= -X:
        return Fraction(2 * S * f[0] * (x + X))
    if x >= X:
        return Fraction(S * S * sum(f[j] + f[j + 1] for j in range(2 * half)) + 2 * S * f[-1] * (x - X))
    u = x + X
    i, r = divmod(u, S)
    tot = Fraction(0)
    for j in range(i):
        tot += Fraction(S * (f[j] + f[j + 1]), 2)
    tot += f[i] * r + Fraction((f[i + 1] - f[i]) * r * r, 2 * S)
    return 2 * S * tot


def curve_at(f, half, sh, x):
    """the interpolated curve at a rational x (flat beyond the ends)"""
    X, S = half << sh, 1 << sh
    if x <= -X:
        return Fraction(f[0])
    if x >= X:
        return Fraction(f[-1])
    u = Fraction(x) + X
    i = int(u // S)
    r = u - i * S
    return f[i] + (f[i + 1] - f[i]) * r / S


# ---- the generator

def test_generator_is_deterministic_and_committed():
    t = gdt.text()
    assert t == gdt.text()
    assert open(gdt.OUT).read() == t, "firmware/gen/drive_tables.h is not what tools/gen_drive_tables.py writes"


def test_tanh_is_the_classic_drives_curve():
    assert gdt.fx_tanh() == TANH
    h = gdt.integral(TANH)
    assert min(h) == 0 and max(h) < 2 ** 31
    d = [h[i + 1] - h[i] for i in range(256)]
    assert d == [TANH[i] + TANH[i + 1] for i in range(256)]


def test_generator_rom_window_and_rows():
    with pytest.raises(AssertionError):
        gdt.arr("X", "int32_t", [-4194304])                       # 0xFFC00000: never a constant
    with pytest.raises(AssertionError):
        gdt.arr("X", "int32_t", [-3145729])                       # 0xFFCFFFFF
    assert "{1,2,3,4,5}" in gdt.rows("R", "int32_t", [[1, 2, 3, 4, 5]])
    c = gdt.curve("T", lambda x: math.tanh(x / 4096), 8, 9)
    assert "T_F[17]" in c and "T_H[17]" in c


def test_bq_q28_and_tpt_g30():
    """an analog 2-pole low-pass at 1 kHz (Q 0.707), bilinear, prewarped: -3.01 dB at 1 kHz, 0 dB at DC"""
    w = 2 * math.pi * 1000
    c = gdt.bq_q28([0, 0, w * w], [1, w / 0.7071, w * w], 1000)
    b, a = np.array(c[:3]) / 2 ** 28, np.array([1, -c[3] / 2 ** 28, -c[4] / 2 ** 28])
    for hz, want in ((1e-3, 0.0), (1000, -3.01)):
        z = np.exp(-2j * np.pi * hz / FS * np.arange(3))
        assert 20 * math.log10(abs((b @ z) / (a @ z))) == pytest.approx(want, abs=0.02)
    g = gdt.tpt_g30(1000) / 2 ** 30
    assert g == pytest.approx(math.tan(math.pi * 1000 / FS) / (1 + math.tan(math.pi * 1000 / FS)), rel=1e-8)


# ---- the curve and its integral

def test_curve_is_softclip(exe):
    """drv_curve on FX_TANH, half 128, sh 10 = the classic drive's softclip, bit for bit, below its top (softclip
    clamps to 131071, one step short of the table's last point: 32744 there, drv_curve's flat end 32745)"""
    x = np.concatenate([np.arange(-140000, 131072, 37), [-131072, -131073, 131071, 0, 1, -1]])
    assert np.array_equal(run(exe, x, "tanh", 128, 10, "curve"), run(exe, x, "tanh", 128, 10, "softclip"))
    top = run(exe, np.array([131072, 140000]), "tanh", 128, 10, "curve")
    assert list(top) == [TANH[256]] * 2 and TANH[256] - run(exe, np.array([131072]), "tanh", 128, 10, "softclip")[0] == 1


@pytest.mark.parametrize("curve", ["tanh", "asym"])
def test_integral_is_exact(exe, curve):
    rng = np.random.default_rng(7)
    if curve == "tanh":
        f, half, sh, path = TANH, 128, 10, "tanh"
    else:                                                    # an asymmetric curve, a coarse table, a finer step
        half, sh = 24, 7
        f = [int(round(32767 * (math.tanh(1.3 * v) if v > 0 else 0.6 * math.tanh(3 * v))))
             for v in np.linspace(-2, 2, 2 * half + 1)]
        path = write_curve(f, half)
    X = half << sh
    h = gdt.integral(f)
    x = np.concatenate([rng.integers(-X - 3000, X + 3000, 300), [-X, -X - 1, X, X - 1, X + 1, 0, 1, -1]])
    got = run(exe, x, path, half, sh, "p", out64=True)
    base = (h[0] << (2 * sh))                                # (h[0] is the constant: P(-X))
    for xi, g in zip(x, got):
        want = exact_P(f, half, sh, int(xi))
        assert want.denominator == 1 and g - base == want, (int(xi), g - base, want)


# ---- ADAA-1

def exact_mean(f, half, sh, a, b):
    if a == b:
        return curve_at(f, half, sh, a)
    return (exact_P(f, half, sh, b) - exact_P(f, half, sh, a)) / (2 * (1 << sh) * (b - a))


@pytest.mark.parametrize("curve,eps", [("tanh", 0), ("tanh", 64), ("asym", 0)])
def test_adaa_is_the_exact_mean(exe, curve, eps):
    """every output within 1 LSB of the exact mean of the curve over the step (|dx| > eps), or of the curve at the
    step's midpoint (|dx| <= eps): small steps, segment crossings, beyond the ends; within 2 LSB for steps of 2^16 or
    more (the 32-bit reduction: both sides shifted until the step fits 16 bits)"""
    rng = np.random.default_rng(11)
    if curve == "tanh":
        f, half, sh, path = TANH, 128, 10, "tanh"
    else:
        half, sh = 24, 7
        f = [int(round(32767 * (math.tanh(1.3 * v) if v > 0 else 0.6 * math.tanh(3 * v))))
             for v in np.linspace(-2, 2, 2 * half + 1)]
        path = write_curve(f, half)
    X = half << sh
    parts = [rng.integers(-X, X, 200),                                        # big jumps
             np.cumsum(rng.integers(-40, 41, 300)),                           # a slow walk (tiny steps, dx 0 too)
             np.cumsum(rng.integers(-900, 901, 300)),                         # segment crossings
             rng.integers(-4 * X, 4 * X, 100),                                # beyond the ends
             np.array([5, 5, 5, X + 900000, -X - 900000, 0])]
    x = np.concatenate(parts).astype(np.int64)
    y = run(exe, x, path, half, sh, "adaa", eps)
    prev, bad = 0, []
    for xi, yi in zip(x, y):
        xi = int(xi)
        dx = xi - prev
        want = exact_mean(f, half, sh, prev, xi) if abs(dx) > eps else curve_at(f, half, sh, xi - (dx >> 1))
        if abs(yi - want) > (1 if abs(dx) < 65536 else 2):      # (a step of 2^16 or more: both reduced, 2 LSB)
            bad.append((prev, xi, int(yi), float(want)))
        prev = xi
    assert not bad, bad[:5]


def test_adaa_no_staircase_on_a_slow_ramp(exe):
    """a ramp of one unit a sample through several segments: the output rises smoothly (a staircase of the segments
    would hold for 1024 samples); and it sits on the curve, half a step back"""
    x = np.arange(-5000, 5000)
    y = run(exe, x, "tanh", 128, 10, "adaa", 0)
    c = run(exe, x - 1, "tanh", 128, 10, "curve")             # (the curve one sample back: the mean of a unit step
    assert np.max(np.abs(y[1:] - c[1:])) <= 1                 #  lies between it and the curve now)
    assert np.all(np.diff(y[1:]) >= 0) and np.max(np.diff(y[1:])) <= 2


def offgrid_db(y, q, n):
    Y = np.abs(np.fft.rfft(y[-n:].astype(float))) ** 2
    b = np.arange(len(Y))
    f = b * FS / n
    grid = (b % q) == 0
    band = (f >= 20) & (f <= 20000)
    return 10 * math.log10(Y[band & ~grid].sum() / Y[grid & (b > 0)].sum())


def float_adaa(x, f, half, sh):
    """ADAA-1 of the interpolated curve in float (the same exact integral): the reference for the integer one"""
    f, S, X = np.asarray(f, float), 1 << sh, half << sh
    cum = np.concatenate([[0.0], np.cumsum((f[:-1] + f[1:]) / 2 * S)])

    def F(v):
        u = np.clip(v + X, 0, 2 * X)
        i = np.minimum(np.floor(u / S).astype(int), 2 * half - 1)
        r = u - i * S
        return (cum[i] + f[i] * r + (f[i + 1] - f[i]) * r * r / (2 * S) + np.where(v > X, (v - X) * f[-1], 0)
                + np.where(v < -X, (v + X) * f[0], 0))
    xf = np.asarray(x, float)
    xp = np.concatenate([[0.0], xf[:-1]])
    d = xf - xp
    u = np.clip(xf + X, 0, 2 * X - 1e-9)
    i = np.floor(u / S).astype(int)
    plain = f[i] + (f[i + 1] - f[i]) * (u / S - i)
    return np.where(d != 0, (F(xf) - F(xp)) / np.where(d == 0, 1, d), plain)


def test_adaa_folds_less_than_the_plain_curve(exe):
    """tanh driven to x3 by a bin-exact sine at 2.1 / 4.2 / 6.3 kHz: the energy folded off the harmonic grid at least
    6 dB under the plain curve's (ADAA-1 on tanh: 7 / 9 / 11 dB here), and the same as ADAA-1 in float within 0.2 dB
    (the integers add nothing)"""
    n = 32768
    for hz in (2100, 4200, 6300):
        k = int(round(hz * n / FS)) | 1
        x = np.round(3 * 32768 * np.sin(2 * np.pi * k * np.arange(n + 4096) / n)).astype(np.int64)
        a = offgrid_db(run(exe, x, "tanh", 128, 10, "adaa", 0), k, n)
        p = offgrid_db(run(exe, x, "tanh", 128, 10, "curve"), k, n)
        fl = offgrid_db(float_adaa(x, TANH, 128, 10), k, n)
        assert a < p - 6 and abs(a - fl) < 0.2, (hz, a, p, fl)


# ---- the biquad and the one-pole

def test_biquad_matches_float(exe):
    w = 2 * math.pi * 300
    c = gdt.bq_q28([1, 0, 0], [1, w / 0.7071, w * w], 300)       # a high-pass, 300 Hz
    rng = np.random.default_rng(3)
    x = rng.integers(-1 << 20, 1 << 20, 4000)
    y = run(exe, x, "tanh", 128, 10, "bq", *c)
    b, a = np.array(c[:3]) / 2 ** 28, [-c[3] / 2 ** 28, -c[4] / 2 ** 28]
    r, x1, x2, y1, y2 = [], 0, 0, 0, 0
    for xi in x.astype(float):
        yi = b[0] * xi + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2
        r.append(yi); x2, x1, y2, y1 = x1, xi, y1, yi
    assert np.max(np.abs(y - np.array(r))) < 2 ** 20 * 1e-4            # (the rounding, through the poles)


def test_tpt_onepole_matches_float(exe):
    G = gdt.tpt_g30(800)
    rng = np.random.default_rng(5)
    x = rng.integers(-1 << 20, 1 << 20, 4000)
    y = run(exe, x, "tanh", 128, 10, "tpt", G)
    g, s, r = G / 2 ** 30, 0.0, []
    for xi in x.astype(float):
        v = (xi - s) * g
        r.append(v + s); s = v + s + v
    assert np.max(np.abs(y - np.array(r))) < 8
