# SPDX-License-Identifier: GPL-3.0-only
"""The effects' headroom gate at the loudest normal playing (0.9.13, the audio review C4 / D5): a v127 8-note chord
(C2 .. G4) into every delay at MIX 127 and FEEDBACK 110, every reverb at MIX 127 (DECAY 127 too), the touch wah, the
chorus and the phaser at DEPTH 127, and the compressor at its hardest (THRESHOLD -40 dB, RATIO 20:1) with MAKEUP up to
MAKEUP_OK, on the one-core build, the two-core one (FR_C1) and the Move / plugin HQ build: no int16 clamp cuts the
signal anywhere (fx.c fx_h_clip: the delay line, the reverb's send, kernel inputs and every int16 ring of HALL, PLATE
and INFINITE, classic and full-rate, the chorus line, the wah's input). tools/fx_pristine.py checks the same counters on
a 4-note chord at MIX 0 / 64 / 127.

Above MAKEUP_OK the clamps start (measured 2026-11-16, COMP at its hardest, the review's dly_hot / rev_hot scripts):
the two-core DIG delay at MAKEUP 48 (+9 dB: 3 samples; 329 at +24 dB, the peak 3.6x full scale), the full-rate HALL's
send at MAKEUP 64 (+12 dB); the wah's input clip by MAKEUP 127 (26 samples). There the dry signal is itself over full
scale: the output limiter already pulls the gain under 1 on 1359 of 2058 sub-blocks at +9 dB. Soft knees would not
help: a knee has to start under full scale, so it would change every render at normal levels (pristine: no), and it
still has to fold the same 11 dB over. Kept as hard clamps, counted, gated here."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import fx_pristine  # noqa: E402

CHORD8 = (36, 43, 48, 52, 55, 60, 64, 67)
MAKEUP_OK = 32                        # CC 113: +6 dB (0 .. 127 = 0 .. +24 dB); 40 clean too, 48 clamps (two-core DIG)
COMP_MAX = [(106, 127), (107, 127), (112, 127)]
CELLS = {
    "delay-dig": [(88, 40), (89, 127), (26, 110)],
    "delay-ana": [(88, 70), (89, 127), (26, 110)],
    "delay-tape": [(88, 127), (89, 127), (26, 110)],
    "hall": [(95, 40), (91, 127), (92, 127)],
    "plate": [(95, 70), (91, 127), (92, 127)],
    "inf": [(95, 127), (91, 127)],
    "wah": [(17, 127), (18, 127)],
    "chorus": [(85, 64), (86, 127)],
    "phaser": [(85, 127), (13, 127), (16, 127)],
    "comp-dig": COMP_MAX + [(113, MAKEUP_OK), (88, 40), (89, 127), (26, 110)],
    "comp-hall": COMP_MAX + [(113, MAKEUP_OK), (95, 40), (91, 127)],
    "comp-inf": COMP_MAX + [(113, MAKEUP_OK), (95, 127), (91, 127)],
    "comp-wah": COMP_MAX + [(113, MAKEUP_OK), (17, 127), (18, 127)],
}
NAMES = ("delay", "reverb", "chorus", "limiter", "wah")


@pytest.mark.parametrize("build", ["one", "c1", "hq"])
def test_no_clamp_at_full_level(build):
    fx_pristine.make([build])
    bad = []
    for name, ccs in CELLS.items():
        tag, _, _, cnt = fx_pristine.render((build, "clip-" + name, 0, fx_pristine.CLEAN + ccs, False,
                                             [(500, n, 127) for n in CHORD8]))
        assert cnt[3] >= 0, f"{build}: a build without the fx-clip counters"
        cut = {NAMES[i]: cnt[i] for i in (0, 1, 2, 4) if cnt[i]}
        if cut:
            bad.append(f"{name}: {cut}")
    assert not bad, f"{build}: clamps at a normal setting: " + "; ".join(bad)
