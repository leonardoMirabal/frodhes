# SPDX-License-Identifier: GPL-3.0-only
"""hal/fm1_keyadc.h (key velocity's part of the scan tick) on the host, in the real hal/fm1_input.h tick.

The host build has no HAL, so this copies the HAL headers with every register access turned into a call of the
test's fake_reg() (a model of port A / B, the 74HC595 chain and the key matrix: tests/c/keyadc_test.c) and the FM-1's
.bss sections dropped, then builds and runs tests/c/keyadc_test.c: restore before read (no row read while driven,
held keys never read open, OCT- / OCT+ never read pressed in the self-test), the drive (DRIVE 1..4 in HD0 / HD, PULL),
the LED blanking (one dark tick per landing, BRIGHTNESS 1's single lit frame never swallowed, LEDS ALL / OFF), one
conversion a tick and none in MASTER's / the battery's slots, a lost conversion retried, the developer modes and items.
A second build without the self-test's settle time must show the trap it guards against."""
import os
import re
import subprocess

from hostrun import ROOT, out

HAL = os.path.join(ROOT, "firmware", "hal")
HEADERS = ["fm1_gpio.h", "fm1_time.h", "fm1_cc.h", "fm1_input.h", "fm1_adc.h", "fm1_keyadc.h"]


def fake_hal(dst):
    os.makedirs(dst, exist_ok=True)
    for h in HEADERS:
        s = open(os.path.join(HAL, h)).read()
        s = re.sub(r"\(volatile uint32_t \*\)(0x[0-9A-Fa-f]+u)", r"fake_reg(\1)", s)
        s = s.replace("(volatile uint32_t *)(FM1_PORT(p) + (r))", "fake_reg(FM1_PORT(p) + (r))")
        s = re.sub(r'section\("[^"]*"\)', "used", s)
        assert "volatile uint32_t *)" not in s, h          # every register access goes through the model
        open(os.path.join(dst, h), "w").write(s)


def build_run(name, *defs):
    d = out("keyadc")
    hal = os.path.join(d, "hal")
    fake_hal(hal)
    exe = os.path.join(d, name)
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-function", *defs, "-I" + hal,
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-o", exe,
                    os.path.join(ROOT, "tests", "c", "keyadc_test.c")], check=True, cwd=ROOT)
    r = subprocess.run([exe], capture_output=True, text=True)
    stats = {m.group(1): int(m.group(2)) for m in re.finditer(r"^stat (\S+) (\d+)$", r.stdout, re.M)}
    return r, stats


def test_keyadc_tick_model():
    r, stats = build_run("keyadc_test")
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    assert r.returncode == 0 and not fails, r.stdout
    assert r.stdout.count("\nok   ") >= 30, r.stdout
    assert stats["selftest_raw_glitch"] == 0 and stats["selftest_buttons"] == 0


def test_selftest_settle_is_needed():
    """without the settle after the low drive the pull-up has not lifted the row by the read: keys (OCT- / OCT+ among
    them) read pressed. The model must see it, or the first test proves nothing about the settle"""
    r, stats = build_run("keyadc_nosettle", "-DFM1_KA_SETTLE_US=0u")
    assert stats["selftest_raw_glitch"] > 0, r.stdout
