#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Frodhes for the Akai MPC (docs/MPC.md): build the plugin folder and its zip.

    python3 mpc/build.py [--target all|native|arm32|arm64] [--version X.Y.Z] [--voices N] [-j N] [--no-lto]

The engine is the desktop plugin's, compiled from the same sources with the Linux plugin build's settings
(plugin/cmake/slots.cmake, tools/plugin_linux.sh): plugin/src/engine_unit.c once per slot (-DFRP_SLOT=0..15, every
symbol private to its slot), plugin/src/bridge.c, frp_paths.c and the coroutine, -O2 -std=gnu11 -ffp-contract=off
-fvisibility=hidden, full LTO on the slots (their constant tables merge), glibc 2.31, the C++ runtime linked in, one
exported symbol. What differs is the MPC Plugin SDK's: its compiler (zig's clang, tools/build.py's targets
arm-linux-gnueabihf.2.31 -mcpu=cortex_a17 and aarch64-linux-gnu.2.31 -mcpu=cortex_a55), its VST 2.4 framework
(mpc/sdk/, MIT) for the wrapper mpc/src/frodhes_mpc.cpp, and the voices: 16 on every engine, as the Move
(Makefile MOVE_DEFS), where the desktop plugin has 24.

Outputs (build/mpc/ unless --out):
    native/frodhes.(dylib|so)        this machine's build, for the SDK's host simulator and the tests
    Frodhes - VST - Frodhes/         the plugin folder: plugin.json, version.xml, VST/frodhes-arm32.so,
                                     VST/frodhes-arm64.so, Plugin Skins/ (mpc/skins/: TUI.json, Q-Links.json, the PNGs)
    frodhes-mpc.zip                  the folder + INSTALL.txt, LICENSE.txt, SOURCE.txt, THIRD-PARTY-NOTICES.txt
    params.json                      the parameters as the host simulator sees them (the skin is checked against it)

The SDK's tools (host_sim, validate.py) come from a checkout of SDK_COMMIT in .local/mpc-sdk (fetched on
first use, or $MPC_SDK); the three framework headers compiled into the plugin are vendored in mpc/sdk/ and checked
against that commit. Exit: 0 ok, 1 a failure (compile, validation), 2 bad arguments or a missing toolchain.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MPC = os.path.join(ROOT, "mpc")
SDK_COMMIT = "febd2ca5bd9eb16c4a412837196e145566bc981b"
SDK_URL = "https://github.com/inmusic-software/mpc-plugin-builder-sdk"
SDK_HEADERS = ("mpc_plugin.h", "mpc_vst2_abi.h", "mpc_resources.h")
ZIG_SERIES = "0.15."                     # the zig the build was checked with (another version: a warning)
SLOTS = 16                               # plugin/src/bridge.h FRP_SLOTS
FOLDER = "Frodhes - VST - Frodhes"       # "<manufacturer> - VST - <name>" (mpc/plugin.json, frodhes_mpc.cpp)
SLUG = "frodhes"
IS_MAC = sys.platform == "darwin"

ARCH = {   # zig target, cpu, coroutine source (tools/build.py ARM_TARGETS)
    "arm32": ("arm-linux-gnueabihf.2.31", "cortex_a17", "plugin/src/coro/coro_arm32.S"),
    "arm64": ("aarch64-linux-gnu.2.31", "cortex_a55", "plugin/src/coro/coro_arm64.S"),
}
# the app's build switches: the desktop plugin's (plugin/cmake/slots.cmake FRP_ENGINE_DEFS) at the Move's voice count
ENGINE_FLAGS = ["-O2", "-std=gnu11", "-ffp-contract=off", "-fvisibility=hidden", "-fPIC", "-Wall",
                "-Wno-unused-function", "-Wno-unused-variable"]
CXX_FLAGS = ["-std=c++17", "-O2", "-fPIC", "-fvisibility=hidden", "-fvisibility-inlines-hidden",
             "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-ffp-contract=off"]


def log(msg):
    print(msg, flush=True)


def die(msg, code=1):
    print("mpc/build.py: " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


# ---- the SDK checkout and the vendored headers
def sdk_dir(fetch=True):
    d = os.environ.get("MPC_SDK") or os.path.join(ROOT, ".local", "mpc-sdk")
    if not os.path.isdir(os.path.join(d, "tools")):
        if not fetch:
            return None
        log("SDK: fetching %s at %s into %s" % (SDK_URL, SDK_COMMIT[:12], d))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if subprocess.run(["git", "clone", "-q", SDK_URL, d]).returncode:
            die("could not clone the MPC Plugin SDK (network?)", 2)
        subprocess.run(["git", "-C", d, "checkout", "-q", SDK_COMMIT], check=True)
    return d


def sdk_head(d):
    try:
        return subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def check_sdk(d):
    head = sdk_head(d)
    if head and head != SDK_COMMIT:
        die("the SDK checkout %s is at %s, not the pinned %s (git -C '%s' checkout %s)" % (d, head[:12], SDK_COMMIT[:12], d, SDK_COMMIT), 2)
    bad = [h for h in SDK_HEADERS if not os.path.isfile(os.path.join(d, "framework", h)) or
           open(os.path.join(d, "framework", h), "rb").read() != open(os.path.join(MPC, "sdk", h), "rb").read()]
    if bad:
        die("mpc/sdk/%s differ from the SDK at %s" % (", ".join(bad), SDK_COMMIT[:12]), 1)


# ---- the compiler
def find_zig(d):
    z = os.environ.get("ZIG") or shutil.which("zig")
    if z:
        argv = [z]
    else:   # the SDK's own way: the ziglang package in a private venv inside the SDK checkout
        sys.path.insert(0, os.path.join(d, "tools"))
        import build as sdkbuild  # noqa: E402  (the SDK's tools/build.py)
        argv = sdkbuild.find_zig(None)
    ver = subprocess.run(argv + ["version"], capture_output=True, text=True).stdout.strip()
    if not ver:
        die("zig does not run (%s)" % " ".join(argv), 2)
    if not ver.startswith(ZIG_SERIES):
        log("WARNING: zig %s (the build was checked with %sx)" % (ver, ZIG_SERIES))
    return argv, ver


def newest(paths):
    t = 0.0
    for p in paths:
        if os.path.isdir(p):
            for r, _d, fs in os.walk(p):
                for f in fs:
                    t = max(t, os.path.getmtime(os.path.join(r, f)))
        elif os.path.isfile(p):
            t = max(t, os.path.getmtime(p))
    return t


class Job:
    def __init__(self, out, argv, deps_t):
        self.out, self.argv, self.deps_t = out, argv, deps_t

    def stale(self):
        cmd = self.out + ".cmd"
        if not os.path.isfile(self.out) or not os.path.isfile(cmd):
            return True
        if open(cmd).read() != "\0".join(self.argv):
            return True
        return os.path.getmtime(self.out) < self.deps_t

    def run(self):
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        nice = ["nice", "-n", "15"] if shutil.which("nice") else []
        p = subprocess.run(nice + self.argv, capture_output=True, text=True)
        if p.returncode:
            return "%s\n$ %s\n%s" % (os.path.basename(self.out), " ".join(self.argv), (p.stdout + p.stderr)[-6000:])
        with open(self.out + ".cmd", "w") as f:
            f.write("\0".join(self.argv))
        return None


def run_jobs(jobs, n):
    todo = [j for j in jobs if j.stale()]
    if not todo:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        for err in ex.map(lambda j: j.run(), todo):
            if err:
                die("compile failed: " + err)


def build_arch(arch, zig, d, args, out_dir):
    """the engine and the wrapper for one target; returns the library's path"""
    obj = os.path.join(out_dir, "obj", arch)
    if arch == "native":
        tgt, coro = [], ["plugin/src/coro/coro_arm64.S", "plugin/src/coro/coro_x86_64.S"]
        lib = os.path.join(out_dir, "native", SLUG + (".dylib" if IS_MAC else ".so"))
    else:
        t, cpu, c = ARCH[arch]
        tgt, coro = ["-target", t, "-mcpu=" + cpu], [c]
        lib = os.path.join(out_dir, "stage", SLUG + "-" + arch + ".so")
    # (macOS: zig's own Mach-O linker has no LTO; the native test build goes without: the audio is the same either
    # way, as the desktop plugin's Gate D shows for its LTO switch)
    lto = [] if args.no_lto or (arch == "native" and IS_MAC) else ["-flto=full"]
    defs = ["-DFR_VOICES=%du" % args.voices, "-DFRP_VOICES=%du" % args.voices, "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1",
            "-DFR_REV_HQ=1", '-DAPP_VER="%s"' % args.version]
    src_t = newest([os.path.join(ROOT, p) for p in ("firmware/src", "firmware/gen", "plugin/src", "plugin/compat",
                                                    "plugin/gen", "plugin/frp_ui_iface.h", "web", "move/move_presets.h",
                                                    "mpc/sdk", "mpc/src")])
    inc_slot = ["-I" + os.path.join(ROOT, p) for p in ("plugin/compat", "firmware/src", "firmware/gen")]
    inc_eng = ["-I" + os.path.join(ROOT, p) for p in ("plugin/src", "plugin/gen", "firmware/src")]
    # the compilers: zig's clang for the MPC targets (the SDK's toolchain); this machine's own for the native test
    # build on macOS (Apple clang: what the desktop plugin is built with; zig cannot link against macOS's libraries
    # without more setup)
    native_mac = arch == "native" and IS_MAC
    ccc = ["cc"] if native_mac else zig + ["cc"]
    cxx = ["c++"] if native_mac else zig + ["c++"]
    jobs, objs = [], []
    for n in range(SLOTS):
        o = os.path.join(obj, "slot%d.o" % n)
        jobs.append(Job(o, ccc + tgt + ENGINE_FLAGS + lto + defs + ["-DFRP_SLOT=%d" % n] + inc_slot +
                        ["-c", os.path.join(ROOT, "plugin/src/engine_unit.c"), "-o", o], src_t))
        objs.append(o)
    for s in ("plugin/src/bridge.c", "plugin/src/frp_paths.c"):
        o = os.path.join(obj, os.path.basename(s)[:-2] + ".o")
        jobs.append(Job(o, ccc + tgt + ENGINE_FLAGS + defs + inc_eng + ["-c", os.path.join(ROOT, s), "-o", o], src_t))
        objs.append(o)
    for s in coro:
        o = os.path.join(obj, os.path.basename(s)[:-2] + ".o")
        jobs.append(Job(o, ccc + tgt + ["-fPIC", "-c", os.path.join(ROOT, s), "-o", o], src_t))
        objs.append(o)
    o = os.path.join(obj, "frodhes_mpc.o")
    jobs.append(Job(o, cxx + tgt + CXX_FLAGS + defs + ['-DFRP_VERSION="%s"' % args.version,
                    "-I" + os.path.join(MPC, "sdk")] + inc_eng + ["-c", os.path.join(MPC, "src/frodhes_mpc.cpp"), "-o", o], src_t))
    objs.append(o)
    log("%-6s compile %d objects (-j%d)" % (arch, len(jobs), args.jobs))
    run_jobs(jobs, args.jobs)
    link = cxx + tgt + ["-shared", "-fPIC", "-O2"] + lto + objs + ["-o", lib]
    if arch == "native":
        if not IS_MAC:
            link += ["-Wl,--version-script=" + os.path.join(d, "tools", "exports.map"), "-lpthread", "-lm"]
    else:
        link += ["-Wl,--gc-sections", "-Wl,-s", "-Wl,--version-script=" + os.path.join(d, "tools", "exports.map"),
                 "-Wl,-z,noexecstack", "-lpthread", "-lm"]
    job = Job(lib, link, max(src_t, newest(objs)))
    log("%-6s link -> %s" % (arch, os.path.relpath(lib, ROOT)))
    run_jobs([job], 1)
    return lib


# ---- the host simulator, the skins, the folder
def test_tools(d, zig, out_dir, arch):
    """the test hosts for one target: vst2_render (mpc/tests), host_sim (the SDK's) and the coroutine's own test
    (plugin/tests/test_coro.c); native in out/native, the ARM ones in out/stage/<arch> (run in Docker: docs/MPC.md)"""
    if arch == "native":
        cxx, cc = (["c++"], ["cc"]) if IS_MAC else (zig + ["c++"], zig + ["cc"])
        tgt, coro, where = [], ["plugin/src/coro/coro_arm64.S", "plugin/src/coro/coro_x86_64.S"], os.path.join(out_dir, "native")
    else:
        t, cpu, c = ARCH[arch]
        cxx, cc = zig + ["c++"], zig + ["cc"]
        tgt, coro, where = ["-target", t, "-mcpu=" + cpu], [c], os.path.join(out_dir, "stage", arch)
    libs = [] if IS_MAC and arch == "native" else ["-ldl", "-lpthread", "-lm"]
    vr = os.path.join(MPC, "tests", "vst2_render.cpp")
    hs = os.path.join(d, "tools", "host-sim", "host_sim.cpp")
    tc = os.path.join(ROOT, "plugin", "tests", "test_coro.c")
    jobs = [Job(os.path.join(where, "vst2_render"), cxx + tgt + ["-std=c++17", "-O2", "-I" + os.path.join(MPC, "sdk"),
                "-o", os.path.join(where, "vst2_render"), vr] + libs, newest([vr, os.path.join(MPC, "sdk")])),
            Job(os.path.join(where, "host_sim"), cxx + tgt + ["-std=c++17", "-O2", "-I" + os.path.join(d, "framework"),
                "-o", os.path.join(where, "host_sim"), hs] + libs, newest([hs])),
            Job(os.path.join(where, "test_coro"), cc + tgt + ["-O2", "-o", os.path.join(where, "test_coro"), tc] +
                [os.path.join(ROOT, s) for s in coro], newest([tc, os.path.join(ROOT, "plugin/src/coro")]))]
    run_jobs(jobs, 2)
    return where


def skins(params_json, folder):
    """Plugin Skins/: the hand-drawn skin in mpc/skins/ (mpc/tools/make_skin.py renders it from mpc/skin.json),
    copied as it is, after checking it matches mpc/skin.json and the plugin's parameters"""
    out = os.path.join(folder, "Plugin Skins")
    if os.path.isdir(out):
        shutil.rmtree(out)
    p = subprocess.run([sys.executable, os.path.join(MPC, "tools", "make_skin.py"), "--check", "--params", params_json],
                       capture_output=True, text=True)
    if p.returncode:
        die("the skin:\n" + p.stdout + p.stderr)
    os.makedirs(out)
    for fn in sorted(os.listdir(os.path.join(MPC, "skins"))):
        if fn.endswith((".png", ".json")):
            shutil.copyfile(os.path.join(MPC, "skins", fn), os.path.join(out, fn))


def pad_version(v):
    nums = [int(x) for x in re.findall(r"\d+", v.split("-")[0])][:4]
    return ".".join(str(x) for x in (nums + [0, 0, 0, 0])[:4])


INSTALL = """Frodhes {ver} for Akai MPC (standalone)
=====================================================================

Frodhes is a physical-model electric piano, vibraphone and marimba with its own effects: the sound engine of the
Frodhes firmware for the M-VAVE FM-1, here as an instrument plugin for MPC standalone hardware. No samples: every
note is computed. HQ engine and effects, 16 voices on every instrument.

Frodhes runs on Akai MPC standalone hardware (checked by a tester on an MPC); it is also tested on a computer, in the
MPC Plugin SDK's host simulator. Reports are welcome: see "What to report back" below.

NEEDS
- MPC OS 3.10.1 or later (Preferences > Info shows the version).
- Preferences > Enhancements (Beta): switch on "Enable Enhancements" AND "External Plugin Support".
  Until both are on, external plugins stay hidden.

INSTALL
1. Copy the folder "{folder}" from this zip into a folder named exactly
       VST2 Plugins
   at the top level of a USB drive, an SD card or the MPC's internal drive (create "VST2 Plugins" if needed):
       <drive>/VST2 Plugins/{folder}/plugin.json
       <drive>/VST2 Plugins/{folder}/VST/frodhes-arm32.so  (and frodhes-arm64.so)
       <drive>/VST2 Plugins/{folder}/Plugin Skins/...
   Keep the folder's name exactly as it is: the MPC finds the plugin's pages by it.
2. Insert the drive. If Frodhes does not appear, restart the MPC (a rescan happens at start).
3. Make a Plugin program (or a plugin track) and pick Frodhes from the plugin browser under "External Plugins".
   Play it from the pads or a keyboard. The presets F01-F57 are in the plugin's preset list.

REMOVE IT (also if it misbehaves)
- Delete the folder "{folder}" from "VST2 Plugins" and restart the MPC. Projects that used it still open, without
  its sound. If the MPC freezes when you load the plugin, power it off, take out the drive that holds the folder,
  start the MPC, then delete the folder on a computer.
- Save your work before trying it. A plugin runs inside the MPC software: a fault in it can stop the MPC.
  Third-party plugins are not supported by Akai.

WHAT TO REPORT BACK (thank you!)
1. Your MPC model (e.g. MPC One, Live II, Live III, Key 61) and MPC OS version (Preferences > Info).
2. Does Frodhes appear and load? Do its pages show (VOICE, PLAY, COMP EQ, ... REVERB) and do the Q-Link knobs
   move them?
3. The CPU meter (the MPC's CPU display) with one Frodhes playing:
   a) about 10 voices: hold a 10-note chord, or play quickly with the sustain pedal down
   b) the most it can do: 16 voices (hold the sustain pedal and play 16 or more keys), with REVERB TYPE HALL,
      DELAY on and CHORUS on
   and, if you can, the same with 2 or 3 Frodhes tracks at once.
4. Any clicks, crackles, dropouts, hanging notes or freezes, and what you were doing when they happened (the
   preset, the number of keys, the audio buffer size in Preferences > Audio if you know it).
5. Does a saved project bring the sound back exactly (save, reload)?

Frodhes is free software under the GNU General Public License version 3 (LICENSE.txt). The source code:
https://github.com/leonardoMirabal/frodhes (SOURCE.txt).
"""

SOURCE = """Frodhes {ver} for Akai MPC.
The complete source code of this plugin (the engine, the MPC wrapper in mpc/ and the build script mpc/build.py) is
at https://github.com/leonardoMirabal/frodhes . Build it with  make mpc  (docs/MPC.md).
"""


def notices(d):
    return ("THIRD-PARTY NOTICES\n\nThe plugin binaries contain:\n\n"
            "1. The MPC Plugin SDK's framework headers (mpc_plugin.h, mpc_vst2_abi.h, mpc_resources.h), commit %s,\n"
            "   https://github.com/inmusic-software/mpc-plugin-builder-sdk , under the MIT licence:\n\n%s\n"
            "2. The C++ runtime of the zig toolchain (LLVM libc++, libc++abi, libunwind, compiler-rt), linked in, under\n"
            "   the Apache License 2.0 with LLVM Exceptions (https://llvm.org/LICENSE.txt).\n\n"
            "Plugin Skins/ holds images drawn by Frodhes's own mpc/tools/make_skin.py (GPL-3.0-only, as the plugin); the\n"
            "text in them is set in Inter Tight and Michroma (SIL Open Font License 1.1, assets/fonts/ in the source).\n"
            % (SDK_COMMIT, open(os.path.join(MPC, "sdk", "LICENSE")).read()))


def main():
    ap = argparse.ArgumentParser(description="Build Frodhes for the Akai MPC (docs/MPC.md)")
    ap.add_argument("--target", default="all", choices=["all", "native", "arm32", "arm64"])
    ap.add_argument("--version", default="0.0.0-dev", help="the version text (a release's X.Y.Z)")
    ap.add_argument("--voices", type=int, default=16, help="voices on every engine (16: the Move's; 24: the desktop "
                    "plugin's, for the bit-exactness test)")
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "mpc"))
    ap.add_argument("--no-lto", action="store_true", help="no LTO on the slots (faster, larger)")
    ap.add_argument("--no-fetch", action="store_true", help="never clone the SDK (exit 2 without it)")
    ap.add_argument("--tools", action="store_true", help="also the ARM test hosts (vst2_render, host_sim, test_coro) "
                    "in build/mpc/stage/<arch>, for the Docker runs (tests/py/test_mpc.py)")
    args = ap.parse_args()
    if not re.match(r"^[0-9A-Za-z.\-]+$", args.version):
        die("--version: letters, digits, dots and dashes", 2)
    d = sdk_dir(fetch=not args.no_fetch)
    if not d:
        die("no MPC Plugin SDK checkout (.local/mpc-sdk or $MPC_SDK)", 2)
    check_sdk(d)
    zig, zver = find_zig(d)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    log("Frodhes MPC %s: SDK %s, zig %s, %d voices, out %s" % (args.version, SDK_COMMIT[:12], zver, args.voices, out))
    native = build_arch("native", zig, d, args, out)
    sim = os.path.join(test_tools(d, zig, out, "native"), "host_sim")
    if args.target == "native":
        return 0
    archs = ["arm32", "arm64"] if args.target == "all" else [args.target]
    libs = {a: build_arch(a, zig, d, args, out) for a in archs}
    if args.tools:
        for a in archs:
            test_tools(d, zig, out, a)
    params_json = os.path.join(out, "params.json")
    p = subprocess.run([sim, native, "dump"], capture_output=True, text=True, timeout=300)
    if p.returncode:
        die("host_sim dump failed:\n" + p.stderr[-3000:])
    with open(params_json, "w") as f:
        f.write(p.stdout)
    if args.target != "all":
        return 0
    folder = os.path.join(out, FOLDER)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "VST"))
    manifest = json.load(open(os.path.join(MPC, "plugin.json")))
    manifest["version"] = ".".join(re.findall(r"\d+", args.version.split("-")[0])[:3]) or "0.0.0"   # (digits
    # and dots, as the MPC and validate.py want; a dev build's "0.0.0-dev" is "0.0.0" here)
    with open(os.path.join(folder, "plugin.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    for a, lib in libs.items():
        shutil.copyfile(lib, os.path.join(folder, "VST", "%s-%s.so" % (SLUG, a)))
    skins(params_json, folder)
    with open(os.path.join(folder, "version.xml"), "w", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<plugincontent version="1.0">\n    <version>%s</version>\n'
                '    <uiversion>0.1.0.0</uiversion>\n</plugincontent>\n' % pad_version(args.version))
    v = subprocess.run([sys.executable, os.path.join(d, "tools", "validate.py"), folder, "--params", params_json],
                       capture_output=True, text=True)
    log(v.stdout.rstrip())
    if v.returncode:
        die("validate.py failed:\n" + v.stderr)
    zpath = os.path.join(out, "frodhes-mpc.zip")
    def put(zf, name, data):   # (a fixed date and mode: the same inputs give the same zip, byte for byte)
        zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
        zf.writestr(zi, data)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for r, dirs, files in os.walk(folder):
            dirs.sort()
            for fn in sorted(files):
                if fn in (".DS_Store", "Thumbs.db"):
                    continue
                full = os.path.join(r, fn)
                put(zf, os.path.join(FOLDER, os.path.relpath(full, folder)).replace(os.sep, "/"), open(full, "rb").read())
        put(zf, "INSTALL.txt", INSTALL.format(ver=args.version, folder=FOLDER))
        put(zf, "SOURCE.txt", SOURCE.format(ver=args.version))
        put(zf, "LICENSE.txt", open(os.path.join(ROOT, "LICENSE")).read())
        put(zf, "THIRD-PARTY-NOTICES.txt", notices(d))
    for a in archs:
        f = os.path.join(folder, "VST", "%s-%s.so" % (SLUG, a))
        log("%-6s %s  %.1f MB  sha256 %s" % (a, os.path.relpath(f, ROOT), os.path.getsize(f) / 1e6,
                                             hashlib.sha256(open(f, "rb").read()).hexdigest()))
    log("zip    %s  sha256 %s" % (os.path.relpath(zpath, ROOT), hashlib.sha256(open(zpath, "rb").read()).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
