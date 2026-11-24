# SPDX-License-Identifier: GPL-3.0-only
"""The monkey (tools/monkey.py) on fixed seeds: 20 s of everything a player or a DAW can send, then the panic, the
probes and a SAVE + power cycle; every invariant must hold (crash / sanitizer, race, peak, wrap, rail, stuck,
silence, probe, roundtrip) and the differential click check on a few events finds no click that is not already a
finding. First, the detectors themselves: each one fails on an injected fault (a wrap, a rail, a stuck voice ...),
and the click check finds the FILTER TYPE click 0.9.5 has (BUGHUNT #4).

Seeds that fail on 0.9.5 are expected failures with their finding (the bug list); strict=False, so a fix
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
KNOWN = {}                                       # (22: BUGHUNT #21, fixed in 0.9.6: tests/py/test_steal_race.py)


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
        r.out += ("\nfirmware/src/fx.c:964:25: runtime error: implicit conversion from type 'int' of value 35351 "
                  "(32-bit, signed) to type 'int16_t' (aka 'short') changed the value to -30185 (16-bit, signed)\n")
    elif fault == "rail":
        r.x[t:t + 40, 1] = 32767
        want = "rail"
    elif fault == "peak":
        r.x[t, 0] = mk.LIM_T + 1   # one LSB over the ceiling (LIM_T 24000; 29204 in the 0.9.10 RCs, reverted 2026-11-08)
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


def test_the_wrap_check_finds_an_int16_wrap():
    """a real fault: the sanitizer build with Rhodes' layer crossfade 4x too hot (rhodes.c rh_pz_mix: v->pbuf, an int16) wraps
    on a loud note; the wrap check names it, and the build without the fault is clean"""
    out = os.path.join(mk.OUTD, "san-wrap-fault")
    os.makedirs(mk.OUTD, exist_ok=True)
    old = "o[i] = (int16_t)(o[i] + (((x - o[i]) * w) >> 15));"            # (rh_pz_mix: the crossfade at the strike)
    b = mk.build(True, [("rhodes.c", old, old.replace("(o[i] + ", "(4 * o[i] + "))], out)
    try:
        ev = [(100, "midi 0x90 60 64"), (300, "midi 0x90 64 90"), (900, "midi 0x80 60 0")]
        pl, m = mk.panic(mk.RAND_MS + 50)
        bad = mk.analyse(mk.run(mk.script_text(ev, pl, m["end"]), b), m)
        assert [k for k, _ in bad] == ["wrap"] and "rhodes.c:" in bad[0][1] and "'int16_t'" in bad[0][1]
        assert mk.analyse(mk.run(mk.script_text(ev, pl, m["end"]), mk.build(True)), m) == []
    finally:
        os.remove(out)


def test_the_click_check_sees_through_a_busy_patch(binary):
    """the difference of the two renders: 0.9.5's FILTER TYPE click (#4) inside seed 14's busy stream (12227 ms, CC 70
    to BP, the chorus and reverb on) reads 6x plain, so the old check missed it; the diff sees it (15x) and names it"""
    ev = mk.gen(14)
    i = ev.index((12227, "midi 0xB0 70 75"))
    ratio, changed, ring, plain, diff = mk.click_one(ev, i, binary, detail=True)
    assert changed == ["P_FL_TYPE"] and ring > -55
    assert plain < 10
    if ratio <= 10:
        pytest.xfail(f"FILTER TYPE no longer clicks here ({ratio:.1f}x): BUGHUNT #4 fixed")


def test_the_diff_finds_a_small_step_under_a_loud_sound(binary):
    """a fault injected into a busy render (seed 14 at 9 s, -19 dBFS with reverb and chorus): a 400 step (-38 dBFS)
    is lost in plain's reference (1x .. 2.2x: 2026-11-13, the upper register's cleaner top leaves the busy render's
    highs a little quieter) and over 10x in the diff; the same render against itself is 0"""
    t = 9000
    ev = [e for e in mk.gen(14) if e[0] <= t and not (t - 60 <= e[0] and mk.starts_sound(e[1]))]
    ev = [(1, "midi 0xB0 40 127")] + ev   # (RESPONSE LIGHT: the render at 0.9.11's level; SND3's FULL curve played it
    # ~3 dB quieter and the 400 step read 2.4x in plain's reference: this checks the diff, not the curve)
    b = mk.run(mk.script_text(ev, [], t + 150), binary).x
    a = b.copy()
    a[mk.ms(t + 5):] += 400
    ratio, plain, diff = mk.click_ratio(a, b, t)
    assert plain < 2.5 and diff > 10
    assert mk.click_ratio(b, b, t)[2] == 0


def test_the_click_stream_ends_at_its_event():
    """nothing after the event is rendered: 0.9.5 seed 273194754's FX press read 16x because a TYPE turn (#12) landed
    12 ms later"""
    ev = [(100, "midi 0x90 60 100"), (500, "btn 2"), (512, "turn 1 -5"), (520, "midi 0xB0 70 50")]
    assert mk.click_stream(ev, 1) == ev[:2]


def test_the_panic_outlasts_a_preset_browse(binary):
    """0.9.5 seed 273194710 (shrunk): a PRESETS turn 300 ms before the panic loads DREAM PAD (INFINITE reverb, a tape
    echo) 400 ms after its last detent, after the panic's FX off; the panic sends it again at +700 ms, so the 10 s
    silence holds (it was a tool mistake, not a ringing tail)"""
    ev = [(16949, "midi 0xC0 21 0"), (17107, "turn 6 -5"), (19624, "midi 0xB0 19 10"), (19698, "keys 84247440"),
          (19727, "turn 6 -5")]
    assert mk.check(ev, binary) == []


def test_the_probe_turns_the_motor_off(binary):
    """0.9.5 seed 273194706 (shrunk): REEDY, a VIBES preset with its motor on: the probe's key-up met the motor's
    pulse (+2 dB, not a release); the probe now switches the motor off (CC 47 0) as well as FX"""
    ev = [(15007, "midi 0xC0 12 0"), (15706, "midi 0xC0 23 0"), (19803, "midi 0xB0 79 64")]
    assert mk.check(ev, binary) == []


def test_the_probe_sets_level_and_known_clicks_take_the_switch(binary):
    """0.9.5 seed 427470656 (shrunk): MARIMBA at LEVEL -12 dB with HAMMER 0: the probe read -43 dBFS, 'dead'; the
    probe now sets LEVEL to 0 dB. An effect switched on at depth 0 gets a depth (PHASER: #2), so the pair is #2"""
    ev = [(16631, "midi 0xB6 79 115"), (16657, "midi 0xB0 31 0"), (18552, "midi 0xB0 30 0"), (20000, "midi 0xB0 110 0")]
    assert mk.check(ev, binary) == []
    assert mk.known_click(["P_PH_SW", "P_PH_DEPTH"]) == "#2" and mk.known_click(["P_PH_DEPTH"]) is None


def test_bughunt25_fx_with_spread(binary):
    """BUGHUNT #25 (monkey seed 512445757, shrunk): FX with SPREAD on: the bypass crossfade goes to the plain mono,
    then the bypassed dry added SPREAD's side in one step (0.9.5: 17.8x); fixed in 0.9.6 (fx.c fx_xf_side)"""
    path = os.path.join(os.path.dirname(__file__), "monkey", "bughunt25-fx-spread.txt")
    ev = [(int(ln.split()[0]), ln.split(" ", 1)[1].strip()) for ln in open(path) if ln[0] != "#"]
    ratio, changed, ring = mk.click_one(ev, len(ev) - 1, binary)
    assert changed == ["FX+SPREAD"] and mk.known_click(changed) == "#25" and ring > -40
    assert ratio <= 10, f"BUGHUNT #25: FX with SPREAD clicks ({ratio:.1f}x)"


def _repro(name):
    path = os.path.join(os.path.dirname(__file__), "monkey", name)
    return [(int(ln.split()[0]), ln.split(" ", 1)[1].strip()) for ln in open(path) if ln[0] != "#"]


def _step(t, size=400):
    """a real click injected into the render with the event: a step of size LSB 5 ms after it"""
    def f(x):
        x[mk.ms(t + 5):] += size
        return x
    return f


@pytest.mark.parametrize("name,window", [("rel0913-shape-after-fx.txt", "bypass end"),
                                         ("rel0913-fx-browse.txt", "browse")])
def test_the_click_windows_pass_the_artefact_and_catch_a_step(binary, name, window):
    """0.9.13 (host monkey seeds 23615257, 23615202): the two windows of click_one where the diff's settled reference
    says nothing. The artefact passes (it read 12.0x / 11.0x with the old reference, plain 1.38x / 0.95x), and a real
    step injected inside the same window is still caught, by plain or by the diff's absolute step (STEP_ABS)"""
    ev = _repro(name)
    i, t = len(ev) - 1, ev[-1][0]
    ratio, changed, ring, plain, diff = mk.click_one(ev, i, binary, detail=True)
    assert ring > -55 and plain < 2 and ratio <= 10, (window, ratio, plain, diff)
    for size in (400, 250):
        r = mk.click_one(ev, i, binary, detail=True, inject=_step(t, size))
        assert r[0] > 10, (window, size, r)


def test_the_bypass_end_window_needs_the_press_and_the_old_reference_read_the_artefact(binary):
    """the bypass-end window opens only for an FX press up to XF_MS before the event, and only its reference changes:
    the same two renders judged with the diff's own settled reference read the artefact (over 10x); the FX events
    the window looks for are the FX button and CC 111 on channel 1"""
    ev = _repro("rel0913-shape-after-fx.txt")
    t = ev[-1][0]
    pre = mk.click_stream(ev, len(ev) - 1)
    a = mk.run(mk.script_text(pre, (), t + 150), binary).x
    b = mk.run(mk.script_text(pre[:-1], (), t + 150), binary).x
    assert mk.click_ratio(a, b, t)[2] > 10                      # the old reference: the artefact
    assert mk.fx_event("btn 2") and mk.fx_event("midi 0xB0 111 127") and not mk.fx_event("midi 0xB1 111 127")
    assert mk.XF_MS >= 46 + 6 and mk.STEP_ABS <= 200


@pytest.mark.parametrize("seed,t,line", [(906792336, 1496, "midi 0xB0 111 0"), (906792829, 5099, "btn 2"),
                                         (906791411, 6944, "turn 3 1"), (23615107, 16826, "btn 3")])
def test_the_bypass_end_window_keeps_to_its_case(binary, seed, t, line):
    """the window opens only when both renders are bypassed at +31 ms and the event is no FX event itself (an FX on
    after an FX press brings the wet back: its own settled diff is the reference), and its second reference never
    lowers the first (an ARPEGGIO knob's notes do not fade with the wet): a first draft read these 18x, 15x, 32x.
    Seed 23615107 (HOLD OFF 17 ms after FX: the arpeggio's 16th note starts only without the event, a smooth onset):
    13.7x with the absolute step on the second difference; a 250 or 400 LSB step injected there is still caught"""
    ev = mk.gen(seed)
    i = ev.index((t, line))
    ratio, changed, ring, plain, diff = mk.click_one(ev, i, binary, detail=True)
    assert ratio <= 10, (seed, ratio, plain, diff)
    if seed == 23615107:
        for size in (250, 400):
            assert mk.click_one(ev, i, binary, detail=True, inject=_step(t, size))[0] > 10, size


# (#22 and #26, the int32 overflows: tests/py/test_overflows.py)


def test_bughunt9_level_with_fx_off(binary):
    """BUGHUNT #9, the open part (0.9.5: 340x): with FX off (bypassed) LEVEL was not ramped (fx.c fx_out), a LEVEL
    change stepped in one sample; fixed in 0.9.6 (LEVEL glides with FX off too)"""
    ev = [(14609, "midi 0x90 28 91"), (14793, "midi 0xB0 111 71"), (17379, "midi 0xB0 110 127")]
    ratio, changed, _ = mk.click_one(ev, 2, binary)
    assert changed == ["P_LEVEL"]
    assert ratio <= 10, f"BUGHUNT #9: LEVEL steps with FX off ({ratio:.1f}x)"


def test_bughunt27_khlon_trem_saves(binary):
    """BUGHUNT #27 (monkey seed 155536243, shrunk; 0.9.5 too): F06 (KHLON, COMP) with TREM on: after the notes the
    output gate shut, but fx_out's idle residue (6..16 LSB) kept fx_quiet from holding, so nothing was written to the
    flash (a SAVE showed U01, a power cycle brought back F06); fixed in 0.9.6 (fx.c fx_gz; also
    test_persistence::test_save_behind_the_shut_gate)"""
    ev = [(19251, "midi 0xC2 5 0"), (19792, "btn 5"), (19822, "turn 2 1"), (20000, "turn 1 1")]
    assert mk.check(ev, binary) == []


def test_ddmin_finds_the_minimal_pair():
    items = list(range(40))
    assert sorted(mk.ddmin(items, lambda xs: 7 in xs and 31 in xs)) == [7, 31]


def _seed(seed, binary, request):
    if seed in KNOWN:
        request.applymarker(pytest.mark.xfail(reason=KNOWN[seed], strict=False))
    r = mk.one(seed, binary, k_clicks=3, do_shrink=False)
    assert not r["fail"], f"seed {seed}: " + "; ".join(f"{k}: {m}" for k, m in r["fail"]) + \
        f"  (replay and shrink: python3 tools/monkey.py --seeds {seed})"


@pytest.mark.parametrize("seed", SEEDS)
def test_seed(seed, binary, request):
    _seed(seed, binary, request)


@pytest.mark.parametrize("seed", SAN_SEEDS)
def test_seed_sanitized(seed, request):
    _seed(seed, mk.build(san=True), request)
