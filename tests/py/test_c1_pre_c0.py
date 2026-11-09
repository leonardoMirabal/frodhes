# SPDX-License-Identifier: GPL-3.0-only
"""C1_PRE_C0 (fx.c, frodhes.c app_audio; off by default): core 0 runs the mono front (COMP, EQ, DRIVE, FILTER) of each
chunk right after its voices, core 1 the rest from TREM on. The same integers as the two-core build without it:

- the HEAVY take (every effect, 14 voices) on each engine, with core 1's jobs in order, late in the block and refused;
- a COMP switch and a FILTER TYPE change while the chord rings (the compressor's switch-on estimate, the filter's
  crossfade: its scratch is its own under C1_PRE_C0, xf_d being core 1's meanwhile);
- with core 1 behind (busy=8: it takes several chunks at once) the front still runs chunk by chunk, so the take
  renders as the two-core build with core 1 on time (the build without C1_PRE_C0 can differ there: COMP's switch-on
  estimate reads its first range, which then is longer)."""
import numpy as np
import pytest

from hostrun import ROOT, build, cc
from test_c1_pipeline import c1_bin, render, take
import os

PRE = os.path.join(ROOT, "build", "host", "c1pre", "frodhes")


def pre_bin():
    return build(PRE, ["-DFR_C1=1", "-DC1_PRE_C0=1"])


def switches():
    """the take, with COMP off -> on and the FILTER's TYPE changed while the chord rings, the PHASER switched with it
    (its crossfade uses xf_d on core 1 while the filter's crossfade runs on core 0)"""
    return take() + [cc(1200, 106, 0), cc(1900, 106, 127), cc(2590, 12, 0), cc(2600, 70, 60), cc(3290, 12, 127),
                     cc(3300, 70, 30)]


def same(a, b, what):
    d = np.flatnonzero(np.any(a != b, axis=0)) if a.shape == b.shape else [0]
    assert len(d) == 0, f"{what}: C1_PRE_C0 differs from frame {d[0]}"


@pytest.mark.parametrize("engine", [0, 1, 2])
def test_pre_c0_heavy(engine):
    a = render(f"pre_a_{engine}", take(engine), c1_bin())
    b = render(f"pre_b_{engine}", take(engine), pre_bin())
    same(a, b, f"engine {engine}")


@pytest.mark.parametrize("mode", ["order=1", "order=1,refuse=3", "late=200"])
def test_pre_c0_core1_modes(mode):
    a = render("pre_ma", switches(), c1_bin(), {"FRODHES_C1": mode})
    b = render("pre_mb", switches(), pre_bin(), {"FRODHES_C1": mode})
    same(a, b, mode)


def test_pre_c0_switches():
    a = render("pre_sa", switches(), c1_bin())
    b = render("pre_sb", switches(), pre_bin())
    same(a, b, "COMP / FILTER switches")


def test_pre_c0_core1_behind():
    a = render("pre_ba", switches(), c1_bin())
    b = render("pre_bb", switches(), pre_bin(), {"FRODHES_C1": "busy=8"})
    same(a, b, "core 1 behind (busy=8) against core 1 on time")
