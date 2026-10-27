# SPDX-License-Identifier: GPL-3.0-only
"""Dinghy 0.4's platform tests (tests/*.c, tests/install_test.py; tests/run_tests.sh runs them all after a firmware
build): the SMK-37 flash map, the pitch wheel, the update loader's screen and the installer always; the M-UPGRADE
entry (ota.c) and the update loader on the FM-1 package when there is a build (build/frodhes.fwsc + the AC79 SDK)."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CC = ["cc", "-O1", "-Wall", "-Wno-unused-function"]
SDK = Path(os.environ.get("AC79_SDK", ROOT / ".local" / "fw-AC79_AIoT_SDK"))
if not (SDK / "cpu").exists() and len(ROOT.parents) > 2 \
        and (ROOT.parents[2] / ".local" / "fw-AC79_AIoT_SDK" / "cpu").exists():   # (a checkout near / has no parents[2])
    SDK = ROOT.parents[2] / ".local" / "fw-AC79_AIoT_SDK"    # (a worktree under .claude/worktrees: the main checkout's)


def run_c(tmp_path, src, *args, defs=()):
    exe = tmp_path / Path(src).stem
    subprocess.run([*CC, *defs, "-Ifirmware/hal", "-o", str(exe), src], cwd=ROOT, check=True)
    r = subprocess.run([str(exe), *args], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_smk_flash_map(tmp_path):
    out = run_c(tmp_path, "tests/smk_flash_test.c", defs=["-DBOARD_SMK37=1"])
    assert " ok" in out          # (the return code says it passed: run_c)


def test_smk_wheel(tmp_path):
    assert "all ok" in run_c(tmp_path, "tests/smk_wheel_test.c")


def test_loader_screen(tmp_path):
    assert "test passed" in run_c(tmp_path, "tests/ldr_screen_test.c", str(tmp_path))


def test_installer():
    r = subprocess.run(["python3", "tests/install_test.py"], cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and "install tests passed" in r.stdout, r.stdout + r.stderr


def built():
    if not (ROOT / "build" / "frodhes.fwsc").exists() or not (SDK / "cpu").exists():
        pytest.skip("needs build/frodhes.fwsc (./build.sh) and the AC79 SDK")


def test_ota_entry_on_the_package(tmp_path):
    built()
    out = run_c(tmp_path, "tests/ota_test.c", "build/frodhes.fwsc")
    assert " ok" in out          # (the return code says it passed: run_c)


def test_loader_installs_the_package(tmp_path):
    """the update loader takes another app's package to this build (ldr_test.c, as run_tests.sh)"""
    built()
    old = tmp_path / "old_app.bin"
    old.write_bytes((ROOT / "build" / "frodhes.bin").read_bytes()[:60000])
    subprocess.run(["python3", "tools/fm1pkg_make.py", str(old), "build/loader/ota.bin", str(tmp_path / "old.fwsc")],
                   cwd=ROOT, check=True, capture_output=True, env={**os.environ, "AC79_SDK": str(SDK)})
    out = run_c(tmp_path, "tests/ldr_test.c", str(tmp_path / "old.fwsc"), "build/frodhes.fwsc")
    assert " ok" in out          # (the return code says it passed: run_c)
