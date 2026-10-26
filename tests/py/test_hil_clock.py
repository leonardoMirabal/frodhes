# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_clock.py (every clock-synced item against an external MIDI clock, CLOCK INT, TRS IN OFF) proven on the
host, without the FM-1: the plans (24 ticks a beat on absolute times, the change, the restart, Continue for the
sequencer, the CLOCK INT decoy, the out-of-range and TRS IN OFF takes, the CC values from the firmware), the
analysis on synthetic recordings (a firmware that follows: PASS; free-running 2 % / 0.8 % off: FAIL; the right rate
never re-phased by a Start: FAIL where a Start must re-phase), the note-onset detector, the MANUAL rows, the
summary table, the sender's absolute timing, the report, the dry run and --reanalyze."""
import json
import os
import sys
import time

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_clock as C  # noqa: E402

FS = 22050                       # (the analysis at any rate; 48 kHz in the selftest)
EXT = C.plan()
INT = C.plan(mode="int")


def _run(takes, mode, fs=FS, seed=1, **kw):
    res = {}
    for tk in takes:
        x, ev = C.synth(tk, mode, fs, seed=seed + sum(map(ord, tk["name"])), **kw)
        res[tk["name"]] = C.analyze(x, fs, ev)
    return res


@pytest.fixture(scope="module")
def good():
    return _run(EXT, "good")


def test_cc_values_from_the_firmware():
    assert C.sync_quarter_cc("TW_SYNC")[0] == 20 and C.sync_quarter_cc("DL_SYNC")[0] == 25
    for p, cc in (("PH_SYNC", 15), ("CP_SYNC", 23), ("LFO_SYNC", 77), ("MOT_SYNC", 51)):
        c, v = C.sync_quarter_cc(p)
        assert c == cc and 0 < v < 128
    assert C.cc_for("DL_SYNC", C.LONG_IDX)[0] == 25


def test_every_synced_item_has_a_row():
    rows = dict(C.ROWS)
    for k, it in C.items().items():
        assert k in rows or k == "range"
    for k in ("trem", "wah", "phaser", "chorus", "filter", "motor", "delay_dig", "delay_ana", "delay_tape",
              "delay_long", "arp", "seq", "range", "channel", "trs_off", "int_clock", "tap"):
        assert k in rows
    assert C.items()["wah"]["kind"] == "manual" and C.items()["tap"]["kind"] == "manual"


def test_plan_ticks_are_absolute_and_24_a_beat():
    for tk in EXT:
        t = [e[0] for e in tk["sched"]]
        assert t == sorted(t)
        beats = [tk["sched"][j][0] for j in tk["beats"]]
        for sg in tk["segs"]:
            assert np.allclose(np.diff(beats[sg["i0"]:sg["i1"]]), 60.0 / sg["bpm"], atol=1e-9)
    names = {tk["name"] for tk in EXT}
    assert {"trem_90", "trem_120", "trem_137", "phaser_90", "phaser_137", "motor_120", "seq_120", "arp_120",
            "delay_tape_120", "delay_long_120", "range_120"} <= names


def test_plan_full_take_change_restart_continue():
    t = next(tk for tk in EXT if tk["name"] == "trem_120")
    assert [(s["kind"], s["bpm"], s["lock"]) for s in t["segs"]] == [("start", 120, 8), ("change", 100, 2),
                                                                       ("start", 100, 8)]
    tr = [e for e in t["sched"] if e[2][0] in ("start", "stop", "continue")]
    assert [e[2][0] for e in tr] == ["start", "stop", "start", "stop"]
    assert abs(tr[2][0] - tr[1][0] - C.PAUSE_BEATS * 0.6) < 0.002
    s = next(tk for tk in EXT if tk["name"] == "seq_120")
    assert [e[2][0] for e in s["sched"] if e[2][0] in ("start", "stop", "continue")] == ["start", "stop", "continue",
                                                                                          "stop"]
    assert not any(e[2] == ("cc", C.CC_SEQ, 0) for e in s["sched"])   # (the sequencer item: no stop after a Start)
    for tk in EXT:
        if tk["kind"] != "seq" and tk["item"] != "trs_off":
            st = [e[0] for e in tk["sched"] if e[2] == ("start",)]
            sq = [e[0] for e in tk["sched"] if e[2] == ("cc", C.CC_SEQ, 0)]
            assert len(st) == len(sq)


def test_plan_int_sends_a_decoy_not_the_grid():
    for tk in INT:
        virt = [e for e in tk["sched"] if e[1] == "virt"]
        decoy = [e for e in tk["sched"] if e[2] == ("clock",) and e[1] == "usb"]
        assert virt and decoy and not any(e[2][0] in ("start", "stop") for e in tk["sched"])
        assert np.median(np.diff([e[0] for e in decoy])) == pytest.approx(60 / (24 * C.DECOY_BPM), rel=1e-6)
    assert {tk["item"] for tk in INT} >= {"trem", "phaser", "delay_long", "seq", "arp"}


def test_plan_range_and_trsoff():
    r = next(tk for tk in EXT if tk["item"] == "range")
    clk = np.array([e[0] for e in r["sched"] if e[2] == ("clock",)])
    d = np.diff(clk)
    assert (d > 0.080).sum() >= 90 and (d < 0.008).sum() >= 180         # 30 BPM and 400 BPM: out of 8..80 ms
    t = C.plan(mode="trsoff")[0]
    usb = [e for e in t["sched"] if e[2] == ("clock",) and e[1] == "usb"]
    trs = [e for e in t["sched"] if e[2] == ("clock",) and e[1] == "clk"]
    assert len(usb) == 12 * 24 and len(trs) == 16 * 24 and min(e[0] for e in trs) > max(e[0] for e in usb)


def test_expected_echo_halves_the_long_division():
    tk = next(t for t in EXT if t["item"] == "delay_long")
    ev = C.take_tau(tk, [e[0] for e in tk["sched"]], 0.0, 48000, 48000)
    assert C.expected_echo(ev, 0) == pytest.approx(1.0, abs=1e-6)      # 1/1 at 120: 2.0 s > 1.486: 1.0 s
    assert C.expected_echo(ev, 2) == pytest.approx(1.2, abs=1e-6)      # at 100: 2.4 s -> 1.2 s


def test_a_following_firmware_passes(good):
    v, per = C.judge(good)
    assert v.startswith("PASS") and "MANUAL" in v, [p["problems"][:2] for p in per.values() if p["problems"]]
    assert per["wah"]["verdict"] == "MANUAL" and per["tap"]["verdict"] == "MANUAL"
    for r in good.values():
        for p in r.get("periods", []):
            assert abs(p["err_pct"]) < 0.15
        for p in r.get("probes", []):
            assert all(abs(e) < 0.4 for e in p["err_ms"])
        for s in r.get("segs", []):
            assert s["found"] >= 0.9 * s["expected"] and s["worst_ms"] < 1000 * C.GRID_TOL_S
    assert abs(good["trem_120"]["restart_shift"]) < 0.03


def test_int_and_trsoff_pass_when_followed():
    assert C.judge(_run(INT, "good"))[0].startswith("PASS")
    assert C.judge(_run(C.plan(mode="trsoff"), "good"))[0].startswith("PASS")


@pytest.mark.parametrize("factor", (1.02, 0.992))
def test_free_running_wrong_rate_fails_every_item(factor):
    v, per = C.judge(_run(EXT, "wrongrate", factor=factor))
    assert v == "FAIL"
    meas = [k for k, it in C.items().items() if it["kind"] != "manual"]
    assert all(per[k]["verdict"] == "FAIL" for k in meas), {k: per[k]["verdict"] for k in meas}


def test_int_following_the_decoy_fails():
    v, per = C.judge(_run(INT, "wrongrate"))
    assert v == "FAIL" and per["delay_dig"]["verdict"] == "FAIL" and per["trem"]["verdict"] == "FAIL"


def test_never_rephased_fails_on_the_restart():
    res = _run(EXT, "freephase", seed=5)
    v, per = C.judge(res)
    assert v == "FAIL"
    for k in ("trem", "motor", "phaser", "filter", "arp"):
        assert per[k]["verdict"] == "FAIL", k
    assert all(p["ok"] for r in res.values() for p in r.get("periods", []))   # the rate is right ...
    assert abs(res["trem_120"]["restart_shift"]) > 0.25                       # ... the Start moved nothing


def test_onsets_find_attacks_not_the_second_rise():
    fs = 48000
    x = np.zeros(fs * 2)
    rng = np.random.default_rng(0)
    on = 0.1 + 0.125 * np.arange(12)
    for k, t in enumerate(on):
        C._burst(x, fs, t, 261.63 * 2 ** ((k % 3) / 12), 0.25, 0.25, click=0.05, rng=rng)
        C._burst(x, fs, t + 0.045, 523.25, 0.03, 0.05)                   # a smaller second rise, no click
    got = C.note_onsets(x.astype(np.float32), fs)
    assert len(got) == len(on)
    assert np.max(np.abs(got - on)) < 0.0015


def test_selftest_meets_its_expectations():
    assert C.selftest(fs=FS, verbose=False)


class _Port:
    def __init__(self):
        self.t = []

    def send(self, msg):
        self.t.append(time.perf_counter())


class _Mido:
    class Message:
        def __init__(self, *a, **kw):
            pass


def test_sender_keeps_absolute_time_and_skips_the_virtual_grid():
    tl = C.Timeline(0.02)
    tl.run(240, 8, None)
    tl.run(240, 2, None, dest="virt")
    sched = sorted(tl.ev, key=lambda e: e[0])
    p = _Port()
    t0 = time.perf_counter() + 0.02
    s = C.Sender(sched, {"clk": p, "usb": p}, t0, _Mido)
    s.run()
    assert len(p.t) == 8 * 24
    late = np.array(p.t) - (t0 + np.array([e[0] for e in sched if e[1] != "virt"]))
    assert late.min() >= 0 and np.median(late) < 0.002 and late[-10:].max() < 0.02


def test_channel_port():
    mido = pytest.importorskip("mido")   # (the CI host job installs no mido: test_hil_rev does the same)
    got = []

    class P:
        def send(self, m):
            got.append(m)
    cp = C.ChannelPort(P(), 7)
    cp.send(mido.Message("note_on", note=60, velocity=1))
    cp.send(mido.Message("clock"))
    assert got[0].channel == 7 and got[1].type == "clock"


def test_report_summary_and_dry_run(tmp_path, capsys, good):
    for col, res, ch in (("usb", good, 8), ("int", _run(INT, "good"), 8)):
        v, txt = C.write_report(col, res, meta="synthetic", out=str(tmp_path / col), channel=ch)
        assert txt.startswith(f"hil_clock --via {col}: PASS")
    tab = C.summary_table(str(tmp_path))
    lines = {ln.split("|")[0].strip(): [c.strip() for c in ln.split("|")[1:]] for ln in tab.splitlines()[2:]}
    assert lines["TREM SYNC (tremolo)"] == ["PASS", "PASS", "not run"]
    assert lines["TREM SYNC on WAH"][:2] == ["MANUAL", "MANUAL"]
    assert lines["TAP tempo (PLAY)"] == ["MANUAL", "-", "-"]
    assert lines["CLOCK INT: an incoming clock changes nothing"][0] == "PASS"
    assert lines["the CHANNEL filter never blocks the clock"][1].startswith("PASS")
    assert lines["TRS IN OFF ignores the TRS clock"][2].startswith("MANUAL")
    assert C.main(["--dry-run", "--via", "usb,trs,int,trsoff", "--port", "Some Interface"]) == 0
    out = capsys.readouterr().out
    assert "trem_120" in out and "range_120" in out and "trsoff_120" in out and "CLOCK INT" in out
    with pytest.raises(SystemExit):
        C.parse_args(["--via", "trs", "--port", ""])


def test_reanalyze_round_trip(tmp_path):
    tk = next(t for t in EXT if t["name"] == "delay_dig_120")
    x, ev = C.synth(tk, "good", FS, seed=4)
    np.savez_compressed(tmp_path / "delay_dig_120.npz", x=(np.stack([x, x], 1) * 32767).astype(np.int16), fs=FS,
                        ev=json.dumps(ev))
    assert C.reanalyze(str(tmp_path)) == 0
    assert json.load(open(tmp_path / "report.json"))["items"]["delay_dig"]["verdict"] == "PASS"


@pytest.mark.parametrize("tau,ok", ((None, True), (4.0, False), (0.01, False)))
def test_tape_judged_against_its_designed_glide(tau, ok):
    """the change 500 -> 600 ms: echoes from the designed glide pass; a glide 2x slower or an instant jump fail"""
    beats = list(np.arange(0, 16) * 0.5) + list(8 + np.arange(0, 12) * 0.6)
    ev = dict(item="delay_tape", beats=beats, segs=[dict(kind="start", i0=0, i1=16, bpm=120, lock=8),
                                                    dict(kind="change", i0=16, i1=28, bpm=100, lock=2)])
    saved = C.TAPE_TAU_UP
    rows = []
    for tp, T in ((4.0, 0.5), (7.0, 0.5), (9.2, 0.6), (12.8, 0.6)):
        if tau:
            C.TAPE_TAU_UP = tau
        sp = C.tape_echoes(tp, 2, T, 0.5 if tp > 8 else T, 8.6)
        C.TAPE_TAU_UP = saved
        rows.append(dict(t_dry=tp, T=T, spacing_ms=[1000 * v for v in sp]))
    res = dict(probes=rows)
    C.tape_judge(res, ev)
    assert all(r["ok"] for r in rows) == ok, [r["err_ms"] for r in rows]


@pytest.mark.parametrize("cap,ok", ((None, True), (1.0 / 32, False), (1.0 / 24, False)))
def test_ana_judged_against_its_designed_bend(cap, ok):
    """ANA's change 500 -> 600 ms with echoes in the line (0.9.6, BUGHUNT #3): echoes from the designed bend (1/16 s a
    s, then (target - d) / 4096 a sample) pass, as on the FM-1 (589.5 / 599.1 ms, rel-096 fbd628b); a bend half as
    fast, or 2/3 as fast, fails. A probe on the new time at once (a silent line: no cap) passes as before"""
    beats = list(np.arange(0, 16) * 0.5) + list(8 + np.arange(0, 12) * 0.6)
    ev = dict(item="delay_ana", beats=beats, segs=[dict(kind="start", i0=0, i1=16, bpm=120, lock=8),
                                                   dict(kind="change", i0=16, i1=28, bpm=100, lock=2)])
    saved = C.ANA_CAP
    rows = []
    for tp, T in ((4.0, 0.5), (7.0, 0.5), (9.2, 0.6), (12.8, 0.6)):
        if cap:
            C.ANA_CAP = cap
        sp = C.tape_echoes(tp, 2, T, 0.5 if tp > 8 else T, 8.6, C.ana_time)
        C.ANA_CAP = saved
        tol = C.DLY_TOL_S + C.DLY_TOL_REL * T
        rows.append(dict(t_dry=tp, T=T, spacing_ms=[1000 * v for v in sp], ok=all(abs(v - T) <= tol for v in sp)))
    assert not rows[2]["ok"]                              # (the plain check alone fails the bend)
    C.tape_judge(dict(probes=rows), ev, C.ana_time, or_plain=True)
    assert all(r["ok"] for r in rows) == ok, [r.get("err_ms") for r in rows]


def test_ana_fm1_take_fits_the_model():
    """the FM-1's probe 2 (rel-096 fbd628b, USB): the bend from 500.2 ms starting 0.63 beat after the change"""
    d = C.ana_time
    sp = C.tape_echoes(8 + 2 * 0.6, 2, 0.6, 0.5, 8 + 0.63 * 0.6, d)
    assert abs(1000 * sp[0] - 589.5) < 2 and abs(1000 * sp[1] - 599.1) < 2, sp


def _period_take(slope, noise, n=8, seed=1):
    """the period's fit on n one-beat windows: phases drifting by slope (cycles a beat) plus a scatter"""
    rng = np.random.default_rng(seed)
    bs = np.arange(n) + 8.5
    return bs, slope * bs + noise * rng.standard_normal(n)


@pytest.mark.parametrize("slope,noise,ok", ((0.0, 0.06, True), (0.0, 0.01, True), (0.012, 0.005, False),
                                            (-0.012, 0.005, False)))
def test_period_tolerance_follows_the_fit(slope, noise, ok):
    """the period passes within PERIOD_TOL + 2 standard errors of the slope: a weak modulation's scatter (the chorus
    from 0.9.6, ~0.06 cycle a window) is not read as a rate error; a real 1.2 % rate with a clean signal still fails"""
    bs, ph = _period_take(slope, noise)
    fit = np.polyfit(bs, ph, 1)
    res = ph - np.polyval(fit, bs)
    se = np.sqrt(np.sum(res ** 2) / (len(bs) - 2) / np.sum((bs - bs.mean()) ** 2))
    assert (abs(fit[0]) <= C.PERIOD_TOL + 2 * se) == ok, (fit[0], se)


def _reply(t, vals):
    out = []
    for k, v in vals.items():
        out += [(t, 100, k), (t, 101, v >> 7), (t, 102, v & 127)]
    return out


def test_clock_counters_queried_parsed_and_matched():
    tk = next(t for t in EXT if t["name"] == "seq_120")
    assert tk["query_labels"] == ["after start 1", "after start 2", "before stop 1", "before stop 2", "take end"]
    assert all(tk["sched"][q][2] == ("cc", C.CC_QUERY, C.QUERY_CLOCK) for q in tk["queries"])
    assert next(t for t in EXT if t["name"] == "trem_90")["query_labels"] == ["take end"]
    stamps = [e[0] for e in tk["sched"]]
    sent = [C.sent_counts(tk, stamps, q) for q in tk["queries"]]
    # 20 + 12 beats before the first Stop, less the tick 25 ms before it (the query goes 30 ms before the Stop)
    assert sent[2]["pulses"] == 32 * 24 - 1 and sent[2]["beats"] == 31 and sent[2]["spp"] == (32 * 24 - 1) // 6
    assert sent[3]["pulses"] == 16 * 24 - 1 and sent[0]["pulses"] <= 2
    # a firmware that counts right; then one whose SPP is 5 sixteenths short at the first Stop
    good = []
    for q, s_ in zip(tk["queries"], sent):
        good += _reply(stamps[q] + 0.02, {30: 1000, 38: 0, 40: s_["pulses"], 41: s_["pulses"], 42: s_["spp"] % 16})
    rows = C.match_counters(tk, stamps, C.parse_counters(good))
    assert len(rows) == 5 and not any(r["mismatch"] for r in rows) and rows[2]["got"]["SPP now"] == sent[2]["pulses"]
    q2 = stamps[tk["queries"][2]] + 0.02
    bad = [m for m in good if abs(m[0] - q2) > 1e-9] + _reply(q2, {30: 1000, 40: sent[2]["pulses"] - 30, 41: sent[2]["pulses"]})
    bad.sort(key=lambda m: m[0])
    rows = C.match_counters(tk, stamps, C.parse_counters(bad))
    assert rows[2]["mismatch"] and "SPP now" in rows[2]["mismatch"][0]
    assert C.match_counters(tk, stamps, []) == []                     # no readout: nothing, silently
    # an old firmware's CC 116 answer (CC 100-102 = USB counters, not ids 30-42) is no readout
    assert C.parse_counters([(1.0, 100, 0), (1.0, 101, 3), (1.0, 102, 7)]) == []


def test_clock_counters_are_pulses():
    """the firmware's SPP now (40) and beat count (41) are pulses (fafd16b: clk_sp, clk_k): 480 pulses after 20 beats
    is a match (it was read as 480 sixteenths / 480 beats against 80 / 20 sent: a false MISMATCH); a pulse or two
    in flight passes, 3 off fails; the mod-16384 wrap is no mismatch"""
    tk = next(t for t in EXT if t["name"] == "seq_120")
    stamps = [e[0] for e in tk["sched"]]
    q = tk["queries"][2]
    n = C.sent_counts(tk, stamps, q)["pulses"]
    def rows(v40, v41):
        return C.match_counters(dict(tk, queries=[q], query_labels=["x"]), stamps,
                                C.parse_counters(_reply(stamps[q] + 0.02, {30: 1, 40: v40, 41: v41})))
    assert not rows(n, n)[0]["mismatch"] and not rows(n - 2, n + 1)[0]["mismatch"]
    assert len(rows(n - 3, n + 3)[0]["mismatch"]) == 2
    assert len(rows(n // 6, n // 24)[0]["mismatch"]) == 2                 # (16ths / beats: not what it sends)
    tw = dict(tk, queries=[q], query_labels=["x"])
    fake = lambda tk_, st, qi: dict(pulses=16384 + 5, spp=0, beats=0)     # noqa: E731
    old, C.sent_counts = C.sent_counts, fake
    try:
        assert not C.match_counters(tw, stamps, C.parse_counters(_reply(stamps[q] + 0.02, {40: 5, 41: 6})))[0]["mismatch"]
    finally:
        C.sent_counts = old


def test_seq_take_waits_out_the_pattern_save(monkeypatch):
    """the seq take: after the pattern recording and silence, SAVE_WAIT_S (>= 1.6 s: the pattern's auto save runs
    1.5 s after its last change) before the take's first reference note; the other takes: no extra wait"""
    log = []
    monkeypatch.setattr(C.time, "sleep", lambda s: log.append(("sleep", s)))

    class Rig:
        def wait_silence(self, limit):
            log.append(("silence", limit))
    C.settle(Rig(), "seq")
    assert log[1][0] == "silence" and log[2] == ("sleep", C.SAVE_WAIT_S) and C.SAVE_WAIT_S >= 1.6
    log.clear()
    C.settle(Rig(), "lfo")
    assert [k for k, _ in log] == ["sleep", "silence"]
    assert C.REF_T[1] - C.REF_T[0] >= 0.35 and C.T_CHORD > C.REF_T[1] + 0.2 and C.T_CLOCK > C.T_CHORD
    tk = next(t for t in EXT if t["name"] == "seq_120")
    assert [tk["sched"][j][0] for j in tk["refs"]] == list(C.REF_T) and tk["first"] == tk["refs"][0]
