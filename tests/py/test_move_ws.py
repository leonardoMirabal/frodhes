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

import websockets  # noqa: E402,F401  (a hard import: a runner without it fails here, never skips the tap tests)
from websockets.sync.server import serve  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_ws  # noqa: E402

NAMES = ["F%02d" % (i + 1) for i in range(19)] + ["U%02d" % (i + 1) for i in range(16)]


def plugin_params():
    """the plugin's params at their defaults (chain_params.json) and the CC each panel key follows (move_ui.h)"""
    import re
    ps = json.load(open(os.path.join(ROOT, "move", "module", "chain_params.json")))
    dflt = {p["key"]: str(p.get("default", 0)) for p in ps}
    ui = open(os.path.join(ROOT, "move", "module", "move_ui.h")).read()
    cc = {}
    for k, c in re.findall(r'\{"(\w+)", \w+, (\d+), MK_(?:PARAM|LATCH)', ui):
        if int(c):
            cc.setdefault(int(c), []).append(k)
    return dflt, cc


class FakeSchwung:
    """slot 0 holds `module` at preset 0; every message received is logged; mute: answer nothing"""

    def __init__(self, module="frodhes", mute=False, refuse=False, noname=False, settings=None, cut_on=None,
                 full=False):
        self.module, self.mute, self.refuse, self.noname = module, mute, refuse, noname
        self.cut_on = cut_on                             # ("go" / "add": cut the connection once, after the first)
        self.settings = dict(settings or {})            # ("s_..." -> value: the settings keys the server reports)
        self.full, self.ccmap, self.dflt = full, {}, {}  # (full: every param, set_param and a take's CCs change them,
        if full:                                         # CC 120 resets the panel, as the plugin does)
            self.dflt, self.ccmap = plugin_params()
            self.settings = dict(self.dflt, **self.settings)
        self.preset, self.log, self.takes, self.cur = 0, [], {}, None
        self.cc_drop = True                              # (the bridge drops the sequencer's and the arpeggiator's
                                                         # and the chords' CCs, move_bridge.h MB_CC_DROP; False:
                                                         # a build before)
        self.cut_sets = []                               # ([n, ...]: after n more set_params, the socket cut without a
        #                                                  close frame, once per entry: a drop in the middle of a restore)
        self.hist, self.conns = [], []                                   # (the order of preset switches and takes: host_fetch's state)
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.url = "ws://127.0.0.1:%d/ws/remote-ui" % self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def params(self):
        return dict({"synth:preset": str(self.preset), "synth:preset_count": str(len(NAMES)),
                     "synth:preset_name": NAMES[self.preset]},
                    **{"synth:" + k: self.settings[k] for k in sorted(self.settings)})   # (by name, as the Move's
        #                                     order is not the panel's: p_dl_depth comes before p_dl_sw)

    def drop_all(self):
        """every connection cut without a close frame (as the manager did, 2026-10-28)"""
        for ws in self.conns:
            ws.socket.close()

    def handle(self, ws):
        self.conns.append(ws)
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
                if self.cut_sets:
                    self.cut_sets[0] -= 1
                    if self.cut_sets[0] <= 0:
                        self.cut_sets.pop(0)
                        ws.socket.close()
                        return
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
                        if self.full:
                            for _, st, d1, d2 in sorted(self.takes[self.cur]["ev"]):
                                if st & 0xF0 == 0xB0 and d1 == 120:
                                    self.settings.update({k: v for k, v in self.dflt.items() if k.startswith("p_")})
                                elif st & 0xF0 == 0xB0:
                                    for k in self.ccmap.get(d1, ()):
                                        self.settings[k] = str(d2)
                        self.hist.append(("take", self.cur))
                        if self.cut_on == "go":
                            self.cut_on = None
                            ws.socket.close()
                            return
                elif m["key"] == "synth:dev_play_add":
                    t = self.takes[self.cur]
                    t["max_piece"] = max(t["max_piece"], len(m["value"]))
                    v = m["value"]
                    if v.startswith("#"):                # (the piece's number, as the plugin checks it)
                        k, v = v[1:].split(" ", 1)
                        assert int(k) == t.setdefault("pieces", 0), (k, t["pieces"])
                        t["pieces"] += 1
                    for e in v.split(";"):
                        if e.strip():
                            t["ev"].append(tuple(int(x, 0) for x in e.split()))
                    if self.cut_on == "add":
                        self.cut_on = None
                        ws.socket.close()
                        return
                if m["key"] == "synth:p_hq":
                    self.hist.append(("hq", int(m["value"]) != 0))
                k = m["key"][6:] if m["key"].startswith("synth:") else None
                if self.full and k in self.settings:
                    if (k == "p_dl_sw" and self.settings["p_dl_depth"] == "0"
                            and str(m["value"]) != self.settings[k]):
                        self.settings["p_dl_depth"] = "50"   # (as the panel: a switch gives MIX at 0 a start)
                    self.settings[k] = str(m["value"])
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
    import move_hil as mh

    def keep(e):                                         # (MB_CC_DROP: CC 43-46, 52-63 never reach the app)
        return not (fake.cc_drop and e[-3] & 0xF0 == 0xB0 and (43 <= e[-2] <= 46 or 52 <= e[-2] <= 63))

    def fetch(take):
        t = fake.takes.get(take)
        if not t or not t["go"]:
            return None
        lead, hq = [], 1                                 # (the plugin loads with the HQ Rhodes on)
        for kind, v in fake.hist:
            if kind == "hq":
                hq = v
            elif kind == "pc":
                lead.append((0xC0, v, 0))
            elif v == take:
                break
            else:
                lead += [e[1:] for e in sorted(fake.takes[v]["ev"]) if e[1] & 0xF0 in (0xB0, 0xC0) and keep(e)]
        pre = 2560 if lead else 0
        lines = ["%d midi 0x%02X %d %d" % ((1 + i // 48,) + e) for i, e in enumerate(lead)]
        lines += ["%d midi 0x%02X %d %d" % ((e[0] + pre,) + e[1:]) for e in t["ev"] if keep(e)]
        run = hostrun.run("tap-" + take, lines, t["dur"] + pre + 20,
                          binary=mh.host_binary(hq))   # (the Move's host build; p_hq: HQ)
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


def fake_cpu(fake):
    """the timer ring a plugin would log: 2000 callbacks of 300..349 us when dev_cpu_log went to 1 since the last
    read, else none"""
    seen = [0]

    def cpu():
        new, seen[0] = fake.log[seen[0]:], len(fake.log)
        on = any(m.get("key") == "synth:dev_cpu_log" and m.get("value") == "1" for m in new)
        return "".join("%d %d 128\n" % (i, 300 + i % 50) for i in range(2000)) if on else ""
    return cpu


def test_tap_path_is_the_host(fake, tmp_path):
    """usb-host over the tap path: the schedule's pieces stay under 256 B, the panel and the program change lead the
    take as in host_phrase, so the 'device' (the host build on the received schedule) equals the host's own render"""
    import move_hil as mh
    r = mh.TapRig(fake.url, host_fetch(fake, str(tmp_path / "tap")), wait=2.0, cpu=fake_cpu(fake))
    mh.TAP_GRID_S, keep = 0.0, mh.TAP_GRID_S
    sleep, mh.time.sleep = mh.time.sleep, lambda s: None
    try:
        ok, rep = mh.usb_host(r, str(tmp_path), program=12, out=lambda s: None)
        assert ok and abs(rep["gain_db"]) < 0.01, rep
        assert all(w["lag_ms"] == 0 and w["residual_db"] < -150 for w in rep["rows"] if w["judged"]), rep["rows"]
        take = fake.takes[sorted(fake.takes)[-1]]
        assert take["max_piece"] < 256 and (5, 0xC0, 12, 0) in take["ev"], take["ev"][:4]
        code = mh.d2(r, str(tmp_path / "d2"), ("rhodes",), ws=fake.url, stress_secs=4.0, hq=1, out=lambda s: None)
        rows = json.load(open(tmp_path / "d2" / "report.json"))["rows"]
        assert code == 0 and "preflight" not in [w["test"] for w in rows], [(w["test"], w["verdict"]) for w in rows]
        assert {"type": "set_param", "slot": 0, "key": "synth:p_hq", "value": "1"} in fake.log
        cpu = [w for w in rows if w["test"] == "cpu"]
        assert len(cpu) == 1 and cpu[0]["report"]["worst"] == 349 and cpu[0]["report"]["n"] == 2000, cpu
        assert "2000 callbacks while usb-stress played, worst 349 us" in cpu[0]["numbers"] and "HQ on" in cpu[0]["numbers"]
        logs = [m["value"] for m in fake.log if m.get("key") == "synth:dev_cpu_log"]
        assert logs == ["1", "0", "1", "0", "0"], logs            # (usb-stress, then usb-restrike: Rhodes, HQ on;
        #                                                             the restore's own 0 at the end)
        rs = [w for w in rows if w["test"] == "usb-restrike"]
        assert len(rs) == 1 and rs[0]["ok"] and "re-strikes" in rs[0]["numbers"], rs
    finally:
        mh.time.sleep, mh.TAP_GRID_S = sleep, keep
        r.remote.close()


def _tap(fake, tmp_path):
    import move_hil as mh
    return mh, mh.TapRig(fake.url, host_fetch(fake, str(tmp_path / "tap")), wait=2.0)


def test_leftover_arp_struck_again(fake, tmp_path, monkeypatch):
    """the 2026-10-28 01:42 run (a build before MB_CC_DROP): the arpeggiator left on in the plugin (CC 52 1, 1/8: a
    saved setup) struck every held note of usb-host's phrase again on its steps; usb-host names it (STRUCK AGAIN).
    The Move's build drops the arpeggiator's CCs, so the same CCs leave the take as the host's, with no reset of them
    in GLOBAL_CCS; usb-host's own reset (GLOBAL_CCS, boot_keys) goes out too"""
    mh, r = _tap(fake, tmp_path)
    monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
    monkeypatch.setattr(mh.time, "sleep", lambda s: None)
    assert not any(43 <= c <= 46 or 52 <= c <= 63 for c, _ in mh.GLOBAL_CCS)
    try:
        fake.cc_drop = False
        r._send("cc", 52, 1)
        r._send("cc", 53, 1)
        r.rec(0.2, [])                                   # (a take that leaves the arpeggiator on)
        with monkeypatch.context() as m:
            m.setattr(r, "boot_settings", lambda: None)
            ok, rep = mh.usb_host(r, str(tmp_path / "left"), out=lambda s: None)
        assert not ok and rep["verdict"].startswith("STRUCK AGAIN"), rep["verdict"]
        assert sum(1 for w in rep["rows"] if w["again_ms"]) >= 5, [w["again_ms"] for w in rep["rows"]]
        fake.cc_drop = True
        with monkeypatch.context() as m:
            m.setattr(r, "boot_settings", lambda: None)
            ok, rep = mh.usb_host(r, str(tmp_path / "dropped"), out=lambda s: None)
        assert ok and not any(w["again_ms"] for w in rep["rows"]), rep["verdict"]
        ok, rep = mh.usb_host(r, str(tmp_path / "reset"), out=lambda s: None)
        assert ok and not any(w["again_ms"] for w in rep["rows"]), rep["verdict"]
        assert [m["key"] for m in fake.log if m.get("key", "").startswith("synth:s_")] == \
            ["synth:" + k for k, _ in mh.boot_keys()]
    finally:
        r.remote.close()


def test_hq_compares_hq(fake, tmp_path, monkeypatch):
    """--hq 1: the fake plugin renders HQ (host_fetch follows p_hq), and usb-host compares with the HQ host build;
    against Classic the same take differs"""
    mh, r = _tap(fake, tmp_path)
    monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
    monkeypatch.setattr(mh.time, "sleep", lambda s: None)
    try:
        r.set_hq(1)
        ok, rep = mh.usb_host(r, str(tmp_path / "hq"), out=lambda s: None)
        assert ok and rep["hq"] == 1 and abs(rep["gain_db"]) < 0.01, rep["verdict"]
        assert all(w["residual_db"] < -150 for w in rep["rows"] if w["judged"]), [w["residual_db"] for w in rep["rows"]]
        assert rep["hq_vs_classic_db"] > 20, rep["hq_vs_classic_db"]
        r.hq = 0                                         # (the reference Classic: the HQ take differs)
        ok, rep = mh.usb_host(r, str(tmp_path / "classic"), out=lambda s: None)
        assert not ok, rep["verdict"]
        r.hq = None                                      # (as the plugin loads: HQ, checked as HQ)
        ok, rep = mh.usb_host(r, str(tmp_path / "load"), out=lambda s: None)
        assert ok and rep["hq_vs_classic_db"] > 20, rep["verdict"]
        fake.hist.append(("hq", False))                  # (p_hq lost on the way: the plugin plays Classic)
        r.hq = 1
        ok, rep = mh.usb_host(r, str(tmp_path / "lost"), out=lambda s: None)
        assert not ok and rep["verdict"].startswith("CLASSIC"), rep["verdict"]
        fake.hist.append(("hq", True))
        ok, rep = mh.usb_host(r, str(tmp_path / "vibes"), program=12, out=lambda s: None)
        assert ok and rep["hq_vs_classic_db"] > 20, rep["verdict"]       # (VIBES: the HQ bars, probed the same way)
    finally:
        r.remote.close()


def test_boot_settings_only_what_differs():
    """the settings Schwung reports at their boot values are not written again; one left on is"""
    import move_hil as mh
    st = dict(mh.boot_keys())
    st["s_tune"] = "7"
    f = FakeSchwung(settings=st)
    try:
        r = mh.TapRig(f.url, lambda take: None, wait=0.1)
        assert r.boot_settings() == ["s_tune"]
        assert [m for m in f.log if m.get("key", "").startswith("synth:s_")] == \
            [{"type": "set_param", "slot": 0, "key": "synth:s_tune", "value": "0"}]
        r.remote.close()
    finally:
        f.close()


def test_tap_rig_reconnects(fake, tmp_path, monkeypatch):
    """the manager drops the rig's connection between two takes: the next take goes on a new one"""
    mh, r = _tap(fake, tmp_path)
    monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
    try:
        r.rec(0.1, [(0.02, "on", 60, 100), (0.05, "off", 60, 0)])
        fake.drop_all()
        time.sleep(0.2)
        tk = r.rec(0.1, [(0.02, "on", 60, 100), (0.05, "off", 60, 0)])
        assert len(tk["x"]) == 4410 and np.abs(tk["x"]).max() > 0
        assert sorted(fake.takes)[-1].endswith("r"), sorted(fake.takes)
    finally:
        r.remote.close()


def test_restrike_fake(fake, tmp_path, monkeypatch):
    """usb-restrike over the fake tap (the HQ host build with the live contact): every re-strike sounds, no glitch,
    not louder after the let-up; the CPU ring read; the listening pair written, HQ left on"""
    import wave
    mh, r = _tap(fake, tmp_path)
    r.cpu_fetch = fake_cpu(fake)
    monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
    monkeypatch.setattr(mh.time, "sleep", lambda s: None)
    try:
        ev, up, dur = mh.restrike_events(3.0)
        ons = [e for e in ev if e[1] == "on"]
        assert len(ons) > 16 + 3 / 0.05 and all(e[2] >= 60 for e in ons[16:])
        offs = {}
        for t, k, n, v in ev:                              # (each re-strike: its key up 20 ms before)
            if k == "off":
                offs[n] = t
            elif k == "on" and n in mh.RESTRIKE_KEYS and t > 0.6:
                assert abs(t - offs[n] - 0.020) < 1e-9, (n, t)
        ok, rep = mh.usb_restrike(r, str(tmp_path / "rs"), secs=3.0, listen_secs=1.5, out=lambda s: None)
        assert ok, rep["verdict"]
        assert rep["cpu"]["n"] == 2000 and not rep["glitches"] and rep["louder_after_up_db"] < 1.0, rep
        for hq in (0, 1):
            w = wave.open(str(tmp_path / "rs" / ("restrike-listen-hq%d.wav" % hq)))
            assert w.getnframes() == int(round(3.5 * 44100))
        assert r.hq == 1
        a = [m["value"] for m in fake.log if m.get("key") == "synth:p_hq"]
        assert a == ["1", "0", "1", "1"], a
    finally:
        r.remote.close()


@pytest.mark.parametrize("cut", ["add", "go"])
def test_tap_retry_never_plays_twice(tmp_path, monkeypatch, cut):
    """the connection cut while a take goes up: cut before go (the plugin never started it) -> the take again
    under a new id; cut after go (it started) -> a clear failure, never the notes a second time"""
    import move_hil as mh
    f = FakeSchwung(cut_on=cut)
    monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
    try:
        r = mh.TapRig(f.url, host_fetch(f, str(tmp_path / "tap")), wait=2.0)
        ev = [(0.02 * i, "on" if i % 2 == 0 else "off", 60 + i // 2, 100) for i in range(40)]
        if cut == "add":
            tk = r.rec(1.0, ev)
            assert np.abs(tk["x"]).max() > 0 and sorted(f.takes)[-1].endswith("r")
            assert sum(1 for t in f.takes.values() if t["go"]) == 1
        else:
            with pytest.raises(RuntimeError, match="had started it"):
                r.rec(1.0, ev)
            assert sum(1 for t in f.takes.values() if t["go"]) == 1
        r.remote.close()
    finally:
        f.close()


def test_stress_leaves_the_effects_on_then_d2_restores(tmp_path, monkeypatch):
    """the user's report of 2026-10-28: after a d2 run the plugin loaded with every effect on and the feedbacks at
    127 (usb-stress's HEAVY CCs, kept in the plugin's saved state). The repro: usb-stress alone leaves them; d2 reads
    the state first and writes it back, and its summary checks the readback"""
    import move_hil as mh
    f = FakeSchwung(full=True)
    try:
        before = dict(f.settings)
        r = mh.TapRig(f.url, host_fetch(f, str(tmp_path / "tap")), wait=2.0, cpu=fake_cpu(f))
        monkeypatch.setattr(mh, "TAP_GRID_S", 0.0)
        monkeypatch.setattr(mh.time, "sleep", lambda s: None)
        mh.usb_stress(r, str(tmp_path / "s"), "rhodes", 2.0, out=lambda s: None)
        left = {k for k in before if f.settings[k] != before[k]}
        assert {"p_dl_fdbk", "p_ph_fdbk", "p_dl_sw", "p_ph_sw"} <= left, left       # (the repro: HEAVY stays)
        f.settings = dict(before)
        code = mh.d2(r, str(tmp_path / "d2"), ("rhodes",), ws=f.url, stress_secs=2.0, hq=1, out=lambda s: None)
        rows = json.load(open(tmp_path / "d2" / "report.json"))["rows"]
        st = [w for w in rows if w["test"] == "state"]
        assert code == 0 and len(st) == 1 and st[0]["ok"], [(w["test"], w["verdict"]) for w in rows]
        assert {k: v for k, v in f.settings.items() if before.get(k) != v} == {}
        assert "| all | state | PASS |" in open(tmp_path / "d2" / "summary.md").read()
        assert {"type": "set_param", "slot": 0, "key": "synth:p_hq", "value": "1"} == \
            [m for m in f.log if m.get("key") == "synth:p_hq"][-1]               # (HQ back to its load value)
        r.remote.close()
    finally:
        f.close()


@pytest.mark.parametrize("how", ["exception", "ctrl-c"])
def test_state_restored_on_failure(how):
    import move_hil as mh
    f = FakeSchwung(full=True)
    try:
        before = dict(f.settings)
        exc = RuntimeError("a test crashed") if how == "exception" else KeyboardInterrupt()
        with pytest.raises(type(exc)):
            with mh.KeptState(f.url, 0, out=lambda s: None) as ks:
                with move_ws.Remote(f.url) as w:
                    for k, v in (("p_dl_fdbk", 127), ("s_arpmode", 2), ("preset", 3)):
                        w.set_param(0, "synth:" + k, v)
                    w.pump(0.3)
                assert f.settings["p_dl_fdbk"] == "127"
                raise exc
        assert ks.report["verdict"] == "PASS" and f.settings == before and f.preset == 0, ks.report
        assert ks.report["written"][0] == "preset"                  # (the preset first: it loads its own values)
    finally:
        f.close()


def test_state_check_fails_when_a_write_does_not_take():
    """a param the plugin will not take back (here: the fake ignores it) fails the check, named"""
    import move_hil as mh
    f = FakeSchwung(full=True)
    try:
        with mh.KeptState(f.url, 0, out=lambda s: None) as ks:
            f.settings["p_rv_decay"] = "127"
            f.full = False                                  # (writes no longer change the fake's params)
        assert ks.report["verdict"].startswith("FAIL") and "p_rv_decay" in ks.report["verdict"], ks.report
    finally:
        f.close()


def test_state_restore_writes_the_switches_first():
    """the Move, 2026-10-28: MIX written 0 before its switch came back 50 (a switch gives MIX at 0 a starting
    amount). The switches and types go first, and what still differs is written again"""
    import move_hil as mh
    f = FakeSchwung(full=True)
    try:
        before = dict(f.settings)
        with mh.KeptState(f.url, 0, out=lambda s: None) as ks:
            f.settings.update(p_dl_sw="80", p_dl_depth="127", p_dl_fdbk="127")
        assert ks.report["verdict"] == "PASS" and f.settings == before, ks.report
        w = ks.report["written"]
        assert w.index("p_dl_sw") < w.index("p_dl_depth"), w
    finally:
        f.close()


@pytest.mark.parametrize("drops", [1, 2])
def test_state_restore_after_a_dropped_socket(monkeypatch, drops):
    """the Move, 2026-10-28 (after usb-restrike): the manager dropped the restore's own socket ("no close frame").
    The fake cuts it in the middle of the write-back. One drop: a new connection, the write-back finished, PASS only
    by the read after it, the drop named; a second drop: FAIL"""
    import move_hil as mh
    monkeypatch.setattr(mh, "RESTORE_RETRY_S", 0.0)
    f = FakeSchwung(full=True)
    try:
        before = dict(f.settings)
        with mh.KeptState(f.url, 0, out=lambda s: None) as ks:
            f.settings.update(p_dl_fdbk="127", p_ph_fdbk="127", p_rv_decay="127")
            f.cut_sets = [6] * drops                      # (after the 4 dev / HQ keys and 2 of the 3 params)
        if drops == 1:
            assert ks.report["verdict"] == "PASS" and f.settings == before, ks.report
            assert len(ks.report["dropped"]) == 1 and not f.cut_sets
        else:
            assert ks.report["verdict"].startswith("FAIL: the state was not restored"), ks.report
    finally:
        f.close()
