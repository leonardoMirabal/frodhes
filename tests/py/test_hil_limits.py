# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py: the rows that failed on every version (2026-10-24, the faster release test plan: no permanent
red). Each was decided from the saved FM-1 reports (build/hil, build/marimba/p5, build/pr4test, build/release-0.9.4,
build/final) and the host build, which reads the same numbers as the FM-1's USB:

- fx_all: the phaser has no width compensation (the chorus' -2.5 LU target judged 'phaser 127 fb127'); 'drive 127'
  is judged against the level the effect sets by design (FXALL_EXPECT; 'phaser 32' was too, at -2.5, until the
  phaser's level was held at every DEPTH in 0.9.6: now 0 +- 1); 'wah 32' / 'wah 127' failed
  (-2.1 / +2.1 LU): fx.c's wah depth law was applied on top of the 0.9.3 make-up gain, a defect fixed 2026-10-24
  (the depth law removed; test_fx_all_wah_level below);
- vibes_jump: the phaser held on, fx_all's 2 LU (it failed 1.5 LU by 0.05);
- vibes_voice: the jack's distances over the bands the sound has on USB (the jack's own harmonics diluted them);
- vibes_fx COMP ATTACK: the strike's level, not its first peak; PHASER FDBK: the take-to-take spread in quadrature."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import hil_scan as h  # noqa: E402


# ---- the jack's harmonics and sdist
def test_sdist_on_usbs_bands_ignores_bands_both_takes_share():
    """two takes that differ in two partials; the jack adds harmonics of its own at -50 .. -57 dB, the same in both
    (FM-1, F3 v100: H2 -56, H3 -50, H5 -57 dB re the strike, absent on USB): sdist's mean over every band within 60 dB
    dilutes the distance; over USB's bands the jack reads what USB reads"""
    a = np.full(26, -100.0)
    a[[5, 6, 11, 16, 17]] = [0, -35, -19, -20, -48]
    b = a.copy()
    b[[16, 17]] = [-16, -41]                       # MALLET 64 -> 127 on F3 (USB, the FM-1): +4 and +7 dB
    ja, jb = a.copy(), b.copy()
    for k, v in ((8, -56), (10, -50), (12, -57), (13, -55), (14, -54)):
        ja[k] = jb[k] = v                          # the jack's harmonics, the same in both takes
    usb = h.sdist(a, b)
    assert usb >= 3.0
    assert h.sdist(ja, jb) < 3.0                   # the row's FAIL on the jack
    assert abs(h.sdist(ja, jb, h.sdist_bands(a, b)) - usb) < 0.05
    assert h.sdist(a, b, h.sdist_bands(a, b)) == usb


# ---- PHASER FDBK: the spread in quadrature
@pytest.mark.parametrize("d, spread, ok", [
    (4.1, 0.0, True), (4.1, 0.6, True),           # the FM-1's USB (0.9.4, the final 0.9.4 scan)
    (4.3, 2.3, True), (4.2, 2.4, True),           # the FM-1's jack, the same takes: failed d >= 2 x spread
    (3.5, 1.2, True), (3.4, 1.0, True),           # MARIMBA (passed either way)
    (1.9, 0.0, False), (2.0, 0.5, False),          # not apart
    (3.0, 2.9, False), (2.4, 2.4, False),          # two takes of one setting differ as much as the settings
])
def test_apart_of_spread(d, spread, ok):
    assert bool(h.apart_of_spread(d, spread)) == ok


# ---- the host build, played as the suites play the FM-1
def test_fx_all_on_the_host_passes():
    """every fx_all setting within its limit; 'drive 127' at its FXALL_EXPECT value, 'phaser 32' at 0 +- 1 (0.9.5 read
    -2.5: the phaser's level drop, fixed in 0.9.6), 'phaser 127 fb127' at 0 +- 2 (no width target)"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.fx_all(r, rep)
    rows = {q["name"]: q["usb_lu"] for q in rep["fx_all"]["rows"]}
    for name, (target, tol) in h.FXALL_EXPECT.items():
        assert abs(rows[name] - target) <= tol, (name, rows[name])
    assert abs(rows["phaser 127 fb127"]) <= 2.0
    assert abs(rows["phaser 32"]) <= 1.0, rows["phaser 32"]
    assert ok and "out of tolerance: none" in msg, msg


def test_fx_all_wah_level():
    """the wah holds the clean level at every depth (fx.c fx_trem: the 0.9.3 make-up gain alone; the depth law wg of
    dba3831, x0.80 .. x1.26 on top of it, read -2.1 / +2.1 LU at DEPTH 32 / 127; without it -0.1 / +0.0)"""
    r, rep = h.HostRig(1), {}
    h.fx_all(r, rep)
    rows = {q["name"]: q["usb_lu"] for q in rep["fx_all"]["rows"]}
    assert abs(rows["wah 32"]) <= 0.5 and abs(rows["wah 127"]) <= 0.5, rows


@pytest.mark.parametrize("engine, notes", [
    ("rhodes", ((48, 108), (55, 108), (64, 108))), ("rhodes", ((60, 100),)), ("rhodes", ((72, 100),)),
    ("vibes", ((48, 108), (55, 108), (64, 108))), ("marimba", ((48, 108), (55, 108), (64, 108))),
])
def test_wah_level_every_depth(engine, notes):
    """the wah's loudness re dry (LUFS, as fx_all reads it) at DEPTH 0 .. 127, RATE 64: within 0.5 LU of dry and
    within 0.5 LU across the depths, on the Rhodes, VIBES and MARIMBA, chords and single notes (the depth law wg gave
    -2.9 .. +2.0 LU on the Rhodes chord). Measured: the Rhodes chord -0.1 .. +0.1, C4 0.0, C5 -0.2, VIBES -0.2 .. +0.1,
    MARIMBA -0.5 .. -0.1. Each take on a fresh rig of the same seed: every take has the same preroll, latency and
    onsets (0.9.10: C3 and G3's shared 392 Hz partial beats, so the chord's loudness over 1.8 s moved 0.45 LU with the
    rig's +-1 ms onset jitter between takes, wah or none)"""
    sel = dict(TYPE=8) if engine == "rhodes" else {}
    bar = {"vibes": 64, "marimba": 127}.get(engine)
    ev = [(0.1, "on", n, v) for n, v in notes] + [(2.1, "off", n, 0) for n, v in notes]

    def lv(**kv):
        r = h.HostRig(1)
        r.dry(**sel, **kv)
        if bar is not None:
            r.cc_raw(79, bar)
        u = r.take(2.6, ev) / 32768.0
        a = np.abs(u).max(axis=1)
        i = int(np.argmax(a > 0.3 * a.max()))
        return h.lufs(u[i:i + int(1.8 * h.FS)])
    base = lv()
    d = [lv(TW_SW=127, TW_DEPTH=x, TW_RATE=64) - base for x in (0, 32, 64, 100, 127)]
    assert max(abs(x) for x in d) <= 0.5, d
    assert max(d) - min(d) <= 0.5, d


@pytest.mark.parametrize("engine, keys", [
    ("rhodes", (36, 48, 60, 72, 84, 96)), ("vibes", (36, 48, 60, 72, 84, 96)), ("marimba", (36, 48, 60, 72)),
])
def test_wah_level_across_the_keyboard(engine, keys):
    """single notes C2 .. C7 at v40 / v100, RATE 0 / 127 (the resonance), DEPTH 64: within 1.0 LU of dry. The make-up's
    46 ms envelopes and x8 ceiling lost a soft high note under a closed resonant wah (Rhodes C6 v40 RATE 127 -3.4,
    C7 -10.6 LU; VIBES C7 -9.7); now the worst is 0.7 LU. MARIMBA up to C5: its top octave's notes are a few tens of
    ms long and still lose 1-4 LU from C6 (fx.c fx_trem's note)"""
    r = h.HostRig(1)
    sel = dict(TYPE=8) if engine == "rhodes" else {}
    bar = {"vibes": 64, "marimba": 127}.get(engine)

    def lv(k, v, **kv):
        r.dry(**sel, **kv)
        if bar is not None:
            r.cc_raw(79, bar)
        u = r.take(2.6, [(0.1, "on", k, v), (2.1, "off", k, 0)]) / 32768.0
        a = np.abs(u).max(axis=1)
        i = int(np.argmax(a > 0.3 * a.max()))
        return h.lufs(u[i:i + int(1.8 * h.FS)])
    bad = {}
    for k in keys:
        for v in (40, 100):
            base = lv(k, v)
            for rate in (0, 127):
                d = lv(k, v, TW_SW=127, TW_DEPTH=64, TW_RATE=rate) - base
                # (C7 v40 under the closed resonant wah: 1.25 LU. RESPONSE FULL's deeper soft end (0.9.10, rh_dyn -9 dB
                # at v20) puts that note at -50.8 LUFS, where the make-up reads soft notes a little loud: +1.08 LU
                # (+1.5 at -56 LUFS in either RESPONSE: the wah's own, open))
                if abs(d) > (1.25 if (engine, k, v, rate) == ("rhodes", 96, 40, 127) else 1.0):
                    bad[(k, v, rate)] = round(d, 2)
    assert not bad, bad


def test_presets_on_the_host_every_preset_on_target():
    """hil_scan's presets suite on the host (the FM-1's USB to 0.1 LU): F01..F19, each preset re F01 within its
    PRESET_TARGET (the Rhodes F01..F11 0 +- 1.5, F12 AUTO WAH's deep sweep 0 +- 2.0, VIBES F13 / F14 +2.5 +- 1.0, MARIMBA F15 -0.2, F16 +2.5 +- 1.0,
    F17..F19 0 +- 1.0: the starters U10..U12 until 0.9.5's RC).
    Before 2026-10-24: F07 -1.9, F08 -1.7, F10 -3.9, F09 +1.47, F13 +3.4, F14 +2.9; the 0.9.5 presets: F02..F12
    -0.7..+0.8, F13 +2.6, F14 +2.0, F15 -0.2, F16 +2.0. 2026-10-25: F11 LEVEL 18 -> 10 (the FM-1 read +1.8, the
    host -0.1; now the host -1.6, judged at HOST_SHIFT's -1.9). 0.9.7: the FM-1 read -1.7 at LEVEL 10 (0.9.5 -1.8, 0.9.6
    -1.7 / -2.0), so LEVEL 18: the host +0.0 (HOST_SHIFT +0.2, the FM-1 ~0.2 under the host). The drive-type merge: F03 +1.1, F07 +1.3, F08 +1.1 (driven:
    MANUAL 'Factory presets', within 1.5 LU; the 1 LU is the chord-matched ones', not a limit for every Rhodes preset).
    0.9.5: the user bank's starters U01..U09 follow (presets 20..28 in the rows), each within 1 LU of F01.
    2026-11-08: the headroom trims put F04 -2.2, F08 -3.6, F13 +1.0, F15 -1.5, F16 -5.0 (hil_scan PRESET_TARGET); the clean
    Rhodes (the physical swing): F11 LEVEL 35, F19 90, U08 75 back to F01's level, F14 +1.2 (headroom), F16 / U07 one step down.
    2026-11-09: 0.9.9's level and balance again (fx-level): F09 +2.0, F13 +3.1, F14 +3.2, F15 +0.2, F16 -1.8, F18 +1.1,
    U02 +0.9, U04 +1.2; then F01 LEVEL +2 (the TYPE panel against 0.9.9) and F11 +2 with it: F09 +1.4, F12 +2.3,
    F13 +2.4, F14 +2.6, F15 -0.4, F16 -2.4, F18 +0.5, F19 +0.6, U02 -0.3, U04 +0.7; F16 LEVEL 25 (no limiter on
    normal playing): -5.4, and by the same rule F13 +0.6, F14 +0.7, F15 -3.8 (hil_scan PRESET_TARGET).
    2026-11-10, FINAL19 v2 (each preset at 0.9.9's of the same name by cb): the targets re-measured by name (hil_scan)"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.presets(r, rep)
    every = list(range(1, h.PR_COUNT + h.PR_STARTERS + 1))       # F01..F19, then the starters U01..U09 (the host only)
    assert h.PR_COUNT == 19 and [q["preset"] for q in rep["presets"]] == every
    lv = [q["level"] for q in rep["presets"]]
    for p, v in zip(every, lv):
        target, tol = h.preset_target(p, host=True)   # (F11: the host reads it 0.2 LU over the FM-1, HOST_SHIFT)
        assert abs(v - lv[0] - target) <= tol, (p, round(v - lv[0], 2))   # (the Rhodes F01..F11: 1.5 LU, the spec; F12 2.0)
    # (F11 RESO SWEEP at F01's level until FINAL19 v2; now 0.9.9's RESO SWEEP by cb: its target, above)
    assert ok and "off target: none" in msg, msg


def test_comp_attack_by_the_strikes_level():
    """vibes_fx's COMP ATTACK takes on the host (20:1, THRESH 100, the chord v108): the strike's level (strike_db)
    rises with ATTACK, 0 -> 127 by far more than the 2 dB limit; the old crest sat on it (+2.0)"""
    import test_phaser_measures as tpm
    cb = [("CMP_SW", 1), ("CMP_AMT", 100), ("CMP_RATIO", 7), ("CMP_GAIN", 0), ("CMP_ATK", 40), ("CMP_REL", 50)]
    T = {x: tpm.take(f"atk{x}", h.with_(cb, ("CMP_ATK", x)), stim=h.ST_CH) for x in (0, 40, 127)}
    s = {x: h.strike_db(T[x].m("usb"), T[x].on("usb")[0]) for x in T}
    c = {x: h.crest_db(T[x].m("usb"), T[x].on("usb")[0]) for x in T}
    assert s[0] < s[40] < s[127], s
    assert s[127] - s[0] >= 6.0, s
    # THRESHOLD (stage 2, 2026-11-10): c[127] - c[0] < 3.0 -> < s[127] - s[0]. The stage-2 compressor (the make-up
    # only up to the reduction) lets a slow attack's strike through unlifted: the crest moves too (10.97 / 14.27 /
    # 15.86 dB), still far less than the strike's level (-3.48 / -0.38 / +5.82)
    assert c[127] - c[0] < s[127] - s[0], (c, s)    # the old measure: less than the strike's level


# ---- the T2 limits refreshed after 0.9.10's sound (2026-11-13): each on the host, where it reads what the FM-1 read
# with 0.9.10 and 0.9.11 (T2 2026-11-12; the THRESHOLD notes in tools/hil_scan.py)
def test_held_tail_on_the_host_closes_inside_the_take():
    """the held A3 (REV 40): 0.9.10's longer hold closes the gate at 15.33 s (FM-1 15.3, 0.9.9 7.5); the limit stays
    1 s before the take's end, so a gate that never closes still fails"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.held_tail(r, rep)
    last = rep["held_tail"]["last_nonzero_s"]
    assert ok, msg
    assert 14.5 <= last < h.HELD_TAIL_MAX <= h.HELD_TAIL_S - 1.0, last


def test_bypass_on_the_host_within_the_refreshed_limit():
    """bypass: the FX button keeps the compressor, the phaser 80 reads -2.5 LU by design: effects on 3.07 LU under the
    bypass on the host (the mean of CHORDS_13 since 0.9.13; A3-C4-E4 alone 2.90; FM-1 3.2), under BYPASS_FX_LU (3.6);
    bypassed re the compressor alone within 0.5"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.bypass(r, rep)
    b = rep["bypass"]
    assert ok, msg
    assert 2.5 <= b["bypassed"] - b["fx_on"] < h.BYPASS_FX_LU == 3.6, b
    assert abs(b["bypassed"] - b["comp_only"]) < 0.5, b


@pytest.mark.parametrize("suite", ["vibes_jump", "marimba_jump"])
def test_jump_rows_on_the_host(monkeypatch, suite):
    """the rows the FM-1 failed with 0.9.10 and 0.9.11 on stale limits pass on the host at the values the FM-1 read:
    the phaser 80 held on (FXALL_EXPECT -2.5 +- 1), the bypass re all on's compressor alone, MARIMBA re ST77 (-1.7)"""
    monkeypatch.setattr(h, "ENGINE", "marimba" if suite.startswith("marimba") else "vibes")
    V = h.VRig("usb", host=1)
    S = h.VSuite(suite, V)
    h.VTESTS[suite](V, S)
    rows = {(q["check"], q["setting"].split(",")[0]): q for q in S.rows}
    want = [("phaser 80: on vs off", "held on"),
            ("FX bypass (CC 111) with everything on", "re the compressor alone (all on's)")]
    if suite == "marimba_jump":
        want.append(("MARIMBA re ST77, A3-E4-A4 v100", "LUFS 0..1 s"))
        assert h.MLEVEL_DB == -1.7
        want.append(("delay DIG 50: switched under a chord", "the step at on / at off (fx_toggle)"))   # (E2: +2.02)
    else:                                          # (E2, T2 0.9.12: the echoes on a whole dry, +1.56 / +2.19 LU)
        want += [("delay DIG 50: on vs off", "held on"), ("delay ANA 50: on vs off", "held on"),
                 ("reverb 50 (no switch: REV 0 -> 50): switched under a chord", "the step at on / at off (fx_toggle)")]
    for k in want:
        assert k in rows and rows[k]["status"] == "ok", (k, rows.get(k))
    assert rows[("FX bypass (CC 111) with everything on", "re no effects")]["status"] == "info"


# ---- T2 0.9.12 (2026-11-14): the rows the FM-1 failed on measures 0.9.10's dry width or 0.9.12's MIX law outdated
def _vsuite(monkeypatch, suite):
    monkeypatch.setattr(h, "ENGINE", "marimba" if suite.startswith("marimba") else "vibes")
    V = h.VRig("usb", host=1)
    S = h.VSuite(suite, V)
    h.VTESTS[suite](V, S)
    return {q["check"]: q for q in S.rows}


def _num(cell):
    return float(cell.split()[0].replace("+", ""))


@pytest.mark.parametrize("suite, check", [("vibes_vel", "keys F3..F6: neighbour step"),
                                          ("marimba_vel", "keys C2..C7: neighbour step")])
def test_key_evenness_as_heard(monkeypatch, suite, check):
    """the dry width (fx_width: S = -0.2 x the 570-4000 Hz band 10 ms back) is a 100 Hz comb on one channel: USB L
    zigzags 2.5 dB key to key (the FM-1 2.48-2.58 on USB L and the jack since 0.9.10), the stereo sum 0.47 / 0.48
    (0.9.9's FM-1: 0.48). The gate reads stereo; the one-channel row is information and shows the comb"""
    rows = _vsuite(monkeypatch, suite)
    st, one = rows[check], rows[check + ", one channel"]
    assert st["status"] == "ok" and _num(st["usb"]) <= 1.0, st
    assert one["status"] == "info" and _num(one["usb"]) >= 2.0, one


@pytest.mark.parametrize("suite", ["vibes_fx", "marimba_fx"])
def test_predelay_and_delay_mix_rows(monkeypatch, suite):
    """REVERB PRE-DELAY by the side signals' cross-correlation (the envelopes read 0 ms for 40 under the width's own
    side); DELAY DEPTH 50 -> 127 by the 1st echo's own level, E2's +2.14 dB (FX_DLY_WET 25600 -> 32768; the old row
    read the 2nd echo re the 1st at 127, where the dry is gone)"""
    rows = _vsuite(monkeypatch, suite)
    for x, ex in ((64, 40), (127, 80)):
        r = [q for k, q in rows.items() if k.startswith("REVERB PRE-DELAY") and k.endswith(f" {x}")][0]
        assert r["status"] == "ok" and abs(_num(r["usb"]) - ex) <= 8, r
    r = rows["DELAY DEPTH 50 -> 127"]
    assert r["status"] == "ok" and abs(_num(r["usb"]) - 2.14) <= 0.5, r


def test_effects_reverb_127_is_the_hall_alone():
    """E2: REV 127 is the reverb alone; 'the direct sound' is the hall's early part, -8.1 LU (target -8.1 +- 3)"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.effects(r, rep)
    row = [q for q in rep["effects"]["rows"] if q["name"] == "reverb 127"][0]
    assert ok, msg
    assert abs(row["delta_lu"] + 8.1) <= 0.5 and row["lr_corr"] < 0.3, row


class _GapRig(h.HostRig):
    """the host rig with a recording gap (Rig.both's xruns) in the takes listed: the call count, 1-based"""

    def __init__(self, gapped):
        super().__init__(1)
        self.gapped, self.calls, self.xruns = set(gapped), 0, []

    def _rec(self, dur, events):
        self.calls += 1
        self.xruns = [("usb", "gap +11 ms at 0.52 s"), ("usb", "gap -11 ms at 0.54 s")] if self.calls in self.gapped else []
        return super()._rec(dur, events)


@pytest.mark.parametrize("gapped, flagged", [((2, 5, 6), False), ((2, 3, 4), True)])
def test_presets_take_a_gapped_recording_again(monkeypatch, gapped, flagged):
    """0.9.12's T2 failed presets on one F15 take with a step-and-back gap pair (its three levels within 0.0005 LU):
    a take with a recording gap is taken again (PR_GAP_RETAKES 2), flagged only if every retake has one too"""
    monkeypatch.setitem(h.REPEATS, "presets", 1)
    monkeypatch.setattr(h, "PR_CHORDS", [h.CH_NOTES])     # (one take a preset: the call count as before 0.9.13)
    monkeypatch.setattr(h, "PR_STARTERS", 0)
    r, rep = _GapRig(gapped), {}
    ok, msg = h.presets(r, rep)
    rows = rep["presets"]
    assert h.PR_GAP_RETAKES == 2 and len(rows) == h.PR_COUNT
    if flagged:                                       # F02's take, then both retakes: gapped
        assert not ok and "a recording gap at ['F02']" in msg, msg
        assert rows[1]["gap_retakes"] == 2 and rows[1]["gaps"] == 2
    else:                                             # F02 once, F04 twice: each retake clean
        assert ok, msg
        assert [q["gap_retakes"] for q in rows[:5]] == [0, 1, 0, 2, 0] and not any(q["gaps"] for q in rows)


@pytest.mark.parametrize("fault", [False, True])
def test_lfo_shape_square_steeper_than_sine(monkeypatch, fault):
    """vibes_fx LFO SHAPE: SQR's steepest slope >= 2x SINE's (lfo_sweep + lfo_steep). 0.9.12's T2 read SINE 13.7 on
    USB and 7.2 on the jack of one take (the 99th percentile over the trace took the decayed chord's dips into the
    16-bit LSBs); with the floor and the half cycles' median the host reads SINE ~7, SQR ~22-23. The fault (SINE
    played for SQR) must fail: ~1x"""
    hs = h
    monkeypatch.setattr(hs, "ENGINE", "vibes")
    P = hs.vp()
    e = hs.lfo_hz(P["LFO_RATE"]["de"])
    V = hs.VRig("usb", host=1)
    fb = [("FL_TYPE", 1), ("FL_FREQ", 64), ("FL_RESO", 0), ("FL_LFO", 0), ("LFO_SHAPE", 0),
          ("LFO_RATE", P["LFO_RATE"]["de"]), ("LFO_SYNC", 0), ("LFO_RETRIG", 0)]

    def run(label, st):
        V.reset(st, label=label)
        return V.take(label, *hs.ST_LONG)
    ref = run("ref", fb)
    lb = hs.with_(fb, ("FL_LFO", 127))
    s0 = run("sine", hs.with_(lb, ("LFO_SHAPE", 0)))
    s4 = run("sqr", hs.with_(lb, ("LFO_SHAPE", 0 if fault else 4)))
    a, b = (hs.lfo_steep(*hs.lfo_sweep(t, ref, "usb", 0.2, 5.3, 250, 2000), e) for t in (s0, s4))
    assert 5 <= a <= 9, a
    if fault:
        assert b < 1.3 * a, (a, b)
    else:
        assert b >= 2.8 * a, (a, b)
    # the same SINE take requantised to 8-LSB steps (a 13-bit capture): the floor holds it (61 without it, seed 3)
    x = np.round(s0.m("usb") * 4096) / 4096

    class Q:
        def m(self, s):
            return x

        def on(self, s):
            return s0.on(s)

        def pre(self, s):
            return s0.pre(s)
    assert abs(hs.lfo_steep(*hs.lfo_sweep(Q(), ref, "usb", 0.2, 5.3, 250, 2000), e) - a) < 0.5


def test_c1_known_only_on_the_two_core_image(monkeypatch):
    """C1_KNOWN (user decision 2026-11-14): only for the two-core image (FRODHES_IMAGE=c1 / --c1, or the host's
    HOST_DEFS -DFR_C1=1), each within its band; the limits are the one-core ones (BYPASS_FX_LU 3.6, U04's target +0.5 +-
    1.0). 0.9.13: the two-core reverb's 'bypass' and 'presets U04' items are gone (the HALL / INFINITE level in band
    and the chord-robust rows); 'types' went in 0.9.12 (A2): none is left"""
    monkeypatch.delenv("FRODHES_IMAGE", raising=False)
    monkeypatch.delenv("HOST_DEFS", raising=False)
    assert not h.c1_image()
    monkeypatch.setenv("HOST_DEFS", "-DFR_C1=1")
    assert h.c1_image()
    monkeypatch.delenv("HOST_DEFS")
    monkeypatch.setenv("FRODHES_IMAGE", "c1")
    assert h.c1_image()
    assert h.BYPASS_FX_LU == 3.6 and h.preset_target(h.PR_COUNT + 4, host=True) == (0.5, 1.0)
    assert h.C1_KNOWN == {}                   # (0.9.12: the TYPEs their own character again; 0.9.13: the two-core
                                              # reverb's bypass and U04 inside the one-core limits)


# ---- 0.9.13: the chord-robust rows (bypass and the effects' reverb rows on CHORDS_13, presets on PR_CHORDS) and the
# reverb's fault injection
def test_chord_sets():
    """CHORDS_13: A3-C4-E4, then 12 roots E1..C6, voicings in turn; PR_CHORDS: six in the targets' register; no
    octave inside a chord (CH_NOTES' rule)"""
    assert len(h.CHORDS_13) == 13 and h.CHORDS_13[0] == h.CH_NOTES == h.PR_CHORDS[0] and len(h.PR_CHORDS) == 6
    assert min(min(c) for c in h.CHORDS_13) == 28 and max(c[0] for c in h.CHORDS_13) == 84
    for c in h.CHORDS_13 + h.PR_CHORDS:
        assert len(set(c)) == 3 and all((b - a) % 12 for a in c for b in c if b > a), c


def _fault_rig():
    """a two-core host build with the HALL and INFINITE returns 3 dB down (RQH_OUT, RI_OG x 0.708)"""
    import hostrun
    src = open(os.path.join(hostrun.ROOT, "firmware", "src", "rev_hq_hall.c")).read() + \
        open(os.path.join(hostrun.ROOT, "firmware", "src", "rev_hq_inf.c")).read()
    import re
    o = int(re.search(r"#define RQH_OUT (\d+)", src).group(1))
    g = int(re.search(r"#define RI_OG (\d+)", src).group(1))
    b = os.path.join(hostrun.HOST, "fault-rev3db", "frodhes")
    hostrun.build(b, extra=["-DFR_C1=1", f"-DRQH_OUT={o * 708 // 1000}", f"-DRI_OG={g * 708 // 1000}"])

    class R(h.HostRig):
        def _rec(self, dur, events):
            orig = hostrun.run
            hostrun.run = lambda *a, **k: orig(*a, **dict(k, binary=b))
            try:
                return super()._rec(dur, events)
            finally:
                hostrun.run = orig
    return R(1)


def test_effects_reverb_rows_fail_a_reverb_3db_quiet():
    """the fault injection: the reverb 3 dB quiet must fail. The reverb alone on the 13 chords' mean, +-1.5 LU, fails
    it (host: -11.1 against -8.1, -17.4 against -14.3); the bypass row (REV 50 under the phaser and chorus: the
    reverb adds ~0.3..0.55 LU there) and the presets (U04: INFINITE under a delay and a chorus) move under 0.3 LU with
    it, inside their limits, on one chord as on the mean (U04 -0.55 either way): they do not see the reverb's level,
    the effects' reverb-alone rows do"""
    r, rep = _fault_rig(), {}
    ok, msg = h.effects(r, rep)
    assert not ok and "'reverb 127'" in msg and "'reverb INF 127'" in msg, msg
    rows = {q["name"]: q for q in rep["effects"]["rows"]}
    assert rows["reverb 127"]["delta_lu"] < -9.6 and rows["reverb INF 127"]["delta_lu"] < -15.8, rows


def test_effects_reverb_rows_on_the_chords_mean():
    """the reverb rows on the host: their mean of CHORDS_13 at the targets (reverb 127 -8.1, INF -14.3, +-1.5), each
    chord reported"""
    r, rep = h.HostRig(1), {}
    ok, msg = h.effects(r, rep)
    assert ok, msg
    rows = {q["name"]: q for q in rep["effects"]["rows"]}
    for n, t in (("reverb 127", -8.1), ("reverb INF 127", -14.3)):
        assert len(rows[n]["chord_lu"]) == 13 and abs(rows[n]["delta_lu"] - t) <= 1.0, rows[n]
