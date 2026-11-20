# SPDX-License-Identifier: GPL-3.0-only
"""The DISPLAY page's colour, brightness and lights in the plugin's own-code build (FR_OWN_HAL): none of its code is
compiled (the preprocessed slot has none of its names), the three cards (COLOR, BRIGHTNESS, LEDS) are inert (no range,
no text, a turn changes nothing), and the settings record keeps its fields (palette, dim, leds)."""
import os
import re
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "build", "own_display")
CC = ["cc", "-std=gnu11", "-w", "-ffp-contract=off", "-DFR_VOICES=24u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1",
      "-DFR_REV_HQ=1", '-DAPP_VER="t"', "-I" + os.path.join(ROOT, "plugin", "compat"),
      "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen")]
NAMES = ["COLOR_ACC", "COLOR_NAME", "dim_scr", "dim_key", "dim_btn", "color_apply", "dim_apply", "dim_pct",
         "color_text", "cd_lighten", "LEDS_NAME", "LEDS_STORED", "LEDS_DISP", "leds_disp", "leds_sus_lit"]


def preprocessed(own):
    r = subprocess.run(CC + [f"-DFR_OWN={own}", "-DFRP_SLOT=0", "-E", "-P", os.path.join(ROOT, "plugin", "src", "engine_unit.c")],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def test_display_code_is_not_compiled():
    old, own = preprocessed(0), preprocessed(1)
    for n in NAMES:
        assert re.search(r"\b" + n + r"\b", old), n          # (the other builds keep it)
        assert not re.search(r"\b" + n + r"\b", own), n
    assert "PG_DISPLAY ? PG_" not in own and not re.search(r"ui_page == PG_MIDI \? PG_DISPLAY", own)


def test_display_cards_are_inert_and_the_record_keeps_its_fields():
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, f"probe.{os.getpid()}")      # (a name of its own per process)
    r = subprocess.run(CC + ["-O1", "-DFR_OWN=1", "-o", exe, os.path.join(ROOT, "tests", "own_gfx_probe.c"), "-lm"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    r = subprocess.run([exe], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0
    lines = [x for x in r.stderr.splitlines() if x.startswith("display")]
    assert lines == [f"display {i} hi 0 val 0 text [] set same" for i in range(3)], lines
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    assert all(re.search(rf"uint8_t [^;]*\b{f}\b", src) for f in ("palette", "dim", "leds"))
