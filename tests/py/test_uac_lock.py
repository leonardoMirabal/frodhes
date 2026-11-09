# SPDX-License-Identifier: GPL-3.0-only
"""The locked USB audio stream (usb.c FELUCCA_UAC_LOCK 1, Frodhes 0.9.9, iOS): every packet nominal, the FM-1's clock
drift taken out by a fine-ratio resampler (uac_tap_lock) that a slow loop on the ring's fill steers.

- The table (firmware/src/uac_fir_lock.h) is what tools/gen_uac_fir_lock.py makes.
- Quality: sines 20 Hz .. 19 kHz through the firmware's own resampler (tests/uac_lock_test.c) at the FM-1's clock
  -600, +451 (the FM-1 as measured) and +600 ppm off, at both host rates: the THD+N against a sine fitted at the output frequency (the error
  of the resampler, its images and the rounding) is within 1 dB of the 48 kHz path before the lock (uac_tap48 at its
  exact 160 / 147, tests/uac_fir_test.c), at every frequency.
- A ramp of the correction (-30 -> +30 ppm over 6 s, the step changed once a block): the error in 20 ms windows
  along the ramp stays at the static level (no zipper, no click).
- The stream (tests/uac_seq_test.c -DFELUCCA_UAC_LOCK=1): packets of exactly 44 / 45 frames (45 once in 10) or 48,
  no underrun, no overrun, at -540 .. +540 ppm for simulated minutes, the render up to 5.4 ms long; the loop's step
  then settles on the drift."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "uac_test")
FS = 44100.0
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")


def build(name, src, *defs):
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"{name}.{WORKER}")
    tmp = f"{exe}.{os.getpid()}"
    subprocess.run(["cc", "-O1", "-w", *defs, "-I" + os.path.join(ROOT, "firmware", "src"), "-o", tmp,
                    os.path.join(ROOT, "tests", src)], check=True)
    os.replace(tmp, exe)
    return exe


@pytest.fixture(scope="module")
def exes():
    return {"lock": build("uac_lock_test", "uac_lock_test.c"), "old": build("uac_fir_test_lk", "uac_fir_test.c")}


def pipe(cmd, x):
    txt = "\n".join(f"{a} {b}" for a, b in x) + "\n"
    p = subprocess.run(cmd, input=txt, capture_output=True, text=True, check=True)
    rows = [ln for ln in p.stdout.splitlines() if ln and ln[0] != "#"]
    s = [ln for ln in p.stdout.splitlines() if ln.startswith("# S ")]
    return np.array([[int(v) for v in ln.split()] for ln in rows], dtype=np.int64), (int(s[0].split()[2]) if s else 0)


def sine(f, secs=0.5, amp=0.5, ph=0.3):
    n = int(FS * secs)
    v = np.round(amp * 32767 * np.sin(2 * np.pi * f / FS * np.arange(n) + ph)).astype(np.int64)
    return np.stack([v, -v], 1)


def thdn(y, fn, skip=400):
    """dB of the residual after a least-squares DC + sine fit at fn cycles an output frame (both channels)"""
    out = []
    for ch in range(2):
        v = y[skip:-50, ch].astype(float)
        n = np.arange(len(v))
        A = np.stack([np.ones_like(v), np.sin(2 * np.pi * fn * n), np.cos(2 * np.pi * fn * n)], 1)
        c, *_ = np.linalg.lstsq(A, v, rcond=None)
        r = v - A @ c
        out.append(10 * np.log10((r ** 2).mean() / ((A[:, 1:] @ c[1:]) ** 2).mean()))
    return max(out)


FREQS = [20, 61, 200, 523, 1000, 2000, 3336, 5565, 7188, 9283, 11990, 15485, 19000]


@pytest.fixture(scope="module")
def baseline(exes):
    """the 48 kHz path before the lock (uac_tap48 at exactly 160 / 147)"""
    res = {}
    for f in FREQS:
        y, _ = pipe([exes["old"], "-"], sine(f))
        res[f] = thdn(y, f / 48000.0)
    return res


def test_table_current():
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_uac_fir_lock.py"), "--check"]).returncode == 0


def test_packed_table_unpacks_exactly(exes):
    """usb.c uac_fl_unpack rebuilds the generator's table in RAM, every int16 (the header holds it packed: 7716 B)"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gen_uac_fir_lock
    got = np.array([[int(x) for x in ln.split()] for ln in
                    subprocess.run([exes["lock"], "table"], capture_output=True, text=True, check=True).stdout.splitlines()])
    assert got.shape == (1280, 12) and (got == gen_uac_fir_lock.table()).all()


@pytest.mark.parametrize("rate,ppm", [(48, -600), (48, 451), (48, 600), (44, -600), (44, 451), (44, 600)])
def test_quality_vs_48k_path(exes, baseline, rate, ppm):
    worst = []
    for f in FREQS:
        y, S = pipe([exes["lock"], str(rate), str(ppm)], sine(f))
        e = thdn(y, f / FS * S / (2560 * 65536))
        worst.append((e - baseline[f], f, e, baseline[f]))
    print(rate, ppm, " ".join(f"{f}:{e:.1f}/{b:.1f}" for _, f, e, b in worst))
    d, f, e, b = max(worst)
    assert d <= 1.0, f"{f} Hz: {e:.1f} dB against {b:.1f} dB before the lock"


def windows(y, f, rate, nb, p0, p1, win):
    """the worst error in windows of `win` outputs, each at its own output frequency (the step from its block)"""
    s0 = (147 * 16 * 65536) if rate == 48 else 2560 * 65536
    per_block = (256 * 2560 * 65536) / s0
    worst = -999.0
    for k in range(2, len(y) // win - 1):
        b = min(nb - 1, int((k + 0.5) * win / per_block))
        ppm = p0 + (p1 - p0) * b / (nb - 1)
        worst = max(worst, thdn(y[k * win:(k + 1) * win], f / FS * s0 * (1 + ppm * 1e-6) / (2560 * 65536), skip=0))
    return worst


@pytest.mark.parametrize("rate", [48, 44])
def test_ramp_no_zipper(exes, rate):
    """the correction ramped -30 -> +30 ppm over 6 s, the step changed every block (10 ppm/s: the loop itself moves
    it far slower; the ramp sweeps every fraction of a phase many times): the error in 20 ms windows is no worse
    than the worst with the step held at -30, +30 or +451 ppm (+1 dB); a zipper (the step's change heard) or a click would stand out"""
    for f in (1000, 7188):
        x = sine(f, secs=6.0)
        nb, win = len(x) // 256, 960 if rate == 48 else 882
        ramp, _ = pipe([exes["lock"], str(rate), "-30", "30"], x)
        wr, wh = windows(ramp, f, rate, nb, -30, 30, win), -999.0
        for ppm in (-30, 30, 451):  # held steps: the error depends on the fraction the step sweeps, so the worst
            held, _ = pipe([exes["lock"], str(rate), str(ppm)], x)
            wh = max(wh, windows(held, f, rate, nb, ppm, ppm, win))
        print(rate, f, "ramp", round(wr, 1), "held", round(wh, 1))
        assert wr <= wh + 1.0


@pytest.mark.parametrize("r48", [False, True])
def test_stream_nominal_and_in_band(r48):
    exe = build("uac_seq_lock" + ("48" if r48 else ""), "uac_seq_test.c", "-DFELUCCA_UAC_PHASE=1", "-DFELUCCA_UAC_LOCK=1")
    p = subprocess.run([exe, "--hours", os.environ.get("UAC_LOCK_HOURS", "0.05"), "--clean"] + (["--r48"] if r48 else []),
                       capture_output=True, text=True)
    rows = [dict(zip(ln.split()[1::2], map(float, ln.split()[2::2]))) for ln in p.stdout.splitlines() if ln.startswith("case ")]
    assert rows and p.returncode == 0, p.stdout[-2000:]
    for c in rows:
        assert c["n_bad"] == 0 and c["nmin"] == (48 if r48 else 44) and c["nmax"] == (48 if r48 else 45), c
        assert c["underruns"] == 0 and c["overruns"] == 0 and c["true_missed"] == 0, c


@pytest.mark.parametrize("r48", [False, True])
def test_stream_exact_zero_for_zero_input(r48):
    """the closed loop (packets, host skips and bursts, nested polls, the prime) with a silent render: every frame the
    host takes is exact zero (T1 S3 counts any non-zero sample after the release)"""
    exe = build("uac_seq_lock" + ("48" if r48 else ""), "uac_seq_test.c", "-DFELUCCA_UAC_PHASE=1", "-DFELUCCA_UAC_LOCK=1")
    p = subprocess.run([exe, "--hours", "0.01", "--zero"] + (["--r48"] if r48 else []), capture_output=True, text=True)
    rows = [dict(zip(ln.split()[1::2], map(float, ln.split()[2::2]))) for ln in p.stdout.splitlines() if ln.startswith("case ")]
    assert rows
    for c in rows:
        assert c["nonzero"] == 0, c


@pytest.mark.parametrize("r48", [False, True])
def test_start_no_transient(r48):
    """after a start the step stays near the seed (the FM-1's measured +450 ppm, here the clock at +451): within
    +-10 ppm, and the stream's time against the FM-1's stays on a line within 0.25 sample over the first 14 s (the
    jack and USB compared sample for sample, T1 S1's tracker: rc-0.9.9f's start went to the clamp, -977 then +730
    ppm, and the two drifted ~3 samples apart off any line)"""
    exe = build("uac_seq_lock" + ("48" if r48 else ""), "uac_seq_test.c", "-DFELUCCA_UAC_PHASE=1", "-DFELUCCA_UAC_LOCK=1")
    p = subprocess.run([exe, "--hours", "0.004", "--clean", "--trace"] + (["--r48"] if r48 else []),
                       capture_output=True, text=True)
    t, u = [], []
    for ln in p.stdout.splitlines():
        if not ln.startswith("trace "):
            continue
        f = ln.split()
        if t and float(f[1]) < t[-1]:
            break                                       # (the first case only: ppm 451, renders of 500 us)
        t.append(float(f[1])); u.append(float(f[-1]))
    t, u = np.array(t), np.array(u)
    m = (t > 0.3) & (t < 14.0)
    assert m.sum() > 1000
    print("u", u[m].min(), u[m].max())
    assert np.all(np.abs(u[m] - 451) <= 10.0), (u[m].min(), u[m].max())
    ph = np.cumsum(u * 1e-6 * np.diff(t, prepend=t[0])) * 44100.0
    a = np.vstack([t[m], np.ones(m.sum())]).T
    dev = ph[m] - a @ np.linalg.lstsq(a, ph[m], rcond=None)[0]
    print("line dev", np.abs(dev).max())
    assert np.abs(dev).max() <= 0.25


@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("ppm", [["451"], ["-977"], ["-977", "977", "6"]])
def test_tail_into_history_bit_exact(rate, ppm):
    """the firmware's tail (platform.c, locked) writes the block straight into the resampler's history and taps once
    (uac_tap_hist); a MASTER ramp's block still goes in 32-frame pieces (uac_tap). Both, and the two mixed, give the
    pieces' stream bit for bit: a chirp + noise, full-scale noise through the knee, silence; L and R in place"""
    exe = build("uac_rs_test", "uac_rs_test.c")
    rng = np.random.default_rng(3)
    t = np.arange(int(FS * 8)) / FS
    x = np.sin(2 * np.pi * (20 * t + 1200 * t * t)) * 24000 + rng.normal(0, 1, t.size) * 6000
    x[int(FS * 3):int(FS * 4)] = rng.choice([-32767, 32767], int(FS))
    x[int(FS * 6):int(FS * 6.5)] = 0
    x = np.clip(np.round(x), -32767, 32767).astype("<i2").tobytes()
    out = {}
    for mode in "012":
        p = subprocess.run([exe, str(rate), *ppm], input=x, capture_output=True,
                           env={**os.environ, "UAC_RS_BLOCK": mode})
        assert p.returncode == 0, (mode, p.returncode, p.stderr.decode()[-300:])
        out[mode] = p.stdout
    assert len(out["0"]) > rate * 7 * 2
    assert out["1"] == out["0"] and out["2"] == out["0"]
