# SPDX-License-Identifier: GPL-3.0-only
"""The split flash erase / program (firmware/hal/fm1_flash.h fl_split_op) on a host model of the FM-1's SPI NOR part
(tests/fl_host_model.h, tests/flash_split_test.c): the settings save's erase and page programs never hold the
interrupts off longer than FL_SLICE_US + 100 us, keep the USB ISR's wait (after a silent audio ISR) <= 700 us, never run the part while XIP is on or an ISR runs, keep the
datasheet's resume-to-suspend minimum, write the right bytes; a part that ignores the suspend (or another JEDEC id)
gets the old blocking erase. FM-1 and SMK-37 store layouts."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CC = ["cc", "-O1", "-Wall", "-Wno-unused-function", "-Wno-int-to-pointer-cast", "-Ifirmware/hal"]


@pytest.mark.parametrize("board", [[], ["-DBOARD_SMK37=1"]], ids=["fm1", "smk37"])
def test_flash_split(tmp_path, board):
    exe = tmp_path / "flash_split_test"
    subprocess.run([*CC, *board, "-o", str(exe), "tests/flash_split_test.c"], cwd=ROOT, check=True)
    r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "flash split: all ok" in r.stdout, r.stdout[-2000:] + r.stderr
