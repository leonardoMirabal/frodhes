#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin is self-contained: each binary needs nothing but the operating system.

    tools/plugin_depcheck.py macos   PATH...    the .clap / .vst3 / .component bundles (or a folder holding them)
    tools/plugin_depcheck.py windows PATH...    Frodhes.clap, Frodhes.vst3 (the bundle folder), or a folder
    tools/plugin_depcheck.py linux   PATH...    [--glibc 2.31]

For every plugin binary found under PATH it checks, and fails (exit 1) on anything else:
  macos    universal (arm64 + x86_64); each slice's minimum macOS <= 11.0; linked only to /usr/lib/libSystem,
           libc++, libobjc and /System/Library/Frameworks (otool -L): no @rpath, no Homebrew, no other dylib
  windows  imports only Windows' own DLLs (kernel32, user32, gdi32 ...): the C runtime is linked in statically (/MT),
           so no VCRUNTIME / MSVCP / ucrtbase / api-ms-win-crt-*, and no libc++ / libgcc DLL (llvm-objdump -p)
  linux    NEEDED only libc, libm, libdl, libpthread, librt, the loader, libX11 and libxcb; every GLIBC_ symbol
           version <= --glibc (the build image's baseline); no GLIBCXX_ / CXXABI_ / GCC_ versions (libstdc++ and
           libgcc are linked in) (readelf -d, readelf -V)
  all      the format's entry is exported (clap_entry; GetPluginFactory; bundleEntry / InitDll / ModuleEntry)
Imports of dlopen / LoadLibrary are listed (info only: the OS's own loaders, used by the wrapper's host code).
Tools: otool / lipo (macOS); llvm-objdump (FRP_OBJDUMP, else PATH, else Homebrew llvm@20);
readelf or llvm-readelf (FRP_READELF, else PATH)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC_OK = re.compile(r"^(/usr/lib/libSystem\.B\.dylib|/usr/lib/libc\+\+\.1\.dylib|/usr/lib/libobjc\.A\.dylib|"
                    r"/System/Library/Frameworks/[A-Za-z0-9]+\.framework/.+)$")
WIN_OK = re.compile(r"^(kernel32|user32|gdi32|shell32|ole32|oleaut32|advapi32|comdlg32|comctl32|uxtheme|dwmapi|"
                    r"shcore|imm32|winmm|version|opengl32|d2d1|dwrite|windowscodecs|shlwapi|ws2_32|bcrypt|"
                    r"api-ms-win-core-[a-z0-9-]+)\.dll$", re.I)   # (no ucrtbase / api-ms-win-crt: the static CRT, /MT)
LINUX_OK = re.compile(r"^(libc\.so\.6|libm\.so\.6|libdl\.so\.2|libpthread\.so\.0|librt\.so\.1|"
                      r"ld-linux[-a-z0-9_.]*\.so\.[12]|libX11\.so\.6|libxcb\.so\.1)$")
ENTRY = {"clap": ["clap_entry"], "vst3": ["GetPluginFactory"], "component": []}
ENTRY_OS = {"macos": {"vst3": ["bundleEntry"]}, "windows": {"vst3": ["InitDll"]}, "linux": {"vst3": ["ModuleEntry"]}}
fails = []


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def tool(env, *names):
    if os.environ.get(env):
        return os.environ[env]
    for n in names:
        p = shutil.which(n) or shutil.which(n, path="/opt/homebrew/opt/llvm@20/bin:/opt/homebrew/opt/llvm/bin")
        if p:
            return p
    sys.exit(f"plugin_depcheck: none of {names} found (set {env})")


def fail(b, msg):
    fails.append(f"{b}: {msg}")
    print(f"FAIL  {b}: {msg}")


def binaries(osname, paths):
    """(binary, format) for every plugin binary under the paths"""
    out = []
    for p in map(Path, paths):
        cands = [p] if p.suffix in (".clap", ".vst3", ".component") else \
            sorted(q for q in p.rglob("*") if q.suffix in (".clap", ".vst3", ".component"))
        for c in cands:
            fmt = c.suffix[1:]
            if osname == "macos":
                if c.is_dir() and (c / "Contents/MacOS").is_dir():
                    out += [(b, fmt) for b in sorted((c / "Contents/MacOS").iterdir())]
            elif c.is_file() and fmt == "clap":
                out.append((c, fmt))
            elif c.is_dir() and (c / "Contents").is_dir():
                pat = "x86_64-win/*.vst3" if osname == "windows" else "*-linux/*.so"
                out += [(b, fmt) for b in sorted((c / "Contents").glob(pat))]
    return sorted(set(out))


def check_macos(b, fmt):
    archs = run("lipo", "-archs", str(b)).split()
    if not {"arm64", "x86_64"} <= set(archs):
        fail(b, f"not universal: {' '.join(archs)}")
    for a in archs:
        libs = [ln.split(" (")[0].strip() for ln in run("otool", "-arch", a, "-L", str(b)).splitlines()[1:]]
        for lib in libs:
            if not MAC_OK.match(lib):
                fail(b, f"{a}: links {lib}")
        lc = run("otool", "-arch", a, "-l", str(b))
        m = re.search(r"cmd LC_BUILD_VERSION.*?minos ([0-9.]+)", lc, re.S) or \
            re.search(r"cmd LC_VERSION_MIN_MACOSX.*?version ([0-9.]+)", lc, re.S)
        mv = tuple(int(x) for x in m.group(1).split(".")) if m else (99,)
        if mv > (11, 0):
            fail(b, f"{a}: minimum macOS {m.group(1) if m else '?'} (want <= 11.0)")
        print(f"ok    {b.name} {a}: {len(libs)} system libraries, minimum macOS {m.group(1) if m else '?'}")
    syms = run("nm", "-gU", "-arch", archs[0], str(b))
    return set(re.findall(r" [TSD] _?(\w+)$", syms, re.M)), "dlopen" in run("nm", "-u", "-arch", archs[0], str(b))


def check_windows(b, fmt, objdump):
    txt = run(objdump, "-p", str(b))
    dlls = re.findall(r"DLL Name: (\S+)", txt)
    for d in dlls:
        if not WIN_OK.match(d):
            fail(b, f"imports {d}")
    if "file format coff-x86-64" not in run(objdump, "-f", str(b)):
        fail(b, "not x86_64 PE")
    print(f"ok    {b.name}: imports {', '.join(sorted(set(d.lower() for d in dlls)))}")
    exp = txt.split("Export Table:", 1)[1] if "Export Table:" in txt else ""
    return set(re.findall(r"^\s+\d+\s+0x[0-9a-f]+\s+(\w+)$", exp, re.M)), "LoadLibrary" in txt


def check_linux(b, fmt, readelf, glibc):
    dyn = run(readelf, "-d", "-W", str(b))
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", dyn)
    for n in needed:
        if not LINUX_OK.match(n):
            fail(b, f"needs {n}")
    vers = set(re.findall(r"Name: ((?:GLIBC|GLIBCXX|CXXABI|GCC)_[0-9.]+)", run(readelf, "-V", "-W", str(b))))
    worst = (0,)
    for v in vers:
        name, num = v.split("_", 1)
        t = tuple(int(x) for x in num.split("."))
        if name != "GLIBC":
            fail(b, f"needs {v} (the C++ runtime must be linked in)")
        elif t > glibc:
            fail(b, f"needs {v} (baseline GLIBC_{'.'.join(map(str, glibc))})")
        worst = max(worst, t) if name == "GLIBC" else worst
    print(f"ok    {b.name}: needs {', '.join(needed)}; newest GLIBC_{'.'.join(map(str, worst))}")
    syms = run(readelf, "--dyn-syms", "-W", str(b))
    exported = set(re.findall(r"\s(?:FUNC|OBJECT)\s+GLOBAL\s+DEFAULT\s+\d+\s+(\w+)$", syms, re.M))
    return exported, bool(re.search(r"UND\s+dlopen", syms))


def main(argv):
    if len(argv) < 2 or argv[0] not in ("macos", "windows", "linux"):
        sys.exit(__doc__)
    osname, rest = argv[0], argv[1:]
    glibc = (2, 31)
    if "--glibc" in rest:
        i = rest.index("--glibc")
        glibc = tuple(int(x) for x in rest[i + 1].split("."))
        del rest[i:i + 2]
    bins = binaries(osname, rest)
    if not bins:
        sys.exit(f"plugin_depcheck: no plugin binaries under {' '.join(rest)}")
    objdump = tool("FRP_OBJDUMP", "llvm-objdump") if osname == "windows" else None
    readelf = tool("FRP_READELF", "readelf", "llvm-readelf") if osname == "linux" else None
    for b, fmt in bins:
        if osname == "macos":
            exported, dl = check_macos(b, fmt)
        elif osname == "windows":
            exported, dl = check_windows(b, fmt, objdump)
        else:
            exported, dl = check_linux(b, fmt, readelf, glibc)
        for e in ENTRY[fmt] + ENTRY_OS[osname].get(fmt, []):
            if e not in exported:
                fail(b, f"does not export {e}")
        if dl:
            print(f"info  {b.name}: imports the OS loader (dlopen / LoadLibrary)")
    print(f"{'FAIL' if fails else 'ok'}    plugin_depcheck {osname}: {len(bins)} binaries, {len(fails)} problems")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
