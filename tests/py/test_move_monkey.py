"""The plugin monkey (M1-12): tools/monkey.py's seeded streams through the Move plugin's own entry points.

    pytest tests/py/test_move_monkey.py                 a few seeds (MOVE_MONKEY_SEEDS, default 4)
    python3 tests/py/test_move_monkey.py --minutes 30   a seeded run for N minutes (CI 10, nightly 60)
    python3 tests/py/test_move_monkey.py --seeds 7-9    given seeds

Each seed: monkey.gen()'s MIDI (notes on any channel, lost note-offs, the pedal, bend, every CC, program changes,
MIDI clock and transport, garbage statuses), plus what only the Move sends: parameter writes through set_param
(every chain_params key, any value), preset browses, state reads and loads, and TYPE changes, preset loads and
state loads under a held, pedalled chord. Then monkey.panic()'s MIDI end (every note off on all 16 channels, the
pedal up, arp, chord, sequencer, delay and reverb off, then the probe note), with the Move-only latches let go
first (SUS, the MIDI channel, the sequencer). It runs in build/move/t/test_bridge "run" under Gate P's guards (a
syscall in a callback kills it, an allocation fails it). The checks are monkey.py's: no crash, the limiter's
ceiling, no rail, exact silence 10-12 s after the panic (no stuck note, tail or DC), the probe note sounds and is
never louder after its key-up than held (+1 dB). A failing seed keeps its script and WAV in build/move/monkey/.
"""
import argparse
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import wave

import re

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import monkey  # noqa: E402

OUT = os.path.join(ROOT, "build", "move", "monkey")
# the limiter's ceiling as the Move plays it: the plugin's output carries the bridge's trim (move_bridge.h MB_TRIM_Q12,
# +2.2 dB, rounded as mb_trimmed rounds), so LIM_T 24000 comes out at 30932 (-0.5 dBFS) and the check is against that
TRIM_Q12 = int(re.search(r"#define MB_TRIM_Q12 (\d+)", open(os.path.join(ROOT, "move", "move_bridge.h")).read()).group(1))
LIM_OUT = (monkey.LIM_T * TRIM_Q12 + 2048) >> 12
SO = "build/move/monkey/so"                 # its own plugin build: never the shipping build/move/frodhes/ (a device
DSP = SO + "/dsp.so"                        # install may be reading that one)
BRIDGE = "build/move/monkey/test_bridge"
_built = []
CHAIN = [p for p in json.load(open(os.path.join(ROOT, "move", "module", "chain_params.json")))
         if p.get("access") != "read"]          # (the GR meter's cells: nothing to set)
RESETS = [("s_sustain", "0"), ("s_mch", "0")]


def wire(rng, p):
    if p["type"] == "enum":
        return str(rng.randrange(len(p["options"])))
    if p["type"] == "float":
        return "%.1f" % rng.uniform(p["min"], p["max"])
    lo, hi = p["min"], p["max"]
    return str(rng.choice([lo, hi, rng.randint(lo, hi)]))


def gen(seed):
    """(script text, the panic's marks)"""
    ev = [(t, ln) for t, ln in monkey.gen(seed) if ln.startswith("midi ")]
    rng = random.Random(seed * 7919 + 17)
    types = next(p for p in CHAIN if p["key"] == "p_type")
    t = 60
    while t < monkey.RAND_MS - 500:
        k = rng.random()
        if k < 0.55:
            p = rng.choice(CHAIN)
            ev.append((t, f"key {p['key']} {wire(rng, p)}"))
        elif k < 0.65:
            ev.append((t, f"preset {rng.randrange(35)}"))
        elif k < 0.73:
            ev.append((t, "getstate"))
        elif k < 0.80:
            ev.append((t, "setstate"))
        else:                                   # under a held, pedalled chord: TYPE, a preset, a state load
            c = rng.randrange(16) if rng.random() < 0.2 else 0
            ev.append((t, f"midi 0x{0xB0 | c:02X} 64 127"))
            notes = [48 + rng.randrange(12) + d for d in (0, 4, 7, 12)]
            ev += [(t + 1, f"midi 0x{0x90 | c:02X} {n} {rng.randint(60, 127)}") for n in notes]
            tt = t + rng.randint(80, 300)
            for _ in range(rng.randint(2, 5)):
                r = rng.random()
                if r < 0.6:
                    ev.append((tt, f"key p_type {wire(rng, types)}"))
                elif r < 0.8:
                    ev.append((tt, f"preset {rng.randrange(35)}"))
                else:
                    ev.append((tt, "setstate"))
                tt += rng.randint(20, 250)
            ev += [(tt, f"midi 0x{0x80 | c:02X} {n} 0") for n in notes if rng.random() < 0.9]
            if rng.random() < 0.7:
                ev.append((tt + rng.randint(0, 400), f"midi 0x{0xB0 | c:02X} 64 0"))
        t += rng.randint(30, 400)
    t0 = monkey.RAND_MS + 50
    pl, mk = monkey.panic(t0)
    pl = [(tt, ln) for tt, ln in pl if ln.startswith("midi ")]
    pl += [(t0 - 30, f"key {k} {v}") for k, v in RESETS]
    ev = [(min(tt, monkey.RAND_MS), ln) for tt, ln in ev] + pl
    lines = [f"{tt} {ln}" for tt, ln in sorted(ev, key=lambda e: e[0])]
    return "\n".join(lines + [f"{mk['end']} end"]) + "\n", mk


def runner():
    """the command prefix for a shell line: natively on Linux arm64, else in the Move image; None if neither"""
    if platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64"):
        return ["sh", "-c"]
    if shutil.which("docker") and subprocess.run(["docker", "image", "ls", "-q", "frodhes-move:bookworm"],
                                                 capture_output=True, text=True).stdout.strip():
        return ["docker", "run", "--rm", "--cpus=2", "--platform", "linux/arm64", "-v", ROOT + ":/src", "-w", "/src",
                "frodhes-move:bookworm", "sh", "-c"]
    return None


def run_seeds(seeds, jobs=2):
    """{seed: (exit code, output, samples (n, 2) int32, marks)}"""
    pre = runner()
    if pre is None:
        return None
    os.makedirs(OUT, exist_ok=True)
    marks = {}
    for s in seeds:
        text, marks[s] = gen(s)
        with open(os.path.join(OUT, f"seed-{s}.txt"), "w") as f:
            f.write(text)
    rel = os.path.relpath(OUT, ROOT)
    build = "" if _built else (f"make -s move-native MOVE_OUT={SO} >/dev/null 2>&1 && cc -O1 -g -std=gnu11 -Wall "
                               f"-o {BRIDGE} move/tests/test_bridge.c -ldl || exit 9; ")
    inner = (build +
             f"printf '%s\\n' {' '.join(str(s) for s in seeds)} | xargs -P {jobs} -I@ sh -c "
             f"'{BRIDGE} run {DSP} {rel}/seed-@.txt {rel}/seed-@.wav > {rel}/seed-@.log 2>&1; "
             f"echo $? > {rel}/seed-@.code'; rm -rf build/move/t/run.*")
    r = subprocess.run(pre + [inner], cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 9:
        raise RuntimeError("the plugin build failed: " + r.stdout[-1000:] + r.stderr[-1000:])
    _built.append(1)
    res = {}
    for s in seeds:
        base = os.path.join(OUT, f"seed-{s}")
        code = int(open(base + ".code").read().strip() or 99) if os.path.exists(base + ".code") else 99
        out = open(base + ".log").read() if os.path.exists(base + ".log") else ""
        x = np.zeros((0, 2), np.int32)
        if os.path.exists(base + ".wav"):
            with wave.open(base + ".wav") as w:
                x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.int32)
        res[s] = (code, out, x, marks[s])
    return res


def analyse(code, out, x, mk):
    """monkey.py's invariants that hold on the plugin path: a list of (check, message)"""
    if code != 0 or "FAIL" in out:
        return [("crash", f"exit {code}: {out[-400:]}")]
    if not len(x):
        return [("crash", "no sound file")]
    bad, ms = [], monkey.ms
    q0, q1 = ms(mk["quiet"][0]), ms(mk["quiet"][1])
    a = np.abs(x).max(1)
    if a[:q0].max() > LIM_OUT:
        bad.append(("peak", f"{a[:q0].max()} at {np.argmax(a[:q0]) / monkey.FS:.3f} s"))
    for c in (0, 1):
        y = x[:, c]
        same = np.concatenate([[False], np.diff(y) == 0]) & (np.abs(y) > 20000)
        if same.any():
            runs = np.diff(np.flatnonzero(np.diff(np.concatenate([[0], same.astype(int), [0]]))))[::2]
            if runs.max() >= 32:
                bad.append(("rail", f"{runs.max()} equal samples over 20000 (channel {c})"))
                break
    if np.abs(x[q0:q1]).max() > 0:
        bad.append(("silence", f"not silent 10-12 s after the panic: peak {np.abs(x[q0:q1]).max()}"))
    n0, rel = mk["note"]
    held_db = monkey.rms_db(x[ms(rel - 60):ms(rel - 10)])
    onset_db = max(monkey.rms_db(x[ms(n0 + k):ms(n0 + k + 20)]) for k in range(0, 200, 10))
    if onset_db < -40:
        bad.append(("probe", f"the probe note after the panic: {onset_db:.1f} dBFS at its onset (dead?)"))
    elif held_db > -70:
        after = max(monkey.rms_db(x[ms(rel + k):ms(rel + k + 10)]) for k in range(0, 300, 5))
        if after > held_db + 1.0:
            bad.append(("probe", f"louder after its key-up: {after:.1f} dBFS against {held_db:.1f} held"))
    return bad


def check_seeds(seeds, jobs=2):
    res = run_seeds(seeds, jobs)
    if res is None:
        return None
    fails = {}
    for s, (code, out, x, mk) in res.items():
        bad = analyse(code, out, x, mk)
        base = os.path.join(OUT, f"seed-{s}")
        if bad:
            fails[s] = bad
        else:
            for ext in (".txt", ".wav", ".log", ".code"):
                if os.path.exists(base + ext):
                    os.remove(base + ext)
    return fails


def test_gen_is_plugin_only():
    """every line is one the run mode knows; the panic's Move resets come before its MIDI"""
    text, mk = gen(3)
    kinds = {ln.split()[1] for ln in text.splitlines()}
    assert kinds <= {"midi", "key", "preset", "getstate", "setstate", "end"}, kinds
    assert "key p_type" in text and "setstate" in text and "preset" in text
    assert text.splitlines()[-1] == f"{mk['end']} end"


def test_analyse_catches(tmp_path):
    """the checks fail on a stuck tone after the panic and on a probe louder after its key-up"""
    _, mk = gen(1)
    n = monkey.ms(mk["end"])
    x = np.zeros((n, 2), np.int32)
    n0, rel = mk["note"]
    x[monkey.ms(n0):monkey.ms(rel), :] = 3000
    assert analyse(0, "ok", x, mk) == []
    y = x.copy()
    y[monkey.ms(mk["quiet"][0]) + 5, 0] = 1
    assert [b[0] for b in analyse(0, "ok", y, mk)] == ["silence"]
    y = x.copy()
    y[monkey.ms(rel + 20):monkey.ms(rel + 40), :] = 9000
    assert [b[0] for b in analyse(0, "ok", y, mk)] == ["probe"]
    assert analyse(1, "FAIL", x, mk)[0][0] == "crash"


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "move", "move_plugin.c")), reason="needs the plugin")
def test_plugin_monkey_seeds():
    n = int(os.environ.get("MOVE_MONKEY_SEEDS", "4"))
    fails = check_seeds(list(range(1, n + 1)), jobs=int(os.environ.get("PYTEST_N", "2")))
    if fails is None:
        pytest.skip("no Linux arm64 and no frodhes-move:bookworm image (make move-image)")
    assert not fails, f"seeds failing (scripts in {os.path.relpath(OUT, ROOT)}): {fails}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=0)
    ap.add_argument("--seeds", default="1-4")
    ap.add_argument("-j", type=int, default=2)
    a = ap.parse_args()
    lo, _, hi = a.seeds.partition("-")
    seed, last = int(lo), int(hi or lo)
    t_end = time.time() + a.minutes * 60
    done, bad = 0, {}
    while True:
        batch = list(range(seed, seed + 16 * a.j)) if a.minutes else list(range(seed, last + 1))
        f = check_seeds(batch, a.j)
        if f is None:
            print("no Linux arm64 and no frodhes-move:bookworm image")
            return 2
        bad.update(f)
        done += len(batch)
        seed = batch[-1] + 1
        for s, b in f.items():
            print(f"FAIL seed {s}: {b}")
        if not a.minutes or time.time() >= t_end:
            break
    print(f"plugin monkey: {done} seeds (up to {seed - 1}), {len(bad)} failing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
