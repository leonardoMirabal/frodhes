# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_multi_cpu.py without the Move: Schwung's debug_log_on made for a run and taken away after it, a flag
the user had before kept (Schwung never trims debug.log: 43 MB on 2026-10-30 after two days on)"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_multi_cpu as mc  # noqa: E402


class FakeMove:
    """a shell on the Move that knows one file, the flag"""
    def __init__(self, flag):
        self.flag, self.cmds = flag, []

    def __call__(self, cmd):
        self.cmds.append(cmd)
        if cmd.startswith("[ -f %s ]" % mc.LOG_FLAG):
            if self.flag:
                return b"kept\n"
            self.flag = True
            return b"made\n"
        if cmd == "rm -f %s" % mc.LOG_FLAG:
            self.flag = False
            return b""
        raise AssertionError(cmd)


def test_log_flag_made_for_the_run_and_taken_away():
    m = FakeMove(False)
    made = mc.log_on(m)
    assert made and m.flag
    mc.log_off(made, m)
    assert not m.flag and len(m.cmds) == 2


def test_log_flag_set_before_is_kept():
    m = FakeMove(True)
    made = mc.log_on(m)
    assert not made and m.flag
    mc.log_off(made, m)
    assert m.flag and len(m.cmds) == 1


def test_log_on_runs_in_a_posix_shell(tmp_path):
    """the command itself, in sh: makes the file once, says kept the second time"""
    flag = str(tmp_path / "debug_log_on")
    old = mc.LOG_FLAG
    mc.LOG_FLAG = flag
    try:
        def sh(cmd):
            return subprocess.run(["sh", "-c", cmd], capture_output=True).stdout
        assert mc.log_on(sh) and os.path.exists(flag)
        assert not mc.log_on(sh)
        mc.log_off(True, sh)
        assert not os.path.exists(flag)
    finally:
        mc.LOG_FLAG = old


def _run(worst, n, **kw):
    r = {"rings": {str(k): {"worst": w, "missing": 0} for k, w in enumerate(worst)}, "perf": {"overruns": 0},
         "takes": [{"gated": 0} for _ in worst]}
    r.update(kw)
    return mc.verdict(r, n)


def test_verdict_bars():
    """one instance within U12 (725 us), two or three each within ~967 us, four reported only"""
    assert _run([573], 1)[0] == "PASS"
    assert _run([700], 1)[0] == "PASS" and "explain" in _run([700], 1)[1]   # +22 % re 0.9.9: a regression to explain
    assert _run([726], 1)[0] == "FAIL"
    assert _run([900, 960, 940], 3)[0] == "PASS"
    assert _run([900, 970, 940], 3)[0] == "FAIL"
    assert _run([1500] * 4, 4)[0] == "INFO"
    assert _run([600, 600], 3)[0] == "FAIL"                                  # an instance's ring missing
    assert _run([600], 1, perf={"overruns": 2})[0] == "FAIL"
    assert _run([600], 1, takes=[{"gated": 1}])[0] == "FAIL"
