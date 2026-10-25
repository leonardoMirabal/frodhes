# SPDX-License-Identifier: GPL-3.0-only
"""The DRIVE's TYPEs (firmware/src/drive.c): the framework, on the firmware's own code (tests/c/drive_scan.c through
tools/drive_scan.py: fx_drive alone, 256 frames a call as fx_process calls it).

- FDRV (the classic drive) is what it was: its output on a fixed signal (sha256), its aliasing, its cost (80 us, the
  bar engines' budget); tools/gate_r.py checks every render besides.
- Every TYPE: AMOUNT 0, OFF and MIX 0 are the dry signal, bit for bit; a stub (its file says DRV_STUB) is the dry
  signal at any setting.
- Switching TYPE while it sounds: the TYPE that runs fades out (46 ms, exactly as OFF), then the new one fades in
  (46 ms): one TYPE runs at a time, never two (CPU); the cost counted (fx_cost_us: the larger of the two while the
  switch waits); no click. From silence (OFF faded, AMOUNT 0, MIX 0) the new TYPE starts at once.
- The cost (fx_cost_us -> drv_cost_us): a TYPE's whole cost, DRV_US_CORE (drive.c around the TYPE, once a block) +
  DRV_US_<T> (its loop), + DRV_US_BLEND while it blends (MIX < 127, a glide, a fade); while a switch waits, the
  larger of the two; within the budget, DRV_US_MAX (180 us, private plan: the cost gate; a TYPE over it does not
  build).
- MIX and the fades: the blend is x + ((y - x) w >> 15), w ramping 64 every 4 samples (46 ms), on multiples of 256.
- A TYPE with a model (not a stub): it drives, its peak keeps headroom, it folds no more than FDRV."""
import hashlib
import os
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import drive_scan as ds  # noqa: E402

FS = 44100
NEW = [t for t, v in sorted(ds.TYPES.items(), key=lambda kv: kv[1]) if v >= 2]   # KHLON TSH8 OHCD BHLUE
MODELLED = [t for t in NEW if not ds.is_stub(t)]
XF_BLOCKS = 8                                            # a fade: 2048 samples (16 a sample of 32768), 46 ms


def drive_c():
    return open(os.path.join(ROOT, "firmware", "src", "drive.c")).read()


def us_const(name):
    """a #define's value: drive.c's own, or a TYPE file's"""
    for p in ["drive.c"] + [f"drv_{t.lower()}.c" for t in NEW]:
        m = re.search(rf"#define {name} (\d+)u", open(os.path.join(ROOT, "firmware", "src", p)).read())
        if m:
            return int(m.group(1))
    raise KeyError(name)


CORE, US_MAX, BLEND = us_const("DRV_US_CORE"), us_const("DRV_US_MAX"), us_const("DRV_US_BLEND")


@pytest.fixture(scope="module")
def exe():
    e = ds.build(tag="pytest-types")
    yield e
    os.remove(e)


def tone(hz=440.0, amp=8000.0, n=32768):
    return np.round(amp * np.sin(2 * np.pi * hz * np.arange(n) / FS)).astype(np.int64)


def chord(n=32768):
    t = np.arange(n) / FS
    x = 6000 * np.sin(2 * np.pi * 41.2 * t) + sum(4000 * np.sin(2 * np.pi * f * t + i) for i, f in
                                                    enumerate((261.63, 329.63, 392.0)))
    return np.round(x).astype(np.int64)


def p(typ, amount=127, tone_v=64, mix=127):
    return ds.params_for(typ, amount, tone_v, mix)


# ---- FDRV: what it was

def test_fdrv_harness_numbers_unchanged(exe):
    """the classic drive's output on a fixed signal, by sha256 (the value main a8320e8 gives: the parked harness
    branch measured it there), its aliasing at 127 / 127, and its cost (FX_US_DRIVE: the bar budget, Gate V)"""
    x = np.concatenate([tone(440), chord(), tone(4186.0)])
    y, cost, _ = ds.render(exe, x, p("FDRV", 127, 127))
    assert cost == 80
    assert hashlib.sha256(y.astype("<i4").tobytes()).hexdigest()[:16] == "bf962ad1b4d7c1fc"
    rows, _, _ = ds.aliasing_tones(exe, p("FDRV", 127, 127))
    assert max(r["folded_db"] for r in rows) == pytest.approx(-30.6, abs=0.1)
    _, cost, _ = ds.render(exe, chord(4096), p("FDRV", 127, 30, 50))
    assert cost == 80                                    # (MIX, TONE: FDRV is 80 whatever its settings)


def test_harness_is_deterministic(exe):
    x = chord()
    a, _, _ = ds.render(exe, x, p("FDRV"))
    b, _, _ = ds.render(exe, x, p("FDRV"))
    assert np.array_equal(a, b) and not np.array_equal(a, x)


# ---- every TYPE: the dry cases

@pytest.mark.parametrize("typ", ["FDRV"] + NEW)
def test_off_amount0_mix0_are_dry(exe, typ):
    """OFF, AMOUNT 0 and MIX 0: the input, bit for bit (after the fades from the power-on state, FDRV)"""
    x = chord(16384)
    for prm in (p("OFF"), dict(p(typ), DRV_SW=0), p(typ, amount=0), p(typ, mix=0)):
        y, cost, _ = ds.render(exe, x, prm)
        assert np.array_equal(y[4096:], x[4096:]), (typ, prm)


@pytest.mark.parametrize("typ", [t for t in NEW if t not in MODELLED])
def test_a_stub_is_dry(exe, typ):
    """a stub TYPE (DRV_STUB) passes the dry signal at any setting, once FDRV (the power-on TYPE) has faded out
    (its fade-in blends dry with dry); it costs drive.c's own share and its own constant, whatever MIX is"""
    x = chord(16384)
    for a, t, m in ((127, 64, 127), (127, 0, 127), (64, 127, 127), (127, 64, 64), (1, 64, 100)):
        y, cost, _ = ds.render(exe, x, p(typ, a, t, m))
        assert np.array_equal(y[2048 + 512:], x[2048 + 512:]), (typ, a, t, m)
        assert cost == CORE + us_const(f"DRV_US_{typ}") + (BLEND if m < 127 else 0), (typ, cost)


# ---- switching

def jumps(y, a, b):
    return float(np.max(np.abs(np.diff(y[a:b].astype(float), 2))))


@pytest.mark.parametrize("to", NEW)
def test_switch_fades_out_then_in(exe, to):
    """FDRV -> a TYPE while a chord sounds: FDRV fades out exactly as OFF does (8 blocks, 46 ms), then the TYPE runs
    alone, from its reset state, fading in; never both in one block; the cost the larger of the two while the switch
    waits, then the TYPE's; no click (the second difference no bigger than the two sounds' own)"""
    T, X = ds.TYPES[to], XF_BLOCKS
    x = chord(32 * 256)
    y, _, _, tr = ds.render(exe, x, p("FDRV"), [(10, "DRV_SW", T)], trace=True)
    off, _, _ = ds.render(exe, x, p("FDRV"), [(10, "DRV_SW", 0)])
    a, _, _ = ds.render(exe, x, p("FDRV"))
    b, _, _ = ds.render(exe, x, p(to))
    on = [t[1] for t in tr]
    nx = [t[2] for t in tr]
    assert on[:10 + X] == [1] * (10 + X) and set(on[10 + X:]) == {T}
    assert nx[:10] == [0] * 10 and nx[10:10 + X] == [T] * X and set(nx[10 + X:]) == {0}
    us = CORE + us_const(f"DRV_US_{to}")
    bl = us + BLEND
    assert [c for c, _, _ in tr[10:10 + X]] == [max(80, bl)] * X                         # (the switch waits)
    assert tr[10 + X][0] == bl and tr[-1][0] == us                                       # (fading in, then alone)
    assert np.array_equal(y[:(10 + X) * 256], off[:(10 + X) * 256])                       # FDRV out, as OFF
    if to not in MODELLED:
        assert np.array_equal(y[(10 + X) * 256:], x[(10 + X) * 256:])                    # (a stub: dry, fading in too)
    ref = max(jumps(a, 1000, 8000), jumps(b, 1000, 8000))
    assert jumps(y, 1000, 8000) <= 1.2 * ref + 40


def test_one_type_at_a_time(exe):
    """FDRV -> KHLON -> TSH8 -> BHLUE one block apart: FDRV fades out (8 blocks), then the last one chosen, BHLUE,
    starts; KHLON and TSH8 never run"""
    t = ds.TYPES
    x = chord(32 * 256)
    sw = [(10, "DRV_SW", t["KHLON"]), (11, "DRV_SW", t["TSH8"]), (12, "DRV_SW", t["BHLUE"])]
    _, cost, _, tr = ds.render(exe, x, p("FDRV"), sw, trace=True)
    pairs = [(o, n) for _, o, n in tr]
    assert pairs[:10] == [(1, 0)] * 10
    assert pairs[10:10 + XF_BLOCKS] == [(1, t["KHLON"]), (1, t["TSH8"])] + [(1, t["BHLUE"])] * (XF_BLOCKS - 2)
    assert set(pairs[10 + XF_BLOCKS:]) == {(t["BHLUE"], 0)} and cost == CORE + us_const("DRV_US_BHLUE")


def test_switch_and_back_before_the_fade_ends(exe):
    """FDRV -> KHLON -> FDRV again 2 blocks later: FDRV never stopped (the same as OFF then ON), KHLON never ran"""
    x = chord(24 * 256)
    y, _, _, tr = ds.render(exe, x, p("FDRV"), [(10, "DRV_SW", ds.TYPES["KHLON"]), (12, "DRV_SW", 1)], trace=True)
    ref, _, _ = ds.render(exe, x, p("FDRV"), [(10, "DRV_SW", 0), (12, "DRV_SW", 1)])
    assert np.array_equal(y, ref) and {o for _, o, _ in tr} == {1}


def test_off_fades_a_type_out_and_fdrv_comes_back_at_once(exe):
    """a TYPE, then OFF: the 46 ms fade, then the dry signal, bit for bit, at no cost (the TYPE stays chosen, idle);
    FDRV from there: it starts at once (the drive is silent) and fades in"""
    k = ds.TYPES["KHLON"]
    x = chord(40 * 256)
    sw = [(20, "DRV_SW", 0), (32, "DRV_SW", 1)]
    y, _, _, tr = ds.render(exe, x, p("KHLON"), sw, trace=True)
    on = [o for _, o, _ in tr]
    assert on[:XF_BLOCKS] == [1] * XF_BLOCKS and set(on[XF_BLOCKS:32]) == {k} and set(on[32:]) == {1}
    assert np.array_equal(y[28 * 256:32 * 256], x[28 * 256:32 * 256])
    assert [c for c, _, _ in tr[28:32]] == [0] * 4
    assert not np.array_equal(y[34 * 256:], x[34 * 256:])


def test_cost_while_off_and_at_amount0(exe):
    """fx_cost_us counts the drive only while it runs: OFF (faded) and AMOUNT 0 cost nothing, as before"""
    for prm in (p("OFF"), p("KHLON", amount=0), p("FDRV", amount=0)):
        _, cost, _ = ds.render(exe, chord(8192), prm)
        assert cost == 0, prm


# ---- a TYPE with a model (none yet: each lands in its own file)

def _aa_on(name):
    """a TYPE's anti-aliasing switch as the build has it (drive_tables.h <NAME>_AA, or the TYPE's own file)"""
    for f in (os.path.join(ROOT, "firmware", "gen", "drive_tables.h"),
              os.path.join(ROOT, "firmware", "src", f"drv_{name.lower()}.c")):
        m = re.search(rf"#define (?:{name}|KH)_AA (\d)", open(f).read()) if os.path.exists(f) else None
        if m:
            return bool(int(m.group(1)))
    return False


# the folded energy a TYPE may show at AMOUNT 127 / TONE 127 (dB), measured, following its AA switch: anti-aliased
# (on in TSH8, BHLUE and OHCD), better than FDRV's -30.6 (OHCD at its full top: about it), what it measures + 0.5 dB; plain (KHLON: its AA switch off,
# KH_AA 0), what its 1x kernel measures (+0.5 dB): a regression guard
ALIAS_AA = {"TSH8": -33.75, "BHLUE": -34.65, "KHLON": -33.0, "OHCD": -29.35}  # (measured with AA: TSH8 -34.25, BHLUE
#   -35.16; +0.5 dB. OHCD: -29.85 at its full range's top (0.9.5: AMOUNT 127 is the DRIVE knob's end, ~13 dB more gain
#   than the capped -32.02; -33.34 at AMOUNT 64, -47.8 at 33); its ADAA-1 is on, the clipper simply works harder)
ALIAS_PLAIN = {"TSH8": -20.25, "BHLUE": -22.0, "KHLON": -18.0, "OHCD": -22.3}    # (KHLON plain: -18.33, OHCD -22.83 measured)
ALIAS_MAX = {t: v for t, v in ALIAS_AA.items() if _aa_on(t)}
ALIAS_MAX.update({t: v for t, v in ALIAS_PLAIN.items() if not _aa_on(t)})


@pytest.mark.parametrize("typ", MODELLED or [pytest.param("none", marks=pytest.mark.skip("every TYPE is a stub"))])
def test_a_modelled_type(exe, typ):
    x = chord()
    y, cost, pk = ds.render(exe, x, p(typ))
    assert not np.array_equal(y[4096:], x[4096:])
    assert 20 * np.log10(max(pk, 1) / 32767) < -1.0, pk
    us = us_const(f"DRV_US_{typ}")
    assert 0 < us and CORE + us + BLEND <= US_MAX, us                                 # the cost gate (estimate: hil_cpu decides)
    assert cost == CORE + us
    rows, _, _ = ds.aliasing_tones(exe, p(typ, 127, 127))
    crow, _, _ = ds.aliasing_tones(exe, p("FDRV", 127, 127))
    assert max(r["folded_db"] for r in rows) <= ALIAS_MAX.get(typ, max(r["folded_db"] for r in crow) + 0.5), typ


# ---- the framework around a real TYPE: tests/c/drv_demo.c in KHLON's place (a test build only)

_DEMO = open(os.path.join(ROOT, "tests", "c", "drv_demo.c")).read()
DEMO_US = int(re.search(r"#define DRV_US_KHLON (\d+)u", _DEMO).group(1))
DEMO_BL = DEMO_US + BLEND


@pytest.fixture(scope="module")
def demo():
    f = os.path.join(ROOT, "tests", "c", "drv_demo.c")
    e = ds.build([f'-DDRV_FILE_KHLON="{f}"'], tag="pytest-demo")
    yield e
    os.remove(e)


def test_demo_type_drives_and_counts(demo):
    x = chord()
    y, cost, pk = ds.render(demo, x, p("KHLON"))
    assert not np.array_equal(y[4096:], x[4096:]) and 20 * np.log10(pk / 32767) < -1.0
    assert cost == CORE + DEMO_US
    a, _, _ = ds.render(demo, x, p("KHLON"))
    assert np.array_equal(a, y)


def wt_of(mix):
    """a TYPE's wet share at MIX v (drive.c fx_drive_types): on multiples of 256"""
    return 32768 if mix >= 127 else (mix * 258 + 128) & ~255


def test_mix_blends_a_type_exactly(demo):
    """MIX 64 is the dry signal plus 16640 / 32768 of (wet - dry), sample for sample, once the MIX glide is over (the
    TYPE runs the same at any MIX); MIX 0 the dry signal"""
    x = chord(16384)
    full, _, _ = ds.render(demo, x, p("KHLON"))
    half, cost, _ = ds.render(demo, x, p("KHLON", mix=64))
    want = x + (((full - x) * wt_of(64)) >> 15)
    assert wt_of(64) == 16640 and np.array_equal(half[4096:], want[4096:]) and cost == CORE + DEMO_BL
    zero, _, _ = ds.render(demo, x, p("KHLON", mix=0))
    assert np.array_equal(zero[4096:], x[4096:])


def test_switches_between_types_do_not_click(demo):
    """FDRV -> the demo -> a stub -> FDRV while a chord sounds: each switch a fade out then in, no click"""
    x = chord(48 * 256)
    t = ds.TYPES
    sw = [(10, "DRV_SW", t["KHLON"]), (22, "DRV_SW", t["TSH8"]), (34, "DRV_SW", 1)]
    y, _, _, tr = ds.render(demo, x, p("FDRV"), sw, trace=True)
    on = [o for _, o, _ in tr]
    assert on[17:19] == [1, t["KHLON"]] and on[25:27] == [t["KHLON"], t["TSH8"]] and on[-1] == 1 and tr[-1][2] == 0
    refs = [ds.render(demo, x, p(k))[0] for k in ("FDRV", "KHLON")] + [x]
    ref = max(jumps(r, 3000, len(x)) for r in refs)
    assert jumps(y, 2000, len(x)) <= 1.2 * ref + 40


def test_mix_glide_ramps_every_4_samples(demo):
    """MIX 127 -> 64 while it sounds: the wet share ramps 64 every 4 samples (4096 a block: the classic drive's 46 ms) to
    16640, and back up to 127; sample for sample the TYPE's full output blended (the TYPE runs the same at any MIX);
    the cost all wet + DRV_US_BLEND while it blends, then all wet; no click"""
    x = chord(48 * 256)                                  # (from power-on: FDRV fades out, the TYPE in: 16 blocks)
    full, _, _ = ds.render(demo, x, p("KHLON"))
    y, _, _, tr = ds.render(demo, x, p("KHLON"), [(20, "DRV_MIX", 64), (34, "DRV_MIX", 127)], trace=True)
    w, want = 32768, full.copy()
    for b in range(20, len(x) // 256):
        wt = 32768 if b >= 34 else wt_of(64)
        w1 = max(w - 4096, wt) if w > wt else min(w + 4096, wt)
        ws = w + (np.arange(256) // 4) * ((w1 - w) >> 6)
        a = b * 256
        want[a:a + 256] = x[a:a + 256] + (((full[a:a + 256] - x[a:a + 256]) * ws) >> 15)
        w = w1
    assert np.array_equal(y, want) and not np.array_equal(y, full)
    cs = [c for c, _, _ in tr[20:]]                      # (blending while MIX < 127 and on the way back, then not)
    assert cs[:17] == [CORE + DEMO_BL] * 17 and set(cs[18:]) == {CORE + DEMO_US}
    assert jumps(y, 2000, len(x)) <= 1.2 * max(jumps(full, 2000, len(x)), jumps(x, 2000, len(x))) + 40


def test_cost_is_whole_and_within_the_budget(demo):
    """drv_cost_us: CORE + the TYPE's loop at MIX 127 at rest, + DRV_US_BLEND while MIX < 127 or the wet share
    glides (any TONE, AMOUNT > 0); every TYPE (and the demo) within DRV_US_MAX blending; drive.c's constants as
    recorded"""
    assert US_MAX == 180 and US_MAX - CORE - BLEND == 165
    for typ in NEW:
        assert CORE + us_const(f"DRV_US_{typ}") + BLEND <= US_MAX, typ
    assert CORE + DEMO_BL <= US_MAX
    x = chord(40 * 256)
    for mix, tone_v, amount in ((127, 64, 127), (0, 64, 127), (1, 0, 1), (64, 127, 64), (126, 64, 127)):
        _, _, _, tr = ds.render(demo, x, p("KHLON", amount, tone_v, mix), [(20, "DRV_MIX", 127 - mix)], trace=True)
        cs = [c for c, _, _ in tr]
        assert set(cs) <= {CORE + DEMO_US, CORE + DEMO_BL, 0}, (mix, tone_v, amount)    # (0: MIX 0, faded: idle)
        end = 127 - mix
        assert cs[-1] == (CORE + DEMO_US if end == 127 else CORE + DEMO_BL), (mix, cs[-1])   # (MIX 0 counted)


def test_a_type_over_the_budget_does_not_build(tmp_path):
    """drive.c's static assertion: a TYPE whose loop costs more than DRV_US_KERNEL_MAX (DRV_US_MAX - DRV_US_CORE -
    DRV_US_BLEND) stops the build; at the ceiling it builds"""
    src = open(os.path.join(ROOT, "firmware", "src", "drv_khlon.c")).read()
    for us, ok in ((US_MAX - CORE - BLEND, True), (US_MAX - CORE - BLEND + 1, False)):
        f = tmp_path / f"drv_khlon_{us}.c"
        f.write_text(re.sub(r"#define DRV_US_KHLON \d+u", f"#define DRV_US_KHLON {us}u", src))
        r = subprocess.run(["cc", "-fsyntax-only", "-Wall", "-Wno-unused-function", f'-DDRV_FILE_KHLON="{f}"',
                            "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                            os.path.join(ROOT, "tests", "c", "drive_scan.c")], capture_output=True, text=True)
        assert (r.returncode == 0) == ok, (us, r.stderr[-400:])
        if not ok:
            assert "over its CPU budget" in r.stderr
