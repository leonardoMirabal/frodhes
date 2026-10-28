# SPDX-License-Identifier: GPL-3.0-only
"""The Move's 16 voices (private plan 05 M3-02, M3-03), on the host build at the Move's settings (tools/gate_h.py
DEFS: FR_VOICES=16u, FR_HQ), every engine: Rhodes Classic, Rhodes HQ (HQ_DEFAULT=1), VIBES, MARIMBA.

- The mix's headroom: 16-note chords at v127, the pedal down and up, through the whole chain (effects and the
  limiter as set by default): all 16 voices sound and no sample reaches full scale.
- Never louder after the key-up (test_release_never_louder.py's +0.5 dB, set against the same script held: a chord's
  notes beat), the engine alone (RAW): a 16-note chord let go; the same under the pedal (the key-up, then the pedal-up);
  a pedal glissando of 40 notes at v127 (24 steals; the pedal-up); the arpeggiator under the pedal (the pedal-up).
- The bar engine's budget (M3-03): on the Move no FM-1-time cost model trims a mode. VIBES and MARIMBA chords and
  scripts/burst render the same with and without FRODHES_NOBUDGET, and the profiler (CC 116) reports no cap under
  127 modes. The detector: the FM-1's cost model at 16 voices (no FR_HQ) does trim scripts/burst."""
import os
import pathlib
import re
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostrun  # noqa: E402
from test_release_never_louder import TOL_DB, W, rms10  # noqa: E402

ROOT = hostrun.ROOT
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_h  # noqa: E402

D = os.path.join(ROOT, "build", "move_voices", hostrun.WORKER)
CHORD = (36, 40, 43, 47, 50, 53, 57, 60, 64, 67, 71, 74, 77, 81, 84, 88)   # 16 keys, every engine's range
BURST = os.path.join(ROOT, "tests", "scripts", "burst.txt")
ENGINES = {                                       # name: (HQ on every note, the TYPE's CC)
    "rhodes": (False, "80 0"),
    "rhodes-hq": (True, "80 0"),
    "vibes": (False, "79 0"),
    "marimba": (False, "79 127"),
}


def binary(hq):
    b = os.path.join(ROOT, "build", "move_voices", "bin", "hq" if hq else "classic")
    return hostrun.once(b, lambda p: gate_h.build(hq, pathlib.Path(p)))


def fm1_16():
    """the FM-1's build at 16 voices: its cost model on (the detector)"""
    b = os.path.join(ROOT, "build", "move_voices", "bin", "fm1-16")
    return hostrun.once(b, lambda p: subprocess.run(
        ["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-DFR_VOICES=16u",
         "-Ifirmware/src", "-Ifirmware/gen", "-o", p, "firmware/src/frodhes.c", "-lm"], cwd=ROOT, check=True))


def head(engine, raw):
    hq, ty = ENGINES[engine]
    ev = [f"40 midi 0xB0 {ty}"]
    if raw:
        ev.append("42 midi 0xB0 119 127")           # the engine alone (RAW)
    if ty.startswith("79"):
        ev.append("44 midi 0xB0 47 0")            # VIBES's motor off (its AM is no key-up)
    return hq, ev


def run(engine, tag, ev, end, raw, env=None, b=None):
    hq, h = head(engine, raw)
    os.makedirs(D, exist_ok=True)
    r = hostrun.run(f"mv-{engine}-{tag}", h + ev, end, env=env, binary=b or binary(hq))
    return r, (r.L.astype(float) + r.R.astype(float)) / 2.0


def chord(t, v=127):
    return [f"{t} midi 0x90 {k} {v}" for k in CHORD]


def up(t):
    return [f"{t} midi 0x80 {k} 0" for k in CHORD]


def voices_max(r):
    m = re.search(r"voices max rhodes (\d+) of (\d+) bars (\d+) of (\d+)", r.out)
    return tuple(int(x) for x in m.groups())


@pytest.mark.parametrize("engine", list(ENGINES))
def test_chords_into_the_limiter(engine):
    """three 16-note v127 chords (1 s held), then one under the pedal: 16 voices, no full-scale sample"""
    ev = []
    for i in range(3):
        ev += chord(300 + 1600 * i) + up(1300 + 1600 * i)
    ev += ["5100 midi 0xB0 64 127"] + chord(5200) + up(5600) + ["7200 midi 0xB0 64 0"]
    r, _ = run(engine, "limiter", ev, 9000, raw=False)
    vm = voices_max(r)
    assert (vm[0] if engine.startswith("rhodes") else vm[2]) == 16, vm
    pk = max(np.abs(r.L).max(), np.abs(r.R).max())
    assert pk < 32767, f"{engine}: a full-scale sample ({pk:.0f})"


def _db(x):
    return 20 * np.log10(np.asarray(x, float) / 32768.0 + 1e-12)


def _diff(x, ref, t_ms, span_ms):
    """(rms rise, peak rise) in dB of the let-go render over the held one (ref: the same script without this key-up or
    pedal-up), the most of any 10 ms window 0..span_ms after t_ms (from 10 ms early: the event's block). A chord's 16
    notes beat, so the level after the key-up is set against the held chord's window by window, not against the
    50 ms before (the one-note rule's reference, test_release_never_louder.py; its onset count too reads the beats as
    onsets): a damper's thump or click shows as a window over the held one's, in RMS or in peak"""
    i = int(t_ms * hostrun.FS / 1000) - W
    n = span_ms // 10 + 1
    a, b = x[i:i + n * W].reshape(n, W), ref[i:i + n * W].reshape(n, W)
    ok = _db(rms10(ref[i:i + n * W])) > -90.0
    rise_r = _db(rms10(x[i:i + n * W])) - _db(rms10(ref[i:i + n * W]))
    rise_p = _db(np.abs(a).max(axis=1)) - _db(np.abs(b).max(axis=1))
    return float(np.max(np.where(ok, rise_r, -99.0))), float(np.max(np.where(ok, rise_p, -99.0)))


def _scenarios():
    """name: (the events, [(check, t ms, span ms, its lines)]): the checks' lines in time order"""
    ks = (48, 55, 60, 64, 67, 72)
    gl = ["200 midi 0xB0 64 127"]
    for i, k in enumerate(range(36, 76)):               # 40 notes, 30 ms apart, each held 40 ms: 24 steals
        gl += hostrun.note(300 + 30 * i, k, 127, 40)
    return {
        "chord127": (chord(300), [("key-up", 1300, 600, up(1300))]),
        "chord60": (chord(300, 60), [("key-up", 1300, 600, up(1300))]),
        "pedal": (["200 midi 0xB0 64 127"] + chord(300),
                  [("key-up", 1300, 290, up(1300)), ("pedal-up", 1600, 600, ["1600 midi 0xB0 64 0"])]),
        "glissando": (gl, [("pedal-up", 2300, 600, ["2300 midi 0xB0 64 0"])]),
        "arp": (["100 midi 0xB0 52 1", "200 midi 0xB0 64 127"] + [f"300 midi 0x90 {k} 110" for k in ks]
                + [f"1800 midi 0x80 {k} 0" for k in ks] + ["1800 midi 0xB0 52 0"],
                [("pedal-up", 2100, 600, ["2100 midi 0xB0 64 0"])]),
    }


RELEASE = [(e, n) for e in ENGINES for n in _scenarios()]


@pytest.mark.parametrize("engine,scen", RELEASE, ids=[f"{e}-{n}" for e, n in RELEASE])
def test_never_louder_after_key_up_16(engine, scen):
    """16-note chords let go (v127, v60), under the pedal (the key-up, the pedal-up), a pedal glissando of 40 notes,
    the arpeggiator under the pedal: each key-up or pedal-up never louder than the same script held, by +0.5 dB on
    any 10 ms window's RMS or peak"""
    base, checks = _scenarios()[scen]
    rel = base + [ln for c in checks for ln in c[3]]
    end = checks[-1][1] + 900
    _, x = run(engine, f"rel-{scen}", rel, end, raw=True)
    bad = []
    for j, (what, t, span, _) in enumerate(checks):
        _, ref = run(engine, f"rel-{scen}-ref{j}", base + [ln for c in checks[:j] for ln in c[3]], end, raw=True)
        rr, rp = _diff(x, ref, t, span)
        if rr > TOL_DB or rp > TOL_DB:
            bad.append(f"{what} at {t} ms: 10 ms RMS {rr:+.1f} dB, peak {rp:+.1f} dB over held")
    assert not bad, f"{engine} {scen}:\n" + "\n".join(bad)


def _cc116(r):
    """the last profiler reply: (CC 112 the lowest mode cap, CC 113 the most live upper modes)"""
    got = {}
    for _, st, a, b in r.midi_out:
        if st == 0xB0 and a in (112, 113):
            got[a] = b
    return got.get(112), got.get(113)


def _budget_ev():
    ev = ["300 midi 0xB0 64 127"]
    for i in range(4):                               # four 16-note chords at v127 into one pedal: 16 voices ringing
        ev += chord(400 + 500 * i) + up(600 + 500 * i)
    return ev + ["3000 midi 0xB0 116 0", "4000 midi 0xB0 64 0"]


@pytest.mark.parametrize("engine", ["vibes", "marimba"])
def test_bar_budget_never_trims_on_the_move(engine):
    r, _ = run(engine, "budget", _budget_ev(), 5000, raw=False)
    n, _ = run(engine, "budget-nb", _budget_ev(), 5000, raw=False, env={"FRODHES_NOBUDGET": "1"})
    assert voices_max(r)[2] == 16
    assert np.array_equal(r.L, n.L) and np.array_equal(r.R, n.R), f"{engine}: the bar budget trimmed a mode"
    cap, used = _cc116(r)
    assert cap == 127 and used is not None and used > 0, (cap, used)


def _burst(b, tag, env=None):
    out = os.path.join(D, tag)
    os.makedirs(D, exist_ok=True)
    e = dict(os.environ, **(env or {}))
    subprocess.run([b, out, BURST], cwd=ROOT, check=True, capture_output=True, env=e)
    return pathlib.Path(out + ".wav").read_bytes()


def test_burst_untrimmed_on_the_move_trimmed_on_the_fm1_model():
    """scripts/burst (40 notes at once, 16 voices) is the one Gate R render the FM-1's cost model trims at 16 voices
    (tools/voices16_study.py): the Move's build renders it as with no budget; the FM-1's model at 16 voices does not"""
    mv = binary(False)
    assert _burst(mv, "burst-mv") == _burst(mv, "burst-mv-nb", {"FRODHES_NOBUDGET": "1"})
    fm = fm1_16()
    assert _burst(fm, "burst-fm") != _burst(fm, "burst-fm-nb", {"FRODHES_NOBUDGET": "1"})


def test_the_release_check_finds_a_thump():
    """the detector: a 2 ms thump over the held chord's level 200 ms after the key-up, or the tail 1 dB louder than
    the let-go render, is found. (A thump under the held chord's level is the one-note grid's to find,
    test_release_never_louder.py: the release is the same code per voice at 10 and 16 voices)"""
    base, checks = _scenarios()["chord127"]
    _, x = run("rhodes", "det", base + checks[0][3], 2200, raw=True)
    _, ref = run("rhodes", "det-ref", base, 2200, raw=True)
    assert max(_diff(x, ref, 1300, 600)) <= TOL_DB
    i = int(1500 * hostrun.FS / 1000)
    th = x.copy()
    th[i:i + 88] += 1.5 * np.abs(ref[i:i + W]).max() * np.hanning(88)
    assert _diff(th, ref, 1300, 600)[1] > TOL_DB
    lo = x.copy()
    lo[i:] *= 10 ** (1 / 20)
    assert _diff(lo, x, 1300, 600)[0] > TOL_DB
