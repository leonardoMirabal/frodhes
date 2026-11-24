# SPDX-License-Identifier: GPL-3.0-only
"""DRIVE TYPE 1 of the plugin's own-code build (FR_OWN: firmware/src/drv_own1.c, tools/drive_gen/own1.py), on the
host at -DFR_OWN=1 (tests/c/drive_scan.c through tools/drive_scan.py, and the host app for the whole chain). Every
other build keeps the classic drive in TYPE 1; this TYPE is designed to it from the outside only: its level per AMOUNT
is the classic drive's black-box profile (tests/baselines/drive_own_target.json, tools/drive_profile.py).

- its tables are what the generator writes; the framework's MIX law, wet share and boot gains are the same values in
  both builds; the classic drive is not in the own-code build's source;
- the level: the RMS gain at -12.3 and -24.3 dBFS (220 Hz) and on a 5-note chord within 0.5 dB of the target at every
  drive row and AMOUNT (TONE 64, MIX 127), and the TONE / MIX corners within 1 dB;
- aliasing (the stepped sweep C5 .. C8, AMOUNT 127, TONE 64) at least as low as the classic drive's at 2x at -12 and
  -6 dBFS (test_drive_os.py's limits), and no worse than the profile's figure at any row and level;
- silence stays silent; a tail ends within 50 ms; a quiet bass note is no staircase; AMOUNT / TONE / MIX swept under a
  chord make no step (zipper) larger than the settings' own; the look-ahead limiter never acts on the drive alone;
- the classic drive's tail check (test_drive_tail.py) on this TYPE;
- Gate O's expected list (GATE_O_EXPECT, tools/gate_o.py): the renders whose DRIVE runs TYPE 1; the probe build agrees
  on the renders checked here (the whole list: tools/gate_o.py --probe --check)."""
import json
import os
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "drive_gen"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_profile as dp  # noqa: E402
import drive_scan as ds  # noqa: E402
import hostrun  # noqa: E402

FS = 44100
OWN = ["-DFR_OWN=1"]
PLUG = ["-DFR_VOICES=24u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]   # (tools/gate_d.py DEFS)
LEVEL_TOL, CORNER_TOL = 0.5, 1.0

# Gate O's expected list: the renders whose DRIVE runs TYPE 1 (tools/gate_o.py --probe; the own-code build's TYPE 1
# is its own drive, so these are the renders it may move; every other render is byte-identical)
GATE_O_EXPECT = [
    'fx/heavy',
    'fx/heavy_cho',
    'fx/m_heavy',
    'fx/m_heavy_cho',
    'fx/m_rev127',
    'fx/rev127',
    'fx/v_heavy',
    'fx/v_heavy_cho',
    'fx/v_rev127',
    'hq/hq_parity',
    'hq/hq_restrike',
    'hq/hq_top',
    'scripts/drive_silence',
    'scripts/fx_slow_paths',
]


@pytest.fixture(scope="module")
def exe():
    e = hostrun.once(os.path.join(ds.OUT, "bin", "drive_scan-own1"), lambda p: _build(p, OWN))
    return e


@pytest.fixture(scope="module")
def exe0():
    return hostrun.once(os.path.join(ds.OUT, "bin", "drive_scan-own0"), lambda p: _build(p, []))


def _build(path, defs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", *defs, "-I" + os.path.join(ROOT, "firmware", "src"),
                    "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", path,
                    os.path.join(ROOT, "tests", "c", "drive_scan.c"), "-lm"], check=True)


def prm(a, t=64, m=127, row=0):
    return ds.params_for("CLASSIC", a, t, m, row)


def chord(n):
    t = np.arange(n) / FS
    x = 6000 * np.sin(2 * np.pi * 41.2 * t) + sum(4000 * np.sin(2 * np.pi * f * t + i) for i, f in
                                                    enumerate((261.63, 329.63, 392.0)))
    return np.round(x).astype(np.int64)


def jumps(y, a=0, b=None):
    return float(np.max(np.abs(np.diff(y[a:b].astype(float), 2))))


# ---- the build

def test_tables_are_the_generators():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "drive_gen", "own1.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_name():
    """TYPE 1's own name: generic (no brand), 5 characters at most (a card value), the same in the plugin window"""
    name = ds.own_name()
    assert name and name != "FDRV" and len(name) <= 5 and name.isalpha() and name.isupper()
    ui = open(os.path.join(ROOT, "plugin", "ui", "frui_params.h")).read()
    assert re.search(r'FRN_DRV\[\] = \{"OFF", "' + name + '"', ui)


def test_framework_values_the_same():
    """the MIX law (DR_WET / DR_DRY), the wet share (dr_share) and the boot gains: the own-code build states them
    (drive.c), every other build has them from fx.c: the same values"""
    outs = []
    for own in (0, 1):
        b = os.path.join(ds.OUT, "bin", f"drive_own_probe{own}-{os.getpid()}")
        os.makedirs(os.path.dirname(b), exist_ok=True)
        subprocess.run(["cc", "-O1", "-w", f"-DFR_OWN={own}", "-I" + os.path.join(ROOT, "firmware", "src"),
                        "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", b,
                        os.path.join(ROOT, "tests", "c", "drive_own_probe.c"), "-lm"], check=True)
        outs.append(subprocess.run([b], capture_output=True, text=True, check=True).stdout)
        os.remove(b)
    assert outs[0].count("\n") > 3000 and outs[0] == outs[1]


def test_classic_drive_not_in_the_own_code_build():
    """the own-code build's preprocessed app has none of the classic drive's functions; the firmware's has them"""
    def pre(defs):
        return subprocess.run(["cc", "-E", "-DDINGHY_HOST", '-DAPP_VER="dev"', *defs,
                               "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                               os.path.join(ROOT, "firmware", "src", "frodhes.c")],
                              capture_output=True, text=True, check=True).stdout
    names = ("fx_drive_classic", "dr_classic", "dr_settled", "dr_after", "dr_os2")
    own, fw = pre(PLUG + OWN), pre(PLUG)
    assert not [n for n in names if re.search(r"\b" + n + r"\b", own)]
    assert all(re.search(r"\b" + n + r"\b", fw) for n in names)
    assert re.search(r"\bdrv_own1_run\b", own) and not re.search(r"\bdrv_own1_run\b", fw)


# ---- the level (the target profile)

@pytest.mark.parametrize("row", range(4))
def test_level_per_amount(exe, row):
    js = json.load(open(dp.JSON))
    st = dp._stims()
    bad, worst = [], 0.0
    for a in range(128):
        r = dp.profile(exe, row, a, 64, 127, st)
        t = js["main"][dp.key(row, a, 64, 127)]
        e = [r["g"][0] - t["g"][0], r["g"][1] - t["g"][1], r["cg"] - t["cg"]]
        worst = max(worst, max(abs(v) for v in e))
        if max(abs(v) for v in e) > LEVEL_TOL:
            bad.append((a, [round(v, 2) for v in e]))
    assert not bad, f"row {row}: AMOUNT, level error (dB: -12 sine, -24 sine, chord) beyond {LEVEL_TOL}: {bad[:8]}"


@pytest.mark.parametrize("row", range(4))
def test_level_corners(exe, row):
    """TONE 0 / 127 and MIX 0 / 64 at AMOUNT 0, 8 .. 127: within CORNER_TOL of the target (TONE is this TYPE's own:
    a low-pass that follows it, so its corners are near the target's, not on it)"""
    js = json.load(open(dp.JSON))
    st = dp._stims()
    bad = []
    for a in dp.CORNER_AMOUNTS[::2] + [127]:
        for t, m in dp.CORNERS:
            r = dp.profile(exe, row, a, t, m, st)
            tg = js["corner"][dp.key(row, a, t, m)]
            e = max(abs(r["g"][0] - tg["g"][0]), abs(r["g"][1] - tg["g"][1]), abs(r["cg"] - tg["cg"]))
            if e > CORNER_TOL:
                bad.append((a, t, m, round(e, 2)))
    assert not bad, f"row {row}: (AMOUNT, TONE, MIX, worst error dB) beyond {CORNER_TOL}: {bad[:8]}"


# ---- aliasing

@pytest.mark.parametrize("amp,limit", [(8000, -58.5), (16000, -38.2)])
def test_aliasing_at_least_the_classic_drives(exe, amp, limit):
    rows, _, _ = ds.aliasing_tones(exe, prm(127), amp=amp)
    worst = max(r["folded_db"] for r in rows)
    assert worst <= limit, f"amp {amp}: the worst folded energy {worst:.1f} dB (the classic drive at 2x: {limit})"


@pytest.mark.parametrize("row", range(4))
def test_aliasing_every_row_and_level(exe, row):
    js = json.load(open(dp.JSON))
    for amp in (8000, 16000, 26000):
        rows, _, _ = ds.aliasing_tones(exe, prm(127, row=row), amp=amp)
        worst = max(r["folded_db"] for r in rows)
        ref = js["alias_amp"][f"alamp/r{row}/{amp}"]["max_db"]
        assert worst <= ref, f"row {row} amp {amp}: {worst:.1f} dB folded, the target's {ref}"


# ---- rest, tails, quiet notes

def test_silence_stays_silent(exe):
    z = np.zeros(256 * 48, np.int64)
    for row in range(4):
        for a in (1, 20, 64, 127):
            for t in (0, 127):
                y, _, _ = ds.render(exe, z, prm(a, t, 127, row))
                assert not y.any(), (row, a, t, np.abs(y).max())


def test_tail_ends_and_stays_zero(exe):
    x = np.concatenate([chord(256 * 20), np.zeros(256 * 600, np.int64)])
    for row in range(4):
        for a in (20, 64, 127):
            y, _, _ = ds.render(exe, x, prm(a, row=row))
            nz = np.flatnonzero(y)
            assert nz[-1] < 256 * 20 + int(0.05 * FS), (row, a, (nz[-1] - 256 * 20) / FS)


def _odd(y, f0=41.2):
    y = np.asarray(y, float)[FS:]
    p = np.abs(np.fft.rfft(y * np.blackman(len(y)))) ** 2
    fr = np.fft.rfftfreq(len(y), 1 / FS)
    pk = [p[np.abs(fr - h * f0) < 1.5].max() for h in (1, 3, 5)]
    return 10 * np.log10(pk[1] / pk[0]), 10 * np.log10(pk[2] / pk[0])


@pytest.mark.parametrize("amp", [40, 160, 320, 640])
def test_quiet_bass_no_staircase(exe, exe0, amp):
    """a 41.2 Hz sine at small inputs, DRIVE 20 (F08's): the odd harmonics (H3, H5) are no more than rounding makes
    (the input rounded, then rounded again at the output's level: + 6 dB) or no worse than the classic drive's at the
    same input (+ 1 dB); a stage kept in whole units would make a staircase there (test_drive_quiet.py)"""
    t = np.arange(2 * FS) / FS
    x = np.round(amp * np.sin(2 * np.pi * 41.2 * t)).astype(np.int64)
    y, _, _ = ds.render(exe, x, prm(20))
    y0, _, _ = ds.render(exe0, x, prm(20))
    out = np.sqrt(2) * np.std(np.asarray(y, float)[FS:])
    floor = _odd(np.round(x * out / amp))
    classic = _odd(y0)
    own = _odd(y)
    for h, o, f, c in zip((3, 5), own, floor, classic):
        lim = max(f + 6.0, c + 1.0)
        assert o <= lim, f"input {amp}: H{h} {o:.1f} dB re H1 (rounding's floor {f:.1f}, the classic {c:.1f})"


# ---- knob moves (zipper)

def test_no_step_on_amount_sweep(exe):
    n = 256 * 150
    x = chord(n)
    y, _, _ = ds.render(exe, x, prm(0), [(10 + k, "DRIVE", k) for k in range(128)])
    a, _, _ = ds.render(exe, x, prm(127))
    assert jumps(y, 2560) <= 1.05 * jumps(a, 2560)


def test_no_step_on_tone_sweep(exe):
    n = 256 * 150
    x = chord(n)
    y, _, _ = ds.render(exe, x, prm(64, 0), [(10 + k, "DRV_TONE", k) for k in range(128)])
    ref = max(jumps(ds.render(exe, x, prm(64, t))[0], 2560) for t in (0, 127))
    assert jumps(y, 2560) <= 1.05 * ref


def test_no_step_on_mix_sweep(exe):
    n = 256 * 150
    x = chord(n)
    y, _, _ = ds.render(exe, x, prm(64, 64, 0), [(10 + k, "DRV_MIX", k) for k in range(128)])
    ref = max(jumps(ds.render(exe, x, prm(64, 64, m))[0], 2560) for m in (0, 64, 127))
    assert jumps(y, 2560) <= 1.05 * ref


# ---- the whole app (the plugin's switches)

def _app():
    return hostrun.build(os.path.join(hostrun.ROOT, "build", "drive_own", "host-own"), extra=PLUG + OWN)


@pytest.mark.parametrize("amount", [20, 64, 127])
def test_limiter_never_acts_on_the_drive(amount):
    """F01 clean (every effect off), the drive alone on TYPE 1: single notes v64 / v127 and a v127 chord, the look-ahead
    limiter idle (fx.c's fx-clip counter), as tools/fx_pristine.py's lim"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import fx_pristine as fxp
    b = _app()
    for tag, notes in (("s64", [57]), ("s127", [57]), ("c127", list(fxp.CHORD))):
        v = 64 if tag == "s64" else 127
        ls = ["100 midi 0xB0 111 0", "200 midi 0xC0 0 0"] + [hostrun.cc(300, n, val) for n, val in fxp.CLEAN] + \
             [hostrun.cc(310, 84, 127), hostrun.cc(320, 81, amount)]
        for k in notes:
            ls += hostrun.note(500, k, v, 1500)
        r = hostrun.run(f"drive_own_lim_{amount}_{tag}", ls, end_ms=fxp.END, binary=b)
        m = re.search(r"fx-clip delay (\d+) reverb (\d+) chorus (\d+) limiter (\d+)", r.out)
        assert m and int(m.group(4)) == 0, (amount, tag, m and m.group(0))


@pytest.mark.parametrize("amount", [20, 64])
def test_tail_is_smooth(amount):
    """test_drive_tail.py's check (F05, the pedal down, a C4 held, the drive alone) on this TYPE"""
    import test_drive_tail as tdt
    b = _app()
    ls = ["0 midi 0xC0 4 0", hostrun.cc(100, 106, 0), hostrun.cc(105, 17, 0), hostrun.cc(110, 91, 0),
          hostrun.cc(115, 84, 127), hostrun.cc(125, 110, 46), hostrun.cc(400, 64, 127), *hostrun.note(500, 60, 100, 500)]
    x = hostrun.run(f"drive_own_tail_{amount}", ls + [hostrun.cc(120, 81, amount)], end_ms=20000, binary=b).L
    x0 = hostrun.run("drive_own_tail_dry", ls + [hostrun.cc(120, 81, 0)], end_ms=20000, binary=b).L
    big, res = tdt.tail_stats(x)
    big0, res0 = tdt.tail_stats(x0)
    assert big < 3.0, f"AMOUNT {amount}: {big:.1f} % steps >= 3 LSB in the tail (dry {big0:.1f} %)"
    assert res < res0 + 0.25, f"AMOUNT {amount}: {res:.2f} LSB above 2 kHz in the tail (dry {res0:.2f})"


# ---- Gate O's expected list

def test_gate_o_list_names_real_renders():
    import gate_o
    names = {n for n, _ in gate_o.all_entries()}
    assert GATE_O_EXPECT and set(GATE_O_EXPECT) <= names and GATE_O_EXPECT == sorted(set(GATE_O_EXPECT))
    assert gate_o.expected() == set(GATE_O_EXPECT)


def test_gate_o_list_agrees_with_the_probe_on_a_sample():
    """the probe (the FR_OWN=0 build with -DFR_DRV1_PROBE) on tests/hq and a subset of 12 names exactly the entries of
    GATE_O_EXPECT among them"""
    import gate_o
    ent = gate_o.all_entries(quick=True)
    hits = gate_o.probe(ent, 2)
    assert set(hits) == set(GATE_O_EXPECT) & {n for n, _ in ent}
