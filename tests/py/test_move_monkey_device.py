"""tools/move_monkey.py, offline: the take's plan (the writes cut and the resets placed before the panic), the
WebSocket's pace, the deadline, the guards and the verdict. No device."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_monkey as mm  # noqa: E402
import monkey  # noqa: E402


def test_segment_plan():
    seg = mm.segment(1000)
    t0 = seg["t0"]
    assert t0 == monkey.RAND_MS + 50
    ts = [e[0] for e in seg["events"]]
    assert ts == sorted(ts) and ts[-1] < seg["dur_ms"]
    rnd = [w for w in seg["writes"] if not w[3]]
    rs = [w for w in seg["writes"] if w[3]]
    assert rnd and all(w[0] <= t0 - mm.CUT_MS for w in rnd)
    assert sorted((k, v) for _, k, v, _ in rs) == sorted(mm.tmm.RESETS)
    assert all(w[0] == t0 - mm.RESET_MS for w in rs)
    assert any(k == "p_type" for _, k, _, _ in rnd) and any(k == "preset" for _, k, _, _ in rnd)
    assert seg["skipped"].get("setstate", 0) > 0
    # (a reset sent at its time lands before the panic at the latest start, and in time at the earliest)
    assert mm.LATE_MS + t0 - mm.RESET_MS < t0 + mm.LEAD_MS and mm.RESET_MS > mm.LATE_MS - mm.LEAD_MS + mm.SAFE_MS


def test_pace_limits_the_burst():
    p = mm.Pace()
    sent = []
    for i in range(100):                   # a burst of 100 writes all due at once
        w = p.at(0.0)
        p.mark(w)
        sent.append(w)
    assert all(b - a >= mm.MIN_GAP_S - 1e-9 for a, b in zip(sent, sent[1:]))
    for i in range(len(sent)):
        n = sum(1 for s in sent if sent[i] <= s < sent[i] + mm.WINDOW_S - 1e-9)
        assert n <= mm.BURST
    # (the manager's set ring holds 32: at this pace it takes a stall of over a second to fill it)
    assert 32 / mm.BURST * mm.WINDOW_S >= 1.0


def test_schedule_deadline():
    seg = mm.segment(1001)
    sch = mm.schedule(seg["writes"], 100.0, seg["t0"])
    assert not any(s[4] for s in sch)                      # the plan as made: nothing late
    assert all(a[0] <= b[0] for a, b in zip(sch, sch[1:]))
    late = mm.schedule([(seg["t0"] - 100, "s_mch", "0", True)], 100.0, seg["t0"])
    assert late[0][4]


def test_guard_events():
    g = mm.guard_events([(10, 0x91, 60, 100), (20, 0x90, 60, 0), (30, 0x82, 61, 5), (40, 0xB3, 64, 127),
                         (50, 0xF8, 0, 0)])
    assert g == [(0.01, "on", 60, 100), (0.02, "off", 60, 0), (0.03, "off", 61, 0), (0.04, "cc", 64, 127)]


def test_judge():
    seg = mm.segment(1002)
    mk = seg["mk"]
    x = np.zeros((monkey.ms(seg["dur_ms"]), 2), np.int32)
    n0, rel = mk["note"]
    x[monkey.ms(n0):monkey.ms(rel), :] = 3000
    info = {"played": len(seg["events"]), "dropped": 0, "bad": 0, "aborted": 0, "late": 3}
    assert mm.judge(x, seg, info, [], 0, [(1.0, "click", "x"), (2.0, "zero", "y")]) == []
    kinds = lambda b: [k for k, _ in b]                       # noqa: E731
    assert kinds(mm.judge(x, seg, dict(info, dropped=2), [], 0, [])) == ["player"]
    assert kinds(mm.judge(x, seg, dict(info, played=5), [], 0, [])) == ["player"]
    assert kinds(mm.judge(x, seg, info, ["web param set ring full"], 0, [])) == ["schwung"]
    assert kinds(mm.judge(x, seg, info, [], 1, [])) == ["reset"]
    assert kinds(mm.judge(x, seg, info, [], 0, [(3.0, "repeat", "z")])) == ["repeat"]
    y = x.copy()
    y[monkey.ms(mk["quiet"][0]) + 3, 1] = 2
    assert kinds(mm.judge(y, seg, info, [], 0, [])) == ["silence"]


def test_dry_run(capsys):
    assert mm.main(["--dry-run", "--seed", "1000", "--minutes", "30"]) == 0
    assert "seed 1000:" in capsys.readouterr().out


def test_no_sysex_to_the_player():
    """the Move, 2026-10-28: the dev player refuses SysEx (move_bridge.h, counted bad), and 4 of 41 seeds had one of
    the stream's garbage 0xF0 statuses; the tool leaves them out and counts them"""
    seg = mm.segment(1015)
    assert all(0x80 <= e[1] <= 0xFF and e[1] not in (0xF0, 0xF7) for e in seg["events"])
    assert seg["skipped"].get("sysex") == 1
