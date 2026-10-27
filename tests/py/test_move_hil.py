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
