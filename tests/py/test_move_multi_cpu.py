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


# ---- pacing and the slots restored (K2: the 0.9.9 run of 2026-11-08, build/move/hil/multi/20261108-003510: the third
# slot's upload hit "web param set ring full", the others broke on the barrier, Frodhes stayed in slots 3-4)
RING = mc.move_ws.SchwungError("set_param failed: web param set ring full")


class FakeSchwung:
    """Schwung's WebSocket for the slots: module writes take effect when the next read comes (as slot_info does);
    fail: errors the next reads raise in turn (the manager's answer to a refused write), one a read"""
    def __init__(self, mods, fail=()):
        self.mods, self.fail, self.writes, self.info, self.params = dict(mods), list(fail), [], {}, {}
        self.pending = {}

    def __call__(self):                          # (Slots' remote factory)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _read(self):
        if self.fail:
            e = self.fail.pop(0)
            if e is not None:
                self.pending.clear()             # (the refused write is lost)
                raise e
        self.mods.update(self.pending)
        self.pending.clear()
        for s, m in self.mods.items():
            self.info[s] = {"synth": m}

    def subscribe(self, s):
        self._read()

    def set_param(self, s, key, value):
        self.writes.append((s, key, value))
        if key == "synth:module":
            self.pending[s] = value

    def until(self, done, timeout=None):
        self._read()
        if not done(self):
            raise TimeoutError("no answer")

    def pump(self, secs):
        self._read()


def test_paced_retries_ring_full_and_raises_the_rest():
    calls, slept = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RING
        return "ok"
    assert mc.paced(fn, sleep=slept.append) == "ok" and slept == list(mc.RING_BACKOFF_S[:2])
    calls.clear()
    try:
        mc.paced(lambda: (_ for _ in ()).throw(mc.move_ws.SchwungError("unknown key")), sleep=slept.append)
        raise AssertionError("not raised")
    except mc.move_ws.SchwungError as e:
        assert "unknown key" in str(e)
    try:
        mc.paced(lambda: (_ for _ in ()).throw(RING), sleep=lambda s: None, backoff=(0.1,))
        raise AssertionError("not raised")
    except mc.move_ws.SchwungError as e:
        assert mc.ring_full(e)


def test_set_module_waits_for_slot_info_and_paces_ring_full():
    w = FakeSchwung({0: "frodhes", 1: "", 2: "", 3: ""}, fail=[None, RING])   # (subscribe ok, the write refused)
    log = []
    assert mc.set_module(w, 2, "frodhes", sleep=lambda s: None, log=log)
    assert w.mods[2] == "frodhes" and len([x for x in w.writes if x[0] == 2]) == 2 and len(log) == 1, (w.writes, log)
    n = len(w.writes)
    assert mc.set_module(w, 2, "frodhes", sleep=lambda s: None) and len(w.writes) == n   # (already there: no write)


def test_slots_restored_after_a_crash():
    """a run that loads Frodhes into slots 3-4 and then crashes: the modules come back as they were (slot 2's other
    module untouched), and slot 1's Frodhes gets its parameters back"""
    w = FakeSchwung({0: "frodhes", 1: "other", 2: "", 3: ""})
    restored = []
    try:
        with mc.Slots("ws://x", [0, 2, 3], remote=w, state_read=lambda url, s: {"preset": "3"},
                      state_restore=lambda url, s, st: restored.append((s, st)) or (["preset"], {}),
                      sleep=lambda s: None) as ks:
            for s in (2, 3):
                mc.set_module(w, s, "frodhes", sleep=lambda s: None)
            raise RuntimeError("BrokenBarrierError")
    except RuntimeError:
        pass
    assert w.mods == {0: "frodhes", 1: "other", 2: "", 3: ""}, w.mods
    assert ks.report["verdict"] == "PASS" and restored == [(0, {"preset": "3"})], ks.report


def test_slots_restore_says_when_it_cannot():
    w = FakeSchwung({0: "frodhes", 1: "", 2: "", 3: ""})
    ks = mc.Slots("ws://x", [0, 1], remote=w, state_read=lambda url, s: {},
                  state_restore=lambda url, s, st: ([], {"p_tone": ("1", "2")}), sleep=lambda s: None)
    with ks:
        mc.set_module(w, 1, "frodhes", sleep=lambda s: None)
        w.fail = [None] + [RING] * 10                 # (the manager's ring stays full: the empty write never lands)
    assert ks.report["verdict"].startswith("FAIL") and "not restored" in ks.report["verdict"], ks.report


def test_paced_play_uploads_again_on_ring_full():
    """a take's upload that hits the ring is uploaded again whole ("begin" first), and "go" comes after the barrier"""
    import threading

    class R(FakeSchwung):
        pass
    r = R({0: "frodhes"}, fail=[RING])               # (the first upload's closing pump: refused)
    up, bar = threading.Lock(), threading.Barrier(1)
    log = []
    mc.paced_play(r, up, bar, sleep=lambda s: None, log=log)
    r.play(0, "t1", 1000, [(10, 0x90, 60, 100), (500, 0x80, 60, 0)])
    begins = [x for x in r.writes if x[1].endswith(":dev_play") and str(x[2]).startswith("begin")]
    gos = [x for x in r.writes if x[1].endswith(":dev_play") and str(x[2]).startswith("go")]
    assert len(begins) == 2 and len(gos) == 1 and r.writes[-1] == gos[0] and len(log) == 1, r.writes


def test_paced_play_aborts_the_barrier_when_the_upload_fails():
    import threading
    r = FakeSchwung({0: "frodhes"}, fail=[mc.move_ws.SchwungError("dev_play refused")])
    bar = threading.Barrier(2)
    mc.paced_play(r, threading.Lock(), bar, sleep=lambda s: None)
    try:
        r.play(0, "t1", 1000, [(10, 0x90, 60, 100)])
        raise AssertionError("not raised")
    except mc.move_ws.SchwungError:
        pass
    assert bar.broken
