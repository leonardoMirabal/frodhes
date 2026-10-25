# SPDX-License-Identifier: GPL-3.0-only
"""The settings record's two sectors, A 0xFC000 / B 0xFD000 (firmware/src/set_store.c), on the flash chip model
(tests/set_ab_test.c): a power cut at every call of a sliced save, from every start, leaves the old record or the new
one; 0.9.4's record loads as it is (the first save to B, A kept); the sequence's wrap; a corrupted header / data /
sequence; the allow-list. FM-1 and SMK-37 (one sector there). The test's copies of platform.c's crc32 / area_load /
sector_load must be platform.c's own."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CC = ["cc", "-O2", "-Wall", "-Wno-unused-function", "-Wno-int-to-pointer-cast", "-Ifirmware/hal"]


def test_set_ab_copies_are_platform_c():
    plat = (ROOT / "firmware/src/platform.c").read_text()
    test = (ROOT / "tests/set_ab_test.c").read_text()
    blocks = re.findall(r"/\* platform\.c begin \*/\n(.*?)/\* platform\.c end \*/", test, re.S)
    assert len(blocks) == 2
    for b in blocks:
        assert b in plat, b[:200]


@pytest.mark.parametrize("board", [[], ["-DBOARD_SMK37=1"]], ids=["fm1", "smk37"])
def test_set_ab(tmp_path, board):
    exe = tmp_path / "set_ab_test"
    subprocess.run([*CC, *board, "-o", str(exe), "tests/set_ab_test.c"], cwd=ROOT, check=True)
    r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "set_ab: all ok" in r.stdout, r.stdout[-2000:] + r.stderr
