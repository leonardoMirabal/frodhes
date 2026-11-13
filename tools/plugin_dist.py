#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The dry-distortion gate (tools/dry_dist.py) through the plugin: every dry_dist script played through the CLAP, the
VST3 and the Audio Unit, each judged against 0.9.11 with dry_dist's own cells and limits, and the three formats
compared sample for sample. The reference is dry_dist's HQ one (tests/baselines/dry_dist_0911_hq.json: 0.9.11 built
with the HQ defines, as the plugin's slots), the scripts without the key cells (the FM-1's own keys: no plugin path).

  python3 tools/plugin_dist.py [--bundles DIR] [--render EXE] [--formats clap,vst3,au] [--engines hq]
                               [--tags TAG,...] [--out DIR] [--base FILE]

The hosts, offline at 44.1 kHz, every event at its frame:
  clap   the project's headless host (plugin/tests/clap_render.c, Gate D's reference): the script as MIDI
  vst3   a minimal VST3 host on the SDK's hosting classes (plugin/tests/vst3_render.cpp): notes as VST3 note events,
         the controllers through the plugin's IMidiMapping as parameter points (a DAW's path; clap-wrapper turns them
         back into MIDI controllers)
  au     Apple's AU host (AVAudioEngine, tools/plugin_au_render.swift): the script as MIDI; the bundle is registered
         for this process only (AudioComponentRegister: nothing is installed)
The controllers reach the app as on the FM-1: CC 111 (FXOFF), 119 (RAW), 80 (TYPE), 40 (RESPONSE). The plugin keeps
them all (plugin/src/slot_bridge.h SB_CC_DROP drops only the sequencer's, the arpeggiator's and the chords').

Engines: "hq" plays the plugin's sound (nothing set). The plugin is HQ only (24 voices on every engine): "classic",
ENGINE CLASSIC on the builds that had it, is refused.

Writes, per engine and format, DIR/<engine>/<format>/<tag>.wav (16-bit stereo, aligned to the host build's time: the
plugin's latency, 258 frames at 44.1 kHz, taken off) and cells.json (`tools/dry_dist.py --judge` reads it), and DIR/report.txt. The
VST3 host is built once into build/plugin-dist (the SDK, the command line tools), the AU host there too. macOS only.
Exit 0: every format PASS. 1: a FAIL or a host error. The sample identity is reported, not judged.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dry_dist  # noqa: E402

FS = dry_dist.FS
TOOLS = os.path.join(ROOT, "build", "plugin-dist")
SDK = os.path.join(os.environ.get("FRP_DEPS_DIR") or os.path.join(ROOT, ".local", "plugin-deps"), "vst3sdk")
ENGINE_VAL = {"hq": None, "classic": 0}


def newer(dst, *srcs):
    return not os.path.exists(dst) or any(os.path.getmtime(s) > os.path.getmtime(dst) for s in srcs)


def build_tools():
    os.makedirs(TOOLS, exist_ok=True)
    au = os.path.join(TOOLS, "plugin_au_render")
    src = os.path.join(ROOT, "tools", "plugin_au_render.swift")
    if newer(au, src):
        subprocess.run(["swiftc", "-O", "-o", au, src], check=True, capture_output=True)
    vst = os.path.join(TOOLS, "vst3_render")
    src = os.path.join(ROOT, "plugin", "tests", "vst3_render.cpp")
    if newer(vst, src):
        if not os.path.isdir(os.path.join(SDK, "public.sdk")):
            sys.exit(f"no VST3 SDK in {SDK} (make plugin-deps)")
        import glob
        fs = []
        for g in ["public.sdk/source/vst/hosting/*.cpp", "base/source/*.cpp", "base/thread/source/*.cpp",
                  "pluginterfaces/base/*.cpp", "public.sdk/source/vst/utility/stringconvert.cpp",
                  "public.sdk/source/common/commonstringconvert.cpp", "public.sdk/source/common/memorystream.cpp",
                  "public.sdk/source/vst/vstinitiids.cpp"]:
            fs += [os.path.relpath(f, SDK) for f in glob.glob(os.path.join(SDK, g))]
        fs = sorted(f for f in set(fs) if "module_linux" not in f and "module_win32" not in f)
        subprocess.run(["nice", "-n", "15", "clang++", "-std=c++17", "-O1", "-w", "-DRELEASE=1", "-I.", *fs, src,
                        "-x", "objective-c++", "-fobjc-arc", "public.sdk/source/vst/hosting/module_mac.mm",
                        "public.sdk/source/common/threadchecker_mac.mm", "-framework", "Cocoa", "-o", vst],
                       cwd=SDK, check=True)
    return au, vst


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).copy()


def write_wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(np.ascontiguousarray(x, dtype="<i2").tobytes())


def to16(f32):
    """a float render as the 16-bit samples the CLAP render writes (plugin/tests/clap_render.c)"""
    return np.clip(np.round(f32.astype(np.float64) * 32768.0), -32767, 32767).astype(np.int16)


def align(x, ref, lat):
    """x (int16, the plugin's own time) cut to ref's frames: the lag where it equals ref sample for sample (searched
    0..999, the latency first), else the latency. (x, lag, identical)"""
    n = len(ref)
    for L in [lat] + [k for k in range(1000) if k != lat]:
        if len(x) - L >= n and np.array_equal(x[L:L + n], ref):
            return x[L:L + n], L, True
    return x[lat:lat + n], lat, False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundles", default=os.path.join(ROOT, os.environ.get("FRP_BUILD", "build/plugin"), "bundles"))
    ap.add_argument("--render", default=os.path.join(ROOT, os.environ.get("FRP_BUILD", "build/plugin"), "frp_clap_render"))
    ap.add_argument("--formats", default="clap,vst3,au")
    ap.add_argument("--engines", default="hq", help="hq (the plugin's sound: HQ only)")
    ap.add_argument("--tags", help="a subset of dry_dist's scripts (default: all 16)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "plugin-dist", "out"))
    ap.add_argument("--base", default=dry_dist.BASE_HQ)
    a = ap.parse_args()
    formats = a.formats.split(",")
    engines = a.engines.split(",")
    for e in engines:
        if e == "classic":
            sys.exit("plugin_dist: the plugin is HQ only (24 voices on every engine): no CLASSIC")
        if e not in ENGINE_VAL:
            sys.exit(f"unknown engine {e}")
    tags = [t for t in dry_dist.tags() if not a.tags or t[0] in a.tags.split(",")]
    base = json.load(open(a.base))
    au_exe, vst_exe = build_tools()
    clap_b = os.path.join(a.bundles, "Frodhes.clap")
    vst_b = os.path.join(a.bundles, "Frodhes.vst3")
    au_b = os.path.join(a.bundles, "Frodhes.component")
    lat = 258                                               # the plugin's latency at 44.1 kHz: an app block + 2 frames
    env = dict(os.environ)
    env.pop("FRP_TEST_EXT", None)
    report, ok_all = [], True
    for eng in engines:
        ev_val = ENGINE_VAL[eng]
        cells = {f: {} for f in formats}
        ident = {f: [] for f in formats if f != "clap"}
        for tag, ccc, tcc, mcc in tags:
            work = os.path.join(a.out, eng, "work")
            os.makedirs(work, exist_ok=True)
            lines = sorted(dry_dist.script(ccc, tcc, mcc, keys=False), key=lambda l: int(l.split()[0]))
            scr = os.path.join(work, tag + ".txt")
            open(scr, "w").write("\n".join(lines) + "\n")
            evf = os.path.join(work, tag + ".ev")
            with open(evf, "w") as f:
                for l in lines:
                    p = l.split()
                    if p[1] == "midi":
                        f.write(f"{math.ceil(int(p[0]) * FS / 1000.0 - 1e-9)} {int(p[2], 16)} {p[3]} {p[4]}\n")
            ref_wav = os.path.join(a.out, eng, "clap", tag + ".wav") if "clap" in formats else os.path.join(work, tag + ".wav")
            os.makedirs(os.path.dirname(ref_wav), exist_ok=True)
            cmd = [a.render, clap_b, scr, ref_wav] + (["--engine", "hq" if ev_val else "classic"] if ev_val is not None else [])
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if r.returncode:
                sys.exit(f"clap_render {tag}: exit {r.returncode}\n{r.stdout}{r.stderr}")
            ref = read_wav(ref_wav)
            frames = len(ref) + 1000
            for fmt in formats:
                d = os.path.join(a.out, eng, fmt)
                os.makedirs(d, exist_ok=True)
                if fmt == "clap":
                    x = ref
                else:
                    raw = os.path.join(work, f"{tag}.{fmt}.f32")
                    ev_arg = str(-1 if ev_val is None else ev_val)
                    if fmt == "vst3":
                        cmd = [vst_exe, vst_b, str(FS), str(frames), evf, raw] + ([ev_arg] if ev_val is not None else [])
                    else:
                        cmd = [au_exe, str(FS), str(frames), evf, raw, ev_arg, au_b]
                    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
                    if r.returncode:
                        sys.exit(f"{fmt} {tag}: exit {r.returncode}\n{r.stderr}")
                    x, L, same = align(to16(np.fromfile(raw, dtype=np.float32).reshape(-1, 2)), ref, lat)
                    ident[fmt].append((tag, L, same))
                    os.remove(raw)
                if fmt != "clap":
                    write_wav(os.path.join(d, tag + ".wav"), x)
                cells[fmt][tag] = dry_dist.measure_wave(x.astype(np.float64).mean(1))
            print(f"{eng:7s} {tag:16s} rendered: {', '.join(formats)}", flush=True)
        for fmt in formats:
            json.dump(cells[fmt], open(os.path.join(a.out, eng, fmt, "cells.json"), "w"), indent=1, sort_keys=True)
            rows = dry_dist.judge(cells[fmt], base)
            bad = [r for r in rows if not r[5]]
            worst = min(rows, key=lambda r: (r[4] - r[2]) if r[4] is not None else -99)
            wm = (worst[4] - worst[2]) if worst[4] is not None else float("nan")
            ok_all &= not bad
            report.append(f"{eng:7s} {fmt:4s} {'PASS' if not bad else 'FAIL'}  {len(bad)} of {len(rows)} values over;"
                          f" worst margin {wm:+.1f} dB ({worst[0]} {worst[1]} {worst[2]:.1f}, limit {worst[4]:.1f})")
            if bad:
                report.append("    " + dry_dist.table(rows, only_bad=True, worst=12).replace("\n", "\n    "))
        for fmt, ls in ident.items():
            n = sum(1 for _, _, s in ls if s)
            lags = sorted({L for _, L, s in ls if s})
            report.append(f"{eng:7s} {fmt:4s} vs clap: {n} of {len(ls)} scripts sample-identical"
                          + (f" (after the {', '.join(map(str, lags))}-frame latency)" if n else ""))
    rep = "\n".join(report)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "report.txt"), "w").write(rep + "\n")
    print(rep)
    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
