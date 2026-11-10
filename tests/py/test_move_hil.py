# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_hil.py and tools/device_null.py, offline: every subcommand's dry run plans commands and runs none; the
CPU ring's log parses and is judged against U12 (and a gap in the callback index fails: the ring overran); the
crackle detectors find a stale repeat, a dropout and a clip in a recording at the Move's 128-frame cadence and
nothing in a clean one; the must-pass is not runnable yet (exit 2); the device null test's proof holds (a glitch, a
1-sample shift and a 0.1 dB gain error each fail; the clean take passes)."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import device_null  # noqa: E402
import move_hil as mh  # noqa: E402
import move_install as mi  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "move_hil.py")


def run(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, cwd=ROOT)


def test_dry_runs_run_nothing():
    for cmd in ("facts", "logs", "cpu", "rollback"):
        lines = []
        t = mi.Target(host="ableton@move.local", go=False, out=lines.append)
        {"facts": mh.facts, "logs": mh.logs, "cpu": mh.cpu, "rollback": mi.rollback}[cmd](t)
        assert t.log and all(c[0] in ("ssh", "scp") for c in t.log), (cmd, t.log)
    r = run("facts")
    assert r.returncode == 0 and "dry run" in r.stdout and "ssh" in r.stdout


def test_cpu_ring_judged():
    good = "# callback us frames\n" + "\n".join("%d %d 128" % (i, 300 + (i % 7) * 40) for i in range(1000))
    s = mh.cpu_summary(mh.parse_ring(good))
    assert s["verdict"] == "PASS" and s["worst"] == 540 and s["missing"] == 0
    hot = good + "\n1000 800 128"
    assert mh.cpu_summary(mh.parse_ring(hot))["verdict"] == "FAIL"
    gap = "\n".join("%d 300" % i for i in list(range(10)) + list(range(15, 20)))
    s = mh.cpu_summary(mh.parse_ring(gap))
    assert s["missing"] == 5 and s["verdict"] == "FAIL"
    assert "over the design" in mh.cpu_summary(mh.parse_ring("0 650\n1 640"))["verdict"]


def write_wav(path, q):
    w = wave.open(path, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(44100)
    w.writeframes(np.asarray(q, "<i2").tobytes())
    w.close()


def test_crackle_detectors_on_a_recording(tmp_path):
    rng = np.random.default_rng(3)
    n = 44100 * 2
    t = np.arange(n) / 44100
    x = 6000 * np.sin(2 * np.pi * 220 * t) * (1 + 0.3 * np.sin(2 * np.pi * 0.7 * t)) + rng.normal(0, 50, n)
    q = np.round(np.stack([x, 0.8 * x], 1)).astype(np.int64)
    clean = str(tmp_path / "clean.wav")
    write_wav(clean, q)
    assert mh.crackle_analyze(clean, out=lambda s: None) == []
    bad = q.copy()
    b = 30000
    bad[b:b + mh.BLOCK] = bad[b - mh.BLOCK:b]                     # one callback repeated
    bad[60000:60000 + 64] = 0                                     # 1.5 ms dropped
    bad[70000, 0] = 32767                                         # a clip
    p = str(tmp_path / "bad.wav")
    write_wav(p, bad)
    kinds = sorted({k for k, _, _ in mh.crackle_analyze(p, out=lambda s: None)})
    assert kinds == ["clip", "stale", "zero"], kinds


def test_plans_and_stubs():
    r = run("crackle", "--engine", "rhodes")
    assert r.returncode == 0 and "rhodes-worst1" in r.stdout and "min of takes" in r.stdout
    r = run("must")
    assert r.returncode == 2 and "Not runnable yet" in r.stdout
    r = run("null")
    assert r.returncode == 0 and "takes" in r.stdout


def test_device_null_proof():
    res = device_null.self_test(out=lambda s: None)
    assert res and all(want == got for _, want, got, _ in res), res
    assert any(not w for _, w, _, _ in res)                       # (fail cases exist)


# ---- over USB-C: the tests against the offline model of the Move (FakeRig); the device runs are the main session's
def _fake(tmp_path, fn, *inject, **kw):
    r = mh.FakeRig(inject=inject, tap=fn is mh.usb_clock)     # (the clock: over the tap, as on the device)
    return fn(r, str(tmp_path), out=lambda s: None, **kw)


def test_usb_ports_picked():
    names = ["IAC Driver Bus 1", "MIDISPORT 2x4 Port 1", "M6", "Frodhes FM-1", "Ableton Move Live Port",
             "Ableton Move Standalone Port"]
    assert mh._pick(names, mh.MIDI_KEYS, "MIDI") == "Ableton Move Standalone Port"
    assert mh._pick(names[:5], mh.MIDI_KEYS, "MIDI") is None          # (the Live port never reaches a slot)
    assert mh._pick(names, mh.MIDI_KEYS, "MIDI", want="midisport") is None
    assert mh._pick(["M6", "Ableton Move"], mh.AUDIO_KEYS, "audio") == "Ableton Move"


def test_usb_note_vel_sus_clock_pass_on_a_good_device(tmp_path):
    ok, rep = _fake(tmp_path, mh.usb_note)
    assert ok and abs(rep["cents"]) < 1 and rep["glitches"] == [], rep
    assert os.path.exists(tmp_path / "note.wav")
    ok, rep = _fake(tmp_path, mh.usb_vel)
    assert ok and rep["range_db"] > 20, rep
    ok, rep = _fake(tmp_path, mh.usb_sus)
    assert ok, rep
    ok, rep = _fake(tmp_path, mh.usb_clock, bpm=100.0)
    assert ok and abs(rep["bpm_seen"] - 100) < 1 and rep["troughs"] >= 25, rep


def test_usb_failures_found(tmp_path):
    ok, rep = _fake(tmp_path, mh.usb_note, ("click", 1.0))
    assert not ok and rep["glitches"], rep
    ok, rep = _fake(tmp_path, mh.usb_sus, ("nosus", 0.0))
    assert not ok and "pedal does not hold" in rep["verdict"], rep
    ok, rep = _fake(tmp_path, mh.usb_sus, ("loud", 5.0))
    assert not ok and "louder after key-up" in rep["verdict"], rep
    ok, rep = mh.usb_clock(mh.FakeRig(tap=True), str(tmp_path), bpm=100.0, secs=4.0, out=lambda s: None)
    assert not ok and "no tremolo" in rep["verdict"], rep


def test_usb_clock_without_the_clock_fails(tmp_path):
    """a tremolo that ignores the clock runs at the internal 120 BPM: against the clock's 100 it fails (the reason the
    default is 100), and with the clock it passes at another tempo too"""
    ok, rep = _fake(tmp_path, mh.usb_clock, ("noclock", 0.0), bpm=100.0)
    assert not ok and "jitter" in rep["verdict"], rep
    ok, rep = _fake(tmp_path, mh.usb_clock, bpm=137.0)
    assert ok and abs(rep["bpm_seen"] - 137) < 1.4, rep


def test_usb_clock_never_over_usb_c(tmp_path):
    """Schwung gives a slot no realtime byte from USB-C (cable 2: channel messages only; the Move's own transport, cable
    0, is broadcast), so the clock sent there never reaches Frodhes: the device's usb-clock over USB-C read jitter 82
    ms and the plugin rt_f8 0 (2026-10-30). The fake models it (its tremolo then runs free), usb_clock refuses a
    USB-C rig before any take, and the command, d2 and the fake d2 run it over the tap"""
    r = mh.FakeRig()
    assert not r.CLOCK_IN and not mh.UsbRig.CLOCK_IN and mh.TapRig.CLOCK_IN
    ok, rep = mh.usb_clock(r, str(tmp_path / "u"), bpm=100.0, out=lambda s: None)
    assert not ok and "not over USB-C" in rep["verdict"] and r.log == [], rep
    # (the model itself: the realtime bytes sent over USB-C are dropped, the tremolo stays free at 120 BPM)
    mh._prep(r, None, **{"c%d" % c: v for c, v in mh.CLOCK_CCS})
    ev = mh.clock_events(100.0, 0.1, 11.6) + [(2.8, "on", 60, 100), (11.4, "off", 60, 0)]
    a = mh.FakeRig(tap=True)
    a.state = dict(r.state)
    x_usb, x_tap = r.rec(12.0, ev)["x"], a.rec(12.0, ev)["x"]
    assert np.abs(x_usb - x_tap).max() > 1000                    # (the same take: only the clock differs)
    a2 = mh.clock_rig(None, r, str(tmp_path), {})
    assert isinstance(a2, mh.FakeRig) and a2.CLOCK_IN
    tap = mh.FakeRig(tap=True)
    assert mh.clock_rig(None, tap, str(tmp_path), {}) is tap
    code = mh.d2(mh.FakeEchoRig(), str(tmp_path / "d"), ("rhodes",), stress_secs=4.0, out=lambda s: None,
                 only=["usb-clock"])
    rep = __import__("json").load(open(tmp_path / "d" / "report.json"))
    row = next(w for w in rep["rows"] if w["test"] == "usb-clock")
    assert code == 1 and "not over USB-C" in row["verdict"], row
    r = run("usb-clock", "--fake", "--out", str(tmp_path / "c"))
    assert r.returncode == 0 and "usb-clock over" in r.stdout and "PASS" in r.stdout, r.stdout[-2000:]


def test_usb_stress_detectors(tmp_path):
    ok, rep = _fake(tmp_path, mh.usb_stress, secs=8.0)
    assert ok, [t["events"] for t in rep["takes"]]
    # (the clicks: 2.85 s, where the fake take's 50 ms lead stays under hil_crackle's LIM_ON (its limiting() mask,
    # 2.76-3.00 s clear), labelled click; 2.41 s, under it (2.40-2.43 s held), labelled click-lim, information.
    # f62488fd's 16-note chords put 2.37 s, the old place, under the limiter)
    ok, rep = _fake(tmp_path, mh.usb_stress, ("click", 2.85), ("click", 2.41), ("stale", 3.3), ("zero", 6.13),
                    secs=16.0)
    ev = {(round(t, 2), k) for t, k, _ in rep["takes"][0]["events"]}
    low = {k for _, k in ev}
    assert not ok and {"click", "click-lim", "stale", "zero"} <= low, ev
    assert (2.85, "click") in ev and (2.41, "click-lim") in ev, ev


def test_usb_cli_fake(tmp_path):
    r = run("usb-note", "--fake", "--out", str(tmp_path))
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
    assert os.path.exists(tmp_path / "report.json")


def test_idle_verdict():
    """the preflight's three cases: digital silence, the microphone (broadband noise with LF rumble, L == R), a
    sound playing (a tone, stereo)"""
    rng = np.random.default_rng(1)
    n = 44100
    assert mh.idle_verdict(np.zeros((n, 2)), 44100)["kind"] == "digital"
    assert mh.idle_verdict(rng.integers(-1, 2, (n, 2)).astype(float), 44100)["kind"] == "digital"   # (dither)
    t = np.arange(n) / 44100
    room = rng.normal(0, 60, n) + 2000 * np.sin(2 * np.pi * 4 * t) + 800 * np.sin(2 * np.pi * 11 * t)
    room = np.round(room)
    v = mh.idle_verdict(np.stack([room, room], 1), 44100)
    assert v["kind"] == "mic", v
    tone = 3000 * np.sin(2 * np.pi * 262 * t)
    assert mh.idle_verdict(np.stack([tone, 0.7 * tone], 1), 44100)["kind"] == "playing"


def test_usb_host_same_sound(tmp_path):
    """usb-host passes a device that plays the host's own render (here: the render itself, 6 dB down and 7 ms late:
    the Move's volume and its latency) and fails one that plays another sound (the fake's model)"""
    class HostEcho(mh.FakeRig):
        def rec(self, dur, events):
            h = mh.host_phrase(events, dur, 0)
            x = np.zeros_like(h)
            x[300:] = 0.5 * h[:-300]
            return {"x": np.round(x), "sr": 44100, "xruns": []}
    ok, rep = mh.usb_host(HostEcho(), str(tmp_path), out=lambda s: None)
    assert ok and abs(rep["gain_db"] + 6.02) < 0.1, rep
    assert all(abs(w["lag_ms"] - 6.8) < 0.1 for w in rep["rows"]), rep["rows"]
    ok, rep = mh.usb_host(mh.FakeRig(), str(tmp_path), out=lambda s: None)
    assert not ok

    class HostPlusOther(HostEcho):               # (the first device run: a short odd-harmonic tone on every key)
        def rec(self, dur, events):
            out = HostEcho.rec(self, dur, events)
            x, t = out["x"], np.arange(int(0.3 * 44100)) / 44100
            for tt, k, n, v in events:
                if k == "on":
                    f = mh.note_hz(n)
                    tone = 3000 * np.exp(-t / 0.08) * (np.sin(2 * np.pi * f * t) + np.sin(6 * np.pi * f * t) / 3)
                    i = int(tt * 44100) + 300
                    x[i:i + len(t)] += tone[:, None]
            return out
    ok, rep = mh.usb_host(HostPlusOther(), str(tmp_path), out=lambda s: None)
    assert not ok and "ANOTHER SOUND" in rep["verdict"] and abs(rep["gain_db"] + 6.02) < 0.1, rep["verdict"]


def test_usb_host_quiet_mix_aligns_on_time(tmp_path):
    """a take as the 2026-10-30 USB-C run had it: Main Out 17.1 dB under the tap (the track's volume), 14.92 ms late,
    in whole LSB. Every note aligns on the take's one latency and the take passes"""
    g, lag = 10 ** (-17.11 / 20), 658

    class QuietMix(mh.FakeRig):
        def rec(self, dur, events):
            h = mh.host_phrase(events, dur, 0)
            x = np.zeros_like(h)
            x[lag:] = g * h[:-lag]
            rng = np.random.default_rng(7)               # (the mix's rounding: TPDF, 1 LSB)
            x += rng.uniform(-0.5, 0.5, x.shape) + rng.uniform(-0.5, 0.5, x.shape)
            return {"x": np.round(x), "sr": 44100, "xruns": []}
    ok, rep = mh.usb_host(QuietMix(), str(tmp_path), out=lambda s: None)
    assert all(abs(w["lag_ms"] - 1000 * lag / 44100) < 0.05 for w in rep["rows"]), [
        (w["note"], w["lag_ms"]) for w in rep["rows"]]
    assert ok, rep["verdict"]


def _quiet_take(fault):
    """the quiet-mix take (Main Out 17.11 dB under the tap, 14.92 ms late, whole LSB with TPDF dither) with a fault"""
    g, lag = 10 ** (-17.11 / 20), 658

    class R(mh.FakeRig):
        def rec(self, dur, events):
            h = mh.host_phrase(events, dur, 0)
            x = np.zeros_like(h)
            x[lag:] = g * h[:-lag]
            x = fault(x, lag)
            rng = np.random.default_rng(7)
            x += rng.uniform(-0.5, 0.5, x.shape) + rng.uniform(-0.5, 0.5, x.shape)
            return {"x": np.round(x), "sr": 44100, "xruns": []}
    return R()


def test_usb_host_floor_judging(tmp_path):
    """a note is judged only when its tail is JUDGE_OVER_FLOOR_DB over the take's rounding floor: 96 v90's tail at
    -59.6 dBFS in the quiet mix (4370bb25) left -39.7 dB of the take's own dither and failed a sound that is the
    host's. The rows say how far over the floor each tail is"""
    ok, rep = mh.usb_host(_quiet_take(lambda x, lag: x), str(tmp_path), out=lambda s: None)
    assert ok, rep["verdict"]
    for w in rep["rows"]:
        assert w["judged"] == (w["tail_dbfs"] >= mh.TAIL_JUDGED_DBFS and w["over_floor_db"] >= mh.JUDGE_OVER_FLOOR_DB)
    assert sum(w["judged"] for w in rep["rows"]) >= 5, rep["rows"]


@pytest.mark.parametrize("fault", ["slip", "gain", "softclip", "softclip0"])
def test_usb_host_catches_planted_faults(tmp_path, fault):
    """the floor rule still catches real faults on the quiet mix: a 1 ms timing slip inside every held note, a 1 dB
    gain error on every other note, a soft clip (tanh, knee 2000 LSB); and a soft clip on a full-level take (knee
    8000 LSB)"""
    _, marks, _ = mh.phrase_events()
    sr = 44100

    def slip(x, lag):
        for _, t, _ in marks:
            k = int((t + 0.7) * sr) + lag
            x = np.concatenate([x[:k], x[k - 44:]])[:len(x)]
        return x

    def gain(x, lag):
        y = x.copy()
        for i, (_, t, _) in enumerate(marks):
            if i % 2:
                y[int(t * sr) + lag:int((t + mh.NOTE_S + 0.5) * sr) + lag] *= 10 ** (1 / 20)
        return y
    f = {"slip": slip, "gain": gain, "softclip": lambda x, lag: 2000 * np.tanh(x / 2000),
         "softclip0": lambda x, lag: 8000 * np.tanh(x / 10 ** (-17.11 / 20) / 8000)}[fault]
    ok, rep = mh.usb_host(_quiet_take(f), str(tmp_path), out=lambda s: None)
    assert not ok and rep["verdict"].startswith("DIFFERS"), rep["verdict"]


def test_usb_host_hq_probe_only_where_the_take_resolves_it(tmp_path):
    """HQ against Classic at 96 v90: the two hosts, each aligned, are ~50 dB apart. A Classic take through a clean
    mix is CLASSIC; an HQ take with a floor of ~-38 dB under the note at the probe (the 2026-10-30 USB-C take, aligned,
    read 1.1 dB nearer Classic within that floor and failed as CLASSIC) cannot tell them apart: PASS, the probe named
    as not resolved"""
    lag = 300

    class Classic(mh.FakeRig):
        hq = None
        floor = 0.0
        played = 0                                   # (the host the take plays: 0 Classic, None HQ)

        def rec(self, dur, events):
            h = mh.host_phrase(events, dur, 0, self.played)
            x = np.zeros_like(h)
            x[lag:] = 0.5 * h[:-lag]
            if self.floor:
                ev, marks, _ = mh.phrase_events()
                t = next(t for name, t, n in marks if name == mh.HQ_PROBE)
                a, b = int(t * 44100) + lag, int((t + mh.NOTE_S) * 44100) + lag
                x[a:b] *= 1 + np.random.default_rng(3).normal(0, 10 ** (self.floor / 20), x[a:b].shape)   # (the
                #                                                       floor under the note all along it)
            return {"x": np.round(x), "sr": 44100, "xruns": []}
    ok, rep = mh.usb_host(Classic(), str(tmp_path), out=lambda s: None)
    assert not ok and rep["verdict"].startswith("CLASSIC") and rep["hq_probe"]["resolved"], rep["verdict"]
    noisy = Classic()
    noisy.floor, noisy.played = -38.0, None
    ok, rep = mh.usb_host(noisy, str(tmp_path), out=lambda s: None)
    assert ok and "not resolved" in rep["verdict"] and not rep["hq_probe"]["resolved"], (rep["verdict"],
                                                                                         rep["hq_probe"])


SCENES_2026_10_30 = ('{"v":3,"active":0,"pairs":[[0,0],[1,1]],"halves":[{"n":16,"locks":['
                     '{"scope":"slot0","target":"synth","param":"s_fx","module":"frodhes","value":1},'
                     '{"scope":"slot0","target":"synth","param":"p_tone","module":"frodhes","value":32},'
                     '{"scope":"slot0","target":"synth","param":"p_bark","module":"frodhes","value":0}]}]}')


def test_scene_locks_and_their_ccs():
    """the set's scenes.json as the Move had it on 2026-10-30: slot 1's locks, none on slot 2; the held values as the
    host's CCs (TONE's wire -64..63 on CC 28 at +64; a latch has none the host takes)"""
    assert mh.scene_locks(SCENES_2026_10_30, 0) == ["p_bark", "p_tone", "s_fx"]
    assert mh.scene_locks(SCENES_2026_10_30, 1) == []
    assert mh.scene_locks("", 0) == [] and mh.scene_locks("[]", 0) == []
    k = mh.move_keys()
    assert mh.held_cc("p_tone", "10", k) == (28, 74) and mh.held_cc("p_bark", "64", k) == (29, 64)
    assert mh.held_cc("s_fx", "1", k) == (0, 1)
    rep = {"ws": {"synth": "frodhes"}, "locks": mh.scene_locks(SCENES_2026_10_30, 0)}
    assert any("scene locks hold p_bark, p_tone, s_fx" in ln for ln in mh.report_lines(rep, {}))


def test_usb_host_plays_the_held_values(tmp_path):
    """the device's VIBES DIFFERS of 2026-10-30: Schwung's scene locks put BARK back to 64 after F13's program change
    (52). A take with the held value differs from the preset's host; with the rig's held() the host gets the held
    values after the program change, and the verdict names them"""
    class Held(mh.FakeRig):
        def rec(self, dur, events):
            h = mh.host_phrase(events, dur, 12, after=[(29, 64)])
            x = np.zeros_like(h)
            x[300:] = 0.5 * h[:-300]
            return {"x": np.round(x), "sr": 44100, "xruns": []}

        def held(self):
            return {"p_bark": (29, 64), "s_fx": (0, 1)}

    class Unaware(Held):
        held = None
    ok, rep = mh.usb_host(Unaware(), str(tmp_path / "a"), 12, out=lambda s: None)
    assert not ok and "DIFFERS" in rep["verdict"], rep["verdict"]
    ok, rep = mh.usb_host(Held(), str(tmp_path / "b"), 12, out=lambda s: None)
    assert ok and rep["verdict"].startswith("PASS"), rep["verdict"]
    assert rep["held"] == {"p_bark": 64, "s_fx": 1} and "p_bark (CC 29 64)" in rep["verdict"], rep
    ok2, rep2 = mh.usb_host_file(str(tmp_path / "b"), out=lambda s: None)   # (the saved run keeps them: held.json)
    assert ok2 and rep2["held"] == rep["held"], rep2["verdict"]


def test_usb_host_96v90_of_the_2026_10_30_take():
    """the 2026-10-30 USB-C run's 96 v90, rebuilt from the Move host build (no recording): the Move's Main Out 17.1 dB
    under the tap and 658 frames (14.92 ms) late, in whole LSB with the mix's TPDF rounding, its tail ~20 LSB; in the
    tail 70 % of the note comes 14 periods later (953 frames), so the tail correlates best there (0.9998) and on time
    a little less (0.9995), as the take did. The old alignment (the best tail correlation) took 953, and the note's
    attack, 6.7 ms off, read as ~-30 dB of ANOTHER SOUND. _align takes the take's lag (658), and the note's extra stays
    far under the -30 dB limit"""
    sr, lag, late, g = 44100, 658, 953, 10 ** (-17.11 / 20)
    ev, marks, dur = mh.phrase_events()
    import dry_dist                                      # (the take played the F01 of its day: dry_dist's TYPE base,
    h = mh.host_phrase(ev, dur, 0, None, mh.GLOBAL_CCS, dry_dist.TYPE_BASE)   # not the proposed 19's F01, 0.9.10)
    t = next(t for name, t, n in marks if name == mh.HQ_PROBE)
    a, b = int(t * sr), int((t + mh.NOTE_S) * sr)
    ta = a + sr // 2
    x = np.zeros_like(h)
    x[lag:] = g * h[:-lag]
    s = ta + lag                                         # (the tail: 30 % on time, 70 % 14 periods late)
    x[s:] = g * (0.3 * h[s - lag:len(h) - lag] + 0.7 * h[s - late:len(h) - late])
    rng = np.random.default_rng(7)
    mv = np.round(x + rng.uniform(-0.5, 0.5, x.shape) + rng.uniform(-0.5, 0.5, x.shape))
    lo, hi = -int(0.01 * sr), int(0.08 * sr)
    rt = mh._mono(h[ta:b])
    tail = max((float(np.dot(mh._mono(mv[ta + k:b + k]), rt) / (np.linalg.norm(mh._mono(mv[ta + k:b + k])) *
                                                                 np.linalg.norm(rt) + 1e-9)), k) for k in range(lo, hi))
    assert tail[1] == late, tail                         # (the trap: the tail's best is 14 periods late)

    def extra(k):
        res = mh._mono(mv[a + k:b + k]) - g * mh._mono(h[a:b])
        return 20 * np.log10(np.sqrt(np.mean(res ** 2)) / np.sqrt(np.mean((g * mh._mono(h[a:b])) ** 2)))
    corr, got = mh._align(mv, h, a, b, ta, b, lo, hi)
    assert got == lag and corr > 0.999, (got, corr)
    assert extra(got) < -35, extra(got)
    # (the old pick: the attack off, "another sound"; 2026-11-08: 0.9.10's note -34.0 dB there against -45.1 aligned,
    # was > -32: the trap kept as a margin over the right lag's residual)
    assert extra(late) > extra(got) + 6, (extra(late), extra(got))


def test_align_breaks_a_steady_tail_tie_on_the_attack():
    """_align: a decaying 2093 Hz tone with a sharp attack, the take 300 samples late; the tail alone has a peak every
    period (21 samples), the whole note one: the lag is 300"""
    sr, n = 44100, 44100
    t = np.arange(n) / sr
    x = 8000 * np.minimum(1, t / 0.002) * np.exp(-t / 0.6) * np.sin(2 * np.pi * 2093 * t)
    x[:200] += np.linspace(4000, 0, 200)                 # (the hammer's knock)
    ref = np.zeros((n + 4000, 2))
    ref[:n, 0] = ref[:n, 1] = x
    mv = np.zeros_like(ref)
    mv[300:] = 0.25 * ref[:-300]
    mv = np.round(mv)
    a, b = 0, n
    corr, lag = mh._align(mv, ref, a, b, a + sr // 2, b, -441, 3528)
    assert lag == 300 and corr > 0.99, (lag, corr)


def test_d2_fake(tmp_path):
    """d2 on the fake Move: every engine's tests in order (usb-host on the engine's preset, three keys; usb-clock once,
    with the Rhodes), one table;
    a sound at idle stops the run at the first preflight (exit 2) and says why"""
    code = mh.d2(mh.FakeEchoRig(), str(tmp_path), ("rhodes", "marimba"), stress_secs=8.0, out=lambda s: None,
                 clock=mh.FakeRig(tap=True))
    md = open(tmp_path / "summary.md").read()
    rep = __import__("json").load(open(tmp_path / "report.json"))
    tests = [(w["engine"], w["test"]) for w in rep["rows"]]
    assert tests[:10] == [("rhodes", t) for t in ("idle", "usb-host", "usb-note-36", "usb-note-60", "usb-note-84",
                                                   "usb-vel", "usb-sus", "usb-clock", "usb-stress")] + [("marimba", "idle")]
    assert len(tests) == 17 and code == 0, [(w["test"], w["verdict"]) for w in rep["rows"] if not w["ok"]]
    assert "| rhodes | usb-host | PASS | gain -6.02 dB" in md and "# Move D2 over USB-C: PASS" in md, md
    host = next(w for w in rep["rows"] if w["engine"] == "marimba" and w["test"] == "usb-host")["report"]
    assert any(not w["judged"] for w in host["rows"])           # (84 v40 has died 0.5 s in: not judged on its tail)
    assert os.path.exists(tmp_path / "marimba" / "usb-note-60" / "note.wav")
    code = mh.d2(mh.FakeEchoRig(inject=[("playing", 0.0)]), str(tmp_path / "p"), out=lambda s: None)
    md = open(tmp_path / "p" / "summary.md").read()
    assert code == 2 and "STOPPED" in md and "| rhodes | idle | FAIL" in md and "usb-host" not in md, md


def test_d2_cli_fake(tmp_path):
    r = run("d2", "--fake", "--ws", "none", "--engines", "vibes", "--secs", "8", "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr
    assert "| vibes | usb-note-53 | PASS" in open(tmp_path / "summary.md").read()


def test_usb_levels_gain_match():
    """usb-levels' two measures find a known gain: Frodhes 6 dB under the stock take, every band alike"""
    sr = 44100
    ev, dur = mh.level_events(100)
    x = np.zeros(int(dur * sr))
    for t, k, a, b in ev:
        if k == "on":
            tt = np.arange(int(0.4 * sr)) / sr
            i = int(t * sr)
            x[i:i + len(tt)] += 4000 * np.sin(2 * np.pi * mh.note_hz(a) * tt) * np.exp(-4 * tt)
    st = np.stack([x, x], 1)
    m = mh.level_match(mh.level_of(st, sr), mh.level_of(st * 10 ** (-6 / 20), sr))
    assert abs(m["gain_lufs_db"] - 6.0) < 0.05 and abs(m["gain_cb_db"] - 6.0) < 0.05


def test_d2_only(tmp_path):
    """d2 --tests: only the named tests (a prefix takes all three usb-note keys); the others are not run at all"""
    r = run("d2", "--fake", "--ws", "none", "--engines", "rhodes", "--secs", "4", "--tests", "usb-note,usb-stress",
            "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr
    rep = __import__("json").load(open(tmp_path / "report.json"))
    assert [w["test"] for w in rep["rows"]] == ["preflight", "idle", "usb-note-36", "usb-note-60", "usb-note-84", "usb-stress"]


def test_clock_probe_fake(tmp_path):
    """clock-probe on a fake plugin: the Move's own F8 PASS (also when they come 2 s after Play: the fallback first);
    only the bridge's fallback, or no transport at all, FAIL and say which; the prompts come in order"""
    said = []
    ok, rep = mh.clock_probe(mh.FakeStatsLink("real"), str(tmp_path / "r"), sleep=lambda s: None, out=said.append)
    assert ok and rep["source"] == "real" and abs(rep["real_per_s"] - 48) < 2 and rep["rt_fa"] == 1 and \
        rep["rt_fc"] == 1, rep
    play = next(i for i, s in enumerate(said) if "PRESS PLAY NOW" in s)
    stop = next(i for i, s in enumerate(said) if "PRESS STOP NOW" in s)
    assert play < stop and "stopped" in said[stop + 1], said
    assert (tmp_path / "r" / "stats.log").exists() and (tmp_path / "r" / "report.json").exists()
    ok, rep = mh.clock_probe(mh.FakeStatsLink("late"), str(tmp_path / "l"), sleep=lambda s: None, out=lambda s: None)
    assert ok and rep["f8_synth"] > 0 and rep["made_after_3s"] == 0, rep
    ok, rep = mh.clock_probe(mh.FakeStatsLink("fallback", bpm=97.0), str(tmp_path / "f"), sleep=lambda s: None,
                             out=lambda s: None)
    assert not ok and rep["source"] == "fallback" and "mb_clock_block" in rep["verdict"], rep
    assert abs(rep["made_per_s"] - 97 * 24 / 60) < 1, rep
    link = mh.FakeStatsLink("none")
    ok, rep = mh.clock_probe(link, str(tmp_path / "n"), play_wait=20, sleep=lambda s: None, out=lambda s: None)
    assert not ok and "never ran" in rep["verdict"] and link.started and link.stopped, rep
    ok, rep = mh.clock_verdict([])
    assert not ok and "older build" in rep["verdict"]


def test_parse_stats():
    rows = mh.parse_stats("ms=5 rt_f8=3 host_bpm10=1200 bpm10=1200\ngarbage\nms=6 rt_f8=x\n")
    assert rows == [{"ms": 5, "rt_f8": 3, "host_bpm10": 1200, "bpm10": 1200}]


def test_stats_link_reads_the_instance_folder():
    """under the loader the plugin writes stats.log in its instance's folder (inst/<k>/): the probe reads and removes
    every instance's (and the module folder's, an older build's) and keeps the longest; it read the module folder's
    only, and found nothing (2026-10-30)"""
    link = object.__new__(mh.StatsLink)
    cmds = []
    files = {mi.INST + "/0/stats.log": "ms=5 rt_f8=1\n", mi.INST + "/1/stats.log": "ms=5 rt_f8=1\nms=6 rt_f8=2\n"}

    def ssh(cmd):
        cmds.append(cmd)
        return "".join("== %s\n%s" % kv for kv in files.items()) if cmd.startswith("for f in") else ""
    link._ssh = ssh
    rows = link.read()
    assert rows == [{"ms": 5, "rt_f8": 1}, {"ms": 6, "rt_f8": 2}], rows
    assert mi.INST + "/*/stats.log" in cmds[0] and link.path == mh.STATS_PATHS
    assert mh.pick_stats("") == [] and mh.pick_stats("== a\ngarbage\n") == []


def test_clock_probe_auto_presses(tmp_path):
    """--auto: the tool presses Play, then Stop, itself (once each, at the prompts)"""
    link = mh.FakeStatsLink("real")
    ok, rep = mh.clock_probe(link, str(tmp_path), sleep=lambda s: None, out=lambda s: None, press=link.press)
    assert ok and link.pressed == ["play", "stop"], (link.pressed, rep)
    r = run("clock-probe", "--fake", "--auto", "--out", str(tmp_path / "cli"))
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout[-2000:]


def test_clock_probe_cli_fake(tmp_path):
    r = run("clock-probe", "--fake", "--inject", "fallback", "--out", str(tmp_path))
    assert r.returncode == 1 and "PRESS PLAY NOW" in r.stdout and "mb_clock_block" in r.stdout, r.stdout[-2000:]
    r = run("clock-probe", "--fake", "--out", str(tmp_path / "ok"))
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout[-2000:]
