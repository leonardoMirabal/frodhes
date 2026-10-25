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

# case -> why it fails on 0.9.5 (fix it, then remove the line)
KNOWN = {
    "rhodes TYPE": "BUGHUNT #12: a Rhodes TYPE change swaps the pickup / body filters under the ringing notes",
    "drive FDRV AMOUNT": "BUGHUNT #5: AMOUNT steps per block, AMOUNT 0 cuts to dry",
    "drive KHLON AMOUNT": "BUGHUNT #5",
    "drive TSH8 AMOUNT": "BUGHUNT #5",
    "drive OHCD AMOUNT": "BUGHUNT #5 (the zipper)",
    "drive BHLUE AMOUNT": "BUGHUNT #5",
    "filter BP FREQ": "BUGHUNT #13: a cutoff jump on the band-pass clicks and rings over",
    "filter TYPE": "BUGHUNT #4: a FILTER TYPE change switches the mode in one sample",
    "wah RATE": "BUGHUNT #14: the wah's resonance (RATE) is not smoothed",
    "wah SHAPE": "BUGHUNT #15: SHAPE does nothing on WAH (the card is shown)",
    "trem/wah EFFECT": "BUGHUNT #7: TREM <-> WAH switches in one sample",
    "phaser FDBK": "BUGHUNT #16: the phaser's feedback is not smoothed",
    "chorus DEPTH": "mix-fix: the chorus DEPTH zipper",
    "cho/pha EFFECT": "BUGHUNT #2: phaser / chorus on and off switch in one sample",
    "delay DIG MIX": "mix-fix: the delay's wet gain per block",
    "delay ANA MIX": "mix-fix",
    "delay TAPE MIX": "mix-fix",
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
    "LEVEL": "BUGHUNT #9: LEVEL steps per block",
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
