# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_c1_burn.py: the burn sweep over hil_c1_soak's run, with a fake run and fake ports (no device): the burn
CCs go out at each step, never while the unit boots and settles after a reset, the sweep stops after a late job / hold at once and after a crash
step's end, the report has a row per step; selftest."""
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hil_c1_burn as H  # noqa: E402
import hil_c1_soak as S  # noqa: E402


def test_selftest():
    H.selftest()


def reply(state=1, whole=4000, late=0, holds=0):
    """CC pairs as the FM-1's query 127 + 30 replies (S.decode's input)"""
    w = whole // 4
    return [(107, w & 127), (108, w >> 7), (116, 0), (117, 50), (100, 30), (101, 0), (102, state), (100, 44), (101, 0),
            (102, late), (100, 45), (101, 0), (102, holds), (100, 48), (101, 31), (102, 32), (100, 49), (101, 2),
            (102, 0)]                                                             # (FR_FAULT: work 4000, idle 256)


@pytest.mark.parametrize("fail_at,expect_steps,why", [(None, 3, None), (130.0, 3, "core 1"), (70.0, 2, "crash")])
def test_sweep_over_fake_run(tmp_path, monkeypatch, fail_at, expect_steps, why):
    clock = [0.0]
    monkeypatch.setattr(H.time, "monotonic", lambda: clock[0])
    sent = []

    class Out:
        def send(self, m):
            sent.append((clock[0], m.type, getattr(m, "control", None), getattr(m, "value", None)))

    import mido
    monkeypatch.setattr(S, "ports", lambda a: (mido, Out(), types.SimpleNamespace(iter_pending=lambda: iter(()))))

    def fake_run(a):
        mido_, out, inp = S.ports(a)
        reads = []
        out.send(mido_.Message("control_change", control=116, value=32))
        S.decode([(100, 86), (101, 0), (102, 4)])                                 # (the boot before the run)
        try:
            while clock[0] < a.minutes * 60:
                out.send(mido_.Message("note_on", note=60, velocity=100))
                if clock[0] % 5 == 0:
                    bad = fail_at is not None and clock[0] >= fail_at
                    if bad and why == "core 1":
                        r = S.decode(reply(state=3, late=1, holds=1))
                    elif bad and why == "crash" and clock[0] == fail_at:
                        r = S.decode(reply())
                        r["boot"] = S.decode([(100, 86), (101, 0), (102, 4)]).get("boot")
                    else:
                        r = S.decode(reply())
                    r["t"] = clock[0]
                    reads.append(r)
                clock[0] += 1.0
        except KeyboardInterrupt:
            pass
        S.decode([])                                                              # (the fault-record query)
        os.makedirs(a.out, exist_ok=True)
        json.dump(dict(reads=reads), open(os.path.join(a.out, f"{a.row}-report.json"), "w"))
        return 2

    monkeypatch.setattr(S, "run", fake_run)
    rc = H.main(["--steps", "0:2", "--step-min", "1", "--core", "c1", "--kind", "flash", "--out", str(tmp_path)])
    rep = json.load(open(tmp_path / "burn-report.json"))
    assert [r["burn_us"] for r in rep["steps"]] == [0, 100, 200][:expect_steps]
    burns = [(t, v) for t, ty, c, v in sent if ty == "control_change" and c == 118]
    assert burns[0][1] == 0 and (60.0, 64 | 1) in burns and burns[-1][1] == 0      # (each step, then off at the end)
    if fail_at is None:
        assert rc == 0 and rep["first_bad"] is None
    else:
        assert rc == 1 and why in rep["first_bad"][1]
        assert max(t for t, ty, c, v in sent if ty == "note_on") < (int(fail_at // 60) + 1) * 60 + 1
    if why == "crash":
        assert rep["steps"][1]["crashes"] == 1 and rep["steps"][1]["crash_per_min"] == 1.0
    assert (tmp_path / "burn-report.md").exists()
    assert rep["steps"][0]["work_us"] == 4000 and rep["steps"][0]["idle_min_us"] == 256


def test_fault_code_only_in_diag_builds():
    """FR_FAULT (frodhes.c flt_burn, CC 117 / 118) is in no build the tools make: only APP_DEFS=-DFR_FAULT; never on
    the host"""
    src = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()
    import re
    guards = re.findall(r"^#.*FR_FAULT.*$", src, re.M)
    assert len(guards) >= 5 and all(g in ("#if defined(FR_FAULT) && FR_C1 && !defined(DINGHY_HOST)",
                                          "#if !defined(DINGHY_HOST) && !(defined(FR_FAULT) && FR_C1)") for g in guards), guards
    for f in ("tools/build.py", "build.sh", "Makefile", ".github/workflows/release.yml"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            assert "FR_FAULT" not in open(p).read(), f
    assert "cc == 117u" not in src.replace("if (cc == 117u) { flt_c0", "")      # (CC 117 / 118: the burn's alone)
    assert "cc == 118u" not in src.replace("if (cc == 118u) { flt_c1", "")


def test_no_burn_while_the_unit_boots_after_a_reset(tmp_path, monkeypatch):
    """2026-11-09: the burn rode on every send, the soak's CC 116 queries after a reboot too: re-applied before 30 s
    up, a crash there counts as a failed boot (RESCUE). Now: burn 0 at the reset, nothing while the unit boots and
    settles (the soak's queries, its note-offs), re-armed when the soak resumes (settle_s after the first answer)"""
    clock = [0.0]
    monkeypatch.setattr(H.time, "monotonic", lambda: clock[0])
    sent = []

    class Out:
        def send(self, m):
            sent.append((clock[0], m.type, getattr(m, "control", None), getattr(m, "value", None)))

    import mido
    monkeypatch.setattr(S, "ports", lambda a: (mido, Out(), types.SimpleNamespace(iter_pending=lambda: iter(()))))
    CRASH, UP, SETTLE = 50.0, 58.0, 35.0                  # (step 1 starts at 60 s: inside the boot window)

    def fake_run(a):                                     # (hil_c1_soak.run's order of sends and decodes)
        mido_, out, inp = S.ports(a)
        cc = lambda c, v: out.send(mido_.Message("control_change", control=c, value=v))   # noqa: E731
        reads = []
        cc(116, 32)
        S.decode([(100, 86), (101, 0), (102, 4)])
        try:
            while clock[0] < a.minutes * 60:
                out.send(mido_.Message("note_on", note=60, velocity=100))
                if clock[0] % 5 == 0:
                    cc(116, 127)
                    r = S.decode(reply())
                    r["t"] = clock[0]
                    if clock[0] == CRASH:                # the jobs counter dropped: the boot record, then the settle
                        cc(116, 32)
                        r["boot"] = S.decode([(100, 86), (101, 0), (102, 4)]).get("boot")
                        reads.append(r)
                        out.send(mido_.Message("note_off", note=60))
                        cc(64, 0)
                        cc(120, 0)
                        while clock[0] < UP:             # booting: the soak's queries go unanswered
                            cc(116, 30)
                            S.decode([])
                            clock[0] += 1.0
                        cc(116, 30)
                        S.decode(reply())                # up
                        up = clock[0]
                        while clock[0] - up < a.settle_s:
                            clock[0] += 0.5
                        cc(116, 30)
                        S.decode(reply())
                        reads.append(dict(t=clock[0], settled=clock[0] - CRASH))
                        cc(80, 0)                        # setup(): the stimulus resumes
                        continue
                    reads.append(r)
                clock[0] += 1.0
        except KeyboardInterrupt:
            pass
        S.decode([])
        os.makedirs(a.out, exist_ok=True)
        json.dump(dict(reads=reads), open(os.path.join(a.out, f"{a.row}-report.json"), "w"))
        return 2

    monkeypatch.setattr(S, "run", fake_run)
    rc = H.main(["--steps", "0:2", "--step-min", "1", "--core", "c1", "--kind", "flash", "--settle-s", str(SETTLE),
                 "--past", "1", "--out", str(tmp_path)])
    burns = [(t, v) for t, ty, c, v in sent if ty == "control_change" and c == 118]
    assert (CRASH, 0) in burns                                                    # (burn 0 at the reset)
    during = [(t, v) for t, v in burns if CRASH < t < UP + SETTLE]
    assert during == [], during                                                   # (nothing while it boots)
    rearm = [(t, v) for t, v in burns if t >= UP + SETTLE]
    assert rearm and rearm[0] == (UP + SETTLE, 64 | 1), rearm                     # (re-armed with the step's burn)
    t_resume = next(t for t, ty, c, v in sent if ty == "control_change" and c == 80 and t > CRASH)
    assert rearm[0][0] == t_resume
    rep = json.load(open(tmp_path / "burn-report.json"))
    assert rc == 1 and "crash" in rep["first_bad"][1] and rep["steps"][0]["crashes"] == 1
