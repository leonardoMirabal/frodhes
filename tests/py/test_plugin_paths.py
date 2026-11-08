# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's user folders (plugin/src/frp_paths.c): every OS's rules (macOS and its App Sandbox, Linux
XDG, Windows APPDATA / UNC / drive roots) tested here, plus this OS's mkdir -p, atomic replace and listing in a
scratch folder. No plugin build needed: plugin/tests/test_paths.c is compiled on its own. (The same test ran on
Windows under Wine and on Linux when the module was written; see docs/PLUGIN.md.)"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_plugin_paths(tmp_path):
    exe = tmp_path / "test_paths"
    r = subprocess.run(["cc", "-O1", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(ROOT, "plugin", "src"),
                        os.path.join(ROOT, "plugin", "tests", "test_paths.c"),
                        os.path.join(ROOT, "plugin", "src", "frp_paths.c"), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([str(exe), str(tmp_path / "scratch")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok    paths")
