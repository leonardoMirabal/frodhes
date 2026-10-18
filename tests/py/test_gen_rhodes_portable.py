# SPDX-License-Identifier: GPL-3.0-only
"""tools/gen_rhodes.py builds the committed rhodes_tables.h on every machine (tools/rhodes_pins.py): the FE pins are
current, they are the model's exact values to the float solver's accuracy (60-digit decimal recomputation), this
machine's own FE agrees with them, and the generator run with them reproduces the header byte for byte."""
import hashlib
import json
import os
import platform
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_rhodes as g    # noqa: E402
import rhodes_pins as rp  # noqa: E402

P = g.load_params()
PINS = rp.load(P)
KEYS = range(g.KEY_LO, g.KEY_HI + 1)
# float FE vs exact, worst seen (Accelerate and OpenBLAS alike): ms 1.8e-6, f 1.1e-7, shapes 3.4e-6
TOL = dict(ms=1e-5, f=1e-6, psi=2e-5)


def test_pins_are_current():
    assert PINS is not None, "rhodes_pins.json missing or stale: python3 tools/gen_rhodes.py --repin"
    assert sorted(PINS) == list(KEYS)
    Ls = g.tine_lengths()
    for n in KEYS:
        assert abs(PINS[n]["L"] / Ls[n] - 1) < 1e-12, n


def close(pin, ref, n):
    if pin["ms"] is None or ref["ms"] is None:
        assert pin["ms"] is None and ref["ms"] is None, n
    else:
        assert abs(float(ref["ms"]) / pin["ms"] - 1) < TOL["ms"], (n, pin["ms"], ref["ms"])
    for m in range(rp.NMODE):
        assert abs(float(ref["f"][m]) / pin["f"][m] - 1) < TOL["f"], (n, m, pin["f"][m], ref["f"][m])
        for k in ("psi_c", "psi_t"):          # a mode shape's sign is arbitrary; |shape| and the products are not
            a, b = pin[k][m], float(ref[k][m])
            assert abs(abs(b) / abs(a) - 1) < TOL["psi"], (n, k, m, a, b)
        assert pin["psi_c"][m] * pin["psi_t"][m] * float(ref["psi_c"][m] * ref["psi_t"][m]) > 0, (n, m)


@pytest.mark.parametrize("n", KEYS)
def test_pins_are_the_exact_model(n):
    close(PINS[n], rp.exact(P, n, PINS[n]), n)


def test_this_machines_fe_agrees():
    Ls = g.tine_lengths()
    for n in KEYS:
        close(PINS[n], g.fe_key(P, n, Ls[n]), n)


def test_header_reproduces_byte_for_byte():
    spec, ft = g.build()
    hdr = g.emit(spec, ft)
    want = open(os.path.join(ROOT, "firmware", "gen", "rhodes_tables.h")).read()
    assert hashlib.sha256(hdr.encode()).hexdigest() == hashlib.sha256(want.encode()).hexdigest()
    # rhodes_spec.json (analysis data, not the tables) matches too on the reference platform; elsewhere its floats
    # may differ in the last ulps (tine lengths extrapolated with np.log / np.exp, the T60 interpolation)
    js = json.dumps(spec, indent=1, sort_keys=True, default=float) + "\n"
    ref = open(os.path.join(ROOT, "docs", "model", "data", "rhodes_spec.json")).read()
    if (sys.platform, platform.machine()) != ("darwin", "arm64"):
        pytest.skip("rhodes_spec.json is compared on macOS arm64 only")
    assert js == ref
