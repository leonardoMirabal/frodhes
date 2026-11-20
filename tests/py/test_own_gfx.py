# SPDX-License-Identifier: GPL-3.0-only
"""The plugin's own screen calls (plugin/src/own_gfx.h) against the screen stack they replace (gfx.c, aatext.c and the
font data): tests/own_gfx_probe.c, the app as the plugin builds it, runs the main loop with every page walked and every
knob's parameter turned, once with the real text widths (-DFR_OWN=0) and once with every text 0 px wide (-DFR_OWN=1).
The hash of the settings, the page and every page's texts, labels, values and ranges after each step must be the same:
no state decision depends on a text's width. Also: a slot object of the own-code build has no font, glyph or aa_ symbol."""
import os
import re
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "own_gfx")
CC = ["cc", "-std=gnu11", "-w", "-ffp-contract=off", "-DFR_VOICES=24u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1",
      "-DFR_REV_HQ=1", '-DAPP_VER="t"', "-I" + os.path.join(ROOT, "plugin", "compat"),
      "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen")]


def probe(own):
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"probe{own}.{os.getpid()}")      # (a name of its own per process)
    r = subprocess.run(CC + ["-O1", f"-DFR_OWN={own}", "-o", exe, os.path.join(ROOT, "tests", "own_gfx_probe.c"), "-lm"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    r = subprocess.run([exe], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0
    return r.stdout.splitlines()


def test_no_state_depends_on_text_width():
    a, b = probe(0), probe(1)
    assert len(a) > 50 and a[-1].startswith("done 3000 ")
    assert len({x.split()[2] for x in a if x.startswith("pass")}) > 40      # (the hash moves: the walk changes state)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert not diff and len(a) == len(b), diff[:5]


def test_slot_object_has_no_font_symbols():
    obj = os.path.join(OUT, f"slot.{os.getpid()}.o")
    os.makedirs(OUT, exist_ok=True)
    r = subprocess.run(CC + ["-O2", "-DFR_OWN=1", "-DFRP_SLOT=0", "-c", "-o", obj,
                             os.path.join(ROOT, "plugin", "src", "engine_unit.c")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    names = subprocess.run(["nm", obj], capture_output=True, text=True).stdout.split()
    bad = [n for n in names if re.match(r"^_?(FONT_|aa_|AF_|AA_|glyph|cv_px|felucca)", n)]
    assert not bad, bad
