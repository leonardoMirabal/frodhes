# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE TYPEs' kernels in the firmware (firmware/src/drv_tsh8.c, drv_bhlue.c, drv_ohcd.c) against their reference loops
(tests/c/drv_kern_ref.c: the 1x integer kernels as designed, whole 256-sample blocks), and their control side:

- bit for bit: the firmware's kernel (256-sample blocks, as drive.c runs it, in place) = the reference, on noise and on a
  synthetic phrase, at the circuit's knob grid (min / noon / max of each knob and a few in between), with the rows of
  tools/drive_gen/<type>.py at those knob positions;
- the rows and tables the firmware runs = what the generator says (the diode table TSH8 builds from its 16 keys; the
  level folded into the last biquad);
- knobs: AMOUNT and TONE swept or jumped while a chord plays (tools/drive_scan.py, fx_drive as fx_process calls it):
  no step larger than the TYPE's own sound at fixed settings (the second difference)."""
import ctypes
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "drive_gen"))
import bhlue as BG  # noqa: E402
import drive_scan as ds  # noqa: E402
import ohcd as OG  # noqa: E402
import tsh8 as TG  # noqa: E402

FS = 44100
I32 = np.ctypeslib.ndpointer(np.int32, flags="C")
I16 = np.ctypeslib.ndpointer(np.int16, flags="C")


@pytest.fixture(scope="module")
def lib():
    out = os.path.join(ROOT, "build", "drive_kernels")
    os.makedirs(out, exist_ok=True)
    so = os.path.join(out, f"kern-{os.getpid()}.so")
    subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-Wall", "-Wno-unused-function",
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                    "-o", so, os.path.join(ROOT, "tests", "c", "drv_kern_test.c"),
                    os.path.join(ROOT, "tests", "c", "drv_kern_ref.c")], check=True)
    lb = ctypes.CDLL(so)
    lb.t_tsh8_kernel.argtypes = [I32, I32, I32, I32, ctypes.c_uint32]
    lb.t_tsh8_build.argtypes = [ctypes.c_int32, I32]
    lb.t_tsh8_build_gf.argtypes = [ctypes.c_int32, I32]
    lb.t_tsh8_run.argtypes = [ctypes.c_int32, ctypes.c_int32, I32, ctypes.c_uint32]
    lb.t_tsh8_run.restype = ctypes.c_uint32
    lb.t_power_on.argtypes = [ctypes.c_int32]
    lb.t_aa_rcp.restype = ctypes.c_void_p
    lb.aa = lb.t_aa_on()                                  # bit 1: TSH8 anti-aliased, bit 0: BHLUE (the header's)
    if lb.aa & 2:
        lb.t_tsh8_kernel_aa.argtypes = [I32, I32, I32, I32, I32, ctypes.c_uint32]
    lb.ref_tsh8_aa.argtypes = [I32, I32, I32, I32, ctypes.c_void_p, I32, I32, ctypes.c_uint32, ctypes.c_int]
    lb.t_tsh8_rows.argtypes = [ctypes.c_int32, ctypes.c_int32, I32, I32]
    lb.ref_tsh8.argtypes = [I32, I32, I32, I32, I32, I32, ctypes.c_uint32, ctypes.c_int]
    lb.t_bhlue_kernel.argtypes = [I32, I32, ctypes.c_uint32]
    lb.t_bhlue_rows.argtypes = [ctypes.c_int32, ctypes.c_int32, I32]
    lb.ref_bhlue.argtypes = [I32, I32, I32, I32, ctypes.c_uint32]
    lb.t_ohcd_kernel.argtypes = [I32, I32, ctypes.c_uint32]
    lb.t_ohcd_rows.argtypes = [ctypes.c_int32, ctypes.c_int32, I32]
    lb.t_ohcd_rcp.restype = ctypes.c_void_p
    lb.ref_ohcd.argtypes = [I32, I32, I32, I16, I32, ctypes.c_void_p, I32, ctypes.c_uint32]
    lb.ref_ohcd_rail.argtypes = [I32, I32, ctypes.c_uint32, ctypes.c_void_p]
    lb.t_ohcd_rail.argtypes = [I32, I32, ctypes.c_uint32]
    if lb.aa & 1:
        lb.t_bhlue_sh.restype = ctypes.c_void_p
        lb.t_bhlue_gf.restype = ctypes.c_void_p
    lb.ref_bhlue_aa.argtypes = [I32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, I32, ctypes.c_uint32]
    yield lb
    os.remove(so)


def a32(v):
    return np.ascontiguousarray(np.asarray(v, dtype=np.int64).astype(np.int32))


def signals():
    """noise at 0.4 of full scale (Q15) and a phrase: a bass line under chords, plucked envelopes, peaks near 0.6"""
    rng = np.random.default_rng(1)
    noise = np.clip(rng.standard_normal(FS * 2) * 0.4, -1, 1) * 32768
    t = np.arange(FS * 3) / FS
    ph = np.zeros_like(t)
    for k, (f0, at) in enumerate([(82.4, 0.0), (110.0, 0.75), (98.0, 1.5), (123.5, 2.25)]):
        env = np.where(t >= at, np.exp(-(t - at) * 3.0), 0.0)
        ph += 0.25 * env * np.sin(2 * np.pi * f0 * (t - at))
        for h, f in enumerate((261.6, 329.6, 392.0, 523.3 * (1 + k / 8))):
            ph += 0.08 * env * np.sin(2 * np.pi * f * (t - at) + h)
    return [a32(np.round(noise)), a32(np.round(ph * 32768))]


TS_GRID = [(d, t) for d in (0.0, 0.5, 1.0) for t in (0.0, 0.5, 1.0)] + [(0.25, 0.75), (77 / 127, 40 / 127)]


@pytest.mark.parametrize("rd,rt", TS_GRID)
def test_tsh8_kernel_bit_exact(lib, rd, rt):
    hc, cb, ct = a32(TG.hp_row()), a32(TG.br_row(rd)), a32(TG.tone_row(rt))
    g = a32(TG.table(TG.drive_r(TG.drive_knob(rd))))
    for x in signals():
        ref = np.zeros_like(x)
        lib.ref_tsh8(hc, cb, ct, g, a32(x.astype(np.int64) * TG.IN_K), ref, len(x), TG.OSH)
        y = x.copy()
        lib.t_tsh8_reset()
        lib.t_tsh8_kernel(cb, ct, g, y, len(y))
        assert np.array_equal(y, ref), (rd, rt, int(np.count_nonzero(y != ref)))


def test_tsh8_header_hp_is_the_generators():
    """(the kernel reads TSH8_HP from the header: the same two words as tools/drive_gen/tsh8.py)"""
    h = open(os.path.join(ROOT, "firmware", "gen", "drive_tables.h")).read()
    words = h.split("TSH8_HP[2] = {")[1].split("}")[0]
    assert [int(v) for v in words.replace("\n", "").split(",")] == TG.hp_row()


def test_tsh8_tables_and_rows(lib):
    """the diode table the firmware builds at every AMOUNT step (8 keys) = the generator's, within 5 LSB of the exact
    one; the rows it runs (64 per knob, interpolated) = the generator's"""
    K, kp = TG.keys(), TG.kpos()
    out = np.zeros(TG.NODES, np.int32)
    for a in range(128):
        lib.t_tsh8_build(a, out)
        assert np.array_equal(out, TG.built_table(K, *kp[a])), a
        ex = TG.table(TG.drive_r(TG.drive_knob(TG.amount_rot(a))))
        assert np.abs(out - ex).max() <= 5, a
    br, tone = np.zeros(5, np.int32), np.zeros(5, np.int32)
    for a, t in ((1, 0), (2, 1), (64, 64), (127, 127), (30, 100), (77, 126), (126, 3)):
        lib.t_tsh8_rows(a, t, br, tone)
        wb, wt = TG.rows_at(a, t)
        assert list(br) == wb and list(tone) == wt, (a, t)


@pytest.mark.parametrize("rd,rt", TS_GRID)
def test_tsh8_aa_kernel_bit_exact(lib, rd, rt):
    if not lib.aa & 2:
        pytest.skip("TSH8's anti-aliased kernel is off (tools/drive_gen/tsh8.py AA)")
    """the anti-aliased kernel = the design's loops (tests/c/drv_kern_ref.c), with the design's own rows at the grid"""
    import aa
    ci, cb, ct = a32(TG.in_pq_row()), a32(TG.br_row(rd)), a32(TG.tone_frac_q(TG.tone_knob(rt)))
    gf = a32(aa.gf(TG.table(TG.drive_r(TG.drive_knob(rd))), TG.node_w(), TG.AA_SF))
    for x in signals():
        ref = np.zeros_like(x)
        lib.ref_tsh8_aa(ci, cb, ct, gf, lib.t_aa_rcp(), a32(x.astype(np.int64) * TG.IN_K), ref, len(x), TG.OSH)
        y = x.copy()
        lib.t_tsh8_reset()
        lib.t_tsh8_kernel_aa(ci, cb, ct, gf, y, len(y))
        assert np.array_equal(y, ref), (rd, rt, int(np.count_nonzero(y != ref)))


@pytest.mark.parametrize("a,t", [(1, 64), (64, 64), (127, 127), (100, 20)])
def test_tsh8_aa_shipped_tables_bit_exact(lib, a, t):
    if not lib.aa & 2:
        pytest.skip("TSH8's anti-aliased kernel is off (tools/drive_gen/tsh8.py AA)")
    """with the rows and the {g, F} table the firmware runs at (a, t): the anti-aliased kernel = the design's loops"""
    br, tone = np.zeros(5, np.int32), np.zeros(5, np.int32)
    lib.t_tsh8_rows(a, t, br, tone)
    gf = np.zeros(2 * TG.NODES, np.int32)
    lib.t_tsh8_build_gf(a, gf)
    ci = a32(TG.in_pq_row())
    for x in signals():
        ref = np.zeros_like(x)
        lib.ref_tsh8_aa(ci, br, tone, gf, lib.t_aa_rcp(), a32(x.astype(np.int64) * TG.IN_K), ref, len(x), TG.OSH)
        y = x.copy()
        lib.t_tsh8_reset()
        lib.t_tsh8_kernel_aa(ci, br, tone, gf, y, len(y))
        assert np.array_equal(y, ref)


def test_tsh8_aa_table_build(lib):
    """(the anti-aliased variant, not in the kernel yet) the {g, F} table the firmware builds, in slices, at every
    AMOUNT step = the generator's (g the same as the plain table, F its exact antiderivative)"""
    import aa  # noqa: F401
    K, kp = TG.keys(), TG.kpos()
    out = np.zeros(2 * TG.NODES, np.int32)
    for a in range(0, 128, 9):
        lib.t_tsh8_build_gf(a, out)
        assert np.array_equal(out, TG.built_gf(K, *kp[a])), a


@pytest.mark.parametrize("a,t", [(1, 64), (64, 64), (127, 127), (100, 20)])
def test_tsh8_shipped_tables_bit_exact(lib, a, t):
    """with the rows and the diode table the firmware runs at (a, t): the kernel = the reference loops"""
    br, tone = np.zeros(5, np.int32), np.zeros(5, np.int32)
    lib.t_tsh8_rows(a, t, br, tone)
    g = np.zeros(TG.NODES, np.int32)
    lib.t_tsh8_build(a, g)
    for x in signals():
        ref = np.zeros_like(x)
        lib.ref_tsh8(a32(TG.hp_row()), br, tone, g, a32(x.astype(np.int64) * TG.IN_K), ref, len(x), TG.OSH)
        y = x.copy()
        lib.t_tsh8_reset()
        lib.t_tsh8_kernel(br, tone, g, y, len(y))
        assert np.array_equal(y, ref)


BH_GRID = [(d, t) for d in (0, 64, 127) for t in (0, 64, 127)] + [(30, 100)]


@pytest.mark.parametrize("sd,st", BH_GRID)
def test_bhlue_kernel_bit_exact(lib, sd, st):
    k = a32(BG.hp_row(sd) + BG.pre_row(sd) + BG.post_row(st))
    for x in signals() + [a32(np.round(np.sin(np.arange(4096) * 0.01) * 2 ** 19))]:   # (the last: past the clamp)
        ref = a32(np.clip(x, -2 ** 18, 2 ** 18))
        if lib.aa & 1:
            lib.ref_bhlue_aa(k, lib.t_bhlue_sh(), lib.t_bhlue_gf(), lib.t_aa_rcp(), ref, len(ref))
        else:
            lib.ref_bhlue(k, *(a32(z) for z in BG.clip_table()), ref, len(ref))
        y = x.copy()
        lib.t_bhlue_reset()
        lib.t_bhlue_kernel(k, y, len(y))
        assert np.array_equal(y, ref), (sd, st, int(np.count_nonzero(y != ref)))


@pytest.mark.parametrize("a0,a1", [(100, 100), (0, 127), (127, 1), (64, 30)])
def test_tsh8_first_table_at_boot(lib, a0, a1):
    """power-on: drv_boot builds TSH8's first table whole (the main loop); no audio block ever does: the first block
    after power-on builds nothing (AMOUNT as at boot) and every block, the AMOUNT moved from boot's, builds at most
    one slice of 32 nodes (as a steady block that follows a knob)"""
    lib.t_power_on(a0)
    assert lib.t_tsh8_ok()
    lib.t_tsh8_reset()
    m = np.zeros(256, np.int32)
    nb = [lib.t_tsh8_run(a1, 64, m, 256) for _ in range(400)]
    assert max(nb) <= 32, max(nb)
    if a0 == a1:
        assert nb[0] == 0 and max(nb) == 0
    else:
        assert sum(nb) > 0                                # (the table follows AMOUNT, a slice a block)


def test_bhlue_gf_built_in_ram(lib):
    """the anti-aliased clipper's {g, F} table, built in RAM from BHLUE_D (drv_bhlue.c bh_gf_build) = the generator's
    gf_table, word for word"""
    if not lib.aa & 1:
        pytest.skip("BHLUE plain (BHLUE_AA 0): no {g, F} table")
    want = BG.gf_table()
    got = np.ctypeslib.as_array(ctypes.cast(lib.t_bhlue_gf(), ctypes.POINTER(ctypes.c_int64)), (len(want),))
    assert [int(v) for v in got] == want


def test_bhlue_rows(lib):
    """the rows the firmware runs (64 per knob + extras, interpolated) = the generator's; every step's PRE / POST
    response within 0.1 dB (30 Hz - 10 kHz) of its own fit, as 128 rows had it"""
    k = np.zeros(12, np.int32)
    for a, t in ((1, 0), (2, 1), (64, 64), (127, 127), (30, 100), (126, 108), (4, 50)):
        lib.t_bhlue_rows(a, t, k)
        assert list(k) == BG.rows_at(a, t), (a, t)
    ra, pa, rt, pt = BG.grids()
    for a in range(1, 128):
        want = BG.hp_row(0, fit=BG.amount_fit(a)) + BG.pre_row(0, fit=BG.amount_fit(a))
        assert BG._err(BG._hpre(BG.at(ra, pa[a])), BG._hpre(want)) <= BG.BAD_DB, a
    for t in range(128):
        assert BG._err(BG._hpost(BG.at(rt, pt[t])), BG._hpost(BG.post_row(t))) <= BG.BAD_DB, t
    assert BG.amount_step(1) == 0 and BG.amount_step(127) == 127
    assert BG.amount_fit(126) == BG.fits()["pre"][126]                 # (127: the realization of 126, see amount_fit)


@pytest.mark.parametrize("a,t", [(1, 64), (64, 64), (127, 127), (100, 20)])
def test_bhlue_shipped_rows_bit_exact(lib, a, t):
    k = np.zeros(12, np.int32)
    lib.t_bhlue_rows(a, t, k)
    for x in signals():
        ref = a32(np.clip(x, -2 ** 18, 2 ** 18))
        if lib.aa & 1:
            lib.ref_bhlue_aa(k, lib.t_bhlue_sh(), lib.t_bhlue_gf(), lib.t_aa_rcp(), ref, len(ref))
        else:
            lib.ref_bhlue(k, *(a32(z) for z in BG.clip_table()), ref, len(ref))
        y = x.copy()
        lib.t_bhlue_reset()
        lib.t_bhlue_kernel(k, y, len(y))
        assert np.array_equal(y, ref)


OH_GRID = [(a, t) for a in (1, 64, 127) for t in (0, 64, 127)] + [(2, 1), (30, 100), (100, 20), (126, 126), (77, 33)]


def _rail_float(v, p, R=OG.RAIL2):
    """the exact ADAA-1 of a clamp at +-R (floats): (F(v) - F(p)) / (v - p), F(x) = x^2 / 2 inside, R |x| - R^2 / 2
    past it; the clamp at the midpoint when v = p"""
    def F(x):
        return 0.5 * x * x if abs(x) <= R else R * abs(x) - 0.5 * R * R
    if v == p:
        return max(-R, min(R, v))
    return (F(v) - F(p)) / (v - p)


def test_ohcd_rail_adaa(lib):
    """the IC1B rail (oh_rail) = the generator's integers (rail_ref) = the reference's, on ramps, steps and sines into
    and far past the rail (both sides, crossings both ways, a jump from one rail to the other), and within 2^-10 of
    the rail (-60 dB; the reciprocal table's 2^-11 and the excess in whole u) of the exact antiderivative quotient: the
    mean inside, the rail past it"""
    R = OG.RAIL2
    rng = np.random.default_rng(5)
    t = np.arange(20000)
    v = np.concatenate([np.round(np.sin(t * 0.01) * 1.6 * R), np.round(np.sin(t * 0.3) * 1.3 * R),
                        np.round(rng.uniform(-2.2 * R, 2.2 * R, 20000)), np.linspace(-1.5 * R, 1.5 * R, 4000).round(),
                        [R, R + 1, R - 1, -R, -R - 1, 3 * R, -3 * R, 0, R + 255, R + 256, R + 257, R]])
    v = a32(v)
    y, r = np.zeros_like(v), np.zeros_like(v)
    lib.t_ohcd_rail(v, y, len(v))
    lib.ref_ohcd_rail(v, r, len(v), lib.t_ohcd_rcp())
    assert np.array_equal(y, r)
    g = [OG.rail_ref(int(v[i]), int(v[i - 1]) if i else 0) for i in range(len(v))]
    assert list(y) == g
    ex = np.array([_rail_float(float(v[i]), float(v[i - 1]) if i else 0.0) for i in range(len(v))])
    assert np.abs(y - ex).max() <= R >> 10, float(np.abs(y - ex).max())
    assert np.abs(y).max() <= R
    inside = (np.abs(v[1:]) <= R) & (np.abs(v[:-1]) <= R)
    assert np.array_equal(y[1:][inside], (v[1:][inside].astype(np.int64) + v[:-1][inside]) >> 1)


@pytest.mark.parametrize("a,t", OH_GRID)
def test_ohcd_shipped_rows_bit_exact(lib, a, t):
    """OHCD: the rows the firmware runs at (a, t) = the generator's (tools/drive_gen/ohcd.py rows_at: the stored rows,
    a step between two their mean, stage 2 x Q of the AMOUNT's group, the level folded into the tone's b), and with
    them the kernel (two-sample filter loops on pointers, the clipper with ADAA-1 on its {g, F} table, the IC1B rail
    with ADAA-1) = the reference's loops (direct form I, one loop a stage, the clipper on its own curve / antiderivative
    arrays, 256-sample blocks), on noise, the phrase, loud sines past both rails and a near-DC ramp (the midpoint
    fallback)"""
    J = OG.design()
    k = np.zeros(15, np.int32)
    lib.t_ohcd_rows(a, t, k)
    w = OG.fw_words(a, t, J)
    assert list(k) == w, (a, t)
    s1, out, lv = OG.rows_at(a, t, J)
    cf = np.ascontiguousarray(np.asarray(J["cf"], np.int16))
    cff = a32([r[1] for r in OG.gf(J)])
    s2 = w[5:8] + [-w[8], -w[9]]
    o = w[10:13] + [-w[13], -w[14]]
    loud = a32(np.round(np.sin(np.arange(8192) * 0.03) * 32767))
    bass = a32(np.round(np.sin(np.arange(16384) * 2 * np.pi * 82.4 / FS) * 30000))
    slow = a32(np.round(np.sin(np.arange(8192) * 0.0005) * 3000))
    for x in signals() + [loud, bass, slow]:
        ref = x.copy()
        lib.ref_ohcd(a32(s1), a32(s2), a32(o), cf, cff, lib.t_ohcd_rcp(), ref, len(ref))
        y = x.copy()
        lib.t_ohcd_reset()
        lib.t_ohcd_kernel(k, y, len(y))
        assert np.array_equal(y, ref), (a, t, int(np.count_nonzero(y != ref)))
        assert np.abs(y).max() < 32767


def test_ohcd_full_range_rails(lib):
    """the full range (0.9.5): AMOUNT 127 is the DRIVE knob's end and AMOUNT <= 33 the earlier capped range (the same
    stage-1 rows); at the top a bass drives stage 2 into its rail (the rail's own branch runs)"""
    J = OG.design()
    assert J["rot"][126] == 1.0 and J["rot"][0] == 0.0
    assert all(abs(J["rot"][a - 1] - 0.3 * (a - 1) / 126) < 1e-15 for a in range(1, 34))
    assert all(r1 > r0 for r0, r1 in zip(J["rot"], J["rot"][1:]))
    bass = a32(np.round(np.sin(np.arange(16384) * 2 * np.pi * 82.4 / FS) * 20000))
    hit = {}
    for a in (1, 127):
        w = OG.fw_words(a, 64, J)
        s1, _, _ = OG.rows_at(a, 64, J)
        cf = np.ascontiguousarray(np.asarray(J["cf"], np.int16))
        cff = a32([r[1] for r in OG.gf(J)])
        # stage 2's output before the rail: the reference with the tone made a pass-through (b0 = 2^28 >> 4 x 16)
        s2 = w[5:8] + [-w[8], -w[9]]
        m = bass.copy()
        lib.ref_ohcd(a32(s1), a32(s2), a32([1 << 28, 0, 0, 0, 0]), cf, cff, lib.t_ohcd_rcp(), m, len(m))
        hit[a] = int(np.abs(m.astype(np.int64)).max())     # (the rail's output, x 2^8 >> 4: R >> 4 at the rail)
    assert hit[127] >= (OG.RAIL2 >> 4) - 2 and hit[1] < hit[127], hit


def test_ohcd_aa_tables():
    """the antiderivative is the private prototype's (OHR_CFF: 256 (g_k + g_k+1) summed); the shared reciprocal table
    is the one the header holds; the group map pairs every AMOUNT's P with its Q"""
    import aa
    J = OG.design()
    cf = J["cf"]
    F = [r[1] for r in OG.gf(J)]
    assert [r[0] for r in OG.gf(J)] == [2 * v for v in cf]
    assert F[:65] == [sum(256 * (cf[i] + cf[i + 1]) for i in range(k)) for k in range(65)]
    h = open(os.path.join(ROOT, "firmware", "gen", "drive_tables.h")).read()
    assert "DRV_AA_RCP[1024]" in h and aa.RB == 11
    assert J["sgrp"][0] == 0 and J["sgrp"][63] == 4 and J["sgrp"][126] == 8
    assert ("OHCD_AA %d" % OG.AA) in h and OG.AA


# ---- knobs while it plays (the firmware path: fx_drive, 256 a block, 32-sample sub-blocks)

@pytest.fixture(scope="module")
def exe():
    e = ds.build(tag="pytest-kernels")
    yield e
    os.remove(e)


def chord(n):
    t = np.arange(n) / FS
    x = 6000 * np.sin(2 * np.pi * 41.2 * t) + sum(4000 * np.sin(2 * np.pi * f * t + i) for i, f in
                                                    enumerate((261.63, 329.63, 392.0)))
    return np.round(x).astype(np.int64)


def d2(y, a, b):
    return float(np.max(np.abs(np.diff(y[a:b].astype(float), 2))))


def quiet_sine(n):
    return np.round(1000 * np.sin(2 * np.pi * 220 * np.arange(n) / FS)).astype(np.int64)


@pytest.mark.parametrize("sig,slack", [("chord", 40), ("quiet", 4)])
@pytest.mark.parametrize("typ", ["TSH8", "BHLUE", "OHCD"])
def test_knob_sweeps_do_not_click(exe, typ, sig, slack):
    """AMOUNT and TONE swept a step a block (as a turned knob sends), then jumped end to end, while a chord or a quiet
    sine (-30 dBFS: a step stands out of its smooth wave) plays: the largest second difference no more than the
    TYPE's own at fixed settings. (The steps glide, a few a block; TSH8's diode table follows in steps of at most
    16, each crossfaded in over 32 samples; BHLUE's last AMOUNT step keeps its neighbour's realization.)"""
    nb = 300
    x = chord(nb * 256) if sig == "chord" else quiet_sine(nb * 256)
    ref = 0.0
    for a in (1, 64, 127):
        for t in (0, 64, 127):
            y, _, _ = ds.render(exe, x[:40 * 256], ds.params_for(typ, a, t))
            ref = max(ref, d2(y, 12 * 256, 40 * 256))
    sw = []
    for b in range(127):                                     # AMOUNT 1 -> 127, TONE 127 -> 0 (blocks 10 .. 136)
        sw += [(10 + b, "DRIVE", 1 + b), (10 + b, "DRV_TONE", 127 - b)]
    sw += [(150, "DRIVE", 1), (170, "DRIVE", 127), (190, "DRV_TONE", 127), (210, "DRV_TONE", 0),
           (230, "DRIVE", 20), (250, "DRIVE", 127)]
    y, _, _ = ds.render(exe, x, ds.params_for(typ, 1, 127), sw)
    got = d2(y, 12 * 256, nb * 256)
    assert got <= 1.2 * ref + slack, (typ, sig, got, ref)


@pytest.mark.parametrize("typ", ["TSH8", "BHLUE", "OHCD"])
def test_level_matched_to_off(exe, typ):
    """the loudness (K-weighted) of the TYPE at its defaults (AMOUNT 64, TONE 64) and at the corners is near the
    input's (OFF): the tables carry the match (tools/drive_gen/calib_levels.py); the phrase is a synthetic one here,
    so the band is wide"""
    import bar_measure as bm
    x = signals()[1].astype(np.int64)
    lin = bm.lufs(x[FS:].astype(float))
    for a, t in ((64, 64), (1, 64), (127, 64), (64, 0), (64, 127)):
        y, _, pk = ds.render(exe, x, ds.params_for(typ, a, t))
        assert abs(bm.lufs(y[FS:].astype(float)) - lin) < 3.0, (typ, a, t)
        assert pk < 32767
