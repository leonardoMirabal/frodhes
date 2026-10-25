# SPDX-License-Identifier: GPL-3.0-only
"""The converter scheduler (firmware/hal/fm1_adc.h, key velocity): tests/c/adc_sched_test.c runs the header's own
tick and main-loop functions against a model of the SARADC. It checks the slot pattern (MASTER on column 0 of
even frames, the battery on column 1 every 256th frame, held 3 ticks, keys on every other tick), one conversion
per tick, the staleness bounds (MASTER 2 frames, the battery 256 frames, with and without key traffic), that a
key start is refused while a slot's conversion runs, the timeouts, the main loop's cached reads (MASTER once per
reading, its ends 0 / 1020 / 1023 passed through), and the boot handshake with the blocking read (0.9.4's
register sequence, kept for the battery's slot)."""
import os
import re
import subprocess

from hostrun import HOST, ROOT


def test_adc_sched_c():
    os.makedirs(HOST, exist_ok=True)
    exe = os.path.join(HOST, "adc_sched_test")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-I" + os.path.join(ROOT, "firmware", "hal"), os.path.join(ROOT, "tests", "c", "adc_sched_test.c"),
                    "-o", exe], check=True)
    p = subprocess.run([exe], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout
    assert " 0 failed" in p.stdout and p.stdout.count("ok ") >= 39, p.stdout


def test_platform_reads_unchanged():
    """MASTER and the battery still go through fm1_adc_read (platform.c / ui.c untouched: the scheduler answers
    there), and the battery's slot uses the SDK settings fm1_adc_read always used"""
    plat = open(os.path.join(ROOT, "firmware", "src", "platform.c")).read()
    ui = open(os.path.join(ROOT, "firmware", "src", "ui.c")).read()
    adc = open(os.path.join(ROOT, "firmware", "hal", "fm1_adc.h")).read()
    assert "(a = fm1_adc_read(FM1_ADC_MASTER)) >= 0" in plat
    assert "return fm1_adc_read(FM1_ADC_BATT);" in ui
    assert re.search(r"#define FM1_ADC_CFG_SDK FM1_ADC_CFG\(6, 15\)", adc)
    assert re.search(r"#define FM1_ADC_CFG_KEY FM1_ADC_CFG\(7, 2\)", adc)
    assert "fm1__adc_slot_start(FM1__ADC_W_BATT, FM1_ADC_BATT, FM1_ADC_CFG_SDK)" in adc
    assert 'section(".bss.fm1_adc_sched")' in adc
