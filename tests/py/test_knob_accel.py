# SPDX-License-Identifier: GPL-3.0-only
"""Knob acceleration (issue #25, "faster knob acceleration": to reduce knob turning for all parameters). ui.c
ui_accel: a fast turn moves an amount more per detent (under 40 ms per detent x2, 20 ms x4, 10 ms x8), so 0 .. 127
takes about one turn; a slow turn keeps single steps; choices keep one step a detent. The host build keeps it off
for the other scripts (exact detents); FRODHES_KNOB_ACCEL turns it on here."""
from hostrun import run as _run


def run(*a, **k):
    return _run(*a, env={"FRODHES_KNOB_ACCEL": "1"}, **k)   # the host turns acceleration on only when asked

K1 = 2          # the host's encoder numbering: K1 .. K4 = 2 .. 5; on PERFORM K1 is DRIVE (p.1, 0 .. 127)
GO_TREM = ["50 btn 5"]   # LFO: the TREM/WAH page, K1 = EFFECT (OFF / TREM / WAH: a choice)


def turned(tag, gap, n=10, pre=(), key="p.1", d=1):
    lines = list(pre) + ["150 dump"] + [f"{200 + i * gap} turn {K1} {d}" for i in range(n)]
    t = 300 + n * gap
    r = run(tag, lines + [f"{t} dump"], t + 100)
    return int(r.state(150)[key]), int(r.state(t)[key])


def test_slow_turns_keep_single_steps():
    for gap in (50, 100, 300):
        a, b = turned(f"ka-slow{gap}", gap)
        assert b - a == 10, (gap, a, b)


def test_fast_turns_accelerate():
    steps = {gap: (lambda ab: ab[1] - ab[0])(turned(f"ka-fast{gap}", gap)) for gap in (30, 15, 8)}
    assert steps[30] > 10 and steps[15] > steps[30] and steps[8] > steps[15], steps
    assert steps[8] >= 64, steps        # (the first detent of a turn counts 1: 1 + 9 x 8 = 73)


def test_one_fast_turn_covers_the_range():
    a, b = turned("ka-range", 8, n=20)
    assert a == 0 and b == 127, (a, b)   # 20 detents, about one turn: 0 .. 100 %, clamped at the top


def test_fast_turn_down():
    lines = ["150 dump"] + [f"{200 + i * 8} turn {K1} 1" for i in range(20)]
    lines += ["600 dump"] + [f"{700 + i * 8} turn {K1} -1" for i in range(20)] + ["1100 dump"]
    r = run("ka-down", lines, 1200)
    assert int(r.state(600)["p.1"]) == 127 and int(r.state(1100)["p.1"]) == 0


def test_choices_never_accelerate():
    r = run("ka-choice-ref", GO_TREM + ["150 dump"], 200)
    labels = r.state(150)
    assert labels["label0"] == "EFFECT", labels["label0"]
    slow = run("ka-choice-slow", GO_TREM + ["150 dump", "200 turn 2 1", "300 dump"], 400)
    fast = run("ka-choice-fast", GO_TREM + ["150 dump"] + [f"{200 + i * 8} turn 2 1" for i in range(2)] + ["300 dump"],
               400)
    s0, s1 = slow.state(150)["card0"], slow.state(300)["card0"]
    f1 = fast.state(300)["card0"]
    assert s0 != s1                       # one detent: the next choice
    two = run("ka-choice-two", GO_TREM + ["200 turn 2 1", "450 turn 2 1", "600 dump"], 700).state(600)["card0"]
    assert f1 == two, (f1, two)           # two fast detents: two choices on, not more
