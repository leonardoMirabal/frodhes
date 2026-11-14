# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's CHECK FOR UPDATES (plugin/src/frp_update.h) without the network: plugin/tests/test_update.c
compiled on its own with the pure part (versions, the response's tag, the 24 h rule, the settings file's text) and
the runtime over a stand-in fetch in a scratch home (the thread, the throttle, the off switch, FRODHES_UPDATE_CHECK=0,
a developer build, failures, a cancel at shutdown, the package's download and its digest). No plugin build needed. The real request per OS:
build/plugin/frp_update_probe (by hand: it needs the network)."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(ROOT, "plugin", "src")


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_plugin_update(tmp_path):
    exe = tmp_path / "test_update"
    r = subprocess.run(["cc", "-O1", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", SRC,
                        os.path.join(ROOT, "plugin", "tests", "test_update.c"), os.path.join(SRC, "frp_update.c"),
                        os.path.join(SRC, "frp_update_rt.c"), os.path.join(SRC, "frp_paths.c"), "-lpthread",
                        "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    env = {k: v for k, v in os.environ.items() if k != "FRODHES_UPDATE_CHECK"}
    r = subprocess.run([str(exe), str(tmp_path / "home")], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok    update check") and "the runtime" in r.stdout
