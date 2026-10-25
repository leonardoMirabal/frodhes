# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_release_level.py (release_level, "no louder after the key-up") proven on the host, without the FM-1:
the analysis on synthetic audio (a decaying note, then a thump louder than the note after the key-up: FAIL; a clean
release and a NOISE knock 20 dB under the note: PASS; a tail swelling over the note: FAIL), the whole-take path on
a device-like clock (the recording late and slow, judged through clock_fit's lag and rate), the plan and the dry run."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_release_level as R  # noqa: E402

FS = 48000


@pytest.mark.parametrize("f0", (65.4, 261.6, 1046.5))
def test_thump_after_the_key_up_fails(f0):
    x, tu = R.synth("thump", FS, f0=f0)
    r = R.judge_release(x, FS, tu, floor=-84.0)
    assert not r["ok"]
    assert r["d_rms"] > 3 and r["d_peak"] > 3 and r["onset_ms"] is not None, r


@pytest.mark.parametrize("f0", (65.4, 261.6, 1046.5))
@pytest.mark.parametrize("kind", ("clean", "knock"))
def test_clean_release_passes(f0, kind):
    x, tu = R.synth(kind, FS, f0=f0)
    r = R.judge_release(x, FS, tu, floor=-84.0)
    assert r["ok"], r
    assert r["d_rms"] <= R.TOL_DB and r["d_peak"] <= R.TOL_DB


def test_swell_fails_without_an_onset():
    x, tu = R.synth("swell", FS)
    r = R.judge_release(x, FS, tu, floor=-84.0)
    assert not r["ok"] and r["d_rms"] > R.TOL_DB and r["onset_ms"] is None, r


def test_self_test():
    assert R.self_test(log=lambda *_: None)


def _take(thump_at=None, lag=0.004, ppm=600e-6, fs=FS):
    """a whole take on the device's clock: three notes held 2 s (the plan's timing), the recording lag s late and
    ppm slow; thump_at: the release (index) that gets the 0.9.5 thump"""
    rel, t, notes = [], R.LEAD, []
    for i, f0 in enumerate((65.4, 261.6, 1046.5)):
        rel.append((f"n{i}", t, t + R.HOLD, "key"))
        notes.append((t, f0))
        t += R.HOLD + R.GAP
    dur = t + R.TAIL
    n = int(dur * (1 + ppm) * fs) + fs
    x = np.random.default_rng(1).normal(0, 2.0, n)
    for i, (ton, f0) in enumerate(notes):
        y, tu = R.synth("thump" if i == thump_at else "clean", fs, f0=f0, t_up=R.HOLD, dur=R.HOLD + R.GAP, seed=i)
        a = int(round((lag + ton * (1 + ppm)) * fs))
        x[a:a + len(y)] += y
    ev = [(ton, "on", 60, 15) for ton, _ in notes] + [(ton + R.HOLD, "off", 60, 0) for ton, _ in notes]
    return x, sorted(ev), rel


@pytest.mark.parametrize("thump_at", (None, 0, 1, 2))
def test_whole_take_on_the_device_clock(thump_at):
    import hil_crackle as HC
    x, ev, rel = _take(thump_at)
    fit = HC.clock_fit(x, ev, FS, rate=600e-6)
    _, rows = R.judge_take(x, FS, ev, rel, (fit["lag"], fit["rate"]))
    bad = [i for i, r in enumerate(rows) if not r["ok"]]
    assert bad == ([] if thump_at is None else [thump_at]), rows


def test_plan_and_dry_run(capsys):
    P = R.plan()
    assert len(P) == len(R.TYPES) * len(R.NOISES)
    assert sum(len(r) for *_, r in P) == 4 * (2 * 9 + 2)          # 72 key-ups + the pedal case (key-up, pedal-up) x 4
    assert sum(len(r) for *_, r in R.plan(quick=True)) == 4 * (2 * 3 + 2)
    full = sum(d + 1.5 for _, _, _, d, _ in P)
    assert 150 < full < 260, full                                 # ~3 min on the FM-1
    for name, ccs, ev, dur, rel in P:
        assert (114, 127) in ccs and any(c == 104 for c, _ in ccs) and any(c == 80 for c, _ in ccs)
        assert max(e[0] for e in ev) < dur
        for _, ton, trel, kind in rel:                              # every release with its 600 ms clear of the next
            nxt = [e[0] for e in ev if e[1] == "on" and e[0] > ton]
            assert not nxt or min(nxt) >= trel + R.POST
    assert R.main(["--dry-run"]) == 0
    assert "80 releases" in capsys.readouterr().out


def _soft(thump=False, hiss=0.0, fs=FS, seed=0):
    """one soft C6 on a take's clock (the note on at R.LEAD, the key-up 2 s later), the recording's own hiss (LSB RMS)
    under it; thump: the 0.9.5 defect after the key-up"""
    y, _ = R.synth("thump" if thump else "clean", fs, f0=1046.5, t_up=R.HOLD, dur=R.HOLD + R.GAP, vel=0.002, seed=seed)
    x = np.random.default_rng(seed + 5).normal(0, 2.0 + hiss, int((R.LEAD + R.HOLD + R.GAP + 0.2) * fs))
    a = int(R.LEAD * fs)
    x[a:a + len(y)] += y
    ev = [(R.LEAD, "on", 84, 15), (R.LEAD + R.HOLD, "off", 84, 0)]
    return x, ev, [("C6 v15", R.LEAD, R.LEAD + R.HOLD, "key")]


def _judge(x, ev, rel):
    return R.judge_take(x, FS, ev, rel, (0.0, 0.0))[1]


@pytest.mark.parametrize("seed", range(6))
def test_jack_under_its_floor_is_judged_on_usb(seed):
    """the release run's 6 jack-only FAILs (2026-10-25, C6 v15, the note 1-5 dB over the jack's hiss): the jack's
    level rows under JACK_MIN_DB over its floor are judged on USB, the report says so with USB's numbers"""
    xu, ev, rel = _soft(seed=seed)
    xj, _, _ = _soft(hiss=40.0, seed=seed)                    # the jack: hiss within a few dB of the note
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["over_floor"] < R.JACK_MIN_DB, j
    R.apply_jack_floor(u, j)
    assert u[0]["ok"] and j[0]["ok"] and not j[0]["level_judged"], (u, j)
    assert "under the jack floor" in j[0]["why"] and "judged on USB" in j[0]["why"] and "USB dRMS" in j[0]["why"]


def test_a_thump_on_a_soft_note_is_caught_on_usb_under_the_jack_floor():
    xu, ev, rel = _soft(thump=True)
    xj, _, _ = _soft(thump=True, hiss=40.0)
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    R.apply_jack_floor(u, j)
    assert not u[0]["ok"] and u[0]["d_rms"] > 3, u                # a USB FAIL is a FAIL, whatever the jack says


def test_jack_over_its_floor_still_counts():
    xu, ev, rel = _soft()
    xj, _, _ = _soft(thump=True)                                  # (a jack-only thump, the note well over its floor)
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["over_floor"] >= R.JACK_MIN_DB
    R.apply_jack_floor(u, j)
    assert u[0]["ok"] and not j[0]["ok"] and j[0]["level_judged"], (u, j)


def _note(vel, boost_db=0.0, hiss=2.0, gain=1.0, seed=0, f0=261.6, fs=FS):
    """one held C4 on a take's clock (on at R.LEAD, the key-up R.HOLD later), the recording's hiss (LSB RMS) under it;
    boost_db: the release made louder, a tonal swell of the note itself (the same sine, correlated with it) 0..60 ms
    after the key-up, then the damper; gain: the source's level (the jack ~13 dB under USB)"""
    t = np.arange(int((R.LEAD + R.HOLD + R.GAP + 0.2) * fs)) / fs
    tn, tu = t - R.LEAD, R.HOLD
    env = np.where(tn < 0, 0.0, vel * np.exp(-np.maximum(tn, 0) / 6.0) * np.minimum(1.0, np.maximum(tn, 0) / 0.002))
    g = np.where((tn >= tu) & (tn < tu + 0.060), 10 ** (boost_db / 20), 1.0)
    env = np.where(tn < tu + 0.060, env * g, env * np.exp(-(tn - tu - 0.060) / 0.040))
    x = gain * env * np.sin(2 * np.pi * f0 * t) * R.FS_LSB
    x += np.random.default_rng(seed + 11).normal(0, hiss, len(t))
    ev = [(R.LEAD, "on", 60, 127), (R.LEAD + R.HOLD, "off", 60, 0)]
    return x, ev, [("C4", R.LEAD, R.LEAD + R.HOLD, "key")]


JACK = 10 ** (-13 / 20)                                           # the jack's level under USB


def test_hiss_peak_is_measured_over_the_after_window():
    x, ev, rel = _note(0.0, hiss=3.0)
    hp = R.hiss_peak(x, FS, [R.LEAD])                              # (only the lead's idle: shorter than POST, its peak)
    assert -72 < hp < -64, hp
    r = _judge(x, ev, rel)[0]
    assert abs(r["hiss_peak_db"] - hp) < 1.0, (r, hp)         # (judge_take: on the found onset)


@pytest.mark.parametrize("seed", range(6))
def test_a_jack_hiss_peak_is_judged_on_usb(seed):
    """today's FAIL (STAGE 77 NOISE 127 C4 v15 pedal-up: jack dPeak +1.2, USB -0.5; and NOISE 48 C6 v60 jack +0.7):
    the note >= 12 dB over the jack floor but its peak < 25 dB over the jack's hiss peak; one hiss sample in the
    'after' window: the jack's dPeak is judged on USB, its dRMS on the jack, and the row says why"""
    xu, ev, rel = _note(0.01, seed=seed)
    xj, _, _ = _note(0.01, gain=JACK, hiss=3.0, seed=seed)
    tu = int((R.LEAD + R.HOLD + 0.200) * FS)
    xj[tu] += 3 * 3.0 + 0.01 * JACK * R.FS_LSB                    # one hiss sample over the note's peak
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["over_floor"] >= R.JACK_MIN_DB and j[0]["peak_over_hiss"] < R.JACK_PEAK_MIN_DB, j
    assert j[0]["d_peak"] > R.TOL_DB and j[0]["d_rms"] <= R.TOL_DB, j    # (the old rule's FAIL)
    R.apply_jack_floor(u, j)
    assert u[0]["ok"] and j[0]["ok"] and not j[0]["peak_judged"], (u, j)
    assert "the peak judged on USB" in j[0]["why"] and "USB dPeak" in j[0]["why"], j


def test_a_jack_rms_rise_still_counts_under_the_peak_margin():
    xu, ev, rel = _note(0.01)
    xj, _, _ = _note(0.01, boost_db=2.0, gain=JACK, hiss=3.0)
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["peak_over_hiss"] < R.JACK_PEAK_MIN_DB and j[0]["d_rms"] > R.TOL_DB, j
    R.apply_jack_floor(u, j)
    assert u[0]["ok"] and not j[0]["ok"] and "RMS" in j[0]["why"], j


@pytest.mark.parametrize("vel", (0.01, 0.3))
def test_a_louder_release_on_both_sources_fails(vel):
    xu, ev, rel = _note(vel, boost_db=1.0)
    xj, _, _ = _note(vel, boost_db=1.0, gain=JACK, hiss=3.0)
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["d_peak"] > R.TOL_DB, j                            # (the jack sees it too)
    R.apply_jack_floor(u, j)
    assert not u[0]["ok"] and u[0]["d_peak"] > R.TOL_DB and u[0]["d_rms"] > R.TOL_DB, u
    assert not (u[0]["ok"] and j[0]["ok"])                         # the release FAILs (USB's row, whatever the jack)
    if j[0]["peak_judged"] is False:
        assert not j[0]["ok"] and "USB dPeak" in j[0]["why"] and "FAIL" in j[0]["why"], j


def test_a_jack_only_tonal_peak_on_a_loud_note_fails():
    xu, ev, rel = _note(0.3)
    xj, _, _ = _note(0.3, boost_db=1.0, gain=JACK, hiss=3.0)
    u, j = _judge(xu, ev, rel), _judge(xj, ev, rel)
    assert j[0]["peak_over_hiss"] >= R.JACK_PEAK_MIN_DB, j
    R.apply_jack_floor(u, j)
    assert u[0]["ok"] and not j[0]["ok"] and j[0]["peak_judged"] and "peak" in j[0]["why"], (u, j)


def test_the_after_window_starts_at_the_key_up():
    """a sample 6 ms before the key-up (inside the old 12 ms guard) is not the release's: it moves neither window"""
    x, ev, rel = _note(0.01, hiss=0.0)
    tu = R.LEAD + R.HOLD
    x[int((tu - 0.006) * FS)] = 0.02 * R.FS_LSB
    r = R.judge_release(x, FS, tu, floor=-96.0)
    assert r["ok"] and r["d_peak"] < 0, r
    x[int((tu + 0.003) * FS)] = 0.02 * R.FS_LSB
    assert not R.judge_release(x, FS, tu, floor=-96.0)["ok"]
