# SPDX-License-Identifier: GPL-3.0-only
"""The USB descriptors as a host reads them (tests/usb_desc_test.c runs usb.c's get_desc unchanged): the FM-1 app is
1209:0006 "Frodhes FM-1" with its MIDI jacks named the same, the SMK-37 builds 1209:0007 / 0008 / 0009 "Frodhes
SMK-37 Pro" / "Frodhes SMK-37 Elite" / "Frodhes MKE-P37" (0.9.7, GitHub #21: tools that find a unit by the model in
its MIDI port's name find Frodhes; a new PID per model, so a Mac that cached "Frodhes" under 1209:0005 or "Felucca"
under 0001 makes a new MIDI device with the new name), the update loader stays 1209:0002 "Felucca Update" with
unnamed jacks, as before; every length (each descriptor, wTotalLength = CFG_LEN) and every string index checked.
tools/build.py's MODEL_PID gives the Elite / MKE-P37 apps their PIDs as here."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CASES = {
    "app": ([], "1209:0006", '"Frodhes FM-1"', "iJack 2", "178 bytes"),
    "app-no-uac": (["-DFELUCCA_UAC=0"], "1209:0006", '"Frodhes FM-1"', "iJack 2", "101 bytes"),
    "smk-pro": (["-DBOARD_SMK37=1", "-DWANT_PID=0x0007", '-DWANT_PROD="Frodhes SMK-37 Pro"'], "1209:0007",
                '"Frodhes SMK-37 Pro"', "iJack 2", "178 bytes"),
    "smk-elite": (["-DBOARD_SMK37=1", '-DSMK_MODEL="SMK-37 Elite"', "-DFELUCCA_USB_PID=0x0008", "-DWANT_PID=0x0008",
                   '-DWANT_PROD="Frodhes SMK-37 Elite"'], "1209:0008", '"Frodhes SMK-37 Elite"', "iJack 2", "178 bytes"),
    "smk-mke": (["-DBOARD_SMK37=1", '-DSMK_MODEL="MKE-P37"', "-DFELUCCA_USB_PID=0x0009", "-DWANT_PID=0x0009",
                 '-DWANT_PROD="Frodhes MKE-P37"'], "1209:0009", '"Frodhes MKE-P37"', "iJack 2", "178 bytes"),
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


def test_build_gives_each_model_its_pid():
    """tools/build.py MODEL_PID: the Elite and MKE-P37 apps get the PIDs this test checks (the Pro is usb.c's own)"""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build
    assert build.MODEL_PID == {"pro": 0x0007, "elite": 0x0008, "mke": 0x0009}
    assert set(build.MODEL_PID) == set(build.MODELS)
