# SPDX-License-Identifier: GPL-3.0-only
"""The Makefile rebuilds a host binary when HOST_DEFS change, also within the second the binary was built in (make's
1 s timestamps), and leaves a binary after that run. Before 2026-11-15 the defs stamp rule removed the binary but make
had already read its time: that run built nothing and left no binary (make 3.81)."""
import os
import stat
import subprocess
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def fake_cc(tmp_path):
    """a compiler stand-in: writes its arguments to the -o file and counts its calls (milliseconds, not a build)"""
    cc = tmp_path / "cc.sh"
    cc.write_text('#!/bin/sh\necho x >> "%s"\nout=\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                  'echo "$ARGS" > "$out"\n' % (tmp_path / "calls"))
    cc.chmod(cc.stat().st_mode | stat.S_IEXEC)
    return str(cc)


def make(tmp_path, defs):
    binary = tmp_path / "frodhes"
    env = dict(os.environ, ARGS=defs)
    env.pop("HOST_DEFS", None)
    subprocess.run(["make", "-s", "-C", ROOT, str(binary), "HOSTBIN=" + str(binary), "HOST_DEFS=" + defs,
                    "CC=" + fake_cc(tmp_path)], check=True, capture_output=True, env=env)
    calls = (tmp_path / "calls").read_text().count("x") if (tmp_path / "calls").exists() else 0
    return binary, calls


def test_defs_change_rebuilds_within_the_same_second(tmp_path):
    binary, n = make(tmp_path, "")
    assert n == 1 and binary.exists()
    t = int(time.time()) + 5                                # the binary no older than the stamp written next: what
    os.utime(binary, (t, t))                                # make sees when both fall in one second
    binary, n = make(tmp_path, "-DFR_C1=1")
    assert n == 2, "a HOST_DEFS change must rebuild the binary"
    assert binary.exists() and binary.read_text().strip() == "-DFR_C1=1"
    assert open(str(binary) + ".defs").read().strip() == "-DFR_C1=1"


def test_same_defs_do_not_rebuild(tmp_path):
    make(tmp_path, "-DFR_C1=1")
    binary, n = make(tmp_path, "-DFR_C1=1")
    assert n == 1 and binary.exists()
