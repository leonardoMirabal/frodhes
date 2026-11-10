# SPDX-License-Identifier: GPL-3.0-only
"""A clean Rhodes at every velocity (0.9.10, the user: "I should get a pristine Rhodes sound but it is completely
distorted"): with EFFECTS OFF (CC 111), on both velocity methods (RESPONSE FULL and LIGHT), a single note's bark
(H2..H5 re H1, 30..150 ms after the strike, gen_rhodes.bark_h1's window) is never over 0.9.9's at v110 and v127,
and H3 stays as far under H1 as 0.9.9's. 0.9.10's first pickup drive took A1 v127 to H2 +19 / H3 +14 dB over H1
(0.9.9: -12 / -42); the swing is now the physical force law's again, 0.9.9's, on every key (params.json
pickup_drive.bark_physical, velmap.json bark_physical), and the level comes back by gain (the make-ups). The top keys
still brighten with velocity, as 0.9.9's (the first fix flattened them at -35 dB)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import control_sweep as cs  # noqa: E402
import dry_dist  # noqa: E402

TB = "".join(ln + "\n" for ln in dry_dist.type_base_lines(45))   # the voicing at its defaults, not F01's

import hostrun  # noqa: E402

# 0.9.9's bark (H2..H5 re H1, dB) and H3 re H1 on the same measure (host build v0.9.9, EFFECTS OFF)
CEIL = {(33, 110): (-15.5, -50), (33, 127): (-12.0, -42), (45, 110): (-11.8, -42), (45, 127): (-8.2, -35),
        (57, 110): (-8.8, -36), (57, 127): (-5.4, -29), (69, 110): (-8.5, -34), (69, 127): (-5.2, -28)}


@pytest.fixture(scope="module")
def binary():
    return hostrun.build()


def harm(x, f0):
    i0 = int(np.argmax(np.abs(x) > 0.1 * np.abs(x).max()))
    seg = x[i0 + int(0.03 * 44100):i0 + int(0.15 * 44100)]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 16)) ** 2
    fr = np.fft.rfftfreq(1 << 16, 1 / 44100)
    h = [float(np.sum(S[(fr >= 0.97 * k * f0) & (fr <= 1.03 * k * f0)])) + 1e-30 for k in range(1, 6)]
    return 10 * np.log10(sum(h[1:]) / h[0]), 10 * np.log10(h[2] / h[0])


@pytest.mark.parametrize("resp", [0, 127], ids=["FULL", "LIGHT"])
@pytest.mark.parametrize("kv", sorted(CEIL), ids=[f"{k}v{v}" for k, v in sorted(CEIL)])
def test_hard_notes_stay_clean(kv, resp, binary):
    k, v = kv
    s = (TB + f"50 midi 0xB0 10 127\n100 midi 0xB0 40 {resp}\n150 midi 0xB0 111 127\n400 midi 0x90 {k} {v}\n"
         f"1400 midi 0x80 {k} 0\n1500 end\n")
    bark, h3 = harm(cs.render(s, binary).mean(1), 440 * 2 ** ((k - 69) / 12))
    cb, c3 = CEIL[kv]
    assert bark <= cb + 0.5 and h3 <= c3 + 1.0, (bark, h3, CEIL[kv])


@pytest.mark.parametrize("resp", [0, 127], ids=["FULL", "LIGHT"])
@pytest.mark.parametrize("k", [69, 81, 93])
def test_velocity_still_brightens_the_top(k, resp, binary):
    """keys 69 and up: the bark at v127 at least 6 dB over v40's, as 0.9.9's swing gives (the first 0.9.10 fix left
    the top flat at -35 dB at every velocity)"""
    def bark(v):
        s = (TB + f"50 midi 0xB0 10 127\n100 midi 0xB0 40 {resp}\n150 midi 0xB0 111 127\n400 midi 0x90 {k} {v}\n"
             f"1400 midi 0x80 {k} 0\n1500 end\n")
        return harm(cs.render(s, binary).mean(1), 440 * 2 ** ((k - 69) / 12))[0]
    assert bark(127) - bark(40) >= 6.0, (k, bark(40), bark(127))
