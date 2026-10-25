# SPDX-License-Identifier: GPL-3.0-only
"""Every control, swept and moved while a pedalled chord rings (tools/control_sweep.py): no zipper, no click (the
DIFFERENTIAL detector: the moved render against the renders held at the start and the end value), no level jump, no
pitch drift, no dropout or burst after a move, no control that does nothing, none that leaves a state behind.

The cases that fail on 0.9.5 are expected failures, each with its finding (private/BUGHUNT-095.md, or the branch that
fixes it); strict=False, so a fix makes them XPASS (then take the line out). The renders are cached by the binary's
hash (build/host/ctl_sweep/): about a minute cold for all 115 cases."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import control_sweep as cs  # noqa: E402

import hostrun  # noqa: E402

# Two checks are bounded differently on purpose (tools/control_sweep.py; 0.9.6):
# - filter * FREQ, trans: the range takes the renders held at the statics the cutoff glides through, for the
#   glide's first 60 ms (FREQ 127 -> 0 is 9 octaves at 1/8 octave a sub-block, 52 ms). Physical: a band-pass swept
#   across the chord is louder at the chord's band than at either end; afterwards the two ends bound it as before.
#   0.9.5's BP "burst" (+8.8 dB over the ends) was that pass in 3 ms, under every static it crossed; its #13 gate
#   is the click, 25.5x (0.9.6: 5.5x).
# - filter TYPE, jump -> xjump: the TYPEs differ by up to 23 dB on the chord, so the level must move by that much and
#   the sweep's window-step check cannot apply. xjump bounds the speed: the moved render's largest 512-sample window
#   step (beyond the held renders' own) at most 0.5 of the held renders' level difference. 0.9.5's one-sample
#   switch: 0.8 (64 > 45); the 46 ms crossfade: 0.37. (0.9.5 also flags click 82.3x and zipper 150x.)
# case -> why it fails on 0.9.5 (fix it, then remove the line)
KNOWN = {
    "drive FDRV AMOUNT": "BUGHUNT #5: AMOUNT steps per block, AMOUNT 0 cuts to dry",
    "drive KHLON AMOUNT": "BUGHUNT #5",
    "wah RATE": "BUGHUNT #14: the wah's resonance (RATE) is not smoothed",
    "wah SHAPE": "BUGHUNT #15: SHAPE does nothing on WAH (the card is shown)",
    "trem/wah EFFECT": "BUGHUNT #7: TREM <-> WAH switches in one sample",
    "delay DIG TIME": "BUGHUNT #3: DIG's time glides at one sample a sample (the read head stops)",
    "delay ANA TIME": "BUGHUNT #3: ANA scrubs at many times speed",
    "delay EFFECT": "BUGHUNT #17: DIG <-> ANA switches the tone and softclip in one sample",
    "rev HALL auto MIX": "BUGHUNT #18: MIX to 0 cuts the send and lifts the ringing tail +6 dB",
    "rev HALL size64 MIX": "BUGHUNT #18",
    "rev PLATE auto MIX": "inf-size (the plate's AUTO SIZE) and BUGHUNT #18",
    "rev PLATE size64 MIX": "BUGHUNT #18",
    "rev INF auto MIX": "inf-size (INFINITE's AUTO SIZE) and BUGHUNT #18",
    "rev INF size64 MIX": "BUGHUNT #18 (and the wet falls 1 dB from MIX 96 to 127)",
    "rev PLATE SIZE": "BUGHUNT #19: SIZE bends a ringing tail's pitch and steps",
    "rev INF SIZE": "BUGHUNT #19: one SIZE detent clicks on INFINITE",
    "rev HALL MOD": "BUGHUNT #10: MOD jumps the line taps",
    "rev PLATE MOD": "BUGHUNT #10",
    "rev INF MOD": "BUGHUNT #10",
    "MASTER": "BUGHUNT #20: MASTER (platform.c) steps once per block (the model of that code)",
}


@pytest.fixture(scope="module")
def binary():
    return hostrun.build()


@pytest.mark.parametrize("case", cs.CASES, ids=[c.name for c in cs.CASES])
def test_control(case, binary, request):
    if case.name in KNOWN:
        request.applymarker(pytest.mark.xfail(reason=KNOWN[case.name], strict=False))
    r = cs.measure(case, binary)
    bad = {k: v for k, v in cs.checks(case, r).items() if not v[2]}
    assert not bad, f"{case.name}: " + ", ".join(f"{k} {v[0]} (limit {v[1]})" for k, v in bad.items())


def test_the_known_list_names_cases():
    """every expected failure names a case that exists (a renamed case must not silently pass)"""
    assert set(KNOWN) <= {c.name for c in cs.CASES}
