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
