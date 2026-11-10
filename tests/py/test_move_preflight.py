# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_hil.py's preflight, offline (FakeRig): a clean fake Move passes; a steady floor on Main Out with no note
(the -54.6 dBFS of 2026-10-29) stops it at the idle check; the microphone is named as such; a sound with every note
that is not in Frodhes' tap (a sampler on the track) fails the tap-against-Main-Out check, with the Move's trim and
mix fitted out first; the channel the tools send on is the slot's receive channel, read from Schwung (WebSocket,
else shadow_chain_config.json), and an unread or contradicting channel stops them with the reason."""
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_hil as mh  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "move_hil.py")


def pre(tmp_path, *inject, **kw):
    r = mh.FakeRig(inject=inject)
    rep = mh.fake_slot_report(**kw)
    return mh.preflight_run(r, r, rep, mh.resolve_channel(rep), str(tmp_path), out=lambda s: None)


def test_pass(tmp_path):
    ok, rep = pre(tmp_path)
    assert ok and rep["verdict"].startswith("PASS"), rep["verdict"]
    t = rep["tap"]
    assert t["corr"] > 0.9999 and t["resid_db"] < -55 and t["resid_dbfs"] < -80, t
    assert abs(t["gain_db"] - 20 * np.log10(mh.FAKE_MIX)) < 0.05, t          # (the trim and the mix, fitted)
    assert abs(t["tap_offset_s"] - mh.FAKE_TAP_LEAD) < 0.01, t
    assert rep["idle"]["kind"] == "digital"
    assert (tmp_path / "preflight-main.wav").exists() and (tmp_path / "preflight.json").exists()


def test_idle_noise_fails(tmp_path):
    ok, rep = pre(tmp_path, ("hiss", 0.0))
    assert not ok and mh.PLAY_SAY in rep["verdict"] and "tap" not in rep, rep["verdict"]
    assert abs(rep["idle"]["floor_db"] + 54.6) < 0.5, rep["idle"]
    ok, rep = pre(tmp_path, ("playing", 0.0))
    assert not ok and mh.PLAY_SAY in rep["verdict"]


def test_mic_named(tmp_path):
    ok, rep = pre(tmp_path, ("mic", 0.0))
    assert not ok and rep["verdict"] == "FAIL: " + mh.MIC_SAY + rep["verdict"][6 + len(mh.MIC_SAY):], rep["verdict"]
    # (the stream that passed idle but does not carry the tap: the microphone hearing the speakers)
    ok, txt = mh.tap_verdict({"kind": "ok", "corr": 0.71, "gain_db": -3.0, "resid_db": -3.0, "resid_dbfs": -40.0,
                              "resid_hz": [], "resid_bands_hz": [], "mix_db": -5.0})
    assert not ok and txt.startswith(mh.MIC_SAY), txt


def test_residual_fails(tmp_path):
    ok, rep = pre(tmp_path, ("foreign", 0.0))
    assert not ok and mh.FOREIGN_SAY in rep["verdict"], rep["verdict"]
    t = rep["tap"]
    assert rep["idle"]["kind"] == "digital" and t["resid_db"] > mh.PF_RESID_DB and t["resid_bands_hz"], t
    assert abs(t["gain_db"] - 20 * np.log10(mh.FAKE_MIX)) < 0.1, t          # (the gain still Frodhes')


def test_tap_silent_and_not_on_main_out():
    x = np.zeros((44100, 2))
    ok, txt = mh.tap_verdict(mh.tap_vs_main(x, 44100, np.zeros((3 * 44100, 2))))
    assert not ok and "did not reach Frodhes" in txt
    rng = np.random.default_rng(0)
    tap = np.zeros((4 * 44100, 2))
    tap[44100:2 * 44100] = rng.normal(0, 3000, (44100, 2))
    main = np.round(0.001 * tap[44100 - 8820:44100 - 8820 + 110250] + rng.normal(0, 1, (110250, 2)))
    ok, txt = mh.tap_verdict(mh.tap_vs_main(main, 44100, tap))
    assert not ok and "not on Main Out" in txt, txt


CONFIG = json.dumps({"slot_channels": [-1, 1, 2, 3], "slot_forward_channels": [-1, -1, 4, -1],
                     "slot_volumes": [1.89, 1, 1, 1]})
SONG = json.dumps({"tracks": [
    {"midiInputMode": "auto", "mixer": {"volume": 6.0}, "devices": [
        {"kind": "instrumentRack", "name": "Sampler", "parameters": {}, "chains": [{"devices": [
            {"kind": "melodicSampler", "name": "", "parameters": {}, "deviceData": {"sampleUri": "noise.wav"}}]}]}]},
    {"midiInputMode": [3], "mixer": {"volume": 0.0}, "devices": []}]})


def test_channel_lookup():
    c = mh.parse_chain_config(CONFIG, 0)
    assert c == {"receive_channel": 0, "forward_channel": -1, "volume": 1.89}
    assert mh.parse_chain_config(CONFIG, 2)["receive_channel"] == 3 and mh.parse_chain_config("garbage") == {}
    t = mh.parse_song(SONG, 0)
    assert t["midi_in"] == "auto" and t["volume_db"] == 6.0
    assert {"kind": "melodicSampler", "name": "", "sample": "noise.wav"} in t["devices"], t
    assert mh.parse_song(SONG, 1)["midi_in"] == [4] and mh.parse_song("{") == {}
    # the WebSocket's receive channel wins; the notes go out on it, Frodhes sees it (forward auto)
    ch = mh.resolve_channel(mh.fake_slot_report(recv=3))
    assert ch["send"] == 3 and ch["plugin"] == 3 and ch["recv_from"] == "Schwung's WebSocket" and not ch["stop"]
    # the WebSocket unread: shadow_chain_config.json
    rep = {"ws": {}, "config": {"receive_channel": 2, "forward_channel": -1}, "track": {}, "errors": ["ws: down"]}
    ch = mh.resolve_channel(rep)
    assert ch["send"] == 2 and ch["recv_from"] == "shadow_chain_config.json" and not ch["stop"]
    # nothing read: stop, naming the channel assumed and --ch
    rep = {"ws": {}, "config": {}, "track": {}, "errors": ["Schwung's WebSocket: refused"]}
    ch = mh.resolve_channel(rep)
    assert ch["stop"] and "assume channel 1" in ch["stop"] and "--ch N" in ch["stop"] and "refused" in ch["stop"]
    assert mh.resolve_channel(rep, 5)["send"] == 5 and not mh.resolve_channel(rep, 5)["stop"]
    # --ch against the slot's channel: stop; All takes any
    assert "receives on channel 3" in mh.resolve_channel(mh.fake_slot_report(recv=3), 1)["stop"]
    ch = mh.resolve_channel(mh.fake_slot_report(recv=0))
    assert ch["send"] == 1 and ch["plugin"] == 1 and any("All" in n for n in ch["notes"]), ch
    assert mh.resolve_channel(mh.fake_slot_report(recv=0), 7)["send"] == 7
    # the forward channel: what Frodhes sees, against its CHAN
    assert mh.resolve_channel(mh.fake_slot_report(recv=2, fwd=4))["plugin"] == 5
    assert mh.resolve_channel(mh.fake_slot_report(recv=2, fwd=-2))["plugin"] == 2
    st = mh.resolve_channel(mh.fake_slot_report(recv=1, mch=2))["stop"]
    assert st and "CHAN is 2" in st and "channel 1" in st, st
    assert not mh.resolve_channel(mh.fake_slot_report(recv=2, mch=2))["stop"]
    # the Move's track listens elsewhere: both reported
    ch = mh.resolve_channel(mh.fake_slot_report(recv=1, midi_in=[4]))
    assert any("[4]" in n and "differ" in n for n in ch["notes"]), ch["notes"]
    lines = mh.report_lines(mh.fake_slot_report(), mh.resolve_channel(mh.fake_slot_report()))
    assert any("synth 'frodhes'" in s for s in lines) and any("melodicSampler (sample none)" in s for s in lines)
    lines = mh.report_lines({"ws": {}, "config": {}, "track": {}, "errors": ["the Move's set: not read"]}, {})
    assert lines == ["cannot read: the Move's set: not read"]


def run(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, cwd=ROOT)


def test_cli(tmp_path):
    r = run("preflight", "--fake", "--out", str(tmp_path / "ok"))
    assert r.returncode == 0 and "PASS: Main Out is Frodhes" in r.stdout, r.stdout + r.stderr
    r = run("usb-note", "--fake", "--inject", "foreign", "--out", str(tmp_path / "n"))
    assert r.returncode == 2 and mh.FOREIGN_SAY in r.stdout and "note 60 v100: onset" not in r.stdout, r.stdout
    r = run("usb-note", "--fake", "--inject", "foreign", "--no-preflight", "--out", str(tmp_path / "s"))
    assert r.returncode == 0, r.stdout + r.stderr
    r = run("d2", "--fake", "--ws", "none", "--engines", "rhodes", "--inject", "hiss", "--out", str(tmp_path / "d"))
    md = open(tmp_path / "d" / "summary.md").read()
    assert r.returncode == 2 and "| all | preflight | FAIL" in md and "usb-host" not in md, md


def test_preflight_unread_channel_stops(tmp_path):
    """the slot's receive channel unread (no "send" in the channel): the preflight stops with the reason, never a
    KeyError (the device run's crash at preflight_cmd: chan["send"])"""
    rep = {"ws": {"synth": "frodhes"}, "config": {}, "track": {}, "errors": ["shadow_chain_config.json: not read"]}
    chan = mh.resolve_channel(rep)
    assert chan["stop"] and "send" not in chan

    class A:
        fake, ws, midi, audio, host, inject = False, "ws://x", None, None, None, ()
    ok, res = mh.preflight_cmd(A(), str(tmp_path), chan, rep)
    assert not ok and res["verdict"].startswith("STOP:") and "--ch N" in res["verdict"], res
