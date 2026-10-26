# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_crackle.py (stress_max, the cracks test) proven on the host, without the FM-1:

  - the self-test on every engine: host renders of the stimulus (the worst case and the user's recipe, the quick
    lengths), 0 events on the clean renders (USB and the simulated jack), and every injected glitch found by its own
    detector, 10 of each kind (a -40 dBFS one-sample click, a repeated frame, an underrun's hold, a 32-frame chunk, a
    stale half block, a repeated block, a 1-5 ms dropout, a clip into full scale, a level step, a lost packet, the jack's
    click and dropout); the stimulus took every voice of the engine (the Rhodes 9, the bars 9);
  - each detector on a synthetic chord, clean and with its glitch;
  - the dry-run plan, the CC 116 reply parsing, the periodicity check, hil_scan's stress_max entry."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_crackle as C  # noqa: E402


@pytest.mark.parametrize("eng", C.ENGINES)
def test_self_test(eng):
    """both timings: the host's own, and the FM-1's (the events DEV_CLOCK late and slow, the jack another JACK_DRIFT
    off and 12 dB under USB), judged without knowing it (clock_fit on each recording)"""
    out, ok = C.self_test((eng,), log=lambda *_: None)
    r = out[eng]
    assert not r["clean"], f"{eng}: false alarms on the clean renders: {r['clean'][:5]}"
    assert r["voices"] and r["voices"][0] == r["voices"][1] == C.voices(eng), f"{eng}: voices {r['voices']}"
    for kk in ("kinds", "kinds_device"):
        missed = {k: v for k, v in r[kk].items() if v != (10, 10)}
        assert not missed, f"{eng} {kk}: injected glitches not found (found, placed): {missed}"
        assert set(r[kk]) == set(C.KINDS)
    assert len(r["clocks"]) == 6 and not any("FAIL" in c for c in r["clocks"]), r["clocks"]
    assert ok


def test_the_device_timing_has_teeth():
    """the self-test's device timing would have caught the FM-1's false steps (2026-10-24): the same render judged the
    old way (one lag per recording from the first sample over 300 LSB, rate 0) gives events, the clock_fit way none.
    The recipe with DELAY DEPTH 0 (CC 89): the old way's false step is the second phrase's first note out of the
    silence the first one leaves (the misplaced clock guards nothing there); with 0.9.5's delay laws (FDBK no longer
    scaled by MIX) the recipe's echo still sounds there (-48 dB on the jack, 0.9 s after the dampers, against -62 with
    0.9.4's), the note rises 9 dB, under the 12 dB a step needs, and the old way had nothing to trip on"""
    name, ccs, ev, dur = [t for t in C.takes("rhodes", 20.0, 24.0, 0) if t[0] == "rhodes-recipe-fx1"][0]
    ccs = list(ccs) + [(89, 0)]                                  # (DELAY DEPTH 0: the silence between the phrases)
    u, _ = C.host_take(name, ccs, ev, dur, tag="stress_max-teeth", clock=C.DEV_CLOCK)
    j = C.sim_jack(u)
    old = C.analyse(u, j, 44100, ev, widen=C.DEV_JITTER, ccs=ccs, clock_usb=(C.onset_lag(u, ev, 44100, 300), 0.0),
                    clock_jack=(C.onset_lag(j, ev, 48000, 300), 0.0))
    assert C.onset_lag(j, ev, 48000, 300) > 0.1                 # (the jack's first note under 300 LSB: a later one)
    assert any(s == "jack" and k == "step" for s, _, k, _ in C.gated(old)), C.gated(old)
    cj = C._jack_clock(C.DEV_CLOCK)
    fu = C.clock_fit(u, ev, 44100, rate=C.DEV_CLOCK[1] + C.RATE_ERR)
    fj = C.clock_fit(j, ev, 48000, rate=cj[1] + C.RATE_ERR)
    new = C.analyse(u, j, 44100, ev, widen=C.DEV_JITTER, ccs=ccs, clock_usb=(fu["lag"], fu["rate"]),
                    clock_jack=(fj["lag"], fj["rate"]))
    assert not C.gated(new), C.gated(new)[:5]
    assert abs(fj["lag"] - cj[0]) < C.DEV_JITTER and abs(fu["lag"] - C.DEV_CLOCK[0]) < C.DEV_JITTER


def test_clock_fit_without_a_rate():
    """a click train out of silence on a clock 700 ppm slow, 9 ms late: the onsets fit both"""
    fs, rate, lag = 44100, 700e-6, 0.009
    ev = [(0.3 + 0.37 * k + 0.05 * (k % 3), "on", 60, 100) for k in range(120)]
    x = np.random.default_rng(0).normal(0, 2, int(48 * fs))
    for t, *_ in ev:
        i = int((lag + t * (1 + rate)) * fs)
        n = np.arange(int(0.2 * fs))
        x[i:i + len(n)] += 8000 * np.exp(-n / (0.03 * fs)) * np.sin(2 * np.pi * 440 * n / fs)
    f = C.clock_fit(x, ev, fs)
    assert f["how"] == "fit" and abs(f["rate"] - rate) < 30e-6 and abs(f["lag"] - lag) < 0.002, f
    g = C.clock_fit(x, ev, fs, rate=rate)
    assert g["how"].startswith("measured") and abs(g["lag"] - lag) < 0.002 and g["n"] >= 100, g


def test_onset_lag_on_a_quiet_jack():
    """the first sound found 12 dB under USB too (the old fixed 300 LSB took a later, louder note)"""
    fs = 48000
    x = np.random.default_rng(1).normal(0, 1, 3 * fs)
    ev = [(0.3, "on", 36, 100), (0.52, "on", 60, 100)]
    for t, a in ((0.3, 150), (0.52, 1500)):
        n = np.arange(int(0.2 * fs))
        x[int((t + 0.01) * fs):int((t + 0.01) * fs) + len(n)] += a * np.exp(-n / (0.05 * fs)) * np.sin(
            2 * np.pi * 100 * n / fs)
    assert abs(C.onset_lag(x, ev, fs) - 0.01) < 0.002
    assert C.onset_lag(x, ev, fs, 300) > 0.2


def test_ring_rate():
    """hil_scan.Ring.rate: a stream 760 ppm fast against the stream clock, its ADC times jittered by 2 ms"""
    import hil_scan
    sr, ppm = 48000, 760e-6
    r = hil_scan.Ring(sr, 1, secs=70.0)
    rng = np.random.default_rng(2)
    blk = np.zeros((480, 1), np.float32)
    for k in range(6000):                                    # (60 s of 10 ms blocks)
        r.push(blk, 100.0 + k * 480 / (sr * (1 + ppm)) + rng.uniform(-0.002, 0.002))
    got = r.rate(100.0, 60.0) / sr - 1
    assert abs(got - ppm) < 5e-6, got


def test_jack_zero_wants_a_cut():
    """a quiet low wave passing 0 slowly (two partials cancelling) stays under 4 LSB for > 1 ms: no dropout; the same
    wave cut to 0 for 1 ms is one"""
    fs = 48000
    t = np.arange(fs) / fs
    tc = t - 0.5                                             # (the slopes cancel at 0.5 s: flat through 0 there)
    x = 150 * np.sin(2 * np.pi * 87 * tc) - 75 * np.sin(2 * np.pi * 174 * tc)
    x += 200 * np.sin(2 * np.pi * 330 * tc) * (np.abs(tc) > 0.004)                    # (sound around)
    flat = np.flatnonzero(np.abs(x[int(0.49 * fs):int(0.51 * fs)]) < 4)
    assert len(flat) > 48                                    # (the wave itself: > 1 ms under 4 LSB)
    assert C.det_zero(x, fs, exact=False) == []
    y = 400 * np.sin(2 * np.pi * 220 * t)
    y[20000:20048] = 0.0
    assert [p for p, _ in C.det_zero(y, fs, exact=False)] == [20000]


def test_step_under_a_motor():
    """a chord out of silence under a 3.5 Hz AM that dips 15 dB: no step with the take's periods (am_periods), a step
    without them; a real -20 dB step under the same AM is found either way"""
    fs = 44100
    ccs = C.engine_ccs("vibes", C.HEAVY)
    per = C.am_periods(ccs)
    assert per and abs(per[0] - 1 / C.lfo_hz(78)) < 1e-9 and abs(per[0] - 0.284) < 0.002
    t = np.arange(int(3 * fs)) / fs
    am = np.where(((t - 0.4) % per[0]) < 0.11, 1.0, 0.18)    # (open 110 ms, then -15 dB)
    x = np.where(t >= 0.4, 3000 * np.sin(2 * np.pi * 220 * t) * am, 0.0)
    x += np.random.default_rng(3).normal(0, 2, len(t))
    ev = [(0.4, "on", 48, 100)]
    on, off = C.guards(ev, ccs), C.guards(ev, ccs, True)
    assert C.det_step(x, fs, on, off) != []                  # (the trough after the first opening)
    assert C.det_step(x, fs, on, off, periods=per) == []
    y = np.where(t >= 0.4, 3000 * np.sin(2 * np.pi * 220 * t) * (0.8 + 0.2 * np.cos(2 * np.pi * t / per[0])), 0.0)
    y[int(1.8 * fs):int(2.1 * fs)] *= 0.1                    # (under a milder AM, +-2 dB: the level holds)
    assert any(abs(p / fs - 1.8) < 0.045 for p, _ in C.det_step(y, fs, on, off, periods=per))


def test_step_in_a_modulations_first_period():
    """marimba-worst2 on the FM-1 (0.9.5 57e0025, 48 kHz, 2026-10-24): +12 dB at 0.480 s, 80 ms after the first chord,
    the filter LFO's crest after its first trough; one period back is before the first note (no level to compare),
    and the jack had the same wave (ncc 0.996, +9.5 dB). Modelled: a chord out of silence at 0.4 s, 14 dB down from
    0.43 to 0.47 s, then back up. USB alone: a step (as before the fix); with the jack (sim_jack, the same wave): the
    music's own, information; a USB-only step at the same moment (the jack clean): an event, both ways"""
    fs = 44100
    ccs = C.engine_ccs("marimba", C.HEAVY)
    per = C.am_periods(ccs)
    assert per and max(per) > 0.3
    t = np.arange(int(2 * fs)) / fs
    am = np.where((t >= 0.43) & (t < 0.47), 0.2, 1.0)
    x = np.where(t >= 0.4, 2000 * np.sin(2 * np.pi * 220 * t) * am, 0.0)
    x += np.random.default_rng(5).normal(0, 2, len(t))
    q = np.round(np.stack([x, x], 1))
    ev = [(0.4, "on", 45, 100)]
    j = C.sim_jack(q)
    alone = C.analyse(q, None, fs, ev, ccs=ccs, only=("step",))
    assert any(k == "step" and abs(p - 0.47) < 0.03 for p, k, _ in alone["usb"]), alone["usb"]
    both = C.analyse(q, j, fs, ev, ccs=ccs, only=("step",), clock_jack=(C.JACK_DELAY, -C.JACK_DRIFT))
    assert not [e for e in both["usb"] if e[1] == "step"], both["usb"]
    assert len(both["info"]["step_jack_too"]) == 1, both["info"]
    y = q.copy()
    y[:int(0.47 * fs)] *= 0.2                                # (a USB-only step up at the same moment: the jack
    y = np.round(y)                                          # has the wave without the dip, at its level all along)
    j2 = C.sim_jack(np.round(np.stack([x / am, x / am], 1)))
    r = C.analyse(y, j2, fs, ev, ccs=ccs, only=("step",), clock_jack=(C.JACK_DELAY, -C.JACK_DRIFT))
    assert any(k == "step" and abs(p - 0.47) < 0.03 for p, k, _ in r["usb"]), (r["usb"], r["info"])


def test_stale_ignores_a_steady_tone():
    """a lone low note whose period is a whole number of frames matches its period before exactly for a few dozen
    frames: no stale; a 32-frame chunk played twice in it is one"""
    fs, P = 44100, 450
    n = np.arange(fs)
    x = np.round(2400 * np.sin(2 * np.pi * n / P) * np.exp(-n / (40 * fs)))
    q = np.stack([x, x], 1).astype(np.int64)
    assert C.det_stale(q) == []
    q2 = q.copy()
    q2[30000:30032] = q2[30000 - 32:30000]
    assert [p for p, _ in C.det_stale(q2)] == [30000]


def test_stale_ignores_the_wave_meeting_its_past():
    """two partials whose sum comes back at a lag for a moment (the FM-1's USB at 22.180 s of rhodes-recipe-dry1,
    2026-10-24: 16 exact frames at lag 517, the difference to the wave 517 frames earlier falling into the run and
    out of it a few LSB a frame, growing to dozens 32 frames out): no stale. The same wave with 32 frames copied in
    from 517 earlier (a jump into the copy and out of it) is one"""
    fs, L, c = 44100, 517, 20200
    n = np.arange(fs)
    x = np.round(400 * np.sin(2 * np.pi * 65.4 * n / fs) + 400 * np.sin(2 * np.pi * 98.0 * n / fs + 1.0))
    k = np.arange(c - 60, c + 61)
    for i in k:                                    # (the difference: a smooth valley, 0 for 31 frames, 37 at +-47)
        x[i] = x[i - L] + np.round(((i - c) / 9.0) ** 4 / 20.0) * (1 if i < c else -1)
    q = np.stack([x, x], 1).astype(np.int64)
    run = C._exact_run(q, c, L, 16)
    assert run[1] - run[0] >= 16 and C.det_stale(q) == []
    q2 = q.copy()
    q2[25000:25032] = q2[25000 - L:25000 - L + 32]          # (on a slope, far off the wave 517 frames earlier)
    assert [p for p, _ in C.det_stale(q2)] == [25000]


def chord(n=44100 * 8, seed=1):
    """a synthetic stereo chord (int LSB): five partials, a slow swell, a little noise; no turning point is flat"""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 44100
    x = sum(a * np.sin(2 * np.pi * f * t + p) for a, f, p in ((5000, 110, 0), (3000, 277, 1), (2000, 440, 2),
                                                               (1200, 1250, 3), (600, 3100, 4)))
    x *= 0.7 + 0.3 * np.sin(2 * np.pi * 0.5 * t)
    for k, t0 in enumerate(np.arange(0.1, n / 44100 - 0.2, 0.37)):   # strikes: a decaying tone every 0.37 s
        f = 220 * 2 ** (k % 7 / 12)
        m = t >= t0
        x[m] += 4000 * np.exp(-(t[m] - t0) / 0.15) * np.sin(2 * np.pi * f * (t[m] - t0))
    x *= 0.5                                                 # (peaks near -12 dBFS)
    L = x + rng.normal(0, 3, n)
    R = 0.8 * x + rng.normal(0, 3, n)
    return np.round(np.stack([L, R], 1))


def kinds(res, src="usb"):
    return sorted({k for _, k, _ in res[src]})


def test_detectors_on_a_synthetic_chord():
    q = chord()
    j = C.sim_jack(q)
    res = C.analyse(q, j, 44100, [])
    assert not C.gated(res), C.gated(res)[:5]
    rng = np.random.default_rng(0)
    for kind in ("click", "repeat1", "hold", "chunk32", "stale128", "block256", "dropout", "clip", "step", "drop1ms",
                 "jack_click", "jack_dropout"):
        u2, j2, marks = C.inject(kind, q, j, [], rng, 3)
        res = C.analyse(u2, j2 if C.KINDS[kind][0] == "jack" else j, 44100, [])
        assert C.found(res, kind, marks) == len(marks) == 3, (kind, kinds(res), kinds(res, "jack"))


def test_a_repeat_at_a_turning_point_is_no_event():
    """the repeat detector wants a slope on both sides: a flat top of a slow wave is music, not an underrun"""
    t = np.arange(4410)
    x = np.round(8000 * np.sin(2 * np.pi * 50 * t / 44100))
    q = np.stack([x, x], 1)
    assert C.det_repeat(q) == []
    i = 1000                                                 # on the rising slope: an underrun's copy
    q2 = np.concatenate([q[:i], q[i - 1:i], q[i:]])
    assert [p for p, _ in C.det_repeat(q2)] == [i - 1]


def test_the_limiter_corner_is_information():
    """a click where the limiter works is 'click-lim' (information), not a gated click"""
    q = chord()
    q = np.round(q * 24000 / np.abs(q).max())
    q[int(np.argmax(np.abs(q[:, 0]))) + 300] += 2000         # (just after the loudest frame: the limiter works)
    res = C.analyse(q, None, 44100, [])
    assert ("click-lim" in kinds(res)) and "click" not in kinds(res)


def test_guards_cover_the_delay_echoes():
    ccs = C.engine_ccs("rhodes", dict(DL_SW=48, DL_DEPTH=64, DL_TIME=60))
    g = C.guards([(1.0, "on", 60, 100)], ccs)
    dt = C.hil_scan.dly_ms(60) / 1000
    assert np.isclose(g[0], 1.0) and np.isclose(g[1], 1.0 + dt) and len(g) >= 3
    assert list(C.guards([(1.0, "on", 60, 100)], C.engine_ccs("rhodes", {}))) == [1.0]


def test_periodicity():
    n, share = C.periodicity([0.1 + k * C.BEAT_S * m for k, m in enumerate([1, 1, 2, 1, 3, 1])])
    assert n == 6 and share == 1.0
    assert C.periodicity([0.1, 0.2]) == (2, None)


def test_one_counter_reader():
    """the counters go through tools/hil_must.py's reader (A's protocol: CC 100 id, 101 / 102 value; the whole ISR on
    CC 108 / 107): a reply without the USB items fails the gate, as no reply does ("not reported" never passes)"""
    import hil_must as M
    acc = M.Acc()
    acc.add(M.parse_reply([(116, 7), (117, 100), (115, 0), (108, 8), (107, 1)]))      # (no USB items)
    rows = M.gate_counters(acc, 48000)
    assert any(r[0] == "USB counters answer" and r[3] == "FAIL" for r in rows)
    assert any(r[3] == "FAIL" for r in M.gate_counters(M.Acc(), 48000))            # no reply at all
    src = open(C.__file__).read()
    assert "hil_must" in src and "USB_CC" not in src and "def parse_reply" not in src


def test_t1_wiring():
    """stress_max is T1's S1: hil_scan TESTS has it (hil_must calls it by name with the keywords it accepts), and it
    is not in hil_scan's default run"""
    import inspect
    import hil_scan
    fn = hil_scan.TESTS["stress_max"]
    sig = inspect.signature(fn).parameters
    assert {"rate", "seconds", "quick", "tier", "engines"} <= set(sig)
    assert "stress_max" in open(os.path.join(ROOT, "tools", "test_map.py")).read()


def test_dry_run_plan(capsys):
    assert C.main(["--dry-run"]) == 0
    o = capsys.readouterr().out
    for eng in C.ENGINES:
        assert f"{eng}-worst1" in o and f"{eng}-recipe-dry1" in o and f"{eng}-no-usb" in o
    assert all(f"voices {C.voices(e)}" in o for e in C.ENGINES) and "voices 10" in o  # each engine its own count
    assert "gates: the firmware's counters (hil_must.gate_counters: they must answer" in o


def test_the_stimulus_holds_every_voice():
    """every worst-case chord is one per voice of its engine, under the pedal"""
    for eng in C.ENGINES:
        for name, ccs, ev, dur in C.takes(eng, 20, 24):
            if "worst" in name:
                ons = [e for e in ev if e[1] == "on"]
                by_t = {}
                for e in ons:
                    by_t.setdefault(e[0], []).append(e[2])
                assert {len(v) for v in by_t.values()} == {C.voices(eng)}
                assert (0.2, "cc", 64, 127) in ev


def test_hil_scan_lists_stress_max(capsys, monkeypatch):
    import hil_scan
    monkeypatch.setattr(sys, "argv", ["hil_scan.py", "--list"])
    assert hil_scan.main() == 0
    assert "stress_max" in capsys.readouterr().out


def test_hil_cpu_reads_the_same_counters():
    """hil_cpu.py (the USB stream open, the release blocks, the slow paths) and this tool read the one protocol
    (hil_cpu USB_ITEMS through hil_must) and the same voice counts"""
    import hil_cpu
    import hil_must
    assert hil_must.USB_ITEMS is hil_cpu.USB_ITEMS and hil_cpu.TARGET_US == C.CPU_TARGET_US
    assert hil_cpu.default_voices("rhodes") == C.voices("rhodes") and hil_cpu.default_voices("vibes") == C.voices("vibes")


def test_track_stops_where_usb_ends():
    """the FM-1's USB clock runs ~550 ppm off the jack's: with both recordings the same length, the jack's last
    windows want USB frames past its end (marimba-worst3, both rates: ncc 0.77 / 0.80 at 59.900 s of a 60 s take,
    the warp's zero padding); those windows are not judged, the rest still are"""
    q = chord(44100 * 30)
    j = C.sim_jack(q, drift=1000e-6)                         # (USB's frame for jack frame a: a (1 + 1000 ppm))
    q2 = q[:int(len(j) * 44100 / 48000)]                     # (both recordings as long: the jack runs past USB;
    rows, ev = C.track(q2, j, 44100)                         # before the fix: ncc 0.895 at 29.900 s)
    assert ev == [], ev
    assert rows and rows[-1][0] > 29.0                       # (judged to within a second of the end)
    j2 = j.copy()                                            # (a slip still found well before the end)
    j2[int(25 * 48000):] = np.concatenate([j[int(25 * 48000) + 48:], np.zeros(48)])
    assert any("slip" in d for _, d in C.track(q2, j2, 44100)[1])


def spell(n=48000 * 2, at=48000, gain=0.25):
    """(usb at 48 kHz, jack): a loud wave that falls into a 1.6 ms quiet spell and climbs out of it, steeply enough
    for det_zero's cut-in test (as the FM-1's marimba at 34.873 s of marimba-worst1), USB with an offset the jack's
    AC coupling has taken off"""
    t = (np.arange(n) - at) / 48000.0
    w = np.clip(np.abs(t) / 0.002 - 0.4, 0.0, 1.0)
    u = 3000 * np.sin(2 * np.pi * 150 * t + 0.3) * w + 160.0
    j = gain * (u - 160.0)
    return u, np.round(j)


def test_a_quiet_spell_usb_has_too_is_no_dropout():
    u, j = spell()
    z = C.det_zero(j, 48000, exact=False)
    assert [abs(p - 48000) < 48 for p, _ in z] == [True]     # (the cut-in test alone takes it for a dropout)
    v, why = C.zero_vs_usb(j, u, z[0][0])
    assert v is False, why
    j2 = np.round(0.25 * (u - 160.0))                        # a real dropout: 2 ms of the loud wave gone
    a = 48000 + int(0.01 * 48000)
    j2[a:a + 96] = 0.0
    p = [p for p, _ in C.det_zero(j2, 48000, exact=False) if abs(p - a) < 5]
    assert p and C.zero_vs_usb(j2, u, p[0])[0] is True
    assert C.zero_vs_usb(j2, np.zeros_like(u), p[0])[0] is None   # (no USB there: no judgement, the event stands)


def test_analyse_reports_the_jack_dropout_not_the_spell():
    """through analyse: USB at 44.1 kHz, the jack at 48; the music's own quiet spell is information, a jack dropout in
    the same take an event"""
    from scipy.signal import resample_poly
    n = 48000 * 6
    t = np.arange(n) / 48000.0
    x = chord(n)[:, 0]                                       # (as a 48 kHz wave: strikes, so the lag is unique)
    x *= np.clip(np.abs(t - 3.0) / 0.002 - 0.4, 0.0, 1.0)  # (the chord falls into a 1.6 ms spell at 3 s, as spell)
    u44 = np.round(resample_poly(x, 147, 160))
    q = np.stack([u44, u44], 1)
    j = np.round(0.25 * np.concatenate([np.zeros(96), x[:-96]]))   # (the jack: 2 ms later, 12 dB under; sim_jack's
    res = C.analyse(q, j, 44100, [], only=("zero",))               # AC coupling leaves the spell at 15 LSB)
    assert [k for _, k, _ in res["jack"]] == [], res["jack"]
    assert len(res["info"]["jack_zero_own"]) == 1, res["info"]
    j2 = j.copy()
    j2[int(4.2 * 48000):int(4.2 * 48000) + 120] = 0.0
    res = C.analyse(q, j2, 44100, [], only=("zero",))
    assert [(round(tt, 2), k) for tt, k, _ in res["jack"]] == [(4.2, "zero")], res["jack"]


def test_a_jack_gap_between_two_zero_crossings():
    """rhodes-worst1 at 17.748 s (INFINITE SUSTAIN, 2026-10-25): a 4.6 ms jack gap began and ended where the wave
    crossed zero (under 40 LSB 0.25 ms either side), so det_zero's cut-in test let it pass as a quiet spell, though
    440 LSB of wave were gone. Such a run is now judged against USB: USB has the sound there, so it is an event; the
    music's own spell (USB just as quiet) stays information"""
    from scipy.signal import resample_poly
    n = 48000 * 6
    t = np.arange(n) / 48000.0
    x = chord(n)[:, 0]
    a0, a1 = int(4.3 * 48000), int(4.7 * 48000)              # (a 400 ms 40 Hz tone in the chord's place: zero
    x[a0:a1] = 2000 * np.sin(2 * np.pi * 40 * (t[a0:a1] - t[a0]))    # crossings every 600 samples, slow ones)
    x *= np.clip(np.abs(t - 3.0) / 0.002 - 0.4, 0.0, 1.0)
    u44 = np.round(resample_poly(x, 147, 160))
    q = np.stack([u44, u44], 1)
    j = np.round(0.25 * np.concatenate([np.zeros(96), x[:-96]]))
    g = a0 + 96 + 600 * 16                                   # (the jack's own zero crossing, 200 ms into the tone)
    j2 = j.copy()
    j2[g + 1:g + 600] = 0.0                                  # (a half cycle, 500 LSB of wave, gone)
    assert np.abs(j[g + 1:g + 600]).max() > 400
    assert all(abs(p - g) > 300 for p, _ in C.det_zero(j2, 48000, exact=False))   # (the cut-in test alone: passed)
    res = C.analyse(q, j2, 44100, [], only=("zero",))
    assert [(round(tt, 2), k) for tt, k, _ in res["jack"]] == [(round((g + 1) / 48000, 2), "zero")], res["jack"]
    assert len(res["info"]["jack_zero_own"]) == 1, res["info"]   # (the spell at 3 s: still the music's own)


def test_the_usb_stream_is_switched_to_the_rate():
    """"--rate 44100" left the FM-1 at 48 kHz (Core Audio kept the device at its nominal rate and converted; every
    take's counter gate said "flowing, 48 kHz"): the recorder now opens USB with the device switched to the rate
    (CoreAudioSettings, as hil_cpu --usb and hil_must's S1), and before any take the FM-1's own stream state must say
    flowing at that rate; a mismatch (or no state) is a RateError, which t1 / main turn into a FAIL"""
    import hil_must as M
    import hil_scan
    from hil_cpu import USB_ITEMS
    sid = {v: k for k, v in USB_ITEMS.items()}["state"]
    opened = []

    class FakeStream:
        def __init__(self, **kw):
            opened.append(kw)
            self.kw = kw

        def start(self):
            class Ti:
                inputBufferAdcTime, currentTime = 1.0, 1.0
            self.kw["callback"](np.zeros((64, self.kw["channels"]), np.float32), 64, Ti, 0)

    class FakeSd:
        InputStream = FakeStream

        @staticmethod
        def CoreAudioSettings(**kw):
            return ("coreaudio", tuple(sorted(kw.items())))

        @staticmethod
        def query_devices(i=None):
            return dict(default_samplerate=48000.0)
    cap = hil_scan.Capture(FakeSd)
    cap.add("usb", 1, (0, 1), 2, sr=44100, switch_rate=True)
    cap.add("jack", 2, (4,), 8, sr=48000)
    assert opened[0]["samplerate"] == 44100
    assert opened[0]["extra_settings"] == ("coreaudio", (("change_device_parameters", True),
                                                         ("fail_if_conversion_required", True)))
    assert "extra_settings" not in opened[1]                  # (the jack's interface keeps its own rate)
    src = open(C.__file__).read()
    assert "switch_rate=True" in src.split("def open(self, usb=True)")[1].split("def send")[0]

    def reply(state):
        return [(100, sid), (101, state >> 7), (102, state & 127)]

    class FakeMidi:
        def __init__(self, states):
            self.states = list(states)

        def query(self, wait=0.25):
            return reply(self.states.pop(0) if len(self.states) > 1 else self.states[0])
    for rate, states, ok in ((44100, [2], True), (48000, [6], True), (44100, [6], False), (48000, [2], False),
                             (44100, [0], False), (44100, [0, 6, 2], True)):
        d = C.Device.__new__(C.Device)
        d.hm, d.rate, d.mdev = M, rate, FakeMidi(states)
        if ok:
            assert C.stream_ok(d.check_rate(wait=2.0), rate)
        else:
            with pytest.raises(C.RateError):
                d.check_rate(wait=0.3)
    d = C.Device.__new__(C.Device)
    d.hm, d.rate = M, 44100
    d.mdev = type("NoUsb", (), {"query": lambda self, wait=0.25: [(116, 7), (117, 100)]})()
    with pytest.raises(C.RateError, match="before the USB ring fix"):
        d.check_rate(wait=0.3)
    mu = open(os.path.join(ROOT, "tools", "hil_must.py")).read()   # (hil_must's S1 checks it before the run too)
    assert "USB stream state before the run" in mu.split("def stress_max(self")[1].split("# S2")[0]


def held_tone(n=44100 * 12):
    """(usb, events): rev_hil's folded jack check in small: silence, a quiet low note held 3 s (a 55 Hz tone decaying
    from its strike, as a tine: an inharmonic partial and a tick), a loud short note, then a few low notes 0.9 s
    each"""
    t = np.arange(n) / 44100
    x = np.zeros(n)
    ev = []
    for t0, d, f, a in [(1.0, 3.0, 55.0, 300.0), (5.0, 1.5, 131.0, 8000.0), (7.0, 0.9, 55.0, 2000.0),
                        (8.2, 0.9, 110.0, 4000.0), (9.4, 0.9, 55.0, 6000.0)]:
        m = (t >= t0) & (t < t0 + d)
        s = t[m] - t0
        x[m] += a * (np.exp(-s / 1.5) * np.sin(2 * np.pi * f * s)               # (the tone, an inharmonic
                     + 0.3 * np.exp(-s / 0.4) * np.sin(2 * np.pi * 4.2 * f * s)      # partial, the strike's
                     + 0.5 * np.exp(-s / 0.01) * np.sin(2 * np.pi * 1700.0 * s))     # bright tick)
        ev += [(t0, "on", 33, 64), (t0 + d, "off", 33, 0)]
    return np.round(np.stack([x, x], 1)), sorted(ev)


def test_drift_fit_on_a_held_low_tone():
    """the folded jack check (rhodes-jackcheck, 2026-10-25): a decaying held tone's raw correlation peaks at the
    search's loud edge, 0.3 s early, in every window; the drift line went through those (L0 -14589 samples against
    the true -144) and every window of the quiet low note gave 'ncc 0.85-0.92 < 0.97' (328 false alarms). With the
    clocks' line as the prior (analyse passes it) the drift line is the true one, the clean take gives no event, and a
    jack that is not USB's wave is still an event"""
    q, ev = held_tone()
    j = C.sim_jack(q)
    u4 = np.asarray(q, float)[:, 0]
    from scipy.signal import resample_poly
    u48 = resample_poly(u4, 640, 147)[::4]
    n = min(len(u48), len(j))
    assert C._drift_fit(u48, j, n, 2400, 32767 * 1e-3)[0] < -10000   # (the edge's false line, without the prior)
    L0, r, inl = C._drift_fit(u48, j, n, 2400, 32767 * 1e-3, prior=(0.0, 0.0))
    a = np.arange(0, n, 4800)                               # (the line within 6 samples of the truth all along)
    off = np.abs(L0 + r * a - (a * C.JACK_DRIFT - C.JACK_DELAY * 48000)).max()
    assert off <= 6 and inl >= 8, (L0, r, inl, off)
    g = C.guards(ev)
    rows, tev = C.track(q, j, 44100, gtimes=g, prior=(0.0, 0.0))
    assert tev == [], tev[:5]
    assert rows
    j2 = j.copy()                                           # (the jack's own fault in the last low note, over
    s = slice(int(9.6 * 48000), int(9.8 * 48000))           # -60 dBFS: 200 ms clipped flat, the level kept)
    j2[s] = np.clip(j2[s], -0.3 * np.abs(j2[s]).max(), 0.3 * np.abs(j2[s]).max())
    j2[s] *= np.sqrt((j[s] ** 2).mean() / (j2[s] ** 2).mean())
    _, tev2 = C.track(q, j2, 44100, gtimes=g, prior=(0.0, 0.0))
    assert any(9.55 <= a / 48000 <= 9.8 and "ncc" in d for a, d in tev2), tev2


def test_the_folded_jack_check_on_the_host():
    """rev_hil.py stress --fold's rhodes-jackcheck rendered by the host build and judged as the release run's
    self-test does (2026-10-25: 161 false 'ncc < 0.97' a timing from 3.3 s on, the drift line 0.3 s off): no event
    on the clean take; the jack's own fault (100 ms clipped flat in the harmonics' notes) is a track event"""
    sys.path.insert(0, os.path.join(ROOT, "tools", "final"))
    import rev_hil as RH
    name, ccs, ev, dur = RH.fold_takes(C, "rhodes", None, [])[0]
    assert name == "rhodes-jackcheck"
    u, _ = C.host_take(name, ccs, ev, dur, tag="pytest-fold")
    j = C.sim_jack(u)
    res = C.analyse(u, j, C.FS_HOST, ev, ccs=ccs)
    assert C.gated(res) == [], C.gated(res)[:5]
    j2 = j.copy()
    s = slice(int(10.0 * 48000), int(10.1 * 48000))        # (note 33 at 80, from 9.5 s: past its attack)
    pk = 0.3 * np.abs(j2[s]).max()
    j2[s] = np.clip(j2[s], -pk, pk) * np.sqrt((j[s] ** 2).mean() / (np.clip(j[s], -pk, pk) ** 2).mean())
    res2 = C.analyse(u, j2, C.FS_HOST, ev, ccs=ccs, only={"track"})
    assert any(9.95 <= t <= 10.1 and kd == "track" for t, kd, _ in res2["jack"]), res2["jack"][:5]


def test_a_repeat_under_the_floor_is_no_event():
    """under -42 dBFS the music's own plateaus run 4 frames and more (FM-1 seed 5: 4 frames at -65 LSB between +8 and
    +14, every counter and both USB benches clean): no event there; the same copies on a loud slope are one"""
    t = np.arange(4410)
    for amp, want in ((150, 0), (8000, 1)):
        x = np.round(amp * np.sin(2 * np.pi * 50 * t / 44100))
        q = np.stack([x, x], 1)
        i = 1000
        q2 = np.concatenate([q[:i], np.repeat(q[i - 1:i], 3, axis=0), q[i:]])
        assert len(C.det_repeat(q2)) == want, amp
