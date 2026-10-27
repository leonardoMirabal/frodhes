# SPDX-License-Identifier: GPL-3.0-only
"""The page's installer (web/fm1install.js) against a fake FM-1 (tests/web/install_test.js, node): run with the host
tests too, so a change to the protocol or the package checks fails here before the web job. Skipped without node."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_web_installer_on_a_fake_fm1():
    r = subprocess.run(["node", "tests/web/install_test.js"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "web install tests passed" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
