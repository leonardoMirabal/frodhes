#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The stub proof (MARIMBA PLAN §8.1 item 4), re-runnable: the per-type plumbing in bars.c cannot alter a sample.

    tools/marimba_stub_proof.py

Writes the --stub header (type 1 = an exact copy of VIBES's tables, tools/gen_bars.py --stub) into build/stub_proof/,
builds the host binary against it (the Makefile's flags, that directory first on the include path), and renders every
tests/scripts/*.txt that selects VIBES by CC 79 0 twice: as written (TYPE 4) and with each CC 79 0 replaced by CC 79
127 (TYPE 5, the stub). Every pair must be byte-identical. Exit 1 on any difference."""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "stub_proof")
PY = sys.executable


def main():
    os.makedirs(OUT, exist_ok=True)
    gen = os.path.join(OUT, "gen")
    if not os.path.exists(os.path.join(gen, "bars_tables.h")) or "--regen" in sys.argv:
        subprocess.run([PY, os.path.join(ROOT, "tools", "gen_bars.py"), "--stub", "--out", gen], check=True,
                       capture_output=True, cwd=ROOT)
    hdr = open(os.path.join(gen, "bars_tables.h")).read()
    assert "1 STUB" in hdr, "not the stub header"
    binary = os.path.join(OUT, "frodhes")
    subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', "-I" + gen,
                    "-Ifirmware/src", "-Ifirmware/gen", "-o", binary, "firmware/src/frodhes.c", "-lm"], check=True,
                   cwd=ROOT)
    sel = re.compile(r"^(\d+ midi 0xB0 79) 0$", re.M)
    same, diff = [], []
    for name in sorted(os.listdir(os.path.join(ROOT, "tests", "scripts"))):
        if not name.endswith(".txt"):
            continue
        src = open(os.path.join(ROOT, "tests", "scripts", name)).read()
        if not sel.search(src) or "79 127" in src or "79 96" in src or "0xC0" in src:   # (a PC loads a preset
            # with TYPE 4 while TYPE 5 rings: the bar-type fade, by design)
            continue
        for mot in (0, 127):                      # the motor set explicitly right after every TYPE selection: P3
            # gives TYPE 5 MARIMBA's parameter meaning (params.c TYPE_FLAGS: no auto motor-on), which the stub's
            # engine rows (flags 3) do not change; the proof is about the engine's per-type plumbing
            fix = re.sub(r"^(\d+)( midi 0xB0 79 \d+)$", lambda m: "%s%s\n%s midi 0xB0 47 %d" % (
                m.group(1), m.group(2), m.group(1), mot), src, flags=re.M)
            shas = []
            for tag, text in (("t4", fix), ("t5", sel.sub(r"\1 127", fix))):
                base = os.path.join(OUT, "%s-m%d-%s" % (name[:-4], mot, tag))
                open(base + ".txt", "w").write(text)
                subprocess.run([binary, base, base + ".txt"], capture_output=True, cwd=ROOT)
                shas.append(hashlib.sha256(open(base + ".wav", "rb").read()).hexdigest())
                os.remove(base + ".wav")
            (same if shas[0] == shas[1] else diff).append("%s (motor %d)" % (name, mot))
    print("stub proof: %d of %d VIBES script renders byte-identical as TYPE 5 (the stub)%s" % (
        len(same), len(same) + len(diff), "; differ: " + ", ".join(diff) if diff else ""))
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
