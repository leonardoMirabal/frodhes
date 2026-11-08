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
  tools/gate_m.py --update [--ref REF] [--engines rhodes] [--dry]
                             after an intended sound change (an engine, the effects): render the MIDI-driven entries
                             with the host build at the Move's voice count from REF (default main) and from the working
                             tree, report what changed (tools/gate_m.py's report: level, peak, residual, octave bands,
                             centroid; build/gate_m/update/report.md + report.json), guard it, then write
                             move16.sha256 as --save does. The guard: an entry may change only when it sounds one of
                             --engines (default rhodes; vibes, marimba, fx, all), by its script (CC 79 0..95 VIBES,
                             96..127 MARIMBA, CC 80 the Rhodes types, a program change: a preset, any engine); every
                             other entry (VIBES / MARIMBA notes, their effect renders) must stay byte-equal, else
                             nothing is written (exit 1). --engines fx allows every entry (an effect change). --dry:
                             the report and the guard only. Re-save Gate R (tools/gate_r.py --save) first: the
                             10-voice entries follow renders.sha256; then make move and tools/gate_m.py (the dsp.so
                             against the new references), make gate-h (the HQ renders), commit all in one commit

The list is Gate R's (tools/gate_r.py jobs()): tests/scripts/*.txt, the 36 x 3 effect cases, the calib renders. An
entry is MIDI-driven when its script has only "midi", "end", "expect", "shot" and "dump" lines (the last three do not
move the sound). Every other entry (FM-1 panel buttons, knobs, keys, the TRS input, stalls, the default run) is
host-only: the same code, covered by Gate R. The renders run in the Move image on macOS (MOVE_RUN, as the Makefile)
and natively on Linux arm64 (MOVE_RUN=). Light: --jobs 2 by default.
"""
import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import tarfile
import wave
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


def entries():
    """[(name, script relative to ROOT)] for the MIDI-driven entries, {name: reason} for the host-only ones"""
    midi, host = [], {}
    for name, script in gate_r.jobs().items():
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


def render(mode, midi, jobs, src=None, tag=None):
    """sha256 per entry of the renders build/gate_m/<tag or mode>/NAME.wav; src: another firmware/ tree (host16)"""
    OUT.mkdir(parents=True, exist_ok=True)
    tag = tag or mode
    lst = OUT / f"{tag}.list"
    lst.write_text("".join(f"{n} {s}\n" for n, s in midi))
    extra = [str(Path(src).relative_to(ROOT)), tag] if src else []
    r = subprocess.run(move_run() + ["sh", "move/tests/gate_m.sh", mode, str(lst.relative_to(ROOT)), str(jobs)] + extra,
                       cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write("".join(ln + "\n" for ln in r.stdout.splitlines() if ln.startswith("FAIL") or "error" in ln))
    if r.returncode:
        sys.stdout.write(r.stdout[-2000:] + r.stderr[-2000:])
        raise SystemExit(f"Gate M: the {mode} renders failed (exit {r.returncode})")
    got = {}
    for n, _ in midi:
        w = OUT / tag / f"{n}.wav"
        got[n] = hashlib.sha256(w.read_bytes()).hexdigest() if w.exists() else "MISSING"
    return got


ENGINES = ("rhodes", "vibes", "marimba")
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)      # the report's octave bands (centre Hz)


def engines_of(script):
    """the engines an entry's notes sound on, by its script: the boot TYPE is the Rhodes (STAGE 77); CC 79 picks the
    bar types (frodhes.c midi_cc: VIBES 0..95, MARIMBA 96..127), CC 80 the Rhodes types; a program change loads a
    preset, which may be any engine ("preset": the guard lets such an entry change with any engine)"""
    cur, out = "rhodes", set()
    if script is None:
        return {"rhodes"}
    for ln in Path(script).read_text().splitlines():
        p = ln.split()
        if len(p) < 4 or ln.startswith("#") or not p[0].isdigit() or p[1] != "midi":
            continue
        st, d1 = int(p[2], 0), int(p[3], 0)
        d2 = int(p[4], 0) if len(p) > 4 else 0
        if st & 0xF0 == 0xB0 and d1 == 79:
            cur = "vibes" if d2 < 96 else "marimba"
        elif st & 0xF0 == 0xB0 and d1 == 80:
            cur = "rhodes"
        elif st & 0xF0 == 0xC0:
            cur = "preset"
        elif st & 0xF0 == 0x90 and d2 > 0:
            out.add(cur)
    return out


def allowed(eng, want):
    """may an entry sounding eng change when the engines want changed? (fx / all: every entry)"""
    return "fx" in want or "all" in want or "preset" in eng or bool(eng & set(want))


def ref_tree(ref):
    """REF's firmware/ and tests/ (the host build includes tests/host.c) under build/gate_m/ref-tree/; its firmware/"""
    d = OUT / "ref-tree"
    subprocess.run(["rm", "-rf", str(d)], check=True)
    d.mkdir(parents=True)
    tar = OUT / "ref-tree.tar"
    with open(tar, "wb") as f:
        subprocess.run(["git", "archive", "--format=tar", ref, "firmware", "tests"], cwd=ROOT, stdout=f, check=True)
    with tarfile.open(tar) as t:
        t.extractall(d)
    tar.unlink()
    return d / "firmware"


def read_wav(path):
    import numpy as np
    w = wave.open(str(path))
    fs, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2" if sw == 2 else "<i4").astype(float) / (1 << (8 * sw - 1))
    return x.reshape(-1, ch).mean(axis=1), fs


def diff_stats(a_path, b_path):
    """the change from render a (REF) to b (now): level, peak, the residual, octave bands, centroid, length"""
    import numpy as np
    a, fs = read_wav(a_path)
    b, _ = read_wav(b_path)
    n = min(len(a), len(b))

    def db(v):
        return 20 * np.log10(max(float(v), 1e-12))

    def rms(x):
        return np.sqrt(np.mean(x ** 2)) if len(x) else 0.0

    out = {"len_s": round(len(b) / fs, 3), "len_diff_frames": len(b) - len(a),
           "rms_ref_db": round(db(rms(a)), 2), "rms_db": round(db(rms(b)), 2),
           "rms_delta_db": round(db(rms(b)) - db(rms(a)), 2),
           "peak_delta_db": round(db(np.abs(b).max()) - db(np.abs(a).max()), 2),
           "resid_db": round(db(rms(b[:n] - a[:n])) - db(rms(a[:n])), 2)}
    A, B = np.abs(np.fft.rfft(a[:n])) ** 2, np.abs(np.fft.rfft(b[:n])) ** 2
    f = np.fft.rfftfreq(n, 1 / fs)
    bands = {}
    for c in BANDS:
        m = (f >= c / 2 ** 0.5) & (f < c * 2 ** 0.5)
        bands[str(c)] = round(float(10 * np.log10((B[m].sum() + 1e-30) / (A[m].sum() + 1e-30))), 2)
    out["bands_delta_db"] = bands
    big = max(bands, key=lambda k: abs(bands[k]))
    out["band_max"] = f"{big} Hz {bands[big]:+.2f} dB"
    out["centroid_hz"] = [round(float((f * P).sum() / (P.sum() + 1e-30))) for P in (A, B)]
    return out


def write_move16(got, base, host):
    """move16.sha256: the entries whose 16-voice render differs from renders.sha256, then the host-only list"""
    diff = sorted(n for n in got if got[n] != base.get(n))
    lines = ["# Gate M (tools/gate_m.py --save): the MIDI-driven renders the Move's 16 voices change against",
             "# renders.sha256 (the host build at the Move's voice count), then the entries that stay host-only.",
             *(f"{got[n]}  {n}" for n in diff),
             *(f"# host-only {n}: {host[n]}" for n in sorted(host))]
    BASE16.write_text("\n".join(lines) + "\n")
    return diff


def update(a, midi, host, base):
    """--update: the REF and the working tree's host16 renders, the report, the guard, then move16.sha256"""
    want = [e.strip() for e in a.engines.split(",") if e.strip()]
    unknown = [e for e in want if e not in ENGINES + ("fx", "all")]
    if unknown or not want:
        raise SystemExit(f"Gate M: --engines {a.engines}: each one of {', '.join(ENGINES)}, fx, all")
    rev = subprocess.run(["git", "rev-parse", "--short", a.ref], cwd=ROOT, capture_output=True, text=True)
    if rev.returncode:
        raise SystemExit(f"Gate M: no commit {a.ref}")
    rev = rev.stdout.strip()
    print(f"Gate M update: REF {a.ref} ({rev}) against the working tree, {len(midi)} MIDI-driven entries, "
          f"engines allowed to change: {', '.join(want)}")
    full = subprocess.run(["git", "rev-parse", a.ref], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    stamp = OUT / "host16ref.rev"                  # REF's renders are kept for the next --update against the same REF
    lst = "".join(f"{n} {s}\n" for n, s in midi)
    if stamp.exists() and stamp.read_text() == full + "\n" + lst and all((OUT / "host16ref" / f"{n}.wav").exists()
                                                                         for n, _ in midi):
        old = {n: hashlib.sha256((OUT / "host16ref" / f"{n}.wav").read_bytes()).hexdigest() for n, _ in midi}
        print(f"Gate M update: REF's renders reused (build/gate_m/host16ref, {rev})")
    else:
        stamp.unlink(missing_ok=True)
        old = render("host16", midi, a.jobs, src=ref_tree(a.ref), tag="host16ref")
        stamp.write_text(full + "\n" + lst)
    new = render("host16", midi, a.jobs)
    miss = [n for n in new if new[n] == "MISSING" or old[n] == "MISSING"]
    if miss:
        raise SystemExit(f"Gate M: no host16 render for {', '.join(miss[:5])}")
    shown = subprocess.run(["git", "show", f"{a.ref}:tests/baselines/renders.sha256"], cwd=ROOT, capture_output=True,
                           text=True)
    old_base = {} if shown.returncode else dict(reversed(ln.split(None, 1)) for ln in shown.stdout.splitlines()
                                                 if ln.strip())
    rows, fails, warns = [], [], []
    for n, s in midi:
        if old[n] == new[n]:
            continue
        eng = engines_of(ROOT / s)
        ok = allowed(eng, want)
        b10 = "same" if old_base.get(n) == base.get(n) else "changed"
        rows.append(dict(name=n, engines=sorted(eng), allowed=ok, renders_sha256=b10,
                         **diff_stats(OUT / "host16ref" / f"{n}.wav", OUT / "host16" / f"{n}.wav")))
        if not ok:
            fails.append(n)
        if b10 == "same":
            warns.append(n)
    rep = OUT / "update"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "report.json").write_text(json.dumps(dict(ref=a.ref, ref_rev=rev, engines=want, entries=len(midi),
                                                     changed=rows, guard_fail=fails, changed_16_only=warns),
                                                indent=1) + "\n")
    md = [f"# Gate M update: {a.ref} ({rev}) to the working tree", "",
          f"{len(rows)} of {len(midi)} MIDI-driven renders change (the host build at the Move's 16 voices); engines "
          f"allowed: {', '.join(want)}; guard: {'FAIL ' + ', '.join(fails) if fails else 'pass'}.", "",
          "| entry | engines | ok | renders.sha256 | rms ref dBFS | rms delta dB | peak delta dB | residual dB re ref | "
          "largest band delta | centroid Hz ref -> now | frames +- |", "|" + "---|" * 11]
    for r in rows:
        md.append(f"| {r['name']} | {' '.join(r['engines'])} | {'yes' if r['allowed'] else 'NO'} | "
                  f"{r['renders_sha256']} | {r['rms_ref_db']} | {r['rms_delta_db']:+.2f} | {r['peak_delta_db']:+.2f} | "
                  f"{r['resid_db']} | {r['band_max']} | {r['centroid_hz'][0]} -> {r['centroid_hz'][1]} | "
                  f"{r['len_diff_frames']} |")
    if warns:
        md += ["", "Changed at 16 voices while renders.sha256 (10 voices) still has REF's hash: re-save Gate R first "
               "(tools/gate_r.py --save), unless the change shows only above 10 voices (steals): " + ", ".join(warns)]
    (rep / "report.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[2:]))
    print(f"Gate M update: report in {(rep / 'report.md').relative_to(ROOT)}")
    if fails:
        print(f"Gate M update: FAIL: {len(fails)} entries that sound none of {', '.join(want)} changed; move16.sha256 "
              f"not written (widen --engines only when that change is intended)")
        return 1
    if a.dry:
        print("Gate M update: --dry: move16.sha256 not written")
        return 0
    diff = write_move16(new, base, host)
    print(f"Gate M update: wrote {len(diff)} 16-voice renders to {BASE16.relative_to(ROOT)}; next: make move, then "
          f"tools/gate_m.py (the dsp.so against them)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help="write tests/baselines/move16.sha256 from the host build")
    ap.add_argument("--list", action="store_true", help="print the classification and stop")
    ap.add_argument("--update", action="store_true", help="REF and now rendered, the report, the guard, move16.sha256")
    ap.add_argument("--ref", default="main", help="with --update: the commit before the sound change (default main)")
    ap.add_argument("--engines", default="rhodes", help="with --update: the engines that may change (rhodes, vibes, "
                    "marimba; fx or all: every entry)")
    ap.add_argument("--dry", action="store_true", help="with --update: the report and the guard only, write nothing")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    midi, host = entries()
    base = read_sums(BASE)
    if a.list:
        for n, s in midi:
            print(f"midi       {n}")
        for n in sorted(host):
            print(f"host-only  {n}: {host[n]}")
        print(f"Gate M: {len(midi)} MIDI-driven, {len(host)} host-only")
        return 0
    if a.update:
        return update(a, midi, host, base)
    if a.save:
        got = render("host16", midi, a.jobs)
        miss = [n for n in got if got[n] == "MISSING"]
        if miss:
            raise SystemExit(f"Gate M: no host16 render for {', '.join(miss[:5])}")
        diff = write_move16(got, base, host)
        print(f"Gate M: saved {len(diff)} 16-voice renders and {len(host)} host-only entries to "
              f"{BASE16.relative_to(ROOT)}")
        return 0
    want16 = read_sums(BASE16)
    got = render("replay", midi, a.jobs)
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
