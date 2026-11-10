# SPDX-License-Identifier: GPL-3.0-only
"""The control zippers and clicks fixed in 0.9.10 (tools/control_sweep.py's material: the pedalled chord struck again
every second, every other effect off), each with its own bound, tighter than the sweep's general limits; each fails
on rc-0910 before its fix (the numbers: before -> after). The renders are shared with test_control_sweep.py
(build/host/ctl_sweep, cached by the binary's hash)."""
import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import control_sweep as cs  # noqa: E402

import hostrun  # noqa: E402
from test_control_sweep import BASE  # noqa: E402


@pytest.fixture(scope="module")
def binary():
    return hostrun.build()


def flip_click(name, a, b, binary, wet=False):
    c = cs.BY_NAME[name]
    mv = cs.render(cs.script(c, value=a, flip=b), binary)
    n = len(mv)
    sa, sb = cs.render(cs.script(c, value=a), binary)[:n], cs.render(cs.script(c, value=b), binary)[:n]
    if wet:
        o = cs.render(cs.script(c, off=True), binary)[:n]
        mv, sa, sb = mv - o, sa - o, sb - o
    return cs.click_ratio(mv, sa, sb)


def sweep_zipper(name, binary):
    c = cs.BY_NAME[name]
    off = cs.render(cs.script(c, off=True), binary)
    st = [cs.render(cs.script(c, value=v), binary) for v in c.statics]
    sw = cs.render(cs.script(c, sweep=True), binary)
    return cs.zipper_ratio(sw, st), cs.zipper_ratio(sw - off, [s - off for s in st])


def test_comp_makeup_glides(binary):
    """MAKEUP 0 <-> 127 in one CC under the ringing chord: no click (17.3x before the glide)"""
    for a, b in ((0, 127), (127, 0)):
        assert flip_click("comp MAKEUP", a, b, binary) <= 3.0, (a, b)


def test_tape_time_heads_follow_the_motor(binary):
    """TAPE TIME swept: the head bump and the high-frequency loss move with the motor's speed (2.53x before)"""
    z, zw = sweep_zipper("delay TAPE TIME", binary)
    assert z <= 2.0, z
    assert zw <= 2.3, zw          # (the echoes' own varispeed: 1.63x the pitch at the sweep's end, r^2 ~ 2.6 in d2)


def test_hall_size_auto_glides(binary):
    """HALL SIZE 1 <-> 0 (AUTO, MIX 64's size: 35 steps away) under a ringing tail: no click in the wet (2.07x)"""
    for a, b in ((1, 0), (0, 1), (127, 0)):
        assert flip_click("rev HALL SIZE", a, b, binary, wet=True) <= 1.5, (a, b)
    z, zw = sweep_zipper("rev HALL SIZE", binary)
    assert max(z, zw) <= 1.5, (z, zw)


def test_plate_size_fade_is_smooth(binary):
    """PLATE SIZE swept: the reads' cross-fade moves every sample (a staircase of 8 steps a site: 2.59x, wet 3.35x)"""
    z, zw = sweep_zipper("rev PLATE SIZE", binary)
    assert z <= 2.0 and zw <= 2.0, (z, zw)


def test_reverb_type_starts_without_a_step(binary):
    """INFINITE after 640 ms of PLATE (the sweep's path): the new tank's wet over its first 200 ms has no more d2 than a
    held INFINITE's (2.7x before: its input began at full level mid-waveform, and its half-band's history held PLATE's
    last input); from OFF it starts the same whichever audio block it falls in (the d2 7 .. 25 by the block before)"""
    c = cs.BY_NAME["rev TYPE"]
    off = cs.render(cs.script(c, off=True), binary)
    held = cs.render(cs.script(c, value=112), binary) - off
    base = [l for l in cs.script(c, value=0).splitlines() if not l.endswith("end")]

    def run(evs):
        ls = base + evs + [f"{cs.T_END} end"]
        return cs.render("\n".join(sorted(ls, key=lambda s: int(s.split()[0]))) + "\n", binary) - off

    a, b = int(2930 * 44.1) // 1024, int(3130 * 44.1) // 1024   # (the 200 ms from the new TYPE's first window)
    ref = cs.d2_max(held)[a:b].max()
    pl = run(["2260 midi 0xB0 95 64", "2900 midi 0xB0 95 96"])
    assert cs.d2_max(pl)[a:b].max() <= ref, (cs.d2_max(pl)[a:b], ref)
    starts = [cs.d2_max(run([f"{t} midi 0xB0 95 96"]))[a:b].max() for t in (2880, 2900, 2906, 2912)]
    assert max(starts) <= ref and max(starts) <= 1.5 * min(starts), (starts, ref)
    z, zw = sweep_zipper("rev TYPE", binary)
    assert zw <= 2.0, zw



def _type_run(binary, evs):
    """rev TYPE's material (the chord struck every second, MIX 64), TYPE off, then these events; the wet"""
    c = cs.BY_NAME["rev TYPE"]
    off = cs.render(cs.script(c, off=True), binary)
    base = [l for l in cs.script(c, value=0).splitlines() if not l.endswith("end")]
    ls = base + evs + [f"{cs.T_END} end"]
    return cs.render("\n".join(sorted(ls, key=lambda s: int(s.split()[0]))) + "\n", binary) - off


def test_plate_modulation_has_no_grain(binary):
    """PLATE's modulated reads move smoothly: a held plate at the default MOD has no more d2 than one at MOD 0 (rc-0910:
    4.0x; the reads stepped 2 samples at each whole sample of the excursion, which brightened the wet 2-12 kHz by
    8-10 dB of noise and made a plate started late read 2.3x a held one in rev TYPE's sweep)"""
    a, e = cs.ms(cs.T_SW0) // 1024, cs.ms(cs.T_SW1) // 1024
    m = cs.d2_max(_type_run(binary, ["250 midi 0xB0 95 80"]))[a:e].max()
    m0 = cs.d2_max(_type_run(binary, ["160 midi 0xB0 68 0", "250 midi 0xB0 95 80"]))[a:e].max()
    assert m <= 1.3 * m0, (m, m0)


def test_type_change_fades_to_zero(binary):
    """HALL -> PLATE under a ringing tail: the old TYPE fades to exactly 0 and the upsampler keeps its history, so the
    switch has no more d2 than a held HALL there (rc-0910: 4.6x; the fade ended at 1/128 and the history was cleared)"""
    w = int(2260 * 44.1) // 1024
    hp = cs.d2_max(_type_run(binary, ["1640 midi 0xB0 95 48", "2260 midi 0xB0 95 80"]))[w - 1:w + 2].max()
    hh = cs.d2_max(_type_run(binary, ["250 midi 0xB0 95 48"]))[w - 1:w + 2].max()
    assert hp <= 1.5 * hh, (hp, hh)

# the reverb's SIZE and TYPE cases on the other reverb kernels: the HQ build (rev_hq.c, rev_hq_plate.c, full rate) and
# the two-core one (FR_C1: the effects on core 1, the same HQ reverb), each check at the sweep's general LIMITS (or
# test_control_sweep.py's BASE bound for the case). 0.9.10: PLATE SIZE's wet bent 6.3 cents p90 there, its jump 3.3
# (PL_XS 32); with the modulated reads mended and the 1 s fade (PL_XS 8) 1.3 cents, 0.7
BUILDS = {
    "hq": ("build/host-hq/frodhes", ["make", "-C", hostrun.ROOT, "build/host-hq/frodhes"]),
    "c1": ("build/host-c1/frodhes", ["make", "-C", hostrun.ROOT, "build/host-c1/frodhes",
                                     "HOSTBIN=build/host-c1/frodhes", "HOST_DEFS=-DFR_C1=1"]),
}
REV_CASES = ["rev HALL SIZE", "rev PLATE SIZE", "rev INF SIZE", "rev TYPE"]
# THRESHOLD (2026-11-10, rc4): the two-core build's PLATE SIZE zipper_wet bounded at 2.1 (LIMITS 2.0); reason: 2.03 is
# one 1024-sample window at 5.085 s (sweep step 205.3, SIZE ~49 falling): a single-sample slope kink of 35 LSB in a
# 3000-LSB wet waveform during the SIZE cross-fade (first differences 103.5, 68, 80), about -70 dBFS broadband under
# an output at 4400 RMS, no step in level; the held plates differ among themselves up to 21 there (SIZE 80) against
# the statics' 16.5; the HQ build reads 1.67 with the same reverb. Fails the same on stage2-0910 (8807d119) and
# fxlevel-0910 (4bbc66c7)
C1_BOUND = {"rev PLATE SIZE": {"zipper_wet": 2.1}}


@pytest.fixture(scope="module", params=list(BUILDS))
def other(request):
    rel, cmd = BUILDS[request.param]
    return hostrun.once(os.path.join(hostrun.ROOT, rel),
                        lambda b: subprocess.run(cmd, check=True, capture_output=True))


@pytest.mark.parametrize("name", REV_CASES)
def test_reverb_size_type_hq_c1(name, other):
    c = cs.BY_NAME[name]
    base = BASE.get(name, {})                          # (test_control_sweep.py's bounds, if the case has one)
    c1 = C1_BOUND.get(name, {}) if "host-c1" in other else {}
    bad = {k: v for k, v in cs.checks(c, cs.measure(c, other)).items()
           if not v[2] and not (k in base and v[0] <= base[k][0]) and not (k in c1 and v[0] <= c1[k])}
    assert not bad, f"{name} ({other}): " + ", ".join(f"{k} {v[0]} (limit {v[1]})" for k, v in bad.items())
