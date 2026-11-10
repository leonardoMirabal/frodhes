# SPDX-License-Identifier: GPL-3.0-only
"""The effects' pristine gate (tools/fx_pristine.py; the user 2026-11-08: "a pristine and clean result for each efx
beside overdrive"): every effect at MIX / DEPTH 0, 64, 127 on the one-core build, the two-core one (FR_C1) and the
Move / plugin HQ build, plus the factory presets: the limiter idle on single notes (v64, v127) and a v127 4-note
chord, no clamp cut in the delay line, the reverb's inputs or the chorus line, MIX 0 at the bypass' level (0.5 dB),
no added non-linearity (superposition residual) beyond the designed saturations.

OPEN lists the cells that fail today; none (2026-11-08, fx-level: the phaser's peak trim, the wah's make-up capped by the peaks, LP / HP RESO compensated, ANA's TIME over a silent line at once, REV_HQ
HALL's quiet threshold 128). A new failure fails the test; a cell that passes again must leave OPEN (the list only
shrinks)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import fx_pristine  # noqa: E402

OPEN = set()
OPEN_HQ_REVERB = set()                                # (REV_HQ: the c1 and HQ builds)


@pytest.mark.parametrize("build", ["one", "c1", "hq"])
def test_fx_pristine(build):
    fx_pristine.make([build])
    fx_pristine.gate([build], presets=True)
    got = {f.split(" ", 1)[1] for f in fx_pristine.gate.fails}
    allowed = OPEN | (OPEN_HQ_REVERB if build != "one" else set())
    rows = "\n".join(fx_pristine.gate.rows)
    assert got <= allowed, f"{build}: new failures {sorted(got - allowed)}\n{rows}"
    assert got == allowed, f"{build}: passes now, remove from OPEN: {sorted(allowed - got)}\n{rows}"
