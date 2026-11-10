# SPDX-License-Identifier: GPL-3.0-only
"""The FX button keeps the compressor (FR_BYP_KEEP_CMP, every build from 0.9.10; the user 2026-11-08: "leave compressor
aside of the efx off button, I don't want to change the dynamic of the piano"). Before, the bypass dropped the
compressor and its make-up: FX off sounded 1.0-1.2 dB weaker on F01-F04 (up to 2.6 dB on others), +7.5 dB with a
+9 dB make-up. Now FX off vs FX on with the other effects at MIX / DEPTH 0 match in level (0.5 dB) and in width (both
mono), on the one-core build and the two-core one (FR_C1, the effects in chunks on core 1)."""
import os
import subprocess

import numpy as np
import pytest

import hostrun

ROOT = hostrun.ROOT
CC = ["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-Ifirmware/src",
      "-Ifirmware/gen"]
FS = hostrun.FS
NOTES = (48, 55, 60, 64)
# every effect off (COMP, DRIVE, TREM, CHO/PHA, DELAY, REVERB MIX, FILTER, SPREAD, EQ flat)
CLEAN = [(106, 0), (84, 0), (17, 0), (85, 0), (88, 0), (91, 0), (70, 0), (105, 0), (34, 64), (35, 64), (37, 64)]
COMP = [(106, 127), (107, 64), (113, 48)]               # COMP on, THR -20 dB, 4:1, make-up +9 dB
AT0 = {                                                 # each effect on at MIX / DEPTH 0
    "delay": [(88, 40), (89, 0)],
    "reverb": [(95, 40), (91, 0)],
    "drive": [(84, 40), (81, 64), (83, 0)],
    "trem": [(17, 64), (18, 0)],
    "chorus": [(85, 64), (86, 0)],
}


def c1_binary():
    b = os.path.join(ROOT, "build", "bypass_level", "c1")
    return hostrun.once(b, lambda p: subprocess.run(CC + ["-DFR_C1=1", "-o", p, "firmware/src/frodhes.c", "-lm"],
                                                     cwd=ROOT, check=True))


BUILDS = {"one": lambda: hostrun.BIN, "c1": c1_binary}


def render(build, tag, pc, ccs, byp):
    ev = ["100 midi 0xB0 111 0", "200 midi 0xC0 %d 0" % pc] + [hostrun.cc(300, n, v) for n, v in ccs]
    if byp:
        ev.append("400 midi 0xB0 111 127")
    for n in NOTES:
        ev += hostrun.note(500, n, 100, 1500)
    r = hostrun.run("bypl-%s-%s-%s" % (build, tag, "byp" if byp else "on"), ev, 2200, binary=BUILDS[build]())
    return r.L[int(0.6 * FS):int(2.0 * FS)], r.R[int(0.6 * FS):int(2.0 * FS)]


def level(lr):
    L, R = lr
    return 10 * np.log10(np.mean((L * L + R * R) / 2) / 32768 ** 2 + 1e-20)


def width(lr):
    """side over mid, dB (-150: mono)"""
    L, R = lr
    m, s = (L + R) / 2, (L - R) / 2
    return max(-150.0, 10 * np.log10((np.mean(s * s) + 1e-12) / (np.mean(m * m) + 1e-12)))


def pair(build, tag, pc, ccs):
    on, off = render(build, tag, pc, ccs, 0), render(build, tag, pc, ccs, 1)
    return level(on) - level(off), width(on), width(off)


@pytest.mark.parametrize("build", list(BUILDS))
@pytest.mark.parametrize("fx", ["none"] + list(AT0))
def test_bypass_matches_effects_at_mix_0_with_the_compressor(build, fx):
    d, won, woff = pair(build, "c-" + fx, 0, CLEAN + COMP + AT0.get(fx, []))
    assert abs(d) <= 0.5, "%s, COMP on, %s at 0: FX on vs off %+.2f dB (the bypass kept the compressor?)" % (build, fx, d)
    assert abs(won - woff) <= 1.0 or max(won, woff) < -60, "%s %s: width on %.1f vs off %.1f dB" % (build, fx, won, woff)


@pytest.mark.parametrize("build", list(BUILDS))
@pytest.mark.parametrize("pc", [0, 1, 2, 3])
def test_factory_rhodes_presets_fx_off_not_weaker(build, pc):
    """F01-F04 (COMP on with make-up, a little reverb): FX off within 0.5 dB of FX on (it was 1.0-1.2 dB weaker)"""
    d, _, _ = pair(build, "p%d" % pc, pc, [])
    assert abs(d) <= 0.5, "%s F%02d: FX on vs off %+.2f dB" % (build, pc + 1, d)
