# SPDX-License-Identifier: GPL-3.0-only
"""The monkey (tools/monkey.py) on fixed seeds: 20 s of everything a player or a DAW can send, then the panic, the
probes and a SAVE + power cycle; every invariant must hold (crash / sanitizer, race, peak, wrap, rail, stuck,
silence, probe, roundtrip) and the differential click check on a few events finds no click that is not already a
finding. First, the detectors themselves: each one fails on an injected fault (a wrap, a rail, a stuck voice ...),
and the click check finds the FILTER TYPE click 0.9.5 has (BUGHUNT #4).

Seeds that fail on 0.9.5 are expected failures with their finding (private/BUGHUNT-095.md); strict=False, so a fix
makes them XPASS (then take the line out). About 1.5 s a seed (15 s sanitized); the whole file under a minute with
-n 2. The long run: python3 tools/monkey.py --minutes 30 -j 2."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import control_sweep as cs  # noqa: E402
import monkey as mk  # noqa: E402

SEEDS = list(range(24))
SAN_SEEDS = [0, 1]
# seed -> why it fails on 0.9.5 (fix it, then remove the line)
KNOWN = {22: "BUGHUNT #21: a steal of a voice that dies in its fade loses the new note (rhodes.c:977)"}


@pytest.fixture(scope="module")
def binary():
    return mk.build()


@pytest.fixture(scope="module")
def clean(binary):
    """seed 0's run (it passes): the base the detectors' self-tests inject their faults into"""
    pl, marks = mk.panic(mk.RAND_MS + 50)
    r = mk.run(mk.script_text(mk.gen(0), pl, marks["end"]), binary)
    assert mk.analyse(r, marks) == []
    return r, marks


def _copy(r):
    c = mk.Res(r.code, r.out, r.x.copy())
    c.dumps = {t: dict(d) for t, d in r.dumps.items()}
    return c


def _kinds(r, marks):
    return {k for k, _ in mk.analyse(r, marks)}


def test_the_stream_is_seeded():
    assert mk.gen(5) == mk.gen(5) and mk.gen(5) != mk.gen(6)
    assert all(0 < t <= mk.RAND_MS for t, _ in mk.gen(5))


@pytest.mark.parametrize("fault", ["wrap", "rail", "peak", "silence", "stuck", "dead", "louder", "race", "san",
                                   "expect", "crash"])
def test_each_detector_fails_on_its_fault(clean, fault):
    r0, m = clean
    r = _copy(r0)
    t = mk.ms(5000)
    want = fault
    if fault == "wrap":
        r.x[t, 0], r.x[t + 1, 0] = 30000, -30000
    elif fault == "rail":
        r.x[t:t + 40, 1] = 32767
        want = "rail"
    elif fault == "peak":
        r.x[t, 0] = 26000
    elif fault == "silence":
        r.x[mk.ms(m["quiet"][0] + 500), 0] = 1
    elif fault == "stuck":
        r.dumps[m["gone"]]["held"] = "1"
    elif fault == "dead":
        n0, rel = m["note"]
        r.x[mk.ms(n0 - 50):mk.ms(rel + 400)] = 0
        want = "probe"
    elif fault == "louder":
        n0, rel = m["note"]
        r.x[mk.ms(rel + 50):mk.ms(rel + 60)] = 20000
        want = "probe"
    elif fault == "race":
        r.out += "\n  9000 ms  RACE STUCK voice 3 note 60\n"
    elif fault == "san":
        r.out += "\nfirmware/src/fx.c:123:9: runtime error: signed integer overflow\n"
        want = "crash"
    elif fault == "expect":
        r.out += "\n 40050 ms  FAIL expect preset: U01 differs in p.3\n"
        r.code = 1
        want = "roundtrip"
    elif fault == "crash":
        r.code = -11
    assert want in _kinds(r, m)


def test_the_click_check_finds_a_known_click(binary):
    """the sweep's dry chord (every effect off, the filter at FREQ 80 RESO 40), then FILTER TYPE LP12 -> HP12 (CC 70)
    while it rings: 0.9.5 switches the mode in one sample (BUGHUNT #4, 62x here); the check must see it and name it"""
    ev = [(50, f"midi 0xB0 {c} {v}") for c, v in cs.BASE] + [(150, "midi 0xB0 74 80"), (150, "midi 0xB0 71 40"),
                                                              (200, "midi 0xB0 70 30"), (300, "midi 0xB0 10 127"),
                                                              (500, "midi 0xB0 64 127")]
    ev += [(500, f"midi 0x90 {n} 100") for n in cs.CHORD] + [(2000, "midi 0xB0 70 50")]
    r = mk.click_one(ev, len(ev) - 1, binary)
    assert r is not None
    ratio, changed, ring = r
    assert ring > -40 and changed == ["P_FL_TYPE"] and mk.known_click(changed) == "#4"
    if ratio <= 10:
        pytest.xfail(f"FILTER TYPE no longer clicks ({ratio:.1f}x): BUGHUNT #4 fixed; pick another known click")


def test_ddmin_finds_the_minimal_pair():
    items = list(range(40))
    assert sorted(mk.ddmin(items, lambda xs: 7 in xs and 31 in xs)) == [7, 31]


def _seed(seed, binary, request):
    if seed in KNOWN:
        request.applymarker(pytest.mark.xfail(reason=KNOWN[seed], strict=False))
    r = mk.one(seed, binary, k_clicks=3, shrink=False)
    assert not r["fail"], f"seed {seed}: " + "; ".join(f"{k}: {m}" for k, m in r["fail"]) + \
        f"  (replay and shrink: python3 tools/monkey.py --seeds {seed})"


@pytest.mark.parametrize("seed", SEEDS)
def test_seed(seed, binary, request):
    _seed(seed, binary, request)


@pytest.mark.parametrize("seed", SAN_SEEDS)
def test_seed_sanitized(seed, request):
    _seed(seed, mk.build(san=True), request)
