# SPDX-License-Identifier: GPL-3.0-only
"""The emulator's sources compile (web/build.sh's flags, web/web_hal.c as the platform), with the host compiler and a
stub emscripten.h (tests/web_stub): web_hal.c stands in for set_store.c and the flash, so a firmware change that it
does not follow broke web/build.sh only, found late (BUGHUNT #40's st_id, settings_old_id, set_at / set_seq)."""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_web_sources_compile():
    cmd = [os.environ.get("CC", "cc"), "-fsyntax-only", "-DDINGHY_HOST", "-DDINGHY_WEB", "-DHOST_SAVE_MS=1u",
           '-DAPP_VER="dev"', "-Itests/web_stub", "-Ifirmware/src", "-Ifirmware/gen", "-Wno-unused-function",
           "-Wno-unused-variable", "-Wno-macro-redefined", "firmware/src/frodhes.c"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


def build_sh_defs():
    """the -D flags web/build.sh gives emcc"""
    import re
    src = open(os.path.join(ROOT, "web", "build.sh")).read()
    line = [l for l in src.splitlines() if l.startswith("emcc ")][0]
    return re.findall(r"-D\w+(?:=\S+)?", line)


def test_web_is_the_two_core_image():
    """2026-11-15 (the user): the emulator plays the FM-1's package, the two-core image (14 voices, the full-rate
    reverb): web/build.sh builds FR_C1 + FR_C1_LAT, and frodhes.c allows it; the sources compile with those flags"""
    d = build_sh_defs()
    assert "-DFR_C1=1" in d and "-DFR_C1_LAT=1" in d and "-DDINGHY_WEB" in d, d
    cmd = [os.environ.get("CC", "cc"), "-fsyntax-only", *[x for x in d if not x.startswith("-DAPP_VER")],
           "-DHOST_SAVE_MS=1u", '-DAPP_VER="dev"', "-Itests/web_stub", "-Ifirmware/src", "-Ifirmware/gen",
           "-Wno-unused-function", "-Wno-unused-variable", "-Wno-macro-redefined", "firmware/src/frodhes.c"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


def test_web_audio_blocks_stay_256_frames():
    """app.js asks web_audio() for blocks of 256 frames; on the two-core image (128-frame interrupts) each block is two
    interrupts, written one after the other (web_out holds 16 blocks of 256 frames whatever HALF_FRAMES is)"""
    src = open(os.path.join(ROOT, "web", "web_hal.c")).read()
    assert "static float web_out[2u * 256u * 16u];" in src
    assert "blocks * (256u / HALF_FRAMES)" in src
