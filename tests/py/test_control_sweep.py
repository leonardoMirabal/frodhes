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

# Three checks are bounded differently on purpose (tools/control_sweep.py; 0.9.6):
# - filter * FREQ, trans: the range takes the renders held at the statics the cutoff glides through, for the
#   glide's first 60 ms (FREQ 127 -> 0 is 9 octaves at 1/8 octave a sub-block, 52 ms). Physical: a band-pass swept
#   across the chord is louder at the chord's band than at either end; afterwards the two ends bound it as before.
#   0.9.5's BP "burst" (+8.8 dB over the ends) was that pass in 3 ms, under every static it crossed; its #13 gate
#   is the click, 25.5x (0.9.6: 5.5x).
# - filter TYPE, jump -> xjump: the TYPEs differ by up to 23 dB on the chord, so the level must move by that much and
#   the sweep's window-step check cannot apply. xjump bounds the speed: the moved render's largest 512-sample window
#   step (beyond the held renders' own) at most 0.5 of the held renders' level difference. 0.9.5's one-sample
#   switch: 0.8 (64 > 45); the 46 ms crossfade: 0.37. (0.9.5 also flags click 82.3x and zipper 150x.)
# - delay EFFECT, no jump / trans: a switch to or from TAPE fades the running type's echoes out (46 ms), then the
#   new type's in (46 ms; TAPE has its own transport, filters and feedback law: the two cannot share one loop's state),
#   so the echoes dip ~15 dB on purpose. DIG <-> ANA crossfade in one loop (#17). The click and zipper stay checked:
#   0.9.5 fails them (14.2x, 36.6x; 0.9.6: 1.5x, 1.2x).
# case -> why it fails on 0.9.5 (fix it, then remove the line)
KNOWN = {
    "rev INF auto MIX": "INFINITE's level guard holds the tank: the wet falls 1.1 dB from MIX 96 to 127 (by design; a law change moves presets)",
    "rev INF size64 MIX": "the same (INFINITE's level guard; #18's jump and click are fixed)",
}


# 0.9.10: the chord is struck again every second (tools/control_sweep.py RESTRIKE; the engine darkens a held note on
# purpose). The checks are unchanged, and with the restruck chord 0.9.9 (main) fails these too: each one is bounded at
# about this branch's value (case -> {check: (limit, main's value under the same source, why)}). Every other check of
# the case stays at its LIMITS. A fix that brings one under LIMITS: take the line out.
BASE = {
    "vibes MALLET": {"jump": (1.6, 1.53, "a restrike at a new MALLET's attack level")},
    "vibes WEIGHT": {"stuck": (2.3, 2.25, "the bar engine's tail after the sweep (VIBES frozen)")},
    "rhodes TYPE": {"jump": (1.9, 1.83, "a TYPE step under a ringing chord")},
    "rhodes TYPE SPREAD": {"jump": (1.9, 1.83, "the same, TYPE with SPREAD")},
    # (0.9.10 zipper fixes, tests/py/test_ctl_zipper.py: TAPE TIME's zipper 2.53 -> 1.9 and PLATE SIZE's wet 3.35 -> 1.87
    # are under LIMITS, their lines out; TAPE TIME's wet 2.83 -> 2.02 bounded tighter: the echoes' own varispeed. PLATE
    # SIZE's jump 2.10 -> 0.92 with the plate's modulated reads mended and its 1 s fade: its line out)
    "delay TAPE TIME": {"zipper_wet": (2.3, 5.27, "the tape's varispeed of fresh strikes, the echoes alone")},
    # THRESHOLD (2026-11-08, the clean Rhodes 61baedac; the effects' code unchanged from 568fe9d6, which passed): the
    # same checks peak at the same instant on 0.9.9, 568fe9d6 and 61baedac (lfo RATE jump at 3.251 s: 1.86 / 1.48 /
    # 1.84; wah RATE zipper at 3.878 s: 2.04 / 1.82 / 2.10; PLATE SIZE jump at 2.740 s: 2.10 / 1.76 / 2.35, zipper_wet
    # 2.78 / 2.28 / 2.74; rev TYPE zipper_wet at 3.019 s: 1.68 / 1.58 / 2.07): the source moves them, the control does
    # the same thing. Still caught on 61baedac's own renders: a zipper (0.3 dB at every CC step) reads 9.5 / 10.1 /
    # 6.1 (wet 11.7) / 7.6 (wet 20.9); a 2.5 dB step under 1 ms reads zipper 9.1 / 2.0 / 7.6 / 8.1.
    "wah RATE": {"zipper": (2.2, 2.04, "the wah's rate swept under fresh strikes")},
}


@pytest.fixture(scope="module")
def binary():
    return hostrun.build()


@pytest.mark.parametrize("case", cs.CASES, ids=[c.name for c in cs.CASES])
def test_control(case, binary, request):
    if case.name in KNOWN:
        request.applymarker(pytest.mark.xfail(reason=KNOWN[case.name], strict=False))
    r = cs.measure(case, binary)
    base = BASE.get(case.name, {})
    bad = {k: v for k, v in cs.checks(case, r).items() if not v[2] and not (k in base and v[0] <= base[k][0])}
    assert not bad, f"{case.name}: " + ", ".join(f"{k} {v[0]} (limit {v[1]})" for k, v in bad.items())


@pytest.mark.parametrize("name", ["rev PLATE SIZE", "rev INF SIZE"])
def test_reverb_size_turns_without_bend_or_click(name, binary):
    """BUGHUNT #19 (0.9.5: PLATE's tail bent 22 cents p90 while SIZE turned, zipper 9x; one INFINITE detent clicked
    14x): SIZE turned by hand under a ringing tail cross-fades the old reads into the new (0.9.6), so the tail keeps
    its pitch (the 440 / 880 Hz partials, the wet too, within 5 cents p90 over the statics own), and no move clicks (0 -> 127, 127 -> 0, one detent
    64 -> 65: 10x at most) or steps (zipper 2x)"""
    case = next(c for c in cs.CASES if c.name == name)
    r = cs.measure(case, binary)
    assert r["pitch_wet"] <= 5.0 and r["pitch"] <= 5.0, (r["pitch"], r["pitch_wet"])   # (over the statics own)
    assert max(r["click"].values()) <= 10.0, r["click"]
    assert r["zipper"] <= 2.0, r["zipper"]


def test_the_known_list_names_cases():
    """every expected failure names a case that exists (a renamed case must not silently pass)"""
    assert set(KNOWN) <= {c.name for c in cs.CASES}
    assert set(BASE) <= {c.name for c in cs.CASES}


@pytest.mark.parametrize("t_ms", [2310, 3270, 4630])
def test_jump3_finds_a_smooth_step(t_ms, binary):
    """K20 (2026-11-09): a gain step smoothed over 50 ms spreads over 2-3 of the jump check's 23 ms windows (wah RATE,
    +3 dB: jump 1.27 / 1.68 / 1.29 at these instants, two under its 1.5). jump3 (the step over 3 windows against the renders held near the sweep's values) reads wah RATE's
    clean sweep under 1.5 dB and the same sweep with a +3 dB step injected over 50 ms (raised cosine) over it"""
    import numpy as np
    case = cs.BY_NAME["wah RATE"]
    held = {v: cs.render(cs.script(case, value=v), binary) for v in cs.NEAR}
    sw = cs.render(cs.script(case, sweep=True), binary)
    assert cs.jump3_db(sw, held) <= cs.LIMITS["jump3"]
    i0, n = cs.ms(t_ms), cs.ms(50)
    g = np.ones(len(sw))
    g[i0:i0 + n] = 1 + (10 ** (3 / 20) - 1) * 0.5 * (1 - np.cos(np.pi * np.arange(n) / n))
    g[i0 + n:] = 10 ** (3 / 20)
    assert cs.jump3_db(sw * g[:, None], held) > cs.LIMITS["jump3"]
