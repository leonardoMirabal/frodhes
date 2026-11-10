# SPDX-License-Identifier: GPL-3.0-only
"""The chorus level-matched to the bypass by the ear's measures, not only the chord's LUFS (2026-11-09: the user on
rc3's jack heard CHORUS on much louder than FX off). The chorus mixes in only the tap's part above ~220 Hz (its comb),
but its make-up multiplied the whole output, so a note under ~220 Hz passed unchanged plus the make-up: C2 +0.7 ..
+1.3 LUFS and up to +2.4 dB critical-band loudness (tools/cb_loud.py) on every engine, while the 5-note chord the make-up
was fitted on read -0.2 LU. fx.c now leaves the output's low part out of the make-up (cho_tap_k) and CHO_MKF /
CHO_HMK are refitted on the critical-band loudness.

Per engine (Rhodes ST77, VIBES, MARIMBA) and DEPTH 40 / 64 / 127 (the presets use 30 .. 70), from every effect off,
CHORUS on vs the FX button's bypass (tools/fx_level_audit.py's cells):
  bass     C2 v80: LUFS within +-0.5 dB, the binaural critical-band loudness within +-0.8 (rc3: +0.7 .. +1.3 / +0.7 ..
           +1.4; with the fix MARIMBA's C2 +0.75 at DEPTH 127: its overtones from ~260 Hz are in the comb)
  louder   C4 v80 and a 4-note chord v80: never over +0.75 dB in LUFS, the binaural and the mono sum's critical-band
           loudness (the ear's "louder"); quieter is the comb's loss on a single partial, not checked here"""
import os
import sys

import pytest

import hostrun

sys.path.insert(0, os.path.join(hostrun.ROOT, "tools"))
import fx_level_audit as A  # noqa: E402

DEPTHS = (40, 64, 127)


@pytest.fixture(scope="module")
def cells():
    jobs = []
    for e in A.ENGINES:
        for s in ("C2v80", "C4v80", "chd80"):
            jobs.append((hostrun.BIN, f"ear|{e}|{s}|byp", 0, A.CLEAN + A.ENGINES[e], 1, A.SIGNALS[s]))
            for d in DEPTHS:
                jobs.append((hostrun.BIN, f"ear|{e}|{s}|{d}", 0, A.CLEAN + A.ENGINES[e] + [(85, 64), (86, d)], 0,
                             A.SIGNALS[s]))
    return A.run_jobs(jobs, int(os.environ.get("PYTEST_N", "2") or 2))


def delta(r, e, s, d, k):
    return r[f"ear|{e}|{s}|{d}"][k] - r[f"ear|{e}|{s}|byp"][k]


@pytest.mark.parametrize("e", list(A.ENGINES))
def test_chorus_bass_matched(cells, e):
    for d in DEPTHS:
        for k, lim in (("lufs", 0.5), ("cb", 0.8)):
            v = delta(cells, e, "C2v80", d, k)
            assert abs(v) <= lim, f"{e} chorus DEPTH {d}: C2 {k} {v:+.2f} dB re bypass (the make-up on the bass)"


@pytest.mark.parametrize("e", list(A.ENGINES))
def test_chorus_never_louder(cells, e):
    for d in DEPTHS:
        for s in ("C4v80", "chd80"):
            for k in ("lufs", "cb", "cb_m"):
                v = delta(cells, e, s, d, k)
                assert v <= 0.75, f"{e} chorus DEPTH {d}: {s} {k} {v:+.2f} dB re bypass"
