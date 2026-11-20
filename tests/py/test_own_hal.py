# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's own hardware layer (plugin/src/own_hal.c, own_store.h) against the browser layer it replaces
(web/web_hal.c, store.h): tests/own_hal_probe.c compiled with -DFR_OWN=0 and -DFR_OWN=1 prints every shared value and
the behaviour of every call the app and the plugin's glue make; the two texts must be equal line for line. Also: the
FR_OWN_HAL build's include list has neither platform.c nor the browser layer."""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "own_hal")
CC = ["cc", "-O1", "-std=gnu11", "-w", "-ffp-contract=off", "-DFR_VOICES=24u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1",
      "-DFR_REV_HQ=1", '-DAPP_VER="t"', "-I" + os.path.join(ROOT, "plugin", "compat"), "-I" + os.path.join(ROOT, "firmware", "src"),
      "-I" + os.path.join(ROOT, "firmware", "gen")]


def probe(own):
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"probe{own}")
    # the last byte of the flash image, and (own layer) the way a slot clears its record at its boot
    extra = ["-DOWN_PROBE_LAST=0xFFFFFu"] + (["-DOWN_PROBE_RESET=OWN_STORE_RESET()"] if own else [])
    r = subprocess.run(CC + [f"-DFR_OWN={own}", *extra, "-o", exe, os.path.join(ROOT, "tests", "own_hal_probe.c"), "-lm"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0
    return r.stdout.splitlines()


def test_own_hal_equals_browser_layer():
    a, b = probe(0), probe(1)
    assert len(a) > 300
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert not diff and len(a) == len(b), diff[:5]


def test_own_hal_build_lists_no_platform_files():
    base = CC + ["-DFRP_SLOT=0", "-M", os.path.join(ROOT, "plugin", "src", "engine_unit.c")]
    deps = {}
    for own in (0, 1):
        r = subprocess.run(base + [f"-DFR_OWN={own}"], capture_output=True, text=True, cwd=ROOT)
        assert r.returncode == 0, r.stderr[-2000:]
        deps[own] = {os.path.basename(p) for p in r.stdout.replace("\\\n", " ").split()[1:]}
    old = {"platform.c", "web_hal.c"}
    assert old <= deps[0]                       # (the browser layer, as the MPC and the web build keep it)
    assert not old & deps[1], old & deps[1]
    assert "own_hal.c" in deps[1]
