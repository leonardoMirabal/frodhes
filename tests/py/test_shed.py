# SPDX-License-Identifier: GPL-3.0-only
"""The overload's voice shed (rhodes.c rh_shed, bars.c bar_shed; the FM-1's audio ISR asks for it when two blocks run
over 5 ms, frodhes.c app_audio), which the host's tests never reached (tools/coverage.sh, 2026-10-26): "T shed" asks
for it on the host. On every engine, with 10 voices under the pedal: one voice fades out (the quietest, a released
one first), the others play on, and the fade does not click (tools/monkey.py's differential click: the render with
the shed against the one without). A shed with nothing sounding does nothing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import monkey as M  # noqa: E402
from hostrun import run  # noqa: E402

ENGINES = {"rhodes": [], "vibes": [(10, "midi 0xB0 79 0"), (11, "midi 0xB0 104 0")], "marimba": [(10, "midi 0xB0 79 127")]}
NOTES = [(48, 30), (52, 100), (55, 64), (60, 110), (62, 20), (64, 90), (67, 70), (71, 50), (74, 120), (77, 40)]


def stream(eng, shed_at=560):
    ev = list(ENGINES[eng]) + [(200, "midi 0xB0 64 127")]
    ev += [(300 + 20 * k, f"midi 0x90 {n} {v}") for k, (n, v) in enumerate(NOTES)]
    ev += [(500, f"midi 0x80 {n} 0") for n, _ in NOTES[:3]]       # (3 let go, the pedal holds them: shed first)
    ev.append((shed_at, "shed"))
    return ev


def voices(r, t):
    return int(r.state(t)["voices"])                        # (the running engine's voices sounding)


@pytest.mark.parametrize("eng", list(ENGINES))
def test_a_shed_fades_one_voice_without_a_click(eng):
    ev = stream(eng)
    lines = [f"{t} {s}" for t, s in ev] + ["550 dump", "620 dump"]
    with_shed = run(f"shed-{eng}", lines, 700)
    without = run(f"shed-{eng}-none", [l for l in lines if not l.endswith(" shed")], 700)
    n0, n1, m1 = voices(with_shed, 550), voices(with_shed, 620), voices(without, 620)
    assert n0 == len(NOTES) and n1 == m1 - 1, (eng, n0, n1, m1)
    h1, k1 = int(with_shed.state(620)["held"]), int(without.state(620)["held"])
    assert h1 == k1 == len(NOTES) - 3, (eng, h1, k1)               # a let-go voice went, the held ones play on
    ratio = M.click_one(ev, len(ev) - 1)[0]
    assert ratio < 3.0, (eng, ratio)


def test_a_shed_with_nothing_sounding_does_nothing():
    r = run("shed-idle", ["100 shed", "200 dump", "300 midi 0x90 60 100", "400 dump"], 600)
    assert voices(r, 200) == 0 and voices(r, 400) == 1 and not r.fails
