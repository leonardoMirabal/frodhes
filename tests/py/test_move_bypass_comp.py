# SPDX-License-Identifier: GPL-3.0-only
"""The Move's FX bypass keeps the compressor (FR_BYP_KEEP_CMP, MOVE_DEFS; the user, 2026-10-28): with FX off, the
Move build's output is the compressor's (its make-up and gain reduction heard), the FM-1 build's the plain dry; with
the compressor off, the two bypasses are the same samples."""
import os
import subprocess

import numpy as np

import hostrun

ROOT = hostrun.ROOT
CC = ["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-Ifirmware/src",
      "-Ifirmware/gen"]


def binary(keep):
    b = os.path.join(ROOT, "build", "move_bypass", "keep" if keep else "fm1")
    defs = ["-DFR_BYP_KEEP_CMP=1"] if keep else []
    return hostrun.once(b, lambda p: subprocess.run(CC + defs + ["-o", p, "firmware/src/frodhes.c", "-lm"],
                                                     cwd=ROOT, check=True))


def render(keep, comp):
    ev = ["40 midi 0xB0 106 %d" % (127 if comp else 0),   # COMP on / off
          "42 midi 0xB0 107 127", "44 midi 0xB0 112 127", "46 midi 0xB0 113 100",   # THR -40 dB, 20:1, make-up
          "48 midi 0xB0 111 127",                         # FX: bypassed
          "400 midi 0x90 48 110", "400 midi 0x90 55 110", "400 midi 0x90 60 110"]
    r = hostrun.run("byp-%s-%s" % ("keep" if keep else "fm1", "c" if comp else "n"), ev, 2400, binary=binary(keep))
    return r.L.astype(float)


def rms(x):
    return float(np.sqrt(np.mean(x[int(0.5 * hostrun.FS):] ** 2)))


def test_the_move_bypass_keeps_the_compressor():
    keep, fm1 = render(True, True), render(False, True)
    assert rms(keep) > 0 and rms(fm1) > 0
    db = 20 * np.log10(rms(keep) / rms(fm1))
    assert not np.array_equal(keep, fm1) and abs(db) > 1.0, "the Move's bypass plays the compressor (make-up, gain reduction): %.1f dB" % db


def test_with_the_compressor_off_the_bypasses_are_equal():
    assert np.array_equal(render(True, False), render(False, False))
