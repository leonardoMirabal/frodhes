# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC port (docs/MPC.md, make mpc): the built plugin folder validated by the SDK's validate.py, the SDK's
host simulator on this machine's build, a note on every engine, the sustain pedal, programs (the host's program
select, a MIDI program change and the PRESET parameter load the same preset), the state round trip, the MPC
transport's tempo reaching the synced effects, the sound byte for byte the desktop plugin's and the host build's
(mpc/tests/bitexact.py --quick), a short soak (mpc/tests/soak.py: one 5-minute segment), and the ARM builds on
Linux in Docker. Every test skips when its part is not built (the MPC port needs zig and the SDK: make mpc; the
comparisons the desktop plugin too: make plugin, and the 24-voice MPC build: make test-mpc builds it)."""
import hashlib
import os
import shutil
import subprocess
import sys
import wave

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MPC = os.path.join(ROOT, "build", "mpc")
EXT = "dylib" if sys.platform == "darwin" else "so"
LIB = os.path.join(MPC, "native", "frodhes." + EXT)
LIB24 = os.path.join(ROOT, "build", "mpc24", "native", "frodhes." + EXT)
RENDER = os.path.join(MPC, "native", "vst2_render")
HOST_SIM = os.path.join(MPC, "native", "host_sim")
FOLDER = os.path.join(MPC, "Frodhes - VST - Frodhes")
SDK = os.environ.get("MPC_SDK") or os.path.join(ROOT, ".local", "mpc-sdk")
CLAP_RENDER = os.path.join(ROOT, "build", "plugin", "frp_clap_render")
CLAP = os.path.join(ROOT, "build", "plugin", "bundles", "Frodhes.clap")

built = pytest.mark.skipif(not (os.path.exists(LIB) and os.path.exists(RENDER)),
                           reason="the MPC port is not built (make mpc: zig and the MPC Plugin SDK)")
desktop = pytest.mark.skipif(not (os.path.exists(CLAP_RENDER) and os.path.exists(CLAP) and os.path.exists(LIB24)),
                             reason="the desktop plugin (make plugin) or the 24-voice MPC build (make test-mpc) missing")


def env(tmp_path, test=None):
    e = {k: v for k, v in os.environ.items() if k != "FRODHES_PLUGIN_TEST"}
    e["FRODHES_PRESET_DIR"] = str(tmp_path / "presets")      # (never the machine's own user presets)
    if test:
        e["FRODHES_PLUGIN_TEST"] = test
    return e


def render(tmp_path, name, text, *args, lib=LIB, tool=RENDER, test=None):
    s = tmp_path / (name + ".txt")
    s.write_text(text)
    w = tmp_path / (name + ".wav")
    r = subprocess.run([tool, lib, str(s), str(w), *map(str, args)], capture_output=True, text=True,
                       env=env(tmp_path, test), cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    return w, r.stdout


def pcm(w):
    import array
    with wave.open(str(w)) as f:
        a = array.array("h")
        a.frombytes(f.readframes(f.getnframes()))
    return a


def peak(w, t0=0.0, t1=None):
    a = pcm(w)
    a = a[int(t0 * 44100) * 2:(int(t1 * 44100) * 2 if t1 else None)]
    return max((abs(x) for x in a), default=0)


def sha(w):
    return hashlib.sha256(open(w, "rb").read()).hexdigest()


@pytest.mark.skipif(not os.path.isdir(FOLDER) or not os.path.isfile(os.path.join(SDK, "tools", "validate.py")),
                    reason="the plugin folder or the SDK is missing (make mpc)")
def test_folder_validates():
    r = subprocess.run([sys.executable, os.path.join(SDK, "tools", "validate.py"), FOLDER], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "0 errors, 0 warnings" in r.stdout, r.stdout[-3000:]
    for so in ("frodhes-arm32.so", "frodhes-arm64.so"):
        assert os.path.getsize(os.path.join(FOLDER, "VST", so)) > 1 << 20


@built
@pytest.mark.skipif(not os.path.exists(HOST_SIM), reason="the host simulator is not built (make mpc)")
def test_host_sim(tmp_path):
    r = subprocess.run([HOST_SIM, LIB, "test", "--wav", str(tmp_path / "t.wav")], capture_output=True, text=True,
                       env=env(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-4000:]
    assert "0 warnings, 0 failures" in r.stdout, r.stdout[-4000:]
    assert "re-saved chunk is byte-identical" in r.stdout


@built
@pytest.mark.parametrize("cc", ["80 0", "80 32", "80 64", "80 96", "79 0", "79 127"],
                         ids=["stage77", "suitcase", "felt", "dyno", "vibes", "marimba"])
def test_every_engine_sounds_and_sustains(tmp_path, cc):
    """a note on each TYPE sounds; the pedal holds it past its key-up, and without the pedal it dies away (the\n    marimba: sound only)"""
    base = f"0 midi 0xB0 {cc}\n0 midi 0xB0 91 0\n0 midi 0xB0 88 0\n"
    w1, _ = render(tmp_path, "ped", base + "100 midi 0xB0 64 127\n200 midi 0x90 60 110\n400 midi 0x80 60 0\n3000 end\n")
    w2, _ = render(tmp_path, "dry", base + "200 midi 0x90 60 110\n400 midi 0x80 60 0\n3000 end\n")
    assert peak(w1, 0.2, 0.5) > 1000
    if cc != "79 127":            # (a marimba has no damper: the pedal changes nothing, its bars ring out in ~1 s)
        assert peak(w1, 2.0, 2.5) > 4 * peak(w2, 2.0, 2.5)


@built
def test_programs(tmp_path):
    """the host's program select, a MIDI program change and the PRESET parameter all load F06 (the same sound once
    the change is through) and the host shows it; the programs are the 57 factory presets"""
    pre, post = "0 midi 0x90 60 100\n", "400 midi 0x90 64 100\n1500 end\n"
    w1, o1 = render(tmp_path, "prog", pre + "300 program 5\n" + post)
    w2, o2 = render(tmp_path, "pc", pre + "300 midi 0xC0 5 0\n" + post)
    w3, o3 = render(tmp_path, "par", pre + "300 param 0x10002 5\n" + post)
    w0, o0 = render(tmp_path, "none", pre + post)
    assert 'program 5 "F06 PUNCH 77", Preset parameter "F06 PUNCH 77"' in o1
    assert 'Preset parameter "F06 PUNCH 77"' in o2 and 'Preset parameter "F06 PUNCH 77"' in o3
    assert 'Preset parameter "F01 STAGE 77"' in o0
    a1, a2, a3, a0 = pcm(w1), pcm(w2), pcm(w3), pcm(w0)
    t = int(0.45 * 44100) * 2
    assert a1[t:] == a2[t:] == a3[t:]
    assert a1[t:] != a0[t:]
    if os.path.exists(HOST_SIM):
        import json
        r = subprocess.run([HOST_SIM, LIB, "dump"], capture_output=True, text=True, env=env(tmp_path), cwd=tmp_path)
        d = json.loads(r.stdout[r.stdout.index("{"):])
        progs = d["programs"]
        assert len(progs) == 57 and progs[0] == "F01 STAGE 77" and progs[56].startswith("F57"), progs
        assert len(d["params"]) == 72


@built
def test_state_round_trip(tmp_path):
    """the chunk loaded over an instance in another state and saved again: the same bytes and values; two
    instances loaded with it play the same samples"""
    _, out = render(tmp_path, "st", "0 midi 0xB0 91 80\n0 midi 0xB0 18 90\n0 midi 0xB0 17 127\n0 param 0x10002 7\n"
                    "100 midi 0xB0 74 40\n200 midi 0x90 60 100\n800 midi 0x80 60 0\n1500 end\n", "--state-check", 1)
    assert "ok    state round trip" in out and "identical, 0 parameter values differ" in out, out


def echo_lag(w, want):
    """the ms from the note's onset to its echo's (tools/gate_d.py core tempo: 1 ms frames, the first over 0.2 of
    the peak)"""
    a = pcm(w)
    e = [sum(abs(a[2 * k] + a[2 * k + 1]) for k in range(i, i + 44)) for i in range(0, len(a) // 2 - 44, 44)]
    on = next(i for i, v in enumerate(e) if v > 0.2 * max(e))
    seg = e[on + int(want * 0.6):on + int(want * 1.4)]
    return int(want * 0.6) + next(i for i, v in enumerate(seg) if v > 0.2 * max(seg))


@built
@pytest.mark.parametrize("bpm", [100, 120, 150])
def test_transport_tempo(tmp_path, bpm):
    """a DIG delay synced to 1/4 (Gate D's core tempo script): its echo a quarter note at the MPC transport's tempo
    after the note; and the desktop plugin at that tempo renders the same"""
    s = ("0 midi 0xB0 88 40\n0 midi 0xB0 89 64\n0 midi 0xB0 26 0\n0 midi 0xB0 25 83\n0 midi 0xB0 91 0\n"
         "0 midi 0xB0 106 0\n300 midi 0x90 72 127\n330 midi 0x80 72 0\n3000 end\n")
    w, _ = render(tmp_path, f"t{bpm}", s, "--tempo", bpm)
    want = 60000 / bpm
    assert abs(echo_lag(w, want) - want) <= 2
    if os.path.exists(CLAP_RENDER) and os.path.exists(LIB24):
        m, _ = render(tmp_path, f"m{bpm}", s, "--tempo", bpm, lib=LIB24)
        c = tmp_path / f"c{bpm}.wav"
        r = subprocess.run([CLAP_RENDER, CLAP, str(tmp_path / f"m{bpm}.txt"), str(c), "--engine", "hq", "--tempo",
                            str(bpm)], capture_output=True, text=True, env=env(tmp_path), cwd=tmp_path)
        assert r.returncode == 0 and sha(c) == sha(m)


@built
@desktop
def test_bit_exact_quick():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mpc", "tests", "bitexact.py"), "--quick", "--only",
                        "ms,exact,blocks", "--jobs", "2"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-1000:]


def docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@built
@desktop
def test_soak_segment():
    """one 5-minute soak segment (mpc/tests/soak.py), with Docker also on the shipped arm64 and arm32 .so"""
    arm = all(os.path.exists(os.path.join(MPC, "stage", a, "vst2_render")) for a in ("arm32", "arm64")) and docker_ok()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mpc", "tests", "soak.py"), "--minutes", "5", "--seed", "3",
                        "--jobs", "1", "--arm", "1" if arm else "0"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-1000:]


@built
@desktop
@pytest.mark.parametrize("arch", ["arm64", "arm32"])
def test_arm_linux_bit_exact(arch):
    """the shipped .so on Linux (Docker; arm32 under QEMU): the host build's sound, byte for byte, and the coroutine"""
    tool = os.path.join(MPC, "stage", arch, "vst2_render")
    if not os.path.exists(tool) or not os.path.isdir(FOLDER):
        pytest.skip(f"the {arch} test tools are not built (python3 mpc/build.py --tools)")
    if not docker_ok():
        pytest.skip("no Docker")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mpc", "tests", "bitexact.py"), "--only", arch, "--arm", "2"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and f"ok    {arch}" in r.stdout, r.stdout[-3000:] + r.stderr[-1000:]
    r = subprocess.run(["sh", os.path.join(ROOT, "mpc", "tests", "arm_run.sh"), arch,
                        os.path.join(MPC, "stage", arch, "test_coro")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
