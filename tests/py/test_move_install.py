# SPDX-License-Identifier: GPL-3.0-only
"""tools/move_install.py against a fake target folder (--target-dir: the same shell commands, run locally): a dry
run changes nothing; an install stages, checks, switches by rename and keeps the previous module; a dsp.so held
open (a loaded instance) still reads its old bytes after the next install (never overwritten in place); the module
folder's own files are carried over; rollback brings the previous one back; --keep prunes; a wrong module id or a
missing dsp.so stops before anything is copied; the ssh commands of a dry run name the Move's folder."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import move_install as mi  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("sha256sum"), reason="no sha256sum")


def pkg(d, ver, so=None, mid="frodhes"):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "dsp.so"), "wb") as f:
        f.write(so or ("ELF " + ver).encode() * 100)
    with open(os.path.join(d, "engine.so"), "wb") as f:      # (the plugin; dsp.so is the loader)
        f.write(("ENGINE " + ver).encode() * 100)
    json.dump({"id": mid, "version": ver}, open(os.path.join(d, "module.json"), "w"))
    json.dump({"children": []}, open(os.path.join(d, "help.json"), "w"))
    return d


def tgt(d, go=True):
    return mi.Target(local_dir=str(d), go=go, out=lambda s: None)


def cur(d, f="dsp.so"):
    return open(os.path.join(d, "frodhes", f), "rb").read()


def kept(d, what="prev"):
    return sorted(x for x in os.listdir(d) if x.startswith(".frodhes.%s-" % what))


def test_dry_run_changes_nothing(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    t = tgt(mods, go=False)
    mi.install(t, pkg(str(tmp_path / "v1"), "1"), stamp="a")
    assert os.listdir(mods) == [] and t.log


def test_install_switch_keep_rollback(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    mi.install(tgt(mods), pkg(str(tmp_path / "v1"), "1"), stamp="20260101-000001")
    assert cur(mods) == b"ELF 1" * 100 and kept(mods) == [] and not kept(mods, "staging")
    assert cur(mods, "engine.so") == b"ENGINE 1" * 100
    (mods / "frodhes" / "settings.bin").write_bytes(b"\x01\x02")          # the module's own data file
    held = open(mods / "frodhes" / "dsp.so", "rb")                         # a loaded instance's mapping
    mi.install(tgt(mods), pkg(str(tmp_path / "v2"), "2"), stamp="20260101-000002")
    assert cur(mods) == b"ELF 2" * 100 and json.loads(cur(mods, "module.json"))["version"] == "2"
    assert held.read() == b"ELF 1" * 100, "the loaded dsp.so was overwritten in place"
    held.close()
    assert cur(mods, "settings.bin") == b"\x01\x02"
    assert kept(mods) == [".frodhes.prev-20260101-000002"]
    mi.rollback(tgt(mods), stamp="r1")
    assert cur(mods) == b"ELF 1" * 100 and kept(mods) == [] and kept(mods, "rolledback") == [".frodhes.rolledback-r1"]


def test_keep_prunes_the_oldest(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    for i in range(5):
        mi.install(tgt(mods), pkg(str(tmp_path / ("v%d" % i)), str(i)), keep=2, stamp="2026010%d" % i)
    assert kept(mods) == [".frodhes.prev-20260103", ".frodhes.prev-20260104"]
    assert cur(mods) == b"ELF 4" * 100


def test_keep_0_leaves_no_previous_module(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    for i in range(3):
        mi.install(tgt(mods), pkg(str(tmp_path / ("v%d" % i)), str(i)), keep=0, stamp="2026010%d" % i)
    assert kept(mods) == []
    assert cur(mods) == b"ELF 2" * 100


def test_bad_package_stops_before_copying(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    with pytest.raises(SystemExit, match="id"):
        mi.install(tgt(mods), pkg(str(tmp_path / "x"), "1", mid="other"))
    os.remove(os.path.join(pkg(str(tmp_path / "y"), "1"), "dsp.so"))
    with pytest.raises(SystemExit, match="missing"):
        mi.install(tgt(mods), str(tmp_path / "y"))
    os.remove(os.path.join(pkg(str(tmp_path / "z"), "1"), "engine.so"))   # (the loader without its engine)
    with pytest.raises(SystemExit, match="engine.so missing"):
        mi.install(tgt(mods), str(tmp_path / "z"))
    assert os.listdir(mods) == []


def test_rollback_without_a_kept_module_fails(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    mi.install(tgt(mods), pkg(str(tmp_path / "v1"), "1"), stamp="a")
    with pytest.raises(SystemExit, match="rollback"):
        mi.rollback(tgt(mods))
    assert cur(mods) == b"ELF 1" * 100


def test_info_and_cli(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    src = pkg(str(tmp_path / "v7"), "7.0")
    tool = os.path.join(ROOT, "tools", "move_install.py")
    r = subprocess.run([sys.executable, tool, "install", "--src", src, "--target-dir", str(mods)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "dry run" in r.stdout and os.listdir(mods) == []
    r = subprocess.run([sys.executable, tool, "--go", "install", "--src", src, "--target-dir", str(mods)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, tool, "info", "--target-dir", str(mods)], capture_output=True, text=True)
    assert '"version": "7.0"' in r.stdout and mi.sha256(os.path.join(src, "dsp.so")) in r.stdout, r.stdout
    assert mi.sha256(os.path.join(src, "engine.so")) in r.stdout, r.stdout
    inst = mods / ".data" / "inst" / "0"                      # (an instance's boot.txt, as the plugin writes it)
    inst.mkdir(parents=True)
    (inst / "boot.txt").write_text("bridge=2 dev_play=1 dsp_crc32=0badc0de starts=1\n")
    r = subprocess.run([sys.executable, tool, "info", "--target-dir", str(mods)], capture_output=True, text=True)
    assert "inst/0/boot.txt: bridge=2 dev_play=1 dsp_crc32=0badc0de" in r.stdout, r.stdout


def test_dry_run_over_ssh_names_the_moves_folder(tmp_path):
    lines = []
    t = mi.Target(host="ableton@move.local", go=False, out=lines.append)
    mi.install(t, pkg(str(tmp_path / "v1"), "1"), stamp="s")
    cmds = [" ".join(c) for c in t.log]
    assert all(c.startswith(("ssh ", "scp ")) for c in cmds)
    assert any(mi.MODS + "/.frodhes.staging-s/dsp.so" in c for c in cmds if c.startswith("scp"))
    assert not any("scp" in c and c.endswith(mi.MODS + "/frodhes/dsp.so") for c in cmds), "a copy onto the live file"
