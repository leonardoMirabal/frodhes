# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_ws.py against a fake Schwung remote-UI server (the message shapes of schwung-manager remote_ui.go):
subscribe gives the slot's module and preset, set_param of synth:preset loads one and is reported back, an error
message is raised, a silent server times out, the CLI prints the state; tools/move_hil.py d2 checks the slot's module
and sets each engine's preset through it."""
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np

import pytest

pytest.importorskip("websockets")
from websockets.sync.server import serve  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_ws  # noqa: E402

NAMES = ["F%02d" % (i + 1) for i in range(19)] + ["U%02d" % (i + 1) for i in range(16)]


class FakeSchwung:
    """slot 0 holds `module` at preset 0; every message received is logged; mute: answer nothing"""

    def __init__(self, module="frodhes", mute=False, refuse=False, noname=False):
        self.module, self.mute, self.refuse, self.noname = module, mute, refuse, noname
        self.preset, self.log, self.takes, self.cur = 0, [], {}, None
        self.hist = []                                   # (the order of preset switches and takes: host_fetch's state)
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.url = "ws://127.0.0.1:%d/ws/remote-ui" % self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def params(self):
        return {"synth:preset": str(self.preset), "synth:preset_count": str(len(NAMES)),
                "synth:preset_name": NAMES[self.preset]}

    def handle(self, ws):
        for raw in ws:
            m = json.loads(raw)
            self.log.append(m)
            if self.mute:
                continue
            s = m.get("slot", 0)
            if m["type"] == "subscribe":
                ws.send(json.dumps({"type": "slot_info", "slot": s, "synth": self.module if s == 0 else "",
                                    "fx1": "", "fx2": "", "midi_fx1": "", "fx": [], "midi_fx": []}))
                ws.send(json.dumps({"type": "param_update", "slot": s,
                                    "params": {"slot:receive_channel": "1", "slot:forward_channel": "-1"}}))
                if s == 0 and self.module:
                    ws.send(json.dumps({"type": "hierarchy", "slot": 0, "component": "synth", "data": {}}))
                    ws.send(json.dumps({"type": "param_update", "slot": 0, "params": self.params()}))
            elif m["type"] == "set_param":
                if self.refuse:
                    ws.send(json.dumps({"type": "error", "message": "set_param failed: busy"}))
                    continue
                if m["key"] == "synth:dev_play":         # (the plugin's player, as move_bridge.h parses it)
                    v = m["value"].split()
                    if v[0] == "begin":
                        self.cur = v[1]
                        self.takes[self.cur] = {"dur": int(v[2]), "ev": [], "go": False, "max_piece": 0}
                    elif v[0] == "go":
                        self.takes[self.cur]["go"] = True
                        self.hist.append(("take", self.cur))
                elif m["key"] == "synth:dev_play_add":
                    t = self.takes[self.cur]
                    t["max_piece"] = max(t["max_piece"], len(m["value"]))
                    for e in m["value"].split(";"):
                        if e.strip():
                            t["ev"].append(tuple(int(x, 0) for x in e.split()))
                if m["key"] == "synth:preset":
                    self.preset = int(m["value"])
                    self.hist.append(("pc", self.preset))
                    # (as the Move: the notify ring's index first, the manager's full re-read 50 ms later;
                    # noname: the re-read never comes)
                    ws.send(json.dumps({"type": "param_update", "slot": s, "params": {"synth:preset": str(self.preset)}}))
                    if not self.noname:
                        time.sleep(0.05)
                        ws.send(json.dumps({"type": "param_update", "slot": s, "params": {"synth:tone": "64"}}))
                        ws.send(json.dumps({"type": "param_update", "slot": s, "params": self.params()}))

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    f = FakeSchwung()
    yield f
    f.close()


def test_subscribe_and_preset(fake):
    with move_ws.Remote(fake.url, timeout=3) as r:
        st = r.subscribe(0)
        assert st["module"] == "frodhes" and st["preset"] == 0 and st["preset_name"] == "F01", st
        assert st["receive_channel"] == "1"
        st = r.set_preset(0, 12)
        assert st["preset"] == 12 and st["preset_name"] == "F13", st
        assert r.params[0]["synth:tone"] == "64"
    assert fake.log[0] == {"type": "subscribe", "slot": 0}
    assert fake.log[1] == {"type": "set_param", "slot": 0, "key": "synth:preset", "value": "12"}


def test_preset_name_comes_late():
    """the index alone, no re-read with the name: the name is not the old preset's; a second subscribe brings it"""
    f = FakeSchwung(noname=True)
    try:
        with move_ws.Remote(f.url, timeout=3) as r:
            assert r.subscribe(0)["preset_name"] == "F01"
            st = r.set_preset(0, 14)
            assert st["preset"] == 14 and st["preset_name"] == "F15", st
            assert [m["type"] for m in f.log] == ["subscribe", "set_param", "subscribe"], f.log
    finally:
        f.close()


def test_empty_slot_and_errors():
    f = FakeSchwung(module="")
    try:
        with move_ws.Remote(f.url, timeout=3) as r:
            assert r.subscribe(0)["module"] == ""
    finally:
        f.close()
    f = FakeSchwung(refuse=True)
    try:
        with move_ws.Remote(f.url, timeout=3) as r:
            r.subscribe(0)
            with pytest.raises(move_ws.SchwungError, match="busy"):
                r.set_preset(0, 3)
    finally:
        f.close()
    f = FakeSchwung(mute=True)
    try:
        with move_ws.Remote(f.url, timeout=0.5) as r:
            with pytest.raises(TimeoutError):
                r.subscribe(0)
    finally:
        f.close()


def test_cli(fake):
    tool = os.path.join(ROOT, "tools", "move_ws.py")
    p = subprocess.run([sys.executable, tool, "preset", "14", "--url", fake.url], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["preset_name"] == "F15"
    p = subprocess.run([sys.executable, tool, "info", "--url", "ws://127.0.0.1:9/ws/remote-ui", "--timeout", "1"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "schwung" in p.stdout


def test_d2_sets_the_presets(fake, tmp_path):
    import move_hil as mh
    r = mh.FakeEchoRig()
    code = mh.d2(r, str(tmp_path), ("rhodes",), ws=fake.url, stress_secs=8.0, out=lambda s: None)
    assert fake.preset == mh.D2_PRESET["rhodes"]
    rows = json.load(open(tmp_path / "report.json"))["rows"]
    assert rows[0]["test"] == "ws" and rows[0]["ok"] and "frodhes" in rows[0]["numbers"], rows[0]
    assert code == 0, [(w["test"], w["verdict"]) for w in rows if not w["ok"]]
    bad = FakeSchwung(module="braids")
    try:
        code = mh.d2(mh.FakeEchoRig(), str(tmp_path / "b"), ("rhodes",), ws=bad.url, out=lambda s: None)
    finally:
        bad.close()
    assert code == 2 and "braids" in open(tmp_path / "b" / "summary.md").read()


def host_fetch(fake, d):
    """the tap a plugin would write, from the host build: the schedule the fake server received, rendered from boot
    (the plugin's take starts on its grid with the same block phase, so the host's ms 0 is the take's start). The
    plugin keeps its state between takes: the preset switches and the CCs and program changes of the earlier takes
    play first, in their order and 48 a ms as the plugin's queue takes them, one grid (2560 ms: the same block
    phase) before the take, which is cut from there"""
    sys.path.insert(0, os.path.join(ROOT, "tests", "py"))
    import hostrun
    import wave

    def fetch(take):
        t = fake.takes.get(take)
        if not t or not t["go"]:
            return None
        lead = []
        for kind, v in fake.hist:
            if kind == "pc":
                lead.append((0xC0, v, 0))
            elif v == take:
                break
            else:
                lead += [e[1:] for e in sorted(fake.takes[v]["ev"]) if e[1] & 0xF0 in (0xB0, 0xC0)]
        pre = 2560 if lead else 0
        lines = ["%d midi 0x%02X %d %d" % ((1 + i // 48,) + e) for i, e in enumerate(lead)]
        lines += ["%d midi 0x%02X %d %d" % ((e[0] + pre,) + e[1:]) for e in t["ev"]]
        run = hostrun.run("tap-" + take, lines, t["dur"] + pre + 20)
        n, f0 = t["dur"] * 441 // 10, pre * 441 // 10
        x = np.stack([run.L[f0:f0 + n], run.R[f0:f0 + n]], 1)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, take + ".wav")
        w = wave.open(path, "wb")
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(np.asarray(x, "<i2").tobytes())
        w.close()
        return path, {"id": take, "frames": n, "dropped": 0, "late": 0, "bad": 0, "aborted": 0}
    return fetch


def test_tap_path_is_the_host(fake, tmp_path):
    """usb-host over the tap path: the schedule's pieces stay under 256 B, the panel and the program change lead the
    take as in host_phrase, so the 'device' (the host build on the received schedule) equals the host's own render"""
    import move_hil as mh
    r = mh.TapRig(fake.url, host_fetch(fake, str(tmp_path / "tap")), wait=2.0)
    mh.TAP_GRID_S, keep = 0.0, mh.TAP_GRID_S
    sleep, mh.time.sleep = mh.time.sleep, lambda s: None
    try:
        ok, rep = mh.usb_host(r, str(tmp_path), program=12, out=lambda s: None)
        assert ok and abs(rep["gain_db"]) < 0.01, rep
        assert all(w["lag_ms"] == 0 and w["residual_db"] < -150 for w in rep["rows"] if w["judged"]), rep["rows"]
        take = fake.takes[sorted(fake.takes)[-1]]
        assert take["max_piece"] < 256 and (5, 0xC0, 12, 0) in take["ev"], take["ev"][:4]
        code = mh.d2(r, str(tmp_path / "d2"), ("rhodes",), ws=fake.url, stress_secs=4.0, out=lambda s: None)
        rows = json.load(open(tmp_path / "d2" / "report.json"))["rows"]
        assert code == 0 and "preflight" not in [w["test"] for w in rows], [(w["test"], w["verdict"]) for w in rows]
    finally:
        mh.time.sleep, mh.TAP_GRID_S = sleep, keep
        r.remote.close()
