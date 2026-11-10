# SPDX-License-Identifier: GPL-3.0-only
"""The dry width (stage 2, 2026-11-10; fx.c fx_width): the plain piano gets a side wall's first reflection (its band
570 Hz .. 4 kHz, 10 ms late, L = M + S, R = M - S), so it is not a mono point on headphones. Every effect off (CLEAN)
and with the FX button off: side / mid of a v80 chord about -27 dB (none before), of C4 about -22 dB; the mono sum by
critical band (tools/cb_loud.py) the engine's own (RAW, CC 119: no width) within 0.3 dB, the two ears' loudness
(binaural, no crosstalk: headphones) that of the mono sum played to both within 0.1 cb, the v127 chord's peak within
0.05 dB of the mono sum's, and no modulation: the side's level steady while a chord is held"""
import os
import sys

import numpy as np
import pytest

import hostrun

sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
import cb_loud as cb  # noqa: E402
import fx_level_audit as A  # noqa: E402

S = A.SIGNALS


def render(tag, ccs, byp, notes):
    A.OUT = os.path.join(hostrun.OUT, "dry_width")
    _, L, R = A.render((hostrun.BIN, tag, 0, ccs, byp, notes))
    a, e = int(A.T_ON * A.FS / 1000), int(A.T_OFF * A.FS / 1000)
    return L[a:e], R[a:e]


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-20)


@pytest.mark.parametrize("byp", [0, 1], ids=["clean", "fxoff"])
@pytest.mark.parametrize("sig,lo,hi", [("chd80", -30.0, -24.0), ("C4v80", -25.0, -19.0), ("chd127", -30.0, -24.0)])
def test_dry_width(byp, sig, lo, hi):
    L, R = render(f"w-{sig}-{byp}", A.CLEAN, byp, S[sig])
    m, s = (L + R) / 2, (L - R) / 2
    w = db(s) - db(m)
    assert lo <= w <= hi, f"{sig}: side / mid {w:.1f} dB (want {lo} .. {hi})"
    # the mono sum by critical band against the engine's own (RAW: no effect, no width, no LEVEL / limiter)
    rl, rr = render(f"w-{sig}-raw", [(119, 127)], 0, S[sig])
    bm, br = np.asarray(cb.bands(m)), np.asarray(cb.bands((rl + rr) / 2))
    ok = br > br.max() * 1e-4                          # the bands within 40 dB of the loudest
    d = 10 * np.log10(bm[ok] / br[ok])
    assert np.abs(d - np.median(d)).max() <= 0.3, f"{sig}: the mono sum by band {np.round(d - np.median(d), 2)}"
    # the two ears (headphones, no crosstalk) against the mono sum to both
    sl, sr, sm = A.spec_loud(cb.bands(L)), A.spec_loud(cb.bands(R)), A.spec_loud(cb.bands(m))
    dl = A.ph(sl.sum() + sr.sum()) - A.ph(2 * sm.sum())
    assert abs(dl) <= 0.1, f"{sig}: binaural loudness {dl:+.2f} cb re the mono sum"
    if sig == "chd127":
        dp = 20 * np.log10(max(np.abs(L).max(), np.abs(R).max()) / np.abs(m).max())
        assert dp <= 0.05, f"{sig}: peak {dp:+.2f} dB over the mono sum's"
    # no modulation: the side's level in 100 ms frames from 0.3 s follows the mid's (their ratio within 1.5 dB)
    f = int(0.1 * A.FS)
    r = [db(s[i:i + f]) - db(m[i:i + f]) for i in range(int(0.3 * A.FS), len(m) - f, f)]
    assert max(r) - min(r) <= 1.5, f"{sig}: the side / mid ratio moves {max(r) - min(r):.2f} dB while held"
