# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's tail (plugin/src/frodhes_clap.c tail_ms): a host that stops rendering at the tail the plugin reports
cuts nothing. plugin/tests/clap_render --tail-check plays a 6-note fortissimo chord with the settings below, reads
the tail after the last event and renders 20 s past it: no sample (16-bit) may sound after the tail. The settings
that hold the sound forever (INFINITE reverb at SUSTAIN / FREEZE, ANA and TAPE echoes that build up) must report an
infinite tail. The HQ sound (the plugin's only one, 24 voices), the worst corners measured for the tables, and seeded
random settings. Skipped when the
plugin is not built."""
import os
import random
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD = os.environ.get("FRP_BUILD", os.path.join(ROOT, "build", "plugin"))
RENDER = os.path.join(BUILD, "frp_clap_render")
CLAP = os.path.join(BUILD, "bundles", "Frodhes.clap")
pytestmark = pytest.mark.skipif(not os.path.exists(RENDER) or not os.path.exists(CLAP),
                                reason="the plugin is not built (make plugin)")

# params_map.h ids
TYPE, DECAY, RELEASE, RV_MIX, RV_DECAY, RV_PREDLY, RV_TYPE, RV_SIZE = 0, 24, 25, 19, 51, 53, 59, 60
DL_SW, DL_MIX, DL_TIME, DL_SYNC, DL_FB, DL_TONE, CP_FB, PH_FB = 13, 14, 15, 16, 17, 18, 12, 32
BYPASS = 0x10004

CASES = [
    ("defaults", {}),
    ("release 127", {RELEASE: 127}),
    ("vibes, ring 127", {TYPE: 4, RELEASE: 0, DECAY: 127}),
    ("hall 127", {RV_TYPE: 1, RV_MIX: 127, RV_DECAY: 127, RV_SIZE: 127, RV_PREDLY: 127, DL_SW: 0}),
    ("plate 127", {RV_TYPE: 2, RV_MIX: 127, RV_DECAY: 127, DL_SW: 0}),
    ("infinite 119", {RV_TYPE: 3, RV_MIX: 127, RV_DECAY: 119, DL_SW: 0}),
    ("dig fb 110", {RV_TYPE: 0, DL_SW: 1, DL_MIX: 127, DL_TIME: 127, DL_FB: 110}),
    ("dig fb 127", {RV_TYPE: 0, DL_SW: 1, DL_MIX: 127, DL_TIME: 127, DL_FB: 127}),
    ("ana fb 114", {RV_TYPE: 0, DL_SW: 2, DL_MIX: 127, DL_TIME: 127, DL_FB: 114}),
    ("tape fb 113", {RV_TYPE: 0, DL_SW: 3, DL_MIX: 127, DL_TIME: 127, DL_FB: 113}),
    ("dig fb 64, short time", {RV_TYPE: 0, DL_SW: 1, DL_MIX: 127, DL_TIME: 40, DL_FB: 64}),
    ("dig synced 2 bars", {RV_TYPE: 0, DL_SW: 1, DL_MIX: 127, DL_SYNC: 13, DL_FB: 90}),
    ("tape 110 into hall 127", {RV_TYPE: 1, RV_MIX: 127, RV_DECAY: 127, DL_SW: 3, DL_MIX: 127, DL_TIME: 127, DL_FB: 110}),
    ("bypass over hall", {RV_TYPE: 1, RV_MIX: 127, RV_DECAY: 127, BYPASS: 1}),
]
INFINITE = [
    ("infinite sustain", {RV_TYPE: 3, RV_MIX: 127, RV_DECAY: 120}),
    ("infinite freeze", {RV_TYPE: 3, RV_MIX: 127, RV_DECAY: 127}),
    ("ana fb 115", {DL_SW: 2, DL_MIX: 127, DL_FB: 115}),
    ("tape fb 114", {DL_SW: 3, DL_MIX: 127, DL_FB: 114}),
]


def expect_infinite(p):
    rv = p.get(RV_TYPE, 1) == 3 and p.get(RV_MIX, 1) > 0 and p.get(RV_DECAY, 64) >= 120
    dl = p.get(DL_MIX, 0) > 0 and ((p.get(DL_SW, 0) == 2 and p.get(DL_FB, 90) >= 115) or
                                   (p.get(DL_SW, 0) == 3 and p.get(DL_FB, 90) >= 114))
    return (rv or dl) and not p.get(BYPASS, 0)


def randoms(n, seed):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        p = {TYPE: rnd.randrange(6), RELEASE: rnd.randrange(128), DECAY: rnd.randrange(128),
             RV_TYPE: rnd.randrange(4), RV_MIX: rnd.choice([0, 40, 127]), RV_DECAY: rnd.randrange(120),
             RV_SIZE: rnd.randrange(128), RV_PREDLY: rnd.randrange(128),
             DL_SW: rnd.randrange(4), DL_MIX: rnd.choice([0, 64, 127]), DL_TIME: rnd.randrange(128),
             DL_SYNC: rnd.choice([0, 0, rnd.randrange(14)]), DL_FB: rnd.randrange(114), DL_TONE: rnd.randrange(128),
             CP_FB: rnd.randrange(128), PH_FB: rnd.randrange(128)}
        out.append((f"random {seed}.{i}", p))
    return out


def tail_check(tmp_path, name, params, engine, want, extra=()):
    lines = [f"10 param {i} {v}" for i, v in params.items()]
    # the chord 1 s after the settings: the plugin tells a changed tail once it held for two checks (64 blocks)
    lines += [f"48100 midi 0x90 {k} 127" for k in (36, 48, 60, 67, 76, 84)]
    lines += [f"48600 midi 0x80 {k} 0" for k in (36, 48, 60, 67, 76, 84)]
    script = tmp_path / "tail.txt"
    script.write_text("\n".join(lines) + "\n")
    r = subprocess.run([RENDER, CLAP, str(script), str(tmp_path / "x.wav"), "--engine", engine, "--tail-check", want, *extra],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"{name} ({engine}): {r.stdout[-1500:]}"
    return r.stdout


@pytest.mark.parametrize("engine", ["hq"])
@pytest.mark.parametrize("name,params", CASES + randoms(6, 1) + randoms(6, 2))
def test_tail_not_cut(tmp_path, engine, name, params):
    want = "infinite" if expect_infinite(params) else "finite"
    out = tail_check(tmp_path, name, params, engine, want)
    if want == "finite" and params:
        assert "0 tail changes told" not in out, f"{name}: the host was not told the tail changed"


@pytest.mark.parametrize("name,params", INFINITE)
def test_tail_infinite(tmp_path, name, params):
    tail_check(tmp_path, name, params, "hq", "infinite")


@pytest.mark.parametrize("name,params", [CASES[3], CASES[12]])
def test_tail_told_on_main_thread_under_the_wrapper(tmp_path, name, params):
    """As clap-wrapper's host (the VST3 / Audio Unit), the change is told from the main thread (its restartComponent and
    property change are main-thread calls); clap_render fails on a call in process."""
    out = tail_check(tmp_path, name, params, "hq", "finite", ("--host-vendor", "wrapper"))
    assert "0 tail changes told" not in out, out
