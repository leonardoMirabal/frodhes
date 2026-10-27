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

import pytest

pytest.importorskip("websockets")
from websockets.sync.server import serve  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_ws  # noqa: E402

NAMES = ["F%02d" % (i + 1) for i in range(19)] + ["U%02d" % (i + 1) for i in range(16)]


class FakeSchwung:
    """slot 0 holds `module` at preset 0; every message received is logged; mute: answer nothing"""

    def __init__(self, module="frodhes", mute=False, refuse=False):
        self.module, self.mute, self.refuse = module, mute, refuse
        self.preset, self.log = 0, []
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
                if m["key"] == "synth:preset":
                    self.preset = int(m["value"])
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
