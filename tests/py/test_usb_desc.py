# SPDX-License-Identifier: GPL-3.0-only
"""The USB descriptors as a host reads them (tests/usb_desc_test.c runs usb.c's get_desc unchanged): the app is
1209:0005 "Frodhes" with its MIDI jacks named "Frodhes" (a new PID, so a Mac that cached "Felucca" under 1209:0001
makes a new MIDI device), the update loader stays 1209:0002 "Felucca Update" with unnamed jacks, as before; every
length (each descriptor, wTotalLength = CFG_LEN) and every string index checked."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CASES = {
    "app": ([], "1209:0005", '"Frodhes"', "iJack 2", "178 bytes"),
    "app-no-uac": (["-DFELUCCA_UAC=0"], "1209:0005", '"Frodhes"', "iJack 2", "101 bytes"),
    "loader": (["-DFELUCCA_LOADER=1", "-DFELUCCA_USB_PID=0x0002", "-DFELUCCA_UAC=0"], "1209:0002",
               '"Felucca Update"', "iJack 0", "101 bytes"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_usb_descriptors(tmp_path, name):
    defs, dev, prod, ijack, size = CASES[name]
    exe = str(tmp_path / f"usb_desc_{name}")
    subprocess.run(["cc", "-O1", "-w", *defs, "-I" + os.path.join(ROOT, "firmware", "src"), "-o", exe,
                    os.path.join(ROOT, "tests", "usb_desc_test.c")], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "usb_desc_test: ok" in out
    assert f"device: {dev} " in out and f"product: {prod}" in out and ijack in out and size in out, out
    assert 'manufacturer: "Hügelton Instruments"' in out
