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
        r.out += ("\nfirmware/src/fx.c:964:25: runtime error: implicit conversion from type 'int' of value 35351 "
                  "(32-bit, signed) to type 'int16_t' (aka 'short') changed the value to -30185 (16-bit, signed)\n")
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


def test_the_wrap_check_finds_an_int16_wrap():
    """a real fault: the sanitizer build with Rhodes' layer crossfade 4x too hot (rhodes.c: v->pbuf, an int16) wraps
    on a loud note; the wrap check names it, and the build without the fault is clean"""
    out = os.path.join(mk.OUTD, "san-wrap-fault")
    os.makedirs(mk.OUTD, exist_ok=True)
    old = "v->pbuf[j] = (int16_t)(a + (((p2[j] - a) * w) >> 15));"
    b = mk.build(True, [("rhodes.c", old, old.replace("(a + ", "(4 * a + "))], out)
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
    is lost in plain's reference (1x) and over 10x in the diff; the same render against itself is 0"""
    t = 9000
    ev = [e for e in mk.gen(14) if e[0] <= t and not (t - 60 <= e[0] and mk.starts_sound(e[1]))]
    b = mk.run(mk.script_text(ev, [], t + 150), binary).x
    a = b.copy()
    a[mk.ms(t + 5):] += 400
    ratio, plain, diff = mk.click_ratio(a, b, t)
    assert plain < 2 and diff > 10
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


def test_bughunt22_plate_input_overflow():
    """BUGHUNT #22 (monkey seed 427470704, shrunk): PLATE's input one-pole (rev_plate.c pl_in) multiplies (b - u) by
    pb in 32 bits; with +24 dB make-up the input passes ~1.2x full scale and it overflows (UBSan; the FM-1 wraps)"""
    text = open(os.path.join(os.path.dirname(__file__), "monkey", "bughunt22-plate-overflow.txt")).read()
    r = mk.run(text, mk.build(san=True))
    if "rev_plate.c" in r.out and "signed integer overflow" in r.out:
        pytest.xfail("BUGHUNT #22: rev_plate.c pl_in overflows int32 (fix it, then this passes)")
    assert r.code == 0 and "runtime error" not in r.out


def test_bughunt25_fx_with_spread(binary):
    """BUGHUNT #25 (monkey seed 512445757, shrunk): FX with SPREAD on: the bypass crossfade goes to the plain mono,
    then the bypassed dry adds SPREAD's side in one step (fx.c fx_process / fx_spread_dry); named, xfail until fixed"""
    path = os.path.join(os.path.dirname(__file__), "monkey", "bughunt25-fx-spread.txt")
    ev = [(int(ln.split()[0]), ln.split(" ", 1)[1].strip()) for ln in open(path) if ln[0] != "#"]
    ratio, changed, ring = mk.click_one(ev, len(ev) - 1, binary)
    assert changed == ["FX+SPREAD"] and mk.known_click(changed) == "#25" and ring > -40
    if ratio > 10:
        pytest.xfail(f"BUGHUNT #25: FX with SPREAD clicks ({ratio:.0f}x)")


def test_bughunt26_wah_overflow():
    """BUGHUNT #26 (monkey seed 995464060 on mix-fix2, shrunk; 0.9.5 too): the touch WAH's filter (fx.c
    fx_wah_unity, bp += (f * (hi >> 1)) >> 13) overflows int32 on a hot input (UBSan; the FM-1 wraps)"""
    text = open(os.path.join(os.path.dirname(__file__), "monkey", "bughunt26-wah-overflow.txt")).read()
    r = mk.run(text, mk.build(san=True))
    if "fx.c" in r.out and "signed integer overflow" in r.out:
        pytest.xfail("BUGHUNT #26: the WAH's filter overflows int32 (fix it, then this passes)")
    assert r.code == 0 and "runtime error" not in r.out


def test_bughunt9_level_with_fx_off(binary):
    """BUGHUNT #9, the part still open (mix-fix2 and 0.9.5 alike: 340x): with FX off (bypassed) LEVEL is not ramped
    (fx.c fx_out: fx_lv_ramp's still = byp || wet != 32768), a LEVEL change steps in one sample"""
    ev = [(14609, "midi 0x90 28 91"), (14793, "midi 0xB0 111 71"), (17379, "midi 0xB0 110 127")]
    ratio, changed, _ = mk.click_one(ev, 2, binary)
    assert changed == ["P_LEVEL"]
    if ratio > 10:
        pytest.xfail(f"BUGHUNT #9: LEVEL steps with FX off ({ratio:.0f}x)")


def test_bughunt27_khlon_trem_never_quiet(binary):
    """BUGHUNT #27 (monkey seed 155536243 on mix-fix2, shrunk; 0.9.5 too): F06 (KHLON, COMP) with TREM on: after the
    notes the output gate shuts, but fx_out's peak stays at 6..16 LSB, so fx_quiet never holds and nothing is written
    to the flash: the SAVE shows U01 and a power cycle brings back F06"""
    ev = [(19251, "midi 0xC2 5 0"), (19792, "btn 5"), (19822, "turn 2 1"), (20000, "turn 1 1")]
    bad = mk.check(ev, binary)
    if any(k == "roundtrip" for k, _ in bad):
        pytest.xfail("BUGHUNT #27: never quiet with KHLON + TREM: nothing saved")
    assert bad == []


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
