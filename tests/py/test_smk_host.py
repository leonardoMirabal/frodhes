# SPDX-License-Identifier: GPL-3.0-only
"""The SMK-37 build of Frodhes on the host (no SMK-37 to try it on: these are its flash-safety proof).

The host build checks every flash erase and write against the firmware's own allow-list and the board's store
(tests/host.c host_fl_check); FRODHES_FLASHLOG=1 prints each call. Here: the SMK build (-DBOARD_SMK37=1
-DFR_VOICES=7u) saves the settings, the preset bank (both copies) and the pattern (both copies), is switched off and
on (FRODHES_FLASH), and plays it all back; every logged place is one of Frodhes' SMK sectors (0x9D000 the settings,
0x9E000-0xA1FFF the presets and the pattern: PR #3's places, Dinghy's blobs 0..3), inside FL_DATA (0x9D000-0xBAFFF),
never USRFLASH, the calibration or the FM-1's places. The FM-1 build's log names only its own sectors. The flash map
itself (tests/smk_flash_test.c -DFRODHES_STORE), the identities and the package sizes too."""
import os
import re
import subprocess
from pathlib import Path

import pytest

from hostrun import ROOT, cc, once, run, build

SMKBIN = os.path.join(ROOT, "build", "host-smk", "frodhes")
SMK_DEFS = ["-DBOARD_SMK37=1", "-DFR_VOICES=7u"]
SMK_OK = {0x9D000: "settings", 0x9E000: "presets A", 0x9F000: "presets B", 0xA0000: "pattern A", 0xA1000: "pattern B"}
FM1_OK = {0xFC000: "settings", 0x97000: "presets A", 0x98000: "presets B", 0x99000: "pattern A", 0x9A000: "pattern B"}
FLASH = re.compile(r"^\s*\d+ ms  flash (\w+) ([\w ]+?) 0x([0-9A-F]+) \+ (\d+) ok$", re.M)


def smk_build():
    def make(b):
        subprocess.run(["cc", "-O2", "-Wall", "-Wno-unused-function", "-DDINGHY_HOST", '-DAPP_VER="dev"', *SMK_DEFS,
                        "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                        "-o", b, os.path.join(ROOT, "firmware", "src", "frodhes.c"), "-lm"], check=True)
    return once(SMKBIN, make)


# the same day as tests/py/test_persistence.py: U01 saved twice (bank A, then B), the pattern recorded twice (A, B),
# the settings saved after each change; then silence
SAVES = [cc(20, 81, 90), "100 btn 9", "200 btn 9",
         cc(3000, 81, 30), "3100 btn 9", "3200 btn 9",
         cc(6000, 63, 127), "6100 midi 0x90 60 100", "6150 midi 0x80 60 0", cc(6200, 63, 0),
         cc(9000, 63, 127), "9100 midi 0x90 64 100", "9150 midi 0x80 64 0", cc(9200, 63, 0),
         "12000 midi 0xC0 0 0"]


def flash_calls(out):
    return [(op, what, int(at, 16), int(n)) for op, what, at, n in FLASH.findall(out)]


def sectors(calls):
    return {at & ~0xFFF for _, _, at, _ in calls}


def flash_path(tag):
    p = os.path.join(ROOT, "build", "host-smk", tag + ".flash")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.remove(p)
    return p


def test_smk_every_flash_call_in_its_store():
    b = smk_build()
    fl = flash_path("smk-flash")
    r = run("smk-flash", SAVES, 13000, flash=fl, env={"FRODHES_FLASHLOG": "1"}, binary=b)
    calls = flash_calls(r.out)
    assert not r.fails and "FAIL flash" not in r.out, r.fails
    assert sectors(calls) == set(SMK_OK), sorted(hex(s) for s in sectors(calls))   # all five: every one was written
    for op, what, at, n in calls:
        assert 0x9D000 <= at and at + n <= 0xA2000, (op, what, hex(at), n)        # FL_DATA, Frodhes' five sectors
        assert at + n <= 0xBB000 and not (0xC0000 <= at < 0xEA000), (what, hex(at))   # never BTIF / USRTRIM / USRFLASH
    erases = [at for op, _, at, n in calls if op == "erase"]
    assert all(at % 0x1000 == 0 for at in erases) and {0x9E000, 0x9F000, 0xA0000, 0xA1000} <= set(erases)
    # the next power-on reads it all back from those sectors: U01 = DRIVE 30, the pattern's 2 notes
    r2 = run("smk-flash-2", ["100 midi 0xC0 16 0", "250 dump"], 400, flash=fl, binary=b)
    s = r2.state(250)
    assert s["header"] == "U01 ST77" and s["p.1"] == "30" and s["notes"] == "2", (s["header"], s["p.1"], s["notes"])


def test_fm1_every_flash_call_in_its_store():
    b = build()
    fl = os.path.join(ROOT, "build", "host", "fm1-flash.flash")
    if os.path.exists(fl):
        os.remove(fl)
    r = run("fm1-flash", SAVES, 13000, flash=fl, env={"FRODHES_FLASHLOG": "1"}, binary=b)
    calls = flash_calls(r.out)
    assert not r.fails and "FAIL flash" not in r.out, r.fails
    assert sectors(calls) == set(FM1_OK), sorted(hex(s) for s in sectors(calls))   # pattern B (0x9A000) included
    for op, what, at, n in calls:
        assert (0x97000 <= at and at + n <= 0x9B000) or (0xFC000 <= at and at + n <= 0xFD000), (what, hex(at), n)


def test_smk_flash_map_frodhes(tmp_path):
    exe = tmp_path / "smk_flash_fr"
    subprocess.run(["cc", "-O1", "-Wall", "-Wno-unused-function", "-DBOARD_SMK37=1", "-DFRODHES_STORE",
                    "-Ifirmware/hal", "-o", str(exe), "tests/smk_flash_test.c"], cwd=ROOT, check=True)
    r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "FAIL" not in r.stdout, r.stdout
    assert "Frodhes may write 11 sectors" in r.stdout and "SMK37DATAFRODHES" in r.stdout
    assert re.findall(r"owner change erases 0x([0-9A-F]+)", r.stdout) == ["9D000", "9E000", "9F000", "A0000", "A1000", "AF000"]


def test_smk_identities_and_names():
    import importlib.util
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    spec = importlib.util.spec_from_file_location("frodhes_build_smk", os.path.join(ROOT, "tools", "build.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.package("smk37", "pro") == ("SMK-37 Pro_470", "frodhes-smk37.fwsc")
    assert m.package("smk37", "elite") == ("SMK-37 Elite_470", "frodhes-smk37-elite.fwsc")
    assert m.package("smk37", "mke") == ("MKE-P37_470", "frodhes-smk37-mke.fwsc")
    assert m.package("smk37", "pro", "0.9.5") == ("SMK-37 Pro_470", "frodhes-0.9.5-smk37.fwsc")
    assert m.package() == ("FM-1_958", "frodhes.fwsc")
    assert m.BOARD_APP_DEFS == {"fm1": [], "smk37": ["-DFR_VOICES=7u"]} and m.BOARD_DEFS["fm1"] == []
    assert m.DATA_FAMILY == "FRODHES"


@pytest.mark.parametrize("model,ident", [("", "SMK-37 Pro_470"), ("-elite", "SMK-37 Elite_470"), ("-mke", "MKE-P37_470")])
def test_smk_packages(model, ident):
    """the SMK packages of the last firmware builds (BOARD=smk37 [MODEL=] ./build.sh), when there are any: the layout,
    the identity, the app within the SMK's slot (619,520 B)"""
    pkg = Path(ROOT) / "build" / f"frodhes-smk37{model}.fwsc"
    if not pkg.exists():
        pytest.skip(f"no {pkg.name} (BOARD=smk37 ./build.sh)")
    r = subprocess.run(["python3", "tests/smk_pkg_test.py", str(pkg), ident], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "smk package test passed" in r.stdout, r.stdout + r.stderr


SAVED = re.compile(r"^\s*(\d+) ms  settings saved", re.M)


IDS = "0 ids smk"                                # (the SMK's own encoder ids: K1 = 0)


def test_smk_master_kept_across_power_cycles():
    """GitHub #14: K1's MASTER in the settings record. Turned (5 detents down: 724 - 5 x 24 = 604), saved by the
    settings' auto save (2 s after the last change), switched off and on: 604 again, before the first pass reads K1"""
    b = smk_build()
    fl = flash_path("smk-master")
    r = run("smk-master", [IDS, "100 turn 0 -5", "200 expect state master 604"], 3000, flash=fl, binary=b)
    assert SAVED.search(r.out), r.out[-2000:]
    run("smk-master-2", [IDS, "1 expect state master 604", "100 turn 0 2", "200 expect state master 652"], 300, flash=fl,
        binary=b)


def test_smk_master_old_record_724():
    """a record without the value (a first boot; a record from before; MASTER never turned, SWING changed: nothing written for it,
    the field stays 0) starts at 724, smk_input.h's own start"""
    b = smk_build()
    fl = flash_path("smk-master-old")
    run("smk-master-old", ["1 expect state master 724"], 100, flash=fl, binary=b)
    r = run("smk-master-old-1", [cc(20, 46, 60), "100 expect state master 724"], 3000, flash=fl, binary=b)
    assert SAVED.search(r.out), r.out[-2000:]
    run("smk-master-old-2", ["1 expect state master 724"], 100, flash=fl, binary=b)


def test_smk_master_no_write_while_turning():
    """K1 turned a detent every 500 ms for 5 s: no flash write while it turns (a write per detent never); one save,
    2 s or more after the last detent, in silence"""
    b = smk_build()
    fl = flash_path("smk-master-turn")
    turns = [f"{100 + 500 * i} turn 0 1" for i in range(11)]          # the last at 5100 ms
    r = run("smk-master-turn", [IDS] + turns + ["5200 expect state master 988"], 9000, flash=fl,
            env={"FRODHES_FLASHLOG": "1"}, binary=b)
    saves = [int(t) for t in SAVED.findall(r.out)]
    assert len(saves) == 1 and saves[0] >= 5100 + 2000, (saves, r.out[-2000:])
    writes = [at for op, _, at, _ in flash_calls(r.out) if op in ("write", "erase")]
    assert writes and all(at & ~0xFFF == 0x9D000 for at in writes), writes
    assert not any(t < 7100 for t in (int(x) for x in re.findall(r"^\s*(\d+) ms  flash", r.out, re.M))), r.out[-2000:]
    run("smk-master-turn-2", ["1 expect state master 988"], 100, flash=fl, binary=b)
