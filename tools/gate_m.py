#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Gate M (M1-10): the shipping Move plugin renders every MIDI-driven Gate R entry byte-identical.

The plugin loads with the HQ Rhodes on (its one piano engine); the replay (move/tests/move_replay.c) sets p_hq 0, the
dev / test key, first, so Gate M proves the Classic path equal to the host renders. The HQ path is Gate H's, and
make test-move compares the plugin as it loads with the host build with HQ on (move/tests/run_eq.sh).

  tools/gate_m.py            replay each MIDI-driven entry through build/move/frodhes/dsp.so (make move first),
                             compare its sha256 with tests/baselines/renders.sha256, or with
                             tests/baselines/move16.sha256 for the renders the Move's 16 voices change (steals)
  tools/gate_m.py --save     render those entries with the host build at the Move's voice count and write
                             move16.sha256: the entries where 16 voices differ from the 10-voice baseline, and the
                             host-only entries with the reason (only from committed code, on purpose)
  tools/gate_m.py --list     print the classification only (no build, no render)

The list is Gate R's (tools/gate_r.py jobs()): tests/scripts/*.txt, the 36 x 3 effect cases, the calib renders. An
entry is MIDI-driven when its script has only "midi", "end", "expect", "shot" and "dump" lines (the last three do not
move the sound). Every other entry (FM-1 panel buttons, knobs, keys, the TRS input, stalls, the default run) is
host-only: the same code, covered by Gate R. The renders run in the Move image on macOS (MOVE_RUN, as the Makefile)
and natively on Linux arm64 (MOVE_RUN=). Light: --jobs 2 by default.

Concurrency (K3: the 0.9.9 release's Gate M had one DIFF, fx/rev127, while another process rebuilt and rewrote the
shared build files mid-gate): the gate holds the GATE_M lock (tools/lock.py) for its whole run; it replays a snapshot
of the plugin (build/move/frodhes/{dsp.so,engine.so} copied to build/gate_m/so/ at the start, the copy checked against
the source, the sha256 printed), its own copy of the scripts (build/gate_m/scripts/, not build/gate_r/'s, which
every jobs() caller rewrites) and its own instance folders (build/gate_m/t/, MOVE_REPLAY_TMP: test-move's cleanup
removes build/move/t/run.*). A plugin rebuilt during the gate is named in the result: the gate tested the snapshot.
"""
import argparse
import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate_r  # noqa: E402

OUT = ROOT / "build" / "gate_m"
BASE = ROOT / "tests" / "baselines" / "renders.sha256"
BASE16 = ROOT / "tests" / "baselines" / "move16.sha256"
SOUNDLESS = {"midi", "end", "expect", "shot", "dump"}
MOVE_DROP = set(range(43, 47)) | set(range(52, 64))   # (move/move_bridge.h MB_CC_DROP: the sequencer's CLEAR /
# UNDO / ERASE / SWING, the arpeggiator, the chords, the sequencer's PLAY / BARS / QUANT / REC)
IMG = os.environ.get("MOVE_IMG", "frodhes-move:bookworm")
PLUGIN = ROOT / "build" / "move" / "frodhes"
SNAP = OUT / "so"                                     # the plugin as the gate replays it
SNAP_FILES = ("dsp.so", "engine.so")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def snapshot(src=PLUGIN, dst=SNAP, tries=3):
    """src's plugin files copied to dst, each copy checked against the source read again after it (a build that
    rewrote it meanwhile: copied again, up to tries times). {file: sha256}; SystemExit when src has none or never
    holds still"""
    dst.mkdir(parents=True, exist_ok=True)
    got = {}
    for f in SNAP_FILES:
        if not (src / f).exists():
            if f == "dsp.so":
                raise SystemExit(f"Gate M: no {src / f}: make move first")
            continue
        for _ in range(tries):
            before = sha(src / f)
            tmp = dst / f".{f}.{os.getpid()}"
            shutil.copyfile(src / f, tmp)
            if sha(tmp) == before == sha(src / f):
                os.replace(tmp, dst / f)
                got[f] = before
                break
            tmp.unlink()
        else:
            raise SystemExit(f"Gate M: {src / f} kept changing while it was copied (a build running?)")
    return got


def changed_since(snap, src=PLUGIN):
    """the plugin files whose source no longer matches the snapshot (rebuilt during the gate)"""
    return sorted(f for f, h in snap.items() if not (src / f).exists() or sha(src / f) != h)


def move_run():
    """the command prefix that runs a command in the Move image (Makefile MOVE_RUN)"""
    if "MOVE_RUN" in os.environ:
        return shlex.split(os.environ["MOVE_RUN"])
    if platform.system() == "Darwin":
        return ["docker", "run", "--rm", "--cpus=2", "--platform", "linux/arm64", "-v", f"{ROOT}:/src", "-w", "/src",
                IMG]
    return []


def classify(script):
    """None when MIDI-driven, else the reason it is host-only"""
    if script is None:
        return "the default run (no script: the built-in demo)"
    kinds = set()
    for ln in Path(script).read_text().splitlines():
        p = ln.split()
        if len(p) >= 2 and not ln.startswith("#") and p[0].isdigit():
            kinds.add(p[1])
    other = sorted(kinds - SOUNDLESS)
    if other:
        return f"FM-1 input lines: {', '.join(other)}"
    drop = sorted({int(p[3]) for p in (ln.split() for ln in Path(script).read_text().splitlines())
                   if len(p) >= 4 and p[1] == "midi" and int(p[2], 0) & 0xF0 == 0xB0 and int(p[3], 0) in MOVE_DROP})
    if drop:
        return (f"the FM-1 sequencer / arpeggiator / chords (CC {', '.join(map(str, drop))}): not on the Move, which has its "
                f"own (the user, 2026-10-28; move/move_bridge.h MB_CC_DROP)")
    return None


def entries(out=None):
    """[(name, script relative to ROOT)] for the MIDI-driven entries, {name: reason} for the host-only ones; the
    generated scripts under out (default Gate R's build/gate_r: the gate itself uses its own, OUT / "scripts")"""
    midi, host = [], {}
    for name, script in gate_r.jobs(out).items():
        why = classify(script)
        if why:
            host[name] = why
        else:
            midi.append((name, Path(script).resolve().relative_to(ROOT)))
    return midi, host


def read_sums(path):
    if not path.exists():
        return {}
    return dict(reversed(ln.split(None, 1)) for ln in path.read_text().splitlines()
                if ln.strip() and not ln.startswith("#"))


def render(mode, midi, jobs):
    OUT.mkdir(parents=True, exist_ok=True)
    lst = OUT / f"{mode}.list"
    lst.write_text("".join(f"{n} {s}\n" for n, s in midi))
    so = f"{SNAP.relative_to(ROOT)}/dsp.so"
    r = subprocess.run(move_run() + ["sh", "move/tests/gate_m.sh", mode, str(lst.relative_to(ROOT)), str(jobs), so],
                       cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write("".join(ln + "\n" for ln in r.stdout.splitlines() if ln.startswith("FAIL") or "error" in ln))
    if r.returncode:
        sys.stdout.write(r.stdout[-2000:] + r.stderr[-2000:])
        raise SystemExit(f"Gate M: the {mode} renders failed (exit {r.returncode})")
    got = {}
    for n, _ in midi:
        w = OUT / mode / f"{n}.wav"
        got[n] = hashlib.sha256(w.read_bytes()).hexdigest() if w.exists() else "MISSING"
    return got


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help="write tests/baselines/move16.sha256 from the host build")
    ap.add_argument("--list", action="store_true", help="print the classification and stop")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    if a.list:
        return gate(a)
    import lock
    with lock.Lock("GATE_M", who="gate_m", why="build/gate_m: the plugin snapshot, scripts and renders", wait_s=3600,
                   poll_s=5):
        return gate(a)


def gate(a):
    midi, host = entries(None if a.list else OUT / "scripts")
    base = read_sums(BASE)
    if a.list:
        for n, s in midi:
            print(f"midi       {n}")
        for n in sorted(host):
            print(f"host-only  {n}: {host[n]}")
        print(f"Gate M: {len(midi)} MIDI-driven, {len(host)} host-only")
        return 0
    if a.save:
        got = render("host16", midi, a.jobs)
        miss = [n for n in got if got[n] == "MISSING"]
        if miss:
            raise SystemExit(f"Gate M: no host16 render for {', '.join(miss[:5])}")
        diff = sorted(n for n in got if got[n] != base.get(n))
        lines = ["# Gate M (tools/gate_m.py --save): the MIDI-driven renders the Move's 16 voices change against",
                 "# renders.sha256 (the host build at the Move's voice count), then the entries that stay host-only.",
                 *(f"{got[n]}  {n}" for n in diff),
                 *(f"# host-only {n}: {host[n]}" for n in sorted(host))]
        BASE16.write_text("\n".join(lines) + "\n")
        print(f"Gate M: saved {len(diff)} 16-voice renders and {len(host)} host-only entries to "
              f"{BASE16.relative_to(ROOT)}")
        return 0
    want16 = read_sums(BASE16)
    snap = snapshot()
    print("Gate M: the plugin as replayed: " + ", ".join(f"{f} {h[:12]}" for f, h in sorted(snap.items())))
    got = render("replay", midi, a.jobs)
    moved = changed_since(snap)
    if moved:
        print(f"Gate M: {', '.join(moved)} rebuilt during the gate: the result is the snapshot's "
              f"({SNAP.relative_to(ROOT)})")
    bad = []
    for n, _ in midi:
        want = want16.get(n, base.get(n))
        if got[n] != want:
            bad.append(n)
            print(f"DIFF  {n}: {'move16' if n in want16 else 'baseline'} {str(want)[:12]}, dsp.so {got[n][:12]}")
    print(f"Gate M: {len(midi) - len(bad)} of {len(midi)} MIDI-driven renders equal "
          f"({len(want16)} against move16.sha256), {len(host)} host-only")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
