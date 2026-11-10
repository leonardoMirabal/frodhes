# SPDX-License-Identifier: GPL-3.0-only
"""The attack's lift (rhodes.c RH_ATK_A, 2026-11-09). The user heard the 0.9.10 Rhodes weak: its longer mid / upper
decay, level-matched to 0.9.9, left the first 60 ms of every note 0.9 .. 1.4 dB under 0.9.9 (tools/fx_level_audit.py
--cmp). The voice's output gain now starts x (1 + RH_ATK_A / 32768) (+1.5 dB) and returns to 1 over ~0.2 s, after the
pickup, so the tone is the same (stage 2: RH_ATK_A 1000, +0.26 dB); up to v96, then less, none at v127 (the user: no hotter signal: the hardest chords'
peak stays rc3's). Against a build without it (-DRH_ATK_A=0), the electric piano with the effects off:

- the attack (RMS 0..60 ms) +0.1 .. +0.3 dB on C2 / C4 / C6 / a 4-note chord at v40 / v80 (stage 2: 0.14 measured;
  at 6200 0.8 .. 0.9), +0.04 .. +0.2 at v110 (0.08), every TYPE; v127 byte-identical;
- from 300 ms on the same level (0.15 dB): only the strike is lifted;
- the tine's harmonics: the gain is the pickup's output gain (linear); the mechanical noises (the knock) are not
  lifted, so a band ratio in the strike's own window moves where the knock dominates it: the dry-distortion gate
  (tools/dry_dist.py, make quick's dry rows; 0 cells over at 64/64 and at HAMMER 48 / BARK 96) is the harmonics' check;
- VIBES and MARIMBA byte-identical (the bar engines have their own strike)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from hostrun import FS, HOST, build, run  # noqa: E402

NOATK = os.path.join(HOST, "frodhes-noatk")
CHORD = (48, 55, 60, 64)


def render(tag, notes, vel, ty=0, cc79=None, binary=None):
    if binary:
        build(binary, ("-DRH_ATK_A=0",))
    ev = ["50 midi 0xB0 10 127", "100 midi 0xB0 111 127", "110 midi 0xB0 106 0", f"120 midi 0xB0 80 {ty}"]
    if cc79 is not None:
        ev.append(f"130 midi 0xB0 79 {cc79}")
    ev += [f"400 midi 0x90 {n} {vel}" for n in notes] + [f"1900 midi 0x80 {n} 0" for n in notes]
    r = run(f"atk-{tag}{'-n' if binary else ''}", ev, 2000, **({"binary": binary} if binary else {}))
    return (r.L.astype(float) + r.R.astype(float)) / 2


def rms_db(x, t0, t1):
    s = x[int((0.4 + t0) * FS):int((0.4 + t1) * FS)]
    return 10 * np.log10(np.mean(s * s) + 1e-9)


CELLS = [(nm, ns, v, ty) for nm, ns in (("C2", (36,)), ("C4", (60,)), ("C6", (84,)), ("chd", CHORD))
         for v in (40, 80, 110, 127) for ty in (0, 32, 64, 96)]


@pytest.mark.parametrize("nm,ns,v,ty", CELLS, ids=[f"{c[0]}v{c[2]}t{c[3]}" for c in CELLS])
def test_attack_lifted_body_same(nm, ns, v, ty):
    a = render(f"{nm}-{v}-{ty}", ns, v, ty)
    b = render(f"{nm}-{v}-{ty}", ns, v, ty, binary=NOATK)
    if v == 127:
        assert np.array_equal(a, b), f"{nm} v127 TYPE {ty}: changed (no lift at v127)"
        return
    d0 = rms_db(a, 0, 0.06) - rms_db(b, 0, 0.06)
    d1 = rms_db(a, 0.3, 1.4) - rms_db(b, 0.3, 1.4)
    lo, hi = (0.04, 0.2) if v > 96 else (0.1, 0.3)     # THRESHOLD (0.3, 1.0) / (0.7, 1.6) -> (0.04, 0.2) / (0.1, 0.3):
    # RH_ATK_A 6200 -> 1000 (stage 2, the coordinator 2026-11-10: the provisional pristine cells under their ceilings)
    assert lo <= d0 <= hi, f"{nm} v{v} TYPE {ty}: attack {d0:+.2f} dB re no lift"
    assert abs(d1) <= 0.15, f"{nm} v{v} TYPE {ty}: 0.3..1.4 s {d1:+.2f} dB re no lift"


@pytest.mark.parametrize("cc79", (0, 110), ids=["VIBES", "MARIMBA"])
def test_bars_untouched(cc79):
    a = render(f"bar-{cc79}", CHORD, 100, cc79=cc79)
    b = render(f"bar-{cc79}", CHORD, 100, cc79=cc79, binary=NOATK)
    assert np.array_equal(a, b)
