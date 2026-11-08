# SPDX-License-Identifier: GPL-3.0-only
"""Must-pass: the dry engine's distortion is never above 0.9.9's (tools/dry_dist.py has the cells and the limits).

Why (2026-11-08): the 0.9.10 Rhodes (sonic-0910 488f39dc) distorted heavily at v110..127 with the effects off; the
user heard it on headphones. Key 33 v127: H2..H5 re H1 +14.5 / +8.0 / +7.1 / -6.5 dB, where 0.9.9 has -14.5 / -47.2.
3285 passing tests did not flag it: none held the dry spectrum at high velocity to a reference.

Each case is one script (config x TYPE x RESPONSE): keys 28..93 at v64 / 96 / 110 / 127 and three 4-note v127 chords,
H2..H6 re H1, the inharmonic residue and the chords' intermodulation, every value <= 0.9.9's + 3 dB (or <= -60 dB
re H1) and H3 re H1 <= -25 dB at v127. The reference: tests/baselines/dry_dist_099.json, measured on 0.9.9
(456b5da4) with `tools/dry_dist.py --bin <0.9.9 host build> --save`. Proved both ways: it fails on 488f39dc (1062 of
2736 values over) and passes on 0.9.9 (DRY_DIST_BIN=<a host binary> runs it on that build). The FM-1 counterpart: tools/hil_jack.py's "dry distortion" section."""
import json
import os
import sys

import pytest

from hostrun import ROOT, build, out

sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist  # noqa: E402

BASE = json.load(open(dry_dist.BASE))


def test_the_reference_covers_every_cell():
    """every config / TYPE has its 0.9.9 cells, and every note and chord in them"""
    ev, _ = dry_dist.events()
    labels = {e[1] if e[0] == "note" else "chord/" + e[1] for e in ev}
    for tag, *_ in dry_dist.tags():
        cfg, ty, _ = tag.split("-")
        assert set(BASE[f"{cfg}-{ty}-A"]) == labels, tag


@pytest.mark.parametrize("tag", [t[0] for t in dry_dist.tags()])
def test_dry_distortion(tag):
    binary = os.environ.get("DRY_DIST_BIN") or build()     # (another build's host binary: the gate's own proof)
    cells = {tag: dry_dist.measure_tag(binary, tag, os.path.dirname(out("dry-dist-" + tag)))}
    rows = dry_dist.judge(cells, BASE)
    bad = [r for r in rows if not r[5]]
    assert not bad, "\n" + dry_dist.table(rows, only_bad=True, worst=60)
