#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC port's seeded soak (docs/MPC.md): a random stream of what an MPC sends a plugin, rendered for a
given length of audio (30 minutes by default) in segments of 5 minutes, each with its own seed and tempo.

The stream: notes on any key at any velocity (runs, chords, fast repeats, lost note-offs), the sustain pedal (half
too), pitch bend, the mod wheel, every CC the app knows (its parameters' FM-1 CCs, at random values), program
changes, and the host's parameter writes (every one of the 72 at random values: the TYPE, PRESET, SUSTAIN, EFFECTS
BYPASS, TUNE included). Each segment ends with a panic (all notes off, the pedal and SUSTAIN up, the delay and the
reverb's mix at 0) and 15 s of nothing.

Per segment, three renders and their checks:
  monkey  the MPC build as it ships (16 voices), random block sizes (1..2048 frames a call), the host's
          parameter-0 nudge, the transport's tempo: no crash, every sample finite (vst2_render), exact silence in the
          last 5 s (no stuck voice, tail, DC or denormal residue), no run of 32 equal samples above 20000 (an output
          stuck on a rail), and the time: the instance's share of real time under 10 % here and no call over its
          block's time (a denormal stall shows as both)
  desktop the same stream through the MPC build at 24 voices (vst2_render --split: a block ends at each
          parameter write, as the desktop host's sample-accurate parameter event) and through the desktop plugin
          (clap_render), 128-frame blocks, the tempo: the same WAV, byte for byte. Without the host's writes of
          TYPE (the MPC takes one a block late, past the host's nudge) and PRESET (a VST 2.4 parameter is a value
          the plugin reads, not an event: a write of the preset it already has changes nothing on the MPC, where
          the desktop plugin loads that preset again, its edits gone); both come through their MIDI there
  host    the stream's MIDI only (no parameter writes) through the MPC build (16 voices) in its host-time test mode
          (FRODHES_PLUGIN_TEST=ms), random blocks, against the host build (tests/host.c, MOVE_DEFS): the same WAV
          (so every click or artefact there is the firmware's own, which tools/monkey.py hunts)

  --arm N  the first N segments' monkey render also on the shipped arm64 and arm32 .so (Linux in Docker; arm32
           under QEMU): the same WAV, byte for byte

  python3 mpc/tests/soak.py [--minutes 30] [--seed 1] [--jobs 2] [--arm N]
Exit 0 all checks pass, 1 a failure (named, the segment's script kept in build/mpc/soak), 2 a part missing.
"""
import argparse
import hashlib
import os
import random
import re
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bitexact  # noqa: E402

OUT = ROOT / "build" / "mpc" / "soak"
SEG_MS = 5 * 60 * 1000
ID_TYPE, ID_PRESET, ID_SUSTAIN = 0, 0x10002, 0x10003


def params():
    """[(id, hi, cc)] of plugin/gen/params_map.h FRP_PARAMS"""
    src = (ROOT / "plugin" / "gen" / "params_map.h").read_text()
    body = src[src.index("FRP_PARAMS[FRP_NPARAM] = {"):]
    return [(int(m.group(1), 0), int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"\{(0x[0-9a-fA-F]+|\d+)u, \"[^\"]*\", \"[^\"]*\", (\d+)u, \d+u, (\d+)u,", body)]


def gen(seed, ms_len):
    """the segment's lines: (ms, text)"""
    rnd = random.Random(seed)
    ps = params()
    ccs = sorted({cc for _, _, cc in ps if cc})
    ev, held, t = [], set(), 100
    while t < ms_len - 20000:
        a = rnd.random()
        if a < 0.36:                                   # a note (sometimes a run of fast repeats)
            for _ in range(rnd.choice((1, 1, 1, 2, 4))):
                k = rnd.randint(21, 108)
                ev.append((t, f"midi 0x90 {k} {rnd.randint(1, 127)}"))
                held.add(k)
                t += rnd.randint(0, 40)
        elif a < 0.42:                                 # a chord
            root = rnd.randint(28, 84)
            for k in (root, root + 4, root + 7, root + rnd.choice((10, 11, 12)), root - 12):
                if 21 <= k <= 108:
                    ev.append((t, f"midi 0x90 {k} {rnd.randint(40, 127)}"))
                    held.add(k)
        elif a < 0.66 and held:                        # a note-off (one in 20 lost)
            k = rnd.choice(sorted(held))
            held.discard(k)
            if rnd.random() > 0.05:
                ev.append((t, f"midi 0x80 {k} {rnd.randint(0, 127)}"))
        elif a < 0.71:
            ev.append((t, f"midi 0xB0 64 {rnd.choice((0, 0, 64, 127, 127))}"))
        elif a < 0.75:
            v = rnd.randint(0, 16383)
            ev.append((t, f"midi 0xE0 {v & 127} {v >> 7}"))
        elif a < 0.77:
            ev.append((t, f"midi 0xB0 1 {rnd.randint(0, 127)}"))
        elif a < 0.87:
            ev.append((t, f"midi 0xB0 {rnd.choice(ccs)} {rnd.randint(0, 127)}"))
        elif a < 0.98:
            pid, hi, _ = rnd.choice(ps)
            ev.append((t, f"param {pid:#x} {rnd.randint(0, hi)}"))
        else:
            ev.append((t, f"midi 0xC0 {rnd.randint(0, 31)} 0"))
        t += int(rnd.expovariate(1 / 140.0)) + 1
    t = max(t, ms_len - 20000)                          # the panic, then 15 s of nothing
    ev += [(t, "midi 0xB0 64 0"), (t, f"param {ID_SUSTAIN:#x} 0"), (t, "midi 0xB0 123 0"), (t, "midi 0xB0 91 0"),
           (t, "midi 0xB0 88 0"), (t, "midi 0xE0 0 64")]
    ev += [(t + 5, f"midi 0x80 {k} 0") for k in range(21, 109)]
    ev.append((ms_len, "end"))
    return ev


def text(ev, keep=lambda line: True):
    return "".join(f"{ms} {line}\n" for ms, line in ev if keep(line))


def pcm(path):
    import array
    with wave.open(str(path)) as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    return a


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else "MISSING"


def segment(i, seed, h16, empty, arm=()):
    fails, info = [], {}
    rnd = random.Random(seed * 7919 + 13)
    tempo = rnd.choice((60, 90, 120, 128, 140, 174, 200))
    ev = gen(seed, SEG_MS)
    d = OUT / f"seg{i:02d}"
    d.mkdir(parents=True, exist_ok=True)
    s_all, s_desk, s_midi = d / "all.txt", d / "desktop.txt", d / "midi.txt"
    s_all.write_text(text(ev))
    s_desk.write_text(text(ev, lambda ln: not ln.startswith((f"param {ID_TYPE:#x} ", f"param {ID_PRESET:#x} "))))
    s_midi.write_text(text(ev, lambda ln: not ln.startswith("param")))
    env = bitexact.env_for(None)
    env["FRODHES_PRESET_DIR"] = str(empty)
    # monkey
    w = d / "monkey.wav"
    r = subprocess.run(["nice", "-n", "15", str(bitexact.VST2_RENDER), str(bitexact.MPC16), str(s_all), str(w),
                        "--block", f"random:{seed}", "--nudge", "1", "--tempo", str(tempo), "--bench", "1"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    m = re.search(r"([\d.]+) of real time; the worst call (\d+) us for (\d+) frames", r.stdout)
    if r.returncode or not m:
        fails.append(f"monkey: {(r.stdout + r.stderr).strip()[-300:]}")
    else:
        share, worst, wn = float(m.group(1)), int(m.group(2)), int(m.group(3))
        info.update(share=share, worst=worst, worst_frames=wn)
        a = pcm(w)
        tail = a[-2 * 44100 * 5:]
        nz = sum(1 for x in tail if x)
        if nz:
            fails.append(f"monkey: the last 5 s not silent ({nz} samples, peak {max(abs(x) for x in tail)})")
        run, last, rail = 0, None, 0
        for x in a[0::2]:
            run = run + 1 if x == last and abs(x) > 20000 else 0
            last = x
            rail = max(rail, run)
        if rail >= 32:
            fails.append(f"monkey: {rail + 1} equal samples above 20000 (an output on a rail)")
        if share > 0.10:
            fails.append(f"monkey: {share * 100:.1f} % of real time (over 10 %: a stall?)")
        if worst * 1e-6 > wn / 44100.0:
            fails.append(f"monkey: a call took {worst} us for {wn} frames (over its time)")
        info["peak"] = max(abs(x) for x in a)
        for arch in arm:   # the shipped .so on Linux (Docker; arm32 under QEMU), the same render: the same WAV
            tool = ROOT / "build" / "mpc" / "stage" / arch / "vst2_render"
            wa = d / f"monkey-{arch}.wav"
            ra = subprocess.run(["nice", "-n", "15", "sh", str(ROOT / "mpc/tests/arm_run.sh"), arch, "env",
                                 f"FRODHES_PRESET_DIR={empty}", str(tool), str(bitexact.SO[arch]), str(s_all), str(wa),
                                 "--block", f"random:{seed}", "--nudge", "1", "--tempo", str(tempo)],
                                cwd=ROOT, capture_output=True, text=True)
            if ra.returncode or sha(wa) != sha(w):
                fails.append(f"{arch}: {sha(wa)[:12]} (exit {ra.returncode}) against {sha(w)[:12]} here "
                             f"{(ra.stdout + ra.stderr).strip()[-200:]}")
            else:
                wa.unlink()
                info.setdefault("arm", []).append(arch)
        w.unlink()
    # desktop
    wc, wm = d / "desk-clap.wav", d / "desk-mpc24.wav"
    rc = subprocess.run(["nice", "-n", "15", str(bitexact.CLAP_RENDER), str(bitexact.CLAP), str(s_desk), str(wc),
                         "--engine", "hq", "--block", "128", "--tempo", str(tempo)], cwd=ROOT, capture_output=True,
                        text=True, env=env)
    rm = subprocess.run(["nice", "-n", "15", str(bitexact.VST2_RENDER), str(bitexact.MPC24), str(s_desk), str(wm),
                         "--block", "128", "--split", "1", "--tempo", str(tempo)], cwd=ROOT, capture_output=True,
                        text=True, env=env)
    if rc.returncode or rm.returncode or sha(wc) != sha(wm):
        fails.append(f"desktop: clap {sha(wc)[:12]} (exit {rc.returncode}), mpc24 {sha(wm)[:12]} (exit {rm.returncode})")
    else:
        wc.unlink(); wm.unlink()
    # host
    wh = bitexact.host_render(h16, f"soak-seg{i:02d}", s_midi)
    wp = d / "host-mpc16.wav"
    env_ms = bitexact.env_for("ms")
    env_ms["FRODHES_PRESET_DIR"] = str(empty)
    rp = subprocess.run(["nice", "-n", "15", str(bitexact.VST2_RENDER), str(bitexact.MPC16), str(s_midi), str(wp),
                         "--block", f"random:{seed + 1}"], cwd=ROOT, capture_output=True, text=True, env=env_ms)
    if rp.returncode or sha(wh) != sha(wp):
        fails.append(f"host: host build {sha(wh)[:12]}, mpc16 {sha(wp)[:12]} (exit {rp.returncode})")
    else:
        wp.unlink(); Path(wh).unlink()
    if not fails:
        for f in (s_all, s_desk, s_midi):
            f.unlink()
        d.rmdir()
    return i, seed, tempo, len(ev), info, fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--arm", type=int, default=0, help="segments also run on arm32 and arm64 in Docker (0)")
    a = ap.parse_args()
    arm = [x for x in ("arm64", "arm32") if (ROOT / "build/mpc/stage" / x / "vst2_render").exists() and
           bitexact.SO[x].exists()] if a.arm else []
    if a.arm and len(arm) < 2:
        print("soak: --arm wants the ARM test tools (python3 mpc/build.py --tools)")
        return 2
    missing = [str(p.relative_to(ROOT)) for p in (bitexact.CLAP_RENDER, bitexact.CLAP, bitexact.MPC16, bitexact.MPC24,
                                                   bitexact.VST2_RENDER) if not p.exists()]
    if missing:
        print("soak: missing " + ", ".join(missing))
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    empty = OUT / "presets-empty"
    empty.mkdir(parents=True, exist_ok=True)
    for f in empty.iterdir():
        f.unlink()
    h16 = bitexact.host_build("16", bitexact.MOVE_DEFS)
    n = max(1, int(round(a.minutes * 60000 / SEG_MS)))
    t0 = time.time()
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda i: segment(i, a.seed * 1000 + i, h16, empty, arm if i < a.arm else ()), range(n)))
    bad = 0
    for i, seed, tempo, nev, info, fails in res:
        st = "FAIL " if fails else "ok   "
        print(f"{st} segment {i:2d} (seed {seed}, {tempo} BPM, {nev} events): {info.get('share', 0) * 100:.2f} % of "
              f"real time, worst call {info.get('worst', 0)} us for {info.get('worst_frames', 0)} frames, peak "
              f"{info.get('peak', 0)}" + (f"; the same on {' and '.join(info['arm'])}" if info.get("arm") else ""))
        for f in fails:
            print(f"      {f}")
        bad += bool(fails)
    print(f"soak: {n} segments, {n * SEG_MS / 60000:.0f} minutes of audio, {sum(r[3] for r in res)} events, "
          f"{bad} failed ({time.time() - t0:.0f} s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
