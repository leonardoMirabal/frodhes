#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Akai MPC port's sound against the desktop plugin's and the host build's, byte for byte (docs/MPC.md).

The scripts: Gate M's MIDI-driven entries (tests/scripts and Gate R's generated effect and calibration scripts; the
same list the desktop plugin's Gate D plays) and tests/hq. Each is rendered to a 16-bit WAV by:

  host24   the host build (tests/host.c) with the desktop plugin's switches (24 voices, HQ)
  host16   the host build with the Move's (MOVE_DEFS: 16 voices, HQ), the MPC's own build
  clap     the desktop plugin (build/plugin: frp_clap_render on bundles/Frodhes.clap), 24 voices
  mpc24    the MPC port built at 24 voices (mpc/build.py --voices 24 --out build/mpc24), mpc/tests/vst2_render
  mpc16    the MPC port as it ships (build/mpc), 16 voices

and the checks (each a list of renders that must be the same file):

  ms       44.1 kHz, the plugins in their host-time test mode (FRODHES_PLUGIN_TEST=ms, as Gate D):
           host24 = clap = mpc24, and host16 = mpc16
  exact    as they ship (no test mode): clap = mpc24 at 44.1 and at 48 kHz
  blocks   random block sizes (1..2048 frames a call) on the MPC, the desktop at 512: mpc24 = clap
  arm64 / arm32   the shipped .so on Linux in Docker (mpc/tests/arm_run.sh; arm32 under QEMU): mpc16 there = host16
           (a subset: --arm N scripts)

  python3 mpc/tests/bitexact.py [--quick] [--jobs 2] [--only ms,exact,blocks,arm64,arm32] [--arm 4]
Exit 0 all the same, 1 a difference (listed), 2 a part missing (the builds: make plugin, make mpc, the 24-voice
build). --quick: tests/hq and 12 scripts spread over the list.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate_m  # noqa: E402

OUT = ROOT / "build" / "mpc" / "bitexact"
NICE = ["nice", "-n", "15"]
HQ = ["-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]
MOVE_DEFS = ["-DFR_VOICES=16u"] + HQ
PLUG_DEFS = ["-DFR_VOICES=24u"] + HQ
CLAP_RENDER = ROOT / "build" / "plugin" / "frp_clap_render"
CLAP = ROOT / "build" / "plugin" / "bundles" / "Frodhes.clap"
NATIVE_EXT = "dylib" if sys.platform == "darwin" else "so"
MPC16 = ROOT / "build" / "mpc" / "native" / f"frodhes.{NATIVE_EXT}"
MPC24 = ROOT / "build" / "mpc24" / "native" / f"frodhes.{NATIVE_EXT}"
VST2_RENDER = ROOT / "build" / "mpc" / "native" / "vst2_render"
SO = {"arm64": ROOT / "build" / "mpc" / "Frodhes - VST - Frodhes" / "VST" / "frodhes-arm64.so",
      "arm32": ROOT / "build" / "mpc" / "Frodhes - VST - Frodhes" / "VST" / "frodhes-arm32.so"}


def run(cmd, env=None):
    return subprocess.run(NICE + [str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True, env=env)


def env_for(test):
    env = {k: v for k, v in os.environ.items() if k != "FRODHES_PLUGIN_TEST"}
    if test:
        env["FRODHES_PLUGIN_TEST"] = test
    return env


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if p and Path(p).exists() else "MISSING"


def host_build(tag, defs):
    out = OUT / "bin" / f"host-{tag}"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}")   # (built aside, then renamed: two runs at once never share a
    r = run(["cc", "-O2", "-w", "-DDINGHY_HOST", *defs, "-DHQ_DEFAULT=1", '-DAPP_VER="dev"', "-Ifirmware/src",  # file)
             "-Ifirmware/gen", "-o", tmp, "firmware/src/frodhes.c", "-lm"])
    if r.returncode:
        raise SystemExit(f"bitexact: the host build {tag} failed:\n{r.stderr[-2000:]}")
    os.replace(tmp, out)
    return out


def host_render(binary, name, script):
    out = OUT / binary.name / name
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.{threading.get_ident()}")
    run([binary, tmp, script])          # (an expect may fail: the sound is what counts, as Gate D)
    made = Path(str(tmp) + ".wav")
    if made.exists():                   # (renamed whole: another run reading the same render sees all of it)
        os.replace(made, Path(str(out) + ".wav"))
    for f in tmp.parent.glob(tmp.name + "*"):   # (whatever else the host build wrote beside it)
        f.unlink()
    return Path(str(out) + ".wav")


def plug(kind, lib, name, script, tag, args, test):
    out = OUT / tag / (name + ".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    tool = [CLAP_RENDER, CLAP] if kind == "clap" else [VST2_RENDER, lib]
    if kind == "clap":
        args = ["--engine", "hq", *args]
    r = run(tool + [script, out, *args], env=env_for(test))
    return out if r.returncode == 0 else None


def subset(midi, k):
    step = max(1, len(midi) // k)
    pick = midi[::step][:k]
    return pick + [e for e in midi if "steal" in e[0] and e not in pick]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="ms,exact,blocks,arm64,arm32")
    ap.add_argument("--arm", type=int, default=4, help="scripts in the Docker checks (4)")
    a = ap.parse_args()
    modes = [m for m in a.only.split(",") if m]
    missing = [str(p.relative_to(ROOT)) for p in (CLAP_RENDER, CLAP, MPC16, MPC24, VST2_RENDER) if not p.exists()]
    if missing:
        print("bitexact: missing " + ", ".join(missing) + " (make plugin; make mpc; python3 mpc/build.py --target "
              "native --voices 24 --out build/mpc24)")
        return 2
    empty = OUT / "presets-empty"           # (the user presets: an empty folder, never the machine's own)
    empty.mkdir(parents=True, exist_ok=True)
    for f in empty.iterdir():
        f.unlink()
    os.environ["FRODHES_PRESET_DIR"] = str(empty)
    midi, _ = gate_m.entries(ROOT / "build" / "mpc" / "scripts")
    hq = [(f"hq/{p.stem}", p.relative_to(ROOT)) for p in sorted((ROOT / "tests" / "hq").glob("*.txt"))]
    todo = hq + (subset(midi, 12) if a.quick else midi)
    fails, counts = [], {}

    def check(mode, name, files):
        shas = [sha(f) for f in files]
        ok = "MISSING" not in shas and len(set(shas)) == 1
        n, k = counts.get(mode, (0, 0))
        counts[mode] = (n + 1, k + ok)
        if not ok:
            fails.append(f"{mode} {name}: " + ", ".join(s[:12] for s in shas))
            print(f"DIFF  {mode} {name}: " + ", ".join(s[:12] for s in shas), flush=True)
        else:
            for f in files[1:]:
                Path(f).unlink()

    def pmap(fn, items):
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(fn, items))

    h24 = h16 = None
    if "ms" in modes or "arm64" in modes or "arm32" in modes:
        h16 = host_build("16", MOVE_DEFS)
    if "ms" in modes:
        h24 = host_build("24", PLUG_DEFS)

        def one_ms(e):
            n, s = e
            f = n.replace("/", "_")
            check("ms 24 voices (host = clap = mpc)", n, [host_render(h24, f, s),
                  plug("clap", None, f, s, "ms-clap", [], "ms"), plug("vst2", MPC24, f, s, "ms-mpc24", [], "ms")])
            check("ms 16 voices (host = mpc)", n, [host_render(h16, f, s), plug("vst2", MPC16, f, s, "ms-mpc16", [], "ms")])
        pmap(one_ms, todo)
    if "exact" in modes:
        def one_exact(e):
            (n, s), rate = e
            f = n.replace("/", "_")
            check(f"exact {rate} Hz (clap = mpc)", n, [plug("clap", None, f, s, f"ex{rate}-clap", ["--rate", rate], None),
                  plug("vst2", MPC24, f, s, f"ex{rate}-mpc24", ["--rate", rate], None)])
        pmap(one_exact, [(e, r) for r in (44100, 48000) for e in todo])
    if "blocks" in modes:
        def one_blocks(e):
            n, s = e
            f = n.replace("/", "_")
            check("blocks random (clap = mpc)", n, [plug("clap", None, f, s, "bl-clap", [], None),
                  plug("vst2", MPC24, f, s, "bl-mpc24", ["--block", "random:7"], None)])
        pmap(one_blocks, subset(midi, 6))
    for arch in ("arm64", "arm32"):
        if arch not in modes:
            continue
        tool = ROOT / "build" / "mpc" / "stage" / arch / "vst2_render"
        if not SO[arch].exists() or not tool.exists():
            print(f"bitexact: {arch} skipped (python3 mpc/build.py --target {arch} --tools)")
            continue
        for n, s in hq[:1] + subset(midi, a.arm)[:a.arm]:
            f = n.replace("/", "_")
            out = OUT / f"ms-{arch}" / (f + ".wav")
            out.parent.mkdir(parents=True, exist_ok=True)
            r = run(["sh", "mpc/tests/arm_run.sh", arch, "env", "FRODHES_PLUGIN_TEST=ms",
                     f"FRODHES_PRESET_DIR={empty}", tool, SO[arch], ROOT / s, out])
            if r.returncode:
                print((r.stdout + r.stderr)[-800:])
            check(f"{arch} 16 voices (host = mpc on Linux)", n, [host_render(h16, f, s), out])
    print()
    for mode, (n, k) in counts.items():
        print(f"{'ok   ' if n == k else 'FAIL '} {mode}: {k} of {n} the same")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
