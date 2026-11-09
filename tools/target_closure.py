#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Which targets a file is part of: the FM-1, the SMK-37, the host tests, the web emulator, the Move, the Move's HQ
code, the docs. Computed from the code (the preprocessor's dependency list, -M -MG, with each target's defines), so
the table in private/plans/05-move-implementation.md section 3.3 cannot go stale.

    tools/target_closure.py [--json] FILE ...     the targets each file affects
    tools/target_closure.py --table [--json]      every target's files

A file in more than one target's closure is shared code: a change to it is classified by the plan's section 3.5
(S-DSP, S-APP, ...). A file in none (tools/, tests/ but tests/host.c, the CI) affects the host tier only.

 fm1    ./build.sh: frodhes.c and the loader with the build's defines, the startup, the link scripts, the packaging
 smk    ./build.sh --board smk37: the same with BOARD_SMK37=1 FR_VOICES=10u
 host   make test: frodhes.c with DINGHY_HOST (platform.c -> tests/host.c), the Makefile, the test scripts
 web    web/build.sh: DINGHY_HOST DINGHY_WEB (platform.c -> web/web_hal.c), the page
 move   make move: move/move_plugin.c (or, until it exists, frodhes.c) with DINGHY_HOST DINGHY_WEB DINGHY_MOVE
        FR_VOICES=16u, and everything under move/
 hq     what the Move compiles with FR_HQ=1 and not without it, and firmware/src/hq/, firmware/gen/hq/
 docs   README.md, docs/, the other *.md at the top, and the sources the doc tests and the screenshots read
        (tests/py/test_docs_vs_code.py src("..."), DOC_SOURCES)
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INC = ["-Ifirmware/hal", "-Ifirmware/src", "-Ifirmware/gen"]
APP = ["-DFELUCCA_ID=\"FM-1_958\"", "-DAPP_VER=\"0.0\""]
MOVE_DEFS = ["-DDINGHY_HOST", "-DDINGHY_WEB", "-DDINGHY_MOVE", "-DFR_VOICES=16u", "-DFR_REV_HQ=1"]   # (the Makefile's MOVE_DEFS: the full-rate reverb)
# (target, [(the C file, its flags)], the other files it is built from)
FIRMWARE_FILES = ["firmware/crt0.S", "firmware/hal/fm1_vec.S", "firmware/hal/fm1_isr.S", "firmware/app.ld",
                  "firmware/loader/crt0_ldr.S", "firmware/loader/loader.ld", "build.sh", "tools/build.py",
                  "tools/fm1pkg_make.py", "tools/lz4blk.py"]
DOC_SOURCES = ["firmware/src/ui.c", "firmware/src/eng_pages.h", "firmware/src/params.c", "tools/manual_shots.py"]
TARGETS = ("fm1", "smk", "host", "web", "move", "hq", "docs")


def run_m(cfile, flags):
    """the files cfile includes with these flags (relative to ROOT, normalized); -MG: a missing header (the
    emulator's emscripten.h) is listed, not an error"""
    cc = os.environ.get("CC", "cc")
    r = subprocess.run([cc, "-M", "-MG", *flags, cfile], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("target_closure: %s -M %s failed:\n%s" % (cc, cfile, r.stderr[-2000:]))
    deps = r.stdout.replace("\\\n", " ").split(":", 1)[1].split()
    out = set()
    for d in deps:
        p = os.path.normpath(d if os.path.isabs(d) else os.path.join(ROOT, d))
        if p.startswith(ROOT + os.sep) and os.path.exists(p):    # (a missing header -MG listed: not a file of ours)
            out.add(os.path.relpath(p, ROOT).replace(os.sep, "/"))
    return out


def tree(*dirs):
    """the files under these directories (git's list when there is one, else a walk), relative to ROOT"""
    out = set()
    for d in dirs:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for base, _, files in os.walk(full):
            for f in files:
                if not f.startswith(".") and "__pycache__" not in base:
                    out.add(os.path.relpath(os.path.join(base, f), ROOT).replace(os.sep, "/"))
    return out


def existing(files):
    return {f for f in files if os.path.exists(os.path.join(ROOT, f))}


def doc_sources():
    t = os.path.join(ROOT, "tests", "py", "test_docs_vs_code.py")
    names = re.findall(r'src\("([^"]+)"\)', open(t).read()) if os.path.exists(t) else []
    return {"firmware/src/" + n for n in names} | set(DOC_SOURCES)


def closures():
    """target -> the set of files (relative to ROOT)"""
    fw = "firmware/src/frodhes.c"
    ldr = "firmware/loader/loader.c"
    c = {}
    c["fm1"] = run_m(fw, INC + APP) | run_m(ldr, ["-Ifirmware/hal", "-Ifirmware/src"]) | existing(FIRMWARE_FILES)
    smk = ["-DBOARD_SMK37=1"]
    c["smk"] = (run_m(fw, smk + ["-DFR_VOICES=10u"] + INC + APP + ['-DDATA_FAMILY="FRODHES"'])
                | run_m(ldr, smk + ["-Ifirmware/hal", "-Ifirmware/src"]) | existing(FIRMWARE_FILES))
    c["host"] = run_m(fw, ["-DDINGHY_HOST"] + INC) | existing(["Makefile"]) | tree("tests/scripts")
    c["web"] = run_m(fw, ["-DDINGHY_HOST", "-DDINGHY_WEB"] + INC) | {f for f in tree("web") if not f.startswith("web/dist/")}
    plugin = "move/move_plugin.c"
    src = plugin if os.path.exists(os.path.join(ROOT, plugin)) else fw
    move_inc = INC + ["-Imove"]
    classic = run_m(src, MOVE_DEFS + move_inc)
    hq = run_m(src, MOVE_DEFS + ["-DFR_HQ=1"] + move_inc)
    c["move"] = classic | hq | tree("move")
    c["hq"] = (hq - classic) | tree("firmware/src/hq", "firmware/gen/hq")
    c["docs"] = ({f for f in tree("docs")} | existing(["README.md", "CONTRIBUTING.md", "LICENSING.md"])
                 | existing(doc_sources()))
    return c


def targets_of(path, c):
    p = os.path.normpath(path).replace(os.sep, "/")
    if os.path.isabs(p):
        p = os.path.relpath(p, ROOT).replace(os.sep, "/")
    return [t for t in TARGETS if p in c[t]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--table", action="store_true", help="every target's files")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    c = closures()
    if a.table or not a.files:
        if a.json:
            print(json.dumps({t: sorted(c[t]) for t in TARGETS}, indent=1))
        else:
            for t in TARGETS:
                print("%-5s %4d files" % (t, len(c[t])))
                for f in sorted(c[t]):
                    print("      " + f)
        return 0
    res = {f: targets_of(f, c) for f in a.files}
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for f, ts in res.items():
            print("%s: %s" % (f, ", ".join(ts) if ts else "none (the host tier only)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
