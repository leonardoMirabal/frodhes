# SPDX-License-Identifier: GPL-3.0-only
"""The release diff's own logic (tools/release_diff.py; the gate itself runs as `make pristine`, the first stage of
make test and make pytest, and `make release-diff`): the pristine ceilings hold whatever the expected-changes file
lists, an unlisted move fails in either direction, a listed one needs a reason, and the reference files belong to
the last release."""
import json
import os
import re
import sys

from hostrun import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_diff as rd  # noqa: E402

CID = "fm1/ST77/FXOFF/A"


def cells(**kw):
    c = {"level": -20.0, "peak": -6.0, "H2": -20.0, "H3": -45.0, "H4": -70.0, "H5": -80.0, "H6": -85.0, "inh": -40.0,
         "cent": 400.0, "decay": 6.0, "keyup": -12.0, "clk": 10.0, "clk_off": 10.0}
    c.update(kw)
    return {CID: {"k33v127": c, "script": {"clip": 0, "noise": -120.0}}}


def verdict(cand, expected=()):
    fails, moves, stale = rd.judge(cand, cells(), list(expected))
    return fails


def test_the_same_sound_passes():
    assert verdict(cells()) == []


def test_the_0910_distortion_fails_even_when_listed():
    """H3 at H1's level at v127 (the 0.9.10 fault): a ceiling, whatever the list says"""
    f = verdict(cells(H3=7.9), [{"match": "*", "metric": "*", "reason": "the sonic improvements"}])
    assert any(x[2] == "H3" and x[6].startswith("CEILING") for x in f)


def test_quiet_harmonics_may_move_below_the_floor():
    assert verdict(cells(H5=-65.0, H6=-70.0)) == []


def test_unlisted_moves_fail_both_ways():
    assert verdict(cells(level=-17.0)) and verdict(cells(level=-23.0)) and verdict(cells(H2=-26.0))


def test_a_listed_move_needs_a_reason_and_its_range():
    e = {"match": f"{CID}/k33*", "metric": "level", "min": 2.0, "max": 4.0}
    assert verdict(cells(level=-17.0), [dict(e, reason="louder on purpose (commit x)")]) == []
    assert verdict(cells(level=-17.0), [dict(e, reason="TODO")])
    assert verdict(cells(level=-15.0), [dict(e, reason="louder on purpose")])        # beyond its max


def test_clips_and_key_up():
    assert verdict({CID: dict(cells()[CID], script={"clip": 3, "noise": -120.0})})
    raw = {"fm1/ST77/RAW/A": cells()[CID]}
    raw["fm1/ST77/RAW/A"] = dict(raw["fm1/ST77/RAW/A"], script={"clip": 3, "noise": -120.0})
    assert not [f for f in rd.judge(raw, {"fm1/ST77/RAW/A": cells()[CID]}, [])[0] if f[6].startswith("CEILING")]
    f = verdict(cells(keyup=1.0), [{"match": "*", "metric": "keyup", "reason": "x"}])
    assert any(x[6].startswith("CEILING") for x in f)


def test_the_baseline_is_the_last_release_and_covers_every_case():
    doc = json.load(open(rd.BASE_FAST))
    assert doc["release"] == rd.LAST
    assert {c[0] for c in rd.cases("fast")} == set(doc["cases"])


def test_the_expected_changes_are_against_the_last_release():
    import yaml
    doc = yaml.safe_load(open(rd.EXPECTED))
    assert doc["against"] == rd.LAST
    for i, e in enumerate(doc.get("changes") or []):
        assert e.get("match") and str(e.get("reason", "")).strip() and not str(e["reason"]).upper().startswith("TODO"), i


def test_the_hq_build_is_the_makefiles():
    mk = open(os.path.join(ROOT, "Makefile")).read()
    move = re.search(r"^MOVE_DEFS \?= (.*)$", mk, re.M).group(1).split()
    assert rd.HQ_DEFS == move + ["-DHQ_DEFAULT=1"]


def test_the_two_core_build_is_the_package_and_the_fm1_verdict():
    """the c1 build (2026-11-14: the FM-1 ships the two-core package only) is tools/build.py --c1's app defines, and
    full mode judges it on every case the one-core FM-1 build has (TYPEs, voice controls, presets), against the last
    release's own c1 build; the fast grid (make pristine) keeps fm1 + hq"""
    src = open(os.path.join(ROOT, "tools", "build.py")).read()
    line = re.search(r'BOARD_APP_DEFS\[BOARD\], "-DFR_C1=1"\] \+ \(\[\] if a\.c1_256 else \["(-DFR_C1_LAT=1)"\]\)', src)
    assert line and rd.C1_DEFS == ["-DFR_C1=1", line.group(1)]
    full = [c for c in rd.cases("full")]
    fm1 = {c[0].split("/", 1)[1]: c for c in full if c[1] == "fm1"}
    c1 = {c[0].split("/", 1)[1]: c for c in full if c[1] == "c1"}
    assert fm1 and set(c1) == set(fm1)
    for k, c in c1.items():
        assert c[2:] == fm1[k][2:], k                  # the same setup, keys and kind
    assert not [c for c in rd.cases("fast") if c[1] == "c1"]
    calls = []
    real = rd.cc_build
    try:
        rd.cc_build = lambda tree, path, defs=(): calls.append((os.path.basename(path), list(defs))) or path
        assert set(rd.tree_bins(ROOT, "/nonexistent", c1=True)) == {"fm1", "hq", "c1"}
        assert ("c1", rd.C1_DEFS) in calls
        assert set(rd.tree_bins(ROOT, "/nonexistent")) == {"fm1", "hq"}
    finally:
        rd.cc_build = real


def _quiet_note(amp, n, ph=0.0):
    import numpy as np
    t = np.arange(n) / rd.FS
    return np.round(amp * np.sin(2 * np.pi * 440.0 * t + ph))


def test_key_rise_fails_a_cut_at_the_key_up():
    """clk_off where the note sounds at the key-up (CLK_RISE, 2026-11-09): a quiet note's int16 steps pass, a cut fails
    (a step injected at the key-up), at -70 dBFS and at -20 dBFS"""
    import numpy as np
    n_pre, n_tail = int(0.2 * rd.FS), int(0.3 * rd.FS)
    for amp in (10.0, 3000.0):
        x = _quiet_note(amp, n_pre + n_tail)
        pre, tail = x[:n_pre], x[n_pre:].copy()
        fade = np.round(amp * np.sin(2 * np.pi * 440.0 * np.arange(n_pre, n_pre + n_tail) / rd.FS)
                        * np.exp(-np.arange(n_tail) / (0.01 * rd.FS)))     # a 10 ms damper: no click
        assert rd.key_rise(fade, pre) <= rd.CLK_RISE, (amp, rd.key_rise(fade, pre))
        k = int(np.argmax(np.abs(tail[:200])))                              # the cut at the wave's crest
        cut = tail.copy()
        cut[k:] = 0.0
        assert rd.key_rise(cut, pre) > rd.CLK_RISE, (amp, rd.key_rise(cut, pre))
        step = fade.copy()
        step[5:] += amp                                                     # a step injected after the key-up
        assert rd.key_rise(step, pre) > rd.CLK_RISE, (amp, rd.key_rise(step, pre))


def test_clk_off_judged_by_the_rise_where_the_note_sounds():
    """a sounding key-up over the ratio's ceiling passes with no rise and fails with one; a silent key-up keeps the
    ceiling (v0.9.9 -100: silent) unless its tail stays within 1 LSB"""
    base = cells(clk_off=-100.0)
    ok = rd.judge(cells(clk_off=4.0, clk_sh=[[4.0, 1.0, 9.0]]), base, [])[0]
    assert not [f for f in ok if f[2] == "clk_off"], ok
    bad = rd.judge(cells(clk_off=4.0, clk_sh=[[4.0, 8.0, 9.0]]), base, [])[0]
    assert any(f[2] == "clk_off" and f[6].startswith("CEILING") for f in bad), bad
    silent = rd.judge(cells(clk_off=4.0, clk_sh=[[4.0, None, 9.0]]), base, [])[0]
    assert any(f[2] == "clk_off" and f[6].startswith("CEILING") for f in silent), silent
    assert not rd.judge(cells(clk_off=-100.0, clk_sh=[[-100.0, None, 1.0]]), base, [])[0]


def test_harmonic_floor_bins_under_minus_100_dbfs_exempt():
    """H_ABS_FLOOR (2026-11-09): a bin at or under -100 dBFS (re H1 + H1) is the floor; above it, judged"""
    base = cells(h1=-50.0, H5=-50.0)                          # (a bin under -60 re H1 in both is no move anyway)
    assert not [f for f in rd.judge(cells(h1=-46.0, H5=-56.0), base, [])[0] if f[2] == "H5"]   # -102 dBFS
    assert [f for f in rd.judge(cells(h1=-30.0, H5=-56.0), base, [])[0] if f[2] == "H5"]       # -86 dBFS


def test_a_silent_key_up_window_is_a_state_not_a_db_value():
    """the 80..280 ms after the key-up all exact zeros reads "silent", never the -300 dBFS of zeros; an effects-on
    cell is the median of the shifts that sound (hq MARIMBA FX B k84v40: -213, -214, -43, -43 read -128 as a dB
    median); silent in both passes, a change silent <-> sounding fails unless an exact-cell `silent:` entry lists it,
    and a tail that sounds where the last release was silent still has the +0.5 dB ceiling"""
    import numpy as np
    x = np.zeros(rd.FS)
    x[:rd.FS // 2] = 100.0
    assert rd.keyup_of(x, rd.FS // 2) == rd.SILENT_TAIL
    x[rd.FS // 2 + int(0.1 * rd.FS)] = 1.0                       # one LSB in the window: it sounds
    assert isinstance(rd.keyup_of(x, rd.FS // 2), float)
    lab = "k84v40"
    runs = [{lab: {"keyup": v, "level": -40.0}} for v in (rd.SILENT_TAIL, rd.SILENT_TAIL, -42.9, -42.8)]
    assert abs(rd.merge_shifts(runs)[lab]["keyup"] + 42.85) < 1e-9
    assert rd.merge_shifts([{lab: {"keyup": rd.SILENT_TAIL}}] * 4)[lab]["keyup"] == rd.SILENT_TAIL
    s = rd.SILENT_TAIL
    base = cells(keyup=s)
    assert rd.judge(cells(keyup=s), base, [])[0] == []
    assert rd.judge(cells(keyup=-12.0), base, [])[0]                     # silent -> sounding: unlisted
    e = {"match": f"{CID}/k33v127", "metric": "keyup", "silent": False, "reason": "the tail rings on (commit x)"}
    assert rd.judge(cells(keyup=-12.0), base, [e])[0] == []
    assert rd.judge(cells(keyup=-12.0), base, [dict(e, match=f"{CID}/k33*")])[0]   # an exact cell only
    f = rd.judge(cells(keyup=1.0), base, [e])[0]
    assert any(x[6].startswith("CEILING") for x in f)                    # louder after the key-up: still a ceiling
    assert verdict(cells(keyup=s))                                       # sounding -> silent: unlisted
    assert verdict(cells(keyup=s), [dict(e, silent=True)]) == []
    assert rd.judge(cells(keyup=-12.0), base, [dict(e, min=-11.5, max=-10.5)])[0]   # outside its min / max
    assert rd.judge(cells(decay=s), cells(decay=s), [])[0] == []         # decay: the hold's end exact zeros, the same
    assert verdict(cells(decay=s))


def test_an_exact_cell_entry_governs_over_a_wildcard():
    """an entry naming the exact cell wins over any wildcard, whatever their order in the file; a move outside the
    exact entry's window fails even where a wildcard's window would hold it (hq F15 k67v20: the */F15/PRESET/* entry
    matched first)"""
    wild = {"match": "fm1/ST77/*", "metric": "level", "min": 2.0, "max": 4.0, "reason": "the TYPE louder"}
    ex = {"match": f"{CID}/k33v127", "metric": "level", "min": 2.8, "max": 3.2, "reason": "this cell, measured"}
    fails, moves, stale = rd.judge(cells(level=-17.0), cells(), [wild, ex])
    assert fails == [] and moves[0][6] == 1 and stale == [0]
    assert rd.judge(cells(level=-16.5), cells(), [wild, ex])[0]          # +3.5: inside the wildcard, outside the exact
    assert rd.judge(cells(level=-16.5), cells(), [wild])[0] == []
    assert rd.exact_entry({"match": "hq/F15/PRESET/[AB]/k67v20"}) and not rd.exact_entry({"match": "*/F15/PRESET/*"})


def test_a_metric_measured_in_some_shifts_only_is_kept():
    """merge_shifts took the metric set from shift 0 alone: a metric some shifts lack was dropped (hq MARIMBA FX B
    k84v40 on c11bc1b8 lost its key-up rise and read clk_off +2.5 against the ceiling); the union, and every shift's
    clk_sh row"""
    lab = "k84v40"
    runs = [{lab: {"clk_off": 2.5}}, {lab: {"clk_off": 2.5, "x": 1.0, "clk_sh": [[2.5, -0.5, 2.0]]}}]
    m = rd.merge_shifts(runs)[lab]
    assert m["x"] == 1.0 and m["clk_off"] == 2.5 and m["clk_sh"] == [[2.5, -0.5, 2.0]]


def test_clk_off_is_judged_per_shift():
    """every shift passes by its own rule or the cell fails: a sounding shift with no rise passes, a silent shift (no
    sample in its last 10 ms) with a real step after the key-up fails the cell, though the median would pass"""
    base = cells(clk_off=-100.0)
    rows = [[2.5, -0.5, 2.0], [2.5, -0.5, 2.0], [2.5, -0.5, 2.0], [2.5, -0.5, 2.0]]
    assert not rd.judge(cells(clk_off=2.5, clk_sh=rows), base, [])[0]
    step = [[2.5, -0.5, 2.0], [2.5, -0.5, 2.0], [2.5, -0.5, 2.0], [30.0, None, 400.0]]
    f = rd.judge(cells(clk_off=2.5, clk_sh=step), base, [])[0]
    assert any(x[2] == "clk_off" and "shift(s) [3]" in x[6] for x in f), f
    quiet = [[2.5, -0.5, 2.0], [-100.0, None, 1.0]]                    # silent and within 1 LSB: silence
    assert not rd.judge(cells(clk_off=2.5, clk_sh=quiet), base, [])[0]
    import numpy as np
    x = np.zeros(rd.FS)
    x[rd.FS // 2 - 100:rd.FS // 2:3] = 1.0                             # a +-1 LSB note: it sounds at the key-up
    assert rd.sounds_at(x, rd.FS // 2) and not rd.sounds_at(x, rd.FS // 2 + int(0.02 * rd.FS))


def test_clicks_reference_floor_finds_every_injected_step():
    """clicks()' reference is floored at one int16 step (CLK_REF_FLOOR, 2026-11-14): a note that decays into exact
    zeros in more than half its window read its own sine re 1e-9 as a +200 dB click (MARIMBA FX A k89v40 203.7 dB).
    The floor is not a weaker detector: on rendered cells (quiet bodies whose median frame is 0, ordinary notes, and
    the key-up tails, silent and sounding) a 1 ms step of 2..256 LSB that the old reference (median + 1e-9) found
    (> +6 dB over the clean cell, the clk ceiling's margin) the new one finds too; and the bigger steps are found"""
    import numpy as np
    from hostrun import BIN, build
    build()
    tmp = os.path.join(os.path.dirname(BIN), "py", "clkref-" + os.environ.get("PYTEST_XDIST_WORKER", "w"))

    def frames(y):
        d = np.abs(np.diff(y, 2))
        n = len(d) // 44
        return d[:n * 44].reshape(n, 44).max(1) if n else np.zeros(1)

    def old(x, ref):
        return max(rd.db((frames(x).max() / (np.median(frames(ref)) + 1e-9)) ** 2), -100.0)

    def step(y, at, a):
        z = y.copy()
        z[at:at + 44] += a
        return z

    tl = lambda t: np.where(np.abs(t) <= rd.LSB_FLOOR, 0.0, t)       # (clk_off's tail, as measure_once)
    cells = (("fm1/MARIMBA/FX/A", "k89v40"), ("fm1/MARIMBA/FXOFF/A", "k89v127"), ("fm1/MARIMBA/FXOFF/A", "k45v20"),
             ("fm1/ST77/FXOFF/A", "k45v64"), ("fm1/ST77/FXOFF/A", "k93v20"), ("fm1/DYNO/FXOFF/A", "k28v127"))
    seen_zero_median, found_new = 0, {}
    for cid, lab in cells:
        case = [c for c in rd.cases("full") if c[0] == cid][0]
        x = rd.render(BIN, os.path.join(tmp, cid.replace("/", "_")), rd.script("full", case[2], case[3])).mean(1)
        e = [e for e in rd.events("full", case[3])[0] if e[0] == lab][0]
        i, off = int(e[1] / 1000 * rd.FS), int((e[1] + rd.HOLD) / 1000 * rd.FS)
        body, pre, tail = x[i + int(0.03 * rd.FS):off], x[off - int(0.2 * rd.FS):off], x[off:off + int(0.3 * rd.FS)]
        seen_zero_median += np.median(frames(body)) == 0
        assert rd.clicks(body, body) < 60.0, (cid, lab, rd.clicks(body, body))   # no +200 dB of a smooth note
        windows = [(lambda a, p=p: (step(body, int(p * rd.FS), a), body), body, body) for p in (0.1, 0.4, 0.7)] + \
                  [(lambda a: (tl(step(tail, int(0.15 * rd.FS), a)), pre), tl(tail), pre)]
        for w, (inj, clean, ref) in enumerate(windows):
            for a in (2, 4, 8, 16, 64, 256):
                y, r = inj(a)
                fo = old(y, r) - old(clean, ref) > 6.0
                fn = rd.clicks(y, r) - rd.clicks(clean, ref) > 6.0
                assert fn or not fo, (cid, lab, "window", w, "step", a, "found by the old reference only")
                found_new[(cid, lab, w, a)] = fn
    assert seen_zero_median >= 2                         # the cells the floor exists for are in the set
    assert all(found_new[(c, l, 3, a)] for c, l in cells for a in (16, 64, 256))     # every key-up tail
    assert all(found_new[(c, l, w, 256)] for c, l in cells for w in range(3))         # 256 LSB in every body
