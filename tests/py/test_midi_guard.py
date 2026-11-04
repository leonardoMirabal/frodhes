# SPDX-License-Identifier: GPL-3.0-only
"""tools/midi_guard.py and its use in the device tools, on the host with a fake MIDI backend (no FM-1, no CoreMIDI,
no audio): a MIDI open / close that hangs (with the GIL held, as python-rtmidi does in CoreMIDI) ends the process in
seconds with HANG_EXIT and the restart line; the exit skips destructors after the atexit handlers; T1 opens its ports
before any audio stream, keeps them for the whole run (S6 too), opens no second pair inside stress_max, and closes
them (an install) only after its streams are stopped and its pollers joined."""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_must as m  # noqa: E402
import midi_guard as g  # noqa: E402
import test_hil_must as T  # noqa: E402  (its World: the computer's audio + MIDI as fakes)

FAKE = textwrap.dedent('''
    import ctypes, sys, time
    sys.path.insert(0, {tools!r})
    import hil_must

    def gil_hang(s=30):                    # a C call that blocks holding the GIL (rtmidi in CoreMIDI)
        ctypes.PyDLL(None).sleep(s)

    class Msg:
        def __init__(self, type, **kw):
            self.type, self.channel = type, 0
            self.__dict__.update(kw)

        def copy(self, **kw):
            n = Msg.__new__(Msg)
            n.__dict__.update(self.__dict__, **kw)
            return n

    class Port:
        name = "Frodhes MIDI"

        def __init__(self, hang_close):
            self.hang_close = hang_close

        def send(self, msg):
            pass

        def iter_pending(self):
            return iter(())

        def close(self):
            if self.hang_close:
                print("closing", flush=True)
                gil_hang()

    class Mido:
        Message = Msg

        def __init__(self, hang):
            self.hang = hang

        def get_output_names(self):
            return ["Frodhes MIDI"]

        get_input_names = get_output_names

        def open_output(self, name):
            return Port(False)

        def open_input(self, name):
            if self.hang == "open":
                print("opening", flush=True)
                gil_hang()
            if self.hang == "open-sleep":   # (a hang that releases the GIL)
                print("opening", flush=True)
                time.sleep(30)
            return Port(self.hang == "close")

    dev = hil_must.Dev(mido=Mido(sys.argv[1]))
    print("opened", flush=True)
    dev.close()
    print("closed", flush=True)
''')


def run_fake(tmp_path, hang, timeout="1"):
    p = tmp_path / "fake.py"
    p.write_text(FAKE.format(tools=os.path.join(ROOT, "tools")))
    env = dict(os.environ, FRODHES_MIDI_TIMEOUT=timeout)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(p), hang], capture_output=True, text=True, timeout=60, env=env)
    return r, time.monotonic() - t0


@pytest.mark.parametrize("hang,before", [("open", "opening"), ("open-sleep", "opening"), ("close", "closing")])
def test_a_hanging_open_or_close_exits_in_seconds(tmp_path, hang, before):
    r, secs = run_fake(tmp_path, hang)
    assert r.returncode == g.HANG_EXIT, (r.returncode, r.stdout, r.stderr)
    assert secs < 10, secs                                  # (the fake hangs 30 s; the watchdog fires at 1 s)
    out = r.stdout
    assert g.HANG_MSG in out and "killall MIDIServer" in out, out
    assert out.index(before) < out.index(g.HANG_MSG)        # (printed before the hang: flushed, not lost)
    assert ("closed" in out) is False
    if hang == "close":
        assert "opened" in out


def test_no_hang_no_exit(tmp_path):
    """open and close return: the timers cancelled, nothing fires after them"""
    p = tmp_path / "ok.py"
    p.write_text(FAKE.format(tools=os.path.join(ROOT, "tools")).replace('print("closed", flush=True)',
                                                                         'print("closed", flush=True)\n'
                                                                         'time.sleep(1.5)\nprint("still here")'))
    r = subprocess.run([sys.executable, str(p), "none"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, FRODHES_MIDI_TIMEOUT="0.5"))
    assert r.returncode == 0 and "still here" in r.stdout and g.HANG_MSG not in r.stdout, r.stdout + r.stderr


def test_exit_runs_atexit_and_skips_destructors(tmp_path):
    p = tmp_path / "ex.py"
    p.write_text(textwrap.dedent(f'''
        import atexit, sys, time
        sys.path.insert(0, {os.path.join(ROOT, "tools")!r})
        import midi_guard

        class Port:                        # an rtmidi port whose destructor hangs
            def __del__(self):
                print("destructor", flush=True)
                time.sleep(30)

        keep = Port()
        atexit.register(lambda: print("streams stopped"))

        def main():
            print("partial", end="")       # (no newline: flushed by the exit)
            return 3
        midi_guard.run(main)
    '''))
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and time.monotonic() - t0 < 10
    assert r.stdout.startswith("partial") and "streams stopped" in r.stdout and "destructor" not in r.stdout


def test_exit_code_of_a_system_exit_message(tmp_path):
    p = tmp_path / "se.py"
    p.write_text(f'import sys\nsys.path.insert(0, {os.path.join(ROOT, "tools")!r})\nimport midi_guard\n'
                 'def main():\n    raise SystemExit("no FM-1 MIDI port")\nmidi_guard.run(main)\n')
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "no FM-1 MIDI port" in r.stderr


def test_the_tools_exit_through_midi_guard():
    """every tool that opens the FM-1's MIDI ports ends the process through midi_guard (no rtmidi teardown) and
    opens them through it (the watchdog)"""
    for f in ("hil_must.py", "hil_scan.py", "hil_crackle.py", "hil_monkey.py", "hil_cpu.py"):
        src = open(os.path.join(ROOT, "tools", f)).read()
        main = src[src.rindex('if __name__ == "__main__":'):]
        assert "midi_guard.run(" in main and "sys.exit(" not in main, f
        assert "mido.open_input(" not in src and "mido.open_output(" not in src, f


# ---- the order: ports first, audio second; close after the streams stop and the pollers join ----------------------

class Log:
    def __init__(self, w):
        self.ev, self.w = [], w
        start, close = T.FakeStream.start, T.FakeStream.close
        log = self.ev

        def st(s):
            log.append("stream start")
            s.live = True
            start(s)

        def cl(s):
            log.append("stream stop")
            s.live = False
            close(s)
        self.patch = [(T.FakeStream, "start", st), (T.FakeStream, "close", cl)]

    def factory(self):
        F, log = self.w.dev_factory(), self.ev

        class LDev(F):
            def __init__(s):
                F.__init__(s)
                log.append("midi open")

            def close(s):
                log.append("midi close")
        return LDev


def logged_must(monkeypatch, tmp_path, w, *extra, installer=None):
    lg = Log(w)
    for obj, k, v in lg.patch:
        monkeypatch.setattr(obj, k, v)
    monkeypatch.setattr(m, "time", T._T())
    monkeypatch.setattr(m, "OUT", str(tmp_path))
    a = T.args("--jack-dev", "none", *extra)
    mu = m.Must(a, sd=w.sd(), dev_factory=lg.factory(), installer=installer, info=lambda: "fake FM-1")
    return mu, lg.ev


def no_midi_while_streaming(ev):
    live = 0
    for e in ev:
        live += (e == "stream start") - (e == "stream stop")
        assert not (live and e.startswith("midi")), ev


def test_t1_ports_first_and_stress_max_shares_them(monkeypatch, tmp_path):
    w = T.World()
    mu, ev = logged_must(monkeypatch, tmp_path, w)
    assert ev == ["midi open"]                          # (Must: the ports before any stream)
    seen = []

    def fake_stress_max(r, rep, rate=48000, seconds=None, quick=False, tier=None, engines=None, dev=None):
        seen.append((r, dev))
        return True, "ok"
    mu.stress_max(fake_stress_max, 48000)
    assert seen == [(None, mu.dev)]                     # our ports, no hs.Rig (no second pair under the stream)
    assert ev.count("midi open") == 1 and "stream start" in ev
    no_midi_while_streaming(ev)


def test_t1_keeps_the_ports_through_s6(monkeypatch, tmp_path):
    w = T.World()
    mu, ev = logged_must(monkeypatch, tmp_path, w, "--scenarios", "S6,S2")
    mu.s6 = lambda: ev.append("S6")
    mu.s2 = lambda: ev.append("S2")
    mu.run()
    assert ev == ["midi open", "S6", "S2"]              # no close / reopen per scenario


def test_install_closes_after_the_streams_stop(monkeypatch, tmp_path):
    w = T.World()

    def inst(pkg):
        w.reenumerate(gone=1, midi_down=0)
        return 0, "ok"
    mu, ev = logged_must(monkeypatch, tmp_path, w, "--fwsc", "new.fwsc", installer=inst)
    mu.open_usb(48000, 0.1)                             # a stream an error left open
    mu.install("new.fwsc")
    assert ev == ["midi open", "stream start", "stream stop", "midi close", "midi open"], ev


# ---- Dev: close after the pollers' join; one query at a time, every reply to the running poller --------------------

class FMido:
    """mido over a fake FM-1 that answers CC 116 with an engine time (n-th reply: n * 4 us)"""

    class Message:
        def __init__(self, type, **kw):
            self.type, self.channel = type, 0
            self.__dict__.update(kw)

        def copy(self, **kw):
            n = FMido.Message.__new__(FMido.Message)
            n.__dict__.update(self.__dict__, **kw)
            return n

    def __init__(self):
        self.q, self.n, self.closed = [], 0, []
        me = self

        class Out:
            name = "Frodhes MIDI"

            def send(s, msg):
                if msg.type == "control_change" and msg.control == 116:
                    me.n += 1
                    me.q += [FMido.Message("control_change", control=c, value=v) for c, v in
                             ((116, 0), (117, me.n), (115, 0))]

            def close(s):
                me.closed.append(("out", [t.is_alive() for t in threading.enumerate() if t.name == "poller"]))

        class In:
            name = "Frodhes MIDI"

            def iter_pending(s):
                while me.q:
                    yield me.q.pop(0)

            def close(s):
                me.closed.append(("in", [t.is_alive() for t in threading.enumerate() if t.name == "poller"]))
        self.Out, self.In = Out, In

    def get_output_names(self):
        return ["Frodhes MIDI"]

    get_input_names = get_output_names

    def open_output(self, name):
        return self.Out()

    def open_input(self, name):
        return self.In()


def test_dev_close_waits_for_the_poller(monkeypatch):
    monkeypatch.setattr(m, "POLL_S", 0.05)
    monkeypatch.setattr(m, "JOIN_S", 0.2)
    fm = FMido()
    d = m.Dev(mido=fm)
    p = m.Poller(d)
    p.th.name = "poller"
    p.__enter__()
    time.sleep(0.2)
    d.close()                                           # the poller still running: the ports stay open
    assert fm.closed == [] and d.inp is not None
    p.__exit__(None, None, None)
    d.close()
    assert [k for k, _ in fm.closed] == ["in", "out"] and not any(a for _, al in fm.closed for a in al)


def test_query_feeds_the_running_poller(monkeypatch):
    """a second reader's query (hil_crackle's check_rate inside T1's poller) does not take a reply from the Acc"""
    monkeypatch.setattr(m, "POLL_S", 0.3)
    fm = FMido()
    d = m.Dev(mido=fm)
    with m.Poller(d) as acc:
        for _ in range(3):
            d.query(0.02)                               # (main thread, while the poller thread queries)
    n0 = fm.n
    assert acc.replies == n0 - 1                        # every reply but __enter__'s restart query
    assert acc.engine_us == n0 * 4
    assert d.taps == []
    d.close()
