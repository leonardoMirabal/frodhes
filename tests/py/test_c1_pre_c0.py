# SPDX-License-Identifier: GPL-3.0-only
"""C1_PRE_C0 (fx.c, frodhes.c app_audio c1_front_take; off by default): each chunk's mono front (COMP, EQ, DRIVE,
FILTER) runs on core 0 right after the chunk's voices or on core 1 before TREM, chosen per chunk; the fronts in order,
each over one chunk. The same integers as the two-core build without it, on any placement:

- the HEAVY take (every effect, 14 voices) on each engine, the front forced onto core 0, onto core 1, alternating
  blocks, random blocks and random chunks, and as on the FM-1 (core 1 behind: core 0 takes it);
- a COMP switch, a FILTER TYPE change and the PHASER switched while the chord rings (the compressor's switch-on
  estimate, the filter's crossfade: its scratch is its own under C1_PRE_C0, xf_d being core 1's meanwhile), with
  core 1's jobs in order, refused and late (core 1 held: one core from then on, against the build without it so);
- with core 1 behind (busy=N: it takes several chunks at once) the front still runs chunk by chunk, so the take
  renders as the two-core build with core 1 on time (the build without C1_PRE_C0 can differ there: COMP's switch-on
  estimate reads its first range, which then is longer)."""
import os

import numpy as np
import pytest

from hostrun import ROOT, build, cc
from test_c1_pipeline import c1_bin, render, take

PRE = os.path.join(ROOT, "build", "host", "c1pre", "frodhes")
PLACE = ["front=1", "front=2", "front=3", "front=4,seed=7", "front=5,seed=11", "front=0"]


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
@pytest.mark.parametrize("place", PLACE)
def test_pre_c0_heavy(engine, place):
    a = render(f"pre_a_{engine}", take(engine), c1_bin())
    b = render(f"pre_b_{engine}_{place}", take(engine), pre_bin(), {"FRODHES_C1": place})
    same(a, b, f"engine {engine} {place}")


@pytest.mark.parametrize("mode", ["order=1", "order=1,refuse=3", "refuse=2,front=5,seed=3", "late=200",
                                  "late=200,front=4,seed=1"])
def test_pre_c0_core1_modes(mode):
    base = ",".join(k for k in mode.split(",") if not k.startswith(("front=", "seed=")))
    a = render("pre_ma", switches(), c1_bin(), {"FRODHES_C1": base})
    b = render("pre_mb", switches(), pre_bin(), {"FRODHES_C1": mode})
    same(a, b, mode)


@pytest.mark.parametrize("place", PLACE)
def test_pre_c0_switches(place):
    a = render("pre_sa", switches(), c1_bin())
    b = render("pre_sb", switches(), pre_bin(), {"FRODHES_C1": place})
    same(a, b, f"COMP / FILTER switches, {place}")


@pytest.mark.parametrize("mode", ["busy=8", "busy=8,front=1", "busy=3,front=5,seed=5", "busy=2,front=4,seed=9"])
def test_pre_c0_core1_behind(mode):
    a = render("pre_ba", switches(), c1_bin())
    b = render("pre_bb", switches(), pre_bin(), {"FRODHES_C1": mode})
    same(a, b, f"core 1 behind ({mode}) against core 1 on time")
