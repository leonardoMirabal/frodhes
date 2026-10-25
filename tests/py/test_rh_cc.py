# SPDX-License-Identifier: GPL-3.0-only
"""rhodes.c's note-on coefficient cache (rh_cc_poll in the main loop, read by rh_init): the voice a note-on builds
from the cache is byte for byte the one rh_coef builds (every key x 4 velocities x TUNE / DECAY / RELEASE settings);
a stale cache (a knob moved, the main loop not yet past rh_cc_poll) and a bend fall back to rh_coef; the burst is
faster on the host (tests/c/rh_noteon.c). The whole engine stays byte-identical: Gate R (tools/gate_r.py). On the FM-1
build rh_init calls rh_coef (149 instructions) twice a live mode only without the cache."""
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))
import hostrun  # noqa: E402

OUT = os.path.join(ROOT, "build", "rh_cc", os.environ.get("PYTEST_XDIST_WORKER", "main"))


def exe():
    path = os.path.join(OUT, "rh_noteon")

    def make(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", "-DRH_VOICES=9u",
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", p, os.path.join(ROOT, "tests", "c", "rh_noteon.c")], check=True)
    return hostrun.once(path, make)


def test_cache_is_exact():
    """5 TUNEs x 4 DECAY/RELEASE x (fresh, stale, bend up, bend down) x 4 velocities x 88 keys: identical voices;
    only the fresh cache at no bend is used (5 x 4 x 4 x 88 = 7040 of 28160)"""
    out = subprocess.run([exe(), "check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
    assert out.stdout.split() == ["cases", "28160", "cached", "7040"]


def test_burst_faster():
    """a 9-note burst: rh_init from the cache at least twice as fast as with rh_coef with clang (host -O2; ~19 vs
    ~66 ns, macOS arm64; the firmware's compiler is LLVM's too). gcc builds the cached copy slower: 36.6-40.9 vs
    66.9-74.0 ns (1.75-1.9x, gcc 14.2 -O2, Linux arm64 in Docker, 6 runs, 2026-10-24), so with gcc the bar is 1.5x,
    still far from rh_coef's own cost. Each path at its best of 5 runs (the least disturbed)"""
    cached, live = [], []
    for _ in range(5):
        f = subprocess.run([exe(), "time", "20000"], capture_output=True, text=True, check=True).stdout.split()
        cached.append(float(f[1]))
        live.append(float(f[3]))
    ver = subprocess.run(["cc", "--version"], capture_output=True, text=True).stdout
    need = 2.0 if "clang" in ver else 1.5
    assert min(live) / min(cached) >= need, (min(live), min(cached), need, ver.splitlines()[:1])


def test_main_loop_polls():
    """the main loop runs rh_cc_poll every pass (frodhes.c), so a knob change reaches the cache"""
    src = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()
    loop = src[src.index("    for (;;) {", src.index("static void app_main(void)")):]
    assert re.search(r"^\s+rh_cc_poll\(\);", loop[:loop.index("\n    }\n}")], re.M)


def test_pickup_ram_probe():
    """-DRH_PU_RAM (the O10 probe: each pickup family's table copied to RAM at boot) sounds the same as the flash table:
    a note on each family (S8: the 1/16" family on keys <= 32 and >= 96, the 1/32" one between) rendered byte-identical
    with the flag and without"""
    def build(tag, defs):
        path = os.path.join(OUT, "pu_" + tag)

        def make(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', *defs,
                            "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                            "-o", p, os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"], check=True)
        return hostrun.once(path, make)
    os.makedirs(OUT, exist_ok=True)
    scr = os.path.join(OUT, "pu.txt")
    with open(scr, "w") as f:
        f.write("50 midi 0x90 28 110\n300 midi 0x90 60 100\n600 midi 0x90 100 90\n900 midi 0x80 28 0\n1500 end\n")
    wavs = []
    for tag, defs in (("flash", []), ("ram", ["-DRH_PU_RAM"])):
        out = os.path.join(OUT, "pu_" + tag + "_out")
        subprocess.run([build(tag, defs), out, scr], check=True, capture_output=True)
        wavs.append(open(out + ".wav", "rb").read())
    assert len(wavs[0]) > 100000 and wavs[0] == wavs[1]
