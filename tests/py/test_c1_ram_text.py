# SPDX-License-Identifier: GPL-3.0-only
"""tools/build.py --c1: .ram_text keeps only core 1's idle loop, entry and ping and the flash driver; core 0's hot code
runs from flash in two-core builds (frodhes.c FR_RAM empty under FR_C1: with both cores busy, code read from RAM glitches
on this chip; the 0.9.10 RC soak's core-0 faults in rh_hd_step)."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build  # noqa: E402

C1_RT = ["fr_c1_entry", "fr_c1_main", "c1_stop_here", "c1_ping", "fl_jedec_ram", "fl_status_ram", "fl_erase4k_ram",
         "fl_prog_ram", "fl_read_ram", "fl_start_ram", "fl_resume_ram"]


def test_flash_driver_and_core1_only_pass():
    errors, notes = build.c1_check(C1_RT)
    assert errors == [], errors


def test_core0_code_in_ram_text_fails():
    errors, _ = build.c1_check(C1_RT + ["rh_hd_step", "bar_pair", "uac_tap_lock"])   # (the 0.9.10 RC's .ram_text)
    assert any("core 0 code in .ram_text" in e and "rh_hd_step" in e for e in errors), errors


def test_fr_ram_empty_under_fr_c1():
    src = open(os.path.join(ROOT, "firmware", "src", "frodhes.c")).read()
    m = re.search(r"#if defined\(FR_C1\) && FR_C1\n#define FR_RAM\s.*?#else\n#define FR_RAM __attribute__", src, re.S)
    assert m, "FR_RAM must be empty in FR_C1 builds"
