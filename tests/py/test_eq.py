# SPDX-License-Identifier: GPL-3.0-only
"""The EQ before the drive (firmware/src/fx_eq.c; COMP -> EQ -> DRIVE): LOW shelf 120 Hz, MID peak (Q 0.9, MID FREQ
200 Hz .. 5 kHz), HIGH shelf 5 kHz, -15 .. +15 dB (knob 64 = 0 dB). On fx.c as the firmware has it (tests/c/eq_test.c)
and on the host build (the records):

- flat (every gain 64) is the input bit for bit, whatever MID FREQ; a band that glides back to 0 dB rests again and
  the output is the input bit for bit;
- the response is the float design's (RBJ biquads) within 0.05 dB, 20 Hz .. 20 kHz;
- a full-speed knob sweep under a sine adds no zipper (the energy off the sine and its harmonics);
- no click when a band turns on or off under a sound; a tail goes to exact 0 and stays there (no limit cycle, no
  idle tone, no DC from silence);
- the cost (fx_cost_us): 0 flat, a band's while it runs;
- the records: an old preset (np < 67) and an old settings record (flags2 bit 2 clear, garbage in the bytes the EQ
  took) load the EQ flat; a saved EQ comes back after a power cycle."""
import os
import struct
import subprocess

import numpy as np
import pytest

from hostrun import FS, ROOT, cc, once, run

EXE = os.path.join(ROOT, "build", "host", "eq_test")
FL, FM, FF, FH = 63, 64, 65, 66                            # params.c P_EQ_LO .. P_EQ_HI
CCS = {FL: 34, FM: 35, FF: 36, FH: 37}


@pytest.fixture(scope="module")
def exe():
    """built once, into a temporary name and renamed into place (hostrun.once): a CI run once ran it half-written
    ("Permission denied", 2026-11-05)"""
    return once(EXE, lambda exe: subprocess.run(
        ["cc", "-O2", "-Wall", "-Wno-unused-function", "-fsanitize=undefined,address", "-fno-sanitize-recover=all",
         "-DDINGHY_HOST", "-Ifirmware/src", "-Ifirmware/gen", "tests/c/eq_test.c", "-o", exe], cwd=ROOT, check=True))


def eq(exe, x, lo=64, mid=64, fr=64, hi=64, mode="run", sched=None):
    env = dict(os.environ, EQ_SCHED=sched or "")
    r = subprocess.run([exe, mode, str(lo), str(mid), str(fr), str(hi)], input=np.asarray(x, "<i4").tobytes(),
                       capture_output=True, check=True, env=env)
    info = r.stderr.decode().split("\n")
    return np.frombuffer(r.stdout, "<i4").astype(np.int64), info


def db(v):
    return max(-15.0, (v - 64) * 15 / 63)


def rbj(kind, f0, g):
    A, w = 10 ** (g / 40), 2 * np.pi * f0 / FS
    c, s = np.cos(w), np.sin(w)
    if kind == "peak":
        al = s / (2 * 0.9)
        b, a = [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
    else:
        sa = 2 * np.sqrt(A) * s / 2 * np.sqrt(2)
        sg = 1 if kind == "low" else -1
        b = [A * ((A + 1) - sg * (A - 1) * c + sa), sg * 2 * A * ((A - 1) - sg * (A + 1) * c), A * ((A + 1) - sg * (A - 1) * c - sa)]
        a = [(A + 1) + sg * (A - 1) * c + sa, -sg * 2 * ((A - 1) + sg * (A + 1) * c), (A + 1) + sg * (A - 1) * c - sa]
    return np.array(b) / a[0], np.array(a) / a[0]


def resp(ba, f):
    b, a = ba
    z = np.exp(-2j * np.pi * f / FS)
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def noise(n, amp=8000, seed=1):
    return np.round(np.random.default_rng(seed).normal(0, amp, n)).clip(-32767, 32767).astype(np.int64)


def test_every_knob_end_in_range(exe):
    """every knob at 0, 1, 127 (and gliding there): no table read past its end (ASan; the monkey's seed 0 found MID at
    0: its mirror, position 512, read FX_EQ_A[129]), and the response stays finite and in range"""
    x = noise(256 * 16, amp=2000)
    for v in (0, 1, 127):
        for fr in (0, 127):
            for mode in ("run", "glide"):
                y, _ = eq(exe, x, lo=v, mid=v, fr=fr, hi=v, mode=mode)
                assert len(y) == len(x) and np.abs(y).max() < 2 ** 20, (v, fr, mode)


def test_flat_is_the_input_bit_for_bit(exe):
    x = noise(256 * 64)
    for fr in (0, 64, 127):
        y, info = eq(exe, x, fr=fr)
        assert np.array_equal(y, x), fr
        assert "run 0 0 0 cost 0" in info[0], info[0]
    y, info = eq(exe, x, mode="glide", fr=127)             # (FREQ alone: MID at 0 dB, nothing moves)
    assert np.array_equal(y, x) and "cost 0" in info[0]


def test_response_is_the_designs(exe):
    n = 1 << 15
    x = np.zeros(n, np.int64)
    x[0] = 1 << 20
    f = np.fft.rfftfreq(n, 1 / FS)
    band = (f > 20) & (f < 20000)
    for lo, mid, fr, hi in ((127, 64, 64, 64), (0, 64, 64, 64), (64, 127, 0, 64), (64, 0, 127, 64), (64, 64, 64, 127),
                            (64, 64, 64, 0), (127, 127, 64, 127), (1, 1, 30, 1), (90, 40, 100, 20), (65, 63, 64, 64)):
        y, _ = eq(exe, x, lo, mid, fr, hi)
        hm = np.fft.rfft(y) / (1 << 20)
        hr = (resp(rbj("low", 120, db(lo)), f) * resp(rbj("peak", 200 * 25 ** (fr / 127), db(mid)), f) *
              resp(rbj("high", 5000, db(hi)), f))
        err = np.max(np.abs(20 * np.log10(np.abs(hm[band]) / np.abs(hr[band]))))
        assert err < 0.05, (lo, mid, fr, hi, err)


def zipper_db(y, k0, lo=3000, guard=300):
    """the energy above lo Hz, off the sine's harmonics (+-guard Hz), re all (dB), Blackman window: a slow gain or
    phase change spreads the sine by a few Hz only; a coefficient stepped once a block (172 Hz) or a sub-block
    (1378 Hz) puts sidebands at multiples of its rate all the way up"""
    w = np.blackman(len(y))
    p = np.abs(np.fft.rfft(y * w)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / FS)
    keep = f > lo
    for h in range(1, len(p) // k0 + 1):
        keep &= np.abs(f - f[h * k0]) > guard
    return 10 * np.log10(p[keep].sum() / p.sum() + 1e-30)


@pytest.mark.parametrize("name,sched", [("MID", "8:64,0,64,64;40:64,127,64,64;72:64,0,64,64"),
                                        ("LOW", "8:0,64,64,64;40:127,64,64,64;72:0,64,64,64"),
                                        ("HIGH", "8:64,64,64,0;40:64,64,64,127;72:64,64,64,0"),
                                        ("FREQ", "8:64,127,0,64;40:64,127,127,64;72:64,127,0,64")])
def test_full_speed_sweeps_do_not_zipper(exe, name, sched):
    """each knob swept end to end and back at MIDI speed (one step a block) under a sine: what the sweep adds off the
    sine's partials above 3 kHz stays under -95 dB, the rounding's floor (the float model stepping the coefficients
    once a block: -76 dB)"""
    n, k0 = 256 * 128, 512                              # (a sine of 64 samples, 689 Hz: its rounding only harmonics)
    x = np.round(12000 * np.sin(2 * np.pi * k0 * np.arange(n) / n)).astype(np.int64)
    steps = []
    for part in sched.split(";"):
        b, v = part.split(":")
        steps.append((int(b), [int(t) for t in v.split(",")]))
    s2 = []                                              # every block a step: the knob as fast as MIDI moves it
    for (b0, v0), (b1, v1) in zip(steps, steps[1:]):
        for k in range(b1 - b0):
            s2.append(f"{b0 + k}:" + ",".join(str(round(a + (c - a) * k / (b1 - b0))) for a, c in zip(v0, v1)))
    y, _ = eq(exe, x, mode="glide", sched=";".join(s2))
    ref, _ = eq(exe, x)
    assert zipper_db(y, k0) < -95, (name, zipper_db(y, k0))          # (measured -98.6 .. -106: the rounding floor,
    assert zipper_db(ref, k0) < -110                                 #  broadband, no line above -118 dB per bin)


def jumps(y):
    return float(np.max(np.abs(np.diff(y.astype(float), 2))))


@pytest.mark.parametrize("v", [(127, 64, 64, 64), (64, 0, 30, 64), (64, 64, 64, 127), (0, 127, 100, 0)])
def test_turning_a_band_on_and_off_does_not_click(exe, v):
    n = 256 * 96
    t = np.arange(n)
    x = np.round(6000 * np.sin(2 * np.pi * 220 * t / FS) + 3000 * np.sin(2 * np.pi * 3300 * t / FS)).astype(np.int64)
    on = ",".join(map(str, v))
    y, info = eq(exe, x, mode="glide", sched=f"10:64,64,64,64;20:{on};60:64,64,64,64")
    a, _ = eq(exe, x, *v)
    ref = max(jumps(x[2560:]), jumps(a[2560:]))
    assert jumps(y[2560:]) <= 1.05 * ref + 2, (v, jumps(y[2560:]), ref)
    assert np.array_equal(y[-256 * 8:], x[-256 * 8:]) and "run 0 0 0 cost 0" in info[0], info[0]   # (back: the input)


@pytest.mark.parametrize("v", [(127, 127, 64, 127), (0, 0, 0, 0), (100, 30, 127, 90)])
def test_tails_end_in_exact_zero(exe, v):
    """noise, then silence: the output reaches exact 0 within 50 ms of the input and stays there (the error feedback
    leaves no limit cycle); silence in is silence out (no DC)"""
    x = np.concatenate([noise(256 * 16, 12000), np.zeros(256 * 200, np.int64)])
    y, _ = eq(exe, x, *v)
    z = 256 * 16 + int(0.05 * FS)
    assert not y[z:].any(), (v, np.flatnonzero(y[256 * 16:])[-1:])
    y0, _ = eq(exe, np.zeros(256 * 32, np.int64), *v)
    assert not y0.any()


def test_cost_counts_the_bands_that_run(exe):
    x = noise(256 * 8)
    assert "cost 0" in eq(exe, x)[1][0]
    assert "cost 34" in eq(exe, x, lo=90)[1][0]
    assert "cost 102" in eq(exe, x, 90, 30, 64, 100)[1][0]


# ---- the records (the host build: its flash in a file, tests/py/test_persistence.py's helpers)

def test_old_records_load_the_eq_flat():
    import test_persistence as tp
    fl = tp.flash_file("eq-old")
    img = bytearray(b"\xff" * tp.NSEC * tp.SECTOR)
    r = tp.v1_record({0: 1, 1: 20}, preset=tp.NO_PRESET)
    r[tp.O_VER], r[tp.O_FLAGS] = 2, 255
    r[234] = 1                                           # flags2: the 19-factory index; bit 2 (the EQ) clear
    r[189:221] = b"\x07" * 32                            # garbage in seq_rsv's old bytes (now panel4 / undo4)
    recs = []
    for i in range(16):
        p = bytearray(tp.defaults()[:63]) + b"\x00" * 17   # a v3 record before the EQ: np 63, the spare bytes 0
        recs.append(struct.pack("<BBBB", 3, 63, 1 if i == 0 else 0, 0) + (b"OLD V3      " if i == 0 else b" " * 12) + bytes(p))
    assert len(recs[0]) == 96
    tp.put_record(img, tp.SET, bytes(r) + b"".join(recs), 5)   # (0.9.6's record: the settings, the 16 presets)
    open(fl, "wb").write(img)
    b = tp.boot("eq-old", fl, ["150 midi 0xC0 16 0", "250 dump"], end=400, check_preset=False)   # (PC 16: U01)
    s100, s250 = b.state(100), b.state(250)
    for i in (FL, FM, FF, FH):
        assert s100[f"p.{i}"] == "64" and s250[f"p.{i}"] == "64", (i, s100[f"p.{i}"], s250[f"p.{i}"])
    assert s250["header"] == "U01 OLD V3" and not b.fails, (s250["header"], b.fails)


def test_a_saved_eq_comes_back():
    import test_persistence as tp
    fl = tp.flash_file("eq-save")
    lines = [cc(200, 34, 90), cc(210, 35, 30), cc(220, 36, 100), cc(230, 37, 20), "3000 btn 9", "3100 btn 9", "6000 dump"]
    a = run("eq-save-a", lines, 6500, flash=fl, check=False)
    assert a.state(6000)[f"p.{FL}"] == "90"
    b = tp.boot("eq-save-b", fl, ["250 dump"], end=400)
    s = b.state(250)
    assert [s[f"p.{i}"] for i in (FL, FM, FF, FH)] == ["90", "30", "100", "20"], s
    rec = tp.settings_record(tp.read_img(fl))
    assert rec[234] & 2 and rec[156] == 90 and list(rec[189:192]) == [30, 100, 20], (rec[234], rec[156], list(rec[189:192]))
    # (settings_t: id 63, LOW, is panel3's last byte, 141 + 15; ids 64.. panel4 from 189; flags2 at 234)
