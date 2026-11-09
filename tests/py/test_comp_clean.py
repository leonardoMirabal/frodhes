# SPDX-License-Identifier: GPL-3.0-only
"""The compressor is clean at the factory settings (2026-11-08). FX off keeps the compressor (FR_BYP_KEEP_CMP: the
user wants it always on), so it must not dirty the piano: with F01's compressor (THR 50, 2:1, ATK 80, REL 72, make-up
8) the v127 chords' intermodulation (tools/dry_dist.py's chords: the energy off every harmonic band of the four notes
re their fundamentals) was -46 .. -56 dB, the dry engine's -76 .. -86: the detector released between a note's
waveform peaks (its ripple at twice the f0 moved the gain) and the gain moved in 0.023 dB table steps. fx.c now holds
the detector across a cycle (CMP_HOLD), smooths the reduction's release (CMP_GSH) and interpolates its log2 / exp2:
-66.4 dB at worst. Each chord within DRY_TOL of the dry engine's and under ABS_LIM, on every electric-piano TYPE."""
import os
import sys

import pytest

import hostrun

sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
import dry_dist  # noqa: E402

ABS_LIM = -62.0          # dB (dry_dist's floor "never heard" is -60)
DRY_TOL = 15.0           # dB over the dry engine's own (measured: at most +14.6, SUIT's mid chord, -66.4 against -81.0, at
                         # CMP_HOLD 256: 1024 read +12.1 but held a loud chord's reduction 0.45 dB longer)


def chords(binary, type_cc, comp, outdir):
    ls = [l for l in dry_dist.script(111, type_cc, 0) if " 106 0" not in l]   # FXOFF, COMP as F01 has it
    if not comp:
        ls.append("105 midi 0xB0 106 0")
    x = dry_dist.render(binary, f"cc-{type_cc}-{int(comp)}", ls, outdir)
    return {k: v["imd"] for k, v in dry_dist.measure_wave(x).items() if k.startswith("chord/")}


@pytest.mark.parametrize("type_cc", [t for _, t in dry_dist.TYPES], ids=[n for n, _ in dry_dist.TYPES])
def test_comp_clean(type_cc):
    out = os.path.join(hostrun.OUT, "comp_clean")
    on, off = chords(hostrun.BIN, type_cc, True, out), chords(hostrun.BIN, type_cc, False, out)
    for k in on:
        assert on[k] <= ABS_LIM, f"{k}: imd {on[k]:.1f} dB with F01's compressor (limit {ABS_LIM}; dry {off[k]:.1f})"
        assert on[k] <= off[k] + DRY_TOL, f"{k}: imd {on[k]:.1f} dB, the dry engine's {off[k]:.1f} + {DRY_TOL}"
