# SPDX-License-Identifier: GPL-3.0-only
"""The REVERB's TYPEs (0.9.5: OFF / HALL / PLATE / INFINITE in one field, P_RV_TYPE, and the REVERB 2 page's SIZE, MOD,
LOW CUT), on the firmware's own code (tests/c/rev_scan.c through tools/rev_scan.py: fx_reverb alone, 256 frames a call
as fx_process calls it) and on the host build (tests/host.c through hostrun.py: the CCs, the records, the saves).

The plan's tests 2..7 (private plan, section 6):
- HALL with every new control at its default is the HALL before (the defaults are today's sound; Gate R itself is
  tools/gate_r.py and tests/py/test_rev_hall.py's fuzz hash);
- T60 per DECAY (the 500 Hz + 1 kHz octaves): PLATE 0.8 .. 6 s and INFINITE 4 .. 60 s (DECAY 0 .. 119), exponential
  in DECAY, within +-15 %;
- INFINITE held (DECAY 127 FREEZE, the input closed; 120..126 SUSTAIN, open) for 10 minutes of audio: within
  +-1 dB, no growth, |DC| < 8 LSB; full-scale bursts while held: no overflow, no growth;
- a TYPE switch while it sounds: no click (the second difference no bigger than either TYPE's own), the old tail gone
  after the fade (~6 ms), stale lines read as empty (PLATE and INFINITE share one pool); OFF lets the tail ring out;
- the tails end in exact zero (not frozen) and the reverb stops costing; fx_quiet() comes, and the settings save;
- the records: old presets and old settings read as HALL + the defaults; the new controls round-trip; the settings
  layout is append-only;
- every new control moves what it should (SIZE, MOD, LOW CUT on every TYPE; TONE, PRE-DELAY on the new ones);
- the levels: each TYPE's wet within 2 dB of HALL's at the same MIX (broadband), its octave bands within 6 dB;
- the cost: fx_cost_us per TYPE (PLATE <= HALL, every TYPE <= HALL + 200 us) and tools/cpu_costs.json's rev_type_us
  rows in step with it.

Before the framework's interface (fx.c without FXP_RV_TYPE) the tests skip; a TYPE whose kernel is still a stub (no
wet at all) skips its sound tests, not the framework's."""
import os
import re
import struct
import subprocess
import sys
import zlib

import numpy as np
import pytest

from hostrun import FS, OUT, ROOT, cc, note, run

sys.path.insert(0, os.path.join(ROOT, "tools"))
import rev_scan as rs  # noqa: E402

BLK = rs.BLK
HAS = rs.has_types()
needs_types = pytest.mark.skipif(not HAS, reason="the reverb TYPEs' interface is not in this tree (no FXP_RV_TYPE)")
T = rs.TYPES
NEW = ("PLATE", "INFINITE")
SOUNDING = ("HALL",) + NEW
BASE_REF = "c82dc87"                                     # sonic-int before the reverb TYPEs (private/reverb/BRIEF)


def p_ids():
    """{name without P_: id} from params.c's enum"""
    s = rs._strip(open(os.path.join(ROOT, "firmware", "src", "params.c")).read())
    m = re.search(r"enum\s*\{\s*(P_TYPE\b[^}]*)\}", s)
    return {n.strip()[2:]: i for i, n in enumerate(x for x in m.group(1).split(",") if x.strip())}


def pdesc(name):
    """(hi, def, cc) of a parameter, from its PARAMS[] line"""
    s = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    m = re.search(r"\[P_" + name + r"\] = \{\"[^\"]*\", (\d+), (\d+), F_\w+, (\d+)", s)
    return tuple(int(v) for v in m.groups())


@pytest.fixture(scope="module")
def exe():
    if not HAS:
        pytest.skip("no reverb TYPEs in this tree")
    e = rs.build(tag="pytest")
    yield e
    os.remove(e)


@pytest.fixture(scope="module")
def silent(exe):
    """the TYPEs whose kernel is not there yet: a stub (its file says STUB) or no wet at all"""
    return {t for t in NEW if rs.is_stub(t) or rs.is_silent_type(exe, t)}


def sounding(silent, typ):
    if typ in silent:
        pytest.skip(f"{typ}'s kernel is a stub")


def held_chord(n, amp=5000.0):
    """a chord that holds (no decay): the switch tests' bed"""
    t = np.arange(n) / FS
    x = sum(amp / 4 * np.sin(2 * np.pi * f * t + i) for i, f in enumerate((220.0, 277.18, 329.63, 440.0)))
    return np.round(x).astype(np.int64)


def jumps(y, a, b):
    """the largest second difference of L and R in [a, b) (a click is a one-sample step in the slope)"""
    return float(np.max(np.abs(np.diff(np.asarray(y[a:b], float), 2, axis=0))))


def default_params():
    """every new control at its params.c default, as fx.c sees it (FXP values = the parameter's value)"""
    d = {}
    for p in ("RV_TYPE", "RV_SIZE", "RV_MOD", "RV_LOCUT"):
        d[p] = pdesc(p)[1]
    return d


# ---- the defaults are HALL, today's

@needs_types
def test_the_defaults_are_hall():
    """params.c: TYPE's default is HALL (1), SIZE AUTO (0), LOW CUT off (0); the TYPE field runs OFF .. INFINITE"""
    hi, de, _ = pdesc("RV_TYPE")
    assert (hi, de) == (3, 1)
    assert pdesc("RV_SIZE")[1] == 0 and pdesc("RV_LOCUT")[1] == 0
    ids = p_ids()
    assert all(ids[n] >= 59 for n in ("RV_TYPE", "RV_SIZE", "RV_MOD", "RV_LOCUT")), "appended (ids are append-only)"


@needs_types
def test_hall_at_its_defaults_is_the_hall_before(exe):
    """fx_reverb at power-on and with every new control set to its default explicitly: the same samples (any MIX,
    DECAY, TONE, PRE-DELAY); OFF at power-on would be silence (the default is HALL)"""
    x = rs.chord(BLK * 200)
    for prm in (dict(RV_DEPTH=100), dict(RV_DEPTH=127, RV_DECAY=127, RV_TONE=0, RV_PREDLY=60),
                dict(RV_DEPTH=30, RV_DECAY=0, RV_TONE=127)):
        a = rs.render(exe, x, prm, blocks=600)
        b = rs.render(exe, x, dict(prm, **default_params()), blocks=600)
        assert np.array_equal(a["y"], b["y"]) and np.any(a["wet"]), prm
        assert a["cost"] == b["cost"]


# ---- T60 per DECAY (test 2)

T60_RANGE = {"PLATE": (0.8, 6.0, 127), "INFINITE": (4.0, 60.0, 119)}   # (DECAY 0, DECAY top, the top's value)


def t60_target(typ, d):
    lo, hi, top = T60_RANGE[typ]
    return lo * (hi / lo) ** (min(d, top) / top)


def measure_t60(exe, typ, d, size=127):
    """the mid-band T60 (500 Hz + 1 kHz octaves, as a reverb time is quoted; TONE at its default damps the top)"""
    secs = t60_target(typ, d) * 0.6 + 4                      # down 36 dB and some (the EDC needs the tail past -25)
    return rs.t60_mid(exe, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_DECAY=d, RV_SIZE=size), secs)


@needs_types
@pytest.mark.parametrize("typ,d", [(t, d) for t in NEW for d in (0, 64, T60_RANGE[t][2])])
def test_t60_per_decay(exe, silent, typ, d):
    sounding(silent, typ)
    t = measure_t60(exe, typ, d)
    want = t60_target(typ, d)
    assert t is not None and abs(t / want - 1) <= 0.15, f"{typ} DECAY {d}: T60 {t} s, target {want:.2f} s"


@needs_types
@pytest.mark.parametrize("typ", NEW)
def test_t60_rises_with_decay(exe, silent, typ):
    sounding(silent, typ)
    ts = [measure_t60(exe, typ, d) for d in (0, 32, 64, 96)]
    assert all(b > a for a, b in zip(ts, ts[1:])), ts


# ---- INFINITE frozen (test 3)

FREEZE_MIN = 10
WIN1 = BLK * 172                                          # ~1 s windows


FREEZE_AT = int(2 * FS / BLK)                             # DECAY 100 -> 127 after the chord (frozen, the input
                                                          # closes: a chord sent while frozen would not go in)


FREEZE = {"freeze": 127, "sustain": 123}                 # rev_types.c: DECAY 127 FREEZE (the input closed), 120..126
                                                          # SUSTAIN (the loop gain 1, the input open)


def frozen(exe, x, minutes=FREEZE_MIN, mix=100, switches=(), decay=127):
    blocks = int(minutes * 60 * FS / BLK) + 5 * 172
    return rs.stats(exe, x, dict(RV_TYPE=T["INFINITE"], RV_DEPTH=mix, RV_DECAY=100), blocks=blocks, win=WIN1,
                    switches=[(FREEZE_AT, "RV_DECAY", decay)] + list(switches))


@needs_types
@pytest.mark.parametrize("mode", sorted(FREEZE))
def test_freeze_holds_ten_minutes(exe, silent, mode):
    """a chord, then 10 minutes of silence in, held: the level over every 10 s (the lines' modulation beats the 1 s
    levels by ~2 dB) within +-1.1 dB of the first minute's (from 10 s: the input faded out, the hold settled), the last
    minute within 1 dB of the first (no growth, no loss), |DC| < 8 LSB over every 10 s, never silent"""
    sounding(silent, "INFINITE")
    s = frozen(exe, rs.chord(2 * FS), decay=FREEZE[mode])
    p = (s["rms"] ** 2).mean(axis=1)[10:]
    n = len(p) // 10
    lv = 10 * np.log10(p[:n * 10].reshape(n, 10).mean(axis=1) + 1e-9)
    ref = 10 * np.log10(p[:60].mean() + 1e-9)
    assert ref > 20 * np.log10(30), f"too quiet to judge: {ref:.1f} dB re 1 LSB"
    assert np.all(np.abs(lv - ref) <= 1.1), f"level {lv.min() - ref:+.2f} .. {lv.max() - ref:+.2f} dB"   # (SUSTAIN:
    # -0.99 .. +0.88 before rev-upsample's half-bands, -1.003 .. +0.87 with them: the lines' beat, not a drift)
    assert abs(10 * np.log10(p[-60:].mean() + 1e-9) - ref) <= 1.0
    dc = s["mean"][10:10 + n * 10].reshape(n, 10, 2).mean(axis=1)
    assert np.all(np.abs(dc) < 8), f"DC up to {np.abs(dc).max():.2f} LSB"
    assert np.all(s["nonzero"][5:] > 0)


@needs_types
@pytest.mark.parametrize("mode", sorted(FREEZE))
def test_full_scale_bursts_while_frozen(exe, silent, mode):
    """frozen, with bursts of several times full scale every 30 s (the fuzz's 3,000,000): no overflow (the wet stays
    within the reverb's own range, 8 x int16 a side, < 2^20), no growth (the last minute no louder than the minute
    after the last burst + 1 dB), no DC"""
    sounding(silent, "INFINITE")
    rng = np.random.default_rng(5)
    n = 5 * 60 * FS
    x = np.zeros(n, np.int64)
    x[:2 * FS] = rs.chord(2 * FS)
    for k in range(1, 10):
        a = k * 30 * FS
        x[a:a + FS // 20] = rng.integers(-3_000_000, 3_000_001, FS // 20)
    s = frozen(exe, x, minutes=6, decay=FREEZE[mode])     # (SUSTAIN takes the bursts in: the line clamps hold it)
    assert np.all(s["max"] < (1 << 20)), f"the wet reached {s['max'].max():.0f}"
    lv = 20 * np.log10(s["rms"].mean(axis=1) + 1e-9)
    after = lv[9 * 30 + 3:9 * 30 + 63]
    assert lv[-60:].max() <= after.max() + 1.0, "it grows after the bursts"
    n = (len(s["mean"]) - 10) // 10
    dc = s["mean"][10:10 + n * 10].reshape(n, 10, 2).mean(axis=1)
    assert np.all(np.abs(dc) < 8), f"DC up to {np.abs(dc).max():.2f} LSB (10 s means)"


@needs_types
def test_unfreeze_rings_out_to_exact_zero(exe, silent):
    """frozen for 20 s, then DECAY 100: the tail decays and ends in exact silence; MIX 0 then costs nothing"""
    sounding(silent, "INFINITE")
    b = int(20 * FS / BLK)
    s = rs.stats(exe, rs.chord(2 * FS), dict(RV_TYPE=T["INFINITE"], RV_DEPTH=100, RV_DECAY=100),
                 blocks=int(400 * FS / BLK), win=BLK * 16,
                 switches=[(FREEZE_AT, "RV_DECAY", 127), (b, "RV_DECAY", 100), (b, "RV_DEPTH", 0)])
    assert np.all(s["nonzero"][:b // 16] > 0)
    assert 0 <= s["last"] < int(390 * FS / BLK), f"still ringing at the end (last {s['last']})"
    assert s["cost"][-1] == 0


# ---- switching TYPE (test 4)

PAIRS = [(a, b) for a in SOUNDING for b in SOUNDING if a != b]
SW = 120                                                  # the switch's block
FADE_MAX = 4                                              # blocks: the plan's ~6 ms fade is 1..2 blocks (5.8 ms each)


@needs_types
@pytest.mark.parametrize("a,b", PAIRS)
def test_switch_does_not_click(exe, a, b):
    """a held chord, TYPE a -> b at block SW: the second difference anywhere no bigger than either TYPE's own render
    (x 1.2 + 40 LSB, the drive switch test's limit); the cost every block at most the larger TYPE's, the new TYPE's
    at the end"""
    x = held_chord(BLK * 300)
    y = rs.render(exe, x, dict(RV_TYPE=T[a], RV_DEPTH=110), [(SW, "RV_TYPE", T[b])], trace=True)
    ra = rs.render(exe, x, dict(RV_TYPE=T[a], RV_DEPTH=110), trace=True)
    rb = rs.render(exe, x, dict(RV_TYPE=T[b], RV_DEPTH=110), trace=True)
    ref = max(jumps(ra["y"], 2000, len(x)), jumps(rb["y"], 2000, len(x)))
    got = jumps(y["y"], 2000, len(x))
    assert got <= 1.2 * ref + 40, f"{a} -> {b}: {got:.0f} against {ref:.0f}"
    ca, cb = max(ra["trace"][10:]), max(rb["trace"][10:])
    assert max(y["trace"][SW:]) <= max(ca, cb) and y["trace"][-1] == cb, (y["trace"][SW - 2:SW + 8], ca, cb)


@needs_types
@pytest.mark.parametrize("a,b", PAIRS)
def test_old_tail_gone_after_the_fade(exe, silent, a, b):
    """a chord into TYPE a until block SW, then silence in and TYPE b: from SW + FADE_MAX on the wet is exactly 0 (the
    old tail faded, the new TYPE's lines read as empty: no old sound, PLATE and INFINITE sharing one pool too); the
    fade itself is no click (the wet's second difference no bigger than before the switch)"""
    sounding(silent, a)
    x = held_chord(BLK * SW)
    r = rs.render(exe, x, dict(RV_TYPE=T[a], RV_DEPTH=110), [(SW, "RV_TYPE", T[b])], blocks=SW + 60)
    w = r["wet"]
    assert np.any(w[(SW - 4) * BLK:SW * BLK]), "nothing to fade"
    assert not np.any(w[(SW + FADE_MAX) * BLK:]), f"{a} -> {b}: old sound after the fade (last block {r['last']})"
    assert jumps(w, (SW - 2) * BLK, (SW + FADE_MAX) * BLK) <= 1.2 * jumps(w, 2000, (SW - 2) * BLK) + 40


@needs_types
def test_stale_pool_reads_as_empty(exe, silent):
    """PLATE and INFINITE share the pool: PLATE fills it loud and long, INFINITE starts on it (and back): with
    silence in from the switch, nothing comes out after the fade"""
    for a, b in (("PLATE", "INFINITE"), ("INFINITE", "PLATE")):
        if a in silent:
            continue
        x = rs.chord(BLK * 200, amp=12000)
        r = rs.render(exe, x[:BLK * SW], dict(RV_TYPE=T[a], RV_DEPTH=127, RV_DECAY=110),
                      [(SW, "RV_TYPE", T[b])], blocks=SW + 100)
        assert not np.any(r["wet"][(SW + FADE_MAX) * BLK:]), (a, b)


@needs_types
@pytest.mark.parametrize("typ", SOUNDING)
def test_off_lets_the_tail_ring_out(exe, typ):
    """TYPE -> OFF while a chord plays: the send stops, the tail rings on (no fade to 0 within the fade time) and ends
    in exact silence; then the reverb costs nothing"""
    x = held_chord(BLK * 2000)
    prm = dict(RV_TYPE=T[typ], RV_DEPTH=110, RV_DECAY=64 if typ != "INFINITE" else 40)
    r = rs.stats(exe, x, prm, blocks=int(200 * FS / BLK), win=BLK, switches=[(SW, "RV_TYPE", 0)])
    if typ != "HALL" and not np.any(r["nonzero"][:SW]):
        pytest.skip(f"{typ}'s kernel is a stub")
    assert np.all(r["nonzero"][SW:SW + 20] > 0), "the tail stopped at OFF"
    assert 0 <= r["last"] < int(195 * FS / BLK), "the tail never ended"
    assert r["cost"][-1] == 0


@needs_types
def test_one_type_at_a_time(exe):
    """HALL -> PLATE -> INFINITE -> HALL one block apart: the cost never above the largest single TYPE's"""
    x = held_chord(BLK * 200)
    sw = [(SW, "RV_TYPE", T["PLATE"]), (SW + 1, "RV_TYPE", T["INFINITE"]), (SW + 2, "RV_TYPE", T["HALL"])]
    r = rs.render(exe, x, dict(RV_DEPTH=110), sw, trace=True)
    top = max(rs.render(exe, x, dict(RV_TYPE=T[t], RV_DEPTH=110), trace=True)["trace"][-1] for t in SOUNDING)
    assert max(r["trace"]) <= top
    assert r["trace"][-1] == rs.render(exe, x, dict(RV_DEPTH=110), trace=True)["trace"][-1]


# ---- the tails end (test 5)

TAIL_S = {"HALL": 30, "PLATE": 40, "INFINITE": 300}


@needs_types
@pytest.mark.parametrize("typ,d", [("HALL", 127), ("PLATE", 64), ("PLATE", 127), ("INFINITE", 64), ("INFINITE", 119)])
def test_tail_ends_in_exact_zero(exe, silent, typ, d):
    """a chord, then silence in, MIX held: the wet ends in exact zero within TAIL_S; MIX 0 at the end: no cost"""
    if typ in silent:
        pytest.skip("a stub")
    n = int(TAIL_S[typ] * FS / BLK)
    s = rs.stats(exe, rs.chord(FS, amp=12000), dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_DECAY=d), blocks=n + 8,
                 win=BLK, switches=[(n, "RV_DEPTH", 0)])
    assert 0 <= s["last"] < n, f"{typ} DECAY {d}: still sounding after {TAIL_S[typ]} s"
    assert s["cost"][-1] == 0


@needs_types
def test_fx_quiet_comes_and_the_settings_save():
    """the host build: INFINITE at DECAY 64, a chord, then a setup change (USB VOL): the settings save within a minute
    (fx_quiet: 500 ms under -66 dBFS); frozen (DECAY 127) they wait while it rings"""
    ty = rv_cc()["INFINITE"]
    _, _, c_dec = pdesc("RV_DECAY")
    _, _, c_type = pdesc("RV_TYPE")
    base = [cc(50, 91, 100), cc(60, c_type, ty)] + note(200, 60, 120, 400) + note(200, 64, 120, 400)
    r = run("rev-quiet", base + [cc(70, c_dec, 64), cc(3000, 114, 127)], 70000)
    assert any(t > 3000 for t in r.saves), r.saves
    f = run("rev-quiet-frozen", base + [cc(70, c_dec, 64), cc(700, c_dec, 127), cc(3000, 114, 127)], 20000)
    assert not any(t > 3000 for t in f.saves), f.saves


# ---- the records (test 6)

_RV_CC = {}


def rv_cc():
    """{TYPE: a CC value that selects it} on the host build: every value 0..127 sent, the TYPE read back; the middle
    of each band"""
    if _RV_CC:
        return _RV_CC
    _, _, c = pdesc("RV_TYPE")
    pid = p_ids()["RV_TYPE"]
    lines, t = [], 100
    for v in range(128):
        lines += [cc(t, c, v), f"{t + 1} dump"]
        t += 2
    r = run("rev-cc-map", lines, t + 5)
    got = {v: int(r.state(100 + 2 * v + 1)[f"p.{pid}"]) for v in range(128)}
    for name, k in T.items():
        vs = [v for v, g in got.items() if g == k]
        assert vs, f"no CC {c} value selects {name}"
        assert vs == list(range(vs[0], vs[-1] + 1)), f"{name}'s band is not one run: {vs}"
        _RV_CC[name] = vs[len(vs) // 2]
    assert got[0] == 0, "CC 0 is OFF"
    return _RV_CC


@needs_types
def test_type_cc_bands():
    m = rv_cc()
    assert set(m) == set(T)
    _, _, c = pdesc("RV_TYPE")
    assert c and c not in (91, 92, 93, 94, 84, 79, 80), c


@needs_types
def test_factory_presets_are_hall_with_the_defaults():
    """every factory preset loads with the reverb TYPE and LOW CUT its record names (0.9.5, the presets' listening
    round: HALL or PLATE, never INFINITE in the factory bank; LOW CUT OFF or 150 / 200 Hz, values 70 / 80) and SIZE /
    MOD at their defaults"""
    ids = p_ids()
    d = default_params()
    lines = []
    for k in range(16):
        lines += [f"{100 + 700 * k} midi 0xC0 {k} 0", f"{600 + 700 * k} dump"]
    r = run("rev-factory", lines, 100 + 700 * 16)
    for k in range(16):
        s = r.state(600 + 700 * k)
        for p, v in d.items():
            got = s[f"p.{ids[p]}"]
            if p.endswith("TYPE"):
                assert got in ("1", "2"), (f"F{k + 1:02d}", p, got)
            elif p.endswith("LOCUT"):
                assert got in ("0", "70", "80"), (f"F{k + 1:02d}", p, got)
            else:
                assert got == str(v), (f"F{k + 1:02d}", p, got)


SECTOR = 4096
V3_LEN = 96


def _img(path):
    d = bytearray(open(path, "rb").read())
    assert len(d) == 5 * SECTOR
    return d


@needs_types
def test_new_controls_round_trip(tmp_path):
    """PLATE, SIZE 90, MOD 20, LOW CUT 70 set over MIDI, saved to U03: after a power cycle the panel has them (the
    settings) and U03 loads them back after another preset; the same with INFINITE and OFF (rv_last kept: OFF is a
    value of its own)"""
    ids = p_ids()
    c = {p: pdesc(p)[2] for p in ("RV_TYPE", "RV_SIZE", "RV_MOD", "RV_LOCUT")}
    m = rv_cc()
    for typ, size, mod, lc in (("PLATE", 90, 20, 70), ("INFINITE", 1, 127, 127), ("OFF", 50, 0, 1)):
        fl = str(tmp_path / f"rt-{typ}.flash")
        want = dict(RV_TYPE=T[typ], RV_SIZE=size, RV_MOD=mod, RV_LOCUT=lc)
        lines = [cc(20, c["RV_TYPE"], m[typ]), cc(21, c["RV_SIZE"], size), cc(22, c["RV_MOD"], mod),
                 cc(23, c["RV_LOCUT"], lc), "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9", "4000 dump"]
        r1 = run(f"rev-rt-{typ}-1", lines, 4500, flash=fl)
        assert r1.saves and r1.bank_saves
        r2 = run(f"rev-rt-{typ}-2", ["100 dump", "150 midi 0xC0 0 0", "700 dump", "750 midi 0xC0 18 0", "1300 dump"],
                 1400, flash=fl)
        s0, s1, s2 = r2.state(100), r2.state(700), r2.state(1300)
        for p, v in want.items():
            assert s0[f"p.{ids[p]}"] == str(v), ("settings", typ, p, s0[f"p.{ids[p]}"])
            assert s2[f"p.{ids[p]}"] == str(v), ("U03", typ, p, s2[f"p.{ids[p]}"])
        assert s1[f"p.{ids['RV_TYPE']}"] == "1" and s2["header"].startswith("U03")


def _base_tree(tmp):
    """the tree before the reverb TYPEs (git archive BASE_REF: firmware, tests), or skip (a shallow checkout)"""
    tree = os.path.join(tmp, "base")
    if os.path.isdir(tree):
        return tree
    tar = os.path.join(tmp, "base.tar")
    r = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "-o", tar, BASE_REF, "firmware", "tests"],
                       capture_output=True)
    if r.returncode:
        pytest.skip(f"no {BASE_REF} in this checkout")
    os.makedirs(tree, exist_ok=True)
    subprocess.run(["tar", "-xf", tar, "-C", tree], check=True)
    return tree


def _base_host(tmp):
    """the host build of BASE_REF's tree"""
    tree = _base_tree(tmp)
    exe = os.path.join(tmp, "frodhes-base")
    subprocess.run(["cc", "-O2", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-I" + os.path.join(tree, "firmware", "src"),
                    "-I" + os.path.join(tree, "firmware", "gen"), "-o", exe,
                    os.path.join(tree, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    return exe


@needs_types
def test_old_records_play_as_before(tmp_path):
    """the settings and a user preset saved by the firmware before the TYPEs (BASE_REF's host build, REVERB MIX 90,
    DECAY 100): the new build reads HALL + the defaults from both, and the panel plays the same samples as the old
    build did (an update keeps the user's sound)"""
    exe = _base_host(str(tmp_path))
    ids = p_ids()
    fl = str(tmp_path / "old.flash")
    setup = [cc(20, 91, 90), cc(21, 92, 100), cc(22, 93, 40), "100 btn 9", "120 turn 2 1", "125 turn 2 1", "140 btn 9"]
    run("rev-old-1", setup, 4500, flash=fl, binary=exe)
    play = ["100 dump"] + note(200, 48, 110, 2750) + note(200, 55, 100, 2750) + ["3000 midi 0xC0 0 0",
                                                                                "3500 midi 0xC0 18 0", "4000 dump"]
    import shutil
    fl2 = str(tmp_path / "old2.flash")
    shutil.copy(fl, fl2)
    a = run("rev-old-2", play, 4100, flash=fl, binary=exe)
    b = run("rev-old-3", play, 4100, flash=fl2)
    for t in (100, 4000):
        for p, v in default_params().items():
            assert b.state(t)[f"p.{ids[p]}"] == str(v), (t, p)
    n = int(2.9 * FS)
    assert np.abs(a.L[int(0.2 * FS):n]).max() > 300
    # (since rev-upsample HALL's half-bands move the wet a little, on purpose: its top octave, and 0.18 ms later. The old
    # records play as the old build played them: every octave band from 63 Hz to 8 kHz within 1 dB (the wet against the
    # dry: +-0.6 dB of comb), the level within
    # 0.1 dB)
    f = np.fft.rfftfreq(n, 1 / FS)
    for x, y in ((a.L[:n], b.L[:n]), (a.R[:n], b.R[:n])):
        x, y = np.asarray(x, float), np.asarray(y, float)
        X, Y = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2
        d = [10 * np.log10(Y[(f >= c / 1.414) & (f < c * 1.414)].sum() / X[(f >= c / 1.414) & (f < c * 1.414)].sum())
             for c in 62.5 * 2.0 ** np.arange(8)]
        print("octaves 63 Hz .. 8 kHz, dB against the old build:", np.round(d, 2))
        assert np.all(np.abs(d) < 1.0), "the old panel sounds different"
        lv = 10 * np.log10(np.sum(y * y) / np.sum(x * x))
        assert abs(lv) < 0.1, f"{lv:+.2f} dB against the old build"


@needs_types
def test_old_v3_bank_new_ids_default(tmp_path):
    """a v3 bank written before the TYPEs (np = 59, whatever is in the bytes beyond): the new controls load at their
    defaults"""
    ids = p_ids()
    dflt = [int(d) for d in re.findall(r'\[P_\w+\] = \{"[^"]*", \d+, (\d+),',
                                        open(os.path.join(ROOT, "firmware", "src", "params.c")).read())]
    recs = []
    for i in range(16):
        p = bytearray(dflt[:59]) + bytearray([0x55] * 5)
        p[19] = 90                                        # (REVERB MIX)
        recs.append(struct.pack("<BBBB", 3, 59, 1 if i == 0 else 0, 0) + b"OLD V3      " + bytes(p) + bytes(16))
    assert len(recs[0]) == V3_LEN
    body = b"".join(recs)
    img = bytearray(b"\xff" * 5 * SECTOR)
    hdr = struct.pack("<IIII", 0x42505246, 3, len(body), zlib.crc32(body)) + body
    img[1 * SECTOR:1 * SECTOR + len(hdr)] = hdr
    fl = str(tmp_path / "v3.flash")
    open(fl, "wb").write(img)
    r = run("rev-v3bank", ["150 midi 0xC0 16 0", "300 dump"], 400, flash=fl, check=False)
    s = r.state(300)
    assert s["header"].startswith("U01")
    for p, v in default_params().items():
        assert s[f"p.{ids[p]}"] == str(v), (p, s[f"p.{ids[p]}"])


def _offsets(tree, fields, tmp, tag):
    src = os.path.join(tmp, f"off-{tag}.c")
    open(src, "w").write("#define main host_main\n#include \"frodhes.c\"\n#undef main\n#include <stddef.h>\n"
                         "int main(void) {\n" + "".join(f'printf("{f} %zu\\n", offsetof(settings_t, {f}));\n'
                                                        for f in fields) +
                         'printf("size %zu\\n", sizeof(settings_t)); return 0; }\n')
    exe = os.path.join(tmp, f"off-{tag}")
    subprocess.run(["cc", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-I" + os.path.join(tree, "firmware", "src"),
                    "-I" + os.path.join(tree, "firmware", "gen"), "-o", exe, src, "-lm"], check=True)
    out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
    return {k: int(v) for k, v in (ln.split() for ln in out.splitlines())}


@needs_types
def test_settings_append_only(tmp_path):
    """every settings_t field of BASE_REF at the same offset now (the reserves excepted: the new fields come out of
    them), 256 bytes, SET_VER 2"""
    tmp = str(tmp_path)
    r = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REF}:firmware/src/params.c"], capture_output=True, text=True)
    if r.returncode:
        pytest.skip(f"no {BASE_REF} in this checkout")
    body = re.search(r"typedef struct \{(\s*uint8_t ver, flags;.*?)\} settings_t;", r.stdout, re.S).group(1)
    fields = []
    for decl in rs._strip(body).split(";"):
        parts = decl.split(None, 1)
        if len(parts) == 2:
            fields += [re.sub(r"\[.*", "", f).strip() for f in parts[1].split(",")]
    keep = [f for f in fields if f and "rsv" not in f and "reserved" not in f]
    assert {"ver", "flags", "panel", "panel2", "preset"} <= set(keep), keep
    old = _offsets(_base_tree(tmp), keep, tmp, "old")
    new = _offsets(ROOT, keep, tmp, "new")
    assert new == old
    assert re.search(r"#define SET_VER 2u", open(os.path.join(ROOT, "firmware", "src", "params.c")).read())


# ---- every new control moves what it should (test 7)

def band_db(w, f0, f1):
    w = np.asarray(w, float)
    m = w[:, 0] + w[:, 1] if w.ndim == 2 else w
    X = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / FS)
    return 10 * np.log10(X[(f >= f0) & (f < f1)].sum() + 1e-9)


@needs_types
@pytest.mark.parametrize("typ", SOUNDING)
def test_low_cut_takes_the_bass_out(exe, silent, typ):
    """60 Hz + 3 kHz in: LOW CUT 127 (800 Hz) takes >= 6 dB more of the wet's 40..100 Hz than of its 2.7..3.3 kHz,
    which moves less than 1.5 dB; LOW CUT 0 is the default (SIZE 100: AUTO's at MIX 100 on HALL and PLATE, and the
    size this was measured at on INFINITE before its AUTO became 64; a pure 3 kHz tone in a tank depends on its size)"""
    if typ in silent:
        pytest.skip("a stub")
    x = rs.sine(FS * 3, 60.0, 5000) + rs.sine(FS * 3, 3000.0, 3000)
    w = {}
    for lc in (0, 127):
        r = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_SIZE=100, RV_LOCUT=lc),
                      blocks=int(3 * FS / BLK))
        w[lc] = r["wet"][FS:]
    lo = band_db(w[0], 40, 100) - band_db(w[127], 40, 100)
    hi = band_db(w[0], 2700, 3300) - band_db(w[127], 2700, 3300)
    assert lo - hi >= 6.0 and abs(hi) < 1.5, (typ, lo, hi)


@needs_types
@pytest.mark.parametrize("typ", SOUNDING)
def test_mod_moves_the_modulation(exe, silent, typ):
    """a 1 kHz sine: the wet's energy off +-6 Hz of it is larger at MOD 127 than at MOD 0 (MOD 0: no wander)"""
    if typ in silent:
        pytest.skip("a stub")
    x = rs.sine(FS * 4, 1000.0, 5000)
    side = {}
    for m in (0, 127):
        w = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_MOD=m, RV_DECAY=100),
                      blocks=int(4 * FS / BLK))["wet"][FS:]
        off = 10 ** (band_db(w, 900, 994) / 10) + 10 ** (band_db(w, 1006, 1100) / 10)
        side[m] = 10 * np.log10(off) - band_db(w, 994, 1006)
    assert side[127] > side[0] + 3.0, (typ, side)


def build_up_ms(w, frac=0.5, span=0.3):
    """the time (ms) the wet takes to reach frac of its energy over its first span s after the burst"""
    w = np.asarray(w, float)
    e = np.cumsum((w[:int(span * FS)] ** 2).sum(axis=1))
    return 1000 * np.argmax(e >= frac * e[-1]) / FS


@needs_types
@pytest.mark.parametrize("typ", SOUNDING)
def test_size_moves_the_room(exe, silent, typ):
    """SIZE 1 (smallest) against SIZE 127 (largest): different wet, the larger one building up later; SIZE 0 (AUTO) on
    HALL is the HALL before (tested above: the defaults)"""
    if typ in silent:
        pytest.skip("a stub")
    x = rs.burst(FS)
    w = {s: rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_SIZE=s), blocks=200)["wet"] for s in (1, 127)}
    assert not np.array_equal(w[1], w[127])
    assert build_up_ms(w[127]) > build_up_ms(w[1]), (typ, build_up_ms(w[1]), build_up_ms(w[127]))


def _peak_hz(w, f0):
    """the strongest frequency within 10 % of f0 (a Hann window, 8x zero padding, a parabola through the top bin)"""
    n = len(w)
    a = np.abs(np.fft.rfft(w * np.hanning(n), 8 * n))
    f = np.fft.rfftfreq(8 * n, 1 / FS)
    i = int(np.argmax(a * ((f > f0 * 0.9) & (f < f0 * 1.1))))
    y0, y1, y2 = np.log(a[i - 1:i + 2] + 1e-9)
    return (i + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)) * FS / (8 * n)


@needs_types
@pytest.mark.parametrize("typ", NEW)
def test_mix_does_not_bend_the_tail(exe, silent, typ):
    """SIZE on AUTO, MIX turned while a tail rings does not bend it (the user, 2026-10-25: turning MIX bent the pitch;
    0.9.5's AUTO size from MIX moved the lines under the tail: INFINITE -50 .. -250 cents, PLATE up to -266): a 440 Hz
    tone, then MIX 64 -> 120 over a second while the tail rings (INFINITE held in FREEZE, PLATE at DECAY 127): the
    tail's pitch within 5 cents of the same take with MIX held, every quarter second from the sweep's start on
    (INFINITE's AUTO is a fixed SIZE 64, sample for sample; PLATE's is MIX latched while it rings)"""
    sounding(silent, typ)
    t0, t1, tz = (3.0, 4.0, 5.5) if typ == "INFINITE" else (1.25, 2.25, 3.0)
    n, b0, b1 = int((tz + 0.5) * FS), int(t0 * FS) // BLK, int(t1 * FS) // BLK
    x = np.zeros(n, np.int64)
    x[:FS] = rs.sine(FS, 440.0, 4000)
    par = dict(RV_TYPE=T[typ], RV_DEPTH=64, RV_DECAY=127, RV_SIZE=0)
    sw = [(b, "RV_DEPTH", 64 + 56 * (b - b0) // (b1 - b0)) for b in range(b0, b1 + 1)]
    held, swept = (rs.render(exe, x, par, s)["wet"][:, 0].astype(float) for s in ((), sw))
    q = FS // 4
    d = [1200 * np.log2(_peak_hz(swept[a:a + q], 440.0) / _peak_hz(held[a:a + q], 440.0))
         for a in range(int(t0 * FS), int(tz * FS), q)]
    assert max(abs(v) for v in d) < 5.0, (typ, ["%+.1f" % v for v in d])
    if typ == "INFINITE":
        w64 = rs.render(exe, x[:2 * FS], dict(par, RV_SIZE=64))["wet"]
        assert np.array_equal(rs.render(exe, x[:2 * FS], par)["wet"], w64)


@needs_types
def test_plate_auto_size_follows_mix_from_silence(exe, silent):
    """PLATE, SIZE on AUTO: MIX changed while the plate is silent sets the size (MIX 40, then 100 at 1 s, the burst
    at 1.5 s: the wet is SIZE 100's, sample for sample); changed while it rings, the size waits for silence (the same
    take with MIX 40 -> 100 during the tail is not SIZE 100's until the tail has ended)"""
    sounding(silent, "PLATE")
    x = np.zeros(3 * FS, np.int64)
    x[int(1.5 * FS):int(1.5 * FS) + FS] = rs.burst(FS)
    par = dict(RV_TYPE=T["PLATE"], RV_DEPTH=40)
    sw = [(FS // BLK, "RV_DEPTH", 100)]
    auto = rs.render(exe, x, dict(par, RV_SIZE=0), sw)["wet"]
    assert np.array_equal(auto, rs.render(exe, x, dict(par, RV_SIZE=100), sw)["wet"])
    late = [(int(1.7 * FS) // BLK, "RV_DEPTH", 100)]
    a, f40, f100 = (rs.render(exe, x, dict(par, RV_SIZE=z), late)["wet"] for z in (0, 40, 100))
    assert np.array_equal(a, f40) and not np.array_equal(a, f100)


@needs_types
@pytest.mark.parametrize("typ", NEW)
def test_tone_and_predelay_on_the_new_types(exe, silent, typ):
    """TONE 0 is darker than TONE 127 (the wet's 4..10 kHz re its 200..800 Hz, >= 6 dB); PRE-DELAY 127 (80 ms) holds
    the wet back: nothing in the first 75 ms after the burst"""
    sounding(silent, typ)
    x = rs.burst(FS, amp=8000)
    r = {}
    for tv in (0, 127):
        w = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_TONE=tv), blocks=300)["wet"][:2 * FS]
        r[tv] = band_db(w, 4000, 10000) - band_db(w, 200, 800)
    assert r[127] - r[0] >= 6.0, (typ, r)
    q = BLK * 20                                         # (silence first: a pre-delay set at power-on glides from
    x = np.concatenate([np.zeros(q, np.int64), x])       # 0 to its tap over 256 samples, fx.c fx_predelay)
    w = rs.render(exe, x, dict(RV_TYPE=T[typ], RV_DEPTH=100, RV_PREDLY=127), blocks=120)["wet"][q:]
    assert not np.any(w[:int(0.075 * FS)]) and np.any(w[int(0.075 * FS):int(0.2 * FS)])


# ---- the levels

OCT = [(177, 354), (354, 707), (707, 1414), (1414, 2828), (2828, 5657)]


@needs_types
@pytest.mark.parametrize("typ", NEW)
def test_levels_match_hall(exe, silent, typ):
    """the same chord at MIX 100, DECAY 64: the TYPE's wet over its first 2 s within 2 dB of HALL's, each octave band
    250 Hz .. 4 kHz within 6 dB (the FFT bands, not the RMS alone)"""
    sounding(silent, typ)
    x = rs.chord(FS, amp=8000)
    w = {t: rs.render(exe, x, dict(RV_TYPE=T[t], RV_DEPTH=100), blocks=int(2 * FS / BLK))["wet"] for t in ("HALL", typ)}
    lv = {t: 20 * np.log10(np.sqrt(np.mean(np.asarray(v, float) ** 2)) + 1e-9) for t, v in w.items()}
    assert abs(lv[typ] - lv["HALL"]) <= 2.0, (typ, lv)
    for a, b in OCT:
        d = band_db(w[typ], a, b) - band_db(w["HALL"], a, b)
        assert abs(d) <= 6.0, (typ, a, b, d)


# ---- the cost

def cost_of(exe, typ, **kv):
    return rs.render(exe, held_chord(BLK * 40), dict(RV_TYPE=T[typ], RV_DEPTH=100, **kv), trace=True)["trace"][-1]


@needs_types
def test_cost_per_type(exe):
    """fx_cost_us with the TYPE running (the voice shedder's view): HALL as before (FX_US_REVERB), PLATE <= HALL,
    INFINITE <= HALL + 200 us (the coordinator's gate), OFF with the tail ended 0; any SIZE / MOD / LOW CUT the same"""
    hall = cost_of(exe, "HALL")
    assert hall == rs.define("FX_US_REVERB")
    assert cost_of(exe, "PLATE") <= hall
    assert cost_of(exe, "INFINITE") <= hall + 200
    knobs = dict(RV_SIZE=1, RV_MOD=127, RV_LOCUT=127)    # (LOW CUT may cost its filter: like for like)
    assert cost_of(exe, "PLATE", **knobs) <= cost_of(exe, "HALL", **knobs)
    for typ in SOUNDING:
        assert cost_of(exe, typ, **knobs) <= hall + 200, typ
    assert rs.render(exe, np.zeros(BLK * 4, np.int64), dict(RV_TYPE=0, RV_DEPTH=100), trace=True)["trace"][-1] == 0


def _costs_json():
    import json
    return json.load(open(os.path.join(ROOT, "tools", "cpu_costs.json")))


@needs_types
def test_cpu_model_rows_follow_the_firmware(exe):
    """tools/cpu_costs.json rev_type_us: each TYPE's cost over HALL, as fx_cost_us counts it (the [E] rows; a device
    run replaces them with [M])"""
    rows = _costs_json()["rev_type_us"]
    hall = cost_of(exe, "HALL")
    for typ in NEW:
        r = rows[typ.lower()]
        if r["src"].startswith("[E]"):
            assert max(r["us"]) == cost_of(exe, typ) - hall, (typ, r["us"], cost_of(exe, typ) - hall)
        elif not rs.is_stub(typ):                        # a kernel landed: its row from fx_cost_us (or measured)
            assert r["src"].startswith("[M]"), f"{typ}: rev_type_us is still the plan's ({r['src']})"


@needs_types
def test_infinite_bright_cost(tmp_path):
    """the bright INFINITE (-DREV_INF_QUARTER=0, the half-rate tank): within HALL + 200 us too, and its
    rev_type_us row (infinite_bright) in step with fx_cost_us"""
    if not re.search(r"#if\s+REV_INF_QUARTER", rs.src_text()):
        pytest.skip("no REV_INF_QUARTER switch in rev_inf.c")
    e = rs.build(["-DREV_INF_QUARTER=0"], tag="bright")
    try:
        hall, inf = cost_of(e, "HALL"), cost_of(e, "INFINITE")
    finally:
        os.remove(e)
    assert inf <= hall + 200, (inf, hall)
    r = _costs_json()["rev_type_us"].get("infinite_bright")
    assert r and (not r["src"].startswith("[E]") or max(r["us"]) == inf - hall), (r, inf - hall)


def test_cpu_budget_every_type():
    """tools/cpu_budget.py predicts every reverb TYPE (HALL = the measured rows, the others their rev_type_us row on
    top); every TYPE within the coordinator's gate (HALL + 200 us); each release scenario with each TYPE within the
    target, apart from test_cpu_budget.py's KNOWN_OVER (the same scenarios with HALL)"""
    import cpu_budget as cb
    from test_cpu_budget import KNOWN_OVER
    t = cb.load()
    assert cb.rev_types(t)[0] == "hall" and {"plate", "infinite"} <= set(cb.rev_types(t))
    for typ in cb.rev_types(t):
        assert cb.rev_delta(t, typ) <= 200 and (typ != "plate" or cb.rev_delta(t, typ) <= 0), typ
        for p in cb.scenarios(t, reverb=typ):
            base = cb.predict(t, p["engine"], p["voices"], bend=p["bend"])
            assert p["whole"] == base["whole"] + cb.rev_delta(t, typ), (typ, p)
            if (p["engine"], p["voices"]) not in KNOWN_OVER and (p["bend"] <= cb.firmware_has_bend()):
                assert p["whole"] <= t["target_us"], (typ, p)
